# Roadmap — what'd make this match a pro YouTube editor

Honest gap analysis between this skill and tools like Adobe Premiere / DaVinci Resolve / Descript / CapCut. Each item lists the open-source dependency that does the heavy lifting so we don't reinvent.

Status legend: ✅ shipped · 🟡 partial · ⏳ next · 💤 later

## A. Capture & ingest

- ✅ **YouTube download** — `yt-dlp`
- ✅ **Auto-captions parse** — YouTube VTT or `whisper-cpp`
- ⏳ **Word-level transcription** — `whisper-cpp -ml 1`, `whisperx` (word alignment + diarization), `whisper-timestamped`
- ⏳ **Speaker diarization** — `pyannote-audio` (open, MIT-style)
- ⏳ **Multi-camera ingest** — sync N cameras by audio cross-correlation; `aubio` / `librosa.cross-correlation`
- 💤 **Live capture** — OBS Studio (open, controllable via `obs-websocket`)

## B. Transcription quality

- 🟡 **Whisper base.en** (current — fast, ok)
- ⏳ **Whisper medium / large-v3** — better for accents, technical jargon. Same `whisper-cpp` binary.
- ⏳ **WhisperX** (GitHub: m-bain/whisperx) — word-level alignment via wav2vec2, plus diarization. The standard for "professional" caption pipelines.
- ⏳ **Faster-Whisper** (GitHub: SYSTRAN/faster-whisper) — CTranslate2-based, 4× faster than openai-whisper at same accuracy.
- ⏳ **Filler-word stripping** — regex `\b(um|uh|like|you know|i mean)\b`, also drop short hesitations identified by VAD (`silero-vad`, MIT).

## C. Smart picking

- ✅ **Loudness peaks** (`ebur128`)
- ✅ **Scene cuts** (ffmpeg `scdet`)
- ✅ **Words-per-minute spikes** (transcript)
- ⏳ **Auto-EDL generator** — emit starter EDL from signals; *shipping this turn*
- ⏳ **Sentiment / emotion** — `vader-sentiment` (lex-based, free) or `transformers` j-hartmann/emotion-english-distilroberta-base
- ⏳ **Keyword spike detection** — `KeyBERT` for the most surprising terms in each window
- ⏳ **CLIP-based "find the moment that looks like X"** — `open_clip_torch`, query frames by text
- ⏳ **Local LLM picker** — `ollama` running `llama3.1:8b` or `mistral`, given the transcript, returns "the 20 best moments and why"
- 💤 **Auto-thumbnail face detection** — `mediapipe` or `OpenCV haar` to pick frames where the speaker's face is clearly readable

## D. Audio engineering

- ✅ **EBU R128 loudnorm** (ffmpeg)
- ✅ **Sidechain music ducking** (ffmpeg `sidechaincompress`)
- ✅ **Cut-boundary fade in/out** (ffmpeg `afade`)
- ⏳ **Noise reduction** — `rnnoise` (open, neural net based, ffmpeg has `arnndn` filter that loads RNNoise models)
- ⏳ **De-essing** — ffmpeg `deesser` filter
- ⏳ **Multiband compression** — ffmpeg `acompressor` (single-band) or chain multiple bands
- ⏳ **Voice EQ presets** — chain ffmpeg `equalizer` + `highpass` + `lowshelf` for "podcast warm" / "telephone" / "bright"
- ⏳ **Speech enhancement (neural)** — `denoiser` (Facebook Research, MIT) or `voicefixer` (open) for serious cleanup
- ⏳ **Click/pop removal** — `audacity` CLI (open) has built-in repair
- ⏳ **Auto-leveling between speakers** — per-speaker loudnorm using diarization
- 💤 **Stem separation** — `Spleeter` / `Demucs` (Facebook, MIT) to isolate music vs. speech in mixed sources

## E. Captions / subtitles

- ✅ **Burned-in 4-word chunks** (current, Pillow renderer)
- ✅ **Three caption styles** (`minimal`, `bold`, `pop`)
- ⏳ **Word-level captions with active highlight** — *shipping this turn*
- ⏳ **Karaoke-timed captions** — proper `.ass` format with `\k` tags; render via ffmpeg's `subtitles` filter (needs `libass`-built ffmpeg — note: brew default does NOT include this; either rebuild ffmpeg from `homebrew/ffmpeg` tap with `--with-libass`, or render words via Pillow as we do)
- ⏳ **Sidecar SRT/VTT export** alongside the burned video
- ⏳ **Speaker name labels** — diarization → "Alice:" prefix on each line
- ⏳ **Translate captions** — `argos-translate` (open, offline) or `LibreTranslate` (open, self-host)
- ⏳ **Animated word reveal** (scale-bounce on each word) — Pillow + per-frame compositing
- 💤 **Auto-emoji insertion** — sentiment → relevant emoji at line breaks (kitschy but very TikTok)

## F. Color / look

- 🟡 **Mild contrast/saturation/sharpening** (current)
- ⏳ **Color-grade looks** — *shipping this turn* (`cinematic`, `warm`, `cool`, `bw`, `vibrant`)
- ⏳ **3D LUT support** — ffmpeg `lut3d` filter loads `.cube` files. Free LUTs at `lutify.me/free-luts` and `freshluts.com` (public-domain or CC).
- ⏳ **Vignette** — ffmpeg `vignette` filter
- ⏳ **Film grain** — ffmpeg `noise` filter or grain plates from `cinegrain.com` (paid) / DaVinci grain plates (free)
- ⏳ **Glow / bloom** — `gblur` + `blend=mode=screen`
- ⏳ **Chromatic aberration** — split RGB channels and offset slightly
- ⏳ **Letterbox bars** — `pad` filter
- 💤 **Auto-detect-and-grade** — analyze histogram, choose preset

## G. Composition

- ✅ **Vertical 9:16 with blurred-fill background**
- ✅ **Vertical 9:16 fill-height (center crop)**
- ⏳ **Smart auto-crop for vertical** — `mediapipe` face/pose detection → crop window tracks the speaker so they stay centered. This is the killer feature of CapCut / Submagic.
- ⏳ **Picture-in-picture** — webcam in corner overlay (reaction edits)
- ⏳ **Multi-cam edit** — pick from N camera angles per moment
- ⏳ **Greenscreen / chroma key** — ffmpeg `chromakey` + `colorkey`
- 💤 **3D parallax pan** — generate depth map (`MiDaS`, MIT) and pan the perspective

## H. Motion graphics / overlays

- ✅ **Lower-third title cards with accent stripe**
- ✅ **Animated title slide-in**
- ⏳ **Logo / watermark overlay** — *shipping this turn*
- ⏳ **End-screen card** — branded card with subscribe + show name
- ⏳ **Lottie animation overlay** — render Lottie JSON to PNG sequence with `puppeteer` headless or `lottie-renderer` (open) and overlay
- ⏳ **Sound-FX-synced motion** — pop/whoosh sync to title-in / caption pop
- ⏳ **Animated counters** — score, ELO, view count
- 💤 **Custom motion graphics** — `Glaxnimate` (GPL) or generate via Python compositor

## I. Transitions

- ✅ **Crossfade** between main segments
- ⏳ **xfade variety** — *shipping this turn* (35+ types: wipeleft, slideup, dissolve, pixelize, circleopen, distance, fadeblack/white, etc.)
- ⏳ **Match cuts** — manual EDL setting
- ⏳ **Whip-pan transition** — fast horizontal pan + motion blur on cut
- ⏳ **Glitch transition** — RGB split + `noise` + offset for one frame
- 💤 **Beat-synced cuts** — `librosa.beat.beat_track` (open) → quantize cut points to detected beats

## J. Music & sound design

- ✅ **Music bed with sidechain ducking**
- ⏳ **Sound FX library** — bundled or generated whoosh/pop/ding tones
- ⏳ **Beat-synced cuts** — see I
- ⏳ **Auto-music-selection** — match track BPM to clip pacing; `librosa` for tempo on candidate tracks
- ⏳ **Stinger drops** — short SFX at scene transitions
- 💤 **AI music generation** — `audiocraft / MusicGen` (Meta, MIT) for custom-fit beds
- 💤 **AI voiceover** — `Piper TTS` (open, MIT, very high quality), `Bark`, `Coqui TTS` for AI narration

## K. Effects

- ⏳ **Speed ramps (slow-mo / time warp)** — ffmpeg `setpts=0.5*PTS` for 2× / `2*PTS` for 0.5×
- ⏳ **Reverse playback** — `reverse` filter
- ⏳ **Stabilization** — `vidstab` (open) — already in ffmpeg as `vidstabdetect` + `vidstabtransform`
- ⏳ **Auto-zoom on reaction peaks** — Ken Burns ramp during top loud_peaks
- ⏳ **Tilt-shift** — `gblur` masked to top/bottom thirds
- ⏳ **CRT / VHS look** — chroma + scanlines + chromatic aberration combo
- 💤 **Particle FX** — Lottie

## L. Smart workflow

- ✅ **Modal SKILL.md** — highlights / shorts / quotecards / thumbnails / chapters / summary
- ⏳ **Auto-EDL generator** — *shipping this turn*
- ⏳ **Silence trimming** — `silenceremove` ffmpeg filter or `silero-vad` (open)
- ⏳ **Filler-word removal** — *shipping this turn*
- ⏳ **Watch-folder mode** — drop a video, get all outputs auto-generated
- ⏳ **Batch processing** — process N URLs in one command
- 💤 **Project save/load** — workdir state IS the project (already kinda there)

## M. Distribution

- 💤 **Auto-upload to YouTube** — official YouTube Data API (OAuth)
- 💤 **Auto-upload to TikTok** — TikTok Open API (limited; many use third-party tools)
- 💤 **Auto-upload to Instagram Reels** — Instagram Graph API (Business accounts)
- 💤 **Auto-upload to X** — `tweepy` (free)
- 💤 **Schedule posts** — `apscheduler` + cron, or `schedule.skill` from this CLI
- 💤 **Auto-tag / hashtag generator** — local LLM analyzing transcript
- 💤 **Description / SEO templates**

## N. Output formats

- ✅ **MP4 H.264 + AAC**
- ⏳ **Multi-format export** — square, vertical, landscape from one EDL
- ⏳ **Sidecar SRT / VTT subtitles**
- ⏳ **Codec presets** — H.265 (HEVC) for size, ProRes for editing handoff, AV1 for archival
- ⏳ **Audio-only export** (podcast `.m4a`)
- ⏳ **Animated thumbnails** (3s loop GIF or MP4)
- 💤 **HDR pipeline**

## Open-source dependency cheat sheet

| Job | Tool | Licence | Cost |
|---|---|---|---|
| Download | yt-dlp | Unlicense | 0 |
| Transcription | whisper-cpp | MIT | 0 |
| Word-level + diarization | WhisperX | BSD-2 | 0 |
| Speaker diarization | pyannote-audio | MIT | 0 |
| Voice activity | silero-vad | MIT | 0 |
| Tempo / beats | librosa, aubio, madmom | ISC / GPL / BSD | 0 |
| Noise reduction | RNNoise (via ffmpeg `arnndn`) | BSD | 0 |
| Stem separation | Demucs / Spleeter | MIT | 0 |
| Stabilization | vidstab (in ffmpeg) | LGPL | 0 |
| Smart cropping | mediapipe | Apache-2 | 0 |
| Frame search by text | open_clip_torch | MIT | 0 |
| TTS (voiceover) | Piper TTS | MIT | 0 |
| Music gen | MusicGen (audiocraft) | MIT | 0 |
| Translation | Argos Translate / LibreTranslate | MIT | 0 |
| Subtitle rendering | libass (via ffmpeg) | ISC | 0 |
| Local LLM picker | ollama + llama3.1 | MIT + custom | 0 |
| Lottie rendering | rlottie / lottie-renderer | LGPL / MIT | 0 |
| Subtitle editing | Aegisub | BSD-3 | 0 |

Heaviest leverage for "professional" results, in priority order:

1. **WhisperX** for word-level captions + speaker labels
2. **mediapipe smart crop** for vertical that tracks the speaker
3. **`silenceremove` + filler-word stripping** for tight cuts
4. **3D LUTs + film grain** for cinematic looks
5. **`librosa` beat sync** for music-driven edits
6. **`Piper` TTS** for AI voiceover sections
7. **`ollama` local LLM** as a second opinion on highlight picking
