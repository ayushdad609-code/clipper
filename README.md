# Clipper 🎬

An autonomous AI engine for turning long-form videos into vertical (9:16) short-form clips (YouTube Shorts, TikTok, Instagram Reels) with burned-in dynamic subtitles, speech-ducked background music, and multi-channel YouTube auto-publishing. Built and optimized for low-resource environments (including ARM64 Android Termux / PRoot Ubuntu).

---

## 🌟 Key Features

1. **Smart Video Ingestion**:
   - Downloads from YouTube (`youtube.com`, `youtu.be`, `shorts`, `live`) using `yt-dlp` with Node.js runtime and Android player client emulation to bypass bot challenges.
   - Downloads directly from Google Drive links using `gdown`.
   - Supports any local video file path.
   - Supports `--section HH:MM:SS-HH:MM:SS` for partial downloads without downloading full multi-hour videos.

2. **Accurate Speech Transcription**:
   - Runs `faster-whisper` (`base`, `tiny`, `small`) with `int8` quantization on CPU.
   - Chunked streaming transcription to keep RAM usage minimal on mobile devices.
   - Automatic caching of word-level JSON transcripts (`downloads/*_transcript.json`).

3. **AI Moment Selection**:
   - Compatible with any OpenAI-standard endpoint (OpenAI, Apinex, NVIDIA NIM, Groq, Ollama, OpenRouter).
   - Multi-chunk transcript evaluation scoring hooks (first 2s), emotional payoffs, and punchlines (1–10).
   - Snaps timestamps to natural sentence and word boundaries.
   - Runtime model switching via `--model` flag.

4. **Vertical 9:16 Video Cutting & Subtitles**:
   - Converts 16:9 landscape to 9:16 vertical (`720x1280`) using `libx264` (`preset veryfast`).
   - Introduces subtle framing offsets and trim jitter to prevent algorithmic duplication.
   - Hard-burns animated ASS dynamic subtitles centered in the lower-third.

5. **Speech-Ducked Background Music**:
   - Automatically loops background tracks from `music/`.
   - Uses FFmpeg `sidechaincompress` audio ducking to lower music volume (~18%) whenever speech occurs, with smooth 1-second fade-outs.

6. **Multi-Channel YouTube Shorts Auto-Publisher**:
   - Headless OAuth 2.0 authentication for mobile environments (`yt_auth.py`).
   - Supports multiple channels simultaneously with round-robin load balancing (`yt_post.py`).
   - Automatic quota protection, exponential backoff, and upload limit handling.
   - Comprehensive upload history logging (`posted.json`) to prevent duplicate posts.
   - Background crontab automation (`cron_publisher.sh`).

---

## 🚀 Quick Start

### 1. Installation

```bash
# Clone the repository
git clone https://github.com/ayushdad609-code/clipper.git
cd clipper

# Setup Python virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configuration (`.env`)

Copy `.env.example` to `.env` and add your LLM credentials:

```bash
cp .env.example .env
```

```ini
OPENAI_BASE_URL=https://api.apinex.bond/v1
OPENAI_MODEL=free/gpt-6-luna
OPENAI_API_KEY=your_api_key_here
```

### 3. Generate Clips

```bash
# Generate 5 vertical clips (20s to 50s each) with background music
python clip.py "https://www.youtube.com/watch?v=VIDEO_ID" --num-clips 5 --min 20 --max 50

# Specify an alternate LLM on the fly
python clip.py "https://youtu.be/VIDEO_ID" --num-clips 3 --model meta/llama-3.3-70b-instruct
```

### 4. Authenticate YouTube Channels

```bash
# Authenticate a channel (generates OAuth link):
python yt_auth.py my_channel

# Non-interactive / scripted token exchange:
python yt_auth.py my_channel --code "http://localhost/?code=4/0A..."
```

### 5. Publish to YouTube Shorts

```bash
# Dry run preview (verifies queue without uploading):
python yt_post.py --dry-run

# Publish as Public across all authenticated channels (round-robin):
python yt_post.py --privacy public

# Target a specific channel only:
python yt_post.py --channel my_channel --privacy public
```

---

## ⏰ Automated Cron Publishing

A ready-to-use cron runner script is provided at `cron_publisher.sh`. To run auto-publishing every 2 hours:

```bash
crontab -e
# Add:
0 */2 * * * /path/to/clipper/cron_publisher.sh
```

---

## 📄 License

MIT License. See [LICENSE](LICENSE) for details.
