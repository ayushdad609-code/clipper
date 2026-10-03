import os
import glob
import json
import random
import subprocess
from typing import List, Dict, Any, Optional

def get_audio_duration(file_path: str) -> float:
    """Returns the duration of an audio or video file in seconds using ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        file_path
    ]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT).decode("utf-8").strip()
        return float(out)
    except Exception as e:
        print(f"[Music] Warning: Could not get duration for {file_path}: {e}")
        return 0.0

def get_available_music_tracks(music_dir: str = "~/clipper/music") -> List[str]:
    """Finds all audio files (.mp3, .m4a, .aac, .wav, .ogg, .flac) in the music directory."""
    expanded_dir = os.path.expanduser(music_dir)
    if not os.path.exists(expanded_dir):
        return []

    supported_extensions = ("*.mp3", "*.m4a", "*.aac", "*.wav", "*.ogg", "*.flac")
    tracks = []
    for ext in supported_extensions:
        tracks.extend(glob.glob(os.path.join(expanded_dir, ext)))
        tracks.extend(glob.glob(os.path.join(expanded_dir, ext.upper())))

    # Filter out empty files
    valid_tracks = [t for t in tracks if os.path.isfile(t) and os.path.getsize(t) > 0]
    return sorted(list(set(valid_tracks)))

def verify_clip_output(file_path: str, expected_min_duration: float = 1.0) -> bool:
    """
    Verifies with ffprobe that:
    1. Video stream is present
    2. Audio stream is present
    3. Duration is valid
    """
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "stream=codec_type,width,height,duration",
        "-show_entries", "format=duration",
        "-of", "json",
        file_path
    ]
    try:
        data = json.loads(subprocess.check_output(cmd, stderr=subprocess.STDOUT).decode("utf-8"))
        streams = data.get("streams", [])
        has_video = any(s.get("codec_type") == "video" for s in streams)
        has_audio = any(s.get("codec_type") == "audio" for s in streams)

        fmt_dur = float(data.get("format", {}).get("duration", 0.0))
        if not (has_video and has_audio and fmt_dur >= expected_min_duration):
            print(f"[Music Verification Failed] {file_path}: has_video={has_video}, has_audio={has_audio}, dur={fmt_dur}s")
            return False
        return True
    except Exception as e:
        print(f"[Music Verification Failed] Could not verify {file_path}: {e}")
        return False

def add_background_music_to_clip(
    clip_path: str,
    music_path: str,
    output_path: str,
    music_volume: float = 0.18
) -> str:
    """
    Overlays background music onto clip:
    - Starts at random offset, loops if shorter than clip (-stream_loop -1).
    - Lowers music volume to ~18%.
    - Ducks music whenever speaker talks using sidechaincompress with clip's own audio as sidechain.
    - Fades music out over the last 1.0s.
    - Does not re-encode video (-c:v copy). Re-encodes audio to aac only.
    - Verifies output with ffprobe.
    """
    clip_dur = get_audio_duration(clip_path)
    if clip_dur <= 0:
        raise ValueError(f"Invalid duration ({clip_dur}s) for clip: {clip_path}")

    music_dur = get_audio_duration(music_path)
    # Pick random start offset
    if music_dur > clip_dur:
        max_offset = max(0.0, music_dur - clip_dur)
        offset = random.uniform(0.0, max_offset)
    else:
        offset = random.uniform(0.0, min(5.0, max(0.1, music_dur * 0.3)))

    fade_start = max(0.0, clip_dur - 1.0)

    # Audio filter graph:
    # 1. Lower music volume & fade out over last 1s
    # 2. Sidechain compression: duck music when voice [0:a] is active
    # 3. Mix voice [0:a] and ducked music [m_ducked]
    filter_complex = (
        f"[1:a]volume={music_volume:.2f},afade=t=out:st={fade_start:.2f}:d=1.0[m_fade];"
        f"[m_fade][0:a]sidechaincompress=threshold=0.08:ratio=4:attack=15:release=250[m_ducked];"
        f"[0:a][m_ducked]amix=inputs=2:duration=first:dropout_transition=0[aout]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", clip_path,
        "-stream_loop", "-1",
        "-ss", f"{offset:.2f}",
        "-i", music_path,
        "-filter_complex", filter_complex,
        "-map", "0:v",
        "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        output_path
    ]

    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"FFmpeg music mixing failed for {clip_path}:\n{res.stderr.strip()}")

    # Verify output stream integrity
    if not verify_clip_output(output_path, expected_min_duration=clip_dur * 0.9):
        raise RuntimeError(f"FFprobe verification failed for {output_path}")

    return output_path

def apply_music_to_clips(
    rendered_clips: List[Dict[str, Any]],
    output_dir: str = "./output",
    music_dir: str = "~/clipper/music",
    music_volume: float = 0.18
) -> List[Dict[str, Any]]:
    """
    Applies background music to all rendered clips if tracks are available.
    """
    tracks = get_available_music_tracks(music_dir)
    if not tracks:
        print(f"[Music] No music tracks found in {music_dir}. Skipping background music step.")
        return rendered_clips

    print(f"\n[Stage 5/5] Adding background music to {len(rendered_clips)} clip(s)...")
    print(f"[Music] Found {len(tracks)} track(s) in {music_dir}:")
    for t in tracks:
        print(f"  🎵 {os.path.basename(t)}")

    for c in rendered_clips:
        clip_file = c.get("clip")
        orig_clip_path = os.path.join(output_dir, clip_file)
        if not os.path.exists(orig_clip_path):
            continue

        base_name, ext = os.path.splitext(clip_file)
        music_clip_file = f"{base_name}_music{ext}"
        music_clip_path = os.path.join(output_dir, music_clip_file)

        selected_track = random.choice(tracks)
        track_name = os.path.basename(selected_track)
        print(f"[Music] Applying track '{track_name}' to {clip_file} -> {music_clip_file}...")

        try:
            add_background_music_to_clip(
                clip_path=orig_clip_path,
                music_path=selected_track,
                output_path=music_clip_path,
                music_volume=music_volume
            )
            c["clip_music"] = music_clip_file
            print(f"[Music] Successfully generated: {music_clip_path}")
        except Exception as e:
            print(f"[Music] Warning: Failed to add music to {clip_file}: {e}")

    # Re-save clips.json with music references
    clips_json_path = os.path.join(output_dir, "clips.json")
    try:
        with open(clips_json_path, "w", encoding="utf-8") as f:
            json.dump(rendered_clips, f, indent=2, ensure_ascii=False)
        print(f"[Music] Updated metadata in {clips_json_path}")
    except Exception as e:
        print(f"[Music] Warning: Could not update {clips_json_path}: {e}")

    return rendered_clips
