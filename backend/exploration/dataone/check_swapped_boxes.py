"""
check_swapped_boxes.py

Read only. Check E: would any box in the "entirely outside BC" group land
in BC if its latitude and longitude had been written the wrong way round?
This saves, and extends, the check of 2026-11-19, which was run only in
the terminal (a here-document in the session of that day).

Which boxes (as on 2026-11-19):
  - the records whose group is entirely_outside in dataone_groups.parquet
    (build_dataone_groups.py);
  - single-box records (one geohash_9 value or none in the versions file):
    the four box fields of DataONE's index;
  - multi-box records (more than one geohash_9 value): every box of
    originals_boxes.parquet, the same four values and texts counted once
    per record, with all four edges numbers and no flag.

How a box is swapped: the whole box at once. The new north and south are
the old east and west, and the new east and west are the old north and
south (the larger as east). Four ways, the first two those of 2026-11-19:
  swap                 as above
  swap, flip lon       the new longitudes change sign (new east = -old
                       south-most, new west = -old north-most)
  swap, flip lat       the new latitudes change sign (-old east, -old west)
  swap, flip both      both
Corners swapped one at a time are not tried (see the report of 2026-11-24).
"In BC" and "touches BC" are classify_multibox_records.bc_tests (shapely
contains and intersects with bc_boundary_gbif.wkt); geometries() puts
south and north in order and makes a point or a line of a box with no area.

As a test of the test, a made-up Vancouver box is written swapped in each
of the four ways, and each way must find its own version.

Output: ~/Desktop/BCBN/Results/dataone_filter3_checks_2026-11-24/swapped_boxes_counts.csv
(one row per way: boxes and records in BC and touching BC), and the boxes
that land in or touch BC, if any, in swapped_boxes_found.csv.

Run, from backend/:
    ../.venv/bin/python exploration/dataone/check_swapped_boxes.py
"""

import os
import sys

import numpy as np
import pandas as pd
import pyarrow.compute as pc
import pyarrow.parquet as pq

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(BACKEND, "pipeline", "dataone"))
from classify_multibox_records import BC_BOUNDARY, BOXES, EDGES, HARVEST, INDEX_EDGES, VERSIONS, bc_tests, read_bc  # noqa: E402

GROUPS = os.path.join(HARVEST, "dataone_groups.parquet")
OUT_DIR = os.path.expanduser("~/Desktop/BCBN/Results/dataone_filter3_checks_2026-11-24")
WAYS = ["swap", "swap, flip lon", "swap, flip lat", "swap, flip both"]
# The 2026-11-19 counts: no box lands in or touches BC in the first two ways.
NOV19 = {"swap": 0, "swap, flip lon": 0}
# A made-up box around Vancouver (north, south, east, west).
VANCOUVER = (49.3, 49.2, -123.0, -123.2)


def swapped(north, south, east, west, way):
    """
    Return (north, south, east, west) of boxes swapped one way: the new
    latitudes from the old east and west, the new longitudes from the old
    north and south (west not greater than east), with the signs changed
    as the way says.
    """
    lo, hi = np.minimum(north, south), np.maximum(north, south)
    new_n, new_s = np.asarray(east, float), np.asarray(west, float)
    new_e, new_w = hi, lo
    if way in ("swap, flip lon", "swap, flip both"):
        new_e, new_w = -lo, -hi
    if way in ("swap, flip lat", "swap, flip both"):
        new_n, new_s = -new_n, -new_s
    return new_n, new_s, new_e, new_w


def written_swapped(box, way):
    """Return how a box (north, south, east, west) would be written if it were swapped one way: the inverse of swapped()."""
    north, south, east, west = box
    lat_sign = -1 if way in ("swap, flip lat", "swap, flip both") else 1
    lon_sign = -1 if way in ("swap, flip lon", "swap, flip both") else 1
    lons = sorted([lon_sign * east, lon_sign * west])
    return np.array([lons[1]]), np.array([lons[0]]), np.array([lat_sign * north]), np.array([lat_sign * south])


def test_the_test(bc):
    """Stop unless each way finds the Vancouver box written swapped that way, and only that way."""
    for written in WAYS:
        found = [way for way in WAYS if bc_tests(bc, *swapped(*written_swapped(VANCOUVER, written), way))[1][0]]
        if found != [written]:
            sys.exit(f"STOPPED: a Vancouver box written '{written}' is found by {found}, not by itself only")
    print("test of the test: each way finds its own swapped Vancouver box, and no other way does")


def read_boxes():
    """
    Return the boxes to test, one row each: id, kind (single or multi), north,
    south, east, west, as described at the top. Prints the counts.
    """
    groups = pd.read_parquet(GROUPS, columns=["id", "group"])
    outside = set(groups.loc[groups["group"] == "entirely_outside", "id"])
    table = pq.read_table(VERSIONS, columns=["id", "geohash_9", "version_status"] + list(INDEX_EDGES.values()))
    table = table.filter(pc.equal(table.column("version_status"), "latest"))
    latest = pd.DataFrame({"id": table.column("id").to_pandas(),
                           "geohash": pc.fill_null(pc.list_value_length(table.column("geohash_9")), 0).to_pandas()})
    for edge, field in INDEX_EDGES.items():
        latest[edge] = table.column(field).to_pandas()
    latest = latest[latest["id"].isin(outside)]
    single = latest[latest["geohash"] <= 1].assign(kind="single")
    multi_ids = set(latest.loc[latest["geohash"] > 1, "id"])
    boxes = pd.read_parquet(BOXES)
    boxes = boxes[boxes["id"].isin(multi_ids)].drop_duplicates(["id"] + EDGES + [f"{e}_text" for e in EDGES])
    usable = boxes[EDGES].notna().all(axis=1) & (boxes["flag"].fillna("") == "")
    print(f"entirely_outside: {len(latest):,} records; single {len(single):,} (with an edge missing: "
          f"{int(single[EDGES].isna().any(axis=1).sum())}, west > east: {int((single['west'] > single['east']).sum())}); "
          f"multi-box {len(multi_ids):,} records, {len(boxes):,} distinct boxes, {int((~usable).sum())} not usable")
    multi = boxes[usable].assign(kind="multi")
    return pd.concat([single[["id", "kind"] + EDGES], multi[["id", "kind"] + EDGES]], ignore_index=True)


def count_ways(bc, boxes):
    """Return one row per way (boxes and records in BC and touching BC, single and multi) and the boxes found."""
    edges = [boxes[e].to_numpy(float) for e in EDGES]
    rows, found = [], []
    for way in WAYS:
        touches, inside = bc_tests(bc, *swapped(*edges, way))
        row = {"way": way}
        for name, mask in (("in_bc", inside), ("touches_bc", touches)):
            hit = boxes[mask]
            row[f"single_records_{name}"] = int((hit["kind"] == "single").sum())
            row[f"multi_boxes_{name}"] = int((hit["kind"] == "multi").sum())
            row[f"multi_records_{name}"] = hit.loc[hit["kind"] == "multi", "id"].nunique()
        rows.append(row)
        found.append(boxes[touches].assign(way=way, in_bc=inside[touches]))
    return pd.DataFrame(rows), pd.concat(found, ignore_index=True)


def main():
    """Test the test, count each way, compare with 2026-11-19, and write the two files."""
    bc = read_bc(BC_BOUNDARY)
    test_the_test(bc)
    boxes = read_boxes()
    counts, found = count_ways(bc, boxes)
    counts.insert(1, "boxes_tested", len(boxes))
    print(counts.to_string(index=False))
    for way, expected in NOV19.items():
        row = counts[counts["way"] == way].iloc[0]
        total = sum(int(v) for k, v in row.items() if k.endswith("touches_bc"))
        print(f"{way}: {'as on 2026-11-19 (0)' if total == expected else f'NOT as on 2026-11-19: {total} touch BC'}")
    lat_bad = ((boxes["north"].abs() > 90) | (boxes["south"].abs() > 90))
    print(f"boxes with a latitude beyond 90: {int(lat_bad.sum())} (single {int((lat_bad & (boxes['kind'] == 'single')).sum())}, "
          f"multi {int((lat_bad & (boxes['kind'] == 'multi')).sum())})")
    os.makedirs(OUT_DIR, exist_ok=True)
    counts.to_csv(os.path.join(OUT_DIR, "swapped_boxes_counts.csv"), index=False, encoding="utf-8-sig")
    found.to_csv(os.path.join(OUT_DIR, "swapped_boxes_found.csv"), index=False, encoding="utf-8-sig")
    print(f"boxes found in any way: {len(found)}; wrote {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
