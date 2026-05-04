#!/usr/bin/env python3
"""WhisperX-based transcription — word-level alignment + speaker diarization.

Opt-in upgrade over `transcribe.sh`:
  - `transcribe.sh`     uses whisper.cpp (cheap, fast, no PyTorch)
  - `transcribe_x.py`   uses WhisperX (heavy PyTorch dep, but better
                        alignment via wav2vec2 + speaker labels via
                        pyannote-audio)

When to use which:
  - Single speaker, casual content       → transcribe.sh (faster, cheaper)
  - Multi-speaker podcast / interview    → transcribe_x.py (gets you
                                            "Alice:" / "Bob:" labels)
  - You want millisecond-accurate caps   → either with --words/word_active

Install (heavy):
    pip install whisperx
    # OR
    uv tool install whisperx

Diarization needs a HuggingFace token AND you must accept the
pyannote terms-of-service on the model page:
    https://huggingface.co/pyannote/speaker-diarization-3.1

Then:
    export HF_TOKEN=hf_...
    python3 scripts/transcribe_x.py <workdir>

Outputs:
    <workdir>/source.en.vtt        — word-level VTT
    <workdir>/speaker_labels.json  — list of (start, end, speaker) ranges

If HF_TOKEN is unset, transcription still runs but speaker labels are
not produced.
"""
import json
import os
import pathlib
import subprocess
import sys


def fail(msg, code=1):
    print(msg, file=sys.stderr)
    sys.exit(code)


def main():
    if len(sys.argv) != 2:
        fail("usage: transcribe_x.py <workdir>")
    workdir = pathlib.Path(sys.argv[1])
    src = workdir / "source.mp4"
    if not src.exists():
        fail("source.mp4 not found in " + str(workdir))

    try:
        import whisperx
    except ImportError:
        fail(
            "whisperx not installed. Install with one of:\n"
            "    pip install whisperx\n"
            "    uv tool install whisperx\n"
            "(Pulls PyTorch + pyannote-audio — ~3GB on disk.)\n"
            "For lightweight word-level captions without diarization,\n"
            "use scripts/transcribe.sh --words instead.",
            127,
        )

    device = os.environ.get("WHISPERX_DEVICE", "cpu")
    model_name = os.environ.get("WHISPERX_MODEL", "base.en")
    compute_type = os.environ.get("WHISPERX_COMPUTE_TYPE", "int8")
    hf_token = os.environ.get("HF_TOKEN") or os.environ.get(
        "HUGGINGFACE_TOKEN"
    )

    # 1. Extract 16k mono wav (whisper expects this).
    wav = workdir / "source.16k.wav"
    if not wav.exists():
        print("[whisperx] extracting 16k mono wav from {}".format(src.name))
        subprocess.run(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(src), "-ac", "1", "-ar", "16000", "-vn", str(wav),
            ],
            check=True,
        )

    # 2. Transcribe.
    print("[whisperx] loading model {} on {} ({})".format(
        model_name, device, compute_type
    ))
    model = whisperx.load_model(
        model_name, device=device, compute_type=compute_type
    )
    audio = whisperx.load_audio(str(wav))
    result = model.transcribe(audio, batch_size=16)
    language = result.get("language", "en")
    print("[whisperx] {} segments, language={}".format(
        len(result["segments"]), language
    ))

    # 3. Word-level alignment.
    print("[whisperx] loading alignment model")
    align_model, metadata = whisperx.load_align_model(
        language_code=language, device=device
    )
    result = whisperx.align(
        result["segments"], align_model, metadata, audio, device,
        return_char_alignments=False,
    )

    # 4. Optional speaker diarization.
    speaker_labels = []
    if hf_token:
        try:
            print("[whisperx] running speaker diarization")
            diarize_model = whisperx.DiarizationPipeline(
                use_auth_token=hf_token, device=device,
            )
            diarize_segments = diarize_model(audio)
            result = whisperx.assign_word_speakers(diarize_segments, result)
            # Aggregate per-segment speaker.
            for seg in result["segments"]:
                speaker = seg.get("speaker", "")
                if speaker:
                    speaker_labels.append({
                        "start": round(float(seg["start"]), 2),
                        "end": round(float(seg["end"]), 2),
                        "speaker": speaker,
                    })
            n_speakers = len(set(s["speaker"] for s in speaker_labels))
            print("[whisperx]   {} speakers detected, {} labeled segments".format(
                n_speakers, len(speaker_labels)
            ))
        except Exception as e:
            print(
                "[whisperx] diarization failed: {}\n".format(e)
                + "    The script continues without speaker labels.\n"
                + "    Common causes:\n"
                + "      - HF_TOKEN invalid or unauthorized\n"
                + "      - You haven't accepted the pyannote ToS at\n"
                + "        https://huggingface.co/pyannote/speaker-diarization-3.1",
                file=sys.stderr,
            )
    else:
        print(
            "[whisperx] HF_TOKEN unset — skipping speaker diarization.\n"
            "  To enable: get a HuggingFace token, accept the pyannote\n"
            "  ToS at https://huggingface.co/pyannote/speaker-diarization-3.1,\n"
            "  then run with HF_TOKEN=hf_... in the env."
        )

    # 5. Write VTT (word-level).
    vtt_path = workdir / "source.en.vtt"
    with vtt_path.open("w") as f:
        f.write("WEBVTT\n\n")
        for seg in result["segments"]:
            words = seg.get("words") or []
            if words:
                # Word-level: emit one cue per word.
                for w in words:
                    if "start" not in w or "end" not in w:
                        continue
                    f.write("{} --> {}\n{}\n\n".format(
                        _ts(w["start"]), _ts(w["end"]), w["word"].strip(),
                    ))
            else:
                f.write("{} --> {}\n{}\n\n".format(
                    _ts(seg["start"]), _ts(seg["end"]), seg["text"].strip(),
                ))
    print("[whisperx] wrote {}".format(vtt_path))

    # 6. Write speaker labels JSON (empty if no diarization).
    sl_path = workdir / "speaker_labels.json"
    sl_path.write_text(json.dumps(speaker_labels, indent=2))
    if speaker_labels:
        print("[whisperx] wrote {}".format(sl_path))

    wav.unlink(missing_ok=True)


def _ts(seconds):
    """VTT timestamp HH:MM:SS.mmm."""
    s = float(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return "{:02d}:{:02d}:{:06.3f}".format(int(h), int(m), sec)


if __name__ == "__main__":
    main()
