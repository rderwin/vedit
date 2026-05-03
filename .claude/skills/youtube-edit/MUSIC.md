# Music for the assembler

The skill mixes a single music track under the main compilation with sidechain ducking — drop a file at `<workdir>/music.<ext>` (mp3/m4a/wav/ogg) and the assembler picks it up automatically (when the active style preset has `use_music: true`, which is the default).

Mixing details:
- Music plays at -28 dB nominal, ducked to about -36 dB whenever speech is loud.
- Filter chain: `sidechaincompress=threshold=0.03:ratio=10:attack=5ms:release=400ms` with a 4× sidechain gain bump so it triggers on normal speaking volume.
- The music is `-stream_loop -1`'d, so a 90-second track will loop seamlessly across an 8-minute compilation.
- Final loudnorm pass targets -16 LUFS for YouTube and -14 LUFS for shorts.

## Where to get music that's safe on YouTube

These sources let you upload to YouTube without Content ID strikes and keep monetization on. Always read the specific track's licence — most are CC-BY (attribute) or CC0 (no attribution), and a few are royalty-free with restrictions.

| Source | Cost | Attribution | Notes |
|---|---|---|---|
| **YouTube Audio Library** (`youtube.com/audiolibrary`) | Free | Sometimes | Official; download as MP3 once signed in. Filter by mood/genre/duration/instrument. |
| **YouTube Creator Music** | Royalty share | No | Inside YT Studio; use a track and YT splits revenue with the rights holder. |
| **Pixabay Music** (`pixabay.com/music`) | Free | None (CC0) | Huge library, quality varies, no signup needed. |
| **Free Music Archive** (`freemusicarchive.org`) | Free | Per-track | Filter by licence — pick CC-BY or CC0. |
| **Uppbeat** (`uppbeat.io`) | Free tier | Yes (free) | Good for vlog energy. Paid tier removes attribution. |
| **Bensound** (`bensound.com`) | Free tier | Yes (free) | Covers ambient / cinematic / corporate well. |
| **ccMixter** (`ccmixter.org`) | Free | Per-track | CC remixes; check each licence. |
| **Epidemic Sound** | Subscription | None | Industry standard for big YouTubers; commercial use covered. |
| **Artlist** | Subscription | None | Same tier as Epidemic. |

Quick recommendation: **Pixabay Music** is the lowest-friction starting point — CC0, no signup, no attribution.

## How to attribute (when required)

If a track requires attribution, paste this into the YouTube description (or wherever the video is posted):

```
Music: <track title> by <artist>
Source: <track URL>
Licence: <CC-BY 4.0 / CC0 1.0 / etc>
```

A `<workdir>/music.txt` next to `music.mp3` is a good place to keep the attribution alongside the file so it doesn't get lost.

## Troubleshooting

- **"music too loud / crowding the voice"** — turn down the music input volume in `assemble_main` (currently `volume=0.40`). Or use a chiller track — energetic tracks fight the voice harder.
- **"music clips off at end of compilation"** — the source track is shorter than the compilation. The assembler uses `-stream_loop -1` so this should not happen; verify the file isn't being read as a static image (some weird container variants).
- **"music should NOT play on a specific clip"** — set `style.use_music: false` (top-level), or per-clip override (TODO — currently per-clip music toggling isn't supported; the bed only applies to the main compilation, not individual clips).
