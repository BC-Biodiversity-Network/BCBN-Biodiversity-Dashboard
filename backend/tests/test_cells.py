"""Tests for GET /api/hexes/{cell} (api/cells.py) and the sensitive-species
hook it goes through (api/sensitive.py). The client and the test species
are in conftest.py. Hexagon A (872800b30ffffff at resolution 7) holds AAAA
(the robin: 3 records from 2020 and 2 with no year) and CCCC (1 record from
2021); hexagon B (8728c9b40ffffff) holds BBBB (3 records from 2019, old key
OLD1 merged into it) and DDDD (1 record from 2018). The test data's years
run from 2018 to 2021."""

import pytest
from fastapi.testclient import TestClient

import api.cells
from api.main import create_app
from api.sensitive import hidden_species
from api.settings import Settings, SettingsError
from conftest import write_test_data
from test_overview import HEX_WITH_NO_KEY_SQL

CELL_A = "872800b30ffffff"
CELL_B = "8728c9b40ffffff"


def hexagon(client, cell, **params):
    """Ask for one hexagon and return the JSON, checking it worked."""
    response = client.get(f"/api/hexes/{cell}", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def listed(result):
    """The species of an answer as [(key, records)], in order."""
    return [(row["specieskey"], row["records"]) for row in result["results"]]


def test_species_most_records_first_with_their_names_and_statuses(client):
    result = hexagon(client, CELL_A)
    assert (result["cell"], result["res"]) == (CELL_A, 7)
    assert (result["records"], result["species"], result["matching"]) == (6, 2, 2)
    assert listed(result) == [("AAAA", 5), ("CCCC", 1)]
    robin = result["results"][0]
    assert robin["col_name"] == "Turdus migratorius"
    assert robin["english_name"] == "American Robin"
    assert robin["statuses"] == ["Yellow"]
    assert robin["name_source"] == "Catalogue of Life, via GBIF"
    assert result["results"][1]["english_name"] is None and result["results"][1]["statuses"] == []
    assert result["english_name_source"] == "BC Species and Ecosystems Explorer (BCSEE)"
    assert result["years_counted"] is None
    assert (result["records_no_year_left_out"], result["records_outside_years"]) == (0, 0)
    assert result["species_asked_for"] is None


@pytest.mark.parametrize("cell, res", [("842800bffffffff", 4), ("852800b3fffffff", 5),
                                       ("862800b37ffffff", 6), (CELL_A, 7)])
def test_resolution_is_read_from_the_cell_id(client, cell, res):
    result = hexagon(client, cell)
    assert result["res"] == res
    assert listed(result) == [("AAAA", 5), ("CCCC", 1)]


@pytest.fixture
def client_with_unkeyed_record(tmp_path):
    """The test data with the extra record that has no specieskey (in hexagon B)."""
    write_test_data(tmp_path, hex_sql=HEX_WITH_NO_KEY_SQL)
    with TestClient(create_app(Settings(data_dir=tmp_path, sensitive_max_res=None))) as test_client:
        yield test_client


@pytest.mark.parametrize("years", [{}, {"year_from": 2019}, {"year_to": 2019}, {"year_from": 2020, "year_to": 2020}])
@pytest.mark.parametrize("res", [4, 5, 6, 7])
def test_totals_match_the_overview_hover_box(client_with_unkeyed_record, res, years):
    """For every hexagon and year range, the records and species match the
    overview map's, including the record with no specieskey (in records,
    not in species, and never listed)."""
    overview = client_with_unkeyed_record.get("/api/overview/hexes", params={"res": res, **years}).json()
    for cell, records, species in zip(overview["cells"], overview["occurrences"], overview["distinct_species"]):
        result = hexagon(client_with_unkeyed_record, cell, **years)
        assert (result["records"], result["species"]) == (records, species)
        assert all(row["specieskey"] for row in result["results"])
        assert len(result["results"]) == species


def test_year_filter_leaves_out_records_with_no_year_and_says_how_many(client):
    result = hexagon(client, CELL_A, year_to=2020)
    assert result["years_counted"] == [2018, 2020]
    assert listed(result) == [("AAAA", 3)]
    assert (result["records"], result["species"]) == (3, 1)
    assert result["records_no_year_left_out"] == 2
    assert result["records_outside_years"] == 1
    both = hexagon(client, CELL_A, year_from=2020)
    assert listed(both) == [("AAAA", 3), ("CCCC", 1)]


def test_filter_by_any_searchable_name(client):
    assert listed(hexagon(client, CELL_A, q="robin")) == [("AAAA", 5)]
    assert listed(hexagon(client, CELL_A, q="Bombus vosnesenskii")) == [("CCCC", 1)]
    none = hexagon(client, CELL_A, q="salix")
    assert (none["matching"], none["results"]) == (0, [])
    # The totals stay those of the whole hexagon.
    assert (none["records"], none["species"]) == (6, 2)
    assert client.get(f"/api/hexes/{CELL_A}", params={"q": "x"}).status_code == 400


def test_paging(client):
    second = hexagon(client, CELL_A, limit=1, offset=1)
    assert listed(second) == [("CCCC", 1)]
    assert (second["matching"], second["offset"], second["limit"]) == (2, 1, 1)
    assert hexagon(client, CELL_A, offset=5)["results"] == []
    for params in ({"limit": 0}, {"limit": 201}, {"offset": -1}):
        assert client.get(f"/api/hexes/{CELL_A}", params=params).status_code == 422


def test_the_species_asked_for_with_its_records_here(client):
    asked = hexagon(client, CELL_A, species="CCCC")["species_asked_for"]
    assert (asked["specieskey"], asked["records"], asked["merged_from"]) == ("CCCC", 1, None)
    elsewhere = hexagon(client, CELL_A, species="BBBB")["species_asked_for"]
    assert elsewhere["records"] == 0
    merged = hexagon(client, CELL_B, species="OLD1")["species_asked_for"]
    assert (merged["specieskey"], merged["requested_key"], merged["merged_from"], merged["records"]) == (
        "BBBB", "OLD1", "OLD1", 3)
    assert merged["col_name"] == "Salix scouleriana"
    assert client.get(f"/api/hexes/{CELL_A}", params={"species": "NOPE"}).status_code == 404
    assert client.get(f"/api/hexes/{CELL_A}", params={"species": "ZZZZ"}).status_code == 404


@pytest.mark.parametrize("cell", ["abc", "882800b30ffffff", "832800bffffffff", "872800b30fffff",
                                  "872800b30fffffg", "772800b30ffffff"])
def test_a_cell_id_that_is_not_a_hexagon_at_4_to_7_is_refused(client, cell):
    response = client.get(f"/api/hexes/{cell}")
    assert response.status_code == 400
    assert "H3 hexagon id" in response.json()["detail"]


def test_a_cell_id_in_capitals_is_read_and_an_empty_hexagon_is_empty(client):
    assert listed(hexagon(client, CELL_A.upper())) == [("AAAA", 5), ("CCCC", 1)]
    empty = hexagon(client, "872800b31ffffff")
    assert (empty["records"], empty["species"], empty["results"]) == (0, 0, [])


def test_bad_year_range_is_refused(client):
    assert client.get(f"/api/hexes/{CELL_A}", params={"year_from": 2021, "year_to": 2019}).status_code == 400
    assert client.get(f"/api/hexes/{CELL_A}", params={"year_from": 1700}).status_code == 400


def test_sensitive_setting_hides_nothing_while_it_is_empty():
    assert hidden_species(Settings(sensitive_max_res=None), 7) == frozenset()
    # A value stops here too, as it stops the API at start-up.
    with pytest.raises(SettingsError, match="not implemented"):
        hidden_species(Settings(sensitive_max_res=5), 7)


def test_the_endpoint_applies_the_sensitive_setting(client, monkeypatch):
    """The endpoint asks sensitive.hidden_species, with the API's own
    settings and the hexagon's resolution, which species to hide, and leaves
    them out of the list, the totals and the species asked for. Here a
    stand-in rule hides the robin finer than resolution 5."""
    asked = []

    def stand_in_rule(settings, res):
        """Hide AAAA at resolutions finer than 5, and record what was asked."""
        asked.append((settings, res))
        return frozenset({"AAAA"}) if res > 5 else frozenset()

    monkeypatch.setattr(api.cells, "hidden_species", stand_in_rule)
    fine = hexagon(client, CELL_A, species="AAAA")
    assert listed(fine) == [("CCCC", 1)]
    assert (fine["records"], fine["species"], fine["matching"]) == (1, 1, 1)
    hidden_block = fine["species_asked_for"]
    assert {field: hidden_block[field] for field in ("records", "records_per_year", "first_year", "last_year",
                                                     "records_no_year", "gbif_keys")} == {
        "records": None, "records_per_year": None, "first_year": None, "last_year": None,
        "records_no_year": None, "gbif_keys": None}
    assert hidden_block["col_name"] == "Turdus migratorius"
    assert fine["sensitive_max_res"] is None
    year_filtered = hexagon(client, CELL_A, year_to=2020)
    assert (year_filtered["records"], year_filtered["records_no_year_left_out"]) == (0, 0)
    coarse = hexagon(client, "852800b3fffffff", species="AAAA")
    assert listed(coarse) == [("AAAA", 5), ("CCCC", 1)]
    assert coarse["species_asked_for"]["records"] == 5
    assert coarse["species_asked_for"]["records_per_year"] == [{"year": 2020, "records": 3}]
    app_settings = client.app.state.data.settings
    assert asked == [(app_settings, 7), (app_settings, 7), (app_settings, 5)]


def test_the_species_asked_for_by_year_with_its_first_and_last_year_and_no_year_records(client):
    asked = hexagon(client, CELL_A, species="AAAA")["species_asked_for"]
    assert asked["records"] == 5
    assert asked["records_per_year"] == [{"year": 2020, "records": 3}]
    assert (asked["first_year"], asked["last_year"], asked["records_no_year"]) == (2020, 2020, 2)
    assert asked["gbif_keys"] == ["AAAA"]


def test_the_species_asked_for_with_a_year_range(client):
    """Only the years in the range are listed and counted; records with no
    year are still given, so the panel can say they are left out."""
    none_in_range = hexagon(client, CELL_A, species="AAAA", year_to=2019)["species_asked_for"]
    assert (none_in_range["records"], none_in_range["records_per_year"]) == (0, [])
    assert (none_in_range["first_year"], none_in_range["last_year"], none_in_range["records_no_year"]) == (
        None, None, 2)
    in_range = hexagon(client, CELL_A, species="CCCC", year_from=2021)["species_asked_for"]
    assert (in_range["records"], in_range["records_per_year"]) == (1, [{"year": 2021, "records": 1}])


def test_gbif_keys_hold_every_key_merged_into_the_card(client):
    """Asked for by its old key or its kept key, the GBIF keys are the kept
    key and every key merged into it, also where it has no records."""
    for key in ("OLD1", "BBBB"):
        asked = hexagon(client, CELL_B, species=key)["species_asked_for"]
        assert asked["gbif_keys"] == ["BBBB", "OLD1"]
        assert asked["records_per_year"] == [{"year": 2019, "records": 3}]
    elsewhere = hexagon(client, CELL_A, species="BBBB")["species_asked_for"]
    assert (elsewhere["records"], elsewhere["records_per_year"], elsewhere["records_no_year"]) == (0, [], 0)
    assert elsewhere["gbif_keys"] == ["BBBB", "OLD1"]
