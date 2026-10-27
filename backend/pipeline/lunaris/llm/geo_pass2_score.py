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
are reported and never scored against. Last, one table comparing all runs
side by side (compare_lines).

Two kinds of run can be given. --run NAME=FOLDER is a pass 2 folder whose
pass 1 is the shared --pass1 folder (the Mac trials). --model-run
NAME=FOLDER is a run folder from geo_filter_run.py (one model, its own
pass1/ and pass2/), such as a Fir run copied back.

Run, from backend/:
    python pipeline/lunaris/llm/geo_pass2_score.py \
        --labels data/lunaris_bc_filter/label_set_2026_10/labels_final.csv \
        --pass1 data/lunaris_bc_filter/pass1_label150 \
        --run C3=data/lunaris_bc_filter/pass2_label150_c3 \
        --run C4=data/lunaris_bc_filter/pass2_label150_c4 \
        --out data/lunaris_bc_filter/score_label150.txt

    python pipeline/lunaris/llm/geo_pass2_score.py \
        --labels data/lunaris_bc_filter/label_set_2026_10/labels_final.csv \
        --model-run qwen3.5:4b=data/lunaris_bc_filter/fir_runs/label150_qwen3.5-4b \
        --model-run qwen3.5:9b=data/lunaris_bc_filter/fir_runs/label150_qwen3.5-9b \
        --out data/lunaris_bc_filter/score_label150_models.txt
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


def read_pass1(folder, labels):
    """
    Return a pass 1 folder's results by record id (answers.jsonl). Stop if
    its records (sample.csv) are not exactly the labelled records.
    """
    sample = pd.read_csv(os.path.join(folder, "sample.csv"), keep_default_na=False)
    if set(sample["record_id"]) != set(labels["record_id"]):
        sys.exit(f"{folder}: its records are not the labelled records")
    results = {}
    with open(os.path.join(folder, "answers.jsonl"), encoding="utf-8") as handle:
        for line in handle:
            result = json.loads(line)
            results[result["record_id"]] = result
    return results


def retried(result):
    """Return True if a result needed a second attempt (geo_filter_run.py's attempts, or a retry file's first_error)."""
    return result.get("attempts", 1) > 1 or bool(result.get("first_error"))


def mean_seconds(results, ids):
    """Return the mean seconds per record over the results that have them, or None."""
    seconds = [results[r]["seconds"] for r in ids if results[r].get("seconds") is not None]
    return statistics.mean(seconds) if seconds else None


def compare_lines(labels, runs, pass1_runs):
    """
    Return one table comparing every run: 7-class accuracy, shown / not
    shown accuracy, shown but should not be, not shown but should be,
    failures and retries in each pass, and mean seconds per record in pass
    1, pass 2 and both. pass1_runs gives each run's pass 1 results.
    """
    ids, truth = list(labels["record_id"]), list(labels["final_label"])
    n = len(ids)
    head = (f"{'run':<18}{'7-class':>9}{'on site':>9}{'shown, should not':>19}{'hidden, should show':>21}"
            f"{'failed p1/p2':>14}{'retried p1/p2':>15}{'s p1':>7}{'s p2':>7}{'s total':>9}")
    lines = ["=== all runs side by side (seconds are the mean per record) ===", head]
    for name, results in runs.items():
        pass1 = pass1_runs[name]
        answers = [class_of(results[r]) for r in ids]
        right = sum(a == t for a, t in zip(answers, truth))
        site = sum(shown(a) == shown(t) for a, t in zip(answers, truth))
        wrongly_shown = sum(shown(a) and not shown(t) for a, t in zip(answers, truth))
        wrongly_hidden = sum(shown(t) and not shown(a) for a, t in zip(answers, truth))
        failed = f"{sum('error' in pass1[r] for r in ids)}/{sum('error' in results[r] for r in ids)}"
        again = f"{sum(retried(pass1[r]) for r in ids)}/{sum(retried(results[r]) for r in ids)}"
        s1, s2 = mean_seconds(pass1, ids), mean_seconds(results, ids)
        both = s1 + s2 if s1 is not None and s2 is not None else None
        shown_s = [f"{v:.1f}" if v is not None else "-" for v in (s1, s2, both)]
        lines.append(f"{name:<18}{right / n:>9.1%}{site / n:>9.1%}{wrongly_shown:>19}{wrongly_hidden:>21}"
                     f"{failed:>14}{again:>15}{shown_s[0]:>7}{shown_s[1]:>7}{shown_s[2]:>9}")
    lines.append(f"({n} records; 7-class and on site are shares right; failed counts records left without "
                 f"an answer after the retry)")
    return lines


def main():
    """Read the labels and the runs, and write the report."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("--labels", required=True, help="the checked labels (final_label column)")
    parser.add_argument("--group", default="needs_llm", help="only the label rows of this group are scored")
    parser.add_argument("--pass1", help="the pass 1 folder the --run runs used")
    parser.add_argument("--run", action="append", default=[], help="NAME=FOLDER of a pass 2 run (with --pass1)")
    parser.add_argument("--model-run", action="append", default=[],
                        help="NAME=FOLDER of a geo_filter_run.py run folder (pass1/ and pass2/ inside)")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    if not args.run and not args.model_run:
        sys.exit("give at least one --run or --model-run")
    if args.run and not args.pass1:
        sys.exit("--run needs --pass1")

    labels = pd.read_csv(args.labels, keep_default_na=False)
    labels = labels[labels["group"] == args.group].sort_values("label_id").reset_index(drop=True)
    runs, pass1_runs, sources = {}, {}, []
    shared = read_pass1(args.pass1, labels) if args.pass1 else None
    for item in args.run:
        name, folder = item.split("=", 1)
        runs[name], pass1_runs[name] = read_answers(folder), shared
    if shared:
        seconds = mean_seconds(shared, labels["record_id"])
        sources.append(f"pass 1: {args.pass1}" + (f"; mean {seconds:.1f} s per record" if seconds else ""))
    for item in args.model_run:
        name, folder = item.split("=", 1)
        pass1_runs[name] = read_pass1(os.path.join(folder, "pass1"), labels)
        runs[name] = read_answers(os.path.join(folder, "pass2"))
        about = os.path.join(folder, "run.json")
        if os.path.exists(about):
            with open(about, encoding="utf-8") as handle:
                info = json.load(handle)
            sources.append(f"{name}: {folder}; {info['model']} digest {info['model_digest'][:12]}, "
                           f"Ollama {info['ollama_version']}")
    lines = [f"labels: {args.labels} ({len(labels)} records of group {args.group}; final_label)",
             "labels: " + ", ".join(f"{k} {v}" for k, v in Counter(labels["final_label"]).most_common()),
             *sources, ""]
    for name, results in runs.items():
        lines += score_run(name, labels, results) + [""]
    lines += error_lines(labels, runs) + [""] + external_lines(labels, runs)
    lines += [""] + compare_lines(labels, runs, pass1_runs)
    with open(args.out, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
