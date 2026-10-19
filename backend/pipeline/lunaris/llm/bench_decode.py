"""
Measure how fast Ollama writes answers on one GPU slice, under a few settings.

Run 1 on Fir showed that reading the prompt is quick (the shared instructions
are reused) but writing the answer runs at about 10 tokens a second, which is
far below what the GPU can do. The suspicion is that the CPU side is the
bottleneck: choosing each next token from a 248,000-word vocabulary, the
presence penalty, and checking it against the JSON schema all happen on the
CPU, and the job had only 2 CPUs. Ollama may also start far more threads
than the 2 CPUs it was given.

This sends the same records to the server several times, changing one
setting at a time:

  as_run              exactly the request run_batch.py sends
  threads             the same, with num_thread set to the job's CPU count
  threads_no_penalty  also with presence_penalty 0 (changes the answers;
                      only to see what the penalty costs)
  threads_no_schema   also without the JSON schema (changes the answers;
                      only to see what the schema check costs)

For each it reports the median speed of writing the answer (tokens a
second), the median time to read the prompt, how busy the job's CPUs were,
and for "threads" whether its answers match as_run: the facts (every field
but the free-text reason) and the exact text. The reason's wording can also
change when a prompt is read from Ollama's cache instead of afresh, so the
facts are the fairer test. The first
two requests of each setting are a warm-up and are not counted, because
changing num_thread makes Ollama reload the model.

It writes nothing into the run folder; only the --out CSV.

Usage (inside a job, see fir/bench_decode.sbatch):

    python bench_decode.py RUN_DIR --ollama-url http://127.0.0.1:PORT \\
        --records 24 --threads 8 --out bench_8cpu.csv
"""

import argparse
import csv
import json
import os
import statistics
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ollama_client import OLLAMA_NUM_CTX, chat_request_body, wait_for_server  # noqa: E402
from prompt_v6 import build_prompt  # noqa: E402
from run_files import load_run, read_inputs  # noqa: E402

# The settings tried, in order: name, set num_thread, keep the schema, presence penalty.
VARIANTS = [
    ("as_run", False, True, None),
    ("threads", True, True, None),
    ("threads_no_penalty", True, True, 0.0),
    ("threads_no_schema", True, False, None),
]

WARM_UP = 2


def cpu_seconds_of_user_processes():
    """
    Return the CPU time used so far by this user's Ollama processes, in seconds.

    Read from /proc, so it works on Linux only; on the Mac it returns None.
    Comparing it before and after a setting shows how busy the CPUs were: CPU
    seconds divided by (wall seconds x CPUs) near 1.0 means they were the
    bottleneck.
    """
    proc = Path("/proc")
    if not proc.exists():
        return None
    ticks = os.sysconf("SC_CLK_TCK")
    total = 0.0
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != os.getuid():
                continue
            command = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
            if "ollama" not in command and "llama-server" not in command:
                continue
            fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            total += (int(fields[11]) + int(fields[12])) / ticks
        except (OSError, ValueError, IndexError):
            continue
    return total


def runner_threads():
    """Return a short note on Ollama's runner processes: their thread counts and --threads flag."""
    proc = Path("/proc")
    if not proc.exists():
        return "unknown (no /proc)"
    notes = []
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != os.getuid():
                continue
            command = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
            if "llama-server" not in command and "runner" not in command:
                continue
            threads = [line.split()[1] for line in (entry / "status").read_text().splitlines()
                       if line.startswith("Threads:")]
            flag = command.split("--threads ")[1].split()[0] if "--threads " in command else "not set"
            notes.append(f"pid {entry.name}: {threads[0] if threads else '?'} threads, --threads {flag}")
        except (OSError, ValueError, IndexError):
            continue
    return "; ".join(notes) or "no runner process found"


def facts_of(content):
    """Return an answer's fields other than the free-text reason, or None if it is not JSON."""
    try:
        answer = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return None
    return {key: value for key, value in answer.items() if key != "reason"} if isinstance(answer, dict) else None


def post_chat(url, body, timeout):
    """Send one /api/chat request and return Ollama's reply as a dict."""
    request = urllib.request.Request(url.rstrip("/") + "/api/chat",
                                     data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def main():
    """Run every setting over the same records and print the comparison."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("run_dir", help="a run folder made by make_batches.py, for its records")
    parser.add_argument("--ollama-url", default=os.environ.get("OLLAMA_HOST", "http://localhost:11434"))
    parser.add_argument("--records", type=int, default=24, help="records per setting (default 24)")
    parser.add_argument("--threads", type=int, default=int(os.environ.get("SLURM_CPUS_PER_TASK", "2")),
                        help="num_thread for the threads settings (default: the job's CPU count)")
    parser.add_argument("--only", default="", help="comma-separated setting names to run (default: all)")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--out", required=True, help="CSV with one row per request")
    args = parser.parse_args()

    url = args.ollama_url if args.ollama_url.startswith("http") else "http://" + args.ollama_url
    run = load_run(args.run_dir)
    records = read_inputs(args.run_dir, 0)[:args.records + WARM_UP]
    wanted = [name for name in args.only.split(",") if name]
    variants = [v for v in VARIANTS if not wanted or v[0] in wanted]
    cpus = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count()
    print(f"Ollama {wait_for_server(url, 180)} at {url}, model {run['model']}")
    print(f"CPUs this job may use: {cpus} (the node has {os.cpu_count()}); "
          f"num_thread for the threads settings: {args.threads}")
    print(f"{len(records) - WARM_UP} records per setting, after {WARM_UP} warm-up requests\n")

    rows = []
    answers = {}
    summary = []
    for name, set_threads, schema, penalty in variants:
        cpu_before = None
        started = None
        for position, record in enumerate(records):
            if position == WARM_UP:
                cpu_before = cpu_seconds_of_user_processes()
                started = time.monotonic()
            body = chat_request_body(run["model"], build_prompt(record), OLLAMA_NUM_CTX,
                                     args.threads if set_threads else None)
            if not schema:
                body.pop("format")
            if penalty is not None:
                body["options"]["presence_penalty"] = penalty
            reply = post_chat(url, body, args.timeout)
            if position < WARM_UP:
                continue
            content = reply.get("message", {}).get("content", "")
            tokens, seconds = reply.get("eval_count") or 0, (reply.get("eval_duration") or 0) / 1e9
            rows.append({
                "setting": name, "record_id": record["record_id"],
                "prompt_tokens": reply.get("prompt_eval_count"),
                "prompt_s": round((reply.get("prompt_eval_duration") or 0) / 1e9, 3),
                "answer_tokens": tokens, "answer_s": round(seconds, 3),
                "tokens_per_s": round(tokens / seconds, 2) if seconds else None,
                "total_s": round((reply.get("total_duration") or 0) / 1e9, 3),
                "content": content,
            })
            answers[(name, record["record_id"])] = content
        wall = time.monotonic() - started
        cpu_after = cpu_seconds_of_user_processes()
        mine = [row for row in rows if row["setting"] == name]
        busy = ((cpu_after - cpu_before) / (wall * cpus)
                if cpu_before is not None and cpu_after is not None and wall else None)
        same = None
        if name == "threads" and any(key[0] == "as_run" for key in answers):
            same_facts = sum(facts_of(answers.get(("as_run", row["record_id"]))) == facts_of(row["content"])
                             for row in mine)
            same_text = sum(answers.get(("as_run", row["record_id"])) == row["content"] for row in mine)
            same = f"{same_facts}/{len(mine)} facts, {same_text}/{len(mine)} text"
        summary.append((name, statistics.median(r["tokens_per_s"] for r in mine if r["tokens_per_s"]),
                        statistics.median(r["prompt_s"] for r in mine),
                        statistics.median(r["total_s"] for r in mine), busy, same, len(mine)))
        print(f"done: {name}  ({runner_threads()})", flush=True)

    print()
    print(f"{'setting':20s} {'answer tok/s':>12s} {'prompt s':>9s} {'total s':>8s} {'CPU busy':>9s}  same as as_run")
    for name, speed, prompt_s, total_s, busy, same, count in summary:
        print(f"{name:20s} {speed:12.1f} {prompt_s:9.2f} {total_s:8.2f} "
              f"{(f'{busy:.0%}' if busy is not None else '?'):>9s}  {same or ''}")
    print()
    print("CPU busy near 100% means the CPUs were the limit. If 'threads' is much faster than")
    print("'as_run' with the same answers, setting num_thread is a free fix.")

    with open(args.out, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
