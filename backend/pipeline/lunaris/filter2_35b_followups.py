"""
filter2_35b_followups.py

Two follow-ups to compare_filter2_35b.py. Reads only; writes new files.

1. A fair score on the reviewed test records that are in the 2026-10-20
   harvest (249 of 259): the 4b and 35b runs on the 900 test records and the
   4b and 35b full runs, all on exactly those records, scored as
   score_table.py scores (decide(), the reviewed weights). A full run's
   verdict is its keep set (decide() plus the hand decisions), the same rule
   filter 3 is built on; score_table.py instead leaves a failed record out.
   Also the reviewed records where the two full runs disagree.

2. An independent check of the records that filter 3 shows as bc_data now
   but the 35b run no longer keeps: a fixed-seed sample of 40, written as
   two files so the reader is not anchored:
     sample40_records.txt   numbered blocks [01] to [40]: id, source
                            catalogue, title, subjects, abstract and places,
                            exactly as harvested, and nothing of ours
     sample40_answers.csv   the same numbers, with both runs' verdict and
                            facts, and the filter 3 class

Outputs:
  in --out:      fair_scores_249.csv, full_runs_disagree_249.csv,
                 bc_data_dropped_by_35b_split.csv and followups_report.md
  in --results:  sample40_records.txt and sample40_answers.csv

Run, from backend/:
    python pipeline/lunaris/filter2_35b_followups.py \\
        --out data/lunaris_llm_runs/filter2_35b_compare/followups \\
        --results /Users/lucia/Desktop/BCBN/Results/filter2_35b_compare_2026-10-27
"""

import argparse
import os
import random
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import compare_filter2_35b as cmp  # noqa: E402
from build_bc_filter_rules import read_records  # noqa: E402
from check_harvest import source_of  # noqa: E402
from filter3_checks import as_harvested  # noqa: E402
from score_trial import score, weights_for  # noqa: E402

TEST900_4B = "data/lunaris_llm_runs/fir_test900/trial_qwen3.5-4b-fir_v6_test900.csv"
SAMPLE_SIZE = 40
# What a reader of sample40_records.txt sees, in this order: (label, harvest field).
RECORD_FIELDS = [("title", "title"), ("subjects", "subjects"), ("abstract", "abstract"), ("places", "places")]
# The facts shown for each run in sample40_answers.csv.
ANSWER_FACTS = ["topic", "about_organisms_themselves", "ecological_purpose_stated", "model_reason"]


# --- The fair score --------------------------------------------------------

def reviewed_in_harvest(labels, harvest_ids):
    """Return the hand-reviewed labels whose record is in the harvest, indexed by record id."""
    reviewed = labels[labels["reviewed_by_hand"] == True]  # noqa: E712 (the column holds True and False)
    return reviewed[reviewed["record_id"].isin(harvest_ids)].set_index("record_id")


def verdicts_from_answers(answers, record_ids):
    """
    Return decide()'s yes or no for each record of a run on the test records.
    Stops if a record is missing or failed, so every run is scored on the
    same records.
    """
    missing = [r for r in record_ids if r not in answers.index or answers.loc[r, "verdict"] == "failed"]
    if missing:
        raise SystemExit(f"ERROR: {len(missing)} records have no answer in a test run, for example {missing[:3]}")
    return answers.loc[list(record_ids), "verdict"]


def verdicts_from_keep(kept, record_ids):
    """Return yes for each record in a keep set and no for the rest."""
    return pd.Series(["yes" if r in kept else "no" for r in record_ids], index=list(record_ids))


def fair_rows(labels, runs):
    """
    Score each run (name to a yes/no Series over the labels' records) exactly
    as score_table.py does, and return one row per run: precision, recall,
    junk kept and real lost, weighted to the corpus.
    """
    weight, truth = weights_for(labels), labels["is_biodiversity"]
    rows = []
    for name, verdicts in runs.items():
        s = score(verdicts.loc[labels.index], truth, weight)
        rows.append({"run": name, "records": len(labels), "precision": s["precision"], "recall": s["recall"],
                     "keeps": s["tp"] + s["fp"], "junk_kept": s["fp"], "real_lost": s["fn"]})
    return pd.DataFrame(rows)


def full_runs_disagree(labels, verdicts_4b, verdicts_35b, answers_4b, answers_35b):
    """
    Return the records where the two full runs' verdicts differ, with the
    label, the record's weight, both verdicts, the title and each run's topic
    and reason, the heaviest first.
    """
    weight = weights_for(labels)
    rows = []
    for record_id in labels.index:
        if verdicts_4b[record_id] == verdicts_35b[record_id]:
            continue
        rows.append({"record_id": record_id, "label": labels.loc[record_id, "is_biodiversity"],
                     "weight": round(float(weight[record_id]), 1), "full_4b": verdicts_4b[record_id],
                     "full_35b": verdicts_35b[record_id], "right": "4b" if verdicts_4b[record_id] ==
                     labels.loc[record_id, "is_biodiversity"] else "35b",
                     "title": labels.loc[record_id, "title"],
                     "topic_4b": cmp.plain(answers_4b.loc[record_id, "topic"]),
                     "topic_35b": cmp.plain(answers_35b.loc[record_id, "topic"]),
                     "reason_4b": cmp.plain(answers_4b.loc[record_id, "model_reason"]),
                     "reason_35b": cmp.plain(answers_35b.loc[record_id, "model_reason"])})
    columns = ["record_id", "label", "weight", "full_4b", "full_35b", "right", "title", "topic_4b", "topic_35b",
               "reason_4b", "reason_35b"]
    return pd.DataFrame(rows, columns=columns).sort_values("weight", ascending=False, kind="stable")


# --- The sample of 40 ------------------------------------------------------

def bc_data_dropped(filter3, kept_35b):
    """Return the record ids filter 3 shows as bc_data that the 35b keep set does not hold, sorted."""
    shown = filter3[filter3["frontend_group"] == "bc_data"]
    return sorted(set(shown.index) - kept_35b)


def draw_sample(record_ids, size=SAMPLE_SIZE, seed=cmp.SEED):
    """
    Return size record ids drawn at random with a fixed seed from the sorted
    list, in the order drawn, so the numbering mixes catalogues and is the
    same every run.
    """
    return random.Random(seed).sample(sorted(record_ids), size)


def record_block(number, record_id, record):
    """
    Return one numbered block for sample40_records.txt: the id, the source
    catalogue and each field exactly as harvested (a list as JSON), and
    nothing else.
    """
    lines = [f"[{number:02d}]", f"id: {record_id}", f"source catalogue: {source_of(record_id)}"]
    lines += [f"{label}: {as_harvested(record[field])}" for label, field in RECORD_FIELDS]
    return "\n".join(lines)


def sample_answers(sample, answers_4b, answers_35b, hand, filter3):
    """
    Return sample40_answers.csv's rows: the number, the id, each run's
    verdict and facts, and the filter 3 class and front end group.
    """
    rows = []
    for number, record_id in enumerate(sample, start=1):
        row = {"number": f"{number:02d}", "record_id": record_id}
        for name, answers in [("4b", answers_4b), ("35b", answers_35b)]:
            row[f"verdict_{name}"] = cmp.answer_text(answers, hand, record_id)
            for fact in ANSWER_FACTS:
                row[f"{fact.replace('model_', '')}_{name}"] = cmp.plain(answers.loc[record_id, fact])
        row["filter3_final_class"] = filter3.loc[record_id, "final_class"]
        row["filter3_frontend_group"] = filter3.loc[record_id, "frontend_group"]
        rows.append(row)
    return pd.DataFrame(rows)


def split_counts(record_ids, answers_35b, filter3):
    """Return the records counted by the 35b topic and by catalogue, as one table (split, value, records)."""
    ids = list(record_ids)
    parts = {"35b topic": [cmp.plain(answers_35b.loc[r, "topic"]) or "(failed)" for r in ids],
             "catalogue": [source_of(r) for r in ids],
             "filter3 final_class": [filter3.loc[r, "final_class"] for r in ids]}
    rows = []
    for split, values in parts.items():
        for value, n in pd.Series(values).value_counts().items():
            rows.append({"split": split, "value": value, "records": int(n)})
    return pd.DataFrame(rows)


# --- Main ------------------------------------------------------------------

def fresh_path(path):
    """Return path after checking nothing is there yet, so no earlier file is ever written over."""
    if os.path.exists(path):
        raise SystemExit(f"ERROR: {path} already exists; nothing is overwritten")
    return path


def report(path, fair, disagree, split, sample_count, dropped_count, results_dir):
    """Write the follow-ups report."""
    def pct(x):
        """Return a share as a percentage with one decimal."""
        return f"{x:.1%}"

    lines = ["# Filter 2 with qwen3.6:35b-a3b: follow-ups", "",
             "## Fair score on the reviewed records in the 2026-10-20 harvest", "",
             f"Every run on exactly the same {fair['records'].iloc[0]} reviewed records, scored as score_table.py "
             "scores (decide(), the reviewed weights). The full runs are scored by their keep sets (decide() plus "
             "the hand decisions), as filter 3 uses them: the one reviewed record the 4b full run failed on "
             "(oai:datadryad.org:doi:10.5061/dryad.dm690, labelled yes) counts as kept, by hand decision. "
             "score_table.py would leave it out and score the 4b full run on 248.", "",
             cmp.markdown(["run", "precision", "recall", "junk kept", "real lost"],
                          [[r.run, pct(r.precision), pct(r.recall), f"{r.junk_kept:,.0f}", f"{r.real_lost:,.0f}"]
                           for r in fair.itertuples()]), "",
             f"### Reviewed records where the 4b and 35b full runs disagree ({len(disagree)})", "",
             f"Right by the label: 4b on {(disagree['right'] == '4b').sum()}, 35b on "
             f"{(disagree['right'] == '35b').sum()}. Heaviest first; also in `full_runs_disagree_249.csv` "
             "with both reasons.", "",
             cmp.markdown(["record_id", "label", "weight", "4b full", "35b full", "35b topic", "title"],
                          [[r.record_id, r.label, f"{r.weight:,.1f}", r.full_4b, r.full_35b, r.topic_35b,
                            str(r.title)[:80]] for r in disagree.itertuples()]), "",
             f"## The {dropped_count} bc_data records the 35b run drops", "",
             f"A sample of {sample_count} (seed {cmp.SEED}, numbered in the order drawn) is in `{results_dir}`: "
             "`sample40_records.txt` (the records only, as harvested) and `sample40_answers.csv` (both runs' "
             "answers and the filter 3 class, by the same numbers). The harvest has no keyword field apart "
             "from `subjects`, which is where the keywords are.", ""]
    for name in ["35b topic", "catalogue", "filter3 final_class"]:
        part = split[split["split"] == name]
        lines += [f"By {name}:", "", cmp.markdown([name, "records"], [[r.value, f"{r.records:,}"]
                                                                     for r in part.itertuples()]), ""]
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


def main():
    """Score the reviewed records fairly, draw the sample of 40, and write the files."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("--answers-4b", default=cmp.RUN_4B)
    parser.add_argument("--answers-35b", default=cmp.RUN_35B)
    parser.add_argument("--test900-4b", default=TEST900_4B)
    parser.add_argument("--test900-35b", default=cmp.TEST900_35B)
    parser.add_argument("--hand", default=cmp.HAND_CSV)
    parser.add_argument("--filter3", default=cmp.FILTER3)
    parser.add_argument("--harvest", default=cmp.HARVEST)
    parser.add_argument("--labels", default=cmp.LABELS)
    parser.add_argument("--out", required=True)
    parser.add_argument("--results", required=True)
    args = parser.parse_args()
    out = {name: fresh_path(os.path.join(args.out, name)) for name in
           ["fair_scores_249.csv", "full_runs_disagree_249.csv", "bc_data_dropped_by_35b_split.csv",
            "followups_report.md"]}
    results = {name: fresh_path(os.path.join(args.results, name)) for name in
               ["sample40_records.txt", "sample40_answers.csv"]}

    # The keep sets, made exactly as in compare_filter2_35b.py.
    filter3, hand = cmp.read_filter3(args.filter3), cmp.read_hand_decisions(args.hand)
    answers_4b, answers_35b = cmp.read_answers(args.answers_4b), cmp.read_answers(args.answers_35b)
    kept_4b = cmp.four_b_keep(args.answers_4b, filter3)
    kept_35b, _ = cmp.keep_set(answers_35b, hand)

    # 1. The fair score: the reviewed records the full runs hold (every
    # harvest record is in both full runs).
    labels = pd.read_csv(args.labels, encoding="utf-8-sig")
    labels["record_id"] = labels["record_id"].astype(str)
    labels = reviewed_in_harvest(labels, set(answers_35b.index))
    ids = list(labels.index)
    runs = {"4b test900 run 1": verdicts_from_answers(cmp.read_answers(args.test900_4b), ids),
            "4b full run": verdicts_from_keep(kept_4b, ids),
            "35b test900": verdicts_from_answers(cmp.read_answers(args.test900_35b), ids),
            "35b full run": verdicts_from_keep(kept_35b, ids)}
    fair = fair_rows(labels, runs)
    disagree = full_runs_disagree(labels, runs["4b full run"], runs["35b full run"], answers_4b, answers_35b)

    # 2. The sample of 40 of the bc_data records the 35b run drops.
    dropped = bc_data_dropped(filter3, kept_35b)
    sample = draw_sample(dropped)
    harvest = read_records(args.harvest, set(sample)).set_index("id", drop=False)
    split = split_counts(dropped, answers_35b, filter3)

    os.makedirs(args.out, exist_ok=True)
    os.makedirs(args.results, exist_ok=True)
    fair.to_csv(out["fair_scores_249.csv"], index=False)
    disagree.to_csv(out["full_runs_disagree_249.csv"], index=False)
    split.to_csv(out["bc_data_dropped_by_35b_split.csv"], index=False)
    with open(results["sample40_records.txt"], "w", encoding="utf-8") as handle:
        handle.write("\n\n".join(record_block(n, r, harvest.loc[r]) for n, r in enumerate(sample, start=1)) + "\n")
    sample_answers(sample, answers_4b, answers_35b, hand, filter3).to_csv(results["sample40_answers.csv"],
                                                                         index=False)
    report(out["followups_report.md"], fair, disagree, split, len(sample), len(dropped), args.results)

    print(fair.to_string(index=False, formatters={"precision": "{:.1%}".format, "recall": "{:.1%}".format,
                                                   "keeps": "{:,.0f}".format, "junk_kept": "{:,.0f}".format,
                                                   "real_lost": "{:,.0f}".format}))
    print(f"\n{len(disagree)} reviewed records where the full runs disagree; {len(dropped)} bc_data records dropped")
    print(f"wrote {args.out} and {args.results}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
