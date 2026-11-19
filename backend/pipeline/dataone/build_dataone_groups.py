"""
build_dataone_groups.py

Write one file that says which group every latest DataONE record is in,
and whether it goes on to filter 2 (the LLM step).

Reads, from the harvest folder:
  dataone_full_harvest_versions.parquet  (mark_versions.py; latest records only)
  dataone_multibox_groups.parquet        (classify_multibox_records.py)
and BC's real outline, bc_boundary_gbif.wkt (the file the Lunaris BC rules
use).

Groups:
  no_box               a box field missing (null) or NaN
  multi_box records    more than one geohash_9 value: the group from
                       dataone_multibox_groups.parquet (touches_bc_all_inside,
                       touches_bc_partly, send_to_llm, entirely_outside);
                       their reason starts with "multi-box:"
  west_gt_east         one box with west greater than east
  inside_bc            one box or point inside BC
  touches_bc_partly    one box partly overlapping BC
  sign_check           one box or point outside BC that touches BC once its
                       longitudes change sign (new west = -east, new
                       east = -west)
  entirely_outside     one box or point outside BC, also after the sign
                       change
A box with north = south and east = west is a point; a box with only one
of the two equal is a line; south and north are swapped where south is the
greater; coordinates beyond the usual ranges are tested as they are. These
are the tests of classify_multibox_records.py, imported from it.

to_filter2 is true for every group except entirely_outside.

Output: dataone_groups.parquet next to the versions file, with id,
datasource, group, reason, to_filter2. The script checks with git
check-ignore that git ignores it, and stops if it exists unless --replace.

Run, from backend/:
    ../.venv/bin/python pipeline/dataone/build_dataone_groups.py
"""

import argparse
import os
import sys

import numpy as np
import pandas as pd
import pyarrow.compute as pc
import pyarrow.parquet as pq

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from classify_multibox_records import (BC_BOUNDARY, EDGES, HARVEST, INDEX_EDGES,  # noqa: E402
                                       SECONDS_PER_RECORD, VERSIONS, bc_tests, check_ignored,
                                       flipped_touches, read_bc)

MULTIBOX = os.path.join(HARVEST, "dataone_multibox_groups.parquet")
OUT = os.path.join(HARVEST, "dataone_groups.parquet")
NOT_TO_FILTER2 = {"entirely_outside"}
GROUP_ORDER = ["no_box", "west_gt_east", "inside_bc", "touches_bc_partly", "touches_bc_all_inside",
               "send_to_llm", "sign_check", "entirely_outside"]

# The counts reported on 2026-11-19 (classify_multibox_records.py), before
# the sign check on single-box records: single-box groups, then multi-box.
LAST_REPORT = {"no_box": 246_155, "west_gt_east": 11_824, "inside_bc": 2_058, "touches_bc_partly": 12_737,
               "entirely_outside": 925_791, "multi:touches_bc_all_inside": 1, "multi:touches_bc_partly": 158,
               "multi:send_to_llm": 238, "multi:entirely_outside": 4_931}


def check_output(replace):
    """Stop before any work unless git ignores OUT and OUT does not exist yet (or --replace)."""
    check_ignored(OUT)
    if os.path.exists(OUT) and not replace:
        sys.exit(f"STOPPED: {OUT} already exists; pass --replace to write over it")


def read_latest():
    """
    Return the latest records: id, datasource, the four index box fields
    (as north, south, east, west), the number of geohash_9 values, and for
    each box field whether it is missing (null) rather than NaN.
    """
    columns = ["id", "datasource", "geohash_9"] + list(INDEX_EDGES.values())
    table = pq.read_table(VERSIONS, columns=columns + ["version_status"])
    table = table.filter(pc.equal(table.column("version_status"), "latest")).select(columns)
    latest = pd.DataFrame({"id": table.column("id").to_pandas(),
                           "datasource": table.column("datasource").to_pandas().str.replace("urn:node:", "", regex=False),
                           "geohash_count": pc.fill_null(pc.list_value_length(table.column("geohash_9")), 0).to_pandas()})
    for edge, field in INDEX_EDGES.items():
        latest[edge] = table.column(field).to_pandas()
        latest[f"{edge}_null"] = table.column(field).is_null().to_pandas()
    return latest


def no_box_reason(row):
    """Return why a record has no usable box: every field missing, some missing, or a field NaN."""
    missing = [e for e in EDGES if row[f"{e}_null"]]
    if len(missing) == 4:
        return "no box fields"
    nan = [e for e in EDGES if not row[f"{e}_null"] and np.isnan(row[e])]
    parts = []
    if missing:
        parts.append("missing: " + ", ".join(missing))
    if nan:
        parts.append("NaN: " + ", ".join(nan))
    return "; ".join(parts)


def box_text(row):
    """Return a box as "N n, S s, E e, W w" for the reason and the examples."""
    return ", ".join(f"{e[0].upper()} {row[e]:g}" for e in EDGES)


def single_box_groups(single, bc):
    """
    Return (group, reason) for the records with one geohash_9 value or none,
    from the index's four box fields.
    """
    group = pd.Series("", index=single.index, dtype=object)
    reason = pd.Series("", index=single.index, dtype=object)
    no_box = single[EDGES].isna().any(axis=1)
    group[no_box] = "no_box"
    reason[no_box] = single[no_box].apply(no_box_reason, axis=1)

    west_gt_east = ~no_box & (single["west"] > single["east"])
    group[west_gt_east] = "west_gt_east"
    reason[west_gt_east] = "one box, west > east: " + single[west_gt_east].apply(box_text, axis=1)

    test = ~no_box & ~west_gt_east
    edges = [single.loc[test, e].to_numpy() for e in EDGES]
    touches, inside = bc_tests(bc, *edges)
    flips = flipped_touches(bc, *edges)
    is_point = ((single.loc[test, "north"] == single.loc[test, "south"])
                & (single.loc[test, "east"] == single.loc[test, "west"])).to_numpy()
    shape = np.where(is_point, "one point", "one box")
    tested = np.select([inside, touches, flips], ["inside_bc", "touches_bc_partly", "sign_check"], "entirely_outside")
    words = np.select([inside, touches, flips],
                      ["inside BC", "partly overlapping BC", "outside BC, but in BC with the longitude sign flipped"],
                      "outside BC")
    group[test] = tested
    reason[test] = [f"{s} {w}" for s, w in zip(shape, words)]
    return group, reason


def multi_box_groups(multi, multibox):
    """Return (group, reason) for the multi-box records, from dataone_multibox_groups.parquet."""
    known = multibox.set_index("id")
    missing = set(multi["id"]) - set(known.index)
    extra = set(known.index) - set(multi["id"])
    if missing or extra:
        sys.exit(f"STOPPED: the multi-box file does not match the latest multi-box records "
                 f"({len(missing)} missing, {len(extra)} extra)")
    group = multi["id"].map(known["group"])
    reason = "multi-box: " + multi["id"].map(known["reason"])
    return group, reason


def check_counts(result, latest, multi_mask):
    """Stop unless the groups add up to the latest records; print them beside the last report."""
    if len(result) != len(latest) or result["id"].duplicated().any() or (result["group"] == "").any():
        sys.exit("STOPPED: the groups do not cover every latest record exactly once")
    single = result.loc[~multi_mask.to_numpy(), "group"].value_counts()
    multi = result.loc[multi_mask.to_numpy(), "group"].value_counts()
    now = {k: int(single.get(k, 0)) for k in ["no_box", "west_gt_east", "inside_bc", "touches_bc_partly",
                                               "entirely_outside", "sign_check"]}
    now.update({f"multi:{k}": int(multi.get(k, 0)) for k in ["touches_bc_all_inside", "touches_bc_partly",
                                                             "send_to_llm", "entirely_outside"]})
    rows = [{"part": k, "last_report": LAST_REPORT.get(k, 0), "now": v, "change": v - LAST_REPORT.get(k, 0)}
            for k, v in now.items()]
    table = pd.DataFrame(rows)
    print("CHECK AGAINST THE LAST REPORT (single-box parts, then multi-box)")
    print(table.to_string(index=False))
    print(f"   total now: {table['now'].sum():,}; latest records: {len(latest):,}; "
          f"last report total: {sum(LAST_REPORT.values()):,}")
    changed = table[table["change"] != 0]
    print("   only change: entirely_outside -> sign_check"
          if set(changed["part"]) <= {"entirely_outside", "sign_check"}
          and changed["change"].sum() == 0 else "   OTHER CHANGES, see the table")


def report(result, latest):
    """Print the count of each group, the top 5 datasources of each, filter 2's load, and 5 sign_check examples."""
    counts = result["group"].value_counts().reindex(GROUP_ORDER).fillna(0).astype(int)
    table = pd.DataFrame({"records": counts, "to_filter2": [g not in NOT_TO_FILTER2 for g in counts.index]})
    print("\nGROUPS")
    print(table.to_string())
    print(f"   total: {counts.sum():,}")
    print("\nTOP 5 DATASOURCES OF EACH GROUP")
    for group in GROUP_ORDER:
        top = result.loc[result["group"] == group, "datasource"].value_counts().head(5)
        print(f"   {group}: " + "; ".join(f"{d} {n:,}" for d, n in top.items()))
    sent = int(result["to_filter2"].sum())
    print(f"\nto_filter2 true: {sent:,} records, {sent * SECONDS_PER_RECORD / 3600:.1f} slice-hours "
          f"at {SECONDS_PER_RECORD} s per record")
    examples = result[result["group"] == "sign_check"].sample(5, random_state=3)
    titles = pq.read_table(VERSIONS, columns=["id", "title"],
                           filters=[("id", "in", examples["id"].tolist())]).to_pandas().set_index("id")["title"]
    boxes = latest.set_index("id").loc[examples["id"]]
    print("\nFIVE sign_check EXAMPLES")
    for record_id, ds in zip(examples["id"], examples["datasource"]):
        title = " ".join(str(titles.get(record_id, "")).split())[:110]
        print(f"   {record_id} | {ds} | {box_text(boxes.loc[record_id])} | {title}")


def main():
    """Group every latest record, check the counts, write the file and print the report."""
    parser = argparse.ArgumentParser(description="Write the group of every latest DataONE record.")
    parser.add_argument("--bc-boundary", default=BC_BOUNDARY, help="BC's outline as WKT (bc_boundary_gbif.wkt)")
    parser.add_argument("--replace", action="store_true", help="write over an existing output file")
    args = parser.parse_args()
    check_output(args.replace)
    bc = read_bc(args.bc_boundary)
    latest = read_latest()
    multi_mask = latest["geohash_count"] > 1
    group = pd.Series("", index=latest.index, dtype=object)
    reason = pd.Series("", index=latest.index, dtype=object)
    group[~multi_mask], reason[~multi_mask] = single_box_groups(latest[~multi_mask], bc)
    group[multi_mask], reason[multi_mask] = multi_box_groups(latest[multi_mask], pd.read_parquet(MULTIBOX))
    result = pd.DataFrame({"id": latest["id"], "datasource": latest["datasource"], "group": group.astype(str),
                           "reason": reason.astype(str)})
    result["to_filter2"] = ~result["group"].isin(NOT_TO_FILTER2)
    check_counts(result, latest, multi_mask)
    report(result, latest)
    result.to_parquet(OUT + ".tmp", index=False, compression="zstd")
    os.replace(OUT + ".tmp", OUT)
    print(f"\nwrote {len(result):,} rows -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
