"""
Pick the records worth reviewing by hand, and write them out for blind review.

The findings all rest on the records where the labels and the keyword filter
disagree. Those are the ones that say the filter drops real data and keeps
material that is out of scope. If a label there is wrong, the finding built on
it is wrong, so every one of them is reviewed.

The records where the label and the filter agree are far more numerous and far
less load bearing, so a share of them is checked instead. That share is what
makes the corrected numbers honest: without it there would be no way to say how
often the agreeing records are wrong, and no way to weight the review back to
the whole set.

Four groups, from the two columns already in the labelled set:

  dropped_yes   the filter dropped it, the label says biodiversity data
                the filter's misses. Reviewed in full.
  kept_no       the filter kept it, the label says it is not
                the filter's noise. Reviewed in full.
  kept_yes      both say biodiversity data                    sampled
  dropped_no    both say not                                  sampled

The file that comes out is blind. It carries the record and nothing else: no
group, no existing label, no model verdicts, no stratum. Seeing any of those
would pull the review toward them, and the result would stop being an
independent check.

Usage:

    python exploration/llm/build_review_set.py
    python exploration/llm/build_review_set.py --sample-fraction 0.2
"""

import argparse
import sys

import pandas as pd


# The four groups, and whether each is reviewed in full.
FULLY_REVIEWED = ["dropped_yes", "kept_no"]
SAMPLED = ["kept_yes", "dropped_no"]

GUIDE = """# Reviewing the test set

{count} records to judge. Which group each one is in is deliberately not shown,
so read each on its own terms.

Fill in two columns.

## is_biodiversity

yes or no. The criteria, unchanged from the first pass:

1. Purely physical environmental data (CTD casts, bathymetry, wave height,
   seismic surveys, weather) is no unless the record itself mentions living
   things.
2. Forestry, agriculture and fisheries data is yes only when the record is
   about the organisms themselves. Tenure boundaries, harvest volumes, farm
   income and licensing are no.
3. Park and protected area boundaries, land cover and topographic maps are yes
   only when an ecological or conservation purpose is stated.
4. Government administrative records are no.
5. Human health and biomedical records are no unless the subject is wild
   organisms or ecosystems.

Rule 2 is the one that moves the result most, so it is worth being deliberate
about where that line sits and applying it the same way every time.

## notes

One short line saying why. If you cannot decide, start the line with UNSURE and
say what makes it hard. An honest UNSURE is worth more than a guess, because
those rows can go to Evan as a short list rather than a spreadsheet.

## While reviewing

Judge only from the title, subjects and abstract in the row. Do not look the
record up and do not open the other files, since the point is a check that is
independent of what has already been decided.

French records can go through a translator. Say so in the notes when the
translation was what decided it, so the count of those is known.

The order is shuffled. Runs of similar records do not mean anything.

Split this over a few sittings. Judgement drifts when it is done in one go, and
it drifts quietly.
"""


def main():
    """Split the labelled set four ways and write a blind review file."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", default="exploration/llm/test_set_labelled_fixed.csv")
    parser.add_argument("--sample-fraction", type=float, default=0.15,
                        help="Share of the agreeing records to check")
    parser.add_argument("--out", default="exploration/llm/review_blind.csv")
    parser.add_argument("--key", default="exploration/llm/review_key.csv",
                        help="Which group each record is in. Leave this shut "
                             "until the review is finished.")
    parser.add_argument("--guide", default="exploration/llm/review_guide.md")
    parser.add_argument("--seed", type=int, default=20260922,
                        help="Fixed so the same sample comes out every time")
    args = parser.parse_args()

    labels = pd.read_csv(args.labels, encoding="utf-8-sig")
    labels["record_id"] = labels["record_id"].astype(str)

    # The group is just the two existing columns put together.
    group = labels["stratum"].astype(str) + "_" + labels["is_biodiversity"].astype(str)
    labels = labels.assign(review_group=group)

    counts = labels["review_group"].value_counts()
    print("the four groups")
    for name in FULLY_REVIEWED + SAMPLED:
        n = int(counts.get(name, 0))
        how = "all" if name in FULLY_REVIEWED else f"{args.sample_fraction:.0%}"
        note = "  <- the filter and the labels disagree" if name in FULLY_REVIEWED else ""
        print(f"  {name:12s} {n:4,}   review {how}{note}")

    chosen = []
    plan = []
    for name in FULLY_REVIEWED + SAMPLED:
        pool = labels[labels["review_group"] == name]
        if not len(pool):
            continue
        if name in FULLY_REVIEWED:
            take = len(pool)
            picked = list(pool["record_id"])
        else:
            take = max(1, round(len(pool) * args.sample_fraction))
            picked = list(pool["record_id"].sample(take, random_state=args.seed))
        chosen += picked
        plan.append({"review_group": name, "in_set": len(pool), "reviewing": take})

    plan = pd.DataFrame(plan)
    print()
    print(f"review set: {len(chosen):,} records, "
          f"{len(chosen) / len(labels) * 100:.0f}% of the labelled set")
    print(plan.to_string(index=False))

    # The blind file. Shuffled, carrying only the record itself.
    base = labels.set_index("record_id")
    review = base.loc[chosen, ["row", "title", "subjects", "abstract"]].copy()

    # The subjects column was saved as a list and came back as one string with
    # the brackets and quotes still in it. Tidy it so it reads as a plain list.
    # Readability only, so a missing run_trial is not worth failing over.
    try:
        from run_trial import tidy_list_field
        review["subjects"] = review["subjects"].apply(
            lambda value: tidy_list_field(str(value)) if pd.notna(value) else "")
    except ImportError:
        print("note: run_trial not importable, leaving the subjects column as is")

    review = review.sample(frac=1, random_state=args.seed + 1)
    review.insert(0, "record_id", review.index)
    review["is_biodiversity"] = ""
    review["notes"] = ""
    review.reset_index(drop=True).to_csv(args.out, index=False, encoding="utf-8-sig")

    # The key, kept apart so it is not read by accident while reviewing. It
    # carries what the merge step needs to weight each group correctly.
    key = base.loc[chosen, ["review_group", "is_biodiversity", "stratum"]].copy()
    key = key.rename(columns={"is_biodiversity": "old_label"})
    key.insert(0, "record_id", key.index)
    key = key.merge(plan, on="review_group", how="left")
    key.to_csv(args.key, index=False, encoding="utf-8-sig")

    with open(args.guide, "w", encoding="utf-8") as handle:
        handle.write(GUIDE.format(count=len(chosen)))

    print()
    print(f"wrote {args.out}      the file to fill in")
    print(f"wrote {args.key}      leave this shut until the review is done")
    print(f"wrote {args.guide}    the criteria, worth rereading before starting")
    print()
    print("Save it as CSV UTF-8, not plain CSV, or the French accents come back")
    print("as nonsense.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
