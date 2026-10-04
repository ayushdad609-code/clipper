#!/data/data/com.termux/files/usr/bin/bash
# ==============================================================================
# Clipper Automated YouTube Shorts Publisher (Cron Runner)
# ==============================================================================
export PATH="/data/data/com.termux/files/usr/bin:/data/data/com.termux/files/usr/bin/applets:$PATH"
LOG_FILE="/data/data/com.termux/files/home/clipper/cron_publisher.log"

echo "======================================================================" >> "$LOG_FILE"
echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] Running Clipper auto-publisher..." >> "$LOG_FILE"
echo "======================================================================" >> "$LOG_FILE"

# Quality gate: min score 7.5 (strong viral potential), auto-publish as public
MIN_SCORE="${CLIPPER_MIN_SCORE:-7.5}"
PRIVACY="${CLIPPER_PRIVACY:-public}"

# Execute inside Ubuntu PRoot container (round-robins across all authenticated channels)
proot-distro login ubuntu -- bash -c "cd /root/clipper && source venv/bin/activate && python3 yt_post.py --min-score $MIN_SCORE --privacy $PRIVACY" >> "$LOG_FILE" 2>&1
EXIT_CODE=$?

echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] Publisher finished with exit code $EXIT_CODE." >> "$LOG_FILE"
echo "" >> "$LOG_FILE"
