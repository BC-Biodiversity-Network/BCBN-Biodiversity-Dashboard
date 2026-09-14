"""
lunaris_check_false_positives.py

Evidence check for changes to the keyword filter, run against the full local
harvest. Nothing should be added to FALSE_POSITIVE_PATTERNS or removed from
NO_PLURAL in lunaris_keywords.py without running this first.

Two modes, named after what they measure:

  --add-mask PHRASE  Measures which currently KEPT records this phrase would
                     DROP. Those are the only records it can hurt, so read the
                     titles it prints: if any of them are real biodiversity
                     datasets, the phrase is too broad.

  --add-word WORD    Measures which currently DROPPED records this word would
                     newly KEEP. Read the titles to judge whether the word
                     brings in biodiversity or another sense of itself. Works
                     for any candidate word, not only plural forms.

The older names --mask and --plural still work, so notes and saved commands
written before the rename keep running.

Run:
    python lunaris_check_false_positives.py --add-mask "power\\s+plants?"
    python lunaris_check_false_positives.py --add-word trees
"""

import argparse
import re

import pandas as pd

import sys
from pathlib import Path

# The filter itself is finalized and lives in pipeline/lunaris_keywords.py.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pipeline"))

from lunaris_keywords import (PLURAL_KEYWORDS, as_text, compile_boundary,
                              is_biodiversity, mask_false_positives)


def load(harvest):
    """Read the harvest and return its titles plus the text to search.

    The search text is each record's title, subjects and abstract run
    together in lower case, with the phrases the filter already blanks out
    removed, so a candidate is judged on top of today's filter rather than
    against the raw text.
    """
    df = pd.read_parquet(harvest)
    titles = df["title"].apply(as_text)
    hay = (titles + " " + df["subjects"].apply(as_text) + " "
           + df["abstract"].apply(as_text)).str.lower()
    return titles, hay.apply(mask_false_positives)


def show(titles, mask, n, seed):
    """Print up to n titles, sampled from the records the mask picks out."""
    picked = titles[mask]
    for t in picked.sample(min(n, len(picked)), random_state=seed):
        print(f"   - {str(t)[:95]}")


def main():
    """Check one candidate phrase or plural against the whole harvest."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--harvest", default="harvest/lunaris_full_harvest.parquet")
    # The first name is the real one; the second is kept so older commands and
    # notes still run.
    ap.add_argument("--add-mask", "--mask", dest="add_mask",
                    help="phrase to blank out: measures which kept records it drops")
    ap.add_argument("--add-word", "--plural", dest="add_word",
                    help="word to add: measures which dropped records it newly keeps")
    ap.add_argument("--samples", type=int, default=20)
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()
    if not (args.add_mask or args.add_word):
        ap.error("give --add-mask or --add-word")

    titles, hay = load(args.harvest)
    kept = hay.apply(is_biodiversity)
    print(f"{len(hay):,} records, {int(kept.sum()):,} currently kept\n")

    if args.add_mask:
        rx = re.compile(args.add_mask, re.IGNORECASE)
        after = hay.apply(lambda h: rx.sub(lambda m: " " * len(m.group(0)), h))
        flip = kept & ~after.apply(is_biodiversity)
        print(f"--add-mask /{args.add_mask}/")
        print(f"  appears in            {int(hay.str.contains(rx).sum()):,}")
        print(f"  kept -> dropped       {int(flip.sum()):,}")
        # Safe only if every single one of these is non-biodiversity.
        print("  (every one of these must be non-biodiversity to be safe)")
        show(titles, flip, args.samples, args.seed)

    if args.add_word:
        p = args.add_word.lower()
        rx = compile_boundary([p])
        newly = ~kept & hay.str.contains(rx)
        # These records are dropped, which means no keyword matched them at all,
        # so none of them can contain another keyword either. This used to be
        # printed as a second figure that was always equal to the first.
        others = [q for q in PLURAL_KEYWORDS if q != p]
        if others:
            still_has_one = int((newly & hay.str.contains(compile_boundary(others))).sum())
            assert still_has_one == 0, (
                f"{still_has_one} dropped record(s) contain a keyword. A dropped "
                "record should contain none, so either the filter or this check "
                "is no longer doing what it says."
            )
        print(f"--add-word {p}")
        print(f"  newly kept            {int(newly.sum()):,}")
        print("  (read these: are they biodiversity, or another sense of the word?)")
        show(titles, newly, args.samples, args.seed)


if __name__ == "__main__":
    main()
