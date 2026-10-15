"""
build_species_image.py

Picks one image per species for the species card and writes
bc_species_image.parquet. build_dwca_tables.py calls build_species_image()
from here when it builds every table from a GBIF download; this script runs
the same selection on its own, from the tables that run already wrote, so
the image can be picked again without unpacking the 50 GB download.

Which images can be picked: only StillImages that pass the credit check in
shared/image_credit.py, the same check the API makes before it shows an
image: an image URL, a link to the original, a creator and a readable
licence. Among those, the ranking is the one build_dwca_tables.py has always
used: best licence first (SPECIES_IMAGE_LICENSE_RANKS), then the most
recent record, then the lowest gbifid and media_order. A species with no
passing image has no row, and its card has no image.

Inputs, all in --tables (written by build_dwca_tables.py):
    bc_occurrence.parquet   the records (specieskey, datasetkey, eventdate)
    bc_media.parquet        their media items, with license_code
    bc_datasets.parquet     the datasets, to find the Beaty datasets
Output, in the same folder:
    bc_species_image.parquet  one image per species (as described above)

The new table is written under a temporary name first
(bc_species_image.tmp.parquet) and only replaces the real one when every
check passes (safe_outputs.py):
    - one row per specieskey
    - every picked image passes the credit check
    - the number of species with an image equals the number of species
      with at least one passing StillImage, counted straight from the media
    - compared with the current table (if there is one): no species whose
      current image passes loses it or gets a different one. The run also
      prints how many species gained, lost or changed their image.
With --key-merge it also prints how many species have an image after the
key merge, which is the number of species cards with an image once
build_species_tables.py has been rerun.

Plain SQL only, so this runs on the cluster's DuckDB 0.10.3.

Run (from backend/, on the server):
    python pipeline/gbif/build_species_image.py --tables ~/bcbn/data/gbif_tables \\
        --key-merge ~/bcbn/data/gbif_tables/species_key_merge.csv \\
        --memory-limit 8GB --threads 8 --temp-dir ~/tmp/duckdb_species_image
"""

import argparse
import sys
import time
from pathlib import Path

import duckdb

from safe_outputs import (clear_temporary, replace_outputs, report_not_replaced,
                          temporary_path)

# shared/ is in backend/, two folders up from this file.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from shared.image_credit import READABLE_LICENCE_PATTERN, passes_sql  # noqa: E402

# The Beaty Biodiversity Museum's datasets on GBIF. Their curator confirmed
# (email, 2026-10-08) that their records are mostly public domain and they
# would like attribution, so their images with no license can be used. They
# are listed by key because GBIF names the publisher "University of British
# Columbia", not the museum, so no publisher name picks out just these.
BEATY_DATASET_KEYS = [
    "07fd0d79-4883-435f-bba1-58fef110cd13",  # UBC Herbarium - Vascular Plant Collection
    "4edd9396-59df-4b01-9e29-dc21a59f9963",  # UBC Herbarium - Bryophytes Collection
    "628abbe5-dc8d-41e9-a0c7-f05efe282649",  # UBC Herbarium - Lichen Collection
    "90302970-1bc6-4865-be76-9aef1dd707f9",  # UBC Herbarium - Algae Collection
    "ca1bcd7e-7387-42f9-81ba-1470db55e3e8",  # UBC Herbarium - Fungi Collection
    "9c45867f-f77d-42f3-9751-ae16bb7c9bc8",  # UBC - Spencer Entomological Collection
    "4caf2040-83ad-4fa7-ba13-005bced721eb",  # Beaty Biodiversity Museum Fish Collection
    "ba0c046d-52bb-4262-a495-652988c9f3f7",  # Cowan Tetrapod Collection - Birds
    "3ad882bb-cd21-4201-8b83-3684bfc6d830",  # Cowan Tetrapod Collection - Mammals
]

# Datasets not in BEATY_DATASET_KEYS but whose title or publisher matches
# this are printed too, in case the museum has a collection not on the list.
BEATY_NEAR_MISS_PATTERN = "university of british columbia|\\bubc\\b|cowan|herbarium"

# Which images may be a species image, best first. Any version counts,
# including no stated version. Images in neither list are never used.
SPECIES_IMAGE_LICENSE_RANKS = [
    (1, "(CC0|PDM)"),
    (2, "CC_BY"),
    (3, "CC_BY_SA"),
    (4, "CC_BY_ND"),
    (5, "CC_BY_NC"),
    (6, "CC_BY_NC_SA"),
    (7, "CC_BY_NC_ND"),
]
# Rank for an image with no license at all (NULL or only spaces) from a
# Beaty dataset. An image whose license is filled in but can't be read,
# such as "Copyright ...", is not used, Beaty or not.
BEATY_BLANK_LICENSE_RANK = 8

# The rank as SQL, for a media row m (bc_media) joined to the datasets in
# beaty_datasets as b (b.datasetkey is NULL for any other dataset).
SPECIES_IMAGE_LICENSES_SQL = (
    "CASE\n"
    + "".join(
        f"        WHEN regexp_full_match(m.license_code, '{code}(_[0-9]+_[0-9]+)?') "
        f"THEN {rank}\n"
        for rank, code in SPECIES_IMAGE_LICENSE_RANKS
    )
    + "        WHEN nullif(trim(m.license), '') IS NULL AND b.datasetkey IS NOT NULL "
    f"THEN {BEATY_BLANK_LICENSE_RANK}\n"
    + "    END"
)


def find_beaty_datasets(con, datasets_path):
    """Make the temp table beaty_datasets, holding BEATY_DATASET_KEYS.

    Returns three lists, so they can be printed and checked:
      the listed datasets found in bc_datasets, as (datasetkey, title,
          publisher, bc_record_count);
      the listed keys not in bc_datasets (a typo, or a dataset with no
          records in this download);
      the near misses (see BEATY_NEAR_MISS_PATTERN), in the same form as
          the first list.
    """
    keys = ", ".join(f"'{key}'" for key in BEATY_DATASET_KEYS)
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE beaty_datasets AS
        SELECT unnest([{keys}]) AS datasetkey
        """
    )
    beaty = con.execute(
        f"""
        SELECT d.datasetkey, d.title, d.publisher, d.bc_record_count
        FROM read_parquet('{datasets_path}') AS d
        WHERE d.datasetkey IN (SELECT datasetkey FROM beaty_datasets)
        ORDER BY d.bc_record_count DESC, d.datasetkey
        """
    ).fetchall()
    found = {row[0] for row in beaty}
    missing = [key for key in BEATY_DATASET_KEYS if key not in found]
    near_misses = con.execute(
        f"""
        SELECT datasetkey, title, publisher, bc_record_count
        FROM read_parquet('{datasets_path}')
        WHERE datasetkey NOT IN (SELECT datasetkey FROM beaty_datasets)
          AND (regexp_matches(lower(coalesce(title, '')), '{BEATY_NEAR_MISS_PATTERN}')
               OR regexp_matches(lower(coalesce(publisher, '')),
                                 '{BEATY_NEAR_MISS_PATTERN}'))
        ORDER BY bc_record_count DESC, datasetkey
        """
    ).fetchall()
    return beaty, missing, near_misses


def beaty_blank_license_counts(con, occurrence_path, media_path, species_image_path):
    """Return, for each dataset in beaty_datasets, (datasetkey, media items,
    media items with a blank license, of those how many are StillImages,
    species whose picture came from this dataset through the blank-license
    rank). This shows which of the museum's datasets actually add pictures.
    Under the strict credit rule an image with no licence never passes, so
    that last column is 0 until a decision is made about these images."""
    return dict(
        (row[0], row[1:])
        for row in con.execute(
            f"""
            WITH media AS (
                SELECT o.datasetkey, m.type,
                       nullif(trim(m.license), '') IS NULL AS blank
                FROM read_parquet('{media_path}') AS m
                JOIN read_parquet('{occurrence_path}') AS o USING (gbifid)
                WHERE o.datasetkey IN (SELECT datasetkey FROM beaty_datasets)
            ),
            counts AS (
                SELECT datasetkey, count(*) AS n_media,
                       count(*) FILTER (WHERE blank) AS n_blank,
                       count(*) FILTER (WHERE blank AND type = 'StillImage')
                           AS n_blank_still
                FROM media
                GROUP BY datasetkey
            ),
            pictures AS (
                SELECT datasetkey, count(*) AS n_species
                FROM read_parquet('{species_image_path}')
                WHERE license_rank = {BEATY_BLANK_LICENSE_RANK}
                GROUP BY datasetkey
            )
            SELECT b.datasetkey, coalesce(c.n_media, 0), coalesce(c.n_blank, 0),
                   coalesce(c.n_blank_still, 0), coalesce(p.n_species, 0)
            FROM beaty_datasets AS b
            LEFT JOIN counts AS c ON c.datasetkey = b.datasetkey
            LEFT JOIN pictures AS p ON p.datasetkey = b.datasetkey
            """
        ).fetchall()
    )


def build_species_image(con, occurrence_path, media_path, out_path):
    """Write bc_species_image.parquet: for each specieskey, one StillImage
    chosen by license, from the images that pass the credit check in
    shared/image_credit.py (image URL, link to the original, creator and a
    readable licence), the same check the API uses before showing an image.
    A species with no passing image gets no row. Needs the beaty_datasets
    table (find_beaty_datasets).

    The license order, best first, is in SPECIES_IMAGE_LICENSE_RANKS: CC0
    or Public Domain Mark, CC BY, CC BY-SA, CC BY-ND, CC BY-NC, CC BY-NC-SA,
    CC BY-NC-ND, then (rank 8) an image with no license from a Beaty
    dataset. Among images of the same rank, the record with the most recent
    eventdate wins (records with no date last), then the lowest gbifid and
    media_order.

    license_rank is that rank, so the dashboard knows the tier (it may, for
    example, show ND images uncropped). license_source is "media" when the
    image's own license was used, or "beaty_blank_public_domain" for rank 8.

    Rank 8 is kept in the ranking, but an image with no licence never passes
    the credit check, so no rank 8 image is picked under the current rule.
    """
    con.execute(
        f"""
        COPY (
            SELECT specieskey, species, gbifid, media_order, identifier,
                   "references", creator, rightsholder, license_code, datasetkey,
                   license_rank,
                   CASE WHEN license_rank = {BEATY_BLANK_LICENSE_RANK}
                        THEN 'beaty_blank_public_domain' ELSE 'media'
                   END AS license_source
            FROM (
                SELECT o.specieskey, o.species, m.gbifid, m.media_order,
                       m.identifier, m."references", m.creator, m.rightsholder,
                       m.license_code, o.datasetkey, o.eventdate,
                       {SPECIES_IMAGE_LICENSES_SQL} AS license_rank,
                       {passes_sql("m")} AS passes
                FROM read_parquet('{media_path}') AS m
                JOIN read_parquet('{occurrence_path}') AS o USING (gbifid)
                LEFT JOIN beaty_datasets AS b ON b.datasetkey = o.datasetkey
                WHERE m.type = 'StillImage' AND o.specieskey IS NOT NULL
            )
            -- Only images that pass the credit check; the ranking below is
            -- unchanged and only chooses among them.
            WHERE license_rank IS NOT NULL AND passes
            QUALIFY row_number() OVER (
                PARTITION BY specieskey
                ORDER BY license_rank, eventdate DESC NULLS LAST,
                         CAST(gbifid AS BIGINT), media_order
            ) = 1
            ORDER BY species, specieskey
        ) TO '{out_path}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )


def species_without_image(con, occurrence_path, media_path, species_image_path):
    """Return how many species with records got no image, split by why.

    A species gets no image when none of its StillImages passes the credit
    check (shared/image_credit.py). The reasons, checked in this order:
    no StillImages at all; images with a readable licence, but none that
    also has an image URL, a link to the original and a creator; only
    images with no licence, with one that could not be read, or both."""
    return con.execute(
        f"""
        WITH species AS (
            SELECT DISTINCT specieskey FROM read_parquet('{occurrence_path}')
            WHERE specieskey IS NOT NULL
        ),
        still AS (
            SELECT o.specieskey,
                   bool_or(regexp_full_match(coalesce(m.license_code, ''),
                                             '{READABLE_LICENCE_PATTERN}')) AS any_readable,
                   bool_or(nullif(trim(m.license), '') IS NULL) AS any_blank,
                   bool_or(nullif(trim(m.license), '') IS NOT NULL
                           AND m.license_code IS NULL) AS any_unread
            FROM read_parquet('{media_path}') AS m
            JOIN read_parquet('{occurrence_path}') AS o USING (gbifid)
            WHERE m.type = 'StillImage'
            GROUP BY o.specieskey
        )
        SELECT
            CASE WHEN st.specieskey IS NULL THEN 'no still images at all'
                 WHEN st.any_readable
                     THEN 'images with a readable licence, but none also with an '
                          || 'image URL, a link to the original and a creator'
                 WHEN st.any_blank AND st.any_unread
                     THEN 'only images with no license or one that could not be read'
                 WHEN st.any_blank
                     THEN 'only images with no license'
                 WHEN st.any_unread
                     THEN 'only images with a license that could not be read'
                 ELSE 'other (check SPECIES_IMAGE_LICENSE_RANKS)' END AS reason,
            count(*)
        FROM species AS s
        LEFT JOIN still AS st USING (specieskey)
        WHERE s.specieskey NOT IN (
            SELECT specieskey FROM read_parquet('{species_image_path}'))
        GROUP BY reason
        ORDER BY reason
        """
    ).fetchall()


def check_species_image(con, occurrence_path, media_path, image_path):
    """Check a species image table against the records and media it was
    picked from. Returns a list of (description, ok) pairs:
      - one row per specieskey
      - every picked image passes the credit check
      - every species with at least one passing StillImage has an image,
        and no other species has one (counted straight from the media, so
        this also checks that the licence ranking covers every readable
        licence)
    """
    rows, keys, failing = con.execute(
        f"""SELECT count(*), count(DISTINCT specieskey),
                   count(*) FILTER (WHERE NOT {passes_sql("i")})
            FROM read_parquet('{image_path}') AS i""").fetchone()
    expected = con.execute(
        f"""SELECT count(DISTINCT o.specieskey)
            FROM read_parquet('{media_path}') AS m
            JOIN read_parquet('{occurrence_path}') AS o USING (gbifid)
            WHERE m.type = 'StillImage' AND o.specieskey IS NOT NULL
              AND {passes_sql("m")}""").fetchone()[0]
    return [
        (f"one row per specieskey ({rows:,} rows, {keys:,} species)", rows == keys),
        (f"every picked image passes the credit check ({failing:,} do not)", failing == 0),
        (f"species with an image ({rows:,}) = species with a passing StillImage "
         f"({expected:,})", rows == expected),
    ]


def compare_with_previous(con, new_path, old_path):
    """Compare the new image table with the current one, species by
    species. Returns a dict of counts:
      gained     species with an image now that had none
      lost       species that had an image and now have none
      changed    species whose image is a different one
      unchanged  species with the same image (same gbifid and media_order)
      lost_passing, changed_passing
                 of lost and changed, those whose current image passes the
                 credit check: a passing image was the best overall, so it
                 is also the best passing one and must be kept; both must
                 be 0
    """
    return dict(zip(
        ["gained", "lost", "changed", "unchanged", "lost_passing", "changed_passing"],
        con.execute(
            f"""
            WITH pairs AS (
                SELECT coalesce(n.specieskey, o.specieskey) AS specieskey,
                       n.specieskey IS NOT NULL AS has_new,
                       o.specieskey IS NOT NULL AS has_old,
                       n.gbifid IS NOT DISTINCT FROM o.gbifid
                           AND n.media_order IS NOT DISTINCT FROM o.media_order AS same,
                       coalesce({passes_sql("o")}, false) AS old_passes
                FROM read_parquet('{new_path}') AS n
                FULL OUTER JOIN read_parquet('{old_path}') AS o
                    ON o.specieskey = n.specieskey
            )
            SELECT count(*) FILTER (WHERE has_new AND NOT has_old),
                   count(*) FILTER (WHERE has_old AND NOT has_new),
                   count(*) FILTER (WHERE has_new AND has_old AND NOT same),
                   count(*) FILTER (WHERE has_new AND has_old AND same),
                   count(*) FILTER (WHERE has_old AND NOT has_new AND old_passes),
                   count(*) FILTER (WHERE has_new AND has_old AND NOT same AND old_passes)
            FROM pairs
            """).fetchone()))


def species_after_merge(con, image_path, key_merge_path):
    """Return how many species have an image once the key merge is applied
    (each old key counted under its kept key), which is how many species
    cards will have an image after build_species_tables.py is rerun."""
    return con.execute(
        f"""SELECT count(DISTINCT coalesce(k.kept_key, i.specieskey))
            FROM read_parquet('{image_path}') AS i
            LEFT JOIN (SELECT old_key, kept_key
                       FROM read_csv_auto('{key_merge_path}', all_varchar = true)) AS k
                ON k.old_key = i.specieskey""").fetchone()[0]


def connect(threads, memory_limit, temp_dir):
    """Open DuckDB with a thread count, a memory limit and, if given, a
    folder to spill to when the memory limit is reached."""
    con = duckdb.connect(config={"threads": threads, "memory_limit": memory_limit})
    if temp_dir:
        con.execute(f"SET temp_directory='{temp_dir}'")
    return con


def main():
    """Pick the species images again from the existing tables, check them,
    and replace bc_species_image.parquet only if every check passes."""
    parser = argparse.ArgumentParser(
        description="Pick one fully credited image per species from the existing tables.")
    parser.add_argument("--tables", required=True,
                        help="Folder with bc_occurrence, bc_media and bc_datasets "
                             "(from build_dwca_tables.py); the output goes here too.")
    parser.add_argument("--key-merge", default=None,
                        help="species_key_merge.csv, to also print how many species "
                             "cards will have an image.")
    parser.add_argument("--memory-limit", default="8GB")
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--temp-dir", default=None,
                        help="Where DuckDB may spill to disk when it reaches the memory limit.")
    args = parser.parse_args()

    tables = Path(args.tables).expanduser()
    occurrence_path = tables / "bc_occurrence.parquet"
    media_path = tables / "bc_media.parquet"
    datasets_path = tables / "bc_datasets.parquet"
    out_path = tables / "bc_species_image.parquet"
    for path in (occurrence_path, media_path, datasets_path):
        if not path.exists():
            print(f"Missing input: {path}")
            return 1

    t_start = time.time()
    clear_temporary([str(out_path)])
    tmp_path = Path(temporary_path(str(out_path)))
    con = connect(args.threads, args.memory_limit,
                  str(Path(args.temp_dir).expanduser()) if args.temp_dir else None)
    try:
        beaty, missing, near_misses = find_beaty_datasets(con, datasets_path)
        print(f"Beaty datasets found: {len(beaty)} of {len(BEATY_DATASET_KEYS)} listed")
        for key in missing:
            print(f"  WARNING: listed Beaty dataset {key} is not in bc_datasets")

        print("Picking one passing image per species...")
        build_species_image(con, occurrence_path, media_path, tmp_path)

        checks = check_species_image(con, occurrence_path, media_path, tmp_path)
        if out_path.exists():
            change = compare_with_previous(con, tmp_path, out_path)
            print(f"\nCompared with the current {out_path.name}:")
            for name in ("gained", "lost", "changed", "unchanged"):
                print(f"  species that {name:<9} their image: {change[name]:>8,}")
            checks += [
                (f"no species whose current image passes loses it "
                 f"({change['lost_passing']:,} do)", change["lost_passing"] == 0),
                (f"no species whose current image passes gets a different one "
                 f"({change['changed_passing']:,} do)", change["changed_passing"] == 0),
            ]
        else:
            print(f"\nNo current {out_path.name} to compare with.")

        if args.key_merge:
            n = species_after_merge(con, tmp_path, Path(args.key_merge).expanduser())
            print(f"\nSpecies with an image after the key merge (species cards with an "
                  f"image once build_species_tables.py is rerun): {n:,}")

        print("\nSpecies with records but no image:")
        for reason, n in species_without_image(con, occurrence_path, media_path, tmp_path):
            print(f"  {reason}: {n:,}")
        print("Picked images by license_rank:")
        for rank, n in con.execute(
                f"""SELECT license_rank, count(*) FROM read_parquet('{tmp_path}')
                    GROUP BY 1 ORDER BY 1""").fetchall():
            print(f"  {rank}: {n:,}")

        print("\nChecks:")
        for description, ok in checks:
            print(f"  {'PASS' if ok else 'FAIL'}  {description}")
    finally:
        con.close()

    if not all(ok for _, ok in checks):
        report_not_replaced([str(out_path)])
        return 1
    replace_outputs([str(out_path)])
    print(f"\nReplaced {out_path} in {time.time() - t_start:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
