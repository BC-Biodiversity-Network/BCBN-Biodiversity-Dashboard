"""
lunaris_check_missed.py

Check whether the keyword filter is MISSING biodiversity datasets, by
analysing the records it DROPPED. Reads the full local harvest, applies
the current keyword list, then looks only at the dropped set:

  (1) Top subject terms among dropped records. If a clearly biodiversity
      subject term shows up here that's not in our keywords, we're missing
      it.

  (2) A RANDOM sample of dropped records (not the first N), so we can
      eyeball whether obvious biodiversity datasets are being dropped.

  (3) A reverse check: count dropped records whose text contains some
      biology-ish probe words that are NOT currently in the keyword list
      (genome, dna, microb, phenolog, pollinat, nesting, breeding, etc).
      High counts flag categories we might be missing.

Run:
    python lunaris_check_missed.py --harvest harvest/lunaris_full_harvest.parquet
"""

import argparse
import re
from collections import Counter

import pandas as pd

import sys
from pathlib import Path

# The filter itself is finalized and lives in pipeline/lunaris_keywords.py.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pipeline"))

from lunaris_keywords import (MATCH_KEYWORDS, as_text, build_haystack,
                              is_biodiversity)


# Biology words deliberately left out of the keyword list. If one of them
# turns up often among the dropped records, the keywords are probably missing
# that whole category.
#
# These are matched as whole words, so every entry must be a word someone would
# actually write -- a stem like "phylogen" matches nothing at all. And a word
# that is already a keyword always scores 0, because a record containing it is
# never dropped. The check below stops either mistake going unnoticed.
PROBE_WORDS = [
    "genome", "genomic", "dna", "rna", "microbe", "microbial", "microbiome",
    "bacteria", "virus", "viral", "pathogen", "phenology", "pollination",
    "nesting", "breeding", "spawning", "migration", "foraging", "predation",
    "genetic", "genetics", "phylogenetic", "phylogeny", "phylogenomic",
    "sequencing",
    "biological", "wild", "forest", "soil", "pollen", "larvae", "larval",
    "nectar", "marine", "aquatic", "terrestrial", "diversity",
    "population dynamics", "evolution", "reproduction",
]

_already_keywords = sorted(set(PROBE_WORDS) & set(MATCH_KEYWORDS))
assert not _already_keywords, (
    "these probe words are already keywords, so they can only ever score 0: "
    f"{_already_keywords}"
)


def main():
    """Report what the filter is throwing away, three ways.

    Prints the commonest subject terms among the dropped records, how often
    each probe word appears in them, and 25 dropped records picked at random
    to read. A high probe count is the signal worth chasing.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--harvest", default="harvest/lunaris_full_harvest.parquet")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    df = pd.read_parquet(args.harvest)
    n = len(df)
    if n == 0:
        raise SystemExit(f"{args.harvest} holds no records.")

    # Judge every record, then throw away the keepers and study the rest.
    titles = df["title"].apply(as_text)
    subjs = df["subjects"].apply(as_text)
    absts = df["abstract"].apply(as_text)
    hay = pd.Series(
        [build_haystack(t, s, a) for t, s, a in zip(titles, subjs, absts)],
        index=df.index)
    kept_mask = hay.apply(is_biodiversity)

    dropped = df[~kept_mask].copy()
    dropped_hay = hay[~kept_mask]
    print(f"Total {n:,}, dropped {len(dropped):,} ({100*len(dropped)/n:.1f}%)\n")

    # (1) Which subject terms are commonest among the dropped records?
    print("=== (1) Top 50 subject terms among DROPPED records ===")
    subj_counter = Counter()
    for subs in dropped["subjects"]:
        if subs is not None and not isinstance(subs, float):
            for s in subs:
                if s:
                    subj_counter[s.strip()] += 1
    for term, c in subj_counter.most_common(50):
        print(f"{c:>6}  {term}")

    # (2) How often does each probe word appear in what we dropped?
    print("\n=== (3) Probe words (NOT in keyword list) found in DROPPED text ===")
    print("High counts = a biodiversity category we might be missing.\n")
    probe_counts = []
    for pw in PROBE_WORDS:
        pat = re.compile(r"(?<![a-zA-Z])" + re.escape(pw) + r"(?![a-zA-Z])", re.IGNORECASE)
        cnt = dropped_hay.apply(lambda h: bool(pat.search(h))).sum()
        probe_counts.append((pw, int(cnt)))
    for pw, cnt in sorted(probe_counts, key=lambda x: -x[1]):
        print(f"{cnt:>6}  {pw}")

    # (3) A random handful to read -- random, not the first 25, so it is not
    # just whatever happens to sort first.
    print("\n=== (2) 25 RANDOM dropped records (any obvious biodiversity here?) ===")
    sample = dropped.sample(min(25, len(dropped)), random_state=args.seed)
    for _, r in sample.iterrows():
        print(f"\nTITLE: {as_text(r['title'])}")
        print(f"SUBJ:  {as_text(r['subjects'])[:120]}")
        print(f"ABS:   {as_text(r['abstract'])[:180]}")


if __name__ == "__main__":
    main()
