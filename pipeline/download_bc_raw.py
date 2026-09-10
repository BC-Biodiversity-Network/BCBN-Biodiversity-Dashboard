"""
download_bc_raw.py

Layer 1 of the pipeline: download the "raw" BC slice of GBIF to a local
parquet file, so later steps read from disk instead of streaming S3 every
time.

What this does (and deliberately does NOT do):
  - Clips to BC's BOUNDING BOX only (a fast lon/lat range check), NOT the
    exact polygon. The bbox is a bit larger than BC (it includes small
    corners of WA/AK/AB), on purpose: we want a wide "raw" copy so later
    steps can re-clip to the exact polygon without re-downloading.
  - Applies NO quality filters. We keep fossils, non-species records,
    flagged coordinates, everything. Quality filtering happens in Layer 2,
    so we can change the filter rules later without re-downloading.
  - Keeps ALL columns present in the GBIF cloud snapshot (50 of them),
    so we don't have to re-download if we later want a field we hadn't
    planned for. Storage is cheap during development; re-downloading is not.

The result is one parquet file: a wide, unfiltered BC-bbox copy of GBIF
that every later step reads locally.

Monthly refresh: GBIF publishes a new full snapshot each month. Pass the
snapshot date on the command line to download a new one, e.g.
    python download_bc_raw.py --snapshot 2026-09-01

DuckDB note: written to work on the cluster's DuckDB 0.10.3. No named
($name) parameters, no newer-only syntax.

Run:
    python download_bc_raw.py --snapshot 2026-08-01 --out /home/songyanf/bcbn/data/bc_raw.parquet
"""

import argparse

import duckdb


# The bounding box of BC, in WGS84 (min_lon, min_lat, max_lon, max_lat).
# Same numbers the polygon script prints as "BC bounding box". Hard-coded
# so this script needs no boundary fetch and no geopandas: a raw bbox
# download doesn't need the exact polygon.
BC_BBOX = (-139.06132777967898, 48.22484008483155,
           -114.05416224618163, 60.00478859404904)


def download_bc_raw(snapshot_date, out_path):
    min_lon, min_lat, max_lon, max_lat = BC_BBOX

    snapshot_path = (
        f"s3://gbif-open-data-us-east-1/occurrence/{snapshot_date}/occurrence.parquet/*"
    )
    print(f"Snapshot: {snapshot_path}")
    print(f"Output:   {out_path}")
    print(f"BC bbox:  {BC_BBOX}")

    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")   # read files over S3
    con.execute("SET s3_region='us-east-1';")
    con.execute("SET s3_access_key_id='';")       # public bucket, no key
    con.execute("SET s3_secret_access_key='';")

    # SELECT * keeps all 50 columns of the snapshot. The bbox BETWEEN lines
    # are the only filter: they let DuckDB skip most of the world using
    # parquet row-group statistics, so this downloads only BC's corner of
    # the archive, not the globe. No quality filters here, on purpose
    # (Layer 2 does those).
    query = f"""
        COPY (
            SELECT *
            FROM read_parquet('{snapshot_path}')
            WHERE decimallongitude BETWEEN {min_lon} AND {max_lon}
              AND decimallatitude  BETWEEN {min_lat} AND {max_lat}
        ) TO '{out_path}' (FORMAT PARQUET)
    """
    print("Downloading BC-bbox raw data to parquet (this streams from S3)...")
    con.execute(query)

    # Report how many rows landed, as a sanity check.
    n = con.execute(f"SELECT count(*) FROM read_parquet('{out_path}')").fetchone()[0]
    con.close()

    print(f"\nDone. Rows written: {n:,}")
    print(f"File: {out_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Download the BC-bbox raw slice of a GBIF snapshot to local parquet."
    )
    parser.add_argument(
        "--snapshot",
        default="2026-08-01",
        help="GBIF snapshot date, e.g. 2026-08-01. Update this for the monthly refresh.",
    )
    parser.add_argument(
        "--out",
        default="/home/songyanf/bcbn/data/bc_raw.parquet",
        help="Where to write the parquet file.",
    )
    args = parser.parse_args()

    download_bc_raw(args.snapshot, args.out)


if __name__ == "__main__":
    main()
