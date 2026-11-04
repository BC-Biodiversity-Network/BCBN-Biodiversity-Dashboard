"""Tests for the place lookup's prompt P1 (pipeline/lunaris/llm/place_pick_prompt.py):
its instructions (pinned by a checksum), the prompt's parts in order and the
record copied unchanged, the candidate lines and our notes, the shuffled
order (the same every time, and a real reordering), the split of a record
with many names, and the check and reading of an answer."""

import hashlib
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "pipeline" / "lunaris" / "llm"))
import place_pick_prompt as pp  # noqa: E402

# The SHA-256 of P1's instructions. If they have to change, this changes with them, on purpose.
P1_INSTRUCTIONS_SHA256 = "faa6d054ba84c1828691a8d5510d8b643adf1b90c69878d0414a16d2e581f4a0"


def candidate(geonameid, name, admin1="British Columbia", country="Canada", feature="lake", lat=51.5, lon=-117.4,
              population=0, matched_name=None):
    """Return one candidate as stage 1 writes it for the model."""
    return {"geonameid": geonameid, "name": name, "matched_name": matched_name or name, "feature": feature,
            "feature_code": "LK", "admin1": admin1, "country": country, "country_code": "CA", "latitude": lat,
            "longitude": lon, "population": population, "kind": "in_bc", "large": False}


SUSAN = {"name": "Susan Lake", "as_written": "Susan Lake", "roles": ["study_location"], "decision": "model",
         "rule": "model", "reason": None, "match": "exact",
         "candidates": [candidate(1, "Susan Lake", lat=51.9331, lon=-120.9069),
                        candidate(2, "Susan Lake", lat=51.5703, lon=-117.3978),
                        candidate(3, "Susan Lake", "Ontario", lat=46.56, lon=-82.48)],
         "omitted_by_country": {}}
ITEM = {"dataset_id": "oai:canwin:susan-lake", "title": "Susan Lake at Recreation Site",
        "abstract": "The Columbia Shuswap Invasive Species Society (CSISS) collects plankton samples.",
        "keywords": ["Upper Columbia", "Lake"], "place_list": [], "catalogue": "CanWIN", "final_class": "bc_only",
        "names": [SUSAN,
                  {"name": "Canada", "as_written": "Canada", "roles": ["study_location"], "decision": "do_not_draw",
                   "rule": "large_area", "reason": "too_broad"},
                  {"name": "CSISS", "as_written": "CSISS", "roles": ["affiliation"], "decision": "do_not_draw",
                   "rule": "affiliation", "reason": "affiliation"}]}


def test_instructions_checksum():
    assert hashlib.sha256(pp.INSTRUCTIONS.encode("utf-8")).hexdigest() == P1_INSTRUCTIONS_SHA256


def test_the_prompt_is_the_instructions_the_record_and_the_names_in_that_order():
    text, ids = pp.build_prompt(ITEM, [0], "lookup")
    assert text.startswith(pp.INSTRUCTIONS + "\n\nRecord\nTitle: Susan Lake at Recreation Site\n")
    assert "Description: The Columbia Shuswap Invasive Species Society (CSISS) collects plankton samples.\n" in text
    assert "Keywords: Upper Columbia; Lake\nPlaces in the record: (none)\n\nCatalogue: CanWIN\n" in text
    assert "Note from us: our earlier reading is that the data were collected only in BC. It may be wrong." in text
    # Other names, but not an author's institution.
    assert "Note from us: other place names found in the record: Canada." in text
    assert text.index("Record\n") < text.index("Place names to decide") < text.index("P1: Susan Lake")
    assert text.endswith('P1: Susan Lake (written in the record as "Susan Lake")\n'
                         "  1. Susan Lake, lake, British Columbia, Canada (51.9331, -120.9069)\n"
                         "  2. Susan Lake, lake, British Columbia, Canada (51.5703, -117.3978)\n"
                         "  3. Susan Lake, lake, Ontario, Canada (46.5600, -82.4800)")
    assert ids == [[1, 2, 3]]


def test_candidate_lines_say_the_other_name_and_population_and_notes_explain_how_found():
    line = pp.candidate_line(4, candidate(9, "Gwaii Haanas National Park Reserve", feature="reserve",
                                          matched_name="Gwaii Haanas", population=1200))
    assert line == ("  4. Gwaii Haanas National Park Reserve (also called Gwaii Haanas), reserve, British Columbia, "
                    "Canada (51.5000, -117.4000), population 1,200")
    contains = dict(SUSAN, match="contains")
    assert "These places have its words in their own name." in pp.name_text("P1", contains, SUSAN["candidates"])
    northern = dict(SUSAN, direction_word_removed="Northern")
    assert 'named without "Northern"' in pp.name_text("P1", northern, SUSAN["candidates"])
    many = dict(SUSAN, omitted_by_country={"US": 280, "GB": 3})
    assert ("Note from us: 283 more places with this name are not listed (280 with country code US, 3 with country "
            "code GB). If the record means one of them, answer not_in_list.") in pp.name_text("P1", many, [])


def test_the_shuffled_order_is_the_same_every_time_and_reorders():
    entry = dict(SUSAN, candidates=[candidate(i, f"Lake {i}") for i in range(1, 11)])
    first = [c["geonameid"] for c in pp.candidate_order(ITEM, entry, "shuffled")]
    assert first == [c["geonameid"] for c in pp.candidate_order(ITEM, entry, "shuffled")]
    assert sorted(first) == list(range(1, 11)) and first != list(range(1, 11))
    assert [c["geonameid"] for c in pp.candidate_order(ITEM, entry, "lookup")] == list(range(1, 11))


def test_a_record_with_many_names_is_split_and_asked_in_both_orders():
    names = [dict(SUSAN, name=f"Lake {i}") for i in range(pp.MAX_NAMES + 3)]
    item = dict(ITEM, names=names)
    units = pp.units(item)
    assert [(u["order"], u["chunk"], len(u["indexes"])) for u in units] == [
        ("lookup", 0, pp.MAX_NAMES), ("lookup", 1, 3), ("shuffled", 0, pp.MAX_NAMES), ("shuffled", 1, 3)]
    text, _ = pp.build_prompt(item, units[1]["indexes"], "lookup")
    assert "P1: Lake 8" in text and "P4:" not in text


def answer(*entries):
    """Return an answer with the given entries."""
    return {"places": [dict({"place": f"P{i}", "reason": "r", "quote": "q", "candidate": 0,
                             "none_because": "ambiguous"}, **entry) for i, entry in enumerate(entries, start=1)]}


def test_answers_are_checked_against_the_prompt():
    ids = [[1, 2, 3]]
    assert pp.check_answer(answer({"candidate": 2, "none_because": ""}), ids) is None
    assert pp.check_answer(answer({}), ids) is None
    assert "1 entries for 2" in pp.check_answer(answer({}), [[1], [2]])
    assert "not 0 to 3" in pp.check_answer(answer({"candidate": 4, "none_because": ""}), ids)
    assert "with none_because" in pp.check_answer(answer({"candidate": 2}), ids)
    assert "with none_because" in pp.check_answer(answer({"none_because": ""}), ids)
    assert "labelled" in pp.check_answer({"places": [dict(answer({})["places"][0], place="P2")]}, ids)
    assert "over 400" in pp.check_answer(answer({"quote": "x" * 401}), ids)
    assert "not {" in pp.check_answer({"places": [], "extra": 1}, ids)


def test_an_answer_is_read_back_to_geonames_ids_in_the_order_asked():
    unit = {"order": "shuffled", "chunk": 0, "indexes": [0]}
    decisions = pp.read_answer(ITEM, unit, answer({"candidate": 2, "none_because": "", "quote": "CSISS"}), [[7, 5, 9]])
    assert decisions == [{"dataset_id": "oai:canwin:susan-lake", "name": "Susan Lake", "order": "shuffled",
                          "candidate_number": 2, "geonameid": 5, "none_because": None, "quote": "CSISS",
                          "reason": "r"}]
    none = pp.read_answer(ITEM, unit, answer({}), [[7, 5, 9]])[0]
    assert (none["geonameid"], none["none_because"]) == (None, "ambiguous")
