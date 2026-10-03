import os
import subprocess
from typing import List, Dict, Any, Optional

def check_subtitle_filter_support() -> bool:
    """Checks if ffmpeg supports subtitle burning via 'ass' or 'subtitles' filter."""
    try:
        out = subprocess.check_output(["ffmpeg", "-filters"], stderr=subprocess.STDOUT).decode("utf-8")
        has_subtitles = "subtitles" in out or " ass " in out
        return has_subtitles
    except Exception as e:
        print(f"[Subtitles] Warning: Could not query ffmpeg filters: {e}")
        return False

def format_ass_time(seconds: float) -> str:
    """Formats seconds into ASS timestamp: H:MM:SS.cc"""
    if seconds < 0:
        seconds = 0.0
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    centis = int(round((seconds - int(seconds)) * 100))
    if centis >= 100:
        centis = 99
    return f"{hrs}:{mins:02d}:{secs:02d}.{centis:02d}"

def generate_ass_subtitles(
    clip_start: float,
    clip_end: float,
    segments: List[Dict[str, Any]],
    output_ass_path: str,
    font_name: str = "DejaVu Sans",
    font_size: int = 42,
    hook_title: Optional[str] = None
) -> str:
    """
    Builds an .ass subtitle file for the clip:
    - Large bold white text, black outline centered in lower third (3-5 words at a time)
    - Animated top hook banner (first 3.5s) to hook viewers immediately
    """
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 720
PlayResY: 1280
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,2,2,30,30,180,1
Style: HookBanner,{font_name},44,&H0000FFFF,&H000000FF,&H00000000,&H90000000,-1,0,0,0,100,100,0,0,1,4,3,8,30,30,120,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []

    # Insert top hook headline for the first 3.5 seconds
    if hook_title and hook_title.strip():
        clean_hook = hook_title.strip().replace("\n", " ")
        if len(clean_hook) > 50:
            clean_hook = clean_hook[:47] + "..."
        events.append(f"Dialogue: 1,0:00:00.00,0:00:03.50,HookBanner,,0,0,0,,{{\\fad(150,350)}}{clean_hook}")

    for seg in segments:
        seg_start = seg.get("start", 0.0)
        seg_end = seg.get("end", 0.0)

        # Skip segments outside the clip
        if seg_end <= clip_start or seg_start >= clip_end:
            continue

        words = seg.get("words", [])
        if words:
            # Group existing word timestamps into 3-5 words
            i = 0
            while i < len(words):
                # Pick 4 words (or remaining 3 to 5)
                remaining = len(words) - i
                if remaining <= 5:
                    chunk_words = words[i:]
                    i = len(words)
                else:
                    chunk_words = words[i:i + 4]
                    i += 4

                w_start = chunk_words[0]["start"]
                w_end = chunk_words[-1]["end"]

                # Relativize to clip start
                rel_start = max(0.0, w_start - clip_start)
                rel_end = max(rel_start + 0.2, w_end - clip_start)
                clip_dur = clip_end - clip_start
                if rel_start >= clip_dur:
                    continue
                rel_end = min(clip_dur, rel_end)

                text = " ".join(w["word"].strip() for w in chunk_words if w["word"].strip())
                if text:
                    start_str = format_ass_time(rel_start)
                    end_str = format_ass_time(rel_end)
                    events.append(f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{text}")
        else:
            # Fallback when word-level timestamps aren't present: split by words and interpolate
            seg_text = seg.get("text", "").strip()
            tokens = seg_text.split()
            if not tokens:
                continue

            rel_seg_start = max(0.0, seg_start - clip_start)
            rel_seg_end = max(rel_seg_start + 0.3, seg_end - clip_start)
            total_dur = rel_seg_end - rel_seg_start

            # Chunk into 4 words
            chunk_size = 4
            word_chunks = [tokens[j:j + chunk_size] for j in range(0, len(tokens), chunk_size)]
            dur_per_chunk = total_dur / max(1, len(word_chunks))

            for k, w_chunk in enumerate(word_chunks):
                chunk_start = rel_seg_start + k * dur_per_chunk
                chunk_end = chunk_start + dur_per_chunk
                text = " ".join(w_chunk)
                start_str = format_ass_time(chunk_start)
                end_str = format_ass_time(chunk_end)
                events.append(f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{text}")

    with open(output_ass_path, "w", encoding="utf-8") as f:
        f.write(header)
        f.write("\n".join(events))
        f.write("\n")

    return output_ass_path
