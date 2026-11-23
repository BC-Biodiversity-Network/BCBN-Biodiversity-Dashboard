"""Tests for the geography prompt variants (pipeline/lunaris/llm/geo_prompt_variants.py),
the DataONE record text (geo_record_text.dataone_record_text and
dataone_box_shares) and how geo_filter_run.py and geo_pass2_trial.py use
them.

The Lunaris prompts must not change: the lunaris variant builds exactly
geo_prompt_v3's and geo_pass2_prompt_c4's prompts, their instructions keep
their fingerprints, and the prompts saved in the Lunaris Fir run folders
rebuild byte for byte (where those folders are on this machine). The
dataone variant differs in its instructions only by "Canadian", and its
record text follows Lucia's decisions of 2026-11-23."""

import gzip
import json
import os
import sys
from argparse import Namespace
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
LLM = BACKEND / "pipeline" / "lunaris" / "llm"
sys.path.insert(0, str(LLM))
sys.path.insert(0, str(LLM.parent))
import geo_filter_run  # noqa: E402
import geo_pass1_trial  # noqa: E402
import geo_pass2_prompt_c4  # noqa: E402
import geo_pass2_trial  # noqa: E402
import geo_prompt_v3  # noqa: E402
import geo_prompt_variants as gv  # noqa: E402
import geo_record_text as gt  # noqa: E402
from bc_filter_rules import load_bc_shape  # noqa: E402
from prompt_v6 import prompt_sha1  # noqa: E402

BOUNDARY = Path.home() / "Desktop" / "BCBN" / "Results" / "bc_boundary_gbif.wkt"
RUNS = BACKEND / "data" / "lunaris_bc_filter" / "fir_runs"
# Saved Lunaris runs and the input folders they were made from.
SAVED = [(BACKEND / "data" / "lunaris_bc_filter" / "fir_inputs" / "label150",
          RUNS / "label150_qwen3.6-35b-a3b_nofa"),
         (BACKEND / "data" / "lunaris_llm_runs" / "filter2_35b_compare" / "fir_input_needs_llm",
          RUNS / "needs_llm_new35b_qwen3.6-35b-a3b_nofa")]
# The fingerprints of the Lunaris instructions (unchanged since the Lunaris runs).
LUNARIS_PASS1_SHA1 = "aeb422116caceae01bf4aa2632a07f40c6fdd666"
LUNARIS_PASS2_SHA1 = "25baa4472cdd216f8ac641112c6aa620201c16fb"

pytestmark = pytest.mark.skipif(not BOUNDARY.exists(), reason="bc_boundary_gbif.wkt is not on this machine")


@pytest.fixture(scope="module")
def shape():
    """BC's real outline."""
    return load_bc_shape(str(BOUNDARY))


def dataone(**fields):
    """Return a made-up DataONE record for the record text, with the given fields over a plain default."""
    record = {"id": "doi:10.0000/example", "repository": "PANGAEA", "title": "Fish counts", "subjects": ["fish"],
              "abstract": "Counts of fish.", "named_location": None, "place_key": None, "site": None,
              "boxes": [], "unreadable_boxes": 0}
    record.update(fields)
    return record


def box(w, e, s, n):
    """Return a box as the records hold it."""
    return {"w": w, "e": e, "s": s, "n": n}


LUNARIS_RECORD = {"id": "oai:example.ca:1", "title": "Fish of Examplia", "subjects": ["fish", "Examplia"],
                  "abstract": "Fish counts.", "places": ["Examplia"], "publisher": "Example Agency",
                  "points": [{"lon": -123.1, "lat": 49.25}],
                  "boxes": [box(-125.0, -120.0, 49.0, 52.0), box(170.0, -170.0, 50.0, 60.0)]}
PLACES = [{"as_written": "Examplia", "name": "Examplia", "role": "study_location"}]
LOOKUPS = [{"query": {"name": "Examplia", "as_written": "Examplia"}, "candidates": [],
            "summary": {"category": "not_found", "places_found": 0, "match": "none", "matched_on": "",
                        "plural_removed": False, "direction_word_removed": "", "historic": False, "hand_places": 0,
                        "only_hand": False, "hints": [], "hint_check": "no hint", "candidate_kinds": []}}]


# --- The variants ----------------------------------------------------------

def test_the_lunaris_instructions_keep_their_fingerprints():
    assert prompt_sha1(geo_prompt_v3.INSTRUCTIONS) == LUNARIS_PASS1_SHA1
    assert prompt_sha1(geo_pass2_prompt_c4.INSTRUCTIONS) == LUNARIS_PASS2_SHA1
    assert gv.fingerprints() == {"geo_prompt_variant": "lunaris", "pass1_instructions_sha1": LUNARIS_PASS1_SHA1,
                                 "pass2_instructions_sha1": LUNARIS_PASS2_SHA1}


def test_the_dataone_instructions_differ_only_by_canadian():
    for lunaris, data_one in ((geo_prompt_v3.INSTRUCTIONS, gv.pass1_instructions("dataone")),
                              (geo_pass2_prompt_c4.INSTRUCTIONS, gv.pass2_instructions("dataone"))):
        assert "Canadian" not in data_one.split("\n")[0]
        assert data_one == lunaris.replace("from a Canadian research data catalogue",
                                           "from a research data catalogue")
        assert len(lunaris) - len(data_one) == len("Canadian ")
    assert gv.fingerprints("dataone")["pass1_instructions_sha1"] != LUNARIS_PASS1_SHA1


def test_the_lunaris_variant_builds_exactly_the_lunaris_prompts(shape):
    assert gv.build_pass1_prompt(LUNARIS_RECORD, shape) == geo_prompt_v3.build_prompt(LUNARIS_RECORD, shape)
    assert gv.build_pass2_prompt(LUNARIS_RECORD, shape, PLACES, LOOKUPS) == \
        geo_pass2_prompt_c4.build_prompt(LUNARIS_RECORD, shape, PLACES, LOOKUPS)


def test_the_variant_comes_from_input_json(tmp_path):
    assert gv.variant_of_input({}) == "lunaris" and gv.variant_of_input(None) == "lunaris"
    assert gv.variant_of_input({"geo_prompt_variant": "dataone"}) == "dataone"
    with pytest.raises(ValueError):
        gv.variant_of_input({"geo_prompt_variant": "v4"})
    assert geo_filter_run.read_variant(str(tmp_path)) == "lunaris"
    (tmp_path / "input.json").write_text(json.dumps({"geo_prompt_variant": "dataone"}))
    assert geo_filter_run.read_variant(str(tmp_path)) == "dataone"
    (tmp_path / "input.json").write_text(json.dumps({"geo_prompt_variant": "v4"}))
    with pytest.raises(geo_filter_run.Stop):
        geo_filter_run.read_variant(str(tmp_path))


def test_run_json_records_the_variant_and_never_mixes_them(tmp_path):
    args = Namespace(input="in", num_ctx=16384, num_thread=6)
    geo_filter_run.check_run_json(str(tmp_path), "m", "d", "0.40.0", args, "dataone")
    about = json.loads((tmp_path / "run.json").read_text())
    assert {k: about[k] for k in gv.fingerprints("dataone")} == gv.fingerprints("dataone")
    geo_filter_run.check_run_json(str(tmp_path), "m", "d", "0.40.0", args, "dataone")
    with pytest.raises(geo_filter_run.Stop):
        geo_filter_run.check_run_json(str(tmp_path), "m", "d", "0.40.0", args, "lunaris")
    # A Lunaris run.json, written before the variants, counts as lunaris.
    old = tmp_path / "old"
    old.mkdir()
    (old / "run.json").write_text(json.dumps({"model": "m", "model_digest": "d"}))
    geo_filter_run.check_run_json(str(old), "m", "d", "0.40.0", args, "lunaris")
    with pytest.raises(geo_filter_run.Stop):
        geo_filter_run.check_run_json(str(old), "m", "d", "0.40.0", args, "dataone")


# --- The saved Lunaris prompts rebuild byte for byte -----------------------

def read_inputs(folder):
    """Return an input folder's records by number, as geo_filter_run reads them."""
    with gzip.open(folder / "records.jsonl.gz", "rt", encoding="utf-8") as handle:
        return {item["number"]: item for item in map(json.loads, handle)}


@pytest.mark.parametrize("inputs,run", SAVED, ids=lambda p: p.name)
def test_the_saved_lunaris_prompts_rebuild_byte_for_byte(shape, inputs, run):
    if not (inputs / "records.jsonl.gz").exists() or not (run / "pass2" / "prompts").exists():
        pytest.skip(f"{run.name} or its input folder is not on this machine")
    records = read_inputs(inputs)
    _, places, lookups = geo_pass2_trial.read_pass1(str(run / "pass1"))
    checked = 0
    for number, item in records.items():
        name = geo_filter_run.prompt_name(number)
        text1 = gv.build_pass1_prompt(item["record"], shape, gv.variant_of_input({}))
        assert text1 == (run / "pass1" / "prompts" / name).read_text(encoding="utf-8")
        assert prompt_sha1(text1) == item["pass1_sha1"]
        saved2 = run / "pass2" / "prompts" / name
        if saved2.exists():
            assert gv.build_pass2_prompt(item["record"], shape, places[number], lookups[number]) == \
                saved2.read_text(encoding="utf-8")
            checked += 1
    assert checked > 0.9 * len(records)


# --- The DataONE record text -----------------------------------------------

def notes_are_ours(text):
    """Return True if every note line in a DataONE record text starts with one of our two prefixes."""
    return all(line.startswith((gt.DATAONE_NOTE, gt.FIELD_NOTE)) for line in text.splitlines()
               if line.startswith("Note from us"))


def test_identifier_repository_and_no_oai_note(shape):
    text = gt.dataone_record_text(dataone(), shape)
    assert "oai:" not in text and "Repository: PANGAEA" in text
    assert "Note from us: this is the DataONE member repository the record comes from." in text
    assert "Note from us: this is the identifier the repository gave the record." in text
    assert notes_are_ours(text)


def test_field_notes_are_short_and_calculated_notes_long(shape):
    record = dataone(abstract="Counts of fish and ...", named_location=["Examplia"],
                     boxes=[box(170.0, -170.0, 50.0, 60.0), box(-123.1, -123.1, 49.25, 49.25), box(10.0, 12.0, 45.0, 46.0)])
    lines = gt.dataone_record_text(record, shape).splitlines()
    short = [line for line in lines if line.startswith(gt.FIELD_NOTE)]
    long_ = [line for line in lines if line.startswith(gt.DATAONE_NOTE)]
    assert [line[len(gt.FIELD_NOTE):].split(" ")[:3] for line in short] == [
        ["these", "are", "the"], ["this", "is", "the"], ["this", "is", "the"], ["a", "bounding", "box"],
        ["the", "record", "gives"]]
    for start in ("this abstract ends", "the record has 3 different", "in this box the west",
                  "read as crossing", "read with west", "this point falls inside", "the other 1 boxes"):
        assert any(line[len(gt.DATAONE_NOTE):].startswith(start) for line in long_), start


def test_place_fields_without_site_boilerplate_and_no_authors(shape):
    record = dataone(named_location=["Examplia"], place_key=["Lake Examplia"],
                     site=["No geographic description provided.", "Examplia Bay", "See range references listed in "
                           "Sources", "Dataset geographic description information is unavailable. {x is empty}",
                           "Examplia Bay"], origin=["Doe, Jane"], contactOrganization=["Example Org"])
    text = gt.dataone_record_text(record, shape)
    assert "Named location (namedLocation): Examplia" in text
    assert "Place keywords (placeKey): Lake Examplia" in text
    assert "Site (site): Examplia Bay\n" in text
    assert "geographic description" not in text and "range references" not in text
    assert "Doe" not in text and "Example Org" not in text
    assert gt.dataone_record_text(dataone(site=["Unknown", "NA"]), shape).count("Site") == 0


def test_a_box_with_no_area_is_a_point_with_the_lunaris_point_notes(shape):
    text = gt.dataone_record_text(dataone(boxes=[box(122.57, 122.57, 49.2, 49.2)]), shape)
    assert "Point 1: pointLongitude 122.57, pointLatitude 49.2" in text and "Bounding box" not in text
    assert "the minus sign is probably missing" in text
    line = gt.dataone_record_text(dataone(boxes=[box(-123.0, -123.0, 49.0, 50.0)]), shape)
    assert "Point 1: pointLongitude -123.0, pointLatitude 49.5" in line and "a line from" in line


def test_a_west_greater_than_east_box_gets_both_readings(shape):
    text = gt.dataone_record_text(dataone(boxes=[box(170.0, -170.0, 50.0, 60.0)]), shape)
    assert ("Read as crossing the 180° meridian, the box spans 20° of longitude; read with west and east swapped, "
            "it spans 340°.") in text
    assert gt.DATAONE_NOTE + "read as crossing the 180° meridian, " in text
    assert gt.DATAONE_NOTE + "read with west and east swapped, " in text
    # The two Lunaris notes on west > east are not used.
    assert "look swapped" not in text and "crosses the 180 degree line" not in text
    pass2 = gt.dataone_record_text(dataone(boxes=[box(170.0, -170.0, 50.0, 60.0)]), shape, box_outside=True)
    assert pass2.count("lies outside it") == 2
    assert notes_are_ours(text)


def test_odd_numbers_and_a_box_in_bc_only_with_its_signs_changed(shape):
    text = gt.dataone_record_text(dataone(boxes=[box(-123.0, -122.0, 49.0, 95.0)]), shape)
    assert "latitude and longitude may be swapped" in text
    same = gt.dataone_record_text(dataone(boxes=[box(50.0, 60.0, 50.0, 55.0)]), shape)
    assert "a longitude here equals a latitude (50.0)" in same
    # A longitude and a latitude of 0 are common and get no note.
    zero = gt.dataone_record_text(dataone(boxes=[box(0.0, 143.0, 0.0, 66.0)]), shape)
    assert "equals a latitude" not in zero
    far = gt.dataone_record_text(dataone(boxes=[box(-200.0, -120.0, 49.0, 50.0)]), shape)
    assert gt.DATAONE_NOTE + "a longitude here is beyond 180 degrees east or west, which may be a mistake" in far
    assert "beyond 180" not in same
    flipped = gt.dataone_record_text(dataone(boxes=[box(120.0, 125.0, 49.0, 50.0)]), shape)
    assert "it would if the signs of its longitudes were changed (west -125.0, east -120.0)" in flipped
    in_bc = gt.dataone_record_text(dataone(boxes=[box(-125.0, -120.0, 49.0, 50.0)]), shape)
    assert "signs of its longitudes" not in in_bc


def test_a_cut_abstract_gets_a_note(shape):
    text = gt.dataone_record_text(dataone(abstract="Counts of fish in the bay and ..."), shape)
    assert gt.DATAONE_NOTE + "this abstract ends with \"...\": the repository's copy of the abstract was cut" in text
    assert "was cut" not in gt.dataone_record_text(dataone(abstract="Counts of fish."), shape)


def test_several_boxes_list_those_touching_bc_and_sum_up_the_rest(shape):
    boxes = [box(-125.0, -120.0, 49.0, 50.0), box(-123.1, -123.1, 49.25, 49.25), box(10.0, 12.0, 45.0, 46.0),
             box(20.0, 22.0, 40.0, 41.0), box(10.0, 12.0, 45.0, 46.0), box(115.0, 118.0, 50.0, 51.0)]
    text = gt.dataone_record_text(dataone(boxes=boxes, unreadable_boxes=2), shape)
    assert "the record has 5 different boxes and points; the 2 that touch British Columbia are listed" in text
    assert "Bounding box 1: westBoundLongitude -125.0" in text and "Point 1: pointLongitude -123.1" in text
    assert "westBoundLongitude 10.0," not in text.split("listed below in full.")[1].split("the other")[0]
    assert ("the other 3 boxes and points do not touch British Columbia; together they lie within "
            "westBoundLongitude 10.0, eastBoundLongitude 118.0, southBoundLatitude 40.0, northBoundLatitude 51.0") in text
    assert "1 of them would touch British Columbia if the signs of their longitudes were changed" in text
    assert "2 more boxes in the record's original document could not be read" in text
    assert notes_are_ours(text)


def test_several_boxes_none_touching_bc_say_so_and_keep_the_summary(shape):
    boxes = [box(10.0, 12.0, 45.0, 46.0), box(20.0, 22.0, 40.0, 41.0), box(-124.0, -124.0, 44.0, 44.0)]
    lines = gt.dataone_record_text(dataone(boxes=boxes), shape).splitlines()
    note = gt.DATAONE_NOTE + "the record has 3 different boxes and points; none of them touches British Columbia."
    assert note in lines
    assert not any("listed below in full" in line for line in lines)
    assert not any(line.startswith(("Bounding box", "Point")) for line in lines)
    summary = lines[lines.index(note) + 1]
    assert summary == (gt.DATAONE_NOTE + "the other 3 boxes and points do not touch British Columbia; together they "
                       "lie within westBoundLongitude -124.0, eastBoundLongitude 22.0, southBoundLatitude 40.0, "
                       "northBoundLatitude 46.0 (the smallest box around all their numbers as recorded).")


# --- The pass 2 box rule and the answer checks ---------------------------

PASS2_UNCLEAR = {"reason": "Unclear.", "evidence": ["Fish counts"], "in_bc": "unclear", "other_canada": "no",
                 "outside_canada": "no", "canada_wide": "no", "global": "no"}


def test_a_west_greater_than_east_box_counts_only_when_both_readings_agree(shape):
    # Swapped, this box is mostly in BC; read as crossing the 180° meridian it is almost the whole globe.
    disagree = dataone(boxes=[box(-115.0, -125.0, 49.0, 54.0)])
    assert gt.dataone_box_shares(disagree, shape) == [None]
    agree = dataone(boxes=[box(-115.0, -125.0, 0.0, 1.0)])
    assert gt.dataone_box_shares(agree, shape) == [0.0]
    plain = dataone(boxes=[box(-125.0, -120.0, 49.0, 50.0), box(-123.0, -123.0, 49.0, 49.0)])
    shares = gt.dataone_box_shares(plain, shape)
    assert shares[0] > 0.5 and shares[1] is None


def test_finish_answer_uses_the_variants_box_rule_and_evidence(shape):
    record = dataone(boxes=[box(-115.0, -125.0, 49.0, 54.0)])
    prompt = gv.build_pass2_prompt(record, shape, PLACES, LOOKUPS, "dataone")
    data_one = geo_pass2_trial.finish_answer({"answer": dict(PASS2_UNCLEAR)}, record, shape, prompt, "dataone")
    assert data_one["box_shares"] == [None] and not data_one["decision"]["in_bc_filled_by_box_rule"]
    assert data_one["decision"]["class"] == "unknown"
    assert data_one["evidence_check"]["exact"] == 1
    # The same box under the Lunaris rule is read with west and east swapped, so it fills in in_bc.
    lunaris = geo_pass2_trial.finish_answer({"answer": dict(PASS2_UNCLEAR)}, record, shape, prompt)
    assert lunaris["decision"]["in_bc_filled_by_box_rule"] and lunaris["decision"]["class"] == "bc_only"


def test_pass_1_keeps_places_written_in_the_record_and_drops_ones_only_in_our_notes(shape):
    record = dataone(title="Fish of Lake Examplia", boxes=[box(-125.0, -120.0, 49.0, 50.0)])
    prompt = gv.build_pass1_prompt(record, shape, "dataone")
    places = [{"as_written": "Lake Examplia", "name": "Lake Examplia", "role": "study_location"},
              {"as_written": "British Columbia", "name": "British Columbia", "role": "study_location"}]
    kept, dropped = geo_prompt_v3.clean_places(places, prompt)
    assert [p["name"] for p in kept] == ["Lake Examplia"] and [p["name"] for p in dropped] == ["British Columbia"]
    assert geo_pass1_trial.finish_answer({"answer": {"places": places}}, prompt)["places"] == kept


def test_the_dataone_prompts_start_with_their_instructions(shape):
    record = dataone()
    one = gv.build_pass1_prompt(record, shape, "dataone")
    two = gv.build_pass2_prompt(record, shape, PLACES, LOOKUPS, "dataone")
    assert one == gv.pass1_instructions("dataone") + "\n\n" + gt.dataone_record_text(record, shape)
    assert two.startswith(gv.pass2_instructions("dataone") + "\n\nRecord to read:")
    assert two.endswith(geo_pass2_prompt_c4.places_text(PLACES, LOOKUPS))
    assert os.path.basename(gv.__file__) == "geo_prompt_variants.py"
