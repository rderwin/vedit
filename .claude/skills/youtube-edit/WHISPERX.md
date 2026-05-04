# WhisperX — word-level alignment + speaker diarization

WhisperX is an opt-in upgrade over `transcribe.sh` (which uses whisper.cpp). It costs you a 3GB+ install (PyTorch + pyannote-audio) but gives you:

- **Word-level alignment** via wav2vec2 — millisecond-accurate timestamps, way better than whisper's coarse segment-level boundaries
- **Speaker diarization** via pyannote — distinguishes who's speaking; the assembler can prefix captions with the speaker name

When to use it:
- Multi-speaker podcasts / interviews → essential for "Alice:" / "Bob:" caption labels
- Anything where you want precise word timing for `caption_mode: "word"` or `"word_active"`
- Long-form content where caption sync drift matters

When **not** to use it:
- Single speaker, casual content — `transcribe.sh --words` produces good-enough word-level timing without the heavy deps
- You don't have 3GB+ to spare on disk

## Install

```bash
# Either:
pip install whisperx

# Or (recommended — isolated env):
uv tool install whisperx
```

For diarization, you also need a HuggingFace token AND must accept the pyannote terms-of-service:

1. Get a token: https://huggingface.co/settings/tokens (read access is enough)
2. Accept the ToS at https://huggingface.co/pyannote/speaker-diarization-3.1 (one-time, free)
3. Optionally also accept https://huggingface.co/pyannote/segmentation-3.0

## Run

```bash
export HF_TOKEN=hf_...
python3 .claude/skills/youtube-edit/scripts/transcribe_x.py <workdir>
```

Outputs:
- `<workdir>/source.en.vtt` — word-level VTT (drop-in replacement for the whisper-cpp output)
- `<workdir>/speaker_labels.json` — list of `{start, end, speaker}` ranges (only when `HF_TOKEN` is set and diarization succeeds)

If `HF_TOKEN` is unset, transcription still runs but no speaker labels are produced. The script never errors on missing diarization — it just skips that step.

Then continue the normal pipeline:

```bash
python3 .claude/skills/youtube-edit/scripts/parse_transcript.py <workdir>
```

`parse_transcript.py` auto-detects `speaker_labels.json` and attaches a `speaker` field to each transcript entry.

## Showing speakers in captions

In your EDL:

```json
{
  "style": {
    "preset": "podcast",
    "show_speaker_labels": true,
    "speaker_map": {
      "SPEAKER_00": "ALICE",
      "SPEAKER_01": "BOB"
    }
  }
}
```

The assembler prefixes the **first** caption of each speaker block with `[ALICE]` (or whatever you mapped). Subsequent captions from the same speaker drop the prefix until the speaker changes — no need to repeat the label every line.

`speaker_map` is optional. Without it, the raw `SPEAKER_00` / `SPEAKER_01` labels are used. Inspect `speaker_labels.json` after diarization to see which raw label corresponds to which person, then write the map.

## Configuration env vars

| Var | Default | Purpose |
|---|---|---|
| `WHISPERX_MODEL` | `base.en` | Whisper model size (`base.en`, `small.en`, `medium.en`, `large-v3`). Bigger = better but slower. |
| `WHISPERX_DEVICE` | `cpu` | `cpu`, `cuda` (NVIDIA), or `mps` (Apple Silicon). MPS support varies — try `cpu` if you hit errors. |
| `WHISPERX_COMPUTE_TYPE` | `int8` | `int8` (fast, low memory) or `float16` / `float32` (more accurate, more memory). |
| `HF_TOKEN` | — | Required for diarization. Set to your HuggingFace read token. |

## Performance notes

On Apple Silicon, M-series CPUs run `base.en + int8` at roughly 5–15× real-time. A 100-min stream takes ~10 min on CPU. MPS support is gated by your PyTorch build — if `WHISPERX_DEVICE=mps` errors, fall back to CPU.

Diarization adds ~20–30% to the runtime.

## Falling back

If WhisperX feels heavy, the lighter path is:

```bash
bash .claude/skills/youtube-edit/scripts/transcribe.sh --words <workdir>
```

This uses whisper.cpp's `-ml 1 -sow` for one-word-per-cue VTT — no PyTorch, no diarization, but you still get word-level captions ready for `caption_mode: "word"` or `"word_active"`.
