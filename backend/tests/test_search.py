"""Tests for GET /api/search (api/search.py).

Besides the shared test species in conftest.py (AAAA American Robin,
BBBB Salix scouleriana with old key OLD1, CCCC Bombus (Pyrobombus)
vosnesenskii, DDDD Carex test, and ZZZZ, in BCSEE but with no card), these
tests add:
    EEEE  Robinia pseudoacacia, fewer records than the robin
    FFFF  Dahlia × pinnata, a hybrid
    GGGG  Culex (Culex) pipiens, a name with a subgenus
    HHHH  Amsinckia menziesii, with two separate BCSEE species on its key,
          so neither English name is the species' own
"""

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from api.settings import Settings
from conftest import BCSEE_SQL, CARD_SQL, write_test_data

BCSEE_SOURCE = "BC Species and Ecosystems Explorer (BCSEE)"


def extra_card(key, col_name, records):
    """SQL for one more card, copied from AAAA with a new key, name and
    record count, and no image, BCSEE row or merged keys."""
    return (f"SELECT * REPLACE ('{key}' AS specieskey, '{col_name}' AS col_name, "
            f"'{col_name}' AS scientificname, {records}::BIGINT AS record_count, "
            f"NULL::VARCHAR[] AS merged_keys) FROM ({CARD_SQL}) WHERE specieskey = 'AAAA'")


CARDS = " UNION ALL ".join([
    f"SELECT * FROM ({CARD_SQL})",
    extra_card("EEEE", "Robinia pseudoacacia", 3),
    extra_card("FFFF", "Dahlia × pinnata", 2),
    extra_card("GGGG", "Culex (Culex) pipiens", 4),
    extra_card("HHHH", "Amsinckia menziesii", 6),
])

# Amsinckia menziesii: BCSEE lists two separate species on this key, so
# neither entry is species-level for the GBIF species.
AMSINCKIA = """
    SELECT 'HHHH', 'Amsinckia menziesii',
           'Yellow: Amsinckia menziesii [small-flowered fiddleneck]; Unknown: Amsinckia intermedia [common fiddleneck]',
           true, true, ['Yellow'], ['PDBOR02080', 'PDBOR02040'],
           [{'status': 'Yellow', 'scientific_label': 'Amsinckia menziesii',
             'name_source': 'GBIF', 'common_label': 'small-flowered fiddleneck',
             'is_species_level': false, 'is_gbif_synonym': false,
             'matched_species_keys': ['HHHH'], 'element_codes': ['PDBOR02080']},
            {'status': 'Unknown', 'scientific_label': 'Amsinckia intermedia',
             'name_source': 'GBIF', 'common_label': 'common fiddleneck',
             'is_species_level': false, 'is_gbif_synonym': true,
             'matched_species_keys': ['HHHH'], 'element_codes': ['PDBOR02040']}],
           ['HHHH'], 'BC Species and Ecosystems Explorer (BCSEE)', '2026-05-05', '2026-09-26'
"""


@pytest.fixture
def client(tmp_path):
    """A test client on the shared test data plus the search species."""
    write_test_data(tmp_path, card_sql=CARDS, bcsee_sql=f"{BCSEE_SQL} UNION ALL {AMSINCKIA}")
    with TestClient(create_app(Settings(data_dir=tmp_path))) as test_client:
        yield test_client


def search(client, query, **params):
    """Run a search and return the JSON response, checking it worked."""
    response = client.get("/api/search", params={"q": query, **params})
    assert response.status_code == 200, response.text
    return response.json()


def keys(response):
    """The species keys of a search response, in order."""
    return [result["specieskey"] for result in response["results"]]


def test_robin_puts_american_robin_first(client):
    """"robin" starts a word in "American Robin" and in "Robinia
    pseudoacacia"; the robin has more records, so it comes first."""
    response = search(client, "robin")
    assert keys(response) == ["AAAA", "EEEE"]
    robin = response["results"][0]
    assert robin["match"] == {"field": "english_name", "text": "American Robin",
                              "kind": "word_start", "bcsee_entry_scientific_label": None}
    assert robin["english_name"] == "American Robin"
    assert robin["english_name_source"] == BCSEE_SOURCE
    assert robin["display_names"] == [
        {"text": "Turdus migratorius", "field": "col_name",
         "source": "Catalogue of Life, via GBIF"},
        {"text": "American Robin", "field": "english_name", "source": BCSEE_SOURCE},
    ]


def test_name_without_subgenus_finds_it(client):
    """"Culex pipiens" finds "Culex (Culex) pipiens" as an exact match, and
    the full name with its subgenus still works."""
    for query in ("Culex pipiens", "culex (culex) pipiens"):
        result = search(client, query)["results"][0]
        assert result["specieskey"] == "GGGG"
        assert result["match"]["kind"] == "exact"
        assert result["match"]["text"] == "Culex (Culex) pipiens"


def test_hybrid_queries(client):
    """"Dahlia pinnata", "Dahlia x pinnata" and "Dahlia × pinnata" all find
    "Dahlia × pinnata" as an exact match."""
    for query in ("Dahlia pinnata", "Dahlia x pinnata", "dahlia × pinnata"):
        result = search(client, query)["results"][0]
        assert (result["specieskey"], result["match"]["kind"]) == ("FFFF", "exact")


def test_bcsee_entry_english_name_is_marked(client):
    """"small-flowered fiddleneck" finds Amsinckia menziesii, but says the
    name belongs to a BCSEE entry, so it is not shown as the species'
    English name."""
    result = search(client, "small-flowered fiddleneck")["results"][0]
    assert result["specieskey"] == "HHHH"
    assert result["english_name"] is None
    assert result["english_name_source"] is None
    assert result["match"] == {"field": "bcsee_entry_english_name",
                               "text": "small-flowered fiddleneck", "kind": "exact",
                               "bcsee_entry_scientific_label": "Amsinckia menziesii"}
    assert result["display_names"][-1] == {
        "text": "small-flowered fiddleneck", "field": "bcsee_entry_english_name",
        "source": BCSEE_SOURCE, "bcsee_entry_scientific_label": "Amsinckia menziesii"}


def test_bcsee_scientific_label_finds_the_card(client):
    """A BCSEE scientific label that is not the card name (here a separate
    BCSEE species on the same key) finds the card, shown third."""
    result = search(client, "Amsinckia intermedia")["results"][0]
    assert result["specieskey"] == "HHHH"
    assert result["match"]["field"] == "bcsee_scientific_label"
    assert [name["field"] for name in result["display_names"]] == [
        "col_name", "bcsee_scientific_label"]


def test_exact_key_in_any_case(client):
    """A typed species key finds its card first, in any letter case."""
    for query in ("AAAA", "aaaa", " AAAA "):
        result = search(client, query)["results"][0]
        assert result["specieskey"] == "AAAA"
        assert result["match"]["field"] == "specieskey"
        assert result["merged_from"] is None


def test_old_merged_key_gives_the_kept_card(client):
    """An old merged key finds the kept key's card with merged_from."""
    result = search(client, "OLD1")["results"][0]
    assert result["specieskey"] == "BBBB"
    assert result["merged_from"] == "OLD1"
    assert result["match"]["field"] == "merged_key"


def test_bcsee_only_species_is_never_found(client):
    """ZZZZ is in BCSEE but has no records in BC, so no card: not by its
    English name, its scientific name or its key."""
    for query in ("Absent Thing", "Nowhere inbc", "ZZZZ"):
        assert search(client, query)["results"] == []


@pytest.mark.parametrize("query", ["", "r", "  -- ", "x"])
def test_too_short_query_is_refused(client, query):
    """Fewer than 2 letters or digits gives 400 with a clear message."""
    response = client.get("/api/search", params={"q": query})
    assert response.status_code == 400
    assert "at least 2 letters or digits" in response.json()["detail"]


def test_missing_query_and_bad_limit(client):
    """No q at all, or a limit outside 1 to 50, is refused by FastAPI."""
    assert client.get("/api/search").status_code == 422
    assert client.get("/api/search", params={"q": "robin", "limit": 0}).status_code == 422
    assert client.get("/api/search", params={"q": "robin", "limit": 51}).status_code == 422


def test_limit_and_each_species_once(client):
    """limit caps the results, and a species matching on several names
    appears once."""
    response = search(client, "turdus", limit=1)
    assert keys(response) == ["AAAA"]
    # The robin matches "Turdus migratorius" as its card name and as its
    # BCSEE scientific label; it is listed once, under the card name.
    response = search(client, "turdus migratorius")
    assert keys(response) == ["AAAA"]
    assert response["results"][0]["match"]["field"] == "col_name"


def test_ranking_tiers(client):
    """An exact name comes before a word that starts with the query, which
    comes before the query inside a word, whatever the record counts."""
    # "pinnata" is a whole word of FFFF; "ouleri" is only inside a word of
    # BBBB (scouleriana).
    assert search(client, "pinnata")["results"][0]["match"]["kind"] == "word_start"
    assert search(client, "ouleri")["results"][0]["match"]["kind"] == "contains"
    exact_first = search(client, "Robinia pseudoacacia")
    assert keys(exact_first)[0] == "EEEE"
    assert exact_first["results"][0]["match"]["kind"] == "exact"


def test_query_is_normalised_in_the_response(client):
    """The response shows the query as it was searched."""
    response = search(client, "  Scouler's   WILLOW ")
    assert response["normalised_query"] == "scoulers willow"
