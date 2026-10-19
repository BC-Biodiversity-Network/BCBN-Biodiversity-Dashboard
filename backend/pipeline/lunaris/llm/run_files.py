"""
The files of one cluster run, and the small helpers that read and write them.

A run is one folder, for example ~/scratch/lunaris_llm/runs/test900 on Fir or
data/lunaris_llm_runs/test900 on the Mac. Inside it:

  run.json                  what the run is: source file, model, label,
                            prompt version, number of batches. Written once
                            by make_batches.py.
  inputs/batch_0000.jsonl.gz
                            the records of batch 0, one JSON object per line,
                            each with the fingerprint of its prompt
  results/batch_0000.jsonl  one line per record with a valid answer, appended
                            as the run goes
  errors/batch_0000.jsonl   one line per failed attempt. A failed record is
                            not done, so it is tried again on the next run.
  results/batch_0000.lock   present while a job is working on batch 0
  logs/                     the Slurm job logs and the Ollama server logs

make_batches.py, run_batch.py, merge_results.py and status.py all go through
this file, so they agree on the names and on what counts as done.
"""

import gzip
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from prompt_v6 import PROMPT_VERSION, check_answer


def batch_name(number):
    """Return the file stem for a batch number, for example batch_0007."""
    return f"batch_{number:04d}"


def inputs_path(run_dir, number):
    """Return the path of a batch's input records."""
    return Path(run_dir) / "inputs" / f"{batch_name(number)}.jsonl.gz"


def results_path(run_dir, number):
    """Return the path of a batch's valid answers."""
    return Path(run_dir) / "results" / f"{batch_name(number)}.jsonl"


def errors_path(run_dir, number):
    """Return the path of a batch's failed attempts."""
    return Path(run_dir) / "errors" / f"{batch_name(number)}.jsonl"


def lock_path(run_dir, number):
    """Return the path of the file that marks a batch as being worked on."""
    return Path(run_dir) / "results" / f"{batch_name(number)}.lock"


def load_run(run_dir):
    """Read run.json, the description of the run written by make_batches.py."""
    path = Path(run_dir) / "run.json"
    if not path.exists():
        raise FileNotFoundError(f"{path} not found. Is {run_dir} a run folder made by make_batches.py?")
    return json.loads(path.read_text(encoding="utf-8"))


def batch_numbers(run_dir):
    """Return the numbers of all batches in the run, in order."""
    numbers = []
    for path in (Path(run_dir) / "inputs").glob("batch_*.jsonl.gz"):
        numbers.append(int(path.name[len("batch_"):].split(".")[0]))
    return sorted(numbers)


def read_inputs(run_dir, number):
    """Return the input records of one batch, as a list of dicts, in order."""
    with gzip.open(inputs_path(run_dir, number), "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def read_jsonl(path):
    """
    Read a JSON lines file and return the parsed lines.

    A job that is stopped in the middle of writing can leave half a line at
    the end. Such a line, or any other line that is not valid JSON, is skipped
    rather than stopping the read. A missing file reads as empty.
    """
    path = Path(path)
    if not path.exists():
        return []
    lines = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                lines.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return lines


def repair_jsonl_tail(path):
    """
    Cut off a half-written last line, so new lines start on a fresh line.

    If a job was killed while writing, the file can end without a newline.
    Appending to it would glue the next answer onto the broken one and lose
    both. Returns how many bytes were removed (0 if the file was fine).
    """
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return 0
    data = path.read_bytes()
    if data.endswith(b"\n"):
        return 0
    keep = data.rfind(b"\n") + 1
    with path.open("r+b") as handle:
        handle.truncate(keep)
    return len(data) - keep


def append_line(path, record):
    """
    Add one JSON line to a file and make sure it is on disk before returning.

    flush and fsync after every line mean that if the job is killed a moment
    later, at most the answers still being worked on are lost, never one that
    was already written.
    """
    with Path(path).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def problem_with_result(line, run, expected_sha1):
    """
    Say why a results line does not count as a valid answer, or None if it does.

    A line counts only if it was produced by this prompt version and this
    model, from exactly the prompt the Mac built for that record, and its
    answer fits the schema.
    """
    if line.get("prompt_version") != PROMPT_VERSION:
        return f"prompt version {line.get('prompt_version')}, not {PROMPT_VERSION}"
    if line.get("model") != run["model"]:
        return f"model {line.get('model')}, not {run['model']}"
    if line.get("prompt_sha1") != expected_sha1:
        return "prompt fingerprint differs from the batch file"
    return check_answer(line.get("answer"))


def valid_results(run_dir, number, run, records):
    """
    Return the valid answers of one batch, as {record_id: results line}.

    records are the batch's input records, used to check each line against
    the prompt fingerprint made on the Mac. If a record has more than one valid
    line, which only happens if two jobs ever ran the same batch, the first
    one is kept.
    """
    expected = {record["record_id"]: record["prompt_sha1"] for record in records}
    valid = {}
    for line in read_jsonl(results_path(run_dir, number)):
        record_id = line.get("record_id")
        if record_id not in expected or record_id in valid:
            continue
        if problem_with_result(line, run, expected[record_id]) is None:
            valid[record_id] = line
    return valid


# How an error message is grouped in the summaries. The first matching
# start of the message decides the kind.
ERROR_KINDS = [
    ("the prompt filled the whole context", "context_full"),
    ("answer does not fit the schema", "schema"),
    ("reply was not valid JSON", "bad_json"),
    ("gave up after", "timeout_or_network"),
    ("Ollama said", "server_refused"),
    ("prompt differs", "prompt_mismatch"),
]


def error_kind(message):
    """Return a short name for the kind of an error message, such as context_full."""
    for start, kind in ERROR_KINDS:
        if message.startswith(start):
            return kind
    return "other"


def now_utc():
    """Return the current time in UTC as text, for example 2026-10-19T18:04:05Z."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(text):
    """Read a time written by now_utc back into a datetime."""
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
