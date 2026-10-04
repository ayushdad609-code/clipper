import os
import json
import random
import subprocess
from typing import List, Dict, Any, Tuple, Optional
from subtitles import generate_ass_subtitles, check_subtitle_filter_support

def get_video_metadata(video_path: str) -> Dict[str, Any]:
    """Retrieves video width, height, duration, and audio presence via ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "stream=codec_type,width,height,duration",
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
        has_audio = False

        for st in streams:
            if st.get("codec_type") == "video":
                width = int(st.get("width", width))
                height = int(st.get("height", height))
                if "duration" in st:
                    try:
                        duration = float(st["duration"])
                    except Exception:
                        pass
            elif st.get("codec_type") == "audio":
                has_audio = True

        if duration <= 0.0 and "duration" in fmt:
            try:
                duration = float(fmt["duration"])
            except Exception:
                duration = 0.0

        return {"width": width, "height": height, "duration": duration, "has_audio": has_audio}
    except Exception as e:
        print(f"[Cutter] Warning: ffprobe failed to get video metadata: {e}. Using defaults.")
        return {"width": 1920, "height": 1080, "duration": 0.0, "has_audio": True}

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

def find_face_detector_model() -> Optional[str]:
    """Finds YuNet face detection ONNX model path if available."""
    candidates = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "face_detection_yunet.onnx"),
        os.path.expanduser("~/clipper/models/face_detection_yunet.onnx"),
        "/root/clipper/models/face_detection_yunet.onnx",
        "/data/data/com.termux/files/home/clipper/models/face_detection_yunet.onnx"
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    return None

def detect_face_framing(
    video_path: str,
    start: float,
    end: float,
    video_width: int,
    video_height: int
) -> Tuple[str, Tuple[int, int, int, int]]:
    """
    Analyzes sample frames across the candidate clip:
    - If a single primary face or close cluster is found, returns ('crop', (crop_w, crop_h, crop_x, crop_y)).
    - If no faces are found, or multiple faces are far apart, returns ('blur', default_crop).
    """
    model_path = find_face_detector_model()
    target_aspect = 9.0 / 16.0
    crop_h = video_height
    crop_w = int(round(video_height * target_aspect))
    crop_w = crop_w - (crop_w % 2)

    default_crop = compute_crop_parameters(video_width, video_height)

    # If video is already vertical or not wider than 9:16
    if video_width <= crop_w:
        return "crop", default_crop

    if not model_path:
        return "blur", default_crop

    try:
        import av
        import cv2

        container = av.open(video_path)
        stream = container.streams.video[0]

        dur = max(0.5, end - start)
        sample_times = [start + dur * p for p in (0.25, 0.5, 0.75)]

        all_centers = []
        too_far_apart = False

        for st in sample_times:
            target_ts = int(st / stream.time_base)
            container.seek(target_ts, any_frame=False, stream=stream)
            frame_img = None
            for packet in container.demux(stream):
                for frame in packet.decode():
                    frame_img = frame.to_ndarray(format="bgr24")
                    break
                if frame_img is not None:
                    break

            if frame_img is None:
                continue

            fh, fw = frame_img.shape[:2]
            detector = cv2.FaceDetectorYN_create(model_path, "", (fw, fh), score_threshold=0.55)
            _, faces = detector.detect(frame_img)

            if faces is not None and len(faces) > 0:
                valid_faces = [f for f in faces if f[2] >= 20 and f[3] >= 20 and f[-1] >= 0.55]
                if len(valid_faces) > 1:
                    min_x = min(f[0] for f in valid_faces)
                    max_x = max(f[0] + f[2] for f in valid_faces)
                    # If faces span more than 75% of the crop width, they are too far apart
                    if (max_x - min_x) > (crop_w * 0.75):
                        too_far_apart = True
                        break
                for f in valid_faces:
                    face_center_x = f[0] + f[2] / 2.0
                    all_centers.append(face_center_x)

        container.close()

        if too_far_apart or not all_centers:
            # Faces far apart or no faces detected -> blurred background layout
            return "blur", default_crop

        # Face detected: center crop around average face center (no random jitter)
        avg_center_x = sum(all_centers) / len(all_centers)
        crop_x = int(round(avg_center_x - crop_w / 2.0))
        max_slack = max(0, video_width - crop_w)
        crop_x = max(0, min(crop_x, max_slack))
        return "crop", (crop_w, crop_h, crop_x, 0)

    except Exception as e:
        print(f"[Cutter] Face detection notice: {e}. Falling back to blurred layout.")
        return "blur", default_crop

def render_clip(
    video_path: str,
    candidate: Dict[str, Any],
    segments: List[Dict[str, Any]],
    output_path: str,
    video_meta: Dict[str, Any],
    framing: str = "auto",
    temp_dir: str = "./tmp"
) -> Dict[str, Any]:
    """
    Cuts and renders a single vertical clip with burned-in subtitles and animated hook headline.
    Framing:
    - 'auto': face-following crop if a face is centered/clustered; blurred-background if no faces or faces far apart.
    - 'blur': uncropped foreground on zoomed/blurred backdrop.
    - 'center': exact 9:16 center crop (zero jitter).
    """
    os.makedirs(temp_dir, exist_ok=True)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # Snapped timestamps with ZERO pre-roll jitter (exact start)
    total_duration = video_meta.get("duration", 0.0)
    actual_start = max(0.0, float(candidate["start"]))
    raw_end = float(candidate["end"])
    if total_duration > 0.0:
        actual_end = min(total_duration, raw_end)
    else:
        actual_end = raw_end

    clip_duration = actual_end - actual_start

    # Subtitles and Hook Banner (first 2.5s)
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

    w_in = video_meta.get("width", 1920)
    h_in = video_meta.get("height", 1080)
    has_audio = video_meta.get("has_audio", True)

    # Determine layout
    if framing == "auto":
        chosen_layout, crop_params = detect_face_framing(video_path, actual_start, actual_end, w_in, h_in)
    elif framing == "center":
        chosen_layout = "crop"
        crop_params = compute_crop_parameters(w_in, h_in)
    else:  # "blur"
        chosen_layout = "blur"
        crop_params = compute_crop_parameters(w_in, h_in)

    crop_w, crop_h, crop_x, crop_y = crop_params

    escaped_ass = ""
    if has_sub_filter and ass_path:
        escaped_ass = ass_path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")

    def build_filter_complex(with_subtitles: bool) -> str:
        if chosen_layout == "blur":
            base_str = (
                "[0:v]split=2[bg_raw][fg_raw];"
                "[bg_raw]scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,boxblur=25:5,eq=brightness=-0.12[bg];"
                "[fg_raw]scale=720:-2[fg];"
                "[bg][fg]overlay=(W-w)/2:(H-h)/2[base]"
            )
        else:
            base_str = f"[0:v]crop={crop_w}:{crop_h}:{crop_x}:{crop_y},scale=720:1280[base]"

        if with_subtitles and escaped_ass:
            return f"{base_str};[base]ass='{escaped_ass}'[v_out]"
        else:
            return f"{base_str};[base]null[v_out]"

    filter_str = build_filter_complex(with_subtitles=bool(escaped_ass))

    def build_cmd(f_complex: str) -> list:
        c = [
            "ffmpeg", "-y",
            "-ss", f"{actual_start:.3f}",
            "-i", video_path,
            "-t", f"{clip_duration:.3f}",
            "-filter_complex", f_complex,
            "-map", "[v_out]",
        ]
        if has_audio:
            c.extend(["-map", "0:a?", "-af", "loudnorm=I=-14:LRA=11:TP=-1.5", "-c:a", "aac", "-b:a", "128k"])
        c.extend([
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "23",
            "-movflags", "+faststart",
            output_path
        ])
        return c

    print(f"[Cutter] Rendering {os.path.basename(output_path)} [{chosen_layout}] ({actual_start:.2f}s -> {actual_end:.2f}s, dur={clip_duration:.2f}s)...")
    res = subprocess.run(build_cmd(filter_str), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    captions_burned = False
    if res.returncode == 0:
        captions_burned = bool(escaped_ass)
    else:
        # If subtitle render failed, log warning and retry without subtitles
        if escaped_ass:
            print(f"[Cutter] Warning: Subtitle rendering failed ({res.stderr.strip()}). Retrying render without subtitles...")
            fallback_filter = build_filter_complex(with_subtitles=False)
            res_fb = subprocess.run(build_cmd(fallback_filter), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if res_fb.returncode != 0:
                raise RuntimeError(f"FFmpeg render failed:\n{res_fb.stderr.strip()}")
            captions_burned = False
        else:
            raise RuntimeError(f"FFmpeg render failed:\n{res.stderr.strip()}")

    # Cleanup ass file
    if ass_path and os.path.exists(ass_path):
        try:
            os.remove(ass_path)
        except Exception:
            pass

    print(f"[Cutter] Successfully rendered: {output_path} (captions: {captions_burned})")

    return {
        "clip": os.path.basename(output_path),
        "start": round(actual_start, 2),
        "end": round(actual_end, 2),
        "duration": round(clip_duration, 2),
        "score": candidate.get("score", 0),
        "title": candidate.get("title", ""),
        "hook": candidate.get("hook", ""),
        "captions": captions_burned,
        "framing": chosen_layout
    }

def cut_all_clips(
    video_path: str,
    candidates: List[Dict[str, Any]],
    segments: List[Dict[str, Any]],
    output_dir: str = "./output",
    min_duration: float = 20.0,
    max_duration: float = 50.0,
    num_clips: int = 3,
    framing: str = "auto"
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
