"""
build_species_tables.py

Build the two tables behind species search and the species card: where
each species was recorded, by year, and one summary row per species.

Input, all in --tables (written by build_dwca_tables.py):
    bc_occurrence.parquet     one row per record, with its H3 cells
    bc_species_image.parquet  one image per species
    bc_datasets.parquet       one row per dataset, with its title
and, from fetch_col_names.py:
    col_names_<release>.parquet  every name in the Catalogue of Life release
                                 (--col-names), with col_release.json next
                                 to it saying which release it is
and, only with --key-merge:
    species_key_merge.csv     old_key, kept_key, species, ... (from
                              make_species_key_merge.py)

Output, in --outdir:
    species_hex.parquet         how many records each species has in each
                                hexagon, per year. Columns: specieskey,
                                year, h3_r4, h3_r5, h3_r6, h3_r7,
                                occurrences.
    species_card_gbif.parquet   one row per species: the Catalogue of
                                Life name and authorship, taxonomy,
                                record counts, first and last year, number
                                of datasets, map extent, the species image
                                and the top 5 datasets, which keys were
                                merged into it, and (merged_names) the
                                Catalogue of Life name and authorship of
                                each merged key whose name is not the
                                card's own. This is also the separate
                                species table, so the taxonomy columns could
                                later be dropped from bc_occurrence.

The species card's name:
  scientificname is the name and authorship of the (kept) specieskey's own
  record in the Catalogue of Life release the GBIF download used, so it is
  always at species level and always matches the key. It is never taken
  from the occurrence records, which can carry a subspecies name, another
  spelling, or a DNA barcode cluster id (BOLD:...). The card has:
    col_name        the Catalogue of Life name (empty if the key is missing)
    col_authorship  its authorship (empty if it has none or the key is
                    missing)
    scientificname  col_name and col_authorship together; for a key missing
                    from the Catalogue of Life file, just the species name
    name_source     "Catalogue of Life, via GBIF", so the page can label the
                    author as coming from there; or a note that the key was
                    not found
    col_release     the release, e.g. "COL26.6 XR (ChecklistBank 315557)"
  The release the download used is read from download_info.json in
  --tables (col_release, written by build_dwca_tables.py from what
  request_gbif_download.py recorded). A download made before that was
  recorded needs it given with --col-release; for the current download it
  is 315557.

Merging keys (--key-merge):
  Some species names have two specieskeys in the Catalogue of Life, often
  the same species entered twice. make_species_key_merge.py lists the pairs
  to merge. As a safety check, every row is refused unless both of its keys
  have records in bc_occurrence under exactly one species name, and it is
  the same name for both keys, or else the Catalogue of Life file
  (--col-names) lists the old key's exact name and authorship as a synonym
  (not an ambiguous one) whose parent is the kept key: the rule
  make_species_key_merge.py applies to its hand-checked rows, checked again
  here with the same code (col_synonym_problems). Any refused row stops the
  run before anything is built. Such a card gets the old key's name and
  authorship in merged_names, so the page can show it. With --key-merge, every record of an
  old_key is counted under its kept_key before anything is built, so a
  merged species has one row in the card, one set of hexagons and its
  records summed. The card takes the kept key's own taxonomy and
  scientific name, the best image of either key (lowest license_rank, the
  kept key's own image on a tie), and lists the old keys in merged_keys.
  bc_occurrence itself is not changed. Without --key-merge every key is
  its own species, as before.

How species_hex is meant to be read:
  - The map for one species and a year range filters on specieskey and
    year, then groups by the h3 column of the zoom level and sums
    occurrences. Coarser resolutions are not stored as separate rows.
  - The overview map with a year filter groups the same way, and counts
    distinct specieskey for the number of species.
  - Records with no year have year = NULL. Any year filter leaves them out.
  - Every record is in it. build_bc_clean.py keeps only records with a
    species name, so almost every record has a specieskey (1 record in the
    October 2026 build does not). A record without one has specieskey =
    NULL and still counts in the overview map's records.
    count(DISTINCT specieskey) ignores NULL, so the species counts are not
    affected, and neither is a query for one species.
  - build_hex_aggregates.py builds the overview map (bc_hex_r4 to r7) from
    this file, so every species count on the site is distinct specieskey
    after the merge, and the merge happens in this script only.
  - The file is sorted by specieskey (NULL last) and then year, so the
    parquet row groups each hold a narrow range of species and a query for
    one species only reads a small part of the file.

A row is one species, year and combination of h3_r4 to h3_r7, not just one
h3_r7 cell: build_dwca_tables.py bins every resolution straight from the
coordinates, and H3 cells only nest approximately, so records in the same
h3_r7 cell can fall in different h3_r5 cells. Grouping by all four keeps
every resolution exactly as in bc_occurrence.

Checks, printed at the end (they are never written into the tables):
  - species_hex's occurrences add up to the records in bc_occurrence, and
    species_card_gbif's record counts to the records with a specieskey.
  - The card has one row per species (after the merge, if any).
  - For each of h3_r4 to h3_r7, the per-hexagon occurrences and distinct
    specieskey (after the merge) worked out straight from the records equal
    species_hex grouped the same way.
  - Every card key is in the Catalogue of Life file (expected: all of them),
    and the file is the release the download used.
  - For information only (counts and a few examples, never a FAIL): keys
    whose Catalogue of Life rank is not species, and keys whose Catalogue of
    Life name differs from the card's species column.

Outputs are only replaced when every check passes: both tables are written
to temporary names first (species_hex.tmp.parquet,
species_card_gbif.tmp.parquet), checked there, and moved onto the real names
only if nothing failed. If a check fails, the previous real files are left
untouched, the temporary files stay for inspection, and the script exits
with status 1.

Run order:
    request_gbif_download.py -> build_dwca_tables.py
        -> fetch_col_names.py (once per Catalogue of Life release)
        -> make_species_key_merge.py
        -> build_species_tables.py --key-merge --col-names
        -> build_hex_aggregates.py

Only plain SQL is used, so this runs on the cluster's DuckDB 0.10.3.

Run (from backend/, on the server):
    python pipeline/gbif/build_species_tables.py --tables ~/bcbn/data/gbif_tables \\
        --outdir ~/bcbn/data/gbif_tables/species_merged \\
        --key-merge ~/bcbn/data/gbif_tables/species_key_merge.csv \\
        --col-names ~/bcbn/data/col/315557/col_names_315557.parquet --col-release 315557 \\
        --memory-limit 8GB --threads 8 --temp-dir ~/tmp/duckdb_species
"""

import argparse
import csv
import json
import os
import sys
import time

import duckdb

from make_species_key_merge import col_synonym_problems, read_col_entries
from safe_outputs import (clear_temporary, replace_outputs, report_not_replaced,
                          temporary_path)

# The H3 resolutions stored on every record in bc_occurrence.
RESOLUTIONS = [4, 5, 6, 7]

# How many datasets the species card lists.
TOP_DATASETS = 5

# How many mismatching hexagons a failed check prints as examples.
EXAMPLES = 5


def connect(threads, memory_limit, temp_dir):
    """Open a duckdb connection with a thread count, a memory limit and,
    if given, a folder to spill to when the memory limit is reached."""
    con = duckdb.connect(config={"threads": threads, "memory_limit": memory_limit})
    if temp_dir:
        con.execute(f"SET temp_directory='{temp_dir}'")
    return con


def check_inputs(con, occurrence_path, image_path, datasets_path):
    """Stop with a clear message if an input file is missing or
    bc_occurrence lacks a column this script needs."""
    for path in (occurrence_path, image_path, datasets_path):
        if not os.path.exists(path):
            raise SystemExit(f"Input not found: {path}")
    columns = {
        row[0]
        for row in con.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{occurrence_path}')"
        ).fetchall()
    }
    needed = [
        "specieskey", "year", "datasetkey", "kingdom", "phylum",
        "class", "order", "family", "genus", "species",
        "decimallatitude", "decimallongitude",
    ] + [f"h3_r{r}" for r in RESOLUTIONS]
    missing = [c for c in needed if c not in columns]
    if missing:
        raise SystemExit(
            f"{occurrence_path} has no column(s) {missing}. This script reads "
            f"bc_occurrence.parquet from build_dwca_tables.py."
        )


def read_key_merge(path):
    """Read the merge list and return it as (old_key, kept_key, reason)
    rows. Stops if a column is missing, a key is mapped twice, or a key is
    mapped onto a key that is itself mapped away (the merge must be one
    step)."""
    with open(path, newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    missing = {"old_key", "kept_key"} - set(rows[0]) if rows else set()
    if missing:
        raise SystemExit(f"{path} has no column(s) {sorted(missing)}")
    pairs = [(r["old_key"].strip(), r["kept_key"].strip(),
              r.get("merge_reason") or r.get("reason") or "")
             for r in rows]
    olds = [old for old, _, _ in pairs]
    twice = sorted({k for k in olds if olds.count(k) > 1})
    chained = sorted({kept for _, kept, _ in pairs} & set(olds))
    if twice or chained:
        raise SystemExit(f"{path} is not a one-step merge: mapped twice {twice}, "
                         f"mapped onto a key that is also mapped away {chained}")
    return pairs


def check_merge_species(con, occurrence_path, merge_pairs, path, col_names_path):
    """Stop the run, listing every bad row, unless each merge row's two keys
    have records in bc_occurrence under exactly one species name each, and
    the two names are exactly the same or the old key's name is a Catalogue
    of Life synonym of the kept key (col_synonym_problems, the rule
    make_species_key_merge.py uses for hand-checked rows). A key with no
    records, or with more than one species name, makes its row bad too,
    because the names cannot be confirmed. Prints every row accepted as a
    synonym."""
    keys = sorted({k for old, kept, _ in merge_pairs for k in (old, kept)})
    if not keys:
        return
    listed = ", ".join(f"'{k}'" for k in keys)
    names = {
        key: found
        for key, found in con.execute(
            f"""SELECT specieskey, list(DISTINCT coalesce(species, '(no species name)'))
                FROM read_parquet('{occurrence_path}')
                WHERE specieskey IN ({listed})
                GROUP BY specieskey"""
        ).fetchall()
    }
    bad, differ = [], []
    for old, kept, _ in merge_pairs:
        problems = [f"{key} has no records in bc_occurrence" for key in (old, kept)
                    if key not in names]
        problems += [f"{key} has {len(names[key])} species names {sorted(names[key])}"
                     for key in (old, kept) if key in names and len(names[key]) != 1]
        if problems:
            bad.append(f"{old} -> {kept}: " + "; ".join(problems))
        elif names[old] != names[kept]:
            differ.append((old, kept))
    # Different names are only allowed for a Catalogue of Life synonym.
    if differ:
        by_id, synonyms_under = read_col_entries(col_names_path, [k for pair in differ for k in pair])
        for old, kept in differ:
            problems = col_synonym_problems(old, kept, by_id, synonyms_under)
            if problems:
                bad.append(f"{old} -> {kept}: species names differ: '{names[old][0]}' vs "
                           f"'{names[kept][0]}', and " + "; ".join(problems))
            else:
                print(f"Key merge: {old} ({names[old][0]}) -> {kept} ({names[kept][0]}) accepted: "
                      f"the Catalogue of Life lists the old name as a synonym of {kept}")
    if bad:
        raise SystemExit(f"{path}: {len(bad)} merge row(s) refused, the two keys of a merge "
                         f"must have exactly the same species name, or the old key's name "
                         f"must be a Catalogue of Life synonym of the kept key:\n  "
                         + "\n  ".join(bad))


def register_records(con, occurrence_path, merge_pairs):
    """Make the temp table key_merge (empty without a merge) and the temp
    view records: bc_occurrence with each old_key replaced by its kept_key,
    and the key it had in bc_occurrence kept as original_specieskey.
    Everything after this reads records, never bc_occurrence directly."""
    con.execute("CREATE OR REPLACE TEMP TABLE key_merge "
                "(old_key VARCHAR, kept_key VARCHAR, reason VARCHAR)")
    if merge_pairs:
        con.executemany("INSERT INTO key_merge VALUES (?, ?, ?)", merge_pairs)
    con.execute(
        f"""
        CREATE OR REPLACE TEMP VIEW records AS
        SELECT * EXCLUDE (old_key, kept_key, reason)
                 REPLACE (coalesce(kept_key, specieskey) AS specieskey),
               specieskey AS original_specieskey
        FROM read_parquet('{occurrence_path}') AS o
        LEFT JOIN key_merge AS m ON m.old_key = o.specieskey
        """
    )


def build_species_hex(con, out_path):
    """Write species_hex.parquet: records per species, year and hexagon,
    sorted by specieskey and then year. Records with no specieskey (rare,
    since build_bc_clean.py keeps only records with a species name) are
    kept, with specieskey = NULL, at the end of the file."""
    cells = ", ".join(f"h3_r{r}" for r in RESOLUTIONS)
    con.execute(
        f"""
        COPY (
            SELECT specieskey, year, {cells}, count(*)::BIGINT AS occurrences
            FROM records
            GROUP BY specieskey, year, {cells}
            ORDER BY specieskey NULLS LAST, year NULLS LAST, {cells}
        ) TO '{out_path}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )


def build_species_card(con, image_path, datasets_path, col_names_path, col_release_label,
                       out_path):
    """Write species_card_gbif.parquet: one row per specieskey with its
    names and taxonomy, record counts, years, map extent, image, top
    datasets and merged keys.

    A species' records can disagree on the taxonomy columns (rarely, e.g.
    after a backbone change, and always for a merged species), so the
    combination found on the kept key's own records is used, and among
    those the most common one.

    The name and authorship come from the kept key's own row in the
    Catalogue of Life file (col_names_path), never from the records. A key
    missing from that file gets its species name, an empty authorship and a
    name_source saying it was not found (the checks then FAIL).

    The image is the best of the images of the key and the keys merged
    into it: lowest license_rank, then the kept key's own image.

    merged_names lists, for each key merged into this one whose Catalogue
    of Life name is not the card's own (a synonym merge such as Accipiter
    cooperii into Astur cooperii), that key with its Catalogue of Life name
    and authorship, as key, name and authorship. It is NULL when there is
    none, which is every same-name merge.
    """
    con.execute(
        f"""
        COPY (
            WITH keyed AS (
                SELECT *, (original_specieskey = specieskey) AS own
                FROM records
                WHERE specieskey IS NOT NULL
            ),
            taxonomy AS (
                SELECT specieskey, kingdom, phylum, class, "order", family,
                       genus, species
                FROM keyed
                GROUP BY specieskey, kingdom, phylum, class, "order", family,
                         genus, species
                QUALIFY row_number() OVER (
                    PARTITION BY specieskey
                    ORDER BY max(CASE WHEN own THEN 1 ELSE 0 END) DESC,
                             count(*) DESC, kingdom, phylum, class, "order",
                             family, genus, species) = 1
            ),
            col AS (
                SELECT id, scientific_name, authorship
                FROM read_parquet('{col_names_path}')
                WHERE id IN (SELECT DISTINCT specieskey FROM keyed)
            ),
            stats AS (
                SELECT specieskey,
                       count(*)::BIGINT AS record_count,
                       count(*) FILTER (WHERE year IS NULL)::BIGINT
                           AS records_no_year,
                       min(year) AS first_year,
                       max(year) AS last_year,
                       count(DISTINCT datasetkey)::INTEGER AS n_datasets,
                       min(decimallatitude) AS min_lat,
                       max(decimallatitude) AS max_lat,
                       min(decimallongitude) AS min_lon,
                       max(decimallongitude) AS max_lon,
                       list(DISTINCT original_specieskey ORDER BY original_specieskey)
                           FILTER (WHERE NOT own) AS merged_keys,
                       count(*) FILTER (WHERE NOT own)::BIGINT AS merged_record_count
                FROM keyed
                GROUP BY specieskey
            ),
            images AS (
                SELECT coalesce(m.kept_key, i.specieskey) AS specieskey,
                       i.specieskey AS image_specieskey, i.identifier,
                       i."references", i.creator, i.rightsholder,
                       i.license_code, i.license_rank, i.license_source
                FROM read_parquet('{image_path}') AS i
                LEFT JOIN key_merge AS m ON m.old_key = i.specieskey
                QUALIFY row_number() OVER (
                    PARTITION BY coalesce(m.kept_key, i.specieskey)
                    ORDER BY i.license_rank,
                             CASE WHEN m.kept_key IS NULL THEN 0 ELSE 1 END,
                             i.gbifid) = 1
            ),
            dataset_counts AS (
                SELECT r.specieskey, r.datasetkey, d.title,
                       count(*)::BIGINT AS records
                FROM keyed AS r
                LEFT JOIN read_parquet('{datasets_path}') AS d
                    ON d.datasetkey = r.datasetkey
                GROUP BY r.specieskey, r.datasetkey, d.title
                QUALIFY row_number() OVER (
                    PARTITION BY r.specieskey
                    ORDER BY count(*) DESC, r.datasetkey) <= {TOP_DATASETS}
            ),
            top_datasets AS (
                SELECT specieskey,
                       list(struct_pack(datasetkey := datasetkey, title := title,
                                        records := records)
                            ORDER BY records DESC, datasetkey) AS top_datasets
                FROM dataset_counts
                GROUP BY specieskey
            ),
            merge_col AS (
                SELECT id, scientific_name, coalesce(authorship, '') AS authorship
                FROM read_parquet('{col_names_path}')
                WHERE id IN (SELECT old_key FROM key_merge)
                   OR id IN (SELECT kept_key FROM key_merge)
            ),
            merged_names AS (
                SELECT m.kept_key AS specieskey,
                       list(struct_pack(key := m.old_key, name := o.scientific_name,
                                        authorship := o.authorship)
                            ORDER BY m.old_key) AS merged_names
                FROM key_merge AS m
                JOIN merge_col AS o ON o.id = m.old_key
                JOIN merge_col AS k ON k.id = m.kept_key
                WHERE o.scientific_name <> k.scientific_name
                GROUP BY m.kept_key
            )
            SELECT s.specieskey,
                   CASE WHEN c.id IS NULL THEN t.species
                        ELSE trim(c.scientific_name || ' ' || coalesce(c.authorship, ''))
                   END AS scientificname,
                   c.scientific_name AS col_name,
                   coalesce(c.authorship, '') AS col_authorship,
                   CASE WHEN c.id IS NULL
                        THEN 'species name from the GBIF records; key not found in the Catalogue of Life'
                        ELSE 'Catalogue of Life, via GBIF'
                   END AS name_source,
                   '{col_release_label}' AS col_release,
                   t.species, t.kingdom,
                   t.phylum, t.class, t."order", t.family, t.genus,
                   s.record_count, s.records_no_year, s.first_year,
                   s.last_year, s.n_datasets,
                   s.min_lat, s.max_lat, s.min_lon, s.max_lon,
                   i.identifier AS image_identifier,
                   i."references" AS image_references,
                   i.creator AS image_creator,
                   i.rightsholder AS image_rightsholder,
                   i.license_code AS image_license_code,
                   i.license_rank AS image_license_rank,
                   i.license_source AS image_license_source,
                   i.image_specieskey,
                   d.top_datasets,
                   s.merged_keys, s.merged_record_count, n.merged_names
            FROM stats AS s
            LEFT JOIN taxonomy AS t ON t.specieskey = s.specieskey
            LEFT JOIN col AS c ON c.id = s.specieskey
            LEFT JOIN images AS i ON i.specieskey = s.specieskey
            LEFT JOIN top_datasets AS d ON d.specieskey = s.specieskey
            LEFT JOIN merged_names AS n ON n.specieskey = s.specieskey
            ORDER BY t.species, s.specieskey
        ) TO '{out_path}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )


def merge_summary(con):
    """Return (rows in the merge list, old keys found in bc_occurrence,
    records moved, species before the merge, species after)."""
    listed = con.execute("SELECT count(*) FROM key_merge").fetchone()[0]
    found, moved, before, after = con.execute(
        """SELECT count(DISTINCT original_specieskey)
                      FILTER (WHERE original_specieskey <> specieskey),
                  count(*) FILTER (WHERE original_specieskey <> specieskey),
                  count(DISTINCT original_specieskey),
                  count(DISTINCT specieskey)
           FROM records"""
    ).fetchone()
    return listed, found, moved, before, after


def read_col_release(col_names_path):
    """Return the release details in col_release.json next to the
    Catalogue of Life names file (written by fetch_col_names.py)."""
    path = os.path.join(os.path.dirname(os.path.abspath(col_names_path)), "col_release.json")
    if not os.path.exists(path):
        raise SystemExit(f"{path} not found: run fetch_col_names.py, which writes it next to "
                         f"the names file.")
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def expected_col_release(tables, given):
    """Return the ChecklistBank release the download used: from
    download_info.json in --tables, or --col-release for a download made
    before it was recorded there. Stops if neither has it, or if they
    disagree."""
    recorded = None
    path = os.path.join(tables, "download_info.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            recorded = (json.load(handle).get("col_release") or {}).get("checklistbank_key")
    if recorded and given and int(recorded) != int(given):
        raise SystemExit(f"--col-release {given} disagrees with download_info.json, which says "
                         f"the download used {recorded}.")
    if not (recorded or given):
        raise SystemExit("Which Catalogue of Life release did the download use? "
                         "download_info.json does not say; pass --col-release (315557 for "
                         "the current download).")
    return int(recorded or given)


def check_col_names(con, card_path, col_names_path, expected_release, file_release):
    """Return (results, info lines) for the Catalogue of Life names.

    results are (name, status, detail): FAIL if any card key is missing from
    the Catalogue of Life file, or if the file is not the release the
    download used. The info lines report, never as a FAIL, the keys whose
    Catalogue of Life rank is not species and the keys whose Catalogue of
    Life name differs from the card's species column, with a few examples.
    """
    missing = con.execute(
        f"""SELECT count(*), list(specieskey ORDER BY specieskey)[1:{EXAMPLES}]
            FROM read_parquet('{card_path}') WHERE col_name IS NULL"""
    ).fetchone()
    results = [
        ("every card key is in the Catalogue of Life file",
         "PASS" if missing[0] == 0 else "FAIL",
         f"{missing[0]:,} missing" + (f", e.g. {', '.join(missing[1])}" if missing[0] else "")),
        ("the Catalogue of Life file is the release the download used",
         "PASS" if file_release == expected_release else "FAIL",
         f"file {file_release}, download {expected_release}"),
    ]
    not_species = con.execute(
        f"""SELECT count(*) OVER (), c.specieskey, c.col_name, n.rank
            FROM read_parquet('{card_path}') AS c
            JOIN read_parquet('{col_names_path}') AS n ON n.id = c.specieskey
            WHERE n.rank IS DISTINCT FROM 'species'
            ORDER BY c.specieskey LIMIT {EXAMPLES}"""
    ).fetchall()
    differ = con.execute(
        f"""SELECT count(*) OVER (),
                   count(*) FILTER (WHERE regexp_replace(col_name, ' \\([^)]*\\)', '') = species) OVER (),
                   specieskey, col_name, species
            FROM read_parquet('{card_path}')
            WHERE col_name IS NOT NULL AND col_name IS DISTINCT FROM species
            -- Examples that differ by more than a subgenus come first.
            ORDER BY CASE WHEN regexp_replace(col_name, ' \\([^)]*\\)', '') = species
                          THEN 1 ELSE 0 END, specieskey
            LIMIT {EXAMPLES}"""
    ).fetchall()
    info = [f"  INFO  Catalogue of Life rank not species: {not_species[0][0] if not_species else 0:,} keys"]
    info += [f"          {key}: {name} ({rank})" for _, key, name, rank in not_species]
    info.append(f"  INFO  Catalogue of Life name differs from the species column: "
                f"{differ[0][0] if differ else 0:,} keys"
                + (f" ({differ[0][1]:,} of them only by a subgenus in brackets)" if differ else ""))
    info += [f"          {key}: '{name}' vs species '{species}'" for _, _, key, name, species in differ]
    return results, info


def check_totals(con, species_hex_path, card_path):
    """Return (name, status, detail) results for the record totals:
    species_hex against all records in bc_occurrence, species_card_gbif
    against the records with a specieskey, and one card row per species."""
    with_key, no_key, n_species = con.execute(
        """SELECT count(specieskey), count(*) - count(specieskey),
                  count(DISTINCT specieskey)
           FROM records"""
    ).fetchone()
    hex_total, hex_no_key = con.execute(
        f"""SELECT coalesce(sum(occurrences), 0),
                   coalesce(sum(occurrences) FILTER (WHERE specieskey IS NULL), 0)
            FROM read_parquet('{species_hex_path}')"""
    ).fetchone()
    card_total, card_rows = con.execute(
        f"""SELECT coalesce(sum(record_count), 0), count(*)
            FROM read_parquet('{card_path}')"""
    ).fetchone()

    def status(ok):
        return "PASS" if ok else "FAIL"

    return [
        ("species_hex occurrences = records in bc_occurrence",
         status(hex_total == with_key + no_key), f"{hex_total:,} vs {with_key + no_key:,}"),
        ("species_hex occurrences with specieskey NULL = records with no specieskey",
         status(hex_no_key == no_key), f"{hex_no_key:,} vs {no_key:,}"),
        ("species_card_gbif record_count total = records with a specieskey",
         status(card_total == with_key), f"{card_total:,} vs {with_key:,}"),
        ("species_card_gbif has one row per species",
         status(card_rows == n_species), f"{card_rows:,} rows vs {n_species:,} species"),
    ]


def check_hex_against_records(con, species_hex_path, resolution):
    """Check species_hex against the records for one resolution.

    For each h3_r{resolution} hexagon, the occurrences and the distinct
    specieskey (after the merge) are worked out twice: straight from the
    records, and from species_hex grouped by that column. Records with no
    cell are compared as one group of their own. Returns (hexagons,
    hexagons whose occurrences differ, hexagons whose species differ,
    examples), where examples are up to EXAMPLES rows of (cell, occurrences
    from the records, from species_hex, species from the records, from
    species_hex).
    """
    col = f"h3_r{resolution}"
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE hex_compare AS
        WITH from_records AS (
            SELECT coalesce({col}, '(no cell)') AS h3_cell, count(*) AS occurrences,
                   count(DISTINCT specieskey) AS n_species
            FROM records
            GROUP BY 1
        ),
        from_species_hex AS (
            SELECT coalesce({col}, '(no cell)') AS h3_cell, sum(occurrences) AS occurrences,
                   count(DISTINCT specieskey) AS n_species
            FROM read_parquet('{species_hex_path}')
            GROUP BY 1
        ),
        cells AS (
            SELECT h3_cell FROM from_records
            UNION SELECT h3_cell FROM from_species_hex
        )
        SELECT c.h3_cell,
               coalesce(r.occurrences, 0) AS record_occurrences,
               coalesce(h.occurrences, 0) AS hex_occurrences,
               coalesce(r.n_species, 0) AS record_species,
               coalesce(h.n_species, 0) AS hex_species
        FROM cells AS c
        LEFT JOIN from_records AS r ON r.h3_cell = c.h3_cell
        LEFT JOIN from_species_hex AS h ON h.h3_cell = c.h3_cell
        """
    )
    cells, occ_bad, species_bad = con.execute(
        """SELECT count(*),
                  count(*) FILTER (WHERE record_occurrences <> hex_occurrences),
                  count(*) FILTER (WHERE record_species <> hex_species)
           FROM hex_compare"""
    ).fetchone()
    examples = con.execute(
        f"""SELECT * FROM hex_compare
            WHERE record_occurrences <> hex_occurrences OR record_species <> hex_species
            ORDER BY h3_cell LIMIT {EXAMPLES}"""
    ).fetchall()
    con.execute("DROP TABLE hex_compare")
    return cells, occ_bad, species_bad, examples


def names_with_several_keys(con):
    """Return species names that still have more than one specieskey (after
    the merge, if any), or keys with more than one name, as (species,
    specieskeys) and (specieskey, names). Printed as a note only: these are
    the names the site shows as more than one species."""
    names = con.execute(
        """SELECT species, list(DISTINCT specieskey)
           FROM records
           WHERE specieskey IS NOT NULL AND species IS NOT NULL
           GROUP BY species HAVING count(DISTINCT specieskey) > 1
           ORDER BY species"""
    ).fetchall()
    keys = con.execute(
        """SELECT specieskey, list(DISTINCT species)
           FROM records
           WHERE specieskey IS NOT NULL
           GROUP BY specieskey
           HAVING count(DISTINCT species) <> 1
           ORDER BY specieskey"""
    ).fetchall()
    return names, keys


def file_size(path):
    """Return a file's size as text in KB or MB."""
    size = os.path.getsize(path)
    if size < 1024 * 1024:
        return f"{size / 1024:,.1f} KB"
    return f"{size / 1024 / 1024:,.1f} MB"


def main():
    """Build species_hex and species_card_gbif under temporary names, check
    them, and replace the real files only if every check passes."""
    parser = argparse.ArgumentParser(
        description="Build the species search and species card tables from "
        "the tables build_dwca_tables.py writes."
    )
    parser.add_argument(
        "--tables",
        required=True,
        help="Folder with bc_occurrence.parquet, bc_species_image.parquet and "
        "bc_datasets.parquet.",
    )
    parser.add_argument("--outdir", required=True, help="Folder to write the tables into.")
    parser.add_argument(
        "--key-merge",
        default=None,
        help="species_key_merge.csv from make_species_key_merge.py. When given, "
        "each old_key's records are counted under its kept_key. Off by default.",
    )
    parser.add_argument(
        "--col-names",
        required=True,
        help="col_names_<release>.parquet from fetch_col_names.py: the Catalogue of Life "
        "release the download used. The card's name and authorship come from it.",
    )
    parser.add_argument(
        "--col-release",
        type=int,
        default=None,
        help="The ChecklistBank release the download used, for a download whose "
        "download_info.json does not record it (315557 for the current download).",
    )
    parser.add_argument(
        "--memory-limit",
        default="10GB",
        help="Most memory DuckDB may use before it spills to --temp-dir "
        "(default 10GB). Keep it below the memory your job is given.",
    )
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument(
        "--temp-dir",
        default=None,
        help="Where DuckDB spills to when it reaches --memory-limit "
        "(default: DuckDB's own choice).",
    )
    args = parser.parse_args()

    occurrence_path = os.path.join(args.tables, "bc_occurrence.parquet")
    image_path = os.path.join(args.tables, "bc_species_image.parquet")
    datasets_path = os.path.join(args.tables, "bc_datasets.parquet")
    species_hex_path = os.path.join(args.outdir, "species_hex.parquet")
    card_path = os.path.join(args.outdir, "species_card_gbif.parquet")
    outputs = [species_hex_path, card_path]
    # Everything is built and checked under the temporary names; the real
    # files are only touched at the very end, if every check passed.
    hex_tmp, card_tmp = temporary_path(species_hex_path), temporary_path(card_path)

    con = connect(args.threads, args.memory_limit, args.temp_dir)
    check_inputs(con, occurrence_path, image_path, datasets_path)
    col_names_path = os.path.expanduser(args.col_names)
    if not os.path.exists(col_names_path):
        raise SystemExit(f"{col_names_path} not found: run fetch_col_names.py first.")
    expected_release = expected_col_release(args.tables, args.col_release)
    release = read_col_release(col_names_path)
    col_release_label = f"{release.get('alias')} (ChecklistBank {release.get('key')})".replace("'", "")
    merge_pairs = read_key_merge(args.key_merge) if args.key_merge else []
    check_merge_species(con, occurrence_path, merge_pairs, args.key_merge, col_names_path)
    register_records(con, occurrence_path, merge_pairs)
    os.makedirs(args.outdir, exist_ok=True)
    clear_temporary(outputs)

    print(f"Tables:    {args.tables}")
    print(f"Key merge: {args.key_merge or 'none (every key is its own species)'}")
    print(f"COL names: {col_names_path} ({col_release_label}; the download used "
          f"{expected_release})")
    print(f"Output:    {args.outdir}\n")
    t_all = time.time()

    t = time.time()
    build_species_hex(con, hex_tmp)
    print(f"species_hex built in {time.time() - t:.1f}s")
    t = time.time()
    build_species_card(con, image_path, datasets_path, col_names_path, col_release_label,
                       card_tmp)
    print(f"species_card_gbif built in {time.time() - t:.1f}s")

    # Checks, all on the temporary files.
    results = check_totals(con, hex_tmp, card_tmp)
    col_results, col_info = check_col_names(con, card_tmp, col_names_path, expected_release,
                                            int(release.get("key")))
    results += col_results
    hex_lines = []
    for r in RESOLUTIONS:
        cells, occ_bad, species_bad, examples = check_hex_against_records(con, hex_tmp, r)
        results.append((f"h3_r{r} occurrences per hexagon: records = species_hex",
                        "PASS" if occ_bad == 0 else "FAIL",
                        f"{occ_bad:,} of {cells:,} hexagons differ"))
        results.append((f"h3_r{r} distinct specieskey per hexagon: records = species_hex",
                        "PASS" if species_bad == 0 else "FAIL",
                        f"{species_bad:,} of {cells:,} hexagons differ"))
        for row in examples:
            hex_lines.append(f"    r{r} {row[0]}: occurrences {row[1]:,} vs {row[2]:,}, "
                             f"species {row[3]:,} vs {row[4]:,}")

    # Summary.
    hex_rows, hex_no_year, hex_no_key, hex_species = con.execute(
        f"""SELECT count(*), count(*) FILTER (WHERE year IS NULL),
                   count(*) FILTER (WHERE specieskey IS NULL),
                   count(DISTINCT specieskey)
            FROM read_parquet('{hex_tmp}')"""
    ).fetchone()
    card_rows, with_image, ranks, merged_rows, image_from_old = con.execute(
        f"""SELECT count(*), count(image_identifier),
                   list(DISTINCT image_license_rank ORDER BY image_license_rank)
                       FILTER (WHERE image_license_rank IS NOT NULL),
                   count(*) FILTER (WHERE merged_keys IS NOT NULL),
                   count(*) FILTER (WHERE image_specieskey <> specieskey)
            FROM read_parquet('{card_tmp}')"""
    ).fetchone()

    print(f"\nspecies_hex.parquet:       {hex_rows:>12,} rows  {file_size(hex_tmp):>10}"
          f"  ({hex_species:,} species; {hex_no_year:,} rows with no year, "
          f"{hex_no_key:,} with no specieskey)")
    print(f"species_card_gbif.parquet: {card_rows:>12,} rows  {file_size(card_tmp):>10}")
    pct = 100 * with_image / card_rows if card_rows else 0
    print(f"  species with an image:   {with_image:>12,} ({pct:.1f}%), "
          f"license ranks used: {ranks}")
    if args.key_merge:
        listed, found, moved, before, after = merge_summary(con)
        print(f"\nKey merge: {listed:,} pairs listed, {found:,} of their old keys are in "
              f"bc_occurrence, {moved:,} records moved to the kept key")
        print(f"  species: {before:,} keys before the merge, {after:,} after "
              f"({before - after:,} fewer); {merged_rows:,} card rows have merged_keys, "
              f"{image_from_old:,} took their image from a merged key")

    print("\nChecks:")
    for name, status, detail in results:
        print(f"  {status:<4}  {name}: {detail}")
    print("\n".join(col_info))
    if hex_lines:
        print("  Hexagons that differ (from the records vs from species_hex):")
        print("\n".join(hex_lines))
    names, keys = names_with_several_keys(con)
    if names or keys:
        print(f"  Note: {len(names):,} species name(s) still have more than one "
              f"specieskey and {len(keys):,} specieskey(s) have no single name; the site "
              f"counts each key as a species.")
        for species, specieskeys in names[:EXAMPLES]:
            print(f"    {species}: {specieskeys}")
        for specieskey, species in keys[:EXAMPLES]:
            print(f"    {specieskey}: {species}")
    con.close()

    if any(status == "FAIL" for _, status, _ in results):
        report_not_replaced(outputs)
        print(f"\nDone in {time.time() - t_all:.1f}s, with a FAILED check")
        sys.exit(1)
    replace_outputs(outputs)
    print("\nAll checks passed; replaced:")
    for path in outputs:
        print(f"  {path}")
    print(f"\nDone in {time.time() - t_all:.1f}s")


if __name__ == "__main__":
    main()
