"""Tests for pass 1 of the geography step: the record text the model is
shown (pipeline/lunaris/llm/geo_record_text.py, used by geo_prompt_v3.py),
the answer check and clean-up in geo_prompt_v3.py, and the code checks in
geo_answer_checks.py.

The record text: every field is given exactly as harvested, empty fields are
left out, every explanation of ours is on its own line starting "Note from
us", boxes and points are given as their raw numbers with our measurements
as notes, and swapped boxes, boxes across the 180 degree line, boxes with no
area and probable missing minus signs get their own notes.

The checks: a place whose as_written is not in the record's own text (the
record without our notes) is dropped, duplicates are removed, and a name
that is neither in the record nor a plain expansion of its as_written is
listed for a person to look at.

They use a made-up BC (a triangle), as the BC filter's tests do, so no
boundary file is needed."""

import sys
from pathlib import Path

import pytest
from shapely.geometry import Polygon

BACKEND = Path(__file__).resolve().parents[1]
# The pipeline scripts are not a package; add their folders to the path the
# way the scripts themselves do.
sys.path.insert(0, str(BACKEND / "pipeline" / "lunaris"))
sys.path.insert(0, str(BACKEND / "pipeline" / "lunaris" / "llm"))
import geo_prompt_v3  # noqa: E402
from bc_filter_rules import BcShape  # noqa: E402
from geo_answer_checks import (  # noqa: E402
    drop_duplicates,
    is_expansion,
    is_short_form,
    name_needs_a_look,
    own_text,
    squash,
    written_in_record,
)
from geo_record_text import record_text  # noqa: E402

# A made-up BC: a triangle from (-139, 60) and (-114, 60) down to (-126.5, 48).
TOY_BC = BcShape(Polygon([(-139, 60), (-114, 60), (-126.5, 48)]))

FIELD_STARTS = ("Title: ", "Keywords: ", "Abstract: ", "Place-name field: ", "Publisher: ",
                "Record identifier: ", "Bounding box ", "Point ")


def record(**fields):
    """A harvested record with every field empty except those given."""
    base = {"id": "oai:example.org:1", "title": None, "subjects": None, "abstract": None, "places": None,
            "publisher": None, "points": None, "boxes": None}
    base.update(fields)
    return base


def record_lines(text):
    """The lines of a record text after "Record to read:" and the blank line."""
    lines = text.splitlines()
    assert lines[:2] == ["Record to read:", ""]
    return lines[2:]


# --- The record text -----------------------------------------------------

def test_fields_are_given_exactly_as_harvested():
    title = "  Étude  du saumon,  C.-B. (1990s)  "
    text = record_text(record(title=title, subjects=["Pacific salmon", "Colombie-Britannique", "L. Examplia"],
                              abstract="Line one.\nLine  two, with  spaces.", places=["Fakeville; B.C.", "Mt. X"],
                              publisher="University of Fakeville Dataverse"), TOY_BC)
    assert f"Title: {title}" in text
    assert "Keywords: Pacific salmon; Colombie-Britannique; L. Examplia" in text
    assert "Abstract: Line one.\nLine  two, with  spaces." in text
    assert "Place-name field: Fakeville; B.C.; Mt. X" in text
    assert "Publisher: University of Fakeville Dataverse" in text
    assert "Record identifier: oai:example.org:1" in text


def test_empty_fields_are_left_out():
    lines = record_lines(record_text(record(title="Only a title", abstract="", subjects=[]), TOY_BC))
    assert not any(line.startswith(("Abstract:", "Keywords:", "Place-name field:", "Publisher:",
                                    "Bounding box", "Point")) for line in lines)


def test_every_line_is_a_field_or_a_marked_note():
    text = record_text(record(title="T", subjects=["k"], abstract="A", places=["P"], publisher="Pub",
                              boxes=[{"w": -128.0, "e": -125.0, "s": 55.0, "n": 58.0}],
                              points=[{"lon": -126.5, "lat": 55.0}]), TOY_BC)
    for line in record_lines(text):
        is_note = line.startswith("Note from us")
        assert is_note != line.startswith(FIELD_STARTS), line
        if not is_note:
            assert "Note from us" not in line
    # The notes on the place-name field and the publisher say what those fields are.
    assert "Note from us: this is the record's place-name field" in text
    assert "Note from us: this is the record's publisher field" in text


def test_box_is_given_as_its_raw_numbers_with_our_measurement_as_a_note():
    text = record_text(record(boxes=[{"w": -128.0, "e": -125.25, "s": 55.0, "n": 58.0}]), TOY_BC)
    assert ("Bounding box 1: westBoundLongitude -128.0, eastBoundLongitude -125.25, "
            "southBoundLatitude 55.0, northBoundLatitude 58.0") in text
    assert ("Note from us (our own calculation, not part of the record): 100% of this box lies in "
            "British Columbia") in text
    # A coordinate-format note comes once, before the boxes.
    assert text.count("Note from us: a bounding box is four numbers") == 1


def test_box_outside_bc():
    text = record_text(record(boxes=[{"w": -80.0, "e": -75.0, "s": 40.0, "n": 45.0}]), TOY_BC)
    assert "0% of this box lies in British Columbia, and it covers 0% of British Columbia" in text


def test_swapped_latitudes_get_a_note_and_are_not_corrected():
    text = record_text(record(boxes=[{"w": -128.0, "e": -125.0, "s": 58.0, "n": 55.0}]), TOY_BC)
    assert "southBoundLatitude 58.0, northBoundLatitude 55.0" in text
    assert "the south latitude is greater than the north latitude, so the two look swapped" in text


def test_swapped_longitudes_get_a_note():
    text = record_text(record(boxes=[{"w": -125.0, "e": -128.0, "s": 55.0, "n": 58.0}]), TOY_BC)
    assert "westBoundLongitude -125.0, eastBoundLongitude -128.0" in text
    assert "the west longitude is greater than the east longitude, so the two look swapped" in text


def test_box_across_the_180_degree_line_gets_its_own_note():
    text = record_text(record(boxes=[{"w": 170.0, "e": -170.0, "s": 50.0, "n": 55.0}]), TOY_BC)
    assert "read as recorded, the box crosses the 180 degree line" in text
    assert "look swapped" not in text


def test_box_with_no_area():
    text = record_text(record(boxes=[{"w": -126.5, "e": -126.5, "s": 55.0, "n": 55.0}]), TOY_BC)
    assert "this box has no area; it is a single point or a line" in text


def test_points_inside_outside_and_with_a_missing_minus_sign():
    text = record_text(record(points=[{"lon": -126.5, "lat": 55.0}, {"lon": -100.0, "lat": 55.0},
                                      {"lon": 126.5, "lat": 55.0}]), TOY_BC)
    assert "Point 1: pointLongitude -126.5, pointLatitude 55.0" in text
    assert "Point 3: pointLongitude 126.5, pointLatitude 55.0" in text      # shown as recorded
    notes = [line for line in text.splitlines() if line.startswith("Note from us (our own calculation")]
    assert notes[0].endswith("this point falls inside British Columbia.")
    assert notes[1].endswith("this point falls outside British Columbia.")
    assert "the minus sign is probably missing. The number is shown as recorded." in notes[2]


def test_build_prompt_is_the_instructions_then_the_record():
    rec = record(title="Lake Fakeville survey")
    prompt = geo_prompt_v3.build_prompt(rec, TOY_BC)
    assert prompt == geo_prompt_v3.INSTRUCTIONS + "\n\n" + record_text(rec, TOY_BC)


def test_instruction_examples_are_made_up():
    # Every example place is made up; real places appear only in the BC reading rule.
    for made_up in ("Lake Examplia", "Mt. Placeholder", "Rivière Imaginaire", "University of Fakeville"):
        assert made_up in geo_prompt_v3.INSTRUCTIONS
    for real_example in ("Guelph", "Winnipeg", "Simcoe", "Kalamalka", "Ontario", "Newfoundland"):
        assert real_example not in geo_prompt_v3.INSTRUCTIONS


# --- Checking and cleaning an answer -------------------------------------

PROMPT = geo_prompt_v3.build_prompt(
    record(title="Salmon in Lake Fakeville, B.C.", subjects=["Mt. Placeholder"],
           abstract="Sampled near the Rivière Imaginaire.", publisher="University of Fakeville",
           points=[{"lon": -126.5, "lat": 55.0}]), TOY_BC)


def place(as_written, name=None, role="study_location"):
    """One place in a version 3 answer."""
    return {"as_written": as_written, "name": name or as_written, "role": role}


def test_check_answer():
    good = {"places": [place("Lake Fakeville")]}
    assert geo_prompt_v3.check_answer(good) is None
    assert geo_prompt_v3.check_answer({"places": []}) is None
    assert geo_prompt_v3.check_answer([]) is not None
    assert geo_prompt_v3.check_answer({"places": [], "extra": 1}) is not None
    assert geo_prompt_v3.check_answer({"places": [dict(place("X"), role="capital")]}) is not None
    assert geo_prompt_v3.check_answer({"places": [{"as_written": "X", "role": "other"}]}) is not None


def test_clean_places_drops_names_not_in_the_record():
    places = [place("Lake Fakeville"), place("British Columbia"),        # from the instructions or our notes
              place("lake  FAKEVILLE", "Lake Fakeville", "other")]       # case and spacing do not matter
    kept, dropped = geo_prompt_v3.clean_places(places, PROMPT)
    assert [p["as_written"] for p in dropped] == ["British Columbia"]
    assert [p["as_written"] for p in kept] == ["Lake Fakeville", "lake  FAKEVILLE"]


def test_clean_places_removes_duplicates_per_name_and_role():
    places = [place("Lake Fakeville"), place("Lake Fakeville", "lake fakeville"),
              place("University of Fakeville", "Fakeville", "affiliation"),
              place("Lake Fakeville", "Lake Fakeville", "affiliation")]
    kept, dropped = geo_prompt_v3.clean_places(places, PROMPT)
    assert dropped == []
    assert [(p["name"], p["role"]) for p in kept] == [
        ("Lake Fakeville", "study_location"), ("Fakeville", "affiliation"), ("Lake Fakeville", "affiliation")]


def test_drop_duplicates_keeps_the_first_and_the_order():
    places = [{"standard_name": "A", "role": "other", "n": 1}, {"standard_name": "b", "role": "other", "n": 2},
              {"standard_name": " a ", "role": "other", "n": 3}]
    kept = drop_duplicates(places, lambda p: p["standard_name"])
    assert [p["n"] for p in kept] == [1, 2]


# --- The code checks -----------------------------------------------------

def test_own_text_leaves_out_the_instructions_and_our_notes():
    text = own_text(PROMPT)
    assert "Lake Examplia" not in text                   # an example in the instructions
    assert "Note from us" not in text
    assert "falls inside British Columbia" not in text   # our note on the point
    assert "Salmon in Lake Fakeville, B.C." in text


def test_written_in_record():
    assert written_in_record("lake fakeville", PROMPT)
    assert written_in_record("Lake\n  Fakeville", PROMPT)
    assert not written_in_record("British Columbia", PROMPT)       # only in our notes
    assert not written_in_record("Lake Examplia", PROMPT)          # only in the instructions
    assert squash("  A \n B ") == "a b"


@pytest.mark.parametrize("short, word, expected", [
    ("Mt.", "Mount", True), ("Pk", "Park", True), ("Prov.", "Provincial", True), ("L.", "Lake", True),
    ("St", "Saint", True), ("Mt", "Lake", False), ("Pkx", "Park", False), ("", "Park", False),
])
def test_is_short_form(short, word, expected):
    assert is_short_form(short, word) == expected


def test_is_expansion():
    assert is_expansion("L. Examplia", "Lake Examplia")
    assert is_expansion("Mt. Placeholder", "Mount Placeholder")
    assert is_expansion("Fakeville", "Fakeville")
    assert not is_expansion("Placeholder", "Mount Placeholder")      # a word added, not expanded
    assert not is_expansion("Rivière Imaginaire", "Imaginaire River")


@pytest.mark.parametrize("as_written, name, needs_a_look", [
    ("Lake Fakeville", "Lake Fakeville", False),        # the name is in the record
    ("University of Fakeville", "Fakeville", False),    # part of the record's text
    ("Mt. Placeholder", "Mount Placeholder", False),    # an abbreviation spelled out
    ("B.C.", "British Columbia", False),                # the BC reading rule
    ("Rivière Imaginaire", "Imaginaire River", True),   # a translation: code cannot judge it
    ("Lake Fakeville", "Lake Examplia", True),          # a name from the instructions
    ("Lake Fakeville", "", True),                       # an empty name
])
def test_name_needs_a_look(as_written, name, needs_a_look):
    assert name_needs_a_look(place(as_written, name), name, PROMPT) == needs_a_look
