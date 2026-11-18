"""
mark_versions.py

Mark the old versions in a DataONE harvest (harvest_dataone.py). Nothing is
deleted and no original column is changed: the output holds every row and
every original column of dataone_full_harvest.parquet, unchanged, plus
three new columns:

  version_status  "latest" or "old"
  old_reason      empty for a latest record; otherwise
                    "replaced"                  rule a
                    "older_in_series"           rule b
                    "replaced+older_in_series"  both rules
  newer_id        the id of the record that replaces it, when known:
                  for rule a the record whose obsoletes names it (several
                  joined with " | " when more than one record names it);
                  for rule b only, the latest record of its series

The rules:
  a. A record whose id appears in another record's obsoletes field is old.
     In the 2026-11-18 harvest obsoletes is one text value per record;
     a list (several values) is handled too, each value counting.
  b. Among the records that share a seriesId, one is kept and the others
     are old. The record kept is the one with the latest dateUploaded (ties:
     the latest dateModified, then the greatest id) among the members rule
     a did not mark: obsoletes is what the authors wrote, so it wins over
     the dates (Lucia, 2026-11-18; before this, 1,550 series, mostly
     HydroShare, lost every record because an older-dated member's
     obsoletes named the newest-dated one). Only when rule a marked every
     member is the record kept chosen among all members by date; rule a
     still marks it old. The dates come in two forms (with and without
     milliseconds), so they are compared as times, not as text.

The search query already asked only for records that no other record marks
as obsoletedBy, so these rules catch the old versions DataONE's own links
miss (see the third DataONE probe).

The output is written next to the harvest as
dataone_full_harvest_versions.parquet. The script checks with git
check-ignore that it is ignored by git before writing, and stops if the file
already exists unless --replace.

Run, from backend/:
    ../.venv/bin/python pipeline/dataone/mark_versions.py
"""

import argparse
import os
import subprocess
import sys

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DEFAULT_HARVEST = os.path.join(BACKEND, "data", "dataone", "harvest_2026_11_18")
IN_NAME = "dataone_full_harvest.parquet"
OUT_NAME = "dataone_full_harvest_versions.parquet"
JOIN = " | "            # joins several newer ids in newer_id

NEW_COLUMNS = [("version_status", pa.string()), ("old_reason", pa.string()), ("newer_id", pa.string())]


def check_ignored(path):
    """Stop unless git ignores the given path (exit 0 from git check-ignore)."""
    result = subprocess.run(["git", "check-ignore", "-q", os.path.abspath(path)], cwd=BACKEND,
                            capture_output=True, text=True)
    if result.returncode == 1:
        sys.exit(f"STOPPED: {path} is not ignored by git; nothing was written")
    if result.returncode != 0:
        sys.exit(f"STOPPED: git check-ignore could not check {path}: {result.stderr.strip()}")


def obsoletes_values(value):
    """
    Return the ids one obsoletes cell names, as a list: [] when empty, one
    id for a text value, every item for a list.
    """
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return []
    if isinstance(value, (list, tuple, np.ndarray)):
        return [str(v) for v in value if v is not None and str(v) != ""]
    return [str(value)] if str(value) != "" else []


def rule_replaced(frame):
    """
    Rule a. Return {id: [ids of the records whose obsoletes names it]} for
    every record of the harvest named in another record's obsoletes.
    """
    present = set(frame["id"])
    replaced = {}
    for newer, value in zip(frame["id"], frame["obsoletes"]):
        for older in obsoletes_values(value):
            if older in present and older != newer:
                replaced.setdefault(older, []).append(newer)
    return replaced


def as_time(series):
    """Return ISO 8601 date-times (with or without milliseconds) as UTC times; NaT when unreadable."""
    return pd.to_datetime(series, utc=True, format="ISO8601", errors="coerce")


def rule_older_in_series(frame, replaced_ids=frozenset()):
    """
    Rule b. Return (old, keeper): old is {id: id of the record kept for its
    series} for every other record of the series; keeper is the set of ids
    kept, one per series with more than one record.

    The record kept is chosen first among the members rule a did not mark
    (replaced_ids): obsoletes is what the authors wrote, so it wins over
    the dates. Among those, the latest dateUploaded, then the latest
    dateModified, then the greatest id. Only when rule a marked every
    member is the record kept chosen among all members, by the same dates;
    rule a still marks it old, since some other record names it.
    """
    shared = frame[frame["seriesId"].notna() & (frame["seriesId"] != "")]
    shared = shared[shared.duplicated("seriesId", keep=False)].copy()
    shared["uploaded"] = as_time(shared["dateUploaded"])
    shared["modified"] = as_time(shared["dateModified"])
    shared["not_replaced"] = ~shared["id"].isin(replaced_ids)
    # Members rule a did not mark come first; then latest first by
    # dateUploaded, dateModified and id.
    shared = shared.sort_values(["seriesId", "not_replaced", "uploaded", "modified", "id"],
                                ascending=[True, False, False, False, False], na_position="last")
    first = shared.groupby("seriesId", sort=False)["id"].transform("first")
    old = dict(zip(shared.loc[shared["id"] != first, "id"], first[shared["id"] != first]))
    return old, set(first.unique())


def mark(frame):
    """
    Return the three new columns (version_status, old_reason, newer_id) for
    the records of frame, in its row order, and the two rule results.
    """
    replaced = rule_replaced(frame)
    older, keepers = rule_older_in_series(frame, frozenset(replaced))
    status, reason, newer = [], [], []
    for record_id in frame["id"]:
        in_a, in_b = record_id in replaced, record_id in older
        if in_a and in_b:
            reason.append("replaced+older_in_series")
        elif in_a:
            reason.append("replaced")
        elif in_b:
            reason.append("older_in_series")
        else:
            reason.append("")
        status.append("old" if (in_a or in_b) else "latest")
        if in_a:
            newer.append(JOIN.join(replaced[record_id]))
        elif in_b:
            newer.append(older[record_id])
        else:
            newer.append(None)
    return status, reason, newer, replaced, older, keepers


def report(frame, replaced, older, keepers):
    """Print the counts of each rule, both, by datasource, and five examples of each rule."""
    in_a = frame["id"].isin(set(replaced))
    in_b = frame["id"].isin(set(older))
    ds = frame["datasource"].str.replace("urn:node:", "", regex=False)
    print("RULE COUNTS")
    print(pd.DataFrame([
        {"what": "rule a: replaced (named in another record's obsoletes)", "records": int(in_a.sum())},
        {"what": "rule b: older in its series", "records": int(in_b.sum())},
        {"what": "both rules", "records": int((in_a & in_b).sum())},
        {"what": "old by either rule", "records": int((in_a | in_b).sum())},
        {"what": "latest", "records": int((~(in_a | in_b)).sum())},
        {"what": "ids named by more than one obsoletes", "records": sum(len(v) > 1 for v in replaced.values())},
        {"what": "series kept-latest records that rule a marks old", "records": int(frame["id"].isin(keepers & set(replaced)).sum())},
    ]).to_string(index=False))
    table = pd.DataFrame({"rule_a": in_a.groupby(ds).sum(), "rule_b": in_b.groupby(ds).sum(),
                          "both": (in_a & in_b).groupby(ds).sum(), "old": (in_a | in_b).groupby(ds).sum(),
                          "records": ds.value_counts()})
    print("\nBY DATASOURCE (top 10 by old records)")
    print(table.sort_values("old", ascending=False).head(10).astype(int).to_string())
    report_series(frame, in_a | in_b, set(replaced), keepers)
    columns = ["id", "seriesId", "title", "dateUploaded"]
    for name, mask in (("rule a only", in_a & ~in_b), ("rule b only", in_b & ~in_a), ("both", in_a & in_b)):
        sample = frame.loc[mask, columns].sample(min(5, int(mask.sum())), random_state=1)
        sample["title"] = sample["title"].str.slice(0, 70)
        print(f"\nEXAMPLES, {name}")
        print(sample.to_string(index=False))


def report_series(frame, old, replaced_ids, keepers):
    """
    Print how many seriesIds have no latest record and how many have more
    than one (three examples of each), and how many series fell back to
    choosing among members rule a had all marked.
    """
    with_series = frame["seriesId"].notna() & (frame["seriesId"] != "")
    latest_per_series = (~old[with_series]).groupby(frame.loc[with_series, "seriesId"]).sum()
    none, several = latest_per_series[latest_per_series == 0], latest_per_series[latest_per_series > 1]
    fallback = frame["id"].isin(keepers & replaced_ids).sum()
    print("\nSERIES")
    print(pd.DataFrame([
        {"what": "seriesIds", "value": len(latest_per_series)},
        {"what": "seriesIds with no latest record", "value": len(none)},
        {"what": "seriesIds with more than one latest record", "value": len(several)},
        {"what": "series where rule a marked every member (fallback by date)", "value": int(fallback)},
    ]).to_string(index=False))
    for name, chosen in (("no latest", none), ("more than one latest", several)):
        for series in chosen.index[:3]:
            members = frame.loc[frame["seriesId"] == series, ["id", "dateUploaded"]]
            print(f"   {name}: {series}: " + "; ".join(f"{i} ({d[:10]})" for i, d in members.values))


def main():
    """Read the harvest, mark the versions, print the report, and write the marked file."""
    parser = argparse.ArgumentParser(description="Mark the old versions in a DataONE harvest.")
    parser.add_argument("--harvest", default=DEFAULT_HARVEST)
    parser.add_argument("--replace", action="store_true", help="write over an existing output file")
    args = parser.parse_args()
    source = os.path.join(args.harvest, IN_NAME)
    out = os.path.join(args.harvest, OUT_NAME)
    check_ignored(out)
    if os.path.exists(out) and not args.replace:
        sys.exit(f"STOPPED: {out} already exists; pass --replace to write over it")
    table = pq.read_table(source)
    frame = table.select(["id", "datasource", "seriesId", "obsoletes", "dateUploaded", "dateModified",
                          "title"]).to_pandas()
    if frame["id"].duplicated().any():
        sys.exit("STOPPED: the harvest holds an id twice")
    status, reason, newer, replaced, older, keepers = mark(frame)
    report(frame, replaced, older, keepers)
    if any(JOIN in i for i in frame["id"]):
        sys.exit(f"STOPPED: an id holds {JOIN!r}, so newer_id could not be split again")
    for (name, kind), values in zip(NEW_COLUMNS, (status, reason, newer)):
        table = table.append_column(pa.field(name, kind), pa.array(values, type=kind))
    tmp = out + ".tmp"
    pq.write_table(table, tmp, compression="zstd")
    os.replace(tmp, out)
    print(f"\nwrote {table.num_rows:,} rows, {table.num_columns} columns -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
