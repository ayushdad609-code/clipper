# YouTube Shorts Publishing Reference

## Authentication Architecture (`yt_auth.py`)

- **Protocol**: Official YouTube Data API v3 via OAuth 2.0.
- **Scopes**:
  - `https://www.googleapis.com/auth/youtube.upload` (uploading videos)
  - `https://www.googleapis.com/auth/youtube.readonly` (verifying channel title, ID, handle)
- **Directory Layout**:
  ```text
  ~/clipper/yt/
  ├── client_secret.json         # Shared Desktop OAuth client secret (chmod 600)
  └── <channel_name>/            # Directory per channel (chmod 700)
      ├── client_secret.json     # Optional channel-specific client secret (chmod 600)
      └── token.json             # Cached user token with refresh token (chmod 600)
  ```
- **Security**: Folder permissions `700`, file permissions `600`. `yt/` and `posted.json` in `.gitignore`. Secrets/tokens are never printed to terminal or logged.
- **Headless Mobile OAuth**:
  1. `python yt_auth.py <channel>` prints the consent URL.
  2. User opens the link in mobile browser, authenticates, and is redirected to `http://localhost/?code=4/0A...` (which fails to load on mobile).
  3. User copies the address bar URL and provides it to `--code "<pasted_url>"`.
  4. Script exchanges code, saves `token.json`, and verifies channel title.

---

## Publishing Engine (`yt_post.py`)

### Deduplication
- Tracks all uploads in `~/clipper/posted.json`.
- Automatically skips clips that have already been uploaded, preventing duplicate posting across runs.

### Clip Selection
- Checks for `clip_XX_music.mp4` first. Falls back to `clip_XX.mp4` if music version is absent.

### Metadata Formatting
- **Title**: Base title clipped to $\le 82$ chars + ` #Shorts` (total $\le 90$ chars).
- **Description**: Hook line + 5 hashtags (`#Shorts #Viral #Trending #Reels #Story`).
- **Tags**: Extracted keywords from clip title and transcript hook ($\le 500$ chars).
- **Category**: `22` (People & Blogs).
- **Made for Kids**: `selfDeclaredMadeForKids=False`.

### Scheduling (`--spread-hours H`)
- Calculates RFC-3339 UTC `publishAt` timestamps across `H` hours.
- Sets `privacyStatus = "private"` (required by YouTube API for scheduled uploads).
- Applies per-channel minute offsets to avoid concurrent video releases.

### Error Handling
- **Chunked Resumable Upload**: 1 MB chunk stream via `MediaFileUpload`.
- **Network Retries**: Exponential backoff on 5xx errors or socket disconnects.
- **`401 Unauthorized`**: Auto-refreshes token via `token.json` and retries upload once.
- **`quotaExceeded`**: Disables the affected channel and reports cleanly.
- **`uploadLimitExceeded`**: Catches YouTube daily spam limits and stops gracefully.

---

## Quota & Platform Limitations

- **Quota per upload**: 1,600 units per `videos.insert`.
- **Daily default quota**: 10,000 units per Google Cloud project.
- **Max uploads per day**: $\approx 6$ uploads per project per day.
- **Unverified OAuth Apps**: Uploads through unverified projects default to `private`. Setting to `public` works if the account owner has authorized the app as a developer/test user in Google Cloud Console.
