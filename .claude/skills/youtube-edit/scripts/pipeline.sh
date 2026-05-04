#!/usr/bin/env bash
# pipeline.sh <URL> [<workdir>]
#
# Full end-to-end pipeline:
#   1. download         (yt-dlp)
#   2. transcribe       (whisper.cpp; transcribe_x.py if WHISPERX=1)
#   3. fix_transcript   (auto-detect mishears)
#   4. analyze          (loudness / scenes / wpm signals)
#   5. (optional) face-track   (FACE_TRACK=1)
#   6. (optional) beats        (auto-detected from music.<ext>)
#   7. pick clips       (PICKER=auto_edl | claude — see env vars)
#   8. promote          (auto_edl.json → edl.json)
#   9. assemble         (full polish render)
#  10. (optional) upload      (UPLOAD=1 if upload.json exists)
#
# Idempotent — re-running is safe; each step skips if its output exists.
#
# Env vars:
#   PICKER            'auto_edl' (default; peak-only heuristic) or 'claude'
#                     (uses claude_pick.py — needs ANTHROPIC_API_KEY).
#   WHISPERX          '1' to use transcribe_x.py instead of transcribe.sh.
#                     Needs whisperx installed; see WHISPERX.md.
#   FACE_TRACK        '1' to run smart_crop.py for vertical_fit=face_track.
#   AUTO_PROMOTE      '1' to skip the human-review step and auto-promote
#                     auto_edl.json → edl.json. By default the pipeline
#                     STOPS at auto_edl.json and prints a hint to review.
#   UPLOAD            '1' to run upload.py after rendering. Requires
#                     <workdir>/upload.json + OAuth setup (see UPLOAD.md).
#
# Examples:
#   pipeline.sh "https://youtube.com/watch?v=ABC"
#   PICKER=claude AUTO_PROMOTE=1 UPLOAD=1 pipeline.sh "URL"
#   WHISPERX=1 FACE_TRACK=1 pipeline.sh "URL"
set -euo pipefail

URL="${1:?usage: pipeline.sh <URL> [<workdir>]}"
WORKDIR="${2:-}"

PICKER="${PICKER:-auto_edl}"
WHISPERX="${WHISPERX:-0}"
FACE_TRACK="${FACE_TRACK:-0}"
AUTO_PROMOTE="${AUTO_PROMOTE:-0}"
UPLOAD="${UPLOAD:-0}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ -z "$WORKDIR" ]; then
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
echo "[pipeline] workdir = $WORKDIR"
echo "[pipeline] picker  = $PICKER (auto_promote=$AUTO_PROMOTE upload=$UPLOAD)"

# 1. Download
if [ ! -f "$WORKDIR/source.mp4" ]; then
  echo "[pipeline] (1) download"
  bash "$SCRIPT_DIR/download.sh" "$URL" "$WORKDIR"
else
  echo "[pipeline] (1) source.mp4 present — skipping download"
fi

# 2. Transcribe
HAVE_VTT=$(ls "$WORKDIR"/source*.vtt 2>/dev/null | head -1 || true)
if [ ! -f "$WORKDIR/transcript.json" ]; then
  if [ "$WHISPERX" = "1" ]; then
    echo "[pipeline] (2) transcribe_x.py (whisperx)"
    python3 "$SCRIPT_DIR/transcribe_x.py" "$WORKDIR"
  elif [ -z "$HAVE_VTT" ]; then
    echo "[pipeline] (2) whisper.cpp"
    bash "$SCRIPT_DIR/transcribe.sh" "$WORKDIR"
  else
    echo "[pipeline] (2) using YouTube VTT: $(basename "$HAVE_VTT")"
  fi
  python3 "$SCRIPT_DIR/parse_transcript.py" "$WORKDIR"
else
  echo "[pipeline] (2) transcript.json present — skipping"
fi

# 3. Fix transcript
echo "[pipeline] (3) fix_transcript"
python3 "$SCRIPT_DIR/fix_transcript.py" "$WORKDIR" || true

# 4. Signals
if [ ! -f "$WORKDIR/signals.json" ]; then
  echo "[pipeline] (4) analyze (signals)"
  python3 "$SCRIPT_DIR/analyze.py" "$WORKDIR"
else
  echo "[pipeline] (4) signals.json present — skipping"
fi

# 5. Face track (optional)
if [ "$FACE_TRACK" = "1" ] && [ ! -f "$WORKDIR/crop_track.json" ]; then
  echo "[pipeline] (5) smart_crop (face tracking)"
  uv run --quiet "$SCRIPT_DIR/smart_crop.py" "$WORKDIR"
fi

# 6. Beats (only if music.<ext> present)
HAVE_MUSIC=$(ls "$WORKDIR"/music.* 2>/dev/null | head -1 || true)
if [ -n "$HAVE_MUSIC" ] && [ ! -f "$WORKDIR/beats.json" ]; then
  echo "[pipeline] (6) beats from $(basename "$HAVE_MUSIC")"
  uv run --quiet "$SCRIPT_DIR/beats.py" "$WORKDIR"
fi

# 7. Pick clips — auto_edl or claude_pick
if [ ! -f "$WORKDIR/auto_edl.json" ] && [ ! -f "$WORKDIR/edl.json" ]; then
  echo "[pipeline] (7) pick — picker=$PICKER"
  case "$PICKER" in
    claude)
      if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
        echo "[pipeline] PICKER=claude but ANTHROPIC_API_KEY unset — falling back to auto_edl" >&2
        python3 "$SCRIPT_DIR/auto_edl.py" "$WORKDIR"
      else
        uv run --quiet "$SCRIPT_DIR/claude_pick.py" "$WORKDIR"
      fi
      ;;
    auto_edl|*)
      python3 "$SCRIPT_DIR/auto_edl.py" "$WORKDIR"
      ;;
  esac
fi

# 8. Promote auto_edl.json → edl.json (gated)
if [ ! -f "$WORKDIR/edl.json" ] && [ -f "$WORKDIR/auto_edl.json" ]; then
  if [ "$AUTO_PROMOTE" = "1" ]; then
    echo "[pipeline] (8) AUTO_PROMOTE=1 → promoting auto_edl.json → edl.json"
    cp "$WORKDIR/auto_edl.json" "$WORKDIR/edl.json"
  else
    echo "[pipeline] (8) STOPPING for human review."
    echo
    echo "  Review:  $WORKDIR/auto_edl.json"
    echo "  Promote: mv $WORKDIR/auto_edl.json $WORKDIR/edl.json"
    echo "  Render:  uv run --quiet $SCRIPT_DIR/assemble.py $WORKDIR"
    echo "  Or rerun this script with AUTO_PROMOTE=1 to skip review."
    exit 0
  fi
fi

# 9. Render
if [ -f "$WORKDIR/edl.json" ] && [ ! -f "$WORKDIR/out/main.mp4" ]; then
  echo "[pipeline] (9) assemble"
  uv run --quiet "$SCRIPT_DIR/assemble.py" "$WORKDIR"
elif [ -f "$WORKDIR/out/main.mp4" ]; then
  echo "[pipeline] (9) main.mp4 present — skipping render"
fi

# 10. Upload (gated)
if [ "$UPLOAD" = "1" ]; then
  if [ ! -f "$WORKDIR/upload.json" ]; then
    echo "[pipeline] (10) UPLOAD=1 but upload.json missing — see UPLOAD.md" >&2
    exit 0
  fi
  if [ -f "$WORKDIR/upload.result.json" ]; then
    echo "[pipeline] (10) already uploaded — see upload.result.json:"
    cat "$WORKDIR/upload.result.json"
  else
    echo "[pipeline] (10) upload"
    uv run --quiet "$SCRIPT_DIR/upload.py" "$WORKDIR"
  fi
fi

echo
echo "[pipeline] DONE. Outputs in $WORKDIR/out/"
ls -lh "$WORKDIR/out" 2>/dev/null | grep -v '^total' || true
