"""Tests for pipeline/lunaris/place_lookup_rules.py, on made-up records and
lookups: each rule decides the names it should and no others, a pick needs
the record itself to name the province, state or country, quotes are copied
unchanged from the record, long candidate lists are shortened without losing
a BC or Canadian candidate, the map's scope follows what the map draws,
straits, island chains and regional districts are large areas, and a name
GeoNames does not have goes to the model with the places whose name holds
its words."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "pipeline" / "lunaris"))
import place_lookup_rules as rules  # noqa: E402
from geonames_lookup import normalise  # noqa: E402

BC_SHAPE = SimpleNamespace(rectangle=(-139.12771, 48.20636, -114.02091, 60.02025))

# What a few hints name, as GeoNamesLookup.hint_places gives it.
HINTS = {"british columbia": {("CA", "02")}, "bc": {("CA", "02")}, "canada": {("CA", None)},
         "washington": {("US", "WA")}, "ontario": {("CA", "08")}}


def hint_places(part):
    """A stand-in for GeoNamesLookup.hint_places."""
    return HINTS.get(normalise(part), set())


def candidate(geonameid, name, country="CA", admin1="02", feature=("P", "PPL"), lat=50.0, lon=-120.0):
    """Return a made-up candidate as GeoNamesLookup.candidate_record gives it."""
    return {"geonameid": geonameid, "main_name": name, "matched_name": name, "country_code": country,
            "country_name": {"CA": "Canada", "US": "United States"}.get(country, country), "admin1_code": admin1,
            "admin1_name": {"02": "British Columbia", "08": "Ontario", "WA": "Washington"}.get(admin1, ""),
            "feature_class": feature[0], "feature_code": feature[1], "feature_code_name": "",
            "population": 0, "latitude": lat, "longitude": lon}


def lookup(candidates, kinds, match="exact", **summary):
    """Return a made-up lookup answer with every candidate."""
    return {"candidates": candidates,
            "summary": {"match": match, "candidate_kinds": kinds, "places_found": len(candidates),
                        "looked_up": "", "plural_removed": False, "direction_word_removed": "", **summary}}


def record(**fields):
    """Return a made-up record with nothing in it but what is given."""
    return {"id": "r", "title": "A study", "abstract": "", "subjects": [], "places": [], **fields}


def name(text, as_written=None, roles=("study_location",)):
    """Return one place name as record_names gives it."""
    return {"name": text, "as_written": as_written or text, "roles": list(roles)}


MAPLE_RIDGE = [candidate(1, "Maple Ridge", "CA", "08", ("T", "RDGE")), candidate(2, "Maple Ridge"),
               candidate(3, "Maple Ridge", "US", "WA")]
MAPLE_KINDS = ["outside_bc", "in_bc", "outside_bc"]


def decide(rec, nm, found):
    """Return the rules' decision with the made-up hints and the real too-broad list."""
    return rules.decide(rec, nm, found, rules.read_too_broad(), hint_places)


def test_names_are_listed_once_with_every_role_and_a_study_place_spelling():
    """A name given twice keeps both roles, and the spelling of the study place rather than the institution's."""
    names = rules.record_names([
        {"name": "Victoria", "as_written": "University of Victoria", "role": "affiliation"},
        {"name": "Victoria", "as_written": "Victoria, BC", "role": "study_location"},
        {"name": "Kamloops", "as_written": "Kamloops", "role": "study_location"}])
    assert names == [{"name": "Victoria", "as_written": "Victoria, BC", "roles": ["affiliation", "study_location"]},
                     {"name": "Kamloops", "as_written": "Kamloops", "roles": ["study_location"]}]


def test_quotes_are_copied_unchanged_from_the_place_list_keywords_title_or_abstract_sentence():
    rec = record(title="Birds of Revelstoke", subjects=["Upper Columbia"],
                 places=["Maple Ridge; British Columbia; Canada"],
                 abstract="Plots were set up in 2020. Samples   came from Coal Creek, near Fernie. Done.")
    assert rules.find_quote(rec, "maple ridge") == ("places", "Maple Ridge; British Columbia; Canada")
    assert rules.find_quote(rec, "Upper Columbia") == ("subjects", "Upper Columbia")
    assert rules.find_quote(rec, "Revelstoke") == ("title", "Birds of Revelstoke")
    assert rules.find_quote(rec, "came from coal creek") == ("abstract", "Samples   came from Coal Creek, near Fernie.")
    assert rules.find_quote(rec, "Nass") == (None, None)


def test_a_long_sentence_is_cut_to_a_window_that_is_still_in_the_record():
    text = "Intro. " + " ".join(["word"] * 200) + " Kakwa " + " ".join(["word"] * 200) + "."
    start = text.index("Kakwa")
    quote = rules.sentence_around(text, start, start + 5)
    assert quote in text and "Kakwa" in quote and len(quote) <= 400


@pytest.mark.parametrize("nm, found, rule, reason", [
    (name("University of Manitoba", roles=["affiliation"]), lookup([candidate(1, "Winnipeg")], ["outside_bc"]),
     "affiliation", "affiliation"),
    (name("Global"), lookup([candidate(1, "Globel", "SI", "")], ["outside_bc"], match="approximate"),
     "too_broad_list", "too_broad"),
    (name("North Pacific", "north Pacific"), lookup([candidate(1, "North Pacific", "US", "MT")], ["outside_bc"]),
     "too_broad_list", "too_broad"),
    (name("Western provinces"), lookup([], [], match="too generic"), "too_generic", "too_broad"),
    (name("Columbia Wetlands"), lookup([], [], match="none"), "not_found", "not_in_geonames"),
    (name("Fraser Ridge"), lookup([candidate(1, "Kinsey Ridge", "US", "CA")], ["outside_bc"], match="approximate"),
     "approximate_only", "not_in_geonames"),
    (name("Canada"), lookup([candidate(1, "Canada", "CA", "", ("A", "PCLI")), candidate(2, "Canada", "US", "KY")],
                            ["contains_bc", "outside_bc"]), "large_area", "too_broad"),
    (name("Hudson Bay"), lookup([candidate(1, "Hudson Bay", "CA", "", ("H", "SEA")), candidate(2, "Hudson Bay", "CA", "08")],
                                ["outside_bc", "outside_bc"]), "large_area", "ocean_or_sea"),
])
def test_rules_that_say_do_not_draw(nm, found, rule, reason):
    result = decide(record(), nm, found)
    assert (result["decision"], result["rule"], result["reason"], result["candidate"]) == (
        "do_not_draw", rule, reason, None)


def test_the_records_own_place_list_picks_the_one_place_in_the_named_province():
    """Maple Ridge: three in GeoNames, one in BC, and the record's place list says British Columbia."""
    rec = record(places=["Maple Ridge; British Columbia; Canada"])
    result = decide(rec, name("Maple Ridge"), lookup(MAPLE_RIDGE, MAPLE_KINDS))
    assert (result["decision"], result["rule"], result["candidate"]["geonameid"]) == ("pick", "record_place_list", 2)
    assert (result["field"], result["quote"]) == ("places", "Maple Ridge; British Columbia; Canada")


def test_a_name_written_with_its_province_in_the_text_is_left_to_the_model_unless_it_is_a_large_area():
    """The text may name a place for another reason ("between Wardner, BC and Libby, Montana"), so no pick."""
    rec = record(abstract="This reservoir extends 140 km between Wardner, BC and Libby, Montana.")
    wardner = lookup([candidate(1, "Wardner"), candidate(2, "Wardner", "US", "WA")], ["in_bc", "outside_bc"])
    result = decide(rec, name("Wardner, British Columbia", "Wardner, BC"), wardner)
    assert (result["decision"], result["rule"]) == ("model", "model")
    assert result["quote"] == "This reservoir extends 140 km between Wardner, BC and Libby, Montana."
    alberta = lookup([candidate(1, "Alberta", "CA", "01", ("A", "ADM1"))], ["outside_bc"])
    result = decide(record(abstract="Pine was sampled in Alberta, Canada."), name("Alberta", "Alberta, Canada"), alberta)
    assert (result["decision"], result["rule"], result["reason"]) == ("do_not_draw", "written_with_hint", "too_broad")


def test_a_hint_that_fits_two_places_or_none_leaves_the_name_to_the_model():
    """Susan Lake: two lakes in BC, so "British Columbia" does not say which."""
    found = lookup([candidate(1, "Susan Lake", feature=("H", "LK"), lat=51.93, lon=-120.91),
                    candidate(2, "Susan Lake", feature=("H", "LK"), lat=51.57, lon=-117.4)], ["in_bc", "in_bc"])
    assert decide(record(places=["Susan Lake; British Columbia"]), name("Susan Lake"), found)["decision"] == "model"
    assert decide(record(places=["Maple Ridge; Alberta"]), name("Maple Ridge"),
                  lookup(MAPLE_RIDGE, MAPLE_KINDS))["decision"] == "model"


def test_hints_are_not_used_for_a_name_found_another_way():
    """"Northern Columbia Basin" found only without "Northern": the hint rules do not pick."""
    found = lookup([candidate(1, "Columbia Basin")], ["in_bc"], direction_word_removed="Northern")
    result = decide(record(places=["Northern Columbia Basin; British Columbia"]), name("Northern Columbia Basin"), found)
    assert result["decision"] == "model"


def test_a_hinted_large_area_is_not_drawn():
    found = lookup([candidate(1, "British Columbia", feature=("A", "ADM1"))], ["in_bc"])
    result = decide(record(places=["British Columbia; Canada"]), name("British Columbia"), found)
    assert (result["decision"], result["rule"], result["reason"]) == ("do_not_draw", "record_place_list", "too_broad")


def test_a_large_best_match_with_a_small_place_in_bc_goes_to_the_model():
    """Victoria: the Australian state comes first, but Victoria in BC is a city."""
    found = lookup([candidate(1, "Victoria", "AU", "07", ("A", "ADM1")), candidate(2, "Victoria", feature=("P", "PPLA"))],
                   ["outside_bc", "in_bc"])
    result = decide(record(), name("Victoria"), found)
    assert (result["decision"], result["rule"]) == ("model", "model")


def test_everything_else_goes_to_the_model_with_its_quote():
    rec = record(abstract="Lakes in the Northern Columbia Basin were sampled.")
    found = lookup(MAPLE_RIDGE, MAPLE_KINDS)
    result = decide(rec, name("Maple Ridge"), found)
    assert (result["decision"], result["quote"]) == ("model", None)
    assert decide(rec, name("Northern Columbia Basin"), found)["quote"] == rec["abstract"]


def test_a_long_candidate_list_keeps_every_bc_and_canadian_place_and_counts_the_rest():
    candidates = ([candidate(i, "Williams Lake", "US", "MN") for i in range(30)]
                  + [candidate(100 + i, "Williams Lake", "CA", "07") for i in range(10)]
                  + [candidate(200 + i, "Williams Lake") for i in range(5)]
                  + [candidate(300 + i, "Williams Lake", "GB", "") for i in range(3)])
    kinds = ["outside_bc"] * 40 + ["in_bc"] * 5 + ["outside_bc"] * 3
    listed, omitted = rules.listed_candidates(candidates, kinds)
    ids = {c["geonameid"] for c in listed}
    assert {200, 201, 202, 203, 204} <= ids and {100 + i for i in range(10)} <= ids
    assert len(listed) == 35 and omitted == {"US": 10, "GB": 3}
    short, none_left = rules.listed_candidates(candidates[:40], kinds[:40])
    assert len(short) == 40 and none_left == {}


@pytest.mark.parametrize("boxes, points, scope", [
    ([], [], True),
    ([{"w": -139.5, "e": -113.5, "s": 48.0, "n": 60.0}], [], True),
    ([{"w": -124.0, "e": -123.0, "s": 50.0, "n": 49.0}], [], True),
    ([{"w": -124.0, "e": -123.0, "s": 49.0, "n": 50.0}], [], False),
    ([], [{"lat": 49.0, "lon": -123.0}], False),
])
def test_the_maps_scope_is_records_with_no_solid_box_and_no_point(boxes, points, scope):
    """A box matching BC's rectangle is drawn dashed, and a swapped box not at all: places would still be listed."""
    assert rules.in_map_scope(boxes, points, BC_SHAPE) is scope


def test_a_model_item_carries_the_record_and_candidates_only_for_names_left_to_the_model():
    rec = record(source_name="CanWIN", final_class="bc_only", frontend_group="bc_data",
                 places=["Maple Ridge; British Columbia; Canada"])
    found = lookup(MAPLE_RIDGE, MAPLE_KINDS)
    decided = [(name("Maple Ridge"), found, decide(rec, name("Maple Ridge"), found)),
               (name("Ridge Lake"), found, {"decision": "model", "rule": "model", "reason": None})]
    item = rules.model_item(rec, True, decided)
    assert (item["catalogue"], item["final_class"], item["place_list"]) == (
        "CanWIN", "bc_only", ["Maple Ridge; British Columbia; Canada"])
    first, second = item["names"]
    assert first["decision"] == "pick" and "candidates" not in first
    assert [c["geonameid"] for c in second["candidates"]] == [1, 2, 3]
    assert second["candidates"][1]["kind"] == "in_bc"


@pytest.mark.parametrize("feature, large, reason", [
    (("H", "STRT"), True, "ocean_or_sea"), (("T", "ISLS"), True, "too_broad"), (("A", "ADM2"), True, "region"),
    (("H", "SEA"), True, "ocean_or_sea"), (("A", "ADM1"), True, "too_broad"), (("L", "RGN"), True, "region"),
    (("H", "CHN"), False, None), (("T", "ISL"), False, None), (("H", "LK"), False, None), (("P", "PPL"), False, None),
])
def test_straits_island_chains_and_regional_districts_are_large_areas(feature, large, reason):
    place = candidate(1, "x", feature=feature)
    assert rules.is_large(place) is large
    if large:
        assert rules.large_reason(place) == reason


def test_juan_de_fuca_strait_is_no_longer_picked_from_the_place_list():
    found = lookup([candidate(1, "Juan de Fuca Strait", feature=("H", "STRT"))], ["spans_bc_border"])
    result = decide(record(places=["Juan de Fuca Strait; Canada"]), name("Juan de Fuca Strait"), found)
    assert (result["decision"], result["rule"], result["reason"]) == ("do_not_draw", "record_place_list", "too_broad")


class FakeTable:
    """A stand-in for GeoNamesLookup with a few table rows, for the contains search."""

    def __init__(self, rows):
        """Keep the rows and the searches asked."""
        self.rows, self.asked = rows, []

    def rows_where(self, condition, parameters):
        """Return the rows whose normalised name holds the asked words as whole words."""
        self.asked.append(parameters[0])
        return [dict(row) for row in self.rows if parameters[0] in f" {row['norm']} "]

    def place_kind(self, place):
        """BC (admin1 02 in Canada) or outside."""
        return "in_bc" if (place["country_code"], place["admin1_code"]) == ("CA", "02") else "outside_bc"

    def candidate_record(self, place):
        """Return the place as a candidate, with the fields the rules read."""
        return {"geonameid": place["geonameid"], "main_name": place["main_name"], "matched_name": place["name"],
                "country_code": place["country_code"], "admin1_code": place["admin1_code"],
                "feature_class": place["feature_class"], "feature_code": place["feature_code"],
                "latitude": 52.0, "longitude": -131.0}


def table_row(geonameid, main_name, country="CA", admin1="02", feature=("L", "RES"), population=0):
    """Return one made-up row of the lookup table."""
    return {"norm": normalise(main_name), "name": main_name, "main_name": main_name, "name_type": "main",
            "language": "", "is_historic": False, "geonameid": geonameid, "feature_class": feature[0],
            "feature_code": feature[1], "country_code": country, "admin1_code": admin1, "population": population}


def test_a_name_geonames_lacks_goes_to_the_model_with_the_places_whose_name_holds_its_words():
    table = FakeTable([table_row(1, "Gwaii Haanas National Park Reserve and Haida Heritage Site"),
                       table_row(2, "Haanas Gwaii Creek"), table_row(3, "Gwaii Haanasville", "US", "WA")])
    norms = rules.contains_norms(name("Gwaii Haanas"))
    wider = rules.contains_lookup(table, norms)
    assert table.asked == [" gwaii haanas "]
    assert [c["geonameid"] for c in wider["candidates"]] == [1]
    assert (wider["summary"]["match"], wider["summary"]["candidate_kinds"]) == ("contains", ["in_bc"])
    declined = decide(record(title="Black Oystercatcher - Gwaii Haanas"), name("Gwaii Haanas"), lookup([], [], match="none"))
    result = rules.widened(declined, wider)
    assert (result["decision"], result["rule"], result["reason"]) == ("model", "model_contains", None)
    assert result["quote"] == "Black Oystercatcher - Gwaii Haanas"
    # Nothing found, or a decision that is not a decline for want of a match: unchanged.
    assert rules.widened(declined, {"candidates": []}) is declined
    affiliation = decide(record(), name("Gwaii Haanas", roles=["affiliation"]), lookup([], [], match="none"))
    assert rules.widened(affiliation, wider) is affiliation


def test_the_contains_search_skips_short_or_generic_names():
    assert rules.contains_norms(name("Nass")) == []
    assert rules.contains_norms(name("Western provinces")) == []
    assert rules.contains_norms(name("Bush Harbour", "Bush Harbour, Kinbasket")) == ["bush harbour"]
