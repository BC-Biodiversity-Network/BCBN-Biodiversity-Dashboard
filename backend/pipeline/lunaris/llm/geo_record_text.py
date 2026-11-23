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
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# The BC outline and box measures live with the BC filter's rules, one
# folder up.
sys.path.insert(0, os.path.dirname(HERE))
from bc_filter_rules import make_box, measure_box, measure_pieces, repair_box  # noqa: E402


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


# How close to 1 a box's share in BC must be to count as the whole box: far
# below any real difference, only enough to absorb rounding in the area sums.
WHOLE_BOX = 1e-9


def inside_and_outside(share):
    """
    Return how a box's share in BC and the rest of it are written, as two
    texts whose numbers add up to 100%:

      share exactly 0        "0%"            and "100%"
      share 1 (within        "100%"          and "0%"
        WHOLE_BOX of it)
      share under 1%         "less than 1%"  and "more than 99%"
      share over 99% that    "more than 99%" and "less than 1%"
        would round to 100%
      otherwise              "N%"            and "(100 - N)%", N being the
                             share rounded as percent() rounds it

    The inside text is the same as percent(share) except at the top end, where
    a box not wholly in BC is never shown as 100% in BC. A box wholly in BC
    can measure a hair under 1 (0.9999999999999999, from rounding in the
    area sums), so a share within WHOLE_BOX of 1 counts as the whole box.
    """
    if share == 0:
        return "0%", "100%"
    if share >= 1 - WHOLE_BOX:
        return "100%", "0%"
    if share < 0.01:
        return "less than 1%", "more than 99%"
    inside = int(f"{share:.0%}"[:-1])
    if inside == 100:
        return "more than 99%", "less than 1%"
    return f"{inside}%", f"{100 - inside}%"


def box_lines(number, box, shape, outside=False):
    """
    Return the lines for one bounding box: its four raw numbers, then our
    notes. The numbers are never corrected; a box whose numbers look swapped
    gets a note saying so. With outside True (pass 2, version C4), the note
    also says how much of the box lies outside BC (inside_and_outside);
    by default it says only how much lies in BC, as pass 1 shows it.
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
        if outside:
            inside_text, outside_text = inside_and_outside(share)
            lines.append(f"Note from us (our own calculation, not part of the record): "
                         f"{inside_text} of this box lies in British Columbia and {outside_text} lies outside "
                         f"it, and it covers {percent(cover)} of British Columbia{size_text}.")
        else:
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


def record_text(record, shape, box_outside=False):
    """
    Return the record part of a geography prompt: "Record to read:" and then
    the record field by field, each with our notes where it needs one.

    record is a dict with id, title, subjects, abstract, places, publisher,
    points and boxes as the harvest stores them. shape is BC's outline
    (bc_filter_rules.BcShape), used only for our notes on boxes and points.
    Fields that are empty are left out. box_outside True makes each box note
    also say how much of the box lies outside BC (pass 2, version C4); the
    default keeps the note as pass 1 shows it.
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
        lines += box_lines(number, box, shape, outside=box_outside)
    for number, point in enumerate(points, start=1):
        lines += point_lines(number, point["lon"], point["lat"], shape)
    return "\n".join(lines)


# --- DataONE --------------------------------------------------------------
#
# The record part of the geography prompts for DataONE records (prompt
# variant "dataone", geo_prompt_variants.py; Lucia's decisions of
# 2026-11-23). record_text above, the Lunaris layout, is not changed.
#
# What differs from Lunaris:
#   - the identifier note says only that the repository gave it (no "oai:"),
#     and a Repository line names the DataONE member repository;
#   - the place fields are DataONE's namedLocation, placeKey and site, with
#     site values that are only boilerplate left out (SITE_BOILERPLATE);
#     authors and organisations are not shown;
#   - a box with no area is shown as a point, so the point notes apply
#     (including the possibly missing minus sign);
#   - a box with west greater than east gets both readings, crossing the
#     180 degree meridian and west and east swapped, each with its share in
#     BC; a latitude beyond 90, a longitude equal to a latitude, and a box
#     that touches BC only with its longitude signs changed get a note;
#   - an abstract ending with "..." (cut by DataONE) gets a note;
#   - with several boxes, the boxes and points that touch BC are listed in
#     full, and the rest only as a count and their overall extent.
# A note that only explains a field (the place fields, the repository, the
# identifier, what a box's numbers mean, a point given as a box) starts with
# FIELD_NOTE, the short prefix Lunaris uses for such notes; a note we worked
# out (shares in BC, the readings of a west > east box, odd numbers, boxes
# that touch BC only with their signs changed, a cut abstract, the boxes not
# listed) starts with DATAONE_NOTE (Lucia, 2026-11-23).

DATAONE_NOTE = "Note from us (our own calculation, not part of the record): "
FIELD_NOTE = "Note from us: "
CUT_MARK = "..."

# Site values that say nothing about a place, left out of the record text.
# Each is matched against the whole value (case and runs of spaces ignored).
SITE_BOILERPLATE = {
    "No geographic description provided.": re.compile(r"no geographic description provided\.?", re.IGNORECASE),
    "See range references listed in Sources.": re.compile(r"see range references listed in sources\.?",
                                                          re.IGNORECASE),
    "Dataset geographic description information is unavailable. {...}": re.compile(
        r"dataset geographic description information is unavailable\.?( \{.*\})?", re.IGNORECASE),
    "Site information unavailable": re.compile(r"site information unavailable\.?", re.IGNORECASE),
    "Unknown": re.compile(r"unknown", re.IGNORECASE),
    "NA": re.compile(r"n/?a", re.IGNORECASE),
}


def boilerplate_of(value):
    """Return the name of the SITE_BOILERPLATE pattern a site value is, or None if it says something."""
    text = " ".join(str(value).split())
    for name, pattern in SITE_BOILERPLATE.items():
        if pattern.fullmatch(text):
            return name
    return None


def field_values(value, drop_boilerplate=False):
    """
    Return a place field's values as a list of texts, each as harvested,
    without exact repeats (in their first order) and without empty values;
    with drop_boilerplate, also without SITE_BOILERPLATE values.
    """
    if not present(value):
        return []
    values = [value] if isinstance(value, str) else list(value)
    kept = []
    for item in values:
        text = str(item)
        if not text.strip() or text in kept or (drop_boilerplate and boilerplate_of(text)):
            continue
        kept.append(text)
    return kept


def latitudes(box):
    """Return a box's south and north edges in order, kept within 85 degrees (as repair_box keeps them)."""
    south, north = sorted((box["s"], box["n"]))
    return max(south, -85.0), min(north, 85.0)


def is_point_box(box):
    """Return True if a box has no area: its west equals its east or its south equals its north."""
    return box["w"] == box["e"] or box["s"] == box["n"]


def crossing_pieces(box):
    """Return a west > east box read as crossing the 180 degree meridian, as rectangles (none if it has no width)."""
    south, north = latitudes(box)
    if south >= north:
        return []
    pieces = []
    if box["w"] < 180.0:
        pieces.append(make_box(box["w"], south, 180.0, north))
    if box["e"] > -180.0:
        pieces.append(make_box(-180.0, south, box["e"], north))
    return pieces


def swapped_pieces(box):
    """Return a west > east box read with west and east swapped, as rectangles."""
    south, north = latitudes(box)
    if south >= north:
        return []
    return [make_box(box["e"], south, box["w"], north)]


def measure_reading(box, pieces, shape):
    """
    Measure one reading of a box (its rectangles) against BC, as measure_box
    does: (share, cover, size), (0, 0, 0) for a box wholly beyond 85 degrees
    north or south, or None for a reading with no area.
    """
    south, north = sorted((box["s"], box["n"]))
    if south >= 85.0 or north <= -85.0:
        return 0.0, 0.0, 0.0
    return measure_pieces(pieces, shape)


def readings(box):
    """
    Return a box's readings as (pieces, ...) lists: for a west > east box,
    crossing the 180 degree meridian and west and east swapped; for any
    other box, the one repair_box gives.
    """
    if box["w"] > box["e"]:
        return [crossing_pieces(box), swapped_pieces(box)]
    return [repair_box(box["w"], box["s"], box["e"], box["n"])]


def touches_bc(box, shape):
    """
    Return True if a box or point touches BC: a point (a box with no area,
    at its middle) that BC's outline covers, or a box that meets BC's
    outline in any of its readings.
    """
    from shapely.geometry import Point

    if is_point_box(box):
        return shape.outline.covers(Point((box["w"] + box["e"]) / 2, (box["s"] + box["n"]) / 2))
    return any(piece.intersects(shape.outline) for pieces in readings(box) for piece in pieces)


def sign_flipped(box):
    """Return a box with the signs of its longitudes changed: new west = -east, new east = -west (0 stays 0, not -0)."""
    return {"w": 0.0 - box["e"], "e": 0.0 - box["w"], "s": box["s"], "n": box["n"]}


def share_sentence(measured, outside):
    """
    Return the sentence our note gives a box's measures, worded as box_lines
    words it (with outside, also the share outside BC), or that it has no
    area. measured is (share, cover, size) or None.
    """
    if measured is None:
        return "this box has no area."
    share, cover, size = measured
    size_text = f"; the box is about {size:.1f} times the size of British Columbia" if size >= 1 else ""
    if outside:
        inside_text, outside_text = inside_and_outside(share)
        return (f"{inside_text} of this box lies in British Columbia and {outside_text} lies outside it, and it "
                f"covers {percent(cover)} of British Columbia{size_text}.")
    return f"{percent(share)} of this box lies in British Columbia, and it covers {percent(cover)} of British Columbia{size_text}."


def degrees(value):
    """Write a number of degrees briefly, rounded to two decimals: 355, 12.5, 0.75."""
    return f"{round(value, 2):g}"


def odd_number_notes(box):
    """
    Return our notes on a box's numbers: a latitude beyond 90 degrees, a
    longitude beyond 180 degrees, and a longitude equal to a latitude (not
    when the value is 0, which is common and says nothing).
    """
    notes = []
    if max(abs(box["s"]), abs(box["n"])) > 90:
        notes.append(DATAONE_NOTE + "a latitude here is beyond 90 degrees north or south, which is not possible, "
                     "so latitude and longitude may be swapped. The numbers are shown as recorded.")
    if max(abs(box["w"]), abs(box["e"])) > 180:
        notes.append(DATAONE_NOTE + "a longitude here is beyond 180 degrees east or west, which may be a mistake "
                     "in the record. The numbers are shown as recorded.")
    equal = sorted({lon for lon in (box["w"], box["e"]) if lon in (box["s"], box["n"]) and lon != 0})
    if equal:
        notes.append(DATAONE_NOTE + f"a longitude here equals a latitude ({', '.join(raw_number(v) for v in equal)}), "
                     "which may be a mistake in the record. The numbers are shown as recorded.")
    return notes


def dataone_box_lines(number, box, shape, outside=False):
    """
    Return the lines for one DataONE box with an area: its four raw numbers,
    then our notes: south greater than north, the two readings of a west >
    east box with the share in BC for each (otherwise the box's share, as
    box_lines gives it), odd numbers, and a box that touches BC only with
    its longitude signs changed. The numbers are never corrected.
    """
    w, e, s, n = box["w"], box["e"], box["s"], box["n"]
    lines = [f"Bounding box {number}: westBoundLongitude {raw_number(w)}, eastBoundLongitude "
             f"{raw_number(e)}, southBoundLatitude {raw_number(s)}, northBoundLatitude {raw_number(n)}"]
    if s > n:
        lines.append(DATAONE_NOTE + "in this box the south latitude is greater than the north latitude, so the "
                     "two look swapped. The numbers are shown as recorded.")
    if w > e:
        crossing_width, swapped_width = 360.0 - (w - e), w - e
        crossing_text = (f"the box spans {degrees(crossing_width)}° of longitude" if crossing_width > 0
                         else "the box has no width")
        lines.append(DATAONE_NOTE + "in this box the west longitude is greater than the east longitude. Read as "
                     f"crossing the 180° meridian, {crossing_text}; read with west and east swapped, it spans "
                     f"{degrees(swapped_width)}°. The numbers are shown as recorded.")
        for label, pieces in (("read as crossing the 180° meridian", crossing_pieces(box)),
                              ("read with west and east swapped", swapped_pieces(box))):
            lines.append(DATAONE_NOTE + f"{label}, " + share_sentence(measure_reading(box, pieces, shape), outside))
    else:
        lines.append(DATAONE_NOTE + share_sentence(measure_box(box, shape), outside))
    lines += odd_number_notes(box)
    flipped = sign_flipped(box)
    if not touches_bc(box, shape) and touches_bc(flipped, shape):
        lines.append(DATAONE_NOTE + "this box does not touch British Columbia, but it would if the signs of its "
                     f"longitudes were changed (west {raw_number(flipped['w'])}, east {raw_number(flipped['e'])}), "
                     "so the minus signs may be missing. The numbers are shown as recorded.")
    return lines


def dataone_point_lines(number, box, shape):
    """
    Return the lines for a DataONE box with no area, shown as a point (its
    middle): what the record gave, then point_lines (in BC or not, and the
    possibly missing minus sign), then odd numbers.
    """
    w, e, s, n = box["w"], box["e"], box["s"], box["n"]
    if w == e and s == n:
        given = FIELD_NOTE + "the record gives this point as a box whose four numbers make a single point."
    else:
        given = (DATAONE_NOTE + f"the record gives this as a box with no area, a line from longitude {raw_number(w)}, "
                 f"latitude {raw_number(s)} to longitude {raw_number(e)}, latitude {raw_number(n)}; it is shown here "
                 "as its middle point.")
    lines = point_lines(number, (w + e) / 2, (s + n) / 2, shape)
    return lines[:1] + [given] + lines[1:] + odd_number_notes(box)


def distinct_boxes(boxes):
    """Return a record's boxes without exact repeats (the same four numbers), in their first order."""
    seen, kept = set(), []
    for box in (list(boxes) if present(boxes) else []):
        key = (box["w"], box["e"], box["s"], box["n"])
        if key not in seen:
            seen.add(key)
            kept.append(box)
    return kept


def extent_text(boxes):
    """Return the smallest box around all the numbers of some boxes, as recorded, in our note's words."""
    lons = [v for b in boxes for v in (b["w"], b["e"])]
    lats = [v for b in boxes for v in (b["s"], b["n"])]
    return (f"westBoundLongitude {raw_number(min(lons))}, eastBoundLongitude {raw_number(max(lons))}, "
            f"southBoundLatitude {raw_number(min(lats))}, northBoundLatitude {raw_number(max(lats))}")


def dataone_record_text(record, shape, box_outside=False):
    """
    Return the record part of a geography prompt for a DataONE record:
    "Record to read:" and then the record field by field, each with our
    notes where it needs one (see the notes above this function).

    record is a dict with id, repository (the DataONE member repository,
    such as PANGAEA), title, subjects (the keywords), abstract,
    named_location, place_key and site (lists, as harvested), boxes (dicts
    with w, e, s and n, every box the record has) and unreadable_boxes (how
    many boxes in the original document had an edge missing or not a
    number). Fields that are empty are left out. box_outside True makes each
    box note also say how much lies outside BC (pass 2, version C4).
    """
    lines = ["Record to read:", ""]
    if present(record.get("title")):
        lines.append(f"Title: {record['title']}")
    if present(record.get("subjects")):
        lines.append("Keywords: " + "; ".join(str(s) for s in record["subjects"]))
    if present(record.get("abstract")):
        lines.append(f"Abstract: {record['abstract']}")
        if str(record["abstract"]).rstrip().endswith(CUT_MARK):
            lines.append(DATAONE_NOTE + "this abstract ends with \"...\": the repository's copy of the abstract was "
                         "cut, so the rest of it is not shown.")
    fields = [("Named location (namedLocation)", field_values(record.get("named_location"))),
              ("Place keywords (placeKey)", field_values(record.get("place_key"))),
              ("Site (site)", field_values(record.get("site"), drop_boilerplate=True))]
    shown = [(label, values) for label, values in fields if values]
    for label, values in shown:
        lines.append(f"{label}: " + "; ".join(values))
    if shown:
        lines.append(FIELD_NOTE + "these are the record's place fields as DataONE's index holds them "
                     "(namedLocation, placeKey and site).")
    if present(record.get("repository")):
        lines.append(f"Repository: {record['repository']}")
        lines.append(FIELD_NOTE + "this is the DataONE member repository the record comes from.")
    lines.append(f"Record identifier: {record['id']}")
    lines.append(FIELD_NOTE + "this is the identifier the repository gave the record.")

    boxes = distinct_boxes(record.get("boxes"))
    listed, rest = boxes, []
    if len(boxes) > 1:
        listed = [b for b in boxes if touches_bc(b, shape)]
        rest = [b for b in boxes if not touches_bc(b, shape)]
    if boxes:
        lines.append(FIELD_NOTE + "a bounding box is four numbers in decimal degrees. westBoundLongitude and "
                     "eastBoundLongitude are longitudes (negative means west of Greenwich); southBoundLatitude and "
                     "northBoundLatitude are latitudes (negative means south of the equator). A point is one "
                     "longitude and one latitude.")
    if len(boxes) > 1:
        lines.append(DATAONE_NOTE + f"the record has {len(boxes)} different boxes and points; the {len(listed)} "
                     "that touch British Columbia are listed below in full.")
    areas = [b for b in listed if not is_point_box(b)]
    points = [b for b in listed if is_point_box(b)]
    for number, box in enumerate(areas, start=1):
        lines += dataone_box_lines(number, box, shape, outside=box_outside)
    for number, box in enumerate(points, start=1):
        lines += dataone_point_lines(number, box, shape)
    if rest:
        flips = sum(touches_bc(sign_flipped(b), shape) for b in rest)
        flip_text = (f"; {flips} of them would touch British Columbia if the signs of their longitudes were "
                     "changed" if flips else "")
        lines.append(DATAONE_NOTE + f"the other {len(rest)} boxes and points do not touch British Columbia; "
                     f"together they lie within {extent_text(rest)} (the smallest box around all their numbers "
                     f"as recorded){flip_text}.")
    if record.get("unreadable_boxes"):
        lines.append(DATAONE_NOTE + f"{record['unreadable_boxes']} more boxes in the record's original document "
                     "could not be read (an edge missing or not a number).")
    return "\n".join(lines)


def dataone_box_shares(record, shape):
    """
    Return the share in BC of each of a DataONE record's boxes with an area,
    for the pass 2 box rule (geo_pass2_classes.fill_in_bc), as box_shares
    does for Lunaris. A west > east box counts only when both its readings
    lead to the same answer (both shares at least the rule's half, or both
    under it); its share is then the smaller one, which is on the same side.
    When the readings disagree the box is left out (None). A box with no
    area is None, as in Lunaris.
    """
    from geo_pass2_classes import BOX_SHARE_FOR_BC

    shares = []
    for box in distinct_boxes(record.get("boxes")):
        if is_point_box(box):
            shares.append(None)
        elif box["w"] > box["e"]:
            measured = [measure_reading(box, pieces, shape) for pieces in (crossing_pieces(box), swapped_pieces(box))]
            if any(m is None for m in measured):
                shares.append(None)
                continue
            first, second = (m[0] for m in measured)
            same = (first >= BOX_SHARE_FOR_BC) == (second >= BOX_SHARE_FOR_BC)
            shares.append(min(first, second) if same else None)
        else:
            measured = measure_box(box, shape)
            shares.append(None if measured is None else measured[0])
    return shares
