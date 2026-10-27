"""Tests for pass 2 version C4 (pipeline/lunaris/llm/geo_pass2_prompt_c4.py):
its instructions (pinned by a checksum), the role names they list, the
prompt's parts in order, the answer schema and its check, the consistency
flags, the box note that also gives the share outside BC
(geo_record_text.inside_and_outside, including its edge cases) while the
default note stays as pass 1 shows it, and the evidence check against the
prompt (geo_pass2_places.evidence_in_text).

The tests that compared C4 with C3 were moved with C3 to
backend/exploration/lunaris/bc_filter_2026_10/old_pass2_prompts/tests/
(test_geo_pass2_c4_with_c3.py). They reuse the made-up BC, record and lookup
results of test_geo_pass2_places.py."""

import hashlib
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
# The pipeline scripts are not a package; add their folders to the path the
# way the scripts themselves do.
sys.path.insert(0, str(BACKEND / "pipeline" / "lunaris"))
sys.path.insert(0, str(BACKEND / "pipeline" / "lunaris" / "llm"))
import geo_pass2_places as pp  # noqa: E402
import geo_pass2_prompt_c4 as c4  # noqa: E402
from geo_pass2_classes import FACTS  # noqa: E402
from geo_record_text import box_lines, inside_and_outside, percent, record_text  # noqa: E402
from test_geo_pass2_places import RECORD, TOY_BC, candidate, lookup, place  # noqa: E402

# The SHA-256 of C4's instructions. If they have to change, this changes with them, on purpose.
C4_INSTRUCTIONS_SHA256 = "a5632aa668218b52c804fbee5bd5df9427dca154ec83912b84273fd057992e29"


def box(w, s, e, n):
    """A box in the form the harvest stores it."""
    return {"w": w, "s": s, "e": e, "n": n}


# --- The instructions ----------------------------------------------------

def test_instructions_checksum():
    assert hashlib.sha256(c4.INSTRUCTIONS.encode("utf-8")).hexdigest() == C4_INSTRUCTIONS_SHA256


def test_new_sentences():
    assert "2. The Place list: the place names in the record, each with a role (" in c4.INSTRUCTIONS
    assert ("evidence: short phrases your answers rely on, copied exactly from the record, the Place list or our "
            "notes (an empty list if none).") in c4.INSTRUCTIONS
    assert c4.INSTRUCTIONS.endswith("Your answers must agree with your reason. If your reason says it is unclear "
                                     "whether data were collected in BC, in_bc is unclear.")


def test_role_names_match_the_place_list():
    roles = "(study location, affiliation, Indigenous nation or territory, or other mention)"
    assert roles in c4.INSTRUCTIONS
    assert list(pp.ROLE_TEXT.values()) == ["study location", "affiliation", "Indigenous nation or territory",
                                           "other mention"]


def test_each_fact_is_asked_once():
    for fact in FACTS:
        assert c4.INSTRUCTIONS.count(f"  {fact}: ") == 1


def test_prompt_is_instructions_then_record_then_places():
    record = dict(RECORD, boxes=[box(-128.0, 55.0, -125.0, 58.0)])
    places, lookups = [place("Fakeville")], [lookup([candidate()])]
    assert c4.build_prompt(record, TOY_BC, places, lookups) == (
        c4.INSTRUCTIONS + "\n\n" + record_text(record, TOY_BC, box_outside=True) + "\n\n"
        + pp.places_text(places, lookups))
    assert "lies outside it" in c4.build_prompt(record, TOY_BC, places, lookups)


# --- The answer ----------------------------------------------------------

def good_answer(**facts):
    """A C4 answer, all facts "no" unless given."""
    answer = {"reason": "r", "evidence": [], "in_bc": "no", "other_canada": "no", "outside_canada": "no",
              "canada_wide": "no", "global": "no"}
    answer.update(facts)
    return answer


def test_answer_schema_puts_the_reason_first():
    assert list(c4.RESPONSE_SCHEMA["properties"]) == ["reason", "evidence", *FACTS]
    assert c4.RESPONSE_SCHEMA["required"] == ["reason", "evidence", *FACTS]
    assert c4.RESPONSE_SCHEMA["properties"]["in_bc"]["enum"] == ["yes", "no", "unclear"]
    assert c4.RESPONSE_SCHEMA["properties"]["global"]["enum"] == ["yes", "no"]


def test_check_answer():
    assert c4.check_answer(good_answer()) is None
    assert c4.check_answer(good_answer(in_bc="maybe")) is not None
    unclear_global = good_answer()
    unclear_global["global"] = "unclear"          # only in_bc may be unclear
    assert c4.check_answer(unclear_global) is not None
    assert c4.check_answer({k: v for k, v in good_answer().items() if k != "global"}) is not None
    assert c4.check_answer(good_answer(evidence="Fakeville")) is not None


def test_consistency_flags_read_in_bc():
    assert c4.consistency_flags(good_answer(in_bc="yes", reason="It is unclear where the data come from.")) == \
        ["reason_says_unsure"]
    assert c4.consistency_flags(good_answer(in_bc="no", reason="Samples were collected near Fakeville in BC.")) == \
        ["reason_says_in_bc"]
    assert c4.consistency_flags(good_answer(in_bc="unclear", reason="It is unclear.")) == []


# --- The box note --------------------------------------------------------

@pytest.mark.parametrize("share, inside, outside", [
    (0.0, "0%", "100%"),
    (1.0, "100%", "0%"),
    (0.9999999999999999, "100%", "0%"),        # a box wholly in BC, with rounding in the area sums
    (0.004, "less than 1%", "more than 99%"),
    (0.0099, "less than 1%", "more than 99%"),
    (0.01, "1%", "99%"),
    (0.17, "17%", "83%"),
    (0.5, "50%", "50%"),
    (0.845, "84%", "16%"),           # rounded as percent() rounds it, so the inside number is as before
    (0.99, "99%", "1%"),
    (0.996, "more than 99%", "less than 1%"),   # not all of it, so never "100%" in BC
])
def test_inside_and_outside(share, inside, outside):
    assert inside_and_outside(share) == (inside, outside)


def test_inside_and_outside_add_up_to_100():
    for step in range(1, 1000):
        share = step / 1000
        inside, outside = inside_and_outside(share)
        if inside.endswith("%") and inside[:-1].isdigit():
            assert int(inside[:-1]) + int(outside[:-1]) == 100
            assert inside == percent(share)


def test_box_note_with_the_outside_share():
    inside_box = box(-128.0, 55.0, -125.0, 58.0)
    note = box_lines(1, inside_box, TOY_BC, outside=True)[-1]
    assert note.startswith("Note from us (our own calculation, not part of the record): 100% of this box lies in "
                           "British Columbia and 0% lies outside it, and it covers ")
    far = box(-80.0, 40.0, -75.0, 45.0)
    assert ("0% of this box lies in British Columbia and 100% lies outside it, and it covers 0% of British "
            "Columbia.") in box_lines(1, far, TOY_BC, outside=True)[-1]


def test_box_numbers_and_the_default_note_are_unchanged():
    b = box(-128.0, 55.0, -125.25, 58.0)
    default, with_outside = box_lines(1, b, TOY_BC), box_lines(1, b, TOY_BC, outside=True)
    assert default[0] == with_outside[0]              # the raw numbers
    assert "100% of this box lies in British Columbia, and it covers " in default[-1]
    assert "lies outside it" not in default[-1]
    assert record_text(RECORD, TOY_BC) == record_text(RECORD, TOY_BC, box_outside=False)


# --- The evidence check --------------------------------------------------

def test_evidence_in_text_exact_loose_and_unmatched():
    text = "Title: Trout in Lake Fakeville\nPlace 1: as written \"Fakeville\"\nNote from us: 84% of this box"
    answer = {"evidence": ["Trout in Lake Fakeville", "trout  in lake FAKEVILLE", "84% of this box", "Examplia"]}
    result = pp.evidence_in_text(answer, text)
    assert result == {"items": 4, "exact": 2, "loose": 3, "unmatched": ["Examplia"]}


def test_evidence_check_looks_only_after_the_instructions():
    prompt = c4.build_prompt(RECORD, TOY_BC, [place("Fakeville")], [lookup([candidate()])])
    answer = {"reason": "r", "evidence": ["Lake Fakeville", "Lake Examplia", "Place list (made by another language model)",
                                          "falls inside British Columbia"],
              "in_bc": "yes", "other_canada": "no", "outside_canada": "no", "canada_wide": "no", "global": "no"}
    kept = dict(answer, evidence=list(answer["evidence"]))
    result = c4.evidence_check(answer, prompt)
    # "Lake Examplia" is not in the record; the other three are (record, Place list header, our note on the point).
    assert result["unmatched"] == ["Lake Examplia"] and result["exact"] == 3
    assert answer == kept                                              # the answer is never changed


def test_evidence_check_ignores_the_instructions():
    prompt = c4.build_prompt(RECORD, TOY_BC, [], [])
    answer = {"evidence": ["Do not make up facts"]}                    # only in the instructions
    assert c4.evidence_check(answer, prompt)["unmatched"] == ["Do not make up facts"]
