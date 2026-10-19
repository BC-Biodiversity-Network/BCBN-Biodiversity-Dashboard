"""
Gather a run's answers into one CSV, in the same format as run_trial.py's.

The rows come out in the order of the run's input records, built by the same
helper run_trial.py uses (answer_to_row in prompt_v6.py), so the columns are
identical and score_trial.py reads the file unchanged. The model column holds
the run's label, qwen3.5:4b-fir by default, so the cluster CSV and the Mac CSV
can be scored side by side.

A record with no valid answer becomes a row with only record_id and
model_error, the same as in run_trial.py. score_trial.py leaves those out and
says how many. By default the merge refuses to write while records are still
missing, because a part-finished run is easy to mistake for a finished one.
--allow-incomplete writes it anyway.

Next to the CSV it writes a small .about.json: model digest, Ollama version,
context sizes, workers, seconds per call and so on, so two runs can be told
apart later.

Usage:

    python merge_results.py RUN_DIR
    python merge_results.py RUN_DIR --out somewhere/else.csv
    python merge_results.py RUN_DIR --allow-incomplete
"""

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prompt_v6 import PROMPT_VERSION, answer_to_row, error_row  # noqa: E402
from run_files import (  # noqa: E402
    batch_numbers,
    errors_path,
    load_run,
    read_inputs,
    read_jsonl,
    valid_results,
)


def safe_file_name(text):
    """Turn a label like qwen3.5:4b-fir into something safe in a file name (":" becomes "-")."""
    return re.sub(r"[^A-Za-z0-9._-]", "-", text)


def median_of(lines, key):
    """Return the median of one of Ollama's timings over the answers, or None if none have it."""
    values = [(line.get("timings") or {}).get(key) for line in lines]
    values = [value for value in values if value is not None]
    return round(statistics.median(values), 3) if values else None


def main():
    """Write the merged CSV and its .about.json, and print a short summary."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("run_dir", help="the run folder")
    parser.add_argument("--out", default=None,
                        help="where to write the CSV (default: inside the run folder, "
                             "trial_<label>_v6_<run folder name>.csv)")
    parser.add_argument("--allow-incomplete", action="store_true",
                        help="write the CSV even if some records have no valid answer yet")
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()
    run = load_run(run_dir)
    label = run["label"]
    out_path = Path(args.out) if args.out else (
        run_dir / f"trial_{safe_file_name(label)}_v{PROMPT_VERSION}_{run_dir.name}.csv")

    rows = []
    used = []
    missing = 0
    failed = 0
    for number in batch_numbers(run_dir):
        records = read_inputs(run_dir, number)
        answers = valid_results(run_dir, number, run, records)
        # The latest error per record, for the records that never succeeded.
        last_error = {}
        for line in read_jsonl(errors_path(run_dir, number)):
            last_error[line.get("record_id")] = line.get("error")
        for record in records:
            record_id = record["record_id"]
            if record_id in answers:
                rows.append(answer_to_row(record_id, label, answers[record_id]))
                used.append(answers[record_id])
            elif record_id in last_error:
                rows.append(error_row(record_id, last_error[record_id]))
                failed += 1
            else:
                rows.append(error_row(record_id, "not run yet"))
                missing += 1

    digests = sorted({line.get("model_digest") for line in used} - {None})
    if len(digests) > 1:
        print(f"ERROR: the answers come from {len(digests)} different model digests "
              f"({', '.join(d[:12] for d in digests)}). A run must use one model. Nothing written.")
        return 1
    if (failed or missing) and not args.allow_incomplete:
        print(f"ERROR: {failed:,} records failed and {missing:,} have not been run yet, out of "
              f"{len(rows):,}. Finish the run (status.py shows what is left), or add "
              f"--allow-incomplete. Nothing written.")
        return 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(out_path, index=False, encoding="utf-8-sig")

    seconds = [line["seconds"] for line in used if line.get("seconds") is not None]
    about = {
        "run": run_dir.name,
        "label": label,
        "model": run["model"],
        "model_digest": digests[0] if digests else None,
        "backends": sorted({str(line.get("backend") or "ollama") for line in used}),
        "server_versions": sorted({str(line.get("server_version") or line.get("ollama_version"))
                                   for line in used}),
        "num_thread_values": sorted({line.get("num_thread") for line in used if line.get("num_thread")}),
        "prompt_version": PROMPT_VERSION,
        "source": run.get("source"),
        "source_sha1": run.get("source_sha1"),
        "records": len(rows),
        "answered": len(used),
        "failed": failed,
        "not_run": missing,
        "num_ctx_values": sorted({line.get("num_ctx") for line in used if line.get("num_ctx")}),
        "workers_values": sorted({line.get("workers") for line in used if line.get("workers")}),
        "hosts": sorted({str(line.get("host")) for line in used}),
        "seconds_per_call_median": round(statistics.median(seconds), 3) if seconds else None,
        "seconds_per_call_mean": round(statistics.mean(seconds), 3) if seconds else None,
        # Ollama's own split of each call: reading the prompt and writing the answer.
        "prompt_seconds_median": median_of(used, "prompt_s"),
        "answer_seconds_median": median_of(used, "answer_s"),
        "output_tokens_median": statistics.median([line["output_tokens"] for line in used
                                                   if line.get("output_tokens")] or [0]),
        "first_answer": min((line["finished"] for line in used), default=None),
        "last_answer": max((line["finished"] for line in used), default=None),
    }
    about_path = out_path.with_suffix(".about.json")
    about_path.write_text(json.dumps(about, indent=1) + "\n", encoding="utf-8")

    print(f"wrote {out_path}")
    print(f"  {len(used):,} answered, {failed:,} failed, {missing:,} not run, of {len(rows):,}")
    if digests:
        print(f"  model {run['model']} digest {digests[0][:12]}, labelled {label}")
    print(f"  details in {about_path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
