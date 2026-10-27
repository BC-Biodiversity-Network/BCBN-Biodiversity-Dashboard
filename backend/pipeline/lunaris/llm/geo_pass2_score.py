"""
geo_pass2_score.py

Score pass 2 runs against labels that a person has checked: a measurement
only. It reads the labels, the pass 1 folder the runs used (to know which
record each run number is) and each run's saved answers, and writes one
report. Nothing is run and nothing is changed.

For each run:
  - 7-class accuracy against final_label;
  - "on the site" accuracy: bc_only, canada_incl_bc, all_canada,
    bc_and_outside_canada and global are shown (SHOWN), elsewhere and
    unknown are not; with how many records are shown but should not be, and
    not shown but should be;
  - a confusion table, label against answer (a failed record counts as
    "failed");
  - how often the box rule filled in in_bc, and whether those were right;
  - failures, retries, and the mean seconds per record.
Then every record some run gets wrong, with the reason for each wrong
answer, and the records with an outside label (true_label_external), which
are reported and never scored against.

Run, from backend/:
    python pipeline/lunaris/llm/geo_pass2_score.py \
        --labels data/lunaris_bc_filter/label_set_2026_10/labels_final.csv \
        --pass1 data/lunaris_bc_filter/pass1_label150 \
        --run C3=data/lunaris_bc_filter/pass2_label150_c3 \
        --run C4=data/lunaris_bc_filter/pass2_label150_c4 \
        --out data/lunaris_bc_filter/score_label150.txt
"""

import argparse
import json
import os
import statistics
import sys
from collections import Counter

import pandas as pd

CLASSES = ["bc_only", "canada_incl_bc", "all_canada", "bc_and_outside_canada", "global", "elsewhere", "unknown"]
# The classes whose records go on the site.
SHOWN = {"bc_only", "canada_incl_bc", "all_canada", "bc_and_outside_canada", "global"}
FAILED = "failed"


def read_answers(folder):
    """
    Return a run's results by record id, from its answers.jsonl, with any
    retries from answers_retry.jsonl put in place of the failures they
    retried (the first failure is kept under first_error).
    """
    results = {}
    with open(os.path.join(folder, "answers.jsonl"), encoding="utf-8") as handle:
        for line in handle:
            result = json.loads(line)
            results[result["record_id"]] = result
    retry_path = os.path.join(folder, "answers_retry.jsonl")
    if os.path.exists(retry_path):
        with open(retry_path, encoding="utf-8") as handle:
            for line in handle:
                retry = json.loads(line)
                retry["first_error"] = results[retry["record_id"]].get("error")
                results[retry["record_id"]] = retry
    return results


def class_of(result):
    """Return a result's class, or "failed" for a record with no answer."""
    return result["decision"]["class"] if "decision" in result else FAILED


def shown(name):
    """Return True if a class goes on the site."""
    return name in SHOWN


def score_run(name, labels, results):
    """Return the report lines for one run: accuracy, site accuracy, confusion table, box rule, speed."""
    answers = [class_of(results[r]) for r in labels["record_id"]]
    truth = list(labels["final_label"])
    n = len(truth)
    right = sum(a == t for a, t in zip(answers, truth))
    site_right = sum(shown(a) == shown(t) for a, t in zip(answers, truth))
    wrongly_shown = sum(shown(a) and not shown(t) for a, t in zip(answers, truth))
    wrongly_hidden = sum(shown(t) and not shown(a) for a, t in zip(answers, truth))
    lines = [f"=== {name} ===",
             f"7-class accuracy: {right} of {n} ({right / n:.1%})",
             f"on the site: {site_right} of {n} ({site_right / n:.1%}); shown but should not be {wrongly_shown}, "
             f"not shown but should be {wrongly_hidden}"]
    # The confusion table: one row per label, one column per answer.
    columns = CLASSES + ([FAILED] if FAILED in answers else [])
    table = Counter(zip(truth, answers))
    width = max(len(c) for c in CLASSES) + 2
    lines.append("confusion (rows: label; columns: answer)")
    lines.append(" " * width + "".join(f"{c[:10]:>11}" for c in columns) + f"{'total':>8}")
    for label in CLASSES:
        row = [table[(label, c)] for c in columns]
        if sum(row):
            lines.append(f"{label:<{width}}" + "".join(f"{v:>11}" for v in row) + f"{sum(row):>8}")
    lines.append(f"{'total':<{width}}" + "".join(f"{answers.count(c):>11}" for c in columns) + f"{n:>8}")
    filled = [(r, t) for r, t in zip(labels["record_id"], truth)
              if "decision" in results[r] and results[r]["decision"]["in_bc_filled_by_box_rule"]]
    filled_right = sum(class_of(results[r]) == t for r, t in filled)
    lines.append(f"box rule filled in in_bc: {len(filled)}; the class was right for {filled_right} of them"
                 + (" (" + ", ".join(labels.set_index("record_id").loc[r, "label_id"] for r, _ in filled) + ")"
                    if filled else ""))
    failures = [r for r in labels["record_id"] if "error" in results[r]]
    retried = [r for r in labels["record_id"] if results[r].get("first_error")]
    lines.append(f"failures left: {len(failures)}; retried after a failure: {len(retried)}"
                 + (" (" + ", ".join(labels.set_index("record_id").loc[r, "label_id"] for r in retried) + ")"
                    if retried else ""))
    seconds = [results[r]["seconds"] for r in labels["record_id"] if "seconds" in results[r]]
    if seconds:
        lines.append(f"seconds per record: mean {statistics.mean(seconds):.1f}, median {statistics.median(seconds):.1f}")
    return lines


def error_lines(labels, runs):
    """Return the lines listing every record some run gets wrong, with the reason for each wrong answer."""
    lines = ["=== records some run gets wrong (7-class); [site] marks a wrong shown / not shown decision ==="]
    for row in labels.itertuples():
        answers = {name: class_of(results[row.record_id]) for name, results in runs.items()}
        wrong = {name: a for name, a in answers.items() if a != row.final_label}
        if not wrong:
            continue
        cells = "; ".join(f"{name} {a}" + (" [site]" if shown(a) != shown(row.final_label) else "")
                          for name, a in answers.items())
        lines.append(f"{row.label_id} {str(row.title)[:80]}")
        lines.append(f"   label {row.final_label}; {cells}")
        for name in wrong:
            result = runs[name][row.record_id]
            reason = result["answer"]["reason"] if "answer" in result else result.get("error", "")
            lines.append(f"   {name} reason: {reason}")
    return lines


def external_lines(labels, runs):
    """Return the lines for records with an outside label: never scored against, only reported."""
    extra = labels[labels["true_label_external"].fillna("") != ""]
    lines = ["=== records with an outside label (true_label_external; not scored) ==="]
    for row in extra.itertuples():
        answers = ", ".join(f"{name} {class_of(results[row.record_id])}" for name, results in runs.items())
        lines.append(f"{row.label_id} {str(row.title)[:70]}: final_label {row.final_label}, "
                     f"true_label_external {row.true_label_external}; {answers}")
    return lines


def main():
    """Read the labels and the runs, and write the report."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("--labels", required=True, help="the checked labels (final_label column)")
    parser.add_argument("--group", default="needs_llm", help="only the label rows of this group are scored")
    parser.add_argument("--pass1", required=True, help="the pass 1 folder the runs used")
    parser.add_argument("--run", action="append", required=True, help="NAME=FOLDER of a pass 2 run")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    labels = pd.read_csv(args.labels, keep_default_na=False)
    labels = labels[labels["group"] == args.group].sort_values("label_id").reset_index(drop=True)
    sample = pd.read_csv(os.path.join(args.pass1, "sample.csv"), keep_default_na=False)
    if set(sample["record_id"]) != set(labels["record_id"]):
        sys.exit("the pass 1 folder's records are not the labelled records")
    runs = {}
    for item in args.run:
        name, folder = item.split("=", 1)
        runs[name] = read_answers(folder)
    pass1_seconds = []
    with open(os.path.join(args.pass1, "answers.jsonl"), encoding="utf-8") as handle:
        for line in handle:
            result = json.loads(line)
            if "seconds" in result:
                pass1_seconds.append(result["seconds"])
    lines = [f"labels: {args.labels} ({len(labels)} records of group {args.group}; final_label)",
             "labels: " + ", ".join(f"{k} {v}" for k, v in Counter(labels["final_label"]).most_common()),
             f"pass 1: {args.pass1}; mean {statistics.mean(pass1_seconds):.1f} s per record", ""]
    for name, results in runs.items():
        lines += score_run(name, labels, results) + [""]
    lines += error_lines(labels, runs) + [""] + external_lines(labels, runs)
    with open(args.out, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
