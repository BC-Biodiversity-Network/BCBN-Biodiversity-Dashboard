"""
bc_filter_rules.py

The rules of the Lunaris BC filter (filter 3). They decide whether a
biodiversity record is about British Columbia only where a record's own
metadata settles it beyond doubt. Everything else goes to the geography
prompt (step 4), which reads the text and can also answer "Canada including
BC" or "unknown".

Each record gets one of three classes here:

  bc         a box or point that is clearly in BC, or a BC-only catalogue
  not_bc     a box or point that is clearly outside BC, or another
             province's catalogue
  needs_llm  everything else

What decides (and nothing else does):

  box      inside BC: 90% or more of the box is in BC             -> bc
           covers BC: the box covers 80% or more of BC, and is
                      less than 5 times BC's size                  -> bc
           outside BC: no overlap, or only touching the edge
                      (under 2% of the box in BC and under 2% of
                      BC covered)                                  -> not_bc
  point    in BC -> bc;  outside BC -> not_bc. A point that lands in BC
           when the sign of its longitude is flipped (122.57 for -122.57)
           is most likely a typo and decides nothing.
  source   a catalogue that only holds BC data -> bc; one that only
           holds another province's or town's data -> not_bc
           (both lists are in bc_filter_sources.csv)

If these agree, that is the class. If they disagree (say a box in BC from
an Alberta catalogue, or one box in BC and another in Ontario), or none of
them applies, the record needs_llm. The catalogue alone does not decide for
a record with a huge box either (a national map on a provincial portal is
not that province's data); such a record needs_llm too.

What no longer decides, and why:

  - boxes partly in BC (50% to 90% "mostly", or under 50% "partly"): a
    share like 45% can be real BC data or an Alberta study whose box
    crosses the Rockies; the text has to be read
  - huge boxes (5 times BC or more) and "Canada" as the only place: whether
    a national dataset counts is the geography prompt's answer to give
  - the place-name field: some repositories fill it with the institution's
    address ("Guelph; Ontario; Canada") instead of where the data is from
  - the text (British Columbia, B.C., BC, the places in bc_places.csv,
    Vancouver): a mention can be an address, or one province in a list

These are still worked out and written to the output (box_class,
box_share, box_cover, box_size, place_hint, place_words, text_title,
text_keywords, text_abstract), as a cross-check: once the geography prompt
has answered, any record where its answer and these columns disagree is
worth a look.

This module only holds the rules. build_bc_filter_rules.py runs them over
the harvest and writes the result.
"""

import csv
import os
import re
from dataclasses import dataclass, field

import pandas as pd
import shapely
from pyproj import Transformer
from shapely import wkt
from shapely.geometry import Point, box as make_box
from shapely.ops import transform

from check_harvest import source_of

HERE = os.path.dirname(os.path.abspath(__file__))
PLACES_CSV = os.path.join(HERE, "bc_places.csv")
SOURCES_CSV = os.path.join(HERE, "bc_filter_sources.csv")

CLASSES = ["bc", "not_bc", "needs_llm"]
# The rules that can decide a class, always listed in this order.
RULE_ORDER = ["box", "point", "source"]

# --- Box rules -------------------------------------------------------------
# Areas are measured in an equal-area map projection (EPSG:6933), so a
# square kilometre counts the same everywhere on Earth. For each box:
#   share = the part of the box that lies in BC
#   cover = the part of BC that the box covers
EQUAL_AREA = "EPSG:6933"

# A box at least 5 times BC's area is "huge": about half of Canada or more.
# BC's own bounding rectangle is only about 2 times BC's area (BC is not a
# rectangle), so a box this big is never just "about BC". On the 2026-10-20
# harvest the box sizes also split naturally here: below are boxes centred on
# one province, above are Canada, North America or "the West and beyond".
HUGE_TIMES_BC = 5.0

# Under 2% either way, the box only touches BC's edge. BC's outline is drawn
# about 2 km outside the real border (see BcShape), so a box for Alberta or
# Yukon overlaps it by a thin strip; that is not BC.
TOUCH = 0.02

INSIDE_SHARE = 0.90   # 90% or more of the box is in BC
MOSTLY_SHARE = 0.50   # half or more of the box is in BC
BC_WIDE_COVER = 0.80  # the box covers 80% or more of BC without being huge

# The box classes, most BC first. For the cross-check columns a record with
# several boxes reports its most BC one.
BOX_ORDER = ["inside", "bc_wide", "mostly_bc", "partly_bc", "huge", "outside"]
# Only these box classes decide anything; the others go to the geography
# prompt.
BOX_DECIDES = {"inside": "bc", "bc_wide": "bc", "outside": "not_bc"}
BOX_NOTE = {
    "inside": "box inside BC",
    "bc_wide": "box covers BC",
    "mostly_bc": "box mostly in BC",
    "partly_bc": "box partly in BC",
    "huge": "huge box (5x BC or more)",
    "outside": "box outside BC",
}

# --- Text rules ------------------------------------------------------------
# British Columbia by name, in English or French.
BC_NAME = re.compile(r"British\s+Columbia|Colombie[-\s]Britannique", re.IGNORECASE)
# "B.C." (or "B. C.") and the French "C.-B.", not as part of a longer word.
BC_ABBR = re.compile(r"(?<![\w.])B\.\s?C\.(?![A-Za-z])|(?<![\w.])C\.-B\.(?![A-Za-z])")
# "BC" in capitals on its own: no letter or digit right before or after it,
# so "BCE", "ABC", "BCG" and "BCs" never match. "AD/BC" is ruled out by the
# slash before it.
BC_CAPS = re.compile(r"(?<![\w/\-])BC(?![\w])")
# A year just before it means "before Christ": "300 BC", "300 B.C.", "AD/BC".
YEAR_BEFORE = re.compile(r"(\d\s*|\bAD\s*/\s*)$")

# --- Place-field cross-check -----------------------------------------------
# The place field no longer decides anything (see the top of this file), but
# what it says is still written out for the cross-check.
# Names in a place field that point away from BC: the other provinces and
# territories, nearby waters, and countries and regions that come up in this
# catalogue.
ELSEWHERE = re.compile(
    r"\b(?:Alberta|Saskatchewan|Manitoba|Ontario|Qu[eé]bec|New Brunswick|Nouveau-Brunswick|"
    r"Nova Scotia|Nouvelle-[EÉ]cosse|Prince Edward Island|[IÎ]le-du-Prince-[EÉ]douard|Newfoundland|"
    r"Labrador|Yukon|Northwest Territories|Territoires du Nord-Ouest|Nunavut|Hudson Bay|"
    r"Great Lakes|Lake Erie|Lake Ontario|Lake Superior|Lake Huron|Gulf of St\.? Lawrence|"
    r"St\.? Lawrence|Beaufort Sea|Arctic Archipelago|Bay of Fundy|Scotian Shelf|Grand Banks|"
    r"United States|U\.S\.A\.|USA|Mexico|Brazil|Argentina|Chile|Peru|Colombia|Ecuador|Costa Rica|"
    r"Panama|United Kingdom|England|Scotland|Ireland|France|Germany|Spain|Portugal|Italy|Norway|"
    r"Sweden|Finland|Denmark|Netherlands|Switzerland|Austria|Poland|Russia|China|Japan|Korea|"
    r"India|Indonesia|Malaysia|Philippines|Thailand|Vietnam|Australia|New Zealand|South Africa|"
    r"Kenya|Tanzania|Uganda|Madagascar|Ethiopia|Greenland|Iceland|Antarctica|Antarctic|"
    r"California|Oregon|Florida|Texas|Michigan|Minnesota|Montana|Colorado|Arizona|Hawaii|"
    r"Alaska|Washington|Europe|Africa|Asia|South America|Central America|Caribbean|Oceania|"
    r"Arctic|Atlantic)\b")
# A place field that says only "Canada" (in one of its usual spellings).
CANADA_ONLY = re.compile(r"^\s*(?:Canada|CANADA|Canada \(.*\)|Canadian|Canadian waters)\s*$")


@dataclass
class BcShape:
    """
    BC's outline, ready for measuring boxes and points against.

    The outline used is the one the GBIF pipeline uses for its download
    filter: bc_boundary_gbif.wkt, written by pipeline/gbif/make_gbif_boundary.py
    from BC's legal, marine-inclusive boundary (ABMS), pushed out about 2 km
    and simplified. Keeping both pipelines on one outline means "in BC" means
    the same thing for GBIF records and for Lunaris records.
    """

    outline: object                      # the outline in longitude and latitude
    outline_equal_area: object = None    # the same outline in the equal-area projection
    area: float = 0.0                    # BC's area in square metres
    to_equal_area: object = field(default=None, repr=False)

    def __post_init__(self):
        """Work out the equal-area version of the outline and its area once."""
        self.to_equal_area = Transformer.from_crs("EPSG:4326", EQUAL_AREA, always_xy=True).transform
        self.outline_equal_area = transform(self.to_equal_area, self.outline)
        self.area = self.outline_equal_area.area


def load_bc_shape(path):
    """Read BC's outline from a WKT file (bc_boundary_gbif.wkt) and return a BcShape."""
    with open(path) as handle:
        return BcShape(wkt.loads(handle.read()))


def repair_box(west, south, east, north):
    """
    Return a box as a list of shapely rectangles in longitude and latitude,
    after repairing the usual faults in Lunaris boxes.

      - South greater than north: the two were swapped, so they are put back.
      - West greater than east: either a box that crosses the 180 degree
        line (west is in the eastern half of the globe and east in the
        western half, as in a box over the Bering Sea), which is split into
        two rectangles, one each side of the line; or west and east swapped,
        which are put back.
      - Latitudes are kept within 85 degrees north or south, where the
        equal-area projection still works.

    A box with no area (west equal to east, or south equal to north) returns
    an empty list. When both are equal it is really a point; points_of picks
    those up.
    """
    if south > north:
        south, north = north, south
    south, north = max(south, -85.0), min(north, 85.0)
    if west == east or south >= north:
        return []
    if west > east:
        if west > 0 > east:
            return [make_box(west, south, 180.0, north), make_box(-180.0, south, east, north)]
        west, east = east, west
    return [make_box(west, south, east, north)]


def measure_box(lunaris_box, shape):
    """
    Measure one box against BC, and return (share, cover, size), or None for
    a box with no area.

      share  the part of the box that lies in BC, from 0 to 1
      cover  the part of BC that the box covers, from 0 to 1
      size   the box's area as a multiple of BC's area

    lunaris_box is a dict with w, e, s and n, as the harvest stores it. The
    box is repaired first (see repair_box). A box wholly beyond 85 degrees
    north or south (around a pole) is far from BC; it is returned as
    (0, 0, 0) without measuring, because the equal-area projection does not
    reach that far.
    """
    south, north = sorted((lunaris_box["s"], lunaris_box["n"]))
    if south >= 85.0 or north <= -85.0:
        return 0.0, 0.0, 0.0
    pieces = repair_box(lunaris_box["w"], lunaris_box["s"], lunaris_box["e"], lunaris_box["n"])
    if not pieces:
        return None
    # Box edges follow lines of longitude and latitude. Adding a point every
    # degree along them keeps their shape when the box is projected.
    box_shape = transform(shape.to_equal_area, shapely.segmentize(shapely.union_all(pieces), 1.0))
    box_area = box_shape.area
    in_bc = box_shape.intersection(shape.outline_equal_area).area
    return in_bc / box_area, in_bc / shape.area, box_area / shape.area


def classify_box(lunaris_box, shape):
    """
    Put one box in a class against BC, and return (class, size as a multiple of BC).

    The class comes from measure_box's share (the part of the box in BC) and
    cover (the part of BC the box covers), checked in this order:

      outside    share under 2% and cover under 2%: no overlap at all, or
                 only touching BC's edge (BC's outline is drawn about 2 km
                 outside the real border, so a neighbour's box often
                 overlaps it by a thin strip)
      huge       the box is 5 times BC's area or more (and is not outside)
      inside     share 90% or more
      bc_wide    share under 90% and cover 80% or more (such as BC's own
                 bounding box, which is about half BC and covers all of it)
      mostly_bc  share from 50% up to 90%, cover under 80%
      partly_bc  share under 50%, but share or cover at least 2%

    Returns (None, None) for a box with no area.
    """
    measured = measure_box(lunaris_box, shape)
    if measured is None:
        return None, None
    share, cover, size = measured
    if share < TOUCH and cover < TOUCH:
        return "outside", size
    if size >= HUGE_TIMES_BC:
        return "huge", size
    if share >= INSIDE_SHARE:
        return "inside", size
    if cover >= BC_WIDE_COVER:
        return "bc_wide", size
    if share >= MOSTLY_SHARE:
        return "mostly_bc", size
    return "partly_bc", size


def describe_boxes(boxes, shape):
    """
    Classify every box of a record, and return what the rules need.

    Returns a dict with:
      votes      for each box class that decides, the class it points to
                 and a note: ("bc", "box inside BC"), ("bc", "box covers BC")
                 or ("not_bc", "box outside BC"); empty when no box is
                 clearly in or clearly outside BC
      has_huge   True if any box is huge (5 times BC or more)
      box_class  the most BC class among the boxes, for the cross-check
      share, cover, size   that box's measures, for the cross-check
    A record with no box with an area gets an empty result.
    """
    found = []
    for b in (boxes if boxes is not None else []):
        kind, _ = classify_box(b, shape)
        if kind:
            found.append((kind, measure_box(b, shape)))
    if not found:
        return {"votes": [], "has_huge": False, "box_class": None, "share": None, "cover": None, "size": None}
    kinds = {kind for kind, _ in found}
    best_kind, (share, cover, size) = min(found, key=lambda f: BOX_ORDER.index(f[0]))
    return {
        "votes": [(BOX_DECIDES[k], BOX_NOTE[k]) for k in BOX_ORDER if k in kinds and k in BOX_DECIDES],
        "has_huge": "huge" in kinds,
        "box_class": best_kind, "share": share, "cover": cover, "size": size,
    }


def points_of(points, boxes):
    """
    Return a record's points as (longitude, latitude) pairs: its DataCite
    points, plus the centre of any box with no area.

    A box with no area is a point written as a box (west = east and south =
    north), or a line, such as (-180, 90, 180, 90), which runs along the 90
    degree line and so is just the North Pole. Its centre stands for it.
    """
    found = [(p["lon"], p["lat"]) for p in (points if points is not None else [])]
    for b in (boxes if boxes is not None else []):
        if b["w"] == b["e"] or b["s"] == b["n"]:
            found.append(((b["w"] + b["e"]) / 2, (b["s"] + b["n"]) / 2))
    return found


def classify_points(points, boxes, shape):
    """
    Return what a record's points say, or None if it has no points: the
    kinds of point found, joined with "+", out of

      inside     a point in BC
      outside    a point outside BC
      sign_typo  a point outside BC that lands in BC when the sign of its
                 longitude is flipped, such as 122.57 instead of -122.57 for
                 Maple Ridge. On 2026-10-20 four records had this, all of
                 them BC data. It is most likely a typo, so it decides
                 nothing.
    """
    kinds = set()
    for lon, lat in points_of(points, boxes):
        if shape.outline.covers(Point(lon, lat)):
            kinds.add("inside")
        elif shape.outline.covers(Point(-lon, lat)):
            kinds.add("sign_typo")
        else:
            kinds.add("outside")
    return "+".join(k for k in ["inside", "outside", "sign_typo"] if k in kinds) or None


def read_bc_places(path=PLACES_CSV):
    """
    Read the BC place list and return a pattern that finds those places in
    text.

    Rows with use "yes" are matched as whole words. "Vancouver" (use
    "special") matches with the exceptions in VANCOUVER. Rows with use "no"
    are shared or ambiguous names kept in the file only as a record of the
    decision; they never match.
    """
    with open(path, newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    names = [r["name"] for r in rows if r["use"].strip() == "yes"]
    names.sort(key=len, reverse=True)  # longest first, so "Fraser River" wins over a shorter name
    pattern = r"\b(?:" + "|".join(re.escape(n) for n in names) + r")\b"
    if any(r["name"] == "Vancouver" and r["use"].strip() == "special" for r in rows):
        pattern += "|" + VANCOUVER
    return re.compile(pattern)


# "Vancouver" counts as BC except where it is clearly something else:
#   Vancouver Island        has its own row in bc_places.csv
#   Vancouver, Washington   also written "Vancouver, WA", "Vancouver, Wash."
#                           or "Vancouver (WA)": the city in Washington State
#   Fort Vancouver          the historic site in Washington State
#   Vancouver Lake          the lake in Washington State
#   George Vancouver        the explorer
# The word before "Vancouver" is checked with a fixed-width look-back, so
# "Fort" and "George" must be followed by exactly one space.
VANCOUVER = (
    r"(?<!\bFort\s)(?<!\bGeorge\s)\bVancouver\b"
    r"(?!\s+(?:Island|Lake)\b)"
    r"(?!,?\s*(?:Washington|WA|Wash)\b)"
    r"(?!\s*\(\s*(?:Washington|WA|Wash\.?)\s*\))"
)


def read_sources(path=SOURCES_CSV):
    """
    Read the catalogue list and return {source: "bc" or "not_bc"}.

    Stops with an error if a row has another class, or if a source is listed
    twice, so a mistake in the file cannot slip through.
    """
    sources = {}
    with open(path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            name, kind = row["source"].strip(), row["class"].strip()
            if kind not in ("bc", "not_bc"):
                raise ValueError(f"{path}: {name} has class {kind!r}; it must be bc or not_bc")
            if name in sources:
                raise ValueError(f"{path}: {name} is listed more than once")
            sources[name] = kind
    return sources


def bc_mentions(text, places):
    """
    Return the BC mentions in a piece of text, as a list of the matched
    words (for example ["British Columbia", "Haida Gwaii"]). An empty list
    means BC is not mentioned.

    "B.C." and "BC" do not count when a year comes just before them, as in
    "300 BC", "300 B.C." or "AD/BC".
    """
    if not text:
        return []
    found = [m.group(0) for m in BC_NAME.finditer(text)][:1]
    for pattern in (BC_ABBR, BC_CAPS):
        for m in pattern.finditer(text):
            if not YEAR_BEFORE.search(text[max(0, m.start() - 8):m.start()]):
                found.append(m.group(0))
                break
    found += [m.group(0) for m in places.finditer(text)][:3]
    return list(dict.fromkeys(found))


def as_text(value):
    """Turn a cell (text, a list of keywords, or missing) into one string."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    if hasattr(value, "__len__") and not isinstance(value, str):
        return " ; ".join(str(v) for v in value)
    return str(value)


@dataclass
class Rules:
    """Everything the rules need, read once: BC's outline, the place list and the catalogue list."""

    shape: BcShape
    places: object
    sources: dict


def load_rules(bc_boundary_path, places_path=PLACES_CSV, sources_path=SOURCES_CSV):
    """Read BC's outline and the two lists, and return them as a Rules."""
    return Rules(load_bc_shape(bc_boundary_path), read_bc_places(places_path), read_sources(sources_path))


def place_hint(places, rules):
    """
    Return what a record's place field says, for the cross-check, as
    (hint, words): hint is "bc", "elsewhere", "canada" or "" (nothing
    recognised), and words are the names that gave the hint.
    """
    text = as_text(places)
    names = [str(p) for p in (places if places is not None else [])]
    found = bc_mentions(text, rules.places)
    if found:
        return "bc", ", ".join(found)
    away = list(dict.fromkeys(ELSEWHERE.findall(text)))
    if away:
        return "elsewhere", ", ".join(away)
    if names and all(CANADA_ONLY.match(n) for n in names):
        return "canada", "Canada"
    return "", ""


def classify_record(record, rules):
    """
    Decide one record, and return a dict with the class, its reasons and the
    cross-check columns.

    record is a dict (or a row) with id, title, subjects, abstract, places,
    points and boxes, as the harvest stores them. The result holds:

      bc_class     bc, not_bc or needs_llm
      decided_by   the rules that decided it (box, point, source, joined
                   with "+"), or "none"
      evidence     a short note for a person to read
      source       the catalogue the record comes from
      and the cross-check columns described at the top of this file
    """
    record_id = str(record["id"])
    source = source_of(record_id)
    boxes = describe_boxes(record.get("boxes"), rules.shape)
    point_class = classify_points(record.get("points"), record.get("boxes"), rules.shape)
    source_kind = rules.sources.get(source)

    # The votes of the three deciding rules: (rule, class, note).
    votes = [("box", kind, note) for kind, note in boxes["votes"]]
    point_kinds = set((point_class or "").split("+"))
    if "inside" in point_kinds:
        votes.append(("point", "bc", "point in BC"))
    if "outside" in point_kinds:
        votes.append(("point", "not_bc", "point outside BC"))
    if source_kind == "bc":
        votes.append(("source", "bc", f"source: {source} (BC only)"))
    elif source_kind == "not_bc":
        votes.append(("source", "not_bc", f"source: {source} (another province)"))

    classes = {kind for _, kind, _ in votes}
    notes = "; ".join(note for _, _, note in votes)
    # A huge box decides nothing, and that includes letting the catalogue
    # decide for it: a national map on a provincial portal is not that
    # province's data. So when the catalogue is the only rule deciding and
    # the record has a huge box, the geography prompt reads it.
    catalogue_only = {rule for rule, _, _ in votes} == {"source"}
    if catalogue_only and boxes["has_huge"]:
        bc_class, evidence = "needs_llm", f"{BOX_NOTE['huge']} (does not decide); {notes} (not alone with a huge box)"
        votes = []
    elif len(classes) == 1:
        bc_class, evidence = classes.pop(), notes
    elif classes:
        # The deciding rules disagree: the geography prompt reads the record.
        bc_class, evidence = "needs_llm", "disagree: " + notes
    else:
        # Nothing decides. Say what there was, if anything.
        bc_class = "needs_llm"
        if "sign_typo" in point_kinds:
            evidence = "point outside BC, but in BC with its longitude sign flipped (likely a typo)"
        elif boxes["box_class"]:
            evidence = BOX_NOTE[boxes["box_class"]] + " (does not decide)"
        else:
            evidence = "nothing that decides"

    hint, words = place_hint(record.get("places"), rules)
    rounded = {k: (round(boxes[k], 4) if boxes[k] is not None else None) for k in ("share", "cover", "size")}
    return {
        "record_id": record_id,
        "source": source,
        "bc_class": bc_class,
        "decided_by": "+".join(r for r in RULE_ORDER if r in {rule for rule, _, _ in votes}) or "none",
        "evidence": evidence,
        # Cross-check only: none of these decides anything.
        "box_class": boxes["box_class"],
        "box_share": rounded["share"],
        "box_cover": rounded["cover"],
        "box_size": rounded["size"],
        "point_class": point_class,
        "place_hint": hint,
        "place_words": words,
        "text_title": ", ".join(bc_mentions(as_text(record.get("title")), rules.places)),
        "text_keywords": ", ".join(bc_mentions(as_text(record.get("subjects")), rules.places)),
        "text_abstract": ", ".join(bc_mentions(as_text(record.get("abstract")), rules.places)),
    }
