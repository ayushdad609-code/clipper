#!/usr/bin/env python3
import sys
import os
import time
import argparse
import traceback

from downloader import resolve_video_path
from transcriber import get_or_create_transcript
from selector import select_clip_candidates
from cutter import cut_all_clips
from music import apply_music_to_clips, get_available_music_tracks

def main():
    parser = argparse.ArgumentParser(
        description="Turn long-form videos into vertical short-form clips (TikTok, Reels, Shorts)."
    )
    parser.add_argument(
        "video_input",
        help="Local video file path or Google Drive link"
    )
    parser.add_argument(
        "--num-clips",
        type=int,
        default=3,
        help="Number of clips to generate (default: 3)"
    )
    parser.add_argument(
        "--min",
        dest="min_duration",
        type=float,
        default=20.0,
        help="Minimum clip duration in seconds (default: 20)"
    )
    parser.add_argument(
        "--max",
        dest="max_duration",
        type=float,
        default=50.0,
        help="Maximum clip duration in seconds (default: 50)"
    )
    parser.add_argument(
        "--output-dir",
        default="./output",
        help="Directory to save output clips and metadata (default: ./output)"
    )
    parser.add_argument(
        "--section",
        default=None,
        help="Optional section to download from YouTube (e.g. 10:00-25:00)"
    )
    parser.add_argument(
        "--whisper-model",
        default="base",
        choices=["tiny", "base", "small"],
        help="faster-whisper model size (default: base)"
    )
    parser.add_argument(
        "--music",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Add background music to clips (default: enabled if music files exist in ~/clipper/music/)"
    )
    parser.add_argument(
        "--music-dir",
        default=os.path.expanduser("~/clipper/music"),
        help="Directory containing background music tracks (default: ~/clipper/music)"
    )
    parser.add_argument(
        "--model",
        default=None,
        help="LLM model name (default: from ~/clipper/.env)"
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="LLM base URL endpoint (default: from ~/clipper/.env)"
    )
    parser.add_argument(
        "--framing",
        choices=["blur", "center"],
        default="blur",
        help="Framing layout: 'blur' (blurred backdrop, full widescreen visible) or 'center' (stable 9:16 center crop) (default: blur)"
    )

    args = parser.parse_args()

    if args.min_duration >= args.max_duration:
        sys.exit(f"[Error] --min ({args.min_duration}s) must be strictly less than --max ({args.max_duration}s).")

    available_music = get_available_music_tracks(args.music_dir)
    if args.music is None:
        music_enabled = len(available_music) > 0
    else:
        music_enabled = args.music

    start_total_time = time.time()
    print("=" * 60)
    print("🎬 CLIPPER: AI Short-Form Video Generator")
    print(f"Target: {args.video_input}")
    print(f"Clips: {args.num_clips} | Duration: {args.min_duration}s - {args.max_duration}s")
    print("=" * 60)

    # ── Stage 1: Resolve Video Input ──────────────────────────────────────────
    print("\n[Stage 1/5] Resolving video input...")
    try:
        video_path = resolve_video_path(args.video_input, section=args.section)
        print(f"Using video file: {video_path}")
    except Exception as e:
        print(f"[Stage 1 Failed] Could not resolve video input: {e}")
        sys.exit(1)

    # ── Stage 2: Audio Extraction & Transcription ─────────────────────────────
    print("\n[Stage 2/5] Audio extraction & faster-whisper transcription...")
    t0 = time.time()
    try:
        segments = get_or_create_transcript(
            video_path,
            temp_dir="./tmp",
            model_size=args.whisper_model
        )
        if not segments:
            raise RuntimeError("Transcription returned 0 segments.")
        t_transcribe = time.time() - t0
        print(f"Transcription complete in {t_transcribe:.1f}s.")
    except Exception as e:
        print(f"[Stage 2 Failed] Transcription error: {e}")
        traceback.print_exc()
        sys.exit(1)

    # ── Stage 3: LLM Candidate Selection ──────────────────────────────────────
    print("\n[Stage 3/5] Selecting top clip candidates using LLM...")
    t0 = time.time()
    try:
        candidates = select_clip_candidates(
            segments=segments,
            min_duration=args.min_duration,
            max_duration=args.max_duration,
            num_clips=args.num_clips,
            model=args.model,
            base_url=args.base_url
        )
        if not candidates:
            raise RuntimeError("No candidate clips were found or returned by the LLM.")
        t_select = time.time() - t0
        print(f"Candidate selection complete in {t_select:.1f}s.")
    except Exception as e:
        print(f"[Stage 3 Failed] Candidate selection error: {e}")
        traceback.print_exc()
        sys.exit(1)

    # ── Stage 4: Cutting, Variations & Subtitles ──────────────────────────────
    print("\n[Stage 4/5] Cutting 9:16 vertical clips and burning subtitles...")
    t0 = time.time()
    try:
        rendered = cut_all_clips(
            video_path=video_path,
            candidates=candidates,
            segments=segments,
            output_dir=args.output_dir,
            min_duration=args.min_duration,
            max_duration=args.max_duration,
            num_clips=args.num_clips,
            framing=args.framing
        )
        t_render = time.time() - t0
        print(f"Rendering complete in {t_render:.1f}s.")
    except Exception as e:
        print(f"[Stage 4 Failed] Rendering error: {e}")
        traceback.print_exc()
        sys.exit(1)

    # ── Stage 5: Background Music Mixing ──────────────────────────────────────
    if music_enabled and available_music:
        t0 = time.time()
        try:
            rendered = apply_music_to_clips(
                rendered_clips=rendered,
                output_dir=args.output_dir,
                music_dir=args.music_dir
            )
            t_music = time.time() - t0
            print(f"Background music mixing complete in {t_music:.1f}s.")
        except Exception as e:
            print(f"[Stage 5 Failed] Music mixing error: {e}")
            traceback.print_exc()

    total_time = time.time() - start_total_time
    print("\n" + "=" * 60)
    print(f"✅ Finished successfully in {total_time:.1f}s!")
    print(f"Generated {len(rendered)} clip(s) in: {os.path.abspath(args.output_dir)}")
    for c in rendered:
        music_info = f" (music: {c['clip_music']})" if "clip_music" in c else ""
        print(f" • {c['clip']}{music_info} ({c['duration']}s) - Score {c['score']}/10: \"{c['title']}\"")
    print("=" * 60)

if __name__ == "__main__":
    main()
