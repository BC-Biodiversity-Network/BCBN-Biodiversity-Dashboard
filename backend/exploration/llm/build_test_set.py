"""
Build a labelling sheet for the LLM trial.

The point of the trial is to find out whether a language model does a better
job than the keyword filter. To answer that you need records the keyword
filter KEPT and records it THREW AWAY, both judged by hand. Without the
thrown-away ones you can only measure how often the filter lets junk through,
and never how much real data it lost, which is the more important number.

So this script samples from both pools, mixes them together, and writes one
CSV for you to fill in, plus a short guide next to it.

It makes no API calls and costs nothing.

Usage:

    python exploration/llm/build_test_set.py \
        --everything exploration/lunaris/harvest/lunaris_full_harvest.parquet \
        --candidates data/lunaris_taxa.parquet \
        --out exploration/llm/test_set_to_label.csv

Options:

    --n-dropped        how many of the filter's rejects to sample (default 400)
    --n-kept           how many of the filter's keeps to sample (default 500)
    --abstract-chars   cut abstracts to this many characters. The default is 0,
                       which keeps them whole. Set it to something like 1500 if
                       the long ones make the spreadsheet awkward to work in.
    --prior-verdicts   optional CSV of judgements you made earlier, so any
                       record you have already looked at carries a reminder
    --seed             random seed, so the same sample can be rebuilt (default 42)

The columns you fill in are:

    is_biodiversity   yes or no. Is this dataset about living things,
                      ecosystems or biodiversity?
    subject           only if yes. One of:
                      species, habitat, forestry, agriculture, genetic,
                      environment, other
    form              only if yes. One of:
                      dataset, report, policy, other
    organism          whatever organism it is about, in any language, or blank
    notes             anything you were unsure about

match_status and taxon_key are deliberately NOT in this sheet. They get worked
out by a later script from whatever you type in the organism column, so you do
not have to look anything up by hand.
"""

import argparse
import sys

import pandas as pd


# The tier columns written by filter 2. We look for these so the "kept" sample
# covers each kind of record rather than being dominated by the most common
# one. If none are present the script still works, it just treats all kept
# records as one group.
TIER_COLUMNS = [
    "species_found",
    "species_off_list",
    "genus_qualified",
    "family_found",
    "higher_taxa_found",
    "genus_bare",
    "common_name_found",
]

ID_COLUMNS = ["id", "record_id", "identifier", "oai_id"]
TITLE_COLUMNS = ["title", "titles", "dc_title"]
SUBJECT_COLUMNS = ["subjects", "subject", "keywords"]
ABSTRACT_COLUMNS = ["abstract", "description", "abstracts"]

SUBJECT_VALUES = ["species", "habitat", "forestry", "agriculture",
                  "genetic", "environment", "other"]
FORM_VALUES = ["dataset", "report", "policy", "other"]


def find_column(frame, candidates, what):
    """Return the first column name from candidates that exists, or stop."""
    for name in candidates:
        if name in frame.columns:
            return name
    print(f"ERROR: could not find a {what} column.")
    print(f"       looked for: {candidates}")
    print(f"       columns present: {list(frame.columns)}")
    sys.exit(1)


def as_text(value):
    """Turn one cell into plain text, so lists and missing values read cleanly."""
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value)
    return str(value)


def shorten(text, limit):
    """
    Cut a long abstract down to a readable length.

    A few abstracts in this corpus are tens of thousands of characters long.
    Left whole they make the spreadsheet unusable, and you do not need to read
    all of one to decide what a record is about.
    """
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit].rstrip() + " ... [cut, see the full record by its id]"


def has_any_tier(row, tier_cols):
    """
    Say whether filter 2 found any organism in this record.

    A tier column can hold a boolean, a count, or a list of names depending on
    how it was written, so this checks for anything that is not empty.
    """
    for col in tier_cols:
        value = row.get(col)
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            if len(value) > 0:
                return True
        elif isinstance(value, bool):
            if value:
                return True
        elif isinstance(value, (int, float)):
            if not pd.isna(value) and value > 0:
                return True
        elif isinstance(value, str):
            if value.strip():
                return True
    return False


def label_kept_rows(kept, tier_cols):
    """
    Split the kept records into groups so the sample covers each kind.

    Records where filter 2 found a scientific name are a different population
    from records where it found nothing, and the model may do well on one and
    badly on the other. Sampling them separately stops the easy group from
    hiding a problem in the hard one.
    """
    if not tier_cols:
        return pd.Series(["kept"] * len(kept), index=kept.index)

    groups = []
    for _, row in kept.iterrows():
        groups.append("kept_organism_found" if has_any_tier(row, tier_cols)
                      else "kept_nothing_found")
    return pd.Series(groups, index=kept.index)


def take(frame, n, seed, label):
    """Take a random sample of n rows, or all of them if there are fewer."""
    if len(frame) == 0:
        print(f"  WARNING: no rows available for {label}")
        return frame
    if len(frame) <= n:
        print(f"  {label}: taking all {len(frame):,} (fewer than the {n:,} asked for)")
        return frame
    print(f"  {label}: sampling {n:,} from {len(frame):,}")
    return frame.sample(n=n, random_state=seed)


def load_prior(path, id_column_guesses):
    """
    Load judgements made in an earlier round, so records you have already
    looked at carry a reminder of what you decided last time.

    Returns a dict from record id to a short note, or an empty dict.
    """
    if not path:
        return {}
    try:
        prior = pd.read_csv(path)
    except Exception as exc:
        print(f"  could not read {path}: {exc}")
        return {}

    id_col = None
    for name in id_column_guesses:
        if name in prior.columns:
            id_col = name
            break
    if id_col is None:
        print(f"  {path} has no id column, skipping the reminders")
        return {}

    # Use whichever column looks like the verdict.
    verdict_col = None
    for name in ["verdict", "judgement", "judgment", "label", "decision", "status"]:
        if name in prior.columns:
            verdict_col = name
            break

    notes = {}
    for _, row in prior.iterrows():
        key = as_text(row[id_col])
        value = as_text(row[verdict_col]) if verdict_col else "seen before"
        if key:
            notes[key] = f"earlier round: {value}"
    print(f"  loaded {len(notes):,} earlier judgements from {path}")
    return notes


def write_guide(path, counts):
    """Write a short guide next to the CSV, for reference while labelling."""
    lines = [
        "# How to fill in the labelling sheet",
        "",
        "Hide the `stratum` column before you start. It says whether the keyword",
        "filter kept or dropped each record, and seeing it will pull your judgement",
        "towards agreeing with the filter. The whole point is an independent answer.",
        "",
        "## The columns",
        "",
        "**is_biodiversity** — `yes` or `no`.",
        "Is this dataset about living things, ecosystems, or biodiversity?",
        "If no, leave the rest of the row blank and move on.",
        "",
        "**subject** — only when is_biodiversity is yes. One of:",
        "",
    ]
    for value in SUBJECT_VALUES:
        lines.append(f"  - `{value}`")
    lines += [
        "",
        "**form** — only when is_biodiversity is yes. One of:",
        "",
    ]
    for value in FORM_VALUES:
        lines.append(f"  - `{value}`")
    lines += [
        "",
        "**organism** — whatever organism the record is about, in any language.",
        "Write what comes to mind. Do not look anything up, a later script",
        "matches whatever you write against the BC species list.",
        "",
        "**notes** — anything you were unsure about.",
        "The unsure ones are where the interesting disagreements show up later,",
        "and they are also what you take to Evan when you need a scope decision.",
        "",
        "## Advice",
        "",
        "Consistency matters more than getting every hard case right. If you find",
        "yourself changing your mind about a whole category halfway through, write",
        "it in notes rather than silently switching, so the change can be found.",
        "",
        "When a record is genuinely borderline, say so in notes instead of forcing",
        "a yes or no. A record marked unsure is information. A record forced into",
        "the wrong box is noise.",
        "",
        "## What is in this sample",
        "",
    ]
    for name, count in counts.items():
        lines.append(f"  {name}: {count:,}")
    lines += [
        "",
        "The dropped pool is the important one. Most of what the keyword filter",
        "threw away really is unrelated, so this sample is there to find the rare",
        "mistakes. Read the result as 'are there misses at all, and roughly how",
        "common', not as a precise rate.",
        "",
    ]
    with open(path, "w") as handle:
        handle.write("\n".join(lines))
    print(f"wrote the guide to {path}")


def main():
    """Read both parquet files, sample from each pool, write the labelling sheet."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--everything", required=True, help="parquet of all harvested records")
    parser.add_argument("--candidates", required=True, help="parquet of records that passed filter 1")
    parser.add_argument("--out", required=True, help="where to write the CSV")
    parser.add_argument("--n-dropped", type=int, default=400)
    parser.add_argument("--n-kept", type=int, default=500)
    parser.add_argument("--abstract-chars", type=int, default=0,
                        help="cut abstracts to this many characters, 0 means no cut")
    parser.add_argument("--prior-verdicts", default=None)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    print(f"reading {args.everything}")
    everything = pd.read_parquet(args.everything)
    print(f"reading {args.candidates}")
    candidates = pd.read_parquet(args.candidates)

    id_all = find_column(everything, ID_COLUMNS, "id")
    id_cand = find_column(candidates, ID_COLUMNS, "id")
    title_col = find_column(everything, TITLE_COLUMNS, "title")
    subject_col = find_column(everything, SUBJECT_COLUMNS, "subjects")
    abstract_col = find_column(everything, ABSTRACT_COLUMNS, "abstract")

    kept_ids = set(candidates[id_cand])
    is_kept = everything[id_all].isin(kept_ids)
    kept = everything[is_kept]
    dropped = everything[~is_kept]

    print()
    print(f"kept by filter 1:    {len(kept):>8,}")
    print(f"dropped by filter 1: {len(dropped):>8,}")

    prior_notes = load_prior(args.prior_verdicts, ID_COLUMNS)

    # Carry the tier information across from the candidates file onto the kept
    # records. The candidates are deduplicated on id first, because a repeated
    # id would multiply rows during the merge.
    tier_cols = [c for c in TIER_COLUMNS if c in candidates.columns]
    if tier_cols:
        print(f"tier columns found:  {tier_cols}")
        tier_slice = candidates[[id_cand] + tier_cols].drop_duplicates(subset=[id_cand])
        merged = kept.merge(tier_slice, left_on=id_all, right_on=id_cand,
                            how="left", suffixes=("", "_tier"))
        merged = merged.assign(stratum=label_kept_rows(merged, tier_cols).values)
    else:
        print("tier columns found:  none, treating all kept records as one group")
        merged = kept.assign(stratum="kept")

    print()
    print("sampling")
    pieces = [take(dropped.assign(stratum="dropped"), args.n_dropped, args.seed, "dropped")]

    kept_groups = sorted(merged["stratum"].unique())
    per_group = max(1, args.n_kept // len(kept_groups))
    for group in kept_groups:
        pieces.append(take(merged[merged["stratum"] == group], per_group, args.seed, group))

    sample = pd.concat(pieces, ignore_index=True)

    # Shuffle the rows. If you label four hundred rejects in a row you start
    # agreeing with the filter out of habit, so the pools are mixed together
    # and the stratum column is put last, where it is easy to keep off screen.
    sample = sample.sample(frac=1.0, random_state=args.seed).reset_index(drop=True)

    record_ids = sample[id_all].map(as_text)
    sheet = pd.DataFrame({
        "row": range(1, len(sample) + 1),
        "record_id": record_ids,
        "title": sample[title_col].map(as_text),
        "subjects": sample[subject_col].map(as_text),
        "abstract": sample[abstract_col].map(as_text).map(
            lambda text: shorten(text, args.abstract_chars)),
        # Everything below here is for you to fill in.
        "is_biodiversity": "",
        "subject": "",
        "form": "",
        "organism": "",
        "notes": record_ids.map(lambda key: prior_notes.get(key, "")),
        # Kept last so it can be hidden while labelling.
        "stratum": sample["stratum"],
    })

    # Excel on a Mac guesses the encoding when it opens a CSV, and without a
    # marker at the start of the file it guesses wrong, turning every accented
    # letter into two symbols. Writing with "utf-8-sig" puts that marker in.
    # Anything reading this file later must use encoding="utf-8-sig" too.
    sheet.to_csv(args.out, index=False, encoding="utf-8-sig")

    # Excel refuses to show more than 32,767 characters in one cell, so warn if
    # any abstract is longer than that.
    too_long = (sheet["abstract"].str.len() > 32_000).sum()
    if too_long:
        print()
        print(f"NOTE: {too_long} abstracts are longer than 32,000 characters.")
        print("      Excel will cut those off in the cell. The file itself is fine,")
        print("      and you can look the full text up by record_id if you need it.")
    counts = sheet["stratum"].value_counts().to_dict()

    guide_path = args.out.rsplit(".", 1)[0] + "_guide.md"
    write_guide(guide_path, counts)

    print()
    print("=" * 66)
    print(f"wrote {len(sheet):,} rows to {args.out}")
    print("=" * 66)
    print()
    print("counts by stratum")
    for name, count in counts.items():
        print(f"  {name:<24} {count:>6,}")
    print()
    print("allowed values")
    print(f"  is_biodiversity  yes / no")
    print(f"  subject          {' / '.join(SUBJECT_VALUES)}")
    print(f"  form             {' / '.join(FORM_VALUES)}")
    print()
    print("Hide the stratum column before you start labelling.")


if __name__ == "__main__":
    sys.exit(main())
