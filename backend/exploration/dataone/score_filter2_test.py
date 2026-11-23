"""
score_filter2_test.py

Score the DataONE filter 2 test runs (the 200 label set records, prompt v6
variant v6_dataone) against Lucia's answer key, the same way Lunaris was
scored. Reads only; writes one CSV outside the repo.

Inputs:
  data/dataone/filter2_runs/fir_dataone_test200/                    qwen3.5:4b
  data/dataone/filter2_runs/fir_dataone_test200_qwen3.6-35b-a3b_nofa/
                                                    qwen3.6:35b-a3b, flash attention off
      each with its merged trial CSV (merge_results.py names it
      trial_<label>_v6_<run name on Fir>.csv; the label comes from run.json)
      and the job log in logs/
  ~/Desktop/BCBN/Results/dataone_label_set_2026-11-19/
      lucia_labels_final_and_comparison.csv   gold_biodiversity, by id
  data/dataone/harvest_2026_11_18/dataone_lunaris_match.parquet
      reuse_lunaris and the Lunaris filter 2 decision
  data/dataone/harvest_2026_11_18/dataone_groups.parquet   each record's group

Each model's facts become yes or no through score_trial.decide(), imported
from exploration/llm/score_trial.py and not changed. The trial CSVs are
read by read_trial (pipeline/dataone/filter2_common.py, shared with the
pipeline), which leaves out failed rows as score_trial.py does. The decide()
rule that answered (a to i, as in the label set README) is found by
rule_of(), which follows decide()'s order; the script stops if rule_of()
and decide() ever disagree.

  union  yes if either model says yes (the Lunaris keep rule)
  both   yes only if both models say yes

For comparison, the Lunaris figures are computed again here from the
Lunaris trial files and the reviewed labels
(exploration/llm/test_set_reviewed_harvest_2026_10.csv, reviewed_by_hand):
the 259 reviewed records with the test900 runs, and the 249 of them in the
2026-10-20 harvest with the full runs. Both plain counts and score_trial's
corpus-weighted precision and recall (weights_for, review_weight) are
given; the DataONE label set has no weights, so its figures are plain
counts.

Output: ~/Desktop/BCBN/Results/dataone_filter2_test_2026-11-20/test200_scores.csv,
one row per test record.

Run, from backend/:
    ../.venv/bin/python exploration/dataone/score_filter2_test.py
"""

import glob
import os
import re
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(BACKEND, "exploration", "llm"))
sys.path.insert(0, os.path.join(BACKEND, "pipeline", "dataone"))
# Reading a trial CSV, the rule letter and the test run folders are shared
# with the pipeline (pipeline/dataone/filter2_common.py).
from filter2_common import TEST_RUNS as RUNS  # noqa: E402
from filter2_common import find_trial, read_trial  # noqa: E402
from score_trial import score, weights_for  # noqa: E402

GOLD = os.path.expanduser("~/Desktop/BCBN/Results/dataone_label_set_2026-11-19/lucia_labels_final_and_comparison.csv")
HARVEST = os.path.join(BACKEND, "data", "dataone", "harvest_2026_11_18")
MATCH = os.path.join(HARVEST, "dataone_lunaris_match.parquet")
GROUPS = os.path.join(HARVEST, "dataone_groups.parquet")
OUT_DIR = os.path.expanduser("~/Desktop/BCBN/Results/dataone_filter2_test_2026-11-20")
OUT = os.path.join(OUT_DIR, "test200_scores.csv")
EXPECTED = 200

LUNARIS_LABELS = os.path.join(BACKEND, "exploration", "llm", "test_set_reviewed_harvest_2026_10.csv")
LUNARIS_RUNS = os.path.join(BACKEND, "data", "lunaris_llm_runs")
LUNARIS_TRIALS = {
    "test900": {"4b": os.path.join(LUNARIS_RUNS, "fir_test900", "trial_qwen3.5-4b-fir_v6_test900.csv"),
                "35b": os.path.join(LUNARIS_RUNS, "fir_test900_qwen3.6-35b-a3b_nofa",
                                    "trial_qwen3.6-35b-a3b-fir-nofa_v6_test900_qwen3.6-35b-a3b_nofa.csv")},
    "full harvest": {"4b": os.path.join(LUNARIS_RUNS, "harvest_2026_10_20",
                                        "trial_qwen3.5-4b-fir_v6_harvest_2026_10_20.csv"),
                     "35b": os.path.join(LUNARIS_RUNS, "fir_harvest_2026_10_20_qwen3.6-35b-a3b_nofa",
                                         "trial_qwen3.6-35b-a3b-fir-nofa_v6_harvest_2026_10_20_qwen3.6-35b-a3b_nofa.csv")},
}

FACTS = ["concerns_living_things", "topic", "experimental_animals", "extinct_only",
         "about_organisms_themselves", "ecological_purpose_stated"]
MORE = ["organisms", "organism_level", "form", "language", "model_reason"]
RULE_NAMES = {"a": "a experimental animals", "b": "b extinct only", "c": "c physical environment",
              "d": "d human health", "e": "e administrative", "f": "f not about living things",
              "g": "g forestry/agri/fisheries", "h": "h land and boundaries", "i": "i anything else (yes)"}

pd.set_option("display.width", 250)
pd.set_option("display.max_colwidth", 60)


def read_dataone():
    """
    Return one row per test record: row, id, datasource, title, gold, group,
    reuse_lunaris, the Lunaris filter 2 decision, and for each model its
    facts, verdict and rule. Stops unless every record was answered.
    """
    gold = pd.read_csv(GOLD, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    gold = gold[["row", "id", "datasource", "title", "gold_biodiversity"]].rename(columns={"gold_biodiversity": "gold"})
    if len(gold) != EXPECTED or gold["id"].nunique() != EXPECTED:
        sys.exit("STOPPED: the answer key does not hold 200 distinct ids")
    groups = pd.read_parquet(GROUPS, columns=["id", "group"]).set_index("id")["group"]
    match = pd.read_parquet(MATCH, columns=["id", "reuse_lunaris", "lunaris_filter2_summary"]).set_index("id")
    table = gold.copy()
    table["group"] = table["id"].map(groups)
    table["reuse_lunaris"] = table["id"].map(match["reuse_lunaris"]).fillna(False).astype(bool)
    table["lunaris_filter2"] = table["id"].map(match["lunaris_filter2_summary"]).fillna("")
    print("RUNS")
    for name, run_dir in RUNS.items():
        path, run = find_trial(run_dir)
        frame, failed = read_trial(path)
        answered = set(frame["record_id"])
        missing = set(table["id"]) - answered
        extra = answered - set(table["id"])
        print(f"   {name}: {os.path.relpath(path, BACKEND)}; model {run['model']}, variant "
              f"{run.get('prompt_variant', 'v6')}; answered {len(frame)}, failed {failed}, "
              f"missing {len(missing)}, not in the key {len(extra)}")
        if failed or missing or extra or frame["record_id"].duplicated().any():
            sys.exit(f"STOPPED: the {name} run did not answer exactly the 200 records")
        frame = frame.set_index("record_id")
        for column in FACTS + MORE + ["verdict", "rule"]:
            table[f"{name}_{column}"] = table["id"].map(frame[column])
    table["union"] = ((table["4b_verdict"] == "yes") | (table["35b_verdict"] == "yes")).map({True: "yes", False: "no"})
    table["both"] = ((table["4b_verdict"] == "yes") & (table["35b_verdict"] == "yes")).map({True: "yes", False: "no"})
    return table


def counts(predicted, truth):
    """Return plain (unweighted) accuracy, precision and recall for yes, and the false yes and false no counts."""
    p, t = predicted == "yes", truth == "yes"
    tp, fp, fn, tn = int((p & t).sum()), int((p & ~t).sum()), int((~p & t).sum()), int((~p & ~t).sum())
    return {"n": tp + fp + fn + tn, "accuracy": (tp + tn) / (tp + fp + fn + tn),
            "precision": tp / (tp + fp) if tp + fp else float("nan"),
            "recall": tp / (tp + fn) if tp + fn else float("nan"), "false_yes": fp, "false_no": fn}


def lunaris_scores():
    """
    Return the Lunaris comparison rows: for the 259 reviewed records (test900
    runs) and the 249 of them in the full harvest (full runs), plain counts
    and score_trial's corpus-weighted precision and recall, for 4b, 35b,
    union and both.
    """
    labels = pd.read_csv(LUNARIS_LABELS, encoding="utf-8-sig")
    labels["record_id"] = labels["record_id"].astype(str)
    labels = labels[labels["reviewed_by_hand"] == True]  # noqa: E712 (the column holds True and False)
    rows = []
    for setting, paths in LUNARIS_TRIALS.items():
        verdicts = {}
        for name, path in paths.items():
            frame, _ = read_trial(path)
            verdicts[name] = frame.set_index("record_id")["verdict"]
        common = sorted(set(labels["record_id"]) & set(verdicts["4b"].index) & set(verdicts["35b"].index))
        base = labels.set_index("record_id").loc[common]
        weight = weights_for(base)
        v = {k: s.loc[common] for k, s in verdicts.items()}
        v["union"] = ((v["4b"] == "yes") | (v["35b"] == "yes")).map({True: "yes", False: "no"})
        v["both"] = ((v["4b"] == "yes") & (v["35b"] == "yes")).map({True: "yes", False: "no"})
        for name in ["4b", "35b", "union", "both"]:
            plain = counts(v[name], base["is_biodiversity"])
            weighted = score(v[name], base["is_biodiversity"], weight)
            rows.append({"set": f"Lunaris {setting} ({len(common)})", "method": name, **plain,
                         "w_precision": weighted["precision"], "w_recall": weighted["recall"]})
    return pd.DataFrame(rows)


def short_facts(r, name):
    """Return one model's key facts for a record as a short text."""
    alive = "alive" if bool(r[f"{name}_concerns_living_things"]) else "not alive"
    return (f"{r[f'{name}_topic']}, {alive}, exp {r[f'{name}_experimental_animals']}, "
            f"extinct {r[f'{name}_extinct_only']}, about_org {r[f'{name}_about_organisms_themselves']}, "
            f"eco {r[f'{name}_ecological_purpose_stated']}")


def report(table):
    """Print points 1 to 4: scores, the disagreements, how often each rule fired, and the Lunaris reuse check."""
    truth = table["gold"]
    rows = [{"set": f"DataONE test ({len(table)})", "method": m, **counts(table[c], truth)}
            for m, c in (("4b", "4b_verdict"), ("35b", "35b_verdict"), ("union", "union"), ("both", "both"))]
    scores = pd.concat([pd.DataFrame(rows), lunaris_scores()], ignore_index=True)
    for c in ("accuracy", "precision", "recall", "w_precision", "w_recall"):
        scores[c] = (scores[c] * 100).round(1)
    print("\n1. SCORES (plain counts; w_ = Lunaris corpus-weighted, as score_trial.py)")
    print(scores.to_string(index=False))

    wrong = table[(table["4b_verdict"] != truth) | (table["35b_verdict"] != truth)].copy()
    wrong = wrong.sort_values("row", key=lambda c: c.astype(int))
    print(f"\n2. RECORDS WHERE A MODEL DISAGREES WITH THE KEY: {len(wrong)} "
          f"(4b wrong {int((table['4b_verdict'] != truth).sum())}, 35b wrong {int((table['35b_verdict'] != truth).sum())}, "
          f"both wrong {int(((table['4b_verdict'] != truth) & (table['35b_verdict'] != truth)).sum())})")
    for r in wrong.to_dict("records"):
        title = " ".join(str(r["title"]).split())[:75]
        print(f"   row {r['row']} | {r['id'][:60]} | {r['datasource']} | {r['group']}\n      {title}")
        print(f"      gold {r['gold']} | 4b {r['4b_verdict']} (rule {r['4b_rule']}: {short_facts(r, '4b')})")
        print(f"      {'':9}| 35b {r['35b_verdict']} (rule {r['35b_rule']}: {short_facts(r, '35b')})")

    print("\n3. HOW OFTEN EACH decide() RULE FIRED")
    fired = pd.DataFrame({name: table[f"{name}_rule"].value_counts() for name in ("4b", "35b")})
    fired = fired.reindex(list(RULE_NAMES)).fillna(0).astype(int)
    fired.index = [RULE_NAMES[k] for k in fired.index]
    for name in ("4b", "35b"):
        right = table[f"{name}_verdict"] == truth
        fired[f"{name}_wrong"] = [int((~right & (table[f"{name}_rule"] == k)).sum()) for k in RULE_NAMES]
    print(fired.to_string())

    reuse = table[table["reuse_lunaris"]]
    print(f"\n4. THE {len(reuse)} TEST RECORDS THAT ALSO REUSE A LUNARIS RESULT")
    view = reuse[["row", "id", "datasource", "gold", "lunaris_filter2", "4b_verdict", "35b_verdict", "union"]]
    print(view.to_string(index=False))
    for name in ("4b_verdict", "35b_verdict", "union"):
        print(f"   {name}: agrees with Lunaris on {int((reuse[name] == reuse['lunaris_filter2']).sum())} of {len(reuse)}; "
              f"with the key on {int((reuse[name] == reuse['gold']).sum())}")
    print(f"   Lunaris decision agrees with the key on {int((reuse['lunaris_filter2'] == reuse['gold']).sum())} of {len(reuse)}")
    return scores, wrong


def speed_from_logs():
    """Point 5: print each run's speed from its job log (the summary lines run_batch.py writes)."""
    print("\n5. SPEED, FROM THE JOB LOGS")
    for name, run_dir in RUNS.items():
        for log in sorted(glob.glob(os.path.join(run_dir, "logs", "lunaris-llm_*.out"))):
            text = open(log, encoding="utf-8").read()
            asked = re.search(r"asked this run: (.*)", text)
            per_call = re.search(r"seconds per call: (.*)", text)
            slice_ = re.search(r"slice\s+(\S+)", text)
            flash = re.search(r"runner flash attention: (.*)", text)
            print(f"   {name}: {os.path.basename(log)}; {slice_.group(1) if slice_ else '?'}; "
                  f"{asked.group(1) if asked else '?'}; seconds per call {per_call.group(1) if per_call else '?'}; "
                  f"flash attention {flash.group(1) if flash else '?'}")


def write_table(table):
    """Write the per-record table (all 200 rows) to OUT."""
    columns = (["row", "id", "datasource", "title", "group", "gold"]
               + [f"{m}_{c}" for m in ("4b", "35b") for c in FACTS + MORE + ["verdict", "rule"]]
               + ["union", "both", "reuse_lunaris", "lunaris_filter2"])
    os.makedirs(OUT_DIR, exist_ok=True)
    table[columns].to_csv(OUT + ".tmp", index=False, encoding="utf-8-sig")
    os.replace(OUT + ".tmp", OUT)
    print(f"\nwrote {len(table)} rows -> {OUT}")


def main():
    """Read the runs and the key, check every record was answered, report and write the per-record table."""
    table = read_dataone()
    report(table)
    speed_from_logs()
    write_table(table)
    return 0


if __name__ == "__main__":
    sys.exit(main())
