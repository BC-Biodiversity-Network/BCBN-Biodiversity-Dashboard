"""
build_lunaris_candidates.py

Apply the biodiversity keyword filter to the Lunaris harvest and write the
records that survive.

This is the Lunaris counterpart of build_bc_clean.py: the harvest is the wide
unfiltered copy, and this step turns it into the product everything downstream
reads. Until this existed the surviving records were only ever a number printed
by a one-off script, never a file.

The rules live in lunaris_keywords.py and are imported, never copied. Each
script used to keep its own copy of the keyword list and they drifted apart,
which is the whole reason that module exists.

Input:  lunaris_full_harvest.parquet  (every harvested record)
Output: lunaris_biodiv_candidates.parquet
          every column of the input, plus:
          matched_keywords - the words that caused the record to be kept, so a
                             keep decision can always explain itself

The output keeps the harvest's row order, so the same input gives the same file
every time. Re-running simply overwrites: filtering is cheap and the rules change
often, which is the opposite of the harvest, where an accidental overwrite costs
hours.

Run:
    python build_lunaris_candidates.py \
        --harvest exploration/lunaris/harvest/lunaris_full_harvest.parquet \
        --out data/lunaris_biodiv_candidates.parquet
"""

import argparse
import collections
import os

import pandas as pd

from lunaris_keywords import build_haystack, is_biodiversity, keyword_hits


def judge(df):
    """Decide which records are biodiversity and which words decided it.

    Takes the harvested records and returns two lists, one entry per record in
    the order they arrived: whether to keep it, and the keywords that fired.
    The search text itself is thrown away - it can always be rebuilt from the
    record, and storing it would double the size of the file for nothing.
    """
    keep, matched = [], []
    for t, s, a in zip(df["title"], df["subjects"], df["abstract"]):
        haystack = build_haystack(t, s, a)
        keep.append(is_biodiversity(haystack))
        matched.append(keyword_hits(haystack))
    return keep, matched


def keyword_counts(matched):
    """Count how many kept records each keyword fired on, commonest first.

    Takes the per-record keyword lists and returns a list of (word, count)
    pairs. This is what shows which words are carrying the filter and which are
    close to dead weight.
    """
    counter = collections.Counter()
    for words in matched:
        counter.update(words)
    return counter.most_common()


def build_candidates(harvest_path, out_path):
    """Filter the harvest and write the candidates file.

    Returns the number of records kept and the per-keyword counts. Any existing
    output file is replaced.
    """
    df = pd.read_parquet(harvest_path)
    print(f"Read {len(df):,} harvested records.")

    keep, matched = judge(df)
    df["_keep"] = keep
    df["matched_keywords"] = matched

    kept = df[df["_keep"]].drop(columns=["_keep"]).reset_index(drop=True)
    print(f"Kept {len(kept):,} ({100 * len(kept) / len(df):.1f}%), "
          f"dropped {len(df) - len(kept):,}.")

    parent = os.path.dirname(out_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    kept.to_parquet(out_path, index=False)
    return len(kept), keyword_counts(kept["matched_keywords"])


def main():
    parser = argparse.ArgumentParser(
        description="Apply the biodiversity keyword filter to the Lunaris harvest."
    )
    parser.add_argument(
        "--harvest",
        default="exploration/lunaris/harvest/lunaris_full_harvest.parquet",
        help="Path to the full Lunaris harvest.",
    )
    parser.add_argument(
        "--out",
        default="data/lunaris_biodiv_candidates.parquet",
        help="Where to write the biodiversity candidates.",
    )
    args = parser.parse_args()

    print(f"Harvest: {args.harvest}")
    print(f"Output:  {args.out}")

    n, counts = build_candidates(args.harvest, args.out)

    print("\nKeywords, by how many kept records each one fired on:")
    for word, count in counts:
        print(f"{count:>7,}  {word}")

    print(f"\nDone. Candidate records written: {n:,}")
    print(f"File: {args.out}")


if __name__ == "__main__":
    main()
