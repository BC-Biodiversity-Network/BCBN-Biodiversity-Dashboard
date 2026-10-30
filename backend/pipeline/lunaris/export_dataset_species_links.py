"""
export_dataset_species_links.py

Writes the links between Lunaris datasets and species that the API reads
(dataset_species_links.parquet in data/api), from the match table of
match_dataset_species.py.

One row per dataset and organism name (after splitting, as the matcher
splits them), in the order the record gives them:
  dataset_id       the dataset's id, as in lunaris_datasets.parquet
  position         the name's place in the dataset's list (0, 1, ...)
  name_as_written  the name as written in the record
  match_status     how the matcher placed it (see match_dataset_species.py)
  specieskey       the species it links to, only for a linked name;
                   empty (null) for every other name
  matched_name     the site name it matched (null when not linked)
  matched_field    which kind of site name that is, or "manual"

A name links to a species only if the matcher matched it exactly (by
scientific or common name, or as an abbreviated genus written out from
another name of the same dataset), or it is a confirmed row of the
hand-checked list (dataset_species_names_manual.csv: status confirmed,
checked_by and checked_date filled). Deferred and rejected rows never
link. The names that do not link are kept too, so the dataset page can
say which organisms the record names that are not linked.

Before writing, it checks the match table is up to date, and stops,
listing every problem, if it is not:
  - every name linked from the hand-checked list is a confirmed row of
    the list today, for the same species, and no confirmed name of the
    list is left unlinked in the table (the list changed since the
    matcher ran). Names are looked up as the matcher does: with an
    abbreviated genus written out first ("O. tschawytscha" by the row
    "oncorhynchus tschawytscha"), then as they are;
  - the table holds exactly the names of the datasets in
    lunaris_datasets.parquet (the datasets were exported again since);
  - every linked species key has a card on the site.

Run, from backend/, after match_dataset_species.py:
    python pipeline/lunaris/export_dataset_species_links.py
"""

import argparse
import os
import sys

import duckdb
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import match_dataset_species as ms  # noqa: E402

DEFAULT_MATCHES = ms.DEFAULT_OUT
DEFAULT_OUT = "data/api/dataset_species_links.parquet"

# The statuses that link a name to a species: the exact matches, and the
# confirmed rows of the hand-checked list ("manual").
EXACT = {"scientific", "common", "expanded_abbreviation"}
LINKED = EXACT | {"manual"}
assert LINKED == ms.LINKED

# The columns of the links table, in order.
COLUMNS = ["dataset_id", "position", "name_as_written", "match_status", "specieskey", "matched_name",
           "matched_field"]


def list_names(row):
    """Return the names a match table row is looked up by in the hand-checked list, as the matcher does: the name with its genus written out first, when it has one, then the name itself."""
    expanded = row["expanded_name"]
    return [name for name in (expanded if isinstance(expanded, str) else None, row["name_norm"]) if name]


def check_manual_links(table, rows):
    """
    Return the problems (a list of text) between the match table and the
    hand-checked list as it is now: a name linked as "manual" that is not a
    confirmed row for that species, and a confirmed name the table does not
    link. A name is looked up as the matcher does (list_names), so "O.
    tschawytscha" can link by the row "oncorhynchus tschawytscha".
    """
    confirmed = {row["name_as_written"]: row["specieskey"] for row in rows if ms.is_confirmed(row)}
    problems = set()
    for row in table.to_dict("records"):
        found = [name for name in list_names(row) if name in confirmed]
        if row["status"] == "manual":
            if not found:
                problems.add(f"{row['name_norm']!r} is linked from the hand-checked list, but is not a "
                             f"confirmed row there now")
            elif confirmed[found[0]] != row["specieskey"]:
                problems.add(f"{found[0]!r} is linked to {row['specieskey']}, but the confirmed row says "
                             f"{confirmed[found[0]]}")
        elif row["status"] not in LINKED and found:
            problems.add(f"{found[0]!r} is a confirmed row of the hand-checked list, but the table does not "
                         f"link it")
    return sorted(problems)


def check_dataset_names(table, datasets, written):
    """
    Return the problems (a list of text) between the match table and the
    exported datasets: each dataset's names, after splitting, must be
    exactly the table's names for it, in the same order, leaving out (as
    the matcher does) a name that is empty or written the same way as an
    earlier one once written the search way. written gives each name
    written the search way.
    """
    found = table.groupby("dataset_id", sort=False)["name"].apply(list).to_dict()
    expected = {}
    for record in datasets.to_dict("records"):
        names, seen = [], set()
        for name in (name for entry in record["organisms"] for name in ms.split_entry(entry)):
            if written[name] and written[name] not in seen:
                seen.add(written[name])
                names.append(name)
        expected[record["id"]] = names
    problems = [f"{dataset_id}: in the match table but not in the datasets"
                for dataset_id in sorted(set(found) - set(expected))]
    for dataset_id, names in expected.items():
        if names != found.get(dataset_id, []):
            problems.append(f"{dataset_id}: its names are not those of the match table")
    return problems


def search_way(names):
    """Return {name: name written the search way} for every name, with the site's search_norm macro."""
    con = duckdb.connect()
    con.execute(ms.SEARCH_NORM_MACRO)
    written = ms.normalise_all(con, names)
    con.close()
    return written


def build_links(table):
    """Return the links table (COLUMNS) from the match table: every name, its place in the dataset's list, and the species only for a linked name."""
    linked = table["status"].isin(LINKED)
    return pd.DataFrame({
        "dataset_id": table["dataset_id"],
        "position": table.groupby("dataset_id", sort=False).cumcount().astype("int64"),
        "name_as_written": table["name"],
        "match_status": table["status"],
        "specieskey": table["specieskey"].where(linked, None),
        "matched_name": table["matched_name"].where(linked, None),
        "matched_field": table["matched_field"].where(linked, None),
    })[COLUMNS]


def main():
    """Check the match table and write the links table."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("--matches", default=DEFAULT_MATCHES)
    parser.add_argument("--manual", default=ms.DEFAULT_MANUAL)
    parser.add_argument("--datasets", default=ms.DEFAULT_DATASETS)
    parser.add_argument("--species", default=ms.DEFAULT_SPECIES)
    parser.add_argument("--out", default=DEFAULT_OUT)
    args = parser.parse_args()
    if os.path.exists(args.out):
        raise SystemExit(f"ERROR: {args.out} already exists; move it away first")

    table = pd.read_parquet(args.matches)
    datasets = pd.read_parquet(args.datasets, columns=["id", "frontend_group", "organisms"])
    cards = set(pd.read_parquet(args.species, columns=["specieskey"])["specieskey"])

    problems = check_manual_links(table, ms.read_manual_names(args.manual))
    names = [name for organisms in datasets["organisms"] for entry in organisms for name in ms.split_entry(entry)]
    problems += check_dataset_names(table, datasets, search_way(names))
    keys = set(table.loc[table["status"].isin(LINKED), "specieskey"])
    problems += [f"species key {key!r} is linked but has no card on the site" for key in sorted(keys - cards)]
    if problems:
        raise SystemExit("ERROR: the match table is out of date; run match_dataset_species.py again:\n  "
                         + "\n  ".join(problems))

    links = build_links(table)
    links.to_parquet(args.out, index=False)

    linked = links[links["specieskey"].notna()].merge(datasets, left_on="dataset_id", right_on="id")
    print(f"wrote {args.out}: {len(links):,} names of {links['dataset_id'].nunique():,} datasets; "
          f"{len(linked):,} linked, to {linked['specieskey'].nunique():,} species")
    for group, rows in linked.groupby("frontend_group"):
        print(f"  {group}: {rows['dataset_id'].nunique():,} datasets linked to "
              f"{rows['specieskey'].nunique():,} species")
    print("  links by status:", links["match_status"].where(links["specieskey"].notna()).value_counts().to_dict())
    return 0


if __name__ == "__main__":
    sys.exit(main())
