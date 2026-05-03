---
name: youtube-edit
description: Download a YouTube video (livestream or VOD), pick the best moments, and produce a compelling highlight compilation plus several short shareable clips. Use when the user gives a YouTube URL and asks to make a highlight reel, supercut, best-of, or shorts.
---

# youtube-edit

Turn a long YouTube video — including livestreams — into a tight highlight compilation and a set of short clips ready to share.

## When to use

The user provides a YouTube URL (`youtube.com/watch?v=...`, `youtu.be/...`, or a live URL) and asks for any of: "highlight reel", "best of", "supercut", "compilation", "shorts", "clips", or "edit this down".

## Inputs

- **url** (required) — YouTube video / livestream URL.
- **workdir** (optional) — defaults to `vedit-runs/<slug>/` under the project root.
- **target_main_minutes** (optional) — desired length of the main compilation. Default 5.
- **num_clips** (optional) — how many short clips to produce. Default 6.
- **vertical** (optional) — also produce 9:16 versions of clips. Default true.

## Workflow

All scripts live in `scripts/` next to this file. Run from the project root.

### 1. Download

```bash
bash .claude/skills/youtube-edit/scripts/download.sh <URL> <workdir>
```

Produces:

- `<workdir>/source.mp4` — full video (merged best video+audio up to 1080p)
- `<workdir>/source.en.vtt` (or similar) — auto-captions
- `<workdir>/source.info.json` — yt-dlp metadata (title, duration, uploader, …)

For a still-live livestream, edit the script to add `--live-from-start` or wait for it to end. Past livestreams (VODs) just work.

### 2. Parse the transcript

```bash
python3 .claude/skills/youtube-edit/scripts/parse_transcript.py <workdir>
```

Writes `<workdir>/transcript.json` — a list of `{start, text}` entries, deduped from YouTube's rolling auto-captions into one entry per new phrase.

### 3. Pick highlights (this is your job)

Read `<workdir>/transcript.json` and `<workdir>/source.info.json`. For long videos the transcript may be big — skim it in chunks if needed.

You're picking two things:

**Main compilation.** A narrative — pulls the viewer in, builds, pays off. Aim for the requested length (default ~5 min). Open with a hook (often from later in the video — a punchy reaction or surprise). Cut between segments at natural breath points; don't strand half-sentences. Close on a strong beat.

**Short clips.** Each is a self-contained moment, ~15–60 seconds, that lands without setup the viewer doesn't have. A complete joke, wipeout, reaction, hot take, or reveal. Each should be shareable as-is.

Write `<workdir>/edl.json`:

```json
{
  "source": "source.mp4",
  "main": {
    "title": "Best of <video title>",
    "segments": [
      {"start": 234.0, "end": 261.5, "label": "cold open: the bet"},
      {"start": 12.0,  "end": 48.0,  "label": "intro"},
      {"start": 305.0, "end": 348.0, "label": "the turn"}
    ]
  },
  "clips": [
    {"slug": "wipeout", "title": "Massive wipeout at minute 7",
     "start": 412.0, "end": 433.5, "vertical": true}
  ]
}
```

Timestamp tips:

- Pad generously: start ~0.5–1s before the first word, end ~0.5s after the last beat. Auto-caption timestamps mark when words *appear*, which can lag the audio by a moment.
- Don't pick clips shorter than ~6s — they won't read on social.
- For reactions and punchlines, leave room at the end for the laugh / silence.
- Order the main `segments` for narrative impact, not chronologically — you can put a later moment first as a cold open.

### 4. Assemble

```bash
python3 .claude/skills/youtube-edit/scripts/assemble.py <workdir>
```

Produces:

- `<workdir>/out/main.mp4` — the compilation
- `<workdir>/out/clips/NN_slug.mp4` — landscape clips
- `<workdir>/out/clips_vertical/NN_slug.mp4` — 9:16 versions (when `vertical: true`)

### 5. Report back

Tell the user where the files are. Give each clip a one-line pitch ("Clip 03: the moment everyone realized he wasn't kidding"). Don't dump the EDL at them.

## Requirements

- `yt-dlp` — install with `uv tool install yt-dlp`. The download script puts `~/.local/bin` on PATH so a uv-installed yt-dlp is found.
- `ffmpeg` — install with `brew install ffmpeg`.

## Not yet implemented

- **No-captions fallback.** If the video has no auto-captions, you need to transcribe with whisper or similar and produce a VTT manually. (TODO.)
- **Auto-upload** to YouTube / TikTok / Shorts. Files land on disk; the user uploads.
- **Captions burn-in / titles overlays.** Cuts are clean — no on-screen text yet.
