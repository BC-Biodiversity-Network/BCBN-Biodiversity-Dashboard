"""
gbif_bc_drops.py

Filter-attribution report for the BC occurrence pipeline.

This is the diagnostic companion to gbif_bc_filtered.py. That script answers
"how many records survive?"; this one answers "where do the records go?" -
how much each individual quality filter is responsible for dropping.

It reuses the same BC boundary source (the ABMS legal boundary, fetched live
and written to a small local parquet file) and the same GEO_ISSUES list, so
the numbers here line up with the count that script produces.


IMPORTANT - THE PER-FILTER DROPS OVERLAP. DO NOT SUM THEM.
===========================================================
Each drop_* number below counts every record in BC that the given filter
would reject, considered on its own and ignoring the other filters. A single
record can be rejected by several filters at once - a fossil specimen with no
species-level ID and a swapped-coordinate flag is counted in drop_basis AND
drop_no_species AND drop_issues.

So adding the drop_* numbers together will OVERCOUNT the losses, usually by a
lot. The only correct figure for the total dropped is:

    total_dropped = total_in_bc - kept

The drop_* numbers are for ranking which filters matter and for spotting a
filter that is doing something unexpected. They are not a partition.


How the query works
-------------------
The outer WHERE narrows to the base set: everything inside BC, using the same
two-step spatial filter as gbif_bc_filtered.py (bounding-box coarse pass, then
ST_Contains against BC's polygon). No quality filters are applied there - that
set is the denominator, total_in_bc.

Inside that set, every filter is measured in ONE pass over the data using
count(*) FILTER (WHERE ...), rather than one scan per filter.

Expect this to run SLOWER than gbif_bc_filtered.py, despite being a single
scan. In that script the cheap quality filters sit in the same WHERE clause as
ST_Contains, so the planner can apply them first and only test the survivors
against the polygon. Here the base set must be defined before any quality
filter is applied - that is what makes it a valid denominator - so ST_Contains
has to be evaluated on every record in the bounding box instead. Against a
boundary polygon of roughly 100k vertices that point-in-polygon test, not the
S3 read, is the bottleneck. This is inherent to the attribution question, not
a defect in the query.

Two notes on reading the output:

  * drop_no_lat and drop_no_lon will be 0. This is expected, not a bug. The
    bounding-box BETWEEN comparisons in the outer WHERE are what defines "in
    BC", and a NULL coordinate fails BETWEEN, so records missing coordinates
    are already outside the base set. The null-coordinate filter is genuinely
    doing no additional work once the spatial filter has run.

  * drop_basis and drop_issues use positive tests (IN (...) and list_has_any),
    while the kept-set uses the negated forms (NOT IN, NOT list_has_any). For
    a record with a NULL basisofrecord or a NULL issue list, the negated form
    is NULL and the record is dropped from kept, but the positive form is also
    not true, so no drop_* column claims it. Such records, if any exist, show
    up in (total_in_bc - kept) without being attributed to a named filter.

Run (needs internet):
    pip install duckdb geopandas shapely requests
    python -u gbif_bc_drops.py
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
    geometry = gdf.union_all()

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
    """Write a single-row parquet file with one GEOMETRY column, `geom`.

    We build the geometry from WKT once here, then save it as a native
    DuckDB GEOMETRY column. The main query reads this file back with
    read_parquet(), so no large WKT string goes into the SQL text.
    """
    wkt = geometry.wkt
    print(f"Boundary WKT length: {len(wkt):,} characters")
    con.execute(
        "COPY (SELECT ST_GeomFromText($wkt) AS geom) TO $path (FORMAT PARQUET)",
        {"wkt": wkt, "path": path},
    )


# 3. Attribute the drops, one filter at a time, in a single scan
def attribute_drops(bbox, boundary_parquet_path, snapshot_path):
    """Return a dict of counts for the BC base set and each filter's drop."""
    min_lon, min_lat, max_lon, max_lat = bbox

    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")      # read files over S3
    con.execute("INSTALL spatial; LOAD spatial;")    # geometry functions (ST_Contains)
    con.execute("SET s3_region='us-east-1';")
    con.execute("SET s3_access_key_id='';")
    con.execute("SET s3_secret_access_key='';")

    # SQL array literal for the issue screen, e.g. ['A', 'B', ...].
    geo_issues_sql = "[" + ", ".join(f"'{issue}'" for issue in GEO_ISSUES) + "]"

    # The outer WHERE is spatial only - bbox coarse pass plus the ST_Contains
    # precise pass - so total_in_bc is every GBIF record inside BC regardless
    # of quality. Each quality filter is then measured against that same base
    # set with a FILTER clause, so all of them share one scan.
    query = f"""
        SELECT
            count(*) AS total_in_bc,

            count(*) FILTER (
                WHERE occ.occurrencestatus <> 'PRESENT'
            ) AS drop_not_present,

            count(*) FILTER (
                WHERE occ.basisofrecord IN ('FOSSIL_SPECIMEN', 'LIVING_SPECIMEN')
            ) AS drop_basis,

            count(*) FILTER (WHERE occ.species IS NULL)          AS drop_no_species,
            count(*) FILTER (WHERE occ.decimallatitude IS NULL)  AS drop_no_lat,
            count(*) FILTER (WHERE occ.decimallongitude IS NULL) AS drop_no_lon,

            count(*) FILTER (
                WHERE list_has_any(occ.issue, {geo_issues_sql})
            ) AS drop_issues,

            count(*) FILTER (
                WHERE occ.occurrencestatus = 'PRESENT'
                  AND occ.basisofrecord NOT IN ('FOSSIL_SPECIMEN', 'LIVING_SPECIMEN')
                  AND occ.species IS NOT NULL
                  AND occ.decimallatitude IS NOT NULL
                  AND occ.decimallongitude IS NOT NULL
                  AND NOT list_has_any(occ.issue, {geo_issues_sql})
            ) AS kept

        FROM read_parquet('{snapshot_path}') AS occ
        CROSS JOIN read_parquet('{boundary_parquet_path}') AS bc
        WHERE occ.decimallongitude BETWEEN {min_lon} AND {max_lon}
          AND occ.decimallatitude  BETWEEN {min_lat} AND {max_lat}
          AND ST_Contains(bc.geom, ST_Point(occ.decimallongitude, occ.decimallatitude))
    """

    print("Running single-pass filter-attribution query...")
    row = con.execute(query).fetchone()
    columns = [d[0] for d in con.description]
    con.close()
    return dict(zip(columns, row))


def report(counts):
    """Print the base set, each filter's drop with its share, and the total."""
    total = counts["total_in_bc"]
    kept = counts["kept"]

    # Human-readable label for each drop_* column, longest first for padding.
    drop_labels = [
        ("drop_not_present", "occurrencestatus <> 'PRESENT'"),
        ("drop_basis",       "basisofrecord fossil/living specimen"),
        ("drop_no_species",  "species IS NULL"),
        ("drop_no_lat",      "decimallatitude IS NULL"),
        ("drop_no_lon",      "decimallongitude IS NULL"),
        ("drop_issues",      "coordinate-quality issue flag"),
    ]

    def pct(n):
        return (100.0 * n / total) if total else 0.0

    width = max(len(label) for _, label in drop_labels)

    print()
    print("=" * 72)
    print("BC OCCURRENCE RECORDS - PER-FILTER DROP ATTRIBUTION")
    print("=" * 72)
    print(f"total_in_bc (inside BC polygon, before quality filters): {total:>14,}")
    print()
    print("Dropped by each filter, measured independently:")
    for key, label in drop_labels:
        n = counts[key]
        print(f"  {label:<{width}}  {n:>14,}  ({pct(n):6.2f}% of total_in_bc)")

    total_dropped = total - kept
    print()
    print(f"  {'kept (passes every filter)':<{width}}  {kept:>14,}  ({pct(kept):6.2f}% of total_in_bc)")
    print(f"  {'total dropped (total_in_bc - kept)':<{width}}  {total_dropped:>14,}  ({pct(total_dropped):6.2f}% of total_in_bc)")
    print()
    print("NOTE: the per-filter drops above OVERLAP - one record can be dropped by")
    print("      more than one filter - so they must NOT be summed. The true total")
    print("      dropped is total_in_bc - kept, shown on the line above.")
    naive_sum = sum(counts[key] for key, _ in drop_labels)
    print(f"      (For reference, naively summing them gives {naive_sum:,}, which")
    print(f"       overcounts the real {total_dropped:,} by {naive_sum - total_dropped:,}.)")
    print("=" * 72)


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

        counts = attribute_drops(bbox, boundary_path, snapshot_path)

    report(counts)


if __name__ == "__main__":
    main()
