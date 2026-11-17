"""
area.py

The area search, for GET /api/area and its CSV download: draw a box on the
map and get everything in the hexagons under it, at one resolution (asked
for by Evan; the rules decided with Lucia in November 2026).

The box: west, south, east and north edges in degrees (longitude -180 to
180, latitude -90 to 90, west less than east, south less than north; a box
across the 180th meridian is not taken). Its sides follow the lines of
longitude and latitude.

The area text (read_area_shapes; the routes' area= parameter, see
area_request, which also still takes an old request's west, south, east
and north as one box): one or more shapes separated by ";", each "b:west,south,east,north" for a box or "p:lng,lat,lng,lat,..."
for a polygon (its first point not repeated at the end; a repeated one is
dropped). An old link's "west,south,east,north" with no prefix is one box.
Every shape is checked (numbers, ranges, a box's edges in order, a polygon
with at least 3 different points whose edges never cross or touch, nothing
across the 180th meridian) and the whole area may have at most
MAX_AREA_POINTS points. Each shape comes back as an AreaShape: its ring of
(longitude, latitude) corners and whether it was a box.

An area of several shapes (area_species takes a list of AreaShape): a
hexagon counts if it overlaps
any of the shapes, and counts once even when it is in two (area_cells).
Each shape must cover at least MIN_AREA_HEXAGONS average hexagons, and the
whole area may touch at most MAX_AREA_HEXAGONS hexagons (check_shape_sizes,
then area_cells). A box's area uses the box formula below, unchanged; a
polygon's uses the spherical polygon formula turf uses
(polygon_area_km2).

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
import re
from dataclasses import dataclass

import h3

from api.cells import DEFAULT_PAGE_SIZE, cards_gbif_keys, hexagons_list
from api.gbif_links import gbif_rings_search_url
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

# The most points a whole area may have, every shape together (a box counts
# as its 4 corners). The area travels in the page's address and, later, in
# the link that sends it to GBIF, so this depends on how long a GBIF link
# gbif.org accepts: GBIF's own API refused addresses over about 4,000
# characters in November 2026, and gbif.org itself is still to be checked
# by hand. It may change.
MAX_AREA_POINTS = 250

# The longest area text a request may give, in characters. 250 points as
# the page writes them (4 decimals) take about 4,500; this leaves room for
# more decimals, and is as long as a whole request may be for uvicorn (16
# KB), so it only stops a request that could not get through anyway.
AREA_TEXT_MAX_LENGTH = 16_000

# The radius of the sphere H3 measures areas on, in km (H3's authalic Earth
# radius), so a box's area compares with h3.average_hexagon_area.
EARTH_RADIUS_KM = 6371.007180918475

# How the area text separates its shapes and names each kind of shape.
SHAPE_SEPARATOR = ";"
BOX_PREFIX = "b:"
POLYGON_PREFIX = "p:"

# A number of degrees as the area text writes it: an optional sign, then
# digits with an optional decimal part ("-120.6", "50", "0.25", ".5"). No
# exponent, no "inf" or "nan", no spaces or "_" inside, so what the page
# writes is all that is read.
DEGREES = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)")

# How much of a value a message quotes back, at most, so one very long
# number cannot make a very long message.
QUOTED_LENGTH = 30

# The box's edges, in the order the area text gives them.
BOX_EDGES = ("west", "south", "east", "north")

# How far apart in longitude two corners joined by an edge may be. Over 180
# degrees, H3 (and GBIF) read the edge as going the other way round the
# Earth, across the 180th meridian, which an area may not do; at exactly
# 180 both ways are as short, so that is refused too.
MAX_EDGE_LONGITUDE_SPAN = 180


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


@dataclass(frozen=True)
class AreaShape:
    """One shape of an area, as read_area_shapes gives it. ring: its corners
    as (longitude, latitude) in degrees, in the order given, without the
    first corner repeated at the end (and without a corner given twice in a
    row); a box's ring runs west-south, east-south, east-north, west-north.
    is_box: whether the shape was given as a box. box: (west, south, east,
    north) for a box, else None. The numbers are the ones every check was
    run on, so the ring later steps get is the ring that was checked."""

    ring: tuple
    is_box: bool
    box: tuple | None = None


def shape_name(number, count, is_box):
    """Return how a message names a shape: "The box" or "The polygon" when
    the area has one shape, else "Shape 2 (a box)" and so on, counting
    from 1 in the order given."""
    kind = "box" if is_box else "polygon"
    return f"The {kind}" if count == 1 else f"Shape {number} (a {kind})"


def quoted(text):
    """Return a value for a message, in quotes, cut to QUOTED_LENGTH
    characters (with "...") when it is longer."""
    text = text.strip()
    return repr(text if len(text) <= QUOTED_LENGTH else text[:QUOTED_LENGTH] + "...")


def read_degrees(name, text):
    """Return one number of a shape as the number every later step uses (a
    Python float, the nearest one to what was written; -0 is read as 0),
    or stop with a clear message when it is empty or not a plain number of
    degrees."""
    stripped = text.strip()
    if not DEGREES.fullmatch(stripped):
        if not stripped:
            raise AreaRequestError(f"{name} has an empty value where a number of degrees should be.")
        hint = ' (separate shapes with ";")' if ":" in stripped else ""
        raise AreaRequestError(f"{name} has {quoted(stripped)} where a number of degrees should be{hint}.")
    return float(stripped) + 0.0


def check_range(name, what, value, text, limit):
    """Stop with a clear message when a longitude (limit 180) or latitude
    (limit 90) is outside -limit to limit degrees; what says which number
    it is, for example "west edge" or "point 3's longitude"."""
    if not -limit <= value <= limit:
        raise AreaRequestError(f"{name}: its {what} is {quoted(text)[1:-1]}, outside -{limit} to {limit} degrees.")


def split_shapes(text):
    """Return the area text's shapes as (is_box, the text after the prefix),
    in order. An old link's text (one part, no ":", not starting with a
    letter) is one box. Stops with a clear message for an empty text, an
    empty shape or a shape with no known prefix."""
    if text is None or not str(text).strip():
        raise AreaRequestError(
            'Give the area as area=..., one or more shapes separated by ";": b:west,south,east,north for a '
            "box, or p:lng,lat,lng,lat,... for a polygon.")
    parts = [part.strip() for part in str(text).strip().split(SHAPE_SEPARATOR)]
    if len(parts) == 1 and ":" not in parts[0] and not parts[0][:1].isalpha():
        # An old link (area=west,south,east,north): one box.
        return [(True, parts[0])]
    shapes = []
    for number, part in enumerate(parts, start=1):
        where = "The area" if len(parts) == 1 else f"Shape {number}"
        if not part:
            raise AreaRequestError(f'{where} is empty: remove the extra ";".')
        if part.startswith(BOX_PREFIX):
            shapes.append((True, part[len(BOX_PREFIX):]))
        elif part.startswith(POLYGON_PREFIX):
            shapes.append((False, part[len(POLYGON_PREFIX):]))
        else:
            prefix = part.split(":", 1)[0] + ":" if ":" in part else None
            start = (f"starts with {quoted(prefix)}, which is not a kind of shape" if prefix
                     else "does not say what kind of shape it is")
            raise AreaRequestError(f"{where} {start}: start it with b: for a box or p: for a polygon.")
    return shapes


def read_numbers(name, body, empty):
    """Return every number of a shape's text (after its prefix), read with
    read_degrees, or stop with the message empty when the text is empty."""
    if not body.strip():
        raise AreaRequestError(empty)
    texts = body.split(",")
    return texts, [read_degrees(name, text) for text in texts]


def read_box_shape(name, body):
    """Return a box shape from its text "west,south,east,north", checking
    there are 4 readable numbers in range, west less than east, south less
    than north, and that it is less than 180 degrees of longitude across
    (see MAX_EDGE_LONGITUDE_SPAN)."""
    texts, values = read_numbers(name, body, f"{name} is empty: give it as b:west,south,east,north.")
    if len(values) != 4:
        raise AreaRequestError(f"{name} needs 4 numbers, west,south,east,north; it has {len(values)}.")
    for edge, value, text in zip(BOX_EDGES, values, texts):
        check_range(name, f"{edge} edge", value, text, 180 if edge in ("west", "east") else 90)
    west, south, east, north = values
    if west >= east:
        raise AreaRequestError(f"{name}: its west edge ({quoted(texts[0])[1:-1]}) must be less than its east "
                               f"edge ({quoted(texts[2])[1:-1]}).")
    if south >= north:
        raise AreaRequestError(f"{name}: its south edge ({quoted(texts[1])[1:-1]}) must be less than its north "
                               f"edge ({quoted(texts[3])[1:-1]}).")
    if east - west >= MAX_EDGE_LONGITUDE_SPAN:
        raise across_meridian(name)
    return AreaShape(ring=((west, south), (east, south), (east, north), (west, north)), is_box=True,
                     box=(west, south, east, north))


def across_meridian(name):
    """The error for a shape with two joined corners 180 or more degrees of
    longitude apart, which H3 and GBIF would read as crossing the 180th
    meridian."""
    return AreaRequestError(
        f"{name} has two joined corners 180 or more degrees of longitude apart, so it would be read as "
        "crossing the 180th meridian. An area across that meridian is not taken.")


def read_polygon_points(name, body):
    """Return a polygon's points from its text "lng,lat,lng,lat,..." as
    [(number as given, longitude, latitude)], checking the numbers are
    readable, come in pairs and are in range. A last point that repeats
    the first is dropped."""
    texts, values = read_numbers(name, body, f"{name} is empty: give it as p:lng,lat,lng,lat,...")
    if len(values) % 2:
        raise AreaRequestError(f"{name} needs its numbers in pairs, longitude then latitude; it has "
                               f"{len(values)} {'number' if len(values) == 1 else 'numbers'}.")
    points = []
    for index in range(0, len(values), 2):
        number = index // 2 + 1
        check_range(name, f"point {number}'s longitude", values[index], texts[index], 180)
        check_range(name, f"point {number}'s latitude", values[index + 1], texts[index + 1], 90)
        points.append((number, values[index], values[index + 1]))
    if len(points) > 1 and points[-1][1:] == points[0][1:]:
        # The first point repeated at the end: the server closes the ring
        # itself, so the copy is dropped.
        points.pop()
    return points


def exact_corners(points):
    """Return the points' (longitude, latitude) as whole numbers, all
    multiplied by the same power of two, so the crossing checks compare
    exactly the numbers that are returned (a float is a whole number over
    a power of two, so nothing is rounded)."""
    ratios = [value.as_integer_ratio() for _, lng, lat in points for value in (lng, lat)]
    denominator = max(den for _, den in ratios)
    numbers = [num * (denominator // den) for num, den in ratios]
    return list(zip(numbers[0::2], numbers[1::2]))


def orientation(a, b, c):
    """Return which way c lies from the line a to b: positive to the left,
    negative to the right, 0 on the line (points as whole-number pairs)."""
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def on_segment(a, b, c):
    """True when c, already known to be on the line through a and b, lies
    between them (ends included)."""
    return min(a[0], b[0]) <= c[0] <= max(a[0], b[0]) and min(a[1], b[1]) <= c[1] <= max(a[1], b[1])


def segments_meet(a, b, c, d):
    """True when the segment a-b and the segment c-d have any point in
    common: crossing, touching at an end, or lying along each other."""
    o1, o2, o3, o4 = orientation(a, b, c), orientation(a, b, d), orientation(c, d, a), orientation(c, d, b)
    if ((o1 > 0 > o2) or (o1 < 0 < o2)) and ((o3 > 0 > o4) or (o3 < 0 < o4)):
        return True
    return ((o1 == 0 and on_segment(a, b, c)) or (o2 == 0 and on_segment(a, b, d))
            or (o3 == 0 and on_segment(c, d, a)) or (o4 == 0 and on_segment(c, d, b)))


def first_crossing(corners):
    """Return the first pair of edges of a closed ring (corners as whole-
    number pairs, the last joined back to the first) that meet anywhere
    but at the corner two neighbouring edges share, as (i, j): edge i runs
    from corner i to corner i + 1. None when there is no such pair. Two
    neighbouring edges meet wrongly only when one doubles back along the
    other."""
    count = len(corners)
    edges = [(corners[i], corners[(i + 1) % count]) for i in range(count)]
    # Each edge's bounding box, so pairs far apart are skipped quickly.
    boxes = [(min(a[0], b[0]), max(a[0], b[0]), min(a[1], b[1]), max(a[1], b[1])) for a, b in edges]
    for i in range(count):
        a, b = edges[i]
        for j in range(i + 1, count):
            box_i, box_j = boxes[i], boxes[j]
            if box_i[1] < box_j[0] or box_j[1] < box_i[0] or box_i[3] < box_j[2] or box_j[3] < box_i[2]:
                continue
            c, d = edges[j]
            if j == i + 1:
                # Neighbours sharing b (= c): wrong only if d lies back
                # along edge i, or a lies along edge j.
                if (orientation(a, b, d) == 0 and on_segment(a, b, d)) or (
                        orientation(c, d, a) == 0 and on_segment(c, d, a)):
                    return i, j
            elif i == 0 and j == count - 1:
                # Neighbours sharing a (= d), the ring's closing corner.
                if (orientation(a, b, c) == 0 and on_segment(a, b, c)) or (
                        orientation(c, d, b) == 0 and on_segment(c, d, b)):
                    return i, j
            elif segments_meet(a, b, c, d):
                return i, j
    return None


def read_polygon_shape(name, points):
    """Return a polygon shape from its points (read_polygon_points),
    dropping a point given twice in a row, and checking it has at least 3
    different points, does not cross the 180th meridian (see
    MAX_EDGE_LONGITUDE_SPAN) and has no edges that cross or touch."""
    kept = []
    for point in points:
        if not kept or kept[-1][1:] != point[1:]:
            kept.append(point)
    if len(kept) > 1 and kept[-1][1:] == kept[0][1:]:
        kept.pop()
    different = len({point[1:] for point in points})
    if different < 3:
        raise AreaRequestError(f"{name} has {different} different "
                               f"{'point' if different == 1 else 'points'}; a polygon needs at least 3.")
    for index, point in enumerate(kept):
        following = kept[(index + 1) % len(kept)]
        if abs(point[1] - following[1]) >= MAX_EDGE_LONGITUDE_SPAN:
            raise across_meridian(name)
    crossing = first_crossing(exact_corners(kept))
    if crossing:
        i, j = crossing
        raise AreaRequestError(
            f"{name} crosses or touches itself: {edge_text(kept, i)} meets {edge_text(kept, j)}. Draw it so "
            "that no two edges cross or touch.")
    return AreaShape(ring=tuple((lng, lat) for _, lng, lat in kept), is_box=False)


def edge_text(points, index):
    """Name an edge of a polygon by the points it joins, numbered as given:
    "the edge from point 3 to point 4" (the last edge goes back to the
    first point)."""
    start, end = points[index][0], points[(index + 1) % len(points)][0]
    return f"the edge from point {start} to point {end}"


def read_area_shapes(text):
    """
    Return the shapes of an area text (see the top of this file) as a list
    of AreaShape, in the order given. Stops with AreaRequestError and a
    message for visitors (it names shapes and points, never a resolution)
    when the text is empty, a shape is empty or has no known prefix, a
    number cannot be read or is out of range, a box's edges are out of
    order, a polygon has fewer than 3 different points or edges that cross
    or touch, a shape crosses the 180th meridian, or the area has more
    than MAX_AREA_POINTS points in all (a box counts as 4; a polygon's
    repeated closing point does not count). The point count comes before
    the crossing checks, the slowest part.

    Every number is read once as the float later steps use, and every check
    runs on those floats (exactly, see exact_corners), so a number written
    with more digits than a float holds cannot pass a check and then
    change into something the check would refuse.
    """
    parts = split_shapes(text)
    read = []
    for number, (is_box, body) in enumerate(parts, start=1):
        name = shape_name(number, len(parts), is_box)
        read.append((name, read_box_shape(name, body) if is_box else read_polygon_points(name, body)))
    total = sum(4 if isinstance(shape, AreaShape) else len(shape) for _, shape in read)
    if total > MAX_AREA_POINTS:
        raise AreaRequestError(
            f"The area has {total:,} points, more than the {MAX_AREA_POINTS:,} an area can have (a box counts "
            "as 4). Use fewer points or fewer shapes.")
    return [shape if isinstance(shape, AreaShape) else read_polygon_shape(name, shape) for name, shape in read]


def box_area_km2(box):
    """Return a box's area in km² on H3's sphere: the radius squared, times
    the width in radians, times the difference of the sines of its north
    and south edges (exact for a box whose sides follow longitude and
    latitude)."""
    west, south, east, north = box
    return (EARTH_RADIUS_KM ** 2 * math.radians(east - west)
            * (math.sin(math.radians(north)) - math.sin(math.radians(south))))


def polygon_area_km2(ring):
    """Return a polygon's area in km² on H3's sphere, from its ring of
    (longitude, latitude) corners in degrees (not closed; either way
    round). The formula is the one turf uses (@turf/area's ringArea), from
    Chamberlain and Duquette, "Some Algorithms for Polygons on a Sphere"
    (JPL Publication 07-03, 2007): half the radius squared times the sum,
    over every corner, of the difference in longitude (in radians) between
    the corner after it and the corner before it, times the sine of its
    latitude. turf uses the Earth's mean radius; this uses H3's
    (EARTH_RADIUS_KM) so the area compares with H3's average hexagon area.
    For a box's four corners it gives exactly box_area_km2's formula, but
    boxes keep that formula so their answers do not change."""
    count = len(ring)
    total = 0.0
    for index in range(count):
        before, corner, after = ring[index - 1], ring[index], ring[(index + 1) % count]
        total += (math.radians(after[0]) - math.radians(before[0])) * math.sin(math.radians(corner[1]))
    return abs(total) * EARTH_RADIUS_KM ** 2 / 2


def shape_area_km2(shape):
    """Return an AreaShape's area in km²: the box formula for a box (as
    before shapes came in), the spherical polygon formula for a polygon."""
    return box_area_km2(shape.box) if shape.is_box else polygon_area_km2(shape.ring)


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


def too_many_hexagons(count, res, name="The box", kind="box"):
    """The error for a shape (name, as shape_name gives it; kind, "box" or
    "polygon") over MAX_AREA_HEXAGONS hexagons (count, about) at a
    resolution, giving the hexagons' size rather than the resolution
    number, as it may be shown to visitors as it is. A box alone gets the
    same words as before shapes came in."""
    return AreaRequestError(
        f"{name} touches about {count:,.0f} hexagons of about {h3.average_hexagon_area(res, 'km^2'):,.1f} km², "
        f"more than the {MAX_AREA_HEXAGONS:,} an area search can count. Draw a smaller {kind} or use larger "
        "hexagons.")


def too_many_joined(count, res):
    """The error for an area whose shapes together touch more than
    MAX_AREA_HEXAGONS hexagons: count is how many were found when the
    search stopped (at least that many), with the hexagons' size."""
    return AreaRequestError(
        f"The area's shapes together touch at least {count:,} hexagons of about "
        f"{h3.average_hexagon_area(res, 'km^2'):,.1f} km², more than the {MAX_AREA_HEXAGONS:,} an area search "
        "can count. Use smaller or fewer shapes, or larger hexagons.")


def check_shape_sizes(shapes, res):
    """Stop with a clear message when a shape covers fewer than
    MIN_AREA_HEXAGONS average hexagons, or would touch more than
    MAX_AREA_HEXAGONS hexagons on its own judging by its area (both checked
    before any hexagon is found, so a huge shape costs nothing), each shape
    named as shape_name names it. Returns each shape's area in km², in
    order. A single box gets exactly check_box_size's checks and words."""
    if len(shapes) == 1 and shapes[0].is_box:
        return [check_box_size(shapes[0].box, res)]
    average = h3.average_hexagon_area(res, "km^2")
    smallest = minimum_area_km2(res)
    areas = []
    for number, shape in enumerate(shapes, start=1):
        name = shape_name(number, len(shapes), shape.is_box)
        kind = "box" if shape.is_box else "polygon"
        area = shape_area_km2(shape)
        if area < smallest:
            raise AreaRequestError(
                f"{name} is about {area:,.1f} km², smaller than the minimum of {smallest:,.1f} km² for "
                f"hexagons of about {average:,.1f} km² ({MIN_AREA_HEXAGONS} times their average area). Draw a "
                f"larger {kind}.")
        if area / average > MAX_AREA_HEXAGONS:
            raise too_many_hexagons(area / average, res, name, kind)
        areas.append(area)
    return areas


def box_cells(box, res):
    """Return every hexagon at resolution res that overlaps the box, even by
    a corner, sorted: the h3 package's "overlap" search, the same as
    polygonToCellsExperimental with containmentOverlapping in h3-js."""
    west, south, east, north = box
    shape = h3.LatLngPoly([(south, west), (south, east), (north, east), (north, west)])
    return sorted(h3.h3shape_to_cells_experimental(shape, res, contain="overlap"))


def shape_cells(shape, res):
    """Return the set of hexagons at resolution res that overlap one shape,
    even by a corner: the h3 package's "overlap" search, as for a box
    (box_cells; a box's ring is its corners in the same order)."""
    polygon = h3.LatLngPoly([(lat, lng) for lng, lat in shape.ring])
    return set(h3.h3shape_to_cells_experimental(polygon, res, contain="overlap"))


def area_cells(shapes, res):
    """Return every hexagon at resolution res that overlaps any of the
    shapes, each once, sorted. Shapes are added one at a time and the
    search stops with a clear message as soon as the joined set passes
    MAX_AREA_HEXAGONS (a single box gets the same message as before, with
    its exact count)."""
    cells = set()
    for shape in shapes:
        cells |= shape_cells(shape, res)
        if len(cells) > MAX_AREA_HEXAGONS:
            if len(shapes) == 1:
                name = shape_name(1, 1, shape.is_box)
                raise too_many_hexagons(len(cells), res, name, "box" if shape.is_box else "polygon")
            raise too_many_joined(len(cells), res)
    return sorted(cells)


def area_request(area, west, south, east, north):
    """Return the shapes a request asks for, as a list of AreaShape: the
    area text (area=, read by read_area_shapes), or an old request's box
    edges (west, south, east and north, read by box_shape exactly as
    before). Stops with a clear message when a request gives both, or
    neither (a request with only some of the edges keeps read_box's own
    message about the missing one)."""
    edges = (west, south, east, north)
    if area is not None and any(edge is not None for edge in edges):
        raise AreaRequestError(
            "Give the area either as area=... or as a box's west, south, east and north edges, not both.")
    if area is not None:
        return read_area_shapes(area)
    if all(edge is None for edge in edges):
        raise AreaRequestError(
            'Give the area as area=..., one or more shapes separated by ";": b:west,south,east,north for a box, '
            "or p:lng,lat,lng,lat,... for a polygon (or, as before, a box as west=, south=, east= and north=, "
            "in degrees).")
    return box_shape(west, south, east, north)


def shape_bounds(shape):
    """Return the box around a shape, {"west", "south", "east", "north"} in
    degrees: a box's own edges, a polygon's smallest and largest longitude
    and latitude."""
    longitudes = [lng for lng, _ in shape.ring]
    latitudes = [lat for _, lat in shape.ring]
    return {"west": min(longitudes), "south": min(latitudes), "east": max(longitudes), "north": max(latitudes)}


def shapes_summary(shapes):
    """Return what the answer says about each shape, in order: its type
    ("box" or "polygon"), how many corners it has (4 for a box) and the box
    around it (shape_bounds)."""
    return [{"type": "box" if shape.is_box else "polygon", "point_count": len(shape.ring),
             "bounds": shape_bounds(shape)} for shape in shapes]


def box_shape(west, south, east, north):
    """Return the box the routes give (its four edges as text, read and
    checked by read_box, with read_box's messages) as a list of one
    AreaShape, so the area search can take it until the routes take the
    area text."""
    box = read_box(west, south, east, north)
    w, s, e, n = box
    return [AreaShape(ring=((w, s), (e, s), (e, n), (w, n)), is_box=True, box=box)]


def area_species(data, shapes, res, year_from=None, year_to=None, q=None, offset=0,
                 limit=DEFAULT_PAGE_SIZE, rank=None, rank_given=None, statuses=None, sort=None):
    """Return the answer for the hexagons under an area (see the top of this
    file): shapes is a list of AreaShape (area_request). The answer gives
    the box (for an area of one box, else None) and the shapes' area (the
    sum of each shape's area, so overlaps count twice), the resolution, how
    many hexagons the area touches, how many shapes it has and what each is
    (shapes_summary), the GBIF search for the whole area (every species,
    one geometry per shape), the totals for the years and one page of
    species (offset, limit; limit None for all of them, for the CSV
    download), each with its GBIF link. res comes as given in the address.
    Raises
    AreaRequestError (and HexesRequestError for a bad year range) for a bad
    area or resolution, ListRequestError for an unknown sort or status,
    QueryTooShort for a name filter too short, RankRequestError for a
    request that does not name a rank, and UnknownRank for a rank with no
    species."""
    res = read_resolution(res)
    area = sum(check_shape_sizes(shapes, res))
    sort, statuses = check_list_options(sort, statuses)
    years = year_range(year_from, year_to, data.meta["first_year"], data.meta["last_year"])
    hidden = hidden_species(data.settings, res)
    box = shapes[0].box if len(shapes) == 1 and shapes[0].is_box else None

    cursor = data.cursor()
    path = require_rank(data, cursor, rank, rank_given or {}) if rank else None
    cells = area_cells(shapes, res)
    _, part = hexagons_list(data, cursor, res, cells, years, hidden, q, statuses, sort, offset, limit, path)

    # Each species' GBIF search inside the area's shapes (one geometry per
    # shape; for one box, the box's own link), for the years counted.
    keys = cards_gbif_keys(cursor, [result["specieskey"] for result in part["results"]])
    rings = [shape.ring for shape in shapes]
    for result in part["results"]:
        result["gbif_link"] = gbif_rings_search_url(keys[result["specieskey"]], rings, years)

    return {
        "box": dict(zip(("west", "south", "east", "north"), box)) if box else None,
        "res": res,
        "box_area_km2": round(area, 1),
        "minimum_area_km2": round(minimum_area_km2(res), 1),
        # How many hexagons at this resolution overlap the area: the front
        # end finds the same ones with h3-js to outline them.
        "hexagon_count": len(cells),
        # The area's shapes: how many, and each one's type, corners and the
        # box around it, in the order given.
        "shape_count": len(shapes),
        "shapes": shapes_summary(shapes),
        # GBIF's search for every species in the area's shapes (one
        # geometry per shape), for the years counted: the panel's "See
        # these records on GBIF" (gbifAreaSearchUrl in gbifLinks.js gives
        # the same link).
        "area_gbif_link": gbif_rings_search_url([], rings, years),
        "year_from": year_from,
        "year_to": year_to,
        # The years actually counted (a missing bound filled in), or null
        # when every record is counted.
        "years_counted": list(years) if years else None,
        **part,
    }
