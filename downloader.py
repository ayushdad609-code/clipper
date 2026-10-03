import os
import re
import glob
import subprocess
from urllib.parse import urlparse, parse_qs
from typing import Optional

def is_google_drive_url(url_or_path: str) -> bool:
    if not isinstance(url_or_path, str):
        return False
    if url_or_path.startswith("http://") or url_or_path.startswith("https://"):
        parsed = urlparse(url_or_path)
        if "drive.google.com" in parsed.netloc or "docs.google.com" in parsed.netloc:
            return True
        if "drive" in parsed.path:
            return True
    return False

def is_youtube_url(url_or_path: str) -> bool:
    if not isinstance(url_or_path, str):
        return False
    if url_or_path.startswith("http://") or url_or_path.startswith("https://"):
        parsed = urlparse(url_or_path)
        netloc = parsed.netloc.lower()
        if "youtube.com" in netloc or "youtu.be" in netloc:
            return True
    return False

def extract_youtube_video_id(url: str) -> str:
    """
    Extracts the clean 11-character video ID from any YouTube URL,
    stripping tracking parameters like ?si=, &feature=, etc.
    """
    parsed = urlparse(url)
    netloc = parsed.netloc.lower()

    video_id = None
    if "youtu.be" in netloc:
        # Format: https://youtu.be/<ID>
        path_parts = parsed.path.strip("/").split("/")
        if path_parts and path_parts[0]:
            video_id = path_parts[0].split("?")[0].split("&")[0]
    elif "youtube.com" in netloc:
        if "/watch" in parsed.path:
            # Format: https://www.youtube.com/watch?v=<ID>
            qs = parse_qs(parsed.query)
            if "v" in qs and qs["v"]:
                video_id = qs["v"][0]
        elif "/shorts/" in parsed.path:
            # Format: https://www.youtube.com/shorts/<ID>
            parts = parsed.path.split("/shorts/")
            if len(parts) > 1:
                video_id = parts[1].strip("/").split("/")[0].split("?")[0]
        elif "/embed/" in parsed.path:
            parts = parsed.path.split("/embed/")
            if len(parts) > 1:
                video_id = parts[1].strip("/").split("/")[0].split("?")[0]
        elif "/live/" in parsed.path:
            parts = parsed.path.split("/live/")
            if len(parts) > 1:
                video_id = parts[1].strip("/").split("/")[0].split("?")[0]

    if video_id:
        video_id = video_id.split("?")[0].split("&")[0]

    # Regex fallback if URL structure is unusual or video_id not yet found
    if not video_id or len(video_id) != 11:
        match = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11})(?:[?&/#]|$)", url)
        if match:
            video_id = match.group(1)

    if not video_id:
        raise ValueError(f"Could not extract a valid YouTube video ID from URL: {url}")

    # Final sanity check: take only the 11 character ID
    m = re.match(r"^([0-9A-Za-z_-]{11})", video_id)
    if m:
        video_id = m.group(1)

    return video_id

def check_for_ip_block_or_bot(output_text: str):
    """
    Checks for HTTP 429 or bot confirmation prompts.
    Raises RuntimeError immediately without looping.
    """
    lower = output_text.lower()
    bot_signatures = [
        "429",
        "too many requests",
        "confirm you're not a bot",
        "confirm you’re not a bot",
        "sign in to confirm you're not a bot",
        "sign in to confirm you’re not a bot",
        "sign in to confirm your age",
        "bot detection",
        "recaptcha"
    ]
    for sig in bot_signatures:
        if sig in lower:
            raise RuntimeError(
                "\n" + "=" * 65 + "\n"
                "[Downloader Error] YouTube IP Block / Bot Detection Encountered!\n"
                "The server returned HTTP 429 or requested bot verification.\n\n"
                "Actionable Fixes:\n"
                "1. Provide your browser cookies in ~/clipper/cookies.txt\n"
                "   (Export with a browser extension like 'Get cookies.txt LOCALLY').\n"
                "2. Switch your network connection (e.g. switch between Wi-Fi and mobile hotspot, or toggle VPN).\n"
                + "=" * 65
            )

def download_youtube_video(
    url: str,
    download_dir: str = "./downloads",
    section: Optional[str] = None
) -> str:
    """
    Downloads a YouTube video using yt-dlp:
    - Extracts clean video ID and strips tracking params.
    - Caches by video ID (or video ID + section) to prevent re-downloads.
    - Passes --js-runtimes node.
    - Uses cookies from ~/clipper/cookies.txt if present (never logged).
    - Tries 'bv*[height<=720]+ba/b' merged into MKV; falls back to 'b/best' and tv player client.
    - Halts immediately on 429 / bot block without infinite retries.
    """
    video_id = extract_youtube_video_id(url)
    clean_url = f"https://www.youtube.com/watch?v={video_id}"
    print(f"[Downloader] Detected YouTube Video ID: {video_id} (Clean URL: {clean_url})")

    os.makedirs(download_dir, exist_ok=True)

    # Check cache by video ID
    clean_sec = ""
    if section:
        clean_sec = "_sec_" + re.sub(r"[^a-zA-Z0-9_-]", "_", section.strip())
    
    cache_prefix = os.path.join(download_dir, f"{video_id}{clean_sec}")
    matched_files = glob.glob(f"{cache_prefix}.*")
    # Filter for non-empty video files
    for f in matched_files:
        if os.path.exists(f) and os.path.getsize(f) > 0 and not f.endswith(".part") and not f.endswith(".ytdl"):
            print(f"[Downloader] Cache hit for YouTube video ID '{video_id}' -> {f}")
            return os.path.abspath(f)

    # Prepare cookies
    cookie_candidates = [
        os.path.expanduser("~/clipper/cookies.txt"),
        os.path.abspath("cookies.txt"),
        os.path.abspath("./cookies.txt")
    ]
    cookie_file = None
    for cand in cookie_candidates:
        if os.path.exists(cand) and os.path.getsize(cand) > 0:
            cookie_file = cand
            break

    # Download attempts strategy
    # Attempt 1: Android client stream (bypasses SABR/bot checks on mobile)
    # Attempt 2: 720p merged video+audio
    # Attempt 3: best single stream (b/best)
    # Attempt 4: TV client extractor fallback
    strategies = [
        {
            "name": "Android client stream (youtube:player_client=android)",
            "args": ["-f", "b/best", "--extractor-args", "youtube:player_client=android", "--merge-output-format", "mkv"]
        },
        {
            "name": "720p merged stream (bv*[height<=720]+ba/b)",
            "args": ["-f", "bv*[height<=720]+ba/b", "--merge-output-format", "mkv"]
        },
        {
            "name": "best format fallback (b/best)",
            "args": ["-f", "b/best", "--merge-output-format", "mkv"]
        },
        {
            "name": "TV client extractor fallback (youtube:player_client=tv)",
            "args": ["-f", "b/best", "--merge-output-format", "mkv", "--extractor-args", "youtube:player_client=tv"]
        }
    ]

    last_error = None

    for attempt, strat in enumerate(strategies, start=1):
        print(f"[Downloader] Starting download attempt {attempt}/{len(strategies)} using {strat['name']}...")
        out_template = os.path.join(download_dir, f"{video_id}{clean_sec}.%(ext)s")

        cmd = [
            "yt-dlp",
            "--no-warnings",
            "--js-runtimes", "node:/usr/bin/node",
            "-o", out_template
        ]

        if cookie_file:
            # Pass cookie file without logging contents
            cmd += ["--cookies", cookie_file]

        if section:
            # yt-dlp expects: --download-sections "*10:00-25:00"
            sec_arg = section.strip()
            if not sec_arg.startswith("*"):
                sec_arg = f"*{sec_arg}"
            cmd += ["--download-sections", sec_arg]

        cmd += strat["args"]
        cmd.append(clean_url)

        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        combined_output = f"{res.stdout}\n{res.stderr}"

        # Check immediately for IP Block / 429 / Bot check
        check_for_ip_block_or_bot(combined_output)

        if res.returncode == 0:
            # Find the downloaded file
            matches = glob.glob(f"{cache_prefix}.*")
            for f in matches:
                if os.path.exists(f) and os.path.getsize(f) > 0 and not f.endswith(".part") and not f.endswith(".ytdl"):
                    print(f"[Downloader] Successfully downloaded YouTube video to: {f}")
                    return os.path.abspath(f)

        last_error = res.stderr.strip() or res.stdout.strip()
        print(f"[Downloader] Attempt {attempt} failed ({last_error[:160]}...). Retrying with next strategy...")

    raise RuntimeError(f"Failed to download YouTube video after {len(strategies)} attempts.\nLast error:\n{last_error}")

def resolve_video_path(
    video_input: str,
    download_dir: str = "./downloads",
    section: Optional[str] = None
) -> str:
    """
    Resolves the video path:
    - If YouTube link: downloads via yt-dlp into download_dir.
    - If Google Drive link: downloads via gdown.
    - If local path: verifies existence.
    """
    if is_youtube_url(video_input):
        return download_youtube_video(video_input, download_dir=download_dir, section=section)

    if is_google_drive_url(video_input):
        print(f"[Downloader] Detected Google Drive URL: {video_input}")
        try:
            import gdown
        except ImportError:
            raise RuntimeError("gdown package is not installed. Please install it with 'pip install gdown'.")

        os.makedirs(download_dir, exist_ok=True)
        print(f"[Downloader] Downloading file to {download_dir}...")
        downloaded = gdown.download(video_input, output=download_dir + "/", quiet=False, fuzzy=True)
        if not downloaded or not os.path.exists(downloaded):
            raise RuntimeError(f"Failed to download video from Google Drive URL: {video_input}")
        print(f"[Downloader] Download completed: {downloaded}")
        return os.path.abspath(downloaded)

    # Local file path
    expanded = os.path.expanduser(video_input)
    if not os.path.exists(expanded):
        raise FileNotFoundError(f"Video file not found at: {video_input}")
    
    return os.path.abspath(expanded)
