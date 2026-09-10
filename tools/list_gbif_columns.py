"""
list_gbif_columns.py

Print the real column names and types of a GBIF snapshot, straight from
the parquet schema on S3. Reads only the schema (DESCRIBE), not the data,
so it's fast and cheap: no full scan, nothing written to disk.

Use this to see exactly which columns exist in the snapshot before
deciding which ones to download.

Run:
    python list_gbif_columns.py --snapshot 2026-08-01
"""

import argparse

import duckdb


def list_columns(snapshot_date):
    snapshot_path = (
        f"s3://gbif-open-data-us-east-1/occurrence/{snapshot_date}/occurrence.parquet/*"
    )
    print(f"Snapshot: {snapshot_path}\n")

    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    con.execute("SET s3_region='us-east-1';")
    con.execute("SET s3_access_key_id='';")
    con.execute("SET s3_secret_access_key='';")

    # DESCRIBE reads only the schema, not the rows, so this is fast.
    rows = con.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{snapshot_path}')"
    ).fetchall()

    print(f"{len(rows)} columns:\n")
    for i, r in enumerate(rows, start=1):
        col_name = r[0]
        col_type = r[1]
        print(f"{i:3}. {col_name:35} {col_type}")


def main():
    parser = argparse.ArgumentParser(
        description="List the columns of a GBIF snapshot."
    )
    parser.add_argument(
        "--snapshot",
        default="2026-08-01",
        help="GBIF snapshot date, e.g. 2026-08-01.",
    )
    args = parser.parse_args()
    list_columns(args.snapshot)


if __name__ == "__main__":
    main()
