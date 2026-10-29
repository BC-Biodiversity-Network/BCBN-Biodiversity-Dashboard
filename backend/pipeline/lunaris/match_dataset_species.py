"""
match_dataset_species.py

Matches the organism names of the exported Lunaris datasets
(lunaris_datasets.parquet, both filter 2 runs) to the species on the site
(species_card_gbif.parquet), for "N datasets mention this species" on a
species card and links from a dataset back to its species. It only builds
and checks the matching: it writes the match table and prints a report,
and changes nothing the site reads.

The names a dataset name can match are exactly the names the site's
species search finds a species by (SEARCH_INDEX_SQL in api/search.py),
written the same way (the search_norm macro: lower case, no accents,
apostrophes dropped, other punctuation turned into spaces, a lone "x"
dropped):
  scientific  the card's Catalogue of Life name, also without a subgenus
              (col_name), the names of keys merged into it (merged_name),
              and the scientific labels of its BCSEE entries
              (bcsee_scientific_label)
  common      the English name of its species-level BCSEE entry
              (english_name) and of its other BCSEE entries, such as a
              population (bcsee_entry_english_name). Our table has no other
              common names, so species BCSEE does not list can only be
              matched by their scientific name.

The steps for each dataset:
  1. Each name as written is split into separate names (split_entry): a
     name in brackets is a name of its own ("picea glauca (white spruce)"),
     and so is each part between slashes ("red fox / arctic fox"). Commas
     outside brackets, "and" and "or" are not split on: they are mostly
     reversed names ("Cherries, sweet") or shared words ("Red-necked and
     Red Phalaropes").
  2. An abbreviated genus ("O. keta") is written out only when exactly one
     genus of our table with that first letter starts another name of the
     same dataset ("Oncorhynchus nerka"); otherwise it stays unmatched.
  3. Each name is matched exactly. One species: linked, by scientific or
     common name, or via an expanded abbreviation. More than one species:
     ambiguous, not linked. None: a group word when it is one word once
     "sp." or "spp." is dropped ("fish", "kelp", "salmon", "Mytilus spp."),
     else unmatched. Names that are a genus or family of our table are
     also marked (higher_taxon), for later.

The match table has one row per dataset and name (see COLUMNS in
match_rows), every name kept as written.

Run, from backend/:
    python pipeline/lunaris/match_dataset_species.py
"""

import argparse
import os
import re
import sys

import duckdb
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, BACKEND)
from api.search import SEARCH_INDEX_SQL, SEARCH_NORM_MACRO  # noqa: E402

DEFAULT_DATASETS = "data/api/lunaris_datasets.parquet"
DEFAULT_SPECIES = "data/api/species_card_gbif.parquet"
DEFAULT_BCSEE = "data/api/bcsee_status_by_gbif_species.parquet"
DEFAULT_OUT = "data/lunaris_species_match/dataset_species_matches.parquet"

# The fields of the site's name index, by the kind of name they are, each
# in order of preference for saying how a name matched.
SCIENTIFIC_FIELDS = ["col_name", "merged_name", "bcsee_scientific_label"]
COMMON_FIELDS = ["english_name", "bcsee_entry_english_name"]
FIELD_ORDER = {field: n for n, field in enumerate(SCIENTIFIC_FIELDS + COMMON_FIELDS)}

# Words that only say "some species of" a genus; dropped when deciding
# whether a name is a group word or a genus.
RANK_WORDS = {"sp", "spp"}

# An abbreviated genus at the start of a name: one letter, a full stop, and
# then a lower-case word ("O. keta", "R. t. granti").
ABBREVIATION = re.compile(r"^\s*([A-Za-z])\.\s*[a-z]")

# The match table's columns, in order.
COLUMNS = ["dataset_id", "frontend_group", "entry", "name", "runs", "name_norm", "status", "specieskey",
           "matched_name", "matched_field", "expanded_name", "candidates", "candidate_names",
           "higher_taxon", "higher_taxon_rank"]


# --- Reading ---------------------------------------------------------------

def open_species(species_path, bcsee_path):
    """
    Return a DuckDB connection with the site's species name index
    (search_names), built exactly as the API builds it, and the
    search_norm macro.
    """
    con = duckdb.connect()
    con.execute(f"CREATE VIEW species_card AS SELECT * FROM read_parquet('{species_path}')")
    con.execute(f"CREATE VIEW bcsee_status AS SELECT * FROM read_parquet('{bcsee_path}')")
    con.execute(SEARCH_NORM_MACRO)
    con.execute(SEARCH_INDEX_SQL)
    return con


def name_lookup(con):
    """Return {written name: [(specieskey, field, name), ...]} from the name index (search_names)."""
    lookup = {}
    for key, field, name, norm in con.execute(
            "SELECT specieskey, field, name, norm FROM search_names ORDER BY specieskey, field, name").fetchall():
        lookup.setdefault(norm, []).append((key, field, name))
    return lookup


def higher_taxa(con):
    """Return ({written genus: genus}, {written family: family}) from the species table."""
    genera = dict(con.execute(
        "SELECT DISTINCT search_norm(genus), genus FROM species_card WHERE coalesce(genus, '') <> ''").fetchall())
    families = dict(con.execute(
        "SELECT DISTINCT search_norm(family), family FROM species_card WHERE coalesce(family, '') <> ''").fetchall())
    return genera, families


def species_names(con):
    """Return {specieskey: (Catalogue of Life name, English name or None)} for the report."""
    rows = con.execute("""
        SELECT c.specieskey, c.col_name,
               (SELECT max(n.name) FROM search_names AS n
                WHERE n.specieskey = c.specieskey AND n.field = 'english_name') AS english
        FROM species_card AS c""").fetchall()
    return {key: (name, english) for key, name, english in rows}


def normalise_all(con, names):
    """Return {name: name written the search way} for every name, using the site's search_norm macro."""
    frame = pd.DataFrame({"name": sorted(set(names))})
    con.register("names_to_normalise", frame)
    written = dict(con.execute("SELECT name, search_norm(name) FROM names_to_normalise").fetchall())
    con.unregister("names_to_normalise")
    return written


# --- One dataset -----------------------------------------------------------

def split_entry(entry):
    """
    Return the separate names in one organism entry as written: each part in
    brackets is a name, and so is what is left outside them ("sockeye (O.
    nerka) salmon" gives "sockeye salmon" and "O. nerka"); a list inside
    brackets is split on commas; every part is split on slashes. Empty
    parts are dropped.
    """
    inside = re.findall(r"\(([^()]*)\)", entry)
    outside = re.sub(r"\s*\([^()]*\)\s*", " ", entry)
    parts = [outside] + [piece for text in inside for piece in text.split(",")]
    names = []
    for part in parts:
        for piece in part.split("/"):
            piece = " ".join(piece.split())
            if piece and piece not in names:
                names.append(piece)
    return names


def runs_of(name, organisms_4b, organisms_35b):
    """Return which filter 2 runs gave an entry ("4b", "35b" or "both"), ignoring letter case."""
    in_4b = name.lower() in {n.lower() for n in organisms_4b}
    in_35b = name.lower() in {n.lower() for n in organisms_35b}
    return "both" if in_4b and in_35b else "4b" if in_4b else "35b" if in_35b else ""


def without_rank_words(norm):
    """Return a written name without the words that only say "some species of" ("mytilus spp" gives "mytilus")."""
    return " ".join(word for word in norm.split() if word not in RANK_WORDS)


def dataset_genera(norms, genera):
    """Return the genera of our table that start a name of the dataset (a written name of two words or more)."""
    found = set()
    for norm in norms:
        words = norm.split()
        if len(words) >= 2 and len(words[0]) > 1 and words[0] in genera:
            found.add(words[0])
    return found


def expand_abbreviation(name, norm, genera_here):
    """
    Return the written name with its abbreviated genus written out, or None:
    when the name starts with an abbreviated genus and exactly one genus of
    the dataset (genera_here) starts with that letter.
    """
    match = ABBREVIATION.match(name)
    words = norm.split()
    if not match or len(words) < 2 or words[0] != match.group(1).lower():
        return None
    candidates = sorted(genus for genus in genera_here if genus.startswith(words[0]))
    if len(candidates) != 1:
        return None
    return " ".join([candidates[0]] + words[1:])


def classify(norm, lookup, genera, families, expanded=None):
    """
    Return the match of one written name (norm, or expanded when its genus
    was written out) as a dict: status, specieskey, matched_name,
    matched_field, candidates, candidate_names, higher_taxon and
    higher_taxon_rank (see the top of this file).
    """
    target = expanded or norm
    hits = lookup.get(target, [])
    keys = sorted({key for key, _, _ in hits})
    result = {"status": "unmatched", "specieskey": None, "matched_name": None, "matched_field": None,
              "candidates": [], "candidate_names": [], "higher_taxon": None, "higher_taxon_rank": None}
    if len(keys) == 1:
        # The best field it matched in: a scientific name before a common one.
        best = min(hits, key=lambda hit: (FIELD_ORDER.get(hit[1], len(FIELD_ORDER)), hit[2]))
        scientific = best[1] in SCIENTIFIC_FIELDS
        result.update(specieskey=keys[0], matched_name=best[2], matched_field=best[1],
                      status="expanded_abbreviation" if expanded else "scientific" if scientific else "common")
        return result
    if len(keys) > 1:
        names = {key: name for key, _, name in reversed(hits)}
        result.update(status="ambiguous", candidates=keys, candidate_names=[names[key] for key in keys])
        return result
    bare = without_rank_words(target)
    if bare in genera:
        result.update(higher_taxon=genera[bare], higher_taxon_rank="genus")
    elif bare in families:
        result.update(higher_taxon=families[bare], higher_taxon_rank="family")
    if len(bare.split()) == 1:
        result["status"] = "group_word"
    return result


def match_rows(record, norms, lookup, genera, families):
    """
    Return the match table rows of one dataset (a dict with id,
    frontend_group, organisms, organisms_4b and organisms_35b): one per
    name after splitting, in the order of COLUMNS. norms gives every name
    written the search way.
    """
    pairs = [(entry, name) for entry in record["organisms"] for name in split_entry(entry)]
    genera_here = dataset_genera([norms[name] for _, name in pairs], genera)
    rows, seen = [], set()
    for entry, name in pairs:
        norm = norms[name]
        if not norm or norm in seen:
            continue
        seen.add(norm)
        expanded = expand_abbreviation(name, norm, genera_here)
        rows.append({
            "dataset_id": record["id"], "frontend_group": record["frontend_group"], "entry": entry,
            "name": name, "runs": runs_of(entry, record["organisms_4b"], record["organisms_35b"]),
            "name_norm": norm, "expanded_name": expanded,
            **classify(norm, lookup, genera, families, expanded),
        })
    return rows


# --- The report ------------------------------------------------------------

def possible_typos(con, names, max_distance=2, min_length=5):
    """
    Return the names (written the search way) that are one or two letters
    away from a name in the site's name index, with the nearest names, as a
    DataFrame (name_norm, distance, near_name, near_field, specieskey). Only
    names of at least min_length letters are checked, and only against
    names with the same first letter (so a slip in the very first letter is
    not found; checking every pair took minutes). For listing only: nothing
    is linked.
    """
    names = sorted({name for name in names if len(name) >= min_length})
    con.register("typo_names", pd.DataFrame({"name_norm": names}))
    found = con.execute(f"""
        SELECT t.name_norm, levenshtein(t.name_norm, s.norm) AS distance, s.name AS near_name,
               s.field AS near_field, s.specieskey
        FROM typo_names AS t JOIN search_names AS s
          ON left(t.name_norm, 1) = left(s.norm, 1)
         AND length(s.norm) BETWEEN length(t.name_norm) - {max_distance} AND length(t.name_norm) + {max_distance}
        WHERE levenshtein(t.name_norm, s.norm) BETWEEN 1 AND {max_distance}
        ORDER BY t.name_norm, distance, near_name""").df()
    con.unregister("typo_names")
    return found


def print_report(table, typos, names_by_key, out_dir, seed):
    """Print the report (the numbered points of the request) and write the sample of linked rows."""
    linked = table[table["specieskey"].notna()]
    print("1. Datasets with at least one linked species")
    for group, frame in table.groupby("frontend_group"):
        total = frame["total_in_group"].iloc[0]
        with_link = frame.loc[frame["specieskey"].notna(), "dataset_id"].nunique()
        print(f"   {group}: {with_link:,} of {total:,} ({with_link / total:.1%})")

    print("\n2. Names (one per dataset and name after splitting)")
    counts = table["status"].value_counts()
    for status in ["scientific", "common", "expanded_abbreviation", "ambiguous", "group_word", "unmatched"]:
        rows = table[table["status"] == status]
        print(f"   {status:22s} {counts.get(status, 0):6,} rows, {rows['name_norm'].nunique():5,} distinct names")
    print(f"   {'total':22s} {len(table):6,} rows, {table['name_norm'].nunique():5,} distinct names")
    taxa = table[table["higher_taxon"].notna()]
    print(f"   names that are a genus or family of our table (not linked): {len(taxa):,} rows, "
          f"{taxa['name_norm'].nunique():,} distinct; by rank {taxa['higher_taxon_rank'].value_counts().to_dict()}")
    print(f"   matched fields: {linked['matched_field'].value_counts().to_dict()}")
    abbreviated = table[table["name"].str.match(ABBREVIATION) & table["expanded_name"].isna()]
    print(f"   abbreviated genera left as they are (no single genus in the dataset): {len(abbreviated):,} rows")

    print("\n3a. The 30 most frequent unmatched names (datasets)")
    unmatched = table[table["status"] == "unmatched"].groupby("name_norm")["dataset_id"].nunique()
    for norm, n in unmatched.sort_values(ascending=False).head(30).items():
        print(f"   {n:4d}  {norm}")
    print("\n3b. Ambiguous names and their candidate species")
    ambiguous = table[table["status"] == "ambiguous"].drop_duplicates("name_norm")
    for _, row in ambiguous.sort_values("name_norm").iterrows():
        n = table.loc[table["name_norm"] == row["name_norm"], "dataset_id"].nunique()
        options = "; ".join(f"{key} {names_by_key[key][0]}" for key in row["candidates"])
        print(f"   {row['name_norm']} ({n} datasets): {options}")

    print(f"\n4. Possible typos: names not linked that are one or two letters from a site name "
          f"({typos['name_norm'].nunique()} names; nearest only)")
    for norm, rows in typos.groupby("name_norm"):
        nearest = rows[rows["distance"] == rows["distance"].min()]
        near = "; ".join(f"{r.near_name} [{r.near_field}, {r.specieskey}]" for r in nearest.head(3).itertuples())
        print(f"   {norm} -> {near} (distance {nearest['distance'].iloc[0]})")

    print("\n5. The 20 species with the most datasets")
    top = linked.groupby("specieskey")["dataset_id"].nunique().sort_values(ascending=False).head(20)
    for key, n in top.items():
        name, english = names_by_key[key]
        print(f"   {n:4d}  {key}  {name}{f' ({english})' if english else ''}")

    sample = linked.sample(n=min(30, len(linked)), random_state=seed)
    sample = sample.assign(species_name=sample["specieskey"].map(lambda key: names_by_key[key][0]),
                           english_name=sample["specieskey"].map(lambda key: names_by_key[key][1]))
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "sample30.csv")
    sample[["dataset_id", "frontend_group", "entry", "name", "status", "expanded_name", "matched_name",
            "matched_field", "specieskey", "species_name", "english_name"]].to_csv(path, index=False, mode="x")
    print(f"\n6. Wrote a sample of {len(sample)} linked rows (seed {seed}) to {path}")


def main():
    """Build the match table, write it, and print the report."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("--datasets", default=DEFAULT_DATASETS)
    parser.add_argument("--species", default=DEFAULT_SPECIES)
    parser.add_argument("--bcsee", default=DEFAULT_BCSEE)
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--report-dir", required=True, help="folder for sample30.csv (must not hold one yet)")
    parser.add_argument("--seed", type=int, default=20261029)
    args = parser.parse_args()
    if os.path.exists(args.out):
        raise SystemExit(f"ERROR: {args.out} already exists; move it away first")

    con = open_species(args.species, args.bcsee)
    lookup = name_lookup(con)
    genera, families = higher_taxa(con)
    datasets = pd.read_parquet(args.datasets, columns=["id", "frontend_group", "organisms", "organisms_4b",
                                                       "organisms_35b"])
    records = datasets.to_dict("records")
    names = [name for record in records for entry in record["organisms"] for name in split_entry(entry)]
    norms = normalise_all(con, names)
    rows = [row for record in records for row in match_rows(record, norms, lookup, genera, families)]
    table = pd.DataFrame(rows, columns=COLUMNS)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    table.to_parquet(args.out, index=False)
    print(f"wrote {args.out}: {len(table):,} rows for {table['dataset_id'].nunique():,} datasets\n")

    report = table.assign(total_in_group=table["frontend_group"].map(datasets["frontend_group"].value_counts()))
    not_linked = table.loc[table["status"].isin(["unmatched", "group_word"]), "name_norm"]
    print_report(report, possible_typos(con, not_linked), species_names(con), args.report_dir, args.seed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
