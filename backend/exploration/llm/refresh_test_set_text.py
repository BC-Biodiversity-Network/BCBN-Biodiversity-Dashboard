"""
Refresh the title, subjects and abstract of the 900-record test set from a
newer Lunaris harvest, keeping every label exactly as it is.

The test set was drawn from the September 2026 harvest. When the harvest is
redone, some records' text changes (a corrected abstract, the English title
now preferred over the French one, new keywords). This writes a copy of the
test set with the new text, so the model can be tried on what the pipeline
will actually see, while the hand labels stay the same and the scores stay
comparable.

What it does:
  - Reads the test set (test_set_reviewed.csv by default) and keeps all its
    columns, in the same order, with the same rows in the same order.
  - Looks each record_id up in the new harvest. If an id appears more than
    once there (it happens, mostly on Zenodo), the row with the latest
    datestamp is used, and the first in the file if that is a tie.
  - Replaces title, subjects and abstract with the new harvest's, written the
    same way build_test_set.py wrote them (its as_text), so the subjects keep
    the same ['a' 'b'] look that run_trial.py knows how to tidy.
  - A record that is not in the new harvest keeps its old text, and is listed.
  - Prints how many records changed in each field, judged the way
    run_trial.py will read them (after a CSV round trip, where for example an
    abstract that says "N/A" reads back as empty), and a few examples.

It never overwrites the input.

Usage:

    python exploration/llm/refresh_test_set_text.py \
        --harvest exploration/lunaris/harvest_2026_10/lunaris_full_harvest.parquet \
        --out exploration/llm/test_set_reviewed_harvest_2026_10.csv
"""

import argparse
import io
import os
import sys

import pandas as pd
import pyarrow.parquet as pq

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_test_set import as_text  # noqa: E402

TEXT_COLUMNS = ["title", "subjects", "abstract"]


def pick_one_row_per_id(harvest):
    """Keep one row per id: the latest datestamp, then the first in the file.
    Returns the rows indexed by id, and how many ids had more than one row."""
    harvest = harvest.reset_index(drop=True)
    repeated = int((harvest["id"].value_counts() > 1).sum())
    if "datestamp" in harvest.columns:
        # A stable sort keeps the file order among rows with the same datestamp.
        harvest = harvest.sort_values("datestamp", ascending=False, kind="stable",
                                      na_position="last")
    return harvest.drop_duplicates("id").set_index("id"), repeated


def as_run_trial_reads_it(frame):
    """Return the frame as run_trial.py will see it: written to CSV and read
    back with pandas' defaults."""
    buffer = io.StringIO()
    frame.to_csv(buffer, index=False)
    buffer.seek(0)
    return pd.read_csv(buffer)


def main():
    """Write the refreshed test set and report what changed."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--harvest", required=True,
                        help="lunaris_full_harvest.parquet of the newer harvest")
    parser.add_argument("--test-set", default="exploration/llm/test_set_reviewed.csv",
                        help="the labelled test set to copy (default: test_set_reviewed.csv)")
    parser.add_argument("--out", required=True, help="where to write the refreshed copy")
    parser.add_argument("--examples", type=int, default=3,
                        help="how many changed records to show (default 3)")
    args = parser.parse_args()

    if os.path.abspath(args.out) == os.path.abspath(args.test_set):
        print("ERROR: --out is the test set itself. Pick a new file name.")
        return 1
    if os.path.exists(args.out):
        print(f"ERROR: {args.out} already exists. Delete it first or pick another name.")
        return 1

    old = pd.read_csv(args.test_set, encoding="utf-8-sig")
    ids = old["record_id"].astype(str)

    columns = ["id"] + TEXT_COLUMNS
    available = pq.read_schema(args.harvest).names
    if "datestamp" in available:
        columns.append("datestamp")
    harvest = pd.read_parquet(args.harvest, columns=columns)
    harvest = harvest[harvest["id"].isin(set(ids))]
    newest, repeated = pick_one_row_per_id(harvest)

    found = ids.isin(newest.index)
    new = old.copy()
    for column in TEXT_COLUMNS:
        fresh = ids[found].map(newest[column].map(as_text))
        new.loc[found, column] = fresh.values
    new.to_csv(args.out, index=False, encoding="utf-8-sig")

    # Report.
    print(f"test set:  {args.test_set} ({len(old):,} records)")
    print(f"harvest:   {args.harvest}")
    print(f"written:   {args.out}\n")
    print(f"found in the new harvest: {found.sum():,} of {len(old):,}")
    if repeated:
        print(f"  {repeated} of them appear more than once there; the latest datestamp was used")
    missing = old.loc[~found, ["row", "record_id"]]
    if len(missing):
        print(f"missing, so their old text was kept ({len(missing)}):")
        for _, record in missing.iterrows():
            print(f"  row {record['row']}: {record['record_id']}")

    before = as_run_trial_reads_it(old[TEXT_COLUMNS]).fillna("").astype(str)
    after = as_run_trial_reads_it(new[TEXT_COLUMNS]).fillna("").astype(str)
    differs = before != after
    any_change = differs.any(axis=1)
    print(f"\nrecords whose text changed: {any_change.sum():,} of {len(old):,}")
    for column in TEXT_COLUMNS:
        print(f"  {column:<9} {differs[column].sum():>4,}")
    reviewed = old.get("reviewed_by_hand")
    if reviewed is not None:
        print(f"  of the {int((reviewed == True).sum())} reviewed by hand: "
              f"{int((any_change & (reviewed == True)).sum())} changed")

    shown = 0
    for position in old.index[any_change]:
        if shown == args.examples:
            break
        shown += 1
        print(f"\nexample {shown}: row {old.at[position, 'row']}, {old.at[position, 'record_id']}")
        for column in TEXT_COLUMNS:
            if differs.at[position, column]:
                print(f"  {column} before: {before.at[position, column][:300]!r}")
                print(f"  {column} after:  {after.at[position, column][:300]!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
