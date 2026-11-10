"""
area.py

The area search, for GET /api/area and its CSV download: draw a box on the
map and get everything in the hexagons under it, at one resolution (asked
for by Evan; the rules decided with Lucia in November 2026).

The box: west, south, east and north edges in degrees (longitude -180 to
180, latitude -90 to 90, west less than east, south less than north; a box
across the 180th meridian is not taken). Its sides follow the lines of
longitude and latitude.

Which hexagons count: every hexagon at the resolution asked for (res, 4 to
7) that overlaps the box, even by a corner, not only those whose centre is
in it. They are found with the h3 package's polygon to cells search in its
"overlap" mode. The front end's h3-js has the same search
(polygonToCellsExperimental with containmentOverlapping) from the same H3
library, and gives exactly the same hexagons (checked on 400 boxes in
November 2026, and by a test that runs h3-js with node), so the map can
outline the hexagons the API counted without the API listing them. The
answer gives how many there are (hexagon_count), not their ids, which for a
large box would be megabytes.

The smallest box (MIN_AREA_HEXAGONS): a box must cover at least that many
average hexagons of its resolution, so nobody can draw a box tightly around
one small place, such as a First Nations reserve, and pull out that place's
records alone. The area is the box's true area on the sphere H3 uses, so it
is compared like for like with H3's average hexagon area.

The largest box (MAX_AREA_HEXAGONS): a box may touch at most that many
hexagons. Measured on a laptop in November 2026, finding the hexagons and
counting their species took about 0.5 s for southern BC at resolution 7
(117,442 hexagons), and 1.9 s and 220 MB more memory for all of BC at
resolution 7 (492,555 hexagons). The map only shows resolution 7 when
zoomed in, so a box that large at that resolution is not a normal request;
the message suggests larger hexagons.

The answer is the hexagon panel's for these hexagons together (see
cells.hexagons_list, the same code): totals for the years (each record and
each species counted once), the records a year range left out, and one
page of species, filtered by name and BC status, in the order asked for,
optionally only a rank's species. Species sensitive.py hides at this
resolution are left out of all of it. Each species in the list has
gbif_link, the GBIF search for its keys (its card key and every key merged
into it) inside the box for the years counted. A single species' map is not
covered yet (species= gives 400).

Plain SQL only (in cells.py), so this runs on the cluster's DuckDB 0.10.3.
"""

import math

import h3

from api.cells import DEFAULT_PAGE_SIZE, cards_gbif_keys, hexagons_list
from api.gbif_links import gbif_box_search_url
from api.hexes import HexesRequestError, year_range
from api.ranks import require_rank
from api.sensitive import hidden_species
from api.settings import RESOLUTIONS
from api.species_list import check_list_options

# The smallest box, in average hexagons of the resolution asked for. Kept
# above 1 so a box cannot be drawn around one small place (such as a First
# Nations reserve) to get only that place's records. Change it here only.
MIN_AREA_HEXAGONS = 4

# The most hexagons a box may touch (see the top of this file for the
# measurements behind it).
MAX_AREA_HEXAGONS = 200_000

# The radius of the sphere H3 measures areas on, in km (H3's authalic Earth
# radius), so a box's area compares with h3.average_hexagon_area.
EARTH_RADIUS_KM = 6371.007180918475


class AreaRequestError(HexesRequestError):
    """A box or resolution the area search cannot use; answered with 400,
    like the hexagon panel's bad requests."""


def read_number(name, text, low, high):
    """Return one edge of the box as a number, or stop with a clear message
    when it is missing, not a number, or outside low to high."""
    if text is None or not str(text).strip():
        raise AreaRequestError(f"give the box's {name} edge as {name}=..., in degrees.")
    try:
        value = float(text)
    except ValueError:
        raise AreaRequestError(f"{name} must be a number of degrees; got {text!r}.") from None
    if not math.isfinite(value) or not low <= value <= high:
        raise AreaRequestError(f"{name} must be between {low} and {high} degrees; got {text!r}.")
    return value


def read_resolution(text):
    """Return the resolution asked for (4 to 7), or stop with a clear
    message."""
    try:
        res = int(str(text).strip())
    except ValueError:
        res = None
    if res not in RESOLUTIONS:
        raise AreaRequestError(
            f"res must be one of {', '.join(str(r) for r in RESOLUTIONS)}; got {text!r}.")
    return res


def read_box(west, south, east, north):
    """Return the box (west, south, east, north) as numbers, checking each
    edge and that west is less than east and south less than north."""
    box = (read_number("west", west, -180, 180), read_number("south", south, -90, 90),
           read_number("east", east, -180, 180), read_number("north", north, -90, 90))
    if box[0] >= box[2]:
        raise AreaRequestError(f"west ({box[0]}) must be less than east ({box[2]}); "
                               "a box across the 180th meridian is not taken.")
    if box[1] >= box[3]:
        raise AreaRequestError(f"south ({box[1]}) must be less than north ({box[3]}).")
    return box


def box_area_km2(box):
    """Return a box's area in km² on H3's sphere: the radius squared, times
    the width in radians, times the difference of the sines of its north
    and south edges (exact for a box whose sides follow longitude and
    latitude)."""
    west, south, east, north = box
    return (EARTH_RADIUS_KM ** 2 * math.radians(east - west)
            * (math.sin(math.radians(north)) - math.sin(math.radians(south))))


def minimum_area_km2(res):
    """Return the smallest box allowed at a resolution, in km²:
    MIN_AREA_HEXAGONS average hexagons."""
    return MIN_AREA_HEXAGONS * h3.average_hexagon_area(res, "km^2")


def check_box_size(box, res):
    """Stop with a clear message when the box is smaller than the minimum
    at this resolution, or would touch more than MAX_AREA_HEXAGONS
    hexagons judging by its area (checked before the hexagons are found, so
    a huge box costs nothing). Returns the box's area in km²."""
    area = box_area_km2(box)
    smallest = minimum_area_km2(res)
    if area < smallest:
        # The message gives the hexagons' size, never the resolution number,
        # as it may be shown to visitors as it is.
        raise AreaRequestError(
            f"The box is about {area:,.1f} km², smaller than the minimum of {smallest:,.1f} km² for "
            f"hexagons of about {h3.average_hexagon_area(res, 'km^2'):,.1f} km² ({MIN_AREA_HEXAGONS} times "
            "their average area). Draw a larger box.")
    estimate = area / h3.average_hexagon_area(res, "km^2")
    if estimate > MAX_AREA_HEXAGONS:
        raise too_many_hexagons(estimate, res)
    return area


def too_many_hexagons(count, res):
    """The error for a box over MAX_AREA_HEXAGONS hexagons (count, about) at
    a resolution, giving the hexagons' size rather than the resolution
    number, as it may be shown to visitors as it is."""
    return AreaRequestError(
        f"The box touches about {count:,.0f} hexagons of about {h3.average_hexagon_area(res, 'km^2'):,.1f} km², "
        f"more than the {MAX_AREA_HEXAGONS:,} an area search can count. Draw a smaller box or use larger "
        "hexagons.")


def box_cells(box, res):
    """Return every hexagon at resolution res that overlaps the box, even by
    a corner, sorted: the h3 package's "overlap" search, the same as
    polygonToCellsExperimental with containmentOverlapping in h3-js."""
    west, south, east, north = box
    shape = h3.LatLngPoly([(south, west), (south, east), (north, east), (north, west)])
    return sorted(h3.h3shape_to_cells_experimental(shape, res, contain="overlap"))


def area_species(data, west, south, east, north, res, year_from=None, year_to=None, q=None, offset=0,
                 limit=DEFAULT_PAGE_SIZE, rank=None, rank_given=None, statuses=None, sort=None):
    """Return the answer for the hexagons under a box (see the top of this
    file): the box and its area, the resolution, how many hexagons it
    touches, the totals for the years and one page of species (offset,
    limit; limit None for all of them, for the CSV download), each with its
    GBIF link. The edges and res come as given in the address. Raises
    AreaRequestError (and HexesRequestError for a bad year range) for a bad
    box or resolution, ListRequestError for an unknown sort or status,
    QueryTooShort for a name filter too short, RankRequestError for a
    request that does not name a rank, and UnknownRank for a rank with no
    species."""
    box = read_box(west, south, east, north)
    res = read_resolution(res)
    area = check_box_size(box, res)
    sort, statuses = check_list_options(sort, statuses)
    years = year_range(year_from, year_to, data.meta["first_year"], data.meta["last_year"])
    hidden = hidden_species(data.settings, res)

    cursor = data.cursor()
    path = require_rank(data, cursor, rank, rank_given or {}) if rank else None
    cells = box_cells(box, res)
    if len(cells) > MAX_AREA_HEXAGONS:
        raise too_many_hexagons(len(cells), res)
    _, part = hexagons_list(data, cursor, res, cells, years, hidden, q, statuses, sort, offset, limit, path)

    # Each species' GBIF search inside the box, for the years counted.
    keys = cards_gbif_keys(cursor, [result["specieskey"] for result in part["results"]])
    for result in part["results"]:
        result["gbif_link"] = gbif_box_search_url(keys[result["specieskey"]], box, years)

    return {
        "box": dict(zip(("west", "south", "east", "north"), box)),
        "res": res,
        "box_area_km2": round(area, 1),
        "minimum_area_km2": round(minimum_area_km2(res), 1),
        # How many hexagons at this resolution overlap the box: the front
        # end finds the same ones with h3-js to outline them.
        "hexagon_count": len(cells),
        "year_from": year_from,
        "year_to": year_to,
        # The years actually counted (a missing bound filled in), or null
        # when every record is counted.
        "years_counted": list(years) if years else None,
        **part,
    }
