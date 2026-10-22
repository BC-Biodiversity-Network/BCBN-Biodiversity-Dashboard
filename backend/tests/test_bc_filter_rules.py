"""Tests for the Lunaris BC filter's rules (pipeline/lunaris/bc_filter_rules.py)
and how build_bc_filter_rules.py picks the records: repairing boxes (swapped
edges, the 180 degree line, polar and zero-size boxes), the box classes and
the "huge" cut-off, the BC text matching (including years, BCE and the
Vancouvers that are not in BC), the two CSV lists, how the rules combine
into one class per record (only solid evidence decides; the place field
and the text are only written out as a cross-check), and which records
count as biodiversity data from the LLM run's output.

Most tests use a made-up BC: a triangle whose bounding rectangle is about
twice its area, as with the real BC, so no boundary file is needed. One test
uses the real outline when it is on disk and is skipped otherwise."""

import os
import sys
from pathlib import Path

import pytest
from shapely.geometry import Polygon

BACKEND = Path(__file__).resolve().parents[1]
# The pipeline scripts are not a package; add their folder to the path the
# way the scripts themselves do.
sys.path.insert(0, str(BACKEND / "pipeline" / "lunaris"))
import bc_filter_rules as rules_module  # noqa: E402
import build_bc_filter_rules as build  # noqa: E402
from bc_filter_rules import (  # noqa: E402
    BcShape,
    Rules,
    bc_mentions,
    classify_box,
    classify_points,
    classify_record,
    read_bc_places,
    read_sources,
    repair_box,
)

REAL_BOUNDARY = Path("/Users/lucia/Desktop/BCBN/Results/bc_boundary_gbif.wkt")

# The made-up BC: a triangle from (-139, 60) to (-114, 60) down to (-126.5, 48).
TOY_BC = BcShape(Polygon([(-139, 60), (-114, 60), (-126.5, 48)]))
PLACES = read_bc_places()
SOURCES = read_sources()
TOY_RULES = Rules(TOY_BC, PLACES, SOURCES)


def box(w, s, e, n):
    """Return a box in the form the harvest stores it."""
    return {"w": w, "s": s, "e": e, "n": n}


def record(boxes=None, title="", subjects=None, abstract="", places=None, points=None,
           record_id="oai:datadryad.org:doi:10.5061/test"):
    """Return a record in the form the harvest stores it, with sensible blanks."""
    return {"id": record_id, "title": title, "subjects": subjects or [], "abstract": abstract,
            "places": places, "points": points, "boxes": boxes}


# --- Repairing boxes -------------------------------------------------------

def test_swapped_south_and_north_are_put_back():
    """A box with south above north is the same box with the two swapped."""
    (fixed,) = repair_box(-130, 56, -125, 52)
    assert fixed.bounds == (-130, 52, -125, 56)
    assert classify_box(box(-130, 56, -125, 52), TOY_BC) == classify_box(box(-130, 52, -125, 56), TOY_BC)


def test_swapped_west_and_east_are_put_back():
    """West greater than east, both in the western half of the globe: swapped edges."""
    (fixed,) = repair_box(-125, 52, -130, 56)
    assert fixed.bounds == (-130, 52, -125, 56)
    assert classify_box(box(-125, 52, -130, 56), TOY_BC)[0] == "inside"


def test_a_box_across_the_180_degree_line_is_split_in_two():
    """A box from 170 E to 170 W covers 20 degrees, not the other 340."""
    pieces = repair_box(170, 50, -170, 60)
    assert sorted(p.bounds for p in pieces) == [(-180, 50, -170, 60), (170, 50, 180, 60)]
    kind, size = classify_box(box(170, 50, -170, 60), TOY_BC)
    # Read as a box from 170 W to 170 E it would span almost the whole globe
    # and be "huge"; split correctly it is a small box far from BC.
    assert kind == "outside"
    assert size < rules_module.HUGE_TIMES_BC


def test_a_box_beyond_85_degrees_is_outside():
    """A box at the North Pole is outside, not dropped for having no measurable area."""
    assert classify_box(box(-180, 89.99, 180, 90), TOY_BC)[0] == "outside"


def test_a_box_with_no_area_is_checked_as_a_point():
    """Zero-size boxes and lines become a point at their centre."""
    assert classify_box(box(-126, 55, -126, 55), TOY_BC) == (None, None)
    assert classify_points(None, [box(-126, 55, -126, 55)], TOY_BC) == "inside"
    assert classify_points(None, [box(-64.6, 44.5, -64.6, 44.5)], TOY_BC) == "outside"
    # A line along the 90 degree line is the North Pole.
    assert classify_points(None, [box(-180, 90, 180, 90)], TOY_BC) == "outside"


# --- Box classes and the huge cut-off --------------------------------------

def test_box_classes():
    """One box for each class, against the made-up BC."""
    assert classify_box(box(-128, 56, -125, 58), TOY_BC)[0] == "inside"
    # BC's own bounding rectangle: about half of it is BC, and it covers all of BC.
    assert classify_box(box(-139, 48, -114, 60), TOY_BC)[0] == "bc_wide"
    assert classify_box(box(-130, 55, -122, 61), TOY_BC)[0] == "mostly_bc"
    # Half in BC, half in a neighbour to the east, with less than half of
    # the box in BC: still a partly BC box.
    assert classify_box(box(-122, 54, -108, 60), TOY_BC)[0] == "partly_bc"
    assert classify_box(box(-100, 45, -90, 50), TOY_BC)[0] == "outside"
    # Canada's own extent, as many national datasets give it.
    assert classify_box(box(-141.003, 41.6755, -52.6174, 83.1139), TOY_BC)[0] == "huge"


def test_a_box_that_only_touches_bc_is_outside():
    """A neighbour's box sharing a thin strip with BC's (slightly wider) outline is not BC."""
    assert classify_box(box(-114.2, 59.0, -100, 70), TOY_BC)[0] == "outside"


@pytest.mark.parametrize("margin", [0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 25, 30])
def test_huge_means_five_times_bc_and_nothing_else(margin):
    """Boxes around BC grow step by step; each is huge exactly when it is 5 times BC or more."""
    kind, size = classify_box(box(-139 - margin, 48 - margin / 2, -114 + margin, 60 + margin / 2), TOY_BC)
    assert (kind == "huge") == (size >= rules_module.HUGE_TIMES_BC)


def test_the_huge_cut_off_falls_between_the_growing_boxes():
    """Among the growing boxes there are both huge and not huge ones, so the test above means something."""
    kinds = {classify_box(box(-139 - m, 48 - m / 2, -114 + m, 60 + m / 2), TOY_BC)[0] for m in [0, 30]}
    assert kinds == {"bc_wide", "huge"}


# --- BC text matching ------------------------------------------------------

@pytest.mark.parametrize("text", [
    "Fish surveys in British Columbia",
    "Inventaire en Colombie-Britannique",
    "Salmon of Victoria, B.C.",
    "Données de la C.-B.",
    "BC Fish Observations",
    "Seabird colonies of Haida Gwaii",
    "Trapping near Fort St. John",
    "Urban trees in Vancouver",
    "Kelp on Vancouver Island",
    "Keywords: BC ; salmon",
])
def test_bc_is_found(text):
    """Each of these mentions BC."""
    assert bc_mentions(text, PLACES)


@pytest.mark.parametrize("text", [
    "Settlement dated to 300 BC",
    "Pottery from 300 B.C.",
    "Calendar notation AD/BC",
    "Sediment from 1200 BCE",
    "Bones from the BCE era",
    "The ABC transporter family",
    "BCG vaccine trial",
    "Removal of PBCs from soil",
    "Wetlands near Vancouver, Washington",
    "Parks of Vancouver, WA",
    "Stormwater in Vancouver, Wash.",
    "Ponds of Vancouver (WA)",
    "Archaeology at Fort Vancouver",
    "Water quality of Vancouver Lake",
    "The voyages of George Vancouver",
    "Grasslands of Victoria, Australia",
    "Flows of the Peace River",
    "A golden eagle nest",
])
def test_bc_is_not_found(text):
    """None of these is about BC: years, other words containing BC, and shared place names."""
    assert bc_mentions(text, PLACES) == []


def test_the_place_list_keeps_shared_names_out():
    """Every name marked "no" in bc_places.csv never matches, and every "yes" name does."""
    import csv
    with open(rules_module.PLACES_CSV, newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        found = bc_mentions(f"Data from {row['name']} in 2020", PLACES)
        if row["use"] == "yes":
            assert found, row["name"]
        if row["use"] == "no":
            assert not found, row["name"]


# --- The catalogue list ----------------------------------------------------

def test_the_source_list():
    """BC-only and other-province catalogues are read with the right class; others are not listed."""
    assert SOURCES["catalogue.data.gov.bc.ca"] == "bc"
    assert SOURCES["catalogue.hakai.org"] == "bc"
    assert SOURCES["data.ontario.ca"] == "not_bc"
    assert SOURCES["open.alberta.ca"] == "not_bc"
    # Repositories that hold data from anywhere are in neither list.
    for source in ["datadryad.org", "https-zenodo.org", "open.canada.ca", "borealisdata.ca-dataverse-ubc"]:
        assert source not in SOURCES
    assert set(SOURCES.values()) == {"bc", "not_bc"}


def test_a_bad_source_list_is_refused(tmp_path):
    """A wrong class or a source listed twice stops the run instead of being used."""
    wrong = tmp_path / "wrong.csv"
    wrong.write_text("source,class,note\nexample.org,maybe,\n")
    with pytest.raises(ValueError, match="must be bc or not_bc"):
        read_sources(wrong)
    twice = tmp_path / "twice.csv"
    twice.write_text("source,class,note\nexample.org,bc,\nexample.org,not_bc,\n")
    with pytest.raises(ValueError, match="more than once"):
        read_sources(twice)


# --- How the rules combine: only solid evidence decides --------------------

@pytest.mark.parametrize("boxes, expected, note", [
    ([box(-128, 56, -125, 58)], "bc", "box inside BC"),
    ([box(-139, 48, -114, 60)], "bc", "box covers BC"),
    ([box(-100, 45, -90, 50)], "not_bc", "box outside BC"),
    ([box(-114.2, 59.0, -100, 70)], "not_bc", "box outside BC"),   # only touching BC's edge
])
def test_boxes_that_decide(boxes, expected, note):
    """Inside BC, covering BC, and outside (or only touching) decide on their own."""
    result = classify_record(record(boxes=boxes), TOY_RULES)
    assert (result["bc_class"], result["decided_by"], result["evidence"]) == (expected, "box", note)


@pytest.mark.parametrize("boxes, box_class", [
    ([box(-130, 55, -122, 61)], "mostly_bc"),
    ([box(-122, 54, -108, 60)], "partly_bc"),
    ([box(-141, 41.7, -52.6, 83.1)], "huge"),
])
def test_boxes_that_do_not_decide(boxes, box_class):
    """Mostly-BC, partly-BC and huge boxes go to the geography prompt, with the box noted."""
    result = classify_record(record(boxes=boxes), TOY_RULES)
    assert (result["bc_class"], result["decided_by"], result["box_class"]) == ("needs_llm", "none", box_class)
    assert result["evidence"].endswith("(does not decide)")


def test_a_box_that_does_not_decide_leaves_the_decision_to_one_that_does():
    """A huge box next to a box inside BC: the inside box decides."""
    result = classify_record(record(boxes=[box(-141, 41.7, -52.6, 83.1), box(-128, 56, -125, 58)]), TOY_RULES)
    assert (result["bc_class"], result["decided_by"]) == ("bc", "box")


def test_points():
    """A point in BC is bc, a point outside is not_bc."""
    inside = classify_record(record(points=[{"lon": -126, "lat": 55}]), TOY_RULES)
    assert (inside["bc_class"], inside["evidence"]) == ("bc", "point in BC")
    outside = classify_record(record(points=[{"lon": -79, "lat": 44}]), TOY_RULES)
    assert (outside["bc_class"], outside["evidence"]) == ("not_bc", "point outside BC")


def test_a_point_with_a_longitude_sign_typo_decides_nothing():
    """126 E is in China, but -126 is in BC: most likely a typo, so the prompt reads it."""
    result = classify_record(record(points=[{"lon": 126, "lat": 55}]), TOY_RULES)
    assert (result["bc_class"], result["point_class"]) == ("needs_llm", "sign_typo")
    assert "longitude sign flipped" in result["evidence"]


def test_the_catalogue_decides_on_its_own():
    """A BC-only catalogue is bc, another province's is not_bc."""
    bc = classify_record(record(record_id="oai:catalogue.data.gov.bc.ca:x"), TOY_RULES)
    assert (bc["bc_class"], bc["decided_by"]) == ("bc", "source")
    other = classify_record(record(record_id="oai:data.ontario.ca:x"), TOY_RULES)
    assert (other["bc_class"], other["decided_by"]) == ("not_bc", "source")


@pytest.mark.parametrize("record_id", ["oai:geoportal.gov.mb.ca:x", "oai:opendata.newwestcity.ca:x"])
def test_the_catalogue_alone_does_not_decide_for_a_huge_box(record_id):
    """A national-size box on a provincial or town portal: the geography prompt reads it."""
    result = classify_record(record(record_id=record_id, boxes=[box(-141, 41.7, -52.6, 83.1)]), TOY_RULES)
    assert (result["bc_class"], result["decided_by"]) == ("needs_llm", "none")
    assert result["evidence"].startswith("huge box (5x BC or more) (does not decide); source: ")


def test_a_huge_box_does_not_stop_a_box_or_point_from_deciding():
    """With a box inside BC as well, the record is decided as before, catalogue included."""
    result = classify_record(record(record_id="oai:catalogue.hakai.org:x",
                                    boxes=[box(-141, 41.7, -52.6, 83.1), box(-128, 56, -125, 58)]), TOY_RULES)
    assert (result["bc_class"], result["decided_by"]) == ("bc", "box+source")


def test_agreeing_rules_are_listed_together():
    """A box and a point in BC, from a BC-only catalogue: all three agree."""
    result = classify_record(record(record_id="oai:catalogue.hakai.org:x", boxes=[box(-128, 56, -125, 58)],
                                    points=[{"lon": -126, "lat": 55}]), TOY_RULES)
    assert (result["bc_class"], result["decided_by"]) == ("bc", "box+point+source")


@pytest.mark.parametrize("fields", [
    {"record_id": "oai:open.alberta.ca:x", "boxes": [box(-128, 56, -125, 58)]},          # box in BC, Alberta catalogue
    {"record_id": "oai:catalogue.data.gov.bc.ca:x", "boxes": [box(-100, 45, -90, 50)]},  # BC catalogue, box outside
    {"boxes": [box(-128, 56, -125, 58), box(-100, 45, -90, 50)]},                        # one box in, one out
    {"points": [{"lon": -126, "lat": 55}, {"lon": -79, "lat": 44}]},                     # one point in, one out
])
def test_disagreeing_rules_need_the_llm(fields):
    """When the deciding rules disagree, the geography prompt reads the record."""
    result = classify_record(record(**fields), TOY_RULES)
    assert result["bc_class"] == "needs_llm"
    assert result["evidence"].startswith("disagree: ")


@pytest.mark.parametrize("fields, hint", [
    ({"places": ["Haida Gwaii; British Columbia"]}, "bc"),
    ({"places": ["Guelph; Ontario; Canada"]}, "elsewhere"),
    ({"places": ["Canada"]}, "canada"),
])
def test_the_place_field_decides_nothing(fields, hint):
    """Whatever the place field says, it is only written out for the cross-check."""
    result = classify_record(record(**fields), TOY_RULES)
    assert (result["bc_class"], result["decided_by"], result["place_hint"]) == ("needs_llm", "none", hint)


def test_the_text_decides_nothing():
    """BC in the title, keywords and abstract is written out for the cross-check, and decides nothing."""
    result = classify_record(record(title="Kelp of Haida Gwaii", subjects=["BC"],
                                    abstract="Collected near Tofino, British Columbia.",
                                    boxes=[box(-141, 41.7, -52.6, 83.1)]), TOY_RULES)
    assert result["bc_class"] == "needs_llm"
    assert result["text_title"] == "Haida Gwaii"
    assert result["text_keywords"] == "BC"
    assert set(result["text_abstract"].split(", ")) == {"British Columbia", "Tofino"}


def test_the_box_cross_check_columns():
    """The most BC box's share, cover and size are written out, rounded."""
    result = classify_record(record(boxes=[box(-122, 54, -108, 60)]), TOY_RULES)
    assert result["box_class"] == "partly_bc"
    assert 0 < result["box_share"] < 0.5
    assert result["box_size"] == round(result["box_size"], 4)


def test_nothing_to_go_on_needs_the_llm():
    """No location, no listed catalogue: the geography prompt decides."""
    result = classify_record(record(title="Metabolic rate of a teleost fish"), TOY_RULES)
    assert (result["bc_class"], result["decided_by"], result["evidence"]) == (
        "needs_llm", "none", "nothing that decides")


# --- The real outline, when it is on disk ----------------------------------

@pytest.mark.skipif(not REAL_BOUNDARY.exists(), reason="bc_boundary_gbif.wkt is not on this machine")
def test_with_the_real_bc_outline():
    """The commonest boxes in the harvest land where the investigation put them."""
    real = rules_module.load_bc_shape(REAL_BOUNDARY)
    assert classify_box(box(-139.5, 48.0, -113.5, 60.0), real)[0] == "bc_wide"        # BC's bounding box
    assert classify_box(box(-141.003, 41.6755, -52.6174, 83.1139), real)[0] == "huge"  # Canada
    assert classify_box(box(-141.0, 60.0, -123.8, 69.7), real)[0] == "outside"        # Yukon, touching BC
    assert classify_box(box(-95.15, 41.67, -74.3, 56.85), real)[0] == "outside"       # Ontario


# --- Which records count as biodiversity data ------------------------------

def llm_row(record_id, topic="organisms", living=True, error=None):
    """Return one row of an LLM run's merged CSV, with facts decide() reads."""
    if error:
        return {"record_id": record_id, "model_error": error}
    return {"record_id": record_id, "model": "qwen3.5:4b-fir", "concerns_living_things": living,
            "organisms": "", "organism_level": "none", "topic": topic,
            "about_organisms_themselves": "not_applicable", "ecological_purpose_stated": "not_applicable",
            "experimental_animals": "no", "extinct_only": "no", "form": "dataset", "language": "english",
            "model_reason": "", "model_error": None}


def write_llm_results(path, rows):
    """Write rows as the merged CSV merge_results.py would, and return the path."""
    import pandas as pd
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")
    return path


def test_biodiversity_ids_apply_decide_and_keep_listed_failures(tmp_path):
    """Answered records go through decide(); a failed record counts only if it is listed."""
    path = write_llm_results(tmp_path / "trial.csv", [
        llm_row("a"),                                  # organisms: biodiversity
        llm_row("b", topic="administrative"),          # government record: not
        llm_row("c", topic="physical_environment"),    # physical measurements: not
        llm_row("d", living=False),                    # nothing living: not
        llm_row("e", error="reply was not valid JSON"),
    ])
    ids, answered_yes, failed_kept = build.biodiversity_ids(path, failed_ids=["e"])
    assert (ids, answered_yes, failed_kept) == ({"a", "e"}, 1, 1)


def test_an_unlisted_failed_record_stops_the_run(tmp_path):
    """A failed record nobody has looked at is never kept or dropped silently."""
    path = write_llm_results(tmp_path / "trial.csv", [llm_row("a"), llm_row("z", error="timeout")])
    with pytest.raises(SystemExit, match="not in FAILED_IN_FULL_RUN"):
        build.biodiversity_ids(path, failed_ids=["e"])


def test_the_listed_failed_records_are_the_three_from_the_full_run():
    """The hand-kept list holds exactly the 3 records whose answer failed on 2026-10-20."""
    assert sorted(build.FAILED_IN_FULL_RUN) == sorted([
        "oai:datadryad.org:doi:10.5061/dryad.dm690",
        "oai:borealisdata.ca-dataverse-ubc:152582_150149",
        "oai:open.canada.ca:219b17c5-0f12-437b-ab82-7f3cfb75ad8e",
    ])
