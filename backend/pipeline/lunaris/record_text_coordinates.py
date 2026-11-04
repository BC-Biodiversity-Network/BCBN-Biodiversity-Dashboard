"""
record_text_coordinates.py

A report only (2026-11-03): find the coordinates the authors wrote in a
record's own text, to see whether they could be drawn as the record's own
points. Nothing here changes the export or the site.

Where: the title, the description, the keywords and the record's own place
list, as harvested (lunaris_datasets.parquet: title, abstract, subjects,
places). Each field is searched on its own; the place list's entries are
joined with " | ".

Forms read (a "half" is a latitude or a longitude on its own):
  dms          degrees, minutes and seconds with a letter: 48°50'19.00"N
  dm           degrees and minutes (whole or decimal) with a letter:
               49°10′N, 48°51.5′N, 52 o 28'N (a letter o as the degree sign)
  d            whole or decimal degrees with a degree sign and a letter:
               54°N, 52.0025°N, 54° North
  decimal      decimal degrees with a letter, no degree sign: 48.3149 N,
               -120.4603W
  labelled     a number after lat, latitude, long, longitude, lon or lng:
               lat: 50.0307, long: −125.0992
  bounds       a box written as labelled edges: West: −139.06° East:
               −110.43° South: 47.68° North: 60.61°
  bare pair    two decimal numbers with at least 3 decimals each, next to
               each other, both at least BARE_PAIR_SMALLEST from 0:
               (49.30275, -123.12643); read as latitude then longitude, or
               the other way when only that fits
A minus sign and a letter both mean south or west: -120.4603W is 120.46 W,
and -87.85E is read as 87.85 W (noted, since the two disagree).

Pairing: a latitude half and a longitude half make a point when they are in
the same field, at most PAIR_GAP characters apart, in either order, with no
place-list separator between them. A pair split over two place-list entries
("-120.4603W | Lac Du Bois Provincial Park | 50.7007N") is accepted only
when it is the field's one latitude and one longitude left over; a list of
many values (the edges of many sites) is left as single halves. Two halves
of the same kind joined by "to", "and", "-" or "–", or two numbers sharing
one letter ("from 20 to 60°N", "132.93962-133.16817W"), are a range, not a
point; a latitude range and a longitude range together are a box. A point
written twice in one record (in two forms, or in two fields) counts once.

Left out (counted where they can be seen, never read): UTM and other
projected coordinates, degrees with no letter and no label ("from 30° to
73°"), malformed values such as "48°651 N", and coordinates in attached
files.

The checks, for each point:
  out_of_range       latitude beyond 90 or longitude beyond 180
  missing_west       no letter or sign for the longitude, and the point
                     falls in Asia or the Pacific (east 50 to 170, latitude
                     15 to 72) where a west longitude would fall in North
                     America
  outside_class      outside the area our class describes: farther than
                     BUFFER_DEGREES from BC's outline for bc_only, outside
                     Canada's extent (with the same margin) for
                     canada_incl_bc and all_canada
  outside_own_boxes  the record has boxes of its own, and the point is in
                     none of them (with BUFFER_DEGREES around each)

The role: the words just before and after a coordinate (ROLE_WINDOW
characters, the same field) are read for what it is: a lab or institution,
a weather or climate station, a reference or comparison site, the end of a
range, a study site, or no role shown, tried in that order. A point in the
place list has no words around it beyond other place names. Only a first
reading, for the report.

Writes, into --out (which must not exist yet):
  pairs.csv          one row per point, range, box or single half found,
                     with the text copied exactly, the parsed values, the
                     field, the role, the checks, the record's class,
                     catalogue and own boxes and points, and whether the map
                     draws anything of the record's own
  left_out.csv       text that looks like a coordinate but was not read
  report.txt         the counts

Run, from backend/:
    python pipeline/lunaris/record_text_coordinates.py \\
        --bc-boundary <path to bc_boundary_gbif.wkt> \\
        --out data/lunaris_place_lookup/text_coordinates
"""

import argparse
import os
import re
import sys
from collections import Counter

import pandas as pd
from shapely.geometry import Point

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from bc_filter_rules import load_bc_shape  # noqa: E402
from place_lookup_rules import in_map_scope  # noqa: E402

DEFAULT_DATASETS = "data/api/lunaris_datasets.parquet"

# How far apart (in characters) a latitude and a longitude may be and still
# make one point.
PAIR_GAP = 60

# How many characters before and after a coordinate are read for its role.
ROLE_WINDOW = 160

# How far (in degrees) a point may lie outside BC, Canada or one of the
# record's own boxes before it is flagged.
BUFFER_DEGREES = 0.5

# A bare pair is read only when both numbers are at least this far from 0,
# so small measurements ("1.234, 5.678") are not read as a place.
BARE_PAIR_SMALLEST = 10

# Canada's extent, for the canada_incl_bc and all_canada checks.
CANADA = {"s": 41.6, "n": 83.2, "w": -141.1, "e": -52.5}

# The place list's separator.
LIST_SEPARATOR = "|"

MINUS = "-−–"
NUMBER = rf"[{MINUS}]?\d{{1,3}}(?:\.\d+)?"
DEGREE = r"(?:[°º˚]|\s?o(?=\s*\d)|\s?deg(?:rees?)?\.?)"
MINUTE = r"(?:['′’]|\s?min\.?)"
SECOND = r"(?:\"|″|''|”|′′|['′’]|\s?sec\.?)"

# The direction: N, S, E or W (or the word), not followed by a letter, a
# digit, a sign or a dash (so "2.5E-3" is not read as a longitude).
LETTER = r"(North|South|East|West|N|S|E|W)(?![\w\-+])"

# A box written as labelled edges: "West: −139.06°", "North: 60.6°".
BOUND = re.compile(rf"\b(West|East|South|North)(?:ern)?(?:\s+bound(?:ing)?(?:\s+coordinate)?)?\s*:\s*({NUMBER})\s*°?")

# Two numbers sharing one letter: "20 to 60°N", "132.93962-133.16817W".
SHARED_RANGE = re.compile(rf"(?<![\w.])(\d{{1,3}}(?:\.\d+)?)\s*°?\s*(to|-|–)\s*(\d{{1,3}}(?:\.\d+)?)\s*°?\s*{LETTER}")

# One half in degrees (a degree sign, "deg" or a letter o before minutes),
# with optional minutes and seconds, then the direction.
HALF_DEGREES = re.compile(
    rf"(?<![\w.])({NUMBER})\s*{DEGREE}\s*(?:(\d{{1,2}}(?:\.\d+)?)\s*{MINUTE}\s*"
    rf"(?:(\d{{1,2}}(?:\.\d+)?)\s*{SECOND}\s*)?)?{LETTER}")

# One half in decimal degrees with no degree sign: it must have a decimal
# point, so "15 N" (nitrogen) or "100 W" (watts) are not read.
HALF_DECIMAL = re.compile(rf"(?<![\w.])([{MINUS}]?\d{{1,3}}\.\d+)\s*{LETTER}")

# One half after a label.
LABELLED = re.compile(rf"\b(lat(?:itude)?|long(?:itude)?|lon|lng)\b\.?\s*[:=]?\s*({NUMBER})(?!\s*[°º˚'′NSEW\d.])",
                      re.IGNORECASE)

# Two bare decimal numbers side by side, each with at least 3 decimals.
BARE_PAIR = re.compile(rf"(?<![\w.])([{MINUS}]?\d{{1,3}}\.\d{{3,}})(?:\s*[,;/]\s*|\s+)([{MINUS}]?\d{{1,3}}\.\d{{3,}})"
                       rf"(?![\w.])")

# Joins that make two halves a range.
RANGE_JOIN = re.compile(r"^\s*(?:to|and|-|–|\u2014|through)\s*$", re.IGNORECASE)

# Text that looks like a coordinate but is left out.
LEFT_OUT = {
    "utm_values": re.compile(r"\b\d{6}(?:\.\d+)?\s*m?\s*E\b.{0,20}?\b\d{7}(?:\.\d+)?\s*m?\s*N\b|\b(?:easting|northing)s?\s*"
                             r"[:=]?\s*\d{6,7}", re.IGNORECASE),
    "utm_named_only": re.compile(r"\bUTM\b", re.IGNORECASE),
    "degrees_without_letter": re.compile(r"(?<![\w.])\d{2,3}(?:\.\d+)?\s*[°º˚](?!\s*[CFKNSEW]|\s*\d)"),
}

# Words that show a coordinate's role, in the order they are tried.
ROLES = [
    ("lab_or_institution", r"laborator|\blab\b|universit|institut|museum|herbarium|hatchery|aquarium|greenhouse|"
                           r"growth chamber|common garden"),
    ("weather_or_climate_station", r"weather station|climate station|meteorolog|environment canada station|"
                                   r"airport|climate data|weather data"),
    ("reference_or_comparison", r"\breference\b|\bcontrol site|\bcompar|\bversus\b|\bvs\.?\b"),
    ("end_of_a_range", r"\brange\b|\blimit\b|\bdistribution\b|northernmost|southernmost|\bextent\b|\bboundar"),
    ("study_site", r"\bsite|\bplot|\bstud(?:y|ied)\b|\bsampl|\bcollect|\bcaptur|\bsurvey|\btransect|\bstation|"
                   r"\blocated\b|\blocation|\bdeploy|\bmooring|\bobservator|\bwere taken|\bfield\b|\bexperiment|"
                   r"\btrap|\bcoordinates|\bgps\b|\bpopulation"),
]


def to_number(text):
    """Return a number written with a hyphen, a minus sign or an en dash as a float."""
    return float(re.sub(rf"^[{MINUS}]", "-", text))


def signed(number, letter):
    """
    Return (value, note) for a number and its direction letter: south and
    west are negative, and so is a number written with a minus sign; a
    minus sign with N or E is noted, since the two disagree.
    """
    negative = number < 0 or letter in "SW"
    note = "minus sign with N or E, read as S or W" if number < 0 and letter in "NE" else ""
    return (-abs(number) if negative else abs(number)), note


def half(axis, value, form, start, end, has_sign=True, note=""):
    """Return one half as a dict."""
    return {"axis": axis, "value": value, "form": form, "signed": has_sign, "start": start, "end": end, "note": note}


def overlaps(found, start, end):
    """Return True if the span start to end overlaps any found item's span."""
    return any(f["start"] < end and start < f["end"] for f in found)


def halves(text):
    """
    Return every latitude or longitude half in the text, in order: dicts
    with axis (lat or lon), value (signed decimal degrees), form, whether a
    letter or sign gave the direction, where it starts and ends, and a note.
    """
    found = []
    for match in SHARED_RANGE.finditer(text):
        letter = match.group(4)[0]
        axis = "lat" if letter in "NS" else "lon"
        for group in (1, 3):
            value, _ = signed(float(match.group(group)), letter)
            end = match.end(group) if group == 1 else match.end()
            found.append(half(axis, value, "decimal" if "." in match.group(group) else "d", match.start(group), end))
    for pattern in (HALF_DEGREES, HALF_DECIMAL):
        for match in pattern.finditer(text):
            if overlaps(found, match.start(), match.end()):
                continue
            groups = match.groups()
            minutes, seconds = (groups[1], groups[2]) if pattern is HALF_DEGREES else (None, None)
            number = to_number(groups[0])
            number = (abs(number) + float(minutes or 0) / 60 + float(seconds or 0) / 3600) * (-1 if number < 0 else 1)
            letter = groups[-1][0]
            value, note = signed(number, letter)
            form = "decimal" if pattern is HALF_DECIMAL else "dms" if seconds else "dm" if minutes else "d"
            found.append(half("lat" if letter in "NS" else "lon", value, form, match.start(), match.end(), note=note))
    for match in LABELLED.finditer(text):
        if overlaps(found, match.start(2), match.end(2)):
            continue
        axis = "lat" if match.group(1).lower().startswith("lat") else "lon"
        value = to_number(match.group(2))
        found.append(half(axis, value, "labelled", match.start(), match.end(), has_sign=value < 0 or axis == "lat"))
    return sorted(found, key=lambda h: h["start"])


def bounds_boxes(text):
    """Return the boxes written as labelled edges (all four of West, East, South and North within PAIR_GAP * 2)."""
    edges = list(BOUND.finditer(text))
    boxes = []
    for i, first in enumerate(edges):
        group = {}
        for match in edges[i:i + 4]:
            if match.start() - first.start() <= PAIR_GAP * 2:
                group.setdefault(match.group(1), match)
        if len(group) == 4 and not any(first.start() < b["end"] for b in boxes):
            value = {side: to_number(m.group(2)) for side, m in group.items()}
            boxes.append({"kind": "box", "form": "bounds", "s": value["South"], "n": value["North"],
                          "w": value["West"], "e": value["East"], "start": min(m.start() for m in group.values()),
                          "end": max(m.end() for m in group.values()), "note": ""})
    return boxes


def bare_pairs(text, taken):
    """Return the bare decimal pairs in the text outside the taken spans, as points (latitude first if it fits)."""
    found = []
    for match in BARE_PAIR.finditer(text):
        if overlaps(taken, match.start(), match.end()):
            continue
        a, b = to_number(match.group(1)), to_number(match.group(2))
        if min(abs(a), abs(b)) < BARE_PAIR_SMALLEST:
            continue
        lat, lon, note = (a, b, "") if abs(a) <= 90 else (b, a, "read as longitude then latitude") if abs(b) <= 90 \
            else (a, b, "")
        found.append({"kind": "point", "form": "bare pair", "lat": lat, "lon": lon, "lon_signed": lon < 0,
                      "start": match.start(), "end": match.end(), "note": note})
    return found


def point_of(a, b, note=""):
    """Return the point made of two halves of different axes."""
    lat, lon = (a, b) if a["axis"] == "lat" else (b, a)
    form = a["form"] if a["form"] == b["form"] else f"{a['form']}+{b['form']}"
    notes = "; ".join(n for n in (note, lat["note"], lon["note"]) if n)
    return {"kind": "point", "form": form, "lat": lat["value"], "lon": lon["value"], "lon_signed": lon["signed"],
            "start": min(a["start"], b["start"]), "end": max(a["end"], b["end"]), "note": notes}


def coordinates(text):
    """
    Return the points, ranges, boxes and single halves in one field's text:
    dicts with kind (point, lat_range, lon_range, box, lat_alone or
    lon_alone), form, the values, where the text starts and ends, and a note.
    """
    found = bounds_boxes(text)
    hs = [h for h in halves(text) if not overlaps(found, h["start"], h["end"])]
    used = set()
    # Ranges: two halves of the same axis joined by "to", "and" or a dash.
    for i in range(len(hs) - 1):
        a, b = hs[i], hs[i + 1]
        if i not in used and a["axis"] == b["axis"] and RANGE_JOIN.match(text[a["end"]:b["start"]]):
            used |= {i, i + 1}
            found.append({"kind": f"{a['axis']}_range", "form": a["form"], "low": min(a["value"], b["value"]),
                          "high": max(a["value"], b["value"]), "start": a["start"], "end": b["end"], "note": ""})
    # Points: a latitude and a longitude close together, with no place-list separator between them.
    for i, a in enumerate(hs):
        if i in used:
            continue
        for j in range(i + 1, len(hs)):
            b = hs[j]
            if j in used or b["start"] - a["end"] > PAIR_GAP:
                break
            if a["axis"] != b["axis"] and LIST_SEPARATOR not in text[a["end"]:b["start"]]:
                used |= {i, j}
                found.append(point_of(a, b))
                break
    # A pair split over place-list entries: only when it is the one latitude and one longitude left.
    left = [k for k in range(len(hs)) if k not in used]
    if len(left) == 2 and {hs[k]["axis"] for k in left} == {"lat", "lon"}:
        a, b = hs[left[0]], hs[left[1]]
        if b["start"] - a["end"] <= PAIR_GAP:
            used |= set(left)
            found.append(point_of(a, b, "split over place-list entries"))
    # A latitude range and a longitude range close together make a box.
    ranges = [f for f in found if f["kind"].endswith("_range")]
    for r in ranges:
        partner = next((o for o in ranges if o is not r and o["kind"] != r["kind"] and not o.get("boxed")
                        and min(abs(o["start"] - r["end"]), abs(r["start"] - o["end"])) <= PAIR_GAP), None)
        if partner and not r.get("boxed"):
            lat, lon = (r, partner) if r["kind"] == "lat_range" else (partner, r)
            r["boxed"] = partner["boxed"] = True
            found.append({"kind": "box", "form": r["form"], "s": lat["low"], "n": lat["high"], "w": lon["low"],
                          "e": lon["high"], "start": min(r["start"], partner["start"]),
                          "end": max(r["end"], partner["end"]), "note": ""})
    found = [f for f in found if not f.get("boxed")]
    alone = [hs[k] for k in range(len(hs)) if k not in used]
    # A bare pair that repeats a point already written in another form is the same point.
    written = [(f["lat"], f["lon"]) for f in found if f["kind"] == "point"]
    found += [p for p in bare_pairs(text, found + alone)
              if not any(abs(p["lat"] - lat) < 1e-3 and abs(p["lon"] - lon) < 1e-3 for lat, lon in written)]
    # A single half that only repeats one side of a point found here is dropped.
    repeated = {("lat", round(f["lat"], 3)) for f in found if f["kind"] == "point"} | \
               {("lon", round(f["lon"], 3)) for f in found if f["kind"] == "point"}
    found += [{"kind": f"{h['axis']}_alone", "form": h["form"], "value": h["value"], "start": h["start"],
               "end": h["end"], "note": "no partner within reach"} for h in alone
              if (h["axis"], round(h["value"], 3)) not in repeated]
    return sorted(found, key=lambda f: f["start"])


def role_of(text, start, end):
    """Return the first role the words around a coordinate show (see ROLES), or "no_role_shown"."""
    around = text[max(0, start - ROLE_WINDOW):start] + " " + text[end:end + ROLE_WINDOW]
    for role, words in ROLES:
        if re.search(words, around, re.IGNORECASE):
            return role
    return "no_role_shown"


def checks(found, final_class, boxes, bc_outline):
    """Return one point's check flags (see the top of the file) joined with "; ", or "" when it passes them all."""
    lat, lon = found["lat"], found["lon"]
    if abs(lat) > 90 or abs(lon) > 180:
        return "out_of_range"
    flags = []
    if not found["lon_signed"] and 50 <= lon <= 170 and 15 <= lat <= 72:
        flags.append("missing_west")
    if final_class == "bc_only" and bc_outline is not None and bc_outline.distance(Point(lon, lat)) > BUFFER_DEGREES:
        flags.append("outside_class")
    if final_class in ("canada_incl_bc", "all_canada") and not (
            CANADA["s"] - BUFFER_DEGREES <= lat <= CANADA["n"] + BUFFER_DEGREES
            and CANADA["w"] - BUFFER_DEGREES <= lon <= CANADA["e"] + BUFFER_DEGREES):
        flags.append("outside_class")
    if boxes and not any(box["s"] - BUFFER_DEGREES <= lat <= box["n"] + BUFFER_DEGREES
                         and box["w"] - BUFFER_DEGREES <= lon <= box["e"] + BUFFER_DEGREES for box in boxes):
        flags.append("outside_own_boxes")
    return "; ".join(flags)


def listed(value):
    """Return a harvested list (or nothing) as a list of strings."""
    return [str(v) for v in value] if value is not None and not isinstance(value, float) else []


def record_fields(record):
    """Return the record's searchable fields, as harvested: title, description, keywords and place list."""
    return {"title": record["title"] if isinstance(record["title"], str) else "",
            "description": record["abstract"] if isinstance(record["abstract"], str) else "",
            "keywords": "; ".join(listed(record["subjects"])),
            "place_list": f" {LIST_SEPARATOR} ".join(listed(record["places"]))}


def record_rows(record, bc_outline, draws_own):
    """
    Return pairs.csv's rows and left_out.csv's rows for one record. A point
    already found in this record (within 0.001 degrees) is not repeated.
    """
    rows, left, seen = [], [], []
    boxes = [dict(b) for b in record["boxes"]] if record["boxes"] is not None else []
    points = listed(record["points"])
    for field, text in record_fields(record).items():
        for found in coordinates(text):
            if found["kind"] == "point":
                if any(abs(found["lat"] - lat) < 1e-3 and abs(found["lon"] - lon) < 1e-3 for lat, lon in seen):
                    continue
                seen.append((found["lat"], found["lon"]))
            row = {"dataset_id": record["id"], "title": record["title"], "catalogue": record["source_name"],
                   "frontend_group": record["frontend_group"], "final_class": record["final_class"],
                   "own_boxes": len(boxes), "own_points": len(points), "map_draws_own": draws_own,
                   "field": field, "kind": found["kind"], "form": found["form"],
                   "text": text[found["start"]:found["end"]], "note": found["note"],
                   "role": role_of(text, found["start"], found["end"]),
                   "context": text[max(0, found["start"] - 120):found["end"] + 120]}
            if found["kind"] == "point":
                row.update(lat=round(found["lat"], 6), lon=round(found["lon"], 6),
                           checks=checks(found, record["final_class"], boxes, bc_outline))
            elif found["kind"] == "box":
                row.update(s=found["s"], n=found["n"], w=found["w"], e=found["e"])
            elif found["kind"].endswith("_range"):
                row.update(low=found["low"], high=found["high"])
            else:
                row.update(value=found["value"])
            rows.append(row)
        for kind, pattern in LEFT_OUT.items():
            for match in pattern.finditer(text):
                left.append({"dataset_id": record["id"], "catalogue": record["source_name"], "field": field,
                             "kind": kind, "text": text[max(0, match.start() - 60):match.end() + 60]})
    return rows, left


def report_lines(data, frame, left):
    """Return report.txt's lines."""
    points = frame[frame["kind"] == "point"]
    lines = ["Coordinates written in a record's own text (report only; nothing drawn)", "",
             f"Shown datasets searched: {len(data):,} (bc_data {int((data['frontend_group'] == 'bc_data').sum()):,}, "
             f"pending {int((data['frontend_group'] == 'pending').sum()):,})", "", "Found, by kind and form:"]
    lines += ["  " + line for line in frame.groupby(["kind", "form"]).size().to_string().splitlines()]
    lines += ["", f"Points: {len(points):,} in {points['dataset_id'].nunique():,} datasets"]
    for group in ("bc_data", "pending"):
        mine = points[points["frontend_group"] == group]
        ids = set(mine["dataset_id"])
        gain = set(mine.loc[~mine["map_draws_own"], "dataset_id"])
        bare = set(mine.loc[(mine["own_boxes"] == 0) & (mine["own_points"] == 0), "dataset_id"])
        clean = set(mine.loc[~mine["map_draws_own"] & (mine["checks"] == ""), "dataset_id"])
        lines.append(f"  {group}: {len(ids):,} datasets with a point ({len(mine):,} points); {len(gain):,} of them have "
                     f"no point or solid box the map draws ({len(bare):,} have no box or point at all); "
                     f"{len(clean):,} of those have a point that passes every check")
    boxes = frame[frame["kind"].isin(["box", "lat_range", "lon_range"])]
    lines.append(f"Ranges and boxes written as text: {len(boxes):,} in {boxes['dataset_id'].nunique():,} datasets; "
                 f"single halves with no partner: {int(frame['kind'].str.endswith('_alone').sum()):,}")
    lines += ["", "Points by field:"] + [f"  {k:12} {v:,}" for k, v in points["field"].value_counts().items()]
    lines += ["", "Checks on the points:", f"  passes every check  {int((points['checks'] == '').sum()):,}"]
    flags = Counter(f for value in points["checks"] for f in value.split("; ") if f)
    lines += [f"  {k:19} {v:,}" for k, v in flags.most_common()]
    lines += ["", "Role shown by the words around each point:"]
    lines += [f"  {k:28} {v:,}" for k, v in points["role"].value_counts().items()]
    lines += ["", "  by field:"] + ["    " + line for line in
                                    pd.crosstab(points["role"], points["field"]).to_string().splitlines()]
    counts = Counter(r["kind"] for r in left)
    lines += ["", "Left out (looks like a coordinate, not read): "
              + ", ".join(f"{k} {v} (in {len({r['dataset_id'] for r in left if r['kind'] == k})} datasets)"
                          for k, v in counts.items())]
    return lines


def main():
    """Search every shown dataset's text and write the report files."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("--datasets", default=DEFAULT_DATASETS)
    parser.add_argument("--bc-boundary", required=True, help="BC's outline as WKT (bc_boundary_gbif.wkt)")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    if os.path.exists(args.out):
        raise SystemExit(f"ERROR: {args.out} already exists; move it aside first")
    bc_shape = load_bc_shape(args.bc_boundary)
    data = pd.read_parquet(args.datasets)
    rows, left = [], []
    for record in data.to_dict("records"):
        boxes = [dict(b) for b in record["boxes"]] if record["boxes"] is not None else []
        draws_own = not in_map_scope(boxes, listed(record["points"]), bc_shape)
        found, missed = record_rows(record, bc_shape.outline, draws_own)
        rows += found
        left += missed
    frame = pd.DataFrame(rows)
    os.makedirs(args.out)
    frame.to_csv(os.path.join(args.out, "pairs.csv"), index=False)
    pd.DataFrame(left).to_csv(os.path.join(args.out, "left_out.csv"), index=False)
    lines = report_lines(data, frame, left)
    with open(os.path.join(args.out, "report.txt"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
