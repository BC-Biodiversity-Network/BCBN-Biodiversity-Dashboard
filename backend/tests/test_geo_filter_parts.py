"""Tests for parts of one BC filter run starting at the same moment
(pipeline/lunaris/llm/geo_filter_run.py and fir/flash_attention.sh), and for
the status tool on a part whose job failed (geo_filter_status.py).

On Fir all 8 parts of the full run started together, and four of them
stopped at once: each wrote the shared pass1/sample.csv through the same
temporary name, sample.csv.tmp, so one part moved the file another was
still writing and the other found it gone. The status tool then showed those
parts as "exit 0 (done)", because the crash was not caught and logs/jobs.jsonl
was written with exit 0. Here many parts start on one run folder at the same
moment, as threads and as separate processes, and every one must get through;
and the status tool must read each part's own Slurm log for its exit code."""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
LLM = BACKEND / "pipeline" / "lunaris" / "llm"
sys.path.insert(0, str(BACKEND / "pipeline" / "lunaris"))
sys.path.insert(0, str(LLM))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import geo_filter_run as gfr  # noqa: E402
import geo_filter_status  # noqa: E402
from test_geo_filter_run import FakeLookup, FakeModel, jsonl, make_input, never_mind  # noqa: E402
from test_geo_pass2_places import TOY_BC  # noqa: E402

PARTS = 8


def leftovers(folder):
    """Return any temporary files left anywhere in a run folder."""
    return [p for p in Path(folder).rglob("*") if ".tmp" in p.name]


def start_together(count, work):
    """Run work(k) in count threads released at the same moment; return the errors they raised, by k."""
    gate, errors = threading.Barrier(count), {}

    def one(k):
        """Wait for every thread, then do the work, keeping any error."""
        gate.wait()
        try:
            work(k)
        except Exception as error:  # noqa: BLE001 (any error is a failure of the test)
            errors[k] = error

    threads = [threading.Thread(target=one, args=(k,)) for k in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return errors


# --- Parts starting together ---------------------------------------------

def test_parts_starting_together_in_threads(tmp_path):
    """Eight parts (twice over, as a resubmit would) start at once on one folder; all get through, nothing is left over."""
    sample, records = gfr.read_input(make_input(tmp_path / "input", 24))
    out = tmp_path / "out"

    def start_part(k):
        """Start part k mod 8 as a job does: open the run and write its pass 1 prompts."""
        part = k % PARTS
        run = gfr.Run(str(out), sample, records, gfr.part_numbers(list(sample.index), part, PARTS), TOY_BC,
                      FakeLookup(), part)
        run.pass1_prompts()

    for _ in range(5):
        assert start_together(2 * PARTS, start_part) == {}
    assert (out / "pass1" / "sample.csv").read_text() == sample.to_csv()
    assert sorted(p.name for p in (out / "pass1" / "prompts").iterdir()) == [f"{n:02d}.txt" for n in range(24)]
    assert leftovers(out) == []


def test_sample_csv_is_written_only_if_missing_or_different(tmp_path):
    """sample.csv is left alone when it already holds the run's records, and put right when it does not."""
    path = tmp_path / "sample.csv"
    assert gfr.write_if_changed(str(path), "number,record_id\n0,a\n") is True
    before = path.stat().st_mtime_ns
    assert gfr.write_if_changed(str(path), "number,record_id\n0,a\n") is False
    assert path.stat().st_mtime_ns == before
    assert gfr.write_if_changed(str(path), "number,record_id\n0,b\n") is True
    assert path.read_text() == "number,record_id\n0,b\n"


def test_parts_finishing_together_write_the_merged_files_once_whole(tmp_path):
    """Once every part is done, several jobs writing the merged files at once all succeed and agree."""
    sample, records = gfr.read_input(make_input(tmp_path / "input", 6))
    out = tmp_path / "out"
    runs = []
    for part in range(2):
        run = gfr.Run(str(out), sample, records, gfr.part_numbers(list(sample.index), part, 2), TOY_BC,
                      FakeLookup(), part)
        run.run(FakeModel(), never_mind, run.pass1_prompts(), {})
        runs.append(run)
    finished = []
    assert start_together(6, lambda k: finished.append(runs[k % 2].finish())) == {}
    assert finished == [True] * 6
    assert [line["number"] for line in jsonl(out / "pass2" / "answers.jsonl")] == list(range(6))
    assert (out / "timing.csv").read_text().count("\n") == 7
    assert leftovers(out) == []


CHILD = """
import os, sys, time
sys.path[:0] = {paths!r}
import geo_filter_run as gfr
from test_geo_pass2_places import TOY_BC
from test_geo_filter_run import FakeLookup
part = int(sys.argv[1])
sample, records = gfr.read_input({input!r})
while not os.path.exists({go!r}):
    time.sleep(0.001)
run = gfr.Run({out!r}, sample, records, gfr.part_numbers(list(sample.index), part, {parts}), TOY_BC,
              FakeLookup(), part)
run.pass1_prompts()
print("ok", part)
"""


def test_parts_starting_together_as_processes(tmp_path):
    """Eight separate processes, released by one file appearing, open the same run folder; all get through."""
    folder = make_input(tmp_path / "input", 16)
    go, out = tmp_path / "go", tmp_path / "out"
    code = CHILD.format(paths=[str(BACKEND / "pipeline" / "lunaris"), str(LLM), str(Path(__file__).resolve().parent)],
                        input=str(folder), go=str(go), out=str(out), parts=PARTS)
    children = [subprocess.Popen([sys.executable, "-c", code, str(part)], stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True) for part in range(PARTS)]
    time.sleep(3)  # let every process load its packages and wait at the gate
    go.write_text("go")
    for part, child in enumerate(children):
        stdout, stderr = child.communicate(timeout=120)
        assert child.returncode == 0 and stdout.strip() == f"ok {part}", stderr
    sample, _ = gfr.read_input(folder)
    assert (out / "pass1" / "sample.csv").read_text() == sample.to_csv()
    assert leftovers(out) == []


def test_temporary_names_differ_by_process_and_thread(tmp_path):
    """Two threads, or this process and another, never get the same temporary name for one file."""
    path = str(tmp_path / "sample.csv")
    names = []
    start_together(2, lambda k: names.append(gfr.temporary_name(path)))
    other = subprocess.run([sys.executable, "-c", f"import sys; sys.path.insert(0, {str(LLM)!r}); "
                            f"sys.path.insert(0, {str(BACKEND / 'pipeline' / 'lunaris')!r}); "
                            f"import geo_filter_run as g; print(g.temporary_name({path!r}))"],
                           capture_output=True, text=True, check=True).stdout.strip()
    assert len(set(names + [other])) == 3


def test_flash_attention_records_written_together(tmp_path):
    """Eight jobs writing the flash attention record at once all pass, and one whole record is left."""
    script = LLM / "fir" / "flash_attention.sh"
    folder = tmp_path / "run"
    command = (f"source {script}; for k in 1 2 3 4 5 6 7 8; do "
               f"( SLURM_JOB_ID=$k flash_attention_check_record {folder} off write >/dev/null || echo FAILED $k ) & "
               f"done; wait")
    result = subprocess.run(["bash", "-c", command], env={"PATH": os.environ["PATH"], "OLLAMA_FLASH_ATTENTION": "0"},
                            capture_output=True, text=True)
    assert "FAILED" not in result.stdout, result.stdout + result.stderr
    lines = (folder / "flash_attention.txt").read_text().splitlines()
    assert lines[0] == "off" and len(lines) == 2
    assert leftovers(folder) == []


def test_an_unexpected_error_is_recorded_as_exit_1(tmp_path, monkeypatch):
    """A crash like the sample.csv one ends with exit 1 and is written to jobs.jsonl as exit 1, with the error."""
    folder = make_input(tmp_path / "input", 2)

    class Lookup(FakeLookup):
        """A stand-in for the GeoNames lookup that takes its arguments."""

        missing_hand_ids = []

        def __init__(self, *args):
            """Ignore the table and settings."""
            super().__init__()

    def crash(*args):
        """Fail as the shared temporary name did on Fir."""
        raise FileNotFoundError("'pass1/sample.csv.tmp' -> 'pass1/sample.csv'")

    monkeypatch.setattr(gfr.geonames_lookup, "GeoNamesLookup", Lookup)
    monkeypatch.setattr(gfr, "load_bc_shape", lambda path: TOY_BC)
    monkeypatch.setattr(gfr, "Run", crash)
    monkeypatch.setattr(sys, "argv", ["geo_filter_run.py", "--input", str(folder), "--out", str(tmp_path / "out"),
                                      "--bc-boundary", str(tmp_path / "bc.wkt"), "--part", "3", "--parts", "8"])
    monkeypatch.setenv("SLURM_JOB_ID", "678332")
    monkeypatch.setenv("SLURM_ARRAY_JOB_ID", "678332")
    monkeypatch.setenv("SLURM_ARRAY_TASK_ID", "3")
    with pytest.raises(FileNotFoundError):
        gfr.main()
    job = jsonl(tmp_path / "out" / "logs" / "jobs.jsonl")[-1]
    assert job["exit_code"] == 1 and job["part"] == 3 and job["array_job_id"] == "678332"
    assert job["reason"].startswith("FileNotFoundError")


# --- The status tool on a failed part ------------------------------------

def job_log(folder, array, task, job_id, part, exit_code=None, said=None, dry_run=False, started="12:00:00Z"):
    """Write a Slurm log as geo_filter.sbatch writes one: header lines, what the run printed, and the end line."""
    lines = [f"start    2026-10-26T{started}", f"host     fc10{task}  job {job_id}  part {part} of {PARTS}",
             f"run      /run  model qwen3.6:35b-a3b  input /in  context 16384  threads 6"
             f"{'  DRY RUN' if dry_run else ''}",
             f"run /run: 12431 records, part {part} of {PARTS} has 1554; model qwen3.6:35b-a3b"]
    if said:
        lines += ["Traceback (most recent call last):", '  File "geo_filter_run.py", line 179, in write_text', said]
    if exit_code is not None:
        lines.append(f"end      2026-10-26T13:00:00Z  exit {exit_code}")
    (folder / f"bc-filter_{array}_{task}.out").write_text("\n".join(lines) + "\n")


def test_status_reads_each_parts_own_log(tmp_path):
    """
    The case seen on Fir: jobs.jsonl says exit 0 for parts whose jobs crashed
    at start. The status shows each part's last job by its log (A_T), with the
    log's real exit code and error, and never calls a part with records left done.
    """
    sample, records = gfr.read_input(make_input(tmp_path / "input", 16))
    out = tmp_path / "out"
    logs = out / "logs"
    for part in (1, 4, 5, 6):
        run = gfr.Run(str(out), sample, records, gfr.part_numbers(list(sample.index), part, PARTS), TOY_BC,
                      FakeLookup(), part)
        run.run(FakeModel(), never_mind, run.pass1_prompts(), {})
    crash = "FileNotFoundError: [Errno 2] No such file or directory: '/run/pass1/sample.csv.tmp'"
    for part in range(PARTS):
        job_id = "678332" if part == 7 else str(678333 + part)
        failed = part in (0, 2, 3, 7)
        gfr.append_line(str(logs / "jobs.jsonl"), {"job_id": job_id, "array_task": str(part), "host": f"fc10{part}",
                                                   "part": part, "dry_run": False, "exit_code": 0})
        if part == 3:
            continue  # no log for part 3: only jobs.jsonl, which says exit 0
        job_log(logs, 678332, part, job_id, part, exit_code=1 if failed else 0, said=crash if failed else None)
    # An older dry run of part 0 and a job of part 6 still running are in the logs too.
    job_log(logs, 678000, 0, "678000", 0, exit_code=0, dry_run=True, started="13:30:00Z")
    job_log(logs, 679000, 6, "679000", 6, started="14:00:00Z")

    lines, unfinished = geo_filter_status.status(str(out), PARTS)
    by_part = {int(line.split()[0]): line for line in lines[2:2 + PARTS]}
    assert unfinished == [0, 2, 3, 7]
    for part in (0, 2, 7):
        assert f"job 678332_{part} on fc10{part}, exit 1 (setup problem" in by_part[part]
        assert "FileNotFoundError" in by_part[part] and f"see logs/bc-filter_678332_{part}.out" in by_part[part]
        assert "done" not in by_part[part]
    assert "exit 0, but 2 record(s) still have no pass 2 answer: not done" in by_part[3]
    for part in (1, 4, 5):
        assert by_part[part].endswith(f"job 678332_{part} on fc10{part}, exit 0 (done)")
    assert "job 679000_6" in by_part[6] and "still running, or killed by Slurm" in by_part[6]
    assert "ONLY=0,2,3,7" in "\n".join(lines)
