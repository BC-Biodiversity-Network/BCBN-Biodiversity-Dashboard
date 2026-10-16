"""Tests for GET /api/overview/hexes (api/overview.py) on the small test data
in conftest.py, plus one record with no specieskey, which the real data
also has.

At res 4 the test records fall in two hexagons:
    842800bffffffff  AAAA 3 (2020) + 2 (no year), CCCC 1 (2021)
    8428c9bffffffff  BBBB 3 (2019), DDDD 1 (2018), and 1 record with no
                     specieskey (2020)
so 11 records in all, 2 of them with no year, and 2 species in each
hexagon. The test data's years run from 2018 to 2021."""

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from api.settings import Settings
from conftest import HEX_SQL, write_test_data

# The test records plus one with no specieskey.
HEX_WITH_NO_KEY_SQL = f"""
    {HEX_SQL}
    UNION ALL
    SELECT NULL::VARCHAR, 2020, '8428c9bffffffff', '8528c9b7fffffff', '8628c9b47ffffff',
           '8728c9b40ffffff', 1::BIGINT
"""


@pytest.fixture
def client(tmp_path):
    """A test client on the test data with the extra record that has no
    specieskey."""
    write_test_data(tmp_path, hex_sql=HEX_WITH_NO_KEY_SQL)
    with TestClient(create_app(Settings(data_dir=tmp_path, sensitive_max_res=None))) as test_client:
        yield test_client


def overview(client, **params):
    """Ask for the overview map and return the JSON, checking it worked."""
    response = client.get("/api/overview/hexes", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def as_rows(result):
    """The cells of an answer as {cell: (records, species)}."""
    return {cell: (records, species) for cell, records, species
            in zip(result["cells"], result["occurrences"], result["distinct_species"])}


def test_records_and_species_per_hexagon(client):
    """With no year filter every record counts, including those with no
    year; the record with no specieskey counts in records but not in
    species."""
    result = overview(client, res=4)
    assert as_rows(result) == {
        "842800bffffffff": (6, 2),
        "8428c9bffffffff": (5, 2),
    }
    assert result["cells"] == sorted(result["cells"])
    assert result["hexagon_count"] == 2
    assert result["records_shown"] == result["records_total"] == 11
    assert result["records_no_year_left_out"] == 0
    assert result["records_outside_years"] == 0
    assert result["years_counted"] is None


@pytest.mark.parametrize("res", [4, 5, 6, 7])
def test_every_resolution_holds_every_record(client, res):
    """At every resolution the hexagons add up to all 11 records, the same
    total as /api/meta."""
    result = overview(client, res=res)
    assert sum(result["occurrences"]) == 11
    assert client.get("/api/meta").json()["record_count"] == 11
    assert len(result["cells"]) == len(result["occurrences"]) == len(result["distinct_species"])


def test_year_filter_leaves_out_records_with_no_year(client):
    """With a year range, records with no year are left out and counted,
    and so are records from other years."""
    result = overview(client, res=4, year_from=2020, year_to=2020)
    assert result["years_counted"] == [2020, 2020]
    assert as_rows(result) == {
        "842800bffffffff": (3, 1),
        "8428c9bffffffff": (1, 0),
    }
    assert result["records_shown"] == 4
    assert result["records_no_year_left_out"] == 2
    assert result["records_outside_years"] == 5
    assert result["records_total"] == 11


def test_one_bound_is_filled_in_from_the_data(client):
    """year_from alone counts to the last year in the data, year_to alone
    from the first."""
    assert overview(client, res=4, year_from=2020)["years_counted"] == [2020, 2021]
    assert overview(client, res=4, year_to=2019)["years_counted"] == [2018, 2019]


def test_a_single_year_shows_only_that_years_hexagons(client):
    """A range of one year shows only the hexagons with records from that
    year."""
    assert as_rows(overview(client, res=7, year_from=2018, year_to=2018)) == {
        "8728c9b40ffffff": (1, 1)}
    assert as_rows(overview(client, res=7, year_from=2021, year_to=2021)) == {
        "872800b30ffffff": (1, 1)}


@pytest.mark.parametrize("params", [
    {}, {"year_from": 2020}, {"year_to": 2019}, {"year_from": 2018, "year_to": 2018},
    {"year_from": 2018, "year_to": 2021}])
def test_every_record_is_accounted_for(client, params):
    """Shown + no year left out + outside the years = all records."""
    result = overview(client, res=5, **params)
    assert (result["records_shown"] + result["records_no_year_left_out"]
            + result["records_outside_years"]) == result["records_total"] == 11


@pytest.mark.parametrize("res", [3, 8])
def test_bad_res_is_refused(client, res):
    """A resolution that is not stored gives 400, like the species map."""
    response = client.get("/api/overview/hexes", params={"res": res})
    assert response.status_code == 400
    assert response.json()["detail"] == f"res must be one of 4, 5, 6, 7; got {res}."


def test_missing_res_is_refused(client):
    """res is required."""
    assert client.get("/api/overview/hexes").status_code == 422


@pytest.mark.parametrize("params, words", [
    ({"year_from": 2021, "year_to": 2019}, "must not be after year_to"),
    ({"year_from": 1999}, "year_from must be between 2018 and 2021"),
    ({"year_to": 2030}, "year_to must be between 2018 and 2021"),
])
def test_bad_year_range_is_refused(client, params, words):
    """A backwards range or a year outside the data gives 400."""
    response = client.get("/api/overview/hexes", params={"res": 4, **params})
    assert response.status_code == 400
    assert words in response.json()["detail"]
