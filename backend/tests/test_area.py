"""Tests for the area search (api/area.py): GET /api/area and
GET /api/export/area. The client and the test data are in conftest.py.
Hexagon A (872800b30ffffff, near 39.81 N, 125.20 W) holds AAAA (the robin:
3 records from 2020 and 2 with no year) and CCCC (1 record from 2021);
hexagon B (8728c9b40ffffff, near 48.86 N, 130.32 W) holds BBBB (3 records
from 2019, old key OLD1 merged into it) and DDDD (1 record from 2018). The
test data's years run from 2018 to 2021."""

import csv
import io
import json
import math
from urllib.parse import parse_qs, urlsplit

import h3
import pytest

import api.area
from api.area import (EARTH_RADIUS_KM, MAX_AREA_HEXAGONS, MIN_AREA_HEXAGONS, box_area_km2, box_cells,
                      minimum_area_km2)
from api.export import export_date
from api.gbif_links import box_wkt, gbif_box_search_url
from frontend_node import FRONTEND, NODE_READY, front_end_module, run_node

# A box over both hexagons, at resolution 4 (several hundred hexagons, two
# of them with records), and a small box over hexagon A alone at 7.
BOTH = {"west": -131.0, "south": 39.5, "east": -124.5, "north": 49.5, "res": 4}
NEAR_A = {"west": -125.25, "south": 39.78, "east": -125.15, "north": 39.85, "res": 7}
ANIMALIA = {"rank": "kingdom", "kingdom": "Animalia"}


def area(client, **params):
    """Ask /api/area and return the JSON, checking it worked."""
    response = client.get("/api/area", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def listed(answer):
    """The species of an answer as [(key, records)], in order."""
    return [(row["specieskey"], row["records"]) for row in answer["results"]]


def box_of(params):
    """The box of a request as (west, south, east, north)."""
    return tuple(params[edge] for edge in ("west", "south", "east", "north"))


def read_download(response):
    """Return (the 4 header lines' text, column names, rows as dicts) of a
    download, checking its BOM, that it is a CSV attachment, and that each
    header line is one quoted field (read back with the csv module)."""
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert response.content.startswith(b'\xef\xbb\xbf"# BCBN ')
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"), newline="")))
    assert [len(row) for row in rows[:4]] == [1, 1, 1, 1]
    columns = rows[4]
    return [row[0] for row in rows[:4]], columns, [dict(zip(columns, row)) for row in rows[5:]]


# --- The answer ----------------------------------------------------------------------


def test_the_box_its_hexagons_and_the_totals(client):
    answer = area(client, **BOTH)
    assert answer["box"] == {"west": -131.0, "south": 39.5, "east": -124.5, "north": 49.5}
    assert answer["res"] == 4
    assert answer["hexagon_count"] == len(box_cells(box_of(BOTH), 4))
    assert {"842800bffffffff", "8428c9bffffffff"} <= set(box_cells(box_of(BOTH), 4))
    assert (answer["records"], answer["species"], answer["matching"]) == (10, 4, 4)
    assert listed(answer) == [("AAAA", 5), ("BBBB", 3), ("CCCC", 1), ("DDDD", 1)]
    assert answer["years_counted"] is None
    assert answer["box_area_km2"] == round(box_area_km2(box_of(BOTH)), 1)
    assert answer["minimum_area_km2"] == round(MIN_AREA_HEXAGONS * h3.average_hexagon_area(4, "km^2"), 1)
    assert answer["rank"] is None and answer["sensitive_max_res"] is None


def test_a_small_box_counts_only_the_hexagons_it_touches(client):
    answer = area(client, **NEAR_A)
    assert "872800b30ffffff" in box_cells(box_of(NEAR_A), 7)
    assert listed(answer) == [("AAAA", 5), ("CCCC", 1)]
    assert (answer["records"], answer["species"]) == (6, 2)


@pytest.mark.parametrize("options", [{}, {"year_from": 2020}, {"year_to": 2019}, {"q": "robin"},
                                     {"status": "none"}, {"sort": "fewest_records"}, {"sort": "english"},
                                     ANIMALIA, {"rank": "kingdom", "kingdom": "Plantae", "year_from": 2019}])
def test_totals_and_list_equal_the_hexagons_asked_one_by_one(client, options):
    """Every hexagon under the box asked for on its own: the area's records
    are their sum, its species are counted once, and each listed species
    has its records summed over them."""
    answer = area(client, **BOTH, **options, limit=200)
    records, species, left_out, outside, per_species = 0, set(), 0, 0, {}
    for cell in box_cells(box_of(BOTH), 4):
        one = client.get(f"/api/hexes/{cell}", params={**options, "limit": 200}).json()
        records += one["records"]
        left_out += one["records_no_year_left_out"]
        outside += one["records_outside_years"]
        species |= {key for key, _ in listed(client.get(f"/api/hexes/{cell}", params={
            **{k: v for k, v in options.items() if k not in ("q", "status")}, "limit": 200}).json())}
        for key, count in listed(one):
            per_species[key] = per_species.get(key, 0) + count
    assert answer["records"] == records
    assert answer["species"] == len(species)
    assert (answer["records_no_year_left_out"], answer["records_outside_years"]) == (left_out, outside)
    assert dict(listed(answer)) == per_species
    assert answer["matching"] == len(per_species)


def test_filters_sort_years_and_rank_apply(client):
    assert listed(area(client, **BOTH, year_from=2020)) == [("AAAA", 3), ("CCCC", 1)]
    years = area(client, **BOTH, year_from=2020)
    assert (years["records"], years["records_no_year_left_out"], years["records_outside_years"]) == (4, 2, 4)
    assert years["years_counted"] == [2020, 2021]
    assert listed(area(client, **BOTH, q="robin")) == [("AAAA", 5)]
    assert [key for key, _ in listed(area(client, **BOTH, status="none"))] == ["BBBB", "CCCC", "DDDD"]
    assert [key for key, _ in listed(area(client, **BOTH, sort="fewest_records"))] == ["CCCC", "DDDD", "BBBB", "AAAA"]
    plants = area(client, **BOTH, rank="kingdom", kingdom="Plantae")
    assert listed(plants) == [("BBBB", 3), ("DDDD", 1)]
    assert (plants["records"], plants["species"]) == (4, 2)
    assert plants["rank"]["name"] == "Plantae"


def test_paging(client):
    first = area(client, **BOTH, limit=2)
    second = area(client, **BOTH, limit=2, offset=2)
    assert [key for key, _ in listed(first) + listed(second)] == ["AAAA", "BBBB", "CCCC", "DDDD"]
    assert (first["matching"], first["offset"], second["offset"]) == (4, 0, 2)


# --- The GBIF links --------------------------------------------------------------------


def geometry_of(link):
    """The polygon of a GBIF link as [(lng, lat)]."""
    wkt = parse_qs(urlsplit(link).query)["geometry"][0]
    return [tuple(map(float, point.split())) for point in wkt[len("POLYGON(("):-2].split(",")]


def test_each_species_links_to_its_records_inside_the_box(client):
    answer = area(client, **BOTH, year_from=2019)
    willow = next(row for row in answer["results"] if row["specieskey"] == "BBBB")
    assert willow["gbif_link"] == gbif_box_search_url(["BBBB", "OLD1"], box_of(BOTH), (2019, 2021))
    query = parse_qs(urlsplit(willow["gbif_link"]).query)
    assert query["taxonKey"] == ["BBBB", "OLD1"]
    assert query["year"] == ["2019,2021"]
    assert query["occurrenceStatus"] == ["PRESENT"]


def test_the_box_polygon_is_closed_and_counter_clockwise(client):
    link = area(client, **NEAR_A)["results"][0]["gbif_link"]
    points = geometry_of(link)
    assert len(points) == 5 and points[0] == points[-1]
    assert points == [(-125.25, 39.78), (-125.15, 39.78), (-125.15, 39.85), (-125.25, 39.85), (-125.25, 39.78)]
    twice_area = sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(points, points[1:]))
    assert twice_area > 0
    assert box_wkt(-120.6, 50.55, -120.1, 50.8) == (
        "POLYGON((-120.600000 50.550000,-120.100000 50.550000,-120.100000 50.800000,"
        "-120.600000 50.800000,-120.600000 50.550000))")


# --- The download --------------------------------------------------------------------


def test_the_download_holds_the_whole_list_with_four_header_lines(client):
    response = client.get("/api/export/area", params={**BOTH, **ANIMALIA, "year_from": 2020, "offset": 1,
                                                      "limit": 1})
    header, columns, rows = read_download(response)
    assert header[0] == f"# BCBN species list for an area, exported {export_date()}"
    count = len(box_cells(box_of(BOTH), 4))
    # The hexagons' size, never their resolution number.
    assert header[1] == (f"# List: species in the {count:,} hexagons of about 1,770 km² each that touch the box "
                         "west -131, south 39.5, east -124.5, north 49.5; only kingdom Animalia "
                         "(kingdom Animalia); records from 2020 to 2021; sorted by most records first")
    assert header[2].startswith("# Source: GBIF occurrence download https://doi.org/10.15468/dl.test;")
    assert header[3] == "# To read in R: read.csv(file, skip = 4)"
    assert columns == ["specieskey", "scientific_name", "english_name", "bcsee_status", "gbif_link"]
    # Paging options never cut the download.
    assert [row["specieskey"] for row in rows] == ["AAAA", "CCCC"]
    assert rows[0]["scientific_name"] == "Turdus migratorius" and rows[0]["bcsee_status"] == "Yellow"
    assert rows[0]["gbif_link"] == gbif_box_search_url(["AAAA"], box_of(BOTH), (2020, 2021))
    assert response.headers["content-disposition"] == (
        f'attachment; filename="bcbn_species_area_Animalia_2020-2021_{export_date()}.csv"')
    assert "resolution" not in "".join(header)


def test_the_download_matches_the_list(client):
    for options in ({}, {"q": "robin"}, {"sort": "english"}, {"status": "yellow"}):
        answer = area(client, **BOTH, **options, limit=200)
        _, _, rows = read_download(client.get("/api/export/area", params={**BOTH, **options}))
        assert [row["specieskey"] for row in rows] == [key for key, _ in listed(answer)]
    response = client.get("/api/export/area", params=NEAR_A)
    assert response.headers["content-disposition"] == f'attachment; filename="bcbn_species_area_{export_date()}.csv"'


# --- Bad requests -----------------------------------------------------------------------


@pytest.mark.parametrize("changes, message", [
    ({"west": None}, "give the box's west edge"),
    ({"north": ""}, "give the box's north edge"),
    ({"west": "abc"}, "west must be a number"),
    ({"south": "nan"}, "south must be between -90 and 90"),
    ({"east": "inf"}, "east must be between -180 and 180"),
    ({"west": -200}, "west must be between -180 and 180"),
    ({"north": 95}, "north must be between -90 and 90"),
    ({"west": -124.5, "east": -131.0}, "west (-124.5) must be less than east (-131.0)"),
    ({"east": -131.0}, "must be less than east"),
    ({"south": 49.5}, "south (49.5) must be less than north (49.5)"),
    ({"res": None}, "res must be one of 4, 5, 6, 7"),
    ({"res": 3}, "res must be one of 4, 5, 6, 7"),
    ({"res": 8}, "res must be one of 4, 5, 6, 7"),
    ({"res": "six"}, "res must be one of 4, 5, 6, 7"),
    ({"status": "purple"}, "status must be among"),
    ({"sort": "size"}, "sort must be one of"),
    ({"q": "a"}, "at least 2"),
    ({"year_from": 2021, "year_to": 2019}, "year"),
    ({"rank": "kingdom"}, "give the kingdom's name"),
    ({"species": "AAAA"}, "does not take species="),
    ({"species": ""}, "does not take species="),
])
def test_bad_requests_give_400_with_a_clear_message(client, changes, message):
    params = {key: value for key, value in {**BOTH, **changes}.items() if value is not None}
    for path in ("/api/area", "/api/export/area"):
        response = client.get(path, params=params)
        assert response.status_code == 400, (path, response.text)
        assert message in response.json()["detail"], response.json()["detail"]


def test_a_rank_no_species_has_gives_404(client):
    for path in ("/api/area", "/api/export/area"):
        assert client.get(path, params={**BOTH, "rank": "kingdom", "kingdom": "Fungi"}).status_code == 404


# --- How small and how large ------------------------------------------------------------


def box_with_area(area_km2, south=49.0, north=49.5, west=-125.0):
    """A box from south to north whose area is area_km2 (its width worked
    out from the area formula)."""
    width = math.degrees(area_km2 / (EARTH_RADIUS_KM ** 2 * (math.sin(math.radians(north))
                                                           - math.sin(math.radians(south)))))
    return {"west": west, "south": south, "east": west + width, "north": north}


@pytest.mark.parametrize("res", [4, 5, 6, 7])
def test_the_smallest_box_at_each_resolution(client, res):
    smallest = MIN_AREA_HEXAGONS * h3.average_hexagon_area(res, "km^2")
    assert minimum_area_km2(res) == pytest.approx(smallest)
    under = client.get("/api/area", params={**box_with_area(smallest * 0.99), "res": res})
    assert under.status_code == 400
    detail = under.json()["detail"]
    assert f"The box is about {smallest * 0.99:,.1f} km², smaller than the minimum of {smallest:,.1f} km²" in detail
    assert (f"for hexagons of about {h3.average_hexagon_area(res, 'km^2'):,.1f} km² ({MIN_AREA_HEXAGONS} times "
            "their average area)") in detail
    assert "resolution" not in detail
    over = client.get("/api/area", params={**box_with_area(smallest * 1.01), "res": res})
    assert over.status_code == 200, over.text
    assert over.json()["box_area_km2"] == round(smallest * 1.01, 1)


def test_the_minimum_is_four_hexagons():
    assert MIN_AREA_HEXAGONS == 4


def test_the_box_area_is_on_h3s_sphere():
    whole = box_area_km2((-180, -90, 180, 90))
    assert whole == pytest.approx(4 * math.pi * EARTH_RADIUS_KM ** 2)
    # H3's own area of a cell, summed over the cells that tile a region,
    # agrees with the formula for a box: one resolution 0 cell's area is
    # measured on the same sphere.
    assert sum(h3.cell_area(cell, "km^2") for cell in h3.get_res0_cells()) == pytest.approx(whole, rel=1e-9)


def test_a_box_over_the_most_hexagons_is_refused(client, monkeypatch):
    all_bc_at_7 = {"west": -139.1, "south": 48.2, "east": -114.0, "north": 60.0, "res": 7}
    response = client.get("/api/area", params=all_bc_at_7)
    assert response.status_code == 400
    assert f"more than the {MAX_AREA_HEXAGONS:,} an area search can count" in response.json()["detail"]
    assert "hexagons of about 5.2 km²" in response.json()["detail"]
    assert "use larger hexagons" in response.json()["detail"]
    assert "resolution" not in response.json()["detail"]
    # A box whose estimate passes but whose real count does not.
    monkeypatch.setattr(api.area, "MAX_AREA_HEXAGONS", len(box_cells(box_of(BOTH), 4)) - 1)
    response = client.get("/api/area", params=BOTH)
    assert response.status_code == 400
    assert f"touches about {len(box_cells(box_of(BOTH), 4)):,} hexagons" in response.json()["detail"]


# --- Hidden species ---------------------------------------------------------------------


def test_hidden_species_never_appear(client, monkeypatch):
    """A stand-in rule hides the robin at resolutions finer than 5: it is
    left out of the list, the totals and the download there, and the rule
    is asked with the area's resolution."""
    asked = []

    def stand_in_rule(settings, res):
        """Hide AAAA finer than resolution 5, and record what was asked."""
        asked.append(res)
        return frozenset({"AAAA"}) if res is not None and res > 5 else frozenset()

    monkeypatch.setattr(api.area, "hidden_species", stand_in_rule)
    fine = area(client, **NEAR_A)
    assert listed(fine) == [("CCCC", 1)]
    assert (fine["records"], fine["species"]) == (1, 1)
    assert 7 in asked
    response = client.get("/api/export/area", params=NEAR_A)
    assert "AAAA" not in response.content.decode("utf-8-sig")
    assert "Turdus" not in response.content.decode("utf-8-sig")
    # At resolution 4 the rule hides nothing, so the robin is listed.
    assert ("AAAA", 5) in listed(area(client, **BOTH))


# --- The same hexagons in the front end ---------------------------------------------------


@pytest.mark.skipif(not NODE_READY, reason="needs node and the front end's packages (npm install in frontend/)")
def test_h3_js_finds_the_same_hexagons(tmp_path):
    """The front end's h3-js, asked for the hexagons overlapping each box
    (polygonToCellsExperimental with containmentOverlapping, the corners as
    [lat, lng]), finds exactly the ones the API counts."""
    boxes = [(box_of(BOTH), 4), (box_of(NEAR_A), 7), ((-120.6, 50.55, -120.1, 50.8), 6),
             ((-120.6, 50.55, -120.1, 50.8), 7), ((-123.3, 48.3, -122.9, 48.7), 5),
             ((-129.123456, 52.5, -127.000001, 54.25), 6), ((-125.0, 49.0, -124.9, 49.07), 7)]
    h3_js = (FRONTEND / "node_modules" / "h3-js" / "dist" / "h3-js.es.js").as_uri()
    script = (f"import {{ polygonToCellsExperimental, POLYGON_TO_CELLS_FLAGS }} from {h3_js!r}\n"
              f"const boxes = {json.dumps([[list(box), res] for box, res in boxes])}\n"
              "for (const [[w, s, e, n], res] of boxes) {\n"
              "  const cells = polygonToCellsExperimental([[s, w], [s, e], [n, e], [n, w]], res,\n"
              "    POLYGON_TO_CELLS_FLAGS.containmentOverlapping)\n"
              "  console.log(JSON.stringify(cells.sort()))\n"
              "}\n")
    found = [json.loads(line) for line in run_node(tmp_path, script).splitlines()]
    assert found == [box_cells(box, res) for box, res in boxes]
    assert all(found)


@pytest.mark.skipif(not NODE_READY, reason="needs node and the front end's packages (npm install in frontend/)")
def test_the_front_ends_area_rules_match_the_apis(tmp_path):
    """Runs the front end's own area.js and gbifLinks.js with node: its
    smallest and largest box, its sphere, every box's area and minimum, the
    hexagons it outlines and its GBIF link for the box are the API's."""
    boxes = [((-120.6, 50.55, -120.1, 50.8), 6), ((-120.6, 50.55, -120.1, 50.8), 7), (box_of(BOTH), 4),
             ((-123.3, 48.3, -122.9, 48.7), 5), ((-129.1234, 52.5, -127.0001, 54.25), 6)]
    script = (f"import {{ MIN_AREA_HEXAGONS, MAX_AREA_HEXAGONS, EARTH_RADIUS_KM, boxAreaKm2, minimumAreaKm2, boxCells }}"
              f" from {front_end_module('area.js')!r}\n"
              f"import {{ gbifBoxSearchUrl }} from {front_end_module('gbifLinks.js')!r}\n"
              f"const boxes = {json.dumps([[list(box), res] for box, res in boxes])}\n"
              "console.log(JSON.stringify({ MIN_AREA_HEXAGONS, MAX_AREA_HEXAGONS, EARTH_RADIUS_KM }))\n"
              "for (const [[west, south, east, north], res] of boxes) {\n"
              "  const box = { west, south, east, north }\n"
              "  console.log(JSON.stringify({ area: boxAreaKm2(box), minimum: minimumAreaKm2(res),\n"
              "    cells: boxCells(box, res), link: gbifBoxSearchUrl({ box, years: [2000, 2020] }),\n"
              "    linkWithKeys: gbifBoxSearchUrl({ keys: ['BBBB', 'OLD1'], box }) }))\n"
              "}\n")
    lines = [json.loads(line) for line in run_node(tmp_path, script).splitlines()]
    assert lines[0] == {"MIN_AREA_HEXAGONS": MIN_AREA_HEXAGONS, "MAX_AREA_HEXAGONS": MAX_AREA_HEXAGONS,
                        "EARTH_RADIUS_KM": EARTH_RADIUS_KM}
    for (box, res), found in zip(boxes, lines[1:]):
        assert found["area"] == pytest.approx(box_area_km2(box), rel=1e-12)
        assert found["minimum"] == pytest.approx(minimum_area_km2(res), rel=1e-12)
        assert found["cells"] == box_cells(box, res)
        assert found["link"] == gbif_box_search_url([], box, (2000, 2020))
        assert found["linkWithKeys"] == gbif_box_search_url(["BBBB", "OLD1"], box)
