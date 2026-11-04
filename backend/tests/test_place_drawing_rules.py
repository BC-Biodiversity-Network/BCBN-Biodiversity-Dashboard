"""Tests for the place lookup's drawing rules (pipeline/lunaris/place_drawing_rules.py),
decided by Lucia on 2026-11-03: large areas and streams are never drawn; a
lake, pond or reservoir is drawn only in BC and only when the Canadian names
file shows it at 1:1,000,000 or less, and is not on the hand list of lakes
never drawn (Lake Koocanusa); a town or a spot is drawn; a park or other
GeoNames class L area is never drawn; any other feature (a bay, island,
delta and so on) is drawn only in Canada and only when the
Canadian names file shows it at 1:1,000,000 or less, under a matching kind
of feature. The Canadian names file is a small one written for each test."""

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "pipeline" / "lunaris"))
import place_drawing_rules as rules  # noqa: E402

CGN_HEADER = ("CGNDB ID,Geographical Name,ISO Language Code,Language,Syllabic Form,Generic Term,Generic Category,"
              "Concise Code,Toponymic Feature ID,Latitude,Longitude,Location,Province - Territory,Relevance at Scale,"
              "Decision Date,Source")


def cgn_row(name, code, latitude, longitude, scale, term="Lake", category="Water Feature"):
    """Return one line of the Canadian names file, with only the fields the rules read filled in."""
    return f"X,{name},eng,English,,{term},{category},{code},id,{latitude},{longitude},,British Columbia,{scale},,"


def cgn_lakes(tmp_path, rows):
    """Write a small Canadian names file (with the real file's byte order mark) and read it."""
    path = tmp_path / "cgn.csv"
    path.write_text("﻿" + "\n".join([CGN_HEADER] + rows) + "\n", encoding="utf-8")
    return rules.read_cgn(str(path))


def place(name, code, latitude=50.0, longitude=-118.0, kind="in_bc", large=False, matched_name=None,
          feature_class="H", country_code="CA"):
    """Return one picked place as stage 1 lists its candidates, with its GeoNames feature class added."""
    return {"geonameid": 1, "name": name, "matched_name": matched_name or name, "feature_class": feature_class,
            "feature_code": code, "country_code": country_code, "kind": kind, "latitude": latitude,
            "longitude": longitude, "large": large}


def test_streams_are_never_drawn(tmp_path):
    """A river or creek is not drawn, whatever its size or where it is."""
    lakes = cgn_lakes(tmp_path, [])
    for code in ("STM", "STMI", "STMX", "STMM"):
        assert rules.drawing_decision(place("Salmon River", code), lakes) == (False, "stream", None)
    assert rules.drawing_decision(place("Salmon River", "STM", kind="outside_bc"), lakes)[1] == "stream"


def test_large_areas_are_not_drawn(tmp_path):
    """A place stage 1 read as a large area, or one with no coordinates, is not drawn."""
    lakes = cgn_lakes(tmp_path, [])
    assert rules.drawing_decision(place("Haro Strait", "STRT", large=True), lakes) == (False, "large_area", None)
    assert rules.drawing_decision(place("Kootenay Lake", "LK", latitude=None), lakes)[1] == "large_area"


def test_bc_lakes_drawn_only_at_one_to_a_million_or_less(tmp_path):
    """A BC lake at 1:50,000, 1:250,000 or 1:1,000,000 is drawn; one at 1:5,000,000 is not."""
    lakes = cgn_lakes(tmp_path, [cgn_row("Susan Lake", "LAKE", 51.5703, -117.3978, 50000),
                                 cgn_row("Mara Lake", "LAKE", 50.78, -119.0, 1000000),
                                 cgn_row("Kootenay Lake", "LAKE", 49.7, -116.9, 5000000)])
    assert rules.drawing_decision(place("Susan Lake", "LK", 51.57, -117.40), lakes) == (True, "lake", 50000)
    assert rules.drawing_decision(place("Mara Lake", "LK", 50.79, -119.01), lakes) == (True, "lake", 1000000)
    assert rules.drawing_decision(place("Kootenay Lake", "LK", 49.71, -116.91), lakes) == (False, "lake_too_big",
                                                                                          5000000)


def test_ponds_and_reservoirs_follow_the_lake_rule(tmp_path):
    """Ponds and reservoirs count as lakes, sized by the Canadian names file's LAKE entries."""
    lakes = cgn_lakes(tmp_path, [cgn_row("Kinbasket Lake", "LAKE", 51.75, -117.57, 5000000, term="Reservoir"),
                                 cgn_row("Mud Pond", "LAKE", 49.0, -117.0, 50000, term="Pond")])
    assert rules.drawing_decision(place("Kinbasket Lake", "RSV", 51.76, -117.57), lakes)[:2] == (False,
                                                                                                 "lake_too_big")
    assert rules.drawing_decision(place("Mud Pond", "PND", 49.0, -117.0), lakes)[:2] == (True, "lake")


def test_lakes_outside_bc_or_without_a_size_are_not_drawn(tmp_path):
    """A lake outside BC is never drawn; a BC lake is not drawn when the names file has no lake of its name nearby."""
    lakes = cgn_lakes(tmp_path, [cgn_row("Susan Lake", "LAKE", 51.5703, -117.3978, 50000),
                                 cgn_row("Long Lake", "RIV", 50.0, -118.0, 50000)])
    assert rules.drawing_decision(place("Susan Lake", "LK", 46.56, -82.48, kind="outside_bc"), lakes) == (
        False, "lake_outside_bc", None)
    # Same name, but more than 0.05 degrees away: another lake.
    assert rules.drawing_decision(place("Susan Lake", "LK", 51.93, -120.91), lakes) == (False, "lake_no_size", None)
    # A names file entry that is not a lake gives no size.
    assert rules.drawing_decision(place("Long Lake", "LK", 50.0, -118.0), lakes) == (False, "lake_no_size", None)


def test_lake_matched_by_the_name_the_record_used(tmp_path):
    """The names file's lake may be found under the name the record matched rather than GeoNames' main name."""
    lakes = cgn_lakes(tmp_path, [cgn_row("Mud Lake", "LAKE", 49.2, -115.2, 250000)])
    picked = place("Mud Reservoir", "RSV", 49.21, -115.21, matched_name="Mud Lake")
    assert rules.drawing_decision(picked, lakes) == (True, "lake", 250000)


def test_the_nearest_lake_of_the_name_gives_the_size(tmp_path):
    """With two lakes of one name close together, the nearer one's scale is used."""
    lakes = cgn_lakes(tmp_path, [cgn_row("Twin Lake", "LAKE", 50.00, -118.00, 50000),
                                 cgn_row("Twin Lake", "LAKE", 50.04, -118.00, 5000000)])
    assert rules.drawing_decision(place("Twin Lake", "LK", 50.035, -118.0), lakes) == (False, "lake_too_big",
                                                                                      5000000)


def test_towns_and_spots_are_drawn_whatever_their_scale(tmp_path):
    """A town or a spot is drawn anywhere: the names file's scale for a town says how prominent it is, not its size."""
    cgn = cgn_lakes(tmp_path, [cgn_row("Bamfield", "UNP", 48.83, -125.13, 2000000, "Community", "Populated Place")])
    assert rules.drawing_decision(place("Bamfield", "PPL", 48.83, -125.14, feature_class="P"), cgn) == (
        True, "point", None)
    assert rules.drawing_decision(place("Seward", "PPL", 60.1, -149.4, kind="outside_bc", feature_class="P",
                                        country_code="US"), cgn) == (True, "point", None)
    assert rules.drawing_decision(place("UBC Farm", "FRM", 49.25, -123.24, feature_class="S"), cgn)[:2] == (
        True, "point")
    assert rules.feature_type({"feature_code": None}) is None


def test_area_features_drawn_only_when_small_enough(tmp_path):
    """A bay, island or park in BC is drawn at 1:1,000,000 or less, not at 1:2,000,000 or 1:5,000,000."""
    cgn = cgn_lakes(tmp_path, [cgn_row("Burrard Inlet", "BAY", 49.30, -123.09, 1000000, "Inlet"),
                               cgn_row("Quadra Island", "ISL", 50.18, -125.25, 2000000, "Island", "Terrain Feature"),
                               cgn_row("Banks Island", "ISL", 53.40, -130.13, 5000000, "Island", "Terrain Feature")])
    assert rules.drawing_decision(place("Burrard Inlet", "INLT", 49.30, -123.10), cgn) == (True, "sized", 1000000)
    assert rules.drawing_decision(place("Quadra Island", "ISL", 50.19, -125.25, feature_class="T"), cgn) == (
        False, "too_big", 2000000)
    assert rules.drawing_decision(place("Banks Island", "ISL", 53.41, -130.13, feature_class="T"), cgn) == (
        False, "too_big", 5000000)


def test_area_features_with_no_size_are_not_drawn(tmp_path):
    """Hudson Bay, Banks Island (NWT), Mackenzie Delta and Oahu have no size here, so they are not drawn."""
    cgn = cgn_lakes(tmp_path, [cgn_row("Banks Island", "ISL", 53.40, -130.13, 250000, "Island", "Terrain Feature")])
    for picked in (place("Hudson Bay", "BAY", 60.0, -85.0, kind="outside_bc"),
                   place("Banks Island", "ISL", 73.0, -121.5, kind="outside_bc", feature_class="T"),
                   place("Mackenzie Delta", "DLTA", 68.7, -134.7, kind="outside_bc", feature_class="T"),
                   place("Oahu", "ISL", 21.5, -158.0, kind="outside_bc", feature_class="T", country_code="US")):
        assert rules.drawing_decision(picked, cgn) == (False, "area_no_size", None), picked["name"]


def test_area_size_comes_only_from_the_same_kind_of_feature(tmp_path):
    """A bay is not given the size of an island or a town of the same name; outside Canada the file is not used."""
    cgn = cgn_lakes(tmp_path, [cgn_row("Cultus Bay", "ISL", 49.05, -121.98, 250000, "Island", "Terrain Feature"),
                               cgn_row("Cultus Bay", "UNP", 49.07, -121.98, 250000, "Community", "Populated Place")])
    assert rules.drawing_decision(place("Cultus Bay", "BAY", 49.06, -121.98), cgn) == (False, "area_no_size", None)
    cgn_bay = cgn_lakes(tmp_path, [cgn_row("Cultus Bay", "BAY", 49.06, -121.98, 250000, "Bay")])
    assert rules.drawing_decision(place("Cultus Bay", "BAY", 49.06, -121.98), cgn_bay) == (True, "sized", 250000)
    assert rules.drawing_decision(place("Cultus Bay", "BAY", 49.06, -121.98, country_code="US"), cgn_bay) == (
        False, "area_no_size", None)


def test_parks_and_other_class_l_areas_are_never_drawn(tmp_path):
    """Gwaii Haanas is at 1:1,000,000 in the names file, but a park is never drawn; nor is Yoho or a small city park."""
    cgn = cgn_lakes(tmp_path, [cgn_row("Gwaii Haanas National Park Reserve and Haida Heritage Site", "PARK", 52.35,
                                       -131.43, 1000000, "National Park Reserve", "Administrative Area")])
    for picked in (place("Gwaii Haanas National Park Reserve and Haida Heritage Site", "PRK", 52.35, -131.43,
                         feature_class="L"),
                   place("Yoho National Park", "PRK", 51.4, -116.5, feature_class="L"),
                   place("Stanley Park", "PRK", 49.30, -123.14, feature_class="L"),
                   place("Clayoquot Sound Biosphere Reserve", "RESV", 49.2, -125.9, feature_class="L")):
        assert rules.drawing_decision(picked, cgn) == (False, "park_or_area", None), picked["name"]


def test_a_place_needs_its_feature_class(tmp_path):
    """The rules refuse a place without its GeoNames feature class rather than guess."""
    picked = place("Bamfield", "PPL")
    del picked["feature_class"]
    try:
        rules.drawing_decision(picked, cgn_lakes(tmp_path, []))
    except ValueError as error:
        assert "feature_class" in str(error)
    else:
        raise AssertionError("a place without its feature class was accepted")


def test_lake_koocanusa_is_never_drawn(tmp_path):
    """Lake Koocanusa is shown at 1:1,000,000, but the hand list keeps both its GeoNames entries off the map."""
    lakes = cgn_lakes(tmp_path, [cgn_row("Lake Koocanusa", "LAKE", 49.2181, -115.2458, 1000000)])
    bc_entry = dict(place("Lake Koocanusa", "LK", 49.21806, -115.24583), geonameid=6048449)
    montana_entry = dict(place("Lake Koocanusa", "RSV", 48.54364, -115.23578, kind="outside_bc"), geonameid=5661428)
    assert rules.drawing_decision(bc_entry, lakes) == (False, "lake_hand_list", None)
    assert rules.drawing_decision(montana_entry, lakes) == (False, "lake_hand_list", None)
    # Without the hand list, the size rule alone would draw it.
    assert rules.drawing_decision(bc_entry, lakes, never_drawn=(frozenset(), frozenset())) == (True, "lake", 1000000)


def test_hand_list_matches_by_id_or_by_name(tmp_path):
    """A listed lake is found by its GeoNames id under another name, or by its name under another id."""
    never_drawn = (frozenset({42}), frozenset({"long lake"}))
    lakes = cgn_lakes(tmp_path, [cgn_row("Long Lake", "LAKE", 50.0, -118.0, 50000),
                                 cgn_row("Other Lake", "LAKE", 51.0, -118.0, 50000)])
    renamed = dict(place("Other Lake", "LK", 51.0, -118.0), geonameid=42)
    assert rules.drawing_decision(renamed, lakes, never_drawn)[1] == "lake_hand_list"
    assert rules.drawing_decision(place("Long Lake", "LK", 50.0, -118.0), lakes, never_drawn)[1] == "lake_hand_list"
    assert rules.drawing_decision(place("Other Lake", "LK", 51.0, -118.0), lakes, never_drawn)[1] == "lake"


def test_hand_list_file_gives_a_reason_for_every_lake(tmp_path):
    """The real hand list has Lake Koocanusa's two entries; a row with no reason is refused."""
    ids, names = rules.read_never_drawn()
    assert {6048449, 5661428} <= ids and "lake koocanusa" in names
    no_reason = tmp_path / "never.csv"
    no_reason.write_text("geonameid,name,reason\n1,Some Lake,\n", encoding="utf-8")
    try:
        rules.read_never_drawn(str(no_reason))
    except SystemExit as error:
        assert "needs a reason" in str(error)
    else:
        raise AssertionError("a row with no reason was accepted")
