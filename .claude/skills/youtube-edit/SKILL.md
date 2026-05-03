---
name: youtube-edit
description: Download a YouTube video (livestream or VOD) and turn it into a polished highlight compilation plus a set of short shareable clips. Cuts are picked by reading the transcript and audio/visual signals; output has lower-third titles, TikTok-style burned-in captions on vertical clips, color grade, loudness normalization, and crossfades. Use when the user gives a YouTube URL and asks for a highlight reel, supercut, best-of, compilation, shorts, or clips.
---

# youtube-edit

Turn a long YouTube video — including livestreams — into a tight highlight compilation and a set of short clips, polished enough to post.

## When to use

The user provides a YouTube URL (`youtube.com/watch?v=...`, `youtu.be/...`, or a live URL) and asks for any of: "highlight reel", "best of", "supercut", "compilation", "shorts", "clips", "edit this down".

## Inputs

- **url** (required) — YouTube video / livestream URL.
- **workdir** (optional) — defaults to `vedit-runs/<slug>/`.
- **target_main_minutes** (optional, default 5) — desired length of the main compilation.
- **num_clips** (optional, default 6) — how many short clips to produce.
- **vertical** (optional, default true) — also produce 9:16 versions of each clip.

## Workflow

Run from the project root. Scripts are in `.claude/skills/youtube-edit/scripts/`.

### 1. Download

```bash
bash .claude/skills/youtube-edit/scripts/download.sh <URL> <workdir>
```

Produces `<workdir>/source.mp4`, `<workdir>/source.en.vtt`, `<workdir>/source.info.json`.

For a still-live stream, edit the script to add `--live-from-start` or wait for it to end. Past livestreams (VODs) work as-is.

### 2. Parse transcript

```bash
python3 .claude/skills/youtube-edit/scripts/parse_transcript.py <workdir>
```

Writes `<workdir>/transcript.json` — `[{start, text}, ...]`, deduped from rolling auto-captions to one entry per new phrase.

### 3. Analyze signals

```bash
python3 .claude/skills/youtube-edit/scripts/analyze.py <workdir>
```

Writes `<workdir>/signals.json`:

- `loudness` — EBU R128 momentary loudness (LUFS) per 1-second window over the whole video
- `loud_peaks` — top ~30 loudest 1s windows, separated by ≥15s gaps. Reactions, laughter, shouts, and music swells live here.
- `scenes` — timestamps where the visual changed sharply (camera cut, screen swap). Useful as natural cut boundaries.
- `wpm` — sliding 10-second words-per-minute. Spikes mean fast/excited speech.

This step is slower than the others — for a 2-hour stream expect a few minutes.

### 4. Pick highlights

This is the part you (Claude) do. Read all three files:

```
<workdir>/transcript.json   # what was said and when
<workdir>/signals.json      # where the energy is and where the cuts are
<workdir>/source.info.json  # title, duration, uploader
```

For long videos the transcript will be big — read in chunks if needed.

#### How to find the moments

Don't read the transcript top-to-bottom hoping to spot gold. Triangulate:

1. **Start with `loud_peaks`** — these are pre-screened "something happened here" timestamps.
2. **For each peak, read the transcript around that time** (±20 seconds). Confirm whether it's a real moment (reaction / punchline / wipeout / reveal) or just background music / a cough.
3. **Cross-reference with `wpm` spikes** — high words-per-minute usually means excited talking, often the *setup* leading into a peak. Look 5-15s *before* a loudness peak to find the joke setup or the building tension.
4. **Use `scenes` for cut points** — when a moment sits between two scene changes, prefer cutting at the scene boundaries. Cuts feel more natural and you don't strand half a sentence.
5. **Skim the transcript directly** for content signals the audio doesn't surface: surprising claims, callbacks, named-entity reveals, escalating arguments, "wait..." / "no way" / "look at this" moments.

#### What makes a good clip

A short clip is good when a stranger scrolling past stops on it. That requires:

- **Self-containedness** — no setup the viewer doesn't have. If understanding the moment requires "earlier in the stream they were arguing about X", either include that setup *in the clip* or pick a different moment.
- **Hook in the first ~2 seconds** — open on the punchline / reaction / surprising visual, not on three seconds of "...so anyway".
- **Resolution before the cut** — don't cut on the laugh; let the laugh land. ~0.5-1s of breath at the end.
- **6-60s long.** Under 6s won't read; over 60s loses retention. Sweet spot 15-30s for shorts.

#### What makes a good main compilation

A compilation is good when it has **shape**:

- **Cold open** — start with the single most arresting moment, often from late in the video. Get the viewer hooked before they know what the show is about.
- **Build** — alternate setups and payoffs. Vary energy: a quiet beat after a loud one hits harder than two loud beats in a row.
- **Callbacks** — if a phrase or bit recurs, place those moments back-to-back even if they're far apart in source time.
- **Strong close** — end on a complete beat (a laugh, a button, a reveal), not on something that begs "what happened next?"
- **Order is not chronological** — you're cutting for narrative, not for a recap.

#### Timestamp craft

- Pad generously: start ~0.5–1s before the first word (auto-caption timestamps lag the audio), end ~0.5s after the last beat.
- Don't cut mid-word. Round outward.
- For reactions and punchlines, leave room for the laugh / silence at the end.
- When two adjacent transcript entries are clearly the same sentence/breath, treat them as one segment.

#### Output the EDL

Write `<workdir>/edl.json`:

```json
{
  "source": "source.mp4",
  "main": {
    "title": "Best of <video title>",
    "segments": [
      {"start": 234.0, "end": 261.5, "label": "Cold open: the bet"},
      {"start": 12.0,  "end": 48.0,  "label": "How it started"},
      {"start": 305.0, "end": 348.0, "label": "The turn"}
    ]
  },
  "clips": [
    {
      "slug": "wipeout",
      "title": "Massive wipeout at minute 7",
      "start": 412.0,
      "end": 433.5,
      "vertical": true
    }
  ]
}
```

Field reference:

- `main.segments[].label` — shows as a lower-third title card for the first ~3s of that segment. Keep it short (≤6 words).
- `clips[].title` — same, on the clip. Skip the field for no title card.
- `clips[].vertical` — also produce a 1080×1920 version with blurred-fill background.
- `clips[].captions` — burn captions on this clip. Defaults to **on** for vertical, **off** for landscape. Set `false` to disable, `true` to force on.

### 5. Assemble

```bash
uv run --quiet .claude/skills/youtube-edit/scripts/assemble.py <workdir>
```

(`uv run` brings in Pillow for the title/caption rendering. The script's PEP 723 metadata declares the dep — no venv to manage.)

What the assembler does for each cut:

1. Frame-accurate trim from source.
2. Mild color grade (`eq` contrast/saturation + light `unsharp`).
3. Vertical clips: blurred-fill 9:16 background with the 16:9 source centered on top.
4. Title card overlay with 0.4s fade-in, ~2.2s hold, 0.4s fade-out.
5. Vertical clips: caption chunks (≤4 words each) burned in TikTok-style — big bold white with thick black stroke.
6. Audio: short fades at the cut boundaries + EBU R128 loudness normalization to social-media targets.

Main compilation segments are joined with 0.5s `xfade` video crossfades and matched `acrossfade` audio crossfades, then a final loudnorm pass.

Outputs land in `<workdir>/out/`:

- `main.mp4` — the compilation
- `clips/NN_slug.mp4` — landscape clips
- `clips_vertical/NN_slug.mp4` — 9:16 versions

### 6. Report back

Tell the user where the files are. Give each clip a one-line pitch ("Clip 03 — the moment everyone realized he wasn't kidding"). Don't dump the EDL at them.

## Requirements

- `yt-dlp` — install with `uv tool install yt-dlp`. The download script puts `~/.local/bin` on PATH so a uv-installed yt-dlp is found.
- `ffmpeg` — install with `brew install ffmpeg`. The polish pipeline uses `eq`, `unsharp`, `xfade`, `acrossfade`, `loudnorm`, `fade`, `overlay`, `gblur` — all in the standard build.
- `uv` — used by `assemble.py` to provide Pillow on the fly.

## Not yet implemented

- **No-captions fallback.** If the video has no auto-captions, the transcript step fails. (Future: whisper transcription.)
- **Auto-upload** to YouTube / TikTok / Shorts. Files land on disk for the user to upload.
- **Animated transitions on clips.** Clips currently use hard cuts (clean, fast). Could add slide/zoom motion.
- **Brand intro / outro cards** for the main compilation.
