# Clipper Video Pipeline Reference

## System Architecture

The pipeline runs inside an **Ubuntu PRoot container** on Android (Termux aarch64):
- Workspace: `~/clipper` (mapped to `/root/clipper` inside PRoot)
- Virtual Environment: `~/clipper/venv`
- Launching command from Termux:
  ```bash
  proot-distro login ubuntu -- bash -c "cd /root/clipper && source venv/bin/activate && <command>"
  ```

---

## 5-Stage Processing Pipeline

### Stage 1: Video Ingestion (`downloader.py`)
- Supports local files, YouTube URLs, and Google Drive links.
- YouTube downloads use `yt-dlp` with client emulation (`ios`, `android`, or `web`) to avoid 429 rate limits.
- Supports `--section HH:MM:SS-HH:MM:SS` for partial downloads without downloading full multi-hour videos.
- Sanitizes tracking query parameters before fetching.

### Stage 2: Audio Extraction & Transcription (`transcriber.py`)
- Extracts 16kHz mono WAV via FFmpeg.
- Runs `faster-whisper` on CPU using `int8` quantization.
- Implements PyAV 14+ compatibility patch.
- Caches transcription as `downloads/<basename>_transcript.json` to prevent expensive re-transcription.

### Stage 3: LLM Candidate Selection (`selector.py`)
- Powered by OpenAI-compatible endpoint configured in `~/clipper/.env` (default: `free/gpt-6-luna` via `https://api.apinex.bond/v1`, or NVIDIA NIM).
- Supports runtime model overrides via `--model <name>` and `--base-url <url>` flags in `clip.py`.
- Selects top moments scored 1-10 with viral hooks, titles, and segment timestamps.
- Snaps start/end timestamps to sentence and word boundaries.

### Stage 4: Vertical 9:16 Video Cutting & Subtitles (`cutter.py`, `subtitles.py`)
- Crops 16:9 landscape to 9:16 vertical (720x1280 resolution).
- Applies slight horizontal framing variations and trim jitter (0.2s-0.5s) to avoid algorithmic duplication.
- Generates ASS subtitles (bold white text, black border, centered in lower third, 3-5 words per cue).
- Hard-burns subtitles with FFmpeg `ass=...` filter.

### Stage 5: Speech-Ducked Background Music (`music.py`)
- Music tracks stored in `~/clipper/music/` (`.mp3`, `.m4a`).
- Selects random track, loops with `-stream_loop -1`, and starts at random offset.
- Lowers music volume to ~18% and ducks music whenever speech occurs using `sidechaincompress`:
  ```text
  [1:a]volume=0.18,afade=t=out:st={fade_start}:d=1.0[m_fade];
  [m_fade][0:a]sidechaincompress=threshold=0.08:ratio=4:attack=15:release=250[m_ducked];
  [0:a][m_ducked]amix=inputs=2:duration=first:dropout_transition=0[aout]
  ```
- Fades music out over the last 1.0 second.
- Video stream copied without re-encoding (`-c:v copy`). Audio re-encoded to `aac -b:a 192k`.
- Preserves original `clip_XX.mp4` and outputs `clip_XX_music.mp4`.
