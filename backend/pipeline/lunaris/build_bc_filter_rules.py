"""
build_bc_filter_rules.py

Run the BC filter's rules (bc_filter_rules.py) over the Lunaris records the
LLM kept as biodiversity data, and write one row per record.

Input:
  --llm-results  the LLM run's own output: the merged trial CSV written by
                 pipeline/lunaris/llm/merge_results.py (one row per record,
                 the model's facts, or model_error for a failed record).
                 Which records are biodiversity data is decided here with
                 decide() from exploration/llm/score_trial.py, the same rule
                 the 900-record test set was scored with (prompt v6).
                 Records whose answer failed count as biodiversity data only
                 if they are listed in FAILED_IN_FULL_RUN below; any other
                 failed record stops the run until someone has looked at it.
  --harvest      the Lunaris harvest (lunaris_full_harvest.parquet), for
                 each record's text and location fields
  --bc-boundary  BC's outline as WKT: the bc_boundary_gbif.wkt written by
                 pipeline/gbif/make_gbif_boundary.py. Read from disk; nothing
                 is downloaded.

Output (--out), one row per record:
  record_id, source, title
  bc_class     bc, not_bc or needs_llm (only solid evidence decides; see
               bc_filter_rules.py)
  decided_by   box, point, source (joined with "+"), or none
  evidence     a short note
  cross-check columns, which decide nothing: box_class, box_share,
  box_cover, box_size (the record's most BC box), point_class, place_hint
  and place_words (what the place field says), text_title, text_keywords
  and text_abstract (the BC words found in each part of the text)

When a record id appears more than once in the harvest, the row with the
latest datestamp is used, as in make_batches.py.

Run, from backend/:
    python pipeline/lunaris/build_bc_filter_rules.py \
        --llm-results data/lunaris_llm_runs/harvest_2026_10_20/trial_qwen3.5-4b-fir_v6_harvest_2026_10_20.csv \
        --bc-boundary /Users/lucia/Desktop/BCBN/Results/bc_boundary_gbif.wkt \
        --out data/lunaris_bc_filter/rules_2026_10.csv
"""

import argparse
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
# decide() lives with the test-set scoring, so the full run is judged by
# exactly the rule the test set was scored with.
sys.path.insert(0, os.path.join(HERE, "..", "..", "exploration", "llm"))
from bc_filter_rules import CLASSES, classify_record, load_rules  # noqa: E402
from score_trial import decide  # noqa: E402

DEFAULT_HARVEST = "exploration/lunaris/harvest_2026_10_20/lunaris_full_harvest.parquet"
# The records whose LLM answer failed in the 2026-10-20 full run. All three
# were runaway answers: the model wrote about 28,000 characters without
# closing its JSON, even with the larger context, so there are no facts to
# apply decide() to. Read by hand they are clearly biodiversity data (two
# copies of an evening primrose evolution dataset, and a Beaufort Sea marine
# fishes survey), so they are counted as biodiversity data.
FAILED_IN_FULL_RUN = [
    "oai:datadryad.org:doi:10.5061/dryad.dm690",
    "oai:borealisdata.ca-dataverse-ubc:152582_150149",
    "oai:open.canada.ca:219b17c5-0f12-437b-ab82-7f3cfb75ad8e",
]


def biodiversity_ids(llm_results_path, failed_ids=FAILED_IN_FULL_RUN):
    """
    Return the record ids the LLM run counts as biodiversity data, and how
    many came from each route, as (ids, answered_yes, failed_kept).

    Answered records go through decide(), exactly as score_trial.py scores
    the test set: rows with a model_error are set aside first, and decide()
    reads each row's facts. A failed record is added only if it is in
    failed_ids; a failed record that is not stops with an error, so a new
    failure is never kept or dropped without a person deciding.
    """
    results = pd.read_csv(llm_results_path, encoding="utf-8-sig", low_memory=False)
    results["record_id"] = results["record_id"].astype(str)
    has_error = results["model_error"].notna() if "model_error" in results.columns else False
    failed = set(results.loc[has_error, "record_id"])
    unexpected = failed - set(failed_ids)
    if unexpected:
        raise SystemExit(f"ERROR: {len(unexpected)} records in {llm_results_path} have no LLM answer and "
                         f"are not in FAILED_IN_FULL_RUN, for example {sorted(unexpected)[:3]}. Look at "
                         f"them, and list them there if they are biodiversity data.")
    answered = results[~has_error]
    yes = set(answered.loc[answered.apply(decide, axis=1) == "yes", "record_id"])
    kept_failed = failed & set(failed_ids)
    return yes | kept_failed, len(yes), len(kept_failed)


def read_records(harvest_path, wanted):
    """
    Return the harvest fields of the wanted records, one row per record id.

    Stops with an error if a record is missing from the harvest, so a wrong
    harvest file cannot quietly shrink the result.
    """
    columns = ["id", "datestamp", "title", "subjects", "abstract", "places", "points", "boxes"]
    harvest = pd.read_parquet(harvest_path, columns=columns)
    harvest = harvest[harvest["id"].isin(wanted)]
    # Several rows for one id: keep the latest datestamp, as make_batches.py does.
    harvest = (harvest.sort_values("datestamp", ascending=False, kind="stable")
                      .drop_duplicates("id"))
    missing = wanted - set(harvest["id"])
    if missing:
        raise SystemExit(f"ERROR: {len(missing)} records are not in {harvest_path}, "
                         f"for example {sorted(missing)[:3]}")
    return harvest


def print_summary(result):
    """
    Print the counts per class, the rules behind each class, and what the
    cross-check columns say about the records left for the geography prompt.
    """
    total = len(result)
    print(f"\n{total:,} records\n")
    print(f"{'class':16s} {'records':>8s} {'share':>6s}")
    for name in CLASSES:
        n = int((result["bc_class"] == name).sum())
        print(f"{name:16s} {n:8,} {n / total:6.1%}")
    print("\nrules behind each class (records; several rules joined with +)")
    for name in CLASSES:
        rules = result.loc[result["bc_class"] == name, "decided_by"].value_counts()
        print(f"  {name}")
        for rule, n in rules.items():
            print(f"    {rule:28s} {n:7,}")
    llm = result[result["bc_class"] == "needs_llm"]
    print(f"\nwhy the {len(llm):,} needs_llm records were not decided")
    disagree = llm["evidence"].str.startswith("disagree")
    print(f"  {'the deciding rules disagree':44s} {disagree.sum():7,}")
    for box_class in ["mostly_bc", "partly_bc", "huge"]:
        n = int(((llm["box_class"] == box_class) & ~disagree
                 & ~llm["evidence"].str.contains("longitude sign flipped")).sum())
        print(f"  {'only a ' + box_class + ' box':44s} {n:7,}")
    typo = llm["evidence"].str.contains("longitude sign flipped")
    print(f"  {'a point with a likely longitude sign typo':44s} {typo.sum():7,}")
    nothing = llm["evidence"] == "nothing that decides"
    print(f"  {'no box, point or listed catalogue':44s} {nothing.sum():7,}")
    print("\ncross-check hints among the needs_llm records (they decide nothing)")
    for hint in ["bc", "elsewhere", "canada"]:
        print(f"  {'place field says ' + hint:44s} {(llm['place_hint'] == hint).sum():7,}")
    text_bc = (llm[["text_title", "text_keywords", "text_abstract"]].fillna("") != "").any(axis=1)
    print(f"  {'text mentions BC (title, keywords or abstract)':44s} {text_bc.sum():7,}")


def main():
    """Classify the records and write the result."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("--llm-results", required=True,
                        help="the LLM run's merged trial CSV (from merge_results.py)")
    parser.add_argument("--harvest", default=DEFAULT_HARVEST)
    parser.add_argument("--bc-boundary", required=True,
                        help="BC's outline as WKT (bc_boundary_gbif.wkt from make_gbif_boundary.py)")
    parser.add_argument("--out", default="data/lunaris_bc_filter/rules_2026_10.csv")
    args = parser.parse_args()

    rules = load_rules(args.bc_boundary)
    wanted, answered_yes, failed_kept = biodiversity_ids(args.llm_results)
    records = read_records(args.harvest, wanted)
    print(f"{len(records):,} records: {answered_yes:,} the LLM kept (decide(), prompt v6) "
          f"plus {failed_kept} whose answer failed, counted by hand")

    rows = []
    for record in records.to_dict("records"):
        row = classify_record(record, rules)
        row["title"] = record["title"]
        rows.append(row)
    result = pd.DataFrame(rows)[["record_id", "source", "title", "bc_class", "decided_by", "evidence",
                                 "box_class", "box_share", "box_cover", "box_size", "point_class",
                                 "place_hint", "place_words", "text_title", "text_keywords", "text_abstract"]]

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    result.to_csv(args.out, index=False, encoding="utf-8")
    print_summary(result)
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
