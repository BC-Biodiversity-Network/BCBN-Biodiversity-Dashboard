"""Tests for pipeline/lunaris/filter2_35b_check_new.py, on made-up answers:
the rule found for an answer follows decide()'s order and always agrees with
decide(), the pools give the asked numbers in a fixed shuffled order, the
answer rows hold the pool and the facts, and the fuller copy keeps every
value of the earlier file and stops if one would change."""

import sys
from pathlib import Path

import pandas as pd
import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "pipeline" / "lunaris"))
sys.path.insert(0, str(BACKEND / "tests"))
import filter2_35b_check_new as chk  # noqa: E402
from score_trial import decide  # noqa: E402
# The made-up answers and hand decisions are shared with the comparison's tests.
from test_compare_filter2_35b import answer, hand_table, write_answers  # noqa: E402


def facts(**changes):
    """Return a kept answer's facts, with some changed."""
    base = {"concerns_living_things": True, "topic": "habitat_or_ecosystem", "about_organisms_themselves":
            "not_applicable", "ecological_purpose_stated": "not_applicable", "experimental_animals": "no",
            "extinct_only": "no"}
    return dict(base, **changes)


@pytest.mark.parametrize("changes, rule", [
    ({}, "kept"),
    ({"experimental_animals": "yes", "topic": "physical_environment"}, "experimental animals"),
    ({"extinct_only": "yes"}, "extinct only"),
    ({"topic": "physical_environment"}, "physical environment"),
    ({"topic": "human_health", "concerns_living_things": False}, "human health"),
    ({"topic": "administrative"}, "administrative"),
    ({"concerns_living_things": False}, "not living things"),
    ({"topic": "forestry_agriculture_fisheries"}, "criterion 2 (not about the organisms)"),
    ({"topic": "forestry_agriculture_fisheries", "about_organisms_themselves": "no"},
     "criterion 2 (not about the organisms)"),
    ({"topic": "forestry_agriculture_fisheries", "about_organisms_themselves": "yes"}, "kept"),
    ({"topic": "land_and_boundaries", "ecological_purpose_stated": "no"}, "criterion 3 (no ecological purpose)"),
    ({"topic": "land_and_boundaries", "ecological_purpose_stated": "yes"}, "kept"),
])
def test_the_rule_follows_decide(changes, rule):
    record = facts(**changes)
    assert chk.decide_rule(record) == rule
    assert (rule == "kept") == (decide(record) == "yes")


def test_a_rule_that_disagrees_with_decide_stops(tmp_path, monkeypatch):
    answers = chk.cmp.read_answers(write_answers(tmp_path, [answer("a", kept=False), answer("f", error="x")]))
    assert chk.rule_for(answers, "a") == "administrative"
    assert chk.rule_for(answers, "f") == "failed"
    monkeypatch.setattr(chk, "decide_rule", lambda record: "kept")
    with pytest.raises(SystemExit, match="does not match decide"):
        chk.rule_for(answers, "a")


def test_the_pools_give_the_asked_numbers_in_a_fixed_order():
    rules = pd.DataFrame({"record_id": [f"b{n:03d}" for n in range(166)] + [f"n{n:04d}" for n in range(1247)]
                          + ["x1"], "bc_class": ["bc"] * 166 + ["needs_llm"] * 1247 + ["not_bc"]})
    picked = chk.draw_pools(rules)
    assert picked == chk.draw_pools(rules.iloc[::-1])
    pools = [pool for _, pool in picked]
    assert (pools.count("bc"), pools.count("needs_llm"), len(set(picked))) == (25, 15, 40)
    assert all(r.startswith("b") == (pool == "bc") for r, pool in picked)
    # Shuffled together, so the first 25 are not all from one pool.
    assert set(pools[:25]) == {"bc", "needs_llm"}


def test_the_new_sample_rows_hold_the_pool_and_facts(tmp_path):
    a = chk.cmp.read_answers(write_answers(tmp_path, [answer("r1", kept=False), answer("r2")], "a.csv"))
    b = chk.cmp.read_answers(write_answers(tmp_path, [answer("r1"), answer("r2")], "b.csv"))
    rows = chk.new_sample_answers([("r2", "needs_llm"), ("r1", "bc")], a, b, hand_table([]))
    assert rows[["number", "record_id", "pool"]].values.tolist() == [["01", "r2", "needs_llm"],
                                                                     ["02", "r1", "step1 bc"]]
    assert rows.loc[1, ["verdict_4b", "concerns_living_things_4b", "form_4b", "reason_4b", "verdict_35b"]].tolist() \
        == ["no", "False", "dataset", "because", "yes"]
    assert [c for c in rows.columns if c.endswith("_35b")] == [
        "verdict_35b", "topic_35b", "concerns_living_things_35b", "about_organisms_themselves_35b",
        "ecological_purpose_stated_35b", "experimental_animals_35b", "form_35b", "reason_35b"]


def test_the_fuller_copy_keeps_the_earlier_values(tmp_path):
    a = chk.cmp.read_answers(write_answers(tmp_path, [answer("r1")], "a.csv"))
    b = chk.cmp.read_answers(write_answers(tmp_path, [answer("r1", kept=False)], "b.csv"))
    earlier = pd.DataFrame([{"number": "01", "record_id": "r1", "verdict_4b": "yes",
                             "topic_4b": "habitat_or_ecosystem", "reason_4b": "because", "verdict_35b": "no",
                             "topic_35b": "administrative", "reason_35b": "because",
                             "filter3_final_class": "bc_only", "filter3_frontend_group": "bc_data"}])
    fuller = chk.fuller_answers(earlier, a, b, hand_table([]))
    assert fuller.loc[0, ["experimental_animals_35b", "form_4b", "rule_35b", "filter3_final_class"]].tolist() == [
        "no", "dataset", "administrative", "bc_only"]
    assert (fuller[earlier.columns] == earlier).all().all()
    earlier.loc[0, "topic_35b"] = "other"
    with pytest.raises(SystemExit, match="would differ"):
        chk.fuller_answers(earlier, a, b, hand_table([]))
