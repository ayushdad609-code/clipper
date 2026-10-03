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
    chunk_char_target: int = 10000
) -> List[Dict[str, Any]]:
    """
    Splits transcript into chunks of ~10k characters.
    Each line formatted as '[start_seconds] text'.
    Returns list of chunk objects with formatted text and relevant segment range.
    """
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
                "segments": current_segments
            })
            current_lines = [line]
            current_len = line_len
            current_segments = [seg]
        else:
            current_lines.append(line)
            current_len += line_len
            current_segments.append(seg)

    if current_lines:
        chunks.append({
            "text": "".join(current_lines),
            "segments": current_segments
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
            parsed = json.load_s_or_eval = json.loads(candidate_json)
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
        "You are an expert viral video editor and content strategist. "
        "Your task is to identify the best vertical short-form clip candidates (Reels, TikTok, Shorts) from the transcript. "
        "Score each candidate from 1 to 10 strictly based on:\n"
        "1. Strong hook in the first 2 seconds that immediately captures curiosity or interest.\n"
        "2. Complete thought with a clear payoff, insight, or punchline.\n"
        "3. High emotion, surprise, or high-value information.\n"
        "4. Standalone sense without needing extra background context.\n"
        f"CRITICAL: Each clip MUST have a duration (end - start) between {min_duration} and {max_duration} seconds.\n"
        "You must respond ONLY with a raw JSON array of objects. Do not include markdown codeblocks or preamble."
    )

    all_raw_candidates: List[Dict[str, Any]] = []

    for i, chunk in enumerate(chunks):
        print(f"[Selector] Processing chunk {i + 1}/{len(chunks)} with LLM ({config['model']})...")
        prompt = (
            f"Here is transcript chunk #{i + 1} with [start_seconds] timestamps:\n\n"
            f"{chunk['text']}\n\n"
            f"Identify {cands_per_chunk} top clip candidates. "
            f"Each candidate MUST be between {min_duration} and {max_duration} seconds long (end - start >= {min_duration}s). "
            "Do NOT return short 5-10 second snippets. Pick complete full scenes.\n"
            "Return ONLY a JSON list with this schema:\n"
            "[\n"
            '  {"start": 12.5, "end": 45.0, "score": 9, "title": "Catchy Title", "hook": "First 2-second hook"}\n'
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
                    score = float(cand.get("score", 5))
                    title = str(cand.get("title", "Untitled Clip")).strip()
                    hook = str(cand.get("hook", "")).strip()

                    snapped_start, snapped_end = snap_to_segments(raw_start, raw_end, segments)

                    all_raw_candidates.append({
                        "start": snapped_start,
                        "end": snapped_end,
                        "score": score,
                        "title": title,
                        "hook": hook
                    })
                except Exception as cand_err:
                    print(f"[Selector] Skipping invalid candidate item: {cand_err}")

        except Exception as e:
            print(f"[Selector] Error in stage 2 (chunk {i+1}): {e}. Skipping chunk.")

    print(f"[Selector] Extracted {len(all_raw_candidates)} total candidate(s) from all chunks.")
    return all_raw_candidates
