"""
build_geonames_lookup.py

Build the GeoNames lookup table that geonames_lookup.py queries, from the
GeoNames export files already in backend/data/geonames/ (see
SOURCE_geonames.md). Nothing is downloaded. The table is built once on the
Mac and then copied to Fir.

Output, in backend/data/geonames/ (ignored by git):
  geonames_lookup.parquet     one row per name, sorted by the normalised name
  geonames_codes.json         the name of each code, from GeoNames' own code
                              files: countries (countryInfo.txt), provinces and
                              states (admin1CodesASCII.txt), feature codes
                              (featureCodes_en.txt) and the nine feature classes
                              (FEATURE_CLASSES below)
  geonames_lookup_build.txt   what went in, what was skipped and why, counts

One row per name of each place: its main name, its ASCII name (only where it
differs from the main name; where they are the same it would be a second,
identical row), and every alternate name from alternateNamesV2.txt, except
the ones that are not names (SKIPPED_CODES below). Columns:

  norm            the normalised name, used only for matching
                  (geonames_lookup.normalise, the same function the queries use)
  name            the name exactly as in GeoNames (the main, ASCII or
                  alternate name this row is for)
  main_name       the place's main name, so a match on an alternate name
                  can still be shown as the place it is
  name_type       main, ascii or alternate
  language        the alternate name's language code ("" for none, "abbr" for
                  an abbreviation, and so on)
  is_historic     the alternate name is marked historic
  geonameid, feature_class, feature_code, country_code, cc2, admin1_code,
  population, latitude, longitude   as in allCountries.txt (cc2 lists any
                  other countries a feature lies in, such as "CA,US" for the
                  Strait of Georgia)
  region          bc (country CA, admin1 02), canada_other (any other CA),
                  outside_canada (everything else, including features with no
                  country such as oceans)
  source          geonames

The big files are read by DuckDB straight from disk, in a database that
spills to disk when it runs short, so they are never loaded whole into
memory. Normalising is done once per distinct name.

Run, from backend/:
    python pipeline/lunaris/build_geonames_lookup.py
"""

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from geonames_lookup import normalise  # noqa: E402

BACKEND = os.path.dirname(os.path.dirname(HERE))
GEONAMES = os.path.join(BACKEND, "data", "geonames")

# The nine feature classes. GeoNames describes them on
# https://www.geonames.org/export/codes.html rather than in a file, so their
# descriptions are copied from there.
FEATURE_CLASSES = {
    "A": "country, state, region,...",
    "H": "stream, lake, ...",
    "L": "parks,area, ...",
    "P": "city, village,...",
    "R": "road, railroad",
    "S": "spot, building, farm",
    "T": "mountain,hill,rock,...",
    "U": "undersea",
    "V": "forest,heath,...",
}

# Alternate "names" that are not names, and why they are skipped. The codes
# come from alternateNamesV2.txt's isolanguage column and its readme; every
# other value there is a language code (ISO 639, sometimes with a country or
# variant, such as zh-CN) or one of three kinds of real name kept on purpose:
# abbr (abbreviations such as "B.C."), piny (pinyin spellings) and fr_1793
# (names from the French Revolution).
SKIPPED_CODES = {
    "link": "a web address, mostly Wikipedia",
    "wkdt": "a Wikidata id",
    "post": "a postal code",
    "iata": "an airport code (IATA)",
    "icao": "an airport code (ICAO)",
    "faac": "an airport code (FAA)",
    "unlc": "a UN/LOCODE trade and transport location code",
    "uicn": "a railway station code (UIC)",
    "tcid": "a Transport Canada aerodrome id",
    "lauc": "a local administrative unit code",
    "nuts": "a European statistical region code (NUTS)",
    "geoid": "a United States census id",
    "phon": "a pronunciation guide (and some phone numbers), not a spelling of the name",
}

ALL_COUNTRIES_COLUMNS = {
    "geonameid": "INTEGER", "name": "VARCHAR", "asciiname": "VARCHAR", "alternatenames": "VARCHAR",
    "latitude": "DOUBLE", "longitude": "DOUBLE", "feature_class": "VARCHAR", "feature_code": "VARCHAR",
    "country_code": "VARCHAR", "cc2": "VARCHAR", "admin1_code": "VARCHAR", "admin2_code": "VARCHAR",
    "admin3_code": "VARCHAR", "admin4_code": "VARCHAR", "population": "BIGINT", "elevation": "VARCHAR",
    "dem": "VARCHAR", "timezone": "VARCHAR", "modification_date": "VARCHAR",
}
ALTERNATE_COLUMNS = {
    "alternate_name_id": "INTEGER", "geonameid": "INTEGER", "isolanguage": "VARCHAR",
    "alternate_name": "VARCHAR", "is_preferred": "VARCHAR", "is_short": "VARCHAR",
    "is_colloquial": "VARCHAR", "is_historic": "VARCHAR", "from_period": "VARCHAR", "to_period": "VARCHAR",
}


def read_tsv(path, columns):
    """
    Return the DuckDB expression that reads one of GeoNames' tab-separated
    files from disk. The files have no header and no quoting (a name may
    contain a quote mark), so quoting and escaping are switched off.
    """
    spec = ", ".join(f"'{name}': '{kind}'" for name, kind in columns.items())
    path = path.replace("'", "''")
    return (f"read_csv('{path}', delim='\\t', header=false, quote='', escape='', "
            f"columns={{{spec}}}, null_padding=true)")


def normalise_batch(names):
    """Normalise a batch of names for DuckDB (an Arrow array in, an Arrow array out)."""
    import pyarrow as pa

    return pa.array([None if name is None else normalise(name) for name in names.to_pylist()],
                    type=pa.string())


def build(con, folder, out, row_group_size):
    """Build the table into out with the DuckDB connection con, and return the lines for the build notes."""
    notes = []

    def step(label, sql):
        """Run one SQL step, print how long it took, and keep a note of it."""
        started = time.monotonic()
        con.execute(sql)
        line = f"{label}: {time.monotonic() - started:.0f} s"
        print(line, flush=True)
        notes.append(line)

    places_file = read_tsv(os.path.join(folder, "allCountries.txt"), ALL_COUNTRIES_COLUMNS)
    step("read allCountries.txt", f"""
        CREATE TABLE places AS
        SELECT geonameid, name, asciiname, latitude, longitude, feature_class, feature_code,
               country_code, coalesce(cc2, '') AS cc2, admin1_code, coalesce(population, 0) AS population,
               CASE WHEN country_code = 'CA' AND admin1_code = '02' THEN 'bc'
                    WHEN country_code = 'CA' THEN 'canada_other'
                    ELSE 'outside_canada' END AS region
        FROM {places_file}""")

    admin1 = os.path.join(folder, "admin1CodesASCII.txt").replace("'", "''")
    step("read admin1CodesASCII.txt", f"""
        CREATE TABLE admin1 AS
        SELECT split_part(code, '.', 1) AS country_code, split_part(code, '.', 2) AS admin1_code,
               admin1_name
        FROM read_csv('{admin1}', delim='\\t', header=false, quote='', escape='',
                      columns={{'code': 'VARCHAR', 'admin1_name': 'VARCHAR', 'ascii': 'VARCHAR', 'id': 'VARCHAR'}})""")

    countries = os.path.join(folder, "countryInfo.txt").replace("'", "''")
    step("read countryInfo.txt", f"""
        CREATE TABLE countries AS
        SELECT column00 AS country_code, column04 AS country_name
        FROM read_csv('{countries}', delim='\\t', header=false, quote='', escape='', comment='#',
                      all_varchar=true, null_padding=true)""")

    alternates_file = read_tsv(os.path.join(folder, "alternateNamesV2.txt"), ALTERNATE_COLUMNS)
    skipped = ", ".join(f"'{code}'" for code in SKIPPED_CODES)
    step("count the skipped alternate names", f"""
        CREATE TABLE skipped AS
        SELECT isolanguage AS code, count(*) AS n FROM {alternates_file}
        WHERE isolanguage IN ({skipped}) GROUP BY 1""")
    step("gather every name", f"""
        CREATE TABLE names AS
        SELECT geonameid, name, 'main' AS name_type, '' AS language, false AS is_historic FROM places
        UNION ALL
        SELECT geonameid, asciiname, 'ascii', '', false FROM places
        WHERE asciiname IS NOT NULL AND asciiname <> '' AND asciiname <> name
        UNION ALL
        SELECT geonameid, alternate_name, 'alternate', coalesce(isolanguage, ''), is_historic = '1'
        FROM {alternates_file}
        WHERE coalesce(isolanguage, '') NOT IN ({skipped})
          AND alternate_name IS NOT NULL AND trim(alternate_name) <> ''
          AND geonameid IN (SELECT geonameid FROM places)""")
    step("normalise each distinct name", """
        CREATE TABLE norms AS SELECT name, normalise_batch(name) AS norm
        FROM (SELECT DISTINCT name FROM names)""")
    out_sql = out.replace("'", "''")
    step("join, sort by normalised name and write parquet", f"""
        COPY (
            SELECT n.norm, m.name, p.name AS main_name, m.name_type, m.language, m.is_historic,
                   m.geonameid,
                   p.feature_class, p.feature_code, p.country_code, p.cc2, p.admin1_code,
                   p.population, p.latitude, p.longitude,
                   p.region, 'geonames' AS source
            FROM names m
            JOIN norms n ON n.name = m.name
            JOIN places p ON p.geonameid = m.geonameid
            WHERE n.norm <> ''
            ORDER BY n.norm, m.geonameid
        ) TO '{out_sql}' (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE {int(row_group_size)})""")

    notes.append("")
    places, = con.execute("SELECT count(*) FROM places").fetchone()
    notes.append(f"places: {places:,}")
    for name_type, n in con.execute(f"SELECT name_type, count(*) FROM read_parquet('{out_sql}') "
                                    "GROUP BY 1 ORDER BY 2 DESC").fetchall():
        notes.append(f"rows of {name_type} names: {n:,}")
    total, historic, bc = con.execute(
        f"SELECT count(*), count(*) FILTER (WHERE is_historic), count(*) FILTER (WHERE region = 'bc') "
        f"FROM read_parquet('{out_sql}')").fetchone()
    notes.append(f"rows in all: {total:,} ({historic:,} historic names, {bc:,} rows for places in BC)")
    empty, = con.execute("SELECT count(*) FROM names m JOIN norms n USING (name) WHERE n.norm = ''").fetchone()
    notes.append(f"names left out because nothing is left after normalising (only punctuation): {empty:,}")
    notes.append("")
    notes.append("Alternate names skipped because they are not names:")
    counts = dict(con.execute("SELECT code, n FROM skipped").fetchall())
    for code, why in SKIPPED_CODES.items():
        notes.append(f"  {code:6s} {counts.get(code, 0):>10,}  {why}")
    return notes


def write_codes(con, folder, path):
    """
    Write the name of each code the lookup shows, from GeoNames' own code
    files, to a small JSON file: countries by ISO code, provinces and states
    by "country.admin1" ("CA.02"), feature codes by "class.code" ("H.STM"),
    and the feature classes. Return how many of each were written.
    """
    features = {}
    with open(os.path.join(folder, "featureCodes_en.txt"), encoding="utf-8") as handle:
        for line in handle:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 2 and parts[0] != "null":
                features[parts[0]] = parts[1]
    codes = {
        "source": "GeoNames code files (countryInfo.txt, admin1CodesASCII.txt, featureCodes_en.txt) "
                  "and https://www.geonames.org/export/codes.html for the feature classes",
        "countries": dict(con.execute("SELECT country_code, country_name FROM countries").fetchall()),
        "admin1": {f"{c}.{a}": n for c, a, n in con.execute(
            "SELECT country_code, admin1_code, admin1_name FROM admin1").fetchall()},
        "feature_classes": FEATURE_CLASSES,
        "feature_codes": features,
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(codes, handle, ensure_ascii=False, indent=1, sort_keys=True)
    return {key: len(value) for key, value in codes.items() if isinstance(value, dict)}


def main():
    """Build the lookup table and write the build notes."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("--geonames", default=GEONAMES, help="the folder with the GeoNames files")
    parser.add_argument("--out", default=os.path.join(GEONAMES, "geonames_lookup.parquet"))
    parser.add_argument("--memory-limit", default="8GB", help="DuckDB's limit; it spills to disk beyond it")
    # Measured on the Mac with 2 threads: 5,000 rows per group gave 172 ms per
    # exact query, 20,000 gave 67 ms, 100,000 gave 20 ms, 300,000 gave 41 ms
    # and 1,000,000 gave 130 ms. Small groups cost time in their metadata,
    # large ones in reading more rows than needed.
    parser.add_argument("--row-group-size", type=int, default=100000,
                        help="rows per parquet row group (see the timings above)")
    args = parser.parse_args()

    import duckdb
    import pyarrow  # noqa: F401  (needed on the Mac for the normalising step)

    started = time.monotonic()
    con = duckdb.connect()
    con.execute(f"SET memory_limit = '{args.memory_limit}'")
    con.execute("SET preserve_insertion_order = false")
    con.execute("SET enable_progress_bar = false")
    con.execute(f"SET temp_directory = '{os.path.join(args.geonames, '.duckdb_spill')}'")
    con.create_function("normalise_batch", normalise_batch, ["VARCHAR"], "VARCHAR", type="arrow")
    notes = build(con, args.geonames, args.out, args.row_group_size)
    counts = write_codes(con, args.geonames, os.path.join(os.path.dirname(args.out), "geonames_codes.json"))
    notes.append("")
    notes.append("code names written to geonames_codes.json: " + ", ".join(f"{k} {v:,}" for k, v in counts.items()))
    minutes = (time.monotonic() - started) / 60
    size = os.path.getsize(args.out)
    notes[0:0] = [f"built {args.out}", f"in {minutes:.1f} minutes; {size / 1e6:,.0f} MB on disk", ""]
    notes += ["", "Place names from GeoNames (https://www.geonames.org/), licensed under CC BY 4.0 "
              "(https://creativecommons.org/licenses/by/4.0/). Filtered and reformatted for the "
              "Lunaris BC filter."]
    with open(os.path.join(os.path.dirname(args.out), "geonames_lookup_build.txt"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(notes) + "\n")
    print("\n".join(notes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
