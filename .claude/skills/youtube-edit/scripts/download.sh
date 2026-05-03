#!/usr/bin/env bash
# download.sh URL workdir
# Downloads a YouTube video + auto-captions into <workdir>.
set -euo pipefail

URL="${1:?usage: download.sh URL workdir}"
WORKDIR="${2:?usage: download.sh URL workdir}"

# Make uv-installed yt-dlp visible.
export PATH="$HOME/.local/bin:$PATH"

if ! command -v yt-dlp >/dev/null 2>&1; then
  echo "error: yt-dlp not installed. Run: uv tool install yt-dlp" >&2
  exit 127
fi
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "error: ffmpeg not installed. Run: brew install ffmpeg" >&2
  exit 127
fi

mkdir -p "$WORKDIR"
cd "$WORKDIR"

# Best video+audio up to 1080p, merged into mp4.
# Pull both manual and auto English subs as VTT.
# Write metadata to source.info.json.
yt-dlp \
  -f 'bv*[height<=1080]+ba/b[height<=1080]/b' \
  --merge-output-format mp4 \
  --write-auto-subs \
  --write-subs \
  --sub-langs 'en.*,en' \
  --sub-format 'vtt' \
  --convert-subs vtt \
  --write-info-json \
  --no-playlist \
  --no-mtime \
  -o 'source.%(ext)s' \
  "$URL"

echo
echo "Downloaded to $WORKDIR:"
ls -lh "$WORKDIR" | awk 'NR>1 {print "  " $NF "  (" $5 ")"}'
