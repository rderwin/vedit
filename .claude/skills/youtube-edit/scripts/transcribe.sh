#!/usr/bin/env bash
# transcribe.sh <workdir>
# Run when YouTube has no auto-captions for the source. Extracts 16k mono
# WAV from source.mp4 and runs whisper.cpp to produce source.en.vtt that
# parse_transcript.py can read.
set -euo pipefail

WORKDIR="${1:?usage: transcribe.sh <workdir>}"
MODEL="${WHISPER_MODEL:-$HOME/.cache/whisper-cpp/ggml-base.en.bin}"

if ! command -v whisper-cli >/dev/null 2>&1; then
  echo "error: whisper-cli not installed. Run: brew install whisper-cpp" >&2
  exit 127
fi
if [ ! -f "$MODEL" ]; then
  echo "error: whisper model not found at $MODEL" >&2
  echo "Download with:" >&2
  echo "  mkdir -p \"$(dirname "$MODEL")\"" >&2
  echo "  curl -L -o \"$MODEL\" \\" >&2
  echo "    https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin" >&2
  exit 1
fi

cd "$WORKDIR"
SRC="source.mp4"
WAV="source.16k.wav"
OUT_BASE="source.en"

if [ ! -f "$SRC" ]; then
  echo "error: $SRC not found in $WORKDIR" >&2
  exit 1
fi

if [ ! -f "$WAV" ]; then
  echo "[transcribe] extracting 16k mono wav from $SRC"
  ffmpeg -y -hide_banner -loglevel error \
    -i "$SRC" -ac 1 -ar 16000 -vn "$WAV"
fi

echo "[transcribe] running whisper-cli on $WAV (model: $(basename "$MODEL"))"
whisper-cli \
  -m "$MODEL" \
  -f "$WAV" \
  -ovtt \
  -of "$OUT_BASE" \
  -l en \
  --no-prints \
  -t "$(sysctl -n hw.activecpu 2>/dev/null || echo 4)"

echo "[transcribe] wrote ${OUT_BASE}.vtt"
rm -f "$WAV"
