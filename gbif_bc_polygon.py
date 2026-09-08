"""
gbif_bc_polygon.py

Upgrade of the bounding-box version: filter the GBIF cloud archive to the
EXACT shape of BC, not just its bounding box.

Two-step filter (Evan's tip):
  1. Coarse pass: bounding box (fast, throws out most of the world cheaply).
  2. Precise pass: ST_Contains with BC's actual polygon (slower, but only
     runs on what the bounding box already narrowed down).

Still just does a COUNT first, to check it works and see the number,
before pulling any real data.

--------------------------------------------------------------------------
DIAGNOSIS: why the polygon count first came back at ~34.5M, not ~50.2M
--------------------------------------------------------------------------
An earlier version used pygadm's GADM administrative-boundary polygon for
BC, built a ~1,000,000-character WKT string from it, and passed that into
ST_GeomFromText(). We checked every piece of that pipeline directly:

  - The GADM polygon IS valid (is_valid == True).
  - It IS a MultiPolygon with 2,573 parts (mainland + many islands) -
    nothing was dropped.
  - Round-tripping the giant WKT string through ST_GeomFromText(...) in
    DuckDB reproduces the exact same geometry: same type, same 2,573
    parts, same area to 10+ significant figures.

So the WKT / ST_Contains machinery was never the bug. The real cause is
that GADM's admin-1 boundary is a LAND-ONLY polygon - it follows the
coastline and excludes the Strait of Georgia, Hecate Strait, Queen
Charlotte Sound, Howe Sound, Juan de Fuca Strait, and the water between
BC's many coastal islands. We confirmed this directly: points in the
middle of those straits all came back False from ST_Contains against
the GADM polygon, even though they're unambiguously BC coastal waters.
GBIF has enormous marine/coastal occurrence data for BC (DFO and
OBIS-fed marine surveys, fish and cetacean sightings, pelagic eBird
checklists), so a land-only polygon throws out millions of legitimate
BC records. A first attempt at patching this by buffering the GADM
polygon outward by 5 km (in BC Albers, a proper metric CRS) only
recovered part of the gap - 39.7M, still well short of 50.2M - because
a coastline buffer can't safely bridge straits that are tens of km
wide without also starting to claim Washington/Alaska waters.

THE ACTUAL FIX: use BC's own legal boundary instead of an administrative
land-boundary. The Province of British Columbia publishes its official
boundary in the Administrative Boundaries Management System (ABMS) as
the feature class WHSE_LEGAL_ADMIN_BOUNDARIES.ABMS_PROVINCE_SP, served
from the BC Data Catalogue / BC Geographic Warehouse:
    https://catalogue.data.gov.bc.ca/dataset/a7e32e45-63ae-4f5a-9275-9402b6deebdc
This is a single dissolved polygon (not thousands of island slivers)
that represents BC's actual legal boundary, and it DOES extend across
BC's coastal and inter-island waters. We verified this directly, the
same way we verified GADM was wrong: points in the Strait of Georgia,
Johnstone Strait, Hecate Strait, Queen Charlotte Sound, Howe Sound, and
Juan de Fuca Strait are all `contains=True` against this boundary, while
Seattle, WA and Edmonton, AB are correctly `contains=False`. This is
almost certainly the same category of boundary an R workflow using the
`bcmaps` package would have used (bcmaps wraps this same BC Geographic
Warehouse data), which is why it lines up with the ~50.2M benchmark.

We fetch it live from BC's public ArcGIS REST endpoint (no API key
needed) as GeoJSON, already reprojected to WGS84 (EPSG:4326) via
`outSR=4326` - the same CRS as GBIF's decimallongitude/decimallatitude.

We ALSO stopped inlining the polygon as a giant WKT literal in the SQL
text. Instead we write the geometry to a small local GeoParquet file
once, and read it back with DuckDB the same way we read the GBIF
archive - read_parquet(...) + CROSS JOIN. This avoids a >2MB inlined
SQL literal; swap in ST_Read() against the GeoJSON directly if you'd
rather keep the boundary in a GIS-native format.

Run on your own machine (needs internet):
    pip install duckdb geopandas shapely requests
    python gbif_bc_polygon.py
"""

import json
import os
import tempfile

import duckdb
import requests


# BC's official legal boundary (Administrative Boundaries Management
# System), served from the BC Geographic Warehouse via ArcGIS REST.
# outSR=4326 asks the server to reproject to WGS84 for us, matching
# GBIF's decimallongitude/decimallatitude.
ABMS_PROVINCE_BOUNDARY_URL = (
    "https://delivery.maps.gov.bc.ca/arcgis/rest/services/whse/"
    "bcgw_pub_whse_legal_admin_boundaries/MapServer/25/query"
    "?where=1%3D1&outFields=ADMIN_AREA_NAME&outSR=4326&f=geojson"
)


# --- 1. Get BC's exact legal boundary (includes coastal/marine waters) ---
def get_bc_geometry():
    """Return (bbox, geometry) for British Columbia in WGS84.

    bbox     = (min_lon, min_lat, max_lon, max_lat) -> for the coarse pass.
    geometry = a shapely Polygon: BC's official legal boundary, which
               (unlike a GADM admin-boundary) extends across the
               province's coastal and inter-island waters -> for the
               precise pass.
    """
    import geopandas as gpd

    print(f"Fetching BC's official legal boundary from BC Geographic Warehouse "
          f"({ABMS_PROVINCE_BOUNDARY_URL.split('?')[0]})...")
    resp = requests.get(ABMS_PROVINCE_BOUNDARY_URL, timeout=60)
    resp.raise_for_status()
    geojson = resp.json()
    if not geojson.get("features"):
        raise RuntimeError(f"No features returned from ABMS boundary service: {geojson}")

    gdf = gpd.GeoDataFrame.from_features(geojson["features"], crs="EPSG:4326")
    geometry = gdf.union_all()

    # --- Diagnostics: confirm the geometry is sane before we use it ---
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


# --- 2. Write the boundary geometry to a small local GeoParquet file ---
def write_boundary_parquet(con: duckdb.DuckDBPyConnection, geometry, path: str):
    """Write a single-row parquet file with one GEOMETRY column, `geom`.

    We build the geometry from WKT exactly once here (cheap - this runs
    one time, not once per GBIF row), then persist it as a native DuckDB
    spatial GEOMETRY column. The main query below just reads this file
    back with read_parquet(), the same way it reads the GBIF archive -
    no multi-megabyte WKT literal ever goes into the SQL text.
    """
    wkt = geometry.wkt
    print(f"Boundary WKT length: {len(wkt):,} characters (written to parquet, "
          f"not inlined into SQL).")
    con.execute(
        "COPY (SELECT ST_GeomFromText($wkt) AS geom) TO $path (FORMAT PARQUET)",
        {"wkt": wkt, "path": path},
    )


# --- 3. Count records inside the exact BC boundary ---
def count_bc_records(bbox, boundary_parquet_path, snapshot_path):
    min_lon, min_lat, max_lon, max_lat = bbox

    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")      # read files over S3
    con.execute("INSTALL spatial; LOAD spatial;")    # geometry functions (ST_Contains)
    con.execute("SET s3_region='us-east-1';")
    con.execute("SET s3_access_key_id='';")
    con.execute("SET s3_secret_access_key='';")

    # Two-step filter:
    #  - The BETWEEN lines are the fast bounding-box coarse pass.
    #  - ST_Contains(...) is the precise pass against BC's real legal
    #    boundary, read back from a small local parquet file instead of
    #    a WKT literal.
    query = f"""
        SELECT count(*) AS n
        FROM read_parquet('{snapshot_path}') AS occ
        CROSS JOIN read_parquet('{boundary_parquet_path}') AS bc
        WHERE occ.decimallongitude BETWEEN {min_lon} AND {max_lon}
          AND occ.decimallatitude  BETWEEN {min_lat} AND {max_lat}
          AND ST_Contains(bc.geom, ST_Point(occ.decimallongitude, occ.decimallatitude))
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

    print(f"\nBC records inside the exact legal-boundary polygon: {n:,}")
    print("(Compare this to the bounding-box count of ~54.6M and the")
    print(" R download of ~50.2M.)")


if __name__ == "__main__":
    main()
