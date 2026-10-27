"""
geo_filter_status.py

Show how far a BC filter run (geo_filter_run.py) has got, part by part, on
the Fir login node. It only reads the run folder, so it is safe at any time,
and it needs only the Python standard library (no pandas, shapely or duckdb).

For each part: its records, how many have a pass 1 and a pass 2 answer, how
many are failing for now (their latest result is an error), and how its
last job ended. Then the totals, the flash attention setting recorded for
the run, whether the merged files are written, and the parts still to
resubmit (as ONLY=... for fir/geo_filter_submit.sh).

How a job ended is read from its own Slurm log in logs/ (bc-filter_A_T.out,
array A, task T), which geo_filter.sbatch ends with "end ... exit N" whatever
happened; logs/jobs.jsonl is used only for a part with no log, and for the
reason a job stopped. A job is shown as A_T, the name of its log; one task
of each array has the array's own number as its Slurm job id, which is why
a bare job id can look like the array's. A log with no end line belongs to a
job still running, or one Slurm killed (time limit or memory). A part with
records still to answer is never shown as done, whatever its last job said.

Run:
    module load python/3.12
    python $B/bcf/code/llm/geo_filter_status.py $B/bcf/runs/needs_llm_qwen3.6-35b-a3b_nofa --parts 8
"""

import argparse
import csv
import json
import os
import re
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


def read_job_log(path):
    """
    Read one job's Slurm log as geo_filter.sbatch writes it and return what it
    says: the part and parts, Slurm job id, host, start time, whether it was a
    dry run, the exit code from its last "end" line (None if it has none), the
    last line saying why it stopped (a STOPPED or ERROR line, or the error
    that ended a traceback), and the job's name A_T from the file name (or the
    job id). None if the log is not a BC filter job's.
    """
    info = {"log": os.path.basename(path), "exit_code": None, "dry_run": False, "started": "", "said": None}
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if line.startswith("start ") and not info["started"]:
                info["started"] = line.split()[1]
            elif line.startswith("host ") and "part" not in info:
                found = re.match(r"host\s+(\S+)\s+job (\S+)\s+part (\d+) of (\d+)", line)
                if found:
                    info.update(host=found[1], job_id=found[2], part=int(found[3]), parts=int(found[4]))
            elif line.startswith("run ") and "DRY RUN" in line:
                info["dry_run"] = True
            elif line.startswith("end "):
                found = re.search(r"exit (\d+)", line)
                if found:
                    info["exit_code"] = int(found[1])
            elif re.match(r"(STOPPED|ERROR|[A-Za-z]+(Error|Exception)\b)", line):
                info["said"] = line.strip()
    if "part" not in info:
        return None
    named = re.search(r"_(\d+)_(\d+)\.out$", path)
    info["job"] = f"{named[1]}_{named[2]}" if named else info["job_id"]
    return info


def last_jobs(run_dir, parts):
    """
    Return each part's last job, by part: from the Slurm logs in logs/ when
    there are any (dry runs and runs split into other parts left out), else
    from logs/jobs.jsonl. Each is a dict with job, host, exit_code, and the
    log's name and the reason it stopped when known.
    """
    logs_dir = os.path.join(run_dir, "logs")
    from_jsonl, reasons = {}, {}
    for job in read_jsonl(os.path.join(logs_dir, "jobs.jsonl")):
        reasons[(str(job.get("job_id")), job.get("part"))] = job.get("reason")
        if not job.get("dry_run"):
            from_jsonl[job["part"]] = job
    from_logs = {}
    names = sorted(os.listdir(logs_dir)) if os.path.isdir(logs_dir) else []
    for name in names:
        if not name.endswith(".out"):
            continue
        info = read_job_log(os.path.join(logs_dir, name))
        if info is None or info["dry_run"] or info["parts"] != parts:
            continue
        old = from_logs.get(info["part"])
        if old is None or info["started"] >= old["started"]:
            from_logs[info["part"]] = info
    jobs = {}
    for part in range(parts):
        if part in from_logs:
            info = from_logs[part]
            jobs[part] = dict(info, reason=reasons.get((info["job_id"], part)) or info["said"])
        elif part in from_jsonl:
            job = from_jsonl[part]
            name = (f"{job['array_job_id']}_{job['array_task']}" if job.get("array_job_id") is not None
                    and job.get("array_task") is not None else job.get("job_id"))
            jobs[part] = {"job": name, "host": job.get("host"), "exit_code": job.get("exit_code"),
                          "reason": job.get("reason"), "log": None}
    return jobs


def describe_job(job, left):
    """
    Return how a part's last job ended, in words: its name, host, exit code and
    what the code means, or that it has not ended. Exit 0 with records left
    (left > 0) is said to be not done.
    """
    if job is None:
        return "no job finished yet"
    text = f"job {job['job']} on {job['host']}, "
    code = job["exit_code"]
    if code is None:
        text += "no end line in its log: still running, or killed by Slurm (time limit or memory)"
    elif code == 0 and left:
        text += f"exit 0, but {left} record(s) still have no pass 2 answer: not done"
    else:
        text += f"exit {code} ({EXIT_CODES.get(code, '?')})"
    if code not in (None, 0) and job.get("reason"):
        text += f"; {job['reason'][:120]}"
    if job.get("log") and (code != 0 or left):
        text += f"; see logs/{job['log']}"
    return text


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
    last_job = last_jobs(run_dir, parts)
    lines = [f"run {run_dir}: {len(numbers)} records in {parts} part(s)",
             f"{'part':>4} {'records':>8} {'pass 1':>8} {'failing':>8} {'pass 2':>8} {'failing':>8}  last job"]
    unfinished, totals = [], [0, 0, 0, 0, 0]
    for part in range(parts):
        mine = [n for n in numbers if n % parts == part]
        counts = [len(mine), sum("answer" in pass1.get(n, {}) for n in mine),
                  sum("error" in pass1.get(n, {}) for n in mine), sum("answer" in pass2.get(n, {}) for n in mine),
                  sum("error" in pass2.get(n, {}) for n in mine)]
        totals = [a + b for a, b in zip(totals, counts)]
        ended = describe_job(last_job.get(part), counts[0] - counts[3])
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
