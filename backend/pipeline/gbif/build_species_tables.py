"""
build_species_tables.py

Build the two tables behind species search and the species card: where
each species was recorded, by year, and one summary row per species.

Input, all in --tables (written by build_dwca_tables.py):
    bc_occurrence.parquet     one row per record, with its H3 cells
    bc_species_image.parquet  one image per species
    bc_datasets.parquet       one row per dataset, with its title
and, for a check only, in --hex-dir (default: the --tables folder):
    bc_hex_r4.parquet ... bc_hex_r7.parquet  (from build_hex_aggregates.py)
and, only with --key-merge:
    species_key_merge.csv     old_key, kept_key, species, reason (from
                              make_species_key_merge.py)

Output, in --outdir:
    species_hex.parquet         how many records each species has in each
                                hexagon, per year. Columns: specieskey,
                                year, h3_r4, h3_r5, h3_r6, h3_r7,
                                occurrences.
    species_card_gbif.parquet   one row per species: names and taxonomy,
                                record counts, first and last year, number
                                of datasets, map extent, the species image
                                and the top 5 datasets, and which keys were
                                merged into it. This is also the separate
                                species table, so the taxonomy columns could
                                later be dropped from bc_occurrence.

Merging keys (--key-merge):
  Some species names have two specieskeys in the Catalogue of Life, often
  the same species entered twice. make_species_key_merge.py lists the pairs
  that are surely the same species. With --key-merge, every record of an
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
  - Every record is in it. Records with no specieskey (identified only to
    genus or above) have specieskey = NULL, so the overview map counts
    every record, as bc_hex_r* does. count(DISTINCT specieskey) ignores
    NULL, so the species counts are not affected, and neither is a query
    for one species.
  - The file is sorted by specieskey (NULL last) and then year, so the
    parquet row groups each hold a narrow range of species and a query for
    one species only reads a small part of the file.

A row is one species, year and combination of h3_r4 to h3_r7, not just one
h3_r7 cell: build_dwca_tables.py bins every resolution straight from the
coordinates, and H3 cells only nest approximately, so records in the same
h3_r7 cell can fall in different h3_r5 cells. Grouping by all four keeps
every resolution exactly as in bc_occurrence.

Checks, printed at the end (the script exits with status 1 if one fails):
  - species_hex's occurrences add up to the records in bc_occurrence, and
    species_card_gbif's record counts to the records with a specieskey.
  - The card has one row per species (after the merge, if any).
  - For each of h3_r4 to h3_r7, grouping species_hex by that column gives
    the same per-hexagon occurrences as bc_hex_rN.
  - The distinct species per hexagon are compared with bc_hex_rN's
    distinct_species. bc_hex counts distinct species names, not keys, so a
    name with two keys counts once there and twice here (when both keys
    are in the hexagon). That difference is expected: the check works out,
    for each hexagon, how much it should be from the names that still have
    more than one key, and reports EXPECTED if every hexagon matches that.
    It also says how much bigger the difference would be without the merge.
  If the bc_hex files are not found, the hexagon checks are skipped with a
  warning.

Only plain SQL is used, so this runs on the cluster's DuckDB 0.10.3.

Run:
    python build_species_tables.py --tables ~/bcbn/data/gbif_tables --outdir ~/bcbn/data/gbif_tables
    python build_species_tables.py --tables ~/bcbn/data/gbif_tables --outdir ~/bcbn/data/gbif_tables \\
        --key-merge ~/bcbn/data/gbif_tables/species_key_merge.csv
"""

import argparse
import csv
import os
import sys
import time

import duckdb

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
        "specieskey", "taxonkey", "year", "datasetkey", "kingdom", "phylum",
        "class", "order", "family", "genus", "species", "scientificname",
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
    pairs = [(r["old_key"].strip(), r["kept_key"].strip(), r.get("reason", ""))
             for r in rows]
    olds = [old for old, _, _ in pairs]
    twice = sorted({k for k in olds if olds.count(k) > 1})
    chained = sorted({kept for _, kept, _ in pairs} & set(olds))
    if twice or chained:
        raise SystemExit(f"{path} is not a one-step merge: mapped twice {twice}, "
                         f"mapped onto a key that is also mapped away {chained}")
    return pairs


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
    sorted by specieskey and then year. Records with no specieskey are kept,
    with specieskey = NULL, at the end of the file."""
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


def build_species_card(con, image_path, datasets_path, out_path):
    """Write species_card_gbif.parquet: one row per specieskey with its
    names and taxonomy, record counts, years, map extent, image, top
    datasets and merged keys.

    A species' records can disagree on the taxonomy columns (rarely, e.g.
    after a backbone change, and always for a merged species), so the
    combination found on the kept key's own records is used, and among
    those the most common one. scientificname is taken from the kept key's
    own records identified to the species itself (taxonkey = the record's
    own specieskey), because a record identified to a subspecies carries
    the subspecies' name; failing that, from its other records.

    The image is the best of the images of the key and the keys merged
    into it: lowest license_rank, then the kept key's own image.
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
            sciname AS (
                SELECT specieskey, scientificname
                FROM keyed
                WHERE scientificname IS NOT NULL
                GROUP BY specieskey, scientificname
                QUALIFY row_number() OVER (
                    PARTITION BY specieskey
                    ORDER BY max(CASE WHEN own THEN 1 ELSE 0 END) DESC,
                             max(CASE WHEN taxonkey = original_specieskey
                                      THEN 1 ELSE 0 END) DESC,
                             count(*) DESC, scientificname) = 1
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
            )
            SELECT s.specieskey, n.scientificname, t.species, t.kingdom,
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
                   s.merged_keys, s.merged_record_count
            FROM stats AS s
            LEFT JOIN taxonomy AS t ON t.specieskey = s.specieskey
            LEFT JOIN sciname AS n ON n.specieskey = s.specieskey
            LEFT JOIN images AS i ON i.specieskey = s.specieskey
            LEFT JOIN top_datasets AS d ON d.specieskey = s.specieskey
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


def check_hex(con, species_hex_path, hex_path, resolution):
    """Compare species_hex grouped by h3_r{resolution} with bc_hex_r{resolution}.

    For the distinct species, bc_hex counts names and species_hex counts
    keys, so in each hexagon the expected difference (ours - bc_hex) is:
    for each name, its number of keys in the hexagon minus 1 (or -1 for a
    name whose records there all lack a key), plus the keys whose records
    have no name. The same sum over the keys before the merge says how big
    the difference would be without it.

    Returns a dict with: cells; occ_bad (hexagons whose occurrences
    differ); species_bad (hexagons whose distinct species differ);
    unexplained (hexagons whose difference is not the expected one);
    extra (total difference, ours - bc_hex, over all hexagons);
    extra_unmerged (the same without the merge); examples (up to EXAMPLES
    rows of h3_cell, our and bc_hex's occurrences, our and bc_hex's
    distinct species, the expected difference).
    """
    col = f"h3_r{resolution}"
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE hex_compare AS
        WITH ours AS (
            SELECT {col} AS h3_cell, sum(occurrences) AS occurrences,
                   count(DISTINCT specieskey) AS n_species
            FROM read_parquet('{species_hex_path}')
            WHERE {col} IS NOT NULL
            GROUP BY {col}
        ),
        theirs AS (
            SELECT h3_cell, occurrences, distinct_species
            FROM read_parquet('{hex_path}')
        ),
        per_name AS (
            SELECT {col} AS h3_cell, species,
                   count(DISTINCT specieskey) AS k,
                   count(DISTINCT original_specieskey) AS k_unmerged
            FROM records
            WHERE {col} IS NOT NULL
            GROUP BY {col}, species
        ),
        expected AS (
            SELECT h3_cell,
                   sum(CASE WHEN species IS NULL THEN k
                            WHEN k = 0 THEN -1 ELSE k - 1 END) AS extra,
                   sum(CASE WHEN species IS NULL THEN k_unmerged
                            WHEN k_unmerged = 0 THEN -1
                            ELSE k_unmerged - 1 END) AS extra_unmerged
            FROM per_name
            GROUP BY h3_cell
        ),
        cells AS (
            SELECT h3_cell FROM ours
            UNION SELECT h3_cell FROM theirs
        )
        SELECT c.h3_cell,
               coalesce(o.occurrences, 0) AS our_occurrences,
               coalesce(t.occurrences, 0) AS their_occurrences,
               coalesce(o.n_species, 0) AS our_species,
               coalesce(t.distinct_species, 0) AS their_species,
               coalesce(e.extra, 0) AS expected_extra,
               coalesce(e.extra_unmerged, 0) AS expected_extra_unmerged
        FROM cells AS c
        LEFT JOIN ours AS o ON o.h3_cell = c.h3_cell
        LEFT JOIN theirs AS t ON t.h3_cell = c.h3_cell
        LEFT JOIN expected AS e ON e.h3_cell = c.h3_cell
        """
    )
    row = con.execute(
        """SELECT count(*),
                  count(*) FILTER (WHERE our_occurrences <> their_occurrences),
                  count(*) FILTER (WHERE our_species <> their_species),
                  count(*) FILTER (WHERE our_species - their_species <> expected_extra),
                  coalesce(sum(our_species - their_species), 0),
                  coalesce(sum(expected_extra_unmerged), 0)
           FROM hex_compare"""
    ).fetchone()
    examples = con.execute(
        f"""SELECT h3_cell, our_occurrences, their_occurrences, our_species,
                   their_species, expected_extra
            FROM hex_compare
            WHERE our_occurrences <> their_occurrences
               OR our_species - their_species <> expected_extra
            ORDER BY h3_cell LIMIT {EXAMPLES}"""
    ).fetchall()
    con.execute("DROP TABLE hex_compare")
    keys = ["cells", "occ_bad", "species_bad", "unexplained", "extra", "extra_unmerged"]
    return {**dict(zip(keys, row)), "examples": examples}


def names_with_several_keys(con):
    """Return species names that still have more than one specieskey (after
    the merge, if any), or keys with more than one name, as (species,
    specieskeys) and (specieskey, names). Either one makes distinct
    specieskey differ from bc_hex's distinct species names in a hexagon."""
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
    """Build species_hex and species_card_gbif, check them and print a
    summary."""
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
        "--hex-dir",
        default=None,
        help="Folder with bc_hex_r4.parquet to bc_hex_r7.parquet from "
        "build_hex_aggregates.py, used only for a check (default: --tables). "
        "If they are not there, the check is skipped.",
    )
    parser.add_argument(
        "--key-merge",
        default=None,
        help="species_key_merge.csv from make_species_key_merge.py. When given, "
        "each old_key's records are counted under its kept_key. Off by default.",
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

    hex_dir = args.hex_dir or args.tables
    occurrence_path = os.path.join(args.tables, "bc_occurrence.parquet")
    image_path = os.path.join(args.tables, "bc_species_image.parquet")
    datasets_path = os.path.join(args.tables, "bc_datasets.parquet")
    species_hex_path = os.path.join(args.outdir, "species_hex.parquet")
    card_path = os.path.join(args.outdir, "species_card_gbif.parquet")

    con = connect(args.threads, args.memory_limit, args.temp_dir)
    check_inputs(con, occurrence_path, image_path, datasets_path)
    merge_pairs = read_key_merge(args.key_merge) if args.key_merge else []
    register_records(con, occurrence_path, merge_pairs)
    os.makedirs(args.outdir, exist_ok=True)

    print(f"Tables:    {args.tables}")
    print(f"Hex dir:   {hex_dir}")
    print(f"Key merge: {args.key_merge or 'none (every key is its own species)'}")
    print(f"Output:    {args.outdir}\n")
    t_all = time.time()

    t = time.time()
    build_species_hex(con, species_hex_path)
    print(f"species_hex built in {time.time() - t:.1f}s")
    t = time.time()
    build_species_card(con, image_path, datasets_path, card_path)
    print(f"species_card_gbif built in {time.time() - t:.1f}s")

    # Checks.
    results = check_totals(con, species_hex_path, card_path)
    hex_lines = []
    extra_lines = []
    missing_hex = [
        r for r in RESOLUTIONS
        if not os.path.exists(os.path.join(hex_dir, f"bc_hex_r{r}.parquet"))
    ]
    for r in RESOLUTIONS:
        hex_path = os.path.join(hex_dir, f"bc_hex_r{r}.parquet")
        if r in missing_hex:
            continue
        c = check_hex(con, species_hex_path, hex_path, r)
        results.append((f"h3_r{r} occurrences per hexagon = bc_hex_r{r}",
                        "PASS" if c["occ_bad"] == 0 else "FAIL",
                        f"{c['occ_bad']:,} of {c['cells']:,} hexagons differ"))
        if c["species_bad"] == 0:
            status = "PASS"
        elif c["unexplained"] == 0:
            status = "EXPECTED"
        else:
            status = "FAIL"
        results.append((f"h3_r{r} distinct species per hexagon vs bc_hex_r{r}", status,
                        f"{c['species_bad']:,} of {c['cells']:,} hexagons differ, "
                        f"{c['extra']:+,} species in total; "
                        f"{c['unexplained']:,} not explained by names with several keys"))
        extra_lines.append(
            f"    r{r}: {c['extra']:+,} with the merge, {c['extra_unmerged']:+,} without it"
        )
        for row in c["examples"]:
            hex_lines.append(
                f"    r{r} {row[0]}: occurrences {row[1]:,} vs {row[2]:,}, "
                f"species {row[3]:,} vs {row[4]:,} (expected difference {row[5]:+,})"
            )

    # Summary.
    hex_rows, hex_no_year, hex_no_key, hex_species = con.execute(
        f"""SELECT count(*), count(*) FILTER (WHERE year IS NULL),
                   count(*) FILTER (WHERE specieskey IS NULL),
                   count(DISTINCT specieskey)
            FROM read_parquet('{species_hex_path}')"""
    ).fetchone()
    card_rows, with_image, ranks, merged_rows, image_from_old = con.execute(
        f"""SELECT count(*), count(image_identifier),
                   list(DISTINCT image_license_rank ORDER BY image_license_rank)
                       FILTER (WHERE image_license_rank IS NOT NULL),
                   count(*) FILTER (WHERE merged_keys IS NOT NULL),
                   count(*) FILTER (WHERE image_specieskey <> specieskey)
            FROM read_parquet('{card_path}')"""
    ).fetchone()

    print(f"\nspecies_hex.parquet:       {hex_rows:>12,} rows  {file_size(species_hex_path):>10}"
          f"  ({hex_species:,} species; {hex_no_year:,} rows with no year, "
          f"{hex_no_key:,} with no specieskey)")
    print(f"species_card_gbif.parquet: {card_rows:>12,} rows  {file_size(card_path):>10}")
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
        print(f"  {status:<8}  {name}: {detail}")
    if missing_hex:
        print(f"  WARNING: skipped the bc_hex check for resolution(s) "
              f"{missing_hex}: bc_hex_rN.parquet not found in {hex_dir}. "
              f"Pass --hex-dir to point at them.")
    if extra_lines:
        print("  bc_hex counts species names and species_hex counts keys, so a name "
              "with two keys")
        print("  counts once in bc_hex and twice here. Extra species over all hexagons:")
        print("\n".join(extra_lines))
    if hex_lines:
        print("  Hexagons whose occurrences differ, or whose species differ by more "
              "than expected (ours vs bc_hex):")
        print("\n".join(hex_lines))
        print("  If the occurrences differ, the bc_hex files were probably built "
              "from a different bc_occurrence.")
    names, keys = names_with_several_keys(con)
    if names or keys:
        print(f"  Note: {len(names):,} species name(s) still have more than one "
              f"specieskey and {len(keys):,} specieskey(s) have no single name.")
        for species, specieskeys in names[:EXAMPLES]:
            print(f"    {species}: {specieskeys}")
        for specieskey, species in keys[:EXAMPLES]:
            print(f"    {specieskey}: {species}")

    print(f"\nDone in {time.time() - t_all:.1f}s")
    con.close()
    if any(status == "FAIL" for _, status, _ in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
