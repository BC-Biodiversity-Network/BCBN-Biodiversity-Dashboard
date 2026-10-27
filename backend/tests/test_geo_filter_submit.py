"""Tests for submitting the BC filter's run on Fir (pipeline/lunaris/llm/fir/
geo_filter_submit.sh and fir/flash_attention.sh), with a stand-in for
Slurm's sbatch that only records what it was given: flash attention is off
by default for qwen3.6 models and left alone for the others; a setting made
on purpose is kept with a warning; a run folder that recorded another
setting is refused; the record is written once and then checked; and the
1g.10gb slice gets 16G of memory."""

import os
import subprocess
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
FIR = BACKEND / "pipeline" / "lunaris" / "llm" / "fir"
SUBMIT = FIR / "geo_filter_submit.sh"
FLASH = FIR / "flash_attention.sh"

FAKE_SBATCH = """#!/bin/bash
# Stand-in for sbatch: write down the options and the flash attention setting.
{ echo "ARGS $*"; echo "OLLAMA_FLASH_ATTENTION=${OLLAMA_FLASH_ATTENTION-<unset>}"; } > "$FAKE_SBATCH_OUT"
"""


def setup(tmp_path):
    """Make a fake /scratch tree with one input folder and model stores, and a fake sbatch; return the environment."""
    base = tmp_path / "lunaris_llm"
    (base / "bcf" / "inputs" / "inp").mkdir(parents=True)
    (base / "bcf" / "inputs" / "inp" / "records.jsonl.gz").write_text("")
    for store in (base / "models", base / "bcf" / "models" / "qwen3.6-35b-a3b", base / "bcf" / "models" / "qwen3.5-9b"):
        (store / "manifests").mkdir(parents=True)
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "sbatch").write_text(FAKE_SBATCH)
    (fake / "sbatch").chmod(0o755)
    return {"PATH": f"{fake}:{os.environ['PATH']}", "HOME": str(tmp_path), "USER": "tester",
            "LUNARIS_BASE": str(base), "FAKE_SBATCH_OUT": str(tmp_path / "sbatch.txt")}


def submit(tmp_path, env, model, **extra):
    """Run the submit script for one model on the fake input; return the finished process and what sbatch got."""
    result = subprocess.run(["bash", str(SUBMIT), model, "inp"], env=dict(env, **extra), capture_output=True,
                            text=True)
    out = tmp_path / "sbatch.txt"
    got = out.read_text() if out.exists() else None
    if out.exists():
        out.unlink()
    return result, got


def run_folder(tmp_path, model):
    """Return the run folder the submit script uses for a model."""
    return tmp_path / "lunaris_llm" / "bcf" / "runs" / f"inp_{model.replace(':', '-')}"


def test_qwen36_gets_flash_attention_off_by_default(tmp_path):
    """A qwen3.6 model is submitted with OLLAMA_FLASH_ATTENTION=0, and the script says so."""
    env = setup(tmp_path)
    result, got = submit(tmp_path, env, "qwen3.6:35b-a3b")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OLLAMA_FLASH_ATTENTION=0 set by default" in result.stdout
    assert "flash attention off" in result.stdout
    assert "OLLAMA_FLASH_ATTENTION=0" in got
    assert "--gpus=nvidia_h100_80gb_hbm3_3g.40gb:1" in got and "--mem=32G" in got


def test_a_setting_made_on_purpose_is_kept_with_a_warning(tmp_path):
    """OLLAMA_FLASH_ATTENTION set by hand is passed on unchanged, with a warning for qwen3.6."""
    env = setup(tmp_path)
    result, got = submit(tmp_path, env, "qwen3.6:35b-a3b", OLLAMA_FLASH_ATTENTION="1")
    assert result.returncode == 0
    assert "WARNING" in result.stdout and "set on purpose" in result.stdout
    assert "OLLAMA_FLASH_ATTENTION=1" in got


def test_other_models_are_left_alone(tmp_path):
    """Other models get no OLLAMA_FLASH_ATTENTION, so Ollama keeps its own default (auto)."""
    env = setup(tmp_path)
    result, got = submit(tmp_path, env, "qwen3.5:9b")
    assert result.returncode == 0
    assert "OLLAMA_FLASH_ATTENTION=<unset>" in got and "flash attention auto" in result.stdout
    assert "--mem=16G" in got


def test_the_1g_slice_gets_16g(tmp_path):
    """qwen3.5:4b runs on a 1g.10gb slice with 16G of memory."""
    env = setup(tmp_path)
    result, got = submit(tmp_path, env, "qwen3.5:4b")
    assert result.returncode == 0
    assert "--gpus=nvidia_h100_80gb_hbm3_1g.10gb:1" in got and "--mem=16G" in got and "--mem=8G" not in got


def test_a_folder_with_another_setting_is_refused(tmp_path):
    """A run folder that recorded flash attention off refuses a submit with it on; the same setting passes."""
    env = setup(tmp_path)
    folder = run_folder(tmp_path, "qwen3.6:35b-a3b")
    folder.mkdir(parents=True)
    (folder / "flash_attention.txt").write_text("off\nrecorded by a test\n")
    result, got = submit(tmp_path, env, "qwen3.6:35b-a3b", OLLAMA_FLASH_ATTENTION="1")
    assert result.returncode == 1 and got is None
    assert "was run with flash attention off" in result.stdout and "must not mix" in result.stdout
    result, got = submit(tmp_path, env, "qwen3.6:35b-a3b")
    assert result.returncode == 0 and "as recorded for this run" in result.stdout


def test_a_dry_run_does_not_check_the_record(tmp_path):
    """A dry run starts no Ollama, so a recorded setting does not stop it."""
    env = setup(tmp_path)
    folder = run_folder(tmp_path, "qwen3.6:35b-a3b")
    folder.mkdir(parents=True)
    (folder / "flash_attention.txt").write_text("on\n")
    result, got = submit(tmp_path, env, "qwen3.6:35b-a3b", DRY_RUN="1")
    assert result.returncode == 0 and got is not None


def test_an_unknown_value_is_refused(tmp_path):
    """OLLAMA_FLASH_ATTENTION must be 0, 1, true, false or unset."""
    env = setup(tmp_path)
    result, got = submit(tmp_path, env, "qwen3.6:35b-a3b", OLLAMA_FLASH_ATTENTION="maybe")
    assert result.returncode == 1 and got is None and "must be 0, 1, true, false or unset" in result.stdout


def flash(tmp_path, command, **extra):
    """Run a command with fir/flash_attention.sh sourced; return the finished process."""
    env = {"PATH": os.environ["PATH"], "MODEL": "m", **extra}
    return subprocess.run(["bash", "-c", f"source {FLASH}; {command}"], env=env, capture_output=True, text=True,
                          cwd=tmp_path)


def test_the_record_is_written_once_then_checked(tmp_path):
    """The first job writes the record; a matching job passes; a different setting fails; check never writes."""
    folder = tmp_path / "run"
    result = flash(tmp_path, f"flash_attention_check_record {folder} off check")
    assert result.returncode == 0 and not (folder / "flash_attention.txt").exists()
    result = flash(tmp_path, f"flash_attention_check_record {folder} off write", OLLAMA_FLASH_ATTENTION="0")
    assert result.returncode == 0
    lines = (folder / "flash_attention.txt").read_text().splitlines()
    assert lines[0] == "off" and "OLLAMA_FLASH_ATTENTION=0" in lines[1]
    assert flash(tmp_path, f"flash_attention_check_record {folder} off write").returncode == 0
    assert flash(tmp_path, f"flash_attention_check_record {folder} auto write").returncode == 1
    assert (folder / "flash_attention.txt").read_text().splitlines()[0] == "off"


def test_settings_are_read_as_ollama_reads_them(tmp_path):
    """Unset or empty is auto, 0 and false are off, 1 and true are on, anything else is refused."""
    for value, expected in [(None, "auto"), ("", "auto"), ("0", "off"), ("false", "off"), ("1", "on"),
                            ("true", "on")]:
        extra = {} if value is None else {"OLLAMA_FLASH_ATTENTION": value}
        result = flash(tmp_path, "flash_attention_setting", **extra)
        assert result.returncode == 0 and result.stdout.strip() == expected, value
    assert flash(tmp_path, "flash_attention_setting", OLLAMA_FLASH_ATTENTION="2").returncode == 1


def test_only_resubmits_some_parts(tmp_path):
    """ONLY=3,5 with PARTS=8 submits just those array tasks; a part beyond PARTS is refused."""
    env = setup(tmp_path)
    result, got = submit(tmp_path, env, "qwen3.6:35b-a3b", PARTS="8", ONLY="3,5")
    assert result.returncode == 0 and "--array=3,5 " in got
    result, got = submit(tmp_path, env, "qwen3.6:35b-a3b", PARTS="8")
    assert "--array=0-7 " in got
    result, got = submit(tmp_path, env, "qwen3.6:35b-a3b", PARTS="8", ONLY="8")
    assert result.returncode == 1 and got is None
    result, got = submit(tmp_path, env, "qwen3.6:35b-a3b", PARTS="8", ONLY="3;rm")
    assert result.returncode == 1 and got is None
