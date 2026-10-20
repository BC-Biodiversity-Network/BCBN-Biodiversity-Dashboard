"""
Compare two runs of the LLM filter record by record, such as the Mac and Fir.

score_trial.py gives each run's precision and recall. Those two numbers are a
poor way to tell whether two runs agree: with the review weights, flipping one
heavy record from the dropped pool moves precision by up to about 10 points.
So this looks at the records themselves:

  how often each fact field agrees (topic, concerns_living_things and so on)
  how often the final yes or no agrees, on all records and on the reviewed ones
  each run's weighted precision and recall on the reviewed records
  every record whose yes or no differs, written to a CSV to read

It then checks the agreed "close enough" gates for a Fir run against run A:

  final yes or no agrees on at least 95% of all records
  concerns_living_things agrees on at least 95% of all records
  on the reviewed records where A and B disagree, B is right roughly as often
      as A: on at least 40% of them, or on no more than 2 fewer than A
  recall within 2 points and precision within 3 points of run A, with the
      single heaviest reviewed record where they disagree set aside

Why the last two are worded this way. With only 259 reviewed records and the
review weights, one heavy record can move precision by about 10 points (on
2026-10-19 one record of weight 1,744 moved it by 10.7), and a fixed cap on
reviewed disagreements (95%, so 13 records) is crossed by two borderline
records either way. Setting the heaviest disagreement aside, and asking
whether the disagreements favour A or B rather than how many there are,
judges the runs on what they get right. The plain agreement on the reviewed
records and the unadjusted scores are still printed, for reading.

The yes or no comes from score_trial.decide(), so the rules are the same ones
the scores use. This is an evaluation tool for the Mac and is not needed on
the cluster.

Usage, from backend/:

    python exploration/llm/compare_runs.py \
        --a exploration/llm/trial_qwen3.5-4b_v6_harvest_2026_10.csv \
        --b data/lunaris_llm_runs/test900/trial_qwen3.5-4b-fir_v6_test900.csv \
        --out data/lunaris_llm_runs/test900/compare_mac_vs_fir.csv
"""

import argparse
import sys

import pandas as pd

from score_trial import decide, score, weights_for

# The fact fields compared one by one, in the order they are printed.
FIELDS = [
    "concerns_living_things",
    "topic",
    "organism_level",
    "about_organisms_themselves",
    "ecological_purpose_stated",
    "experimental_animals",
    "extinct_only",
    "form",
    "language",
    "organisms",
]

# The agreed gates for "close enough" (see the top of the file).
MIN_VERDICT_AGREEMENT = 95.0
MIN_LIVING_AGREEMENT = 95.0
MAX_RECALL_GAP = 2.0
MAX_PRECISION_GAP = 3.0
# On the reviewed disagreements, B must be right on at least this share of
# them, or on no more than MAX_RIGHT_SHORTFALL fewer than A.
MIN_SHARE_B_RIGHT = 0.40
MAX_RIGHT_SHORTFALL = 2


def load_trial(path):
    """
    Read a trial CSV the way score_trial.py does and add the yes or no verdict.

    Records that failed (a model_error row) are left out and counted. Returns
    the answered records indexed by record_id, the run's model label and the
    number of failed records.
    """
    frame = pd.read_csv(path, encoding="utf-8-sig")
    frame["record_id"] = frame["record_id"].astype(str)
    failed = 0
    if "model_error" in frame.columns:
        failed = int(frame["model_error"].notna().sum())
        frame = frame[frame["model_error"].isna()]
    label = frame["model"].dropna().iloc[0] if len(frame) else path
    frame = frame.copy()
    frame["verdict"] = frame.apply(decide, axis=1)
    return frame.set_index("record_id"), label, failed


def comparable(field, series):
    """
    Put one field into a form where equal answers compare equal.

    Text is lower-cased and trimmed. The organisms field is compared as a set
    of names, so the same names in another order count as agreeing.
    """
    if field == "organisms":
        return series.fillna("").map(
            lambda text: tuple(sorted({name.strip().lower() for name in str(text).split(";") if name.strip()})))
    if field == "concerns_living_things":
        return series.map(lambda value: str(value).strip().lower())
    return series.fillna("").astype(str).str.strip().str.lower()


def heaviest_flip(differs_reviewed, weight, labels):
    """
    Return the reviewed record where A and B disagree that weighs the most, or None.

    Ties (many records share a weight) go to the lowest row number, so the
    choice is the same every time.
    """
    flips = differs_reviewed[differs_reviewed].index
    if not len(flips):
        return None
    order = pd.DataFrame({"weight": weight.reindex(flips), "row": labels["row"].reindex(flips)})
    return order.sort_values(["weight", "row"], ascending=[False, True]).index[0]


def gate(text, passed):
    """Print one gate with PASS or CHECK in front, and return whether it passed."""
    print(f"  {'PASS ' if passed else 'CHECK'}  {text}")
    return passed


def main():
    """Compare the two runs, print the summary and the gates, and write the differing records."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--a", required=True, help="the reference run, for example the Mac trial CSV")
    parser.add_argument("--b", required=True, help="the run to check, for example the merged Fir CSV")
    parser.add_argument("--labels", default="exploration/llm/test_set_reviewed_harvest_2026_10.csv",
                        help="the labelled test set with reviewed_by_hand and is_biodiversity")
    parser.add_argument("--out", required=True, help="CSV to write the records whose yes or no differs")
    args = parser.parse_args()

    a, name_a, failed_a = load_trial(args.a)
    b, name_b, failed_b = load_trial(args.b)
    if name_a == name_b:
        name_a, name_b = f"{name_a} (A)", f"{name_b} (B)"
    common = [record_id for record_id in a.index if record_id in b.index]
    a, b = a.loc[common], b.loc[common]

    print(f"A: {name_a}  {args.a}")
    print(f"B: {name_b}  {args.b}")
    print(f"compared on {len(common):,} records answered by both "
          f"(failed: {failed_a} in A, {failed_b} in B)")

    # Field by field agreement.
    print()
    print("FACT FIELDS, share of records where A and B give the same answer")
    agreement = {}
    for field in FIELDS:
        same = comparable(field, a[field]) == comparable(field, b[field])
        agreement[field] = same.mean() * 100
        print(f"  {field:28s} {agreement[field]:6.1f}%   ({(~same).sum():,} differ)")

    # The final yes or no.
    differs = a["verdict"] != b["verdict"]
    verdict_all = (~differs).mean() * 100
    print()
    print(f"FINAL YES OR NO agrees on {verdict_all:.1f}% of all {len(common):,} records "
          f"({differs.sum():,} differ)")

    labels = pd.read_csv(args.labels, encoding="utf-8-sig")
    labels["record_id"] = labels["record_id"].astype(str)
    labels = labels.set_index("record_id")
    reviewed = labels[labels["reviewed_by_hand"] == True]  # noqa: E712 (column holds True/False)
    reviewed = reviewed.loc[[record_id for record_id in reviewed.index if record_id in common]]
    weight = weights_for(reviewed)
    truth = reviewed["is_biodiversity"]
    differs_reviewed = differs.loc[reviewed.index]
    verdict_reviewed = (~differs_reviewed).mean() * 100
    print(f"  on the {len(reviewed):,} reviewed records: {verdict_reviewed:.1f}% "
          f"({differs_reviewed.sum():,} differ)")

    score_a = score(a.loc[reviewed.index, "verdict"], truth, weight)
    score_b = score(b.loc[reviewed.index, "verdict"], truth, weight)
    print()
    print("WEIGHTED SCORES ON THE REVIEWED RECORDS")
    for name, s in [(name_a, score_a), (name_b, score_b)]:
        print(f"  {name:28s} precision {s['precision'] * 100:5.1f}%   recall {s['recall'] * 100:5.1f}%")
    print(f"  {'B minus A':28s} precision {(score_b['precision'] - score_a['precision']) * 100:+5.1f}    "
          f"recall {(score_b['recall'] - score_a['recall']) * 100:+5.1f}")

    # The same, with the heaviest reviewed disagreement set aside: B is given
    # A's answer on that one record, so it counts for neither run.
    heaviest = heaviest_flip(differs_reviewed, weight, labels)
    b_adjusted = b.loc[reviewed.index, "verdict"].copy()
    if heaviest is not None:
        b_adjusted[heaviest] = a.loc[heaviest, "verdict"]
    score_b_adjusted = score(b_adjusted, truth, weight)
    precision_gap = (score_b_adjusted["precision"] - score_a["precision"]) * 100
    recall_gap = (score_b_adjusted["recall"] - score_a["recall"]) * 100
    if heaviest is not None:
        print(f"  setting aside the heaviest disagreement, row {labels.at[heaviest, 'row']} "
              f"(weight {weight[heaviest]:,.0f}, labelled {truth[heaviest]}, "
              f"A {a.loc[heaviest, 'verdict']}, B {b.loc[heaviest, 'verdict']}):")
        print(f"  {name_b:28s} precision {score_b_adjusted['precision'] * 100:5.1f}%   "
              f"recall {score_b_adjusted['recall'] * 100:5.1f}%")
        print(f"  {'B minus A':28s} precision {precision_gap:+5.1f}    recall {recall_gap:+5.1f}")

    # The records worth reading.
    out = pd.DataFrame(index=differs[differs].index)
    out.index.name = "record_id"
    for column in ["row", "title", "stratum", "reviewed_by_hand", "is_biodiversity"]:
        out[column] = labels[column].reindex(out.index) if column in labels.columns else None
    out["weight"] = weight.reindex(out.index)
    out["verdict_a"] = a.loc[out.index, "verdict"]
    out["verdict_b"] = b.loc[out.index, "verdict"]
    is_reviewed = out["reviewed_by_hand"] == True  # noqa: E712
    out["right_one"] = None
    out.loc[is_reviewed, "right_one"] = out.loc[is_reviewed].apply(
        lambda r: "A" if r["verdict_a"] == r["is_biodiversity"] else "B", axis=1)
    for field in ["topic", "concerns_living_things", "about_organisms_themselves",
                  "ecological_purpose_stated", "experimental_animals", "model_reason"]:
        out[f"{field}_a"] = a.loc[out.index, field]
        out[f"{field}_b"] = b.loc[out.index, field]
    # Reviewed records first, heaviest first, as they move the scores most.
    out = out.sort_values(["reviewed_by_hand", "weight"], ascending=[False, False], na_position="last")
    out.to_csv(args.out, encoding="utf-8-sig")
    print()
    print(f"wrote the {len(out):,} records whose yes or no differs to {args.out}")
    right = out.loc[is_reviewed, "right_one"].value_counts()
    right_a, right_b = int(right.get("A", 0)), int(right.get("B", 0))
    if is_reviewed.any():
        print(f"  of the reviewed ones, A is right on {right_a}, B on {right_b}")
    flips_reviewed = right_a + right_b
    split_ok = (flips_reviewed == 0 or right_b >= MIN_SHARE_B_RIGHT * flips_reviewed
                or right_a - right_b <= MAX_RIGHT_SHORTFALL)

    print()
    print("GATES FOR CLOSE ENOUGH")
    results = [
        gate(f"final yes or no agrees on {verdict_all:.1f}% of all records "
             f"(need {MIN_VERDICT_AGREEMENT:.0f}%)", verdict_all >= MIN_VERDICT_AGREEMENT),
        gate(f"concerns_living_things agrees on {agreement['concerns_living_things']:.1f}% "
             f"(need {MIN_LIVING_AGREEMENT:.0f}%)",
             agreement["concerns_living_things"] >= MIN_LIVING_AGREEMENT),
        gate(f"reviewed disagreements split evenly enough: B right on {right_b} of "
             f"{flips_reviewed}, A on {right_a} (need B on {MIN_SHARE_B_RIGHT:.0%} or "
             f"at most {MAX_RIGHT_SHORTFALL} fewer than A)", split_ok),
        gate(f"recall {recall_gap:+.1f} points from A, heaviest disagreement set aside "
             f"(allowed {MAX_RECALL_GAP:.0f})", abs(recall_gap) <= MAX_RECALL_GAP),
        gate(f"precision {precision_gap:+.1f} points from A, heaviest disagreement set aside "
             f"(allowed {MAX_PRECISION_GAP:.0f})", abs(precision_gap) <= MAX_PRECISION_GAP),
    ]
    print(f"  (for reading, not a gate: the reviewed records agree on {verdict_reviewed:.1f}%)")
    if failed_a or failed_b:
        results.append(gate(f"no failed records (A {failed_a}, B {failed_b})", False))
    print()
    if all(results):
        print("All gates pass.")
    else:
        print("Some gates need a look. Read the reviewed records in the CSV, heaviest first.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
