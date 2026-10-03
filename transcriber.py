import os
import json
import subprocess
import shutil
from typing import List, Dict, Any, Optional

def get_audio_duration(video_or_audio_path: str) -> float:
    """Gets duration in seconds using ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_or_audio_path
    ]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT).decode("utf-8").strip()
        return float(out)
    except Exception as e:
        print(f"[Transcriber] Warning: Could not determine duration using ffprobe: {e}")
        return 0.0

def extract_audio(video_path: str, output_wav_path: str) -> str:
    """
    Extracts mono 16kHz WAV audio from the video file using ffmpeg.
    """
    print(f"[Transcriber] Extracting mono 16kHz audio from {os.path.basename(video_path)}...")
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vn",
        "-acodec", "pcm_s16le",
        "-ac", "1",
        "-ar", "16000",
        output_wav_path
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        raise RuntimeError(
            f"Failed to extract audio from video using ffmpeg:\n{res.stderr.strip()}"
        )
    if not os.path.exists(output_wav_path) or os.path.getsize(output_wav_path) == 0:
        raise RuntimeError(f"Extracted audio file is empty or missing: {output_wav_path}")
    print(f"[Transcriber] Audio extracted successfully: {output_wav_path}")
    return output_wav_path

def get_transcript_cache_path(video_path: str) -> str:
    """Generates the path for caching the transcript next to the video."""
    video_dir = os.path.dirname(video_path)
    base_name = os.path.splitext(os.path.basename(video_path))[0]
    candidate = os.path.join(video_dir, f"{base_name}_transcript.json")
    # If the video directory is not writable, fall back to current working directory
    if os.access(video_dir, os.W_OK):
        return candidate
    return os.path.join(os.getcwd(), f"{base_name}_transcript.json")

def load_cached_transcript(cache_path: str) -> Optional[List[Dict[str, Any]]]:
    """Loads cached transcript JSON if valid."""
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list) and len(data) > 0 and "start" in data[0] and "text" in data[0]:
                print(f"[Transcriber] Found valid cached transcript at: {cache_path}")
                return data
        except Exception as e:
            print(f"[Transcriber] Failed to read cache file {cache_path}: {e}. Re-transcribing.")
    return None

def save_transcript_cache(cache_path: str, segments: List[Dict[str, Any]]):
    """Saves transcript JSON to cache path."""
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(segments, f, indent=2, ensure_ascii=False)
        print(f"[Transcriber] Transcript cached at: {cache_path}")
    except Exception as e:
        print(f"[Transcriber] Warning: Could not write transcript cache to {cache_path}: {e}")

def transcribe_audio(
    audio_path: str,
    model_size: str = "base",
    chunk_length_seconds: int = 300
) -> List[Dict[str, Any]]:
    """
    Transcribes audio using faster-whisper on CPU with int8 quantization.
    For long audio, processes in chunks to keep memory usage low.
    """
    try:
        import av
        import faster_whisper.audio
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise RuntimeError(
            "faster-whisper is not installed or failed to import on this device.\n"
            "If installation fails on your aarch64 environment, consider using an external Whisper API fallback "
            "(e.g., OpenAI /v1/audio/transcriptions)."
        ) from e

    print(f"[Transcriber] Initializing faster-whisper (model='{model_size}', device='cpu', compute_type='int8')...")
    try:
        model = WhisperModel(model_size, device="cpu", compute_type="int8", cpu_threads=4)
    except Exception as e:
        raise RuntimeError(
            f"Failed to load faster-whisper model '{model_size}': {e}.\n"
            "On low-memory aarch64 devices, faster-whisper may run out of memory or fail to initialize.\n"
            "Suggestion: Try '--whisper-model tiny' or switch to an external Whisper API endpoint."
        ) from e

    duration = get_audio_duration(audio_path)
    print(f"[Transcriber] Total audio duration: {duration:.1f}s (~{duration/60:.1f} mins)")

    all_segments: List[Dict[str, Any]] = []

    # If audio is relatively short, transcribe directly with streaming generator
    if duration <= chunk_length_seconds:
        print("[Transcriber] Transcribing audio with faster-whisper...")
        try:
            segments, info = model.transcribe(
                audio_path,
                beam_size=1,
                word_timestamps=True,
                vad_filter=True
            )
            for seg in segments:
                words_list = []
                if seg.words:
                    for w in seg.words:
                        words_list.append({"word": w.word, "start": round(w.start, 3), "end": round(w.end, 3)})
                all_segments.append({
                    "start": round(seg.start, 3),
                    "end": round(seg.end, 3),
                    "text": seg.text.strip(),
                    "words": words_list
                })
        except Exception as e:
            raise RuntimeError(
                f"faster-whisper transcription error: {e}.\n"
                "If faster-whisper is too slow or encounters memory errors, consider using an API fallback."
            ) from e
    else:
        # Long audio: slice into chunks of chunk_length_seconds to prevent memory accumulation
        print(f"[Transcriber] Long audio detected. Processing in {chunk_length_seconds}s chunks to keep RAM low...")
        temp_dir = os.path.join(os.path.dirname(audio_path), "_whisper_chunks")
        os.makedirs(temp_dir, exist_ok=True)
        try:
            current_start = 0.0
            chunk_idx = 0
            while current_start < duration:
                chunk_file = os.path.join(temp_dir, f"chunk_{chunk_idx:04d}.wav")
                print(f"[Transcriber] Transcribing chunk {chunk_idx + 1} ({current_start:.0f}s - {min(duration, current_start + chunk_length_seconds):.0f}s)...")
                # Extract chunk
                cmd = [
                    "ffmpeg", "-y",
                    "-ss", str(current_start),
                    "-t", str(chunk_length_seconds),
                    "-i", audio_path,
                    "-c", "copy",
                    chunk_file
                ]
                subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)

                segments, _ = model.transcribe(
                    chunk_file,
                    beam_size=1,
                    word_timestamps=True,
                    vad_filter=True
                )
                for seg in segments:
                    abs_start = round(current_start + seg.start, 3)
                    abs_end = round(current_start + seg.end, 3)
                    words_list = []
                    if seg.words:
                        for w in seg.words:
                            words_list.append({
                                "word": w.word,
                                "start": round(current_start + w.start, 3),
                                "end": round(current_start + w.end, 3)
                            })
                    all_segments.append({
                        "start": abs_start,
                        "end": abs_end,
                        "text": seg.text.strip(),
                        "words": words_list
                    })

                # Remove processed chunk file immediately to save disk
                if os.path.exists(chunk_file):
                    os.remove(chunk_file)

                current_start += chunk_length_seconds
                chunk_idx += 1
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    print(f"[Transcriber] Transcription finished. Total segments: {len(all_segments)}")
    return all_segments

def get_or_create_transcript(
    video_path: str,
    temp_dir: str = "./tmp",
    model_size: str = "base"
) -> List[Dict[str, Any]]:
    """
    High-level transcription function with caching.
    """
    cache_path = get_transcript_cache_path(video_path)
    cached = load_cached_transcript(cache_path)
    if cached is not None:
        print(f"[Transcriber] Skipping audio extraction & transcription (cache hit).")
        return cached

    os.makedirs(temp_dir, exist_ok=True)
    base_name = os.path.splitext(os.path.basename(video_path))[0]
    wav_path = os.path.join(temp_dir, f"{base_name}_audio.wav")

    try:
        extract_audio(video_path, wav_path)
        segments = transcribe_audio(wav_path, model_size=model_size)
        save_transcript_cache(cache_path, segments)
        return segments
    finally:
        if os.path.exists(wav_path):
            os.remove(wav_path)
