"""
export_lunaris_datasets.py

Writes backend/data/api/lunaris_datasets.parquet, the Lunaris datasets the
website's Datasets tab shows: one row per copy group whose best front end
group is bc_data or pending (the "dropped" groups are left out). Reads the
filter 3 union table, the 2026-10-20 harvest (parsed and raw XML), the pass
1 place lookups of both Fir runs, the two filter 2 runs and BC's outline
(for the BC rectangle flag). Reads only; writes the one file and stops if
it already exists, unless --replace.

Which copy is shown. Copies of one dataset (same trailing UUID, or the
same title and abstract; see build_label_sample.dataset_keys) form a copy
group. The group's front end group is the best one any copy has (bc_data,
then pending), as in filter 3. Among the copies in that front end group,
the most recent version is shown: the latest DataCite Issued date, then the
latest OAI datestamp, then the lowest record id. The harvest has no version
number and no modified date, so these are the only dates there are.

The columns:
  id, title, abstract, subjects, publisher, places
                       as harvested (places: DataCite geoLocationPlace)
  issued               the DataCite Issued date (YYYY-MM-DD)
  source, source_name  the catalogue id and its readable name
                       (lunaris_catalogue_names.csv)
  link, link_type      https://doi.org/<DOI> ("doi") when the record's
                       identifier is a DOI, else its URL identifier
                       ("url"), the landing page on the original site
  licences             every DataCite rightsList entry, name and URI exactly
                       as harvested (None when missing). Most records also
                       have an entry with no name whose URI is an access
                       right (info:eu-repo/semantics/openAccess or
                       restrictedAccess) rather than a licence
  final_class, frontend_group
                       from filter 3, for the copy shown
  location_type        box, point, looked_up_place or none: the first that
                       applies, in that order; looked_up_place needs a place
                       that can be drawn (see drawable_place)
  location_source      "harvested" for box and point, "our_geonames_lookup"
                       for looked_up_place, empty for none
  boxes, points        as harvested
  looked_up_places     every place name pass 1 found, with the GeoNames
                       candidate we use for it (see pick_candidate): its
                       coordinates, name, feature class and code, kind,
                       whether it is a large area (see LARGE_AREA_CODES),
                       and the lookup's own top candidate, kept for
                       checking; empty for records decided by step 1 rules
                       (no pass 1)
  box_swapped, box_zero_width, box_most_of_world, box_matches_bc_rectangle,
  box_matches_canada_extent
                       what the record's boxes are like (see box_flags);
                       true when any of its boxes has the flag. The boxes
                       themselves are kept as harvested
  mappable             true when the site's map can draw something for the
                       dataset (see is_mappable): a box that is drawn, a
                       point, or a looked-up place that is drawn
  box_shared_by        for each box, in the order of boxes, how many
                       records of the same catalogue in the whole harvest
                       have exactly the same box (this record included)
  organisms_4b, organisms_35b, organism_level_4b, organism_level_35b
                       filter 2 answers of each run (empty if it failed)
  organisms, organism_level
                       both runs together (see combined_level)
  copy_count, other_copies
                       the group size, and the other copies' id, catalogue
                       and link

Run, from backend/:
    python pipeline/lunaris/export_lunaris_datasets.py --bc-boundary <path to bc_boundary_gbif.wkt>
"""

import argparse
import json
import os
import sys
import xml.etree.ElementTree as ET

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from bc_filter_rules import is_bc_rectangle, load_bc_shape
from check_harvest import source_of

HERE = os.path.dirname(os.path.abspath(__file__))

DEFAULT_UNION = "data/lunaris_bc_filter/filter3_final_union/filter3_merged.csv"
DEFAULT_HARVEST = "exploration/lunaris/harvest_2026_10_20/lunaris_full_harvest.parquet"
DEFAULT_RAW_XML = "exploration/lunaris/harvest_2026_10_20/lunaris_raw_xml.parquet"
DEFAULT_RUNS = ["data/lunaris_bc_filter/fir_runs/needs_llm_qwen3.6-35b-a3b_nofa",
                "data/lunaris_bc_filter/fir_runs/needs_llm_new35b_qwen3.6-35b-a3b_nofa"]
DEFAULT_ANSWERS_4B = "data/lunaris_llm_runs/harvest_2026_10_20/trial_qwen3.5-4b-fir_v6_harvest_2026_10_20.csv"
DEFAULT_ANSWERS_35B = ("data/lunaris_llm_runs/fir_harvest_2026_10_20_qwen3.6-35b-a3b_nofa/"
                       "trial_qwen3.6-35b-a3b-fir-nofa_v6_harvest_2026_10_20_qwen3.6-35b-a3b_nofa.csv")
DEFAULT_OUT = "data/api/lunaris_datasets.parquet"
NAMES_CSV = os.path.join(HERE, "lunaris_catalogue_names.csv")

# Front end groups, best first; only groups whose best is one of the first
# two are exported.
GROUPS = ["bc_data", "pending", "dropped"]
EXPORTED = ["bc_data", "pending"]
LEVELS = ["species", "group", "none"]
DATACITE = {"d": "http://datacite.org/schema/kernel-4"}
# A box covering at least this share of the whole longitude by latitude
# range counts as covering most of the world.
MOST_OF_WORLD = 0.5
# The Canada-wide extent we asked Lunaris about (west, east, south, north).
# A box with every edge within CANADA_EXTENT_MARGIN degrees of it is
# flagged. On the 2026-10-20 harvest any margin from 0.05 to 0.5 degrees
# finds the same boxes; the next nearest are rounded boxes such as w -140,
# e -50, s 42, n 82, which are left unflagged.
CANADA_EXTENT_BOX = (-141.0, -52.6, 41.7, 83.1)
CANADA_EXTENT_MARGIN = 0.5
# The GeoNames feature codes, by feature class, of places too large to
# draw as a point: countries, provinces and states (A); continents and
# regions, such as Pacific Northwest or Kootenay (L); oceans, seas and gulfs
# (H); and mountain ranges (T). They are listed in the text, not drawn.
LARGE_AREA_CODES = {
    "A": {"TERR", "ADM1", "ADM1H"},
    "L": {"CONT", "RGN", "RGNE", "AREA"},
    "H": {"OCN", "SEA", "GULF"},
    "T": {"MTS"},
}
# Every country code (PCL, PCLI, PCLD, PCLF, PCLIX, PCLS, PCLH) starts so.
COUNTRY_CODE_PREFIX = "PCL"
# DataCite rights URIs that give an access right (open, restricted and so
# on), not a licence; used only for the printed licence counts.
ACCESS_RIGHTS_PREFIX = "info:eu-repo/semantics/"

BOX = pa.struct([("w", pa.float64()), ("e", pa.float64()), ("s", pa.float64()), ("n", pa.float64())])
POINT = pa.struct([("lat", pa.float64()), ("lon", pa.float64())])
CANDIDATE_FIELDS = [
    ("geonames_id", pa.int64()), ("geonames_name", pa.string()), ("feature_class", pa.string()),
    ("feature_code", pa.string()), ("country_code", pa.string()), ("admin1_name", pa.string()),
    ("latitude", pa.float64()), ("longitude", pa.float64()), ("kind", pa.string()),
]
PLACE = pa.struct([
    ("name", pa.string()), ("as_written", pa.string()), ("role", pa.string()),
    ("looked_up", pa.string()), ("category", pa.string()),
    *CANDIDATE_FIELDS,
    ("any_candidate_in_bc", pa.bool_()), ("choice", pa.string()), ("large_area", pa.bool_()),
    ("top_candidate", pa.struct(CANDIDATE_FIELDS)),
])
LICENCE = pa.struct([("name", pa.string()), ("uri", pa.string())])
COPY = pa.struct([("id", pa.string()), ("source", pa.string()), ("source_name", pa.string()),
                  ("link", pa.string())])
SCHEMA = pa.schema([
    ("id", pa.string()), ("title", pa.string()), ("abstract", pa.string()),
    ("subjects", pa.list_(pa.string())), ("publisher", pa.string()), ("places", pa.list_(pa.string())),
    ("issued", pa.string()), ("source", pa.string()), ("source_name", pa.string()),
    ("link", pa.string()), ("link_type", pa.string()), ("licences", pa.list_(LICENCE)),
    ("final_class", pa.string()), ("frontend_group", pa.string()),
    ("location_type", pa.string()), ("location_source", pa.string()),
    ("boxes", pa.list_(BOX)), ("points", pa.list_(POINT)), ("looked_up_places", pa.list_(PLACE)),
    ("box_swapped", pa.bool_()), ("box_zero_width", pa.bool_()), ("box_most_of_world", pa.bool_()),
    ("box_matches_bc_rectangle", pa.bool_()), ("box_matches_canada_extent", pa.bool_()),
    ("box_shared_by", pa.list_(pa.int64())), ("mappable", pa.bool_()),
    ("organisms_4b", pa.list_(pa.string())), ("organisms_35b", pa.list_(pa.string())),
    ("organism_level_4b", pa.string()), ("organism_level_35b", pa.string()),
    ("organisms", pa.list_(pa.string())), ("organism_level", pa.string()),
    ("copy_count", pa.int64()), ("other_copies", pa.list_(COPY)),
])


# --- Reading ---------------------------------------------------------------

def read_union(path):
    """Return the filter 3 union table, every value as text, with a group_key column (the copy group, or the id)."""
    table = pd.read_csv(path, dtype=str, keep_default_na=False)
    if table["record_id"].duplicated().any():
        raise SystemExit(f"ERROR: {path} holds a record id twice")
    table["group_key"] = table["dup_group_id"].where(table["dup_group_id"] != "", table["record_id"])
    return table


def read_catalogue_names(path=NAMES_CSV):
    """Return {source: readable name} from lunaris_catalogue_names.csv."""
    names = pd.read_csv(path, dtype=str, keep_default_na=False)
    if names["source"].duplicated().any():
        raise SystemExit(f"ERROR: {path} names a source twice")
    return dict(zip(names["source"], names["name"]))


def datacite_facts(xml):
    """
    Return (Issued date, identifier, identifier type, licences) from one
    record's raw OAI XML; empty text and no licences when missing. The
    licences are the rightsList entries in order, each {"name", "uri"}
    exactly as harvested, None for a part that is missing or empty.
    """
    resource = ET.fromstring(xml).find(".//d:resource", DATACITE)
    if resource is None:
        return "", "", "", []
    issued = resource.find("d:dates/d:date[@dateType='Issued']", DATACITE)
    identifier = resource.find("d:identifier", DATACITE)
    licences = [{"name": rights.text or None, "uri": rights.get("rightsURI") or None}
                for rights in resource.findall("d:rightsList/d:rights", DATACITE)]
    return ((issued.text or "").strip() if issued is not None else "",
            (identifier.text or "").strip() if identifier is not None else "",
            identifier.get("identifierType", "") if identifier is not None else "",
            licences)


def read_datacite(path, ids):
    """Return a table by record id of issued, identifier, identifier_type, licences and datestamp, for the ids given."""
    raw = pd.read_parquet(path, columns=["id", "datestamp", "raw_xml"], filters=[("id", "in", sorted(ids))])
    raw = raw.drop_duplicates("id").set_index("id")
    facts = pd.DataFrame([datacite_facts(xml) for xml in raw["raw_xml"]], index=raw.index,
                         columns=["issued", "identifier", "identifier_type", "licences"])
    facts["datestamp"] = raw["datestamp"].fillna("")
    missing = set(ids) - set(facts.index)
    if missing:
        raise SystemExit(f"ERROR: {len(missing)} records are not in {path}, for example {sorted(missing)[0]}")
    return facts


def read_harvest(path, ids):
    """Return the parsed harvest's text and location fields by record id, for the ids given."""
    columns = ["id", "title", "abstract", "subjects", "publisher", "places", "points", "boxes"]
    harvest = pd.read_parquet(path, columns=columns, filters=[("id", "in", sorted(ids))])
    return harvest.drop_duplicates("id").set_index("id")


def read_filter2(path):
    """Return a filter 2 run's answers by record id: organisms and organism_level, empty for a failed record."""
    answers = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    if answers["record_id"].duplicated().any():
        raise SystemExit(f"ERROR: {path} holds a record id twice")
    failed = answers["model_error"] != ""
    answers.loc[failed, ["organisms", "organism_level"]] = ""
    return answers.set_index("record_id")[["organisms", "organism_level"]]


def box_key(source, box):
    """Return the key of one box of a record from a catalogue: the catalogue and the box's edges exactly as harvested."""
    return (source, box["w"], box["e"], box["s"], box["n"])


def read_box_counts(path):
    """
    Return {box_key: number of records} over the whole harvest: for each
    catalogue and exact box, how many records have that box (a record with
    the same box twice counts once).
    """
    harvest = pd.read_parquet(path, columns=["id", "boxes"]).drop_duplicates("id")
    counts = {}
    for record_id, boxes in zip(harvest["id"], harvest["boxes"]):
        source = source_of(record_id)
        for key in {box_key(source, box) for box in as_list(boxes)}:
            counts[key] = counts.get(key, 0) + 1
    return counts


def read_pass1_places(run_dirs):
    """
    Return {record id: list of places} from the pass 1 answers and GeoNames
    lookups of the runs given. Each lookup line belongs to one place, in the
    order pass 1 gave them; this stops if the two files do not line up.
    """
    found = {}
    for run in run_dirs:
        places, lookups = {}, {}
        with open(os.path.join(run, "pass1", "answers.jsonl"), encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                places[row["number"]] = (row["record_id"], row.get("places") or [])
        with open(os.path.join(run, "pass1", "lookup.jsonl"), encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                lookups.setdefault(row["record"], []).append(row)
        for number, (record_id, record_places) in places.items():
            record_lookups = lookups.get(number, [])
            if [p["name"] for p in record_places] != [r["query"]["name"] for r in record_lookups]:
                raise SystemExit(f"ERROR: pass 1 places and lookups do not line up for {record_id} in {run}")
            found[record_id] = [place_from_lookup(p, r) for p, r in zip(record_places, record_lookups)]
    return found


# --- One record ------------------------------------------------------------

def link_of(identifier, identifier_type):
    """Return (link, link_type): a DOI as https://doi.org/<DOI>, a URL as it is, else empty."""
    if identifier_type == "DOI" and identifier:
        return "https://doi.org/" + identifier, "doi"
    if identifier.startswith(("http://", "https://")):
        return identifier, "url"
    return "", ""


def is_canada_extent(box):
    """
    Return True if a box is the Canada-wide extent (CANADA_EXTENT_BOX):
    each edge within CANADA_EXTENT_MARGIN degrees. Swapped edges are put
    back first, as in step 1's is_bc_rectangle; a box that crosses the 180
    degree line never matches.
    """
    west, east = box["w"], box["e"]
    south, north = sorted((box["s"], box["n"]))
    if west > east:
        if west > 0 > east:
            return False
        west, east = east, west
    return all(abs(edge - extent) <= CANADA_EXTENT_MARGIN
               for edge, extent in zip((west, east, south, north), CANADA_EXTENT_BOX))


def box_flags(box, shape):
    """
    Return the set of flags for one harvested box (a dict with w, e, s, n),
    with shape BC's outline (a BcShape, or anything with its rectangle):
      swapped         south greater than north, or west greater than east
                      when that is not a box crossing the 180 degree line
                      (west in the eastern half, east in the western half)
      zero_width      west equal to east, or south equal to north
      most_of_world   the box covers at least MOST_OF_WORLD of the whole
                      longitude by latitude range
      matches_bc_rectangle   BC's own bounding rectangle, as step 1 decides
                             it (is_bc_rectangle, within BC_RECTANGLE_MARGIN
                             degrees)
      matches_canada_extent  the Canada-wide extent (is_canada_extent)
    """
    flags = set()
    west, east, south, north = box["w"], box["e"], box["s"], box["n"]
    crosses_180 = west > 0 > east
    if south > north or (west > east and not crosses_180):
        flags.add("swapped")
    if west == east or south == north:
        flags.add("zero_width")
    width = (east - west) % 360 if crosses_180 else abs(east - west)
    if (width / 360) * (abs(north - south) / 180) >= MOST_OF_WORLD:
        flags.add("most_of_world")
    if is_bc_rectangle(box, shape):
        flags.add("matches_bc_rectangle")
    if is_canada_extent(box):
        flags.add("matches_canada_extent")
    return flags


def is_large_area(feature_class, feature_code):
    """Return True for a GeoNames place too large to draw as a point (LARGE_AREA_CODES, or any country code)."""
    if feature_class == "A" and (feature_code or "").startswith(COUNTRY_CODE_PREFIX):
        return True
    return (feature_code or "") in LARGE_AREA_CODES.get(feature_class, set())


def candidate_fields(candidate, kind):
    """Return one GeoNames candidate as the fields kept for it (all None for no candidate)."""
    return {
        "geonames_id": candidate.get("geonameid"), "geonames_name": candidate.get("main_name"),
        "feature_class": candidate.get("feature_class"), "feature_code": candidate.get("feature_code"),
        "country_code": candidate.get("country_code"), "admin1_name": candidate.get("admin1_name"),
        "latitude": candidate.get("latitude"), "longitude": candidate.get("longitude"), "kind": kind,
    }


def pick_candidate(with_coordinates):
    """
    Return (candidate, kind, choice) for one place name from its candidates
    with coordinates, in the lookup's own ranking: the best-ranked candidate
    in BC (kind in_bc) if there is one, else the top candidate. choice is
    "top" when that is the lookup's top candidate, "best_in_bc" when a BC
    candidate was picked over a top candidate outside BC, and "" when there
    is no candidate.
    """
    if not with_coordinates:
        return {}, None, ""
    in_bc = [pair for pair in with_coordinates if pair[1] == "in_bc"]
    candidate, kind = in_bc[0] if in_bc else with_coordinates[0]
    return candidate, kind, "top" if candidate is with_coordinates[0][0] else "best_in_bc"


def place_from_lookup(place, lookup):
    """
    Return one pass 1 place with the GeoNames candidate we use for it (see
    pick_candidate), or empty coordinates when no candidate has any; whether
    that candidate is a large area; and the lookup's own top candidate in
    top_candidate, so the choice can be checked. any_candidate_in_bc says
    whether any candidate for the name lies in BC.
    """
    candidates = lookup["candidates"]
    kinds = lookup["summary"].get("candidate_kinds", [])
    with_coordinates = [(c, k) for c, k in zip(candidates, kinds)
                        if c.get("latitude") is not None and c.get("longitude") is not None]
    candidate, kind, choice = pick_candidate(with_coordinates)
    top, top_kind = with_coordinates[0] if with_coordinates else ({}, None)
    return {
        "name": place.get("name"), "as_written": place.get("as_written"), "role": place.get("role"),
        "looked_up": lookup["summary"].get("looked_up"), "category": lookup["summary"].get("category"),
        **candidate_fields(candidate, kind),
        "any_candidate_in_bc": any(k == "in_bc" for _, k in with_coordinates),
        "choice": choice,
        "large_area": bool(candidate) and is_large_area(candidate.get("feature_class"), candidate.get("feature_code")),
        "top_candidate": candidate_fields(top, top_kind),
    }


def drawable_place(place):
    """Return True for a looked-up place the map draws: it has coordinates, is not a large area and is not an author's institution."""
    return place["latitude"] is not None and not place["large_area"] and place["role"] != "affiliation"


# Boxes with these flags are not drawn on the map (boxes that match BC's
# rectangle or the Canada-wide extent are drawn, dashed).
NOT_DRAWN_FLAGS = {"swapped", "zero_width", "most_of_world"}


def is_mappable(boxes, points, looked_up_places, shape):
    """
    Return True if the site's map can draw something for a dataset: a box
    without a NOT_DRAWN_FLAGS flag, a point, or a looked-up place that can be
    drawn (drawable_place). The same rules as describeLocation in the front
    end's datasetText.js.
    """
    return (any(not (box_flags(box, shape) & NOT_DRAWN_FLAGS) for box in boxes) or bool(points)
            or any(drawable_place(place) for place in looked_up_places))


def location_type(boxes, points, looked_up_places):
    """Return (location_type, location_source): box, then point, then a looked-up place that can be drawn, then none."""
    if boxes:
        return "box", "harvested"
    if points:
        return "point", "harvested"
    if any(drawable_place(p) for p in looked_up_places):
        return "looked_up_place", "our_geonames_lookup"
    return "none", ""


def organism_list(text):
    """Return the organisms answer ("a; b; c") as a list, without empty names."""
    return [name.strip() for name in str(text).split(";") if name.strip()]


def combined_level(level_4b, level_35b):
    """Return species if either run says species, else group if either says group, else none; empty if neither answered."""
    levels = {level_4b, level_35b} - {""}
    for level in LEVELS:
        if level in levels:
            return level
    return ""


def merged_names(first, second):
    """Return the names of both lists, first list first, each name once (ignoring letter case)."""
    seen, names = set(), []
    for name in first + second:
        if name.lower() not in seen:
            seen.add(name.lower())
            names.append(name)
    return names


def as_list(value):
    """Return a harvested list field (an array, a list or None) as a plain list."""
    return [] if value is None else list(value)


def as_text(value):
    """Return a harvested text field as text, or None when it is missing (pandas reads missing text as NaN)."""
    return None if value is None or (isinstance(value, float) and pd.isna(value)) else str(value)


# --- The whole table -------------------------------------------------------

def pick_shown(members, facts):
    """
    Return the record id to show for one copy group (a DataFrame of its
    rows): among the copies in the group's best front end group, the
    latest Issued date, then the latest OAI datestamp, then the lowest id.
    """
    rank = members["frontend_group"].map(GROUPS.index)
    best = members[rank == rank.min()]
    order = pd.DataFrame({"record_id": best["record_id"].values,
                          "issued": best["record_id"].map(facts["issued"]).values,
                          "datestamp": best["record_id"].map(facts["datestamp"]).values})
    order = order.sort_values(["issued", "datestamp", "record_id"], ascending=[False, False, True])
    return order["record_id"].iloc[0]


def exported_groups(table):
    """Return the rows of every copy group whose best front end group is bc_data or pending."""
    best = table.groupby("group_key")["frontend_group"].agg(lambda g: min(g, key=GROUPS.index))
    keys = best[best.isin(EXPORTED)].index
    return table[table["group_key"].isin(keys)]


def build_rows(members, facts, harvest, places, answers_4b, answers_35b, names, shape, box_counts=None):
    """
    Return the export as a list of dicts, one per copy group, in the order
    of SCHEMA. shape is BC's outline, for box_flags; box_counts is
    read_box_counts' answer, for box_shared_by (None counts each box once).
    """
    missing = sorted(set(members["source"]) - set(names))
    if missing:
        raise SystemExit(f"ERROR: no readable name in {NAMES_CSV} for: {', '.join(missing)}")
    links = {rid: link_of(facts.at[rid, "identifier"], facts.at[rid, "identifier_type"]) for rid in members["record_id"]}
    rows = []
    for _, group in members.groupby("group_key", sort=True):
        shown_id = pick_shown(group, facts)
        row = group.set_index("record_id").loc[shown_id]
        record = harvest.loc[shown_id]
        boxes = [dict(b) for b in as_list(record["boxes"])]
        points = [dict(p) for p in as_list(record["points"])]
        looked_up = places.get(shown_id, [])
        flags = set().union(*(box_flags(b, shape) for b in boxes)) if boxes else set()
        kind, source_of_location = location_type(boxes, points, looked_up)
        organisms = {}
        for name, answers in (("4b", answers_4b), ("35b", answers_35b)):
            answered = shown_id in answers.index
            organisms[name] = (organism_list(answers.at[shown_id, "organisms"]) if answered else [],
                               answers.at[shown_id, "organism_level"] if answered else "")
        link, link_type = links[shown_id]
        rows.append({
            "id": shown_id, "title": as_text(record["title"]), "abstract": as_text(record["abstract"]),
            "subjects": as_list(record["subjects"]), "publisher": as_text(record["publisher"]),
            "places": as_list(record["places"]), "issued": facts.at[shown_id, "issued"],
            "source": row["source"], "source_name": names[row["source"]],
            "link": link, "link_type": link_type, "licences": facts.at[shown_id, "licences"],
            "final_class": row["final_class"], "frontend_group": row["frontend_group"],
            "location_type": kind, "location_source": source_of_location,
            "boxes": boxes, "points": points, "looked_up_places": looked_up,
            "box_swapped": "swapped" in flags, "box_zero_width": "zero_width" in flags,
            "box_most_of_world": "most_of_world" in flags,
            "box_matches_bc_rectangle": "matches_bc_rectangle" in flags,
            "box_matches_canada_extent": "matches_canada_extent" in flags,
            "box_shared_by": [(box_counts or {}).get(box_key(row["source"], b), 1) for b in boxes],
            "mappable": is_mappable(boxes, points, looked_up, shape),
            "organisms_4b": organisms["4b"][0], "organisms_35b": organisms["35b"][0],
            "organism_level_4b": organisms["4b"][1], "organism_level_35b": organisms["35b"][1],
            "organisms": merged_names(organisms["4b"][0], organisms["35b"][0]),
            "organism_level": combined_level(organisms["4b"][1], organisms["35b"][1]),
            "copy_count": len(group),
            "other_copies": [{"id": rid, "source": src, "source_name": names[src], "link": links[rid][0]}
                             for rid, src in sorted(zip(group["record_id"], group["source"])) if rid != shown_id],
        })
    return rows


def changed_from_dup_show(members, rows):
    """Return how many groups now show another copy than filter 3's dup_show rule."""
    old = set(members.loc[members["dup_show"] == "True", "record_id"])
    return sum(row["id"] not in old for row in rows)


def only_bc_or_canada_boxes(row):
    """Return True if a row has boxes, every one BC's rectangle or the Canada-wide extent, and no point."""
    return (bool(row["boxes"]) and not row["points"]
            and all(is_bc_rectangle(b, row["shape"]) or is_canada_extent(b) for b in row["boxes"]))


def licence_label(licence):
    """Return a licence entry as one short line for the printed counts: its name (cut to 60 characters) or its URI."""
    name = " ".join((licence["name"] or "").split())
    if name:
        return (name[:57] + "...") if len(name) > 60 else name
    return licence["uri"] or "(empty entry)"


def is_access_right(licence):
    """Return True for a rights entry with no name whose URI is an access right (ACCESS_RIGHTS_PREFIX), not a licence."""
    return not (licence["name"] or "").strip() and (licence["uri"] or "").startswith(ACCESS_RIGHTS_PREFIX)


def print_counts(frame, changed, shape):
    """
    Print the counts asked for: rows per front end group and class, per
    location type, per link type; the box flags, with each exact box that
    matches BC's rectangle or the Canada-wide extent by catalogue (how many
    shown datasets have it, and how many records of the catalogue in the
    whole harvest); and the most common licences.
    """
    print(f"{len(frame):,} rows (one per copy group); {changed} groups show a more recent copy than dup_show")
    print("\nrows per frontend_group and final_class:")
    print(pd.crosstab(frame["final_class"], frame["frontend_group"], margins=True, margins_name="total").to_string())
    print("\nrows per location_type:")
    print(pd.crosstab(frame["location_type"], frame["frontend_group"], margins=True, margins_name="total").to_string())
    places = pd.DataFrame([place for row in frame["looked_up_places"] for place in row])
    if not places.empty:
        print("\nlooked-up places (all records): by choice, large areas, authors' institutions")
        print("  choice:", places["choice"].value_counts().to_dict())
        print("  large areas:", int(places["large_area"].sum()), places.loc[places["large_area"], "feature_code"]
              .value_counts().to_dict())
        print("  authors' institutions:", int((places["role"] == "affiliation").sum()),
              "; drawable:", int(places.apply(drawable_place, axis=1).sum()))
    print("\nrows the map can draw (mappable):")
    print(pd.crosstab(frame["mappable"], frame["frontend_group"], margins=True, margins_name="total").to_string())
    print("\nrows per link_type:")
    print(pd.crosstab(frame["link_type"], frame["frontend_group"], margins=True, margins_name="total").to_string())
    flag_names = ["swapped", "zero_width", "most_of_world", "matches_bc_rectangle", "matches_canada_extent"]
    print("\nboxes flagged:", {flag: int(frame[f"box_{flag}"].sum()) for flag in flag_names})
    shared = []
    for _, row in frame.iterrows():
        for box, count in zip(row["boxes"], row["box_shared_by"]):
            for flag in {"matches_bc_rectangle", "matches_canada_extent"} & box_flags(box, shape):
                shared.append({"flag": flag, "catalogue": row["source_name"],
                               "box (w, e, s, n)": f"{box['w']:g}, {box['e']:g}, {box['s']:g}, {box['n']:g}",
                               "harvest_records": count})
    if shared:
        shared = pd.DataFrame(shared)
        table = (shared.groupby(["flag", "catalogue", "box (w, e, s, n)"])
                 .agg(shown_datasets=("harvest_records", "size"), harvest_records=("harvest_records", "first"))
                 .reset_index().sort_values(["flag", "shown_datasets"], ascending=[True, False]))
        print("\nexact boxes matching BC's rectangle or the Canada-wide extent, by catalogue:")
        print(table.to_string(index=False))
    only = frame.assign(shape=[shape] * len(frame)).apply(only_bc_or_canada_boxes, axis=1)
    print(f"\nrows whose only location is boxes matching BC or Canada (no other box, no point): {int(only.sum())}")
    print(pd.crosstab(frame.loc[only, "source_name"], frame.loc[only, "frontend_group"], margins=True,
                      margins_name="total").sort_values("total", ascending=False).head(6).to_string())

    licences = frame["licences"].map(lambda entries: [e for e in entries if not is_access_right(e)])
    print(f"\nrows with no licence (no rights entry, or only an access right): {int((licences.map(len) == 0).sum())}")
    print("rights entries that are access rights:",
          pd.Series([e["uri"] for entries in frame["licences"] for e in entries if is_access_right(e)])
          .value_counts().to_dict())
    print("most common licences (rows with each):")
    labels = licences.map(lambda entries: sorted({licence_label(e) for e in entries}))
    print(labels.explode().dropna().value_counts().head(10).to_string())
    print("\norganism_level:", frame["organism_level"].value_counts().to_dict())


def main():
    """Read everything, build the export, write the parquet file and print the counts."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("--union", default=DEFAULT_UNION)
    parser.add_argument("--harvest", default=DEFAULT_HARVEST)
    parser.add_argument("--raw-xml", default=DEFAULT_RAW_XML)
    parser.add_argument("--runs", nargs="+", default=DEFAULT_RUNS)
    parser.add_argument("--answers-4b", default=DEFAULT_ANSWERS_4B)
    parser.add_argument("--answers-35b", default=DEFAULT_ANSWERS_35B)
    parser.add_argument("--bc-boundary", required=True,
                        help="BC's outline as WKT (bc_boundary_gbif.wkt), the one step 1 used")
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--replace", action="store_true", help="write over an existing output file")
    args = parser.parse_args()
    if os.path.exists(args.out) and not args.replace:
        raise SystemExit(f"ERROR: {args.out} already exists; pass --replace to write over it")

    shape = load_bc_shape(args.bc_boundary)
    members = exported_groups(read_union(args.union))
    ids = set(members["record_id"])
    facts = read_datacite(args.raw_xml, ids)
    harvest = read_harvest(args.harvest, ids)
    box_counts = read_box_counts(args.harvest)
    rows = build_rows(members, facts, harvest, read_pass1_places(args.runs), read_filter2(args.answers_4b),
                      read_filter2(args.answers_35b), read_catalogue_names(), shape, box_counts)
    table = pa.Table.from_pylist(rows, schema=SCHEMA)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    pq.write_table(table, args.out)
    print_counts(pd.DataFrame(rows), changed_from_dup_show(members, rows), shape)
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
