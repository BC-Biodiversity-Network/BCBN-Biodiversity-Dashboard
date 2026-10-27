"""
geo_pass2_trial.py

Try pass 2 of the geography step on the records of a pass 1 trial, on the
local Ollama, and write what came back. The prompt is version C4
(geo_pass2_prompt_c4.py; the earlier versions and this script's version
that could run them are in
backend/exploration/lunaris/bc_filter_2026_10/old_pass2_prompts/).
The model answers facts; code works out the class (geo_pass2_classes.decide,
which first applies the box rule that may read an "unclear" in_bc as "yes").

It reads the pass 1 trial folder (data/lunaris_bc_filter/pass1_trial_v3):
sample.csv for the records, answers.jsonl for the places pass 1 kept, and
lookup.jsonl for each place's GeoNames candidates (geonames_lookup.py
--pass1 writes it, one line per place in the same order). The records
themselves come from the harvest, and the record text is built exactly as
pass 1 built it. Each box's share in BC is measured as the note on the box
shows it (bc_filter_rules.measure_box).

With --reclassify, the model is not asked again: the classes are worked
out afresh from the answers already saved in --out (after a change to the
class rules), and written next to them without changing the run's own files.

Settings are those of the Mac runs: the shared ask_ollama, temperature 0,
thinking off, a 16,384-token context, Ollama choosing its own threads.

Output (--out), all ignored by git:
  prompts/NN.txt              the exact text sent for each record
  answers.jsonl               one line per record: the answer or the error,
                              tokens, seconds, the boxes' shares in BC, the
                              decision (model_in_bc, in_bc_used,
                              in_bc_filled_by_box_rule, class), the
                              evidence check against the prompt, and the
                              consistency flags
  report.txt                  per record: title, facts, class, evidence,
                              reason, flags; then the counts per class,
                              failures, speed and prompt length
  answers_reclassified.jsonl  with --reclassify: the saved answers with the
  report_reclassified.txt     decision worked out afresh, and their report

Run, from backend/:
    python pipeline/lunaris/llm/geo_pass2_trial.py \
        --bc-boundary /Users/lucia/Desktop/BCBN/Results/bc_boundary_gbif.wkt
"""

import argparse
import json
import os
import statistics
import sys
import time
from collections import Counter

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import geo_pass2_prompt_c4  # noqa: E402
from bc_filter_rules import load_bc_shape, measure_box  # noqa: E402
from geo_pass1_trial import read_harvest  # noqa: E402
from geo_pass2_classes import CLASSES, decide  # noqa: E402
from ollama_client import OLLAMA_NUM_CTX, ask_ollama, wait_for_server  # noqa: E402

# A prompt longer than this share of the context is reported as close to the limit.
NEAR_LIMIT = 0.8

# Where the trial is written unless --out says otherwise.
DEFAULT_OUT = "data/lunaris_bc_filter/pass2_trial_c4"


def read_pass1(folder):
    """
    Return the pass 1 trial's records and places: the sample (a DataFrame
    with number, record_id and title), the places kept for each record
    number, and the lookup result for each of those places, in the same
    order. Stop if the lookup lines do not line up with the places.
    """
    sample = pd.read_csv(os.path.join(folder, "sample.csv"), keep_default_na=False)
    places = {}
    with open(os.path.join(folder, "answers.jsonl"), encoding="utf-8") as handle:
        for number, line in enumerate(handle):
            result = json.loads(line)
            places[number] = [] if "error" in result else result.get("places", result["answer"]["places"])
    lookups = {number: [] for number in places}
    with open(os.path.join(folder, "lookup.jsonl"), encoding="utf-8") as handle:
        for line in handle:
            result = json.loads(line)
            lookups[result["record"]].append(result)
    for number, kept in places.items():
        queries = [(r["query"]["name"], r["query"]["as_written"]) for r in lookups[number]]
        if queries != [(p["name"], p["as_written"]) for p in kept]:
            sys.exit(f"record {number}: lookup.jsonl does not match the places in answers.jsonl; "
                     f"rerun geonames_lookup.py --pass1 {folder}")
    return sample, places, lookups


def box_shares(record, shape):
    """
    Return the share in BC of each of a record's bounding boxes, from 0 to 1,
    as the note on the box shows it (None for a box with no area).
    """
    boxes = record.get("boxes")
    if boxes is None or len(boxes) == 0:
        return []
    shares = []
    for box in boxes:
        measured = measure_box(box, shape)
        shares.append(None if measured is None else measured[0])
    return shares


def add_decision(result, shares):
    """Add the boxes' shares and the decision (geo_pass2_classes.decide) to an answered result."""
    result["box_shares"] = shares
    result["decision"] = decide(result["answer"], shares)


def write_report(path, sample, results, prompt):
    """Write the per-record answers and classes, then the counts per class and the run figures."""
    lines = []
    for _, row in sample.iterrows():
        result = results[row["number"]]
        lines.append(f"[{row['number']:02d}] {row['title']}")
        if "error" in result:
            lines.append(f"     ERROR: {result['error']}")
            continue
        answer, decision = result["answer"], result["decision"]
        facts = ", ".join(f"{fact} {answer[fact]}" for fact in prompt.RESPONSE_SCHEMA["required"][2:])
        lines.append(f"     facts:    {facts}")
        if decision["in_bc_filled_by_box_rule"]:
            shares = ", ".join(f"{s:.0%}" for s in result["box_shares"] if s is not None)
            lines.append(f"     box rule: in_bc read as yes (box shares in BC: {shares})")
        lines.append(f"     class:    {decision['class']} (worked out by code)")
        lines.append(f"     evidence: {'; '.join(answer['evidence']) or '(none)'}")
        lines.append(f"     reason:   {answer['reason']}")
        if result.get("evidence_check", {}).get("unmatched"):
            lines.append(f"     evidence not found in the prompt: {result['evidence_check']['unmatched']}")
        if result.get("consistency_flags"):
            lines.append(f"     consistency flags: {', '.join(result['consistency_flags'])}")
    answered = [r for r in results.values() if "answer" in r]
    counts = Counter(r["decision"]["class"] for r in answered)
    lines += ["", "CLASSES: " + ", ".join(f"{name} {counts.get(name, 0)}" for name in CLASSES)]
    filled = sorted(r["number"] for r in answered if r["decision"]["in_bc_filled_by_box_rule"])
    lines.append(f"in_bc filled in by the box rule: {len(filled)}" + (f" (records {filled})" if filled else ""))
    checks = [r["evidence_check"] for r in answered if "evidence_check" in r]
    if checks:
        items = sum(c["items"] for c in checks)
        lines.append(f"evidence items found in the prompt: exactly {sum(c['exact'] for c in checks)} of {items}; "
                     f"ignoring case and extra spaces {sum(c['loose'] for c in checks)} of {items}")
    failed = [n for n, r in results.items() if "error" in r]
    lines.append(f"failures: {len(failed)} of {len(results)}" + (f" (records {failed})" if failed else ""))
    flagged = sorted(n for n, r in results.items() if r.get("consistency_flags"))
    lines.append(f"answers flagged as inconsistent with their reason: {len(flagged)}"
                 + (f" (records {flagged})" if flagged else ""))
    seconds = [r["seconds"] for r in results.values() if "seconds" in r]
    tokens = {n: r["input_tokens"] for n, r in results.items() if r.get("input_tokens")}
    if seconds:
        lines.append(f"seconds per record: mean {statistics.mean(seconds):.1f}, median "
                     f"{statistics.median(seconds):.1f}, range {min(seconds):.1f} to {max(seconds):.1f}")
    if tokens:
        longest = max(tokens, key=tokens.get)
        lines.append(f"prompt tokens: mean {statistics.mean(tokens.values()):,.0f}, median "
                     f"{statistics.median(tokens.values()):,.0f}, longest {tokens[longest]:,} (record "
                     f"{longest:02d}), context {OLLAMA_NUM_CTX:,}")
        near = sorted(n for n, t in tokens.items() if t > NEAR_LIMIT * OLLAMA_NUM_CTX)
        lines.append(f"prompts above {NEAR_LIMIT:.0%} of the context: {near or 'none'}")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def reclassify(out, sample, harvest, shape, prompt):
    """
    Work out the classes afresh from the answers saved in out, without
    asking the model, and write answers_reclassified.jsonl and
    report_reclassified.txt next to them. The run's own files are not changed.
    """
    results = {}
    with open(os.path.join(out, "answers.jsonl"), encoding="utf-8") as handle:
        for line in handle:
            result = json.loads(line)
            results[result["number"]] = result
    record_ids = dict(zip(sample["number"], sample["record_id"]))
    with open(os.path.join(out, "answers_reclassified.jsonl"), "w", encoding="utf-8") as handle:
        for number in sorted(results):
            result = results[number]
            if "answer" in result:
                add_decision(result, box_shares(harvest[record_ids[number]], shape))
            handle.write(json.dumps(result, ensure_ascii=False) + "\n")
    write_report(os.path.join(out, "report_reclassified.txt"), sample, results, prompt)
    print(f"reclassified {len(results)} saved answers in {out}")


def main():
    """Build the pass 2 prompts for a pass 1 trial's records, ask the model, and write the outputs."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("--pass1", default="data/lunaris_bc_filter/pass1_trial_v3",
                        help="the pass 1 trial folder, with lookup.jsonl from geonames_lookup.py --pass1")
    parser.add_argument("--harvest", default="exploration/lunaris/harvest_2026_10_20/lunaris_full_harvest.parquet")
    parser.add_argument("--bc-boundary", required=True, help="bc_boundary_gbif.wkt")
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--model", default="qwen3.5:4b")
    parser.add_argument("--ollama-url", default="http://localhost:11434")
    parser.add_argument("--prompts-only", action="store_true",
                        help="only write the prompts, without asking the model")
    parser.add_argument("--reclassify", action="store_true",
                        help="only work out the classes afresh from the answers saved in --out")
    args = parser.parse_args()
    prompt = geo_pass2_prompt_c4

    sample, places, lookups = read_pass1(args.pass1)
    shape = load_bc_shape(args.bc_boundary)
    harvest = read_harvest(args.harvest, sample["record_id"])
    if args.reclassify:
        reclassify(args.out, sample, harvest, shape, prompt)
        return 0

    os.makedirs(os.path.join(args.out, "prompts"), exist_ok=True)
    prompts = {}
    for _, row in sample.iterrows():
        number = row["number"]
        prompts[number] = prompt.build_prompt(harvest[row["record_id"]], shape, places[number], lookups[number])
        with open(os.path.join(args.out, "prompts", f"{number:02d}.txt"), "w", encoding="utf-8") as handle:
            handle.write(prompts[number])
    lengths = [len(text) for text in prompts.values()]
    print(f"{len(prompts)} prompts written; characters: mean {statistics.mean(lengths):,.0f}, "
          f"longest {max(lengths):,}")
    if args.prompts_only:
        return 0

    print(f"Ollama {wait_for_server(args.ollama_url, 60)} at {args.ollama_url}, model {args.model}, "
          f"pass 2 prompt {prompt.GEO_PASS2_VERSION}")
    results = {}
    with open(os.path.join(args.out, "answers.jsonl"), "w", encoding="utf-8") as answers:
        for _, row in sample.iterrows():
            number = row["number"]
            started = time.monotonic()
            result = ask_ollama(args.ollama_url, args.model, prompts[number], schema=prompt.RESPONSE_SCHEMA)
            result["seconds"] = round(time.monotonic() - started, 2)
            if "answer" in result:
                problem = prompt.check_answer(result["answer"])
                if problem:
                    result = {"error": f"answer does not fit the schema: {problem}",
                              "seconds": result["seconds"], "input_tokens": result.get("input_tokens")}
                else:
                    add_decision(result, box_shares(harvest[row["record_id"]], shape))
                    # Check the evidence against the prompt; the answer is never changed by it.
                    result["evidence_check"] = prompt.evidence_check(result["answer"], prompts[number])
                    # Flags only; the answer and the class are never changed by them.
                    result["consistency_flags"] = prompt.consistency_flags(result["answer"])
            result["record_id"], result["number"] = row["record_id"], number
            results[number] = result
            answers.write(json.dumps(result, ensure_ascii=False) + "\n")
            answers.flush()
            said = result["decision"]["class"] if "answer" in result else "ERROR"
            print(f"  [{number:02d}] {result['seconds']:5.1f} s  {result.get('input_tokens') or 0:6,} tokens  "
                  f"{said:22s} {row['title'][:60]}", flush=True)

    write_report(os.path.join(args.out, "report.txt"), sample, results, prompt)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
