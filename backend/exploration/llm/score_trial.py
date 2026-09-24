"""
Turn the model's facts into a yes or no, then score it against the labels.

run_trial.py only collected facts. This applies the five criteria to those
facts, which is where the biodiversity decision actually gets made. Keeping it
here means a change to the criteria costs one edit and one rerun of this
script, with no API calls at all.

Three things get scored against the same labels:

  the keyword filter   what it already does, from the stratum column
  each model           the rules below, applied to what the model reported

One thing to be careful about, and the reason this is not a plain crosstab:
the sample is stratified. 500 records were drawn from a pool of 18,931 and 400
from a pool of 104,548, so a sampled dropped record stands for about 261 real
records while a sampled kept record stands for about 38. Counting them equally
would make the dropped pool look 7 times smaller than it is. Every corpus level
figure below is weighted accordingly.

Usage:

    python exploration/llm/score_trial.py \
        --trial exploration/llm/trial_gemini-3.5-flash-lite.csv \
        --trial exploration/llm/trial_gemini-3.1-flash-lite.csv
"""

import argparse
import sys

import pandas as pd


# Pool sizes, used to weight the two strata back to the real corpus.
KEPT_POOL = 18931
DROPPED_POOL = 104548


def decide(record):
    """
    Apply the five criteria to one model answer and return "yes" or "no".

    This is the only place the biodiversity decision is made. Each block below
    is one of the criteria, in the order they are checked. If Evan draws a line
    somewhere else, this function is what changes, and nothing has to be asked
    of the model again.
    """
    topic = record.get("topic")
    alive = bool(record.get("concerns_living_things"))

    # Criterion 5. Human health and biomedical records are out, unless the
    # subject is wild organisms, in which case the model would not have called
    # the topic human_health in the first place.
    if topic == "human_health":
        return "no"

    # Criterion 4. Government administrative records are out.
    if topic == "administrative":
        return "no"

    # Criterion 1. Nothing living in it, nothing to index. This also covers
    # purely physical measurements, which the model marks as not concerning
    # living things unless the record itself mentions them.
    if not alive:
        return "no"

    # Criterion 2. Forestry, agriculture and fisheries count only when the
    # record is about the organisms, not about tenure, volumes or prices.
    if topic == "forestry_agriculture_fisheries":
        return "yes" if record.get("about_organisms_themselves") == "yes" else "no"

    # Criterion 3. Boundaries, land cover and maps count only when an
    # ecological or conservation purpose is stated.
    if topic == "land_and_boundaries":
        return "yes" if record.get("ecological_purpose_stated") == "yes" else "no"

    # Everything else that concerns living things is in.
    return "yes"


def decide_loose_rule3(record):
    """
    The same decision, but with criterion 3 relaxed.

    Under this version a boundary or land cover record counts as biodiversity
    data whenever it concerns living things, whether or not it spells out an
    ecological purpose. This exists so the sensitivity of the result to that
    one line can be reported rather than assumed away.
    """
    if record.get("topic") == "land_and_boundaries" and bool(record.get("concerns_living_things")):
        return "yes"
    return decide(record)


def decide_strict_rule2(record):
    """
    The same decision, but with criterion 2 tightened.

    Under this version forestry, agriculture and fisheries records are out
    entirely, even when they are about the organisms. Some of the records that
    turned up in the dropped pool sit exactly on this line.
    """
    if record.get("topic") == "forestry_agriculture_fisheries":
        return "no"
    return decide(record)


def weights_for(frame):
    """
    Give each record the number of real records it stands for.

    A record drawn from the kept pool represents 18,931 / 500 of the corpus, a
    record from the dropped pool 104,548 / 400. Without this the dropped pool,
    which is where the filter's misses live, would be counted as if it were the
    same size as the kept pool.

    If the labels came out of a hand review, that review covered some groups in
    full and others by sample, so merge_review.py worked out a weight per record
    that accounts for both stages. Where that column exists it is used instead.
    """
    if "review_weight" in frame.columns and frame["review_weight"].notna().all():
        return frame["review_weight"]
    kept_n = (frame["stratum"] == "kept").sum()
    dropped_n = (frame["stratum"] == "dropped").sum()
    if not kept_n or not dropped_n:
        raise ValueError("the scored records do not cover both strata")
    return frame["stratum"].map({
        "kept": KEPT_POOL / kept_n,
        "dropped": DROPPED_POOL / dropped_n,
    })


def score(predicted, truth, weight):
    """
    Work out precision, recall and the four cells, weighted to the corpus.

    predicted and truth are both series of "yes" and "no". weight says how many
    real records each row stands for.
    """
    p = predicted == "yes"
    t = truth == "yes"
    tp = weight[p & t].sum()
    fp = weight[p & ~t].sum()
    fn = weight[~p & t].sum()
    tn = weight[~p & ~t].sum()
    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": tp / (tp + fp) if (tp + fp) else float("nan"),
        "recall": tp / (tp + fn) if (tp + fn) else float("nan"),
        "agreement": (tp + tn) / (tp + fp + fn + tn),
    }


def print_scores(name, s):
    """Print one method's numbers as a short block."""
    print(f"  {name}")
    print(f"    precision {s['precision'] * 100:5.1f}%   of what it keeps, this share is real")
    print(f"    recall    {s['recall'] * 100:5.1f}%   of the real records, this share survives")
    print(f"    agreement {s['agreement'] * 100:5.1f}%   weighted to the corpus")
    print(f"    would keep {s['tp'] + s['fp']:,.0f} records, of which "
          f"{s['fp']:,.0f} are not biodiversity data")
    print(f"    would lose {s['fn']:,.0f} real records")


def main():
    """Score every trial file against the labels and print the comparison."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", default="exploration/llm/test_set_labelled_fixed.csv")
    parser.add_argument("--trial", action="append", required=True,
                        help="A trial CSV from run_trial.py. Give this more than "
                             "once to compare models.")
    parser.add_argument("--disagreements", default="exploration/llm/for_evan_disagreements.csv",
                        help="Where to write the records worth a human look")
    parser.add_argument("--reviewed-only", action="store_true",
                        help="Score only the records a person reviewed, using the "
                             "weights merge_review.py worked out. Use this with a "
                             "reviewed label file.")
    args = parser.parse_args()

    labels = pd.read_csv(args.labels, encoding="utf-8-sig")
    labels["record_id"] = labels["record_id"].astype(str)

    if args.reviewed_only:
        if "reviewed_by_hand" not in labels.columns:
            print("ERROR: --reviewed-only needs a label file from merge_review.py")
            return 1
        before = len(labels)
        labels = labels[labels["reviewed_by_hand"] == True]
        print(f"scoring only the {len(labels):,} reviewed records of {before:,}, "
              f"weighted back to the corpus")

    # Load every trial and attach its verdict.
    trials = {}
    for path in args.trial:
        frame = pd.read_csv(path, encoding="utf-8-sig")
        frame["record_id"] = frame["record_id"].astype(str)
        if "model_error" in frame.columns:
            failed = frame["model_error"].notna().sum()
            if failed:
                print(f"note: {failed} records failed in {path}, leaving them out")
            frame = frame[frame["model_error"].isna()]
        name = frame["model"].dropna().iloc[0] if "model" in frame.columns else path
        frame["verdict"] = frame.apply(decide, axis=1)
        frame["verdict_loose3"] = frame.apply(decide_loose_rule3, axis=1)
        frame["verdict_strict2"] = frame.apply(decide_strict_rule2, axis=1)
        trials[name] = frame
        print(f"loaded {len(frame):,} scored records from {name}")

    # Only score records every trial answered, so the models are compared on
    # exactly the same rows. A part finished run is fine, it just narrows this.
    common = set(labels["record_id"])
    for frame in trials.values():
        common &= set(frame["record_id"])
    common = sorted(common)

    base = labels[labels["record_id"].isin(common)].set_index("record_id").loc[common]
    print()
    print(f"scoring {len(common):,} records answered by every model "
          f"(the labelled set has {len(labels):,})")
    kept_n = (base["stratum"] == "kept").sum()
    print(f"  {kept_n:,} from the kept pool, {len(base) - kept_n:,} from the dropped pool")

    weight = weights_for(base)
    truth = base["is_biodiversity"]

    print()
    print("=" * 70)
    print("SCORED AGAINST THE LABELS, WEIGHTED TO THE FULL CORPUS")
    print("=" * 70)
    print()

    # The keyword filter's own verdict is simply which pool a record is in.
    filter_verdict = base["stratum"].map({"kept": "yes", "dropped": "no"})
    print_scores("keyword filter (what runs today)", score(filter_verdict, truth, weight))

    for name, frame in trials.items():
        verdicts = frame.set_index("record_id").loc[common, "verdict"]
        print()
        print_scores(name, score(verdicts, truth, weight))

    # How much of the answer rides on the two criteria still being confirmed.
    print()
    print("=" * 70)
    print("HOW MUCH THE TWO UNSETTLED CRITERIA MOVE THE RESULT")
    print("=" * 70)
    print()
    print("  Criterion 2 is forestry, agriculture and fisheries. Criterion 3 is")
    print("  boundaries and land cover. These are the two lines Evan has not")
    print("  confirmed yet, so here is each model under a different reading.")
    print()
    for name, frame in trials.items():
        indexed = frame.set_index("record_id").loc[common]
        print(f"  {name}")
        for label, column in [("as written", "verdict"),
                              ("criterion 3 relaxed", "verdict_loose3"),
                              ("criterion 2 tightened", "verdict_strict2")]:
            s = score(indexed[column], truth, weight)
            print(f"    {label:24s} precision {s['precision'] * 100:5.1f}%  "
                  f"recall {s['recall'] * 100:5.1f}%")
        print()

    # The question the whole trial exists to answer.
    print("=" * 70)
    print("THE RECORDS STRING MATCHING CANNOT REACH")
    print("=" * 70)
    print()
    blind = base[(base["stratum"] == "kept")
                 & (base["filter2_found"] == "no")
                 & (base["is_biodiversity"] == "yes")]
    print(f"  {len(blind):,} sampled records are biodiversity data that filter 2")
    print( "  found no organism name in. A longer keyword list cannot reach the")
    print( "  ones that never write a name down. Can a model?")
    print()
    for name, frame in trials.items():
        verdicts = frame.set_index("record_id").loc[blind.index, "verdict"]
        found = (verdicts == "yes").sum()
        print(f"    {name}: {found} of {len(blind)} recovered "
              f"({found / len(blind) * 100:.0f}%)")

    # Records worth a person's time, chosen by where the machines disagree.
    if len(trials) >= 2:
        names = list(trials)
        frames = [trials[n].set_index("record_id").loc[common, "verdict"] for n in names]
        differ = frames[0] != frames[1]
        print()
        print("=" * 70)
        print("WHERE THE TWO MODELS DISAGREE WITH EACH OTHER")
        print("=" * 70)
        print()
        print(f"  {differ.sum():,} of {len(common):,} records "
              f"({differ.sum() / len(common) * 100:.1f}%)")
        print( "  These are the ones worth a person reading, because the machines")
        print( "  could not settle them between themselves.")

        out = base.loc[differ[differ].index, ["row", "title", "is_biodiversity", "notes", "stratum"]].copy()
        for name, verdicts in zip(names, frames):
            out[f"verdict_{name}"] = verdicts[differ]
        out.to_csv(args.disagreements, index=False, encoding="utf-8-sig")
        print(f"  written to {args.disagreements}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
