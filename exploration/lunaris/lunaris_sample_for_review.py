"""
lunaris_sample_for_review.py

Pull a small, REPRESENTATIVE sample of Lunaris records to test semantic
judgment (e.g. hand it to Claude Code and ask it to classify each as
biodiversity or not). We want a mix, not just random, so we can see how a
semantic judge handles the tricky cases:

  - clearly biodiversity (should be YES)
  - keyword-kept but questionable (meat plant, E. coli, etc.)
  - keyword-dropped (should mostly be NO, but check for missed ones)
  - French-language records (does the semantic judge handle French?)

Writes a CSV with title, subjects, abstract (and a "sampled_because" tag),
which you can open, or hand to Claude Code for classification.

Run:
    python lunaris_sample_for_review.py --harvest harvest/lunaris_full_harvest.parquet --out sample_for_review.csv
"""

import argparse
import re

import pandas as pd

import sys
from pathlib import Path

# The filter itself is finalized and lives in pipeline/lunaris_keywords.py.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pipeline"))

from lunaris_keywords import as_text, build_haystack, is_biodiversity


# Everyday French words. Two or more of them means the record is in French.
FRENCH_HINTS = ["données", "selon", "les ", "des ", "une ", "aux ", "être",
                "notre", "cette", "dans le", "pour la"]


# Phrases that make a kept record worth a second look, because the keyword
# that kept it may not have been biological.
QUESTIONABLE = ["meat plant", "power plant", "e. coli", "escherichia",
                "acoustic doppler", "seismic"]


def looks_french(text):
    """True if the text holds at least two everyday French words."""
    t = text.lower()
    return sum(1 for h in FRENCH_HINTS if h in t) >= 2


def main():
    """Write a mixed sample of records for someone to judge by hand.

    Takes --per_group records from each of four groups, so the sample is made
    of the awkward cases rather than 48 easy ones, and writes them to --out
    tagged with the group each came from.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--harvest", default="harvest/lunaris_full_harvest.parquet")
    parser.add_argument("--out", default="sample_for_review.csv")
    parser.add_argument("--per_group", type=int, default=12,
                        help="How many records per category.")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    df = pd.read_parquet(args.harvest)
    df["_title"] = df["title"].apply(as_text)
    df["_subj"] = df["subjects"].apply(as_text)
    df["_abs"] = df["abstract"].apply(as_text)
    df["_hay"] = [build_haystack(t, s, a)
                  for t, s, a in zip(df["_title"], df["_subj"], df["_abs"])]
    df["_kept"] = df["_hay"].apply(is_biodiversity)
    df["_french"] = df["_abs"].apply(looks_french)
    df["_questionable"] = df["_hay"].apply(lambda h: any(q in h for q in QUESTIONABLE))

    n = args.per_group
    seed = args.seed
    picks = []

    # Group 1: kept, with nothing suspicious about the keyword that kept it.
    g = df[df["_kept"] & ~df["_questionable"]].sample(min(n, sum(df["_kept"] & ~df["_questionable"])), random_state=seed)
    for _, r in g.iterrows():
        picks.append((r, "kept_clear"))

    # Group 2: kept, but on a keyword that is often not biological.
    q = df[df["_kept"] & df["_questionable"]]
    if len(q):
        g = q.sample(min(n, len(q)), random_state=seed)
        for _, r in g.iterrows():
            picks.append((r, "kept_questionable"))

    # Group 3: dropped -- mostly correct, but this is where misses hide.
    g = df[~df["_kept"]].sample(min(n, sum(~df["_kept"])), random_state=seed)
    for _, r in g.iterrows():
        picks.append((r, "dropped"))

    # Group 4: French records, kept or not, to check the French keywords.
    fr = df[df["_french"]]
    if len(fr):
        g = fr.sample(min(n, len(fr)), random_state=seed)
        for _, r in g.iterrows():
            picks.append((r, "french"))

    rows = []
    for r, tag in picks:
        rows.append({
            "sampled_because": tag,
            "keyword_kept": r["_kept"],
            "title": r["_title"],
            "subjects": r["_subj"][:200],
            "abstract": r["_abs"][:600],
        })

    out = pd.DataFrame(rows)
    out.to_csv(args.out, index=False, encoding="utf-8-sig")
    print(f"Wrote {len(out)} records to {args.out}")
    print("\nBreakdown by category:")
    print(out["sampled_because"].value_counts().to_string())


if __name__ == "__main__":
    main()
