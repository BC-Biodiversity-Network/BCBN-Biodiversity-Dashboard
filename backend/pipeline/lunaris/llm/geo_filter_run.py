"""
geo_filter_run.py

Run the BC filter's two LLM passes on a list of records with one model:
pass 1 v3 (geo_prompt_v3.py, the place names), the local GeoNames lookup
(../geonames_lookup.py), then pass 2 C4 (geo_pass2_prompt_c4.py, the facts;
code works out the class). It runs on Fir, started by fir/geo_filter.sbatch,
and on the Mac against the local Ollama, which is how it was tested.

The prompts and the checks on each answer are those of the Mac trials,
through the same functions: geo_prompt_v3.build_prompt and
geo_pass1_trial.finish_answer for pass 1, geonames_lookup.GeoNamesLookup
for the lookup, geo_pass2_prompt_c4.build_prompt and
geo_pass2_trial.finish_answer for pass 2. The request is the shared
ask_ollama's (temperature 0, thinking off, the answer schema, a 16,384-token
context), with Ollama's num_thread option on Fir, as in the v6 run.

The input folder comes from geo_filter_prepare.py: sample.csv and
records.jsonl.gz, each record with the fingerprint of its pass 1 prompt as
the Mac built it. Each pass 1 prompt is rebuilt here and checked against
that fingerprint before anything is asked; if one differs, nothing is asked.

How it works through the records, for its part of them (--part K of
--parts N: the records whose number leaves K when divided by N, so an array
job can split a long run):
  1. pass 1 for every record that has no pass 1 answer yet;
  2. the lookup for every place pass 1 kept, for records not yet looked up
     (or whose pass 1 places changed);
  3. pass 2 for every record that has no pass 2 answer yet, or whose pass 2
     prompt would now be different. A record whose pass 1 failed is not
     asked pass 2; it gets an error saying so, and is asked once pass 1
     succeeds.
A failed answer (an error, bad JSON, or an answer that does not fit the
schema) is asked once more at once (ask_ollama itself also tries a timed-out
call once more). Every result is appended to a progress file as soon as it
comes, so a job that stops loses nothing, and the next job skips what is
done. Failures are also written to logs/failures_partK.txt.

When every record of the run has a result from both passes, the merged
files are written in the Mac trial layout, so geo_pass2_trial.py
(--reclassify), geonames_lookup.py and geo_pass2_score.py read them:

  run.json                     model, digest, Ollama version, settings
  pass1/sample.csv             the records, numbered
  pass1/prompts/NN.txt         the exact pass 1 prompts
  pass1/progress_partK.jsonl   every pass 1 result as it came (all attempts' outcome)
  pass1/lookup_progress_partK.jsonl  the lookup results per record
  pass1/answers.jsonl          merged: one line per record, in order
  pass1/lookup.jsonl, lookup.txt     merged lookup results
  pass1/report.txt             as geo_pass1_trial.py writes it
  pass2/prompts/NN.txt         the exact pass 2 prompts
  pass2/progress_partK.jsonl   every pass 2 result as it came
  pass2/answers.jsonl          merged: one line per record, in order
  pass2/report.txt             as geo_pass2_trial.py writes it
  timing.csv                   seconds per record: pass 1, lookup, pass 2
  logs/failures_partK.txt      one line per failed attempt
  logs/jobs.jsonl              one line per job: where, when, how it ended

--expect-flash-attn off (set by fir/geo_filter.sbatch when flash attention
is turned off, as for qwen3.6 models) makes the run read the runner's command
line in Ollama's log once the model has loaded, and stop before the first
record if it did not start with "--flash-attn off" (check_runner_flash_attn).

--dry-run asks no model and needs no server: it checks the input, rebuilds
and writes the pass 1 prompts, opens the lookup, and says what a run would
do. With --reference-pass1 and --reference-pass2 (a Mac run's folders), it
also checks byte for byte that the pass 1 prompts are the Mac's, and that
the pass 2 prompts built from the Mac's pass 1 answers, with this machine's
lookup, are the Mac's pass 2 prompts.

Exit codes (0 to 6 as in run_batch.py):
  0  every record of this part has an answer from both passes
  1  a setup problem: missing input, no model, another model or digest in
     this folder, or a pass 1 prompt that differs from the Mac's
  2  the server stopped answering part way; rerun
  3  another job holds this part's lock
  4  the run finished but some records have no answer
  5  the model was not entirely on the GPU; nothing more was asked
  6  the model did not load in time
  7  dry run only: some prompts differ from the reference run's

Run, from backend/ (the Mac test):
    python pipeline/lunaris/llm/geo_filter_run.py \
        --input data/lunaris_bc_filter/fir_inputs/label150 \
        --out data/lunaris_bc_filter/fir_mac_test/label150_qwen3.5-4b \
        --bc-boundary /Users/lucia/Desktop/BCBN/Results/bc_boundary_gbif.wkt \
        --model qwen3.5:4b --limit 2 --allow-cpu
"""

import argparse
import gzip
import json
import os
import socket
import sys
import time
from pathlib import Path

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
from bc_filter_rules import load_bc_shape  # noqa: E402
import geo_pass1_trial  # noqa: E402
import geo_pass2_prompt_c4 as pass2_prompt  # noqa: E402
import geo_pass2_trial  # noqa: E402
import geo_prompt_v3 as pass1_prompt  # noqa: E402
import geonames_lookup  # noqa: E402
from ollama_client import (  # noqa: E402
    OLLAMA_NUM_CTX,
    OLLAMA_TIMEOUT,
    OllamaUnreachable,
    ask_ollama,
    gpu_share,
    model_digest,
    prepare_ollama,
    wait_for_server,
)
from prompt_v6 import prompt_sha1  # noqa: E402
from run_batch import CONSECUTIVE_SERVER_FAILURES, LockHeld, normalise_url, take_lock  # noqa: E402
from run_files import append_line, error_kind, now_utc, read_jsonl, repair_jsonl_tail  # noqa: E402

# Error kinds (run_files.error_kind) that point at the server rather than one record.
SERVER_ERRORS = ("timeout_or_network", "server_refused")

# What a pass 2 result says for a record whose pass 1 has no answer.
NO_PASS1 = "pass 1 has no answer for this record, so pass 2 was not asked"


class Stop(Exception):
    """Raised to end the run early with an exit code and a reason (see the codes at the top)."""

    def __init__(self, code, reason):
        """Keep the exit code and the reason."""
        super().__init__(reason)
        self.code = code
        self.reason = reason


# --- Input and folders ---------------------------------------------------

def read_input(folder, limit=None):
    """
    Return the input folder's sample (a DataFrame indexed by number) and its
    records by number, each a dict with record_id, record and pass1_sha1.
    With limit, only the first limit records. Stop if the two files disagree.
    """
    sample = pd.read_csv(os.path.join(folder, "sample.csv"), keep_default_na=False, index_col="number")
    records = {}
    with gzip.open(os.path.join(folder, "records.jsonl.gz"), "rt", encoding="utf-8") as handle:
        for line in handle:
            item = json.loads(line)
            records[item["number"]] = item
    if sorted(records) != list(sample.index) or any(
            records[n]["record_id"] != sample.loc[n, "record_id"] for n in sample.index):
        raise Stop(1, f"{folder}: sample.csv and records.jsonl.gz do not list the same records")
    if limit:
        sample = sample.loc[sample.index < limit]
        records = {n: records[n] for n in sample.index}
    return sample, records


def part_numbers(numbers, part, parts):
    """Return the record numbers that belong to one part: those that leave part when divided by parts."""
    return [n for n in numbers if n % parts == part]


def prompt_name(number):
    """Return a prompt's file name, as the Mac trials name them (00.txt, 01.txt ...)."""
    return f"{number:02d}.txt"


def write_text(path, text):
    """Write a file whole: to a temporary name first, then put it in place, so a reader never sees half of it."""
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(temporary, path)


def write_jsonl(path, lines):
    """Write JSON lines whole (see write_text)."""
    write_text(path, "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines))


def progress_files(folder, stem):
    """Return every part's progress file of one kind in a folder (stem_partK.jsonl), whatever the parts were."""
    if not os.path.isdir(folder):
        return []
    return sorted(os.path.join(folder, name) for name in os.listdir(folder)
                  if name.startswith(stem + "_part") and name.endswith(".jsonl"))


def latest_results(folder, stem):
    """
    Return the latest progress line for each record, by number, over every
    part's file: the latest one with an answer if there is one, else the
    latest line. Lines are compared by when they finished.
    """
    best = {}
    for path in progress_files(folder, stem):
        for line in read_jsonl(path):
            number = line["number"]
            old = best.get(number)
            if old is None or ("answer" in line, line["finished"]) >= ("answer" in old, old["finished"]):
                best[number] = line
    return best


# --- The two passes and the lookup, for one record -----------------------

def ask_with_retry(ask, prompt_text, finish):
    """
    Ask one prompt and check the answer (finish), and if that fails, ask
    once more at once. Return the result, with attempts (1 or 2), the first
    error when a second attempt was needed, and seconds for the attempt
    kept. OllamaUnreachable is not caught: a server that is gone stops the run.
    """
    first_error = None
    for attempt in (1, 2):
        started = time.monotonic()
        result = ask(prompt_text)
        result["seconds"] = round(time.monotonic() - started, 2)
        result = finish(result)
        result["attempts"] = attempt
        if first_error:
            result["first_error"] = first_error
        if "answer" in result:
            return result
        first_error = first_error or result["error"]
    return result


def pass1_places(result):
    """Return the places a pass 1 result kept (geo_pass1_trial.final_places), or None if it has no answer."""
    return geo_pass1_trial.final_places(result) if "answer" in result else None


def place_keys(places):
    """Return (name, as_written) for each place, the way the lookup is asked and pass 2 checks it."""
    return [[p["name"], p["as_written"]] for p in places]


def look_up(lookup, places):
    """
    Look up each place pass 1 kept and return the results as they read back
    from JSON (as pass 2 reads lookup.jsonl on the Mac), with the seconds taken.
    """
    started = time.monotonic()
    results = [lookup.lookup(p["name"], p["as_written"]) for p in places]
    results = json.loads(json.dumps(results, ensure_ascii=False))
    return results, round(time.monotonic() - started, 3)


def build_pass2(record, shape, places, lookups):
    """Return a record's pass 2 prompt (version C4)."""
    return pass2_prompt.build_prompt(record, shape, places, lookups)


# --- The run -------------------------------------------------------------

class Run:
    """
    One job's work on one run folder: the records of its part, the
    progress so far, and where to write. lookup is the GeoNames lookup.
    """

    def __init__(self, out, sample, records, numbers, shape, lookup, part):
        """Keep the run's inputs and work out the paths of this part's files."""
        self.out, self.sample, self.records, self.numbers = out, sample, records, numbers
        self.shape, self.lookup, self.part = shape, lookup, part
        self.pass1_dir = os.path.join(out, "pass1")
        self.pass2_dir = os.path.join(out, "pass2")
        for folder in (os.path.join(self.pass1_dir, "prompts"), os.path.join(self.pass2_dir, "prompts"),
                       os.path.join(out, "logs")):
            os.makedirs(folder, exist_ok=True)
        self.pass1_progress = os.path.join(self.pass1_dir, f"progress_part{part}.jsonl")
        self.lookup_progress = os.path.join(self.pass1_dir, f"lookup_progress_part{part}.jsonl")
        self.pass2_progress = os.path.join(self.pass2_dir, f"progress_part{part}.jsonl")
        self.failures = os.path.join(out, "logs", f"failures_part{part}.txt")
        # The run's records, numbered, as the Mac trials' sample.csv (every part writes the same file).
        write_text(os.path.join(self.pass1_dir, "sample.csv"), sample.to_csv())
        for path in (self.pass1_progress, self.lookup_progress, self.pass2_progress):
            cut = repair_jsonl_tail(path)
            if cut:
                print(f"removed a half-written last line ({cut} bytes) from {os.path.basename(path)}")

    def record(self, number):
        """Return one record's harvest row."""
        return self.records[number]["record"]

    def pass1_prompts(self):
        """
        Build this part's pass 1 prompts, write them to pass1/prompts, and
        check each against the fingerprint made on the Mac. Return them by
        number; raise Stop (exit 1) if any differs, before anything is asked.
        """
        prompts, differ = {}, []
        for number in self.numbers:
            text = pass1_prompt.build_prompt(self.record(number), self.shape)
            write_text(os.path.join(self.pass1_dir, "prompts", prompt_name(number)), text)
            if prompt_sha1(text) != self.records[number]["pass1_sha1"]:
                differ.append(number)
            prompts[number] = text
        if differ:
            raise Stop(1, f"{len(differ)} pass 1 prompts differ from the ones made on the Mac (records "
                          f"{differ[:10]}); copy the code again, or check the Python packages")
        return prompts

    def log_failure(self, stage, number, result):
        """Add one line to this part's failures log."""
        with open(self.failures, "a", encoding="utf-8") as handle:
            handle.write(f"{now_utc()}  {stage}  [{number:02d}] {self.records[number]['record_id']}  "
                         f"attempts {result.get('attempts', 0)}  {result['error'][:300]}\n")

    def save(self, path, stage, number, result, extra):
        """Append one result to a progress file, with its number, record id, time and extra fields."""
        line = {"number": number, "record_id": self.records[number]["record_id"], **extra, **result,
                "finished": now_utc()}
        append_line(path, line)
        if "error" in result:
            self.log_failure(stage, number, result)
        return line

    def state(self):
        """Return the latest pass 1, lookup and pass 2 lines of the whole run, by number."""
        return (latest_results(self.pass1_dir, "progress"), latest_results(self.pass1_dir, "lookup_progress"),
                latest_results(self.pass2_dir, "progress"))

    def todo(self, prompts1):
        """
        Return what is left for this part: the numbers needing pass 1, the
        lookup, and pass 2, each judged from the progress files as the
        docstring at the top describes.
        """
        pass1, lookups, pass2 = self.state()
        need1 = [n for n in self.numbers if "answer" not in pass1.get(n, {})]
        need_lookup, need2 = [], []
        for n in self.numbers:
            places = pass1_places(pass1[n]) if n in pass1 else None
            if places is not None and lookups.get(n, {}).get("places") != place_keys(places):
                need_lookup.append(n)
            done2 = pass2.get(n, {})
            if n in need1 or n in need_lookup or "answer" not in done2:
                need2.append(n)
            elif done2.get("prompt_sha1") != prompt_sha1(
                    build_pass2(self.record(n), self.shape, places, lookups[n]["results"])):
                need2.append(n)
        return need1, need_lookup, need2

    def run(self, ask, check_gpu, prompts1, extra):
        """
        Do this part's three steps (see the top of the file), asking with
        ask(prompt, schema). check_gpu is called after the first answer and
        raises Stop if the model is not on the GPU. extra is added to every
        progress line (model, digest). Returns the counts asked and failed.
        """
        need1, _, _ = self.todo(prompts1)
        print(f"pass 1: {len(self.numbers) - len(need1)} of {len(self.numbers)} done, {len(need1)} to ask")
        counts = {"pass1_asked": 0, "pass1_failed": 0, "lookups": 0, "pass2_asked": 0, "pass2_failed": 0}
        in_a_row = [0]

        def asked(stage, number, result):
            """Check the GPU after the first answer, count server failures in a row, and print one line."""
            if "answer" in result:
                check_gpu()
                in_a_row[0] = 0
            elif error_kind(result["error"]) in SERVER_ERRORS:
                in_a_row[0] += 1
                if in_a_row[0] >= CONSECUTIVE_SERVER_FAILURES:
                    raise Stop(2, f"{in_a_row[0]} requests in a row failed with a timeout or a server "
                                  f"error, so the server looks broken")
            said = "ERROR " + result["error"][:80] if "error" in result else "ok"
            print(f"  {stage} [{number:02d}] {result.get('seconds', 0):5.1f} s  attempts "
                  f"{result.get('attempts', 0)}  {said}", flush=True)

        for number in need1:
            result = ask_with_retry(lambda text: ask(text, pass1_prompt.RESPONSE_SCHEMA), prompts1[number],
                                    lambda r: geo_pass1_trial.finish_answer(r, prompts1[number]))
            self.save(self.pass1_progress, "pass1", number, result,
                      dict(extra, prompt_sha1=self.records[number]["pass1_sha1"]))
            counts["pass1_asked"] += 1
            counts["pass1_failed"] += "error" in result
            asked("pass1", number, result)

        _, need_lookup, _ = self.todo(prompts1)
        pass1, _, _ = self.state()
        for number in need_lookup:
            places = pass1_places(pass1[number])
            results, seconds = look_up(self.lookup, places)
            append_line(self.lookup_progress, {"number": number, "record_id": self.records[number]["record_id"],
                                               "places": place_keys(places), "results": results,
                                               "seconds": seconds, "finished": now_utc()})
            counts["lookups"] += 1
        print(f"lookup: {len(need_lookup)} records looked up")

        _, _, need2 = self.todo(prompts1)
        pass1, lookups, _ = self.state()
        print(f"pass 2: {len(self.numbers) - len(need2)} of {len(self.numbers)} done, {len(need2)} to ask")
        for number in need2:
            places = pass1_places(pass1[number]) if number in pass1 else None
            if places is None:
                result = {"error": NO_PASS1, "attempts": 0}
                self.save(self.pass2_progress, "pass2", number, result, extra)
                continue
            text = build_pass2(self.record(number), self.shape, places, lookups[number]["results"])
            write_text(os.path.join(self.pass2_dir, "prompts", prompt_name(number)), text)
            result = ask_with_retry(lambda t: ask(t, pass2_prompt.RESPONSE_SCHEMA), text,
                                    lambda r: geo_pass2_trial.finish_answer(r, self.record(number), self.shape, text))
            self.save(self.pass2_progress, "pass2", number, result, dict(extra, prompt_sha1=prompt_sha1(text)))
            counts["pass2_asked"] += 1
            counts["pass2_failed"] += "error" in result
            asked("pass2", number, result)
        return counts

    def finish(self):
        """
        If every record of the run has a result from both passes, write the
        merged files (see the top of the file) and return True; else say
        which are missing and return False. Any part's job may do this; the
        files are written whole, so two doing it at once write the same thing.
        """
        pass1, lookups, pass2 = self.state()
        numbers = list(self.sample.index)
        missing = [n for n in numbers if n not in pass1 or n not in pass2]
        if missing:
            print(f"merged files not written yet: {len(missing)} records have no result from both passes "
                  f"(other parts still running, or not run yet)")
            return False
        write_jsonl(os.path.join(self.pass1_dir, "answers.jsonl"), [pass1[n] for n in numbers])
        flat = [dict(result, record=n) for n in numbers if n in lookups and "answer" in pass1[n]
                for result in lookups[n]["results"]]
        write_jsonl(os.path.join(self.pass1_dir, "lookup.jsonl"), flat)
        write_text(os.path.join(self.pass1_dir, "lookup.txt"), "\n".join(geonames_lookup.report_lines(flat)) + "\n")
        prompts1 = {}
        for n in numbers:
            with open(os.path.join(self.pass1_dir, "prompts", prompt_name(n)), encoding="utf-8") as handle:
                prompts1[n] = handle.read()
        geo_pass1_trial.write_report(os.path.join(self.pass1_dir, "report.txt"), self.sample,
                                     {pass1[n]["record_id"]: pass1[n] for n in numbers}, prompts1)
        write_jsonl(os.path.join(self.pass2_dir, "answers.jsonl"), [pass2[n] for n in numbers])
        geo_pass2_trial.write_report(os.path.join(self.pass2_dir, "report.txt"), self.sample.reset_index(),
                                     {n: pass2[n] for n in numbers}, pass2_prompt)
        rows = [{"number": n, "record_id": pass1[n]["record_id"], "pass1_s": pass1[n].get("seconds"),
                 "pass1_attempts": pass1[n].get("attempts"), "lookup_s": lookups.get(n, {}).get("seconds"),
                 "pass2_s": pass2[n].get("seconds"), "pass2_attempts": pass2[n].get("attempts")}
                for n in numbers]
        timing = pd.DataFrame(rows)
        timing["total_s"] = timing[["pass1_s", "lookup_s", "pass2_s"]].sum(axis=1, min_count=1)
        timing.to_csv(os.path.join(self.out, "timing.csv"), index=False)
        answered = [pass2[n] for n in numbers if "answer" in pass2[n]]
        classes = pd.Series([r["decision"]["class"] for r in answered]).value_counts().to_dict()
        print(f"merged files written for {len(numbers)} records: pass 1 failed "
              f"{sum('error' in pass1[n] for n in numbers)}, pass 2 failed {len(numbers) - len(answered)}; "
              f"seconds per record: pass 1 {timing['pass1_s'].mean():.1f}, pass 2 {timing['pass2_s'].mean():.1f}, "
              f"total {timing['total_s'].mean():.1f}; classes {classes}")
        return True


# --- Dry run -------------------------------------------------------------

def compare_with_reference(run, prompts1, reference1, reference2):
    """
    Compare this machine's prompts with a reference run's (the Mac's), byte
    for byte: the pass 1 prompts, and the pass 2 prompts built from the
    reference's own pass 1 answers with this machine's lookup. Differing
    prompts are written to dry_run_differences/ for a look. Return the
    number that differ.
    """
    differ_dir = os.path.join(run.out, "dry_run_differences")
    by_id = {run.records[n]["record_id"]: n for n in run.numbers}
    differ = 0
    same1 = missing1 = 0
    for number in run.numbers:
        path = os.path.join(reference1, "prompts", prompt_name(number))
        if not os.path.exists(path):
            missing1 += 1
            continue
        with open(path, encoding="utf-8") as handle:
            if handle.read() == prompts1[number]:
                same1 += 1
            else:
                differ += 1
                os.makedirs(differ_dir, exist_ok=True)
                write_text(os.path.join(differ_dir, "pass1_" + prompt_name(number)), prompts1[number])
    print(f"pass 1 prompts: {same1} identical to {reference1}, {differ} differ, {missing1} not in the reference")
    if not reference2:
        return differ
    same2 = differ2 = missing2 = 0
    for line in read_jsonl(os.path.join(reference1, "answers.jsonl")):
        number = by_id.get(line["record_id"])
        if number is None:
            continue
        path = os.path.join(reference2, "prompts", prompt_name(number))
        places = pass1_places(line)
        if places is None or not os.path.exists(path):
            missing2 += 1
            continue
        results, _ = look_up(run.lookup, places)
        text = build_pass2(run.record(number), run.shape, places, results)
        with open(path, encoding="utf-8") as handle:
            if handle.read() == text:
                same2 += 1
            else:
                differ2 += 1
                os.makedirs(differ_dir, exist_ok=True)
                write_text(os.path.join(differ_dir, "pass2_" + prompt_name(number)), text)
    print(f"pass 2 prompts (from the reference's pass 1 answers): {same2} identical to {reference2}, "
          f"{differ2} differ, {missing2} not comparable")
    return differ + differ2


# --- Flash attention -----------------------------------------------------

def runner_flash_attn(server_log):
    """
    Return the flash attention setting of the last runner Ollama started, as
    its command line in the server log gives it ("--flash-attn off" gives
    "off"), "none" if that command line has no --flash-attn, or None if the
    log shows no runner command line (Ollama's "starting llama-server" line)
    or cannot be read.
    """
    try:
        lines = [line for line in open(server_log, encoding="utf-8", errors="replace")
                 if "starting llama-server" in line]
    except OSError:
        return None
    if not lines:
        return None
    words = lines[-1].replace('"', " ").split()
    for word, value in zip(words, words[1:]):
        if word == "--flash-attn":
            return value
    return "none"


def check_runner_flash_attn(server_log, expected):
    """
    Print the flash attention setting the runner started with, and raise
    Stop (exit 1) if flash attention should be off but the runner's command
    line does not say "--flash-attn off". Other settings are only reported.
    """
    found = runner_flash_attn(server_log)
    print(f"runner   flash attention: {found or 'no runner command line in the log'} (asked for {expected})")
    if expected == "off" and found != "off":
        raise Stop(1, f"flash attention should be off, but the runner started with "
                      f"{'--flash-attn ' + found if found else 'no command line in ' + server_log}; nothing "
                      f"was asked. Check OLLAMA_FLASH_ATTENTION=0 reached the job (fir/flash_attention.sh)")


# --- Main ----------------------------------------------------------------

def check_run_json(out, model, digest, version, args):
    """
    Write run.json on a folder's first run. On later runs, stop (exit 1) if
    it names another model or digest, so a folder never mixes weights.
    """
    path = os.path.join(out, "run.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            about = json.load(handle)
        if about["model"] != model or about["model_digest"] != digest:
            raise Stop(1, f"{out} holds answers from {about['model']} (digest {about['model_digest'][:12]}), "
                          f"not {model} (digest {digest[:12]}); use a new run folder")
        return
    about = {"model": model, "model_digest": digest, "ollama_version": version, "input": args.input,
             "num_ctx": args.num_ctx, "num_thread": args.num_thread, "pass1_prompt":
             f"v{pass1_prompt.GEO_PROMPT_VERSION}", "pass2_prompt": pass2_prompt.GEO_PASS2_VERSION,
             "created": now_utc()}
    write_text(path, json.dumps(about, indent=2) + "\n")


def main():
    """Run the two passes for this part of the records, or a dry run, and return the exit code."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("--input", required=True, help="the input folder from geo_filter_prepare.py")
    parser.add_argument("--out", required=True, help="the run folder, one per model")
    parser.add_argument("--bc-boundary", required=True, help="bc_boundary_gbif.wkt")
    parser.add_argument("--table", default=geonames_lookup.DEFAULT_TABLE, help="geonames_lookup.parquet")
    parser.add_argument("--codes", default=geonames_lookup.DEFAULT_CODES, help="geonames_codes.json")
    parser.add_argument("--model", default="qwen3.5:4b")
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
    parser.add_argument("--dry-run", action="store_true", help="ask nothing; check and say what would be done")
    parser.add_argument("--reference-pass1", help="dry run: a Mac pass 1 folder to compare prompts with")
    parser.add_argument("--reference-pass2", help="dry run: the Mac pass 2 folder made from it")
    parser.add_argument("--lookup-memory", default="2GB")
    parser.add_argument("--lookup-threads", type=int, default=2)
    args = parser.parse_args()
    if not 0 <= args.part < args.parts:
        print(f"ERROR: --part {args.part} must be from 0 to {args.parts - 1}")
        return 1

    started, host = now_utc(), socket.gethostname()
    job = {"job_id": os.environ.get("SLURM_JOB_ID"), "array_task": os.environ.get("SLURM_ARRAY_TASK_ID"),
           "host": host, "part": args.part, "parts": args.parts, "model": args.model, "started": started,
           "dry_run": args.dry_run}
    os.makedirs(os.path.join(args.out, "logs"), exist_ok=True)
    code, counts, run = 0, {}, None
    try:
        sample, records = read_input(args.input, args.limit)
        numbers = part_numbers(list(sample.index), args.part, args.parts)
        print(f"run {args.out}: {len(sample)} records, part {args.part} of {args.parts} has {len(numbers)}; "
              f"model {args.model}")
        shape = load_bc_shape(args.bc_boundary)
        lookup = geonames_lookup.GeoNamesLookup(args.table, geonames_lookup.DEFAULT_HAND, args.codes,
                                                args.lookup_memory, args.lookup_threads)
        for entry in lookup.missing_hand_ids:
            print(f"warning: hand-added {entry['name']!r} points to GeoNames id {entry['geonames_id']}, "
                  f"which is not in the table")
        run = Run(args.out, sample, records, numbers, shape, lookup, args.part)
        prompts1 = run.pass1_prompts()
        print(f"pass 1 prompts: {len(prompts1)} rebuilt, all identical to the Mac's fingerprints")

        if args.dry_run:
            need1, need_lookup, need2 = run.todo(prompts1)
            print(f"dry run: would ask pass 1 for {len(need1)}, look up {len(need_lookup)} (plus any new from "
                  f"pass 1), ask pass 2 for {len(need2)} of {len(numbers)} records; nothing asked")
            if args.reference_pass1:
                differ = compare_with_reference(run, prompts1, args.reference_pass1, args.reference_pass2)
                code = 7 if differ else 0
            return code

        url = normalise_url(args.ollama_url)
        version = wait_for_server(url, 180)
        digest = model_digest(url, args.model)
        if digest is None:
            raise Stop(1, f"the server at {url} does not have {args.model}. Download it on the login node "
                          f"first, only after Lucia approves (fir/README.md, step 8B). This job never downloads.")
        print(f"Ollama {version} at {url}, {args.model} digest {digest[:12]}")
        job.update(ollama_version=version, model_digest=digest)
        check_run_json(args.out, args.model, digest, version, args)
        if not args.allow_cpu:
            failed_code, message = prepare_ollama(url, args.model, args.num_ctx, args.num_thread,
                                                  args.server_log, load_timeout=args.load_timeout)
            if failed_code:
                raise Stop(failed_code, f"stopped before the first record on {host}: {message}")
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

        def ask(text, schema):
            """Send one prompt with its answer schema to this run's Ollama."""
            return ask_ollama(url, args.model, text, num_ctx=args.num_ctx, timeout=args.timeout,
                              num_thread=args.num_thread, schema=schema)

        extra = {"model": args.model, "model_digest": digest}
        try:
            counts = run.run(ask, check_gpu, prompts1, extra)
        finally:
            lock.unlink(missing_ok=True)
        need1, need_lookup, need2 = run.todo(prompts1)
        code = 4 if need1 or need_lookup or need2 else 0
        if code:
            print(f"left without an answer in this part: pass 1 {len(need1)}, pass 2 {len(need2)} "
                  f"(see logs/failures_part{args.part}.txt); rerun to try them again")
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
    finally:
        if run is not None and not args.dry_run and code in (0, 4):
            run.finish()
        append_line(os.path.join(args.out, "logs", "jobs.jsonl"),
                    dict(job, finished=now_utc(), exit_code=code, **counts))


if __name__ == "__main__":
    sys.exit(main())
