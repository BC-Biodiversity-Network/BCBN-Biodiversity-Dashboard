"""
The record part of the geography prompts (geo_prompt_v3.py, and the earlier
versions 1 and 2).

Every version of the geography prompt shows the model the record in the same
way, so the versions can be compared on their instructions alone. Every field
is given exactly as it is in the harvest: nothing is rewritten, summarised,
corrected or translated. Where a field needs explaining, a separate line
beginning "Note from us:" is added next to it. The boxes and points are given
as their raw numbers; what the BC filter's rules worked out about them (how
much of a box lies in BC, whether a point does, and whether the numbers look
swapped or a minus sign looks missing) is added as a note marked as our own
calculation. The numbers themselves are never corrected.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# The BC outline and box measures live with the BC filter's rules, one
# folder up.
sys.path.insert(0, os.path.dirname(HERE))
from bc_filter_rules import measure_box  # noqa: E402


def raw_number(value):
    """
    Write a coordinate exactly as the harvest holds it: Python's shortest
    form that reads back as the same number (so -123.5, not -123.50000).
    """
    return repr(float(value))


def present(value):
    """Return True if a field holds something to show (not missing, not empty)."""
    if value is None:
        return False
    if isinstance(value, float) and value != value:  # a missing number (NaN)
        return False
    if hasattr(value, "__len__"):
        return len(value) > 0
    return True


def percent(share):
    """Write a share from 0 to 1 as a percentage, saying "less than 1%" rather than rounding a small overlap to 0%."""
    if share == 0:
        return "0%"
    if share < 0.01:
        return "less than 1%"
    return f"{share:.0%}"


def box_lines(number, box, shape):
    """
    Return the lines for one bounding box: its four raw numbers, then our
    notes. The numbers are never corrected; a box whose numbers look swapped
    gets a note saying so.
    """
    w, e, s, n = box["w"], box["e"], box["s"], box["n"]
    lines = [f"Bounding box {number}: westBoundLongitude {raw_number(w)}, eastBoundLongitude "
             f"{raw_number(e)}, southBoundLatitude {raw_number(s)}, northBoundLatitude {raw_number(n)}"]
    if s > n:
        lines.append("Note from us: in this box the south latitude is greater than the north "
                     "latitude, so the two look swapped. The numbers are shown as recorded.")
    if w > e:
        if w > 0 > e:
            lines.append("Note from us: in this box the west longitude is greater than the east "
                         "longitude; read as recorded, the box crosses the 180 degree line. The "
                         "numbers are shown as recorded.")
        else:
            lines.append("Note from us: in this box the west longitude is greater than the east "
                         "longitude, so the two look swapped. The numbers are shown as recorded.")
    measured = measure_box(box, shape)
    if measured is None:
        lines.append("Note from us (our own calculation, not part of the record): this box has "
                     "no area; it is a single point or a line.")
    else:
        share, cover, size = measured
        size_text = f"; the box is about {size:.1f} times the size of British Columbia" if size >= 1 else ""
        lines.append(f"Note from us (our own calculation, not part of the record): "
                     f"{percent(share)} of this box lies in British Columbia, and it covers "
                     f"{percent(cover)} of British Columbia{size_text}.")
    return lines


def point_lines(number, lon, lat, shape):
    """
    Return the lines for one point: its raw numbers, then our note on
    whether it falls in BC. A point that would fall in BC with a minus sign
    added to its longitude gets a note that the sign is probably missing; the
    number itself is not changed.
    """
    from shapely.geometry import Point

    lines = [f"Point {number}: pointLongitude {raw_number(lon)}, pointLatitude {raw_number(lat)}"]
    if shape.outline.covers(Point(lon, lat)):
        verdict = "this point falls inside British Columbia."
    elif shape.outline.covers(Point(-lon, lat)):
        verdict = ("this point falls outside British Columbia, but it would fall inside British "
                   "Columbia if its longitude were negative, so the minus sign is probably missing. "
                   "The number is shown as recorded.")
    else:
        verdict = "this point falls outside British Columbia."
    lines.append("Note from us (our own calculation, not part of the record): " + verdict)
    return lines


def record_text(record, shape):
    """
    Return the record part of a geography prompt: "Record to read:" and then
    the record field by field, each with our notes where it needs one.

    record is a dict with id, title, subjects, abstract, places, publisher,
    points and boxes as the harvest stores them. shape is BC's outline
    (bc_filter_rules.BcShape), used only for our notes on boxes and points.
    Fields that are empty are left out.
    """
    lines = ["Record to read:", ""]
    if present(record.get("title")):
        lines.append(f"Title: {record['title']}")
    if present(record.get("subjects")):
        lines.append("Keywords: " + "; ".join(str(s) for s in record["subjects"]))
    if present(record.get("abstract")):
        lines.append(f"Abstract: {record['abstract']}")
    if present(record.get("places")):
        lines.append("Place-name field: " + "; ".join(str(p) for p in record["places"]))
        lines.append("Note from us: this is the record's place-name field (geoLocationPlace). "
                     "Some repositories fill in the institution's address here rather than where "
                     "the data was collected.")
    if present(record.get("publisher")):
        lines.append(f"Publisher: {record['publisher']}")
        lines.append("Note from us: this is the record's publisher field, the organisation that "
                     "published the data.")
    lines.append(f"Record identifier: {record['id']}")
    lines.append("Note from us: this is the identifier the catalogue gives the record. The part "
                 "after \"oai:\" names the repository the record was harvested from.")
    boxes = list(record.get("boxes")) if present(record.get("boxes")) else []
    points = list(record.get("points")) if present(record.get("points")) else []
    if boxes or points:
        lines.append("Note from us: a bounding box is four numbers in decimal degrees. "
                     "westBoundLongitude and eastBoundLongitude are longitudes (negative means "
                     "west of Greenwich); southBoundLatitude and northBoundLatitude are latitudes "
                     "(negative means south of the equator). A point is one longitude and one "
                     "latitude.")
    for number, box in enumerate(boxes, start=1):
        lines += box_lines(number, box, shape)
    for number, point in enumerate(points, start=1):
        lines += point_lines(number, point["lon"], point["lat"], shape)
    return "\n".join(lines)
