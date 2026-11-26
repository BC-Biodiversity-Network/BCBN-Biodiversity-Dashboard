"""
check_filter2_repeat.py

Read only. Check F: the 200 records of the DataONE filter 2 test (test200,
2026-11-21) that do not reuse Lunaris (186) were asked again in the full
filter 2 run. For each model (qwen3.5:4b, qwen3.6:35b-a3b) and for the
union (yes if either model says yes), list the records whose answer
differs between the two runs, with the gold label and which run agrees
with it, and say whether each such record's prompt was the same.

Where the answers come from:
  test run   the merged trial CSVs of the test run folders
             (filter2_common.TEST_RUNS), with decide()'s verdict, read by
             exploration/dataone/score_filter2_test.read_dataone, which also
             gives gold_biodiversity (lucia_labels_final_and_comparison.csv)
             and reuse_lunaris
  full run   filter2_union.parquet: answer_4b, answer_35b and keep_union,
             the answers filter 2 kept (a record whose full-run prompt
             Ollama cut has its rerun answer there; rerun_<model> says so)
The prompt fingerprint is the prompt_sha1 of each record's results line
(run_files.valid_results, as merge_results.py reads them), in the test run
folder and in the full run folder (or the rerun folder when the answer
came from the rerun). When the fingerprints differ, the input lines of the
two runs are compared field by field. When they are the same, the run
settings in the two results lines are compared (num_ctx, num_thread,
model digest, Ollama version, host, job).

Output: ~/Desktop/BCBN/Results/dataone_filter3_checks_2026-11-24/filter2_run_differences.csv

Run, from backend/:
    ../.venv/bin/python exploration/dataone/check_filter2_repeat.py
"""

import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(BACKEND, "pipeline", "dataone"))
sys.path.insert(0, os.path.join(BACKEND, "pipeline", "lunaris", "llm"))
from filter2_common import FILTER2_RUNS, TEST_RUNS  # noqa: E402
from run_files import batch_numbers, load_run, read_inputs, valid_results  # noqa: E402
from score_filter2_test import read_dataone  # noqa: E402

UNION = os.path.join(BACKEND, "data", "dataone", "filter2_results", "filter2_union.parquet")
FULL_RUNS = {"4b": os.path.join(FILTER2_RUNS, "fir_dataone_harvest_2026_11_18"),
             "35b": os.path.join(FILTER2_RUNS, "fir_dataone_harvest_2026_11_18_qwen3.6-35b-a3b_nofa")}
RERUNS = {"4b": os.path.join(FILTER2_RUNS, "fir_dataone_cut_rerun_2026_11_23"),
          "35b": os.path.join(FILTER2_RUNS, "fir_dataone_cut_rerun_2026_11_23_qwen3.6-35b-a3b_nofa")}
OUT = os.path.expanduser("~/Desktop/BCBN/Results/dataone_filter3_checks_2026-11-24/filter2_run_differences.csv")
EXPECTED = 186
SETTINGS = ["num_ctx", "num_thread", "model_digest", "ollama_version", "host", "job_id", "workers"]
FIELDS = ["title", "subjects", "abstract"]


def run_lines(run_dir, wanted):
    """
    Return the valid results lines and the input lines of the wanted
    records in one run folder, as two dicts by record id, read batch by
    batch with run_files as merge_results.py reads them.
    """
    run = load_run(run_dir)
    results, inputs = {}, {}
    for number in batch_numbers(run_dir):
        records = read_inputs(run_dir, number)
        mine = [r for r in records if r["record_id"] in wanted]
        if not mine:
            continue
        inputs.update({r["record_id"]: r for r in mine})
        valid = valid_results(run_dir, number, run, records)
        results.update({i: valid[i] for i in wanted if i in valid})
    return results, inputs


def what_differs(test_result, full_result, test_input, full_input):
    """Return in words whether the prompt was the same and, if so, which run settings differed; if not, which input fields."""
    if test_result is None or full_result is None:
        return "no valid results line in one run", ""
    if test_result["prompt_sha1"] == full_result["prompt_sha1"]:
        settings = [f"{k} {test_result.get(k)} vs {full_result.get(k)}" for k in SETTINGS
                    if test_result.get(k) != full_result.get(k)]
        return "same", "; ".join(settings)
    fields = [f for f in FIELDS if test_input.get(f) != full_input.get(f)]
    return "different", "input fields differ: " + (", ".join(fields) if fields else "none (the prompt text itself)")


def agrees(gold, test, full):
    """Return which run agrees with the gold label: test, full, both or neither."""
    return {(True, True): "both", (True, False): "test", (False, True): "full", (False, False): "neither"}[
        (test == gold, full == gold)]


def main():
    """Find the 186 records, compare the answers of the two runs, check the prompts, print and write the list."""
    table = read_dataone()
    table = table[~table["reuse_lunaris"]].set_index("id")
    if len(table) != EXPECTED:
        sys.exit(f"STOPPED: {len(table)} test records do not reuse Lunaris, not {EXPECTED}")
    union = pd.read_parquet(UNION).set_index("id").reindex(table.index)
    if union["keep_source"].isna().any():
        sys.exit("STOPPED: some test records are not in the filter 2 union")
    wanted = set(table.index)
    lines = {}
    for model in ("4b", "35b"):
        test = run_lines(TEST_RUNS[model], wanted)
        full = run_lines(FULL_RUNS[model], wanted)
        rerun = run_lines(RERUNS[model], wanted) if os.path.isdir(RERUNS[model]) else ({}, {})
        lines[model] = test, full, rerun
    rows = []
    for model, test_col, full_col in (("4b", "4b_verdict", "answer_4b"), ("35b", "35b_verdict", "answer_35b"),
                                      ("union", "union", "keep_union")):
        full_answer = union[full_col]
        if model == "union":
            full_answer = full_answer.map({True: "yes", False: "no"}).fillna("failed")
        differ = table.index[table[test_col] != full_answer]
        print(f"{model}: {len(differ)} of {len(table)} records answer differently "
              f"(test vs full: {pd.crosstab(table.loc[differ, test_col], full_answer[differ]).to_dict()})")
        for record_id in differ:
            row = {"model": model, "record_id": record_id, "datasource": table.at[record_id, "datasource"],
                   "group": table.at[record_id, "group"], "title": table.at[record_id, "title"],
                   "gold_biodiversity": table.at[record_id, "gold"], "test_answer": table.at[record_id, test_col],
                   "full_answer": full_answer[record_id],
                   "agrees_with_gold": agrees(table.at[record_id, "gold"], table.at[record_id, test_col],
                                              full_answer[record_id])}
            for one in (("4b", "35b") if model == "union" else (model,)):
                (test_res, test_in), (full_res, full_in), (re_res, re_in) = lines[one]
                from_rerun = bool(union.at[record_id, f"rerun_{one}"])
                full_line = (re_res if from_rerun else full_res).get(record_id)
                full_input = (re_in if from_rerun else full_in).get(record_id, {})
                same, note = what_differs(test_res.get(record_id), full_line, test_in.get(record_id, {}), full_input)
                row[f"{one}_test"] = table.at[record_id, f"{one}_verdict"]
                row[f"{one}_full"] = union.at[record_id, f"answer_{one}"]
                row[f"{one}_test_rule"] = table.at[record_id, f"{one}_rule"]
                row[f"{one}_full_from_rerun"] = from_rerun
                row[f"{one}_prompt"] = same
                row[f"{one}_what_differs"] = note
            rows.append(row)
    out = pd.DataFrame(rows)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_colwidth", 70)
    show = ["model", "record_id", "datasource", "gold_biodiversity", "test_answer", "full_answer", "agrees_with_gold",
            "4b_prompt", "35b_prompt", "4b_what_differs", "35b_what_differs", "title"]
    print(out.reindex(columns=show).fillna("").to_string(index=False))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    out.to_csv(OUT, index=False, encoding="utf-8-sig")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
