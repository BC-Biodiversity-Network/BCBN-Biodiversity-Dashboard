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

Output, in --outdir:
    species_hex.parquet         how many records each species has in each
                                hexagon, per year. Columns: specieskey,
                                year, h3_r4, h3_r5, h3_r6, h3_r7,
                                occurrences.
    species_card_gbif.parquet   one row per species: names and taxonomy,
                                record counts, first and last year, number
                                of datasets, map extent, the species image
                                and the top 5 datasets. This is also the
                                separate species table, so the taxonomy
                                columns could later be dropped from
                                bc_occurrence.

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
  - For each of h3_r4 to h3_r7, grouping species_hex by that column gives
    the same per-hexagon occurrences as bc_hex_rN, and counting distinct
    specieskey gives bc_hex's distinct_species. (bc_hex counts distinct
    species names, which match specieskeys one to one unless a name has
    two keys or a key has no single name; any such are printed.) If the
    bc_hex files are not found, this check is skipped with a warning.

Only plain SQL is used, so this runs on the cluster's DuckDB 0.10.3.

Run:
    python build_species_tables.py --tables ~/bcbn/data/gbif_tables --outdir ~/bcbn/data/gbif_tables
"""

import argparse
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


def build_species_hex(con, occurrence_path, out_path):
    """Write species_hex.parquet: records per species, year and hexagon,
    sorted by specieskey and then year. Records with no specieskey are kept,
    with specieskey = NULL, at the end of the file."""
    cells = ", ".join(f"h3_r{r}" for r in RESOLUTIONS)
    con.execute(
        f"""
        COPY (
            SELECT specieskey, year, {cells}, count(*)::BIGINT AS occurrences
            FROM read_parquet('{occurrence_path}')
            GROUP BY specieskey, year, {cells}
            ORDER BY specieskey NULLS LAST, year NULLS LAST, {cells}
        ) TO '{out_path}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )


def build_species_card(con, occurrence_path, image_path, datasets_path, out_path):
    """Write species_card_gbif.parquet: one row per specieskey with its
    names and taxonomy, record counts, years, map extent, image and top
    datasets.

    A species' records can disagree on the taxonomy columns (rarely, e.g.
    after a backbone change), so the most common combination is used.
    scientificname is taken from records identified to the species itself
    (taxonkey = specieskey), because a record identified to a subspecies
    carries the subspecies' name; if there are none, from all its records.
    """
    con.execute(
        f"""
        COPY (
            WITH records AS (
                SELECT * FROM read_parquet('{occurrence_path}')
                WHERE specieskey IS NOT NULL
            ),
            taxonomy AS (
                SELECT specieskey, kingdom, phylum, class, "order", family,
                       genus, species
                FROM records
                GROUP BY specieskey, kingdom, phylum, class, "order", family,
                         genus, species
                QUALIFY row_number() OVER (
                    PARTITION BY specieskey
                    ORDER BY count(*) DESC, kingdom, phylum, class, "order",
                             family, genus, species) = 1
            ),
            sciname AS (
                SELECT specieskey, scientificname
                FROM records
                WHERE scientificname IS NOT NULL
                GROUP BY specieskey, scientificname
                QUALIFY row_number() OVER (
                    PARTITION BY specieskey
                    ORDER BY max(CASE WHEN taxonkey = specieskey THEN 1 ELSE 0 END) DESC,
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
                       max(decimallongitude) AS max_lon
                FROM records
                GROUP BY specieskey
            ),
            dataset_counts AS (
                SELECT r.specieskey, r.datasetkey, d.title,
                       count(*)::BIGINT AS records
                FROM records AS r
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
                   d.top_datasets
            FROM stats AS s
            LEFT JOIN taxonomy AS t ON t.specieskey = s.specieskey
            LEFT JOIN sciname AS n ON n.specieskey = s.specieskey
            LEFT JOIN read_parquet('{image_path}') AS i
                ON i.specieskey = s.specieskey
            LEFT JOIN top_datasets AS d ON d.specieskey = s.specieskey
            ORDER BY t.species, s.specieskey
        ) TO '{out_path}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )


def check_totals(con, occurrence_path, species_hex_path, card_path):
    """Return (name, passed, detail) results for the record totals:
    species_hex against all records in bc_occurrence, species_card_gbif
    against the records with a specieskey."""
    with_key, no_key = con.execute(
        f"""SELECT count(specieskey), count(*) - count(specieskey)
            FROM read_parquet('{occurrence_path}')"""
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
    n_species = con.execute(
        f"""SELECT count(DISTINCT specieskey) FROM read_parquet('{occurrence_path}')"""
    ).fetchone()[0]
    return [
        ("species_hex occurrences = records in bc_occurrence",
         hex_total == with_key + no_key,
         f"{hex_total:,} vs {with_key + no_key:,}"),
        ("species_hex occurrences with specieskey NULL = records with no specieskey",
         hex_no_key == no_key, f"{hex_no_key:,} vs {no_key:,}"),
        ("species_card_gbif record_count total = records with a specieskey",
         card_total == with_key, f"{card_total:,} vs {with_key:,}"),
        ("species_card_gbif has one row per specieskey",
         card_rows == n_species, f"{card_rows:,} rows vs {n_species:,} species"),
    ]


def check_hex(con, species_hex_path, hex_path, resolution):
    """Compare species_hex grouped by h3_r{resolution} with bc_hex_r{resolution}.

    Returns (cells, occurrence mismatches, species mismatches, examples),
    where examples are up to EXAMPLES mismatching rows of (h3_cell, our
    occurrences, bc_hex's occurrences, our distinct specieskey, bc_hex's
    distinct_species).
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
        cells AS (
            SELECT h3_cell FROM ours
            UNION SELECT h3_cell FROM theirs
        )
        SELECT c.h3_cell,
               coalesce(o.occurrences, 0) AS our_occurrences,
               coalesce(t.occurrences, 0) AS their_occurrences,
               coalesce(o.n_species, 0) AS our_species,
               coalesce(t.distinct_species, 0) AS their_species
        FROM cells AS c
        LEFT JOIN ours AS o ON o.h3_cell = c.h3_cell
        LEFT JOIN theirs AS t ON t.h3_cell = c.h3_cell
        """
    )
    cells, occ_bad, species_bad = con.execute(
        """SELECT count(*),
                  count(*) FILTER (WHERE our_occurrences <> their_occurrences),
                  count(*) FILTER (WHERE our_species <> their_species)
           FROM hex_compare"""
    ).fetchone()
    examples = con.execute(
        f"""SELECT * FROM hex_compare
            WHERE our_occurrences <> their_occurrences
               OR our_species <> their_species
            ORDER BY h3_cell LIMIT {EXAMPLES}"""
    ).fetchall()
    con.execute("DROP TABLE hex_compare")
    return cells, occ_bad, species_bad, examples


def names_with_several_keys(con, occurrence_path):
    """Return species names that have more than one specieskey, or keys with
    more than one name, as (species, specieskeys) and (specieskey, names).
    Either one makes distinct specieskey differ from bc_hex's distinct
    species names in a hexagon."""
    names = con.execute(
        f"""SELECT species, list(DISTINCT specieskey)
            FROM read_parquet('{occurrence_path}')
            WHERE specieskey IS NOT NULL AND species IS NOT NULL
            GROUP BY species HAVING count(DISTINCT specieskey) > 1
            ORDER BY species"""
    ).fetchall()
    keys = con.execute(
        f"""SELECT specieskey, list(DISTINCT species)
            FROM read_parquet('{occurrence_path}')
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
    os.makedirs(args.outdir, exist_ok=True)

    print(f"Tables:  {args.tables}")
    print(f"Hex dir: {hex_dir}")
    print(f"Output:  {args.outdir}\n")
    t_all = time.time()

    t = time.time()
    build_species_hex(con, occurrence_path, species_hex_path)
    print(f"species_hex built in {time.time() - t:.1f}s")
    t = time.time()
    build_species_card(con, occurrence_path, image_path, datasets_path, card_path)
    print(f"species_card_gbif built in {time.time() - t:.1f}s")

    # Checks.
    results = check_totals(con, occurrence_path, species_hex_path, card_path)
    hex_lines = []
    missing_hex = [
        r for r in RESOLUTIONS
        if not os.path.exists(os.path.join(hex_dir, f"bc_hex_r{r}.parquet"))
    ]
    for r in RESOLUTIONS:
        hex_path = os.path.join(hex_dir, f"bc_hex_r{r}.parquet")
        if r in missing_hex:
            continue
        cells, occ_bad, species_bad, examples = check_hex(
            con, species_hex_path, hex_path, r
        )
        results.append((f"h3_r{r} occurrences per hexagon = bc_hex_r{r}",
                        occ_bad == 0, f"{occ_bad:,} of {cells:,} hexagons differ"))
        results.append((f"h3_r{r} distinct species per hexagon = bc_hex_r{r}",
                        species_bad == 0, f"{species_bad:,} of {cells:,} hexagons differ"))
        for row in examples:
            hex_lines.append(
                f"    r{r} {row[0]}: occurrences {row[1]:,} vs {row[2]:,}, "
                f"species {row[3]:,} vs {row[4]:,}"
            )

    # Summary.
    hex_rows, hex_no_year, hex_no_key, hex_species = con.execute(
        f"""SELECT count(*), count(*) FILTER (WHERE year IS NULL),
                   count(*) FILTER (WHERE specieskey IS NULL),
                   count(DISTINCT specieskey)
            FROM read_parquet('{species_hex_path}')"""
    ).fetchone()
    card_rows, with_image, ranks = con.execute(
        f"""SELECT count(*), count(image_identifier),
                   list(DISTINCT image_license_rank ORDER BY image_license_rank)
                       FILTER (WHERE image_license_rank IS NOT NULL)
            FROM read_parquet('{card_path}')"""
    ).fetchone()

    print(f"\nspecies_hex.parquet:       {hex_rows:>12,} rows  {file_size(species_hex_path):>10}"
          f"  ({hex_species:,} species; {hex_no_year:,} rows with no year, "
          f"{hex_no_key:,} with no specieskey)")
    print(f"species_card_gbif.parquet: {card_rows:>12,} rows  {file_size(card_path):>10}")
    pct = 100 * with_image / card_rows if card_rows else 0
    print(f"  species with an image:   {with_image:>12,} ({pct:.1f}%), "
          f"license ranks used: {ranks}")

    print("\nChecks:")
    for name, passed, detail in results:
        print(f"  {'PASS' if passed else 'FAIL'}  {name}: {detail}")
    if missing_hex:
        print(f"  WARNING: skipped the bc_hex check for resolution(s) "
              f"{missing_hex}: bc_hex_rN.parquet not found in {hex_dir}. "
              f"Pass --hex-dir to point at them.")
    if hex_lines:
        print("  Examples of hexagons that differ (ours vs bc_hex):")
        print("\n".join(hex_lines))
        print("  If the occurrences differ, the bc_hex files were probably built "
              "from a different bc_occurrence.")
    names, keys = names_with_several_keys(con, occurrence_path)
    if names or keys:
        print(f"  Note: {len(names):,} species name(s) have more than one "
              f"specieskey and {len(keys):,} specieskey(s) have no single name; "
              f"these can make the distinct species counts differ from bc_hex.")
        for species, specieskeys in names[:EXAMPLES]:
            print(f"    {species}: {specieskeys}")
        for specieskey, species in keys[:EXAMPLES]:
            print(f"    {specieskey}: {species}")

    print(f"\nDone in {time.time() - t_all:.1f}s")
    con.close()
    if not all(passed for _, passed, _ in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
