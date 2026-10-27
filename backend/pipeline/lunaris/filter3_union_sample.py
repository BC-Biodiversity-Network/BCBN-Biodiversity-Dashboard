"""
filter3_union_sample.py

An independent check of the union build (build_filter3_union.py): a sample
of the records the front end would gain from the 35b run, that is
frontend_group bc_data, filter2_keep_source 35b_only, one row per copy group
(dup_show). Same format as the earlier samples. Reads only; writes two new
files and stops if either already exists:

  sample30_union_new_bcdata_records.txt   numbered blocks [01] to [30], the
                                          harvest fields only, nothing from
                                          the models or from us
  sample30_union_new_bcdata_answers.csv   the same numbers: the 35b filter 2
                                          verdict and facts, the filter 3
                                          final class, how it was decided and
                                          the BC evidence

Run, from backend/:
    python pipeline/lunaris/filter3_union_sample.py \\
        --results /Users/lucia/Desktop/BCBN/Results/filter2_35b_compare_2026-10-27
"""

import argparse
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import compare_filter2_35b as cmp  # noqa: E402
import filter2_35b_check_new as chk  # noqa: E402
import filter2_35b_followups as fu  # noqa: E402
from build_bc_filter_rules import read_records  # noqa: E402

DEFAULT_UNION = "data/lunaris_bc_filter/filter3_final_union/filter3_merged.csv"
SAMPLE_SIZE = 30
# The filter 3 columns that say how a record was placed in BC, with the names
# they get in the answers file.
FILTER3_COLUMNS = {
    "final_class": "filter3_final_class", "decided_by": "filter3_decided_by",
    "rule_name": "filter3_rule_name", "rule_evidence": "filter3_rule_evidence",
    "in_bc_used": "filter3_model_in_bc", "evidence": "filter3_model_evidence",
    "reason": "filter3_model_reason", "note": "filter3_note",
}


def union_pool(table):
    """Return the record ids, sorted, that are bc_data, kept only by 35b, and the shown row of their copy group."""
    wanted = ((table["frontend_group"] == "bc_data") & (table["filter2_keep_source"] == "35b_only")
              & (table["dup_show"].astype(str) == "True"))
    return sorted(table.loc[wanted, "record_id"])


def union_answers(sample, answers_35b, hand, table):
    """
    Return the answers file's rows: the number, the id, the 35b run's filter
    2 verdict and facts, then the filter 3 class, decided_by and the BC
    evidence (FILTER3_COLUMNS), every value as text.
    """
    rows_by_id = table.set_index("record_id")
    rows = []
    for number, record_id in enumerate(sample, start=1):
        row = {"number": f"{number:02d}", "record_id": record_id}
        row.update(chk.answer_columns(answers_35b, hand, record_id, "35b"))
        for column, name in FILTER3_COLUMNS.items():
            row[name] = cmp.plain(rows_by_id.at[record_id, column])
        rows.append(row)
    return pd.DataFrame(rows)


def main():
    """Draw the sample and write the records and the answers."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("--union", default=DEFAULT_UNION, help="filter3_final_union/filter3_merged.csv")
    parser.add_argument("--answers-35b", default=cmp.RUN_35B)
    parser.add_argument("--hand", default=cmp.HAND_CSV)
    parser.add_argument("--harvest", default=cmp.HARVEST)
    parser.add_argument("--results", required=True)
    args = parser.parse_args()
    paths = {name: fu.fresh_path(os.path.join(args.results, name)) for name in
             ["sample30_union_new_bcdata_records.txt", "sample30_union_new_bcdata_answers.csv"]}

    table = pd.read_csv(args.union, dtype=str, keep_default_na=False)
    pool = union_pool(table)
    sample = fu.draw_sample(pool, SAMPLE_SIZE)
    answers_35b, hand = cmp.read_answers(args.answers_35b), cmp.read_hand_decisions(args.hand)
    harvest = read_records(args.harvest, set(sample)).set_index("id", drop=False)

    with open(paths["sample30_union_new_bcdata_records.txt"], "w", encoding="utf-8") as handle:
        handle.write("\n\n".join(fu.record_block(n, r, harvest.loc[r]) for n, r in enumerate(sample, start=1)) + "\n")
    answers = union_answers(sample, answers_35b, hand, table)
    answers.to_csv(paths["sample30_union_new_bcdata_answers.csv"], index=False)
    print(f"{len(pool)} records in the pool; {len(sample)} drawn")
    print(answers["filter3_decided_by"].value_counts().to_string())
    print(answers["filter3_final_class"].value_counts().to_string())
    print(f"wrote {', '.join(paths)} in {args.results}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
