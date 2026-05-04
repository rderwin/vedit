# Cookbook — recipes for common use cases

The skill has a lot of knobs. This file gives you a working starting EDL for the most common things people make.

Each recipe assumes you've already run the shared workflow (`download.sh` → `transcribe.sh` → `parse_transcript.py` → `fix_transcript.py` → `analyze.py`) for that workdir. Then drop the EDL below into `<workdir>/edl.json` and run `assemble.py`.

---

## 1. Comedy / podcast highlight reel

For a long-form interview or comedy set — narrative arc with breath, warm tone, conservative captions.

```json
{
  "source": "source.mp4",
  "style": {
    "preset": "podcast",
    "audio_clean": true,
    "drop_fillers": true,
    "end_card": {
      "title": "Show Name",
      "subtitle": "Episode 42 — full episode link in description",
      "cta": "Subscribe for more",
      "duration": 4.0
    },
    "export_formats": ["audio", "srt"]
  },
  "main": {
    "title": "Best of Episode 42",
    "segments": [
      {"start": 1843, "end": 1882, "label": "Cold open: the bet"},
      {"start": 124, "end": 168, "label": "How they met"},
      {"start": 932, "end": 996, "label": "The argument"},
      {"start": 2240, "end": 2295, "label": "Plot twist"},
      {"start": 3120, "end": 3165, "label": "Callback"}
    ]
  },
  "clips": [
    {"slug": "the_bet", "title": "Did he actually do it?",
     "start": 1843, "end": 1882, "vertical": true},
    {"slug": "argument", "title": "This is so dumb",
     "start": 932, "end": 996, "vertical": true}
  ]
}
```

What's happening:
- `preset: podcast` → warm color grade, bold-pill captions, fade transitions
- `audio_clean: true` runs `afftdn` denoise (default-on for podcast preset; explicit here for clarity)
- `drop_fillers: true` strips um/uh from captions
- `end_card` adds a 4s subscribe card at the tail
- `export_formats: ["audio", "srt"]` produces `main.m4a` (podcast distro) + `main.srt` (sidecar subtitles)

---

## 2. TikTok shorts factory (no main compilation)

You scraped a 2-hour stream; you want 20 self-contained shorts with TikTok-2024 captions. No main.

```json
{
  "source": "source.mp4",
  "style": {
    "preset": "tiktok",
    "vertical_fit": "auto",
    "caption_mode": "word_active",
    "caption_style": "pop",
    "auto_zoom": true,
    "drop_fillers": true,
    "accent": "#FFD24A"
  },
  "main": {},
  "clips": [
    {"slug": "moment_1", "title": "Wait what?",
     "start": 412, "end": 428, "vertical": true},
    {"slug": "moment_2", "title": "He actually did it",
     "start": 891, "end": 915, "vertical": true},
    {"slug": "moment_3", "title": "Best wipeout",
     "start": 1243, "end": 1267, "vertical": true}
    // ... 17 more
  ]
}
```

What's happening:
- `vertical_fit: "auto"` picks `face_track` if a clear speaker is on camera, `fill_height` if the content is screen-recorded, `blur_fill` otherwise
- `caption_mode: "word_active"` shows a 3-word window with the active word highlighted (modern TikTok-2024 style)
- `auto_zoom: true` adds a subtle 1.0 → 1.06 ken-burns ramp at each loud peak inside the clip
- `drop_fillers: true` strips um/uh
- `accent: "#FFD24A"` cycles through everything (caption pills, lower-third stripe, end card if present)

To enable face-tracking: `python3 scripts/smart_crop.py <workdir>` first.

---

## 3. Gameplay / screen-recorded content

Chess, esports, code walkthroughs — content lives in the center column of a 16:9 frame.

```json
{
  "source": "source.mp4",
  "style": {
    "preset": "gameplay",
    "vertical_fit": "fill_height",
    "look": "vibrant",
    "caption_mode": "word",
    "main_transition": "slideleft",
    "sfx_on_xfade": true,
    "auto_zoom": true,
    "lut": "summer_pop"
  },
  "main": {
    "title": "Best of Stream 47",
    "segments": [
      {"start": 200, "end": 240, "label": "First win"},
      {"start": 1240, "end": 1290, "label": "The clutch"},
      {"start": 2890, "end": 2940, "label": "Disaster"}
    ]
  },
  "clips": [
    {"slug": "clutch", "title": "How did he see that?",
     "start": 1240, "end": 1290, "vertical": true}
  ]
}
```

What's happening:
- `vertical_fit: "fill_height"` — the action fills the 9:16 frame instead of sitting tiny inside a blur (`face_track` would chase chrome around the edges; for screen-rec content `fill_height` is right)
- `lut: "summer_pop"` + `look: "vibrant"` — punched-up colors for high-energy gameplay
- `main_transition: "slideleft"` — slide transitions feel snappier than fades for fast content
- `sfx_on_xfade: true` adds a quick whoosh at each transition

---

## 4. Quote cards for a Twitter thread

You want 8 PNG quote cards from the best one-liners — for a Twitter thread or LinkedIn post.

```json
{
  "title": "Show Name — Episode 42",
  "uploader": "Speaker Name",
  "format": "square",
  "accent": "#FFD24A",
  "quotes": [
    {"text": "If you don't know what you want, the universe will pick for you."},
    {"text": "Confidence isn't 'they will like me.' It's 'I'll be fine if they don't.'"},
    {"text": "The best ideas sound like obvious ideas, in retrospect."},
    {"text": "Most arguments aren't about facts; they're about which facts get attention."}
  ]
}
```

Save as `<workdir>/quotes.json`, then:

```bash
uv run --quiet scripts/quotecards.py <workdir>
```

For 9:16 mobile quotes: `"format": "vertical"`. For 16:9 LinkedIn share images: `"format": "landscape"`.

---

## 5. Thumbnail candidates from the EDL

You've already picked the best moments — make 8 thumbnails fast without writing `thumbnails.json`:

```bash
uv run --quiet scripts/thumbnails.py <workdir> --from-edl
```

This reads `edl.json` clips, picks the midpoint frame of each, uses the clip title as the bold-caps headline, and writes `out/thumbnails/NN_slug.jpg`.

For a YouTube upload, you only need one thumbnail — pick the best from the candidates and discard the rest. For A/B testing on TubeBuddy or YouTube's experiments, ship the top 3.

---

## 6. Reaction edit (gameplay + facecam)

You have a screen recording AND a webcam track. You want them combined.

1. Drop the screen recording at `<workdir>/source.mp4` and the webcam at `<workdir>/webcam.mp4`.
2. EDL:

```json
{
  "source": "source.mp4",
  "style": {
    "preset": "gameplay",
    "vertical_fit": "fill_height",
    "pip_position": "bottom_right",
    "pip_scale": 0.20,
    "logo_position": "top_right"
  },
  "main": {
    "title": "Reactions",
    "segments": [
      {"start": 100, "end": 140, "label": "..."},
      {"start": 240, "end": 280, "label": "..."}
    ]
  },
  "clips": []
}
```

What's happening:
- `webcam.mp4` is auto-detected — overlays in `bottom_right` at 20% width with a thin dark border
- `logo.png` (if present) sits in `top_right` so it doesn't fight the webcam corner
- Vertical clips skip the PiP (too cramped); landscape and main keep it

---

## 7. Music-driven montage

You have a high-energy track and want cuts to land on the beat.

1. Drop `music.mp3` in the workdir.
2. Run beat detection:
   ```bash
   uv run --quiet scripts/beats.py <workdir>
   ```
3. EDL:

```json
{
  "source": "source.mp4",
  "style": {
    "preset": "tiktok",
    "use_music": true,
    "beat_sync": true,
    "look": "vibrant",
    "main_transition": "wipeleft"
  },
  "main": {
    "title": "Best of",
    "segments": [
      {"start": 100, "end": 105, "label": "..."},
      {"start": 240, "end": 246, "label": "..."},
      {"start": 380, "end": 385, "label": "..."}
    ]
  },
  "clips": []
}
```

What's happening:
- `use_music: true` mixes the music with sidechain ducking (already on for `tiktok` preset)
- `beat_sync: true` shifts each segment's end to the nearest beat (within ±1.5s)
- Result: cuts land on the music. No human picking required.

---

## 8. Cinematic short

Not for social — a 60-second cinematic short with deep color grade, slow transitions, and a black-fade-to-end.

```json
{
  "source": "source.mp4",
  "style": {
    "preset": "cinematic",
    "look": "cinematic",
    "lut": "cinematic",
    "main_transition": "fadeblack",
    "title_anim": "fade",
    "audio_clean": true,
    "stabilize": true,
    "quality": "high",
    "end_card": {
      "title": "Director Name",
      "subtitle": "[short film name]",
      "cta": " ",
      "duration": 3.0
    }
  },
  "main": {
    "title": "...",
    "segments": [
      {"start": 30, "end": 50, "label": " "},
      {"start": 120, "end": 150, "label": " "},
      {"start": 240, "end": 270, "label": " "}
    ]
  },
  "clips": []
}
```

What's happening:
- Empty `label: " "` suppresses the lower-third for clean shots without on-screen text
- `lut: "cinematic"` (bundled) + `look: "cinematic"` stack — the LUT establishes the look, the look adjusts contrast
- `main_transition: "fadeblack"` slows the pacing
- `quality: "high"` writes higher-bitrate output (libx264 preset slow, CRF 17)

---

## 9. Multi-format batch (one EDL → YouTube + IG Reel + TikTok)

```json
{
  "source": "source.mp4",
  "style": {
    "preset": "tiktok",
    "vertical_fit": "auto",
    "export_formats": ["square", "vertical", "srt", "audio"]
  },
  "main": {"title": "...", "segments": [/* ... */]},
  "clips": [/* ... */]
}
```

Produces:
- `out/main.mp4` — landscape 16:9 (YouTube)
- `out/main_square.mp4` — 1:1 (LinkedIn, IG feed)
- `out/main_vertical.mp4` — 9:16 (Reels, Shorts)
- `out/main.srt` — sidecar subtitles for upload
- `out/main.m4a` — audio-only (podcast distro)
- `out/clips/...` and `out/clips_vertical/...` as usual

One render, five deliverables.

---

## 10. Channel best-of (multi-URL supercut)

Pick moments from 5 different videos on a channel.

1. Run the shared workflow on each URL into separate workdirs (`vedit-runs/abc/`, `vedit-runs/def/`, ...).
2. Create `vedit-runs/best-of-channel/multi_edl.json`:

```json
{
  "style": {"preset": "tiktok"},
  "target_dims": [1280, 720],
  "main": {
    "title": "Best of @channel — March 2026",
    "segments": [
      {"workdir": "../abc", "start": 412, "end": 433, "label": "From: Stream A"},
      {"workdir": "../def", "start": 1240, "end": 1270, "label": "From: Stream B"},
      {"workdir": "../ghi", "start": 880, "end": 910, "label": "From: Stream C"}
    ]
  },
  "clips": []
}
```

3. Run:

```bash
uv run --quiet scripts/supercut.py vedit-runs/best-of-channel
```

Each segment renders against its own `source.mp4`, all are normalized to `target_dims`, then assembled with crossfades.

---

## Tips when adapting these recipes

- **Always run `fix_transcript.py`** before picking — it auto-detects domain mishears (e.g. "chest" → "chess" on a chess stream).
- **Auto-EDL is faster than hand-writing JSON for first pass.** Run `auto_edl.py` (peak heuristic) or `claude_pick.py` (SDK; needs API key) to get a starter EDL, then edit the picks by hand.
- **Quality preset "fast" cuts iteration time in half** when tuning picks. Switch to "high" or "balanced" for the final render.
- **Run `quickstart.sh <URL>` first**, read its `auto_edl.json`, then tune the EDL from there. Quickstart is idempotent — it skips steps whose outputs already exist.
