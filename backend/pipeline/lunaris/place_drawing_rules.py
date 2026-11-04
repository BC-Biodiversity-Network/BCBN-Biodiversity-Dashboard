"""
place_drawing_rules.py

Decide whether one picked GeoNames place may be drawn on the map as a point.
This runs after the place lookup's model step (prompt P1), on the places the
model picked and on stage 1's rule picks; it is not part of the prompt or the
run. Lucia decided the rules for streams and lakes, and the hand list of
lakes never drawn, on 2026-11-03, and widened the size rule from lakes to
other features the same day, after the hard-case test on Fir picked Hudson
Bay, Banks Island and Yoho National Park, which would each have been drawn
as one point. She then confirmed that towns and spots are drawn without the
size cut, and that parks, reserves and other GeoNames class L areas are
never drawn.

The rules, in this order (the first that applies decides):
  large_area          a country, province, region, ocean, sea, range,
                      strait, island chain or regional district, or a place
                      with no coordinates (place_lookup_rules.is_large):
                      not drawn
  stream              any river, creek, brook or stream (GeoNames codes
                      STM...): never drawn. The point GeoNames and the
                      Canadian names file give a stream is one spot on it,
                      often its mouth, while the data may come from anywhere
                      along it.
  lake_hand_list      a place in place_rules_lakes_never_drawn.csv (by its
                      GeoNames id or its name), a lake too long or too wide
                      for one point although its size alone would let it be
                      drawn (Lake Koocanusa, a reservoir about 145 km long):
                      not drawn
  lake_outside_bc     a lake, pond or reservoir (GeoNames codes LK...,
                      RSV... and PND...) that GeoNames does not put in BC or
                      across its border: not drawn (there is no size for it)
  lake_no_size        a lake in BC with no entry of the same name within
                      0.05 degrees among the Canadian names file's lakes
                      (concise code LAKE): not drawn
  lake_too_big        a lake in BC that the Canadian names file shows only
                      at a scale larger than 1:1,000,000 (Kootenay, Shuswap,
                      Upper Arrow, Babine and Adams Lakes are at
                      1:5,000,000): not drawn
  lake                a lake in BC shown at 1:1,000,000 or less: drawn
  point               a populated place or a spot (GeoNames classes P and S:
                      a town, a farm, a building, a station): drawn. The
                      Canadian names file's scale is not a size for these:
                      it says how prominent a place is (Vancouver is at
                      1:17,500,000, Kitimat at 1:7,500,000, Bamfield at
                      1:2,000,000), so the size rule does not apply to them.
  park_or_area        a park, reserve or other area (GeoNames class L),
                      whatever its scale: never drawn. Gwaii Haanas National
                      Park Reserve covers about 1,500 km2 of southern Haida
                      Gwaii and Yoho National Park about 1,300 km2; one point
                      cannot show where in them data were collected.
  area_no_size        any other feature (a bay, gulf, inlet, island, delta,
                      mountain, valley, forest and so on) with no size:
                      outside Canada, or with no entry in the Canadian names
                      file of the same name and a matching kind of feature
                      within 0.05 degrees: not drawn. The names file used
                      here covers BC only, so every such feature elsewhere in
                      Canada has no size.
  too_big             such a feature that the Canadian names file shows only
                      at a scale larger than 1:1,000,000 (Banks Island and
                      Vancouver Island at 1:5,000,000, Quadra Island at
                      1:2,000,000): not drawn
  sized               such a feature shown at 1:1,000,000 or less (Burrard
                      Inlet, Barkley Sound, Porlier Pass): drawn

The Canadian names file's "Relevance at Scale" is the map scale at which a
name is shown; a bigger feature is shown on maps of a larger area (a larger
number). Its lakes (concise code LAKE, which also covers ponds, reservoirs,
lagoons and sloughs) are at 1:50,000, 1:250,000, 1:1,000,000 or 1:5,000,000.

Reads (read only): the Canadian names file for BC,
data/geonames/cgn_bc_csv_eng.csv, and the hand list
place_rules_lakes_never_drawn.csv (geonameid, name, reason; every GeoNames
entry of a listed lake has its own row, such as Lake Koocanusa's BC and
Montana entries).
"""

import os
import sys
from functools import lru_cache

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from geonames_lookup import normalise  # noqa: E402
from place_lookup_rules import is_large  # noqa: E402

DEFAULT_CGN = "data/geonames/cgn_bc_csv_eng.csv"
NEVER_DRAWN_CSV = os.path.join(HERE, "place_rules_lakes_never_drawn.csv")

# GeoNames feature codes of streams, and of lakes, ponds and reservoirs.
STREAM_CODE_STARTS = ("STM",)
LAKE_CODE_STARTS = ("LK", "RSV", "PND")

# GeoNames classes whose places are points, drawn without a size: populated
# places (P) and spots, buildings and farms (S).
POINT_CLASSES = ("P", "S")

# The Canadian names file's kinds of feature (Generic Category) that may give
# a size to a GeoNames place of each class, so that a name shared by two
# kinds of feature (a lake and a town) never gives one the other's size.
CGN_CATEGORIES = {
    "H": ("Water Feature", "Ice and Snow Feature"),
    "T": ("Terrain Feature", "Volcanic Feature", "Underground Feature", "Ice and Snow Feature"),
    "V": ("Feature Associated with Vegetation",),
    "A": ("Administrative Area",),
}

# The largest map scale at which a feature may still be drawn as a point.
LARGEST_SCALE = 1_000_000

# How near (in degrees of latitude and longitude) the Canadian names file's
# entry must be to the GeoNames point to count as the same feature.
SAME_FEATURE_DEGREES = 0.05

# The kinds of place (GeoNamesLookup.place_kind) that count as in BC.
IN_BC_KINDS = ("in_bc", "spans_bc_border")


def read_cgn(path=DEFAULT_CGN):
    """
    Return the Canadian names file's BC entries, grouped by their name in
    matching form: {name: [(latitude, longitude, scale, category, concise code)]}.
    """
    cgn = pd.read_csv(path, dtype=str, encoding="utf-8-sig")
    found = {}
    for name, latitude, longitude, scale, category, concise in zip(
            cgn["Geographical Name"], cgn["Latitude"], cgn["Longitude"], cgn["Relevance at Scale"],
            cgn["Generic Category"], cgn["Concise Code"]):
        found.setdefault(normalise(name), []).append((float(latitude), float(longitude), int(scale), category,
                                                      concise))
    return found


@lru_cache(maxsize=None)
def read_never_drawn(path=NEVER_DRAWN_CSV):
    """
    Return the hand list of lakes never drawn as (GeoNames ids, names in
    matching form). Every row must give a reason.
    """
    listed = pd.read_csv(path, dtype={"geonameid": int, "name": str, "reason": str})
    if listed["reason"].isna().any() or (listed["reason"].str.strip() == "").any():
        raise SystemExit(f"ERROR: every row of {path} needs a reason")
    return frozenset(listed["geonameid"]), frozenset(normalise(name) for name in listed["name"])


def on_never_drawn_list(place, never_drawn):
    """Return True if the place's GeoNames id, or its name as GeoNames or the record writes it, is on the hand list."""
    ids, names = never_drawn
    if place.get("geonameid") in ids:
        return True
    return any(normalise(place.get(field) or "") in names for field in ("name", "matched_name"))


def feature_type(place):
    """Return "stream", "lake" or None for a GeoNames place, from its feature code."""
    code = place.get("feature_code") or ""
    if code.startswith(STREAM_CODE_STARTS):
        return "stream"
    if code.startswith(LAKE_CODE_STARTS):
        return "lake"
    return None


def cgn_scale(place, cgn, fits):
    """
    Return the map scale the Canadian names file gives the entry nearest the
    place (within SAME_FEATURE_DEGREES) with the same name, among the entries
    fits(category, concise code) accepts, or None if there is none. The name
    is tried as GeoNames writes it and as the record matched it.
    """
    best = None
    names = {normalise(place.get(field) or "") for field in ("name", "matched_name")} - {""}
    for name in names:
        for latitude, longitude, scale, category, concise in cgn.get(name, []):
            if not fits(category, concise):
                continue
            distance = ((latitude - place["latitude"]) ** 2 + (longitude - place["longitude"]) ** 2) ** 0.5
            if distance < SAME_FEATURE_DEGREES and (best is None or distance < best[0]):
                best = (distance, scale)
    return best[1] if best else None


def drawing_decision(place, cgn, never_drawn=None):
    """
    Return (drawn, rule, scale) for one picked GeoNames place: whether it may
    be drawn as a point, the rule that decided (see the top of the file), and
    the map scale where one was looked up (else None).

    The place is a candidate as stage 1 lists it (model_candidate) with its
    GeoNames feature_class added: name, matched_name, feature_class,
    feature_code, country_code, kind, latitude, longitude and large (stage
    1's is_large reading; a place without it is read with is_large). cgn is
    read_cgn()'s result; never_drawn is read_never_drawn()'s, read from the
    hand list when not given.
    """
    if "feature_class" not in place:
        raise ValueError(f"place {place.get('geonameid')} has no feature_class; add it from the GeoNames table")
    large = place["large"] if "large" in place else is_large(place)
    if place.get("latitude") is None or place.get("longitude") is None or large:
        return False, "large_area", None
    kind = feature_type(place)
    if kind == "stream":
        return False, "stream", None
    if on_never_drawn_list(place, never_drawn if never_drawn is not None else read_never_drawn()):
        return False, "lake_hand_list", None
    if kind == "lake":
        if place.get("kind") not in IN_BC_KINDS:
            return False, "lake_outside_bc", None
        scale = cgn_scale(place, cgn, lambda category, concise: concise == "LAKE")
        if scale is None:
            return False, "lake_no_size", None
        if scale > LARGEST_SCALE:
            return False, "lake_too_big", scale
        return True, "lake", scale
    feature_class = place["feature_class"] or ""
    if feature_class in POINT_CLASSES:
        return True, "point", None
    if feature_class == "L":
        return False, "park_or_area", None
    categories = CGN_CATEGORIES.get(feature_class, ())
    scale = None
    if place.get("country_code") == "CA":
        scale = cgn_scale(place, cgn, lambda category, concise: category in categories)
    if scale is None:
        return False, "area_no_size", None
    if scale > LARGEST_SCALE:
        return False, "too_big", scale
    return True, "sized", scale
