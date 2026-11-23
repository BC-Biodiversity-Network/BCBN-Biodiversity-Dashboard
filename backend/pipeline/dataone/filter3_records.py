"""
filter3_records.py

Build DataONE records in the form the geography prompts' DataONE record
text reads (geo_record_text.dataone_record_text, prompt variant "dataone"
in pipeline/lunaris/llm/geo_prompt_variants.py), and count the site
boilerplate that text leaves out. Read only.

A record is a dict with:
  id, repository     the record id and its DataONE member repository
                     (datasource without "urn:node:", such as PANGAEA)
  title, subjects, abstract   title, keywords and abstract as harvested
  named_location, place_key, site   DataONE's place fields, as harvested
  boxes              every box, as {w, e, s, n}: for a multi-box record
                     (dataone_multibox_groups.parquet) the boxes read from
                     its original document (originals_boxes.parquet), else
                     the one box of DataONE's index (none when an edge is
                     missing or NaN, the no_box group)
  unreadable_boxes   for a multi-box record, how many boxes of its original
                     document had an edge missing or not a number (a flag)
Authors (origin) and organisations (contactOrganization) are not read: the
record text does not show them.
"""

import math
import os
import sys
from collections import Counter

import pandas as pd
import pyarrow.parquet as pq

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(BACKEND, "pipeline", "lunaris"))
sys.path.insert(0, os.path.join(BACKEND, "pipeline", "lunaris", "llm"))
from classify_multibox_records import BOXES, EDGES, HARVEST, INDEX_EDGES, VERSIONS  # noqa: E402
from geo_record_text import boilerplate_of  # noqa: E402

MULTIBOX = os.path.join(HARVEST, "dataone_multibox_groups.parquet")
TEXT_COLUMNS = ["id", "datasource", "title", "abstract", "keywords", "namedLocation", "placeKey", "site"]


def as_list(value):
    """Return a list field as a plain list, a single text as a one-item list, and a missing value as None."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if isinstance(value, str):
        return [value]
    return list(value)


def is_number(value):
    """Return True if a box edge holds a real number (not missing, not NaN)."""
    return value is not None and not (isinstance(value, float) and math.isnan(value))


def read_fields(ids):
    """Return the text, place and index box fields of the given latest records, by id."""
    columns = TEXT_COLUMNS + list(INDEX_EDGES.values()) + ["version_status"]
    table = pq.read_table(VERSIONS, columns=columns, filters=[("id", "in", list(ids))]).to_pandas()
    table = table[table["version_status"] == "latest"].drop_duplicates("id").set_index("id")
    missing = set(ids) - set(table.index)
    if missing:
        sys.exit(f"STOPPED: {len(missing)} records are not latest records in the versions file, for example "
                 f"{sorted(missing)[:3]}")
    return table


def read_multibox(ids):
    """
    Return, for the given records that are multi-box, their boxes from the
    original documents as {id: (boxes, unreadable count)}: boxes with all
    four edges numbers and no flag, in document order; the rest counted.
    """
    multi = set(pd.read_parquet(MULTIBOX, columns=["id"])["id"]) & set(ids)
    if not multi:
        return {}
    boxes = pd.read_parquet(BOXES)
    boxes = boxes[boxes["id"].isin(multi)]
    found = {}
    for record_id, rows in boxes.groupby("id", sort=False):
        usable = rows[rows[EDGES].notna().all(axis=1) & (rows["flag"].fillna("") == "")]
        found[record_id] = ([{"w": r.west, "e": r.east, "s": r.south, "n": r.north} for r in usable.itertuples()],
                            len(rows) - len(usable))
    for record_id in multi - set(found):
        found[record_id] = ([], 0)
    return found


def read_geo_records(ids):
    """Return the given records as geography prompt records (see the module docstring), as {id: record}."""
    fields = read_fields(ids)
    multi = read_multibox(ids)
    records = {}
    for record_id, row in fields.iterrows():
        if record_id in multi:
            boxes, unreadable = multi[record_id]
        else:
            edges = {k: row[INDEX_EDGES[name]] for k, name in (("w", "west"), ("e", "east"), ("s", "south"),
                                                                ("n", "north"))}
            boxes = [{k: float(v) for k, v in edges.items()}] if all(is_number(v) for v in edges.values()) else []
            unreadable = 0
        records[record_id] = {
            "id": record_id, "repository": str(row["datasource"]).replace("urn:node:", ""),
            "title": row["title"], "subjects": as_list(row["keywords"]), "abstract": row["abstract"],
            "named_location": as_list(row["namedLocation"]), "place_key": as_list(row["placeKey"]),
            "site": as_list(row["site"]), "boxes": boxes, "unreadable_boxes": unreadable}
    return records


def site_boilerplate_counts(ids):
    """
    Return, for the given records, how many records each site boilerplate
    pattern removes a value from (geo_record_text.SITE_BOILERPLATE), and how
    many records are left with no site value at all, as (Counter, number).
    """
    fields = read_fields(ids)
    removed, emptied = Counter(), 0
    for site in fields["site"]:
        values = as_list(site) or []
        kinds = {boilerplate_of(v) for v in values} - {None}
        removed.update(kinds)
        if values and all(boilerplate_of(v) for v in values):
            emptied += 1
    return removed, emptied
