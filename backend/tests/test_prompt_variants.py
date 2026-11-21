"""Tests for the prompt v6 variants (pipeline/lunaris/llm/prompt_variants.py)
and for run_batch.py choosing a run's variant.

prompt v6 itself must not change: its instructions keep the fingerprint the
Lunaris runs recorded in run.json, the v6 variant builds exactly
prompt_v6.build_prompt's text, and the prompts stored in the Lunaris run
folders on this Mac still rebuild to their fingerprints. The DataONE
variant differs from v6 in exactly one line, its first sentence. A run
folder without prompt_variant is v6.

The run folder tests use the real (git-ignored) run folders under data/
and are skipped where those are not present."""

import gzip
import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
LLM = BACKEND / "pipeline" / "lunaris" / "llm"
sys.path.insert(0, str(LLM))
import prompt_variants  # noqa: E402
import run_batch  # noqa: E402
from prompt_v6 import INSTRUCTIONS, build_prompt, prompt_sha1  # noqa: E402
from prompt_variants import (DATAONE_FIRST_SENTENCE, V6_FIRST_SENTENCE, build_prompt_for,  # noqa: E402
                             instructions_for, instructions_sha1, variant_of_run)

# The instructions fingerprint every Lunaris v6 run recorded in its run.json
# (for example data/lunaris_llm_runs/harvest_2026_10_20/run.json).
V6_INSTRUCTIONS_SHA1 = "920ea84cb2bf7a60b374b27d7d764a879fcbff94"

RECORDS = [
    {"record_id": "a", "title": "Eagle nests", "subjects": "birds, nests", "abstract": "Counts of nests."},
    {"record_id": "b", "title": "Only a title", "subjects": None, "abstract": None},
    {"record_id": "c", "title": None, "subjects": "[]", "abstract": "No title, empty subjects."},
    {"record_id": "d", "title": "List text", "subjects": "['environnement' 'Une ville verte']", "abstract": ""},
]

LUNARIS_RUNS = ["data/lunaris_llm_runs/test900", "data/lunaris_llm_runs/test900_x4",
                "data/lunaris_llm_runs/harvest_2026_10_20",
                "data/lunaris_llm_runs/fir_harvest_2026_10_20_qwen3.6-35b-a3b_nofa"]
DATAONE_RUNS = ["data/dataone/filter2_input/dataone_test200", "data/dataone/filter2_input/dataone_harvest_2026_11_18"]


def read_batch(path):
    """Return the records of one gzipped JSON lines batch file."""
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def test_v6_instructions_keep_the_lunaris_fingerprint():
    assert prompt_sha1(INSTRUCTIONS) == V6_INSTRUCTIONS_SHA1
    assert instructions_sha1("v6") == V6_INSTRUCTIONS_SHA1
    assert instructions_for("v6") is INSTRUCTIONS


def test_the_v6_variant_builds_exactly_the_v6_prompt():
    for record in RECORDS:
        assert build_prompt_for(record, "v6") == build_prompt(record)
        assert build_prompt_for(record) == build_prompt(record)


def test_the_dataone_variant_differs_in_exactly_one_sentence():
    v6, dataone = INSTRUCTIONS.splitlines(), instructions_for("v6_dataone").splitlines()
    assert len(v6) == len(dataone)
    changed = [i for i, (a, b) in enumerate(zip(v6, dataone)) if a != b]
    assert changed == [0]
    assert v6[0].startswith(V6_FIRST_SENTENCE)
    assert dataone[0] == v6[0].replace(V6_FIRST_SENTENCE, DATAONE_FIRST_SENTENCE)
    assert DATAONE_FIRST_SENTENCE + " For each record, report facts" in dataone[0]
    assert instructions_sha1("v6_dataone") != V6_INSTRUCTIONS_SHA1


def test_a_dataone_prompt_differs_from_v6_only_in_that_sentence():
    for record in RECORDS:
        v6, dataone = build_prompt(record), build_prompt_for(record, "v6_dataone")
        assert dataone == v6.replace(V6_FIRST_SENTENCE, DATAONE_FIRST_SENTENCE, 1)
        assert dataone[len(instructions_for("v6_dataone")):] == v6[len(INSTRUCTIONS):]


def test_a_run_without_prompt_variant_is_v6():
    assert variant_of_run({"model": "qwen3.5:4b"}) == "v6"
    assert variant_of_run({"prompt_variant": "v6_dataone"}) == "v6_dataone"
    with pytest.raises(ValueError):
        variant_of_run({"prompt_variant": "v7"})


def test_ask_one_builds_the_variant_prompt_and_checks_its_fingerprint():
    record = dict(RECORDS[0])
    record["prompt_sha1"] = prompt_sha1(build_prompt_for(record, "v6_dataone"))
    sent = []

    def ask(prompt):
        """Stand-in for the server: keep the prompt it was sent and reply with an error."""
        sent.append(prompt)
        return {"error": "not asked for real"}

    run_batch.ask_one(record, ask, "v6_dataone")
    assert sent == [build_prompt_for(record, "v6_dataone")]
    # The same record under v6 (an old run folder) does not match the
    # variant's fingerprint, so nothing is sent.
    _, result, _ = run_batch.ask_one(record, ask)
    assert result == {"error": "prompt differs from the one made on the Mac"} and len(sent) == 1


@pytest.mark.parametrize("run", LUNARIS_RUNS)
def test_the_lunaris_run_folders_still_rebuild_to_their_fingerprints(run):
    run_dir = BACKEND / run
    if not (run_dir / "run.json").exists():
        pytest.skip(f"{run} is not on this machine")
    about = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert variant_of_run(about) == "v6" and about["instructions_sha1"] == V6_INSTRUCTIONS_SHA1
    batches = sorted((run_dir / "inputs").glob("batch_*.jsonl.gz"))
    for path in (batches[0], batches[-1]):
        for record in read_batch(path):
            assert prompt_sha1(build_prompt_for(record, variant_of_run(about))) == record["prompt_sha1"]


@pytest.mark.parametrize("run", DATAONE_RUNS)
def test_the_dataone_run_folders_use_the_dataone_variant(run):
    run_dir = BACKEND / run
    if not (run_dir / "run.json").exists():
        pytest.skip(f"{run} is not on this machine")
    about = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert about["prompt_variant"] == "v6_dataone"
    assert about["instructions_sha1"] == instructions_sha1("v6_dataone")
    first = sorted((run_dir / "inputs").glob("batch_*.jsonl.gz"))[0]
    for record in read_batch(first)[:500]:
        assert prompt_sha1(build_prompt_for(record, "v6_dataone")) == record["prompt_sha1"]
        assert prompt_sha1(build_prompt(record)) != record["prompt_sha1"]
