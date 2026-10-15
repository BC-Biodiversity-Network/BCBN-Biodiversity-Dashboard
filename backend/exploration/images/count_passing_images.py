"""
count_passing_images.py

Counts how many species could have a card photo if the species image were
picked only among images that pass the API's credit check, and what two
possible looser credit rules would add. Read-only: it writes nothing.

The API (backend/api/images.py, card_image) shows an image only when it
has all four of:
    - an image URL                     (identifier)
    - a link to the original           (references)
    - a creator                        (creator)
    - a licence the API can read       (license_code: CC0, the Public Domain
                                        Mark, or CC BY with any of NC, SA, ND)
Today bc_species_image (build_dwca_tables.py) picks one image per species
by licence and date without this check, so a species can get an image the
API then hides, even when another of its images would pass.

What it prints, all counted by specieskey (after the key merge, if
--key-merge is given, the same way as the species cards):
    1. species with records, species with an image the current ranking can
       pick, and, with --card, how many of today's card images pass
    2. species with at least one passing image: what a passing-only
       selection would give
    3. how many extra species each fallback would add on its own, and both
       together (neither is in use; the strict rule stays for now):
         rights holder fallback  the rights holder counts as the creator
                                 when there is no creator
         record page fallback    the GBIF record page
                                 (https://www.gbif.org/occurrence/<gbifid>)
                                 counts as the link to the original when
                                 there is no link; every record has one
    4. species whose StillImages all fail, split by where the images come
       from (BOLD, Beaty, Mushroom Observer, iNaturalist, museums and
       herbaria, other) and by what is missing

Neither fallback helps an image with no readable licence (for example the
Beaty images published without one), because the licence is always
required.

Inputs, in --tables (from build_dwca_tables.py): bc_media.parquet and
bc_occurrence.parquet. The licence ranking and the list of Beaty datasets
are imported from pipeline/gbif/build_species_image.py, and the readable
licences from shared/image_credit.py, so the counts follow the pipeline's
and the API's own rules.

Plain SQL only, so it runs on the server's DuckDB 0.10.3. Run on the
server (bc_media for the full download is only there), from backend/:
    python exploration/images/count_passing_images.py --tables ~/bcbn/data/gbif_tables \\
        --key-merge ~/bcbn/data/gbif_tables/species_key_merge.csv \\
        --card ~/bcbn/data/gbif_tables/species_merged/species_card_gbif.parquet \\
        --memory-limit 8GB
"""

import argparse
import sys
from pathlib import Path

import duckdb

# The pipeline's GBIF scripts are not a package; add their folder to the
# path the way the pipeline scripts themselves do, and backend/ for shared/.
BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND / "pipeline" / "gbif"))
sys.path.insert(0, str(BACKEND))
from build_species_image import BEATY_DATASET_KEYS, SPECIES_IMAGE_LICENSES_SQL  # noqa: E402
from shared.image_credit import READABLE_LICENCE_PATTERN as READABLE_LICENCE  # noqa: E402

# Where an image comes from. BOLD and Mushroom Observer are told apart by
# the image's web address, Beaty by its dataset, museums and herbaria by the
# record being a preserved specimen.
SOURCE_SQL = """
    CASE WHEN m.identifier ILIKE '%boldsystems%' THEN 'BOLD'
         WHEN b.datasetkey IS NOT NULL THEN 'Beaty'
         WHEN m.identifier ILIKE '%mushroomobserver%' THEN 'Mushroom Observer'
         WHEN m.identifier ILIKE '%inaturalist%' THEN 'iNaturalist'
         WHEN o.basisofrecord = 'PRESERVED_SPECIMEN' THEN 'museums and herbaria'
         ELSE 'other' END
"""


def has_text(column):
    """SQL that is true when the column holds something other than spaces."""
    return f"(nullif(trim({column}), '') IS NOT NULL)"


def connect(memory_limit, threads):
    """Open DuckDB with a memory limit, so it spills to disk rather than
    running out of memory on the full tables."""
    con = duckdb.connect()
    con.execute(f"SET memory_limit = '{memory_limit}'")
    con.execute(f"SET threads = {int(threads)}")
    return con


def make_lookup_tables(con, key_merge):
    """Make the two small tables the image query joins to: the Beaty
    datasets, and the key merge (old_key to kept_key; empty without
    --key-merge)."""
    con.execute("CREATE TEMP TABLE beaty_datasets (datasetkey VARCHAR)")
    con.executemany("INSERT INTO beaty_datasets VALUES (?)", [[k] for k in BEATY_DATASET_KEYS])
    if key_merge:
        con.execute(
            f"""CREATE TEMP TABLE key_merge AS
                SELECT old_key, kept_key
                FROM read_csv_auto('{Path(key_merge).expanduser()}', all_varchar = true)""")
    else:
        con.execute("CREATE TEMP TABLE key_merge (old_key VARCHAR, kept_key VARCHAR)")


def make_images_table(con, tables):
    """Make a temporary table with one row per StillImage of a record with
    a specieskey: its (kept) specieskey, its licence rank (NULL if the
    current ranking would never pick it), its source, and which parts of
    the credit it has."""
    con.execute(f"""
        CREATE TEMP TABLE images AS
        SELECT coalesce(k.kept_key, o.specieskey) AS specieskey,
               {SPECIES_IMAGE_LICENSES_SQL} AS license_rank,
               {SOURCE_SQL} AS source,
               {has_text('m.identifier')} AS has_url,
               {has_text('m."references"')} AS has_link,
               {has_text('m.creator')} AS has_creator,
               {has_text('m.rightsholder')} AS has_rightsholder,
               regexp_full_match(coalesce(m.license_code, ''), '{READABLE_LICENCE}')
                   AS has_licence
        FROM read_parquet('{tables / "bc_media.parquet"}') AS m
        JOIN read_parquet('{tables / "bc_occurrence.parquet"}') AS o USING (gbifid)
        LEFT JOIN beaty_datasets AS b ON b.datasetkey = o.datasetkey
        LEFT JOIN key_merge AS k ON k.old_key = o.specieskey
        WHERE m.type = 'StillImage' AND o.specieskey IS NOT NULL
    """)


def count_species_with_records(con, tables):
    """Return the number of species (kept keys) with at least one record."""
    return con.execute(f"""
        SELECT count(DISTINCT coalesce(k.kept_key, o.specieskey))
        FROM read_parquet('{tables / "bc_occurrence.parquet"}') AS o
        LEFT JOIN key_merge AS k ON k.old_key = o.specieskey
        WHERE o.specieskey IS NOT NULL""").fetchone()[0]


def count_card_images_passing(con, card):
    """Return (cards with an image, cards whose image passes the API's
    check) for a species_card_gbif.parquet."""
    return con.execute(f"""
        SELECT count(image_identifier),
               count(*) FILTER (WHERE {has_text('image_identifier')}
                   AND {has_text('image_references')} AND {has_text('image_creator')}
                   AND regexp_full_match(coalesce(image_license_code, ''),
                                         '{READABLE_LICENCE}'))
        FROM read_parquet('{Path(card).expanduser()}')""").fetchone()


def count_per_rule(con):
    """Return the species counts for the strict rule and each fallback:
    (any pickable image, strict, rights holder fallback, record page
    fallback, both fallbacks). Each counts species with at least one image
    that passes that rule."""
    return con.execute("""
        SELECT count(DISTINCT specieskey) FILTER (WHERE license_rank IS NOT NULL),
               count(DISTINCT specieskey) FILTER (
                   WHERE has_url AND has_link AND has_creator AND has_licence),
               count(DISTINCT specieskey) FILTER (
                   WHERE has_url AND has_link AND (has_creator OR has_rightsholder)
                         AND has_licence),
               count(DISTINCT specieskey) FILTER (
                   WHERE has_url AND has_creator AND has_licence),
               count(DISTINCT specieskey) FILTER (
                   WHERE has_url AND (has_creator OR has_rightsholder) AND has_licence)
        FROM images""").fetchone()


def failing_by_source(con):
    """Return one row per combination of sources, for the species whose
    StillImages all fail the strict rule: the sources, the number of
    species, and how many of them have no image with a creator, a link and
    a readable licence respectively, and how many each fallback would
    rescue."""
    return con.execute("""
        WITH per_species AS (
            SELECT specieskey,
                   string_agg(DISTINCT source, ' + ' ORDER BY source) AS sources,
                   bool_or(has_url AND has_link AND has_creator AND has_licence) AS passes,
                   bool_or(has_creator) AS any_creator,
                   bool_or(has_link) AS any_link,
                   bool_or(has_licence) AS any_licence,
                   bool_or(has_url AND has_link AND (has_creator OR has_rightsholder)
                           AND has_licence) AS rightsholder_rescues,
                   bool_or(has_url AND has_creator AND has_licence) AS record_page_rescues
            FROM images
            GROUP BY specieskey)
        SELECT sources, count(*),
               count(*) FILTER (WHERE NOT any_creator),
               count(*) FILTER (WHERE NOT any_link),
               count(*) FILTER (WHERE NOT any_licence),
               count(*) FILTER (WHERE rightsholder_rescues),
               count(*) FILTER (WHERE record_page_rescues)
        FROM per_species
        WHERE NOT passes
        GROUP BY sources
        ORDER BY count(*) DESC, sources""").fetchall()


def main():
    """Read the arguments, count, and print the report."""
    parser = argparse.ArgumentParser(
        description="Count species that could have a fully credited card photo.")
    parser.add_argument("--tables", required=True,
                        help="Folder with bc_media.parquet and bc_occurrence.parquet.")
    parser.add_argument("--key-merge", default=None,
                        help="species_key_merge.csv, to count by kept key like the cards.")
    parser.add_argument("--card", default=None,
                        help="species_card_gbif.parquet, to also count today's card "
                             "images that pass.")
    parser.add_argument("--memory-limit", default="8GB")
    parser.add_argument("--threads", type=int, default=8)
    args = parser.parse_args()
    tables = Path(args.tables).expanduser()

    con = connect(args.memory_limit, args.threads)
    make_lookup_tables(con, args.key_merge)
    make_images_table(con, tables)

    species = count_species_with_records(con, tables)
    pickable, strict, rightsholder, record_page, both = count_per_rule(con)

    print(f"DuckDB {duckdb.__version__}; tables {tables}"
          f"{'; key merge applied' if args.key_merge else '; no key merge'}")
    print()
    print(f"species with records:                                {species:>8,}")
    print(f"species with an image the current ranking can pick: {pickable:>8,}")
    if args.card:
        with_image, passing = count_card_images_passing(con, args.card)
        print(f"today's cards with an image:                         {with_image:>8,}")
        print(f"  of which pass the API's check (shown today):       {passing:>8,}")
    print()
    print(f"species with at least one image passing the strict rule "
          f"(passing-only selection): {strict:,}")
    print(f"  extra species with the rights holder fallback alone:   {rightsholder - strict:>7,}")
    print(f"  extra species with the record page fallback alone:     {record_page - strict:>7,}")
    print(f"  extra species with both fallbacks:                     {both - strict:>7,}")
    print()

    rows = failing_by_source(con)
    print(f"species whose StillImages all fail the strict rule: {sum(r[1] for r in rows):,}")
    print("  (no creator / no link / no readable licence = none of the species' images "
          "has one; rescued = would pass with that fallback)")
    print(f"  {'sources':40} {'species':>8} {'no creator':>11} {'no link':>8} "
          f"{'no licence':>11} {'rescued by rights holder':>25} {'by record page':>15}")
    for sources, n, no_creator, no_link, no_licence, by_holder, by_page in rows:
        print(f"  {sources:40} {n:>8,} {no_creator:>11,} {no_link:>8,} {no_licence:>11,} "
              f"{by_holder:>25,} {by_page:>15,}")


if __name__ == "__main__":
    main()
