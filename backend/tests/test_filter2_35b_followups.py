"""Tests for pipeline/lunaris/filter2_35b_followups.py, on made-up records:
the reviewed records outside the harvest are left out, a test run missing a
record stops, a full run is scored by its keep set, the scores match
score_trial.score, disagreements are listed heaviest first, the sample of 40
is fixed and drawn from the right records, the record blocks hold nothing of
ours, and no earlier file is written over."""

import sys
from pathlib import Path

import pandas as pd
import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "pipeline" / "lunaris"))
sys.path.insert(0, str(BACKEND / "tests"))
import filter2_35b_followups as fu  # noqa: E402
# The made-up answers and hand decisions are shared with the comparison's tests.
from test_compare_filter2_35b import answer, hand_table, write_answers  # noqa: E402


def labels_table():
    """Return four reviewed labels (one outside the harvest) and one unreviewed, with weights."""
    return pd.DataFrame({"record_id": ["a", "b", "c", "gone", "unreviewed"],
                         "title": ["A", "B", "C", "G", "U"],
                         "is_biodiversity": ["yes", "yes", "no", "yes", "no"],
                         "stratum": ["kept", "dropped", "kept", "kept", "dropped"],
                         "review_weight": [10.0, 200.0, 10.0, 10.0, 5.0],
                         "reviewed_by_hand": [True, True, True, True, False]})


def test_only_reviewed_records_in_the_harvest_are_scored():
    labels = fu.reviewed_in_harvest(labels_table(), {"a", "b", "c", "unreviewed"})
    assert list(labels.index) == ["a", "b", "c"]


def test_a_test_run_missing_a_record_stops(tmp_path):
    answers = fu.cmp.read_answers(write_answers(tmp_path, [answer("a"), answer("b", error="cut off")]))
    with pytest.raises(SystemExit, match="no answer"):
        fu.verdicts_from_answers(answers, ["a", "b"])
    with pytest.raises(SystemExit, match="no answer"):
        fu.verdicts_from_answers(answers, ["a", "c"])
    assert fu.verdicts_from_answers(answers, ["a"]).tolist() == ["yes"]


def test_a_full_run_is_scored_by_its_keep_set():
    assert fu.verdicts_from_keep({"a", "c"}, ["a", "b", "c"]).to_dict() == {"a": "yes", "b": "no", "c": "yes"}


def test_scores_are_weighted_like_score_table():
    labels = fu.reviewed_in_harvest(labels_table(), {"a", "b", "c"})
    run = pd.Series({"a": "yes", "b": "no", "c": "yes"})
    row = fu.fair_rows(labels, {"run": run}).iloc[0]
    # Kept a (real, 10) and c (junk, 10); lost b (real, 200).
    assert (row["records"], row["junk_kept"], row["real_lost"], row["keeps"]) == (3, 10.0, 200.0, 20.0)
    assert row["precision"] == pytest.approx(0.5)
    assert row["recall"] == pytest.approx(10 / 210)


def test_disagreements_are_listed_heaviest_first(tmp_path):
    labels = fu.reviewed_in_harvest(labels_table(), {"a", "b", "c"})
    answers = fu.cmp.read_answers(write_answers(tmp_path, [answer(r) for r in "abc"]))
    four = pd.Series({"a": "yes", "b": "yes", "c": "yes"})
    big = pd.Series({"a": "no", "b": "no", "c": "yes"})
    rows = fu.full_runs_disagree(labels, four, big, answers, answers)
    assert rows["record_id"].tolist() == ["b", "a"]
    assert rows["right"].tolist() == ["4b", "4b"]
    assert rows["weight"].tolist() == [200.0, 10.0]


def filter3_table():
    """Return a small filter 3 table with three bc_data records and one pending."""
    frame = pd.DataFrame({"record_id": ["oai:x.ca:1", "oai:x.ca:2", "oai:y.org:3", "oai:y.org:4"],
                          "frontend_group": ["bc_data", "bc_data", "bc_data", "pending"],
                          "final_class": ["bc_only", "bc_only", "canada_incl_bc", "all_canada"]})
    return frame.set_index("record_id", drop=False)


def test_the_dropped_bc_data_records_and_their_split(tmp_path):
    table = filter3_table()
    dropped = fu.bc_data_dropped(table, {"oai:x.ca:2"})
    assert dropped == ["oai:x.ca:1", "oai:y.org:3"]
    answers = fu.cmp.read_answers(write_answers(tmp_path, [answer("oai:x.ca:1", kept=False),
                                                           answer("oai:y.org:3", error="x")]))
    split = fu.split_counts(dropped, answers, table)
    assert split[split["split"] == "35b topic"].set_index("value")["records"].to_dict() == {
        "administrative": 1, "(failed)": 1}
    assert split[split["split"] == "catalogue"]["records"].tolist() == [1, 1]


def test_the_sample_is_fixed_and_in_the_order_drawn():
    ids = [f"r{n:03d}" for n in range(341)]
    sample = fu.draw_sample(ids)
    assert sample == fu.draw_sample(list(reversed(ids)))
    assert len(sample) == 40 == len(set(sample)) and set(sample) <= set(ids)
    assert sample != sorted(sample)


def test_a_record_block_holds_only_the_harvest():
    record = pd.Series({"title": "Kelp", "subjects": ["kelp", "BC"], "abstract": "A survey.\nTwo lines.",
                        "places": None, "points": "[1, 2]", "doi": "10.1/x"})
    block = fu.record_block(7, "oai:x.ca:1", record)
    assert block == ("[07]\nid: oai:x.ca:1\nsource catalogue: x.ca\ntitle: Kelp\nsubjects: [\"kelp\", \"BC\"]\n"
                     "abstract: A survey.\nTwo lines.\nplaces: ")


def test_sample_answers_are_numbered_like_the_records(tmp_path):
    table = filter3_table()
    a = fu.cmp.read_answers(write_answers(tmp_path, [answer("oai:x.ca:1"), answer("oai:y.org:3")], "a.csv"))
    b = fu.cmp.read_answers(write_answers(tmp_path, [answer("oai:x.ca:1", kept=False),
                                                     answer("oai:y.org:3", kept=False)], "b.csv"))
    rows = fu.sample_answers(["oai:y.org:3", "oai:x.ca:1"], a, b, hand_table([]), table)
    assert rows["number"].tolist() == ["01", "02"]
    assert rows.loc[0, ["record_id", "verdict_4b", "verdict_35b", "topic_35b", "reason_35b",
                        "filter3_final_class"]].tolist() == ["oai:y.org:3", "yes", "no", "administrative",
                                                             "because", "canada_incl_bc"]


def test_no_earlier_file_is_written_over(tmp_path):
    (tmp_path / "there.csv").write_text("x")
    with pytest.raises(SystemExit, match="already exists"):
        fu.fresh_path(str(tmp_path / "there.csv"))
    assert fu.fresh_path(str(tmp_path / "new.csv")).endswith("new.csv")
