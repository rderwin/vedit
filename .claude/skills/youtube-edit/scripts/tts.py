#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.9"
# dependencies = ["piper-tts>=1.2; sys_platform != 'darwin'"]
# ///
"""Text-to-speech — synthesize a wav from text.

Backend selection (first that works):
  1. piper-tts  (offline, neural; best quality; pip install piper-tts)
  2. macOS `say`  (built-in on macOS; ok quality)
  3. Linux `espeak`  (built-in on most distros; functional)

Usage:
  uv run --quiet tts.py "your narration text" output.wav
  uv run --quiet tts.py --voice en_US-amy-medium "..." out.wav
  uv run --quiet tts.py --voice Samantha "..." out.wav   # macOS say
  uv run --quiet tts.py --rate 175 "..." out.wav          # words/min for `say`

Generated audio fits cleanly into the assembler — drop the wav into
the workdir and reference as a music bed, or as a custom intro track,
or splice into the main compilation manually with ffmpeg concat.

Piper voices: list available with `piper --download-list`. Common
free voices:
  en_US-amy-medium       neutral US English, 22050 Hz
  en_US-ryan-medium      neutral male
  en_GB-alan-medium      RP British male
  en_US-libritts-high    high-quality 22050 Hz US English
"""
import argparse
import os
import shutil
import subprocess
import sys


def piper_synth(text, out_path, voice):
    """Use the piper-tts Python package — neural TTS, offline."""
    try:
        from piper.voice import PiperVoice
    except ImportError:
        return False
    try:
        # piper expects a .onnx model file. The Python lib downloads on demand
        # if you pass `download_dir`. Default cache: ~/.local/share/piper.
        cache_dir = os.path.expanduser(
            "~/.local/share/piper/voices"
        )
        os.makedirs(cache_dir, exist_ok=True)
        model_path = os.path.join(cache_dir, voice + ".onnx")
        if not os.path.exists(model_path):
            print("[tts] downloading piper voice {}".format(voice))
            from piper.download import ensure_voice_exists, get_voices
            voices_info = get_voices(cache_dir, update_voices=True)
            ensure_voice_exists(voice, [cache_dir], cache_dir, voices_info)
        v = PiperVoice.load(model_path)
        with open(out_path, "wb") as f:
            v.synthesize(text, f)
        print("[tts] piper → {} ({})".format(out_path, voice))
        return True
    except Exception as e:
        print("[tts] piper failed: {}".format(e), file=sys.stderr)
        return False


def say_synth(text, out_path, voice, rate):
    """macOS `say` — built-in, no install."""
    if not shutil.which("say"):
        return False
    # `say -o file.wav` writes WAVE (LEI16). The voice is the macOS voice
    # name, e.g. Samantha, Alex, Daniel. List with `say -v ?`.
    cmd = ["say", "--data-format=LEI16@22050"]
    if voice:
        cmd += ["-v", voice]
    if rate:
        cmd += ["-r", str(int(rate))]
    cmd += ["-o", out_path, "--", text]
    try:
        subprocess.run(cmd, check=True)
        print("[tts] say → {} ({})".format(out_path, voice or "default"))
        return True
    except subprocess.CalledProcessError as e:
        print("[tts] say failed: {}".format(e), file=sys.stderr)
        return False


def espeak_synth(text, out_path, voice):
    """Linux `espeak` — built-in on most distros."""
    bin_name = "espeak-ng" if shutil.which("espeak-ng") else "espeak"
    if not shutil.which(bin_name):
        return False
    cmd = [bin_name, "-w", out_path]
    if voice:
        cmd += ["-v", voice]
    cmd += [text]
    try:
        subprocess.run(cmd, check=True)
        print("[tts] {} → {} ({})".format(bin_name, out_path, voice or "default"))
        return True
    except subprocess.CalledProcessError as e:
        print("[tts] {} failed: {}".format(bin_name, e), file=sys.stderr)
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("text", help="text to synthesize (use quotes)")
    ap.add_argument("out", help="output wav path")
    ap.add_argument("--voice", default=None,
                    help="voice name (piper: en_US-amy-medium / "
                         "say: Samantha / espeak: en+m1)")
    ap.add_argument("--rate", type=int, default=175,
                    help="speaking rate (words/min — used by `say`)")
    ap.add_argument("--backend", choices=["auto", "piper", "say", "espeak"],
                    default="auto",
                    help="force a specific backend")
    args = ap.parse_args()

    # Pick a sensible default voice per backend.
    backends = {
        "piper": (piper_synth, args.voice or "en_US-amy-medium"),
        "say":   (say_synth,   args.voice or "Samantha"),
        "espeak": (espeak_synth, args.voice or "en"),
    }
    if args.backend != "auto":
        order = [args.backend]
    else:
        order = []
        # Prefer piper if available; otherwise OS-native.
        if sys.platform == "darwin":
            order = ["piper", "say"]
        else:
            order = ["piper", "espeak"]

    for name in order:
        fn, voice = backends[name]
        if name == "say":
            ok = fn(args.text, args.out, voice, args.rate)
        else:
            ok = fn(args.text, args.out, voice)
        if ok:
            sys.exit(0)

    sys.exit(
        "[tts] no working backend. Tried: {}\n"
        "Install one:\n"
        "    pip install piper-tts             # cross-platform, neural\n"
        "    brew install espeak               # macOS via Homebrew\n"
        "    apt install espeak-ng              # Linux".format(", ".join(order))
    )


if __name__ == "__main__":
    main()
