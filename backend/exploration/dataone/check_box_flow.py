"""
check_box_flow.py

Read only. Check D: for each group of records picked by its box, how many
records are left at each stage, from the harvest to the front end group.
Output: ~/Desktop/BCBN/Results/dataone_filter3_checks_2026-11-24/box_checks_flow.csv

The stages (columns), each counted in records:
  latest              latest-version records (dataone_groups.parquet holds
                      exactly these, one row each)
  to_filter2          not ruled out by the boxes (dataone_groups.to_filter2)
  reused_lunaris      took the Lunaris filter 2 answer
                      (filter2_union.parquet, reuse_lunaris)
  no_text             to_filter2 but not in the filter 2 union: no title,
                      abstract or keywords (filter2_input/no_text_records.csv)
  asked_filter2       asked the filter 2 models (union rows not reused)
  kept_filter2        keep_union true in filter2_union.parquet
  in_filter3          rows of filter3_final.parquet (kept_filter2 plus the
                      either-yes records)
  route_*, class_*, group_*   filter 3 route, final_class, frontend_group

The groups (rows). A record is single-box when dataone_groups' reason does
not start with "multi-box:" (one geohash_9 value or none), multi-box when
it does.
  From dataone_groups.parquet, column group (and the multi-box mark):
    single box entirely outside BC   entirely_outside, single
    multi-box, all outside           entirely_outside, multi
    multi-box, touching BC           touches_bc_all_inside and
                                     touches_bc_partly, multi
    multi-box, sent to the LLM       send_to_llm
    single box inside BC             inside_bc
    single box partly in BC          touches_bc_partly, single
    west > east                      west_gt_east
    sign_check                       sign_check
    no box                           no_box
  From the box numbers. Single-box records: the four box fields of
  DataONE's index (northBoundCoord and the others in the versions file).
  Multi-box records: every box of originals_boxes.parquet with four numbers;
  the record counts if any box does. These rows cut across the groups above.
    wider than 180 degrees           single only: west <= east and
                                     east - west > 180; split by whether
                                     the box as written touches BC (group
                                     inside_bc or touches_bc_partly) or not
    west 180 and east -180           west == 180 and east == -180
    latitude beyond 90               |north| or |south| > 90
    longitude beyond 180             |east| or |west| > 180
    longitude equal to latitude      east or west equals north or south,
                                     not when the number is 0 (the rules of
                                     geo_record_text.odd_number_notes)

Run, from backend/:
    ../.venv/bin/python exploration/dataone/check_box_flow.py
"""

import os
import sys

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(BACKEND, "pipeline", "dataone"))
sys.path.insert(0, os.path.join(BACKEND, "pipeline", "lunaris", "llm"))
from classify_multibox_records import BOXES, EDGES, HARVEST, INDEX_EDGES, VERSIONS  # noqa: E402
from geo_pass2_classes import CLASSES  # noqa: E402

GROUPS = os.path.join(HARVEST, "dataone_groups.parquet")
UNION = os.path.join(BACKEND, "data", "dataone", "filter2_results", "filter2_union.parquet")
NO_TEXT = os.path.join(BACKEND, "data", "dataone", "filter2_input", "no_text_records.csv")
FINAL = os.path.join(BACKEND, "data", "dataone", "filter3", "filter3_final.parquet")
OUT = os.path.expanduser("~/Desktop/BCBN/Results/dataone_filter3_checks_2026-11-24/box_checks_flow.csv")
ROUTES = ["lunaris", "rule", "llm", "hand", "lunaris_either_yes"]
FRONTEND = ["bc_data", "pending", "dropped"]

pd.set_option("display.width", 300)
pd.set_option("display.max_columns", 40)


# --- Reading ------------------------------------------------------------------------

def read_groups():
    """Return dataone_groups.parquet indexed by id, with a multi column (reason starts with "multi-box:")."""
    groups = pd.read_parquet(GROUPS).set_index("id")
    groups["multi"] = groups["reason"].str.startswith("multi-box:")
    return groups


def read_index_boxes(ids):
    """Return the index box (north, south, east, west) of the given latest records, by id."""
    table = pq.read_table(VERSIONS, columns=["id", "version_status"] + list(INDEX_EDGES.values())).to_pandas()
    table = table[(table["version_status"] == "latest") & table["id"].isin(set(ids))].drop_duplicates("id")
    return table.set_index("id").rename(columns={v: k for k, v in INDEX_EDGES.items()})[EDGES].astype(float)


def read_multi_boxes(ids):
    """Return every box with four numbers of the given multi-box records (originals_boxes.parquet)."""
    boxes = pd.read_parquet(BOXES, columns=["id"] + EDGES)
    boxes = boxes[boxes["id"].isin(set(ids))]
    return boxes[boxes[EDGES].notna().all(axis=1)].astype({e: float for e in EDGES})


# --- The odd boxes ---------------------------------------------------------------------

def odd_marks(boxes):
    """
    Return, for each box (a table with north, south, east, west), whether
    it is a west 180 / east -180 box, has a latitude beyond 90, a longitude
    beyond 180, or a longitude equal to a latitude (not 0).
    """
    n, s, e, w = (boxes[c] for c in EDGES)
    equal = (((e == n) | (e == s)) & (e != 0)) | (((w == n) | (w == s)) & (w != 0))
    return pd.DataFrame({"w180_e-180": (w == 180) & (e == -180),
                         "lat_beyond_90": (n.abs() > 90) | (s.abs() > 90),
                         "lon_beyond_180": (e.abs() > 180) | (w.abs() > 180),
                         "lon_equals_lat": equal}, index=boxes.index)


def odd_records(groups):
    """
    Return {row name: set of ids} for the rows picked by box numbers: the
    wide single boxes (touching BC as written or not), and the four odd
    marks, each for single-box records (index box) and multi-box records
    (any original box).
    """
    single_ids = groups.index[~groups["multi"] & (groups["group"] != "no_box")]
    single = read_index_boxes(single_ids).dropna()
    wide = (single["west"] <= single["east"]) & (single["east"] - single["west"] > 180)
    touches = groups.loc[single.index, "group"].isin(["inside_bc", "touches_bc_partly"])
    rows = {"wider than 180, single, touching BC as written": set(single.index[wide & touches]),
            "wider than 180, single, not touching BC as written": set(single.index[wide & ~touches])}
    marks = odd_marks(single)
    multi = read_multi_boxes(groups.index[groups["multi"]])
    multi_marks = odd_marks(multi)
    names = {"w180_e-180": "west 180 and east -180", "lat_beyond_90": "latitude beyond 90",
             "lon_beyond_180": "longitude beyond 180", "lon_equals_lat": "longitude equal to latitude"}
    for mark, name in names.items():
        rows[f"{name}, single"] = set(single.index[marks[mark]])
        rows[f"{name}, multi-box (any box)"] = set(multi.loc[multi_marks[mark], "id"])
    return rows


def group_rows(groups):
    """Return {row name: set of ids} for the rows picked by dataone_groups' group and multi-box mark."""
    g, multi = groups["group"], groups["multi"]
    picks = {
        "single box entirely outside BC": (g == "entirely_outside") & ~multi,
        "multi-box, all outside": (g == "entirely_outside") & multi,
        "multi-box, touching BC": g.isin(["touches_bc_all_inside", "touches_bc_partly"]) & multi,
        "multi-box, sent to the LLM": g == "send_to_llm",
        "single box inside BC": g == "inside_bc",
        "single box partly in BC": (g == "touches_bc_partly") & ~multi,
        "west > east": g == "west_gt_east",
        "sign_check": g == "sign_check",
        "no box": g == "no_box",
    }
    return {name: set(groups.index[mask]) for name, mask in picks.items()}


# --- The flow -------------------------------------------------------------------------------

def flow_row(name, ids, groups, union, no_text, final):
    """Return one row of the flow table: the record count at each stage for one set of ids."""
    ids = pd.Index(sorted(ids))
    in_union = union.reindex(ids).dropna(subset=["keep_source"])
    rows = final.reindex(ids).dropna(subset=["route"])
    row = {"group": name, "latest": len(ids), "to_filter2": int(groups.loc[ids, "to_filter2"].sum()),
           "reused_lunaris": int(in_union["reuse_lunaris"].astype(bool).sum()),
           "no_text": len(set(ids) & no_text), "asked_filter2": int((~in_union["reuse_lunaris"].astype(bool)).sum()),
           "kept_filter2": int(in_union["keep_union"].fillna(False).astype(bool).sum()), "in_filter3": len(rows)}
    row.update({f"route_{r}": int((rows["route"] == r).sum()) for r in ROUTES})
    row.update({f"class_{c}": int((rows["final_class"] == c).sum()) for c in CLASSES})
    row.update({f"group_{f}": int((rows["frontend_group"] == f).sum()) for f in FRONTEND})
    return row


def check_flow(table, groups):
    """Return the stages that do not add up: to_filter2 = reused + no text + asked, and the routes, classes and groups sum to in_filter3."""
    problems = []
    for r in table.to_dict("records"):
        if r["to_filter2"] != r["reused_lunaris"] + r["no_text"] + r["asked_filter2"]:
            problems.append(f"{r['group']}: to_filter2 is not reused + no text + asked")
        for prefix in ("route_", "class_", "group_"):
            if sum(v for k, v in r.items() if k.startswith(prefix)) != r["in_filter3"]:
                problems.append(f"{r['group']}: {prefix} columns do not add up to in_filter3")
    return problems


def main():
    """Count every group at every stage, check the sums, print the table and write box_checks_flow.csv."""
    groups = read_groups()
    union = pd.read_parquet(UNION, columns=["id", "keep_source", "reuse_lunaris", "keep_union"]).set_index("id")
    no_text = set(pd.read_csv(NO_TEXT, dtype=str)["id"])
    final = pd.read_parquet(FINAL, columns=["record_id", "route", "final_class", "frontend_group"]).set_index("record_id")
    rows = {**group_rows(groups), **odd_records(groups)}
    table = pd.DataFrame([flow_row(name, ids, groups, union, no_text, final) for name, ids in rows.items()])
    main_rows = table.iloc[:9]
    total = main_rows.drop(columns="group").sum()
    table = pd.concat([table, pd.DataFrame([{"group": "total of the 9 groups (every record once)", **total}])],
                      ignore_index=True)
    print(f"latest records: {len(groups):,}; total of the 9 groups: {int(total['latest']):,}; filter 3 rows: "
          f"{len(final):,}, in the 9 groups: {int(total['in_filter3']):,}")
    problems = check_flow(table, groups)
    print("sums:", "all add up" if not problems else problems)
    print(table.set_index("group").T.to_string())
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    table.to_csv(OUT, index=False, encoding="utf-8-sig")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
