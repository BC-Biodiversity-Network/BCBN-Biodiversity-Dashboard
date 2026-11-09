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
    zero (Python's own rounding would round a half to the even digit)."""
    step = Decimal(1).scaleb(-decimals)
    return format(Decimal(number).quantize(step, rounding=ROUND_HALF_UP), "f")


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
    params = [("checklistKey", GBIF_CHECKLIST_KEY)]
    params += [("taxonKey", key) for key in keys]
    params += [("geometry", cell_wkt(cell)), ("occurrenceStatus", "PRESENT")]
    if years:
        params.append(("year", f"{years[0]},{years[1]}"))
    query = "&".join(f"{form_encode(name)}={form_encode(str(value))}" for name, value in params)
    return f"{GBIF_OCCURRENCE_SEARCH}?{query}"
