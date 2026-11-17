"""Tests for an area made of several shapes (api/area.py): the hexagons of
each shape joined into one set, the size rules for each shape and for the
whole area, the polygon area formula, and that the routes' single box
still gives exactly the answers it gave before shapes came in. The client
and the test data are in conftest.py: hexagon A (872800b30ffffff, near
39.81 N, 125.20 W) holds AAAA (5 records) and CCCC (1); hexagon B
(8728c9b40ffffff, near 48.86 N, 130.32 W) holds BBBB (3) and DDDD (1)."""

import json
import math

import h3
import pytest

import api.area
from api.area import (MAX_AREA_HEXAGONS, AreaRequestError, area_cells, area_species, box_area_km2, box_cells,
                      box_shape, check_box_size, polygon_area_km2, read_area_shapes, shape_cells)
from api.cells import cards_gbif_keys, hexagons_list
from api.export import export_area, export_date
from api.gbif_links import gbif_box_search_url
from frontend_node import NODE_READY, front_end_module, run_node

CELL_A = "872800b30ffffff"
CELL_B = "8728c9b40ffffff"

# A box around hexagon A, the same box moved east so the two overlap (both
# hold hexagon A), and a triangle around hexagon B, at resolution 7.
A_BOX = "b:-125.25,39.78,-125.15,39.85"
A_BOX_MOVED = "b:-125.22,39.78,-125.12,39.85"
B_TRIANGLE = "p:-130.40,48.82,-130.24,48.82,-130.32,48.92"


def answer(client, text, res=7, **options):
    """Return area_species' answer for an area text at a resolution, on the
    test data, with every species listed."""
    return area_species(client.app.state.data, read_area_shapes(text), res, limit=None, **options)


def listed(result):
    """The species of an answer as [(key, records)], in order."""
    return [(row["specieskey"], row["records"]) for row in result["results"]]


def refused(client, text, res=7):
    """Return the message the area search refuses an area text with."""
    with pytest.raises(AreaRequestError) as error:
        answer(client, text, res)
    return str(error.value)


# --- One box, exactly as before ---------------------------------------------------------

BOXES = [
    {"west": -131.0, "south": 39.5, "east": -124.5, "north": 49.5, "res": 4},
    {"west": -125.25, "south": 39.78, "east": -125.15, "north": 39.85, "res": 7},
    {"west": -126.0, "south": 39.0, "east": -124.0, "north": 41.0, "res": 6},
    {"west": -131.0, "south": 48.5, "east": -130.0, "north": 49.2, "res": 5},
]


@pytest.mark.parametrize("box", BOXES)
@pytest.mark.parametrize("options", [{}, {"year_from": 2020}, {"sort": "english", "q": "robin"},
                                     {"rank": "kingdom", "kingdom": "Animalia"}])
def test_one_box_gives_the_same_answer_as_before(client, box, options):
    """The routes' box goes through box_shape and the list of shapes, and
    the answer is the one the box code alone gives: its hexagons
    (box_cells), its area (box_area_km2), the hexagon panel's counting
    (hexagons_list) and the box's GBIF links. The text form of the same
    box gives the same answer too."""
    edges = (box["west"], box["south"], box["east"], box["north"])
    response = client.get("/api/area", params={**box, **options, "limit": 200})
    assert response.status_code == 200, response.text
    got = response.json()

    data, cursor = client.app.state.data, client.app.state.data.cursor()
    cells = box_cells(edges, box["res"])
    path = None
    if "rank" in options:
        from api.ranks import require_rank
        path = require_rank(data, cursor, "kingdom", {"kingdom": "Animalia"})
    years = (options["year_from"], data.meta["last_year"]) if "year_from" in options else None
    statuses, sort = None, options.get("sort", "most_records")
    from api.species_list import check_list_options
    sort, statuses = check_list_options(sort, statuses)
    _, part = hexagons_list(data, cursor, box["res"], cells, years, frozenset(), options.get("q"), statuses, sort,
                            0, 200, path)
    keys = cards_gbif_keys(cursor, [row["specieskey"] for row in part["results"]])
    assert got["box"] == {edge: box[edge] for edge in ("west", "south", "east", "north")}
    assert got["hexagon_count"] == len(cells)
    assert got["box_area_km2"] == round(box_area_km2(edges), 1)
    assert listed(got) == listed(part)
    assert (got["records"], got["species"]) == (part["records"], part["species"])
    assert [row["gbif_link"] for row in got["results"]] == [
        gbif_box_search_url(keys[row["specieskey"]], edges, years) for row in part["results"]]
    # The same box written as area text gives the same answer.
    text = f"b:{box['west']},{box['south']},{box['east']},{box['north']}"
    assert area_species(data, read_area_shapes(text), box["res"], options.get("year_from"), None,
                        options.get("q"), 0, 200, options.get("rank"),
                        {"kingdom": "Animalia"} if "rank" in options else None, None,
                        options.get("sort")) == got


def test_one_box_gets_the_same_messages_as_before(client, monkeypatch):
    # Too small, the same words as check_box_size has always given.
    small = (-125.2, 39.8, -125.19, 39.81)
    with pytest.raises(AreaRequestError) as old:
        check_box_size(small, 7)
    response = client.get("/api/area", params=dict(zip(("west", "south", "east", "north"), small), res=7))
    assert response.status_code == 400 and response.json()["detail"] == str(old.value)
    assert str(old.value).startswith("The box is about ") and str(old.value).endswith("Draw a larger box.")
    # Too big by its real count: "The box touches about N hexagons", as before.
    both = BOXES[0]
    count = len(box_cells((both["west"], both["south"], both["east"], both["north"]), 4))
    monkeypatch.setattr(api.area, "MAX_AREA_HEXAGONS", count - 1)
    response = client.get("/api/area", params=both)
    assert response.json()["detail"] == (
        f"The box touches about {count:,} hexagons of about {h3.average_hexagon_area(4, 'km^2'):,.1f} km², "
        f"more than the {count - 1:,} an area search can count. Draw a smaller box or use larger hexagons.")


def test_box_shape_reads_the_edges_as_before():
    assert box_shape("-120.6", "50.55", "-120.1", "50.8")[0].box == (-120.6, 50.55, -120.1, 50.8)
    with pytest.raises(AreaRequestError, match=r"west \(2.0\) must be less than east \(1.0\)"):
        box_shape("2", "0", "1", "1")


# --- Several shapes ------------------------------------------------------------------------


def test_two_shapes_that_do_not_overlap(client):
    result = answer(client, f"{A_BOX};{B_TRIANGLE}")
    a_cells = shape_cells(read_area_shapes(A_BOX)[0], 7)
    b_cells = shape_cells(read_area_shapes(B_TRIANGLE)[0], 7)
    assert CELL_A in a_cells and CELL_B in b_cells and not a_cells & b_cells
    assert result["hexagon_count"] == len(a_cells) + len(b_cells)
    assert listed(result) == [("AAAA", 5), ("BBBB", 3), ("CCCC", 1), ("DDDD", 1)]
    assert (result["records"], result["species"]) == (10, 4)
    # The shapes' area is the sum of each shape's.
    shapes = read_area_shapes(f"{A_BOX};{B_TRIANGLE}")
    assert result["box_area_km2"] == round(box_area_km2(shapes[0].box) + polygon_area_km2(shapes[1].ring), 1)
    # Not one box, so no box; each GBIF link has one geometry per shape.
    assert result["box"] is None
    from api.gbif_links import gbif_rings_search_url
    keys = cards_gbif_keys(client.app.state.data.cursor(), ["AAAA", "BBBB"])
    by_key = {row["specieskey"]: row["gbif_link"] for row in result["results"]}
    assert by_key["AAAA"] == gbif_rings_search_url(keys["AAAA"], [shape.ring for shape in shapes])
    assert by_key["BBBB"].count("geometry=") == 2


def test_two_shapes_that_overlap_count_shared_hexagons_once(client):
    first = shape_cells(read_area_shapes(A_BOX)[0], 7)
    second = shape_cells(read_area_shapes(A_BOX_MOVED)[0], 7)
    assert CELL_A in first & second
    result = answer(client, f"{A_BOX};{A_BOX_MOVED}")
    assert result["hexagon_count"] == len(first | second) < len(first) + len(second)
    # Hexagon A is in both, but its records count once.
    assert listed(result) == [("AAAA", 5), ("CCCC", 1)]
    assert result["records"] == 6
    # The same shape twice is the same as once.
    assert answer(client, f"{A_BOX};{A_BOX}")["hexagon_count"] == len(first)


def test_a_box_and_a_polygon(client):
    result = answer(client, f"{B_TRIANGLE};{A_BOX}")
    expected = area_cells(read_area_shapes(f"{B_TRIANGLE};{A_BOX}"), 7)
    assert result["hexagon_count"] == len(expected)
    assert CELL_A in expected and CELL_B in expected
    assert listed(result) == [("AAAA", 5), ("BBBB", 3), ("CCCC", 1), ("DDDD", 1)]


def test_area_cells_is_the_sorted_union_of_each_shapes_cells():
    shapes = read_area_shapes(f"{A_BOX};{A_BOX_MOVED};{B_TRIANGLE}")
    union = set().union(*(shape_cells(shape, 7) for shape in shapes))
    assert area_cells(shapes, 7) == sorted(union)
    # A box's cells are box_cells' cells.
    assert sorted(shape_cells(shapes[0], 7)) == box_cells(shapes[0].box, 7)


# --- Too small, too big ------------------------------------------------------------------------


def test_a_polygon_that_is_too_small(client):
    tiny = "p:-125.20,39.80,-125.19,39.80,-125.195,39.81"
    area = polygon_area_km2(read_area_shapes(tiny)[0].ring)
    average = h3.average_hexagon_area(7, "km^2")
    expected = (f"is about {area:,.1f} km², smaller than the minimum of {4 * average:,.1f} km² for hexagons of "
                f"about {average:,.1f} km² (4 times their average area). Draw a larger polygon.")
    assert refused(client, tiny) == "The polygon " + expected
    assert refused(client, f"{A_BOX};{tiny}") == "Shape 2 (a polygon) " + expected
    # A small box among other shapes is named the same way.
    assert refused(client, f"{B_TRIANGLE};b:-125.2,39.8,-125.19,39.81").startswith("Shape 2 (a box) is about ")
    assert "Draw a larger box." in refused(client, f"{B_TRIANGLE};b:-125.2,39.8,-125.19,39.81")


def test_a_shape_that_is_too_big(client, monkeypatch):
    # A polygon over all of BC, at the finest hexagons: refused from its
    # area alone, before any hexagon is found.
    monkeypatch.setattr(api.area, "shape_cells", lambda *args: pytest.fail("hexagons were searched"))
    bc = "p:-139,48,-114,48,-114,60,-139,60"
    area = polygon_area_km2(read_area_shapes(bc)[0].ring)
    average = h3.average_hexagon_area(7, "km^2")
    expected = (f"touches about {area / average:,.0f} hexagons of about {average:,.1f} km², more than the "
                f"{MAX_AREA_HEXAGONS:,} an area search can count. Draw a smaller polygon or use larger hexagons.")
    assert refused(client, bc) == "The polygon " + expected
    assert refused(client, f"{A_BOX};{bc}") == "Shape 2 (a polygon) " + expected


def test_an_area_too_big_only_when_its_shapes_are_joined(client, monkeypatch):
    shapes = read_area_shapes(f"{A_BOX};{A_BOX_MOVED};{B_TRIANGLE}")
    first, second = shape_cells(shapes[0], 7), shape_cells(shapes[1], 7)
    joined = len(first | second)
    # Each shape on its own is under the maximum; the first two together
    # pass it.
    monkeypatch.setattr(api.area, "MAX_AREA_HEXAGONS", joined - 1)
    assert len(first) <= joined - 1 and len(second) <= joined - 1
    searched = []
    real = api.area.shape_cells
    monkeypatch.setattr(api.area, "shape_cells", lambda shape, res: searched.append(shape) or real(shape, res))
    message = refused(client, f"{A_BOX};{A_BOX_MOVED};{B_TRIANGLE}")
    assert message == (
        f"The area's shapes together touch at least {joined:,} hexagons of about "
        f"{h3.average_hexagon_area(7, 'km^2'):,.1f} km², more than the {joined - 1:,} an area search can count. "
        "Use smaller or fewer shapes, or larger hexagons.")
    # The search stopped as soon as the joined set passed the maximum: the
    # third shape was never searched.
    assert searched == shapes[:2]


def test_an_area_at_the_maximum_is_allowed(client, monkeypatch):
    joined = len(area_cells(read_area_shapes(f"{A_BOX};{A_BOX_MOVED}"), 7))
    monkeypatch.setattr(api.area, "MAX_AREA_HEXAGONS", joined)
    assert answer(client, f"{A_BOX};{A_BOX_MOVED}")["hexagon_count"] == joined


# --- The polygon area formula ----------------------------------------------------------------


@pytest.mark.parametrize("box", [(-125.0, 49.0, -124.0, 49.5), (-139.1, 48.2, -114.0, 60.0), (0, -10, 30, 45)])
def test_a_polygon_area_of_a_boxs_corners_equals_the_box_formula(box):
    west, south, east, north = box
    ring = [(west, south), (east, south), (east, north), (west, north)]
    assert polygon_area_km2(ring) == pytest.approx(box_area_km2(box), rel=1e-12)
    # Either way round.
    assert polygon_area_km2(ring[::-1]) == pytest.approx(box_area_km2(box), rel=1e-12)


def test_a_polygon_area_adds_up_like_its_pieces():
    # An L shape is the sum of the two boxes it is made of.
    l_shape = [(0, 0), (2, 0), (2, 1), (1, 1), (1, 3), (0, 3)]
    assert polygon_area_km2(l_shape) == pytest.approx(box_area_km2((0, 0, 2, 1)) + box_area_km2((0, 1, 1, 3)),
                                                      rel=1e-12)
    # A triangle is half of its box when its slanted edge runs corner to corner
    # (the formula's edges are straight in longitude and the sine of latitude).
    triangle = [(0, 0), (4, 0), (0, 2)]
    assert polygon_area_km2(triangle) == pytest.approx(box_area_km2((0, 0, 4, 2)) / 2, rel=1e-9)


def test_the_polygon_area_is_close_to_h3s_own_for_a_hexagon():
    """The formula on a hexagon's own corners gives about H3's area for it
    (H3's edges are great circles, the formula's are not quite, so they
    differ a little)."""
    for cell in (CELL_A, CELL_B, "842800bffffffff"):
        ring = [(lng, lat) for lat, lng in h3.cell_to_boundary(cell)]
        assert polygon_area_km2(ring) == pytest.approx(h3.cell_area(cell, "km^2"), rel=1e-3)


# --- The CSV ---------------------------------------------------------------------------------


def test_the_csv_of_several_shapes_names_how_many(client):
    name, text = export_area(client.app.state.data, export_date(), read_area_shapes(f"{A_BOX};{B_TRIANGLE}"), 7)
    line = text.split("\r\n")[1]
    assert "that touch the area's 2 shapes" in line
    assert line.startswith('"# List: species in the ')


# --- The GBIF link of several shapes ----------------------------------------------------------


def circle_text(points, lng=-122.8, lat=49.2, rx=0.25, ry=0.15):
    """Return the area text of a polygon of this many points (wobbly, so not
    every edge is alike), counter-clockwise."""
    numbers = []
    for i in range(points):
        angle = 2 * math.pi * i / points
        radius = 1 + 0.15 * math.sin(9 * angle)
        numbers += [f"{lng + rx * radius * math.cos(angle):.4f}", f"{lat + ry * radius * math.sin(angle):.4f}"]
    return "p:" + ",".join(numbers)


# The cases the Python and JavaScript links are compared on: (name, rings,
# keys, years).
GBIF_CASES = [
    ("one box", [shape.ring for shape in read_area_shapes("b:-120.6,50.55,-120.1,50.8")], [], [2000, 2020]),
    ("two boxes", [shape.ring for shape in read_area_shapes("b:-120.6,50.55,-120.1,50.8;b:-123.3,49.2,-123,49.35")],
     ["59PMB"], None),
    ("a box and a clockwise polygon",
     [shape.ring for shape in read_area_shapes(f"{A_BOX};p:-130.4,48.82,-130.32,48.92,-130.24,48.82")],
     ["BBBB", "OLD1"], [2019, 2019]),
    ("a concave polygon", [shape.ring for shape in read_area_shapes("p:0,0,0,3,3,3,3,2,1,2,1,1,3,1,3,0")],
     [], None),
    ("a 250-point polygon", [shape.ring for shape in read_area_shapes(circle_text(250))], ["X"], [1900, 2026]),
    ("a box edge written -0", [box_shape("-1", "-0", "0", "1")[0].ring], [], None),
]


def test_one_box_gives_exactly_the_boxs_link():
    from api.gbif_links import gbif_box_search_url, gbif_rings_search_url
    for box in BOXES:
        edges = (box["west"], box["south"], box["east"], box["north"])
        ring = box_shape(*map(str, edges))[0].ring
        assert gbif_rings_search_url(["AAAA"], [ring], (2000, 2020)) == gbif_box_search_url(["AAAA"], edges,
                                                                                           (2000, 2020))
        assert gbif_rings_search_url([], [ring]) == gbif_box_search_url([], edges)


def test_each_shape_is_one_closed_counter_clockwise_geometry():
    from urllib.parse import parse_qs, urlsplit

    from api.gbif_links import gbif_rings_search_url, ring_is_clockwise
    rings = GBIF_CASES[2][1]
    query = parse_qs(urlsplit(gbif_rings_search_url(["BBBB"], rings)).query)
    assert len(query["geometry"]) == 2
    for wkt, ring in zip(query["geometry"], rings):
        points = [tuple(map(float, pair.split())) for pair in wkt[len("POLYGON(("):-2].split(",")]
        assert points[0] == points[-1] and points[0] == ring[0]
        assert not ring_is_clockwise(points[:-1])
        assert sorted(points[:-1]) == sorted(ring)
    # The polygon was given clockwise and is turned round.
    assert ring_is_clockwise(rings[1])


@pytest.mark.skipif(not NODE_READY, reason="needs node and the front end's packages")
def test_the_area_links_match_gbif_links_js_run_by_node(tmp_path):
    """Runs gbifAreaSearchUrl in frontend/src/gbifLinks.js on each case and
    compares the text of every link with gbif_rings_search_url's."""
    from api.gbif_links import gbif_rings_search_url
    cases = [{"rings": [[list(point) for point in ring] for ring in rings], "keys": keys, "years": years}
             for _, rings, keys, years in GBIF_CASES]
    (tmp_path / "cases.json").write_text(json.dumps(cases), encoding="utf-8")
    script = (f"import {{ readFileSync }} from 'node:fs'\n"
              f"import {{ gbifAreaSearchUrl }} from {front_end_module('gbifLinks.js')!r}\n"
              f"const cases = JSON.parse(readFileSync({str(tmp_path / 'cases.json')!r}, 'utf8'))\n"
              "for (const one of cases) console.log(gbifAreaSearchUrl(one))\n")
    output = run_node(tmp_path, script).splitlines()
    expected = [gbif_rings_search_url(keys, rings, years) for _, rings, keys, years in GBIF_CASES]
    for (name, *_), mine, theirs in zip(GBIF_CASES, expected, output):
        assert theirs == mine, name
    assert len(output) == len(GBIF_CASES)
