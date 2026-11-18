"""
gbif_links.py

Links to GBIF's own occurrence search for some species inside one H3
hexagon, built exactly as the front end builds them
(frontend/src/gbifLinks.js), so the hexagon CSV download gives the same
link as the hexagon panel for the same hexagon, keys and years.

The link names: the Catalogue of Life checklist (our species keys are ids
in it), each key (a card's own key and every key merged into it), the
hexagon's outline, PRESENT records only (as in our download), and the year
range when there is one. The parameters come in the same order as the front
end's, and are written the way a browser's URLSearchParams writes them, so
the two links are the same text, not only the same search.

The outline: the hexagon's corners from the h3 package (the same H3 library
the front end's h3-js is made from), longitude then latitude, counter-
clockwise, the first corner repeated at the end, each number with six
decimals rounded as JavaScript's toFixed rounds them.

The area search (area.py) links to the same search with its box as the
geometry instead of a hexagon: a 5-point polygon, west-south, east-south,
east-north, west-north and west-south again, which runs counter-clockwise
and is closed, as GBIF asks.

An area of several shapes gets one geometry parameter per shape
(gbif_rings_search_url): GBIF searches the union of several geometry
parameters, where a MULTIPOLYGON of overlapping shapes is refused (tested on
GBIF's API in November 2026). Each ring is written closed and
counter-clockwise, turned round when it was given the other way, keeping
its first corner first; a box's ring is already counter-clockwise, so a
single box gives exactly the box's link. ringsWkt and gbifAreaSearchUrl in
gbifLinks.js do the same, and a test runs both.
"""

from decimal import ROUND_HALF_UP, Decimal

import h3

# GBIF's occurrence search page.
GBIF_OCCURRENCE_SEARCH = "https://www.gbif.org/occurrence/search"

# The Catalogue of Life checklist in GBIF's registry (see gbifLinks.js).
GBIF_CHECKLIST_KEY = "7ddf754f-d193-4cc9-b351-99906754a03b"

# How many decimals of a degree each corner keeps: six is about 0.1 m.
DECIMALS = 6

# The characters a browser leaves as they are in a query parameter; a space
# becomes "+" and everything else is written as %XX (capital letters).
FORM_SAFE = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789*-._")


def to_fixed(number, decimals=DECIMALS):
    """Write a number with exactly this many decimals, as JavaScript's
    toFixed does: from the number's exact value, a half rounded away from
    zero (Python's own rounding would round a half to the even digit).
    Zero is written without a sign, as toFixed writes -0 ("0.000000")."""
    step = Decimal(1).scaleb(-decimals)
    return format(Decimal(number if number != 0 else 0).quantize(step, rounding=ROUND_HALF_UP), "f")


def cell_wkt(cell):
    """Return a hexagon's outline as WKT, the way GBIF takes a geometry and
    the way gbifLinks.js writes it: POLYGON((lng lat,...)) with the ring
    closed."""
    corners = list(h3.cell_to_boundary(cell))
    ring = corners + corners[:1]
    return "POLYGON((" + ",".join(f"{to_fixed(lng)} {to_fixed(lat)}" for lat, lng in ring) + "))"


def form_encode(text):
    """Write text as a browser's URLSearchParams writes a name or value."""
    written = []
    for byte in text.encode("utf-8"):
        character = chr(byte)
        if character in FORM_SAFE:
            written.append(character)
        elif character == " ":
            written.append("+")
        else:
            written.append(f"%{byte:02X}")
    return "".join(written)


def gbif_cell_search_url(keys, cell, years=None):
    """Return the address of a GBIF occurrence search for these species
    keys inside one hexagon, PRESENT records only, for a year range
    ((from, to), both included) or every year (None): the same text
    gbifCellSearchUrl in gbifLinks.js gives."""
    return gbif_search_url(keys, cell_wkt(cell), years)


def box_wkt(west, south, east, north):
    """Return a box as WKT for GBIF: a closed 5-point polygon running
    counter-clockwise (west-south, east-south, east-north, west-north,
    west-south), longitude first, six decimals as for a hexagon."""
    corners = [(west, south), (east, south), (east, north), (west, north), (west, south)]
    return "POLYGON((" + ",".join(f"{to_fixed(lng)} {to_fixed(lat)}" for lng, lat in corners) + "))"


def gbif_box_search_url(keys, box, years=None):
    """Return the address of a GBIF occurrence search for these species
    keys inside a box (west, south, east, north), PRESENT records only, for
    a year range ((from, to)) or every year (None): the hexagon's search
    with the box as its geometry."""
    return gbif_search_url(keys, box_wkt(*box), years)


def ring_is_clockwise(ring):
    """True when a ring of (longitude, latitude) corners (not closed) runs
    clockwise on a map with longitude across and latitude up: its signed
    area (the shoelace sum, added up in this order, as ringIsClockwise in
    gbifLinks.js does, so both sides get the same sign) is below zero."""
    total = 0.0
    for index in range(len(ring)):
        lng, lat = ring[index]
        next_lng, next_lat = ring[(index + 1) % len(ring)]
        total += lng * next_lat - next_lng * lat
    return total < 0


def ring_wkt(ring):
    """Return one ring of (longitude, latitude) corners (not closed, as an
    AreaShape holds it) as WKT for GBIF: POLYGON((lng lat,...)), turned
    counter-clockwise if it was clockwise (keeping its first corner first),
    closed by repeating the first corner, six decimals as for a hexagon."""
    corners = list(ring)
    if ring_is_clockwise(corners):
        corners = corners[:1] + corners[:0:-1]
    corners.append(corners[0])
    return "POLYGON((" + ",".join(f"{to_fixed(lng)} {to_fixed(lat)}" for lng, lat in corners) + "))"


def rings_wkt(rings):
    """Return the GBIF geometry values of an area's shapes (rings as
    ring_wkt takes them, for example [shape.ring for shape in shapes]):
    one POLYGON per shape, in the order given."""
    return [ring_wkt(ring) for ring in rings]


def gbif_rings_search_url(keys, rings, years=None):
    """Return the address of a GBIF occurrence search for these species
    keys inside an area of one or more shapes (rings, see rings_wkt), one
    geometry parameter per shape, PRESENT records only, for a year range
    ((from, to)) or every year (None). For a single box it is exactly
    gbif_box_search_url's link."""
    return gbif_search_url(keys, rings_wkt(rings), years)


def gbif_area_link_maker(rings, years=None):
    """Return a function that writes the GBIF search for some species keys
    inside an area of shapes (rings, see rings_wkt) for the years: the same
    text as gbif_rings_search_url(keys, rings, years), but with the area's
    outline written and encoded once, so the CSV of an area does not write a
    detailed outline again for each of its thousands of rows. The function
    takes the keys (a card's key and every key merged into it)."""
    params = [("geometry", one) for one in rings_wkt(rings)] + [("occurrenceStatus", "PRESENT")]
    if years:
        params.append(("year", f"{years[0]},{years[1]}"))
    tail = "&".join(f"{form_encode(name)}={form_encode(str(value))}" for name, value in params)
    head = f"{GBIF_OCCURRENCE_SEARCH}?{form_encode('checklistKey')}={form_encode(GBIF_CHECKLIST_KEY)}"

    def link(keys):
        """Return the search for these keys inside the area."""
        return head + "".join(f"&taxonKey={form_encode(str(key))}" for key in keys) + "&" + tail

    return link


def gbif_search_url(keys, geometry, years=None):
    """Write a GBIF occurrence search address, the one place the links are
    made: the Catalogue of Life checklist, one taxonKey per key, the area
    as WKT (one geometry, or a list of them, one parameter each), PRESENT
    records only, and the years when given, in the same order and written
    the same way as gbifLinks.js does."""
    geometries = [geometry] if isinstance(geometry, str) else list(geometry)
    params = [("checklistKey", GBIF_CHECKLIST_KEY)]
    params += [("taxonKey", key) for key in keys]
    params += [("geometry", one) for one in geometries]
    params += [("occurrenceStatus", "PRESENT")]
    if years:
        params.append(("year", f"{years[0]},{years[1]}"))
    query = "&".join(f"{form_encode(name)}={form_encode(str(value))}" for name, value in params)
    return f"{GBIF_OCCURRENCE_SEARCH}?{query}"
