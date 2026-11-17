"""The front end's area shapes (frontend/src/areaShapes.js) must agree with
the API's (api/area.py): this runs areaShapes.js itself with node and
compares, for the same input, the shapes read (or the message of the
refusal, word for word), the areas, the size messages, the hexagons and the
area text written. It also checks pointWouldCross, the check the page will
make while a polygon is drawn, against shapely. The inputs are hand-picked
cases (one box, several shapes, a concave polygon, a 250-point polygon and
every kind of bad input) and a few thousand made-up ones from a fixed seed.
Skipped where node or the front end's packages are missing."""

import json
import math
import random
from pathlib import Path

import pytest
from shapely.geometry import LineString

from api.area import AreaRequestError, area_cells, check_shape_sizes, read_area_shapes, shape_area_km2
from frontend_node import NODE_READY, front_end_module, run_node

pytestmark = pytest.mark.skipif(not NODE_READY, reason="needs node and the front end's packages")


def circle(points, lng=-123.0, lat=49.0, radius=0.5, wobble=0.0):
    """Return the area text of a polygon of this many points around a
    circle, with 4 decimals (never crossing itself)."""
    numbers = []
    for i in range(points):
        angle = 2 * math.pi * i / points
        r = radius * (1 + wobble * math.sin(9 * angle))
        numbers += [f"{lng + r * math.cos(angle):.4f}", f"{lat + r * math.sin(angle):.4f}"]
    return "p:" + ",".join(numbers)


# Hand-picked area texts: what is read, and every kind of refusal.
HAND_TEXTS = [
    # Read.
    "b:-120.6,50.55,-120.1,50.8", "-120.6,50.55,-120.1,50.8", " -120.6, 50.55 ,-120.1,50.8 ",
    "p:-123,49,-122,49,-122.5,50", "p:-123,49,-122,49,-122.5,50,-123,49", "p:-123,49,-122,49,-122,49,-122.5,50",
    "p:-123,49,-122,49,-122.5,50,-123.00,+49.0", "b:-121,50,-120,51;p:-120.5,50.5,-119,50.5,-119.5,52;b:-121,50,-120,51",
    "p:0,0,0,3,3,3,3,2,1,2,1,1,3,1,3,0", "p:0,0,1,0,2,0,2,2", "b:-180,-90,-179,90", "p:179,89,180,89,180,90",
    "p:-0,0,1,0,1,1", "b:-100,0,79.9,1", "p:-170,0,0,0,170,0,170,1,0,1,-170,1", circle(250), circle(246) + ";b:0,0,1,1",
    "p:0,0,4,0,4,4,2,0.0001,0,4", "p:.5,0,5.,0,+1,1", "p:0,0,1,0,1.00000000000000000001,1,1,1",
    # Refused: the text.
    "", "   ", "b:0,0,1,1;;b:2,2,3,3", "b:0,0,1,1;", "x:0,0,1,1", "B:0,0,1,1", "b:0,0,1,1;0,0,1,1", "p0,0,1,0,1,1",
    "p", "b:", "p:", "p: ;b:0,0,1,1", ";", ":", "b:p:0,0,1,1",
    # Refused: numbers.
    "b:0,0,1,abc", "b:0,,1,1", "p:0,0,1,0,nan,1", "p:0,0,1,0,inf,1", "b:0,0,1e1,1", "b:0,0,1_0,1", "b:0,0,1 0,1",
    "b:0,0,1", "0,0,1,1,2", "p:0,0,1,0,1", "p:1", "p:0,0,1,0,1,1,", "b:0,0,1,1 p:2,2,3,2,3,3", "b:0,0,1,+-1",
    "b:0,0,1,--1", "b:0,0,1,.", "b:0,0,1,-", "b:\u0661,\u0662,\u0663,\u0664", "b:\uff11,0,2,1",
    "b:0,0,1,x" + "9" * 5000, "b:0,0,1," + "9" * 5000, "b:0,0,1,a'b", 'b:0,0,1,a"b', "b:0,0,1,a'\"b",
    "b:0,0,1,a\\b", "b:0,0,1,\x01x", "b:0,0,1,a\u2028b", "b:0,0,1,a\tb", "b:0,0,1,\u00e9t\u00e9",
    "\u00e9,1,2,3", "b:0,0,1,1\x1c", "b:0,0,1,\ufeff1", "b:0,0,1,\xa01", "b:0,0,1,\u200b1",
    # Refused: ranges and box edges.
    "b:-180.5,0,1,1", "b:0,0,181,1", "b:0,-91,1,1", "b:0,0,1,90.01", "p:0,0,1,0,200,1", "p:0,0,1,95,1,1",
    "b:1,0,1,1", "b:2,0,1,1", "b:0,1,1,1", "b:0,2,1,1", "b:0,0,1,1;b:2,0,1,1", "b:1,0,1.00000000000000000001,1",
    "b:180.0000000001,0,181,1",
    # Refused: polygons.
    "p:0,0", "p:0,0,1,1", "p:0,0,1,1,0,0", "p:0,0,1,1,1,1,0,0,1,1", "b:0,0,1,1;p:0,0,1,1",
    "p:0,0,1,1,1,0,0,1", "p:0,0,1,1,1,0,0,1,0,0", "p:0,0,4,0,4,4,2,0,0,4", "p:0,0,2,2,4,0,4,4,2,2,0,4",
    "p:0,0,2,0,1,0,1,1", "p:0,0,1,0,2,0", "p:0,0,3,0,3,1,2,1,2,0,1,0,1,1,0,1", "p:0,0,0,1,1,0,1,1",
    "p:0,0,4,0,4,4,2,0.0000,0,4", "p:0,10,4,10,4,14,2,10.00000000000000000001,0,14",
    "p:10,10,10.0000000000000001,10,10,10.0000000000000001",
    # Refused: the 180th meridian.
    "b:-179,0,179,1", "-179,0,179,1", "b:-90,0,90,1", "p:170,50,-170,50,-170,51", "p:-170,51,-170,50,170,50",
    "b:0,0,1,1;p:179,0,-179,0,-179,1", "b:-90.000000000000000001,0,89.99999999999999999,1",
    "p:0,0,179.99999999999999999,0,0,1", "b:-179.71,0,0.289999999999999,5",
    # Refused: too many points.
    circle(251), circle(247) + ";b:0,0,1,1", circle(1000),
]


def made_up_texts(count, seed=11):
    """Return count made-up area texts from a fixed seed: polygons on a
    small grid (so crossings, touching corners and edges lying along each
    other are common), boxes with edges in and out of order and range, odd
    numbers and odd separators, and areas of several shapes."""
    rng = random.Random(seed)
    odd = ["", " ", "abc", "1e1", "+-1", ".", "5.", ".5", "-0", "+0", "\u0661", "1_0", "nan", "200", "-95",
           "1.00000000000000000001", "0.5", "a'b", "\t2", "3 ", "\u00e9"]

    def number(low=-4, high=4):
        if rng.random() < 0.06:
            return rng.choice(odd)
        return rng.choice([str(rng.randint(low, high)), f"{rng.uniform(low, high):.{rng.randint(0, 5)}f}",
                           f"{rng.randint(low, high)}.5"])

    def polygon():
        points = [(number(), number()) for _ in range(rng.randint(1, 8))]
        if rng.random() < 0.2:
            points.append(points[0])
        return "p:" + ",".join(f"{a},{b}" for a, b in points)

    def box():
        return "b:" + ",".join(number(-6, 6) for _ in range(rng.choice([4, 4, 4, 3, 5])))

    texts = []
    for _ in range(count):
        shapes = [polygon() if rng.random() < 0.6 else box() for _ in range(rng.choice([1, 1, 1, 2, 3]))]
        text = rng.choice([";", ";", ";;", " ; "]).join(shapes)
        if rng.random() < 0.05:
            text = text.replace("p:", rng.choice(["P:", "q:", "p", "b:"]), 1)
        texts.append(text)
    return texts


def python_reading(text):
    """Return what the API makes of an area text, in the form the node script
    prints: {"shapes": [[ring, is_box, box]]} or {"error": message}."""
    try:
        shapes = read_area_shapes(text)
    except AreaRequestError as error:
        return {"error": str(error)}
    return {"shapes": [[[list(point) for point in shape.ring], shape.is_box,
                        dict(zip(("west", "south", "east", "north"), shape.box)) if shape.box else None]
                       for shape in shapes]}


def run_js(folder, cases, body):
    """Run areaShapes.js with node on cases (any JSON), with body the
    JavaScript that prints one JSON line per case (it sees cases and the
    module's exports), and return the parsed lines."""
    folder = Path(folder)
    (folder / "cases.json").write_text(json.dumps(cases), encoding="utf-8")
    script = (f"import {{ readFileSync }} from 'node:fs'\n"
              f"import * as shapes from {front_end_module('areaShapes.js')!r}\n"
              f"const cases = JSON.parse(readFileSync({str(folder / 'cases.json')!r}, 'utf8'))\n"
              f"{body}\n")
    return [json.loads(line) for line in run_node(folder, script).splitlines()]


# The JavaScript that reads each text and prints the shapes or the message.
READ_JS = """
for (const text of cases) {
  try {
    const read = shapes.readAreaText(text)
    console.log(JSON.stringify({ shapes: read.map((shape) => [shape.ring, shape.isBox, shape.box]) }))
  } catch (error) {
    if (!(error instanceof shapes.AreaProblem)) throw error
    console.log(JSON.stringify({ error: error.message }))
  }
}
"""


def test_reading_and_its_messages_agree_on_hand_picked_texts(tmp_path):
    found = run_js(tmp_path, HAND_TEXTS, READ_JS)
    assert len(found) == len(HAND_TEXTS)
    for text, js in zip(HAND_TEXTS, found):
        assert js == python_reading(text), text[:80]
    # The cases cover both outcomes.
    assert sum("shapes" in js for js in found) >= 15 and sum("error" in js for js in found) >= 60


def test_reading_and_its_messages_agree_on_made_up_texts(tmp_path):
    texts = made_up_texts(4000)
    found = run_js(tmp_path, texts, READ_JS)
    differ = [(text, js, python_reading(text)) for text, js in zip(texts, found) if js != python_reading(text)]
    assert differ == []
    read = sum("shapes" in js for js in found)
    assert read > 300 and len(texts) - read > 1000, read


# Areas at a resolution: one box, several shapes, a concave polygon, a
# 250-point polygon, and each size refusal (too small, too big from its
# area, too big only when joined).
SIZE_CASES = [
    ("b:-120.6,50.55,-120.1,50.8", 6), ("b:-120.6,50.55,-120.1,50.8", 7), ("b:-131,39.5,-124.5,49.5", 4),
    ("b:-125.25,39.78,-125.15,39.85;b:-125.22,39.78,-125.12,39.85", 7),
    ("b:-125.25,39.78,-125.15,39.85;p:-130.40,48.82,-130.24,48.82,-130.32,48.92", 7),
    ("p:-123,49,-123,49.3,-122.7,49.3,-122.7,49.2,-122.9,49.2,-122.9,49.1,-122.7,49.1,-122.7,49", 7),
    (circle(250, -122.8, 49.2, 0.25, 0.15), 7), (circle(250, -121.5, 49.9, 1.2, 0.15), 6),
    (";".join([circle(84, -122.8, 49.2, 0.2), circle(83, -121.5, 50.6, 0.2), circle(83, -119.5, 49.9, 0.2)]), 6),
    ("p:-125.20,39.80,-125.19,39.80,-125.195,39.81", 7),
    ("b:-125.25,39.78,-125.15,39.85;p:-125.20,39.80,-125.19,39.80,-125.195,39.81", 7),
    ("b:-125.2,39.8,-125.19,39.81", 7), ("b:-123,49,-122.9,49.05", 4),
    ("p:-139,48,-114,48,-114,60,-139,60", 7), ("b:-125.25,39.78,-125.15,39.85;p:-139,48,-114,48,-114,60,-139,60", 7),
    ("b:-139.1,48.2,-114,60", 7), ("b:-130,50,-120,59;b:-120,50,-110,59", 7),
]


def python_sizes(text, res):
    """Return the API's areas, size message (or None) and hexagons for an
    area text at a resolution."""
    shapes = read_area_shapes(text)
    areas = [shape_area_km2(shape) for shape in shapes]
    try:
        check_shape_sizes(shapes, res)
        cells = area_cells(shapes, res)
        return areas, None, cells
    except AreaRequestError as error:
        return areas, str(error), None


def test_sizes_messages_and_hexagons_agree(tmp_path):
    body = """
for (const [text, res] of cases) {
  const read = shapes.readAreaText(text)
  const joined = shapes.shapeSizeProblem(read, res) ? null : shapes.shapesCells(read, res)
  console.log(JSON.stringify({ areas: read.map(shapes.shapeAreaKm2), problem: shapes.shapesProblem(read, res),
    cells: joined && !joined.problem ? joined.cells : null }))
}
"""
    found = run_js(tmp_path, SIZE_CASES, body)
    problems = 0
    for (text, res), js in zip(SIZE_CASES, found):
        areas, problem, cells = python_sizes(text, res)
        assert js["areas"] == pytest.approx(areas, rel=1e-12), text[:60]
        assert js["problem"] == problem, text[:60]
        assert js["cells"] == cells, text[:60]
        problems += problem is not None
    assert problems == 8


def test_writing_gives_text_the_api_reads_the_same(tmp_path):
    texts = ["b:-120.6,50.55,-120.1,50.8", "-120.6,50.55,-120.1,50.8",
             "b:-121,50,-120,51;p:-120.5,50.5,-119,50.5,-119.5,52", "p:-0,0,1,0,1,1",
             "p:-121.12345,50.00004,-120.8,50,-120.9,50.2", circle(250)]
    body = """
for (const text of cases) {
  const read = shapes.readAreaText(text)
  const written = shapes.areaText(read)
  const again = shapes.readAreaText(written)
  console.log(JSON.stringify({ written, again: again.map((shape) => [shape.ring, shape.isBox, shape.box]),
    stable: shapes.areaText(again) === written }))
}
"""
    for text, js in zip(texts, run_js(tmp_path, body=body, cases=texts)):
        # The API reads what the page writes as the page reads it back.
        assert python_reading(js["written"]) == {"shapes": js["again"]}
        assert js["stable"]
        # Shapes with at most 4 decimals come back unchanged.
        if text != "p:-121.12345,50.00004,-120.8,50,-120.9,50.2":
            assert python_reading(text) == {"shapes": js["again"]}, text[:60]
    assert run_js(tmp_path, ["b:-120.6,50.55,-120.1,50.8"], body)[0]["written"] == "b:-120.6,50.55,-120.1,50.8"


def chains(count, seed=5):
    """Return count (open chain, next point) pairs on a small grid, each
    chain simple (no two of its edges meet but neighbours at their shared
    corner), the next point neither the last corner nor the first."""
    rng = random.Random(seed)
    found = []
    while len(found) < count:
        points = [(rng.randint(0, 4) + rng.choice([0, 0, 0.5]), rng.randint(0, 4) + rng.choice([0, 0, 0.5]))
                  for _ in range(rng.randint(1, 6))]
        if any(points[i] == points[i + 1] for i in range(len(points) - 1)):
            continue
        if len(points) > 1 and not LineString(points).is_simple:
            continue
        point = (rng.randint(0, 4) + rng.choice([0, 0, 0.5]), rng.randint(0, 4) + rng.choice([0, 0, 0.5]))
        if point in (points[-1], points[0]):
            continue
        found.append((points, point))
    return found


def test_point_would_cross_agrees_with_shapely(tmp_path):
    """Adding the point makes the open chain cross or touch itself exactly
    when shapely says the longer chain is not simple."""
    cases = [[[list(p) for p in chain], list(point)] for chain, point in chains(3000)]
    body = "for (const [ring, point] of cases) console.log(JSON.stringify(shapes.pointWouldCross(ring, point)))"
    found = run_js(tmp_path, cases, body)
    expected = [len(chain) >= 1 and not LineString(chain + [tuple(point)]).is_simple for chain, point in cases]
    differ = [(chain, point) for (chain, point), js, want in zip(cases, found, expected) if js != want]
    assert differ == []
    assert 300 < sum(expected) < 2700
