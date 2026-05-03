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

### 3. Extract editing signals

```bash
python3 .claude/skills/youtube-edit/scripts/analyze.py <workdir>
```

Writes `<workdir>/signals.json` with:

- `loudness` — EBU R128 momentary loudness per 1-second window
- `loud_peaks` — top ~30 loudest 1s windows (≥15s gaps), pre-screened for "something happened"
- `scenes` — visual scene-change timestamps
- `wpm` — sliding 10s words-per-minute curve

Use these signals to **find** moments. Don't read the transcript top-to-bottom — triangulate:

1. Start with `loud_peaks`. Each is a candidate moment.
2. For each peak, read the transcript ±20s to confirm what's actually there.
3. Cross-check `wpm` spikes — high words/min often marks the *setup* leading into a peak. Look 5–15s before a loudness peak.
4. `scenes` are clean cut boundaries when present.
5. Skim the transcript for content signals the audio doesn't surface — surprising claims, callbacks, named-entity reveals, "wait..." / "no way" / "look at this" beats.

You're picking moments here, not committing to outputs yet — the same picks feed multiple modes below.

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

**Style options (top-level `style` block):**

- `vertical_fit`: `"blur_fill"` (default; scales to fit width with blurred-fill background — best for talking-head video) or `"fill_height"` (scales to fill the 9:16 height and crops sides — best for screen-recorded streams, gameplay, chess.com, etc., where the action lives in the center column).

**Per-clip overrides:**

- `vertical_fit`: same options as the global style, set per-clip.
- `captions`: `true`/`false`. Default on for vertical, off for landscape.

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

Combine the same kind of moment from multiple YouTube URLs into one compilation. Useful for "every time X says Y" or "channel best-of".

1. Run the shared workflow (download → transcribe → analyze) once for each URL into separate `<workdir>` directories.
2. Pick moments from each (you'll likely build per-video EDLs, then merge).
3. Hand-build a `multi_edl.json` listing each clip's source workdir + start/end:

   ```json
   {
     "clips": [
       {"workdir": "vedit-runs/abc",  "start": 412.0, "end": 433.5, "title": "..."},
       {"workdir": "vedit-runs/xyz",  "start": 12.0,  "end": 35.0,  "title": "..."}
     ]
   }
   ```

4. *(TODO: scripts/supercut.py — runs each cut through the polish pipeline against the right source, then xfades them together. Until that lands, fall back to per-video `assemble.py` runs followed by manual ffmpeg concat.)*

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

- ✅ `highlights`, `shorts`, `quotecards`, `thumbnails`, `chapters`, `summary`
- ⏳ `supercut` — multi-URL combinator script (see Mode section above)
- ⏳ Word-level captions — single-word pop-in with active highlight (TikTok 2024 style). Requires `whisper-cli -ml 1` for word-level VTT.
- ⏳ Animated title slide-in — currently fade-only.
- ⏳ Auto-zoom on reaction peaks — small kenburns ramp during top loudness peaks.
- ⏳ Music bed with auto-ducking under speech.
- ⏳ Auto-upload to YouTube / TikTok / Shorts.
