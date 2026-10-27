"""Tests for pipeline/lunaris/compare_filter2_35b.py, on made-up records: the
answers go through decide(), hand decisions win over the model both ways, a
failed record with no hand decision stops, the groups and catalogue counts
add up, the sample is the same every run, the rules check finds a changed
row, copy groups and new copies are found, the reviewed records are lined
up, and the hand decision file holds the 4 decisions as agreed."""

import sys
from pathlib import Path

import pandas as pd
import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "pipeline" / "lunaris"))
import compare_filter2_35b as cmp  # noqa: E402
from build_bc_filter_rules import FAILED_IN_FULL_RUN  # noqa: E402

MANITOBA = "oai:borealisdata.ca-dataverse-manitoba:700505_703695_703696"


def answer(record_id, kept=True, error=None, tokens=1000, topic=None):
    """Return one row of a merged trial CSV: a kept answer, a dropped one, or a failure."""
    if error:
        return {"record_id": record_id, "model": "m", "model_reason": "", "input_tokens": tokens,
                "model_error": error}
    return {"record_id": record_id, "model": "m", "concerns_living_things": kept,
            "organisms": "fish" if kept else "", "organism_level": "species" if kept else "none",
            "topic": topic or ("habitat_or_ecosystem" if kept else "administrative"),
            "about_organisms_themselves": "not_applicable", "ecological_purpose_stated": "not_applicable",
            "experimental_animals": "no", "extinct_only": "no", "form": "dataset", "model_reason": "because",
            "input_tokens": tokens, "model_error": None}


def write_answers(tmp_path, rows, name="answers.csv"):
    """Write answer rows as a merged trial CSV (with the byte order mark merge_results.py writes) and return its path."""
    path = tmp_path / name
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")
    return path


def hand_table(rows):
    """Return hand decisions from (record_id, kept) pairs, as read_hand_decisions returns them."""
    frame = pd.DataFrame([{"record_id": r, "kept": k, "reason": "why", "decided_by": "Lucia",
                           "decided_on": "2026-10-27"} for r, k in rows],
                         columns=["record_id", "kept", "reason", "decided_by", "decided_on"])
    return frame.set_index("record_id", drop=False)


def test_answers_go_through_decide_and_failures_are_marked(tmp_path):
    path = write_answers(tmp_path, [answer("a"), answer("b", kept=False), answer("c", error="cut off")])
    answers = cmp.read_answers(path)
    assert answers["verdict"].to_dict() == {"a": "yes", "b": "no", "c": "failed"}


def test_a_record_twice_in_the_answers_stops(tmp_path):
    path = write_answers(tmp_path, [answer("a"), answer("a")])
    with pytest.raises(SystemExit):
        cmp.read_answers(path)


def test_hand_decisions_win_over_the_model_both_ways(tmp_path):
    answers = cmp.read_answers(write_answers(tmp_path, [
        answer("model_yes"), answer("model_no", kept=False), answer("hand_yes", kept=False),
        answer("hand_no", kept=True), answer("failed_no", error="cut off"), answer("failed_yes", error="cut off")]))
    hand = hand_table([("hand_yes", "yes"), ("hand_no", "no"), ("failed_no", "no"), ("failed_yes", "yes")])
    kept, counts = cmp.keep_set(answers, hand)
    assert kept == {"model_yes", "hand_yes", "failed_yes"}
    assert counts == {"answered": 4, "failed": 2, "model_yes": 2, "hand_yes_added": 2, "hand_no_removed": 1,
                      "kept": 3}


def test_a_failed_record_without_a_hand_decision_stops(tmp_path):
    answers = cmp.read_answers(write_answers(tmp_path, [answer("a"), answer("b", error="cut off")]))
    with pytest.raises(SystemExit, match="no hand decision"):
        cmp.keep_set(answers, hand_table([]))


def test_a_hand_decision_for_a_record_not_in_the_run_stops(tmp_path):
    answers = cmp.read_answers(write_answers(tmp_path, [answer("a")]))
    with pytest.raises(SystemExit, match="not in the run"):
        cmp.keep_set(answers, hand_table([("zzz", "no")]))


def test_hand_decisions_must_say_yes_or_no_once(tmp_path):
    path = tmp_path / "hand.csv"
    path.write_text("record_id,kept,reason,decided_by,decided_on\na,maybe,why,L,2026-10-27\n")
    with pytest.raises(SystemExit, match="yes or no"):
        cmp.read_hand_decisions(path)
    path.write_text("record_id,kept,reason,decided_by,decided_on\na,yes,why,L,d\na,no,why,L,d\n")
    with pytest.raises(SystemExit, match="twice"):
        cmp.read_hand_decisions(path)


def test_the_hand_decision_file_holds_the_agreed_decisions():
    hand = cmp.read_hand_decisions()
    assert set(hand.index[hand["kept"] == "yes"]) == set(FAILED_IN_FULL_RUN)
    assert list(hand.index[hand["kept"] == "no"]) == [MANITOBA]
    assert set(hand["decided_by"]) == {"Lucia"}
    assert hand.loc[FAILED_IN_FULL_RUN, "decided_on"].tolist() == ["2026-10-21"] * 3
    row = hand.loc[MANITOBA]
    assert (row["decided_by"], row["decided_on"]) == ("Lucia", "2026-10-27")
    assert row["reason"] == ("Search strategy for a medical scoping review on benzodiazepine prescribing; abstract "
                             "is a database search string; no organisms, observations or places. The model's "
                             "reply was cut off twice (invalid JSON).")


def test_groups_and_catalogue_counts():
    groups = cmp.keep_groups({"oai:x.ca:1", "oai:x.ca:2", "oai:y.org:3"}, {"oai:x.ca:1", "oai:y.org:4"})
    assert groups.to_dict() == {"oai:x.ca:1": "both", "oai:x.ca:2": "only_4b", "oai:y.org:3": "only_4b",
                                "oai:y.org:4": "only_35b"}
    counts = cmp.by_catalogue(groups)
    assert counts.loc["x.ca"].to_dict() == {"both": 1, "only_35b": 0, "only_4b": 1, "changed": 1, "net": -1}
    assert counts.loc["y.org"].to_dict() == {"both": 0, "only_35b": 1, "only_4b": 1, "changed": 2, "net": 0}


def test_the_sample_is_the_same_every_run():
    ids = {f"r{n:03d}" for n in range(100)}
    first = cmp.sample_ids(ids)
    assert first == cmp.sample_ids(set(reversed(sorted(ids))))
    assert len(first) == 30 and first == sorted(first) and set(first) <= ids
    assert cmp.sample_ids({"b", "a"}) == ["a", "b"]


def test_answers_side_by_side(tmp_path):
    a = cmp.read_answers(write_answers(tmp_path, [answer("r", kept=True), answer("f", error="x")], "a.csv"))
    b = cmp.read_answers(write_answers(tmp_path, [answer("r", kept=False), answer("f", kept=True)], "b.csv"))
    hand = hand_table([("f", "yes")])
    assert cmp.facts_that_differ(a, b, "r") == "concerns_living_things, topic, organisms, organism_level"
    assert cmp.facts_that_differ(a, b, "f") == "one run failed"
    assert cmp.answer_text(a, hand, "f") == "failed (hand decision: kept yes)"
    assert cmp.answer_text(b, hand, "r") == "no"
    assert cmp.facts_text(a, "f") == ""
    assert cmp.facts_text(b, "r").startswith("concerns_living_things: False; topic: administrative")


def test_the_rules_check_finds_a_changed_row(tmp_path):
    rows = pd.DataFrame([{c: "" for c in cmp.RULES_COLUMNS} | {"record_id": r, "bc_class": "bc", "box_share": 0.5}
                         for r in ["b", "a"]], columns=cmp.RULES_COLUMNS)
    path = tmp_path / "rules.csv"
    rows.to_csv(path, index=False)
    assert cmp.rules_differences(rows, path) == 0
    changed = rows.copy()
    changed.loc[0, "bc_class"] = "needs_llm"
    assert cmp.rules_differences(changed, path) == 1
    with pytest.raises(SystemExit):
        cmp.rules_differences(rows.iloc[:1], path)


def filter3_rows(rows):
    """Return a small filter 3 table, every value as text, from (record_id, group, shown, decided_by) tuples."""
    frame = pd.DataFrame([{"record_id": r, "dup_group_id": g, "dup_show": str(s), "decided_by": d,
                           "final_class": "bc_only", "frontend_group": "bc_data"} for r, g, s, d in rows])
    return frame.set_index("record_id", drop=False)


def test_copy_group_effects_of_records_no_longer_kept():
    table = filter3_rows([("a1", "G0001", True, "llm"), ("a2", "G0001", False, "llm"),
                          ("b1", "G0002", True, "llm"), ("b2", "G0002", False, "llm"),
                          ("c1", "G0003", True, "llm"), ("c2", "G0003", False, "llm"), ("d", "", True, "rule")])
    effects = cmp.group_effects({"a1", "b2", "c1", "c2", "d"}, table, {"a2", "b1"})
    assert effects == {"groups_losing_a_copy": 3, "groups_losing_their_shown_row": 1, "groups_gone_entirely": 1}


def test_newly_kept_copies_of_filter3_records():
    uuid = "11111111-2222-3333-4444-555555555555"
    harvest = pd.DataFrame({"title": ["Kelp", "Kelp", "Other", "Lone"], "abstract": ["A kelp survey."] * 2 + ["x", ""]},
                           index=[f"oai:one.ca:{uuid}", f"oai:two.ca:{uuid}", "oai:one.ca:t", "oai:two.ca:lone"])
    table = filter3_rows([(f"oai:one.ca:{uuid}", "G0001", True, "hand"), ("oai:one.ca:t", "", True, "llm")])
    copies = cmp.newly_kept_copies({f"oai:two.ca:{uuid}", "oai:two.ca:lone"},
                                   {f"oai:one.ca:{uuid}", "oai:one.ca:t"}, table, harvest)
    assert copies[["record_id", "copy_of", "copy_decided_by", "catalogue"]].values.tolist() == [
        [f"oai:two.ca:{uuid}", f"oai:one.ca:{uuid}", "hand", "two.ca"]]


def test_reviewed_records_are_lined_up(tmp_path):
    labels = pd.DataFrame({"record_id": ["same", "differs", "gone", "unreviewed"],
                           "is_biodiversity": ["yes", "yes", "no", "no"],
                           "reviewed_by_hand": [True, True, True, False]})
    test = cmp.read_answers(write_answers(tmp_path, [answer("same"), answer("differs"), answer("gone")], "t.csv"))
    full = cmp.read_answers(write_answers(tmp_path, [answer("same"), answer("differs", kept=False, tokens=999)],
                                          "f.csv"))
    rows = cmp.reviewed_compare(labels, test, full).set_index("record_id")
    assert list(rows.index) == ["same", "differs", "gone"]
    assert rows.loc["differs", ["test900", "full_run", "same_input_tokens"]].tolist() == ["yes", "no", False]
    assert rows.loc["gone", "full_run"] == "not in the harvest"
    assert rows.loc["same", "facts_that_differ"] == ""


def test_the_fir_input_folder_is_never_overwritten(tmp_path):
    with pytest.raises(SystemExit, match="already exists"):
        cmp.prepare_fir_input("rules.csv", "harvest.parquet", "bc.wkt", str(tmp_path))


def test_the_rules_columns_match_rules_v2():
    path = BACKEND / cmp.RULES_V2
    if not path.exists():
        pytest.skip("rules_2026_10_v2.csv is not on this machine")
    assert list(pd.read_csv(path, nrows=0).columns) == cmp.RULES_COLUMNS
