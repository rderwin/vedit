#!/usr/bin/env bash
# watch.sh <urls_file> [<runs_root>]
#
# Watch a text file for new YouTube URLs (one per line) and run the full
# pipeline on each. Idempotent — re-runs skip already-processed URLs.
#
# Drop a URL into the watched file from anywhere (Slack bot, browser
# extension, cron job, alfred snippet) and the next polling tick picks it up.
#
#   echo "https://youtube.com/watch?v=ABC" >> ~/vedit-queue.txt
#
# Configuration:
#   POLL_SECS      seconds between checks (default 30)
#   AUTO_PICKER    'auto_edl' (default) or 'claude' — which fallback picker
#                  to use. claude requires ANTHROPIC_API_KEY.
#   AUTO_RENDER    if '1', after picking the EDL is auto-renamed and
#                  assemble.py is run. Off by default — by default the
#                  pipeline stops at auto_edl.json for human review.
set -euo pipefail

URLS_FILE="${1:?usage: watch.sh <urls_file> [<runs_root>]}"
RUNS_ROOT="${2:-vedit-runs}"
POLL_SECS="${POLL_SECS:-30}"
AUTO_PICKER="${AUTO_PICKER:-auto_edl}"
AUTO_RENDER="${AUTO_RENDER:-0}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# State: processed URLs go in <urls_file>.done so we don't re-process.
DONE_FILE="${URLS_FILE}.done"
touch "$DONE_FILE"
mkdir -p "$RUNS_ROOT"

echo "[watch] watching $URLS_FILE (poll every ${POLL_SECS}s)"
echo "[watch] runs go under $RUNS_ROOT"
echo "[watch] picker: $AUTO_PICKER, auto-render: $AUTO_RENDER"

extract_video_id() {
  local url="$1"
  echo "$url" | sed -nE 's#.*[?&]v=([A-Za-z0-9_-]+).*#\1#p; t end
                        s#.*youtu\.be/([A-Za-z0-9_-]+).*#\1#p
                        :end' | head -1
}

process_one() {
  local url="$1"
  local vid
  vid=$(extract_video_id "$url")
  if [ -z "$vid" ]; then
    echo "[watch] could not extract video id from: $url" >&2
    return 1
  fi
  local workdir="$RUNS_ROOT/$vid"
  echo "[watch] -> $url ($workdir)"

  # 1. Run quickstart through to auto_edl.json
  bash "$SCRIPT_DIR/quickstart.sh" "$url" "$workdir"

  # 2. Optionally substitute claude_pick.py for the auto_edl picker
  if [ "$AUTO_PICKER" = "claude" ]; then
    if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
      echo "[watch] AUTO_PICKER=claude but ANTHROPIC_API_KEY unset" >&2
      return 1
    fi
    uv run --quiet "$SCRIPT_DIR/claude_pick.py" "$workdir"
  fi

  # 3. Optionally promote and render
  if [ "$AUTO_RENDER" = "1" ]; then
    if [ -f "$workdir/auto_edl.json" ]; then
      mv "$workdir/auto_edl.json" "$workdir/edl.json"
    fi
    uv run --quiet "$SCRIPT_DIR/assemble.py" "$workdir"
  else
    echo "[watch] auto_edl.json ready — review and run assemble.py manually."
    echo "        (or rerun with AUTO_RENDER=1)"
  fi

  echo "$url" >> "$DONE_FILE"
  echo "[watch] done: $url"
}

while true; do
  # Diff URLs vs DONE_FILE — process anything new.
  while IFS= read -r url; do
    [ -z "$url" ] && continue
    [[ "$url" =~ ^[[:space:]]*# ]] && continue  # skip comments
    if grep -qxF "$url" "$DONE_FILE"; then
      continue
    fi
    if process_one "$url"; then
      :
    else
      echo "[watch] failed: $url — leaving in queue, will retry next tick" >&2
    fi
  done < "$URLS_FILE"

  sleep "$POLL_SECS"
done
