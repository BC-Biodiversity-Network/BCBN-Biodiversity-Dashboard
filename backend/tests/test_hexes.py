"""Tests for GET /api/species/{key}/hexes (api/hexes.py). The client and the
test species are in conftest.py: AAAA (the robin) has 3 records from 2020
and 2 with no year; BBBB has 3 records from 2019 and old key OLD1 merged
into it; ZZZZ is in BCSEE but has no card. The test data's years run from
2018 to 2021."""

import pytest


def hexes(client, key, **params):
    """Ask for a species map and return the JSON, checking it worked."""
    response = client.get(f"/api/species/{key}/hexes", params=params)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("key, card_key", [("AAAA", "AAAA"), ("OLD1", "BBBB")])
@pytest.mark.parametrize("res", [4, 5, 6, 7])
def test_counts_add_up_to_the_card(client, key, card_key, res):
    """With no year filter, the cells hold every record of the species,
    including those with no year: they add up to the card's record_count
    at every resolution, also for an old merged key."""
    card = client.get(f"/api/species/{card_key}").json()
    result = hexes(client, key, res=res)
    assert result["specieskey"] == card_key
    assert result["res"] == res
    assert sum(result["occurrences"]) == card["records"]["record_count"]
    assert result["records_shown"] == result["records_total"] == card["records"]["record_count"]
    assert result["records_no_year_left_out"] == 0
    assert result["records_outside_years"] == 0
    assert result["years_counted"] is None
    assert result["hexagon_count"] == len(result["cells"]) == len(result["occurrences"])


def test_cells_and_counts(client):
    """The robin's 5 records are all in one hexagon at res 7."""
    result = hexes(client, "AAAA", res=7)
    assert result["cells"] == ["872800b30ffffff"]
    assert result["occurrences"] == [5]


def test_old_merged_key(client):
    """An old merged key gives the kept key's map and says which key was
    asked for."""
    result = hexes(client, "OLD1", res=5)
    assert (result["specieskey"], result["requested_key"], result["merged_from"]) == (
        "BBBB", "OLD1", "OLD1")
    assert hexes(client, "AAAA", res=5)["merged_from"] is None


def test_year_filter_leaves_out_records_with_no_year(client):
    """With a year filter, the 2 records with no year are left out and
    counted as such; a missing bound is the last year in the data."""
    result = hexes(client, "AAAA", res=7, year_from=2020)
    assert result["years_counted"] == [2020, 2021]
    assert result["records_shown"] == 3
    assert result["occurrences"] == [3]
    assert result["records_no_year_left_out"] == 2
    assert result["records_outside_years"] == 0
    assert result["records_total"] == 5


def test_year_filter_with_no_records_in_range(client):
    """A range with none of the species' records gives an empty map, and
    the counts say where every record went."""
    result = hexes(client, "AAAA", res=7, year_from=2018, year_to=2019)
    assert result["cells"] == [] and result["occurrences"] == []
    assert result["hexagon_count"] == 0
    assert result["records_shown"] == 0
    assert result["records_outside_years"] == 3
    assert result["records_no_year_left_out"] == 2


def test_only_year_to(client):
    """year_to alone counts from the first year in the data."""
    result = hexes(client, "BBBB", res=7, year_to=2019)
    assert result["years_counted"] == [2018, 2019]
    assert result["records_shown"] == 3


@pytest.mark.parametrize("params", [
    {}, {"year_from": 2020}, {"year_to": 2020}, {"year_from": 2018, "year_to": 2019},
    {"year_from": 2021, "year_to": 2021}])
def test_every_record_is_accounted_for(client, params):
    """Shown + no year left out + outside the years = all records."""
    result = hexes(client, "AAAA", res=6, **params)
    assert (result["records_shown"] + result["records_no_year_left_out"]
            + result["records_outside_years"]) == result["records_total"] == 5


@pytest.mark.parametrize("res", [3, 8, 0, -1])
def test_bad_res_is_refused(client, res):
    """A resolution that is not stored gives 400 with the allowed ones."""
    response = client.get("/api/species/AAAA/hexes", params={"res": res})
    assert response.status_code == 400
    assert response.json()["detail"] == f"res must be one of 4, 5, 6, 7; got {res}."


def test_missing_or_non_number_res_is_refused(client):
    """res is required and must be a whole number."""
    assert client.get("/api/species/AAAA/hexes").status_code == 422
    assert client.get("/api/species/AAAA/hexes", params={"res": "r7"}).status_code == 422


@pytest.mark.parametrize("params, words", [
    ({"year_from": 2021, "year_to": 2019}, "must not be after year_to"),
    ({"year_from": 1999}, "year_from must be between 2018 and 2021"),
    ({"year_to": 2030}, "year_to must be between 2018 and 2021"),
])
def test_bad_year_range_is_refused(client, params, words):
    """A backwards range or a year outside the data gives 400."""
    response = client.get("/api/species/AAAA/hexes", params={"res": 7, **params})
    assert response.status_code == 400
    assert words in response.json()["detail"]


@pytest.mark.parametrize("key", ["NOPE", "ZZZZ"])
def test_unknown_and_bcsee_only_keys_are_404(client, key):
    """An unknown key, and a species BCSEE lists but with no records in BC,
    give 404."""
    response = client.get(f"/api/species/{key}/hexes", params={"res": 7})
    assert response.status_code == 404
    assert f"'{key}'" in response.json()["detail"]
