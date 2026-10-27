"""
geo_filter_status.py

Show how far a BC filter run (geo_filter_run.py) has got, part by part, on
the Fir login node. It only reads the run folder, so it is safe at any time,
and it needs only the Python standard library (no pandas, shapely or duckdb).

For each part: its records, how many have a pass 1 and a pass 2 answer, how
many are failing for now (their latest result is an error), and how its
last job ended (from logs/jobs.jsonl). Then the totals, the flash attention
setting recorded for the run, whether the merged files are written, and the
parts still to resubmit (as ONLY=... for fir/geo_filter_submit.sh).

Run:
    module load python/3.12
    python $B/bcf/code/llm/geo_filter_status.py $B/bcf/runs/needs_llm_qwen3.6-35b-a3b_nofa --parts 8
"""

import argparse
import csv
import json
import os
import sys

# What each exit code of geo_filter_run.py means, and what to do about it.
EXIT_CODES = {
    0: "done",
    1: "setup problem: read the job log; do not just resubmit",
    2: "the server stopped answering: resubmit",
    3: "another job holds this part: check squeue --me",
    4: "some records failed: resubmit (only those are asked again)",
    5: "model not on the GPU: resubmit with EXCLUDE=<node>",
    6: "setup failed or the model did not load: resubmit, with EXCLUDE=<node> if it repeats",
}


def read_jsonl(path):
    """Read a JSON lines file, skipping a half-written line; a missing file reads as empty."""
    lines = []
    if not os.path.exists(path):
        return lines
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            try:
                lines.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return lines


def latest(folder, stem):
    """Return each record's latest progress line of one kind, by number (an answer wins over an error)."""
    best = {}
    if not os.path.isdir(folder):
        return best
    for name in sorted(os.listdir(folder)):
        if not (name.startswith(stem + "_part") and name.endswith(".jsonl")):
            continue
        for line in read_jsonl(os.path.join(folder, name)):
            old = best.get(line["number"])
            if old is None or ("answer" in line, line["finished"]) >= ("answer" in old, old["finished"]):
                best[line["number"]] = line
    return best


def status(run_dir, parts):
    """Return the status lines for a run folder split into parts, and the parts not yet complete."""
    with open(os.path.join(run_dir, "pass1", "sample.csv"), encoding="utf-8") as handle:
        numbers = [int(row["number"]) for row in csv.DictReader(handle)]
    pass1 = latest(os.path.join(run_dir, "pass1"), "progress")
    pass2 = latest(os.path.join(run_dir, "pass2"), "progress")
    last_job = {}
    for job in read_jsonl(os.path.join(run_dir, "logs", "jobs.jsonl")):
        if not job.get("dry_run"):
            last_job[job["part"]] = job
    lines = [f"run {run_dir}: {len(numbers)} records in {parts} part(s)",
             f"{'part':>4} {'records':>8} {'pass 1':>8} {'failing':>8} {'pass 2':>8} {'failing':>8}  last job"]
    unfinished, totals = [], [0, 0, 0, 0, 0]
    for part in range(parts):
        mine = [n for n in numbers if n % parts == part]
        counts = [len(mine), sum("answer" in pass1.get(n, {}) for n in mine),
                  sum("error" in pass1.get(n, {}) for n in mine), sum("answer" in pass2.get(n, {}) for n in mine),
                  sum("error" in pass2.get(n, {}) for n in mine)]
        totals = [a + b for a, b in zip(totals, counts)]
        job = last_job.get(part)
        if job:
            code = job.get("exit_code")
            ended = f"job {job.get('job_id')} on {job.get('host')}, exit {code} ({EXIT_CODES.get(code, '?')})"
        else:
            ended = "no job finished yet"
        lines.append(f"{part:>4} {counts[0]:>8} {counts[1]:>8} {counts[2]:>8} {counts[3]:>8} {counts[4]:>8}  {ended}")
        if counts[3] < counts[0]:
            unfinished.append(part)
    lines.append(f"{'all':>4} {totals[0]:>8} {totals[1]:>8} {totals[2]:>8} {totals[3]:>8} {totals[4]:>8}")
    record = os.path.join(run_dir, "flash_attention.txt")
    if os.path.exists(record):
        with open(record, encoding="utf-8") as handle:
            lines.append(f"flash attention: {handle.readline().strip()} (recorded for this run)")
    merged = os.path.exists(os.path.join(run_dir, "pass2", "answers.jsonl"))
    lines.append("merged files: " + ("written" if merged else "not yet (written once every part is done)"))
    if unfinished:
        lines.append(f"parts not done yet: {unfinished}; once their jobs have ended, resubmit them with "
                     f"ONLY={','.join(map(str, unfinished))} and the same PARTS")
    return lines, unfinished


def main():
    """Print the status of one run folder."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("run_dir")
    parser.add_argument("--parts", type=int, default=1, help="the PARTS the run was submitted with")
    args = parser.parse_args()
    lines, _ = status(args.run_dir, args.parts)
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
