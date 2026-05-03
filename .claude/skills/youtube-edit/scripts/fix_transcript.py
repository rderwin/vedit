#!/usr/bin/env python3
"""Fix common whisper mishears in <workdir>/transcript.json using context.

Two layers:
  1. Auto-derived domain dictionary from source.info.json (title, tags,
     uploader). If the title says "chess" anywhere, treat "chest"
     occurrences as candidates for "chess".
  2. Manual overrides from <workdir>/transcript_fixes.json:
       {
         "replace": {"chest": "chess", "Bily": "Billy"},
         "case_insensitive": true,
         "whole_word": true
       }
     Anything in `replace` is a direct word-by-word substitution.

The script is conservative: it only swaps EXACT word matches (so "chest"
in "chess pieces" never breaks). It also writes a `transcript.raw.json`
backup before editing so re-running with different fixes is safe.

Usage:
  python3 fix_transcript.py <workdir>
  python3 fix_transcript.py <workdir> --dry-run  (prints what would change)
"""
import argparse
import json
import pathlib
import re
import sys


# Generic confusables that often need disambiguation by context. These
# pairs only fire when the OTHER word appears prominently in the video
# title / tags / nearby transcript.
CONFUSABLE_PAIRS = [
    ("chest",       "chess"),
    ("chests",      "chess"),
    ("Bily",        "Billy"),
    ("Markus",      "Markus"),  # placeholder — most LLMs will get this right
    ("read it",     "Reddit"),
    ("git hub",     "GitHub"),
    ("you tube",    "YouTube"),
    ("tick tock",   "TikTok"),
    ("dog coin",    "dogecoin"),
    ("doge coin",   "dogecoin"),
]


def derive_auto_replacements(workdir, transcript=None):
    """Build a {wrong: right} dict by scanning info.json AND the transcript
    for which spelling of a confusable pair dominates.

    Strategy: for each (wrong, right) pair, count occurrences of each in
    the metadata + transcript. If `right` appears clearly more often than
    `wrong` (≥3× and at least 5 occurrences total), replace `wrong` with
    `right` everywhere — those are likely mishears of the dominant spelling.

    If the metadata says one and the transcript says the other, trust
    the metadata (it's human-written).
    """
    metadata_text = ""
    info_path = workdir / "source.info.json"
    if info_path.exists():
        try:
            info = json.loads(info_path.read_text())
            metadata_text = " ".join([
                info.get("title", "") or "",
                info.get("description", "") or "",
                info.get("uploader", "") or info.get("channel", "") or "",
                " ".join(info.get("tags") or []),
                " ".join(info.get("categories") or []),
            ])
        except Exception:
            pass

    transcript_text = " ".join(e["text"] for e in (transcript or []))
    full = (metadata_text + " " + transcript_text).lower()

    def count(word):
        # whole-word count, case-insensitive
        return len(re.findall(r"\b" + re.escape(word.lower()) + r"\b", full))

    fixes = {}
    for wrong, right in CONFUSABLE_PAIRS:
        if wrong == right:
            continue
        # If metadata mentions `right` directly, that's strong evidence.
        in_metadata_right = right.lower() in metadata_text.lower()
        in_metadata_wrong = wrong.lower() in metadata_text.lower()
        if in_metadata_right and not in_metadata_wrong:
            fixes[wrong] = right
            continue
        if in_metadata_wrong and not in_metadata_right:
            continue  # metadata says the "wrong" is actually right here

        # Otherwise compare transcript counts.
        c_wrong = count(wrong)
        c_right = count(right)
        total = c_wrong + c_right
        if total >= 5 and c_right >= 3 * max(c_wrong, 1):
            fixes[wrong] = right
    return fixes


def apply_replacements(transcript, replacements, *, case_insensitive=True,
                       whole_word=True):
    """Run word-level replacements over the transcript in-place. Returns
    a list of (entry_idx, before, after) for logging."""
    flags = re.IGNORECASE if case_insensitive else 0
    # Compile one regex per replacement key. Use \b for whole-word.
    compiled = []
    for wrong, right in replacements.items():
        if whole_word:
            pattern = r"\b" + re.escape(wrong) + r"\b"
        else:
            pattern = re.escape(wrong)
        compiled.append((re.compile(pattern, flags), right))

    changes = []
    for i, e in enumerate(transcript):
        original = e["text"]
        new = original
        for pat, right in compiled:
            new = pat.sub(right, new)
        if new != original:
            changes.append((i, original, new))
            e["text"] = new
    return changes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workdir")
    ap.add_argument("--dry-run", action="store_true",
                    help="Show changes but don't write the file.")
    ap.add_argument("--no-auto", action="store_true",
                    help="Skip the auto-derived confusable list.")
    args = ap.parse_args()

    workdir = pathlib.Path(args.workdir)
    tpath = workdir / "transcript.json"
    if not tpath.exists():
        sys.exit("missing " + str(tpath))

    transcript = json.loads(tpath.read_text())

    # Build the merged replacement dictionary.
    replacements = {}
    if not args.no_auto:
        replacements.update(derive_auto_replacements(workdir, transcript))

    fpath = workdir / "transcript_fixes.json"
    user_opts = {"case_insensitive": True, "whole_word": True}
    if fpath.exists():
        spec = json.loads(fpath.read_text())
        replacements.update(spec.get("replace") or {})
        user_opts["case_insensitive"] = bool(spec.get("case_insensitive", True))
        user_opts["whole_word"] = bool(spec.get("whole_word", True))

    if not replacements:
        print("[fix] no replacements (no auto matches; no transcript_fixes.json).")
        return

    print("[fix] applying {} replacement(s):".format(len(replacements)))
    for k, v in sorted(replacements.items()):
        print("  {!r} → {!r}".format(k, v))

    changes = apply_replacements(transcript, replacements, **user_opts)
    print("[fix] {} entries changed".format(len(changes)))
    for i, before, after in changes[:10]:
        print("  [{:>4}] {} → {}".format(i, before, after))
    if len(changes) > 10:
        print("  ... and {} more".format(len(changes) - 10))

    if args.dry_run:
        print("[fix] --dry-run: no file written")
        return

    # Backup original on first run only (so re-runs don't lose the backup).
    backup = workdir / "transcript.raw.json"
    if not backup.exists():
        backup.write_text(tpath.read_text())
        print("[fix] backup → {}".format(backup))

    tpath.write_text(json.dumps(transcript, indent=2))
    print("[fix] wrote {}".format(tpath))


if __name__ == "__main__":
    main()
