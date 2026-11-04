"""Tests for the species lists of the rank card and a hexagon's panel: the
name filter, the BC status filter, the order, the counts and paging
(api/species_list.py, through GET /api/ranks/{rank} and GET
/api/hexes/{cell}), and that the sensitive-species hook still applies.

The test data is test_ranks.py's, with BCSEE statuses on four more
species, so every status option has a species:

    AAAA  Turdus migratorius        Yellow, American Robin (conftest.py)
    GGGG  Turdus naumanni           Yellow as a species, Naumann's Thrush,
                                    and Red for a subspecies, as Canada
                                    Goose is in the real data
    CCCC  Bombus vosnesenskii       Blue, Western Bumble Bee
    EEEE  Prunella vulgaris         Exotic, Common Self-heal
    HHHH  Apis test                 in BCSEE with no shown status (Not
                                    Reviewed), Test Bee

FFFF (Prunella modularis) and IIII (Turdidae test) are not in BCSEE.

Kingdom Animalia holds AAAA (5 records), CCCC (1), FFFF (2), GGGG (7),
HHHH (1) and IIII (1). Hexagon A (872800b30ffffff, resolution 7) holds
AAAA (3 in 2020, 2 with no year), CCCC (1, 2021), GGGG (3 in 1999, 4 in
2003) and HHHH (1, 2005)."""

import pytest
from fastapi.testclient import TestClient

import api.cells
import api.ranks
from api.main import create_app
from api.settings import Settings
from api.species_list import ListRequestError, check_list_options
from conftest import BCSEE_SQL, write_test_data
from test_ranks import CELL_A, RANK_CARD_SQL, RANK_HEX_SQL, TURDUS


def bcsee_row(key, name, statuses, entries):
    """One more BCSEE row: the key, its name, its shown statuses and its
    entries as (status, label, common name, species level or not)."""
    status_list = ", ".join(f"'{status}'" for status in statuses)
    entry_list = ", ".join(
        f"""{{'status': '{status}', 'scientific_label': '{label}', 'name_source': 'GBIF',
              'common_label': '{common.replace("'", "''")}', 'is_species_level': {str(level).lower()},
              'is_gbif_synonym': false, 'matched_species_keys': ['{key}'], 'element_codes': ['X']}}"""
        for status, label, common, level in entries)
    return f"""SELECT '{key}', '{name}', 'test', false, false, [{status_list}]::VARCHAR[], ['X'],
                      [{entry_list}], ['{key}'], 'BC Species and Ecosystems Explorer (BCSEE)',
                      '2026-05-05', '2026-09-26'"""


LIST_BCSEE_SQL = " UNION ALL ".join([
    f"SELECT * FROM ({BCSEE_SQL})",
    bcsee_row("GGGG", "Turdus naumanni", ["Yellow", "Red"], [
        ("Yellow", "Turdus naumanni", "Naumann's Thrush", True),
        ("Red", "Turdus naumanni eunomus", "Naumann's Thrush, eastern subspecies", False)]),
    bcsee_row("CCCC", "Bombus vosnesenskii", ["Blue"], [
        ("Blue", "Bombus vosnesenskii", "Western Bumble Bee", True)]),
    bcsee_row("EEEE", "Prunella vulgaris", ["Exotic"], [
        ("Exotic", "Prunella vulgaris", "Common Self-heal", True)]),
    bcsee_row("HHHH", "Apis test", [], [("Not Reviewed", "Apis test", "Test Bee", True)]),
])

ANIMALIA = {"kingdom": "Animalia"}
NO_COUNTS = {"red": 0, "blue": 0, "yellow": 0, "exotic": 0, "none": 0}


@pytest.fixture
def client(tmp_path):
    """A test client on the rank test data with the extra BCSEE rows."""
    write_test_data(tmp_path, card_sql=RANK_CARD_SQL, hex_sql=RANK_HEX_SQL, bcsee_sql=LIST_BCSEE_SQL)
    with TestClient(create_app(Settings(data_dir=tmp_path, sensitive_max_res=None))) as test_client:
        yield test_client


def kingdom(client, **params):
    """Ask for the Animalia card with these list options and return the JSON."""
    response = client.get("/api/ranks/kingdom", params={**ANIMALIA, **params})
    assert response.status_code == 200, response.text
    return response.json()


def hexagon(client, **params):
    """Ask for hexagon A with these list options and return the JSON."""
    response = client.get(f"/api/hexes/{CELL_A}", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def keys(result):
    """The keys of a list in order, from either answer."""
    return [row["specieskey"] for row in result.get("members", result.get("results"))]


@pytest.mark.parametrize("sort, expected", [
    (None, ["GGGG", "AAAA", "FFFF", "HHHH", "CCCC", "IIII"]),
    ("most_records", ["GGGG", "AAAA", "FFFF", "HHHH", "CCCC", "IIII"]),
    ("fewest_records", ["HHHH", "CCCC", "IIII", "FFFF", "AAAA", "GGGG"]),
    ("scientific", ["HHHH", "CCCC", "FFFF", "IIII", "AAAA", "GGGG"]),
    # By English name, the species with none last, by scientific name.
    ("english", ["AAAA", "GGGG", "HHHH", "CCCC", "FFFF", "IIII"]),
])
def test_the_rank_card_sorts_its_species(client, sort, expected):
    """Equal records go by scientific name (Apis, Bombus, Turdidae test)."""
    result = kingdom(client, **({"sort": sort} if sort else {}))
    assert keys(result) == expected
    assert result["sort"] == (sort or "most_records")
    # The totals and the next rank down stay the whole rank's.
    assert (result["species_count"], result["matching"]) == (6, 6)


def test_the_hexagon_list_sorts_the_same_way(client):
    assert keys(hexagon(client)) == ["GGGG", "AAAA", "HHHH", "CCCC"]
    assert keys(hexagon(client, sort="fewest_records")) == ["HHHH", "CCCC", "AAAA", "GGGG"]
    assert keys(hexagon(client, sort="scientific")) == ["HHHH", "CCCC", "AAAA", "GGGG"]
    assert keys(hexagon(client, sort="english")) == ["AAAA", "GGGG", "HHHH", "CCCC"]


def test_a_species_counts_for_every_shown_status_including_a_subspecies(client):
    """Naumann's Thrush is Yellow as a species and Red for a subspecies: it
    counts for both, as Canada Goose does. A species in BCSEE with no
    shown status, and one not in BCSEE, count as no status."""
    result = kingdom(client)
    assert result["status_counts"] == {"red": 1, "blue": 1, "yellow": 2, "exotic": 0, "none": 3}
    assert keys(kingdom(client, status="red")) == ["GGGG"]
    assert keys(kingdom(client, status="yellow")) == ["GGGG", "AAAA"]
    assert keys(kingdom(client, status="none")) == ["FFFF", "HHHH", "IIII"]
    thrush = kingdom(client, status="red")["members"][0]
    assert thrush["statuses"] == ["Red", "Yellow"]
    assert thrush["english_name"] == "Naumann's Thrush"
    plants = client.get("/api/ranks/kingdom", params={"kingdom": "Plantae", "status": "exotic"}).json()
    assert keys(plants) == ["EEEE"]


def test_several_statuses_keep_the_species_with_any_of_them(client):
    result = kingdom(client, status=["red", "none"])
    assert keys(result) == ["GGGG", "FFFF", "HHHH", "IIII"]
    assert result["matching"] == 4
    assert result["status"] == ["red", "none"]
    # Case, repeats and order do not matter.
    assert kingdom(client, status=["None", "RED", "red"])["status"] == ["red", "none"]


def test_the_name_filter_uses_the_species_search_names(client):
    """English names from BCSEE are searchable, a subspecies' too, as in the
    species search."""
    assert keys(kingdom(client, q="thrush")) == ["GGGG"]
    assert keys(kingdom(client, q="eastern subspecies")) == ["GGGG"]
    assert keys(kingdom(client, q="turdus")) == ["GGGG", "AAAA"]
    assert keys(kingdom(client, q="bumble")) == ["CCCC"]
    assert keys(hexagon(client, q="bee")) == ["HHHH", "CCCC"]
    assert kingdom(client, q="nothing like it")["members"] == []


def test_the_counts_follow_the_name_filter_and_not_the_status_filter(client):
    """matching follows both filters; the status counts follow the name
    filter only, so a box that is not ticked still says what it would
    show."""
    turdus = kingdom(client, q="turdus")
    assert turdus["status_counts"] == {**NO_COUNTS, "red": 1, "yellow": 2}
    assert turdus["matching"] == 2
    blue = kingdom(client, q="turdus", status="blue")
    assert blue["status_counts"] == turdus["status_counts"]
    assert (blue["matching"], blue["members"]) == (0, [])
    # The card's totals stay the whole kingdom's.
    assert (blue["species_count"], blue["record_count"]) == (6, 17)


def test_the_hexagon_counts_follow_its_years_and_filters(client):
    """The hexagon's counts are of its own species for the years asked for;
    its totals stay the whole hexagon's."""
    every_year = hexagon(client)
    assert every_year["status_counts"] == {"red": 1, "blue": 1, "yellow": 2, "exotic": 0, "none": 1}
    recent = hexagon(client, year_from=2020, year_to=2021)
    assert keys(recent) == ["AAAA", "CCCC"]
    assert recent["status_counts"] == {**NO_COUNTS, "blue": 1, "yellow": 1}
    red = hexagon(client, status="red", q="thrush")
    assert (keys(red), red["matching"]) == (["GGGG"], 1)
    assert (red["records"], red["species"]) == (14, 4)


def test_a_rank_s_hexagon_list_filters_within_the_rank(client):
    result = hexagon(client, rank="genus", **TURDUS, status="red")
    assert keys(result) == ["GGGG"]
    assert result["status_counts"] == {**NO_COUNTS, "red": 1, "yellow": 2}


def test_paging_goes_through_the_filtered_and_sorted_list(client):
    first = kingdom(client, status="none", sort="scientific", limit=2)
    second = kingdom(client, status="none", sort="scientific", limit=2, offset=2)
    assert (keys(first), keys(second)) == (["HHHH", "FFFF"], ["IIII"])
    assert first["matching"] == second["matching"] == 3
    assert keys(hexagon(client, status="yellow", limit=1, offset=1)) == ["AAAA"]


@pytest.mark.parametrize("path", ["/api/ranks/kingdom?kingdom=Animalia", f"/api/hexes/{CELL_A}"])
@pytest.mark.parametrize("bad, words", [
    ("sort=newest", "sort must be one of"),
    ("status=purple", "status must be among"),
    ("q=t", "at least 2"),
])
def test_unknown_options_and_short_filters_are_refused(client, path, bad, words):
    separator = "&" if "?" in path else "?"
    response = client.get(f"{path}{separator}{bad}")
    assert response.status_code == 400
    assert words in response.json()["detail"]


def test_the_options_on_their_own():
    assert check_list_options(None, None) == ("most_records", [])
    assert check_list_options("english", [" Exotic ", "", "blue"]) == ("english", ["blue", "exotic"])
    with pytest.raises(ListRequestError):
        check_list_options("relevance", [])


def test_the_lists_still_apply_the_sensitive_setting(client, monkeypatch):
    """The rank card asks sensitive.hidden_species with no resolution, and
    the hexagon list with its resolution; a hidden species is in no list,
    no filter result and no count. Here a stand-in rule hides Naumann's
    Thrush (GGGG), the only Red species."""
    asked = []

    def stand_in_rule(settings, res):
        """Hide GGGG, and record what was asked."""
        asked.append(res)
        return frozenset({"GGGG"})

    monkeypatch.setattr(api.ranks, "hidden_species", stand_in_rule)
    monkeypatch.setattr(api.cells, "hidden_species", stand_in_rule)
    card = kingdom(client, status="red")
    assert (card["members"], card["matching"]) == ([], 0)
    assert card["status_counts"] == {"red": 0, "blue": 1, "yellow": 1, "exotic": 0, "none": 3}
    assert kingdom(client, q="thrush")["members"] == []
    assert keys(kingdom(client, sort="english")) == ["AAAA", "HHHH", "CCCC", "FFFF", "IIII"]
    panel = hexagon(client, q="thrush")
    assert (panel["results"], panel["matching"]) == ([], 0)
    assert hexagon(client)["status_counts"]["red"] == 0
    assert asked == [None, None, None, 7, 7]
