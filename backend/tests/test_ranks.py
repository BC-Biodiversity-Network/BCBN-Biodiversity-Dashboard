"""Tests for GET /api/ranks/{rank} (api/ranks.py) and the sensitive-species
hook it goes through (api/sensitive.py). The test data is conftest.py's,
with five more species, so the cards can show homonyms and missing ranks:

    EEEE  Prunella vulgaris   Plantae Tracheophyta Magnoliopsida Lamiales
                              Lamiaceae Prunella, 4 records (1 with no year),
                              2010 to 2015
    FFFF  Prunella modularis  Animalia Chordata Aves Passeriformes
                              Prunellidae Prunella, 2 records, 2001 to 2002
    GGGG  Turdus naumanni     Animalia Chordata Aves Passeriformes Turdidae
                              Turdus, 7 records, 1999 to 2003
    HHHH  Apis test           Animalia Arthropoda Insecta (no order) Apidae
                              Apis, 1 record, 2005
    IIII  Turdidae test       Animalia Chordata Aves Passeriformes Turdidae
                              (no genus), 1 record, 2006

conftest.py's own species: AAAA Turdus migratorius (Turdidae, 5 records, 2
with no year, 2020), BBBB Salix scouleriana (Salicaceae), CCCC Bombus
vosnesenskii (Hymenoptera Apidae, 1 record, 2021), DDDD Carex test
(Cyperaceae).

Their records by hexagon (the five extra species added to conftest.py's
HEX_SQL): hexagon A (872800b30ffffff at resolution 7) holds AAAA (3 in
2020, 2 with no year), CCCC (1, 2021), GGGG (3 in 1999, 4 in 2003) and HHHH
(1, 2005); hexagon B (8728c9b40ffffff) holds BBBB (3, 2019), DDDD (1,
2018), EEEE (3 in 2010, 1 with no year), FFFF (2, 2001) and IIII (1, 2006)."""

from conftest import HEX_SQL

import pytest
from fastapi.testclient import TestClient

import api.cells
import api.ranks
from api.main import create_app
from api.sensitive import hidden_species
from api.settings import Settings
from conftest import CARD_SQL, write_test_data


def extra(key, name, kingdom, phylum, class_, order, family, genus, records, no_year, first, last):
    """One more card row, copied from DDDD with its own key, names, classification and counts."""
    def text(value):
        return "NULL::VARCHAR" if value is None else f"'{value}'"
    return f"""SELECT * REPLACE ('{key}' AS specieskey, '{name}' AS scientificname, '{name}' AS col_name,
                                 '{name}' AS species, {text(kingdom)} AS kingdom, {text(phylum)} AS phylum,
                                 {text(class_)} AS class, {text(order)} AS "order", {text(family)} AS family,
                                 {text(genus)} AS genus, {records}::BIGINT AS record_count,
                                 {no_year}::BIGINT AS records_no_year, {first} AS first_year, {last} AS last_year)
               FROM base WHERE specieskey = 'DDDD'"""


RANK_CARD_SQL = f"""
    WITH base AS ({CARD_SQL})
    SELECT * FROM base
    UNION ALL {extra('EEEE', 'Prunella vulgaris', 'Plantae', 'Tracheophyta', 'Magnoliopsida', 'Lamiales',
                     'Lamiaceae', 'Prunella', 4, 1, 2010, 2015)}
    UNION ALL {extra('FFFF', 'Prunella modularis', 'Animalia', 'Chordata', 'Aves', 'Passeriformes',
                     'Prunellidae', 'Prunella', 2, 0, 2001, 2002)}
    UNION ALL {extra('GGGG', 'Turdus naumanni', 'Animalia', 'Chordata', 'Aves', 'Passeriformes',
                     'Turdidae', 'Turdus', 7, 0, 1999, 2003)}
    UNION ALL {extra('HHHH', 'Apis test', 'Animalia', 'Arthropoda', 'Insecta', None, 'Apidae', 'Apis',
                     1, 0, 2005, 2005)}
    UNION ALL {extra('IIII', 'Turdidae test', 'Animalia', 'Chordata', 'Aves', 'Passeriformes', 'Turdidae', None,
                     1, 0, 2006, 2006)}
"""

A = ("842800bffffffff", "852800b3fffffff", "862800b37ffffff", "872800b30ffffff")
B = ("8428c9bffffffff", "8528c9b7fffffff", "8628c9b47ffffff", "8728c9b40ffffff")
CELL_A, CELL_B = A[3], B[3]


def hex_row(key, year, cells, records):
    """One row of species_hex for a species, a year (None for none), a
    hexagon's ids at resolutions 4 to 7, and its records."""
    year_text = "NULL" if year is None else str(year)
    return f"SELECT '{key}', {year_text}, '{cells[0]}', '{cells[1]}', '{cells[2]}', '{cells[3]}', {records}::BIGINT"


RANK_HEX_SQL = " UNION ALL ".join([f"SELECT * FROM ({HEX_SQL})"] + [
    hex_row("GGGG", 1999, A, 3), hex_row("GGGG", 2003, A, 4), hex_row("HHHH", 2005, A, 1),
    hex_row("EEEE", 2010, B, 3), hex_row("EEEE", None, B, 1), hex_row("FFFF", 2001, B, 2),
    hex_row("IIII", 2006, B, 1),
])

THRUSHES = {"kingdom": "Animalia", "phylum": "Chordata", "class": "Aves", "order": "Passeriformes",
            "family": "Turdidae"}
TURDUS = {**THRUSHES, "genus": "Turdus"}
PLANT_PRUNELLA = {"kingdom": "Plantae", "phylum": "Tracheophyta", "class": "Magnoliopsida", "order": "Lamiales",
                  "family": "Lamiaceae", "genus": "Prunella"}
BIRD_PRUNELLA = {"kingdom": "Animalia", "phylum": "Chordata", "class": "Aves", "order": "Passeriformes",
                 "family": "Prunellidae", "genus": "Prunella"}


@pytest.fixture
def client(tmp_path):
    """A test client on the test data with the five extra species."""
    write_test_data(tmp_path, card_sql=RANK_CARD_SQL, hex_sql=RANK_HEX_SQL)
    with TestClient(create_app(Settings(data_dir=tmp_path, sensitive_max_res=None))) as test_client:
        yield test_client


def card(client, rank, **params):
    """Ask for a rank card and return the JSON, checking it worked."""
    response = client.get(f"/api/ranks/{rank}", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def members(result):
    """The species of a card as [(key, records)], in order."""
    return [(row["specieskey"], row["record_count"]) for row in result["members"]]


def test_a_genus_card_with_its_species_records_and_years(client):
    result = card(client, "genus", **TURDUS)
    assert (result["rank"], result["name"]) == ("genus", "Turdus")
    assert result["path"] == [{"rank": rank, "name": name} for rank, name in TURDUS.items()]
    assert members(result) == [("GGGG", 7), ("AAAA", 5)]
    assert (result["species_count"], result["record_count"], result["records_no_year"]) == (2, 12, 2)
    assert (result["first_year"], result["last_year"]) == (1999, 2020)
    assert (result["children_rank"], result["children"]) == (None, None)
    robin = result["members"][1]
    assert (robin["col_name"], robin["english_name"], robin["statuses"]) == (
        "Turdus migratorius", "American Robin", ["Yellow"])
    assert robin["name_source"] == "Catalogue of Life, via GBIF"
    assert (result["classification_source"], result["name_source"]) == ("GBIF", "Catalogue of Life, via GBIF")
    assert result["english_name_source"] == "BC Species and Ecosystems Explorer (BCSEE)"
    assert result["sensitive_max_res"] is None


def test_the_next_rank_down_counts_species_missing_it_on_their_own(client):
    """The thrush family's genera: Turdus, and the species with no genus,
    which still counts in the family."""
    result = card(client, "family", **THRUSHES)
    assert result["species_count"] == 3
    assert result["children_rank"] == "genus"
    assert result["children"] == [
        {"name": "Turdus", "species_count": 2, "record_count": 12},
        {"name": None, "species_count": 1, "record_count": 1},
    ]


def test_a_kingdom_card_holds_every_species_under_it(client):
    result = card(client, "kingdom", kingdom="Animalia")
    assert result["species_count"] == 6
    assert result["children_rank"] == "phylum"
    assert result["children"] == [
        {"name": "Chordata", "species_count": 4, "record_count": 15},
        {"name": "Arthropoda", "species_count": 2, "record_count": 2},
    ]


def test_homonyms_are_told_apart_by_their_path(client):
    assert members(card(client, "genus", **PLANT_PRUNELLA)) == [("EEEE", 4)]
    assert members(card(client, "genus", **BIRD_PRUNELLA)) == [("FFFF", 2)]
    mixed = {**PLANT_PRUNELLA, "family": "Prunellidae"}
    assert client.get("/api/ranks/genus", params=mixed).status_code == 404


def test_a_missing_rank_is_part_of_the_path(client):
    """Apidae with no order (HHHH) is not the same family as Apidae under
    Hymenoptera (CCCC); a missing rank given empty is the same as not given."""
    insects = {"kingdom": "Animalia", "phylum": "Arthropoda", "class": "Insecta"}
    assert members(card(client, "family", **insects, family="Apidae")) == [("HHHH", 1)]
    assert members(card(client, "family", **insects, order="", family="Apidae")) == [("HHHH", 1)]
    assert members(card(client, "family", **insects, order="Hymenoptera", family="Apidae")) == [("CCCC", 1)]
    no_order = card(client, "family", **insects, family="Apidae")
    assert no_order["path"][3] == {"rank": "order", "name": None}
    assert card(client, "class", **insects)["children"] == [
        {"name": "Hymenoptera", "species_count": 1, "record_count": 1},
        {"name": None, "species_count": 1, "record_count": 1},
    ]


def test_paging(client):
    second = card(client, "genus", **TURDUS, limit=1, offset=1)
    assert members(second) == [("AAAA", 5)]
    assert (second["species_count"], second["offset"], second["limit"]) == (2, 1, 1)
    assert card(client, "genus", **TURDUS, offset=5)["members"] == []
    for params in ({"limit": 0}, {"limit": 201}, {"offset": -1}):
        assert client.get("/api/ranks/genus", params={**TURDUS, **params}).status_code == 422


@pytest.mark.parametrize("rank, params, words", [
    ("species", {"genus": "Turdus"}, "rank must be one of"),
    ("genus", {**THRUSHES}, "give the genus's name"),
    ("family", {**TURDUS}, "down to family only"),
])
def test_a_request_that_does_not_name_a_rank_is_refused(client, rank, params, words):
    response = client.get(f"/api/ranks/{rank}", params=params)
    assert response.status_code == 400
    assert words in response.json()["detail"]


def test_a_path_no_species_has_is_not_found(client):
    assert client.get("/api/ranks/genus", params={**THRUSHES, "genus": "turdus"}).status_code == 404
    response = client.get("/api/ranks/genus", params={"genus": "Turdus"})
    assert response.status_code == 404
    assert "kingdom=(missing)" in response.json()["detail"]


def test_sensitive_setting_hides_nothing_on_cards_while_it_is_empty():
    assert hidden_species(Settings(sensitive_max_res=None), None) == frozenset()


def test_the_card_applies_the_sensitive_setting(client, monkeypatch):
    """The card asks sensitive.hidden_species, with the API's own settings
    and no resolution, which species to hide, and leaves them out of the
    list, the totals and the children; a rank whose species are all hidden
    is not found. Here a stand-in rule hides the robin (AAAA) and the
    no-order bee (HHHH)."""
    asked = []

    def stand_in_rule(settings, res):
        """Hide AAAA and HHHH, and record what was asked."""
        asked.append((settings, res))
        return frozenset({"AAAA", "HHHH"})

    monkeypatch.setattr(api.ranks, "hidden_species", stand_in_rule)
    genus = card(client, "genus", **TURDUS)
    assert members(genus) == [("GGGG", 7)]
    assert (genus["species_count"], genus["record_count"], genus["records_no_year"]) == (1, 7, 0)
    assert (genus["first_year"], genus["last_year"]) == (1999, 2003)
    family = card(client, "family", **THRUSHES)
    assert family["children"] == [
        {"name": "Turdus", "species_count": 1, "record_count": 7},
        {"name": None, "species_count": 1, "record_count": 1},
    ]
    insects = {"kingdom": "Animalia", "phylum": "Arthropoda", "class": "Insecta", "family": "Apidae"}
    assert client.get("/api/ranks/family", params=insects).status_code == 404
    app_settings = client.app.state.data.settings
    assert asked == [(app_settings, None)] * 3


def rank_map(client, rank, path, **params):
    """Ask for a rank map and return the JSON, checking it worked."""
    response = client.get(f"/api/ranks/{rank}/hexes", params={**path, **params})
    assert response.status_code == 200, response.text
    return response.json()


def map_rows(result):
    """The hexagons of a map as {cell: (records, species)}."""
    return {cell: (records, species) for cell, records, species
            in zip(result["cells"], result["occurrences"], result["distinct_species"])}


def test_a_rank_map_counts_records_and_species_of_the_rank_only(client):
    result = rank_map(client, "genus", TURDUS, res=7)
    assert map_rows(result) == {CELL_A: (12, 2)}
    assert (result["rank"], result["name"]) == ("genus", "Turdus")
    assert result["path"][-1] == {"rank": "genus", "name": "Turdus"}
    assert (result["hexagon_count"], result["records_shown"], result["records_total"]) == (1, 12, 12)
    assert (result["records_no_year_left_out"], result["records_outside_years"]) == (0, 0)
    assert result["years_counted"] is None and result["sensitive_max_res"] is None
    family = rank_map(client, "family", THRUSHES, res=4)
    assert map_rows(family) == {A[0]: (12, 2), B[0]: (1, 1)}
    animals = rank_map(client, "kingdom", {"kingdom": "Animalia"}, res=7)
    assert map_rows(animals) == {CELL_A: (14, 4), CELL_B: (3, 2)}


@pytest.mark.parametrize("res", [4, 5, 6, 7])
@pytest.mark.parametrize("rank, path", [("genus", TURDUS), ("family", THRUSHES), ("kingdom", {"kingdom": "Animalia"}),
                                        ("genus", PLANT_PRUNELLA)])
def test_a_rank_map_holds_every_record_of_the_card_at_every_resolution(client, rank, path, res):
    result = rank_map(client, rank, path, res=res)
    assert result["records_shown"] == result["records_total"] == card(client, rank, **path)["record_count"]


def test_a_rank_map_with_years_leaves_out_records_with_no_year_and_says_how_many(client):
    result = rank_map(client, "genus", TURDUS, res=7, year_from=2000)
    assert result["years_counted"] == [2000, 2021]
    assert map_rows(result) == {CELL_A: (7, 2)}
    assert (result["records_shown"], result["records_total"]) == (7, 12)
    assert (result["records_no_year_left_out"], result["records_outside_years"]) == (2, 3)


def test_rank_map_homonyms_and_missing_ranks_follow_the_path(client):
    assert map_rows(rank_map(client, "genus", PLANT_PRUNELLA, res=7)) == {CELL_B: (4, 1)}
    assert map_rows(rank_map(client, "genus", BIRD_PRUNELLA, res=7)) == {CELL_B: (2, 1)}
    insects = {"kingdom": "Animalia", "phylum": "Arthropoda", "class": "Insecta", "family": "Apidae"}
    assert map_rows(rank_map(client, "family", insects, res=7)) == {CELL_A: (1, 1)}


def test_a_rank_map_request_that_is_wrong_is_refused(client):
    assert client.get("/api/ranks/genus/hexes", params={**TURDUS, "res": 8}).status_code == 400
    assert client.get("/api/ranks/genus/hexes", params={**TURDUS}).status_code == 422
    assert client.get("/api/ranks/genus/hexes", params={**THRUSHES, "res": 7}).status_code == 400
    assert client.get("/api/ranks/genus/hexes",
                      params={**TURDUS, "res": 7, "year_from": 2021, "year_to": 2019}).status_code == 400
    assert client.get("/api/ranks/genus/hexes", params={**THRUSHES, "genus": "Nope", "res": 7}).status_code == 404


def hexagon(client, cell, **params):
    """Ask for one hexagon and return the JSON, checking it worked."""
    response = client.get(f"/api/hexes/{cell}", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def listed(result):
    """The species of a hexagon's answer as [(key, records)], in order."""
    return [(row["specieskey"], row["records"]) for row in result["results"]]


def test_a_hexagon_restricted_to_a_rank_lists_its_species_only(client):
    result = hexagon(client, CELL_A, rank="genus", **TURDUS)
    assert listed(result) == [("GGGG", 7), ("AAAA", 5)]
    assert (result["records"], result["species"], result["matching"]) == (12, 2, 2)
    assert result["rank"]["name"] == "Turdus" and result["rank"]["path"][-2] == {"rank": "family", "name": "Turdidae"}
    robin = result["results"][1]
    assert (robin["col_name"], robin["english_name"], robin["statuses"]) == (
        "Turdus migratorius", "American Robin", ["Yellow"])
    # The rank has no species in hexagon B: zeros, not an error.
    empty = hexagon(client, CELL_B, rank="genus", **TURDUS)
    assert (empty["records"], empty["species"], empty["results"]) == (0, 0, [])
    # The same hexagon with no rank is unchanged.
    assert hexagon(client, CELL_A)["rank"] is None


def test_a_hexagon_restricted_to_a_rank_with_years_a_filter_and_pages(client):
    years = hexagon(client, CELL_A, rank="genus", **TURDUS, year_to=2010)
    assert listed(years) == [("GGGG", 7)]
    assert (years["records_no_year_left_out"], years["records_outside_years"]) == (2, 3)
    assert listed(hexagon(client, CELL_A, rank="genus", **TURDUS, q="robin")) == [("AAAA", 5)]
    second = hexagon(client, CELL_A, rank="kingdom", kingdom="Animalia", limit=2, offset=2)
    # Equal records go by name: Apis test (HHHH) before Bombus (CCCC).
    assert listed(second) == [("HHHH", 1), ("CCCC", 1)]
    assert second["matching"] == 4


@pytest.mark.parametrize("res", [4, 5, 6, 7])
def test_the_hexagon_totals_match_the_rank_map(client, res):
    for rank, path in (("genus", TURDUS), ("kingdom", {"kingdom": "Animalia"}), ("family", THRUSHES)):
        result = rank_map(client, rank, path, res=res, year_from=2000)
        for cell, (records, species) in map_rows(result).items():
            panel = hexagon(client, cell, rank=rank, **path, year_from=2000)
            assert (panel["records"], panel["species"]) == (records, species)


def test_a_hexagon_rank_request_that_is_wrong_is_refused(client):
    assert client.get(f"/api/hexes/{CELL_A}", params={"rank": "genus", **THRUSHES}).status_code == 400
    assert client.get(f"/api/hexes/{CELL_A}", params={"rank": "genus", **TURDUS, "species": "AAAA"}).status_code == 400
    assert client.get(f"/api/hexes/{CELL_A}", params={"rank": "tribe", "genus": "Turdus"}).status_code == 400
    assert client.get(f"/api/hexes/{CELL_A}", params={"rank": "genus", **THRUSHES, "genus": "Nope"}).status_code == 404


def test_the_rank_map_and_hexagon_list_apply_the_sensitive_setting(client, monkeypatch):
    """The map and the hexagon list ask sensitive.hidden_species with the
    hexagon's resolution and leave those species out of every count; whether
    the rank exists is asked with no resolution, as for the card. Here a
    stand-in rule hides GGGG and FFFF finer than resolution 5."""
    asked = []

    def stand_in_rule(settings, res):
        """Hide GGGG and FFFF at resolutions finer than 5, and record what was asked."""
        asked.append(res)
        return frozenset({"GGGG", "FFFF"}) if res is not None and res > 5 else frozenset()

    monkeypatch.setattr(api.ranks, "hidden_species", stand_in_rule)
    monkeypatch.setattr(api.cells, "hidden_species", stand_in_rule)
    fine = rank_map(client, "genus", TURDUS, res=7)
    assert map_rows(fine) == {CELL_A: (5, 1)}
    assert fine["records_total"] == 5
    coarse = rank_map(client, "genus", TURDUS, res=5)
    assert map_rows(coarse) == {A[1]: (12, 2)}
    # A rank whose only species is hidden at this resolution has a card,
    # so its map is found, with nothing on it.
    bird = rank_map(client, "genus", BIRD_PRUNELLA, res=7)
    assert (bird["cells"], bird["records_total"]) == ([], 0)
    panel = hexagon(client, CELL_A, rank="genus", **TURDUS)
    assert listed(panel) == [("AAAA", 5)]
    assert (panel["records"], panel["species"]) == (5, 1)
    assert asked == [None, 7, None, 5, None, 7, 7, None]
