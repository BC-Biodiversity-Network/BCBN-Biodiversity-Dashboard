"""
build_hex_aggregates.py

Bin the BC occurrence records into H3 hexagons so the dashboard map can
draw a density surface. Tens of millions of points cannot go to the browser;
the browser gets one small table per zoom tier instead.

Input:  bc_occurrence.parquet   (from build_dwca_tables.py: inside BC's
                                 polygon, quality-filtered, one row per
                                 record, with each record's H3 cell in
                                 h3_r4 to h3_r7)
Output goes to two places, in two formats:

  --outdir (backend/data/), parquet, not committed:
        bc_hex_r{4..7}.parquet   (one row per non-empty hexagon)
        bc_hex_r{5,6}_<species>.parquet  (the same aggregation for one species,
                                          as a filter-then-aggregate test case)

  --frontend-dir (frontend/public/data/), gzipped CSV, committed:
        bc_hex_r{4..7}.csv.gz    (the four tiers the map serves)

Two formats because a browser cannot open a parquet file without a WebAssembly
parser. The tiers the map needs come to a few hundred KB as gzipped CSV, so
loading a parser would cost more than it saves. The parquet files stay for
analysis work, where the types and the compression are worth having.

Schema of every output, so the browser can use one loader for all of them:
    h3_cell           VARCHAR  15-char H3 index string (what deck.gl's
                               H3HexagonLayer takes as `hexagon`)
    occurrences       BIGINT   records in that cell
    distinct_species  INTEGER  distinct `species` values in that cell

No H3 is computed here: each resolution is a plain GROUP BY on that
resolution's h3_rN column, which build_dwca_tables.py fills with the h3
Python package. So this runs on the cluster's DuckDB 0.10.3, which has no h3
extension (that only exists from DuckDB 1.0 on), and needs no h3 package
either. The browser draws the hexagons from the cell ids itself, with h3-js.

build_dwca_tables.py bins each resolution directly from
decimallatitude/decimallongitude. Do NOT derive a coarse resolution by
calling h3_cell_to_parent on a finer one: H3's hexagons only nest
approximately, and measured on this data 5.96% of records land in a
different res-5 cell under a res-7 rollup than under direct binning.

Resolution 3 is no longer written: bc_occurrence has no h3_r3 column, and
the front end never used it.

Run:
    python build_hex_aggregates.py --occurrence ~/bcbn/data/dwca/bc_occurrence.parquet --outdir ~/bcbn/data
"""

import argparse
import csv
import gzip
import io
import os
import time
from pathlib import Path

import duckdb

# The resolutions written as parquet: every h3_rN column in bc_occurrence.
RESOLUTIONS = [4, 5, 6, 7]

# Aggregated separately as a filter-then-aggregate test case. American robin:
# 914,491 records spread over more BC hexagons than any other common species,
# so it exercises the taxon filter across the whole province rather than one
# coastal cluster.
TEST_SPECIES = "Turdus migratorius"
TEST_RESOLUTIONS = [5, 6]

# The resolutions copied to the front end as gzipped CSV. Res 4 is the province
# view, 5 the regional view, 6 the local view, and 7 the closest view, added so
# that zooming past about level 8 keeps resolving finer instead of just making
# the same hexagons bigger.
#
# Res 3 is not shipped (or built): at 127 cells for the whole province it is too
# coarse to draw. Res 8 is deliberately not shipped either. Hexagons there are about 1.1 km
# across, which is finer than anyone has asked for, and the file would add
# roughly 800 KB to the repository. Res 7 is already about 2.8 km across, finer
# than the 5 km hexagons used by the Biodiversite Quebec atlas we are following.
FRONTEND_RESOLUTIONS = [4, 5, 6, 7]


def connect(threads, memory_limit, temp_dir):
    """Open a duckdb connection with a thread count and memory limit."""
    con = duckdb.connect(config={"threads": threads, "memory_limit": memory_limit})
    if temp_dir:
        con.execute(f"SET temp_directory='{temp_dir}'")
    return con


def check_columns(con, occurrence_path):
    """Stop with a clear message if the input lacks a column this script
    needs, e.g. when it is given bc_clean.parquet, which has no H3 cells."""
    columns = {
        row[0]
        for row in con.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{occurrence_path}')"
        ).fetchall()
    }
    needed = ["species"] + [f"h3_r{r}" for r in RESOLUTIONS]
    missing = [c for c in needed if c not in columns]
    if missing:
        raise SystemExit(
            f"{occurrence_path} has no column(s) {missing}. This script reads "
            f"bc_occurrence.parquet from build_dwca_tables.py, which holds each "
            f"record's H3 cells."
        )


def aggregate(con, occurrence_path, out_path, resolution, species=None):
    """Group the records by their cell at one resolution and write a
    parquet. Returns summary figures for the progress line."""
    # A record without a cell would have no hexagon to draw, so it is left
    # out. build_dwca_tables.py keeps only records with coordinates, so there
    # should be none.
    where = f"WHERE h3_r{resolution} IS NOT NULL"
    params = []
    if species is not None:
        where += " AND species = ?"
        params = [species]

    con.execute(
        f"""
        COPY (
            SELECT
              h3_r{resolution}                 AS h3_cell,
              count(*)::BIGINT                 AS occurrences,
              count(DISTINCT species)::INTEGER AS distinct_species
            FROM read_parquet('{occurrence_path}')
            {where}
            GROUP BY 1
            ORDER BY h3_cell
        ) TO '{out_path}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """,
        params,
    )

    return con.execute(
        f"""SELECT count(*), sum(occurrences), min(occurrences),
                   quantile_cont(occurrences, 0.5), max(occurrences),
                   count(*) FILTER (occurrences = 1)
            FROM read_parquet('{out_path}')"""
    ).fetchone()


def write_frontend_csv(con, parquet_path, csv_path):
    """Copy one finished aggregate to a gzipped CSV for the browser to read.

    Reads the parquet that was just written rather than re-querying the
    occurrence data, so the CSV cannot drift from the parquet it mirrors.
    Returns the size of the CSV in bytes.
    """
    rows = con.execute(
        f"""SELECT h3_cell, occurrences, distinct_species
            FROM read_parquet('{parquet_path}')
            ORDER BY h3_cell"""
    ).fetchall()

    # These files are committed, so the same data has to produce the same bytes
    # every time. Otherwise every run of the pipeline shows up as a change in
    # git that contains nothing. Gzip works against that by default: it records
    # the name of the source file and the time of compression in its header, and
    # both of those differ from run to run. Passing an empty filename and an
    # mtime of 0 leaves them out.
    #
    # This is written by hand instead of with df.to_csv("x.csv.gz") because
    # to_csv gives no way to set either of those two fields. Please do not
    # simplify it back.
    with open(csv_path, "wb") as raw_file:
        with gzip.GzipFile(
            filename="", mtime=0, compresslevel=9, fileobj=raw_file, mode="wb"
        ) as gzip_file:
            with io.TextIOWrapper(
                gzip_file, encoding="utf-8", newline=""
            ) as text_file:
                # The column names and their order match the parquet exactly, so
                # the front end loader does not need to know which format it is
                # reading. Integers are written by the csv module as plain
                # digits: no thousands separators, no scientific notation, and
                # no index column.
                writer = csv.writer(text_file, lineterminator="\n")
                writer.writerow(["h3_cell", "occurrences", "distinct_species"])
                writer.writerows(rows)

    return os.path.getsize(csv_path)


def main():
    """Build every aggregate, then copy the front end tiers to gzipped CSV."""
    parser = argparse.ArgumentParser(
        description="Bin the BC occurrence records into H3 hexagons, using "
        "the cells build_dwca_tables.py stored on each record."
    )
    parser.add_argument(
        "--occurrence",
        default="/home/songyanf/bcbn/data/dwca/bc_occurrence.parquet",
        help="Path to bc_occurrence.parquet from build_dwca_tables.py.",
    )
    parser.add_argument(
        "--outdir",
        default="/home/songyanf/bcbn/data",
        help="Directory to write the hexagon aggregates into.",
    )
    parser.add_argument(
        "--frontend-dir",
        default=str(
            Path(__file__).resolve().parents[2] / "frontend" / "public" / "data"
        ),
        help="Directory to write the browser-readable gzipped CSVs into.",
    )
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--memory-limit", default="10GB")
    parser.add_argument("--temp-dir", default=None)
    args = parser.parse_args()

    con = connect(args.threads, args.memory_limit, args.temp_dir)
    check_columns(con, args.occurrence)
    os.makedirs(args.outdir, exist_ok=True)
    os.makedirs(args.frontend_dir, exist_ok=True)

    print(f"Occurrences:   {args.occurrence}")
    print(f"Parquet dir:   {args.outdir}")
    print(f"Front end dir: {args.frontend_dir}\n")

    t_all = time.time()
    for r in RESOLUTIONS:
        out = os.path.join(args.outdir, f"bc_hex_r{r}.parquet")
        t = time.time()
        cells, occ, mn, med, mx, single = aggregate(con, args.occurrence, out, r)
        size = os.path.getsize(out)
        print(
            f"res {r}: {cells:>7,} cells  {occ:>12,} occurrences  "
            f"min {mn} / median {med:.0f} / max {mx:,}  "
            f"{single:,} singletons ({100 * single / cells:.1f}%)  "
            f"{size / 1024:.0f} KB  {time.time() - t:.1f}s"
        )

    slug = TEST_SPECIES.lower().replace(" ", "_")
    for r in TEST_RESOLUTIONS:
        out = os.path.join(args.outdir, f"bc_hex_r{r}_{slug}.parquet")
        t = time.time()
        cells, occ, mn, med, mx, single = aggregate(
            con, args.occurrence, out, r, species=TEST_SPECIES
        )
        size = os.path.getsize(out)
        print(
            f"res {r} [{TEST_SPECIES}]: {cells:>6,} cells  {occ:>9,} occurrences  "
            f"min {mn} / median {med:.0f} / max {mx:,}  {size / 1024:.0f} KB  "
            f"{time.time() - t:.1f}s"
        )

    # The browser copies are made from the finished parquet files, so this runs
    # after the loops above rather than inside them.
    print()
    csv_total = 0
    for r in FRONTEND_RESOLUTIONS:
        parquet_path = os.path.join(args.outdir, f"bc_hex_r{r}.parquet")
        csv_path = os.path.join(args.frontend_dir, f"bc_hex_r{r}.csv.gz")
        size = write_frontend_csv(con, parquet_path, csv_path)
        csv_total += size
        print(f"front end res {r}: {size:>7,} bytes  {csv_path}")
    print(f"front end total: {csv_total:,} bytes ({csv_total / 1024:.0f} KB)")

    print(f"\nDone in {time.time() - t_all:.1f}s")
    con.close()


if __name__ == "__main__":
    main()
