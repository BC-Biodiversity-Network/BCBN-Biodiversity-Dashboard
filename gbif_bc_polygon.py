"""
gbif_bc_polygon_filtered.py

Filter the GBIF cloud archive to British Columbia, with Evan's quality filters.

Two-step spatial filter:
  1. Bounding box (fast coarse pass)
  2. ST_Contains with BC's exact marine-inclusive polygon (precise pass)

Plus Evan's quality filters (from download_global_gbif.R):
  - occurrenceStatus = PRESENT
  - basisOfRecord not fossil/living specimen
  - species not null
  - coordinates not null
  - drop records with any of 12 geo-coordinate issues

No deduplication (Evan's pipeline doesn't dedupe, so neither do we).

Run:
    pip install duckdb pygadm geopandas
    python gbif_bc_polygon_filtered.py
"""

import duckdb


# The 12 geo-coordinate issues to exclude (from Evan's download_global_gbif.R)
GEO_ISSUES = [
    "COORDINATE_REPROJECTION_FAILED",
    "COORDINATE_REPROJECTION_SUSPICIOUS",
    "COORDINATE_UNCERTAINTY_METERS_INVALID",
    "PRESUMED_NEGATED_LATITUDE",
    "PRESUMED_NEGATED_LONGITUDE",
    "PRESUMED_SWAPPED_COORDINATE",
    "FOOTPRINT_WKT_MISMATCH",
    "FOOTPRINT_WKT_INVALID",
    "COUNTRY_COORDINATE_MISMATCH",
    "COORDINATE_PRECISION_INVALID",
    "CONTINENT_COUNTRY_MISMATCH",
    "CONTINENT_COORDINATE_MISMATCH",
]


# --- 1. Get BC's exact polygon AND its bounding box, in WGS84 ---
def get_bc_geometry():
    """Return (bbox, polygon_wkt) for BC in WGS84.

    bbox        = (min_lon, min_lat, max_lon, max_lat)  -> coarse pass
    polygon_wkt = BC's exact shape as WKT                -> precise pass
    """
    import pygadm
    bc = pygadm.Items(name="British Columbia", content_level=1)

    # GADM data loads without a CRS label but is already WGS84 lon/lat.
    if bc.crs is None:
        bc = bc.set_crs("EPSG:4326")
    else:
        bc = bc.to_crs("EPSG:4326")

    min_lon, min_lat, max_lon, max_lat = bc.total_bounds
    bbox = (min_lon, min_lat, max_lon, max_lat)
    polygon_wkt = bc.union_all().wkt

    print("Got BC polygon and bounding box from pygadm.")
    return bbox, polygon_wkt


# --- 2. Count records inside BC, with quality filters ---
def count_bc_records(bbox, polygon_wkt, snapshot_path):
    min_lon, min_lat, max_lon, max_lat = bbox

    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    con.execute("INSTALL spatial; LOAD spatial;")
    con.execute("SET s3_region='us-east-1';")
    con.execute("SET s3_access_key_id='';")
    con.execute("SET s3_secret_access_key='';")

    # Build the geo-issues list as a SQL array literal, e.g. ['A','B',...]
    issues_sql = "[" + ", ".join(f"'{i}'" for i in GEO_ISSUES) + "]"

    query = f"""
        SELECT count(*) AS n
        FROM read_parquet('{snapshot_path}')
        WHERE
              -- quality filters (Evan's)
              occurrencestatus = 'PRESENT'
          AND basisofrecord NOT IN ('FOSSIL_SPECIMEN', 'LIVING_SPECIMEN')
          AND species IS NOT NULL
          AND decimallatitude  IS NOT NULL
          AND decimallongitude IS NOT NULL
          AND NOT list_has_any(issue, {issues_sql})

          -- geographic: bounding box coarse pass
          AND decimallongitude BETWEEN {min_lon} AND {max_lon}
          AND decimallatitude  BETWEEN {min_lat} AND {max_lat}

          -- geographic: exact BC polygon precise pass
          AND ST_Contains(
                ST_GeomFromText('{polygon_wkt}'),
                ST_Point(decimallongitude, decimallatitude)
              )
    """
    print("Running count query with quality filters (this is slower)...")
    n = con.execute(query).fetchone()[0]
    return n


def main():
    bbox, polygon_wkt = get_bc_geometry()
    print(f"BC bounding box: {bbox}")
    print(f"BC polygon WKT length: {len(polygon_wkt)} characters")

    snapshot_date = "2026-08-01"   # update to the latest snapshot if needed
    snapshot_path = (
        f"s3://gbif-open-data-us-east-1/occurrence/{snapshot_date}/occurrence.parquet/*"
    )
    print(f"Using snapshot: {snapshot_path}")

    n = count_bc_records(bbox, polygon_wkt, snapshot_path)
    print(f"\nBC records (exact polygon + quality filters): {n:,}")
    print("Compare to the previous polygon-only count of 40.75M.")


if __name__ == "__main__":
    main()
