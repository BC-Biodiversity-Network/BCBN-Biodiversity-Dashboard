"""
filter2_35b_check_new.py

A second independent check, in the same format as filter2_35b_followups.py.
Reads only; writes new files, and stops if any of them already exists.

1. A sample of 40 records that only the 35b run keeps (the newly kept):
   25 from those step 1's rules call bc and 15 from those left for the
   geography prompt (needs_llm), drawn with a fixed seed and then shuffled
   together, so a record's number does not give its pool away.
     sample40_new35b_records.txt   numbered blocks [01] to [40], as harvested,
                                   nothing from the models or from us
     sample40_new35b_answers.csv   the same numbers: the pool, and each run's
                                   verdict and facts

2. A copy of the earlier sample40_answers.csv with three more facts for each
   run (concerns_living_things, experimental_animals, form) and the rule of
   decide() that turned the 35b answer into no, saved as
   sample40_answers_full.csv. The original is only read.

Run, from backend/:
    python pipeline/lunaris/filter2_35b_check_new.py \\
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
import filter2_35b_followups as fu  # noqa: E402
from build_bc_filter_rules import read_records  # noqa: E402
from score_trial import decide  # noqa: E402

RULES_NEW = "data/lunaris_llm_runs/filter2_35b_compare/rules_newly_kept_35b.csv"
# How many records to draw from each pool, by step 1 class.
POOLS = {"bc": 25, "needs_llm": 15}
POOL_NAMES = {"bc": "step1 bc", "needs_llm": "needs_llm"}
# The facts shown for each run, in this order.
FACTS = ["topic", "concerns_living_things", "about_organisms_themselves", "ecological_purpose_stated",
         "experimental_animals", "form", "model_reason"]


# --- Which rule of decide() decided ----------------------------------------

def decide_rule(record):
    """
    Return the rule of decide() (exploration/llm/score_trial.py) that decided
    a model answer, checked in decide()'s own order:

      experimental animals    experimental_animals is yes
      extinct only            extinct_only is yes
      physical environment    topic is physical_environment
      human health            topic is human_health
      administrative          topic is administrative
      not living things       concerns_living_things is not true
      criterion 2             forestry, agriculture or fisheries, and not
                              about the organisms themselves
      criterion 3             land and boundaries, with no ecological
                              purpose stated
      kept                    none of these, so decide() says yes

    The caller checks this agrees with decide() itself.
    """
    topic = record.get("topic")
    if record.get("experimental_animals") == "yes":
        return "experimental animals"
    if record.get("extinct_only") == "yes":
        return "extinct only"
    if topic == "physical_environment":
        return "physical environment"
    if topic == "human_health":
        return "human health"
    if topic == "administrative":
        return "administrative"
    if not bool(record.get("concerns_living_things")):
        return "not living things"
    if topic == "forestry_agriculture_fisheries" and record.get("about_organisms_themselves") != "yes":
        return "criterion 2 (not about the organisms)"
    if topic == "land_and_boundaries" and record.get("ecological_purpose_stated") != "yes":
        return "criterion 3 (no ecological purpose)"
    return "kept"


def rule_for(answers, record_id):
    """
    Return the rule that decided a record's answer, or "failed" for a failed
    record. Stops if the rule disagrees with decide(), so the named rule is
    always the one that really decided.
    """
    row = answers.loc[record_id]
    if row["verdict"] == "failed":
        return "failed"
    rule = decide_rule(row)
    if (rule == "kept") != (decide(row) == "yes"):
        raise SystemExit(f"ERROR: the rule found for {record_id} ({rule}) does not match decide()")
    return rule


# --- The sample of newly kept records --------------------------------------

def draw_pools(rules, pools=POOLS, seed=cmp.SEED):
    """
    Return (record id, pool) pairs: from each step 1 class in pools, that many
    records drawn from its sorted ids, one random generator with a fixed seed
    for all draws, then all of them shuffled together with it.
    """
    generator = random.Random(seed)
    picked = []
    for name, size in pools.items():
        ids = sorted(rules.loc[rules["bc_class"] == name, "record_id"])
        picked += [(record_id, name) for record_id in generator.sample(ids, size)]
    generator.shuffle(picked)
    return picked


def answer_columns(answers, hand, record_id, name):
    """Return one run's verdict and facts for a record, as columns ending in _<name>."""
    columns = {f"verdict_{name}": cmp.answer_text(answers, hand, record_id)}
    for fact in FACTS:
        columns[f"{fact.replace('model_', '')}_{name}"] = cmp.plain(answers.loc[record_id, fact])
    return columns


def new_sample_answers(picked, answers_4b, answers_35b, hand):
    """Return sample40_new35b_answers.csv's rows: number, id, pool, then each run's verdict and facts."""
    rows = []
    for number, (record_id, pool) in enumerate(picked, start=1):
        row = {"number": f"{number:02d}", "record_id": record_id, "pool": POOL_NAMES[pool]}
        row.update(answer_columns(answers_4b, hand, record_id, "4b"))
        row.update(answer_columns(answers_35b, hand, record_id, "35b"))
        rows.append(row)
    return pd.DataFrame(rows)


# --- The fuller copy of the earlier sample ---------------------------------

def fuller_answers(earlier, answers_4b, answers_35b, hand):
    """
    Return the earlier sample40_answers.csv (every value as text) with each
    run's verdict and full facts in FACTS order, the filter 3 columns as they
    were, and the rule that decided the 35b answer. Stops if a value the
    earlier file already held would come out different.
    """
    rows = []
    for row in earlier.to_dict("records"):
        record_id = row["record_id"]
        new = {"number": row["number"], "record_id": record_id}
        new.update(answer_columns(answers_4b, hand, record_id, "4b"))
        new.update(answer_columns(answers_35b, hand, record_id, "35b"))
        new["filter3_final_class"] = row["filter3_final_class"]
        new["filter3_frontend_group"] = row["filter3_frontend_group"]
        new["rule_35b"] = rule_for(answers_35b, record_id)
        changed = [c for c in earlier.columns if new[c] != row[c]]
        if changed:
            raise SystemExit(f"ERROR: {record_id}: {changed} would differ from the earlier file")
        rows.append(new)
    return pd.DataFrame(rows)


# --- Main ------------------------------------------------------------------

def main():
    """Draw the newly kept sample, write it, and write the fuller copy of the earlier answers."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("--answers-4b", default=cmp.RUN_4B)
    parser.add_argument("--answers-35b", default=cmp.RUN_35B)
    parser.add_argument("--hand", default=cmp.HAND_CSV)
    parser.add_argument("--filter3", default=cmp.FILTER3)
    parser.add_argument("--rules-new", default=RULES_NEW, help="rules_newly_kept_35b.csv")
    parser.add_argument("--harvest", default=cmp.HARVEST)
    parser.add_argument("--results", required=True)
    args = parser.parse_args()
    paths = {name: fu.fresh_path(os.path.join(args.results, name)) for name in
             ["sample40_new35b_records.txt", "sample40_new35b_answers.csv", "sample40_answers_full.csv"]}
    earlier_path = os.path.join(args.results, "sample40_answers.csv")

    filter3, hand = cmp.read_filter3(args.filter3), cmp.read_hand_decisions(args.hand)
    answers_4b, answers_35b = cmp.read_answers(args.answers_4b), cmp.read_answers(args.answers_35b)
    kept_4b = cmp.four_b_keep(args.answers_4b, filter3)
    kept_35b, _ = cmp.keep_set(answers_35b, hand)

    # 1. The newly kept: the step 1 rules file must hold exactly them.
    rules = pd.read_csv(args.rules_new, dtype=str, keep_default_na=False)
    if set(rules["record_id"]) != kept_35b - kept_4b:
        raise SystemExit(f"ERROR: {args.rules_new} does not hold exactly the records only the 35b run keeps")
    picked = draw_pools(rules)
    ids = [record_id for record_id, _ in picked]
    harvest = read_records(args.harvest, set(ids)).set_index("id", drop=False)
    blocks = [fu.record_block(n, r, harvest.loc[r]) for n, r in enumerate(ids, start=1)]
    answers = new_sample_answers(picked, answers_4b, answers_35b, hand)

    # 2. The fuller copy of the earlier sample.
    earlier = pd.read_csv(earlier_path, dtype=str, keep_default_na=False)
    fuller = fuller_answers(earlier, answers_4b, answers_35b, hand)

    with open(paths["sample40_new35b_records.txt"], "w", encoding="utf-8") as handle:
        handle.write("\n\n".join(blocks) + "\n")
    answers.to_csv(paths["sample40_new35b_answers.csv"], index=False)
    fuller.to_csv(paths["sample40_answers_full.csv"], index=False)

    print(answers["pool"].value_counts().to_string())
    print("\nthe rule that decided the 35b answer in the earlier sample:")
    print(fuller["rule_35b"].value_counts().to_string())
    print(f"\nwrote {', '.join(paths)} in {args.results}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
