"""Tests for the reserve layer and the reserve reminder (api/reserves.py):
GET /api/reserves, reserve_names in /api/hexes/{cell} and /api/area, the
note in their CSV downloads, rule A and rule B, the API without the
reserve files, and the privacy rules (nothing takes a reserve as input;
reserve_cells is never a table the API's SQL can reach).

Every reserve here is made up. The test data in conftest.py has hexagon A
(872800b30ffffff at res 7; its parents are 862800b37ffffff,
852800b3fffffff and 842800bffffffff) with AAAA and CCCC, and hexagon B
(8728c9b40ffffff; parents 8628c9b47ffffff, 8528c9b7fffffff,
8428c9bffffffff) with BBBB and DDDD. The made-up reserves:
    90001 "Made-up Creek 1"  touches hexagon A at every size
    90002 "ALPHA LAKE 2"     touches hexagon A at res 7 and res 4 only
    90003 "zeta point 3"     touches hexagon B at res 7 and res 4 only
So hexagon B at res 6 and 5 touches no reserve."""

import csv
import io
import json
import logging

import pandas as pd
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from api.db import open_data
from api.export import RESERVE_NOTE, RESERVE_NOTE_MAX_NAMES
from api.main import create_app
from api.reserves import CELLS_FILE, INFO_FILE, OUTLINES_FILE, ReserveData, reserve_names
from api.settings import RESERVE_REMINDER_RULE, Settings, SettingsError
from conftest import write_test_data

A = {7: "872800b30ffffff", 6: "862800b37ffffff", 5: "852800b3fffffff", 4: "842800bffffffff"}
B = {7: "8728c9b40ffffff", 6: "8628c9b47ffffff", 5: "8528c9b7fffffff", 4: "8428c9bffffffff"}

NAMES = {"90001": "Made-up Creek 1", "90002": "ALPHA LAKE 2", "90003": "zeta point 3"}

CELL_ROWS = ([(res, A[res], "90001") for res in (4, 5, 6, 7)]
             + [(7, A[7], "90002"), (4, A[4], "90002"), (7, B[7], "90003"), (4, B[4], "90003")])

# A box over both hexagons at res 4, and a small one over hexagon A at res
# 7 (the same boxes as test_area.py).
BOTH = {"west": -131.0, "south": 39.5, "east": -124.5, "north": 49.5, "res": 4}
NEAR_A = {"west": -125.25, "south": 39.78, "east": -125.15, "north": 39.85, "res": 7}

INFO = {
    "source_title": "Made-up Lands Test Boundaries",
    "source_publisher": "Test Publisher",
    "licence": "Test Licence",
    "licence_url": "https://example.org/licence",
    "boundary_note": "This data set is not to be used for defining boundaries.",
    "shapefile": "made_up.shp",
    "latest_revdate": "2026-01-02",
    "build_date": "2026-01-03",
}

# Words a route or parameter about reserves would use.
RESERVE_WORDS = ("reserve", "alcode", "nation", "indigenous", "aboriginal")


def square(lon, lat, points=60):
    """A made-up closed ring of points around (lon, lat), with enough
    points that the layer is larger than the 1 KB gzip threshold."""
    ring = [[round(lon + 0.01 * (i % 2), 5), round(lat + 0.0001 * i, 5)] for i in range(points)]
    return {"type": "Polygon", "coordinates": [ring + [ring[0]]]}


def write_reserve_files(folder, cell_rows=CELL_ROWS, names=NAMES, info=INFO, skip=()):
    """Write the three reserve files into folder, leaving out any named in
    skip. One feature carries an extra property, which the layer must not
    pass on."""
    if OUTLINES_FILE not in skip:
        features = [{"type": "Feature", "properties": {"alcode": code, "name": name},
                     "geometry": square(-125.2 + i, 39.8)} for i, (code, name) in enumerate(names.items())]
        features[0]["properties"]["extra"] = "must not be served"
        with open(folder / OUTLINES_FILE, "w", encoding="utf-8") as handle:
            json.dump({"type": "FeatureCollection", "features": features}, handle)
    if CELLS_FILE not in skip:
        frame = pd.DataFrame(cell_rows, columns=["res", "h3_cell", "alcode"])
        frame["res"] = frame["res"].astype("int8")
        frame.to_parquet(folder / CELLS_FILE, index=False)
    if INFO_FILE not in skip:
        with open(folder / INFO_FILE, "w", encoding="utf-8") as handle:
            json.dump(info, handle)
    return folder


def make_client(folder, rule=RESERVE_REMINDER_RULE):
    """A test client on the test data in folder, with the reserve rule."""
    return TestClient(create_app(Settings(data_dir=folder, sensitive_max_res=None,
                                          reserve_reminder_rule=rule)))


@pytest.fixture
def reserve_dir(tmp_path):
    """The test data with the made-up reserve files."""
    return write_reserve_files(write_test_data(tmp_path))


@pytest.fixture
def rclient(reserve_dir):
    """A client with the reserve files and the default rule."""
    with make_client(reserve_dir) as client:
        yield client


@pytest.fixture
def rule_b_client(reserve_dir):
    """A client with the reserve files and rule B."""
    with make_client(reserve_dir, "B") as client:
        yield client


def names_for(client, path, **params):
    """Ask path and return its reserve_names, checking it worked."""
    response = client.get(path, params=params)
    assert response.status_code == 200, response.text
    return response.json()["reserve_names"]


def header_lines(client, path, **params):
    """Ask for a download and return its 4 header lines' text (BOM taken
    off), checking with the csv module that the file has exactly 4 rows of
    one "# " field each (one line of the file each) before the column names."""
    response = client.get(path, params=params)
    assert response.status_code == 200, response.text
    text = response.content.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text, newline="")))
    header = [row[0] for row in rows[:4]]
    assert [len(row) for row in rows[:4]] == [1, 1, 1, 1]
    assert all(line.startswith("# ") for line in header)
    assert sum(1 for row in rows if row and row[0].startswith("# ")) == 4
    assert text.split("\r\n")[4].startswith("specieskey,")
    return header


# --- The layer ---------------------------------------------------------------------


def test_layer_has_outlines_with_alcode_and_name_only_and_the_source(rclient):
    """The layer gives every outline with alcode and name only (an extra
    property in the file is dropped) and the source info."""
    answer = rclient.get("/api/reserves").json()
    assert answer["available"] is True
    features = answer["outlines"]["features"]
    assert {f["properties"]["alcode"]: f["properties"]["name"] for f in features} == NAMES
    assert all(set(f["properties"]) == {"alcode", "name"} for f in features)
    assert all(f["geometry"]["type"] == "Polygon" for f in features)
    for field in ("source_title", "licence", "licence_url", "boundary_note", "shapefile",
                  "latest_revdate", "build_date"):
        assert answer["source"][field] == INFO[field]


def test_layer_is_sent_gzipped(rclient):
    """The layer (over 1 KB) is gzipped for a client that accepts it."""
    response = rclient.get("/api/reserves", headers={"Accept-Encoding": "gzip"})
    assert response.headers["content-encoding"] == "gzip"
    assert response.headers["content-type"] == "application/json"


# --- The hexagon and area answers ------------------------------------------------------


@pytest.mark.parametrize("res, expected", [
    (7, ["ALPHA LAKE 2", "Made-up Creek 1"]),
    (6, ["Made-up Creek 1"]),
    (5, ["Made-up Creek 1"]),
    (4, ["ALPHA LAKE 2", "Made-up Creek 1"]),
])
def test_hexagon_names_its_reserves_sorted(rclient, res, expected):
    """A hexagon's answer names the reserves it touches, sorted ignoring
    case, at every size under rule A, in both the all-species panel and the
    one-species panel."""
    assert names_for(rclient, f"/api/hexes/{A[res]}") == expected
    assert names_for(rclient, f"/api/hexes/{A[res]}", species="AAAA") == expected


def test_hexagon_without_a_reserve_gives_an_empty_list(rclient):
    """Hexagon B at res 6 touches no reserve."""
    assert names_for(rclient, f"/api/hexes/{B[6]}") == []


def test_area_names_every_reserve_any_hexagon_touches(rclient):
    """An area names every reserve touched by any of its hexagons, each
    once, sorted."""
    assert names_for(rclient, "/api/area", **BOTH) == ["ALPHA LAKE 2", "Made-up Creek 1", "zeta point 3"]
    assert names_for(rclient, "/api/area", **NEAR_A) == ["ALPHA LAKE 2", "Made-up Creek 1"]


def test_the_default_rule_is_a():
    """The site's rule is A, and the settings use it unless told otherwise."""
    assert RESERVE_REMINDER_RULE == "A"
    assert Settings().reserve_reminder_rule == "A"


def test_rule_b_names_reserves_only_at_res_6_and_7(rule_b_client):
    """Under rule B the larger sizes give an empty list, for hexagons and
    areas, and res 6 and 7 still name their reserves."""
    assert names_for(rule_b_client, f"/api/hexes/{A[7]}") == ["ALPHA LAKE 2", "Made-up Creek 1"]
    assert names_for(rule_b_client, f"/api/hexes/{A[6]}") == ["Made-up Creek 1"]
    assert names_for(rule_b_client, f"/api/hexes/{A[5]}") == []
    assert names_for(rule_b_client, f"/api/hexes/{A[4]}") == []
    assert names_for(rule_b_client, "/api/area", **BOTH) == []
    assert names_for(rule_b_client, "/api/area", **NEAR_A) == ["ALPHA LAKE 2", "Made-up Creek 1"]
    # The layer is the same under both rules.
    assert rule_b_client.get("/api/reserves").json()["available"] is True


def test_an_unknown_rule_stops_the_api(reserve_dir):
    """A rule that is neither A nor B stops the start-up with a clear
    message, so a typing mistake cannot quietly turn the reminder off."""
    with pytest.raises(SettingsError, match="reserve reminder rule"):
        open_data(Settings(data_dir=reserve_dir, reserve_reminder_rule="C"))


def test_names_are_sorted_ignoring_case_and_given_once():
    """Two reserves with the same name give it once; case does not change
    the order."""
    reserves = ReserveData(names={"1": "b lake", "2": "A Creek", "3": "b lake"},
                           cells={(7, "x"): ("1", "2"), (7, "y"): ("3",)}, layer=b"{}")
    assert reserve_names(reserves, "A", 7, ["x", "y"]) == ["A Creek", "b lake"]
    assert reserve_names(reserves, "B", 5, ["x"]) == []


# --- The downloads -------------------------------------------------------------------


def test_hexagon_download_has_the_note_at_the_end_of_line_2(rclient):
    """A hexagon download whose hexagon touches reserves ends line 2 with
    the note naming them, and still has exactly 4 header lines."""
    header = header_lines(rclient, f"/api/export/hexes/{A[7]}")
    note = RESERVE_NOTE.format(names="ALPHA LAKE 2, Made-up Creek 1")
    assert header[1].startswith("# List: species in H3 hexagon 872800b30ffffff")
    assert header[1].endswith("sorted by most records first." + note)
    assert header[1].endswith(
        " This area includes First Nations reserve land (ALPHA LAKE 2, Made-up Creek 1). Some of these "
        "records may have been collected on a reserve. If you use them, check whether they were, and if "
        "so, whether the Nation agreed to the collection. See the Indigenous data page of the site.")
    assert header[3] == "# To read in R: read.csv(file, skip = 4)"


def test_area_download_has_the_note_with_every_name(rclient):
    """An area download names every reserve its hexagons touch, on line 2."""
    header = header_lines(rclient, "/api/export/area", **BOTH)
    assert header[1].startswith("# List: species in the ")
    assert header[1].endswith(RESERVE_NOTE.format(names="ALPHA LAKE 2, Made-up Creek 1, zeta point 3"))


def test_no_note_without_a_reserve(rclient, rule_b_client):
    """No note when the hexagon touches no reserve, or when the rule gives
    none at this size; rank and dataset downloads never have one."""
    assert "reserve" not in header_lines(rclient, f"/api/export/hexes/{B[6]}")[1]
    assert "reserve" not in header_lines(rule_b_client, "/api/export/area", **BOTH)[1]
    assert "reserve" not in header_lines(rule_b_client, f"/api/export/hexes/{A[4]}")[1]
    rank = header_lines(rclient, "/api/export/ranks/kingdom", kingdom="Animalia")
    assert not any("reserve" in line for line in rank)
    response = rclient.get("/api/export/datasets")
    assert response.status_code == 200
    assert "reserve" not in response.content.decode("utf-8-sig").split("\r\n", 4)[1]


def many_reserves(count):
    """count made-up reserves, all touching hexagon A at res 7, named so
    that they sort in number order: "Test Reserve 0001" and up."""
    names = {f"8{i:04d}": f"Test Reserve {i:04d}" for i in range(1, count + 1)}
    return names, [(7, A[7], code) for code in names]


@pytest.mark.parametrize("count, listed, more", [
    (10, 10, ""),
    (11, 10, ", and 1 more reserve"),
    (1500, 10, ", and 1,490 more reserves"),
])
def test_the_note_lists_at_most_10_names(tmp_path, count, listed, more):
    """The note names at most RESERVE_NOTE_MAX_NAMES reserves, the first
    ones in sorted order, then how many more ("1 more reserve" for one);
    10 or fewer are all listed. The header keeps exactly 4 lines, and the
    API answer still holds every name."""
    assert RESERVE_NOTE_MAX_NAMES == 10
    names, rows = many_reserves(count)
    folder = write_reserve_files(write_test_data(tmp_path), cell_rows=rows, names=names)
    with make_client(folder) as client:
        header = header_lines(client, f"/api/export/hexes/{A[7]}")
        assert names_for(client, f"/api/hexes/{A[7]}") == sorted(names.values())
    first = ", ".join(f"Test Reserve {i:04d}" for i in range(1, listed + 1))
    assert header[1].endswith(RESERVE_NOTE.format(names=first + more))
    assert header[1].count("Test Reserve") == listed
    assert ("more reserve" in header[1]) == (count > 10)


# --- Without the reserve files ----------------------------------------------------------


@pytest.mark.parametrize("skip", [(OUTLINES_FILE, CELLS_FILE, INFO_FILE), (CELLS_FILE,), (OUTLINES_FILE,),
                                  (INFO_FILE,)])
def test_the_api_works_without_the_reserve_files(tmp_path, caplog, skip):
    """With any reserve file missing, the API starts, logs one clear
    message, the layer has no outlines, and every list of names is empty
    (so no download has the note)."""
    folder = write_reserve_files(write_test_data(tmp_path), skip=skip)
    with caplog.at_level(logging.WARNING, logger="api.reserves"):
        with make_client(folder) as client:
            layer = client.get("/api/reserves").json()
            assert layer == {"available": False, "source": None, "outlines": None}
            assert names_for(client, f"/api/hexes/{A[7]}") == []
            assert names_for(client, "/api/area", **BOTH) == []
            assert "reserve" not in header_lines(client, f"/api/export/hexes/{A[7]}")[1]
    messages = [record.getMessage() for record in caplog.records if record.name == "api.reserves"]
    assert len(messages) == 1
    assert "Reserve outlines and reminders are OFF" in messages[0]
    assert all(name in messages[0] for name in skip)


def test_a_cell_naming_a_reserve_with_no_outline_turns_the_reserves_off(tmp_path, caplog):
    """A cell row for an alcode with no outline (no name to show) is a bad
    file: the reserves are off, with the reason logged."""
    folder = write_reserve_files(write_test_data(tmp_path), cell_rows=CELL_ROWS + [(7, A[7], "99999")])
    with caplog.at_level(logging.WARNING, logger="api.reserves"):
        data = open_data(Settings(data_dir=folder))
    try:
        assert not data.reserves.available
        assert "99999" in data.reserves.problem
    finally:
        data.close()


# --- Privacy ----------------------------------------------------------------------------


def test_no_route_or_parameter_is_about_reserves(rclient):
    """Go through every API route: none may take a reserve (alcode or name)
    as input. Only /api/reserves may mention reserves, and it takes no
    parameters at all. This covers the parameters FastAPI knows about; the
    rank names some routes read from the query string directly (kingdom to
    genus) are checked by name too."""
    from api.db import RANKS

    routes = [route for route in rclient.app.routes if isinstance(route, APIRoute)]
    assert any(route.path == "/api/reserves" for route in routes)
    for route in routes:
        dependant = route.dependant
        params = [param.name for param in (dependant.path_params + dependant.query_params
                                           + dependant.header_params + dependant.cookie_params
                                           + dependant.body_params)]
        if route.path == "/api/reserves":
            assert params == [], f"/api/reserves takes parameters: {params}"
            assert route.methods == {"GET"}
            continue
        assert not any(word in route.path.lower() for word in RESERVE_WORDS), route.path
        for name in params + list(RANKS):
            assert not any(word in name.lower() for word in RESERVE_WORDS), f"{route.path} takes {name}"


def test_reserve_cells_is_never_a_table_the_api_can_query(rclient):
    """reserve_cells is read into Python only: the API's DuckDB connection
    has no table or view about reserves, so no query can join it to the
    records, species or datasets."""
    connection = rclient.app.state.data.connection
    names = [row[0] for row in connection.execute(
        "SELECT table_name FROM information_schema.tables").fetchall()]
    assert names and not any("reserve" in name.lower() or "alcode" in name.lower() for name in names)
    assert rclient.app.state.data.reserves.available
