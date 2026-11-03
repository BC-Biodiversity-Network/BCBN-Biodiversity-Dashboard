"""Tests for pipeline/lunaris/build_filter3_union.py, on made-up records: the
Fir run check stops with a clear message for each way a run can be missing
or wrong, the keep sources are named and the hand no is left out, reused rows
are checked against final_of, the self-check gives the old table back, and
the union works copy groups and hand checks out again over old and new
records together, a hand "no" leaves out reused and new rows alike (listed
by removed_by_hand), and the later filter 3 hand decisions are applied in the
union only (listed by hand_applied)."""

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "pipeline" / "lunaris"))
sys.path.insert(0, str(BACKEND / "tests"))
import build_filter3_union as un  # noqa: E402
from test_compare_filter2_35b import hand_table  # noqa: E402
# The made-up records of filter 3's own tests.
from test_filter3_final import UUID_A, f3, made_up  # noqa: E402

CATALOGUES = ["catalogue.hakai.org"]
OLD_IDS = ["oai:x:bc1", "oai:x:nb1", f"oai:canwin:{UUID_A}", "oai:catalogue.hakai.org:h1"]
NEW_IDS = [f"oai:open.canada.ca:{UUID_A}", "oai:catalogue.hakai.org:h2"]


# --- The Fir run check -----------------------------------------------------

def write_run(folder, ids, model="qwen3.6:35b-a3b", flash="off", repeat=False):
    """Write a made-up run folder: both answer files, run.json and flash_attention.txt."""
    for part in ["pass1", "pass2"]:
        (folder / part).mkdir(parents=True, exist_ok=True)
        lines = [json.dumps({"record_id": r}) for r in ids + (ids[:1] if repeat else [])]
        (folder / part / "answers.jsonl").write_text("\n".join(lines) + "\n")
    (folder / "run.json").write_text(json.dumps({"model": model, "pass1_prompt": "v3", "pass2_prompt": "C4"}))
    (folder / "flash_attention.txt").write_text(f"{flash}\nrecorded by job 1\n")
    return folder


def test_a_complete_run_passes(tmp_path):
    about = un.check_run(str(write_run(tmp_path / "run", ["a", "b"])), {"a", "b"})
    assert about["model"] == "qwen3.6:35b-a3b"


def test_a_missing_run_folder_says_how_to_copy_it_back(tmp_path):
    with pytest.raises(SystemExit, match="is not there yet") as stop:
        un.check_run(str(tmp_path / "nothing"), {"a"})
    assert "scp -r 'songyanf@fir.alliancecan.ca:/scratch/songyanf/lunaris_llm/bcf/runs/" in str(stop.value)


def test_missing_merged_files_stop(tmp_path):
    run = write_run(tmp_path / "run", ["a"])
    (run / "pass2" / "answers.jsonl").rename(run / "pass2" / "answers_part0.jsonl")
    with pytest.raises(SystemExit, match="merged files are not written yet"):
        un.check_run(str(run), {"a"})


@pytest.mark.parametrize("ids, repeat", [(["a"], False), (["a", "b", "c"], False), (["a", "x"], False),
                                         (["a", "b"], True)])
def test_a_run_without_exactly_the_records_once_stops(tmp_path, ids, repeat):
    run = write_run(tmp_path / "run", ids, repeat=repeat)
    with pytest.raises(SystemExit, match="must have exactly the 2 needs_llm records"):
        un.check_run(str(run), {"a", "b"})


def test_another_model_or_flash_attention_stops(tmp_path):
    with pytest.raises(SystemExit, match="run.json says"):
        un.check_run(str(write_run(tmp_path / "one", ["a"], model="qwen3.5:9b")), {"a"})
    with pytest.raises(SystemExit, match="flash attention on"):
        un.check_run(str(write_run(tmp_path / "two", ["a"], flash="on")), {"a"})


# --- Keep sources ----------------------------------------------------------

def test_keep_sources_name_the_run_and_leave_out_the_hand_no():
    hand = hand_table([("h_yes", "yes"), ("h_no", "no")])
    sources = un.keep_sources({"both", "four", "h_yes", "h_no"}, {"both", "big", "h_yes"}, hand)
    assert sources.to_dict() == {"big": "35b_only", "both": "both", "four": "4b_only", "h_yes": "both"}
    with pytest.raises(SystemExit, match="kept by neither"):
        un.keep_sources({"a"}, {"b"}, hand_table([("c", "yes")]))


# --- The table -------------------------------------------------------------

def old_and_new(tmp_path, monkeypatch, hakai_hand=None):
    """
    Return a made-up old filter 3 table (built by build_filter3_final and read
    back from its CSV, as the union reads it), the new records' rows, the
    keep sources and the harvest.
    """
    if hakai_hand is not None:
        monkeypatch.setattr(f3, "HAKAI_HAND", hakai_hand)
    rules, pass1, pass2, harvest = made_up()
    old_rules = rules[rules["record_id"].isin(OLD_IDS)].reset_index(drop=True)
    new_rules = rules[rules["record_id"].isin(NEW_IDS)].reset_index(drop=True)
    pick = lambda results, ids: {r: v for r, v in results.items() if r in ids}  # noqa: E731
    old = f3.merge(old_rules, pick(pass1, OLD_IDS), pick(pass2, OLD_IDS), harvest, CATALOGUES)
    f3.add_duplicates(old, harvest)
    old.to_csv(tmp_path / "old.csv", index=False)
    old = pd.read_csv(tmp_path / "old.csv", low_memory=False)
    new = f3.record_rows(new_rules, pick(pass1, NEW_IDS), pick(pass2, NEW_IDS), harvest, CATALOGUES)
    sources = pd.Series({**{r: "both" for r in OLD_IDS[:3]}, OLD_IDS[3]: "4b_only",
                         **{r: "35b_only" for r in NEW_IDS}})
    return old, new, sources, harvest


def test_the_self_check_gives_the_old_table_back(tmp_path, monkeypatch):
    old, _, _, harvest = old_and_new(tmp_path, monkeypatch)
    assert un.self_check(old, harvest, CATALOGUES) == []
    changed = old.copy()
    changed.loc[changed["record_id"] == "oai:x:nb1", "dup_show"] = False
    assert un.self_check(changed, harvest, CATALOGUES) == ["oai:x:nb1"]


def test_a_reused_row_that_does_not_match_final_of_stops(tmp_path, monkeypatch):
    old, _, _, _ = old_and_new(tmp_path, monkeypatch)
    rows = un.reused_rows(old, CATALOGUES)
    assert not set(un.WHOLE_SET_COLUMNS[3:]) & set(rows.columns)
    assert rows.set_index("record_id").loc["oai:catalogue.hakai.org:h1", "decided_by"] == "bc_only_catalogue"
    old.loc[old["record_id"] == "oai:x:bc1", "final_class"] = "elsewhere"
    with pytest.raises(SystemExit, match="do not match final_of"):
        un.reused_rows(old, CATALOGUES)


def test_the_union_works_copy_groups_out_again(tmp_path, monkeypatch):
    old, new, sources, harvest = old_and_new(tmp_path, monkeypatch)
    table = un.build_union(old, new, sources, harvest, CATALOGUES).set_index("record_id")
    assert list(table.columns)[-1] == "filter2_keep_source" and len(table) == 6
    assert table.loc[NEW_IDS, "filter2_keep_source"].tolist() == ["35b_only", "35b_only"]
    # The CanWIN record had no copy in the old table; the new open.canada.ca record is its copy.
    assert old.set_index("record_id").at[f"oai:canwin:{UUID_A}", "dup_group_size"] == 1
    pair = table.loc[[f"oai:canwin:{UUID_A}", f"oai:open.canada.ca:{UUID_A}"]]
    assert set(pair["dup_group_size"]) == {2} and pair["dup_group_id"].nunique() == 1
    assert pair["dup_show"].sum() == 1
    changes = un.changes_vs_old(table.reset_index(), old)
    assert changes["record_id"].tolist() == [f"oai:canwin:{UUID_A}"]
    assert "dup_group_size" in changes.iloc[0]["changed"]


def test_a_hand_check_passes_to_a_new_copy(tmp_path, monkeypatch):
    checked = f"oai:canwin:{UUID_A}"
    old, new, sources, harvest = old_and_new(tmp_path, monkeypatch, {checked: ("bc_only", "by hand")})
    table = un.build_union(old, new, sources, harvest, CATALOGUES).set_index("record_id")
    copy = table.loc[f"oai:open.canada.ca:{UUID_A}"]
    assert (copy["final_class"], copy["decided_by"]) == ("bc_only", "hand")
    assert f"hand check applied via copy {checked}" in copy["note"]
    assert copy["llm_class"] == "bc_only" and copy["before_hand"] == "bc_only (llm)"


def test_the_union_must_be_exactly_the_keep_set(tmp_path, monkeypatch):
    old, new, sources, harvest = old_and_new(tmp_path, monkeypatch)
    with pytest.raises(SystemExit, match="not exactly the union keep set"):
        un.build_union(old, new, sources.drop(NEW_IDS[0]), harvest, CATALOGUES)


def test_a_hand_no_leaves_out_a_reused_row_and_a_new_one(tmp_path, monkeypatch):
    old, new, sources, harvest = old_and_new(tmp_path, monkeypatch)
    hand = hand_table([("oai:x:nb1", "no"), (NEW_IDS[1], "no")])
    hand["recorded_in"] = "review"
    no = frozenset(hand.index[hand["kept"] == "no"])
    kept = sources.drop(list(no))
    table = un.build_union(old, new, kept, harvest, CATALOGUES, no).set_index("record_id")
    assert not no & set(table.index) and len(table) == 4
    removed = un.removed_by_hand(old, new, hand).set_index("record_id")
    assert removed.loc["oai:x:nb1", ["in_filter3_final", "final_class", "frontend_group", "reason"]].tolist() == [
        "yes", "elsewhere", "dropped", "why"]
    assert removed.loc[NEW_IDS[1], ["in_filter3_final", "frontend_group", "recorded_in"]].tolist() == [
        "no", "", "review"]
    # A reused row the keep set leaves out without a hand "no" still stops.
    with pytest.raises(SystemExit, match="not exactly the union keep set"):
        un.build_union(old, new, kept, harvest, CATALOGUES)
    assert un.changes_vs_old(table.reset_index(), old)["record_id"].tolist() == [f"oai:canwin:{UUID_A}"]


def test_later_filter3_hand_decisions_apply_in_the_union_only(tmp_path, monkeypatch):
    old, new, sources, harvest = old_and_new(tmp_path, monkeypatch)
    checked = f"oai:canwin:{UUID_A}"
    extra = {checked: ("elsewhere", "checked by hand: L (filter3_hand_decisions.csv)")}
    table = un.build_union(old, new, sources, harvest, CATALOGUES, extra_checks=extra)
    by_id = table.set_index("record_id")
    assert by_id.loc[[checked, NEW_IDS[0]], "final_class"].tolist() == ["elsewhere", "elsewhere"]
    assert set(by_id.loc[[checked, NEW_IDS[0]], "frontend_group"]) == {"dropped"}
    applied = un.hand_applied(table, extra)
    assert sorted(applied["record_id"]) == sorted([checked, NEW_IDS[0]])
    assert applied.set_index("record_id").at[checked, "before_hand"] == "all_canada (llm)"
    # The self-check never sees the later decisions, so filter3_final is still given back.
    assert un.self_check(old, harvest, CATALOGUES) == []

