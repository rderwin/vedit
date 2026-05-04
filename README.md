# vedit

A Claude Code skill that turns a YouTube URL into a polished, shareable video.

```
URL ─▶ download ─▶ transcribe ─▶ analyze ─▶ pick ─▶ render ─▶ upload
```

One command, end-to-end:

```bash
PICKER=claude AUTO_PROMOTE=1 UPLOAD=1 \
  bash .claude/skills/youtube-edit/scripts/pipeline.sh "https://youtube.com/watch?v=ABC"
```

That's: download with `yt-dlp` → transcribe with `whisper.cpp` (or `whisperx` for word-level + speaker diarization) → auto-fix common mishears → extract loudness/scene/wpm signals → pick the best moments (Claude API, local Ollama, or peak heuristic) → render with the full polish pipeline → upload to YouTube via OAuth.

## What's in the skill

The interesting code lives in `.claude/skills/youtube-edit/`. Top-level docs:

| File | Topic |
|---|---|
| [`SKILL.md`](.claude/skills/youtube-edit/SKILL.md) | Full workflow + every EDL knob |
| [`EXAMPLES.md`](.claude/skills/youtube-edit/EXAMPLES.md) | 10 working starter EDLs (podcast / shorts / gameplay / cinematic / multi-format / supercut / etc) |
| [`MUSIC.md`](.claude/skills/youtube-edit/MUSIC.md) | YouTube-safe music sources + sidechain ducking |
| [`WHISPERX.md`](.claude/skills/youtube-edit/WHISPERX.md) | Word-level alignment + speaker diarization (opt-in) |
| [`UPLOAD.md`](.claude/skills/youtube-edit/UPLOAD.md) | YouTube OAuth setup + auto-upload |
| [`ROADMAP.md`](.claude/skills/youtube-edit/ROADMAP.md) | What's shipped and what's next, annotated with the OSS deps |

## Output modes

One source video, many deliverables:

| Mode | What you get |
|---|---|
| `highlights` | 3–7 min main compilation + N short clips |
| `shorts` | 10–30 self-contained vertical clips with TikTok-style captions |
| `quotecards` | Square / vertical PNG quote cards for X / IG / LinkedIn |
| `thumbnails` | YouTube thumbnail candidates with bold-caps headlines |
| `chapters` | YouTube chapter markers ready to paste |
| `summary` | Markdown show-notes / blog post |
| `supercut` | Clips pulled from multiple URLs into one compilation |

The same shared transcript + signals pass feeds every mode — pick once, ship many.

## Polish features

The render pipeline is rich enough to compete with paid editors. A non-exhaustive list:

- **Color** — 7 looks, 5 bundled CC0 3D LUTs, custom `.cube` files
- **Captions** — 3 modes (phrase / word / word_active karaoke) × 3 styles (minimal / bold / pop)
- **Vertical** — 4 fits (blur_fill / fill_height / face_track / auto), with smart_crop.py running OpenCV face detection for `face_track`
- **Transitions** — 35+ xfade types (fade / wipeleft / circleopen / dissolve / pixelize / squeezeh / …)
- **Motion** — auto-zoom on reaction peaks (zoompan), animated title slide-in, speed ramps (incl. reverse)
- **Audio** — sidechain music ducking, loudness normalization, FFT denoise (`afftdn`), SFX on title-in and xfades, beat-synced cuts via librosa
- **Compositing** — picture-in-picture (drop `webcam.mp4` in workdir), corner logo, end card with title/subtitle/CTA
- **Export** — main + square + vertical (9:16) + audio-only (m4a) + SRT sidecar from one EDL
- **i18n** — `style.translate_to: ['es', 'fr']` renders extra vertical clips with translated captions via argos-translate
- **Speaker labels** — `style.show_speaker_labels: true` prefixes the first caption of each speaker block with `[ALICE]` (needs WhisperX diarization)
- **Stabilization** — `deshake` for shaky footage

## Picker tiers

Cost / quality / setup tradeoffs for choosing what to clip:

| Picker | Cost | Setup | Quality |
|---|---|---|---|
| `auto_edl.py` | Free, instant | Built-in | OK starter — peak heuristic only |
| `pick_local.py` | Free, ~30s | Ollama running locally | Good — local LLM (default llama3.1:8b) |
| `claude_pick.py` | ~$0.05 / pick | `ANTHROPIC_API_KEY` | Best — Anthropic API (default Sonnet 4.6) |

When you're invoking the skill *through* Claude Code interactively, **Claude is the picker** — none of the three scripts are needed. The scripts are for unattended runs (cron, batch, watch-folder).

## Requirements

```bash
# Required
brew install ffmpeg whisper-cpp
uv tool install yt-dlp

# Whisper model (140MB)
mkdir -p ~/.cache/whisper-cpp
curl -L -o ~/.cache/whisper-cpp/ggml-base.en.bin \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin

# Optional — for the heavier features
brew install espeak                            # TTS fallback (Linux: apt install espeak-ng)
pip install whisperx                           # word-level + diarization
pip install argostranslate                     # caption translation
ollama pull llama3.1:8b                        # local LLM picker
```

`uv` handles Pillow, OpenCV, librosa, the Anthropic SDK, the Google API client, argos-translate, ollama-python, etc. on demand via PEP 723 inline metadata in each script.

## Output layout

```
vedit-runs/<videoId>/
  source.mp4
  transcript.json             # phrase-level, deduped, mishears fixed
  signals.json                # loud_peaks + scenes + wpm
  edl.json                    # human-edited or auto-promoted
  out/
    main.mp4                  # the highlight compilation
    main_square.mp4           # 1080×1080 (LinkedIn / IG)
    main_vertical.mp4         # 1080×1920 (Reels / Shorts)
    main.m4a                  # audio-only podcast distro
    main.srt                  # subtitle sidecar
    clips/                    # landscape clips
    clips_vertical/           # 9:16 with TikTok captions
    clips_vertical_es/        # translated copies, one dir per language
    quotecards/               # PNG quote cards
    thumbnails/               # YouTube thumbnail candidates
    chapters.txt              # paste into YT description
    summary.md                # show notes / blog post
    upload.result.json        # video URL after auto-upload
```

`vedit-runs/` is gitignored.

## License

The skill code is MIT. The bundled 3D LUTs (`.claude/skills/youtube-edit/luts/`) are CC0.
