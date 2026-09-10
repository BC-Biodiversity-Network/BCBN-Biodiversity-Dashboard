"""
gbif_bc_polygon.py

Count the GBIF occurrence records that fall inside British Columbia,
using BC's exact shape (not just its bounding box) plus a set of
record-quality filters.

How the spatial filter works (two steps, per Evan's tip):
  1. Coarse pass: bounding box. A cheap numeric comparison that throws
     out most of the world quickly.
  2. Precise pass: ST_Contains against BC's real polygon. Slower, so it
     only runs on the records the bounding box already kept.

Boundary source:
  We use BC's official legal boundary from the Administrative Boundaries
  Management System (ABMS), served by the BC Geographic Warehouse. Unlike
  a land-only administrative boundary, this one extends across BC's
  coastal and inter-island waters (Strait of Georgia, Hecate Strait,
  etc.), so it keeps the province's large amount of marine occurrence
  data (fish, cetaceans, seabirds). We fetch it live from BC's public
  ArcGIS REST endpoint as GeoJSON, already in WGS84 (EPSG:4326) to match
  GBIF's coordinates.

  The geometry is written once to a small local parquet file, as WKT text,
  and read back with read_parquet() and ST_GeomFromText(). It is stored as
  text rather than as a DuckDB GEOMETRY column because DuckDB 0.10.3 (the
  cluster's pinned version) reads a GEOMETRY written to parquet back as an
  unusable BLOB - see write_boundary_parquet() for the details.

Filters applied (a record is kept only if it passes all of these):
  1. Bounding box: coordinates fall inside BC's bounding box (coarse pass).
  2. BC polygon: coordinates fall inside BC's legal boundary (ST_Contains).
  3. occurrencestatus = 'PRESENT': keep real observations, drop ABSENT
     survey non-detections.
  4. basisofrecord not in (FOSSIL_SPECIMEN, LIVING_SPECIMEN): drop fossils
     and captive/cultivated specimens.
  5. species is not null: require a species-level identification.
  6. decimallatitude / decimallongitude not null: require coordinates.
  7. No coordinate-quality issue: drop records GBIF flagged with any of
     the GEO_ISSUES flags (see the list below).
  No deduplication, matching Evan's pipeline.

This script only does a COUNT, to confirm the pipeline works and see the
number before pulling any real data.

Run (needs internet):
    pip install duckdb geopandas shapely requests
    python gbif_bc_polygon.py
"""

import os
import tempfile

import duckdb
import requests


# Coordinate-quality issue flags from Evan's download_global_gbif.R.
# A record is dropped if GBIF tagged it with ANY of these; they all mean
# the coordinates themselves are suspect (reprojection failures, swapped
# or negated lat/lon, coordinates that disagree with the stated country
# or continent). GBIF's `issue` column is a list, so a record with no
# issues has an empty list and is kept.
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


# BC's official legal boundary (ABMS), served from the BC Geographic
# Warehouse via ArcGIS REST. outSR=4326 asks the server to return it in
# WGS84, matching GBIF's decimallongitude/decimallatitude.
ABMS_PROVINCE_BOUNDARY_URL = (
    "https://delivery.maps.gov.bc.ca/arcgis/rest/services/whse/"
    "bcgw_pub_whse_legal_admin_boundaries/MapServer/25/query"
    "?where=1%3D1&outFields=ADMIN_AREA_NAME&outSR=4326&f=geojson"
)


# 1. Get BC's exact legal boundary (includes coastal/marine waters)
def get_bc_geometry():
    """Return (bbox, geometry) for British Columbia in WGS84.

    bbox     = (min_lon, min_lat, max_lon, max_lat), for the coarse pass.
    geometry = BC's official legal boundary polygon, for the precise pass.
    """
    import geopandas as gpd

    print(f"Fetching BC's legal boundary from BC Geographic Warehouse "
          f"({ABMS_PROVINCE_BOUNDARY_URL.split('?')[0]})...")
    resp = requests.get(ABMS_PROVINCE_BOUNDARY_URL, timeout=60)
    resp.raise_for_status()
    geojson = resp.json()
    if not geojson.get("features"):
        raise RuntimeError(f"No features returned from ABMS boundary service: {geojson}")

    gdf = gpd.GeoDataFrame.from_features(geojson["features"], crs="EPSG:4326")
    # union_all() is geopandas >= 1.0. An old cluster image may only have the
    # older unary_union property, so fall back to it rather than crashing.
    geometry = gdf.union_all() if hasattr(gdf, "union_all") else gdf.unary_union

    # Print the geometry's basic facts so we can eyeball that it's sane
    # before spending time on the scan.
    n_parts = len(geometry.geoms) if geometry.geom_type == "MultiPolygon" else 1
    print(f"BC legal boundary: type={geometry.geom_type}, parts={n_parts}, "
          f"valid={geometry.is_valid}, area(deg^2)={geometry.area:.3f}")
    if not geometry.is_valid:
        from shapely.validation import make_valid
        print("Geometry was invalid - repairing with make_valid()...")
        geometry = make_valid(geometry)

    min_lon, min_lat, max_lon, max_lat = geometry.bounds
    bbox = (min_lon, min_lat, max_lon, max_lat)

    return bbox, geometry


# 2. Write the boundary geometry to a small local parquet file
def write_boundary_parquet(con: duckdb.DuckDBPyConnection, geometry, path: str):
    """Write a single-row parquet file with one VARCHAR column, `geom_wkt`.

    Two separate DuckDB 0.10.3 constraints shape this function:

    1. Parameters are inlined, not bound. 0.10.3's parser rejects named
       parameters ($wkt, $path) outright, and positional '?' does not help
       here either: 0.10.3 will not infer a placeholder's type for a spatial
       function, failing with "ST_GeomFromText requires a string argument".
       So the WKT and the path go straight into the SQL text. This runs once
       per script, so a long literal costs nothing that matters.

    2. The boundary is stored as WKT TEXT, not as a DuckDB GEOMETRY column.
       On 0.10.3, COPYing a GEOMETRY to parquet writes DuckDB's internal
       serialization and reads it back as a plain BLOB, which ST_Contains
       refuses to bind against. Routing that BLOB through ST_GeomFromWKB is
       worse than useless: 0.10.3 accepts it and silently returns WRONG
       answers (a point known to be inside the polygon tested False), because
       the blob is not standard WKB. Storing WKT and calling ST_GeomFromText
       on read is verified correct on both 0.10.3 and current DuckDB.
    """
    wkt = geometry.wkt
    print(f"Boundary WKT length: {len(wkt):,} characters")
    # Shapely does not emit single quotes in WKT, but escape defensively so
    # the inlined literal cannot terminate the string early.
    wkt_sql = wkt.replace("'", "''")
    con.execute(
        f"COPY (SELECT '{wkt_sql}' AS geom_wkt) TO '{path}' (FORMAT PARQUET)"
    )


# 3. Count records inside the exact BC boundary
def count_bc_records(bbox, boundary_parquet_path, snapshot_path):
    min_lon, min_lat, max_lon, max_lat = bbox

    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")      # read files over S3
    con.execute("INSTALL spatial; LOAD spatial;")    # geometry functions (ST_Contains)
    con.execute("SET s3_region='us-east-1';")
    con.execute("SET s3_access_key_id='';")
    con.execute("SET s3_secret_access_key='';")

    # SQL array literal for the issue screen, e.g. ['A', 'B', ...].
    geo_issues_sql = "[" + ", ".join(f"'{issue}'" for issue in GEO_ISSUES) + "]"

    # Spatial filter (bbox coarse pass + ST_Contains precise pass) plus
    # Evan's record-quality filters: keep only PRESENT records, drop
    # fossils and captive/cultivated specimens, require a species-level ID
    # and coordinates, and drop anything flagged with a coordinate-quality
    # issue. No deduplication, matching Evan's pipeline.
    query = f"""
        SELECT count(*) AS n
        FROM read_parquet('{snapshot_path}') AS occ
        CROSS JOIN (
            SELECT ST_GeomFromText(geom_wkt) AS geom
            FROM read_parquet('{boundary_parquet_path}')
        ) AS bc
        WHERE occ.decimallongitude BETWEEN {min_lon} AND {max_lon}
          AND occ.decimallatitude  BETWEEN {min_lat} AND {max_lat}
          AND ST_Contains(bc.geom, ST_Point(occ.decimallongitude, occ.decimallatitude))
          AND occ.occurrencestatus = 'PRESENT'
          AND occ.basisofrecord NOT IN ('FOSSIL_SPECIMEN', 'LIVING_SPECIMEN')
          AND occ.species IS NOT NULL
          AND occ.decimallatitude IS NOT NULL
          AND occ.decimallongitude IS NOT NULL
          AND NOT list_has_any(occ.issue, {geo_issues_sql})
    """
    print("Running polygon count query (this is slower than the bbox one)...")
    n = con.execute(query).fetchone()[0]
    return n


def main():
    bbox, geometry = get_bc_geometry()
    print(f"BC bounding box: {bbox}")

    snapshot_date = "2026-08-01"   # update to the latest snapshot if needed
    snapshot_path = (
        f"s3://gbif-open-data-us-east-1/occurrence/{snapshot_date}/occurrence.parquet/*"
    )
    print(f"Using snapshot: {snapshot_path}")

    with tempfile.TemporaryDirectory() as tmpdir:
        boundary_path = os.path.join(tmpdir, "bc_boundary.parquet")

        con = duckdb.connect()
        con.execute("INSTALL spatial; LOAD spatial;")
        write_boundary_parquet(con, geometry, boundary_path)
        con.close()

        n = count_bc_records(bbox, boundary_path, snapshot_path)

    print(f"\nBC records inside the BC legal-boundary polygon: {n:,}")


if __name__ == "__main__":
    main()
