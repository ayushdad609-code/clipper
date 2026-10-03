import os
import json
import random
import subprocess
from typing import List, Dict, Any, Tuple, Optional
from subtitles import generate_ass_subtitles, check_subtitle_filter_support

def get_video_metadata(video_path: str) -> Dict[str, Any]:
    """Retrieves video width, height, and duration via ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,duration",
        "-show_entries", "format=duration",
        "-of", "json",
        video_path
    ]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT).decode("utf-8")
        data = json.loads(out)
        streams = data.get("streams", [])
        fmt = data.get("format", {})
        
        width = 1920
        height = 1080
        duration = 0.0

        if streams:
            width = int(streams[0].get("width", 1920))
            height = int(streams[0].get("height", 1080))
            if "duration" in streams[0]:
                try:
                    duration = float(streams[0]["duration"])
                except Exception:
                    pass

        if duration <= 0.0 and "duration" in fmt:
            try:
                duration = float(fmt["duration"])
            except Exception:
                duration = 0.0

        return {"width": width, "height": height, "duration": duration}
    except Exception as e:
        print(f"[Cutter] Warning: ffprobe failed to get video metadata: {e}. Using defaults.")
        return {"width": 1920, "height": 1080, "duration": 0.0}

def filter_and_rank_candidates(
    candidates: List[Dict[str, Any]],
    min_duration: float,
    max_duration: float,
    num_clips: int,
    segments: Optional[List[Dict[str, Any]]] = None
) -> List[Dict[str, Any]]:
    """
    1. Re-snaps candidates strictly to speech segment boundaries if duration adjustment is needed.
    2. Drops candidates that cannot be cleanly snapped within [min_duration, max_duration].
    3. Removes overlaps, prioritizing higher score.
    4. Keeps top N.
    """
    valid = []
    for cand in candidates:
        c_start = cand["start"]
        c_end = cand["end"]
        dur = c_end - c_start

        # If slightly below min_duration, extend end strictly to the next speech segment boundary
        if dur < min_duration and segments:
            matching_ends = [
                s["end"] for s in segments 
                if s["end"] > c_start and min_duration <= (s["end"] - c_start) <= max_duration
            ]
            if matching_ends:
                cand["end"] = round(matching_ends[0], 2)
                dur = cand["end"] - c_start

        # If slightly above max_duration, trim end strictly to the latest segment boundary within max_duration
        elif dur > max_duration and segments:
            matching_ends = [
                s["end"] for s in segments 
                if s["end"] > c_start and min_duration <= (s["end"] - c_start) <= max_duration
            ]
            if matching_ends:
                cand["end"] = round(matching_ends[-1], 2)
                dur = cand["end"] - c_start

        if min_duration <= dur <= max_duration:
            cand["duration"] = round(dur, 2)
            valid.append(cand)
        else:
            print(f"[Cutter] Dropping candidate [{c_start:.1f}-{c_end:.1f}] (duration {dur:.1f}s outside [{min_duration}-{max_duration}]s without clean sentence boundary).")

    # Sort candidates by score descending
    valid.sort(key=lambda c: c.get("score", 0), reverse=True)

    selected: List[Dict[str, Any]] = []
    for cand in valid:
        c_start = cand["start"]
        c_end = cand["end"]
        # Check overlap with any already selected candidate
        overlap = False
        for s in selected:
            # Overlap condition: max(start1, start2) < min(end1, end2)
            if max(c_start, s["start"]) < min(c_end, s["end"]):
                overlap = True
                break
        if not overlap:
            selected.append(cand)
            if len(selected) >= num_clips:
                break

    print(f"[Cutter] Selected top {len(selected)} non-overlapping candidate(s) for rendering.")
    return selected

def compute_crop_parameters(
    video_width: int,
    video_height: int
) -> Tuple[int, int, int, int]:
    """
    Calculates exact 9:16 center crop parameters with zero jitter.
    Returns (crop_w, crop_h, crop_x, crop_y).
    """
    target_aspect = 9.0 / 16.0
    aspect = video_width / video_height

    if aspect >= target_aspect:
        # Wider than 9:16 (e.g. landscape 16:9 or 4:3)
        crop_h = video_height
        crop_w = int(round(video_height * target_aspect))
        crop_w = crop_w - (crop_w % 2)
        slack = max(0, video_width - crop_w)
        crop_x = slack // 2  # Exact stable center crop (no jitter)
        crop_y = 0
    else:
        # Taller than 9:16
        crop_w = video_width
        crop_h = int(round(video_width / target_aspect))
        crop_h = crop_h - (crop_h % 2)
        slack_y = max(0, video_height - crop_h)
        crop_x = 0
        crop_y = slack_y // 2

    return crop_w, crop_h, crop_x, crop_y

def render_clip(
    video_path: str,
    candidate: Dict[str, Any],
    segments: List[Dict[str, Any]],
    output_path: str,
    video_meta: Dict[str, Any],
    framing: str = "blur",
    temp_dir: str = "./tmp"
) -> Dict[str, Any]:
    """
    Cuts and renders a single vertical clip with burned-in subtitles and animated hook headline.
    Framing:
    - 'blur': 16:9 uncropped foreground centered with zoomed/blurred background (best for multi-speaker/wide scenes).
    - 'center': 9:16 stable center crop (zero jitter).
    """
    os.makedirs(temp_dir, exist_ok=True)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # Snapped timestamps with ZERO jitter to prevent opening on previous sentence tails
    total_duration = video_meta.get("duration", 0.0)
    actual_start = max(0.0, float(candidate["start"]))
    raw_end = float(candidate["end"])
    if total_duration > 0.0:
        actual_end = min(total_duration, raw_end)
    else:
        actual_end = raw_end

    clip_duration = actual_end - actual_start

    # Subtitles and Hook Banner
    has_sub_filter = check_subtitle_filter_support()
    ass_path = None

    if has_sub_filter:
        base_name = os.path.splitext(os.path.basename(output_path))[0]
        ass_path = os.path.join(temp_dir, f"{base_name}.ass")
        generate_ass_subtitles(
            clip_start=actual_start,
            clip_end=actual_end,
            segments=segments,
            output_ass_path=ass_path,
            hook_title=candidate.get("hook")
        )

    # Filtergraph construction based on framing
    w_in = video_meta.get("width", 1920)
    h_in = video_meta.get("height", 1080)

    escaped_ass = ""
    if has_sub_filter and ass_path:
        escaped_ass = ass_path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")

    if framing == "blur":
        # Blurred backdrop: preserves 100% of widescreen video, no speaker cut off
        filter_str = (
            "[0:v]split=2[bg_raw][fg_raw];"
            "[bg_raw]scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,boxblur=25:5,eq=brightness=-0.12[bg];"
            "[fg_raw]scale=720:-2[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2[base]"
        )
        if escaped_ass:
            filter_str += f";[base]ass='{escaped_ass}'[v_out]"
        else:
            filter_str += ";[base]null[v_out]"
    else:
        # Exact stable center crop
        crop_w, crop_h, crop_x, crop_y = compute_crop_parameters(w_in, h_in)
        filter_str = f"[0:v]crop={crop_w}:{crop_h}:{crop_x}:{crop_y},scale=720:1280[base]"
        if escaped_ass:
            filter_str += f";[base]ass='{escaped_ass}'[v_out]"
        else:
            filter_str += ";[base]null[v_out]"

    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{actual_start:.3f}",
        "-i", video_path,
        "-t", f"{clip_duration:.3f}",
        "-filter_complex", filter_str,
        "-map", "[v_out]",
        "-map", "0:a?",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        output_path
    ]

    print(f"[Cutter] Rendering {os.path.basename(output_path)} [{framing}] ({actual_start:.2f}s -> {actual_end:.2f}s, dur={clip_duration:.2f}s)...")
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    # Fail loudly on FFmpeg errors (do not silently drop subtitles)
    if res.returncode != 0:
        raise RuntimeError(f"FFmpeg render failed with code {res.returncode}:\n{res.stderr.strip()}")

    # Cleanup ass file
    if ass_path and os.path.exists(ass_path):
        try:
            os.remove(ass_path)
        except Exception:
            pass

    print(f"[Cutter] Successfully rendered: {output_path}")

    return {
        "clip": os.path.basename(output_path),
        "start": round(actual_start, 2),
        "end": round(actual_end, 2),
        "duration": round(clip_duration, 2),
        "score": candidate.get("score", 0),
        "title": candidate.get("title", ""),
        "hook": candidate.get("hook", "")
    }

def cut_all_clips(
    video_path: str,
    candidates: List[Dict[str, Any]],
    segments: List[Dict[str, Any]],
    output_dir: str = "./output",
    min_duration: float = 20.0,
    max_duration: float = 50.0,
    num_clips: int = 3,
    framing: str = "blur"
) -> List[Dict[str, Any]]:
    """
    Filters, ranks, cuts, and exports all clips along with clips.json.
    """
    selected = filter_and_rank_candidates(
        candidates=candidates,
        min_duration=min_duration,
        max_duration=max_duration,
        num_clips=num_clips,
        segments=segments
    )
    if not selected:
        raise RuntimeError("No valid clip candidates met the criteria.")

    video_meta = get_video_metadata(video_path)
    os.makedirs(output_dir, exist_ok=True)

    rendered_clips = []
    for idx, cand in enumerate(selected, start=1):
        out_name = f"clip_{idx:02d}.mp4"
        out_path = os.path.join(output_dir, out_name)
        clip_info = render_clip(
            video_path=video_path,
            candidate=cand,
            segments=segments,
            output_path=out_path,
            video_meta=video_meta,
            framing=framing
        )
        rendered_clips.append(clip_info)

    # Write clips.json
    clips_json_path = os.path.join(output_dir, "clips.json")
    with open(clips_json_path, "w", encoding="utf-8") as f:
        json.dump(rendered_clips, f, indent=2, ensure_ascii=False)
    print(f"[Cutter] Exported metadata to {clips_json_path}")

    return rendered_clips
