#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.9"
# dependencies = ["argostranslate>=1.9"]
# ///
"""Translate <workdir>/transcript.json into target languages and emit
<workdir>/transcript.<lang>.json files.

Uses argos-translate — an offline neural MT library based on OpenNMT
and Argos's own published language models. Models download once
(~50–250 MB per language pair) and run locally.

Usage:
    uv run --quiet translate.py <workdir> --to es,fr,pt-BR

Args:
    --to     comma-separated list of target language codes (ISO 639-1).
             Recognized examples: es, fr, de, pt, it, ja, zh, ko, ru,
             ar, hi, vi, id, tr, pl, nl, sv. Use 'pt-BR' for Brazilian
             Portuguese — the script normalizes to argos's underlying
             pair (still 'pt' in Argos as of writing).
    --from   source language code. Default: 'en'.
    --models-dir  optional cache directory for argos models (default:
                  argos's per-user default under ~/.local/share/argos-translate).

The output transcript.{lang}.json files have the same shape as
transcript.json — same `start` and `speaker`, with `text` translated.

assemble.py reads `style.translate_to` (a single lang code) to render
extra captioned vertical clips with translated captions, in addition to
the English ones. See SKILL.md for the EDL keys.
"""
import argparse
import json
import pathlib
import sys


def fail(msg, code=1):
    print(msg, file=sys.stderr)
    sys.exit(code)


def normalize_lang(code):
    """argos uses bare ISO 639-1; map common variants."""
    code = code.lower().strip()
    return {
        "pt-br": "pt",
        "pt-pt": "pt",
        "zh-cn": "zh",
        "zh-tw": "zh",
        "en-us": "en",
        "en-gb": "en",
    }.get(code, code)


def ensure_pair(argos, src, tgt):
    """Make sure argos has a model for src→tgt. Download if missing."""
    installed = {(p.from_code, p.to_code) for p in argos.translate.get_installed_languages()
                 for p in p.translations_from}
    # The above is fiddly — use a simpler check via get_installed_languages.
    langs = argos.translate.get_installed_languages()
    have = False
    for L in langs:
        if L.code == src:
            for trans in L.translations_from:
                if trans.to_lang.code == tgt:
                    have = True
                    break
        if have:
            break
    if have:
        return
    # Need to download.
    print("[translate] downloading model {} → {}".format(src, tgt))
    argos.package.update_package_index()
    pkgs = argos.package.get_available_packages()
    cand = [p for p in pkgs if p.from_code == src and p.to_code == tgt]
    if not cand:
        fail(
            "argos has no published model for {} → {}.\n"
            "Available targets from {}: {}".format(
                src, tgt, src,
                ", ".join(sorted(set(
                    p.to_code for p in pkgs if p.from_code == src
                ))) or "(none)",
            )
        )
    download_path = cand[0].download()
    argos.package.install_from_path(download_path)


def translate_entries(argos, src, tgt, transcript):
    """Translate the `text` of each entry; preserve other fields."""
    langs = argos.translate.get_installed_languages()
    src_lang = next((L for L in langs if L.code == src), None)
    tgt_lang = next((L for L in langs if L.code == tgt), None)
    if not src_lang or not tgt_lang:
        fail("argos missing language: src={} tgt={}".format(src, tgt))
    translation = src_lang.get_translation(tgt_lang)

    out = []
    n = len(transcript)
    for i, e in enumerate(transcript):
        # argos translate is one-shot per call; for speed we batch by paragraph
        # but keep the line-by-line output to preserve timing fidelity.
        try:
            text = translation.translate(e["text"])
        except Exception as ex:
            print("[translate]   line {} failed ({}); keeping original".format(
                i, ex
            ))
            text = e["text"]
        copy = dict(e)
        copy["text"] = text
        out.append(copy)
        # Coarse progress every 10%.
        if n >= 50 and i % max(1, n // 10) == 0:
            print("[translate]   {}/{} ({}%)".format(i + 1, n, int(100 * (i + 1) / n)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workdir")
    ap.add_argument(
        "--to", required=True,
        help="comma-separated target language codes (e.g. 'es,fr,de')",
    )
    ap.add_argument("--from", dest="from_lang", default="en")
    args = ap.parse_args()

    workdir = pathlib.Path(args.workdir)
    tpath = workdir / "transcript.json"
    if not tpath.exists():
        fail("transcript.json not found in " + str(workdir))
    transcript = json.loads(tpath.read_text())
    if not transcript:
        fail("transcript.json is empty")

    src = normalize_lang(args.from_lang)
    targets = [normalize_lang(c) for c in args.to.split(",") if c.strip()]

    try:
        import argostranslate.translate as _t
        import argostranslate.package as _p
    except ImportError:
        fail(
            "argostranslate not installed. Install:\n"
            "    pip install argostranslate\n"
            "Or via uv:\n"
            "    uv tool install argostranslate"
        )

    class _Argos:
        translate = _t
        package = _p
    argos = _Argos()

    for tgt in targets:
        if tgt == src:
            print("[translate] skipping {} → {} (same)".format(src, tgt))
            continue
        ensure_pair(argos, src, tgt)
        print("[translate] {} → {} ({} lines)".format(
            src, tgt, len(transcript)
        ))
        translated = translate_entries(argos, src, tgt, transcript)
        out_path = workdir / "transcript.{}.json".format(tgt)
        out_path.write_text(json.dumps(translated, indent=2, ensure_ascii=False))
        print("[translate] wrote {}".format(out_path))


if __name__ == "__main__":
    main()
