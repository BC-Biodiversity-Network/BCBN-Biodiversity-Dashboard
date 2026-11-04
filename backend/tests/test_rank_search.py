"""Tests for the ranks in GET /api/search (api/rank_search.py) and the
sensitive-species hook they go through (api/sensitive.py). The test data is
test_ranks.py's (conftest.py's species, two Prunellas, a second Turdus, a
bee with no order, a thrush with no genus), with two more species of one
genus that GBIF files once under an order and once with none, as it does
for Lachesilla in the real data:

    JJJJ  Lachesilla one  Animalia Arthropoda Insecta Psocodea Lachesillidae
                          Lachesilla, 2 records
    KKKK  Lachesilla two  Animalia Arthropoda Insecta (no order)
                          Lachesillidae Lachesilla, 1 record"""

import pytest
from fastapi.testclient import TestClient

import api.rank_search
from api.main import create_app
from api.rank_search import parent_words, rank_label
from api.settings import Settings
from conftest import write_test_data
from test_ranks import RANK_CARD_SQL, RANK_HEX_SQL, extra

SEARCH_CARD_SQL = f"""{RANK_CARD_SQL}
    UNION ALL {extra('JJJJ', 'Lachesilla one', 'Animalia', 'Arthropoda', 'Insecta', 'Psocodea', 'Lachesillidae',
                     'Lachesilla', 2, 0, 2010, 2011)}
    UNION ALL {extra('KKKK', 'Lachesilla two', 'Animalia', 'Arthropoda', 'Insecta', None, 'Lachesillidae',
                     'Lachesilla', 1, 0, 2012, 2012)}
"""


def make_client(tmp_path):
    """A test client on the search test data."""
    write_test_data(tmp_path, card_sql=SEARCH_CARD_SQL, hex_sql=RANK_HEX_SQL)
    return TestClient(create_app(Settings(data_dir=tmp_path, sensitive_max_res=None)))


@pytest.fixture
def client(tmp_path):
    """A started test client on the search test data."""
    with make_client(tmp_path) as test_client:
        yield test_client


def ranks(client, q, **params):
    """The ranks a search finds, as [(label, species, records, match kind)]."""
    response = client.get("/api/search", params={"q": q, **params})
    assert response.status_code == 200, response.text
    return [(row["label"], row["species_count"], row["record_count"], row["match"]["kind"])
            for row in response.json()["ranks"]]


def test_homonyms_are_labelled_with_their_parent(client):
    assert ranks(client, "prunella") == [
        ("Genus Prunella (Lamiaceae)", 1, 4, "exact"),
        ("Genus Prunella (Prunellidae)", 1, 2, "exact"),
    ]
    # "prunella" is not in "prunellidae"; "prunell" is in both.
    assert ranks(client, "prunell") == [
        ("Genus Prunella (Lamiaceae)", 1, 4, "word_start"),
        ("Family Prunellidae (Passeriformes)", 1, 2, "word_start"),
        ("Genus Prunella (Prunellidae)", 1, 2, "word_start"),
    ]


def test_each_rank_comes_with_its_full_path(client):
    response = client.get("/api/search", params={"q": "prunella"}).json()
    plant = response["ranks"][0]
    assert (plant["rank"], plant["name"]) == ("genus", "Prunella")
    assert plant["path"] == [{"rank": "kingdom", "name": "Plantae"}, {"rank": "phylum", "name": "Tracheophyta"},
                             {"rank": "class", "name": "Magnoliopsida"}, {"rank": "order", "name": "Lamiales"},
                             {"rank": "family", "name": "Lamiaceae"}, {"rank": "genus", "name": "Prunella"}]
    # The species search is unchanged and still finds both species.
    assert {row["specieskey"] for row in response["results"]} == {"EEEE", "FFFF"}


def test_a_shared_parent_makes_the_label_walk_up_the_path(client):
    """Lachesilla is filed under Lachesillidae twice, once with the order
    Psocodea and once with none; the family is told apart by its parent."""
    assert ranks(client, "lachesilla") == [
        ("Genus Lachesilla (Lachesillidae, Psocodea)", 1, 2, "exact"),
        ("Genus Lachesilla (Lachesillidae, no order)", 1, 1, "exact"),
    ]
    assert ranks(client, "lachesillidae") == [
        ("Family Lachesillidae (Psocodea)", 1, 2, "exact"),
        ("Family Lachesillidae (Insecta)", 1, 1, "exact"),
    ]


def test_a_missing_parent_is_skipped_for_the_nearest_named_one(client):
    # Equal on everything else, the one with the missing order comes first.
    assert ranks(client, "apidae") == [
        ("Family Apidae (Insecta)", 1, 1, "exact"),
        ("Family Apidae (Hymenoptera)", 1, 1, "exact"),
    ]


def test_counts_tiers_and_order(client):
    assert ranks(client, "turdidae") == [("Family Turdidae (Passeriformes)", 3, 13, "exact")]
    # Both start with "turd"; the family has more records.
    assert ranks(client, "turd") == [("Family Turdidae (Passeriformes)", 3, 13, "word_start"),
                                     ("Genus Turdus (Turdidae)", 2, 12, "word_start")]
    assert ranks(client, "animalia") == [("Kingdom Animalia", 8, 20, "exact")]
    assert ranks(client, "ordat") == [("Phylum Chordata (Animalia)", 4, 15, "contains")]


def test_how_many_ranks(client):
    assert ranks(client, "lachesilla", rank_limit=2) == [
        ("Genus Lachesilla (Lachesillidae, Psocodea)", 1, 2, "exact"),
        ("Genus Lachesilla (Lachesillidae, no order)", 1, 1, "exact"),
    ]
    assert ranks(client, "prunella", rank_limit=0) == []
    assert client.get("/api/search", params={"q": "prunella", "rank_limit": 21}).status_code == 422
    assert client.get("/api/search", params={"q": "p"}).status_code == 400


def test_labels_without_the_api():
    """The label rules on their own: no parent, a parent that tells the
    ranks apart, and a walk up the path when it does not."""
    kingdom = [("kingdom", "Animalia")]
    assert rank_label(kingdom, []) == "Kingdom Animalia"
    plant = [("kingdom", "Plantae"), ("phylum", "Tracheophyta"), ("class", "Magnoliopsida"), ("order", "Lamiales"),
             ("family", "Lamiaceae"), ("genus", "Prunella")]
    assert parent_words(plant, []) == ["Lamiaceae"]
    with_order = [("kingdom", "Animalia"), ("phylum", "Arthropoda"), ("class", "Insecta"), ("order", "Psocodea"),
                  ("family", "Lachesillidae"), ("genus", "Lachesilla")]
    no_order = [*with_order[:3], ("order", None), *with_order[4:]]
    assert parent_words(with_order, [no_order]) == ["Lachesillidae", "Psocodea"]
    assert parent_words(no_order, [with_order]) == ["Lachesillidae", "no order"]


def test_the_rank_search_applies_the_sensitive_setting(tmp_path, monkeypatch):
    """The rank names are built once, at start-up, from the species
    sensitive.hidden_species gives with no resolution, asked with the API's
    own settings. Here a stand-in rule hides the bird Prunella (FFFF) and
    one thrush (GGGG): the bird genus and its family are not found, and no
    count includes them."""
    asked = []

    def stand_in_rule(settings, res):
        """Hide FFFF and GGGG, and record what was asked."""
        asked.append((settings, res))
        return frozenset({"FFFF", "GGGG"})

    monkeypatch.setattr(api.rank_search, "hidden_species", stand_in_rule)
    with make_client(tmp_path) as client:
        assert ranks(client, "prunella") == [("Genus Prunella (Lamiaceae)", 1, 4, "exact")]
        assert ranks(client, "turdidae") == [("Family Turdidae (Passeriformes)", 2, 6, "exact")]
        assert asked == [(client.app.state.data.settings, None)]
