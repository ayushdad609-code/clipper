#!/usr/bin/env python3
"""
YouTube OAuth Authentication Tool for Clipper
Authenticates YouTube channels via OAuth 2.0 and verifies channel titles.
"""

import os
import sys
import json
import argparse
import urllib.parse
from typing import Optional, Tuple

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# OAuth Scopes:
# - youtube.upload: Required to upload video clips
# - youtube.readonly: Required to list channel title, ID, and subscriber stats to verify the channel
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly"
]

DEFAULT_YT_DIR = os.path.expanduser("~/clipper/yt")


def get_channel_dir(channel_name: str, base_dir: str = DEFAULT_YT_DIR) -> str:
    chan_dir = os.path.join(base_dir, channel_name)
    os.makedirs(chan_dir, mode=0o700, exist_ok=True)
    try:
        os.chmod(chan_dir, 0o700)
    except Exception:
        pass
    return chan_dir


def find_client_secret(chan_dir: str, base_dir: str = DEFAULT_YT_DIR, explicit_path: Optional[str] = None) -> Optional[str]:
    """Finds client_secret.json in channel dir, shared yt dir, or explicit path."""
    candidates = [
        explicit_path,
        os.path.join(chan_dir, "client_secret.json"),
        os.path.join(base_dir, "client_secret.json")
    ]
    for c in candidates:
        if c and os.path.isfile(c) and os.path.getsize(c) > 0:
            return os.path.abspath(c)
    return None


def verify_channel(creds: Credentials) -> Tuple[bool, dict]:
    """Calls YouTube API to get authenticated channel snippet & statistics."""
    try:
        youtube = build("youtube", "v3", credentials=creds)
        res = youtube.channels().list(part="snippet,statistics", mine=True).execute()
        items = res.get("items", [])
        if not items:
            return False, {"error": "No YouTube channel found for this Google account. Please create a channel on YouTube first."}
        ch = items[0]
        snippet = ch.get("snippet", {})
        stats = ch.get("statistics", {})
        return True, {
            "title": snippet.get("title", "Unknown"),
            "id": ch.get("id", "Unknown"),
            "custom_url": snippet.get("customUrl", ""),
            "subscribers": stats.get("subscriberCount", "Hidden"),
            "video_count": stats.get("videoCount", "0")
        }
    except HttpError as e:
        return False, {"error": f"YouTube API error: {e}"}
    except Exception as e:
        return False, {"error": f"Verification error: {e}"}


def extract_code_from_input(user_input: str) -> str:
    """Extracts authorization code from full localhost URL, query string, or raw code."""
    user_input = user_input.strip()
    # Check if full URL or query string
    if "code=" in user_input:
        # Check standard URL parse
        parsed = urllib.parse.urlparse(user_input)
        params = urllib.parse.parse_qs(parsed.query)
        if "code" in params and params["code"]:
            return params["code"][0]
        # In case pasted as 'code=4/0A...&scope=...'
        parts = user_input.split("code=")
        if len(parts) > 1:
            code_part = parts[1].split("&")[0]
            return urllib.parse.unquote(code_part)
    return user_input


def save_token(token_path: str, creds: Credentials):
    """Saves credentials to token.json and sets chmod 600."""
    with open(token_path, "w", encoding="utf-8") as f:
        f.write(creds.to_json())
    try:
        os.chmod(token_path, 0o600)
    except Exception:
        pass


def print_missing_client_secret_help(chan_dir: str, base_dir: str):
    print("\n" + "=" * 70)
    print("❌ MISSING: client_secret.json")
    print("=" * 70)
    print("YouTube Data API v3 video uploads REQUIRE an OAuth 2.0 Client ID.")
    print("Note: An API Key (starting with 'AIzaSy...') cannot be used for uploads.")
    print("\nFollow these simple steps to obtain your client_secret.json:")
    print("1. Go to Google Cloud Console: https://console.cloud.google.com")
    print("2. Navigate to 'APIs & Services' -> 'Credentials'.")
    print("3. Click '+ CREATE CREDENTIALS' -> 'OAuth client ID'.")
    print("   (If prompted to configure OAuth Consent Screen, choose 'External',")
    print("    enter 'Clipper' as App Name, and add your email).")
    print("4. Set Application type: 'Desktop app' (Name: Clipper Desktop).")
    print("5. Click 'Create', then click 'Download JSON'.")
    print("6. Save that file to either:")
    print(f"   • Specific channel: {os.path.join(chan_dir, 'client_secret.json')}")
    print(f"   • Or shared for all: {os.path.join(base_dir, 'client_secret.json')}")
    print("=" * 70 + "\n")


def authenticate_channel(
    channel_name: str,
    base_dir: str = DEFAULT_YT_DIR,
    secret_file: Optional[str] = None,
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None,
    manual_code_url: Optional[str] = None
) -> bool:
    chan_dir = get_channel_dir(channel_name, base_dir)
    token_path = os.path.join(chan_dir, "token.json")

    # If client_id and client_secret are provided directly, create client_secret.json
    if client_id and client_secret:
        target_secret = os.path.join(chan_dir, "client_secret.json")
        secret_data = {
            "installed": {
                "client_id": client_id.strip(),
                "project_id": "clipper-youtube",
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                "client_secret": client_secret.strip(),
                "redirect_uris": ["http://localhost"]
            }
        }
        with open(target_secret, "w", encoding="utf-8") as f:
            json.dump(secret_data, f, indent=2)
        try:
            os.chmod(target_secret, 0o600)
        except Exception:
            pass

    # 1. Check existing token.json
    if os.path.isfile(token_path):
        try:
            creds = Credentials.from_authorized_user_file(token_path, SCOPES)
            if creds and creds.valid:
                print(f"[OAuth] Found valid token for '{channel_name}'. Verifying channel...")
                ok, info = verify_channel(creds)
                if ok:
                    print("\n" + "=" * 60)
                    print(f"✅ Channel '{channel_name}' is already authenticated!")
                    print(f"   Channel Title:  {info['title']}")
                    print(f"   Channel ID:     {info['id']}")
                    if info['custom_url']:
                        print(f"   Handle:         {info['custom_url']}")
                    print(f"   Subscribers:    {info['subscribers']}")
                    print("=" * 60 + "\n")
                    return True
            elif creds and creds.expired and creds.refresh_token:
                print(f"[OAuth] Refreshing expired token for '{channel_name}'...")
                creds.refresh(Request())
                save_token(token_path, creds)
                ok, info = verify_channel(creds)
                if ok:
                    print("\n" + "=" * 60)
                    print(f"✅ Token refreshed and verified for '{channel_name}'!")
                    print(f"   Channel Title:  {info['title']}")
                    print(f"   Channel ID:     {info['id']}")
                    print("=" * 60 + "\n")
                    return True
        except Exception as e:
            print(f"[OAuth] Existing token unusable ({e}). Starting fresh OAuth flow...")

    # 2. Locate client_secret.json
    secret_path = find_client_secret(chan_dir, base_dir, secret_file)
    if not secret_path:
        print_missing_client_secret_help(chan_dir, base_dir)
        return False

    # Ensure secret file permissions
    try:
        os.chmod(secret_path, 0o600)
    except Exception:
        pass

    # 3. Create OAuth Flow
    # Using http://localhost as redirect URI for Desktop app without PKCE state mismatch
    flow = Flow.from_client_secrets_file(
        secret_path,
        scopes=SCOPES,
        redirect_uri="http://localhost",
        autogenerate_code_verifier=False
    )

    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent"
    )

    print("\n" + "=" * 70)
    print(f"🔐 AUTHENTICATING YOUTUBE CHANNEL: {channel_name}")
    print("=" * 70)
    print("Step 1: Open the following URL in your phone browser:")
    print("-" * 70)
    print(auth_url)
    print("-" * 70)
    print("Step 2: Sign in with the Google account for this channel and tap 'Allow'.")
    print("Step 3: The browser will redirect to an address like:")
    print("        http://localhost/?code=4/0A...&scope=...")
    print("        (The browser will say 'This site can’t be reached' — this is NORMAL!)")
    print("Step 4: Copy the ENTIRE URL from your phone browser's address bar and paste it below.")
    print("=" * 70 + "\n")

    if manual_code_url:
        redirect_url = manual_code_url
    else:
        try:
            redirect_url = input("Paste the redirect URL (or auth code) here: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[OAuth] Authentication cancelled by user.")
            return False

    if not redirect_url:
        print("[OAuth] Error: Empty URL or code provided.")
        return False

    code = extract_code_from_input(redirect_url)
    if not code:
        print("[OAuth] Error: Could not extract authorization code.")
        return False

    print("[OAuth] Exchanging authorization code for tokens...")
    try:
        flow.fetch_token(code=code)
        creds = flow.credentials
        save_token(token_path, creds)
        print(f"[OAuth] Token saved to: {token_path} (permissions: 600)")
    except Exception as e:
        print(f"[OAuth] Failed to exchange code for token: {e}")
        return False

    # 4. Verify channel with YouTube API
    print("[OAuth] Verifying channel identity via YouTube Data API v3...")
    ok, info = verify_channel(creds)
    if ok:
        print("\n" + "=" * 60)
        print(f"🎉 Channel authenticated and verified successfully!")
        print(f"   Name:           {channel_name}")
        print(f"   Channel Title:  {info['title']}")
        print(f"   Channel ID:     {info['id']}")
        if info['custom_url']:
            print(f"   Handle:         {info['custom_url']}")
        print(f"   Subscribers:    {info['subscribers']}")
        print(f"   Total Videos:   {info['video_count']}")
        print("=" * 60 + "\n")
        return True
    else:
        print(f"[OAuth] Verification failed: {info.get('error')}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Authenticate YouTube channels for Clipper.")
    parser.add_argument("name", help="Channel name or identifier (e.g. channel1, ayushdad, tech)")
    parser.add_argument("--client-secret", default=None, help="Path to client_secret.json")
    parser.add_argument("--client-id", default=None, help="OAuth 2.0 Client ID string")
    parser.add_argument("--client-secret-val", default=None, help="OAuth 2.0 Client Secret string")
    parser.add_argument("--yt-dir", default=DEFAULT_YT_DIR, help="Base directory for yt channels")
    parser.add_argument("--code", default=None, help="Directly supply redirect URL or code for non-interactive use")

    args = parser.parse_args()
    success = authenticate_channel(
        channel_name=args.name,
        base_dir=args.yt_dir,
        secret_file=args.client_secret,
        client_id=args.client_id,
        client_secret=args.client_secret_val,
        manual_code_url=args.code
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
