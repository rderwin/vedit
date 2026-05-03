#!/usr/bin/env bash
# transcribe.sh [--words] [--prompt "..."] <workdir>
# Extract audio + run whisper.cpp to produce source.en.vtt.
#
#   --words / WHISPER_WORDS=1 — word-level VTT (one word per cue).
#   --prompt "..."           — initial prompt to bias vocabulary
#                              (defaults to the video title + uploader
#                              from source.info.json if present, plus
#                              anything in <workdir>/whisper_prompt.txt).
#
# A good prompt steers Whisper away from common mishears in your domain.
# For a chess stream, include "chess, ELO, rook, bishop, knight, queen,
# pawn, blunder, mate". For a tech podcast, include the company / product
# names that keep getting mangled.
set -euo pipefail

WORDS="${WHISPER_WORDS:-0}"
PROMPT="${WHISPER_PROMPT:-}"

while [ "${1:-}" != "" ]; do
  case "$1" in
    --words) WORDS=1; shift ;;
    --prompt) PROMPT="$2"; shift 2 ;;
    --) shift; break ;;
    -*) echo "unknown flag: $1" >&2; exit 1 ;;
    *) break ;;
  esac
done

WORKDIR="${1:?usage: transcribe.sh [--words] [--prompt TEXT] <workdir>}"
MODEL="${WHISPER_MODEL:-$HOME/.cache/whisper-cpp/ggml-base.en.bin}"

if ! command -v whisper-cli >/dev/null 2>&1; then
  echo "error: whisper-cli not installed. Run: brew install whisper-cpp" >&2
  exit 127
fi
if [ ! -f "$MODEL" ]; then
  echo "error: whisper model not found at $MODEL" >&2
  echo "Download:" >&2
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

# Build the auto-prompt unless the user supplied one explicitly.
if [ -z "$PROMPT" ]; then
  AUTO_PARTS=()
  if [ -f "source.info.json" ]; then
    # Extract title, uploader, tags via python-stdlib (avoid jq dep).
    AUTO=$(python3 - <<'PY'
import json, sys
try:
    d = json.load(open("source.info.json"))
except Exception:
    sys.exit(0)
parts = []
title = (d.get("title") or "").strip()
if title:
    parts.append(title)
uploader = (d.get("uploader") or d.get("channel") or "").strip()
if uploader:
    parts.append("Speaker: " + uploader)
tags = d.get("tags") or []
if tags:
    parts.append("Topics: " + ", ".join(tags[:10]))
print(" — ".join(parts))
PY
)
    if [ -n "$AUTO" ]; then
      AUTO_PARTS+=("$AUTO")
    fi
  fi
  if [ -f "whisper_prompt.txt" ]; then
    AUTO_PARTS+=("$(cat whisper_prompt.txt)")
  fi
  if [ ${#AUTO_PARTS[@]} -gt 0 ]; then
    # Concatenate with separators, cap to ~896 chars (whisper prompt limit).
    PROMPT="$(printf '%s\n' "${AUTO_PARTS[@]}")"
    PROMPT="${PROMPT:0:896}"
  fi
fi

if [ ! -f "$WAV" ]; then
  echo "[transcribe] extracting 16k mono wav from $SRC"
  ffmpeg -y -hide_banner -loglevel error \
    -i "$SRC" -ac 1 -ar 16000 -vn "$WAV"
fi

EXTRA_ARGS=()
if [ "$WORDS" = "1" ]; then
  EXTRA_ARGS+=(-ml 1 -sow)
  echo "[transcribe] word-level mode (one word per cue)"
fi
if [ -n "$PROMPT" ]; then
  EXTRA_ARGS+=(--prompt "$PROMPT")
  echo "[transcribe] prompt: ${PROMPT:0:120}…"
fi

echo "[transcribe] running whisper-cli on $WAV (model: $(basename "$MODEL"))"
whisper-cli \
  -m "$MODEL" \
  -f "$WAV" \
  -ovtt \
  -of "$OUT_BASE" \
  -l en \
  --no-prints \
  -t "$(sysctl -n hw.activecpu 2>/dev/null || echo 4)" \
  "${EXTRA_ARGS[@]}"

echo "[transcribe] wrote ${OUT_BASE}.vtt"
rm -f "$WAV"
