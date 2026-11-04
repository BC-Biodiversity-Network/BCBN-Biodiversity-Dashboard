"""
check_sensitive_records.py

A report, not a pipeline step: how far GBIF publishers already generalise
the locations of BC's at-risk species in our records, and which records of
those species still look exact. It changes no table the site uses.

Two modes:

    lists    (on the Mac) writes the candidate species lists, one row per
             species on the site, from the species cards and the BCSEE
             tables:
               list_a  BCSEE Red or Blue, for the species itself or any
                       subspecies, variety or population on its key (the
                       same rule as the site's status chips);
               list_b  federal: SARA Schedule 1, or COSEWIC Endangered,
                       Threatened, Special Concern or Extirpated, for the
                       species or any subspecies, variety or population.
             A list_d column (the CDC masked layer) can be added to the
             file by hand later; the records mode reads it if it is there.

    records  (on the server, where the GBIF download is) reads, for every
             record on the site (bc_occurrence.parquet), the fields the
             site's tables do not keep, straight out of the download zip's
             occurrence.txt: dataGeneralizations, informationWithheld, and
             the coordinates as text (verbatimLatitude and verbatimLongitude
             when the file has them, else decimalLatitude and
             decimalLongitude as GBIF wrote them). Nothing is extracted to
             disk: the zip is read as a stream, and only those fields are
             kept, in a CSV of about 2 to 4 GB in --workdir. It then writes
             the report tables and report.txt to --outdir.

Definitions used in the report:
  - a note: dataGeneralizations or informationWithheld not empty;
  - large uncertainty: coordinateUncertaintyInMeters over 10,000 m;
  - generalised: a note or a large uncertainty;
  - uncertainty bins: missing, under 100 m, 100 m to 1 km, over 1 km to
    10 km, over 10 km;
  - decimal places: of the coordinates as text, the larger of the latitude's
    and the longitude's count (a trailing zero may have been dropped from
    one of them); "other" for a value that is not a plain decimal number;
  - publisher groups: iNaturalist (its research-grade dataset), eBird (EOD),
    museum and herbarium collections (basisOfRecord PRESERVED_SPECIMEN), and
    the rest;
  - possibly exact: a species on list a, b or d, 4 or more decimal places,
    uncertainty under 100 m or missing, and no note.

Records are matched to species as on the site: a record's specieskey, or
the card it was merged into.

Usage:
    # On the Mac:
    python pipeline/gbif/check_sensitive_records.py lists \\
        --cards data/api/species_card_gbif.parquet \\
        --bcsee-by-species data/api/bcsee_status_by_gbif_species.parquet \\
        --bcsee ~/Desktop/BCBN/Results/bcsee_status.parquet \\
        --out ~/Desktop/BCBN/Results/sensitive_check/species_lists.csv

    # On the server (mank03), with python -u so a log file shows progress
    # as it happens; the workdir needs about 5 to 20 GB:
    python -u check_sensitive_records.py records \\
        --zip ~/bcbn/data/gbif_dwca/0009771-260928105237408.zip \\
        --occurrence ~/bcbn/data/gbif_tables/bc_occurrence.parquet \\
        --datasets ~/bcbn/data/gbif_tables/bc_datasets.parquet \\
        --cards ~/bcbn/data/gbif_tables/species_merged/species_card_gbif.parquet \\
        --lists ~/bcbn/sensitive_check_2026-11/species_lists.csv \\
        --workdir ~/bcbn/data/tmp_sensitive_2026-11 \\
        --outdir ~/bcbn/sensitive_check_2026-11/out \\
        --memory-limit 16GB --threads 8

The script needs only duckdb (0.10.3 or later) and the standard library, so
it can run from any folder.

Plain SQL only, so this runs on the cluster's DuckDB 0.10.3.
"""

import argparse
import csv
import io
import os
import sys
import time
import zipfile

import duckdb

INATURALIST = "50c9509d-22c7-4a22-a47d-8c48425ef4a7"
EBIRD = "4fa7b334-ce0d-4e88-aaae-2e0c138d049e"

# The occurrence.txt fields the records mode keeps (lower case), and the
# ones it uses when the file has them.
NEEDED = ["gbifid", "datageneralizations", "informationwithheld", "decimallatitude", "decimallongitude"]
OPTIONAL = ["verbatimlatitude", "verbatimlongitude"]

# Notes longer than this are cut in the extracted CSV (they are only shown
# as examples and counted as filled in).
NOTE_LENGTH = 300


def make_lists(args):
    """Write the species list file (see the top of this file)."""
    con = duckdb.connect()
    con.execute(f"CREATE VIEW cards AS SELECT * FROM read_parquet('{args.cards}')")
    con.execute(f"CREATE VIEW bys AS SELECT * FROM read_parquet('{args.bcsee_by_species}')")
    con.execute(f"CREATE VIEW raw AS SELECT * FROM read_parquet('{args.bcsee}')")
    con.execute("""
        CREATE TABLE entries AS
        SELECT b.gbif_species_key AS specieskey, r.bc_list, r.sara_schedule, r.cosewic
        FROM bys AS b, unnest(b.element_codes) AS e(element_code)
        LEFT JOIN raw AS r ON r.element_code = e.element_code""")
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    con.execute(f"""
        COPY (
            SELECT c.specieskey, c.col_name, c.kingdom, c.class, c.family, c.record_count,
                   coalesce(list_contains(b.statuses, 'Red') OR list_contains(b.statuses, 'Blue'), false) AS list_a,
                   coalesce(bool_or(e.sara_schedule = '1'
                                    OR regexp_matches(coalesce(e.cosewic, ''),
                                                      'Endangered|Threatened|Special Concern|Extirpated')),
                            false) AS list_b,
                   nullif(array_to_string(b.statuses, ' '), '') AS bcsee_statuses,
                   string_agg(DISTINCT CASE WHEN e.sara_schedule = '1' THEN 'SARA 1' END, ' ' ORDER BY CASE WHEN e.sara_schedule = '1' THEN 'SARA 1' END) AS sara,
                   string_agg(DISTINCT e.cosewic, '; ' ORDER BY e.cosewic) AS cosewic
            FROM read_parquet('{args.cards}') AS c
            LEFT JOIN bys AS b ON b.gbif_species_key = c.specieskey
            LEFT JOIN entries AS e ON e.specieskey = c.specieskey
            GROUP BY c.specieskey, c.col_name, c.kingdom, c.class, c.family, c.record_count, b.statuses
            ORDER BY c.specieskey
        ) TO '{args.out}' (HEADER, DELIMITER ',')""")
    counts = con.execute(f"""SELECT count(*), sum(CASE WHEN list_a THEN 1 ELSE 0 END),
                                    sum(CASE WHEN list_b THEN 1 ELSE 0 END)
                             FROM read_csv_auto('{args.out}', header=true)""").fetchone()
    print(f"wrote {args.out}: {counts[0]:,} species, list a {counts[1]:,}, list b {counts[2]:,}")


def extract_fields(zip_path, out_csv):
    """Stream occurrence.txt out of the zip and write the needed fields of
    every record to out_csv. Returns the field names written."""
    started = time.time()
    with zipfile.ZipFile(zip_path) as archive:
        member = next((name for name in archive.namelist() if name.endswith("occurrence.txt")), None)
        if member is None:
            sys.exit(f"{zip_path} has no occurrence.txt")
        with archive.open(member) as raw:
            text = io.TextIOWrapper(raw, encoding="utf-8", newline="")
            header = text.readline().rstrip("\r\n").split("\t")
            index = {name.lower(): position for position, name in enumerate(header)}
            missing = [name for name in NEEDED if name not in index]
            if missing:
                sys.exit(f"occurrence.txt has no {', '.join(missing)} column")
            wanted = NEEDED + [name for name in OPTIONAL if name in index]
            positions = [index[name] for name in wanted]
            last = max(positions)
            print(f"reading {member} from {zip_path}; keeping {', '.join(wanted)}")
            with open(out_csv, "w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(wanted)
                for count, line in enumerate(text, start=1):
                    fields = line.rstrip("\r\n").split("\t", last + 1)
                    if len(fields) <= last:
                        fields += [""] * (last + 1 - len(fields))
                    writer.writerow([fields[position][:NOTE_LENGTH] for position in positions])
                    if count % 5_000_000 == 0:
                        print(f"  {count:,} records, {time.time() - started:,.0f} s")
    print(f"extracted the fields of every record in {time.time() - started:,.0f} s")
    return wanted


def decimals_sql(text):
    """SQL for the decimal places of a coordinate given as text, or NULL when
    it is not a plain decimal number."""
    return (f"CASE WHEN regexp_matches(trim({text}), '^[-+]?[0-9]+([.][0-9]*)?$') "
            f"THEN length(regexp_extract(trim({text}), '[.]([0-9]*)$', 1)) END")


def report(args, fields):
    """Join the extracted fields to the site's records and write the report."""
    con = duckdb.connect()
    con.execute(f"SET memory_limit = '{args.memory_limit}'")
    con.execute(f"SET threads = {args.threads}")
    con.execute(f"SET temp_directory = '{os.path.join(args.workdir, 'duckdb_tmp')}'")
    extracted = os.path.join(args.workdir, "occurrence_fields.csv")
    columns = ", ".join(f"'{name}': 'VARCHAR'" for name in fields)
    con.execute(f"""CREATE VIEW zipped AS SELECT * FROM read_csv('{extracted}', header=true,
                                                             columns={{{columns}}})""")
    has_verbatim = "verbatimlatitude" in fields and "verbatimlongitude" in fields
    lists = con.execute(f"DESCRIBE SELECT * FROM read_csv_auto('{args.lists}', header=true)").fetchall()
    has_d = any(row[0] == "list_d" for row in lists)
    con.execute(f"""
        CREATE TABLE lists AS
        SELECT specieskey, col_name, coalesce(list_a, false) AS list_a, coalesce(list_b, false) AS list_b,
               {"coalesce(list_d, false)" if has_d else "false"} AS list_d
        FROM read_csv_auto('{args.lists}', header=true, all_varchar=false)""")
    # Old keys merged into a card count as the card's species, as on the site.
    con.execute(f"""
        CREATE TABLE key_to_card AS
        SELECT specieskey AS key, specieskey AS card FROM read_parquet('{args.cards}')
        UNION ALL
        SELECT unnest(merged_keys) AS key, specieskey AS card FROM read_parquet('{args.cards}')
        WHERE merged_keys IS NOT NULL""")
    lat_text = "z.verbatimlatitude" if has_verbatim else "z.decimallatitude"
    lon_text = "z.verbatimlongitude" if has_verbatim else "z.decimallongitude"
    print("joining the extracted fields to the site's records")
    con.execute(f"""
        CREATE TABLE rec AS
        SELECT o.gbifid, o.datasetkey, o.basisofrecord, k.card AS specieskey,
               o.decimallatitude, o.decimallongitude, o.coordinateuncertaintyinmeters AS uncertainty,
               nullif(trim(z.datageneralizations), '') AS data_generalizations,
               nullif(trim(z.informationwithheld), '') AS information_withheld,
               {lat_text} AS lat_given, {lon_text} AS lon_given,
               greatest({decimals_sql(lat_text)}, {decimals_sql(lon_text)}) AS decimals,
               CASE WHEN o.datasetkey = '{INATURALIST}' THEN 'iNaturalist'
                    WHEN o.datasetkey = '{EBIRD}' THEN 'eBird'
                    WHEN o.basisofrecord = 'PRESERVED_SPECIMEN' THEN 'museum and herbarium collections'
                    ELSE 'the rest' END AS publisher_group,
               coalesce(l.list_a, false) AS list_a, coalesce(l.list_b, false) AS list_b,
               coalesce(l.list_d, false) AS list_d
        FROM read_parquet('{args.occurrence}') AS o
        LEFT JOIN zipped AS z ON z.gbifid = o.gbifid
        LEFT JOIN key_to_card AS k ON k.key = o.specieskey
        LEFT JOIN lists AS l ON l.specieskey = k.card""")
    con.execute(f"""
        CREATE TABLE r AS
        SELECT *,
               data_generalizations IS NOT NULL OR information_withheld IS NOT NULL AS has_note,
               coalesce(uncertainty > 10000, false) AS large_uncertainty,
               CASE WHEN uncertainty IS NULL THEN '1 missing'
                    WHEN uncertainty < 100 THEN '2 under 100 m'
                    WHEN uncertainty <= 1000 THEN '3 100 m to 1 km'
                    WHEN uncertainty <= 10000 THEN '4 over 1 km to 10 km'
                    ELSE '5 over 10 km' END AS uncertainty_bin,
               CASE WHEN decimals IS NULL THEN '4 other or missing'
                    WHEN decimals <= 2 THEN '1 two or fewer'
                    WHEN decimals = 3 THEN '2 three'
                    ELSE '3 four or more' END AS decimals_bin
        FROM rec""")
    con.execute("""ALTER TABLE r ADD COLUMN generalised BOOLEAN;
                   UPDATE r SET generalised = has_note OR large_uncertainty""")
    con.execute("""ALTER TABLE r ADD COLUMN possibly_exact BOOLEAN;
                   UPDATE r SET possibly_exact = (list_a OR list_b OR list_d) AND decimals >= 4
                                                 AND (uncertainty IS NULL OR uncertainty < 100) AND NOT has_note""")
    con.execute(f"""CREATE TABLE ds AS SELECT datasetkey, title, publisher
                    FROM read_parquet('{args.datasets}')""")
    out = args.outdir
    os.makedirs(out, exist_ok=True)

    def write(name, query):
        con.execute(f"COPY ({query}) TO '{os.path.join(out, name)}' (HEADER, DELIMITER ',')")
        print(f"  wrote {name}")

    # Part 3: each list and the baseline, by publisher group.
    lists_sql = """
        SELECT 'list a' AS list, * FROM r WHERE list_a
        UNION ALL SELECT 'list b', * FROM r WHERE list_b
        UNION ALL SELECT 'list d', * FROM r WHERE list_d
        UNION ALL SELECT 'a, b or d', * FROM r WHERE list_a OR list_b OR list_d
        UNION ALL SELECT 'baseline: all other species', * FROM r WHERE NOT (list_a OR list_b OR list_d)"""
    measures = """count(*) AS records,
        sum(CASE WHEN has_note THEN 1 ELSE 0 END) AS with_note,
        sum(CASE WHEN data_generalizations IS NOT NULL THEN 1 ELSE 0 END) AS with_data_generalizations,
        sum(CASE WHEN information_withheld IS NOT NULL THEN 1 ELSE 0 END) AS with_information_withheld,
        sum(CASE WHEN uncertainty_bin = '1 missing' THEN 1 ELSE 0 END) AS uncertainty_missing,
        sum(CASE WHEN uncertainty_bin = '2 under 100 m' THEN 1 ELSE 0 END) AS uncertainty_under_100m,
        sum(CASE WHEN uncertainty_bin = '3 100 m to 1 km' THEN 1 ELSE 0 END) AS uncertainty_100m_to_1km,
        sum(CASE WHEN uncertainty_bin = '4 over 1 km to 10 km' THEN 1 ELSE 0 END) AS uncertainty_1_to_10km,
        sum(CASE WHEN uncertainty_bin = '5 over 10 km' THEN 1 ELSE 0 END) AS uncertainty_over_10km,
        sum(CASE WHEN decimals_bin = '1 two or fewer' THEN 1 ELSE 0 END) AS decimals_2_or_fewer,
        sum(CASE WHEN decimals_bin = '2 three' THEN 1 ELSE 0 END) AS decimals_3,
        sum(CASE WHEN decimals_bin = '3 four or more' THEN 1 ELSE 0 END) AS decimals_4_or_more,
        sum(CASE WHEN decimals_bin = '4 other or missing' THEN 1 ELSE 0 END) AS decimals_other,
        sum(CASE WHEN possibly_exact THEN 1 ELSE 0 END) AS possibly_exact"""
    write("3_lists_by_publisher_group.csv", f"""
        SELECT list, publisher_group, {measures} FROM ({lists_sql}) GROUP BY 1, 2
        UNION ALL SELECT list, 'all', {measures} FROM ({lists_sql}) GROUP BY 1
        ORDER BY 1, 2""")
    write("3_lists_by_dataset.csv", f"""
        SELECT x.list, x.datasetkey, ds.title, ds.publisher, {measures}
        FROM ({lists_sql}) AS x LEFT JOIN ds USING (datasetkey)
        GROUP BY 1, 2, 3, 4 HAVING count(*) >= 100 ORDER BY 1, records DESC, 2""")

    # Part 2c: species each publisher already generalises, with or without
    # being on our lists, and what their notes say.
    write("2c_generalised_species_by_publisher.csv", """
        SELECT ds.publisher, r.datasetkey, ds.title, r.specieskey, l.col_name,
               r.list_a, r.list_b, r.list_d,
               count(*) AS records,
               sum(CASE WHEN r.has_note THEN 1 ELSE 0 END) AS with_note,
               sum(CASE WHEN r.large_uncertainty AND NOT r.has_note THEN 1 ELSE 0 END) AS large_uncertainty_only
        FROM r LEFT JOIN ds USING (datasetkey) LEFT JOIN lists AS l ON l.specieskey = r.specieskey
        GROUP BY ALL
        HAVING sum(CASE WHEN r.generalised THEN 1 ELSE 0 END) > 0
        ORDER BY 1, 3, with_note DESC, records DESC, r.specieskey, r.datasetkey""")
    write("2c_publishers_summary.csv", """
        SELECT ds.publisher, r.datasetkey, ds.title, count(*) AS records,
               count(DISTINCT r.specieskey) AS species,
               sum(CASE WHEN r.has_note THEN 1 ELSE 0 END) AS with_note,
               count(DISTINCT CASE WHEN r.has_note THEN r.specieskey END) AS species_with_a_note,
               sum(CASE WHEN r.large_uncertainty AND NOT r.has_note THEN 1 ELSE 0 END) AS large_uncertainty_only,
               count(DISTINCT CASE WHEN r.generalised THEN r.specieskey END) AS species_generalised
        FROM r LEFT JOIN ds USING (datasetkey)
        GROUP BY ALL HAVING sum(CASE WHEN r.generalised THEN 1 ELSE 0 END) > 0
        ORDER BY with_note DESC, large_uncertainty_only DESC, r.datasetkey""")
    write("2c_note_texts.csv", """
        SELECT ds.title, field, note, count(*) AS records, count(DISTINCT r.specieskey) AS species
        FROM (SELECT datasetkey, specieskey, 'dataGeneralizations' AS field, data_generalizations AS note
              FROM r WHERE data_generalizations IS NOT NULL
              UNION ALL
              SELECT datasetkey, specieskey, 'informationWithheld', information_withheld
              FROM r WHERE information_withheld IS NOT NULL) AS r
        LEFT JOIN ds USING (datasetkey)
        GROUP BY ALL ORDER BY records DESC, ds.title, field, note LIMIT 500""")

    # Part 4: possibly exact records.
    write("4_possibly_exact_by_species.csv", """
        SELECT r.specieskey, l.col_name, r.list_a, r.list_b, r.list_d, count(*) AS possibly_exact,
               count(DISTINCT r.datasetkey) AS datasets
        FROM r LEFT JOIN lists AS l USING (specieskey) WHERE r.possibly_exact
        GROUP BY ALL ORDER BY possibly_exact DESC, r.specieskey""")
    write("4_possibly_exact_by_dataset.csv", """
        SELECT r.datasetkey, ds.title, ds.publisher, r.publisher_group, count(*) AS possibly_exact,
               count(DISTINCT r.specieskey) AS species
        FROM r LEFT JOIN ds USING (datasetkey) WHERE r.possibly_exact
        GROUP BY ALL ORDER BY possibly_exact DESC, r.datasetkey""")
    # About 20 examples: the record with the most decimal places of each of
    # the 20 species with the most possibly exact records, spread over
    # datasets.
    write("4_possibly_exact_examples.csv", """
        WITH top AS (SELECT specieskey FROM r WHERE possibly_exact GROUP BY 1 ORDER BY count(*) DESC LIMIT 20),
             ranked AS (SELECT r.*, row_number() OVER (PARTITION BY r.specieskey
                                                       ORDER BY r.decimals DESC, r.gbifid) AS n
                        FROM r JOIN top USING (specieskey) WHERE r.possibly_exact)
        SELECT l.col_name, ranked.specieskey,
               concat_ws(', ', CASE WHEN ranked.list_a THEN 'a' END, CASE WHEN ranked.list_b THEN 'b' END,
                         CASE WHEN ranked.list_d THEN 'd' END) AS lists,
               ds.title AS dataset, ranked.gbifid, ranked.lat_given, ranked.lon_given,
               ranked.decimallatitude, ranked.decimallongitude, ranked.uncertainty
        FROM ranked LEFT JOIN lists AS l USING (specieskey) LEFT JOIN ds USING (datasetkey)
        WHERE ranked.n = 1 ORDER BY l.col_name""")

    # Part 5: species on the lists that no publisher generalises.
    write("5_listed_species_generalisation.csv", """
        SELECT r.specieskey, l.col_name, r.list_a, r.list_b, r.list_d, count(*) AS records,
               sum(CASE WHEN r.generalised THEN 1 ELSE 0 END) AS generalised_records,
               sum(CASE WHEN r.has_note THEN 1 ELSE 0 END) AS with_note,
               sum(CASE WHEN r.possibly_exact THEN 1 ELSE 0 END) AS possibly_exact_records,
               count(DISTINCT r.datasetkey) AS datasets,
               count(DISTINCT CASE WHEN r.generalised THEN r.datasetkey END) AS datasets_that_generalise
        FROM r LEFT JOIN lists AS l USING (specieskey) WHERE r.list_a OR r.list_b OR r.list_d
        GROUP BY ALL ORDER BY generalised_records = 0 DESC, records DESC, r.specieskey""")

    # A short text report of the headline numbers.
    lines = [f"Sensitive-species record check, {time.strftime('%Y-%m-%d %H:%M')}",
             f"coordinates as text from: {'verbatimLatitude/verbatimLongitude' if has_verbatim else 'decimalLatitude/decimalLongitude as GBIF wrote them'}",
             f"list d in the list file: {'yes' if has_d else 'no'}", ""]
    total, unmatched = con.execute("SELECT count(*), sum(CASE WHEN lat_given IS NULL THEN 1 ELSE 0 END) FROM r").fetchone()
    lines.append(f"site records: {total:,}; not found in the zip's occurrence.txt: {unmatched:,}")
    for list_name, records, notes, large, exact in con.execute(f"""
            SELECT list, count(*), sum(CASE WHEN has_note THEN 1 ELSE 0 END),
                   sum(CASE WHEN large_uncertainty THEN 1 ELSE 0 END), sum(CASE WHEN possibly_exact THEN 1 ELSE 0 END)
            FROM ({lists_sql}) GROUP BY 1 ORDER BY 1""").fetchall():
        lines.append(f"{list_name:30} {records:>12,} records, {notes:>10,} with a note, "
                     f"{large:>10,} over 10 km, {exact:>10,} possibly exact")
    never = con.execute("""SELECT count(*), sum(records) FROM (
                               SELECT specieskey, count(*) AS records, sum(CASE WHEN generalised THEN 1 ELSE 0 END) AS g
                               FROM r WHERE list_a OR list_b OR list_d GROUP BY 1) WHERE g = 0""").fetchone()
    lines.append(f"species on a, b or d with no generalised record at all: {never[0]:,} ({never[1] or 0:,} records)")
    text = "\n".join(lines) + "\n"
    open(os.path.join(out, "report.txt"), "w", encoding="utf-8").write(text)
    print(text)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    modes = parser.add_subparsers(dest="mode", required=True)
    lists = modes.add_parser("lists", help="write the candidate species lists (on the Mac)")
    lists.add_argument("--cards", required=True)
    lists.add_argument("--bcsee-by-species", required=True)
    lists.add_argument("--bcsee", required=True, help="bcsee_status.parquet, every BCSEE column")
    lists.add_argument("--out", required=True)
    records = modes.add_parser("records", help="check the records (on the server)")
    records.add_argument("--zip", required=True)
    records.add_argument("--occurrence", required=True)
    records.add_argument("--datasets", required=True)
    records.add_argument("--cards", required=True)
    records.add_argument("--lists", required=True)
    records.add_argument("--workdir", required=True)
    records.add_argument("--outdir", required=True)
    records.add_argument("--memory-limit", default="8GB")
    records.add_argument("--threads", type=int, default=8)
    records.add_argument("--reuse-extract", action="store_true",
                         help="use the occurrence_fields.csv already in --workdir")
    args = parser.parse_args()
    if args.mode == "lists":
        make_lists(args)
        return
    os.makedirs(args.workdir, exist_ok=True)
    extracted = os.path.join(args.workdir, "occurrence_fields.csv")
    if args.reuse_extract and os.path.exists(extracted):
        with open(extracted, encoding="utf-8") as handle:
            fields = handle.readline().strip().split(",")
    else:
        fields = extract_fields(args.zip, extracted)
    report(args, fields)


if __name__ == "__main__":
    main()
