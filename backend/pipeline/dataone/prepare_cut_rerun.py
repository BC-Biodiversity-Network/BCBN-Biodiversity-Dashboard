"""
prepare_cut_rerun.py

Make the run folders that ask again, with a larger context, the DataONE
records whose prompts Ollama cut silently in the full filter 2 runs of
2026-11-21 (approved by Lucia on 2026-11-23).

Ollama 0.40 cuts a prompt longer than the context to num_ctx // 2 + 2
tokens (8,194 at 16,384) and the model then answers without the
instructions. run_batch.py now reports such an answer as context_full
(run_batch.cut_by_ollama), but the full runs were made before that, so
these answers sit in their results/ as valid and a resubmit would not ask
them again. Hence new run folders holding only these records.

Which records: build_filter2_union.py's cut_<model> column (the full
run's prompt was cut) is true for 259 records in the 4b run and 260 in the
35b run. The
one more in the 35b run (sha256:15ea1dbb...) failed in the 4b run with
bad_json at 16,384, so it has no 4b answer to judge; its prompt was cut in
the 35b run, so it is longer than 16,384 tokens there and very likely for
the 4b too (the other 259 were cut in both). It is asked again in both
folders, so both hold the same 260 records. The script
checks this against the run folders themselves too: every results line
with cut_by_ollama true, in each run.

How the folders are made:
  4b   dataone_cut_rerun_2026_11_23: the 260 lines of the full run's input
       batches (data/dataone/filter2_input/dataone_harvest_2026_11_18,
       the folder that was copied to Fir) copied byte for byte, in the
       full run's order, into one batch, inputs/batch_0000.jsonl.gz. Each
       line keeps its full-run position (nothing reads it when running or
       merging; it ties the record back to the full run). run.json is the
       full run's, with records, batches, created and the long prompt count
       set for these records and a rerun_of note.
  35b  dataone_cut_rerun_2026_11_23_qwen3.6-35b-a3b_nofa: made from the 4b
       folder by new_model_run.make_run, exactly as new_model_run.py makes
       a run for another model (inputs copied byte for byte, model and
       label changed, made_from added).
Before finishing, both folders are read back: the same 260 ids, every line
byte for byte equal to its line in the full run, every prompt rebuilt with
the run's variant (v6_dataone) matching its fingerprint, and the two
folders' batch files identical.

Outputs (data/dataone/filter2_input/, ignored by git). The script stops if
a folder is already there, unless --replace is given and the folder has
never been run (prepare_filter2_input.remove_unrun).

Run, from backend/:
    ../.venv/bin/python pipeline/dataone/prepare_cut_rerun.py
"""

import argparse
import gzip
import hashlib
import json
import os
import sys
from pathlib import Path

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, "..", ".."))
LLM = os.path.join(BACKEND, "pipeline", "lunaris", "llm")
sys.path.insert(0, HERE)
sys.path.insert(0, LLM)
from build_filter2_union import OUT_UNION, RUN_DIRS, last_error_kinds  # noqa: E402
from make_batches import LONG_PROMPT_CHARS  # noqa: E402
from new_model_run import make_run, sha1_of  # noqa: E402
from prepare_filter2_input import FULL_RUN, OUT_DIR, remove_unrun  # noqa: E402
from prompt_v6 import prompt_sha1  # noqa: E402
from prompt_variants import build_prompt_for, instructions_sha1, variant_of_run  # noqa: E402
from run_batch import cut_by_ollama  # noqa: E402
from run_files import batch_numbers, inputs_path, load_run, now_utc, read_jsonl, results_path  # noqa: E402

RERUN_4B = os.path.join(OUT_DIR, "dataone_cut_rerun_2026_11_23")
RERUN_35B = os.path.join(OUT_DIR, "dataone_cut_rerun_2026_11_23_qwen3.6-35b-a3b_nofa")
MODEL_35B, LABEL_35B = "qwen3.6:35b-a3b", "qwen3.6:35b-a3b-fir-nofa"
PROMPT_VARIANT = "v6_dataone"
EXPECTED = {"4b": 259, "35b": 260}
EXPECTED_RERUN = 260
SECOND_PASS_NUM_CTX = 65_536


def stop(message):
    """Stop the script with a message that starts with STOPPED."""
    sys.exit(f"STOPPED: {message}")


def cut_ids_from_union():
    """Return {model: set of ids} whose full run prompt was cut (cut_<model> true) in filter2_union.parquet."""
    union = pd.read_parquet(OUT_UNION, columns=["id", "cut_4b", "cut_35b"])
    return {name: set(union.loc[union[f"cut_{name}"], "id"]) for name in ("4b", "35b")}


def cut_ids_from_results(run_dir):
    """Return the ids of every results line in a run folder whose prompt Ollama cut (run_batch.cut_by_ollama)."""
    ids = set()
    for number in batch_numbers(run_dir):
        for line in read_jsonl(results_path(run_dir, number)):
            if cut_by_ollama(line.get("input_tokens"), line.get("num_ctx")):
                ids.add(line["record_id"])
    return ids


def choose_ids():
    """
    Return the ids to ask again (the cut ones of either run) after checking
    the union file and the run folders agree, the counts are 259 and 260,
    and the one record only the 35b run shows cut failed with bad_json in
    the 4b full run (its last error line there).
    """
    from_union = cut_ids_from_union()
    for name, run_dir in RUN_DIRS.items():
        from_results = cut_ids_from_results(run_dir)
        if from_results != from_union[name]:
            stop(f"{name}: the run folder's cut answers ({len(from_results)}) are not the union file's "
                 f"prompt_cut ones ({len(from_union[name])})")
        if len(from_union[name]) != EXPECTED[name]:
            stop(f"{name}: {len(from_union[name])} cut answers, not {EXPECTED[name]}")
    only_35b = from_union["35b"] - from_union["4b"]
    if from_union["4b"] - from_union["35b"] or len(only_35b) != 1:
        stop("the 4b cut records are not the 35b ones less exactly one")
    errors_4b = last_error_kinds(RUN_DIRS["4b"])
    if any(errors_4b.get(i) != "bad_json" for i in only_35b):
        stop("the record cut only in the 35b run did not fail with bad_json in the 4b run")
    ids = from_union["4b"] | from_union["35b"]
    if len(ids) != EXPECTED_RERUN:
        stop(f"{len(ids)} records to ask again, not {EXPECTED_RERUN}")
    print(f"records to ask again: {len(ids)} (4b cut {len(from_union['4b'])}, 35b cut {len(from_union['35b'])}; "
          f"{sorted(only_35b)[0][:24]}... is cut in 35b and bad_json in 4b, asked again in both)")
    return ids, sorted(only_35b)[0]


def raw_lines(run_dir, number):
    """Return one input batch's lines as the exact bytes in the file (each with its newline)."""
    with gzip.open(inputs_path(run_dir, number), "rb") as handle:
        return [line for line in handle if line.strip()]


def pick_lines(ids):
    """
    Return the full run's input lines for the given ids, as exact bytes, in
    the full run's order, after checking the input folder on the Mac is the
    one Fir ran (each batch file's sha1 equals the copy that came back).
    """
    for number in batch_numbers(FULL_RUN):
        here, back = inputs_path(FULL_RUN, number), inputs_path(RUN_DIRS["4b"], number)
        if sha1_of(here) != sha1_of(back):
            stop(f"{here.name} on the Mac differs from the copy that came back from Fir")
    picked = []
    for number in batch_numbers(FULL_RUN):
        for line in raw_lines(FULL_RUN, number):
            if json.loads(line)["record_id"] in ids:
                picked.append(line)
    if len(picked) != len(ids):
        stop(f"found {len(picked)} of the {len(ids)} records in the full run's inputs")
    return picked


def write_rerun(lines, ids, only_35b, replace):
    """Write the 4b rerun folder: one batch of the picked lines, and run.json from the full run's. Return run.json."""
    target = Path(RERUN_4B)
    if target.exists():
        if not replace:
            stop(f"{target} already exists; pass --replace to write it again (only if it has never run)")
        remove_unrun(target)
    run = load_run(FULL_RUN)
    if run.get("prompt_variant") != PROMPT_VARIANT or run.get("instructions_sha1") != instructions_sha1(PROMPT_VARIANT):
        stop("the full run is not a v6_dataone run")
    (target / "inputs").mkdir(parents=True)
    for folder in ("results", "errors", "logs"):
        (target / folder).mkdir()
    # mtime=0 keeps the gzip bytes the same from one make to the next, as in make_batches.py.
    with gzip.GzipFile(inputs_path(target, 0), "wb", mtime=0) as raw:
        for line in lines:
            raw.write(line)
    long_prompts = sum(len(build_prompt_for(json.loads(line), PROMPT_VARIANT)) > LONG_PROMPT_CHARS for line in lines)
    run.update({
        "created": now_utc(),
        "records": len(lines),
        "batches": 1,
        "prompts_over_chars": {"limit": LONG_PROMPT_CHARS, "count": long_prompts},
        "rerun_of": {
            "run": os.path.relpath(FULL_RUN, BACKEND),
            "reason": "Ollama cut these prompts to num_ctx // 2 + 2 tokens (8,194 at 16,384) without an error",
            "chosen_from": os.path.relpath(OUT_UNION, BACKEND) + " (cut_4b or cut_35b true)",
            "union_sha1": sha1_of(OUT_UNION),
            "cut_in_4b": EXPECTED["4b"], "cut_in_35b": EXPECTED["35b"],
            "bad_json_in_4b_cut_in_35b": only_35b,
            "num_ctx": SECOND_PASS_NUM_CTX,
            "positions": "each line keeps its position in the full run",
        },
    })
    (target / "run.json").write_text(json.dumps(run, indent=1) + "\n", encoding="utf-8")
    return run


def verify(run_dir, ids, lines):
    """
    Read a rerun folder back and stop unless it holds exactly the given ids,
    each line byte for byte as in the full run, and every prompt rebuilt
    with the run's own variant matches its fingerprint. Returns the batch's sha1.
    """
    run = load_run(run_dir)
    variant = variant_of_run(run)
    if variant != PROMPT_VARIANT or run["instructions_sha1"] != instructions_sha1(variant):
        stop(f"{run_dir} is not a v6_dataone run")
    if batch_numbers(run_dir) != [0] or run["records"] != len(ids) or run["batches"] != 1:
        stop(f"{run_dir} does not hold one batch of {len(ids)} records")
    back = raw_lines(run_dir, 0)
    if back != lines:
        stop(f"{run_dir}: the lines are not byte for byte the full run's")
    seen = [json.loads(line)["record_id"] for line in back]
    if len(set(seen)) != len(seen) or set(seen) != ids:
        stop(f"{run_dir} does not hold exactly the chosen ids, once each")
    for line in back:
        record = json.loads(line)
        if prompt_sha1(build_prompt_for(record, variant)) != record["prompt_sha1"]:
            stop(f"{run_dir}: the prompt of {record['record_id']} does not match its fingerprint")
    return sha1_of(inputs_path(run_dir, 0))


def main():
    """Choose the records, write both rerun folders, read them back and say what they hold."""
    parser = argparse.ArgumentParser(description="Make the DataONE run folders for the cut prompts.")
    parser.add_argument("--replace", action="store_true", help="write the folders again if they never ran")
    args = parser.parse_args()
    ids, only_35b = choose_ids()
    lines = pick_lines(ids)
    if Path(RERUN_35B).exists():
        if not args.replace:
            stop(f"{RERUN_35B} already exists; pass --replace to write it again (only if it has never run)")
        remove_unrun(RERUN_35B)
    run_4b = write_rerun(lines, ids, only_35b, args.replace)
    run_35b = make_run(RERUN_4B, RERUN_35B, MODEL_35B, LABEL_35B)
    sha_4b, sha_35b = verify(RERUN_4B, ids, lines), verify(RERUN_35B, ids, lines)
    if sha_4b != sha_35b:
        stop("the two folders' batch files differ")
    batches = sorted({json.loads(line)["position"] // load_run(FULL_RUN)["batch_size"] for line in lines})
    for run_dir, run in ((RERUN_4B, run_4b), (RERUN_35B, run_35b)):
        print(f"wrote {os.path.relpath(run_dir, BACKEND)}: {run['records']} records in 1 batch, model {run['model']}, "
              f"label {run['label']}, variant {run['prompt_variant']}, instructions {run['instructions_sha1'][:7]}")
    print(f"   batch_0000.jsonl.gz sha1 {sha_4b} in both; prompts over {LONG_PROMPT_CHARS:,} characters: "
          f"{run_4b['prompts_over_chars']['count']}")
    print(f"   from {len(batches)} of the full run's batches: {batches}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
