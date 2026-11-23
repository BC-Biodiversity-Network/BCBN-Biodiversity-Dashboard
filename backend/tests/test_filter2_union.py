"""Tests for the DataONE filter 2 union (pipeline/dataone/build_filter2_union.py)
and its shared helpers (pipeline/dataone/filter2_common.py), on made-up
records and made-up run folders in a temporary folder.

The shared helpers: rule_of follows decide() for every rule, read_trial
reads a merged CSV as before, id_pattern sorts ids. The reruns of the cut
prompts: a rerun folder that is not there (or not merged yet) is reported
and leaves the cut records failed; a merged rerun is read and checked like
a full run, and its answers replace the cut ones while records it could not
answer keep its reason (context_full, bad_json, not_run)."""

import gzip
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "pipeline" / "dataone"))
sys.path.insert(0, str(BACKEND / "pipeline" / "lunaris" / "llm"))
sys.path.insert(0, str(BACKEND / "exploration" / "llm"))
import build_filter2_union as bfu  # noqa: E402
import filter2_common as common  # noqa: E402
from prompt_variants import instructions_sha1  # noqa: E402
from score_trial import decide  # noqa: E402

MERGE = BACKEND / "pipeline" / "lunaris" / "llm" / "merge_results.py"
DIGEST = "d8b0f5e9760cd1682034f292d7ef72ec46f432149be0df7574bf2d6e92e38c04"

# An answer that fits prompt v6's schema; facts are changed per test.
ANSWER = {"reason": "Counts of nesting eagles.", "concerns_living_things": True, "organisms": ["eagle"],
          "organism_level": "species", "topic": "organisms", "about_organisms_themselves": "not_applicable",
          "ecological_purpose_stated": "not_applicable", "experimental_animals": "no", "extinct_only": "no",
          "form": "dataset", "language": "english"}

# One record per decide() rule, with the letter it should give.
RULE_CASES = [
    ({"experimental_animals": "yes"}, "a"), ({"extinct_only": "yes"}, "b"),
    ({"topic": "physical_environment"}, "c"), ({"topic": "human_health"}, "d"),
    ({"topic": "administrative"}, "e"), ({"concerns_living_things": False, "topic": "other"}, "f"),
    ({"topic": "forestry_agriculture_fisheries", "about_organisms_themselves": "yes"}, "g"),
    ({"topic": "forestry_agriculture_fisheries", "about_organisms_themselves": "no"}, "g"),
    ({"topic": "land_and_boundaries", "ecological_purpose_stated": "yes"}, "h"),
    ({"topic": "land_and_boundaries", "ecological_purpose_stated": "no"}, "h"),
    ({}, "i"),
]


# --- The shared helpers ------------------------------------------------------

@pytest.mark.parametrize("change,letter", RULE_CASES)
def test_rule_of_names_the_rule_decide_used(change, letter):
    record = dict(ANSWER, **change)
    assert common.rule_of(record) == letter
    assert common.answer_of_rule(letter, record) == decide(record)


def test_id_pattern():
    assert common.id_pattern("0000aa6924377b6a7e5ab59bcec5d4f3") == "hex32"
    assert common.id_pattern("sha256:0189a291bc5a") == "sha256"
    assert common.id_pattern("doi:10.5061/dryad.x") == "doi"
    assert common.id_pattern("{B1F2AA6C-62CD-48FF-AE2F-A143E3F1F3DF}") == "other"


def test_read_trial_leaves_out_failed_rows_and_adds_verdict_and_rule(tmp_path):
    path = tmp_path / "trial.csv"
    rows = [dict(ANSWER, record_id="a"), dict(ANSWER, record_id="b", topic="physical_environment"),
            {"record_id": "c", "model_error": "reply was not valid JSON"}]
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")
    frame, failed = common.read_trial(path)
    assert failed == 1
    assert list(frame["record_id"]) == ["a", "b"]
    assert list(frame["verdict"]) == ["yes", "no"] and list(frame["rule"]) == ["i", "c"]


def test_read_trial_stops_on_a_living_things_value_that_is_not_true_or_false(tmp_path):
    path = tmp_path / "trial.csv"
    pd.DataFrame([dict(ANSWER, record_id="a", concerns_living_things="maybe")]).to_csv(path, index=False)
    with pytest.raises(SystemExit):
        common.read_trial(path)


# --- Small parts of the union -------------------------------------------------

def test_keep_source():
    assert bfu.source_of(True, True) == "both"
    assert bfu.source_of(True, False) == "4b_only" and bfu.source_of(None, True) == "35b_only"
    assert bfu.source_of(False, None) == "neither" and bfu.source_of(None, None) == "failed"


def test_next_step():
    assert bfu.next_step("prompt_cut", "prompt_cut").startswith("rerun with NUM_CTX=65536")
    assert bfu.next_step("context_full", "bad_json").startswith("rerun with a larger context")
    assert bfu.next_step("not_run", "bad_json") == "finish the rerun"
    assert bfu.next_step("bad_json", "bad_json") == "hand decision"


def full_table():
    """Return a made-up full run table: two cut records, one answered, one bad_json."""
    table = pd.DataFrame({"id": ["cut1", "cut2", "ok", "bad"],
                          "answer": ["failed", "failed", "yes", "failed"],
                          "rule": ["", "", "i", ""],
                          "failure": ["prompt_cut", "prompt_cut", "", "bad_json"],
                          "input_tokens": [8194, 8194, 4515, None],
                          "cut": [True, True, False, False]})
    return table.set_index("id")


def test_without_a_rerun_the_table_is_unchanged():
    out = bfu.apply_rerun(full_table(), None)
    assert out.drop(columns="rerun").equals(full_table())
    assert not out["rerun"].any()


def test_a_rerun_replaces_only_its_records():
    rerun = pd.DataFrame({"id": ["cut1", "cut2"], "answer": ["no", "failed"], "rule": ["c", ""],
                          "failure": ["", "context_full"], "input_tokens": [30000, None],
                          "cut": [False, False]}).set_index("id")
    out = bfu.apply_rerun(full_table(), rerun)
    assert out.loc["cut1", ["answer", "rule", "failure"]].tolist() == ["no", "c", ""]
    assert out.loc["cut2", ["answer", "failure"]].tolist() == ["failed", "context_full"]
    assert out.loc[["ok", "bad"]].drop(columns="rerun").equals(full_table().loc[["ok", "bad"]])
    assert out["rerun"].tolist() == [True, True, False, False]
    # The full run's cut column is kept: it says what happened in the full run.
    assert out["cut"].tolist() == [True, True, False, False]


# --- Reading a rerun folder ----------------------------------------------------

def test_a_missing_rerun_is_reported_not_a_crash(tmp_path, monkeypatch):
    monkeypatch.setitem(bfu.RERUN_DIRS, "4b", str(tmp_path / "not_copied_back"))
    table, reason = bfu.read_rerun("4b", {"cut1"}, DIGEST)
    assert table is None and reason == "not there yet"


def test_a_rerun_that_is_not_merged_yet_is_reported(tmp_path, monkeypatch):
    run_dir = make_rerun_folder(tmp_path, merge=False)
    monkeypatch.setitem(bfu.RERUN_DIRS, "4b", str(run_dir))
    table, reason = bfu.read_rerun("4b", {"r1", "r2", "r3", "r4"}, DIGEST)
    assert table is None and reason.startswith("not merged yet")


def make_rerun_folder(tmp_path, merge=True):
    """
    Make a small 4b rerun folder of four records in run_batch.py's format:
    r1 answered (yes), r2 answered (physical environment, no), r3 failed
    with context_full, r4 not reached. With merge, merge it with the real
    merge_results.py --allow-incomplete, as on Fir.
    """
    run_dir = tmp_path / "fir_dataone_cut_rerun_2026_11_23"
    for folder in ("inputs", "results", "errors", "logs"):
        (run_dir / folder).mkdir(parents=True)
    ids = ["r1", "r2", "r3", "r4"]
    records = [{"position": n, "record_id": i, "title": f"Title {i}", "subjects": None, "abstract": None,
                "prompt_sha1": f"sha-{i}"} for n, i in enumerate(ids)]
    with gzip.open(run_dir / "inputs" / "batch_0000.jsonl.gz", "wt", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")
    run = {"records": 4, "batches": 1, "prompt_version": 6, "prompt_variant": "v6_dataone",
           "instructions_sha1": instructions_sha1("v6_dataone"), "model": "qwen3.5:4b", "label": "qwen3.5:4b-fir",
           "rerun_of": {"run": "data/dataone/filter2_input/dataone_harvest_2026_11_18"}}
    (run_dir / "run.json").write_text(json.dumps(run), encoding="utf-8")
    answers = {"r1": ANSWER, "r2": dict(ANSWER, topic="physical_environment")}
    with open(run_dir / "results" / "batch_0000.jsonl", "w", encoding="utf-8") as handle:
        for i, answer in answers.items():
            handle.write(json.dumps({"record_id": i, "model": "qwen3.5:4b", "model_digest": DIGEST,
                                     "prompt_version": 6, "prompt_sha1": f"sha-{i}", "num_ctx": 65536,
                                     "answer": answer, "input_tokens": 30000, "seconds": 5.0,
                                     "finished": "2026-11-24T00:00:00Z"}) + "\n")
    with open(run_dir / "errors" / "batch_0000.jsonl", "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"record_id": "r3", "error": "the prompt filled the whole context (65530 of 65536)",
                                 "kind": "context_full", "num_ctx": 65536}) + "\n")
    if merge:
        subprocess.run([sys.executable, str(MERGE), str(run_dir), "--allow-incomplete"], check=True,
                       capture_output=True)
    return run_dir


def test_a_merged_rerun_is_read_and_its_answers_replace_the_cut_ones(tmp_path, monkeypatch):
    run_dir = make_rerun_folder(tmp_path)
    monkeypatch.setitem(bfu.RERUN_DIRS, "4b", str(run_dir))
    rerun, facts = bfu.read_rerun("4b", {"r1", "r2", "r3", "r4"}, DIGEST)
    assert rerun.loc[["r1", "r2"], "answer"].tolist() == ["yes", "no"]
    assert rerun.loc[["r1", "r2"], "rule"].tolist() == ["i", "c"]
    assert rerun.loc["r3", ["answer", "failure"]].tolist() == ["failed", "context_full"]
    assert rerun.loc["r4", ["answer", "failure"]].tolist() == ["failed", "not_run"]
    assert facts["num_ctx"] == [65536] and facts["failures"] == {"context_full": 1, "not_run": 1}

    full = pd.DataFrame({"id": ["r1", "r2", "r3", "r4", "other"], "answer": ["failed"] * 4 + ["no"],
                         "rule": [""] * 4 + ["f"], "failure": ["prompt_cut"] * 4 + [""],
                         "input_tokens": [8194] * 4 + [4000], "cut": [True] * 4 + [False]}).set_index("id")
    out = bfu.apply_rerun(full, rerun)
    assert out["answer"].tolist() == ["yes", "no", "failed", "failed", "no"]
    assert out["failure"].tolist() == ["", "", "context_full", "not_run", ""]


def test_a_rerun_of_other_records_or_another_digest_stops(tmp_path, monkeypatch):
    run_dir = make_rerun_folder(tmp_path)
    monkeypatch.setitem(bfu.RERUN_DIRS, "4b", str(run_dir))
    with pytest.raises(SystemExit):
        bfu.read_rerun("4b", {"r1", "r2", "r3", "x"}, DIGEST)
    with pytest.raises(SystemExit):
        bfu.read_rerun("4b", {"r1", "r2", "r3", "r4"}, "another digest")
