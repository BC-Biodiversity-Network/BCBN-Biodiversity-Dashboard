"""Tests for the classes the BC filter works out by code from pass 2's facts
(pipeline/lunaris/llm/geo_pass2_classes.py): every row of its table, and the
box rule that may read an "unclear" in_bc as "yes". They do not depend on any
prompt version.

These tests were first written with version C's prompt tests (moved to
backend/exploration/lunaris/bc_filter_2026_10/old_pass2_prompts/tests/
test_geo_pass2_c.py) and are kept here unchanged."""

import itertools
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
# The pipeline scripts are not a package; add their folders to the path the
# way the scripts themselves do.
sys.path.insert(0, str(BACKEND / "pipeline" / "lunaris"))
sys.path.insert(0, str(BACKEND / "pipeline" / "lunaris" / "llm"))
from geo_pass2_classes import BOX_SHARE_FOR_BC, CLASSES, FACTS, class_of, decide, fill_in_bc  # noqa: E402

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


def good_answer(**facts):
    """An answer, all facts "no" unless given."""
    answer = {"reason": "r", "evidence": [], "in_bc": "no", "other_canada": "no", "outside_canada": "no",
              "canada_wide": "no", "global": "no"}
    answer.update(facts)
    return answer


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
