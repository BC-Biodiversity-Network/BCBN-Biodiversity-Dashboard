"""
build_reserves.py

Build the reserve files the site uses, from the BC shapefile of "Aboriginal
Lands of Canada Legislative Boundaries" (Natural Resources Canada, Open
Government Licence - Canada), downloaded by hand.

Input:  the shapefile, for example data/raw/reserves/AL_TA_BC_2_190_eng.shp
        (given as the first argument). Its fields used here are ALCODE (a
        unique id), NAME1 (the name), REVDATE (revision date, YYYYMMDD).

Outputs, written to backend/data/api/ (the folder the API reads; covered by
the data/ rule in .gitignore, so never committed):

  reserves_outlines.geojson
      For the map layer. One feature per reserve, with only two
      properties: alcode and name. name is NAME1 exactly as in the
      shapefile. Outlines are repaired, simplified in BC Albers by
      --tolerance-m metres (keeping each shape valid and never dropping a
      reserve), and rounded to 5 decimals of a degree (about 1 m).

  reserve_cells.parquet
      For the reminders. Columns res, h3_cell, alcode: one row for every
      H3 cell, at every hexagon size the site uses (resolutions 4 to 7),
      that overlaps a reserve at all, once per reserve it overlaps. It uses
      the original shapes (only repaired with make_valid, never
      simplified), and the same rule as the box search: the h3 package's
      "overlap" search, which is containmentOverlapping in h3-js. h3_cell
      is written the same way as the h3_r4 to h3_r7 columns elsewhere
      (lower-case hex text).

  reserves_info.json
      Where the outlines come from, for the site to show: source title,
      publisher, licence, the source's note that the data set is not to be
      used for defining boundaries, the shapefile name, the latest REVDATE,
      the build date, and the simplification tolerance.

These files only say which cells touch which reserve. Nothing here reads,
joins or counts any records or species, and none of the outputs hold any.

Each output is written to a temporary name first and checked; only if every
check passes are the real files replaced (see pipeline/gbif/safe_outputs.py).

Run from the repository root:
    .venv/bin/python backend/pipeline/reserves/build_reserves.py \\
        data/raw/reserves/AL_TA_BC_2_190_eng.shp
"""

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

import geopandas as gpd
import h3
import pandas as pd
import shapely
from shapely.geometry import MultiPolygon, Polygon, mapping

# The temporary-file helpers live with the GBIF pipeline scripts. Those
# scripts are not a package, so add their folder to the path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gbif"))
from safe_outputs import (  # noqa: E402
    clear_temporary,
    replace_outputs,
    report_not_replaced,
    temporary_path,
)

# backend/pipeline/reserves/build_reserves.py -> backend/data/api
DEFAULT_OUTDIR = Path(__file__).resolve().parents[2] / "data" / "api"

OUTLINES_FILE = "reserves_outlines.geojson"
CELLS_FILE = "reserve_cells.parquet"
INFO_FILE = "reserves_info.json"

# The hexagon sizes the site uses: the same as RESOLUTIONS in
# backend/api/settings.py and frontend/src/zoomTiers.js.
RESOLUTIONS = (4, 5, 6, 7)

# Coordinates for the work: plain longitude and latitude, and BC Albers
# (metres, the same distance everywhere in BC) for simplifying.
WGS84 = "EPSG:4326"
BC_ALBERS = "EPSG:3005"

# The default simplification tolerance in metres. At the map's deepest zoom
# (MAX_ZOOM in zoomTiers.js) one pixel covers about 18 m of ground, so a
# 20 m tolerance moves an outline by about one pixel at most.
DEFAULT_TOLERANCE_M = 20

# 5 decimals of a degree is about 1 m, well under the tolerance.
DECIMALS = 5

# Facts about the source, written into reserves_info.json. The note and the
# licence text are copied from the shapefile's own metadata file
# (AL_TA_BC_2_190_ISO_eng.xml).
SOURCE_TITLE = "Aboriginal Lands of Canada Legislative Boundaries"
SOURCE_PUBLISHER = "Natural Resources Canada"
LICENCE_NAME = "Open Government Licence - Canada"
LICENCE_URL = "https://open.canada.ca/en/open-government-licence-canada"
BOUNDARY_NOTE = ("This data set is not to be used for defining boundaries. "
                 "Administrative decisions should be based on legal documents "
                 "and legal survey plans.")

# The shapefile fields this script needs.
NEEDED_FIELDS = ("ALCODE", "NAME1", "REVDATE")


def polygon_parts(geometry):
    """Return the polygon parts of a geometry as one MultiPolygon, dropping
    any points or lines. make_valid can turn a broken polygon into a mix of
    shapes, and only the areas matter here. Returns an empty MultiPolygon
    when there is no area left."""
    if geometry is None or geometry.is_empty:
        return MultiPolygon()
    if isinstance(geometry, Polygon):
        return MultiPolygon([geometry])
    if isinstance(geometry, MultiPolygon):
        return geometry
    # A GeometryCollection (or something else with parts): keep the areas.
    polygons = []
    for part in getattr(geometry, "geoms", []):
        polygons.extend(polygon_parts(part).geoms)
    return MultiPolygon(polygons)


def repair(geometry):
    """Return the geometry made valid with make_valid, as a MultiPolygon.
    A valid geometry comes back with the same shape."""
    return polygon_parts(shapely.make_valid(geometry))


def read_reserves(shapefile):
    """Read the shapefile and return a GeoDataFrame in WGS84 with columns
    alcode, name, revdate and geometry (repaired, never simplified), and
    the number of geometries that were invalid before repair. Stops with a
    message if a needed field is missing, an ALCODE is empty or repeated,
    a name is empty, or a shape has no area left after repair."""
    frame = gpd.read_file(shapefile)
    missing = [field for field in NEEDED_FIELDS if field not in frame.columns]
    if missing:
        raise SystemExit(f"{shapefile} has no field(s) {', '.join(missing)}.")
    if frame.crs is None:
        raise SystemExit(f"{shapefile} has no coordinate system (.prj file).")

    invalid_before = int((~frame.geometry.is_valid).sum())

    # The source is in NAD83(CSRS), EPSG:4617. It differs from WGS84 by
    # about a metre in BC, far below the simplification tolerance and the
    # hexagon sizes, but convert anyway so the outputs say what they are.
    frame = frame.to_crs(WGS84)

    reserves = gpd.GeoDataFrame(
        {
            "alcode": frame["ALCODE"].astype(str),
            # Names exactly as in NAME1: no change of case or letters.
            "name": frame["NAME1"],
            "revdate": frame["REVDATE"],
        },
        geometry=[repair(geometry) for geometry in frame.geometry],
        crs=WGS84,
    )

    if reserves["alcode"].str.strip().eq("").any():
        raise SystemExit("A reserve has an empty ALCODE.")
    if not reserves["alcode"].is_unique:
        repeated = sorted(reserves.loc[reserves["alcode"].duplicated(), "alcode"])
        raise SystemExit(f"ALCODE is repeated: {', '.join(repeated[:10])}")
    if reserves["name"].isna().any() or reserves["name"].astype(str).str.strip().eq("").any():
        raise SystemExit("A reserve has no NAME1.")
    empty = reserves.loc[reserves.geometry.is_empty, "alcode"].tolist()
    if empty:
        raise SystemExit(f"These reserves have no area after repair: {', '.join(empty)}")
    return reserves, invalid_before


def simplify_outline(geometry, tolerance_m, to_albers, to_wgs84):
    """Return a simplified copy of one reserve's outline (WGS84 in and out):
    simplified in BC Albers by tolerance_m metres with its topology kept,
    then snapped to a grid of DECIMALS decimals. Falls back to the
    unsimplified shape, snapped the same way, if simplifying leaves it
    empty or invalid, so no reserve ever drops off the map."""
    grid = 10 ** -DECIMALS
    albers = shapely.transform(geometry, to_albers, interleaved=False)
    simple = albers.simplify(tolerance_m, preserve_topology=True)
    result = polygon_parts(shapely.set_precision(
        shapely.transform(simple, to_wgs84, interleaved=False), grid))
    if result.is_empty or not result.is_valid:
        result = polygon_parts(shapely.set_precision(geometry, grid))
    return result


def albers_transforms():
    """Return two functions for shapely.transform(..., interleaved=False):
    WGS84 to BC Albers, and back."""
    from pyproj import Transformer

    forward = Transformer.from_crs(WGS84, BC_ALBERS, always_xy=True)
    back = Transformer.from_crs(BC_ALBERS, WGS84, always_xy=True)
    return (lambda x, y: forward.transform(x, y)), (lambda x, y: back.transform(x, y))


def rounded(coordinates):
    """Round every number in a nested GeoJSON coordinate list to DECIMALS,
    so the file holds short numbers like -123.12345, not long floats."""
    if isinstance(coordinates, (int, float)):
        return round(coordinates, DECIMALS)
    return [rounded(item) for item in coordinates]


def outlines_geojson(reserves, tolerance_m):
    """Return the map layer as a GeoJSON FeatureCollection (a dict): one
    feature per reserve, sorted by alcode, with only alcode and name as
    properties and the simplified outline as geometry."""
    to_albers, to_wgs84 = albers_transforms()
    features = []
    for row in reserves.sort_values("alcode").itertuples():
        outline = simplify_outline(row.geometry, tolerance_m, to_albers, to_wgs84)
        # A reserve in one piece is written as a Polygon, which is simpler
        # for the map than a MultiPolygon with one part.
        if len(outline.geoms) == 1:
            outline = outline.geoms[0]
        geometry = mapping(outline)
        features.append({
            "type": "Feature",
            "properties": {"alcode": row.alcode, "name": row.name},
            "geometry": {"type": geometry["type"],
                         "coordinates": rounded(geometry["coordinates"])},
        })
    return {"type": "FeatureCollection", "features": features}


def cells_for_shape(geometry, res):
    """Return the set of H3 cells at resolution res that overlap a
    (repaired) shape at all: the h3 package's "overlap" search, the same
    as containmentOverlapping in h3-js and the box search in
    backend/api/area.py. Holes are respected, so a cell lying wholly in a
    hole does not count."""
    cells = set()
    for polygon in polygon_parts(geometry).geoms:
        shape = h3.geo_to_h3shape(mapping(polygon))
        cells.update(h3.h3shape_to_cells_experimental(shape, res, contain="overlap"))
    return cells


def reserve_cells(reserves, resolutions=RESOLUTIONS):
    """Return a DataFrame with columns res, h3_cell, alcode: one row for
    each cell at each resolution that overlaps a reserve, once per reserve,
    sorted by res, h3_cell, alcode."""
    rows = []
    for res in resolutions:
        for row in reserves.itertuples():
            for cell in cells_for_shape(row.geometry, res):
                rows.append((res, cell, row.alcode))
    frame = pd.DataFrame(rows, columns=["res", "h3_cell", "alcode"])
    frame["res"] = frame["res"].astype("int8")
    return frame.sort_values(["res", "h3_cell", "alcode"]).reset_index(drop=True)


def latest_revdate(reserves):
    """Return (latest, missing): the latest REVDATE as YYYY-MM-DD (None if
    no reserve has one) and how many reserves have no REVDATE. REVDATE is
    YYYYMMDD text in the shapefile; newly added reserves can have none
    (only a creation date), and those are skipped. A value that is there
    but is not a date stops the build."""
    dates = []
    missing = 0
    for value in reserves["revdate"]:
        if pd.isna(value) or str(value).strip() == "":
            missing += 1
            continue
        text = str(value).strip()
        try:
            dates.append(datetime.datetime.strptime(text, "%Y%m%d").date())
        except ValueError:
            raise SystemExit(f"REVDATE {text!r} is not a date in YYYYMMDD form.")
    latest = max(dates).isoformat() if dates else None
    return latest, missing


def reserves_info(reserves, shapefile, tolerance_m, build_date):
    """Return the metadata file's contents (a dict): where the outlines
    come from and how they were built."""
    latest, missing = latest_revdate(reserves)
    return {
        "source_title": SOURCE_TITLE,
        "source_publisher": SOURCE_PUBLISHER,
        "licence": LICENCE_NAME,
        "licence_url": LICENCE_URL,
        "boundary_note": BOUNDARY_NOTE,
        "shapefile": Path(shapefile).name,
        "latest_revdate": latest,
        "reserves_without_revdate": missing,
        "build_date": build_date,
        "reserve_count": int(len(reserves)),
        "simplify_tolerance_m": tolerance_m,
        "resolutions": list(RESOLUTIONS),
    }


def check_outputs(reserves, outlines_path, cells_path, info_path):
    """Read the written (temporary) files back and return a list of
    problems; an empty list means every check passed."""
    problems = []
    expected = dict(zip(reserves["alcode"], reserves["name"]))

    with open(outlines_path, encoding="utf-8") as f:
        outlines = json.load(f)
    features = outlines.get("features", [])
    found = {}
    for feature in features:
        properties = feature.get("properties", {})
        if set(properties) != {"alcode", "name"}:
            problems.append(f"An outline has properties {sorted(properties)}, "
                            "not just alcode and name.")
            break
        found[properties["alcode"]] = properties["name"]
        shape = shapely.geometry.shape(feature["geometry"])
        if shape.is_empty or not shape.is_valid:
            problems.append(f"The outline of {properties['alcode']} is empty or invalid.")
    if found.keys() != expected.keys() or len(features) != len(expected):
        problems.append(f"The outlines have {len(features)} features for "
                        f"{len(expected)} reserves, or a different set of alcodes.")
    elif any(found[code] != name for code, name in expected.items()):
        problems.append("An outline's name differs from NAME1.")

    cells = pd.read_parquet(cells_path)
    if list(cells.columns) != ["res", "h3_cell", "alcode"]:
        problems.append(f"{CELLS_FILE} has columns {list(cells.columns)}.")
    else:
        if set(cells["res"].unique()) != set(RESOLUTIONS):
            problems.append(f"{CELLS_FILE} does not hold every resolution {RESOLUTIONS}.")
        wrong = [cell for cell, res in zip(cells["h3_cell"], cells["res"])
                 if not h3.is_valid_cell(cell) or h3.get_resolution(cell) != res]
        if wrong:
            problems.append(f"{len(wrong)} cell(s) are not valid at their res, e.g. {wrong[0]}.")
        if cells.duplicated().any():
            problems.append(f"{CELLS_FILE} has repeated rows.")
        # Any shape with area overlaps at least one cell at every size.
        for res in RESOLUTIONS:
            covered = set(cells.loc[cells["res"] == res, "alcode"])
            lost = sorted(set(expected) - covered)
            if lost:
                problems.append(f"At res {res}, no cell for reserve(s) {', '.join(lost[:10])}.")

    with open(info_path, encoding="utf-8") as f:
        info = json.load(f)
    if info.get("boundary_note") != BOUNDARY_NOTE:
        problems.append(f"{INFO_FILE} does not carry the boundary note.")
    return problems


def build(shapefile, outdir, tolerance_m=DEFAULT_TOLERANCE_M, build_date=None):
    """Build the three files from the shapefile into outdir. Returns a dict
    of what was built (counts and sizes) when every check passes; raises
    SystemExit, leaving the real files untouched, when one fails."""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    paths = [str(outdir / OUTLINES_FILE), str(outdir / CELLS_FILE), str(outdir / INFO_FILE)]
    outlines_path, cells_path, info_path = paths
    clear_temporary(paths)

    if build_date is None:
        build_date = datetime.date.today().isoformat()

    reserves, invalid_before = read_reserves(shapefile)

    with open(temporary_path(outlines_path), "w", encoding="utf-8") as f:
        # ensure_ascii=False keeps names with accents readable in the file.
        json.dump(outlines_geojson(reserves, tolerance_m), f,
                  ensure_ascii=False, separators=(",", ":"))

    cells = reserve_cells(reserves)
    cells.to_parquet(temporary_path(cells_path), index=False)

    info = reserves_info(reserves, shapefile, tolerance_m, build_date)
    with open(temporary_path(info_path), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2)
        f.write("\n")

    problems = check_outputs(reserves, *[temporary_path(path) for path in paths])
    if problems:
        for problem in problems:
            print(f"PROBLEM: {problem}")
        report_not_replaced(paths)
        raise SystemExit(1)
    replace_outputs(paths)

    return {
        "reserves": len(reserves),
        "invalid_before_repair": invalid_before,
        "cells_per_res": {int(res): int(count) for res, count in
                          cells.groupby("res")["h3_cell"].nunique().items()},
        "rows_per_res": {int(res): int(count) for res, count in
                         cells.groupby("res").size().items()},
        "sizes": {Path(path).name: os.path.getsize(path) for path in paths},
        "paths": paths,
    }


def main():
    """Read the arguments, build the files and print a short report."""
    parser = argparse.ArgumentParser(description="Build the reserve files the site uses.")
    parser.add_argument("shapefile", help="The BC Aboriginal Lands shapefile (.shp).")
    parser.add_argument("--outdir", default=str(DEFAULT_OUTDIR),
                        help="Where to write the files (default: backend/data/api).")
    parser.add_argument("--tolerance-m", type=float, default=DEFAULT_TOLERANCE_M,
                        help="Simplification tolerance for the map outlines, in metres.")
    args = parser.parse_args()

    result = build(args.shapefile, args.outdir, args.tolerance_m)
    print(f"Reserves: {result['reserves']:,} "
          f"({result['invalid_before_repair']} invalid before repair)")
    for res in RESOLUTIONS:
        print(f"  res {res}: {result['cells_per_res'][res]:,} cells touch a reserve "
              f"({result['rows_per_res'][res]:,} cell-reserve pairs)")
    for name, size in result["sizes"].items():
        print(f"  {name}: {size:,} bytes")
    print("\nDone. Files:")
    for path in result["paths"]:
        print(f"  {path}")


if __name__ == "__main__":
    main()
