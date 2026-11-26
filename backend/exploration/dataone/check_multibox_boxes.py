"""
check_multibox_boxes.py

Read-only checks on the boxes fetch_multibox_documents.py --parse read from
the original documents of the multi-box records (originals_boxes.parquet).
Nothing is written and nothing is downloaded.

  1. Why a document's box count differs from its number of geohash_9 values
     in DataONE's index. For every document the boxes are split into those
     DataONE's index reads (EML: //dataset/coverage/geographicCoverage/
     boundingCoordinates; ISO: identificationInfo/MD_DataIdentification/
     extent/EX_Extent/geographicElement) and those elsewhere in the document,
     and the boxes DataONE seems to skip (an empty edge, a latitude beyond
     +-90) are counted. Duplicate boxes and boxes with west greater than
     east are counted too, to show whether DataONE drops them.
     A second check compares the geohash_9 values themselves with the boxes,
     using what the index seems to do: a box with north = south becomes the
     point (north, west); a box with west greater than east gets its centre
     moved 180 degrees of longitude; any other box gets its centre.
  2. The boxes the parser flagged and the documents it could not read.

Run, from backend/:
    ../.venv/bin/python exploration/dataone/check_multibox_boxes.py
"""

import os
import sys
from collections import Counter

import pandas as pd
import pyarrow.compute as pc
import pyarrow.parquet as pq

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
HARVEST = os.path.join(BACKEND, "data", "dataone", "harvest_2026_11_18")
VERSIONS = os.path.join(HARVEST, "dataone_full_harvest_versions.parquet")
BOXES = os.path.join(HARVEST, "originals_boxes.parquet")
INDEX = os.path.join(HARVEST, "originals", "index.csv")
PARSE_LOG = os.path.join(HARVEST, "originals", "parse_log.csv")
EDGES = ["north", "south", "east", "west"]
TEXTS = [f"{e}_text" for e in EDGES]
GEOHASH_ALPHABET = "0123456789bcdefghjkmnpqrstuvwxyz"
EXAMPLES = 3

# The end of the path (as the parser stores it) of the boxes DataONE's index reads.
INDEX_PATH_END = {"EML": ["dataset", "coverage", "geographicCoverage"],
                  "ISO": ["identificationInfo", "MD_DataIdentification", "extent", "EX_Extent", "geographicElement"]}


def geohash(lat, lon, length=9):
    """Return the geohash of a point, `length` characters long."""
    lat_range, lon_range = [-90.0, 90.0], [-180.0, 180.0]
    code, bits, value, even = "", 0, 0, True
    while len(code) < length:
        span, number = (lon_range, lon) if even else (lat_range, lat)
        middle = (span[0] + span[1]) / 2
        if number >= middle:
            value, span[0] = value * 2 + 1, middle
        else:
            value, span[1] = value * 2, middle
        even, bits = not even, bits + 1
        if bits == 5:
            code, bits, value = code + GEOHASH_ALPHABET[value], 0, 0
    return code


def has_numbers(row):
    """Return True when all four edges of a box are numbers."""
    return all(pd.notna(row[e]) for e in EDGES)


def plain_centre_geohash(row):
    """Return the geohash of a box's centre (None when an edge is missing)."""
    if not has_numbers(row):
        return None
    return geohash((row["north"] + row["south"]) / 2, (row["east"] + row["west"]) / 2)


def dataone_geohash(row):
    """
    Return the geohash DataONE's index seems to give a box: None when an
    edge is missing or a latitude is beyond +-90; the point (north, west)
    when north = south; otherwise the centre, moved 180 degrees of
    longitude when west is greater than east.
    """
    if not has_numbers(row) or abs(row["north"]) > 90 or abs(row["south"]) > 90:
        return None
    if row["north"] == row["south"]:
        return geohash(row["north"], row["west"])
    lon = (row["east"] + row["west"]) / 2
    if row["west"] > row["east"]:
        lon = lon + 180 if lon < 0 else lon - 180
    return geohash((row["north"] + row["south"]) / 2, lon)


def read_inputs():
    """Return the boxes, the multi-box latest records (id -> datasource, formatId, geohash list), the download index and the parse log."""
    boxes = pd.read_parquet(BOXES)
    table = pq.read_table(VERSIONS, columns=["id", "datasource", "formatId", "geohash_9", "version_status"])
    table = table.filter(pc.equal(table.column("version_status"), "latest"))
    records = table.to_pandas()
    records = records[records["geohash_9"].map(lambda v: v is not None and len(v) > 1)].set_index("id")
    records["datasource"] = records["datasource"].str.replace("urn:node:", "", regex=False)
    records["geohash_9"] = records["geohash_9"].map(list)
    return boxes, records, pd.read_csv(INDEX), pd.read_csv(PARSE_LOG)


def mark_boxes(boxes):
    """Add to each box: read by the index, empty edge, latitude beyond +-90, west > east, duplicate, and its two geohashes."""
    boxes = boxes.copy()
    boxes["at_index_path"] = [path.split("/")[-len(INDEX_PATH_END[kind]):] == INDEX_PATH_END[kind]
                              for path, kind in zip(boxes["path"], boxes["format"])]
    numbers = boxes[EDGES].notna().all(axis=1)
    boxes["empty_edge"] = ~numbers
    boxes["bad_latitude"] = numbers & ((boxes["north"].abs() > 90) | (boxes["south"].abs() > 90))
    boxes["west_gt_east"] = numbers & (boxes["west"] > boxes["east"])
    boxes["duplicate"] = boxes.duplicated(["id"] + EDGES + TEXTS)
    boxes["plain_gh"] = [plain_centre_geohash(r) for _, r in boxes[EDGES].iterrows()]
    boxes["dataone_gh"] = [dataone_geohash(r) for _, r in boxes[EDGES].iterrows()]
    return boxes


def per_document(boxes, records):
    """Return one row per document: box counts by kind, geohash_9 count, and whether the geohashes match the boxes."""
    rows = []
    for record_id, group in boxes.groupby("id", sort=False):
        listed = Counter(records.at[record_id, "geohash_9"])
        read = group[group["at_index_path"]]
        skipped = read["empty_edge"] | read["bad_latitude"]
        rows.append({
            "id": record_id, "datasource": records.at[record_id, "datasource"],
            "boxes": len(group), "geohash_9": sum(listed.values()),
            "other_path": int((~group["at_index_path"]).sum()),
            "empty_edge": int(read["empty_edge"].sum()), "bad_latitude": int(read["bad_latitude"].sum()),
            "duplicate_at_index_path": int(read["duplicate"].sum()),
            "west_gt_east_at_index_path": int(read["west_gt_east"].sum()),
            "index_boxes_kept": int((~skipped).sum()),
            "plain_match": Counter(read["plain_gh"].dropna()) == listed,
            "dataone_match": Counter(read["dataone_gh"].dropna()) == listed,
        })
    docs = pd.DataFrame(rows)
    docs["same_count"] = docs["boxes"] == docs["geohash_9"]
    docs["explained"] = docs["index_boxes_kept"] == docs["geohash_9"]
    return docs


def show_examples(title, frame, columns):
    """Print up to EXAMPLES rows of frame under a title."""
    print(f"\n   {title}")
    print(frame[columns].head(EXAMPLES).to_string(index=False))


def check_mismatch(boxes, docs):
    """Check 1: print the reasons the box count differs from the geohash_9 count, with examples."""
    differ = docs[~docs["same_count"]]
    print(f"1. BOX COUNT AND geohash_9 COUNT\n   documents with boxes: {len(docs):,}; same count: "
          f"{int(docs['same_count'].sum()):,}; different: {len(differ):,}")
    reasons = {
        "boxes outside dataset/coverage (index does not read them)": differ["other_path"] > 0,
        "box with an empty edge (index skips it)": differ["empty_edge"] > 0,
        "box with a latitude beyond +-90 (index skips it)": differ["bad_latitude"] > 0,
        "duplicate boxes at the index path": differ["duplicate_at_index_path"] > 0,
        "west > east at the index path": differ["west_gt_east_at_index_path"] > 0,
    }
    table = pd.DataFrame([{"reason": k, "documents": int(m.sum())} for k, m in reasons.items()])
    print(table.to_string(index=False))
    print(f"   explained once the first three are taken out: {int(differ['explained'].sum()):,} of {len(differ):,}")
    with_dup = docs[docs["duplicate_at_index_path"] > 0]
    print(f"   documents with duplicate boxes at the index path: {len(with_dup):,}; their geohash_9 list "
          f"keeps every copy in {int(with_dup['explained'].sum()):,}")
    with_wge = docs[docs["west_gt_east_at_index_path"] > 0]
    print(f"   documents with a west > east box at the index path: {len(with_wge):,}; "
          f"every such box still has a geohash_9 value in {int(with_wge['explained'].sum()):,}")

    other = boxes[~boxes["at_index_path"]]
    index_keys = set(map(tuple, boxes.loc[boxes["at_index_path"], ["id"] + EDGES].astype(str).values))
    repeats = other[["id"] + EDGES].astype(str).apply(tuple, axis=1).isin(index_keys)
    by_path = pd.DataFrame({"boxes": other.groupby("path").size(), "documents": other.groupby("path")["id"].nunique(),
                            "same_as_an_index_box": repeats.groupby(other["path"]).sum()})
    print("\n   boxes outside the index path, by the path the parser stored (5 levels up):")
    print(by_path.sort_values("boxes", ascending=False).to_string())

    cols = ["id", "datasource", "boxes", "geohash_9"]
    show_examples("examples: boxes outside dataset/coverage", differ[differ["other_path"] > 0].assign(
        paths=lambda f: f["id"].map(lambda i: "; ".join(sorted(set(other.loc[other["id"] == i, "path"]))))),
        cols + ["other_path", "paths"])
    show_examples("examples: empty edge", differ[differ["empty_edge"] > 0], cols + ["empty_edge"])
    show_examples("examples: latitude beyond +-90", differ[differ["bad_latitude"] > 0], cols + ["bad_latitude"])
    bad = boxes[boxes["bad_latitude"]]
    if len(bad):
        print(bad[["id"] + EDGES].to_string(index=False))

    same = docs[docs["same_count"]]
    print("\n   do the geohash_9 values match the boxes? (all documents with boxes)")
    print(pd.DataFrame([
        {"test": "plain box centres", "documents_matching": int(docs["plain_match"].sum())},
        {"test": "DataONE's way (north = south -> (north, west); west > east -> centre moved 180)",
         "documents_matching": int(docs["dataone_match"].sum())},
        {"test": "same count but values differ from plain centres", "documents_matching": int((~same["plain_match"]).sum())},
        {"test": "not matched either way", "documents_matching": int((~docs["dataone_match"]).sum())},
    ]).to_string(index=False))
    show_examples("examples: not matched either way", docs[~docs["dataone_match"]], cols)


def check_flagged(boxes, records, index, parse_log):
    """Check 2: print the flagged boxes and the documents the parser skipped."""
    flagged = boxes[boxes["flag"] != ""].copy()
    flagged["datasource"] = flagged["id"].map(records["datasource"])
    flagged["formatId"] = flagged["id"].map(records["formatId"])
    print(f"\n2. FLAGGED BOXES: {len(flagged)}")
    print(flagged[["id", "datasource", "formatId", "box"] + TEXTS + ["flag", "path"]].to_string(index=False))
    skipped = parse_log.merge(index[["id", "status", "bytes"]], on="id", how="left")
    skipped["datasource"] = skipped["id"].map(records["datasource"])
    print(f"\n   documents skipped by the parser: {len(skipped)}")
    print(skipped[["id", "datasource", "formatId", "status", "bytes", "problem"]].to_string(index=False))


def main():
    """Run checks 1 and 2 and print the tables."""
    pd.set_option("display.width", 250)
    pd.set_option("display.max_colwidth", 90)
    boxes, records, index, parse_log = read_inputs()
    boxes = mark_boxes(boxes)
    docs = per_document(boxes, records)
    check_mismatch(boxes, docs)
    check_flagged(boxes, records, index, parse_log)
    return 0


if __name__ == "__main__":
    sys.exit(main())
