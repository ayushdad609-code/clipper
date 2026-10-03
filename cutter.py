import os
import json
import random
import subprocess
from typing import List, Dict, Any, Tuple
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
    num_clips: int
) -> List[Dict[str, Any]]:
    """
    1. Drops any outside --min/--max
    2. Removes overlaps, keeping the higher score
    3. Sorts by score descending
    4. Keeps top N
    """
    valid = []
    for cand in candidates:
        dur = cand["end"] - cand["start"]
        # If slightly below min_duration (e.g. 14s-19s), extend end
        if dur < min_duration and dur >= min_duration - 7.0:
            cand["end"] = cand["start"] + min_duration
            dur = min_duration
        # If slightly above max_duration (e.g. 51s-57s), trim end
        elif dur > max_duration and dur <= max_duration + 7.0:
            cand["end"] = cand["start"] + max_duration
            dur = max_duration

        if min_duration <= dur <= max_duration:
            valid.append(cand)
        else:
            print(f"[Cutter] Dropping candidate [{cand['start']}-{cand['end']}] (duration {dur:.1f}s outside [{min_duration}-{max_duration}]s).")

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
    Calculates 9:16 vertical crop parameters with a small random horizontal offset.
    Returns (crop_w, crop_h, crop_x, crop_y).
    """
    target_aspect = 9.0 / 16.0
    aspect = video_width / video_height

    if aspect >= target_aspect:
        # Wider than 9:16 (e.g. landscape 16:9 or 4:3)
        crop_h = video_height
        crop_w = int(round(video_height * target_aspect))
        # Ensure even dimension
        crop_w = crop_w - (crop_w % 2)

        slack = max(0, video_width - crop_w)
        center_x = slack // 2
        
        # Small random horizontal crop offset (variation)
        max_offset = min(int(slack * 0.15), 40)
        offset = random.randint(-max_offset, max_offset) if max_offset > 0 else 0
        crop_x = max(0, min(slack, center_x + offset))
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
    temp_dir: str = "./tmp"
) -> Dict[str, Any]:
    """
    Cuts and renders a single vertical clip with variation and burned-in subtitles.
    """
    os.makedirs(temp_dir, exist_ok=True)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # 1. Random trim jitter (0.2s - 0.5s) at start and end
    jitter_start = random.uniform(0.2, 0.5)
    jitter_end = random.uniform(0.2, 0.5)

    total_duration = video_meta.get("duration", 0.0)
    raw_start = candidate["start"]
    raw_end = candidate["end"]

    actual_start = max(0.0, raw_start - jitter_start)
    if total_duration > 0.0:
        actual_end = min(total_duration, raw_end + jitter_end)
    else:
        actual_end = raw_end + jitter_end
    
    clip_duration = actual_end - actual_start

    # 2. Crop & Scale parameters (720x1280, 9:16)
    w_in = video_meta["width"]
    h_in = video_meta["height"]
    crop_w, crop_h, crop_x, crop_y = compute_crop_parameters(w_in, h_in)

    vf_filters = [
        f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y}",
        "scale=720:1280"
    ]

    # 3. Subtitles
    has_sub_filter = check_subtitle_filter_support()
    ass_path = None

    if has_sub_filter:
        base_name = os.path.splitext(os.path.basename(output_path))[0]
        ass_path = os.path.join(temp_dir, f"{base_name}.ass")
        generate_ass_subtitles(
            clip_start=actual_start,
            clip_end=actual_end,
            segments=segments,
            output_ass_path=ass_path
        )
        # Escape path for ffmpeg filter: escape colons and backslashes
        escaped_ass = ass_path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
        vf_filters.append(f"ass='{escaped_ass}'")
    else:
        print("[Subtitles] NOTICE: ffmpeg does not support 'subtitles' or 'ass' filters. Skipping caption burn-in.")

    vf_str = ",".join(vf_filters)

    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{actual_start:.3f}",
        "-i", video_path,
        "-t", f"{clip_duration:.3f}",
        "-vf", vf_str,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        output_path
    ]

    print(f"[Cutter] Rendering {os.path.basename(output_path)} ({actual_start:.2f}s -> {actual_end:.2f}s, dur={clip_duration:.2f}s)...")
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    if res.returncode != 0:
        # If burning subtitles failed, retry without subtitles
        if has_sub_filter and ass_path:
            print(f"[Cutter] Warning: ffmpeg failed with subtitles filter. Retrying render without subtitles...")
            fallback_vf = f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y},scale=720:1280"
            cmd_fallback = [
                "ffmpeg", "-y",
                "-ss", f"{actual_start:.3f}",
                "-i", video_path,
                "-t", f"{clip_duration:.3f}",
                "-vf", fallback_vf,
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "23",
                "-c:a", "aac",
                "-b:a", "128k",
                "-movflags", "+faststart",
                output_path
            ]
            res_fb = subprocess.run(cmd_fallback, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if res_fb.returncode != 0:
                raise RuntimeError(f"FFmpeg render failed:\n{res_fb.stderr.strip()}")
        else:
            raise RuntimeError(f"FFmpeg render failed:\n{res.stderr.strip()}")

    # Cleanup ass file
    if ass_path and os.path.exists(ass_path):
        os.remove(ass_path)

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
    num_clips: int = 3
) -> List[Dict[str, Any]]:
    """
    Filters, ranks, cuts, and exports all clips along with clips.json.
    """
    selected = filter_and_rank_candidates(candidates, min_duration, max_duration, num_clips)
    if not selected:
        raise RuntimeError("No valid clip candidates met the criteria.")

    video_meta = get_video_metadata(video_path)
    os.makedirs(output_dir, exist_ok=True)

    rendered_clips = []
    for idx, cand in enumerate(selected, start=1):
        out_name = f"clip_{idx:02d}.mp4"
        out_path = os.path.join(output_dir, out_name)
        clip_info = render_clip(video_path, cand, segments, out_path, video_meta)
        rendered_clips.append(clip_info)

    # Write clips.json
    clips_json_path = os.path.join(output_dir, "clips.json")
    with open(clips_json_path, "w", encoding="utf-8") as f:
        json.dump(rendered_clips, f, indent=2, ensure_ascii=False)
    print(f"[Cutter] Exported metadata to {clips_json_path}")

    return rendered_clips
