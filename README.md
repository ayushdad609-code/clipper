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

3. **AI Moment Selection & Calibrated Scoring**:
   - Compatible with any OpenAI-standard endpoint (OpenAI, Apinex, NVIDIA NIM, Groq, Ollama, OpenRouter).
   - 60-second sliding window overlap across transcript chunks so moments spanning chunk edges are never lost.
   - Strict 3-tier scoring calibration (hook 40%, story 30%, payoff 30%) with cross-chunk deduplication.
   - Generates punchy 3–8 word hook headlines for visual viewer retention.
   - Snaps timestamps strictly to sentence and word boundaries, with clean re-snapping during duration adjustments.

4. **Vertical 9:16 Video Cutting & Subtitles**:
   - Framing options:
     - `--framing blur` (default): 16:9 uncropped foreground centered on a zoomed, blurred backdrop. Multiple speakers and wide action remain 100% visible.
     - `--framing center`: Stable 9:16 center crop.
   - Zero-jitter cutting guarantees clips open crisply at sentence starts without trailing syllables from previous sentences.
   - Hard-burns animated top hook headline banner (first 3.5s) and dynamic ASS subtitles centered in the lower third.
   - Fail-loud subtitle rendering ensures missing fonts or syntax errors are never silently masked.

5. **Speech-Ducked Background Music**:
   - Automatically loops background tracks from `music/`.
   - Uses FFmpeg `sidechaincompress` audio ducking to lower music volume (~18%) whenever speech occurs, with smooth 1-second fade-outs.

6. **Multi-Channel YouTube Shorts Auto-Publisher & Quality Gate**:
   - Headless OAuth 2.0 authentication for mobile environments (`yt_auth.py`).
   - Supports multiple channels simultaneously with round-robin load balancing (`yt_post.py`).
   - `--min-score` quality gate to drop weak clips and protect channel average retention.
   - Automatic quota protection, exponential backoff, and upload limit handling.
   - Comprehensive upload history logging (`posted.json`) to prevent duplicate posts.
   - Background crontab automation (`cron_publisher.sh`) with safe defaults (`--min-score 7.5 --privacy private`).

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
# Generate 5 vertical clips with blurred backdrop framing (default) and background music
python clip.py "https://www.youtube.com/watch?v=VIDEO_ID" --num-clips 5 --min 20 --max 50

# Generate clips using center crop framing
python clip.py "https://www.youtube.com/watch?v=VIDEO_ID" --framing center

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
# Dry run preview (verifies queue and quality filtering without uploading):
python yt_post.py --dry-run --min-score 7.5

# Upload only top-tier viral clips (score >= 8.0) as Private for creator review:
python yt_post.py --min-score 8.0 --privacy private

# Publish across all authenticated channels (round-robin):
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
