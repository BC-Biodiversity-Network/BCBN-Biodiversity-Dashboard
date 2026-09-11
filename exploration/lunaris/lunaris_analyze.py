"""
lunaris_analyze.py

Read the full local Lunaris harvest and run two analyses to tune the
biodiversity keyword filter, on the FULL dataset:

  (A) Abstract word frequency (stopwords removed).
  (B) Test-filter: apply the draft keyword list to subject + title +
      abstract, report kept vs dropped, print KEPT and DROPPED samples.

Reads the local file, so re-run freely after editing the keyword list.

Run:
    python lunaris_analyze.py --harvest harvest/lunaris_full_harvest.parquet
"""

import argparse
import re
from collections import Counter

import pandas as pd

import sys
from pathlib import Path

# The filter itself is finalized and lives in pipeline/lunaris_keywords.py.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pipeline"))

from lunaris_keywords import as_text, build_haystack, is_biodiversity


STOPWORDS = set("""
the of and to in a for is are on with by this that as from at be or an
data dataset study used using we our results was were which it its
been has have had can may also more not their they these such between
de la le les des et en un une pour dans sur par au aux du ce cette est
sont ont été plus ou qui se ne pas avec comme leur
""".split())


def main():
    """Report the commonest abstract words and the filter's kept/dropped split.

    The word counts are how a keyword worth adding gets spotted; the split is
    the headline number, printed with ten sample records from each side so the
    decisions can be eyeballed.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--harvest", default="harvest/lunaris_full_harvest.parquet")
    parser.add_argument("--topwords", type=int, default=80)
    args = parser.parse_args()

    df = pd.read_parquet(args.harvest)
    n = len(df)
    if n == 0:
        raise SystemExit(f"{args.harvest} holds no records.")
    print(f"Loaded {n:,} records from {args.harvest}\n")

    word_counter = Counter()
    kept_mask = []
    for _, row in df.iterrows():
        title = as_text(row["title"])
        subjects = as_text(row["subjects"])
        abstract = as_text(row["abstract"])

        # (A) Count the words in this abstract, ignoring the common ones.
        for w in re.findall(r"[a-zàâçéèêëîïôûùüÿ]+", abstract.lower()):
            if len(w) >= 4 and w not in STOPWORDS:
                word_counter[w] += 1

        # (B) Ask the filter whether this record counts as biodiversity.
        haystack = build_haystack(title, subjects, abstract)
        kept_mask.append(is_biodiversity(haystack))

    # One result per record, in order. If the loop above ever skips a row or
    # appends twice, the results would attach to the wrong records, so stop
    # here rather than report a quietly wrong split.
    assert len(kept_mask) == len(df), (
        f"got {len(kept_mask)} filter results for {len(df)} records -- the loop "
        "above must append exactly one result per row"
    )

    df["_kept"] = kept_mask
    kept = df[df["_kept"]]
    dropped = df[~df["_kept"]]

    print(f"=== (A) Top {args.topwords} abstract words (stopwords removed) ===")
    for w, c in word_counter.most_common(args.topwords):
        print(f"{c:>7}  {w}")

    print(f"\n=== (B) Test filter on full dataset ===")
    print(f"Total {n:,}, kept {len(kept):,} ({100*len(kept)/n:.1f}%), "
          f"dropped {len(dropped):,} ({100*len(dropped)/n:.1f}%)")

    print("\n--- 10 KEPT samples ---")
    for _, r in kept.head(10).iterrows():
        subj = as_text(r["subjects"])[:120]
        ab = as_text(r["abstract"])[:200]
        print(f"\nTITLE: {as_text(r['title'])}")
        print(f"SUBJ:  {subj}")
        print(f"ABS:   {ab}")

    print("\n\n--- 10 DROPPED samples ---")
    for _, r in dropped.head(10).iterrows():
        subj = as_text(r["subjects"])[:120]
        ab = as_text(r["abstract"])[:200]
        print(f"\nTITLE: {as_text(r['title'])}")
        print(f"SUBJ:  {subj}")
        print(f"ABS:   {ab}")


if __name__ == "__main__":
    main()
