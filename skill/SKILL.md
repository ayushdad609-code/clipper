---
name: clipper
description: >-
  Turn long-form YouTube or Google Drive videos into vertical 9:16 short-form clips with burned-in subtitles, speech-ducked background music, and auto-publish them to YouTube Shorts using Data API v3 on Android Termux / PRoot Ubuntu. Use whenever the user asks to generate clips, add background music, manage YouTube channels, authenticate OAuth tokens, schedule or upload YouTube Shorts, or manage the Clipper pipeline.
---

# Clipper: AI Video Clipping & YouTube Shorts Automation

Clipper is an end-to-end autonomous video processing and publishing engine running on Android Termux (inside an Ubuntu PRoot container).

## Quick Command Cheatsheet

Run these commands using the helper script or from inside the PRoot environment:

```bash
# Helper shortcut from Termux:
~/.agents/skills/clipper/scripts/run_clipper.sh <script> [args...]

# Or manually:
proot-distro login ubuntu -- bash -c "cd /root/clipper && source venv/bin/activate && python3 <script> [args...]"
```

### 1. Generating Short-Form Clips
```bash
# Basic clip generation (3 clips, 20-50s each, with background music)
python clip.py "https://www.youtube.com/watch?v=VIDEO_ID"

# Specify number of clips, duration window, and partial download section
python clip.py "https://www.youtube.com/watch?v=VIDEO_ID" \
  --num-clips 5 \
  --min 25 \
  --max 45 \
  --section 10:00-25:00

# Generate vertical clips with blurred backdrop framing (default, keeps all speakers visible)
python clip.py "https://www.youtube.com/watch?v=VIDEO_ID" --framing blur

# Generate vertical clips with exact center crop
python clip.py "https://www.youtube.com/watch?v=VIDEO_ID" --framing center

# Disable background music
python clip.py "https://www.youtube.com/watch?v=VIDEO_ID" --no-music
```

### 2. YouTube Channel Authentication
```bash
# Authenticate a channel (prints URL, prompts for redirect URL pasteback):
python yt_auth.py ayushdad

# Non-interactive / scriptable code supply:
python yt_auth.py ayushdad --code "http://localhost/?code=4/0A..."
```

### 3. Publishing to YouTube Shorts
```bash
# Dry-run preview (checks clips.json vs posted.json, plans uploads without API calls)
python yt_post.py --dry-run --min-score 7.5

# Upload only top-tier clips (score >= 8.0) as Private for creator review
python yt_post.py --min-score 8.0 --privacy private

# Upload as Public
python yt_post.py --privacy public

# Upload 1 clip per channel as Private
python yt_post.py --privacy private --per-channel 1

# Schedule uploads spaced across 12 hours
python yt_post.py --privacy private --spread-hours 12

# Target a specific channel only
python yt_post.py --channel ayushdad --privacy public

# Automated Background Cron Publisher (every 2 hours)
# Script: ~/clipper/cron_publisher.sh
# Logs: ~/clipper/cron_publisher.log
crontab -l
```

---

## Detailed Technical Guides

- [5-Stage Video Processing Pipeline](./references/pipeline.md)
  Covers video download (`downloader.py`), Whisper transcription (`transcriber.py`), LLM moment selection (`selector.py`), vertical 9:16 cropping & subtitles (`cutter.py`, `subtitles.py`), and FFmpeg `sidechaincompress` music ducking (`music.py`).
- [YouTube Data API v3 & Publishing Engine](./references/youtube_publishing.md)
  Covers OAuth 2.0 flow for headless mobile browser, security permissions, round-robin channel assignment, deduplication with `posted.json`, quota limits, and error recovery.

---

## Key Directories and Files

| Path | Purpose |
| :--- | :--- |
| `~/clipper/clip.py` | Main orchestration pipeline script |
| `~/clipper/music/` | Background music audio files (`.mp3`, `.m4a`) |
| `~/clipper/output/` | Rendered MP4 clips and `clips.json` metadata |
| `~/clipper/posted.json` | Upload history log preventing duplicate uploads |
| `~/clipper/yt/<channel>/` | Channel OAuth tokens and secrets (`chmod 700 / 600`) |
| `/sdcard/Movies/Clipper/` | Synced phone gallery folder for watching clips directly on Android |
