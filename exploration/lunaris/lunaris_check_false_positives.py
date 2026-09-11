"""
lunaris_check_false_positives.py

Evidence check for changes to the keyword filter, run against the full local
harvest. Nothing should be added to FALSE_POSITIVE_PATTERNS or removed from
NO_PLURAL in lunaris_keywords.py without running this first.

Two modes:

  --mask PHRASE    Which records would blanking out this phrase turn from
                   kept into dropped? Those are the only records it can hurt,
                   so read the titles it prints: if any of them are real
                   biodiversity datasets, the phrase is too broad.

  --plural WORD    Which dropped records would this plural form start
                   keeping, and for how many is it the only reason? Read the
                   titles to judge whether the plural pulls in more
                   non-biodiversity than the singular does.

Run:
    python lunaris_check_false_positives.py --mask "power\\s+plants?"
    python lunaris_check_false_positives.py --plural trees
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
    ap.add_argument("--mask", help="phrase to test blanking out")
    ap.add_argument("--plural", help="candidate plural form to evaluate")
    ap.add_argument("--samples", type=int, default=20)
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()
    if not (args.mask or args.plural):
        ap.error("give --mask or --plural")

    titles, hay = load(args.harvest)
    kept = hay.apply(is_biodiversity)
    print(f"{len(hay):,} records, {int(kept.sum()):,} currently kept\n")

    if args.mask:
        rx = re.compile(args.mask, re.IGNORECASE)
        after = hay.apply(lambda h: rx.sub(lambda m: " " * len(m.group(0)), h))
        flip = kept & ~after.apply(is_biodiversity)
        print(f"--mask /{args.mask}/")
        print(f"  appears in            {int(hay.str.contains(rx).sum()):,}")
        print(f"  kept -> dropped       {int(flip.sum()):,}")
        # Safe only if every single one of these is non-biodiversity.
        print("  (every one of these must be non-biodiversity to be safe)")
        show(titles, flip, args.samples, args.seed)

    if args.plural:
        p = args.plural.lower()
        rx = compile_boundary([p])
        newly = ~kept & hay.str.contains(rx)
        others = [q for q in PLURAL_KEYWORDS if q != p]
        sole = newly & ~hay.str.contains(compile_boundary(others)) if others else newly
        print(f"--plural {p}")
        print(f"  newly kept            {int(newly.sum()):,}")
        print(f"  sole reason           {int(sole.sum()):,}")
        print("  (read these: are they biodiversity, or another sense of the word?)")
        show(titles, sole, args.samples, args.seed)


if __name__ == "__main__":
    main()
