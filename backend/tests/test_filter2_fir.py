"""Tests for running filter 2 (prompt v6) on Fir with qwen3.6:35b-a3b: the v6
submit script (pipeline/lunaris/llm/fir/submit.sh) with a stand-in for
sbatch, run_batch.py's flash attention check, new_model_run.py (a new run
folder with the same input batches), and exploration/llm/score_table.py
(several trials scored side by side, with score_trial.py's own rules).

For qwen3.6 models the submit script picks the 3g.40gb slice, the model's own
store and flash attention off; qwen3.5:4b is as before except that the
1g.10gb slice now gets 16G; a run folder that recorded another flash
attention setting is refused."""

import gzip
import json
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

BACKEND = Path(__file__).resolve().parents[1]
LLM = BACKEND / "pipeline" / "lunaris" / "llm"
SUBMIT = LLM / "fir" / "submit.sh"
sys.path.insert(0, str(LLM))
sys.path.insert(0, str(BACKEND / "exploration" / "llm"))
import new_model_run  # noqa: E402
import run_batch  # noqa: E402
import score_table  # noqa: E402

FAKE_SBATCH = """#!/bin/bash
# Stand-in for sbatch: write down the options and the settings the job would get.
{ echo "ARGS $*"
  for v in OLLAMA_FLASH_ATTENTION LUNARIS_MODELS_DIR LOAD_TIMEOUT STAGE_TIMEOUT NUM_THREAD; do
    echo "$v=${!v-<unset>}"
  done; } > "$FAKE_SBATCH_OUT"
"""


def setup(tmp_path, model):
    """Make a fake /scratch tree with a run folder for a model and a store for qwen3.6; return env and folder."""
    base = tmp_path / "lunaris_llm"
    run = base / "runs" / "test"
    run.mkdir(parents=True)
    (run / "run.json").write_text(json.dumps({"records": 2, "model": model, "label": f"{model}-fir"}, indent=1))
    (base / "bcf" / "models" / "qwen3.6-35b-a3b" / "manifests").mkdir(parents=True)
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "sbatch").write_text(FAKE_SBATCH)
    (fake / "sbatch").chmod(0o755)
    env = {"PATH": f"{fake}:{os.environ['PATH']}", "HOME": str(tmp_path), "USER": "tester",
           "LUNARIS_BASE": str(base), "FAKE_SBATCH_OUT": str(tmp_path / "sbatch.txt")}
    return env, run


def submit(tmp_path, env, run, array="0", **extra):
    """Run submit.sh from inside the run folder; return the finished process and what sbatch got (or None)."""
    result = subprocess.run(["bash", str(SUBMIT), array], env=dict(env, **extra), cwd=run, capture_output=True,
                            text=True)
    out = tmp_path / "sbatch.txt"
    got = out.read_text() if out.exists() else None
    if out.exists():
        out.unlink()
    return result, got


# --- submit.sh -----------------------------------------------------------

def test_qwen36_gets_3g_its_store_and_flash_attention_off(tmp_path):
    """A qwen3.6 run gets the 3g.40gb slice, 32G, its own store, longer timeouts and flash attention off."""
    env, run = setup(tmp_path, "qwen3.6:35b-a3b")
    result, got = submit(tmp_path, env, run, "0-56%8")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "--gpus=nvidia_h100_80gb_hbm3_3g.40gb:1" in got and "--mem=32G" in got and "--array=0-56%8" in got
    assert "OLLAMA_FLASH_ATTENTION=0" in got and "NUM_THREAD=6" in got
    assert f"LUNARIS_MODELS_DIR={tmp_path}/lunaris_llm/bcf/models/qwen3.6-35b-a3b" in got
    assert "LOAD_TIMEOUT=900" in got and "STAGE_TIMEOUT=1800" in got
    assert "set by default" in result.stdout and "flash attention off" in result.stdout


def test_4b_is_as_before_except_memory(tmp_path):
    """qwen3.5:4b keeps the 1g.10gb slice, Ollama's own flash attention and the default store; memory is 16G."""
    env, run = setup(tmp_path, "qwen3.5:4b")
    result, got = submit(tmp_path, env, run, "0-53%18")
    assert result.returncode == 0
    assert "--gpus=nvidia_h100_80gb_hbm3_1g.10gb:1" in got and "--mem=16G" in got and "NUM_THREAD=2" in got
    assert "OLLAMA_FLASH_ATTENTION=<unset>" in got and "LUNARIS_MODELS_DIR=<unset>" in got
    assert "LOAD_TIMEOUT=<unset>" in got
    result, got = submit(tmp_path, env, run, NUM_CTX="65536")
    assert "--gpus=nvidia_h100_80gb_hbm3_2g.20gb:1" in got


def test_a_missing_store_is_refused(tmp_path):
    """Without the qwen3.6 store nothing is submitted (and nothing is downloaded)."""
    env, run = setup(tmp_path, "qwen3.6:35b-a3b")
    result, got = submit(tmp_path, env, run, MODELS_DIR=str(tmp_path / "nowhere"))
    assert result.returncode == 1 and got is None and "never downloads" in result.stdout


def test_a_run_with_another_flash_attention_setting_is_refused(tmp_path):
    """A run folder that recorded flash attention off refuses a submit with it on."""
    env, run = setup(tmp_path, "qwen3.6:35b-a3b")
    (run / "flash_attention.txt").write_text("off\nrecorded by a test\n")
    result, got = submit(tmp_path, env, run, OLLAMA_FLASH_ATTENTION="1")
    assert result.returncode == 1 and got is None and "must not mix" in result.stdout
    result, got = submit(tmp_path, env, run)
    assert result.returncode == 0 and "as recorded for this run" in result.stdout


def test_time_limit_and_no_run_json(tmp_path):
    """TIME sets the time limit; a malformed TIME, or no run.json, is refused."""
    env, run = setup(tmp_path, "qwen3.6:35b-a3b")
    result, got = submit(tmp_path, env, run, TIME="06:00:00")
    assert result.returncode == 0 and "--time=06:00:00" in got
    result, got = submit(tmp_path, env, run, TIME="6h")
    assert result.returncode == 1 and got is None
    (run / "run.json").unlink()
    result, got = submit(tmp_path, env, run)
    assert result.returncode == 1 and "no run.json" in result.stdout


# --- run_batch.py --------------------------------------------------------

LINE = ('time=x level=INFO msg="starting llama-server" cmd="/node/llama-server --model /blob -c 16384 '
        '--flash-attn {} -b 1024 -ub 1024"\n')


def test_run_batch_flash_attention_check(tmp_path):
    """Asked for off, only a runner with --flash-attn off passes; other settings are only reported."""
    log = tmp_path / "ollama.log"
    log.write_text(LINE.format("auto"))
    assert "should be off" in run_batch.flash_attention_problem(str(log), "off")
    assert run_batch.flash_attention_problem(str(log), "auto") is None
    log.write_text(LINE.format("auto") + LINE.format("off"))
    assert run_batch.flash_attention_problem(str(log), "off") is None
    assert "no command line" in run_batch.flash_attention_problem(str(tmp_path / "missing.log"), "off")


# --- new_model_run.py ----------------------------------------------------

def test_a_new_model_run_copies_the_inputs_exactly(tmp_path):
    """The new folder has byte-identical batches, the new model and label, empty folders; the source is untouched."""
    source = tmp_path / "test900"
    (source / "inputs").mkdir(parents=True)
    (source / "results").mkdir()
    (source / "results" / "batch_0000.jsonl").write_text("an old answer\n")
    for n in range(2):
        with gzip.open(source / "inputs" / f"batch_{n:04d}.jsonl.gz", "wt") as handle:
            handle.write(json.dumps({"record_id": f"r{n}", "prompt_sha1": "abc"}) + "\n")
    run = {"records": 2, "batches": 2, "model": "qwen3.5:4b", "label": "qwen3.5:4b-fir", "instructions_sha1": "x"}
    (source / "run.json").write_text(json.dumps(run))
    before = {p: p.read_bytes() for p in source.rglob("*") if p.is_file()}
    target = tmp_path / "test900_35b"
    made = new_model_run.make_run(str(source), str(target), "qwen3.6:35b-a3b", "qwen3.6:35b-a3b-fir-nofa")
    assert made["model"] == "qwen3.6:35b-a3b" and made["label"] == "qwen3.6:35b-a3b-fir-nofa"
    assert made["instructions_sha1"] == "x" and made["made_from"]["model"] == "qwen3.5:4b"
    for n in range(2):
        name = f"batch_{n:04d}.jsonl.gz"
        assert (target / "inputs" / name).read_bytes() == (source / "inputs" / name).read_bytes()
    assert [p.name for p in sorted(target.iterdir())] == ["errors", "inputs", "logs", "results", "run.json"]
    assert not any((target / "results").iterdir())
    assert before == {p: p.read_bytes() for p in source.rglob("*") if p.is_file()}
    with pytest.raises(SystemExit):
        new_model_run.make_run(str(source), str(target), "qwen3.6:35b-a3b", "again")


# --- score_table.py ------------------------------------------------------

def test_score_table_scores_each_trial_by_name(tmp_path):
    """Two trials with the same model column stay apart, scored with score_trial's rules, plus Claude's labels."""
    labels = pd.DataFrame({"record_id": ["a", "b", "c", "d"], "stratum": ["kept", "kept", "dropped", "dropped"],
                           "is_biodiversity": ["yes", "no", "yes", "no"], "opus_label": ["yes", "yes", "yes", "no"],
                           "reviewed_by_hand": [True, True, True, True], "review_weight": [1.0, 1.0, 10.0, 10.0]})
    yes = {"concerns_living_things": True, "topic": "ecology", "experimental_animals": "no", "extinct_only": "no"}
    no = {"concerns_living_things": False, "topic": "administrative", "experimental_animals": "no",
          "extinct_only": "no"}
    good = pd.DataFrame([dict(yes, record_id="a"), dict(no, record_id="b"), dict(yes, record_id="c"),
                         dict(no, record_id="d")]).assign(model="same-label")
    keeps_all = pd.DataFrame([dict(yes, record_id=r) for r in "abcd"]).assign(model="same-label")
    good.to_csv(tmp_path / "good.csv", index=False)
    keeps_all.to_csv(tmp_path / "all.csv", index=False)
    rows, scored = score_table.table(labels, {"good": str(tmp_path / "good.csv"), "keeps all": str(tmp_path / "all.csv")},
                                     reviewed_only=True)
    by_name = {r["run"]: r for r in rows}
    assert scored == 4 and list(by_name) == ["keyword filter", "Claude (opus_label)", "good", "keeps all"]
    assert by_name["good"]["precision"] == 1.0 and by_name["good"]["recall"] == 1.0
    assert by_name["keeps all"]["junk_kept"] == 11.0 and by_name["keeps all"]["real_lost"] == 0
    assert by_name["Claude (opus_label)"]["junk_kept"] == 1.0
    assert by_name["keyword filter"]["real_lost"] == 10.0
