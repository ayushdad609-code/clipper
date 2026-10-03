#!/data/data/com.termux/files/usr/bin/bash
# Helper script to execute clipper commands inside the Ubuntu PRoot container
set -e

if [ $# -eq 0 ]; then
    echo "Usage: ./run_clipper.sh <python_script_and_args>"
    echo "Examples:"
    echo "  ./run_clipper.sh clip.py 'https://youtube.com/watch?v=...' --num-clips 3"
    echo "  ./run_clipper.sh yt_post.py --dry-run"
    echo "  ./run_clipper.sh yt_post.py --privacy public"
    exit 1
fi

proot-distro login ubuntu -- bash -c "cd /root/clipper && source venv/bin/activate && python3 $*"
