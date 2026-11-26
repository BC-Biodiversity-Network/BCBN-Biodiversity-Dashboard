"""Tests for pipeline/reserves/build_reserves.py: the map outlines, the
cells that touch each reserve, and the metadata file. Every reserve here is
a made-up shape near Vancouver; no real reserve data is used, and nothing
is written outside the test's own folder."""

import json
import sys
from pathlib import Path

import geopandas as gpd
import h3
import pandas as pd
import pytest
from shapely.geometry import Polygon, box

from api.area import box_cells

# The pipeline scripts are not a package; add their folder to the path the
# way the other pipeline tests do.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline" / "reserves"))
import build_reserves as reserves  # noqa: E402

# A made-up box near Vancouver, about 4 km by 3 km.
WEST, SOUTH, EAST, NORTH = -123.10, 49.30, -123.05, 49.33

# A bow tie: its two triangles cross at one point, so it is invalid until
# it is repaired.
BOW_TIE = Polygon([(-123.20, 49.40), (-123.18, 49.42), (-123.18, 49.40), (-123.20, 49.42)])


def write_shapefile(folder, rows):
    """Write a tiny shapefile with the fields the script reads, in the
    source's coordinate system (EPSG:4617), and return its path. rows are
    (alcode, name, revdate, geometry)."""
    frame = gpd.GeoDataFrame(
        {
            "ALCODE": [row[0] for row in rows],
            "NAME1": [row[1] for row in rows],
            "REVDATE": [row[2] for row in rows],
            "ALTYPE": ["Indian Reserve"] * len(rows),
        },
        geometry=[row[3] for row in rows],
        crs="EPSG:4617",
    )
    path = Path(folder) / "made_up.shp"
    frame.to_file(path, encoding="UTF-8")
    return path


def test_rectangle_gets_the_same_cells_as_the_box_search():
    """A reserve shaped like a box gets exactly the cells the box search
    counts for that box, at every hexagon size: the same overlap rule."""
    shape = box(WEST, SOUTH, EAST, NORTH)
    for res in reserves.RESOLUTIONS:
        assert sorted(reserves.cells_for_shape(shape, res)) == box_cells(
            (WEST, SOUTH, EAST, NORTH), res)


def test_a_corner_is_enough():
    """A cell counts when the reserve only reaches into it, not only when
    the cell's centre is inside: a tiny reserve away from any cell centre
    still gets its cell."""
    cell = h3.latlng_to_cell(49.31, -123.08, 7)
    lat, lng = h3.cell_to_latlng(cell)
    # A 20 m square about 300 m from the cell's centre, still inside it.
    tiny = box(lng + 0.004, lat, lng + 0.0043, lat + 0.0002)
    assert not tiny.contains(gpd.points_from_xy([lng], [lat])[0])
    assert cell in reserves.cells_for_shape(tiny, 7)


def test_cells_wholly_in_a_hole_do_not_count():
    """A cell that lies wholly inside a hole in a reserve does not touch
    the reserve, but the same cell counts once the hole is filled."""
    centre_lat, centre_lng = 49.315, -123.075
    cell = h3.latlng_to_cell(centre_lat, centre_lng, 7)
    # A hole about 4 km across, far larger than a res 7 cell (about 2.8 km).
    hole = box(centre_lng - 0.03, centre_lat - 0.02, centre_lng + 0.03, centre_lat + 0.02)
    outer = box(centre_lng - 0.1, centre_lat - 0.07, centre_lng + 0.1, centre_lat + 0.07)
    with_hole = Polygon(outer.exterior.coords, [hole.exterior.coords])
    assert cell not in reserves.cells_for_shape(with_hole, 7)
    assert cell in reserves.cells_for_shape(outer, 7)


def test_repair_makes_a_bow_tie_valid():
    """make_valid turns the bow tie into valid polygons with area, and a
    valid shape keeps its area."""
    assert not BOW_TIE.is_valid
    fixed = reserves.repair(BOW_TIE)
    assert fixed.is_valid and fixed.area > 0 and fixed.geom_type == "MultiPolygon"
    square = box(WEST, SOUTH, EAST, NORTH)
    assert reserves.repair(square).area == pytest.approx(square.area)


def test_build_writes_the_three_files(tmp_path):
    """A full build from a made-up shapefile: outlines with only alcode and
    name (names exactly as given), cells at every size for every reserve,
    and the metadata, with a missing revision date skipped."""
    names = {
        "90001": "Made-Up Creek 1A",
        "90002": "T'ESTÉ ẔEẔ ? lake",
        "90003": "TINY 3",
    }
    shapefile = write_shapefile(tmp_path, [
        ("90001", names["90001"], "20250102", box(WEST, SOUTH, EAST, NORTH)),
        ("90002", names["90002"], "20260304", BOW_TIE),
        # A 30 m square: much smaller than the simplification tolerance.
        ("90003", names["90003"], None, box(-123.0, 49.5, -122.9996, 49.5003)),
    ])
    outdir = tmp_path / "out"
    result = reserves.build(shapefile, outdir, tolerance_m=100, build_date="2026-01-31")

    with open(outdir / reserves.OUTLINES_FILE, encoding="utf-8") as f:
        outlines = json.load(f)
    found = {f["properties"]["alcode"]: f["properties"]["name"] for f in outlines["features"]}
    assert found == names
    assert all(set(f["properties"]) == {"alcode", "name"} for f in outlines["features"])
    # The tiny reserve is still drawn, even though it is far smaller than
    # the tolerance.
    tiny = next(f for f in outlines["features"] if f["properties"]["alcode"] == "90003")
    assert tiny["geometry"]["coordinates"]

    cells = pd.read_parquet(outdir / reserves.CELLS_FILE)
    assert list(cells.columns) == ["res", "h3_cell", "alcode"]
    for res in reserves.RESOLUTIONS:
        at_res = cells[cells["res"] == res]
        assert set(at_res["alcode"]) == set(names)
        # The box reserve's cells are exactly the box search's.
        assert sorted(at_res.loc[at_res["alcode"] == "90001", "h3_cell"]) == box_cells(
            (WEST, SOUTH, EAST, NORTH), res)

    with open(outdir / reserves.INFO_FILE, encoding="utf-8") as f:
        info = json.load(f)
    assert info["latest_revdate"] == "2026-03-04"
    assert info["reserves_without_revdate"] == 1
    assert info["build_date"] == "2026-01-31"
    assert info["shapefile"] == "made_up.shp"
    assert "not to be used for defining boundaries" in info["boundary_note"]
    assert info["licence"] == "Open Government Licence - Canada"

    assert result["invalid_before_repair"] == 1
    # No temporary files are left behind after a build that passed.
    assert sorted(p.name for p in outdir.iterdir()) == sorted(
        [reserves.OUTLINES_FILE, reserves.CELLS_FILE, reserves.INFO_FILE])


def test_repeated_alcode_stops_before_writing(tmp_path):
    """Two reserves with the same ALCODE stop the build before any file is
    written."""
    shapefile = write_shapefile(tmp_path, [
        ("90001", "ONE", "20250102", box(WEST, SOUTH, EAST, NORTH)),
        ("90001", "TWO", "20250102", BOW_TIE),
    ])
    outdir = tmp_path / "out"
    with pytest.raises(SystemExit):
        reserves.build(shapefile, outdir)
    assert list(outdir.iterdir()) == []


def test_latest_revdate_rejects_a_bad_date():
    """A revision date that is there but is not YYYYMMDD stops the build."""
    frame = pd.DataFrame({"revdate": ["20250102", "2025-01-03"]})
    with pytest.raises(SystemExit):
        reserves.latest_revdate(frame)
