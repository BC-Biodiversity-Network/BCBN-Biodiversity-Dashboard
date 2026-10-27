"""Tests for pipeline/lunaris/filter3_union_sample.py, on made-up rows: only
bc_data records kept only by 35b and shown for their copy group are in the
pool, and the answers hold the 35b facts and the filter 3 evidence by
number."""

import sys
from pathlib import Path

import pandas as pd

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "pipeline" / "lunaris"))
sys.path.insert(0, str(BACKEND / "tests"))
import filter3_union_sample as us  # noqa: E402
# The made-up answers and hand decisions are shared with the comparison's tests.
from test_compare_filter2_35b import answer, hand_table, write_answers  # noqa: E402


def union_table():
    """Return made-up union rows as read from the CSV (every value as text)."""
    rows = [("keep", "bc_data", "35b_only", "True"), ("hidden_copy", "bc_data", "35b_only", "False"),
            ("both_runs", "bc_data", "both", "True"), ("pending", "pending", "35b_only", "True"),
            ("also", "bc_data", "35b_only", "True")]
    frame = pd.DataFrame(rows, columns=["record_id", "frontend_group", "filter2_keep_source", "dup_show"])
    return frame.assign(final_class="bc_only", decided_by="rule", rule_name="box", rule_evidence="box inside BC",
                        in_bc_used="", evidence="", reason="", note="")


def test_the_pool_is_new_bc_data_shown_rows():
    assert us.union_pool(union_table()) == ["also", "keep"]


def test_the_answers_hold_the_35b_facts_and_the_filter3_evidence(tmp_path):
    answers = us.cmp.read_answers(write_answers(tmp_path, [answer("keep"), answer("also")]))
    rows = us.union_answers(["keep", "also"], answers, hand_table([]), union_table())
    assert rows["number"].tolist() == ["01", "02"]
    assert rows.loc[0, ["verdict_35b", "topic_35b", "filter3_final_class", "filter3_decided_by",
                        "filter3_rule_name", "filter3_rule_evidence", "filter3_model_in_bc"]].tolist() == [
        "yes", "habitat_or_ecosystem", "bc_only", "rule", "box", "box inside BC", ""]
    assert list(rows.columns)[-len(us.FILTER3_COLUMNS):] == list(us.FILTER3_COLUMNS.values())
