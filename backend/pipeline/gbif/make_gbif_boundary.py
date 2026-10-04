"""
make_gbif_boundary.py

Write BC's boundary as a small WKT polygon to paste into a GBIF download
filter (the "geometry" predicate).

Input:  BC's exact legal boundary (ABMS, marine-inclusive), fetched the
        same way build_bc_clean.py does (~54,000 points).
Output: bc_boundary_gbif.wkt  (one POLYGON, a few hundred points)

What it does:
  1. Fetches the boundary with build_bc_clean.get_bc_geometry(), so the
     download filter and the clean product start from the same shape.
  2. Pushes the boundary outward by --buffer-m metres, in BC Albers
     (EPSG:3005) so the distance means the same thing everywhere in BC.
  3. Simplifies it with a tolerance of --tolerance-m metres. The
     simplified edge never moves more than the tolerance from the pushed
     out edge, so as long as the tolerance is smaller than the buffer, the
     result still covers all of BC.
  4. Converts back to WGS84, rounds to 5 decimals (about 1 m), and writes
     the points counter-clockwise, as GBIF expects.
  5. Checks that the written polygon contains the original boundary in
     plain lon/lat (how GBIF tests points), and stops if it does not.

The result is slightly bigger than BC, so a download made with it also
catches records up to about the buffer distance outside the border.
build_bc_clean.py clips those away with the exact polygon.

With the defaults (2 km buffer, 1.9 km tolerance) the polygon has 266
points and adds about 1.2% to BC's area.

Run:
    python make_gbif_boundary.py --out /Users/lucia/Desktop/BCBN/Results/bc_boundary_gbif.wkt
"""

import argparse

import shapely
from pyproj import Transformer
from shapely.geometry import Polygon
from shapely.geometry.polygon import orient
from shapely.ops import transform

from build_bc_clean import get_bc_geometry

# BC Albers: equal-area, metres. Used for the buffer, simplification and
# area figures.
BC_ALBERS = "EPSG:3005"
WGS84 = "EPSG:4326"

# 5 decimals of a degree is about 1 m, plenty for a download filter.
DECIMALS = 5


def make_gbif_polygon(geometry, buffer_m, tolerance_m):
    """Return a simplified, counter-clockwise WGS84 polygon that contains
    `geometry`."""
    to_albers = Transformer.from_crs(WGS84, BC_ALBERS, always_xy=True).transform
    to_wgs84 = Transformer.from_crs(BC_ALBERS, WGS84, always_xy=True).transform

    albers = transform(to_albers, geometry)
    grown = albers.buffer(buffer_m, join_style="mitre", mitre_limit=2.0)
    # Keep the outer ring only: a hole could leave part of BC uncovered,
    # and GBIF handles a single plain polygon best.
    grown = Polygon(grown.exterior)
    simple = Polygon(grown.simplify(tolerance_m, preserve_topology=True).exterior)

    wgs84 = transform(to_wgs84, simple)
    rounded = Polygon(
        [(round(x, DECIMALS), round(y, DECIMALS)) for x, y in wgs84.exterior.coords]
    )
    return orient(rounded, sign=1.0)


def area_km2(geometry):
    to_albers = Transformer.from_crs(WGS84, BC_ALBERS, always_xy=True).transform
    return transform(to_albers, geometry).area / 1e6


def main():
    parser = argparse.ArgumentParser(
        description="Write BC's boundary as a small WKT polygon for a GBIF download filter."
    )
    parser.add_argument(
        "--out",
        default="/Users/lucia/Desktop/BCBN/Results/bc_boundary_gbif.wkt",
        help="Where to write the WKT file.",
    )
    parser.add_argument(
        "--buffer-m",
        type=float,
        default=2000,
        help="How far to push the boundary outward before simplifying (metres).",
    )
    parser.add_argument(
        "--tolerance-m",
        type=float,
        default=1900,
        help="Simplification tolerance (metres). Must be smaller than --buffer-m.",
    )
    args = parser.parse_args()

    if args.tolerance_m >= args.buffer_m:
        raise SystemExit("--tolerance-m must be smaller than --buffer-m, "
                         "or the simplified polygon can cut into BC.")

    geometry = get_bc_geometry()
    polygon = make_gbif_polygon(geometry, args.buffer_m, args.tolerance_m)

    wkt = shapely.to_wkt(polygon, rounding_precision=DECIMALS, trim=True)
    with open(args.out, "w") as f:
        f.write(wkt)

    # Check what was actually written, not the in-memory polygon.
    with open(args.out) as f:
        written = shapely.from_wkt(f.read())
    if not written.is_valid:
        raise RuntimeError("Written polygon is not valid.")
    if not written.contains(geometry):
        raise RuntimeError("Written polygon does not cover all of BC's boundary. "
                           "Try a smaller --tolerance-m or a larger --buffer-m.")
    if not written.exterior.is_ccw:
        raise RuntimeError("Written polygon is not counter-clockwise.")

    original_km2 = area_km2(geometry)
    added_km2 = area_km2(written) - original_km2
    print(f"Original boundary: {shapely.get_num_coordinates(geometry):,} points, "
          f"{original_km2:,.0f} km2")
    print(f"GBIF polygon:      {len(written.exterior.coords):,} points, "
          f"{len(wkt):,} characters")
    print(f"Area added:        {added_km2:,.0f} km2 "
          f"({added_km2 / original_km2 * 100:.2f}%)")
    print(f"\nDone. File: {args.out}")


if __name__ == "__main__":
    main()
