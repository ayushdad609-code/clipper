#!/usr/bin/env python3
"""
YouTube Video Poster for Clipper
Distributes and uploads short-form clips to YouTube channels via official Data API v3.
Features:
- Round-robin channel assignment
- Deduplication via posted.json (never double-posts)
- Resumable upload with exponential backoff
- Speech/music clip selection (prefers clip_XX_music.mp4)
- Automated scheduling across --spread-hours
- Handles quotaExceeded, 401 token refresh, and uploadLimitExceeded
- Comprehensive --dry-run mode
"""

import os
import sys
import json
import time
import socket
import random
import argparse
import datetime
import http.client
from typing import List, Dict, Any, Optional, Tuple

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly"
]

DEFAULT_YT_DIR = os.path.expanduser("~/clipper/yt")
DEFAULT_POSTED_FILE = os.path.expanduser("~/clipper/posted.json")


def load_channels(yt_dir: str = DEFAULT_YT_DIR, allow_unauthenticated: bool = False) -> Dict[str, Dict[str, Any]]:
    """Discovers all channels with valid token.json in yt_dir, or unauthenticated dirs if allow_unauthenticated=True."""
    channels = {}
    if not os.path.exists(yt_dir):
        return channels

    for entry in sorted(os.listdir(yt_dir)):
        chan_dir = os.path.join(yt_dir, entry)
        if not os.path.isdir(chan_dir):
            continue

        token_path = os.path.join(chan_dir, "token.json")
        if not os.path.isfile(token_path):
            if allow_unauthenticated:
                channels[entry] = {
                    "name": entry,
                    "dir": chan_dir,
                    "token_path": token_path,
                    "creds": None,
                    "youtube": None,
                    "title": f"{entry} (Pending Auth)",
                    "id": "PENDING_OAUTH"
                }
            continue

        try:
            creds = Credentials.from_authorized_user_file(token_path, SCOPES)
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                with open(token_path, "w", encoding="utf-8") as f:
                    f.write(creds.to_json())
                try:
                    os.chmod(token_path, 0o600)
                except Exception:
                    pass

            youtube = build("youtube", "v3", credentials=creds)
            res = youtube.channels().list(part="snippet", mine=True).execute()
            items = res.get("items", [])
            title = items[0]["snippet"]["title"] if items else entry
            cid = items[0]["id"] if items else "Unknown"

            channels[entry] = {
                "name": entry,
                "dir": chan_dir,
                "token_path": token_path,
                "creds": creds,
                "youtube": youtube,
                "title": title,
                "id": cid
            }
        except Exception as e:
            if allow_unauthenticated:
                channels[entry] = {
                    "name": entry,
                    "dir": chan_dir,
                    "token_path": token_path,
                    "creds": None,
                    "youtube": None,
                    "title": f"{entry} (Auth Error: {e})",
                    "id": "ERROR"
                }
            else:
                print(f"[Warning] Could not initialize channel '{entry}': {e}")

    return channels


def load_posted_records(posted_path: str = DEFAULT_POSTED_FILE) -> List[Dict[str, Any]]:
    if not os.path.isfile(posted_path):
        return []
    try:
        with open(posted_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[Warning] Could not read {posted_path}: {e}")
        return []


def save_posted_record(record: Dict[str, Any], posted_path: str = DEFAULT_POSTED_FILE):
    records = load_posted_records(posted_path)
    records.append(record)
    with open(posted_path, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)


def format_title(title: str) -> str:
    """Ensure title + ' #Shorts' is <= 90 characters."""
    suffix = " #Shorts"
    max_len = 90 - len(suffix)
    clean_title = title.strip()
    if len(clean_title) > max_len:
        clean_title = clean_title[:max_len - 3].rstrip() + "..."
    return f"{clean_title}{suffix}"


def format_description(hook: str) -> str:
    """Formats description with hook line and 3-5 hashtags."""
    base_hook = hook.strip() if hook else "Watch this viral clip!"
    hashtags = "#Shorts #Viral #Trending #Reels #Story"
    return f"{base_hook}\n\n{hashtags}\n"


def format_tags(title: str, hook: str) -> List[str]:
    """Generates 3-8 tags from title and hook words."""
    words = [w.strip(".,!?\"'()[]{}").lower() for w in f"{title} {hook}".split()]
    stopwords = {"the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "of", "with", "is", "was", "are", "were", "this", "that", "it", "my", "we", "i"}
    meaningful = [w for w in words if len(w) > 3 and w not in stopwords]
    tags = ["Shorts", "Viral", "YouTube Shorts"]
    for w in meaningful:
        tag = w.capitalize()
        if tag not in tags and len(tags) < 8:
            tags.append(tag)
    return tags


def compute_schedule(
    clip_index: int,
    channel_index: int,
    total_channels: int,
    spread_hours: float
) -> str:
    """
    Computes an RFC 3339 publishAt timestamp spaced across spread_hours
    with per-channel offset to ensure channels don't upload at the same time.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    # Start at least 15 minutes in the future
    base_delay_minutes = 15.0

    total_window_minutes = spread_hours * 60.0
    slot_minutes = (total_window_minutes / max(1, clip_index + 1)) * (clip_index + 0.5)

    # Offset per channel (e.g. channel 0 = 0m, channel 1 = 12m, channel 2 = 24m)
    channel_offset_minutes = (channel_index * 13.0) % 45.0

    # Add small random jitter (+- 5 minutes)
    jitter = random.uniform(-4.0, 4.0)

    scheduled_delay = base_delay_minutes + slot_minutes + channel_offset_minutes + jitter
    publish_time = now + datetime.timedelta(minutes=max(10.0, scheduled_delay))

    # Zero out seconds for clean schedule
    publish_time = publish_time.replace(second=0, microsecond=0)
    return publish_time.isoformat().replace("+00:00", "Z")


def upload_single_video(
    youtube,
    creds: Credentials,
    chan_dir: str,
    video_path: str,
    title: str,
    description: str,
    tags: List[str],
    privacy: str,
    publish_at: Optional[str] = None
) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Performs resumable upload with chunking, retry on network error,
    and handling for quotaExceeded, uploadLimitExceeded, and 401 refresh.
    Returns (success, video_id_or_none, error_message_or_none).
    """
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": "22",
            "defaultLanguage": "en"
        },
        "status": {
            "privacyStatus": "private" if publish_at else privacy,
            "selfDeclaredMadeForKids": False
        }
    }
    if publish_at:
        body["status"]["publishAt"] = publish_at

    media = MediaFileUpload(
        video_path,
        mimetype="video/mp4",
        chunksize=1024 * 1024,
        resumable=True
    )

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media
    )

    response = None
    retry_count = 0
    max_retries = 6

    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                percent = int(status.progress() * 100)
                print(f"      Uploading: {percent}%...", end="\r", flush=True)
        except HttpError as e:
            err_str = str(e)
            if "quotaExceeded" in err_str:
                return False, None, "QUOTA_EXCEEDED"
            elif "uploadLimitExceeded" in err_str:
                return False, None, "UPLOAD_LIMIT_EXCEEDED"
            elif e.resp.status == 401:
                print("\n      [OAuth] Received 401. Refreshing token...")
                try:
                    creds.refresh(Request())
                    token_path = os.path.join(chan_dir, "token.json")
                    with open(token_path, "w", encoding="utf-8") as f:
                        f.write(creds.to_json())
                    try:
                        os.chmod(token_path, 0o600)
                    except Exception:
                        pass
                    # Recreate youtube service and retry request
                    youtube = build("youtube", "v3", credentials=creds)
                    request = youtube.videos().insert(
                        part="snippet,status",
                        body=body,
                        media_body=media
                    )
                    continue
                except Exception as refresh_err:
                    return False, None, f"TOKEN_REFRESH_FAILED: {refresh_err}"
            elif e.resp.status in [500, 502, 503, 504]:
                retry_count += 1
                if retry_count > max_retries:
                    return False, None, f"HTTP_SERVER_ERROR_{e.resp.status}"
                wait_secs = 2 ** retry_count
                print(f"\n      [Server Error {e.resp.status}] Retrying in {wait_secs}s...")
                time.sleep(wait_secs)
            else:
                return False, None, f"HTTP_ERROR_{e.resp.status}: {err_str}"
        except (socket.error, http.client.RemoteDisconnected, ConnectionResetError, TimeoutError) as net_err:
            retry_count += 1
            if retry_count > max_retries:
                return False, None, f"NETWORK_ERROR: {net_err}"
            wait_secs = 2 ** retry_count
            print(f"\n      [Network Error] {net_err}. Retrying in {wait_secs}s...")
            time.sleep(wait_secs)

    print("      Uploading: 100%! Done.    ")
    video_id = response.get("id")
    return True, video_id, None


def main():
    parser = argparse.ArgumentParser(
        description="Upload clips from ./output to YouTube channels using Data API v3."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate plan and show what would be uploaded without making API calls"
    )
    parser.add_argument(
        "--privacy",
        default="private",
        choices=["private", "unlisted", "public"],
        help="YouTube privacy status (default: private)"
    )
    parser.add_argument(
        "--per-channel",
        type=int,
        default=None,
        help="Maximum number of clips to upload per channel this run"
    )
    parser.add_argument(
        "--spread-hours",
        type=float,
        default=None,
        help="Spread video publishing across H hours (sets publishAt and privacy=private)"
    )
    parser.add_argument(
        "--output-dir",
        default="./output",
        help="Directory where clips.json and mp4 files reside (default: ./output)"
    )
    parser.add_argument(
        "--yt-dir",
        default=DEFAULT_YT_DIR,
        help="Directory containing channel subdirectories (default: ~/clipper/yt)"
    )
    parser.add_argument(
        "--posted-file",
        default=DEFAULT_POSTED_FILE,
        help="Path to posted.json database (default: ~/clipper/posted.json)"
    )
    parser.add_argument(
        "--channel",
        default=None,
        help="Target a specific channel name only (e.g. ayushdad)"
    )
    parser.add_argument(
        "--category-id",
        default="22",
        help="YouTube video category ID (default: 22 - People & Blogs)"
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=None,
        help="Minimum clip score required to upload (e.g. 7.5 or 8.0). Drops lower-rated clips from upload plan."
    )

    args = parser.parse_args()

    print("=" * 70)
    print("🚀 CLIPPER: YouTube Shorts Auto-Publisher")
    print("=" * 70)

    # 1. Discover Channels
    channels = load_channels(args.yt_dir, allow_unauthenticated=args.dry_run)
    if not channels:
        print(f"[Error] No authenticated channels found in: {args.yt_dir}")
        print("Please authenticate at least one channel first using:")
        print("   python yt_auth.py <channel_name>")
        sys.exit(1)

    if args.channel:
        if args.channel not in channels:
            print(f"[Error] Specified channel '{args.channel}' not found among available channels: {list(channels.keys())}")
            sys.exit(1)
        channels = {args.channel: channels[args.channel]}

    channel_keys = list(channels.keys())
    print(f"Targeting {len(channel_keys)} channel(s):")
    for k in channel_keys:
        ch = channels[k]
        print(f" • [{ch['name']}] \"{ch['title']}\" (ID: {ch['id']})")

    # 2. Load Clips
    clips_json_path = os.path.join(args.output_dir, "clips.json")
    if not os.path.isfile(clips_json_path):
        print(f"[Error] clips metadata not found at {clips_json_path}")
        sys.exit(1)

    with open(clips_json_path, "r", encoding="utf-8") as f:
        clips = json.load(f)

    if not clips:
        print("[Error] No clips found in clips.json.")
        sys.exit(1)

    # 3. Filter already posted clips
    posted_records = load_posted_records(args.posted_file)

    def is_already_posted(clip_item: Dict[str, Any]) -> bool:
        formatted_t = format_title(clip_item.get("title", ""))
        for r in posted_records:
            if r.get("title") == formatted_t:
                return True
        return False

    unposted_clips = [c for c in clips if not is_already_posted(c)]
    already_posted_count = len(clips) - len(unposted_clips)

    print(f"\nTotal clips: {len(clips)} | Already posted: {already_posted_count} | Remaining: {len(unposted_clips)}")

    # Apply quality filter if --min-score is specified
    if args.min_score is not None:
        filtered_by_score = []
        for c in unposted_clips:
            score = float(c.get("score", 0.0))
            if score >= args.min_score:
                filtered_by_score.append(c)
            else:
                print(f"[Quality Gate] Skipping '{c.get('title')}' (score {score:.1f} < threshold {args.min_score:.1f})")
        dropped_count = len(unposted_clips) - len(filtered_by_score)
        unposted_clips = filtered_by_score
        print(f"Quality filter (--min-score {args.min_score:.1f}): Kept {len(unposted_clips)}, Dropped {dropped_count} low-scoring clip(s).")

    if not unposted_clips:
        print("✅ No remaining clips meet the upload criteria or all clips have already been posted! Nothing to upload.")
        sys.exit(0)

    # 4. Round-Robin Distribution & per-channel limits
    plan = []  # List of (clip_info, channel_info, scheduled_publish_at)
    channel_counts = {k: 0 for k in channel_keys}

    for i, c in enumerate(unposted_clips):
        # Round-robin channel selection
        ch_key = channel_keys[i % len(channel_keys)]

        if args.per_channel and channel_counts[ch_key] >= args.per_channel:
            # Check if any other channel has capacity
            available = [k for k in channel_keys if channel_counts[k] < args.per_channel]
            if not available:
                break
            ch_key = available[0]

        channel_counts[ch_key] += 1
        ch_idx = channel_keys.index(ch_key)

        publish_at = None
        if args.spread_hours:
            publish_at = compute_schedule(
                clip_index=channel_counts[ch_key] - 1,
                channel_index=ch_idx,
                total_channels=len(channel_keys),
                spread_hours=args.spread_hours
            )

        # Resolve video file: check music version first
        orig_clip_file = c["clip"]
        base_name, ext = os.path.splitext(orig_clip_file)
        music_clip_file = f"{base_name}_music{ext}"
        music_path = os.path.join(args.output_dir, music_clip_file)
        regular_path = os.path.join(args.output_dir, orig_clip_file)

        if os.path.isfile(music_path):
            chosen_file = music_clip_file
            chosen_path = music_path
        elif os.path.isfile(regular_path):
            chosen_file = orig_clip_file
            chosen_path = regular_path
        else:
            print(f"[Warning] Clip file not found on disk: {orig_clip_file}, skipping.")
            continue

        title = format_title(c.get("title", f"Short Clip {i+1}"))
        description = format_description(c.get("hook", ""))
        tags = format_tags(c.get("title", ""), c.get("hook", ""))

        plan.append({
            "clip_meta": c,
            "clip_file": chosen_file,
            "clip_path": chosen_path,
            "channel": channels[ch_key],
            "title": title,
            "description": description,
            "tags": tags,
            "privacy": args.privacy,
            "publish_at": publish_at
        })

    print(f"\nPlanned uploads: {len(plan)} clip(s) across {len(channel_keys)} channel(s)")
    print("-" * 70)

    # 5. Display Plan / Dry-Run
    for idx, item in enumerate(plan, start=1):
        ch = item["channel"]
        sched_info = f" [Scheduled: {item['publish_at']}]" if item['publish_at'] else f" [Privacy: {item['privacy']}]"
        print(f"#{idx} 📺 Target Channel: [{ch['name']}] \"{ch['title']}\"")
        print(f"   📁 File:         {item['clip_file']}")
        print(f"   🎬 Title:        {item['title']}")
        print(f"   📝 Hook:         {item['clip_meta'].get('hook', 'N/A')[:60]}...")
        print(f"   🏷️  Tags:         {', '.join(item['tags'][:4])}...")
        print(f"   ⚙️  Status:       {sched_info}")
        print()

    if args.dry_run:
        print("=" * 70)
        print("🔍 DRY RUN COMPLETE: No videos were uploaded.")
        print(f"Ready to upload {len(plan)} clip(s). Run without --dry-run to start upload.")
        print("=" * 70)
        sys.exit(0)

    # 6. Execute Uploads
    print("=" * 70)
    print("🚀 Starting Uploads...")
    print("=" * 70)

    disabled_channels = set()
    uploaded_count = 0

    for idx, item in enumerate(plan, start=1):
        ch = item["channel"]
        ch_name = ch["name"]

        if ch_name in disabled_channels:
            print(f"⏭️  Skipping clip #{idx} for channel '{ch_name}' (channel quota/limit exceeded).")
            continue

        print(f"\n[{idx}/{len(plan)}] Uploading {item['clip_file']} to [{ch_name}] \"{ch['title']}\"...")
        print(f"      Title: \"{item['title']}\"")

        t_start = time.time()
        success, video_id, error = upload_single_video(
            youtube=ch["youtube"],
            creds=ch["creds"],
            chan_dir=ch["dir"],
            video_path=item["clip_path"],
            title=item["title"],
            description=item["description"],
            tags=item["tags"],
            privacy=item["privacy"],
            publish_at=item["publish_at"]
        )

        if success and video_id:
            uploaded_count += 1
            elapsed = time.time() - t_start
            shorts_url = f"https://youtube.com/shorts/{video_id}"
            print(f"      ✅ Upload Successful in {elapsed:.1f}s!")
            print(f"      🔗 Video ID:   {video_id}")
            print(f"      📱 Shorts URL: {shorts_url}")

            # Record in posted.json
            record = {
                "clip": item["clip_meta"]["clip"],
                "clip_file_used": item["clip_file"],
                "channel": ch_name,
                "channel_title": ch["title"],
                "channel_id": ch["id"],
                "video_id": video_id,
                "youtube_url": shorts_url,
                "title": item["title"],
                "privacy": item["privacy"],
                "publish_at": item["publish_at"],
                "posted_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
            }
            save_posted_record(record, args.posted_file)

        else:
            if error in ["QUOTA_EXCEEDED", "UPLOAD_LIMIT_EXCEEDED"]:
                print(f"      ⛔ Channel [{ch_name}] hit {error}! Disabling channel for this run.")
                disabled_channels.add(ch_name)
            else:
                print(f"      ❌ Upload failed for {item['clip_file']}: {error}")

    print("\n" + "=" * 70)
    print(f"🏁 Finished! Successfully uploaded {uploaded_count}/{len(plan)} clip(s).")
    print(f"Log updated: {os.path.abspath(args.posted_file)}")
    print("=" * 70)


if __name__ == "__main__":
    main()
