"""
Score several trial files on the answer key and print one table side by side.

score_trial.py prints a block per model and keys trials by their model
column, so two runs with the same label (the two Fir 4b runs) become one.
This names each trial on the command line instead, and adds two reference
rows: the keyword filter, and Claude (the opus_label column, Claude Opus's
own label for every test record). Everything is scored exactly as
score_trial.py scores it: its decide() turns a model's facts into yes or no,
and its weights_for() and score() weight each record back to the corpus.
Nothing in score_trial.py is changed.

For each row: weighted precision and recall, how many records it would keep,
how many of those are not biodiversity data (junk kept), how many real
records it would lose, how many records failed (model_error; left out, as
score_trial.py leaves them out), and the seconds per call from the
.about.json merge_results.py writes next to a cluster CSV, when there is one.
Only the records every trial answered are scored, so the rows compare the
same records.

Usage, from backend/:

    python exploration/llm/score_table.py --reviewed-only \\
        --labels exploration/llm/test_set_reviewed_harvest_2026_10.csv \\
        --trial "fir 4b run 1=data/lunaris_llm_runs/fir_test900/trial_qwen3.5-4b-fir_v6_test900.csv" \\
        --trial "fir 4b x4=data/lunaris_llm_runs/fir_test900_x4/trial_qwen3.5-4b-fir_v6_test900_x4.csv"
"""

import argparse
import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from score_trial import decide, score, weights_for  # noqa: E402


def read_trial(path):
    """Return a trial's answered rows with their verdict, and how many records failed."""
    frame = pd.read_csv(path, encoding="utf-8-sig")
    frame["record_id"] = frame["record_id"].astype(str)
    failed = frame[frame["model_error"].notna()] if "model_error" in frame.columns else frame.iloc[0:0]
    frame = frame[frame["model_error"].isna()] if "model_error" in frame.columns else frame
    frame = frame.copy()
    frame["verdict"] = frame.apply(decide, axis=1)
    return frame.set_index("record_id"), set(failed["record_id"].astype(str))


def seconds_per_call(path):
    """Return the mean and median seconds per call from the .about.json beside a merged CSV, or (None, None)."""
    about = os.path.splitext(path)[0] + ".about.json"
    if not os.path.exists(about):
        return None, None
    with open(about, encoding="utf-8") as handle:
        info = json.load(handle)
    return info.get("seconds_per_call_mean"), info.get("seconds_per_call_median")


def row(name, predicted, truth, weight, failed=None, seconds=(None, None)):
    """Return one table row: the weighted scores of a yes/no series, failures and seconds."""
    s = score(predicted, truth, weight)
    return {"run": name, "precision": s["precision"], "recall": s["recall"], "keeps": s["tp"] + s["fp"],
            "junk_kept": s["fp"], "real_lost": s["fn"], "failed": failed, "s_mean": seconds[0],
            "s_median": seconds[1]}


def table(labels, trials, reviewed_only):
    """
    Score each trial (name to CSV path) on the labels and return the rows, the
    number of records scored, and each trial's failures among the labelled
    records. The keyword filter and Claude rows come first.
    """
    if reviewed_only:
        labels = labels[labels["reviewed_by_hand"] == True]  # noqa: E712 (the column holds True and False)
    loaded = {name: read_trial(path) for name, path in trials.items()}
    common = set(labels["record_id"])
    for frame, _ in loaded.values():
        common &= set(frame.index)
    common = sorted(common)
    base = labels.set_index("record_id").loc[common]
    weight, truth = weights_for(base), base["is_biodiversity"]
    rows = [row("keyword filter", base["stratum"].map({"kept": "yes", "dropped": "no"}), truth, weight)]
    if "opus_label" in base.columns:
        rows.append(row("Claude (opus_label)", base["opus_label"], truth, weight))
    labelled = set(labels["record_id"])
    for name, (frame, failed) in loaded.items():
        rows.append(row(name, frame.loc[common, "verdict"], truth, weight,
                        f"{len(failed)} ({len(failed & labelled)} labelled)", seconds_per_call(trials[name])))
    return rows, len(common)


def main():
    """Read the labels and trials and print the table."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--labels", default="exploration/llm/test_set_reviewed_harvest_2026_10.csv")
    parser.add_argument("--trial", action="append", required=True, help='"NAME=path/to/trial.csv"; give it once per run')
    parser.add_argument("--reviewed-only", action="store_true", help="score only the hand-reviewed records")
    args = parser.parse_args()
    labels = pd.read_csv(args.labels, encoding="utf-8-sig")
    labels["record_id"] = labels["record_id"].astype(str)
    trials = dict(item.split("=", 1) for item in args.trial)
    rows, scored = table(labels, trials, args.reviewed_only)
    print(f"{scored} records scored (answered by every trial), weighted to the corpus\n")
    print(f"{'run':<24}{'precision':>10}{'recall':>8}{'keeps':>9}{'junk kept':>11}{'real lost':>11}"
          f"{'failed':>18}{'s/call mean':>13}{'median':>8}")
    for r in rows:
        mean = f"{r['s_mean']:.1f}" if r["s_mean"] is not None else "-"
        median = f"{r['s_median']:.1f}" if r["s_median"] is not None else "-"
        print(f"{r['run']:<24}{r['precision']:>10.1%}{r['recall']:>8.1%}{r['keeps']:>9,.0f}{r['junk_kept']:>11,.0f}"
              f"{r['real_lost']:>11,.0f}{(r['failed'] or '-'):>18}{mean:>13}{median:>8}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
