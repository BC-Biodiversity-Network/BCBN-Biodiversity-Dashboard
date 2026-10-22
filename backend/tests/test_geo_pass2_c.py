"""Tests for pass 2 version C: the class code works out from the model's
facts (pipeline/lunaris/llm/geo_pass2_classes.py), every row of its table,
the box rule that may read an "unclear" in_bc as "yes", and the prompt
(geo_pass2_prompt_c.py): its instructions unchanged, the record and the
places after them, the rules kept from earlier versions, the new fields and
box rules, the answer check, and the consistency flags.

They reuse the made-up BC, record and lookup results of
test_geo_pass2_places.py."""

import itertools
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
# The pipeline scripts are not a package; add their folders to the path the
# way the scripts themselves do.
sys.path.insert(0, str(BACKEND / "pipeline" / "lunaris"))
sys.path.insert(0, str(BACKEND / "pipeline" / "lunaris" / "llm"))
import geo_pass2_places as places_part  # noqa: E402
import geo_pass2_prompt_c as c  # noqa: E402
from geo_pass2_classes import BOX_SHARE_FOR_BC, CLASSES, FACTS, class_of, decide, fill_in_bc  # noqa: E402
from geo_record_text import record_text  # noqa: E402
from test_geo_pass2_places import RECORD, TOY_BC, candidate, lookup, place  # noqa: E402

# The SHA-256 of C's instructions as used in the trials. The C prompt text
# must not change; if it has to, this changes with it, on purpose.
C_INSTRUCTIONS_SHA256 = "ba7ea1832fe4f28be90a59f7bc6d30883981d0fce833b1b3976c28f29e348def"

ANY = None   # in a table row: every value this fact may take

# The table in geo_pass2_classes' docstring:
# (global, outside_canada, canada_wide, in_bc, other_canada) -> class
TABLE = [
    (("yes", ANY, ANY, ANY, ANY), "global"),
    (("no", "yes", ANY, "yes", ANY), "bc_and_outside_canada"),
    (("no", "yes", "yes", ANY, ANY), "bc_and_outside_canada"),
    (("no", "no", "yes", ANY, ANY), "all_canada"),           # outside "yes" with canada_wide "yes" is row 2
    (("no", ANY, "no", "yes", "yes"), "canada_incl_bc"),     # except outside "yes" with in_bc "yes": row 2
    (("no", "no", "no", "yes", "no"), "bc_only"),
    (("no", ANY, "no", "no", ANY), "elsewhere"),
    (("no", ANY, "no", "unclear", ANY), "unknown"),
]
ORDER = ["global", "outside_canada", "canada_wide", "in_bc", "other_canada"]


def expand(row):
    """Every set of facts a table row covers, its ANY columns filled with each allowed value."""
    choices = [FACTS[name] if value is ANY else [value] for name, value in zip(ORDER, row)]
    return [dict(zip(ORDER, values)) for values in itertools.product(*choices)]


# --- The classes ---------------------------------------------------------

@pytest.mark.parametrize("row, expected", TABLE)
def test_every_table_row(row, expected):
    for facts in expand(row):
        # Rows written with ANY overlap earlier rows; the earlier row wins, as in the module.
        if expected == "all_canada" and facts["outside_canada"] == "yes":
            continue
        if expected == "canada_incl_bc" and facts["outside_canada"] == "yes":
            continue
        assert class_of(facts) == expected, facts


def test_every_combination_has_one_class():
    names = list(FACTS)
    combos = list(itertools.product(*FACTS.values()))
    assert len(combos) == 48
    for values in combos:
        assert class_of(dict(zip(names, values))) in CLASSES


def test_table_covers_every_combination():
    covered = {tuple(f[name] for name in ORDER) for row, _ in TABLE for f in expand(row)}
    assert len(covered) == 48


@pytest.mark.parametrize("facts, expected", [
    # A worldwide record is global whatever else is said.
    (dict(in_bc="no", other_canada="no", outside_canada="no", canada_wide="no", global_="yes"), "global"),
    # BC and Washington: BC plus a place outside Canada.
    (dict(in_bc="yes", other_canada="no", outside_canada="yes", canada_wide="no", global_="no"),
     "bc_and_outside_canada"),
    # All of Canada and the US.
    (dict(in_bc="unclear", other_canada="yes", outside_canada="yes", canada_wide="yes", global_="no"),
     "bc_and_outside_canada"),
    (dict(in_bc="yes", other_canada="yes", outside_canada="no", canada_wide="yes", global_="no"), "all_canada"),
    (dict(in_bc="yes", other_canada="yes", outside_canada="no", canada_wide="no", global_="no"), "canada_incl_bc"),
    (dict(in_bc="yes", other_canada="no", outside_canada="no", canada_wide="no", global_="no"), "bc_only"),
    # Alberta only.
    (dict(in_bc="no", other_canada="yes", outside_canada="no", canada_wide="no", global_="no"), "elsewhere"),
    # Washington only, with a box that touches BC.
    (dict(in_bc="no", other_canada="no", outside_canada="yes", canada_wide="no", global_="no"), "elsewhere"),
    (dict(in_bc="unclear", other_canada="no", outside_canada="no", canada_wide="no", global_="no"), "unknown"),
])
def test_examples(facts, expected):
    facts["global"] = facts.pop("global_")
    assert class_of(facts) == expected


# --- The prompt ----------------------------------------------------------

def test_instructions_are_unchanged():
    import hashlib

    assert hashlib.sha256(c.INSTRUCTIONS.encode("utf-8")).hexdigest() == C_INSTRUCTIONS_SHA256


def test_prompt_is_instructions_then_record_then_places():
    places = [place("Fakeville"), place("University of Fakeville", "Fakeville", "affiliation")]
    lookups = [lookup([candidate()]), lookup([candidate()])]
    assert c.build_prompt(RECORD, TOY_BC, places, lookups) == (
        c.INSTRUCTIONS + "\n\n" + record_text(RECORD, TOY_BC) + "\n\n" + places_part.places_text(places, lookups))


def test_rules_kept_from_earlier_versions():
    # The rules on affiliations, GeoNames matches, shared names, facts and general knowledge.
    for sentence in ("An affiliation (the address of an author, university, agency, museum or repository) is not "
                     "where the data was collected.",
                     '"No GeoNames match" does not mean a place is outside BC',
                     "Different places can share a name.",
                     'starting that part with "general knowledge:"',
                     "Do not make up facts about the record."):
        assert sentence in c.INSTRUCTIONS
    assert c.INSTRUCTIONS.index("Keep in mind:") < c.INSTRUCTIONS.index("Boxes and points:") \
        < c.INSTRUCTIONS.index("Facts:") < c.INSTRUCTIONS.index("Examples, with made-up places:")


def test_each_field_is_explained_once():
    for fact in FACTS:
        assert c.INSTRUCTIONS.count(f"  {fact}: ") == 1
    assert 'Only "yes" if the record names them or clearly says so' in c.INSTRUCTIONS
    assert "Unclear is a good answer for in_bc" in c.INSTRUCTIONS


def test_box_rules_say_what_they_mean_for_in_bc():
    boxes = c.INSTRUCTIONS[c.INSTRUCTIONS.index("Boxes and points:"):c.INSTRUCTIONS.index("Facts:")]
    assert "If at least half of a box lies in BC and the record names no place outside BC" in boxes
    assert "A box that only partly touches BC is not by itself evidence for in_bc" in boxes
    assert "answer in_bc" not in boxes and "canada_including_bc" not in boxes


def test_no_class_names_from_earlier_versions():
    for old in ("canada_including_bc", "elsewhere:", "unknown:"):
        assert old not in c.INSTRUCTIONS


def test_examples_are_made_up():
    for real in ("Victoria", "Vancouver", "Okanagan", "Rocky", "Alberta", "Washington", "Winnipeg", "Arctic"):
        assert real not in c.INSTRUCTIONS


def test_answer_schema_puts_the_reason_first():
    assert list(c.RESPONSE_SCHEMA["properties"]) == ["reason", "evidence", *FACTS]
    assert c.RESPONSE_SCHEMA["properties"]["in_bc"]["enum"] == ["yes", "no", "unclear"]
    assert c.RESPONSE_SCHEMA["properties"]["global"]["enum"] == ["yes", "no"]


def good_answer(**facts):
    """A version C answer, all facts "no" unless given."""
    answer = {"reason": "r", "evidence": [], "in_bc": "no", "other_canada": "no", "outside_canada": "no",
              "canada_wide": "no", "global": "no"}
    answer.update(facts)
    return answer


def test_check_answer():
    assert c.check_answer(good_answer()) is None
    assert c.check_answer(good_answer(in_bc="maybe")) is not None
    unclear_global = good_answer()
    unclear_global["global"] = "unclear"          # only in_bc may be unclear
    assert c.check_answer(unclear_global) is not None
    assert c.check_answer({k: v for k, v in good_answer().items() if k != "global"}) is not None


def test_class_from_code():
    assert decide(good_answer(in_bc="yes"), [])["class"] == "bc_only"
    assert decide(good_answer(in_bc="unclear"), [])["class"] == "unknown"


# --- The box rule --------------------------------------------------------

def test_box_rule_fills_in_an_unclear_in_bc():
    result = decide(good_answer(in_bc="unclear"), [0.845])
    assert result == {"model_in_bc": "unclear", "in_bc_used": "yes", "in_bc_filled_by_box_rule": True,
                      "class": "bc_only"}


def test_box_rule_at_exactly_half_and_just_below():
    assert BOX_SHARE_FOR_BC == 0.5
    assert decide(good_answer(in_bc="unclear"), [0.5])["in_bc_filled_by_box_rule"]
    below = decide(good_answer(in_bc="unclear"), [0.4999])
    assert not below["in_bc_filled_by_box_rule"] and below["class"] == "unknown"


@pytest.mark.parametrize("facts", [dict(other_canada="yes"), dict(outside_canada="yes"),
                                   dict(other_canada="yes", outside_canada="yes")])
def test_box_rule_not_when_the_model_named_data_beyond_bc(facts):
    result = decide(good_answer(in_bc="unclear", **facts), [0.9])
    assert not result["in_bc_filled_by_box_rule"] and result["in_bc_used"] == "unclear"


@pytest.mark.parametrize("in_bc", ["no", "yes"])
def test_box_rule_only_changes_unclear(in_bc):
    result = decide(good_answer(in_bc=in_bc), [0.9])
    assert not result["in_bc_filled_by_box_rule"] and result["in_bc_used"] == in_bc
    assert decide(good_answer(in_bc="no"), [0.9])["class"] == "elsewhere"


def test_box_rule_with_several_boxes():
    assert decide(good_answer(in_bc="unclear"), [0.1, None, 0.6])["in_bc_filled_by_box_rule"]
    assert not decide(good_answer(in_bc="unclear"), [0.1, None, 0.3])["in_bc_filled_by_box_rule"]
    assert not decide(good_answer(in_bc="unclear"), [None])["in_bc_filled_by_box_rule"]
    assert not decide(good_answer(in_bc="unclear"), [])["in_bc_filled_by_box_rule"]


def test_box_rule_keeps_the_models_answer():
    given = good_answer(in_bc="unclear")
    kept = dict(given)
    decide(given, [0.9])
    assert given == kept
    assert fill_in_bc(given, [0.9]) is True


def test_box_rule_does_not_override_canada_wide():
    # A Canada-wide answer stays all_canada whether or not the box rule fills in in_bc.
    assert decide(good_answer(in_bc="unclear", canada_wide="yes"), [0.9])["class"] == "all_canada"


def test_consistency_flags_read_in_bc():
    assert c.consistency_flags(good_answer(in_bc="yes", reason="It is unclear where the data come from.")) == \
        ["reason_says_unsure"]
    assert c.consistency_flags(good_answer(in_bc="no", reason="Samples were collected near Fakeville in BC.")) == \
        ["reason_says_in_bc"]
    assert c.consistency_flags(good_answer(in_bc="unclear", reason="It is unclear.")) == []
