"""
build_hex_aggregates.py

Bin the clean BC occurrence product into H3 hexagons so the dashboard map can
draw a density surface. 38.7M points cannot go to the browser; the browser gets
one small table per zoom tier instead.

Input:  bc_clean.parquet   (Layer 2 output: inside BC's polygon,
                            quality-filtered, occurrence-level)
Output goes to two places, in two formats:

  --outdir (backend/data/), parquet, not committed:
        bc_hex_r{3..7}.parquet   (one row per non-empty hexagon)
        bc_hex_r{5,6}_<species>.parquet  (the same aggregation for one species,
                                          as a filter-then-aggregate test case)

  --frontend-dir (frontend/public/data/), gzipped CSV, committed:
        bc_hex_r{4,5,6}.csv.gz   (the three tiers the map serves)

Two formats because a browser cannot open a parquet file without a WebAssembly
parser. The three tiers the map needs come to about 141 KB as gzipped CSV, so
loading a parser would cost more than it saves. The parquet files stay for
analysis work, where the types and the compression are worth having.

Schema of every output, so the browser can use one loader for all of them:
    h3_cell           VARCHAR  15-char H3 index string (what deck.gl's
                               H3HexagonLayer takes as `hexagon`)
    occurrences       BIGINT   records in that cell
    distinct_species  INTEGER  distinct `species` values in that cell

Each resolution is binned directly from decimallatitude/decimallongitude.
Do NOT derive a coarse resolution by calling h3_cell_to_parent on a finer one:
H3's hexagons only nest approximately, and measured on this data 5.96% of
records land in a different res-5 cell under a res-7 rollup than under direct
binning. Coarse tiers must be their own pass over the raw coordinates.

Binning is done by duckdb's community h3 extension rather than a Python loop.
Measured on a 5M-row slice: duckdb 3.9s end-to-end vs 5.2s for pandas + a
python-h3 loop, and duckdb streams instead of holding the frame in memory.

Run:
    python build_hex_aggregates.py --clean ~/bcbn/data/bc_clean.parquet --outdir ~/bcbn/data
"""

import argparse
import csv
import gzip
import io
import os
import time
from pathlib import Path

import duckdb

# The tiers the dashboard actually serves, plus 3 and 7 which are written
# because they cost almost nothing and are useful to experiment with.
RESOLUTIONS = [3, 4, 5, 6, 7]

# Aggregated separately as a filter-then-aggregate test case. American robin:
# 914,491 records spread over more BC hexagons than any other common species,
# so it exercises the taxon filter across the whole province rather than one
# coastal cluster.
TEST_SPECIES = "Turdus migratorius"
TEST_RESOLUTIONS = [5, 6]

# The resolutions copied to the front end as gzipped CSV. Res 4 is the province
# view, 5 the regional view, 6 the local view. Res 3 is too coarse to be worth
# drawing and res 7 has one record in a fifth of its cells, so neither is
# shipped. To ship res 7 later, add it to this list; nothing else needs to
# change.
FRONTEND_RESOLUTIONS = [4, 5, 6]


def connect(threads, memory_limit, temp_dir):
    """Open a duckdb connection with the h3 extension loaded."""
    con = duckdb.connect(config={"threads": threads, "memory_limit": memory_limit})
    if temp_dir:
        con.execute(f"SET temp_directory='{temp_dir}'")
    con.execute("INSTALL h3 FROM community")
    con.execute("LOAD h3")
    return con


def aggregate(con, clean_path, out_path, resolution, species=None):
    """Bin one resolution straight from the coordinates and write a parquet."""
    where = ""
    params = []
    if species is not None:
        where = "WHERE species = ?"
        params = [species]

    con.execute(
        f"""
        COPY (
            SELECT
              h3_h3_to_string(
                h3_latlng_to_cell(decimallatitude, decimallongitude, {resolution})
              ) AS h3_cell,
              count(*)::BIGINT           AS occurrences,
              count(DISTINCT species)::INTEGER AS distinct_species
            FROM read_parquet('{clean_path}')
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
        description="Bin the clean BC occurrence product into H3 hexagons."
    )
    parser.add_argument(
        "--clean",
        default="/home/songyanf/bcbn/data/bc_clean.parquet",
        help="Path to the Layer-2 clean parquet file.",
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
    os.makedirs(args.outdir, exist_ok=True)
    os.makedirs(args.frontend_dir, exist_ok=True)

    print(f"Clean input:   {args.clean}")
    print(f"Parquet dir:   {args.outdir}")
    print(f"Front end dir: {args.frontend_dir}\n")

    t_all = time.time()
    for r in RESOLUTIONS:
        out = os.path.join(args.outdir, f"bc_hex_r{r}.parquet")
        t = time.time()
        cells, occ, mn, med, mx, single = aggregate(con, args.clean, out, r)
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
            con, args.clean, out, r, species=TEST_SPECIES
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
