"""Tests for the part of the pass 2 prompt after the record
(pipeline/lunaris/llm/geo_pass2_places.py), used by version C4: the Place
list from pass 1 with the note that another language model made it, the
GeoNames candidates
with each code's name in brackets, at most 5 per name with the count of all,
affiliations listed without candidates, hand-added entries marked as ours,
places with no match or too generic to look up, notes on how a name was
searched, a name listed twice referring back, that none of our own reading
of the candidates is given, and the consistency check.

These tests were first written for the earlier trial versions A and A2; the
version of this file for version C's wording of the list is in
backend/exploration/lunaris/bc_filter_2026_10/old_pass2_prompts/tests/
(test_geo_pass2_places_with_c_wording.py).

They use made-up lookup results, so the GeoNames table is not needed. The
made-up BC, record and helpers here are also used by test_geo_pass2_c4.py."""

import copy
import sys
from pathlib import Path

import pytest
from shapely.geometry import Polygon

BACKEND = Path(__file__).resolve().parents[1]
# The pipeline scripts are not a package; add their folders to the path the
# way the scripts themselves do.
sys.path.insert(0, str(BACKEND / "pipeline" / "lunaris"))
sys.path.insert(0, str(BACKEND / "pipeline" / "lunaris" / "llm"))
import geo_pass2_places as pp  # noqa: E402
from bc_filter_rules import BcShape  # noqa: E402

TOY_BC = BcShape(Polygon([(-139, 60), (-114, 60), (-126.5, 48)]))

RECORD = {"id": "oai:example.org:1", "title": "Trout in Lake Fakeville", "subjects": ["Fakeville; B.C."],
          "abstract": "Counts from 2001.", "places": None, "publisher": "University of Placeholder",
          "points": [{"lon": -126.5, "lat": 55.0}], "boxes": None}


def candidate(**fields):
    """One lookup candidate in geonames_lookup's shape, with made-up values unless given."""
    base = {"geonameid": 1001, "main_name": "Fakeville", "matched_name": "Fakeville", "matched_from": "geonames",
            "name_type": "main", "language": "", "is_historic": False, "country_code": "CA",
            "country_name": "Canada", "admin1_code": "02", "admin1_name": "British Columbia",
            "feature_class": "P", "feature_class_name": "city, village,...", "feature_code": "PPL",
            "feature_code_name": "populated place", "population": 1200, "latitude": 55.0,
            "longitude": -126.0, "cc2": "", "cc2_names": []}
    base.update(fields)
    return base


def lookup(candidates, **summary):
    """One lookup result: the candidates and the summary fields pass 2 reads."""
    base = {"category": "both", "places_found": len(candidates), "match": "exact" if candidates else "none",
            "matched_on": "name" if candidates else "", "plural_removed": False, "direction_word_removed": "",
            "candidate_kinds": ["in_bc"] * len(candidates)}
    base.update(summary)
    return {"query": {}, "candidates": candidates, "summary": base}


def place(as_written, name=None, role="study_location"):
    """One place as pass 1 kept it."""
    return {"as_written": as_written, "name": name or as_written, "role": role}


def many(count):
    """count made-up candidates with ids 1 to count."""
    return [candidate(geonameid=n, main_name=f"Fakeville {n}") for n in range(1, count + 1)]


# --- The places and their candidates -------------------------------------

def test_places_come_with_the_note_that_a_model_found_them():
    text = pp.places_text([place("Lake Fakeville", "Lake Fakeville")], [lookup([candidate()])])
    assert pp.PLACES_NOTE in text and pp.CANDIDATES_NOTE in text
    lines = text.splitlines()
    assert lines[0] == "Place list (made by another language model):"
    assert lines[1] == ("Note from us: another language model made this list, so it may contain mistakes (a missed "
                        "place, a misread name, or a name that is not a place).")
    assert 'Place 1: as written "Lake Fakeville"; name "Lake Fakeville"; role study location' in text


def test_candidate_shows_geonames_fields_with_code_names_in_brackets():
    text = pp.places_text([place("Fakeville")],
                          [lookup([candidate(cc2="CA,US", cc2_names=["Canada", "United States"])])])
    assert ("Candidate 1: GeoNames id 1001; main name \"Fakeville\"; matched name \"Fakeville\" (main name); "
            "country CA (Canada); province or state 02 (British Columbia); feature class P (city, village,...), "
            "feature code PPL (populated place); population 1200; second countries CA (Canada), "
            "US (United States)") in text
    assert "from GeoNames' own code files (admin1CodesASCII, countryInfo, featureCodes_en), not from us" in text


def test_candidate_with_no_country_and_a_code_without_a_name():
    ocean = candidate(geonameid=7, main_name="Placeholder Ocean", matched_name="Placeholder Ocean", country_code="",
                      country_name="", admin1_code="00", admin1_name="", feature_class="H",
                      feature_class_name="stream, lake, ...", feature_code="OCN", feature_code_name="ocean",
                      population=0)
    text = pp.places_text([place("Placeholder Ocean")], [lookup([ocean])])
    assert "country none; province or state 00;" in text


def test_alternate_and_historic_names_are_shown_as_geonames_gives_them():
    old = candidate(matched_name="Old Fakeville", name_type="alternate", language="en", is_historic=True)
    text = pp.places_text([place("Old Fakeville")], [lookup([old])])
    assert 'matched name "Old Fakeville" (alternate name, language en, historic)' in text


def test_hand_added_region_is_marked_as_ours_with_its_note():
    region = candidate(geonameid=None, main_name="Examplia Valley", matched_name="Examplia Valley",
                       matched_from="hand", name_type="hand", feature_class="L", feature_class_name="",
                       feature_code="HAND", feature_code_name="", population=0, hand_note="well-known BC region")
    text = pp.places_text([place("Examplia Valley")], [lookup([region])])
    assert 'Candidate 1: added by us, not from GeoNames: "Examplia Valley", a region in CA (Canada)' in text
    assert "Note from us: we added this place by hand: well-known BC region" in text
    assert "GeoNames id None" not in text


def test_hand_added_name_for_a_geonames_place_is_marked_as_ours():
    alias = candidate(main_name="Fakeville National Park of Canada", matched_name="Fakeville National Park",
                      matched_from="hand", name_type="hand", hand_note="GeoNames lists it only under its long name")
    text = pp.places_text([place("Fakeville National Park")], [lookup([alias])])
    assert 'matched through the name "Fakeville National Park", which we added by hand (not in GeoNames)' in text
    assert "Note from us: we added this name by hand: GeoNames lists it only under its long name" in text


def test_no_match_and_too_generic():
    text = pp.places_text([place("Placeholder Bay"), place("lakes", "Lakes")],
                          [lookup([], category="not_found"), lookup([], category="too_generic", match="too generic")])
    assert "Place 1" in text and "  No GeoNames match." in text
    assert "not looked up in GeoNames, because the name is too general on its own" in text


def test_at_most_five_candidates_with_the_count_of_all():
    text = pp.places_text([place("Fakeville")], [lookup(many(8), places_found=37)])
    assert "GeoNames places with this name (5 of 37 shown):" in text
    assert "Candidate 5:" in text and "Candidate 6:" not in text
    assert "at most 5 for each name" in pp.CANDIDATES_NOTE
    one = pp.places_text([place("Fakeville")], [lookup([candidate()])])
    assert "GeoNames places with this name (1 in all):" in one


def test_lookup_results_are_not_changed():
    result = lookup(many(8), places_found=8)
    kept = copy.deepcopy(result)
    pp.places_text([place("Fakeville")], [result])
    assert result == kept


def test_affiliation_is_listed_without_candidates():
    places = [place("University of Fakeville", "Fakeville", "affiliation"), place("Fakeville", "Fakeville")]
    text = pp.places_text(places, [lookup(many(2)), lookup(many(2))])
    first, second = text.split("Place 2:")
    assert "role affiliation" in first and "Candidate" not in first
    assert pp.AFFILIATION_NOTE in first
    # The same name as a study location still gets its candidates.
    assert "Candidate 1:" in second and "the same as for place" not in second


@pytest.mark.parametrize("summary, note", [
    ({"match": "approximate"}, "no GeoNames place has exactly this name; these have a similar spelling"),
    ({"plural_removed": True}, "found after removing the plural"),
    ({"direction_word_removed": "Southern"},
     'nothing has this exact name; found after removing the word "Southern" at its start'),
    ({"matched_on": "as_written"}, "found by the name as written in the record"),
])
def test_notes_on_how_a_name_was_searched(summary, note):
    text = pp.places_text([place("Fakeville")], [lookup([candidate()], **summary)])
    assert f"Note from us: {note}" in text


def test_same_name_for_two_roles_refers_back():
    places = [place("Fakeville"), place("Fakeville", role="other")]
    text = pp.places_text(places, [lookup([candidate()]), lookup([candidate()])])
    assert text.count("Candidate 1:") == 1
    assert "GeoNames places: the same as for place 1 above." in text


def test_no_places():
    assert pp.places_text([], []) == ("Place list (made by another language model):\n"
                                      "Note from us: the other language model found no place names in this record.")


def test_none_of_our_own_reading_is_given():
    places = [place("Fakeville"), place("Placeholder River")]
    text = pp.places_text(places, [lookup([candidate(), candidate(geonameid=2, country_code="US")], category="both"),
                                   lookup([candidate()], category="spans_bc_border")])
    for word in ("in BC only", "in_bc", "both", "spans", "contains BC", "outside BC", "ocean or sea",
                 "candidate_kinds", "category"):
        assert word not in text


def test_every_added_line_is_marked():
    places = [place("Fakeville"), place("Placeholder Bay"), place("University of Fakeville", "Fakeville", "affiliation")]
    text = pp.places_text(places, [lookup([candidate()], match="approximate"), lookup([]), lookup([])])
    starts = ("Note from us", "Place ", "  Note from us", "  Candidate ", "  GeoNames places", "  No GeoNames match")
    for line in text.splitlines()[1:]:
        assert line == "" or line.startswith(starts), line


# --- The checks on an answer ---------------------------------------------

def answer(location, reason):
    """An answer with a location, for the consistency check."""
    return {"reason": reason, "location": location}


def test_flags_an_unsure_reason_with_a_definite_location():
    assert pp.consistency_flags(answer("in_bc", "The record does not say where the samples came from.")) == \
        ["reason_says_unsure"]
    assert pp.consistency_flags(answer("unknown", "It is unclear where the data were collected.")) == []


def test_flags_in_bc_with_a_reason_outside_bc_and_the_reverse():
    assert pp.consistency_flags(answer("in_bc", "The lake lies outside British Columbia.")) == \
        ["reason_says_outside_bc"]
    assert pp.consistency_flags(answer("elsewhere", "The samples were collected near Fakeville in BC.")) == \
        ["reason_says_in_bc"]
    # "outside BC" in the same reason is not taken as a contradiction.
    assert pp.consistency_flags(answer("elsewhere", "Collected in BC? No: the lake is outside BC.")) == []


def test_no_flag_for_a_consistent_answer():
    assert pp.consistency_flags(answer("in_bc", "The abstract says the plots are in Lake Examplia, BC.")) == []
    assert pp.consistency_flags(answer("elsewhere", "The study was in Placeholder Bay, outside Canada.")) == []
