"""
check_filter3_failures.py

Look at the records that failed in the DataONE filter 3 full run (job
5559473, 70,521 records in 30 parts, qwen3.6:35b-a3b, NUM_CTX 65536,
variant dataone), so Lucia can decide how to finish them. It only reads:
nothing in pipeline/, the run folder or the inputs is changed, and no
pipeline code imports this file. No network.

What it checks and finds:
  1. The failure files (logs/failures_part*.txt) list exactly the expected
     16 records (EXPECTED), and every other record has an answer from both
     passes in the merged files (pass1/answers.jsonl, pass2/answers.jsonl),
     which also agree with the latest lines of the progress files.
  2. For each of the 16: id, repository, title, a link, its filter 3 group
     (filter3_rules.parquet), and its boxes measured against BC with the
     rule code build_filter3_rules.py uses (bc_filter_rules.classify_box,
     and classify_record to check the stored rule result).
  3. Pass 1 timeouts: the prompt's length in characters, and from the
     Ollama server logs (logs/ollama_*.log, written by llama-server at
     verbosity 4) the prompt's tokens and how many tokens the model had
     written when each call was cut off.
  4. Pass 2 context_full: the pass 1 places, the GeoNames candidates the
     prompt shows and how many are in BC, the prompt rebuilt in the dataone
     variant and compared with the saved prompt and fingerprint, its length
     in characters, its real length in tokens (the log's "truncating input
     prompt" line), an estimate from characters, and the NUM_CTX it needs.
  5. GPU memory: every buffer llama-server says it put on the GPU, at
     NUM_CTX 16384 (the label test run) and 65536 (this run), and a straight
     line through the two for larger contexts (a guess, said so), against
     the 3g.40gb slice's free memory.
  6. Prompt reading speed for long prompts, from the logs, to guess how long
     a longer prompt takes (a guess, said so).

It writes ~/Desktop/BCBN/Results/dataone_filter3_failures_2026-11-24/failed16.csv,
one row per record (see ROW_COLUMNS), and prints the rest.

Run, from backend/:
    ../.venv/bin/python exploration/dataone/check_filter3_failures.py
"""

import argparse
import glob
import gzip
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from statistics import median
from urllib.parse import quote

import pandas as pd
from shapely.geometry import Point

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, "..", ".."))
LLM = os.path.join(BACKEND, "pipeline", "lunaris", "llm")
sys.path.insert(0, os.path.join(BACKEND, "pipeline", "dataone"))
sys.path.insert(0, os.path.join(BACKEND, "pipeline", "lunaris"))
sys.path.insert(0, LLM)
from bc_filter_rules import (  # noqa: E402
    classify_box, classify_points, classify_record, load_rules, measure_box, points_of)
from build_filter3_rules import OUT as RULES  # noqa: E402
from classify_multibox_records import BC_BOUNDARY  # noqa: E402
from filter3_records import read_fields  # noqa: E402
from geo_filter_run import NO_PASS1, build_pass2, latest_results, pass1_places, prompt_name, read_input  # noqa: E402
from ollama_client import OLLAMA_TIMEOUT  # noqa: E402
from prompt_v6 import prompt_sha1  # noqa: E402
from run_files import read_jsonl  # noqa: E402

RUN = os.path.join(BACKEND, "data", "dataone", "filter3_runs", "dataone_needs_llm_qwen3.6-35b-a3b_nofa")
INPUT = os.path.join(BACKEND, "data", "dataone", "filter3_input", "dataone_needs_llm")
# The label test run: the same model, flash attention off and slice, at NUM_CTX 16384 (its first job).
LABEL_TEST_LOGS = os.path.join(BACKEND, "data", "dataone", "filter3_runs", "dataone_label_test_qwen3.6-35b-a3b_nofa",
                               "logs")
OUT_DIR = os.path.expanduser("~/Desktop/BCBN/Results/dataone_filter3_failures_2026-11-24")
VARIANT = "dataone"
VIEW = "https://search.dataone.org/view/"

# The 16 failures Lucia listed: record number -> (what failed, the record id).
EXPECTED = {
    5050: ("pass1_timeout", "doi:10.6085/AA/pisco_recruitment.1441.2"),
    5622: ("pass1_timeout", "https://pasta.lternet.edu/package/metadata/eml/edi/1913/1"),
    3121: ("pass1_timeout", "doi:10.5063/F14F1P2Q"),
    5066: ("pass1_timeout", "doi:10.6085/AA/pisco_recruitment.147.1"),
    10775: ("pass1_timeout", "sha256:0ebe4a5a39e831ae74baa1f2520a73ef2f45f5ab382ded1b5c09fca4761221c1"),
    63575: ("pass1_timeout", "sha256:e5916e3e6a940796f6a6de118a7bf5167c804c7e0da46f84912a232beaad42df"),
    19176: ("pass1_timeout", "sha256:30ccf7c7b8e669f9abc1f6952582d1c74c5fdb92c0c2907563b9aac1e7ec87f3"),
    69126: ("pass1_timeout", "sha256:fc1f818e459f9d6020cc96f7d592ffd4824440a1dffe1fd85231373b86ca6b3f"),
    4960: ("pass2_context_full", "doi:10.6085/AA/marine_cbs.11.14"),
    4962: ("pass2_context_full", "doi:10.6085/AA/marine_cbs.5.8"),
    4963: ("pass2_context_full", "doi:10.6085/AA/marine_cbs.9.9"),
    2983: ("pass2_context_full", "doi:10.5063/0V8B6X"),
    3196: ("pass2_context_full", "doi:10.5063/F1S75DNC"),
    3202: ("pass2_context_full", "doi:10.5063/F1V69GVP"),
    3145: ("pass2_context_full", "doi:10.5063/F1D50K8H"),
    5311: ("pass2_context_full", "ess-dive-6d8ce27f1a3be98-20240918T231445477"),
}
NUM_CTX = 65536
PARTS = 30
# Context sizes to try for a rerun; the model was trained to 262,144 (n_ctx_train in the logs).
CONTEXTS = [65536, 131072, 196608, 262144]
# ask_ollama calls a prompt "full" within 16 tokens of num_ctx (ollama_client.ask_ollama).
FULL_MARGIN = 16

GROUP_NAMES = {"no box": "no box", "rules: needs_llm": "needs_llm"}
ALWAYS_GROUP_NAMES = {"west_gt_east": "west > east", "send_to_llm": "multi-box send_to_llm",
                      "sign_check": "sign_check"}
BC_CANDIDATE = re.compile(r"CA \(Canada\)[;,] province or state 02 \(British Columbia\)")

ROW_COLUMNS = [
    "number", "record_id", "failure", "repository", "link", "title", "group", "route_reason", "multibox",
    "box_count", "boxes", "box_classes", "box_max_share_in_bc", "any_box_touches_bc", "any_box_inside_bc",
    "any_box_overlaps_bc_at_all", "point_class", "point_count", "points_in_bc", "rule_class", "rule_evidence", "text_mentions_bc",
    "pass1_prompt_chars", "pass1_prompt_tokens", "pass1_prompt_tokens_from", "pass1_timeout_s",
    "pass1_calls_timed_out", "pass1_tokens_written_per_call", "pass1_tokens_in_context_at_cutoff",
    "pass1_output_kept", "pass1_place_count", "pass1_places", "pass2_places_shown",
    "pass2_candidates_in_prompt", "pass2_bc_candidates_in_prompt", "geonames_places_found_total",
    "pass2_prompt_chars", "pass2_record_text_chars", "pass2_place_list_chars", "pass2_prompt_matches_saved",
    "pass2_prompt_matches_fingerprint", "pass2_prompt_tokens_measured", "pass2_prompt_tokens_estimated",
    "num_ctx_needed", "bc_geonames_matches", "harvest_title", "harvest_abstract", "harvest_keywords",
    "harvest_namedLocation", "harvest_placeKey", "harvest_site",
]


def stop(message):
    """Stop the script with a message that starts with STOPPED."""
    sys.exit(f"STOPPED: {message}")


def as_json(value):
    """Return a harvest value as text for the CSV: lists as JSON, a missing value as empty, text as it is."""
    if value is None or (isinstance(value, float) and value != value):
        return ""
    if isinstance(value, str):
        return value
    if hasattr(value, "tolist"):
        value = value.tolist()
    return json.dumps(value, ensure_ascii=False)


# --- 1. The failure files and the merged files --------------------------------

FAILURE_LINE = re.compile(r"^(\S+)\s+(pass1|pass2)\s+\[(\d+)\]\s+(\S+)\s+attempts (\d+)\s+(.*)$")


def read_failures(run):
    """
    Return every line of the run's failure files as dicts with time, stage,
    number, record_id, attempts, message and the part file it came from.
    """
    lines = []
    for path in sorted(glob.glob(os.path.join(run, "logs", "failures_part*.txt"))):
        with open(path, encoding="utf-8") as handle:
            for text in handle:
                match = FAILURE_LINE.match(text.rstrip("\n"))
                if not match:
                    stop(f"{path}: a line I cannot read: {text!r}")
                time, stage, number, record_id, attempts, message = match.groups()
                lines.append({"time": time, "stage": stage, "number": int(number), "record_id": record_id,
                              "attempts": int(attempts), "message": message, "file": os.path.basename(path)})
    return lines


def failure_kind(line):
    """Return what a failure line says: pass1_timeout, pass2_context_full, pass2_no_pass1, or other."""
    if line["stage"] == "pass1" and "timed out" in line["message"]:
        return "pass1_timeout"
    if line["stage"] == "pass2" and "filled the whole context" in line["message"]:
        return "pass2_context_full"
    if line["stage"] == "pass2" and line["message"] == NO_PASS1:
        return "pass2_no_pass1"
    return "other"


def check_failures(lines):
    """
    Compare the failure files with EXPECTED and print what differs. Return
    the real failure line (not the "pass 2 not asked" one) of each record.
    """
    print(f"failure files: {len(lines)} lines in {len({l['file'] for l in lines})} files")
    real, differences = {}, []
    for line in lines:
        kind = failure_kind(line)
        expected = EXPECTED.get(line["number"])
        if expected is None:
            differences.append(f"[{line['number']}] {line['record_id']} is not in the expected 16 ({kind})")
            continue
        if line["record_id"] != expected[1]:
            differences.append(f"[{line['number']}] has id {line['record_id']}, expected {expected[1]}")
        if kind == "pass2_no_pass1":
            if expected[0] != "pass1_timeout":
                differences.append(f"[{line['number']}] has a 'pass 1 has no answer' line but its pass 1 did not fail")
            continue
        if kind != expected[0]:
            differences.append(f"[{line['number']}] failed as {kind}, expected {expected[0]}")
        if line["number"] in real:
            differences.append(f"[{line['number']}] has more than one {kind} line")
        # The record's part is its number modulo the parts (geo_filter_run.part_numbers).
        if line["file"] != f"failures_part{line['number'] % PARTS}.txt":
            differences.append(f"[{line['number']}] is in {line['file']}, not its part's file")
        real[line["number"]] = line
    for number in sorted(set(EXPECTED) - set(real)):
        differences.append(f"[{number}] is expected but has no failure line")
    no_pass1 = {l["number"] for l in lines if failure_kind(l) == "pass2_no_pass1"}
    timeouts = {n for n, (kind, _) in EXPECTED.items() if kind == "pass1_timeout"}
    if no_pass1 != timeouts:
        differences.append(f"'pass 2 not asked' lines for {sorted(no_pass1)}, expected {sorted(timeouts)}")
    counts = pd.Series([failure_kind(l) for l in lines]).value_counts().to_dict()
    print(f"  kinds of line: {counts}")
    print("  differences from the 16 listed: " + ("; ".join(differences) if differences else "none"))
    return real


def check_merged(run, numbers):
    """
    Check the merged files: one line per record in order, every record but
    the 16 with an answer from both passes, the 16 without (pass 1 for the
    timeouts, pass 2 for all), and each line the same as the latest line of
    the progress files. Print the result and return both merged files by number.
    """
    merged = {}
    for stage in ("pass1", "pass2"):
        lines = list(read_jsonl(os.path.join(run, stage, "answers.jsonl")))
        if [l["number"] for l in lines] != numbers:
            stop(f"{stage}/answers.jsonl does not hold every record once, in order")
        merged[stage] = {l["number"]: l for l in lines}
    differences = []
    for number in numbers:
        has1, has2 = "answer" in merged["pass1"][number], "answer" in merged["pass2"][number]
        kind = EXPECTED.get(number, (None,))[0]
        want1, want2 = kind != "pass1_timeout", kind is None
        if (has1, has2) != (want1, want2):
            differences.append(f"[{number}] pass 1 answer {has1}, pass 2 answer {has2}")
    for stage, stem in (("pass1", "progress"), ("pass2", "progress")):
        latest = latest_results(os.path.join(run, stage), stem)
        if set(latest) != set(numbers):
            differences.append(f"{stage} progress files cover {len(latest)} records, not {len(numbers)}")
        changed = [n for n in numbers if latest.get(n) != merged[stage][n]]
        if changed:
            differences.append(f"{stage}: {len(changed)} merged lines differ from the latest progress line, "
                               f"for example {changed[:5]}")
    answered1 = sum("answer" in l for l in merged["pass1"].values())
    answered2 = sum("answer" in l for l in merged["pass2"].values())
    print(f"merged files: {len(numbers)} records; pass 1 answered {answered1}, pass 2 answered {answered2}")
    print("  every other record has both answers, and the merged files match the progress files: "
          + ("yes" if not differences else "NO: " + "; ".join(differences[:10])))
    return merged


# --- The Ollama server logs ------------------------------------------------------

LOG_TIME = re.compile(r"^time=(\S+?)([+-]\d\d:\d\d) ")
GIN = re.compile(r"^\[GIN\] (\d{4}/\d\d/\d\d - \d\d:\d\d:\d\d) \| (\d+) \|\s*(\S+) \|.*POST\s+\"/api/chat\"")
NEW_PROMPT = re.compile(r"\| task (\d+) \| new prompt, n_ctx_slot = (\d+), .*task\.n_tokens = (\d+)")
N_GEN = re.compile(r"\| task (\d+) \| n_gen =\s*(\d+), tg =\s*([\d.]+) t/s")
PROMPT_EVAL = re.compile(r"\| task (\d+) \| prompt eval time =\s*([\d.]+) ms /\s*(\d+) tokens")
EVAL = re.compile(r"\| task (\d+) \|        eval time =\s*([\d.]+) ms /\s*(\d+) tokens")
RELEASE = re.compile(r"\| task (\d+) \| stop processing: n_tokens = (\d+), truncated = (\d+)")
TRUNCATE = re.compile(r"truncating input prompt\" limit=(\d+) prompt=(\d+) keep=(\d+) new=(\d+)")


def log_part(path):
    """Return the part number of a server log, from its name (ollama_<job>_<part>.log)."""
    return int(os.path.basename(path).rsplit("_", 1)[1].split(".")[0])


def read_requests(path):
    """
    Return every /api/chat request in one Ollama server log, in order, as
    dicts: when it ended (UTC, from the [GIN] line), the HTTP status and
    duration, and what llama-server logged for its task: the prompt's
    tokens (task.n_tokens), the prompt's tokens before Ollama cut it (from a
    "truncating input prompt" line just before), the prompt reading time,
    the tokens written (the last n_gen line, or the eval line), and the
    tokens in the context when the task stopped. A request cut off by the
    client (status 500 after the time limit) is logged before its task stops,
    so the stop line is added to it afterwards.
    """
    offset, requests, by_task = None, [], {}
    task, pending_cut = None, None
    with open(path, encoding="utf-8", errors="replace") as handle:
        for text in handle:
            if offset is None:
                found = LOG_TIME.match(text)
                if found:
                    sign = 1 if found.group(2)[0] == "+" else -1
                    hours, minutes = map(int, found.group(2)[1:].split(":"))
                    offset = timezone(sign * timedelta(hours=hours, minutes=minutes))
            if "truncating input prompt" in text:
                found = TRUNCATE.search(text)
                pending_cut = {"limit": int(found.group(1)), "prompt": int(found.group(2))}
                continue
            found = NEW_PROMPT.search(text)
            if found:
                task = {"task": int(found.group(1)), "n_ctx": int(found.group(2)),
                        "prompt_tokens": int(found.group(3)), "cut_from": None, "written": 0,
                        "rate_last": None, "prompt_eval_s": None, "context_at_stop": None, "truncated": None}
                if pending_cut:
                    task["cut_from"], pending_cut = pending_cut["prompt"], None
                by_task[task["task"]] = task
                continue
            for pattern in (N_GEN, PROMPT_EVAL, EVAL, RELEASE):
                found = pattern.search(text)
                if not found:
                    continue
                item = by_task.get(int(found.group(1)))
                if item is None:
                    break
                if pattern is N_GEN:
                    item["written"], item["rate_last"] = int(found.group(2)), float(found.group(3))
                elif pattern is PROMPT_EVAL:
                    item["prompt_eval_s"] = float(found.group(2)) / 1000
                elif pattern is EVAL:
                    item["written"] = int(found.group(3))
                else:
                    item["context_at_stop"], item["truncated"] = int(found.group(2)), int(found.group(3))
                break
            found = GIN.match(text)
            if found:
                local = datetime.strptime(found.group(1), "%Y/%m/%d - %H:%M:%S").replace(tzinfo=offset)
                request = {"ended": local.astimezone(timezone.utc), "status": int(found.group(2)),
                           "duration": found.group(3), "part": log_part(path), "log": os.path.basename(path)}
                # The task stays the same object, so a stop line logged later still reaches it.
                request["task_info"] = task
                requests.append(request)
                task = None
    return requests


def task_field(request, name):
    """Return one field of a request's task, or None if no task was logged for it."""
    return (request["task_info"] or {}).get(name)


def utc(text):
    """Return a failure line's time (2026-11-24T00:41:34Z) as a UTC datetime."""
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def timeout_calls(requests, failure):
    """
    Return the cut-off calls (status 500) of a pass 1 timeout: those in its
    part's log that ended up to 4 time limits before the failure was written.
    ask_ollama tries a timed-out call once more and ask_with_retry asks the
    prompt once more, so there should be 4.
    """
    end = utc(failure["time"])
    window = timedelta(seconds=4 * OLLAMA_TIMEOUT + 60)
    return [r for r in requests if r["part"] == failure["number"] % PARTS and r["status"] == 500
            and end - window <= r["ended"] <= end + timedelta(seconds=5)]


def cut_call(requests, failure):
    """Return the request of a pass 2 context_full failure: the cut request in its part's log that ended closest to it."""
    end = utc(failure["time"])
    cut = [r for r in requests if r["part"] == failure["number"] % PARTS and task_field(r, "cut_from")
           and abs((r["ended"] - end).total_seconds()) <= 10]
    if len(cut) != 1:
        stop(f"[{failure['number']}]: {len(cut)} cut requests in its part's log near {failure['time']}")
    return cut[0]


# --- 2. The records, their groups and boxes -------------------------------------------

def read_records(numbers):
    """Return the input folder's records (as the prompts read them) for the given numbers, with their sha1s."""
    found = {}
    with gzip.open(os.path.join(INPUT, "records.jsonl.gz"), "rt", encoding="utf-8") as handle:
        for line in handle:
            item = json.loads(line)
            if item["number"] in numbers:
                found[item["number"]] = item
    return found


def group_of(rules_row):
    """Return the filter 3 group named the way Lucia names them, from a filter3_rules.parquet row."""
    if rules_row["group"] in ALWAYS_GROUP_NAMES:
        return ALWAYS_GROUP_NAMES[rules_row["group"]]
    return GROUP_NAMES.get(rules_row["route_reason"], rules_row["route_reason"])


def box_facts(record, rules, rules_row):
    """
    Measure each box of a record against BC with the rule code, and return
    the CSV fields: the boxes, how many boxes are in each class, the largest
    share of a box in BC, whether any
    box touches BC (a class other than outside: 2% or more of the box in BC,
    or 2% or more of BC covered, the rules' TOUCH), lies inside it (90% or
    more), or overlaps it at all (any share above 0, which the rules count
    as outside below 2%), and the points: a box with no area is a point
    (bc_filter_rules.points_of), counted in and outside BC as
    classify_points tests them. classify_record is run again as
    build_filter3_rules.rule_results runs it, and must give the stored
    rule class and box class.
    """
    points = points_of(None, record["boxes"])
    points_in_bc = sum(rules.shape.outline.covers(Point(lon, lat)) for lon, lat in points)
    boxes = record["boxes"]
    classes, shares, any_overlap = [], [], False
    for box in boxes:
        kind, _ = classify_box(box, rules.shape)
        measured = measure_box(box, rules.shape)
        classes.append(kind or "no area")
        shares.append(None if measured is None else round(measured[0], 4))
        any_overlap = any_overlap or (measured is not None and (measured[0] > 0 or measured[1] > 0))
    lunaris_form = {"id": record["id"], "title": record["title"], "subjects": record["subjects"],
                    "abstract": record["abstract"], "places": record["named_location"], "boxes": boxes,
                    "points": None}
    again = classify_record(lunaris_form, rules)
    if again["bc_class"] != rules_row["rule_class"] or (again["box_class"] or "") != rules_row["box_class"]:
        stop(f"{record['id']}: the rules now give {again['bc_class']}/{again['box_class']}, the table has "
             f"{rules_row['rule_class']}/{rules_row['box_class']}")
    known = [s for s in shares if s is not None]
    return {"box_count": len(boxes), "boxes": json.dumps(boxes),
            "box_classes": ", ".join(f"{kind} {count}" for kind, count in Counter(classes).most_common()),
            "box_max_share_in_bc": max(known) if known else "",
            "any_box_touches_bc": any(k not in ("outside", "no area") for k in classes),
            "any_box_inside_bc": any(k == "inside" for k in classes),
            "any_box_overlaps_bc_at_all": any_overlap,
            "point_class": classify_points(None, boxes, rules.shape) or "",
            "point_count": len(points), "points_in_bc": points_in_bc,
            "rule_class": again["bc_class"], "rule_evidence": again["evidence"]}


# --- 3. Pass 1 ----------------------------------------------------------------------

def read_prompt(stage, number):
    """Return a saved prompt of the run (pass1 or pass2), or None if there is none."""
    path = os.path.join(RUN, stage, "prompts", prompt_name(number))
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def pass1_facts(number, item, merged, requests, failure):
    """
    Return the CSV fields about pass 1: the prompt's length, its tokens (from
    the answer, or for a timeout from the server log), and for a timeout the
    time limit, the calls cut off, the tokens written by each and the
    tokens in the context when each was cut off.
    """
    text = read_prompt("pass1", number)
    if prompt_sha1(text) != item["pass1_sha1"]:
        stop(f"[{number}]: the saved pass 1 prompt is not the one the input folder's fingerprint names")
    facts = {"pass1_prompt_chars": len(text), "pass1_timeout_s": "", "pass1_calls_timed_out": "",
             "pass1_tokens_written_per_call": "", "pass1_tokens_in_context_at_cutoff": "", "pass1_output_kept": ""}
    line = merged["pass1"][number]
    if "answer" in line:
        facts.update(pass1_prompt_tokens=line["input_tokens"], pass1_prompt_tokens_from="the answer (Ollama's count)")
        return facts
    calls = timeout_calls(requests, failure)
    tokens = sorted({task_field(c, "prompt_tokens") for c in calls})
    facts.update(pass1_prompt_tokens=", ".join(str(t) for t in tokens),
                 pass1_prompt_tokens_from="Ollama log (task.n_tokens)",
                 pass1_timeout_s=OLLAMA_TIMEOUT, pass1_calls_timed_out=len(calls),
                 pass1_tokens_written_per_call=", ".join(str(task_field(c, "written")) for c in calls),
                 pass1_tokens_in_context_at_cutoff=", ".join(str(task_field(c, "context_at_stop")) for c in calls),
                 pass1_output_kept="no: stream off, nothing returned, the log does not hold the text")
    return facts


# --- 4. Pass 2 ----------------------------------------------------------------------

def place_list_facts(prompt):
    """
    Read the Place list part of a pass 2 prompt and return how many places
    it shows, how many GeoNames candidates (all, and in BC), and the BC
    candidates as text, each with its place.
    """
    start = prompt.index("\nPlace 1:") if "\nPlace 1:" in prompt else len(prompt)
    places = candidates = 0
    bc, place = [], ""
    for line in prompt[start:].split("\n"):
        if line.startswith("Place "):
            places += 1
            place = line.split(";")[0]
        # Candidate lines start with two spaces (geo_pass2_places.candidate_lines).
        elif re.match(r"^  Candidate \d+:", line):
            candidates += 1
            if BC_CANDIDATE.search(line):
                name = re.search(r"main name \"([^\"]*)\"|not from GeoNames: \"([^\"]*)\"", line)
                gid = re.search(r"GeoNames id (\d+)", line)
                code = re.search(r"feature code (\S+)", line)
                bc.append(f"{place} -> {name.group(1) or name.group(2)}"
                          + (f" (GeoNames {gid.group(1)}, {code.group(1)})" if gid else " (added by us)"))
    return places, candidates, bc


def chars_per_token(merged):
    """
    Return the median characters per token of the run's long pass 2 prompts
    that were answered (16,000 tokens or more, Ollama's own count), and how
    many there were: the rate used to estimate a prompt's tokens.
    """
    rates = []
    for number, line in merged["pass2"].items():
        if "answer" in line and line["input_tokens"] >= 16000:
            rates.append(len(read_prompt("pass2", number)) / line["input_tokens"])
    return median(rates), len(rates)


def needed_context(tokens, answer_room):
    """Return the smallest of CONTEXTS that holds the prompt and the answer room, or "more than 262144"."""
    for size in CONTEXTS:
        if tokens + answer_room <= size - FULL_MARGIN:
            return size
    return f"more than {CONTEXTS[-1]}"


def pass2_facts(number, item, merged, lookups, shape, requests, failure, rate, answer_room):
    """
    Return the CSV fields about pass 2 for a context_full record: pass 1's
    places, the prompt rebuilt in the dataone variant and checked against the
    saved prompt and the fingerprint saved with the result, the Place list
    counts, the lengths, the tokens measured (the log) and estimated (from
    characters), and the NUM_CTX it needs.
    """
    places = pass1_places(merged["pass1"][number])
    rebuilt = build_pass2(item["record"], shape, places, lookups[number]["results"], VARIANT)
    saved = read_prompt("pass2", number)
    shown, candidates, bc = place_list_facts(saved)
    record_end = saved.index("\nPlace list") if "\nPlace list" in saved else saved.index("\nPlace 1:")
    measured = task_field(cut_call(requests, failure), "cut_from")
    found_total = sum(r["summary"]["places_found"] for r in lookups[number]["results"]
                      if r.get("summary") and r["summary"].get("places_found"))
    return {"pass1_place_count": len(places),
            "pass1_places": " | ".join(f"{p['as_written']} -> {p['name']} [{p['role']}]" for p in places),
            "pass2_places_shown": shown, "pass2_candidates_in_prompt": candidates,
            "pass2_bc_candidates_in_prompt": len(bc), "geonames_places_found_total": found_total,
            "pass2_prompt_chars": len(saved), "pass2_record_text_chars": record_end,
            "pass2_place_list_chars": len(saved) - record_end,
            "pass2_prompt_matches_saved": rebuilt == saved,
            "pass2_prompt_matches_fingerprint": prompt_sha1(rebuilt) == merged["pass2"][number].get("prompt_sha1"),
            "pass2_prompt_tokens_measured": measured,
            "pass2_prompt_tokens_estimated": round(len(saved) / rate),
            "num_ctx_needed": needed_context(measured, answer_room),
            "bc_geonames_matches": " | ".join(bc)}


# --- 5 and 6. GPU memory and prompt speed ----------------------------------------------

BUFFERS = [
    ("model weights", re.compile(r"CUDA0 model buffer size =\s*([\d.]+) MiB")),
    ("KV cache", re.compile(r"llama_kv_cache:\s+CUDA0 KV buffer size =\s*([\d.]+) MiB")),
    ("recurrent state", re.compile(r"CUDA0 RS buffer size =\s*([\d.]+) MiB")),
    ("compute", re.compile(r"sched_reserve:\s+CUDA0 compute buffer size =\s*([\d.]+) MiB")),
    ("vision model", re.compile(r"load_hparams: model size:\s*([\d.]+) MiB")),
    ("vision compute", re.compile(r"reserve_compute_meta:\s+CUDA0 compute buffer size =\s*([\d.]+) MiB")),
]


def memory_of(path):
    """
    Return what one server log says about GPU memory: n_ctx, the slice, its
    free memory at the fit step, the fit step's projection, and every GPU
    buffer in the order logged (KV cache and compute appear twice: the main
    model, then the speculative draft; compute a third time when the draft
    is set up again).
    """
    buffers, about = [], {}
    with open(path, encoding="utf-8", errors="replace") as handle:
        for number, text in enumerate(handle):
            if number > 2000:
                break
            if "llama_context: n_ctx  " in text and "n_ctx" not in about:
                about["n_ctx"] = int(text.split("=")[1])
            if "projected to use" in text:
                about["projected"], about["free"] = map(int, re.findall(r"(\d+) MiB", text)[:2])
            if "Device 0:" in text and "slice" not in about:
                about["slice"] = text.split("NVIDIA ")[1].split(",")[0]
            if "flash_attn  " in text and "flash_attn" not in about:
                about["flash_attn"] = text.split("=")[1].strip()
            for name, pattern in BUFFERS:
                found = pattern.search(text)
                if found:
                    buffers.append((name, float(found.group(1))))
    about["buffers"] = buffers
    return about


def memory_table(small, large):
    """
    Print the GPU buffers at the two measured contexts and a straight line
    through them for larger contexts, and return the totals by context.
    Counted once each: the draft's compute buffer is logged twice (made, then
    set up again for speculative decoding); whether that is one buffer or two
    is not in the log, so both totals are given.
    """
    if [n for n, _ in small["buffers"]] != [n for n, _ in large["buffers"]]:
        stop("the two logs list different GPU buffers")
    names = [n for n, _ in small["buffers"]]
    seen, rows = {}, []
    for index, name in enumerate(names):
        seen[name] = seen.get(name, 0) + 1
        label = name + ("" if seen[name] == 1 else " (draft)" if seen[name] == 2 else " (draft, set up again)")
        rows.append((label, small["buffers"][index][1], large["buffers"][index][1]))
    print(f"\nGPU buffers in MiB, {large['slice']}, flash attention {large['flash_attn']} (measured, from the logs):")
    print(f"  {'buffer':34} {small['n_ctx']:>8} {large['n_ctx']:>8}")
    for label, a, b in rows:
        print(f"  {label:34} {a:8.0f} {b:8.0f}")
    once = [r for r in rows if "set up again" not in r[0]]
    totals = {}
    for which, chosen in (("once", once), ("twice", rows)):
        a, b = sum(r[1] for r in chosen), sum(r[2] for r in chosen)
        slope = (b - a) / (large["n_ctx"] - small["n_ctx"])
        totals[which] = {size: a + slope * (size - small["n_ctx"]) for size in [small["n_ctx"]] + CONTEXTS}
        print(f"  total, draft compute counted {which}: {a:.0f} and {b:.0f} MiB measured; "
              f"{slope * 1024:.1f} MiB more per 1,024 tokens of context")
    print(f"  free on the slice when the model loaded: {large['free']} MiB (of {large['slice']}); the fit step "
          f"projected {small['projected']} and {large['projected']} MiB (without the vision part)")
    print("  guessed totals (straight line through the two measured contexts):")
    for size in CONTEXTS:
        once_total, twice_total = totals["once"][size], totals["twice"][size]
        print(f"    NUM_CTX {size:>6}: {once_total:7.0f} MiB (draft compute once), {twice_total:7.0f} MiB (twice); "
              f"left on the slice {large['free'] - once_total:7.0f} / {large['free'] - twice_total:7.0f} MiB; "
              f"on a whole H100 (80 GB, about 81,000 MiB free) left about {81000 - twice_total:.0f} MiB")
    return totals


def prompt_speed(requests):
    """
    Print how long the server took to read long prompts (prompt eval time,
    the tokens it read), measured, and a guess for longer ones: time per
    token grows with the prompt when flash attention is off, so a line
    a + b * tokens is fitted to seconds per token.
    """
    points = sorted((task_field(r, "prompt_tokens"), task_field(r, "prompt_eval_s")) for r in requests
                    if task_field(r, "prompt_eval_s") and task_field(r, "prompt_tokens") >= 20000)
    print(f"\nprompt reading on the slice, prompts of 20,000 tokens or more (measured, {len(points)} requests; "
          f"only the part not already cached is read):")
    for tokens, seconds in points[-12:]:
        print(f"  {tokens:6d} tokens: {seconds:6.1f} s")
    if len(points) < 3:
        return
    xs = [t for t, _ in points]
    ys = [s / t for t, s in points]
    mean_x, mean_y = sum(xs) / len(xs), sum(ys) / len(ys)
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / sum((x - mean_x) ** 2 for x in xs)
    start = mean_y - slope * mean_x
    print("  guess for longer prompts (a line through seconds per token, on the slice):")
    for tokens in (65641, 71067, 131072, 176864):
        print(f"    {tokens:6d} tokens: about {tokens * (start + slope * tokens):5.0f} s")


# --- Main ---------------------------------------------------------------------------

def main():
    """Check the failures, find the facts of each record, write the CSV and print the summaries."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("--out", default=OUT_DIR, help="folder for failed16.csv")
    args = parser.parse_args()

    sample, records = read_input(INPUT)
    numbers = list(sample.index)
    with open(os.path.join(RUN, "run.json"), encoding="utf-8") as handle:
        about = json.load(handle)
    if about["num_ctx"] != NUM_CTX or about["geo_prompt_variant"] != VARIANT or len(numbers) != 70521:
        stop(f"the run is not the one expected: {about}")
    print(f"run {RUN}\n  {len(numbers)} records, model {about['model']}, NUM_CTX {about['num_ctx']}, "
          f"variant {about['geo_prompt_variant']}\n")

    real = check_failures(read_failures(RUN))
    merged = check_merged(RUN, numbers)

    requests = []
    for path in sorted(glob.glob(os.path.join(RUN, "logs", "ollama_*.log"))):
        requests += read_requests(path)
    cut_off = [r for r in requests if r["status"] == 500]
    cut = [r for r in requests if task_field(r, "cut_from")]
    print(f"\nserver logs: {len(requests)} /api/chat requests; {len(cut_off)} cut off by the time limit "
          f"(status 500); {len(cut)} prompts cut by Ollama (\"truncating input prompt\")")
    print(f"  time limit per call: {OLLAMA_TIMEOUT} s (ollama_client.OLLAMA_TIMEOUT, geo_filter_run.py --timeout "
          f"default; fir/geo_filter.sbatch does not pass --timeout)")

    rules = load_rules(BC_BOUNDARY)
    table = pd.read_parquet(RULES).set_index("id")
    items = read_records(set(EXPECTED))
    ids = [EXPECTED[n][1] for n in EXPECTED]
    harvest = read_fields(ids)
    lookups = latest_results(os.path.join(RUN, "pass1"), "lookup_progress")
    rate, rate_count = chars_per_token(merged)
    answered2 = [l for l in merged["pass2"].values() if "answer" in l]
    answer_room = max(l["output_tokens"] for l in answered2)
    print(f"  estimate rate: {rate:.2f} characters per token (median of {rate_count} answered pass 2 prompts of "
          f"16,000 tokens or more); answer room {answer_room} tokens (the longest pass 2 answer of the run)")

    rows = []
    for number in sorted(EXPECTED, key=lambda n: (EXPECTED[n][0], n)):
        kind, record_id = EXPECTED[number]
        item = items[number]
        record, rules_row, raw = item["record"], table.loc[record_id], harvest.loc[record_id]
        row = {"number": number, "record_id": record_id, "failure": kind, "repository": record["repository"],
               "link": VIEW + quote(record_id, safe=""), "title": record["title"], "group": group_of(rules_row),
               "route_reason": rules_row["route_reason"], "multibox": bool(rules_row["multibox"]),
               "text_mentions_bc": sample.loc[number, "text_mentions_bc"]}
        row.update(box_facts(record, rules, rules_row))
        row.update(pass1_facts(number, item, merged, requests, real[number]))
        if kind == "pass2_context_full":
            row.update(pass2_facts(number, item, merged, lookups, rules.shape, requests, real[number], rate,
                                   answer_room))
        for name in ("title", "abstract", "keywords", "namedLocation", "placeKey", "site"):
            row["harvest_" + name] = as_json(raw[name])
        rows.append(row)
    table_out = pd.DataFrame(rows).reindex(columns=ROW_COLUMNS)
    # Counts that only some rows have stay whole numbers, not 264.0.
    for column in ("pass1_place_count", "pass2_places_shown", "pass2_candidates_in_prompt",
                   "pass2_bc_candidates_in_prompt", "geonames_places_found_total", "pass2_prompt_chars",
                   "pass2_record_text_chars", "pass2_place_list_chars", "pass2_prompt_tokens_measured",
                   "pass2_prompt_tokens_estimated"):
        table_out[column] = table_out[column].astype("Int64")
    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, "failed16.csv")
    table_out.to_csv(path, index=False, encoding="utf-8-sig")

    pd.set_option("display.width", 250)
    pd.set_option("display.max_colwidth", 60)
    print(f"\nwrote {path} ({len(table_out)} rows)\n")
    print(table_out[["number", "failure", "repository", "group", "box_classes", "any_box_touches_bc", "point_count",
                     "points_in_bc",
                     "pass1_prompt_chars", "pass1_prompt_tokens", "pass1_tokens_written_per_call"]].to_string(index=False))
    two = table_out[table_out["failure"] == "pass2_context_full"]
    print()
    print(two[["number", "pass1_place_count", "pass2_candidates_in_prompt", "pass2_bc_candidates_in_prompt",
               "pass2_prompt_chars", "pass2_record_text_chars", "pass2_place_list_chars",
               "pass2_prompt_matches_saved", "pass2_prompt_matches_fingerprint", "pass2_prompt_tokens_measured",
               "pass2_prompt_tokens_estimated", "num_ctx_needed"]].to_string(index=False))
    print("\npass 1 timeouts, each call (measured, server logs):")
    for number, line in real.items():
        if EXPECTED[number][0] != "pass1_timeout":
            continue
        for call in timeout_calls(requests, line):
            print(f"  [{number}] {call['log']} ended {call['ended']:%H:%M:%SZ} status {call['status']} after "
                  f"{call['duration']}: prompt {task_field(call, 'prompt_tokens')} tokens, wrote "
                  f"{task_field(call, 'written')} tokens (last speed {task_field(call, 'rate_last')} t/s), "
                  f"{task_field(call, 'context_at_stop')} tokens in the context when stopped")

    small = memory_of(sorted(glob.glob(os.path.join(LABEL_TEST_LOGS, "ollama_*.log")))[0])
    large = memory_of(sorted(glob.glob(os.path.join(RUN, "logs", "ollama_*.log")))[0])
    if small["n_ctx"] >= large["n_ctx"] or small["slice"] != large["slice"] or small["flash_attn"] != large["flash_attn"]:
        stop(f"the two memory logs do not compare: {small['n_ctx']}/{small['slice']}, {large['n_ctx']}/{large['slice']}")
    memory_table(small, large)
    prompt_speed(requests)
    return 0


if __name__ == "__main__":
    sys.exit(main())
