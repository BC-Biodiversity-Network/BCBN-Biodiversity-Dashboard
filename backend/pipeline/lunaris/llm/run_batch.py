"""
Ask the model about every record in one batch, and save each answer as it comes.

This is the cluster version of run_trial.py's loop. It is run by the Slurm
job script fir/llm_array.sbatch, one batch per array task, against a server
the job script starts on the same node: Ollama (the default) or, for the
plan B test, vLLM (--backend vllm). It also runs on the Mac against the local
Ollama, which is how it was tested.

What it does:

  1. Reads run.json and the batch's input records.
  2. Takes the batch's lock file, so two jobs never write the same batch.
  3. Checks the server is up and has the model, and records the model's
     digest (for vLLM, a fingerprint of the weight files). It never
     downloads a model: on Fir that is done once on the login node. With
     Ollama, after the first answer it checks the whole model is on the GPU,
     and stops before writing anything if it is not.
  4. Skips every record that already has a valid answer in
     results/batch_NNNN.jsonl, so rerunning a batch only does what is missing.
  5. For each remaining record, rebuilds the prompt, checks its fingerprint
     matches the one made on the Mac, asks the model, checks the answer fits
     the schema, and appends it to the results file at once. A failure goes
     to errors/batch_NNNN.jsonl instead and is tried again next time.

With Ollama the request is the same as run_trial.py's: the shared
ask_ollama, with temperature 0, thinking off, the answer schema, and a 16,384
token context. --num-thread adds Ollama's num_thread option, which does not
change the answers. With vLLM, vllm_client.ask_vllm asks for the same things.

Exit codes, so the Slurm log and sacct show how a batch ended:
  0  every record in the batch has a valid answer
  1  a setup problem: no server, no model, a different model digest, or a
     prompt that does not match the Mac's
  2  the server stopped answering part way; rerun the batch
  3  another job holds this batch's lock
  4  the run finished but some records still have no valid answer
  5  the model was not entirely on the GPU, so nothing was written; resubmit
     on another node (fir/submit.sh with EXCLUDE=<this node>)

Usage:

    python run_batch.py RUN_DIR --batch 0 --ollama-url http://127.0.0.1:11434
    python run_batch.py RUN_DIR --batch 0 --workers 4
    python run_batch.py RUN_DIR --batch 0 --limit 5          # smoke test
    python run_batch.py RUN_DIR --batch 0 --num-ctx 65536    # retry long records
    python run_batch.py RUN_DIR --batch 0 --num-thread 8     # Ollama CPU threads
    python run_batch.py RUN_DIR --batch 0 --backend vllm --ollama-url http://127.0.0.1:8000 --workers 32
"""

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ollama_client import (  # noqa: E402
    OLLAMA_NUM_CTX,
    OLLAMA_TIMEOUT,
    OllamaUnreachable,
    ask_ollama,
    gpu_share,
    model_digest,
    wait_for_server,
)
from prompt_v6 import PROMPT_VERSION, build_prompt, check_answer, prompt_sha1  # noqa: E402
import vllm_client  # noqa: E402
from run_files import (  # noqa: E402
    append_line,
    batch_name,
    error_kind,
    errors_path,
    gpu_failures_path,
    load_run,
    lock_path,
    now_utc,
    read_inputs,
    read_jsonl,
    repair_jsonl_tail,
    results_path,
    valid_results,
)

# How many records between two progress lines in the log.
REPORT_EVERY = 25


class LockHeld(Exception):
    """Raised when another job, still running, is working on this batch."""


def lock_is_stale(info):
    """
    Decide whether a lock file left by an earlier job can be taken over.

    A lock is stale when the job that wrote it has ended, for example because
    it hit its time limit. On the cluster this asks Slurm whether that job id
    is still in the queue. Without Slurm (on the Mac) it checks whether the
    process is still alive on the same machine. When neither can be checked,
    the lock is treated as held, to be safe.
    """
    job_id = info.get("job_id")
    if job_id and shutil.which("squeue"):
        answer = subprocess.run(["squeue", "-h", "-j", str(job_id), "-o", "%i"],
                                capture_output=True, text=True)
        return answer.stdout.strip() == ""
    if info.get("host") == socket.gethostname() and info.get("pid"):
        try:
            os.kill(int(info["pid"]), 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        return False
    return False


def take_lock(path):
    """
    Create the batch's lock file, or take over a stale one.

    The file holds the Slurm job id, the host and the process id, so a later
    job can tell whether the holder is still running. Raises LockHeld if it is.
    """
    info = {"job_id": os.environ.get("SLURM_JOB_ID"), "host": socket.gethostname(),
            "pid": os.getpid(), "since": now_utc()}
    path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            # O_EXCL makes the create fail if the file already exists, so only
            # one job can win.
            handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(handle, "w") as out:
                out.write(json.dumps(info))
            return
        except FileExistsError:
            try:
                held = json.loads(path.read_text())
            except (OSError, ValueError):
                held = {}
            if not lock_is_stale(held):
                raise LockHeld(f"job {held.get('job_id')} on {held.get('host')} "
                               f"(pid {held.get('pid')}) since {held.get('since')}")
            print(f"taking over a stale lock from job {held.get('job_id')} "
                  f"on {held.get('host')}")
            path.unlink(missing_ok=True)
    raise LockHeld("could not create the lock file")


def ask_one(record, ask):
    """
    Build one record's prompt, check it, and ask the model.

    Runs in a worker thread. ask is the function that sends one prompt to the
    server (Ollama or vLLM). Returns the record, the result in ask_ollama's
    shape ({"answer": ...} or {"error": ...}) and how many seconds the call
    took. A prompt whose fingerprint differs from the batch file is not sent.
    """
    prompt = build_prompt(record)
    if prompt_sha1(prompt) != record["prompt_sha1"]:
        return record, {"error": "prompt differs from the one made on the Mac"}, 0.0
    started = time.monotonic()
    result = ask(prompt)
    seconds = time.monotonic() - started
    if "answer" in result:
        problem = check_answer(result["answer"])
        if problem:
            result = {"error": f"answer does not fit the schema: {problem}"}
    return record, result, seconds


def normalise_url(text):
    """Turn an address like 127.0.0.1:11434 (as OLLAMA_HOST holds it) into a URL."""
    return text if text.startswith("http") else "http://" + text


def main():
    """Run one batch and return the exit code described at the top of the file."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("run_dir", help="the run folder made by make_batches.py")
    parser.add_argument("--batch", type=int, default=os.environ.get("SLURM_ARRAY_TASK_ID"),
                        help="the batch number (default: the Slurm array task id)")
    parser.add_argument("--backend", choices=["ollama", "vllm"], default="ollama",
                        help="which kind of server answers (default ollama)")
    parser.add_argument("--ollama-url", default=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
                        help="the server's address, Ollama or vLLM (default: $OLLAMA_HOST, "
                             "else localhost:11434)")
    parser.add_argument("--num-thread", type=int, default=None,
                        help="Ollama's num_thread: CPU threads for its runner (default: Ollama picks)")
    parser.add_argument("--workers", type=int, default=1,
                        help="questions in flight at once. Match OLLAMA_NUM_PARALLEL (default 1)")
    parser.add_argument("--num-ctx", type=int, default=OLLAMA_NUM_CTX,
                        help=f"Ollama context size in tokens (default {OLLAMA_NUM_CTX})")
    parser.add_argument("--timeout", type=int, default=OLLAMA_TIMEOUT,
                        help=f"seconds to wait for one answer (default {OLLAMA_TIMEOUT})")
    parser.add_argument("--wait", type=int, default=None,
                        help="seconds to wait for the server to come up "
                             "(default 180 for Ollama, 900 for vLLM, which starts slowly)")
    parser.add_argument("--allow-cpu", action="store_true",
                        help="carry on even if part of the model runs on the CPU (for tests only)")
    parser.add_argument("--limit", type=int, default=0,
                        help="only ask about this many of the remaining records (smoke test)")
    args = parser.parse_args()

    if args.batch is None:
        print("ERROR: no batch number. Give --batch, or run this as a Slurm array task.")
        return 1
    run_dir = Path(args.run_dir)
    number = int(args.batch)
    url = normalise_url(args.ollama_url)
    run = load_run(run_dir)
    model = run["model"]
    records = read_inputs(run_dir, number)
    name = batch_name(number)
    print(f"{name} of run {run_dir.name}: {len(records):,} records, model {model}, "
          f"prompt v{PROMPT_VERSION}, {args.backend}, {args.workers} worker(s), "
          f"context {args.num_ctx:,}" + (f", {args.num_thread} threads" if args.num_thread else ""))

    lock = lock_path(run_dir, number)
    try:
        take_lock(lock)
    except LockHeld as held:
        print(f"ERROR: {name} is being worked on by {held}. Not starting a second copy.")
        return 3

    # Slurm sends SIGTERM when the time limit is reached. Turning it into an
    # ordinary exit lets the lock file be removed on the way out. Every answer
    # already written is safe on disk either way.
    def on_term(signum, frame):
        """Stop the run when Slurm asks the job to end."""
        raise SystemExit(2)
    signal.signal(signal.SIGTERM, on_term)

    try:
        return run_one_batch(args, run_dir, number, run, model, records, url)
    finally:
        lock.unlink(missing_ok=True)


def run_one_batch(args, run_dir, number, run, model, records, url):
    """Do the work of main() once the lock is held. Returns the exit code."""
    name = batch_name(number)
    out_path = results_path(run_dir, number)
    err_path = errors_path(run_dir, number)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    err_path.parent.mkdir(parents=True, exist_ok=True)
    for path in (out_path, err_path):
        cut = repair_jsonl_tail(path)
        if cut:
            print(f"removed a half-written last line ({cut} bytes) from {path.name}")

    vllm = args.backend == "vllm"
    try:
        if vllm:
            version = vllm_client.wait_for_server(url, args.wait or 900)
            digest = vllm_client.weights_fingerprint(url, model)
        else:
            version = wait_for_server(url, args.wait or 180)
            digest = model_digest(url, model)
    except (OllamaUnreachable, vllm_client.ServerUnreachable) as error:
        print(f"ERROR: {error}")
        return 1
    if digest is None:
        print(f"ERROR: the server at {url} does not have {model}. Download it on the login node "
              f"first (see fir/README.md). This job does not download anything.")
        return 1
    print(f"{args.backend} {version} at {url}, {model} digest {digest[:12]}")

    def ask(prompt):
        """Send one prompt to this run's server."""
        if vllm:
            return vllm_client.ask_vllm(url, model, prompt, timeout=args.timeout)
        return ask_ollama(url, model, prompt, num_ctx=args.num_ctx, timeout=args.timeout,
                          num_thread=args.num_thread)

    # Every answer in a run must come from the same weights. If this batch
    # already holds answers from another digest, stop instead of mixing them.
    earlier = {line.get("model_digest") for line in read_jsonl(out_path)} - {None}
    if earlier - {digest}:
        print(f"ERROR: {out_path.name} already has answers from digest(s) "
              f"{', '.join(sorted(d[:12] for d in earlier))}, but the server now has "
              f"{digest[:12]}. Start a new run folder rather than mixing models.")
        return 1

    done = valid_results(run_dir, number, run, records)
    pending = [record for record in records if record["record_id"] not in done]
    if args.limit:
        pending = pending[:args.limit]
    print(f"{len(done):,} already done, {len(pending):,} to ask now")

    job_id = os.environ.get("SLURM_JOB_ID")
    host = socket.gethostname()
    fresh = 0
    failed = {}
    seconds = []
    started = time.monotonic()
    stop_reason = None
    stop_code = 0
    gpu_checked = False

    pool = ThreadPoolExecutor(max_workers=args.workers)
    queue = iter(pending)
    running = set()

    def top_up():
        """Keep a few questions queued per worker, without building them all at once."""
        while len(running) < args.workers * 2:
            record = next(queue, None)
            if record is None:
                return
            running.add(pool.submit(ask_one, record, ask))

    try:
        top_up()
        while running:
            finished, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in finished:
                running.discard(future)
                try:
                    record, result, took = future.result()
                except (OllamaUnreachable, vllm_client.ServerUnreachable) as error:
                    if stop_reason is None:
                        stop_reason, stop_code = f"the server stopped answering ({error})", 2
                    continue
                if stop_code == 5:
                    # The model is not on the GPU: answers still in flight
                    # are dropped, not written.
                    continue
                record_id = record["record_id"]
                if "answer" in result and not gpu_checked and not vllm:
                    # Once the model is loaded, make sure all of it is on the
                    # GPU before keeping any answer. On a busy Fir node
                    # Ollama's GPU discovery can time out, and then it runs
                    # the model on the CPU without stopping.
                    gpu_checked = True
                    share = gpu_share(url, model)
                    print(f"model on the GPU: {'unknown' if share is None else f'{share:.0%}'}")
                    if (share is None or share < 0.999) and not args.allow_cpu:
                        append_line(gpu_failures_path(run_dir), {
                            "batch": number, "host": host, "job_id": job_id,
                            "gpu_share": share, "finished": now_utc(),
                        })
                        stop_reason, stop_code = (f"the model is not entirely on the GPU on {host} "
                                                  f"(GPU discovery probably timed out). Resubmit "
                                                  f"with EXCLUDE={host.split('.')[0]}"), 5
                        continue
                if "answer" in result:
                    append_line(out_path, {
                        "record_id": record_id,
                        "model": model,
                        "model_digest": digest,
                        "backend": args.backend,
                        "server_version": version,
                        "ollama_version": None if vllm else version,
                        "num_thread": args.num_thread,
                        "prompt_version": PROMPT_VERSION,
                        "prompt_sha1": record["prompt_sha1"],
                        "num_ctx": args.num_ctx,
                        "workers": args.workers,
                        "answer": result["answer"],
                        "input_tokens": result.get("input_tokens"),
                        "output_tokens": result.get("output_tokens"),
                        "thinking_tokens": result.get("thinking_tokens"),
                        "seconds": round(took, 3),
                        "timings": result.get("timings"),
                        "host": host,
                        "job_id": job_id,
                        "finished": now_utc(),
                    })
                    failed.pop(record_id, None)
                    seconds.append(took)
                else:
                    kind = error_kind(result["error"])
                    append_line(err_path, {
                        "record_id": record_id,
                        "error": result["error"],
                        "kind": kind,
                        "num_ctx": args.num_ctx,
                        "host": host,
                        "job_id": job_id,
                        "finished": now_utc(),
                    })
                    failed[record_id] = kind
                    print(f"    {record_id}: {result['error'][:150]}")
                    if kind == "prompt_mismatch" and stop_reason is None:
                        stop_reason, stop_code = ("a prompt differs from the one made on the Mac, "
                                                  "so the code or its packages differ"), 1
                fresh += 1
                if fresh % REPORT_EVERY == 0:
                    rate = fresh / (time.monotonic() - started) * 3600
                    print(f"  {len(done) + len(seconds):,} of {len(records):,} done, "
                          f"{len(failed)} failed this run, {rate:,.0f} records/hour", flush=True)
            if stop_reason is None:
                top_up()
    except BaseException:
        # Interrupted, for example by Slurm's time limit: note it, so the
        # questions still in flight are not waited for on the way out.
        if stop_reason is None:
            stop_reason, stop_code = "interrupted", 2
        raise
    finally:
        # Do not wait for questions still in flight if the run is ending early.
        pool.shutdown(wait=stop_reason is None, cancel_futures=True)

    done = valid_results(run_dir, number, run, records)
    missing = len(records) - len(done)
    elapsed = time.monotonic() - started
    print()
    print(f"{name}: {len(done):,} of {len(records):,} records have a valid answer")
    print(f"  asked this run: {fresh:,} in {elapsed / 60:.1f} min"
          + (f", {fresh / elapsed * 3600:,.0f} records/hour" if fresh and elapsed else ""))
    if seconds:
        print(f"  seconds per call: {sum(seconds) / len(seconds):.2f} on average, "
              f"{min(seconds):.2f} to {max(seconds):.2f}")
    if failed:
        kinds = {}
        for kind in failed.values():
            kinds[kind] = kinds.get(kind, 0) + 1
        print("  failed this run: " + ", ".join(f"{kind} {count}" for kind, count in sorted(kinds.items())))
        if "context_full" in kinds:
            print("  context_full records need a larger context: rerun with --num-ctx 65536")
    if stop_reason:
        print(f"STOPPED EARLY: {stop_reason}. Everything written so far is kept; rerun the batch.")
        return stop_code
    return 0 if missing == 0 else 4


if __name__ == "__main__":
    sys.exit(main())
