import os
import re
import json
import time
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv

# Load config from ~/clipper/.env
load_dotenv(dotenv_path=os.path.expanduser("~/clipper/.env"))

def get_llm_config() -> Dict[str, str]:
    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("NVIDIA_API_KEY")
    base_url = os.getenv("OPENAI_BASE_URL") or "https://integrate.api.nvidia.com/v1"
    model = os.getenv("OPENAI_MODEL") or "z-ai/glm-5.3-flash"

    if not api_key:
        raise ValueError(
            "API key is missing! Please set OPENAI_API_KEY or NVIDIA_API_KEY in ~/clipper/.env."
        )
    return {
        "api_key": api_key,
        "base_url": base_url,
        "model": model
    }

def split_transcript_into_chunks(
    segments: List[Dict[str, Any]],
    chunk_char_target: int = 7000,
    overlap_seconds: float = 60.0
) -> List[Dict[str, Any]]:
    """
    Splits transcript into chunks with generous overlap (default: 60s)
    so candidate clips spanning chunk boundaries are never missed.
    """
    if not segments:
        return []

    chunks = []
    current_lines = []
    current_len = 0
    current_segments = []

    for seg in segments:
        line = f"[{seg['start']:.1f}] {seg['text']}\n"
        line_len = len(line)

        if current_len + line_len > chunk_char_target and current_lines:
            chunks.append({
                "text": "".join(current_lines),
                "segments": list(current_segments)
            })

            # Retain trailing segments within overlap_seconds for the next chunk
            last_end = current_segments[-1]["end"] if current_segments else 0.0
            overlap_cutoff = max(0.0, last_end - overlap_seconds)
            overlap_segs = [s for s in current_segments if s["end"] >= overlap_cutoff]
            if len(overlap_segs) >= len(current_segments):
                overlap_segs = overlap_segs[len(overlap_segs) // 2 :]

            current_segments = list(overlap_segs)
            current_lines = [f"[{s['start']:.1f}] {s['text']}\n" for s in current_segments]
            current_len = sum(len(l) for l in current_lines)

            current_lines.append(line)
            current_len += line_len
            current_segments.append(seg)
        else:
            current_lines.append(line)
            current_len += line_len
            current_segments.append(seg)

    if current_lines:
        chunks.append({
            "text": "".join(current_lines),
            "segments": list(current_segments)
        })

    return chunks

def extract_json_array(text: str) -> Optional[List[Dict[str, Any]]]:
    """
    Defensively extracts a JSON array from raw model output.
    Strips code fences and finds the first [...] block.
    """
    cleaned = text.strip()
    # Strip markdown backticks
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = cleaned.strip()

    # Find the outermost array brackets
    start_idx = cleaned.find("[")
    end_idx = cleaned.rfind("]")
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        candidate_json = cleaned[start_idx : end_idx + 1]
        try:
            parsed = json.loads(candidate_json)
            if isinstance(parsed, list):
                return parsed
        except Exception:
            # Try removing trailing commas
            fixed = re.sub(r",\s*([\]}])", r"\1", candidate_json)
            try:
                parsed = json.loads(fixed)
                if isinstance(parsed, list):
                    return parsed
            except Exception:
                pass
    return None

def call_llm_with_retry(
    prompt: str,
    system_prompt: str,
    client: Any,
    model: str,
    max_retries: int = 4
) -> str:
    """Calls OpenAI-compatible endpoint with backoff for rate limits (429) and timeouts."""
    backoff = 2.0
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                stream=False,
                timeout=60.0
            )
            content = response.choices[0].message.content or ""
            return content
        except Exception as e:
            err_msg = str(e)
            is_rate_limit = "429" in err_msg or "rate" in err_msg.lower()
            is_timeout = "timeout" in err_msg.lower()

            if (is_rate_limit or is_timeout) and attempt < max_retries - 1:
                print(f"[Selector] API warning ({err_msg}). Retrying in {backoff:.1f}s (attempt {attempt+1}/{max_retries})...")
                time.sleep(backoff)
                backoff *= 2.0
            else:
                if attempt == max_retries - 1:
                    raise RuntimeError(f"LLM API request failed after {max_retries} attempts: {e}")
                print(f"[Selector] Error calling LLM: {e}. Retrying in {backoff:.1f}s...")
                time.sleep(backoff)
                backoff *= 2.0
    return ""

def snap_to_segments(
    start: float,
    end: float,
    segments: List[Dict[str, Any]]
) -> tuple[float, float]:
    """Snaps start and end timestamps to the nearest segment boundaries."""
    if not segments:
        return start, end

    # Find segment whose start is closest to candidate start
    best_start_seg = min(segments, key=lambda s: abs(s["start"] - start))
    snapped_start = best_start_seg["start"]

    # Find segment whose end is closest to candidate end
    best_end_seg = min(segments, key=lambda s: abs(s["end"] - end))
    snapped_end = best_end_seg["end"]

    # Ensure snapped_end is strictly after snapped_start
    if snapped_end <= snapped_start:
        snapped_end = best_start_seg["end"]

    return round(snapped_start, 2), round(snapped_end, 2)

def select_clip_candidates(
    segments: List[Dict[str, Any]],
    min_duration: float = 20.0,
    max_duration: float = 50.0,
    num_clips: int = 3,
    model: Optional[str] = None,
    base_url: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Splits transcript into chunks and prompts LLM for clip candidates.
    Returns snapped candidate list.
    """
    config = get_llm_config()
    if model:
        config["model"] = model
    if base_url:
        config["base_url"] = base_url
    import openai
    kwargs = {"api_key": config["api_key"]}
    if config["base_url"]:
        kwargs["base_url"] = config["base_url"]
    kwargs["timeout"] = 180.0
    client = openai.OpenAI(**kwargs)

    chunks = split_transcript_into_chunks(segments, chunk_char_target=7000)
    print(f"[Selector] Transcript split into {len(chunks)} chunk(s) for LLM analysis.")

    # Calculate target candidates per chunk to meet num_clips
    cands_per_chunk = max(5, int((num_clips * 1.5) / max(1, len(chunks))) + 2)

    system_prompt = (
        "You are an elite YouTube Shorts & TikTok content strategist and viral video editor. "
        "Your goal is to identify high-retention vertical clips from the transcript.\n\n"
        "STRICT SCORING CALIBRATION RUBRIC (Do NOT inflate scores! Use the full 1-10 scale):\n"
        "• 9.0 - 10.0 (VIRAL MASTERPIECE): Explosive curiosity hook in the first 1.5 seconds, dramatic tension, completely self-contained, punchy emotional or comedic payoff. (Award sparingly! Max ~15% of clips).\n"
        "• 7.5 - 8.9 (STRONG CLIP): Clear compelling hook, coherent self-contained story/insight, solid payoff.\n"
        "• 6.0 - 7.4 (AVERAGE / PASSABLE): Moderately interesting topic, but slow initial hook (>3s to get interesting) or weak ending.\n"
        "• 1.0 - 5.9 (WEAK / SKIP): Incomplete thought, requires prior context, rambling, or dull delivery.\n\n"
        "CRITICAL RULES:\n"
        f"1. Duration MUST be strictly between {min_duration} and {max_duration} seconds (end - start >= {min_duration}s).\n"
        "2. The 'hook' field MUST be a punchy 3-8 word viewer-facing headline/caption (e.g. 'He spent $500,000 on THIS?!') to be burned onto the video.\n"
        "3. Respond ONLY with a raw JSON array. Do not include markdown codeblocks or preamble."
    )

    all_raw_candidates: List[Dict[str, Any]] = []

    for i, chunk in enumerate(chunks):
        print(f"[Selector] Processing chunk {i + 1}/{len(chunks)} with LLM ({config['model']})...")
        prompt = (
            f"Here is transcript chunk #{i + 1} with [start_seconds] timestamps:\n\n"
            f"{chunk['text']}\n\n"
            f"Identify {cands_per_chunk} candidates. Evaluate each rigorously.\n"
            f"Each candidate MUST be between {min_duration} and {max_duration} seconds long.\n"
            "Return ONLY a JSON array with this schema:\n"
            "[\n"
            '  {\n'
            '    "start": 12.5,\n'
            '    "end": 45.0,\n'
            '    "hook_score": 8.5,\n'
            '    "story_score": 8.0,\n'
            '    "payoff_score": 9.0,\n'
            '    "score": 8.5,\n'
            '    "title": "Short Catchy YouTube Title",\n'
            '    "hook": "Punchy 3-8 Word Video Headline"\n'
            '  }\n'
            "]"
        )

        try:
            content = call_llm_with_retry(prompt, system_prompt, client, config["model"])
            parsed = extract_json_array(content)

            # Retry once on failure
            if parsed is None:
                print(f"[Selector] Warning: Could not parse JSON from chunk {i+1}. Retrying with repair prompt...")
                repair_prompt = f"Fix this output into a valid JSON array only:\n{content}"
                repaired_content = call_llm_with_retry(repair_prompt, "Return ONLY the valid JSON array.", client, config["model"])
                parsed = extract_json_array(repaired_content)

            if parsed is None:
                print(f"[Selector] Warning: Skipping chunk {i+1} because LLM output could not be parsed as JSON.")
                continue

            for cand in parsed:
                try:
                    raw_start = float(cand["start"])
                    raw_end = float(cand["end"])
                    h_score = float(cand.get("hook_score", cand.get("score", 6.0)))
                    s_score = float(cand.get("story_score", cand.get("score", 6.0)))
                    p_score = float(cand.get("payoff_score", cand.get("score", 6.0)))
                    # Calibrate score using weighted formula
                    calibrated_score = round(0.4 * h_score + 0.3 * s_score + 0.3 * p_score, 1)

                    title = str(cand.get("title", "Untitled Clip")).strip()
                    hook = str(cand.get("hook", "")).strip()

                    snapped_start, snapped_end = snap_to_segments(raw_start, raw_end, segments)

                    all_raw_candidates.append({
                        "start": snapped_start,
                        "end": snapped_end,
                        "hook_score": h_score,
                        "story_score": s_score,
                        "payoff_score": p_score,
                        "score": calibrated_score,
                        "title": title,
                        "hook": hook
                    })
                except Exception as cand_err:
                    print(f"[Selector] Skipping invalid candidate item: {cand_err}")

        except Exception as e:
            print(f"[Selector] Error in stage 3 (chunk {i+1}): {e}. Skipping chunk.")

    # Deduplicate candidates across overlapping chunks (|start1 - start2| <= 3.5s)
    deduped_candidates: List[Dict[str, Any]] = []
    # Sort by score descending so higher scoring duplicates are preferred
    all_raw_candidates.sort(key=lambda c: c.get("score", 0), reverse=True)
    for c in all_raw_candidates:
        is_dup = False
        for kept in deduped_candidates:
            if abs(c["start"] - kept["start"]) <= 3.5:
                is_dup = True
                break
        if not is_dup:
            deduped_candidates.append(c)

    print(f"[Selector] Extracted {len(all_raw_candidates)} total candidates ({len(deduped_candidates)} unique non-duplicates across chunks).")

    # Second LLM pass: re-score with strict rubric, penalize weak openers, and diversify topics
    final_candidates = rescore_and_diversify_candidates(
        candidates=deduped_candidates,
        segments=segments,
        num_clips=num_clips,
        client=client,
        model=config["model"]
    )
    return final_candidates

WEAK_OPENERS = {"so", "and", "yeah", "yes", "but", "like", "well", "okay", "ok", "right", "uh", "um", "actually"}

def get_candidate_opening_text(cand: Dict[str, Any], segments: List[Dict[str, Any]], word_limit: int = 12) -> str:
    """Extracts the first few words spoken at the beginning of a candidate clip."""
    words = []
    c_start = cand["start"]
    for s in segments:
        if s["end"] <= c_start:
            continue
        if s["start"] > c_start + 8.0:
            break
        if "words" in s and s["words"]:
            for w in s["words"]:
                if w["start"] >= c_start - 0.1:
                    words.append(w["word"].strip())
                    if len(words) >= word_limit:
                        break
        else:
            words.extend(s.get("text", "").split())
        if len(words) >= word_limit:
            break
    return " ".join(words[:word_limit]).strip()

def rescore_and_diversify_candidates(
    candidates: List[Dict[str, Any]],
    segments: List[Dict[str, Any]],
    num_clips: int,
    client: Any,
    model: str
) -> List[Dict[str, Any]]:
    """
    Second LLM Pass:
    - Re-scores candidates using strict editorial rubric.
    - Heavily penalizes weak connective openers ("so", "and", "yeah", "well", "like").
    - Enforces diversity across topics so clips don't cover the same moment twice.
    """
    if not candidates:
        return []

    # Tag opening lines and check weak openers
    eval_payload = []
    for idx, c in enumerate(candidates, start=1):
        opening = get_candidate_opening_text(c, segments)
        c["_temp_id"] = idx
        c["opening_text"] = opening

        first_word = opening.split()[0].lower().strip(".,!?\"'()[]{}") if opening.split() else ""
        has_weak = first_word in WEAK_OPENERS
        c["weak_opener"] = has_weak

        eval_payload.append({
            "id": idx,
            "title": c.get("title", ""),
            "hook": c.get("hook", ""),
            "duration": round(c["end"] - c["start"], 1),
            "opening_line": opening,
            "initial_score": c.get("score", 6.0)
        })

    system_prompt = (
        "You are the Senior Editorial Director for viral YouTube Shorts and TikTok content.\n"
        "Perform a RIGOROUS SECOND-PASS RE-SCORING of candidate clips.\n\n"
        "STRICT RUBRIC:\n"
        "1. PENALIZE WEAK OPENERS: Clips starting with connective/filler words ('so', 'and', 'yeah', 'but', 'well', 'like', 'you know') "
        "ruin viewer retention in the feed. Penalize them by at least -1.5 to -3.0 points from initial_score.\n"
        "2. STANDALONE PAYOFF: Standalone, punchy self-contained stories with instant curiosity score highest (8.0 - 9.5).\n"
        "3. TOPIC DIVERSITY: Assign a 1-3 word 'topic' to each clip so different topics can be selected.\n"
        "Return ONLY a JSON array with schema: [{\"id\": 1, \"adjusted_score\": 8.5, \"topic\": \"Topic Name\", \"penalty_applied\": 0.0}]"
    )

    prompt = (
        f"Re-score these {len(eval_payload)} candidates. Penalize weak openers and assign a topic:\n"
        f"{json.dumps(eval_payload, indent=2)}\n\n"
        "Return ONLY the raw JSON array."
    )

    print(f"[Selector] Running Second-Pass LLM re-scoring & diversity filter on {len(candidates)} candidates...")
    try:
        content = call_llm_with_retry(prompt, system_prompt, client, model)
        parsed = extract_json_array(content)
        if parsed and isinstance(parsed, list):
            rescore_map = {item["id"]: item for item in parsed if isinstance(item, dict) and "id" in item}
            for c in candidates:
                tid = c.get("_temp_id")
                if tid in rescore_map:
                    rm = rescore_map[tid]
                    c["score"] = round(float(rm.get("adjusted_score", c["score"])), 1)
                    c["topic"] = str(rm.get("topic", "General")).strip()
            print("[Selector] Second-pass re-scoring completed successfully.")
        else:
            print("[Selector] Warning: Could not parse second-pass JSON. Applying rule-based penalty fallback.")
            for c in candidates:
                if c.get("weak_opener"):
                    c["score"] = round(max(1.0, c.get("score", 6.0) - 2.0), 1)
    except Exception as e:
        print(f"[Selector] Second-pass LLM call notice ({e}). Applying rule-based opener penalty fallback.")
        for c in candidates:
            if c.get("weak_opener"):
                c["score"] = round(max(1.0, c.get("score", 6.0) - 2.0), 1)

    # Clean temporary keys
    for c in candidates:
        c.pop("_temp_id", None)

    # Sort by score descending
    candidates.sort(key=lambda x: x.get("score", 0), reverse=True)

    # Select with topic diversity
    diversified: List[Dict[str, Any]] = []
    seen_topics = set()
    target_count = max(num_clips * 2, 6)

    # First pass: pick best scoring per unique topic
    for c in candidates:
        top = c.get("topic", "").strip().lower()
        if top and top not in seen_topics:
            diversified.append(c)
            seen_topics.add(top)
            if len(diversified) >= target_count:
                break

    # Second pass: fill remaining slots with highest scoring remaining
    for c in candidates:
        if c not in diversified:
            diversified.append(c)
            if len(diversified) >= target_count:
                break

    print(f"[Selector] Final selection: {len(diversified)} diverse, re-scored candidate(s).")
    return diversified
