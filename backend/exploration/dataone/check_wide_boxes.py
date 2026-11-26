"""
check_wide_boxes.py

Read only. Looks at the DataONE records in the entirely_outside group
(thrown out by the rule, never sent to the LLM) for boxes that are wider
than 180 degrees of longitude: west < east and east - west > 180. Some of
these were probably meant to cross the 180 degree meridian, with west and
east written the wrong way round. Example from the label set, "Pacific
Ocean": N 53, S 0, E 170.598, W -108.748. Read as it is, it runs 279
degrees from -108.748 east through 0 to 170.598 and misses BC; read as
crossing 180, it runs 81 degrees from 170.598 across 180 to -108.748 and
touches BC.

Each wide box is read the other way, as two boxes with the same north and
south: east to 180, and -180 to west. The reading touches BC if either
piece touches it, and is inside BC if every piece is inside. The BC
outline and the touches / inside tests are the ones build_dataone_groups.py
uses (bc_boundary_gbif.wkt; bc_tests from classify_multibox_records.py).

Single-box records (one geohash_9 value or none): the four box fields of
DataONE's index. Multi-box records: every box in originals_boxes.parquet
(the same four values and texts counted once per record, as
classify_multibox_records.py does); a record counts if any of its boxes
touches.

The records that touch are matched against Lunaris by DOI or UUID with the
functions of match_lunaris.py; title matches are shown but, as decided,
never used to skip the LLM.

Export (added 2026-11-20): the single-box records that touch BC read as
crossing 180 are written, one row per record, to
~/Desktop/BCBN/Results/dataone_wide_boxes_2026-11-20/wide_boxes_touch_bc.csv
(outside the repo) for checking by hand:
  id, seriesId, datasource, title, abstract, keywords, then every other
  text field of the harvest parquet as harvested (a list field, such as
  keywords, is written as a JSON list, ["first", "second"], because some
  keywords themselves hold "; "; nothing is cleaned or shortened), then north, south, east, west as DataONE's index has
  them, width_as_is, width_crossing_180, link (built as in
  build_label_set.py) and group (from dataone_groups.parquet).
The file is read back and checked before the script ends. Nothing else is
written. No network requests.

Run, from backend/:
    ../.venv/bin/python exploration/dataone/check_wide_boxes.py
"""

import json
import os
import sys

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, "..", ".."))
PIPELINE = os.path.join(BACKEND, "pipeline", "dataone")
sys.path.insert(0, PIPELINE)
sys.path.insert(0, os.path.join(BACKEND, "pipeline", "lunaris"))
from build_dataone_groups import OUT as GROUPS, read_latest  # noqa: E402
from classify_multibox_records import (BC_BOUNDARY, BOXES, EDGES, SECONDS_PER_RECORD, VERSIONS,  # noqa: E402
                                       bc_tests, read_bc)
from match_lunaris import STRONG, build_keys, match_one, read_lunaris  # noqa: E402
from build_label_set import VIEW  # noqa: E402
from urllib.parse import quote  # noqa: E402

WIDE = 180          # a box wider than this many degrees of longitude is checked
EXAMPLES = 15
EXPORT_DIR = os.path.expanduser("~/Desktop/BCBN/Results/dataone_wide_boxes_2026-11-20")
EXPORT = os.path.join(EXPORT_DIR, "wide_boxes_touch_bc.csv")
EXPECTED_ROWS = 29  # the count from the run of 2026-11-20
FIRST_COLUMNS = ["id", "seriesId", "datasource", "title", "abstract", "keywords"]
BOX_FIELDS = {"north": "northBoundCoord", "south": "southBoundCoord",
              "east": "eastBoundCoord", "west": "westBoundCoord"}


def is_wide(east, west):
    """Return True where a box has west < east and is wider than 180 degrees as it is."""
    east, west = np.asarray(east, float), np.asarray(west, float)
    return (west < east) & (east - west > WIDE)


def crossing_tests(bc, north, south, east, west):
    """
    Read each box as crossing 180: one piece from east to 180 and one from
    -180 to west, both with the box's north and south. Return two boolean
    arrays: the reading touches BC (either piece), and it is inside BC
    (every piece). A piece with no width left (east at or beyond 180, or
    west at or beyond -180) is left out.
    """
    north, south = np.asarray(north, float), np.asarray(south, float)
    east, west = np.asarray(east, float), np.asarray(west, float)
    n = len(north)
    touches = np.zeros(n, bool)
    inside = np.ones(n, bool)
    has_piece = np.zeros(n, bool)
    # Piece 1: from the old east, going east, up to 180.
    # Piece 2: from -180, going east, up to the old west.
    for lo, hi in ((east, np.full(n, 180.0)), (np.full(n, -180.0), west)):
        real = lo < hi
        if real.any():
            t, i = bc_tests(bc, north[real], south[real], hi[real], lo[real])
            touches[real] |= t
            inside[real] &= i
            has_piece |= real
    return touches, inside & has_piece


def self_test(bc):
    """
    Check the test on made-up boxes before using it: one that should touch
    BC only when read as crossing 180, and one that should not touch either
    way. Print both and stop if either comes out wrong.
    """
    cases = pd.DataFrame([
        {"case": "should touch: N Pacific, lat 45-55, from 170 E across 180 to -110",
         "north": 55.0, "south": 45.0, "east": 170.0, "west": -110.0, "expect": True},
        {"case": "should not touch: tropical Pacific, lat 0-30, same longitudes",
         "north": 30.0, "south": 0.0, "east": 170.0, "west": -110.0, "expect": False},
    ])
    as_is = bc_tests(bc, *[cases[e].to_numpy() for e in EDGES])[0]
    touches, inside = crossing_tests(bc, *[cases[e].to_numpy() for e in EDGES])
    cases["wide"] = is_wide(cases["east"], cases["west"])
    cases["touches as it is"] = as_is
    cases["touches crossing 180"] = touches
    cases["inside crossing 180"] = inside
    cases["right"] = (cases["touches crossing 180"] == cases["expect"]) & cases["wide"] & ~cases["touches as it is"]
    print("6. SELF-TEST (made-up boxes)")
    print(cases.drop(columns="expect").to_string(index=False))
    if not cases["right"].all():
        sys.exit("STOPPED: the self-test failed")
    print()


def read_boxes():
    """
    Return one row per box of the entirely_outside records: id, datasource,
    kind (single or multi) and the four box values. Single-box records use
    the index fields; multi-box records use originals_boxes.parquet.
    """
    groups = pd.read_parquet(GROUPS, columns=["id", "datasource", "group"])
    outside = groups[groups["group"] == "entirely_outside"]
    latest = read_latest()
    latest = latest[latest["id"].isin(outside["id"])]
    single = latest[latest["geohash_count"] <= 1][["id", "datasource"] + EDGES].assign(kind="single")
    multi_ids = set(latest.loc[latest["geohash_count"] > 1, "id"])
    boxes = pd.read_parquet(BOXES)
    boxes = boxes[boxes["id"].isin(multi_ids)]
    boxes = boxes.drop_duplicates(["id"] + EDGES + [f"{e}_text" for e in EDGES])
    missing = multi_ids - set(boxes["id"])
    if missing:
        sys.exit(f"STOPPED: {len(missing)} multi-box records have no boxes in originals_boxes.parquet")
    multi = boxes.merge(outside[["id", "datasource"]], on="id")[["id", "datasource"] + EDGES].assign(kind="multi")
    every = pd.concat([single, multi], ignore_index=True)
    unusable = every[EDGES].isna().any(axis=1)
    print(f"entirely_outside records: {len(outside):,} (single {len(single):,}, multi {len(multi_ids):,}, "
          f"{len(multi):,} distinct boxes); boxes with a missing value: {int(unusable.sum())}\n")
    return every[~unusable].reset_index(drop=True)


def count_table(wide):
    """Return the counts for points 1 and 2: boxes and records, single and multi, wide / touching / inside."""
    rows = []
    for kind in ("single", "multi"):
        part = wide[wide["kind"] == kind]
        row = {"kind": kind}
        for name, mask in (("wide", slice(None)), ("touch", part["touches"]), ("inside", part["inside"])):
            chosen = part.loc[mask]
            row[f"{name}_boxes"] = len(chosen)
            row[f"{name}_records"] = chosen["id"].nunique()
        rows.append(row)
    table = pd.DataFrame(rows).set_index("kind")
    table.loc["total"] = table.sum()
    return table


def by_datasource(wide):
    """Return wide and touching records per datasource (point 3), most touching first."""
    per = wide.groupby(["datasource", "id"]).agg(touches=("touches", "any")).reset_index()
    table = per.groupby("datasource").agg(wide_records=("id", "size"), touch_records=("touches", "sum"))
    return table.sort_values(["touch_records", "wide_records"], ascending=False)


def pick_examples(touching, count):
    """Return up to `count` touching boxes, one per datasource in turn so as many datasources as possible appear."""
    shuffled = touching.drop_duplicates("id").sample(frac=1, random_state=20261120)
    order = shuffled["datasource"].value_counts().index
    queues = {d: list(shuffled[shuffled["datasource"] == d].index) for d in order}
    chosen = []
    while len(chosen) < count and any(queues.values()):
        for d in order:
            if queues[d] and len(chosen) < count:
                chosen.append(queues[d].pop(0))
    return touching.loc[chosen]


def titles_of(ids):
    """Return {id: title} for the given ids, from the latest-versions file."""
    table = pq.read_table(VERSIONS, columns=["id", "title"], filters=[("id", "in", list(ids))]).to_pandas()
    return table.drop_duplicates("id").set_index("id")["title"].to_dict()


def lunaris_matches(ids):
    """
    Return one row per touching record: match_type (doi, uuid, title_only
    or none, as in match_lunaris.py) and the Lunaris filter 2 decisions of
    the DOI or UUID matches (yes, no or mixed).
    """
    columns = ["id", "seriesId", "webUrl", "title"]
    records = pq.read_table(VERSIONS, columns=columns, filters=[("id", "in", list(ids))]).to_pandas()
    records = records.drop_duplicates("id")
    lunaris = read_lunaris()
    keys = build_keys(lunaris)
    rows = []
    for record in records.to_dict("records"):
        found = match_one(record, keys)
        ways = set().union(*found.values()) if found else set()
        match_type = "doi" if "doi" in ways else "uuid" if "uuid" in ways else "title_only" if ways else "none"
        strong = [i for i, w in found.items() if w & set(STRONG)]
        decisions = {lunaris.at[i, "filter2"] for i in strong}
        decision = "" if not decisions else decisions.pop() if len(decisions) == 1 else "mixed"
        rows.append({"id": record["id"], "match_type": match_type, "lunaris_filter2": decision})
    return pd.DataFrame(rows)


def as_cell(value):
    """
    Return one harvest value as a CSV cell, unchanged: a list becomes a
    JSON list with its items as they are (non-ASCII letters kept), a
    missing value becomes "".
    """
    if value is None or (isinstance(value, float) and value != value):
        return ""
    if not isinstance(value, str) and hasattr(value, "__iter__"):
        return json.dumps([None if v is None else str(v) for v in value], ensure_ascii=False)
    return value


def export_rows(touching):
    """
    Return the export table for the touching single-box records: every
    text field of the harvest parquet as harvested, the four index box
    values, both widths, the dataset page link and the current group.
    """
    single = touching[touching["kind"] == "single"].drop_duplicates("id")
    ids = single["id"].tolist()
    table = pq.read_table(VERSIONS, filters=[("id", "in", ids)])
    table = table.filter(pc.equal(table.column("version_status"), "latest"))
    fields = table.to_pandas()
    if len(fields) != len(ids) or fields["id"].duplicated().any():
        sys.exit("STOPPED: the harvest does not hold exactly one latest row for each record")
    text_fields = [f.name for f in table.schema
                   if f.name not in BOX_FIELDS.values()
                   and (pa.types.is_string(f.type) or pa.types.is_list(f.type))]
    other = [n for n in text_fields if n not in FIRST_COLUMNS]
    out = pd.DataFrame({n: fields[n].map(as_cell) for n in FIRST_COLUMNS + other})
    for edge, field in BOX_FIELDS.items():
        out[edge] = fields[field]
    out["width_as_is"] = out["east"] - out["west"]
    out["width_crossing_180"] = 360 - out["width_as_is"]
    out["link"] = [VIEW + quote(i, safe="") for i in out["id"]]
    groups = pd.read_parquet(GROUPS, columns=["id", "group"]).set_index("id")["group"]
    out["group"] = out["id"].map(groups)
    return out.sort_values(["datasource", "id"]).reset_index(drop=True)


def check_export(path, expected_ids, bc):
    """
    Read the CSV back and stop unless it has exactly EXPECTED_ROWS rows,
    the same ids as the check found, each record in entirely_outside, with
    a box wider than 180 that touches BC read as crossing 180, and the
    title, abstract and keywords exactly as in the harvest.
    """
    back = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    problems = []
    if len(back) != EXPECTED_ROWS:
        problems.append(f"{len(back)} rows, not {EXPECTED_ROWS}")
    if set(back["id"]) != set(expected_ids) or back["id"].duplicated().any():
        problems.append("the ids differ from the ones the check found")
    if (back["group"] != "entirely_outside").any():
        problems.append("a record is not in entirely_outside")
    edges = [back[e].astype(float).to_numpy() for e in EDGES]
    if not is_wide(edges[2], edges[3]).all() or not crossing_tests(bc, *edges)[0].all():
        problems.append("a box is not wide or does not touch BC read as crossing 180")
    harvest = pq.read_table(VERSIONS, columns=["id", "title", "abstract", "keywords", "version_status"],
                            filters=[("id", "in", back["id"].tolist())]).to_pandas()
    harvest = harvest[harvest["version_status"] == "latest"].set_index("id")
    for column in ("title", "abstract", "keywords"):
        same = [as_cell(harvest.at[i, column]) == v for i, v in zip(back["id"], back[column])]
        if not all(same):
            problems.append(f"{column} differs from the harvest in {len(same) - sum(same)} rows")
    if problems:
        sys.exit("STOPPED: the export check failed: " + "; ".join(problems))
    print(f"   read back: {len(back)} rows, {len(back.columns)} columns; ids, group, boxes, titles, "
          f"abstracts and keywords all match the harvest")


def export(touching, bc):
    """Write the touching single-box records to EXPORT, then read the file back and check it."""
    rows = export_rows(touching)
    os.makedirs(EXPORT_DIR, exist_ok=True)
    rows.to_csv(EXPORT + ".tmp", index=False, encoding="utf-8-sig")
    os.replace(EXPORT + ".tmp", EXPORT)
    print(f"\nEXPORT: wrote {len(rows)} rows -> {EXPORT}")
    print(f"   columns: {', '.join(rows.columns)}")
    check_export(EXPORT, touching.loc[touching["kind"] == "single", "id"].unique(), bc)


def main():
    """Run the self-test, check every wide box, print the report and write the export."""
    pd.set_option("display.width", 250)
    pd.set_option("display.max_colwidth", 70)
    bc = read_bc(BC_BOUNDARY)
    self_test(bc)
    boxes = read_boxes()
    wide = boxes[is_wide(boxes["east"], boxes["west"])].copy()
    wide["touches"], wide["inside"] = crossing_tests(bc, *[wide[e].to_numpy() for e in EDGES])
    wide["width_as_is"] = wide["east"] - wide["west"]
    wide["width_crossing"] = 360 - wide["width_as_is"]

    print("1-2. WIDE BOXES (west < east, wider than 180) AND THOSE THAT TOUCH BC READ AS CROSSING 180")
    print(count_table(wide).to_string())
    multi = wide[wide["kind"] == "multi"].groupby("id")["touches"].agg(["size", "sum"])
    print(f"   multi-box records with a touching box: {int((multi['sum'] > 0).sum())}, "
          f"of which every wide box touches: {int(((multi['sum'] == multi['size']) & (multi['sum'] > 0)).sum())}")
    print(f"   ('inside' needs both pieces inside BC; the piece from east to 180 can only be inside if "
          f"east is 180 or more: {int((wide['east'] >= 180).sum())} wide boxes)")
    big = wide["width_as_is"] > 360
    print(f"   wide boxes wider than 360 as they are (longitudes beyond 180): {int(big.sum())}, "
          f"touching: {int((big & wide['touches']).sum())}\n")

    print("3. BY DATASOURCE (records)")
    print(by_datasource(wide).to_string())

    touching = wide[wide["touches"]]
    titles = titles_of(touching["id"].unique())
    print(f"\n4. {EXAMPLES} EXAMPLES THAT TOUCH BC READ AS CROSSING 180")
    examples = pick_examples(touching, EXAMPLES).copy()
    examples["title"] = [" ".join(str(titles.get(i, "")).split())[:70] for i in examples["id"]]
    show = ["id", "datasource", "kind", "title"] + EDGES + ["width_as_is", "width_crossing"]
    print(examples[show].round(3).to_string(index=False))
    label = touching[(touching["north"].round(3) == 53) & (touching["east"].round(3) == 170.598)
                     & (touching["west"].round(3) == -108.748)]
    print(f"   the label-set example (N 53, E 170.598, W -108.748) found among the touching boxes: "
          f"{len(label)} box(es), ids {label['id'].tolist()[:3]}")

    print("\n5. LUNARIS MATCHES OF THE TOUCHING RECORDS (DOI or UUID, as in match_lunaris.py)")
    matches = lunaris_matches(touching["id"].unique())
    matches = matches.merge(touching.drop_duplicates("id")[["id", "kind"]], on="id")
    print(pd.crosstab(matches["match_type"], matches["kind"], margins=True).to_string())
    strong = matches[matches["match_type"].isin(STRONG)]
    print("   Lunaris filter 2 decision of the DOI or UUID matches: "
          + str(strong["lunaris_filter2"].value_counts().to_dict()))
    new = int((~matches["match_type"].isin(STRONG)).sum())
    print(f"   new for filter 2 (no DOI or UUID match; title_only included): {new:,} records, "
          f"{new * SECONDS_PER_RECORD:,.0f} s = {new * SECONDS_PER_RECORD / 3600:.2f} slice-hours "
          f"at {SECONDS_PER_RECORD} s per record")
    export(touching, bc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
