"""
build_label_sample.py

Draw the fixed 200-record label set for the Lunaris BC filter: records that
Claude will suggest labels for and Lucia will check, so that pass 2 (versions
C3 and C4) can be scored on them. It only samples and exports; no model is
run, and no pipeline output is changed.

Population: the step 1 rules output (rules_2026_10_v2.csv), whose records are
in three groups: needs_llm, bc and not_bc.

Left out:
  - the 40 records of the pass 1 and pass 2 trials (from the pass 1 trial's
    sample.csv, which the pass 2 trials use too), and any copy of them;
  - all but one copy of the same dataset. Two records count as copies when
    they share the UUID at the end of their identifier (the same dataset
    from CanWIN and open.canada.ca, or from CIOOS and Hakai), or have the
    same title and the same, non-empty abstract once case and spacing are
    ignored (the same dataset on a provincial portal under another
    identifier). Copies are linked into groups, so three copies of one
    dataset are one group. At most one record of a group is ever in the
    sample: inside each population group one copy is kept (the lowest
    record id), and a dataset already drawn for one group is not drawn
    again for another.

Sample: simple random sampling with a fixed seed inside each group: 150
needs_llm, 25 bc and 25 not_bc, drawn in that order. The 200 are then
shuffled with the same seed and numbered L001 to L200, so the group cannot be
read from the number.

Output (--out, ignored by git):
  index.csv    label_id, record_id, title, group (needs_llm, rules_bc,
               rules_not_bc), step1_evidence, publisher, and empty columns
               claude_label, claude_reason, claude_unsure, lucia_label,
               lucia_note
  records.txt  the 200 records in label order, each starting with its
               label_id on a line of its own, then the record exactly as the
               pass 2 C4 prompt shows it (geo_record_text.record_text with
               box notes giving the shares inside and outside BC); no
               instructions and no place list
  README.md    the seed, population file, exclusions, counts, how to rerun

Run, from backend/:
    python pipeline/lunaris/build_label_sample.py \
        --bc-boundary /Users/lucia/Desktop/BCBN/Results/bc_boundary_gbif.wkt
"""

import argparse
import os
import re
import sys
from collections import Counter

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "llm"))
from bc_filter_rules import load_bc_shape  # noqa: E402
from geo_pass1_trial import read_harvest  # noqa: E402
from geo_record_text import record_text  # noqa: E402

# The seed for every random step, so the same 200 come out every time.
SEED = 20261023

# How many records to draw from each group, in the order they are drawn.
SAMPLE_SIZES = [("needs_llm", 150), ("bc", 25), ("not_bc", 25)]
# The group names written to index.csv.
GROUP_NAMES = {"needs_llm": "needs_llm", "bc": "rules_bc", "not_bc": "rules_not_bc"}

UUID_AT_END = re.compile(r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$")
INDEX_COLUMNS = ["label_id", "record_id", "title", "group", "step1_evidence", "publisher",
                 "claude_label", "claude_reason", "claude_unsure", "lucia_label", "lucia_note"]


def squash(text):
    """Lower-case a text and collapse its spacing, for comparing titles and abstracts."""
    return " ".join(str(text or "").lower().split())


def dataset_keys(records):
    """
    Return, for each record (by record_id), the key of its group of copies:
    records that share a trailing UUID, or the same title and non-empty
    abstract, end up with the same key, linked through any chain of shared
    UUIDs and texts. A record with no copy has its own id as its key.
    """
    parent = {}

    def find(item):
        """The group an item belongs to (union-find, with path halving)."""
        while parent.setdefault(item, item) != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def join(a, b):
        """Put two items in the same group."""
        parent[find(a)] = find(b)

    for row in records.itertuples():
        match = UUID_AT_END.search(row.record_id)
        if match:
            join(row.record_id, "uuid:" + match.group(1).lower())
        if squash(row.abstract) and squash(row.title):
            join(row.record_id, "text:" + squash(row.title) + " || " + squash(row.abstract))
    return {record_id: find(record_id) for record_id in records["record_id"]}


def read_population(rules_path, harvest_path):
    """
    Return the step 1 rules output joined with each record's title, abstract
    and publisher from the harvest (latest datestamp per id), and its
    dataset key (dataset_keys).
    """
    rules = pd.read_csv(rules_path, keep_default_na=False)
    harvest = pd.read_parquet(harvest_path, columns=["id", "datestamp", "abstract", "publisher"])
    harvest = harvest.sort_values("datestamp", ascending=False, kind="stable").drop_duplicates("id")
    population = rules.merge(harvest[["id", "abstract", "publisher"]], left_on="record_id", right_on="id",
                             how="left").drop(columns="id")
    population["dataset_key"] = population["record_id"].map(dataset_keys(population))
    return population


def read_trial_ids(trial_sample):
    """Return the record ids of the pass 1 and pass 2 trials, from the pass 1 trial's sample.csv."""
    return set(pd.read_csv(trial_sample, keep_default_na=False)["record_id"])


def draw_sample(population, trial_ids, seed):
    """
    Return the 200 records, drawn group by group (SAMPLE_SIZES): leave out
    the trial records and their copies, keep one copy per dataset (the
    lowest record id) inside each group, leave out datasets already drawn
    for an earlier group, then draw at random with the seed.
    """
    trial_keys = set(population.loc[population["record_id"].isin(trial_ids), "dataset_key"])
    used_keys = set(trial_keys)
    drawn = []
    for group, size in SAMPLE_SIZES:
        pool = population[(population["bc_class"] == group) & ~population["dataset_key"].isin(used_keys)]
        pool = pool.sort_values("record_id").drop_duplicates("dataset_key")
        part = pool.sample(size, random_state=seed)
        used_keys |= set(part["dataset_key"])
        drawn.append(part.assign(group=GROUP_NAMES[group]))
    sample = pd.concat(drawn)
    # Shuffle so the label number does not show the group.
    sample = sample.sample(frac=1, random_state=seed).reset_index(drop=True)
    sample["label_id"] = [f"L{n:03d}" for n in range(1, len(sample) + 1)]
    return sample


def counts(population, trial_ids):
    """
    Return the figures for the README and the report: records per group,
    trial records found, and copy groups (datasets with two or more records)
    inside each group and across groups.
    """
    found = population["record_id"].isin(trial_ids)
    sizes = population.groupby("dataset_key")["record_id"].transform("size")
    in_copies = population[sizes > 1]
    per_group = {}
    for group, _ in SAMPLE_SIZES:
        part = population[population["bc_class"] == group]
        within = part.groupby("dataset_key").size()
        per_group[group] = {"records": len(part), "copy_groups": int((within > 1).sum()),
                            "records_in_copy_groups": int(within[within > 1].sum())}
    spanning = in_copies.groupby("dataset_key")["bc_class"].nunique()
    trial_keys = set(population.loc[found, "dataset_key"])
    return {
        "per_group": per_group,
        "trial_found": int(found.sum()),
        "trial_copies": int((population["dataset_key"].isin(trial_keys) & ~found).sum()),
        "copy_groups_all": int(in_copies["dataset_key"].nunique()),
        "copy_groups_spanning_groups": int((spanning > 1).sum()),
    }


def write_index(path, sample):
    """Write index.csv: the sample's columns and the empty label columns."""
    index = pd.DataFrame({
        "label_id": sample["label_id"], "record_id": sample["record_id"], "title": sample["title"],
        "group": sample["group"], "step1_evidence": sample["evidence"], "publisher": sample["publisher"].fillna(""),
    })
    for column in INDEX_COLUMNS[6:]:
        index[column] = ""
    index[INDEX_COLUMNS].to_csv(path, index=False)


def write_records(path, sample, harvest_path, shape):
    """
    Write records.txt: each record's label_id on a line of its own, then the
    record exactly as the pass 2 C4 prompt shows it, records separated by a
    blank line. Return the number of characters written.
    """
    harvest = read_harvest(harvest_path, sample["record_id"])
    parts = [f"{row.label_id}\n{record_text(harvest[row.record_id], shape, box_outside=True)}"
             for row in sample.itertuples()]
    text = "\n\n\n".join(parts) + "\n"
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return len(text)


def write_readme(path, args, figures, sample, characters):
    """Write README.md: the seed, the population file, the exclusions, the counts and how to rerun."""
    per = figures["per_group"]
    drawn = Counter(sample["group"])
    lines = [
        "# Label set for the Lunaris BC filter, 2026-10",
        "",
        "A fixed sample of 200 records for scoring pass 2 (versions C3 and C4). Claude suggests labels;",
        "Lucia checks them. Built by `backend/pipeline/lunaris/build_label_sample.py`; no model was run.",
        "",
        f"- Seed: {args.seed}",
        f"- Population: `{args.population}` (step 1 rules output)",
        f"- Harvest: `{args.harvest}`",
        "",
        "## Population",
        "",
        "| group | records | copy groups inside it | records in those groups |",
        "|---|---|---|---|",
    ]
    for group, _ in SAMPLE_SIZES:
        lines.append(f"| {group} | {per[group]['records']:,} | {per[group]['copy_groups']:,} | "
                     f"{per[group]['records_in_copy_groups']:,} |")
    lines += [
        "",
        f"Copy groups in all: {figures['copy_groups_all']:,}; of these, {figures['copy_groups_spanning_groups']:,} "
        "have copies in more than one group.",
        "",
        "## Left out",
        "",
        f"- The {figures['trial_found']} records of the pass 1 and pass 2 trials (`{args.trial_sample}`), and "
        f"{figures['trial_copies']} copies of them.",
        "- All but one copy of each dataset. Copies are records that share the UUID at the end of their identifier, "
        "or have the same title and the same non-empty abstract (case and spacing ignored); they are linked into "
        "groups. Inside each group the copy with the lowest record id is kept, and a dataset already drawn for one "
        "group is not drawn again.",
        "",
        "## Sample",
        "",
        "Simple random sampling inside each group, drawn in this order: "
        + ", ".join(f"{size} from {group}" for group, size in SAMPLE_SIZES)
        + f". The 200 were shuffled with the same seed and numbered L001 to L200. Drawn: "
        + ", ".join(f"{name} {drawn[name]}" for name in GROUP_NAMES.values()) + ".",
        "",
        "## Files",
        "",
        "- `index.csv`: one row per record, with empty columns for the labels "
        "(claude_label, claude_reason, claude_unsure, lucia_label, lucia_note).",
        f"- `records.txt`: the 200 records in label order, as the pass 2 C4 prompt shows them, without "
        f"instructions or place list ({characters:,} characters).",
        "",
        "## Rerun",
        "",
        "From `backend/`:",
        "",
        "```",
        "python pipeline/lunaris/build_label_sample.py \\",
        "    --bc-boundary /Users/lucia/Desktop/BCBN/Results/bc_boundary_gbif.wkt",
        "```",
        "",
        "The same seed and the same input files give the same 200 records.",
    ]
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def main():
    """Draw the sample and write index.csv, records.txt and README.md."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("--population", default="data/lunaris_bc_filter/rules_2026_10_v2.csv")
    parser.add_argument("--harvest", default="exploration/lunaris/harvest_2026_10_20/lunaris_full_harvest.parquet")
    parser.add_argument("--trial-sample", default="data/lunaris_bc_filter/pass1_trial_v3/sample.csv",
                        help="the trials' sample.csv, whose records are left out")
    parser.add_argument("--bc-boundary", required=True, help="bc_boundary_gbif.wkt")
    parser.add_argument("--out", default="data/lunaris_bc_filter/label_set_2026_10")
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()

    population = read_population(args.population, args.harvest)
    trial_ids = read_trial_ids(args.trial_sample)
    figures = counts(population, trial_ids)
    if figures["trial_found"] != 40:
        sys.exit(f"expected the 40 trial records in the population, found {figures['trial_found']}")
    sample = draw_sample(population, trial_ids, args.seed)

    os.makedirs(args.out, exist_ok=True)
    write_index(os.path.join(args.out, "index.csv"), sample)
    characters = write_records(os.path.join(args.out, "records.txt"), sample, args.harvest,
                               load_bc_shape(args.bc_boundary))
    write_readme(os.path.join(args.out, "README.md"), args, figures, sample, characters)
    print(f"population: {len(population):,} records; trial records found: {figures['trial_found']}, "
          f"copies of them: {figures['trial_copies']}")
    for group, part in figures["per_group"].items():
        print(f"  {group}: {part['records']:,} records, {part['copy_groups']:,} copy groups "
              f"({part['records_in_copy_groups']:,} records)")
    print(f"copy groups in all: {figures['copy_groups_all']:,}, across groups: {figures['copy_groups_spanning_groups']:,}")
    print("drawn: " + ", ".join(f"{k} {v}" for k, v in Counter(sample["group"]).items()))
    print(f"records.txt: {characters:,} characters; wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
