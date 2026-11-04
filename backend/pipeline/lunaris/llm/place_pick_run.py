"""
place_pick_run.py

Run the place lookup's model step (prompt P1, place_pick_prompt.py) on the
input folder from place_pick_prepare.py, with one model. It runs on Fir,
started by fir/place_pick.sbatch, and on the Mac against the local Ollama
for a test. It needs only pandas: no GeoNames table, no shapely.

Every prompt is rebuilt here and checked against the fingerprint made on
the Mac before anything is asked; if one differs, nothing is asked. The
request is the shared ask_ollama's (temperature 0, thinking off, the answer
schema, a 16,384-token context), with Ollama's num_thread option on Fir.

Each record has its prompts (units): one per order (the lookup's and a
shuffled one) and per group of at most MAX_NAMES names. This job does the
records of its part (--part K of --parts N: the records whose number leaves
K when divided by N). A unit with no answer yet is asked; an answer that
does not fit the schema or this prompt (place_pick_prompt.check_answer) is
asked once more at once. Every result is appended to
progress_partK.jsonl as soon as it comes, so a job that stops loses
nothing, and the next job skips what is done. Failures are also written to
logs/failures_partK.txt.

When every unit of the run has a result, the merged files are written:
  run.json            model, digest, Ollama version, settings
  prompts/            the exact prompts (record number, order, chunk)
  answers.jsonl       one line per record, place name and order: the
                      GeoNames id chosen (or none, and why), the quote and
                      the reason, as place_pick_prompt.read_answer gives them
  failed.csv          the units with no answer after both attempts
  report.txt          counts: answers per order, places chosen, the reasons
                      for none, how often the two orders agree, and timing
  logs/jobs.jsonl     one line per job: where, when, how it ended

Nothing here decides what is drawn: the checks (the quote is in the record,
both orders agree, the place is not a large area, and the rules for rivers
and lakes) are stage 3, on the Mac.

Exit codes, as geo_filter_run.py's:
  0  every unit of this part has an answer
  1  a setup problem (missing input, no model, another model or digest in
     this folder, a prompt unlike the Mac's)
  2  the server stopped answering part way; rerun
  3  another job holds this part's lock
  4  the run finished but some units have no answer
  5  the model was not entirely on the GPU; nothing more was asked
  6  the model did not load in time

Run, from backend/ (a Mac test):
    python pipeline/lunaris/llm/place_pick_run.py \\
        --input data/lunaris_place_lookup/fir_inputs/places_p1 \\
        --out data/lunaris_place_lookup/mac_test/places_p1_qwen3.6-35b-a3b \\
        --model qwen3.6:35b-a3b --limit 2 --allow-cpu

A test on chosen records instead of the first ones: --only-ids <file>, a
text file with one dataset id per line (empty lines and lines starting with
"#" are skipped). Every id must be in the input; the records keep their
numbers, so their prompts and fingerprints are the full run's.
"""

import argparse
import gzip
import json
import os
import socket
import sys
import time
from collections import Counter
from pathlib import Path

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import place_pick_prompt as prompt  # noqa: E402
from ollama_client import (  # noqa: E402
    OLLAMA_NUM_CTX,
    OLLAMA_TIMEOUT,
    OllamaUnreachable,
    ask_ollama,
    gpu_share,
    model_digest,
    prepare_ollama,
    runner_flash_attn,
    wait_for_server,
)
from prompt_v6 import prompt_sha1  # noqa: E402
from run_batch import CONSECUTIVE_SERVER_FAILURES, LockHeld, normalise_url, take_lock  # noqa: E402
from run_files import append_line, error_kind, now_utc, read_jsonl, repair_jsonl_tail  # noqa: E402

# Error kinds (run_files.error_kind) that point at the server rather than one prompt.
SERVER_ERRORS = ("timeout_or_network", "server_refused")


class Stop(Exception):
    """Raised to end the run early with an exit code and a reason (see the codes at the top)."""

    def __init__(self, code, reason):
        """Keep the exit code and the reason."""
        super().__init__(reason)
        self.code = code
        self.reason = reason


def read_ids(path):
    """Return the dataset ids in a test list: one per line, skipping empty lines and lines starting with "#"."""
    with open(path, encoding="utf-8") as handle:
        return [line.strip() for line in handle if line.strip() and not line.lstrip().startswith("#")]


def read_input(folder, limit=None, only_ids=None):
    """
    Return the input folder's records, as a list in number order, each with
    its number, dataset id, item and units. With limit, only the first limit
    records; with only_ids (a list of dataset ids), only those records, and
    stop if one is not in the input. Stop if sample.csv and items.jsonl.gz
    disagree.
    """
    sample = pd.read_csv(os.path.join(folder, "sample.csv"), keep_default_na=False)
    with gzip.open(os.path.join(folder, "items.jsonl.gz"), "rt", encoding="utf-8") as handle:
        records = [json.loads(line) for line in handle]
    if [(r["number"], r["dataset_id"]) for r in records] != list(zip(sample["number"], sample["dataset_id"])):
        raise Stop(1, f"{folder}: sample.csv and items.jsonl.gz do not list the same records")
    if only_ids is not None:
        missing = sorted(set(only_ids) - {r["dataset_id"] for r in records})
        if missing or not only_ids:
            raise Stop(1, f"the test list names {len(missing)} dataset ids not in {folder} ({missing[:3]})"
                          if missing else "the test list has no dataset ids")
        records = [r for r in records if r["dataset_id"] in set(only_ids)]
    return records[:limit] if limit else records


def write_text(path, text):
    """Write a text file whole: under a name unique to this machine and process, then moved into place."""
    temporary = f"{path}.tmp.{socket.gethostname()}.{os.getpid()}"
    with open(temporary, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(temporary, path)


def latest_results(folder):
    """
    Return the latest progress line for each unit, over every part's file:
    the latest with an answer if there is one, else the latest line.
    """
    best = {}
    for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        if not (name.startswith("progress_part") and name.endswith(".jsonl")):
            continue
        for line in read_jsonl(os.path.join(folder, name)):
            old = best.get(line["unit"])
            if old is None or ("answer" in line, line["finished"]) >= ("answer" in old, old["finished"]):
                best[line["unit"]] = line
    return best


def finish_answer(result, candidate_ids):
    """
    Check one result against the schema and its prompt (check_answer), and
    return it; an answer that does not fit becomes an error, keeping seconds
    and input_tokens. A result that is already an error is returned as it is.
    """
    if "answer" not in result:
        return result
    problem = prompt.check_answer(result["answer"], candidate_ids)
    if problem:
        return {"error": f"answer does not fit: {problem}", "seconds": result.get("seconds"),
                "input_tokens": result.get("input_tokens")}
    return result


def ask_with_retry(ask, text, candidate_ids):
    """
    Ask one prompt and check the answer, and if that fails, ask once more at
    once. Return the result with attempts (1 or 2), the first error when a
    second attempt was needed, and seconds. OllamaUnreachable is not caught.
    """
    first_error = None
    for attempt in (1, 2):
        started = time.monotonic()
        result = ask(text)
        result["seconds"] = round(time.monotonic() - started, 2)
        result = finish_answer(result, candidate_ids)
        result["attempts"] = attempt
        if first_error:
            result["first_error"] = first_error
        if "answer" in result:
            return result
        first_error = first_error or result["error"]
    return result


class Run:
    """One job's work on one run folder: the records of its part, the progress so far, and where to write."""

    def __init__(self, out, records, part, parts):
        """Keep the run's records and work out this part's units and files."""
        self.out, self.records, self.part = out, records, part
        self.mine = [r for r in records if r["number"] % parts == part]
        for folder in (os.path.join(out, "prompts"), os.path.join(out, "logs")):
            os.makedirs(folder, exist_ok=True)
        self.progress = os.path.join(out, f"progress_part{part}.jsonl")
        self.failures = os.path.join(out, "logs", f"failures_part{part}.txt")
        cut = repair_jsonl_tail(self.progress)
        if cut:
            print(f"removed a half-written last line ({cut} bytes) from {os.path.basename(self.progress)}")

    def prompts(self):
        """
        Build this part's prompts, write them to prompts/, and check each
        against the Mac's fingerprint. Return {unit name: (record, unit,
        text, candidate_ids)}; raise Stop (exit 1) if any differs.
        """
        built, differ = {}, []
        for record in self.mine:
            for unit in record["units"]:
                text, ids = prompt.build_prompt(record["item"], unit["indexes"], unit["order"])
                write_text(os.path.join(self.out, "prompts", unit["unit"] + ".txt"), text)
                if prompt_sha1(text) != unit["sha1"]:
                    differ.append(unit["unit"])
                built[unit["unit"]] = (record, unit, text, ids)
        if differ:
            raise Stop(1, f"{len(differ)} prompts differ from the ones made on the Mac ({differ[:5]}); copy the "
                          f"code again, or check the Python version")
        return built

    def todo(self, built):
        """Return this part's unit names with no answer yet, in order."""
        done = latest_results(self.out)
        return [name for name in built if "answer" not in done.get(name, {})]

    def run(self, ask, check_gpu, built, extra):
        """
        Ask every unit of this part that has no answer, with ask(text).
        check_gpu is called after the first answer. extra is added to every
        progress line (model, digest). Returns the counts asked and failed.
        """
        todo = self.todo(built)
        print(f"prompts: {len(built) - len(todo)} of {len(built)} done, {len(todo)} to ask")
        counts, in_a_row = {"asked": 0, "failed": 0}, 0
        for name in todo:
            record, unit, text, ids = built[name]
            result = ask_with_retry(ask, text, ids)
            line = {"number": record["number"], "dataset_id": record["dataset_id"], "unit": name,
                    "order": unit["order"], "chunk": unit["chunk"], "prompt_sha1": unit["sha1"], **extra, **result}
            if "answer" in result:
                line["decisions"] = prompt.read_answer(record["item"], unit, result["answer"], ids)
            append_line(self.progress, dict(line, finished=now_utc()))
            counts["asked"] += 1
            if "answer" in result:
                check_gpu()
                in_a_row = 0
            else:
                counts["failed"] += 1
                with open(self.failures, "a", encoding="utf-8") as handle:
                    handle.write(f"{now_utc()}  {name}  {record['dataset_id']}  attempts {result.get('attempts', 0)}  "
                                 f"{result['error'][:300]}\n")
                if error_kind(result["error"]) in SERVER_ERRORS:
                    in_a_row += 1
                    if in_a_row >= CONSECUTIVE_SERVER_FAILURES:
                        raise Stop(2, f"{in_a_row} prompts in a row failed with a timeout or a server error")
            said = "ERROR " + result["error"][:80] if "error" in result else "ok"
            print(f"  {name} {result.get('seconds', 0):5.1f} s  attempts {result.get('attempts', 0)}  {said}",
                  flush=True)
        return counts

    def finish(self):
        """
        If every unit of the run has a result, write answers.jsonl,
        failed.csv and report.txt and return True; else say how many are
        missing and return False.
        """
        done = latest_results(self.out)
        names = [unit["unit"] for record in self.records for unit in record["units"]]
        missing = [name for name in names if name not in done]
        if missing:
            print(f"merged files not written yet: {len(missing)} prompts have no result (other parts still "
                  f"running, or not run yet)")
            return False
        decisions = [d for name in names if "answer" in done[name] for d in done[name]["decisions"]]
        write_text(os.path.join(self.out, "answers.jsonl"),
                   "".join(json.dumps(d, ensure_ascii=False) + "\n" for d in decisions))
        failed = [{"unit": name, "dataset_id": done[name]["dataset_id"], "error": done[name]["error"]}
                  for name in names if "answer" not in done[name]]
        write_text(os.path.join(self.out, "failed.csv"), pd.DataFrame(failed, columns=["unit", "dataset_id", "error"])
                   .to_csv(index=False))
        write_text(os.path.join(self.out, "report.txt"), "\n".join(report_lines(decisions, done, names)) + "\n")
        print(f"merged files written: {len(decisions):,} answers, {len(failed)} prompts failed")
        return True


def report_lines(decisions, done, names):
    """Return report.txt's lines: answers per order, places chosen, reasons for none, agreement and timing."""
    frame = pd.DataFrame(decisions)
    lines = [f"Place lookup, model step ({prompt.PLACE_PICK_VERSION}): {len(names):,} prompts, "
             f"{sum('answer' in done[n] for n in names):,} answered", ""]
    if frame.empty:
        return lines
    for order, part in frame.groupby("order"):
        chosen = part["geonameid"].notna() | (part["candidate_number"] > 0)
        lines.append(f"{order}: {len(part):,} names, a place chosen for {int(chosen.sum()):,}; none because: "
                     f"{dict(Counter(part.loc[~chosen, 'none_because']))}")
    # The place each order chose for each record and name (None for none).
    # Plain dictionaries, not a pivot table: a pivot drops an order whose
    # answers all chose none, which stopped the first Fir test (KeyError).
    chosen_by = {}
    for decision in decisions:
        chosen_by.setdefault((decision["dataset_id"], decision["name"]), {})[decision["order"]] = decision["geonameid"]
    both = [found for found in chosen_by.values() if all(order in found for order in prompt.ORDERS)]
    if both:
        first, second = prompt.ORDERS
        same_place = sum(found[first] is not None and found[first] == found[second] for found in both)
        both_none = sum(found[first] is None and found[second] is None for found in both)
        lines.append(f"both orders answered for {len(both):,} names; the same place (or both none) for "
                     f"{same_place + both_none:,} (the same place {same_place:,}, both none {both_none:,}), "
                     f"different for {len(both) - same_place - both_none:,}")
    seconds = [done[n].get("seconds") for n in names if done[n].get("seconds") is not None]
    if seconds:
        lines.append(f"seconds per prompt: mean {sum(seconds) / len(seconds):.1f}, longest {max(seconds):.1f}")
    return lines


def check_runner_flash_attn(server_log, expected):
    """Stop (exit 1) if flash attention should be off but the runner did not start with "--flash-attn off"."""
    found = runner_flash_attn(server_log)
    print(f"runner   flash attention: {found or 'no runner command line in the log'} (asked for {expected})")
    if expected == "off" and found != "off":
        raise Stop(1, f"flash attention should be off, but the runner started with "
                      f"{'--flash-attn ' + found if found else 'no command line in ' + server_log}; nothing was asked")


def check_run_json(out, model, digest, version, args):
    """Write run.json on a folder's first run; on later runs, stop (exit 1) if it names another model or digest."""
    path = os.path.join(out, "run.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            about = json.load(handle)
        if about["model"] != model or about["model_digest"] != digest:
            raise Stop(1, f"{out} holds answers from {about['model']} (digest {about['model_digest'][:12]}), "
                          f"not {model} (digest {digest[:12]}); use a new run folder")
        return
    write_text(path, json.dumps({"model": model, "model_digest": digest, "ollama_version": version,
                                 "input": args.input, "limit": args.limit, "only_ids": args.only_ids,
                                 "num_ctx": args.num_ctx, "num_thread": args.num_thread,
                                 "prompt": prompt.PLACE_PICK_VERSION, "created": now_utc()}, indent=2) + "\n")


def main():
    """Ask this part's prompts, or do a dry run, and return the exit code."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("--input", required=True, help="the input folder from place_pick_prepare.py")
    parser.add_argument("--out", required=True, help="the run folder, one per model")
    parser.add_argument("--model", default="qwen3.6:35b-a3b")
    parser.add_argument("--ollama-url", default="http://localhost:11434")
    parser.add_argument("--num-ctx", type=int, default=OLLAMA_NUM_CTX)
    parser.add_argument("--num-thread", type=int, default=None, help="Ollama's CPU threads (Fir: the job's CPUs)")
    parser.add_argument("--timeout", type=int, default=OLLAMA_TIMEOUT, help="seconds allowed for one answer")
    parser.add_argument("--server-log", help="Ollama's log, to check it found the GPU (Fir)")
    parser.add_argument("--load-timeout", type=int, default=360, help="seconds the model may take to load")
    parser.add_argument("--allow-cpu", action="store_true", help="skip the GPU checks (the Mac)")
    parser.add_argument("--expect-flash-attn", choices=["off", "on", "auto"],
                        help="the flash attention setting asked for; with off, stop if the runner did not use it")
    parser.add_argument("--part", type=int, default=0)
    parser.add_argument("--parts", type=int, default=1)
    parser.add_argument("--limit", type=int, help="only the first this many records (a test)")
    parser.add_argument("--only-ids", help="only the records whose dataset ids this file lists (a test)")
    parser.add_argument("--dry-run", action="store_true", help="ask nothing; check the prompts and say what would be done")
    args = parser.parse_args()
    if not 0 <= args.part < args.parts:
        print(f"ERROR: --part {args.part} must be from 0 to {args.parts - 1}")
        return 1
    if args.limit and args.only_ids:
        print("ERROR: give --limit or --only-ids, not both")
        return 1

    host = socket.gethostname()
    job = {"job_id": os.environ.get("SLURM_JOB_ID"), "array_task": os.environ.get("SLURM_ARRAY_TASK_ID"),
           "host": host, "part": args.part, "parts": args.parts, "model": args.model, "started": now_utc(),
           "dry_run": args.dry_run}
    os.makedirs(os.path.join(args.out, "logs"), exist_ok=True)
    code, counts, run = 0, {}, None
    try:
        records = read_input(args.input, args.limit, read_ids(args.only_ids) if args.only_ids else None)
        run = Run(args.out, records, args.part, args.parts)
        built = run.prompts()
        print(f"run {args.out}: {len(records)} records, part {args.part} of {args.parts} has {len(run.mine)} "
              f"({len(built)} prompts, all identical to the Mac's fingerprints); model {args.model}")
        if args.dry_run:
            print(f"dry run: would ask {len(run.todo(built))} prompts; nothing asked")
            return 0

        url = normalise_url(args.ollama_url)
        version = wait_for_server(url, 180)
        digest = model_digest(url, args.model)
        if digest is None:
            raise Stop(1, f"the server at {url} does not have {args.model}; this job never downloads")
        print(f"Ollama {version} at {url}, {args.model} digest {digest[:12]}")
        job.update(ollama_version=version, model_digest=digest)
        check_run_json(args.out, args.model, digest, version, args)
        if not args.allow_cpu:
            failed_code, message = prepare_ollama(url, args.model, args.num_ctx, args.num_thread, args.server_log,
                                                  load_timeout=args.load_timeout)
            if failed_code:
                raise Stop(failed_code, f"stopped before the first prompt on {host}: {message}")
            print(message)
        if args.server_log and args.expect_flash_attn:
            check_runner_flash_attn(args.server_log, args.expect_flash_attn)
        lock = Path(args.out) / "locks" / f"part{args.part}.lock"
        try:
            take_lock(lock)
        except LockHeld as error:
            raise Stop(3, f"part {args.part} is being worked on by {error}")
        checked = [args.allow_cpu]

        def check_gpu():
            """After the first answer, make sure all of the model is on the GPU (not on the Mac)."""
            if checked[0]:
                return
            checked[0] = True
            share = gpu_share(url, args.model)
            print(f"model on the GPU: {'unknown' if share is None else f'{share:.0%}'}")
            if share is None or share < 0.999:
                raise Stop(5, f"the model is not entirely on the GPU on {host}; resubmit with "
                              f"EXCLUDE={host.split('.')[0]}")

        def ask(text):
            """Send one prompt with the answer schema to this run's Ollama."""
            return ask_ollama(url, args.model, text, num_ctx=args.num_ctx, timeout=args.timeout,
                              num_thread=args.num_thread, schema=prompt.RESPONSE_SCHEMA)

        try:
            counts = run.run(ask, check_gpu, built, {"model": args.model, "model_digest": digest})
        finally:
            lock.unlink(missing_ok=True)
        left = run.todo(built)
        code = 4 if left else 0
        if left:
            print(f"left without an answer in this part: {len(left)} prompts (see logs/failures_part{args.part}.txt); "
                  f"rerun to try them again")
        return code
    except Stop as stop:
        print(f"STOPPED (exit {stop.code}): {stop.reason}")
        code = stop.code
        job["reason"] = stop.reason
        return code
    except OllamaUnreachable as error:
        print(f"STOPPED (exit 2): the server stopped answering ({error})")
        code = 2
        job["reason"] = str(error)
        return code
    except Exception as error:
        # Anything unexpected: record exit 1 in logs/jobs.jsonl (not 0), then
        # let Python print the traceback and end with exit 1.
        code = 1
        job["reason"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        # The job's line in logs/jobs.jsonl is written even if writing the
        # merged files fails (that error then ends the job with exit 1).
        try:
            if run is not None and not args.dry_run and code in (0, 4):
                run.finish()
        except Exception as error:
            code = 1
            job["reason"] = f"writing the merged files: {type(error).__name__}: {error}"
            raise
        finally:
            append_line(os.path.join(args.out, "logs", "jobs.jsonl"), dict(job, finished=now_utc(), exit_code=code,
                                                                            **counts))


if __name__ == "__main__":
    sys.exit(main())
