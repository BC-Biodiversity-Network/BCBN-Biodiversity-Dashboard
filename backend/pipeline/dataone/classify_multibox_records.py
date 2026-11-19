"""
classify_multibox_records.py

Put each multi-box latest DataONE record (more than one geohash_9 value)
into a group, using every box fetch_multibox_documents.py --parse read from
its original document (originals_boxes.parquet), and BC's real outline
(bc_boundary_gbif.wkt, the file the Lunaris BC rules use). Then count the
five groups again across all latest records.

How a box is tested (the same as the five-group count of 2026-11-18):
  - a box with north = south and east = west is a point;
  - a box with only north = south, or only east = west, is a line;
  - a box with south greater than north has the two swapped first;
  - coordinates beyond the usual ranges are tested as they are;
  - "touches BC" means the box or point meets BC (inside or partly
    overlapping); "inside BC" means BC contains it.

How a multi-box record is grouped. Duplicate boxes (the same four values)
are counted once; no box is removed from originals_boxes.parquet.
  touches_bc_all_inside  at least one box touches BC, and every box is
                         inside BC
  touches_bc_partly      at least one box touches BC, but not every box
                         is inside BC
  send_to_llm            no box touches BC, and the record has a box with
                         west greater than east, a flagged box (an edge
                         missing or not a number), or no usable box at all
                         (this includes the documents the parser could not
                         read); or every box is outside BC but one of them
                         lands in BC when its longitudes change sign
                         (west and east become -east and -west)
  entirely_outside       every box is usable and outside BC, also with its
                         longitudes' signs changed

The records with one box or none are grouped from the four box fields of
DataONE's index, as on 2026-11-18:
  1 no box (a field missing or NaN), 3 west greater than east,
  4a inside BC, 4b partly overlapping BC, 5 entirely outside BC.

Output: dataone_multibox_groups.parquet next to the versions file, one row
per multi-box record: id, group, boxes, boxes_touching_bc, reason. The
script checks with git check-ignore that git ignores it, and stops if it
exists unless --replace.

Run, from backend/:
    ../.venv/bin/python pipeline/dataone/classify_multibox_records.py
"""

import argparse
import os
import subprocess
import sys

import numpy as np
import pandas as pd
import pyarrow.compute as pc
import pyarrow.parquet as pq
import shapely

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
HARVEST = os.path.join(BACKEND, "data", "dataone", "harvest_2026_11_18")
VERSIONS = os.path.join(HARVEST, "dataone_full_harvest_versions.parquet")
BOXES = os.path.join(HARVEST, "originals_boxes.parquet")
PARSE_LOG = os.path.join(HARVEST, "originals", "parse_log.csv")
OUT = os.path.join(HARVEST, "dataone_multibox_groups.parquet")
BC_BOUNDARY = os.path.expanduser("~/Desktop/BCBN/Results/bc_boundary_gbif.wkt")
EDGES = ["north", "south", "east", "west"]
TEXTS = [f"{e}_text" for e in EDGES]
INDEX_EDGES = {"north": "northBoundCoord", "south": "southBoundCoord",
               "east": "eastBoundCoord", "west": "westBoundCoord"}
SECONDS_PER_RECORD = 0.76   # filter 2, qwen3.6:35b-a3b on one 3g.40gb slice (Lunaris median)

GROUP_ORDER = ["touches_bc_all_inside", "touches_bc_partly", "send_to_llm", "entirely_outside"]


def check_ignored(path):
    """Stop unless git ignores the given path (exit 0 from git check-ignore)."""
    result = subprocess.run(["git", "check-ignore", "-q", os.path.abspath(path)], cwd=BACKEND,
                            capture_output=True, text=True)
    if result.returncode == 1:
        sys.exit(f"STOPPED: {path} is not ignored by git; nothing was written")
    if result.returncode != 0:
        sys.exit(f"STOPPED: git check-ignore could not check {path}: {result.stderr.strip()}")


def read_bc(path):
    """Read BC's outline from a WKT file and return it, prepared for fast tests."""
    with open(path, encoding="utf-8") as handle:
        outline = shapely.from_wkt(handle.read())
    shapely.prepare(outline)
    return outline


def geometries(north, south, east, west):
    """
    Return one shapely geometry per box, from arrays of numbers with west
    not greater than east: a point, a line (zero width or zero height) or a
    box. South and north are swapped first where south is the greater.
    """
    north, south = np.asarray(north, float), np.asarray(south, float)
    east, west = np.asarray(east, float), np.asarray(west, float)
    top, bottom = np.maximum(north, south), np.minimum(north, south)
    point = (top == bottom) & (east == west)
    line = ((top == bottom) | (east == west)) & ~point
    area = ~(point | line)
    shapes = np.empty(len(top), dtype=object)
    shapes[point] = shapely.points(west[point], top[point])
    if line.any():
        ends = np.stack([np.column_stack([west[line], bottom[line]]), np.column_stack([east[line], top[line]])], axis=1)
        shapes[line] = shapely.linestrings(ends)
    shapes[area] = shapely.box(west[area], bottom[area], east[area], top[area])
    return shapes


def bc_tests(bc, north, south, east, west):
    """Return two boolean arrays for boxes with west not greater than east: touches BC, and inside BC."""
    shapes = geometries(north, south, east, west)
    return shapely.intersects(bc, shapes), shapely.contains(bc, shapes)


def flipped_touches(bc, north, south, east, west):
    """Return whether each box touches BC once its longitudes change sign (west, east -> -east, -west)."""
    return bc_tests(bc, north, south, -np.asarray(east, float), -np.asarray(west, float))[0]


def read_latest():
    """Return the latest records: id, datasource, the four index box fields (as north..west) and the number of geohash_9 values."""
    columns = ["id", "datasource", "geohash_9"] + list(INDEX_EDGES.values())
    table = pq.read_table(VERSIONS, columns=columns + ["version_status"])
    table = table.filter(pc.equal(table.column("version_status"), "latest")).select(columns)
    latest = table.to_pandas().rename(columns={v: k for k, v in INDEX_EDGES.items()})
    latest["datasource"] = latest["datasource"].str.replace("urn:node:", "", regex=False)
    latest["geohash_count"] = latest["geohash_9"].map(lambda v: 0 if v is None else len(v))
    return latest.drop(columns="geohash_9")


def single_box_groups(latest, bc):
    """
    Return the group ("1", "3", "4a", "4b" or "5") of each record with one
    geohash_9 value or none, from the index's four box fields, and whether
    a group 5 record would touch BC with its longitudes' signs changed.
    """
    group = pd.Series("5", index=latest.index)
    no_box = latest[EDGES].isna().any(axis=1)
    west_gt_east = ~no_box & (latest["west"] > latest["east"])
    group[no_box] = "1"
    group[west_gt_east] = "3"
    test = ~no_box & ~west_gt_east
    edges = [latest.loc[test, e].to_numpy() for e in EDGES]
    touches, inside = bc_tests(bc, *edges)
    tested = group[test].to_numpy()
    tested[touches] = "4b"
    tested[inside] = "4a"
    group[test] = tested
    flip = pd.Series(False, index=latest.index)
    outside = group == "5"
    flip[outside] = flipped_touches(bc, *[latest.loc[outside, e].to_numpy() for e in EDGES])
    return group, flip


def read_boxes(bc):
    """
    Return the distinct boxes of originals_boxes.parquet (the same four
    values and texts counted once per record), each marked usable, west >
    east, touches BC, inside BC, and touches BC with its longitudes flipped.
    """
    boxes = pd.read_parquet(BOXES)
    boxes = boxes.drop_duplicates(["id"] + EDGES + TEXTS).reset_index(drop=True)
    boxes["usable"] = boxes[EDGES].notna().all(axis=1) & (boxes["flag"] == "")
    boxes["west_gt_east"] = boxes["usable"] & (boxes["west"] > boxes["east"])
    test = boxes["usable"] & ~boxes["west_gt_east"]
    edges = [boxes.loc[test, e].to_numpy() for e in EDGES]
    touches, inside = bc_tests(bc, *edges)
    for name, values in (("touches", touches), ("inside", inside), ("flip_touches", flipped_touches(bc, *edges))):
        boxes[name] = False
        boxes.loc[test, name] = values
    return boxes


def classify(record_ids, boxes, unreadable):
    """
    Return one row per multi-box record: id, group, boxes, boxes_touching_bc
    and reason, by the rules in this file's description. `unreadable` maps
    the ids the parser could not read to its note.
    """
    per = boxes.groupby("id").agg(boxes=("usable", "size"), usable=("usable", "sum"),
                                  west_gt_east=("west_gt_east", "sum"), touches=("touches", "sum"),
                                  inside=("inside", "sum"), flip_touches=("flip_touches", "sum"))
    per["flagged"] = per["boxes"] - per["usable"]
    per = per.reindex(record_ids, fill_value=0)
    rows = []
    for record_id, r in per.iterrows():
        problems = []
        if record_id in unreadable:
            problems.append(f"document could not be read ({unreadable[record_id]})")
        if r["west_gt_east"]:
            problems.append(f"west > east in {r['west_gt_east']} box(es)")
        if r["flagged"]:
            problems.append(f"flagged value in {r['flagged']} box(es)")
        if r["usable"] - r["west_gt_east"] == 0 and record_id not in unreadable:
            problems.append("no usable box")
        if r["touches"]:
            group = "touches_bc_all_inside" if r["inside"] == r["boxes"] else "touches_bc_partly"
            reason = f"{r['touches']} of {r['boxes']} boxes touch BC, {r['inside']} inside"
            if problems:
                reason += "; also " + "; ".join(problems)
        elif problems:
            group, reason = "send_to_llm", "; ".join(problems)
        elif r["flip_touches"]:
            group = "send_to_llm"
            reason = f"all {r['boxes']} boxes outside BC, but {r['flip_touches']} land in BC with longitudes' signs changed"
        else:
            group, reason = "entirely_outside", f"all {r['boxes']} boxes outside BC"
        rows.append({"id": record_id, "group": group, "boxes": int(r["boxes"]),
                     "boxes_touching_bc": int(r["touches"]), "reason": reason})
    return pd.DataFrame(rows)


def check_output(replace):
    """Stop before any work unless git ignores OUT and OUT does not exist yet (or --replace)."""
    check_ignored(OUT)
    if os.path.exists(OUT) and not replace:
        sys.exit(f"STOPPED: {OUT} already exists; pass --replace to write over it")


def write(result):
    """Write the multi-box groups to OUT, through a temporary file."""
    result.to_parquet(OUT + ".tmp", index=False, compression="zstd")
    os.replace(OUT + ".tmp", OUT)
    print(f"\nwrote {len(result):,} rows -> {OUT}")


def report_multibox(result, boxes, latest):
    """Print the multi-box groups, the reasons for send_to_llm, and the boxes that came from other parts of the documents."""
    ds = result["id"].map(latest.set_index("id")["datasource"]).rename("datasource")
    print("MULTI-BOX RECORDS BY GROUP")
    table = result.groupby("group").agg(records=("id", "size"), boxes=("boxes", "sum"))
    print(table.reindex(GROUP_ORDER).fillna(0).astype(int).to_string())
    print(f"   total: {len(result):,}")
    llm = result[result["group"] == "send_to_llm"]
    kinds = {"document could not be read": "could not be read", "west > east": "west > east",
             "flagged value": "flagged value", "no usable box": "no usable box", "longitudes' signs": "flipped longitude"}
    print("\n   send_to_llm, records with each reason (one record can have several):")
    print(pd.DataFrame([{"reason": label, "records": int(llm["reason"].str.contains(key, regex=False).sum())}
                        for key, label in kinds.items()]).to_string(index=False))
    touching = result[result["group"].str.startswith("touches")]
    also = touching["reason"].str.contains("also", regex=False)
    print(f"   touches_bc records that also have a west > east or flagged box: {int(also.sum())}")
    print("\n   top datasources by group:")
    print(pd.crosstab(ds, result["group"]).reindex(columns=GROUP_ORDER, fill_value=0)
          .assign(total=lambda f: f.sum(axis=1)).sort_values("total", ascending=False).head(8).to_string())


def report_five_groups(latest, single, flip, result):
    """Print the five groups across all latest records before and after the multi-box records were placed, and filter 2's load."""
    multi = latest["geohash_count"] > 1
    groups = single[~multi].value_counts()
    placed = result["group"].map({"touches_bc_all_inside": "4a", "touches_bc_partly": "4b",
                                  "entirely_outside": "5", "send_to_llm": "2"}).value_counts()
    names = [("1", "1 no box"), ("2", "2 several boxes"), ("3", "3 west > east"),
             ("4a", "4a inside BC"), ("4b", "4b partly overlapping BC"), ("5", "5 entirely outside BC")]
    rows = []
    for key, name in names:
        before = int(groups.get(key, 0)) + (int(multi.sum()) if key == "2" else 0)
        after = int(groups.get(key, 0)) + int(placed.get(key, 0))
        rows.append({"group": name if key != "2" else "2 several boxes -> after: send to LLM",
                     "before": before, "after": after, "change": after - before})
    table = pd.DataFrame(rows)
    print("\nFIVE GROUPS, ALL LATEST RECORDS")
    print(table.to_string(index=False))
    print(f"   total before: {table['before'].sum():,}; after: {table['after'].sum():,}; latest records: {len(latest):,}")
    to_filter2 = table.loc[table["group"].str[0].isin(["1", "2", "3", "4"]), ["before", "after"]].sum()
    for when in ("before", "after"):
        hours = to_filter2[when] * SECONDS_PER_RECORD / 3600
        print(f"   to filter 2 ({when}): {to_filter2[when]:,} records, {hours:.1f} slice-hours at {SECONDS_PER_RECORD} s")
    five = single[~multi] == "5"
    print(f"   not applied: group 5 single-box records that would land in BC with the longitude flip: "
          f"{int((flip & ~multi & five).sum()):,} of {int(five.sum()):,}")


def main():
    """Group the multi-box records, write the result, and print the five groups for all latest records."""
    parser = argparse.ArgumentParser(description="Group the multi-box DataONE records by their boxes and BC.")
    parser.add_argument("--bc-boundary", default=BC_BOUNDARY, help="BC's outline as WKT (bc_boundary_gbif.wkt)")
    parser.add_argument("--replace", action="store_true", help="write over an existing output file")
    args = parser.parse_args()
    pd.set_option("display.width", 200)
    check_output(args.replace)
    bc = read_bc(args.bc_boundary)
    latest = read_latest()
    single, flip = single_box_groups(latest, bc)
    record_ids = latest.loc[latest["geohash_count"] > 1, "id"].sort_values().tolist()
    boxes = read_boxes(bc)
    missing = set(boxes["id"]) - set(record_ids)
    if missing:
        sys.exit(f"STOPPED: {len(missing)} ids in {BOXES} are not multi-box latest records")
    log = pd.read_csv(PARSE_LOG)
    unreadable = {i: "empty document" if "no element found" in p else p for i, p in zip(log["id"], log["problem"])}
    result = classify(record_ids, boxes, unreadable)
    report_multibox(result, boxes, latest)
    report_five_groups(latest, single, flip, result)
    write(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
