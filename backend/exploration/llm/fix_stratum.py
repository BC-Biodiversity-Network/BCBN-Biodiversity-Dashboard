"""
Fix the stratum column in the labelled sheet, and bring back the filter 2
information that was lost when the sheet was built.

Why this is needed: the sampling script read the filter 2 tier columns with a
check that only recognised Python lists. Those columns actually arrive as numpy
arrays, so every kept record looked empty and they all got labelled
"kept_nothing_found". The sample itself is fine, it is a random draw from all
the kept records, but the label on it is wrong.

This script does two things:

  1. Renames that stratum to plain "kept", which is what it really is.
  2. Joins the tier columns back on by record id, so each row carries whether
     filter 2 actually found an organism in it.

The labels you wrote are not touched. The result is written to a new file, so
the original is left alone.

Usage:

    python exploration/llm/fix_stratum.py \
        --labelled exploration/llm/test_set_labelled.csv \
        --candidates data/lunaris_taxa.parquet \
        --out exploration/llm/test_set_labelled_fixed.csv
"""

import argparse
import sys

import pandas as pd


TIER_COLUMNS = [
    "species_found",
    "species_off_list",
    "genus_qualified",
    "family_found",
    "higher_taxa_found",
    "genus_bare",
    "common_name_found",
]

# genus_bare is deliberately left out of the "found something" test. A lone
# genus name is recorded but was never counted as a find, because too many
# genus names are ordinary English words.
COUNTS_AS_FOUND = [c for c in TIER_COLUMNS if c != "genus_bare"]


def is_filled(value):
    """
    Say whether one tier cell holds at least one name.

    These cells hold a list of names, which can arrive as a Python list or as a
    numpy array depending on how the file was written. A numpy array is not a
    list, so the test is "does it have a length" rather than "is it a list".
    """
    if value is None:
        return False
    if hasattr(value, "__len__") and not isinstance(value, str):
        return len(value) > 0
    if isinstance(value, str):
        return bool(value.strip())
    return False


def first_name(row, columns):
    """Return the first organism name found across the given tier columns."""
    for col in columns:
        value = row.get(col)
        if is_filled(value):
            if hasattr(value, "__len__") and not isinstance(value, str):
                return str(list(value)[0])
            return str(value)
    return ""


def main():
    """Read the labelled sheet, repair the stratum, add the filter 2 columns."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labelled", required=True)
    parser.add_argument("--candidates", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    # utf-8-sig, because the sheet was written with a marker at the start so
    # that Excel would read the accents correctly.
    sheet = pd.read_csv(args.labelled, encoding="utf-8-sig")
    candidates = pd.read_parquet(args.candidates)

    print(f"labelled sheet: {len(sheet):,} rows")
    print(f"candidates:     {len(candidates):,} rows")

    tier_cols = [c for c in TIER_COLUMNS if c in candidates.columns]
    if not tier_cols:
        print("ERROR: no tier columns in the candidates file")
        sys.exit(1)

    # The id column has been called different things at different points in the
    # pipeline, so find it rather than assume a name.
    id_col = None
    for name in ["id", "record_id", "identifier", "oai_id"]:
        if name in candidates.columns:
            id_col = name
            break
    if id_col is None:
        print("ERROR: no id column in the candidates file. Columns are:")
        print(list(candidates.columns))
        sys.exit(1)
    print(f"joining on candidates column: {id_col}")

    # Work out, for each candidate, whether filter 2 found anything and what.
    slim = candidates[[id_col] + tier_cols].drop_duplicates(subset=[id_col]).copy()
    slim["filter2_found"] = slim.apply(
        lambda row: "yes" if any(is_filled(row[c]) for c in COUNTS_AS_FOUND) else "no",
        axis=1,
    )
    slim["filter2_organism"] = slim.apply(lambda row: first_name(row, COUNTS_AS_FOUND), axis=1)
    slim = slim[[id_col, "filter2_found", "filter2_organism"]]

    merged = sheet.merge(slim, left_on="record_id", right_on=id_col, how="left")
    if id_col != "record_id":
        merged = merged.drop(columns=[id_col])

    merged["stratum"] = merged["stratum"].replace({"kept_nothing_found": "kept"})

    # Check for kept rows that found no partner in the candidates file BEFORE
    # filling anything in. A kept row that did not match is a real problem: it
    # would quietly become "no" and make the blind spot look bigger than it is.
    unmatched = ((merged["stratum"] == "kept") & (merged["filter2_found"].isna())).sum()
    if unmatched:
        print(f"WARNING: {unmatched} kept rows did not match a candidate by id.")
        print("They are marked 'unmatched' and left out of the table below.")
    merged.loc[
        (merged["stratum"] == "kept") & (merged["filter2_found"].isna()), "filter2_found"
    ] = "unmatched"

    # The dropped records never went through filter 2 at all, which is not the
    # same as filter 2 finding nothing in them.
    merged.loc[merged["stratum"] == "dropped", "filter2_found"] = "not run"
    merged.loc[merged["stratum"] == "dropped", "filter2_organism"] = ""
    merged["filter2_organism"] = merged["filter2_organism"].fillna("")

    merged.to_csv(args.out, index=False, encoding="utf-8-sig")

    print()
    print("=" * 62)
    print(f"wrote {args.out}")
    print("=" * 62)
    print()
    print("stratum counts")
    print(merged["stratum"].value_counts().to_string())
    print()
    print("kept records, did filter 2 find an organism, against your label")
    kept = merged[merged["stratum"] == "kept"]
    table = pd.crosstab(kept["filter2_found"], kept["is_biodiversity"])
    print(table.to_string())
    print()

    # The interesting cell: filter 2 found nothing, but the record was judged
    # to be biodiversity data anyway. That is what a string match cannot reach.
    try:
        blind_spot = table.loc["no", "yes"]
        total_no = table.loc["no"].sum()
        print(f"Filter 2 found no organism in {total_no:,} of the sampled kept records.")
        print(f"Of those, {blind_spot:,} were still judged to be biodiversity data,")
        print(f"which is {blind_spot / total_no * 100:.0f}% of them.")
        print()
        print("That share is the part a string match cannot reach, whatever")
        print("keywords you add, because the record never writes an organism down.")
    except Exception:
        pass


if __name__ == "__main__":
    sys.exit(main())
