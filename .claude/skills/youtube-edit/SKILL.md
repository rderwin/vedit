---
name: youtube-edit
description: Turn a YouTube video (livestream, VOD, podcast, vlog, gameplay) into shareable assets — highlight compilation, vertical shorts with TikTok-style captions, quote cards, thumbnails, YouTube chapter markers, and show-notes summaries. Use when the user gives a YouTube URL and asks for any of: highlight reel, supercut, best-of, compilation, shorts, clips, quote cards, thumbnail ideas, chapter markers, show notes / blog post / summary.
---

# youtube-edit

Take a long YouTube video and ship multiple polished outputs from one transcript + signal pass:

| Mode | Output | Good for |
|---|---|---|
| `highlights`   | 3–7 min compilation `main.mp4` + N short clips | Best-of reels, "watch the whole thing in 5 min" |
| `shorts`       | 10–30 vertical clips with burned-in captions | Feeding TikTok / Reels / YouTube Shorts |
| `quotecards`   | Square / vertical PNG quote cards            | Static social posts (X, IG, LinkedIn) |
| `thumbnails`   | YouTube thumbnail candidates with bold text  | Upload thumbnails, A/B testing |
| `chapters`     | YouTube chapter-marker block                 | Pasting into the upload description |
| `summary`      | Markdown writeup with timestamps             | Show notes, blog post, newsletter |
| `supercut`     | Cuts pulled from multiple URLs               | "Every time X says Y", channel best-of |

The first three steps (download, transcribe, analyze) are shared across every mode — run them once, then mix and match.

## When to use

The user provides one or more YouTube URLs and asks for any of the outputs above, or speaks generically: "make this into something", "I just streamed for 2 hours, help", "post-process this".

## Shared workflow (run once per video)

All modes operate on a `<workdir>`, default `vedit-runs/<videoId>/`.

### 1. Download

```bash
bash .claude/skills/youtube-edit/scripts/download.sh <URL> <workdir>
```

Produces `source.mp4`, `source.info.json`, and (when YouTube has them) `source.en.vtt`.

For still-live streams, edit the script to add `--live-from-start`, or wait for the broadcast to end.

### 2. Get a transcript

If `source.en.vtt` was downloaded, just parse it:

```bash
python3 .claude/skills/youtube-edit/scripts/parse_transcript.py <workdir>
```

If no VTT is available (fresh livestream uploads, podcasts, anything without YouTube auto-captions), use whisper.cpp:

```bash
bash .claude/skills/youtube-edit/scripts/transcribe.sh <workdir>
python3 .claude/skills/youtube-edit/scripts/parse_transcript.py <workdir>
```

Both produce `<workdir>/transcript.json` — a list of `{start, text}` deduped from rolling captions.

**Whisper options worth knowing:**

- `transcribe.sh --words <workdir>` — word-level VTT (one word per cue). Pair with `style.caption_mode: "word"` in the EDL for millisecond-accurate TikTok-style captions.
- `transcribe.sh --prompt "..." <workdir>` — initial prompt to bias whisper's vocabulary. By default the script auto-builds a prompt from `source.info.json` (title, uploader, tags). For a chess stream where you want to avoid "chess"/"chest" mishears: `--prompt "Chess stream. ELO, blunder, rook, bishop, knight, queen, pawn, mate, fork, pin"`. A `<workdir>/whisper_prompt.txt` is also auto-detected and appended.

### 2b. Fix transcript mishears (run every time)

```bash
python3 .claude/skills/youtube-edit/scripts/fix_transcript.py <workdir>
```

This is part of the standard workflow — always run it before the picking step. It auto-detects domain context from `source.info.json` *and* the transcript itself: if "chess" appears 26 times and "chest" only 4, the script replaces "chest" → "chess" everywhere. (For the live test on the Billy Markus chess stream this caught all four "chest" mishears.)

User overrides go in `<workdir>/transcript_fixes.json`:

```json
{
  "replace": {"chest": "chess", "Bily": "Billy", "git hub": "GitHub"},
  "case_insensitive": true,
  "whole_word": true
}
```

The original transcript is backed up to `transcript.raw.json` before any change, so re-running with different fixes is safe. `--dry-run` previews changes without writing.

If you find a recurring mishear during a session, add it to `transcript_fixes.json` and re-run; downstream steps (signals, EDL, captions) all pick up the corrected text automatically.

### 2c. Smart vertical crop (optional — talking-head content)

If the source has a clearly identifiable speaker on camera, run face tracking once so the assembler can crop a 9:16 window that *follows the speaker* rather than locking to center:

```bash
uv run --quiet .claude/skills/youtube-edit/scripts/smart_crop.py <workdir>
```

Writes `<workdir>/crop_track.json` with smoothed face-center samples (every 0.5s by default, `--every <seconds>` to change). Then in the EDL set `style.vertical_fit: "face_track"` and the assembler uses a time-varying crop expression that tracks the speaker. Same killer feature as CapCut / Submagic, free.

For screen-recorded content (gameplay, dashboards, chess.com) skip this step — `vertical_fit: "fill_height"` is what you want.

### 2d. Detect music beats (optional — only if you have a music track)

If you've dropped a `music.<ext>` in the workdir for the bed, you can also run beat detection so cuts can land on the music:

```bash
uv run --quiet .claude/skills/youtube-edit/scripts/beats.py <workdir>
```

Writes `<workdir>/beats.json` with `tempo_bpm` and beat times. Set `style.beat_sync: true` in the EDL to auto-snap each main segment's end to the nearest beat (within ±1.5s). Cuts feel musical without the picker thinking about it.

### 3. Extract editing signals

```bash
python3 .claude/skills/youtube-edit/scripts/analyze.py <workdir>
```

Writes `<workdir>/signals.json` with:

- `loudness` — EBU R128 momentary loudness per 1-second window
- `loud_peaks` — top ~30 loudest 1s windows (≥15s gaps), pre-screened for "something happened"
- `scenes` — visual scene-change timestamps
- `wpm` — sliding 10s words-per-minute curve

### 4. Pick moments (this is YOUR job)

**Claude is the picker.** When you (Claude) are invoked through this skill, you read `transcript.json` + `signals.json` + `source.info.json` yourself, decide which moments matter, and write the EDL. Everything else in this skill — download, transcribe, fix, analyze, render, multi-format, music ducking, smart vertical, all of it — is mechanics around that one human-meaningful decision. Don't outsource it; nothing else has your judgment about what's actually funny / interesting / quotable.

How to read the data:

1. Open `signals.json`'s `loud_peaks` first. Each is a "something happened here" candidate, pre-screened.
2. For each peak, read the transcript ±20s to confirm what's there. Most peaks are real moments; some are background music / a sneeze / a click.
3. Cross-check `wpm` spikes — high words/min often marks the *setup* leading into a peak. Look 5–15s **before** a loudness peak to find the joke setup or building tension.
4. `scenes` are clean cut boundaries when present.
5. Skim the transcript for content signals the audio doesn't surface — surprising claims, callbacks, named-entity reveals, "wait..." / "no way" / "look at this" beats.

For each candidate, write a one-line internal description. You're picking moments here, not committing to outputs yet — the same picks feed every mode below.

**For unattended / batch runs** (no Claude in the loop), use a fallback picker:

- `python3 scripts/auto_edl.py <workdir>` — a peak-only heuristic picker. Writes `auto_edl.json` ready to rename to `edl.json`. Decent for batch jobs; nowhere near as good as Claude reading the transcript.
- `python3 scripts/claude_pick.py <workdir>` — calls the Anthropic API to do real picking with a non-interactive Claude. Needs `ANTHROPIC_API_KEY` in the environment. Uses prompt caching on the transcript so re-running for different modes (`shorts` vs `highlights`) is cheap. Best autonomous option.

---

## Mode: `highlights`

A 3–7 minute compilation plus a handful of short clips.

Write `<workdir>/edl.json`:

```json
{
  "source": "source.mp4",
  "style": {
    "vertical_fit": "fill_height"
  },
  "main": {
    "title": "Best of <video title>",
    "segments": [
      {"start": 234.0, "end": 261.5, "label": "Cold open"},
      {"start": 12.0,  "end": 48.0,  "label": "How it started"},
      {"start": 305.0, "end": 348.0, "label": "The turn"}
    ]
  },
  "clips": [
    {"slug": "wipeout", "title": "Massive wipeout",
     "start": 412.0, "end": 433.5, "vertical": true}
  ]
}
```

Run:

```bash
uv run --quiet .claude/skills/youtube-edit/scripts/assemble.py <workdir>
```

Outputs in `<workdir>/out/`: `main.mp4`, `clips/NN_slug.mp4`, `clips_vertical/NN_slug.mp4`.

**Picking craft:**

- *Main:* narrative shape (cold open → setup → build → turn → close). Open with the punchiest moment, often from late in the video. Order is NOT chronological. End on a strong beat, not on something that begs "what happened next?"
- *Clips:* each must be self-contained. ~15–30s sweet spot. Hook in the first 2 seconds. Leave ~0.5–1s of breath at the end. Don't cut on the laugh — let it land.
- Pad starts ~0.5–1s before the first word (caption timestamps lag the audio).

**Top-level `style` block** — bundles a coherent look. Set a `preset` and override individual keys as needed:

```json
"style": {
  "preset": "tiktok",
  "look": "cinematic",
  "vertical_fit": "fill_height",
  "caption_style": "pop",
  "caption_mode": "word",
  "title_anim": "slide",
  "main_transition": "fadeblack",
  "accent": "#FFD24A",
  "use_music": true,
  "audio_clean": true,
  "sfx_on_title": true,
  "drop_fillers": true,
  "lut": "presets/teal_orange.cube",
  "logo_position": "top_right",
  "logo_opacity": 0.85,
  "logo_scale": 0.10,
  "end_card": {
    "title": "Show name",
    "subtitle": "Episode title",
    "cta": "Subscribe for more",
    "duration": 4.0
  }
}
```

| Preset | vertical_fit | caption_style | caption_mode | title_anim | look | transition | music |
|---|---|---|---|---|---|---|---|
| `default`     | blur_fill   | minimal | phrase | fade  | default   | fade      | on |
| `tiktok`      | blur_fill   | pop     | word   | slide | vibrant   | fade      | on |
| `gameplay`    | fill_height | pop     | word   | slide | vibrant   | slideleft | on |
| `podcast`     | blur_fill   | bold    | phrase | fade  | warm      | fade      | on |
| `cinematic`   | blur_fill   | minimal | phrase | fade  | cinematic | fadeblack | on |
| `documentary` | blur_fill   | bold    | phrase | fade  | bw        | fadeblack | on |

**Style key reference:**

- `vertical_fit` — `blur_fill` (talking-head; doesn't crop the subject), `fill_height` (screen-recorded; center column fills 9:16), `face_track` (smoothly follows the speaker; requires `crop_track.json` from `smart_crop.py`), or `auto` (heuristic — picks face_track / fill_height / blur_fill based on whether `crop_track.json` shows reliable face motion and the source aspect ratio).
- `caption_style` — `minimal` (white text + thick black stroke), `bold` (white text on dark rounded pill), `pop` (black text on accent-colored pill, MrBeast-adjacent).
- `caption_mode` — `phrase` (≤4-word chunks) or `word` (one word per chunk, TikTok-2024 style; falls back to phrase if more than 80 words in a clip).
- `title_anim` — `fade` (alpha-only) or `slide` (slide in from the left + fade).
- `look` — color-grade preset. Choices: `default`, `cinematic`, `warm`, `cool`, `bw`, `vibrant`, `punchy`. Layered ON TOP of an optional 3D LUT.
- `lut` — path (relative to workdir or absolute) to a `.cube` 3D LUT applied before the local color grade. Free LUTs at lutify.me/free-luts and freshluts.com.
- `main_transition` — default xfade type between main segments. Any of the 35+ supported types: `fade`, `fadeblack`, `fadewhite`, `dissolve`, `pixelize`, `wipeleft`/`right`/`up`/`down`, `slideleft`/`right`/`up`/`down`, `circleopen`/`close`, `radial`, `squeezeh`/`v`, `zoomin`, etc.
- `accent` — hex color used by title stripes, `pop` caption pills, end-card stripe & CTA, thumbnails.
- `auto_zoom` — subtle ken-burns ramp (1.0 → 1.06) on each top-loudness peak inside the clip. Pulls peaks from `signals.json`.
- `stabilize` — runs `deshake` on the source. Single-pass; for shaky footage. Off by default.
- `beat_sync` — when a `beats.json` is present, snap each main segment's end to the nearest beat. Off by default.
- `quality` — encoder preset: `fast` (iterate quickly), `balanced` (default), `high` (final delivery), `h265` (smaller files), `archival` (visually lossless).
- `export_formats` — list of extra outputs to render alongside `main.mp4`. Choices: `square` (1080×1080), `vertical` (1080×1920), `srt` (sidecar subtitles), `audio` (`main.m4a` for podcast distro). One EDL → many deliverables.
- `caption_mode` — `phrase` (≤4-word chunks; default), `word` (one word per chunk, TikTok-2024 style), or `word_active` (3-word window with the active word highlighted; the modern karaoke look).
- `sfx_on_title` — synthesized whoosh accent at each title-in. Off by default.
- `sfx_on_xfade` — synthesized whoosh accent at each main-segment xfade midpoint. Off by default.
- `pip_position`, `pip_scale`, `pip_round` — picture-in-picture knobs. Drop a `webcam.mp4` / `cam.mp4` / `pip.mp4` in the workdir and the assembler overlays it on landscape clips and main segments at the configured corner. Skipped on vertical clips (too cramped).
- `lut` — bundled name (`cinematic`, `warm_vintage`, `cool_noir`, `summer_pop`, `bw_high_contrast`) or path to a `.cube` file. Bundled LUTs in `luts/`.
- `use_music` — drop a `music.mp3`/`.m4a`/`.wav`/`.ogg` in the workdir; the main compilation gets a sidechain-ducked music bed. See [`MUSIC.md`](MUSIC.md) for legal sources.
- `audio_clean` — runs `afftdn` (FFT-based denoise) before loudnorm. Default on for podcast/documentary/cinematic presets.
- `sfx_on_title` — synthesized whoosh accent at the start of each title-in. Default off.
- `drop_fillers` — strip `um/uh/uhh/uhm/er` from captions before they're rendered.
- `logo_position` — `top_right` / `top_left` / `bottom_right` / `bottom_left`. A `logo.png` (or `.jpg`) in the workdir is auto-detected and overlaid at the corner.
- `logo_opacity` (0..1), `logo_scale` (fraction of width).
- `end_card` — branded card appended to the main compilation. Object with `title`, `subtitle`, `cta`, `duration` (seconds), `transition` (xfade type into the card; default `fadeblack`).

**Per-segment / per-clip overrides** (any of the style keys can be set on a single clip or main-segment):

- `vertical_fit`, `caption_style`, `caption_mode`, `title_anim`, `look`, `accent`, `audio_clean`, `sfx_on_title`
- `transition` (per main segment) — xfade INTO this segment.
- `speed` (per segment or clip) — `0.5` for slow-mo, `2.0` for fast-fwd. `-1` reverses. Caption timestamps and PNG durations adjust automatically.
- `captions: true|false` per clip. Default on for vertical, off for landscape.
- `stings: [{t, name, volume?}]` — comedy sound stings mixed at clip-relative `t` seconds. Built-in `name`s: `boom`, `airhorn`, `riser`, `pop`, `ding`, `trombone`. Drop a wav at `<workdir>/sfx/<name>.wav` to override or add new ones (e.g. a real vine-boom mp3).
- `overlays: [{t, dur, text, position?, style?, accent?, font_scale?}]` — free-form text overlays at clip-relative `t`. `position` is one of `top_left`, `top_center`, `top_right`, `mid_left`, `center`, `mid_right`, `bot_left`, `bot_center`, `bot_right`. `style` is `comment` (narrator-voice default — soft white with shadow), `pop` (accent pill), `bold` (dark pill), or `minimal` (white with stroke). Use ASCII glyphs (`<--`, `=>`) rather than `←` `→` — the bundled fonts don't include arrow chars.

---

## Mode: `shorts`

Pure short-form output — many vertical clips, no main compilation. Same EDL schema, just don't include a `main` block. Aim for 10–30 clips when the source is long.

For shorts specifically:

- Keep clips 12–30s. Anything longer loses retention.
- Always set `vertical: true` and prefer `vertical_fit: "fill_height"` for screen recordings.
- Use a punchy `title` per clip — that's the lower-third headline.
- Captions are on by default for vertical clips and significantly improve mute-watch retention.

After running `assemble.py`, outputs land in `<workdir>/out/clips_vertical/`.

---

## Mode: `quotecards`

Static PNG quote cards for posting on X / Instagram / LinkedIn / Slack.

Write `<workdir>/quotes.json`:

```json
{
  "title": "Billy Markus — Road to 1000 ELO",
  "uploader": "Billy Markus",
  "format": "square",
  "accent": "#FFD24A",
  "quotes": [
    {"text": "Just some shit poster who's 426. Who's ass at this game? I'm all right.",
     "attribution": "Billy Markus"},
    {"text": "Losses become lessens. My lesson is to be less bad."}
  ]
}
```

Run:

```bash
uv run --quiet .claude/skills/youtube-edit/scripts/quotecards.py <workdir>
```

Outputs `<workdir>/out/quotecards/NN_slug.png` — one PNG per quote.

`format`: `square` (1080×1080), `vertical` (1080×1920), or `landscape` (1920×1080). The script auto-shrinks the font when the quote is long.

**Picking quotes:** look for one-liners that survive without context — declarative, surprising, or self-deprecating. Avoid quotes that need a setup paragraph.

---

## Mode: `thumbnails`

YouTube thumbnail candidates: a frame from the source + huge bold caps + optional small subhead + accent stripe + light vignette + color pop.

Write `<workdir>/thumbnails.json`:

```json
{
  "source": "source.mp4",
  "format": "youtube",
  "accent": "#FFD24A",
  "thumbnails": [
    {"t": 412.5, "headline": "I LOST MY QUEEN", "subhead": "live reaction"},
    {"t": 6435,  "headline": "I GOT BETTER",   "subhead": "from 347 → 524"}
  ]
}
```

Run:

```bash
uv run --quiet .claude/skills/youtube-edit/scripts/thumbnails.py <workdir>
```

Outputs `<workdir>/out/thumbnails/NN_slug.jpg`.

`format`: `youtube` (1280×720), `square` (1080×1080), or `vertical` (1080×1920).

**Picking the frame:** prefer moments with strong facial expression, a clear visual subject (a chess board mid-blunder, a graph spiking, a face mid-reaction). The `loud_peaks` list is a great starting point — each peak is a candidate frame. Avoid frames in the middle of a transition (you'll get a half-resolved blur).

---

## Mode: `chapters`

Generate a YouTube-format chapter block to paste into the description.

Write `<workdir>/chapters.json`:

```json
{
  "chapters": [
    {"t": 0,    "title": "Intro"},
    {"t": 92,   "title": "First game starts"},
    {"t": 1018, "title": "First win"},
    {"t": 3238, "title": "Did not even see that"},
    {"t": 6435, "title": "I got better, yee-haw"}
  ]
}
```

Run:

```bash
python3 .claude/skills/youtube-edit/scripts/chapters.py <workdir>
```

Prints the markers and writes `<workdir>/out/chapters.txt`.

YouTube rules: first chapter must be at `0:00`, must be ascending, minimum 3. The script enforces the first; provide ≥3 yourself.

**Picking chapters:** look for content shifts — game starts, topic changes, key beats. Good chapters are one-glance descriptive, not coy. "First win" beats "It happens".

---

## Mode: `summary`

A markdown writeup of the video — show notes / blog post / newsletter copy.

This mode doesn't need a separate script. Read `transcript.json`, `signals.json`, and `source.info.json`, then write `<workdir>/out/summary.md` directly. Suggested structure:

```markdown
# {video title}

*{uploader} · {duration} · {date} · {URL}*

## TL;DR
2-3 bullets. What the viewer gets out of watching.

## Highlights
- **0:09** Cold open — short summary
- **17:10** First win — short summary
- ...

## Best lines
> Quote 1.

> Quote 2.

## Watch on YouTube
{URL}
```

Use `loud_peaks` to anchor the highlights list. Use the transcript to lift the best lines verbatim. Keep summaries scannable — bold the timestamps, keep bullets short.

---

## Mode: `supercut`

Combine moments from multiple YouTube videos into one compilation. Useful for "every time X says Y", channel best-of, or theme-based supercuts ("best wipeouts of the year").

1. Run the shared workflow (download → transcribe → analyze) once per URL into separate `<workdir>` directories under `vedit-runs/`.
2. Pick moments from each video by reading their transcripts and signals.
3. Create a new output workdir (e.g. `vedit-runs/supercut-1/`) and write `multi_edl.json` referencing the source workdirs:

   ```json
   {
     "style": {"preset": "tiktok"},
     "target_dims": [1280, 720],
     "main": {
       "title": "Best of the channel",
       "segments": [
         {"workdir": "../abc", "start": 412.0, "end": 433.5, "label": "From stream A"},
         {"workdir": "../xyz", "start":  12.0, "end":  35.0,
          "label": "From stream B", "transition": "wipeleft"}
       ]
     },
     "clips": [
       {"workdir": "../abc", "slug": "best_a", "title": "...",
        "start": 412.0, "end": 425.0, "vertical": true}
     ]
   }
   ```

   `workdir` paths resolve relative to the directory of `multi_edl.json` itself, so `../abc` works.

4. Run:

   ```bash
   uv run --quiet .claude/skills/youtube-edit/scripts/supercut.py vedit-runs/supercut-1
   ```

5. Output lands in `vedit-runs/supercut-1/out/main.mp4`, `out/clips/`, `out/clips_vertical/` — all parts normalized to `target_dims` so cross-source dimensions don't fight.

Music + logo are looked up in the **output** workdir, not the source workdirs — drop one `music.mp3` and one `logo.png` for the supercut as a whole.

---

## Combining modes

The shared signals + transcript pass means it's cheap to ship multiple outputs from one source — for example:

- `highlights` for the long-form recap
- `shorts` for daily TikTok drip
- `quotecards` for the X thread
- `thumbnails` for the YouTube upload
- `chapters` + `summary` for the description / newsletter

A reasonable default workflow when the user is non-specific: produce `highlights` + `shorts` + `chapters` + `summary` on the first pass. Quote cards and thumbnails are typically taste-driven so wait for direction.

## Requirements

- `yt-dlp` — `uv tool install yt-dlp` (download script puts `~/.local/bin` on PATH).
- `ffmpeg` — `brew install ffmpeg`. Standard build is sufficient (`eq`, `unsharp`, `xfade`, `acrossfade`, `loudnorm`, `fade`, `overlay`, `gblur`).
- `uv` — used by the Pillow-driven scripts (`assemble.py`, `quotecards.py`, `thumbnails.py`).
- **For the transcribe fallback:** `whisper-cpp` (`brew install whisper-cpp`) and a model file:
  ```bash
  mkdir -p ~/.cache/whisper-cpp
  curl -L -o ~/.cache/whisper-cpp/ggml-base.en.bin \
    https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin
  ```

## Status / TODO

See [`ROADMAP.md`](ROADMAP.md) for the full landscape of features and the open-source dependencies that would do the heavy lifting for each.

**Shipped:**

- ✅ Modes: `highlights`, `shorts`, `quotecards`, `thumbnails`, `chapters`, `summary`, `supercut`
- ✅ Six style presets (`default`, `tiktok`, `gameplay`, `podcast`, `cinematic`, `documentary`)
- ✅ Three caption styles (`minimal`, `bold`, `pop`)
- ✅ Two caption modes (`phrase`, `word`)
- ✅ Animated title slide-in
- ✅ Seven color-grade looks (`default`, `cinematic`, `warm`, `cool`, `bw`, `vibrant`, `punchy`)
- ✅ 3D LUT (`.cube`) support via `style.lut`
- ✅ 35+ xfade transitions per main segment
- ✅ Logo overlay (auto-detected from `logo.png`)
- ✅ End-screen card with title / subtitle / CTA
- ✅ Music bed with sidechain auto-ducking ([`MUSIC.md`](MUSIC.md))
- ✅ Speed ramps (`speed: 0.5` slow-mo / `2.0` fast-fwd)
- ✅ Audio cleanup (`afftdn` denoise) before loudnorm
- ✅ Synthesized SFX whoosh on title-in
- ✅ Filler-word stripping (`drop_fillers`)
- ✅ Auto-EDL generator from signals (`auto_edl.py`)
- ✅ Multi-URL supercut script

**Recently shipped:**

- ✅ Smart vertical face-tracking — `vertical_fit: "face_track"` follows the speaker
- ✅ `vertical_fit: "auto"` — picks the best mode from the source heuristically
- ✅ Karaoke captions (`caption_mode: "word_active"`) — TikTok-2024 active-word highlight
- ✅ Auto-zoom on reaction peaks — subtle kenburns at top loudness peaks
- ✅ Stabilization — single-pass `deshake`
- ✅ Beat-synced cuts — librosa beat detection + auto-snap segment ends
- ✅ Multi-format export — square + vertical + SRT subtitles + audio-only from one EDL
- ✅ Quality presets — fast / balanced / high / h265 / archival
- ✅ Picture-in-picture overlay (drop `webcam.mp4` in workdir)
- ✅ Reverse playback (`speed: -1`)
- ✅ Sound FX on title-in and xfade transitions
- ✅ 5 bundled CC0 3D LUTs (cinematic / warm_vintage / cool_noir / summer_pop / bw_high_contrast)
- ✅ Anthropic-SDK picker (`claude_pick.py`) for unattended runs
- ✅ Watch-folder mode (`watch.sh`) — drop URLs into a text file, pipeline runs them
- ✅ One-command quickstart (`quickstart.sh URL`) — download → transcribe → fix → analyze → auto-EDL
- ✅ Cookbook of working sample EDLs ([`EXAMPLES.md`](EXAMPLES.md))

**On the way (see ROADMAP.md):**

- ⏳ Speaker diarization labels (WhisperX integration)
- ⏳ Two-pass `vidstab` (better than `deshake`; needs custom-built ffmpeg)
- ⏳ Caption translation (argos-translate / LibreTranslate)
- ⏳ AI voiceover for narration (Piper TTS)
- ⏳ Auto-upload to YouTube / TikTok / Shorts (OAuth flows)
