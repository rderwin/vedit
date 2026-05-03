# vedit

Tools for turning long YouTube videos (especially livestreams) into tight, shareable cuts.

## youtube-edit (Claude Code skill)

Drop a YouTube URL on Claude Code and ask it to "make a highlight reel and a few clips". It will:

1. Download the video and auto-captions with `yt-dlp`.
2. Parse the captions into a clean, deduped, timestamped transcript.
3. Skim the transcript and pick highlights — both a narrative compilation and a set of short, shareable clips.
4. Cut and assemble with `ffmpeg`. Optionally produces 9:16 versions for shorts.

See [`.claude/skills/youtube-edit/SKILL.md`](.claude/skills/youtube-edit/SKILL.md) for the full workflow and EDL schema.

### Requirements

```bash
brew install ffmpeg
uv tool install yt-dlp
```

### Output

Per-run output lands in `vedit-runs/<slug>/out/`:

- `main.mp4` — the highlight compilation
- `clips/NN_slug.mp4` — landscape clips
- `clips_vertical/NN_slug.mp4` — 9:16 versions (when requested)

`vedit-runs/` is gitignored — these files can be large.
