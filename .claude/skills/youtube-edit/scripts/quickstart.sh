#!/usr/bin/env bash
# quickstart.sh <YouTube URL> [<workdir>]
# Run the full skill pipeline from a YouTube URL to a starter EDL.
#
# Steps:
#   1. download
#   2. transcribe (whisper.cpp if no auto-captions)
#   3. fix mishears
#   4. extract signals
#   5. (optional) face-track for talking-head content
#   6. (optional) detect beats if a music.<ext> is present
#   7. generate auto_edl.json
#
# After this, review auto_edl.json — rename to edl.json and run
# assemble.py to render. quickstart doesn't render automatically because
# the picks should always have a human pass.
set -euo pipefail

URL="${1:?usage: quickstart.sh <URL> [<workdir>]}"
WORKDIR="${2:-}"

# Find the directory this script lives in so we can call siblings.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ -z "$WORKDIR" ]; then
  # Auto-derive from the URL's video id.
  VID=$(echo "$URL" | sed -nE 's#.*[?&]v=([A-Za-z0-9_-]+).*#\1#p')
  if [ -z "$VID" ]; then
    VID=$(echo "$URL" | sed -nE 's#.*youtu\.be/([A-Za-z0-9_-]+).*#\1#p')
  fi
  if [ -z "$VID" ]; then
    VID="$(date +%s)"
  fi
  WORKDIR="vedit-runs/$VID"
fi
mkdir -p "$WORKDIR"
echo "[quickstart] workdir = $WORKDIR"

# 1. Download.
if [ ! -f "$WORKDIR/source.mp4" ]; then
  echo "[quickstart] (1/7) downloading"
  bash "$SCRIPT_DIR/download.sh" "$URL" "$WORKDIR"
else
  echo "[quickstart] (1/7) source.mp4 already present, skipping download"
fi

# 2. Transcribe — VTT from YouTube if available, else whisper.
HAVE_VTT=$(ls "$WORKDIR"/source*.vtt 2>/dev/null | head -1 || true)
if [ -z "$HAVE_VTT" ] && [ ! -f "$WORKDIR/transcript.json" ]; then
  echo "[quickstart] (2/7) no VTT — running whisper"
  bash "$SCRIPT_DIR/transcribe.sh" "$WORKDIR"
elif [ -n "$HAVE_VTT" ]; then
  echo "[quickstart] (2/7) using YouTube VTT: $(basename "$HAVE_VTT")"
fi
if [ ! -f "$WORKDIR/transcript.json" ]; then
  echo "[quickstart] (2/7) parsing transcript"
  python3 "$SCRIPT_DIR/parse_transcript.py" "$WORKDIR"
fi

# 3. Fix mishears.
echo "[quickstart] (3/7) fix_transcript"
python3 "$SCRIPT_DIR/fix_transcript.py" "$WORKDIR" || true

# 4. Signals.
if [ ! -f "$WORKDIR/signals.json" ]; then
  echo "[quickstart] (4/7) signals (loudness + scenes + wpm)"
  python3 "$SCRIPT_DIR/analyze.py" "$WORKDIR"
else
  echo "[quickstart] (4/7) signals.json already present, skipping"
fi

# 5. Face track (optional — only if QUICKSTART_FACE_TRACK=1).
if [ "${QUICKSTART_FACE_TRACK:-0}" = "1" ] && [ ! -f "$WORKDIR/crop_track.json" ]; then
  echo "[quickstart] (5/7) face tracking"
  uv run --quiet "$SCRIPT_DIR/smart_crop.py" "$WORKDIR"
else
  echo "[quickstart] (5/7) skipping face track (set QUICKSTART_FACE_TRACK=1 to enable)"
fi

# 6. Beats (only if music is present).
HAVE_MUSIC=$(ls "$WORKDIR"/music.* 2>/dev/null | head -1 || true)
if [ -n "$HAVE_MUSIC" ] && [ ! -f "$WORKDIR/beats.json" ]; then
  echo "[quickstart] (6/7) beats from $(basename "$HAVE_MUSIC")"
  uv run --quiet "$SCRIPT_DIR/beats.py" "$WORKDIR"
else
  echo "[quickstart] (6/7) skipping beats (no music.* in workdir)"
fi

# 7. Auto-EDL.
echo "[quickstart] (7/7) generating starter EDL"
python3 "$SCRIPT_DIR/auto_edl.py" "$WORKDIR" --clips 8 --main-minutes 5 --preset tiktok

echo
echo "[quickstart] DONE."
echo "  Review:  $WORKDIR/auto_edl.json"
echo "  Promote: mv $WORKDIR/auto_edl.json $WORKDIR/edl.json"
echo "  Render:  uv run --quiet $SCRIPT_DIR/assemble.py $WORKDIR"
