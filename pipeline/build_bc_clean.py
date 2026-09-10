"""
build_bc_clean.py

Layer 2 of the pipeline: turn the raw BC-bbox download (Layer 1) into a
clean "occurrence" product that the dashboard will read.

Input:  bc_raw.parquet   (Layer 1 output: BC bounding box, all columns,
                           NO filtering, ~55M rows)
Output: bc_clean.parquet  (inside BC's exact polygon, quality-filtered,
                           occurrence-level, ~38-39M rows expected)

What it does:
  1. Fetches BC's exact legal boundary (ABMS, marine-inclusive), the same
     way gbif_bc_filtered.py does.
  2. Clips the raw data from the bounding box down to the EXACT polygon
     using ST_Contains (the bbox was just a coarse pre-filter in Layer 1).
  3. Applies Evan's quality filters: keep PRESENT only, drop fossils and
     living specimens, require a species-level id and coordinates, and
     drop records flagged with any coordinate-quality issue.
  4. Writes the surviving records (all columns) to bc_clean.parquet.

This reads the LOCAL raw file, not S3, so it's fast and can be re-run
cheaply whenever the filter rules change (no re-download needed).

DuckDB note: written for the cluster's DuckDB 0.10.3. The boundary is
stored as WKT text and converted with ST_GeomFromText on read, because
0.10.3 reads a GEOMETRY column back from parquet as an unusable BLOB.

Run:
    python build_bc_clean.py --raw ~/bcbn/data/bc_raw.parquet --out ~/bcbn/data/bc_clean.parquet
"""

import argparse
import os
import tempfile

import duckdb
import requests


# Coordinate-quality issue flags from Evan's download_global_gbif.R.
# A record is dropped if GBIF tagged it with ANY of these.
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
# Warehouse via ArcGIS REST, reprojected to WGS84 to match GBIF.
ABMS_PROVINCE_BOUNDARY_URL = (
    "https://delivery.maps.gov.bc.ca/arcgis/rest/services/whse/"
    "bcgw_pub_whse_legal_admin_boundaries/MapServer/25/query"
    "?where=1%3D1&outFields=ADMIN_AREA_NAME&outSR=4326&f=geojson"
)


def get_bc_geometry():
    """Return BC's official legal boundary polygon (WGS84) as a shapely
    geometry. This is the marine-inclusive boundary, so coastal records
    are kept."""
    import geopandas as gpd

    print(f"Fetching BC's legal boundary from BC Geographic Warehouse "
          f"({ABMS_PROVINCE_BOUNDARY_URL.split('?')[0]})...")
    resp = requests.get(ABMS_PROVINCE_BOUNDARY_URL, timeout=60)
    resp.raise_for_status()
    geojson = resp.json()
    if not geojson.get("features"):
        raise RuntimeError(f"No features returned from ABMS boundary service: {geojson}")

    gdf = gpd.GeoDataFrame.from_features(geojson["features"], crs="EPSG:4326")
    geometry = gdf.union_all()

    n_parts = len(geometry.geoms) if geometry.geom_type == "MultiPolygon" else 1
    print(f"BC legal boundary: type={geometry.geom_type}, parts={n_parts}, "
          f"valid={geometry.is_valid}, area(deg^2)={geometry.area:.3f}")
    if not geometry.is_valid:
        from shapely.validation import make_valid
        print("Geometry was invalid - repairing with make_valid()...")
        geometry = make_valid(geometry)

    return geometry


def write_boundary_parquet(con, geometry, path):
    """Write the boundary as a WKT-text column to a small parquet file.

    Stored as text (not a GEOMETRY column) because DuckDB 0.10.3 reads a
    GEOMETRY written to parquet back as an unusable BLOB. On read we
    convert the text with ST_GeomFromText once, on the single boundary
    row, so the 2 MB WKT is parsed once, not per data row.
    """
    wkt = geometry.wkt
    print(f"Boundary WKT length: {len(wkt):,} characters")
    # Inline the WKT and path directly (0.10.3 doesn't infer placeholder
    # types for ST_GeomFromText). Escape single quotes in the WKT so the
    # string literal can't terminate early.
    wkt_escaped = wkt.replace("'", "''")
    con.execute(
        f"COPY (SELECT '{wkt_escaped}' AS geom_wkt) TO '{path}' (FORMAT PARQUET)"
    )


def build_clean(raw_path, boundary_parquet_path, out_path):
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial;")

    geo_issues_sql = "[" + ", ".join(f"'{issue}'" for issue in GEO_ISSUES) + "]"

    # Read the local raw file (occ) and the one-row boundary file (bc).
    # CROSS JOIN attaches the single boundary geometry to every row so we
    # can test ST_Contains. The boundary WKT is converted to a geometry
    # once, in the bc subquery, not per data row.
    #
    # Filters:
    #   - ST_Contains: keep only points inside BC's exact polygon.
    #   - occurrencestatus = PRESENT: drop ABSENT non-detections.
    #   - basisofrecord not fossil/living: drop fossils and captive.
    #   - species not null: require a species-level id.
    #   - coordinates not null.
    #   - no coordinate-quality issue.
    # SELECT occ.* keeps all 50 columns of the surviving records.
    query = f"""
        COPY (
            SELECT occ.*
            FROM read_parquet('{raw_path}') AS occ
            CROSS JOIN (
                SELECT ST_GeomFromText(geom_wkt) AS geom
                FROM read_parquet('{boundary_parquet_path}')
            ) AS bc
            WHERE ST_Contains(bc.geom, ST_Point(occ.decimallongitude, occ.decimallatitude))
              AND occ.occurrencestatus = 'PRESENT'
              AND occ.basisofrecord NOT IN ('FOSSIL_SPECIMEN', 'LIVING_SPECIMEN')
              AND occ.species IS NOT NULL
              AND occ.decimallatitude IS NOT NULL
              AND occ.decimallongitude IS NOT NULL
              AND NOT list_has_any(occ.issue, {geo_issues_sql})
        ) TO '{out_path}' (FORMAT PARQUET)
    """
    print("Building clean BC product (polygon + quality filters)...")
    con.execute(query)

    n = con.execute(f"SELECT count(*) FROM read_parquet('{out_path}')").fetchone()[0]
    return n


def main():
    parser = argparse.ArgumentParser(
        description="Build the clean BC occurrence product from the raw download."
    )
    parser.add_argument(
        "--raw",
        default="/home/songyanf/bcbn/data/bc_raw.parquet",
        help="Path to the Layer-1 raw parquet file.",
    )
    parser.add_argument(
        "--out",
        default="/home/songyanf/bcbn/data/bc_clean.parquet",
        help="Where to write the clean product.",
    )
    args = parser.parse_args()

    print(f"Raw input: {args.raw}")
    print(f"Output:    {args.out}")

    geometry = get_bc_geometry()

    with tempfile.TemporaryDirectory() as tmpdir:
        boundary_path = os.path.join(tmpdir, "bc_boundary.parquet")

        con = duckdb.connect()
        con.execute("INSTALL spatial; LOAD spatial;")
        write_boundary_parquet(con, geometry, boundary_path)
        con.close()

        n = build_clean(args.raw, boundary_path, args.out)

    print(f"\nDone. Clean records written: {n:,}")
    print(f"File: {args.out}")


if __name__ == "__main__":
    main()
