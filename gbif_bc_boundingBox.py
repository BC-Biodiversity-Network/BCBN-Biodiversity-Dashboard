"""
gbif_bc_filter.py

Goal (Evan's task): geographically filter the entire GBIF cloud archive
down to just British Columbia, using Python, and see if the download
works / is a reasonable size.

Approach:
  1. Get the bounding box of BC in WGS84 (min/max lon/lat).
  2. Point DuckDB at the GBIF cloud parquet archive on AWS S3.
  3. Filter decimallongitude / decimallatitude to the BC box.
  4. Start with a COUNT to check it's connectable and the size is reasonable,
     BEFORE pulling any actual data.

Run on your own machine (needs internet):
    pip install duckdb pygadm geopandas
    python gbif_bc_filter.py
"""

import duckdb


# --- 1. Get BC's bounding box in WGS84 ---
def get_bc_bbox():
    """Return (min_lon, min_lat, max_lon, max_lat) for British Columbia in WGS84.

    Tries pygadm first (level 1 = provinces). If that fails (e.g. GADM server
    is down), falls back to a hard-coded BC bounding box so you can still work.
    """
    try:
        import pygadm
        # Level 1 = provinces/territories. Get British Columbia by name.
        bc = pygadm.Items(name="British Columbia", content_level=1)
        # geopandas .total_bounds gives (minx, miny, maxx, maxy) = (min_lon, min_lat, max_lon, max_lat)
        min_lon, min_lat, max_lon, max_lat = bc.total_bounds
        print("Got BC bounding box from pygadm.")
        return (min_lon, min_lat, max_lon, max_lat)
    except Exception as e:
        print(f"pygadm failed ({e}); using fallback BC bounding box.")
        # Fallback: approximate BC bounding box in WGS84 (lon/lat).
        # BC spans roughly: lon -139.06 to -114.03, lat 48.30 to 60.00
        return (-139.06, 48.30, -114.03, 60.00)


# --- 2 & 3. Query the GBIF cloud archive, filtered to the BC box ---
def count_bc_records(bbox, snapshot_path):
    """Connect DuckDB to the GBIF S3 archive and COUNT records inside the BC box.

    We only COUNT first, so we don't pull gigabytes before we know it works.
    """
    min_lon, min_lat, max_lon, max_lat = bbox

    con = duckdb.connect()
    # httpfs lets DuckDB read files over http/S3
    con.execute("INSTALL httpfs; LOAD httpfs;")
    # GBIF open data is public: use anonymous access, no credentials needed
    con.execute("SET s3_region='us-east-1';")
    con.execute("SET s3_access_key_id='';")
    con.execute("SET s3_secret_access_key='';")

    query = f"""
        SELECT count(*) AS n
        FROM read_parquet('{snapshot_path}')
        WHERE decimallongitude BETWEEN {min_lon} AND {max_lon}
          AND decimallatitude  BETWEEN {min_lat} AND {max_lat}
    """
    print("Running count query against GBIF cloud archive...")
    n = con.execute(query).fetchone()[0]
    return n


def main():
    bbox = get_bc_bbox()
    print(f"BC bounding box (min_lon, min_lat, max_lon, max_lat): {bbox}")

    # GBIF monthly snapshots live at:
    #   s3://gbif-open-data-<region>/occurrence/<YYYY-MM-DD>/occurrence.parquet/*
    # Use us-east-1 and a recent snapshot date. UPDATE the date to the latest
    # available snapshot (they're released roughly monthly, dated the 1st).
    snapshot_date = "2026-08-01"   # <-- update to the latest snapshot if needed
    snapshot_path = (
        f"s3://gbif-open-data-us-east-1/occurrence/{snapshot_date}/occurrence.parquet/*"
    )
    print(f"Using snapshot: {snapshot_path}")

    n = count_bc_records(bbox, snapshot_path)
    print(f"\nBC records in this snapshot (inside bounding box): {n:,}")
    print("If this number is reasonable, the next step is to actually pull the")
    print("columns we need into a local parquet/duckdb, instead of just counting.")


if __name__ == "__main__":
    main()
