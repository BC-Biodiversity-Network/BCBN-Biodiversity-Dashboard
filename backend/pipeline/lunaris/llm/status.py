"""
Show how far a run has got, in one command.

Prints: records done out of the total, batches done, part done and not
started, which batches a job is working on now, open errors grouped by kind,
the speed over the last hour and the time left at that speed, where each
record's time goes (reading the prompt or writing the answer), nodes where
jobs stopped before their first record (and why), the model
digests seen (there should be exactly one), your lunaris-llm jobs in the Slurm
queue, and the fir/submit.sh command that resubmits only the unfinished batches.

It only reads files, so it is safe to run at any time, also while jobs are
writing.

Usage (on Fir):

    python ~/lunaris_llm/code/status.py ~/scratch/lunaris_llm/runs/test900
"""

import argparse
import json
import shutil
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from statistics import median
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_files import (  # noqa: E402
    batch_numbers,
    errors_path,
    gpu_failures_path,
    load_run,
    lock_path,
    parse_utc,
    read_inputs,
    read_jsonl,
    results_path,
    valid_results,
)

# The job name set in fir/llm_array.sbatch, used to find this run's jobs.
JOB_NAME = "lunaris-llm"


def as_ranges(numbers):
    """Write a list of numbers the way sbatch --array wants them, for example 0-3,7,9-12."""
    parts = []
    start = previous = None
    for number in sorted(numbers):
        if start is None:
            start = previous = number
        elif number == previous + 1:
            previous = number
        else:
            parts.append(f"{start}-{previous}" if previous > start else f"{start}")
            start = previous = number
    if start is not None:
        parts.append(f"{start}-{previous}" if previous > start else f"{start}")
    return ",".join(parts)


def queue_lines():
    """Return this user's lunaris-llm jobs from squeue, or None where there is no Slurm."""
    if not shutil.which("squeue"):
        return None
    answer = subprocess.run(["squeue", "--me", "-n", JOB_NAME, "-h",
                             "-o", "%.18i %.9T %.10M %.10l %R"],
                            capture_output=True, text=True)
    return [line for line in answer.stdout.splitlines() if line.strip()]


def main():
    """Print the run's progress and error summary."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("run_dir", help="the run folder")
    parser.add_argument("--throttle", type=int, default=18,
                        help="how many batches at once in the suggested resubmit command (default 18)")
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()
    run = load_run(run_dir)
    now = datetime.now(timezone.utc)
    hour_ago = now - timedelta(hours=1)

    total = 0
    done_total = 0
    complete, partial, untouched, in_progress = [], [], [], {}
    open_errors = Counter()
    last_errors = []
    digests = Counter()
    timings = {"prompt_s": [], "answer_s": [], "output_tokens": []}
    last_hour = 0
    earliest = None
    seconds = []

    for number in batch_numbers(run_dir):
        records = read_inputs(run_dir, number)
        answers = valid_results(run_dir, number, run, records)
        total += len(records)
        done_total += len(answers)
        for line in answers.values():
            digests[line.get("model_digest")] += 1
            if line.get("finished") and parse_utc(line["finished"]) >= hour_ago:
                for key in ("prompt_s", "answer_s"):
                    if (line.get("timings") or {}).get(key) is not None:
                        timings[key].append(line["timings"][key])
                if line.get("output_tokens"):
                    timings["output_tokens"].append(line["output_tokens"])
                last_hour += 1
                finished = parse_utc(line["finished"])
                earliest = finished if earliest is None else min(earliest, finished)
                if line.get("seconds") is not None:
                    seconds.append(line["seconds"])

        # An error only counts while the record still has no valid answer.
        latest = {}
        for line in read_jsonl(errors_path(run_dir, number)):
            if line.get("record_id") not in answers:
                latest[line.get("record_id")] = line
        for line in latest.values():
            open_errors[line.get("kind", "other")] += 1
        last_errors += list(latest.values())

        if lock_path(run_dir, number).exists():
            try:
                in_progress[number] = json.loads(lock_path(run_dir, number).read_text()).get("job_id")
            except (OSError, ValueError):
                in_progress[number] = "?"
        if len(answers) == len(records):
            complete.append(number)
        elif answers or results_path(run_dir, number).exists():
            partial.append(number)
        else:
            untouched.append(number)

    batches = len(complete) + len(partial) + len(untouched)
    share = done_total / total * 100 if total else 0
    print(f"run {run_dir.name}: {total:,} records in {batches} batches, model {run['model']}, "
          f"label {run['label']}")
    print(f"  done:     {done_total:,} of {total:,} records ({share:.1f}%)")
    print(f"  batches:  {len(complete)} done, {len(partial)} part done, {len(untouched)} not started")
    if in_progress:
        print("  working now: " + ", ".join(f"batch {n} (job {job})" for n, job in sorted(in_progress.items())))

    if open_errors:
        print(f"  open errors: {sum(open_errors.values()):,} records still failing: "
              + ", ".join(f"{kind} {count}" for kind, count in open_errors.most_common()))
        for line in sorted(last_errors, key=lambda l: l.get("finished", ""))[-3:]:
            print(f"    {line.get('finished')} {line.get('record_id')}: {str(line.get('error'))[:110]}")
        if open_errors.get("context_full"):
            print("    context_full: once the rest is done, resubmit with NUM_CTX=65536 fir/submit.sh (fir/README.md)")
    else:
        print("  open errors: none")

    if last_hour:
        left = total - done_total
        print(f"  last hour: {last_hour:,} records, median {sorted(seconds)[len(seconds) // 2]:.2f} s per call"
              if seconds else f"  last hour: {last_hour:,} records")
        # The speed is measured over the part of the last hour that had answers
        # in it, so a run that started ten minutes ago is not judged by a
        # whole hour.
        window = (now - earliest).total_seconds()
        if left and window >= 120:
            per_hour = last_hour / window * 3600
            print(f"  speed: {per_hour:,.0f} records/hour; time left at that speed: "
                  f"about {left / per_hour:.1f} h")
        elif left:
            print("  speed: too early to tell (under two minutes of answers)")
    else:
        print("  last hour: no answers written")
    if timings["prompt_s"] and timings["answer_s"]:
        prompt_s, answer_s = median(timings["prompt_s"]), median(timings["answer_s"])
        tokens = median(timings["output_tokens"]) if timings["output_tokens"] else 0
        print(f"  per record (median, last hour): reading the prompt {prompt_s:.2f} s, writing the "
              f"answer {answer_s:.2f} s" + (f" for {tokens:.0f} tokens ({tokens / answer_s:.0f} tokens/s)"
                                             if tokens and answer_s else ""))

    # Jobs that stopped before their first record because the model was not
    # on the GPU or did not load, by node, with the latest reason.
    failures = read_jsonl(gpu_failures_path(run_dir))
    off_gpu = Counter(str(line.get("host", "?")).split(".")[0] for line in failures)
    if off_gpu:
        print("  jobs that stopped before their first record: "
              + ", ".join(f"{host} ({n}x)" for host, n in off_gpu.most_common()))
        for line in failures[-3:]:
            print(f"    {line.get('finished')} {str(line.get('host', '?')).split('.')[0]} "
                  f"exit {line.get('exit_code', 5)}: {str(line.get('reason', 'not on the GPU'))[:110]}")
        print("    if one node keeps failing, leave it out when resubmitting (EXCLUDE=node);"
              " if many fail at once, suspect the shared filesystem")

    if digests:
        print("  model digests: " + ", ".join(f"{(d or '?')[:12]} ({n:,})" for d, n in digests.items())
              + ("" if len(digests) == 1 else "   WARNING: more than one model in this run"))

    jobs = queue_lines()
    if jobs is not None:
        print(f"  {JOB_NAME} jobs in the queue: {len(jobs)}")
        for line in jobs[:10]:
            print(f"    {line.strip()}")

    unfinished = [n for n in partial + untouched if n not in in_progress]
    if unfinished:
        # submit.sh picks the GPU slice and leaves out the nodes named in EXCLUDE.
        script = Path(__file__).resolve().parent / "fir" / "submit.sh"
        repeat = [host for host, n in off_gpu.items() if n >= 2]
        prefix = f"EXCLUDE={','.join(repeat)} " if repeat else ""
        print("  to (re)submit the unfinished batches:")
        print(f"    cd {run_dir} && {prefix}{script} {as_ranges(unfinished)}%{args.throttle}")
    elif done_total == total:
        print(f"  all done. Merge with: python {Path(__file__).resolve().parent / 'merge_results.py'} {run_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
