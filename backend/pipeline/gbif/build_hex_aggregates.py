"""
build_hex_aggregates.py

Bin the BC records into H3 hexagons so the dashboard's overview map can draw
a density surface. Tens of millions of points cannot go to the browser; the
browser gets one small table per zoom tier instead.

Input:  species_hex.parquet  (from build_species_tables.py, built with
                              --key-merge: records per species, year and
                              hexagon, with each hexagon at h3_r4 to h3_r7)
Output goes to two places, in two formats:

  --outdir, parquet, not committed:
        bc_hex_r{4..7}.parquet   (one row per non-empty hexagon)

  --frontend-dir, gzipped CSV (the four tiers the map serves):
        bc_hex_r{4..7}.csv.gz
  The pipeline runs on the server, so --frontend-dir there is a folder
  outside the repository (e.g. ~/bcbn/data/gbif_tables/hex_frontend_csv).
  The CSVs are then copied by hand to the Mac, compared with the committed
  ones, copied into frontend/public/data and committed from the Mac.

Two formats because a browser cannot open a parquet file without a WebAssembly
parser. The tiers the map needs come to a few hundred KB as gzipped CSV, so
loading a parser would cost more than it saves. The parquet files stay for
analysis work, where the types and the compression are worth having.

Schema of every output, so the browser can use one loader for all of them:
    h3_cell           VARCHAR  15-char H3 index string (what deck.gl's
                               H3HexagonLayer takes as `hexagon`)
    occurrences       BIGINT   records in that cell, including records with
                               no specieskey (identified only to genus or
                               above)
    distinct_species  INTEGER  distinct specieskey in that cell, after the
                               key merge

Why from species_hex: species_hex already has the key merge applied (the
merge happens in build_species_tables.py only), so the species counted here
are exactly the species cards the site shows for a hexagon. Each resolution
is a plain GROUP BY on species_hex's h3_rN column: occurrences is
sum(occurrences), distinct_species is count(DISTINCT specieskey), which
leaves out the rows with no specieskey. Records with no cell at a resolution
have no hexagon to draw and are left out (build_dwca_tables.py keeps only
records with coordinates, so there should be none).

No H3 is computed here, so this runs on the cluster's DuckDB 0.10.3, which has
no h3 extension, and needs no h3 package either. The browser draws the
hexagons from the cell ids itself, with h3-js. build_dwca_tables.py bins each
resolution directly from decimallatitude/decimallongitude; do NOT derive a
coarse resolution by calling h3_cell_to_parent on a finer one: H3's hexagons
only nest approximately, and measured on this data 5.96% of records land in a
different res-5 cell under a res-7 rollup than under direct binning.

Checks, printed at the end (they are never written into the outputs):
  - for each resolution, the total occurrences equal the total in
    species_hex;
  - one row per hexagon, and no row without a cell;
  - each CSV holds exactly the rows of its parquet.
Outputs are only replaced when every check passes: everything is written to
temporary names first (bc_hex_r5.tmp.parquet, bc_hex_r5.tmp.csv.gz), checked
there, and moved onto the real names only if nothing failed. If a check
fails, the previous files are left untouched, the temporary files stay for
inspection, and the script exits with status 1.

Run order:
    request_gbif_download.py -> build_dwca_tables.py
        -> fetch_col_names.py (once per Catalogue of Life release)
        -> make_species_key_merge.py
        -> build_species_tables.py --key-merge --col-names
        -> build_hex_aggregates.py

Run (from backend/):
    python pipeline/gbif/build_hex_aggregates.py \\
        --species-hex ~/bcbn/data/gbif_tables/species_merged/species_hex.parquet \\
        --outdir ~/bcbn/data/gbif_tables/hex \\
        --frontend-dir ~/bcbn/data/gbif_tables/hex_frontend_csv

On the server, ~/bcbn/data/gbif_tables/hex/ already holds the bc_hex parquet
files and the two robin test files (bc_hex_r5_turdus_migratorius.parquet,
bc_hex_r6_turdus_migratorius.parquet) from the old version of this script.
bc_hex_r4.parquet to bc_hex_r7.parquet there are replaced after the checks
pass. The robin files are no longer written, so they stay there until they
are deleted by hand.
"""

import argparse
import csv
import gzip
import io
import os
import sys
import time

import duckdb

from safe_outputs import (clear_temporary, replace_outputs, report_not_replaced,
                          temporary_path)

# The resolutions written: every h3_rN column in species_hex. Res 4 is the
# province view, 5 the regional view, 6 the local view, and 7 the closest
# view, added so that zooming past about level 8 keeps resolving finer
# instead of just making the same hexagons bigger. All four are also written
# as gzipped CSV for the front end.
#
# Res 3 is not built: at 127 cells for the whole province it is too coarse
# to draw. Res 8 is deliberately not built either. Hexagons there are about
# 1.1 km across, which is finer than anyone has asked for, and the file would
# add roughly 800 KB to the repository. Res 7 is already about 2.8 km across,
# finer than the 5 km hexagons used by the Biodiversite Quebec atlas we are
# following.
RESOLUTIONS = [4, 5, 6, 7]


def connect(threads, memory_limit, temp_dir):
    """Open a duckdb connection with a thread count and memory limit."""
    con = duckdb.connect(config={"threads": threads, "memory_limit": memory_limit})
    if temp_dir:
        con.execute(f"SET temp_directory='{temp_dir}'")
    return con


def check_columns(con, species_hex_path):
    """Stop with a clear message if species_hex.parquet is missing or lacks
    a column this script needs (for example when it is given
    bc_occurrence.parquet by mistake)."""
    if not os.path.exists(species_hex_path):
        raise SystemExit(
            f"{species_hex_path} not found. Run build_species_tables.py --key-merge "
            f"first: the overview map is built from its species_hex.parquet."
        )
    columns = {
        row[0]
        for row in con.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{species_hex_path}')"
        ).fetchall()
    }
    needed = ["specieskey", "occurrences"] + [f"h3_r{r}" for r in RESOLUTIONS]
    missing = [c for c in needed if c not in columns]
    if missing:
        raise SystemExit(
            f"{species_hex_path} has no column(s) {missing}. This script reads "
            f"species_hex.parquet from build_species_tables.py."
        )


def aggregate(con, species_hex_path, out_path, resolution):
    """Group species_hex by its cell at one resolution and write a parquet:
    occurrences summed, distinct specieskey counted."""
    col = f"h3_r{resolution}"
    con.execute(
        f"""
        COPY (
            SELECT
              {col}                                 AS h3_cell,
              sum(occurrences)::BIGINT              AS occurrences,
              count(DISTINCT specieskey)::INTEGER   AS distinct_species
            FROM read_parquet('{species_hex_path}')
            WHERE {col} IS NOT NULL
            GROUP BY {col}
            ORDER BY h3_cell
        ) TO '{out_path}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )


def summary_figures(con, parquet_path):
    """Return (cells, occurrences, min, median, max, cells with a single
    record) for one aggregate, for the progress line."""
    return con.execute(
        f"""SELECT count(*), sum(occurrences), min(occurrences),
                   quantile_cont(occurrences, 0.5), max(occurrences),
                   count(*) FILTER (occurrences = 1)
            FROM read_parquet('{parquet_path}')"""
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


def check_outputs(con, species_hex_path, parquet_path, csv_path, resolution):
    """Return (name, passed, detail) results for one resolution's outputs:
    total occurrences equal species_hex's total, one row per hexagon and no
    row without a cell, and the CSV holds exactly the parquet's rows."""
    expected = con.execute(
        f"SELECT coalesce(sum(occurrences), 0) FROM read_parquet('{species_hex_path}')"
    ).fetchone()[0]
    total, rows, cells, no_cell = con.execute(
        f"""SELECT coalesce(sum(occurrences), 0), count(*), count(DISTINCT h3_cell),
                   count(*) FILTER (WHERE h3_cell IS NULL)
            FROM read_parquet('{parquet_path}')"""
    ).fetchone()
    differing = con.execute(
        f"""SELECT count(*) FROM (
                (SELECT h3_cell, occurrences, distinct_species FROM read_parquet('{parquet_path}')
                 EXCEPT
                 SELECT h3_cell, occurrences, distinct_species FROM read_csv('{csv_path}',
                     header = true, columns = {{'h3_cell': 'VARCHAR', 'occurrences': 'BIGINT',
                                                'distinct_species': 'INTEGER'}}))
                UNION ALL
                (SELECT h3_cell, occurrences, distinct_species FROM read_csv('{csv_path}',
                     header = true, columns = {{'h3_cell': 'VARCHAR', 'occurrences': 'BIGINT',
                                                'distinct_species': 'INTEGER'}})
                 EXCEPT
                 SELECT h3_cell, occurrences, distinct_species FROM read_parquet('{parquet_path}')))"""
    ).fetchone()[0]
    return [
        (f"res {resolution} total occurrences = species_hex total", total == expected,
         f"{total:,} vs {expected:,}"),
        (f"res {resolution} one row per hexagon, none without a cell",
         rows == cells and no_cell == 0, f"{rows:,} rows, {cells:,} hexagons, {no_cell} without a cell"),
        (f"res {resolution} CSV rows = parquet rows", differing == 0,
         f"{differing:,} rows differ"),
    ]


def main():
    """Build every aggregate and its browser CSV under temporary names,
    check them, and replace the real files only if every check passes."""
    parser = argparse.ArgumentParser(
        description="Build the overview map's hexagon tables from species_hex.parquet "
        "(build_species_tables.py), counting species as distinct specieskey."
    )
    parser.add_argument("--species-hex", required=True,
                        help="species_hex.parquet from build_species_tables.py --key-merge.")
    parser.add_argument("--outdir", required=True,
                        help="Directory to write bc_hex_r{4..7}.parquet into.")
    parser.add_argument("--frontend-dir", required=True,
                        help="Directory to write the browser's gzipped CSVs into. On the "
                             "server, a folder outside the repository; the CSVs are copied "
                             "to frontend/public/data by hand.")
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--memory-limit", default="10GB")
    parser.add_argument("--temp-dir", default=None)
    args = parser.parse_args()

    species_hex_path = os.path.expanduser(args.species_hex)
    con = connect(args.threads, args.memory_limit, args.temp_dir)
    check_columns(con, species_hex_path)
    os.makedirs(args.outdir, exist_ok=True)
    os.makedirs(args.frontend_dir, exist_ok=True)

    parquet_paths = {r: os.path.join(args.outdir, f"bc_hex_r{r}.parquet") for r in RESOLUTIONS}
    csv_paths = {r: os.path.join(args.frontend_dir, f"bc_hex_r{r}.csv.gz") for r in RESOLUTIONS}
    outputs = list(parquet_paths.values()) + list(csv_paths.values())
    clear_temporary(outputs)

    print(f"species_hex:   {species_hex_path}")
    print(f"Parquet dir:   {args.outdir}")
    print(f"Front end dir: {args.frontend_dir}\n")

    t_all = time.time()
    results = []
    for r in RESOLUTIONS:
        parquet_tmp, csv_tmp = temporary_path(parquet_paths[r]), temporary_path(csv_paths[r])
        t = time.time()
        aggregate(con, species_hex_path, parquet_tmp, r)
        # The CSV is made from the parquet just written, so it cannot drift
        # from the parquet it mirrors.
        csv_size = write_frontend_csv(con, parquet_tmp, csv_tmp)
        cells, occ, mn, med, mx, single = summary_figures(con, parquet_tmp)
        print(
            f"res {r}: {cells:>7,} cells  {occ:>12,} occurrences  "
            f"min {mn} / median {med:.0f} / max {mx:,}  "
            f"{single:,} singletons ({100 * single / cells:.1f}%)  "
            f"parquet {os.path.getsize(parquet_tmp) / 1024:.0f} KB, CSV {csv_size:,} bytes  "
            f"{time.time() - t:.1f}s"
        )
        results += check_outputs(con, species_hex_path, parquet_tmp, csv_tmp, r)
    con.close()

    print("\nChecks:")
    for name, passed, detail in results:
        print(f"  {'PASS' if passed else 'FAIL'}  {name}: {detail}")

    if not all(passed for _, passed, _ in results):
        report_not_replaced(outputs)
        print(f"\nDone in {time.time() - t_all:.1f}s, with a FAILED check")
        return 1
    replace_outputs(outputs)
    print("\nAll checks passed; replaced:")
    for path in outputs:
        print(f"  {path}")
    total = sum(os.path.getsize(csv_paths[r]) for r in RESOLUTIONS)
    print(f"front end total: {total:,} bytes ({total / 1024:.0f} KB)")
    print(f"\nDone in {time.time() - t_all:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
