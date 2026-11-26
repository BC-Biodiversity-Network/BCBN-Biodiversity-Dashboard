"""
check_dataone_harvest.py

Read-only checks of a finished DataONE harvest (pipeline/dataone/harvest_dataone.py).
Nothing here talks to DataONE: it reads the harvest folder and the BC place
list, prints short tables, and writes them as CSVs.

The checks:
  1. rows in the merged file against numFound at the start, and repeated ids
  2. rows by datasource (top 20) and how many datasources there are
  3. how often each field is filled, for the whole set and the top 10 datasources
  4. records with a box, boxes that are really a point, boxes with west > east
  5. records with more than one geohash_9 value (several boxes in the
     original): how many, by datasource, how many the BC text rules (the
     probe's BC names and bc_places.csv with its Vancouver rule) do not
     already catch, and of those how many have a box centre near BC
  6. records with obsoletes filled, and seriesId values used more than once
  7. how long the harvest took, its requests, download size and disk size
  8. records with a box edge DataONE sent as the text "NaN" (stored as a
     numeric NaN), by datasource, from the merged file and from the
     harvest's nan_box_values.csv

The four box fields hold only the first box of the original record, and
geohash_9 holds the centre of every box (checked 2026-11-18 with
check_dataone_bboxes.py), so check 5 uses the geohash centres to see how
many records might have a BC box that is not their first.

Output: printed tables, and the same tables as CSVs in
~/Desktop/BCBN/Results/dataone_harvest_check_2026-11-18/.

Run, from backend/:
    ../.venv/bin/python exploration/dataone/check_dataone_harvest.py
"""

import argparse
import csv
import json
import os
import re
import sys

import pandas as pd
import pyarrow.compute as pc
import pyarrow.parquet as pq

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DEFAULT_HARVEST = os.path.join(BACKEND, "data", "dataone", "harvest_2026_11_18")
BC_PLACES = os.path.join(BACKEND, "pipeline", "lunaris", "bc_places.csv")
OUT_DIR = os.path.expanduser("~/Desktop/BCBN/Results/dataone_harvest_check_2026-11-18")

# BC's bounding rectangle, as in the probes, and the two widened versions.
BC_N, BC_S, BC_W, BC_E = 60.05, 48.20, -139.10, -114.00
WIDENINGS = {"a_6_degrees": 6.0, "b_15_degrees": 15.0}

# The probe's BC names and its Vancouver rule (Vancouver counts unless it is
# followed by Island, which is listed on its own, or by Washington or WA).
BC_NAMES = ["British Columbia", "Colombie-Britannique"]
VANCOUVER_OK = re.compile(r"\bVancouver\b(?!\s*(?:Island|,?\s*(?:Washington|WA)\b))")

GEOHASH_ALPHABET = "0123456789bcdefghjkmnpqrstuvwxyz"
SECONDS_PER_DOCUMENT = 1.0   # the 1 request per second rule; the real time is a little more


def phrase_re(phrases):
    """Return a whole-word, any-case matcher for a list of phrases (as in the probe)."""
    return re.compile(r"(?<![a-z])(?:" + "|".join(re.escape(p.lower()) for p in phrases) + r")(?![a-z])")


def bc_text_matchers():
    """
    Return (BC name matcher, place name matcher): the probe's BC names, and
    the 'yes' names of bc_places.csv. Vancouver (the 'special' name) is
    matched separately with VANCOUVER_OK.
    """
    places = pd.read_csv(BC_PLACES)
    yes = places.loc[places["use"] == "yes", "name"].tolist()
    return phrase_re(BC_NAMES), phrase_re(yes)


def text_of(title, abstract, keywords):
    """Return the title, abstract and keywords of one record as one string (pandas gives NaN for a missing text)."""
    parts = [t if isinstance(t, str) else "" for t in (title, abstract)]
    if keywords is not None:
        parts.append(" ".join(str(k) for k in keywords))
    return " ".join(parts)


def caught_by_bc_text(text, bc_re, places_re):
    """Return True when the text names BC or a BC place, by the probe's rules."""
    low = text.lower()
    return bool(bc_re.search(low) or places_re.search(low) or VANCOUVER_OK.search(text))


def geohash_centre(code):
    """Return (lat, lon), the centre of a geohash cell."""
    lat, lon, even = [-90.0, 90.0], [-180.0, 180.0], True
    for char in code.strip().lower():
        bits = GEOHASH_ALPHABET.index(char)
        for shift in range(4, -1, -1):
            half = lon if even else lat
            mid = (half[0] + half[1]) / 2
            if (bits >> shift) & 1:
                half[0] = mid
            else:
                half[1] = mid
            even = not even
    return (lat[0] + lat[1]) / 2, (lon[0] + lon[1]) / 2


def near_bc(lat, lon, widen):
    """Return True when a point is inside BC's rectangle widened by `widen` degrees on each side."""
    return (BC_S - widen <= lat <= BC_N + widen) and (BC_W - widen <= lon <= BC_E + widen)


def save(frame, name):
    """Write one table as a CSV in OUT_DIR."""
    frame.to_csv(os.path.join(OUT_DIR, name), index=False, encoding="utf-8-sig")


def filled_mask(column):
    """
    Return a boolean array: True where a column holds something. A list
    counts when it has at least one item; text when it is not empty.
    """
    import pyarrow as pa
    if pa.types.is_list(column.type):
        return pc.fill_null(pc.greater(pc.list_value_length(column), 0), False)
    if pa.types.is_string(column.type):
        return pc.fill_null(pc.not_equal(column, ""), False)
    if pa.types.is_floating(column.type):
        # A box edge DataONE sent as "NaN" is stored as NaN: not filled.
        return pc.fill_null(pc.invert(pc.is_nan(column)), False)
    return pc.is_valid(column)


def check_counts(harvest, merged, progress):
    """Checks 1 and 2: rows against numFound, repeated ids, rows by datasource."""
    ids = pq.read_table(merged, columns=["id", "datasource"])
    rows = ids.num_rows
    unique = len(pc.unique(ids.column("id")))
    found = [p["num_found"] for p in progress["pages"]]
    print("\n1. ROWS")
    table1 = pd.DataFrame([
        {"what": "numFound at the start (page 0)", "value": found[0]},
        {"what": "numFound on the last page", "value": found[-1]},
        {"what": "rows in the merged file", "value": rows},
        {"what": "distinct ids", "value": unique},
        {"what": "repeated ids (rows minus distinct)", "value": rows - unique},
        {"what": "pages", "value": len(progress["pages"])},
    ])
    print(table1.to_string(index=False))
    save(table1, "1_rows.csv")
    by_source = ids.column("datasource").to_pandas().str.replace("urn:node:", "", regex=False).value_counts()
    print(f"\n2. ROWS BY DATASOURCE ({len(by_source)} datasources; top 20)")
    print(by_source.head(20).to_string())
    save(by_source.rename_axis("datasource").reset_index(name="records"), "2_by_datasource.csv")
    return by_source


def check_filled(merged, by_source):
    """Check 3: share of records with each field filled, overall and for the top 10 datasources."""
    top = by_source.head(10).index.tolist()
    source = pq.read_table(merged, columns=["datasource"]).column("datasource").to_pandas() \
        .str.replace("urn:node:", "", regex=False)
    result = {}
    for name in pq.read_schema(merged).names:
        column = pq.read_table(merged, columns=[name]).column(name)
        mask = pd.Series(filled_mask(column).to_numpy(zero_copy_only=False))
        row = {"ALL": mask.mean()}
        grouped = mask.groupby(source).mean()
        for ds in top:
            row[ds] = grouped.get(ds, 0.0)
        result[name] = row
    table = pd.DataFrame(result).T
    print("\n3. FIELDS FILLED (share of records)")
    print((table * 100).round(1).to_string())
    save((table * 100).round(2).rename_axis("field").reset_index(), "3_fields_filled_percent.csv")


def check_boxes(merged):
    """
    Check 4: records with a box, boxes that are a point, boxes with west
    greater than east. pandas reads both a missing edge and a NaN edge as
    NaN, so a box with a NaN edge counts as having only some edges here;
    check 8 counts those apart.
    """
    t = pq.read_table(merged, columns=["northBoundCoord", "southBoundCoord", "eastBoundCoord",
                                       "westBoundCoord"]).to_pandas()
    has = t.notna().all(axis=1)
    b = t[has]
    point = (b["northBoundCoord"] == b["southBoundCoord"]) & (b["eastBoundCoord"] == b["westBoundCoord"])
    partial = t.notna().any(axis=1) & ~has
    table = pd.DataFrame([
        {"what": "records with a box (all four edges)", "value": int(has.sum())},
        {"what": "  of which a point (north = south and east = west)", "value": int(point.sum())},
        {"what": "  of which west greater than east", "value": int((b["westBoundCoord"] > b["eastBoundCoord"]).sum())},
        {"what": "records with only some of the four edges", "value": int(partial.sum())},
        {"what": "records with no box", "value": int((~t.notna().any(axis=1)).sum())},
    ])
    print("\n4. BOXES")
    print(table.to_string(index=False))
    save(table, "4_boxes.csv")


def check_several_boxes(merged):
    """
    Check 5: records with more than one geohash_9 value, by datasource; those
    the BC text rules do not catch; and, of those, how many have a box centre
    near BC (a, b) or anywhere (c), which is how many original documents
    would have to be read to see every box exactly.
    """
    t = pq.read_table(merged, columns=["id", "datasource", "title", "abstract", "keywords", "geohash_9",
                                       "northBoundCoord", "southBoundCoord", "eastBoundCoord",
                                       "westBoundCoord"])
    lengths = pc.fill_null(pc.list_value_length(t.column("geohash_9")), 0)
    several = t.filter(pc.greater(lengths, 1)).to_pandas()
    several["ds"] = several["datasource"].str.replace("urn:node:", "", regex=False)
    several["values"] = several["geohash_9"].map(len)
    bc_re, places_re = bc_text_matchers()
    several["caught_by_text"] = [caught_by_bc_text(text_of(ti, ab, kw), bc_re, places_re)
                                 for ti, ab, kw in zip(several["title"], several["abstract"], several["keywords"])]
    centres = several["geohash_9"].map(lambda codes: [geohash_centre(c) for c in codes])
    for name, widen in WIDENINGS.items():
        several[name] = centres.map(lambda pts, w=widen: any(near_bc(la, lo, w) for la, lo in pts))
    several["c_anywhere"] = True
    # Whether the first box (the one in the index) already touches BC's rectangle.
    n, s = several["northBoundCoord"], several["southBoundCoord"]
    e, w = several["eastBoundCoord"], several["westBoundCoord"]
    several["first_box_touches_bc_rectangle"] = (s <= BC_N) & (n >= BC_S) & (w <= BC_E) & (e >= BC_W)

    print(f"\n5. RECORDS WITH MORE THAN ONE geohash_9 VALUE: {len(several):,}")
    print(f"   values per record: median {several['values'].median():.0f}, max {several['values'].max():,}")
    by_ds = several.groupby("ds").agg(records=("id", "size"), not_caught_by_text=("caught_by_text", lambda x: (~x).sum()))
    by_ds = by_ds.sort_values("records", ascending=False)
    print("   by datasource (top 10):")
    print(by_ds.head(10).to_string())
    save(by_ds.rename_axis("datasource").reset_index(), "5_several_boxes_by_datasource.csv")

    rest = several[~several["caught_by_text"]]
    rows = []
    for name in ["a_6_degrees", "b_15_degrees", "c_anywhere"]:
        chosen = rest[rest[name]]
        count = len(chosen)
        rows.append({"rule": name, "records_not_caught_by_text": len(rest), "with_a_centre_in_reach": count,
                     "of_which_first_box_touches_bc_rectangle": int(chosen["first_box_touches_bc_rectangle"].sum()),
                     "documents_to_download": count,
                     "hours_at_1_per_second": round(count * SECONDS_PER_DOCUMENT / 3600, 2)})
    table = pd.DataFrame(rows)
    print(f"\n   caught by the BC text rules: {int(several['caught_by_text'].sum()):,}; "
          f"not caught: {len(rest):,}")
    print(table.to_string(index=False))
    save(table, "5_documents_to_read.csv")
    by_ds_a = rest[rest["a_6_degrees"]].groupby("ds").size().sort_values(ascending=False)
    print("   rule a by datasource (top 10):")
    print(by_ds_a.head(10).to_string())
    save(by_ds_a.rename_axis("datasource").reset_index(name="records"), "5_rule_a_by_datasource.csv")
    save(rest.loc[rest["a_6_degrees"], ["id", "ds", "title", "values", "first_box_touches_bc_rectangle"]],
         "5_rule_a_records.csv")


def check_versions(merged):
    """Check 6: records with obsoletes filled, and seriesId values used by more than one record."""
    t = pq.read_table(merged, columns=["obsoletes", "seriesId"]).to_pandas()
    obsoletes = t["obsoletes"].fillna("").ne("").sum()
    series = t["seriesId"].dropna()
    series = series[series != ""]
    counts = series.value_counts()
    repeated = counts[counts > 1]
    table = pd.DataFrame([
        {"what": "records with obsoletes filled", "value": int(obsoletes)},
        {"what": "records with a seriesId", "value": int(len(series))},
        {"what": "distinct seriesId values", "value": int(len(counts))},
        {"what": "seriesId values used by more than one record", "value": int(len(repeated))},
        {"what": "records sharing a seriesId with another", "value": int(repeated.sum())},
        {"what": "most records on one seriesId", "value": int(counts.max()) if len(counts) else 0},
    ])
    print("\n6. VERSIONS")
    print(table.to_string(index=False))
    save(table, "6_versions.csv")


def check_nan_edges(harvest, merged):
    """
    Check 8: records with at least one box edge stored as NaN (DataONE sent
    the text "NaN"), by datasource, from the merged file, set against the
    harvest's own list (nan_box_values.csv).
    """
    edges = ["northBoundCoord", "southBoundCoord", "eastBoundCoord", "westBoundCoord"]
    t = pq.read_table(merged, columns=["id", "datasource"] + edges)
    any_nan = None
    per_edge = {}
    for edge in edges:
        mask = pc.fill_null(pc.is_nan(t.column(edge)), False)
        per_edge[edge] = int(pc.sum(mask).as_py() or 0)
        any_nan = mask if any_nan is None else pc.or_(any_nan, mask)
    hit = t.filter(any_nan).select(["id", "datasource"]).to_pandas()
    hit["datasource"] = hit["datasource"].str.replace("urn:node:", "", regex=False)
    listed = pd.read_csv(os.path.join(harvest, "nan_box_values.csv"))
    print(f"\n8. BOX EDGES SENT AS \"NaN\": {len(hit):,} records; NaN values per edge: {per_edge}")
    print(f"   nan_box_values.csv: {len(listed):,} values in {listed['id'].nunique():,} records; "
          f"same records as the merged file: {set(listed['id']) == set(hit['id'])}")
    by_ds = hit["datasource"].value_counts()
    print(by_ds.to_string() if len(by_ds) else "   none")
    save(by_ds.rename_axis("datasource").reset_index(name="records"), "8_nan_edges_by_datasource.csv")
    pattern = listed.groupby("id")["field"].apply(lambda f: "+".join(sorted(f))).value_counts()
    print("   which edges are NaN together:", pattern.to_dict())


def folder_size(path):
    """Return the total size in bytes of every file under a folder."""
    return sum(os.path.getsize(os.path.join(root, f)) for root, _, files in os.walk(path) for f in files)


def check_cost(harvest, merged, progress):
    """Check 7: time, requests, bytes downloaded and disk size, from the log and the files."""
    with open(os.path.join(harvest, "requests_log.csv"), encoding="utf-8") as handle:
        log = list(csv.DictReader(handle))
    started = pd.Timestamp(progress["started"])
    finished = pd.Timestamp(progress.get("finished", log[-1]["when"]))
    table = pd.DataFrame([
        {"what": "requests sent", "value": len(log)},
        {"what": "statuses", "value": str(pd.Series([r["status"] for r in log]).value_counts().to_dict())},
        {"what": "wall time (start to finish)", "value": str(finished - started)},
        {"what": "seconds waiting for answers (sum)", "value": round(sum(float(r["seconds"]) for r in log), 1)},
        {"what": "downloaded (MB, JSON)", "value": round(sum(int(r["bytes"]) for r in log) / 1e6, 1)},
        {"what": "pages folder on disk (MB)", "value": round(folder_size(os.path.join(harvest, "pages")) / 1e6, 1)},
        {"what": "merged file on disk (MB)", "value": round(os.path.getsize(merged) / 1e6, 1)},
        {"what": "whole harvest folder (MB)", "value": round(folder_size(harvest) / 1e6, 1)},
    ])
    print("\n7. COST")
    print(table.to_string(index=False))
    save(table, "7_cost.csv")


def main():
    """Run every check on the harvest folder given (default: the 2026-11-18 harvest)."""
    parser = argparse.ArgumentParser(description="Read-only checks of a DataONE harvest.")
    parser.add_argument("--harvest", default=DEFAULT_HARVEST)
    args = parser.parse_args()
    merged = os.path.join(args.harvest, "dataone_full_harvest.parquet")
    with open(os.path.join(args.harvest, "progress.json"), encoding="utf-8") as handle:
        progress = json.load(handle)
    if not progress.get("complete") or not os.path.exists(merged):
        sys.exit("the harvest is not complete, or the merged file is missing")
    os.makedirs(OUT_DIR, exist_ok=True)
    by_source = check_counts(args.harvest, merged, progress)
    check_filled(merged, by_source)
    check_boxes(merged)
    check_several_boxes(merged)
    check_versions(merged)
    check_cost(args.harvest, merged, progress)
    check_nan_edges(args.harvest, merged)
    return 0


if __name__ == "__main__":
    sys.exit(main())
