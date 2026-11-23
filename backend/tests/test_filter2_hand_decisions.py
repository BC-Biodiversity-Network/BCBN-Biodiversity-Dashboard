"""Tests for the DataONE filter 2 hand decisions
(pipeline/dataone/filter2_hand_decisions.csv) and how build_filter2_union.py
applies them.

The file has the Lunaris file's columns and is read by the same function
(compare_filter2_35b.read_hand_decisions). A hand decision overrides the
models, is marked keep_source "hand" with its reason and the keep_source
before, and a record that failed in both models and has one is no longer
in failed_both. A hand decision for a record not in the union stops the
script. The union tests use a small made-up table; one test checks the real
file against the real union when it is on this machine."""

import sys
from pathlib import Path

import pandas as pd
import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "pipeline" / "dataone"))
import build_filter2_union as bfu  # noqa: E402
from compare_filter2_35b import read_hand_decisions  # noqa: E402

DATAONE_HAND = BACKEND / "pipeline" / "dataone" / "filter2_hand_decisions.csv"
LUNARIS_HAND = BACKEND / "pipeline" / "lunaris" / "filter2_hand_decisions.csv"
UNION = BACKEND / "data" / "dataone" / "filter2_results" / "filter2_union.parquet"
DECIDED_BY = "Claude's reading, approved by Lucia, 2026-11-23"


def small_union():
    """Return a made-up union of four records, as build_union leaves it before the hand decisions."""
    union = pd.DataFrame({"id": ["failed1", "only4b", "both1", "lun1"],
                          "answer_4b": ["failed", "yes", "yes", ""],
                          "answer_35b": ["failed", "no", "yes", ""],
                          "keep_union": pd.array([None, True, True, False], dtype="boolean"),
                          "keep_source": ["failed", "4b_only", "both", "lunaris"],
                          "failure_4b": ["bad_json", "", "", ""], "failure_35b": ["bad_json", "", "", ""],
                          "hand_reason": "", "before_hand": ""})
    return union


def hand_table(rows):
    """Return hand decisions as read_hand_decisions gives them, from (record_id, kept, reason) rows."""
    frame = pd.DataFrame([{"record_id": i, "kept": k, "reason": r, "decided_by": DECIDED_BY,
                           "decided_on": "2026-11-23", "recorded_in": "", "quote_from_record": ""}
                          for i, k, r in rows])
    return frame.set_index("record_id", drop=False)


# --- The file ----------------------------------------------------------------

def test_the_file_has_the_lunaris_columns_and_the_seven_decisions():
    lunaris = pd.read_csv(LUNARIS_HAND, dtype=str, keep_default_na=False)
    hand = read_hand_decisions(str(DATAONE_HAND))
    assert list(hand.columns) == list(lunaris.columns)
    assert len(hand) == 7
    assert hand["kept"].value_counts().to_dict() == {"no": 5, "yes": 2}
    assert (hand["decided_by"] == DECIDED_BY).all() and (hand["decided_on"] == "2026-11-23").all()
    assert (hand["reason"] != "").all() and (hand["quote_from_record"] != "").all()


def test_a_repeated_id_or_a_kept_other_than_yes_or_no_stops(tmp_path):
    good = pd.read_csv(DATAONE_HAND, dtype=str, keep_default_na=False)
    twice = tmp_path / "twice.csv"
    pd.concat([good, good.head(1)]).to_csv(twice, index=False)
    with pytest.raises(SystemExit):
        read_hand_decisions(str(twice))
    maybe = tmp_path / "maybe.csv"
    good.assign(kept="maybe").to_csv(maybe, index=False)
    with pytest.raises(SystemExit):
        read_hand_decisions(str(maybe))


# --- Applying it ---------------------------------------------------------------

def test_a_hand_decision_overrides_the_models_and_is_marked():
    hand = hand_table([("failed1", "yes", "About populations."), ("only4b", "no", "Human health.")])
    out = bfu.apply_hand(small_union(), hand).set_index("id")
    assert out.loc["failed1", ["keep_union", "keep_source", "before_hand", "hand_reason"]].tolist() == \
        [True, "hand", "failed", "About populations."]
    assert out.loc["only4b", ["keep_union", "keep_source", "before_hand", "hand_reason"]].tolist() == \
        [False, "hand", "4b_only", "Human health."]
    # The models' answers stay as they were.
    assert out.loc["only4b", ["answer_4b", "answer_35b"]].tolist() == ["yes", "no"]
    # Records without a hand decision are unchanged.
    untouched = small_union().set_index("id").loc[["both1", "lun1"]]
    assert out.loc[["both1", "lun1"]].equals(untouched)


def test_a_hand_decision_for_a_record_not_in_the_union_stops():
    with pytest.raises(SystemExit):
        bfu.apply_hand(small_union(), hand_table([("not_there", "yes", "x")]))


def test_a_failed_record_with_a_hand_decision_is_no_longer_failed():
    before = bfu.failed_both(small_union()[small_union()["keep_source"] != "failed"])
    assert before.empty and list(before.columns)[:3] == ["id", "datasource", "group"]
    out = bfu.apply_hand(small_union(), hand_table([("failed1", "no", "Physical environment.")]))
    failed = bfu.failed_both(out)
    assert failed.empty
    assert list(failed.columns) == ["id", "datasource", "group", "failure_4b", "failure_35b", "next_step",
                                    "title", "abstract_start", "link"]


def test_the_real_decisions_name_records_in_the_union():
    if not UNION.exists():
        pytest.skip("filter2_union.parquet is not on this machine")
    union = pd.read_parquet(UNION, columns=["id", "keep_source", "keep_union"])
    hand = read_hand_decisions(str(DATAONE_HAND))
    assert set(hand.index) <= set(union["id"])
    decided = union.set_index("id").loc[hand.index]
    assert (decided["keep_source"] == "hand").all()
    assert decided["keep_union"].map({True: "yes", False: "no"}).tolist() == hand["kept"].tolist()
