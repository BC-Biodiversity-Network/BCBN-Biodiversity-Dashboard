"""Tests for the links between Lunaris datasets and species in the API: the
species filter of GET /api/datasets, the linked species and other
organisms of GET /api/datasets/{id}, the count on the species card (the
same datasets as the filtered list), and the start-up checks on
dataset_species_links.parquet. The test links are described at the top of
conftest.py."""

import pytest

from api.db import DataFilesError, open_data
from api.settings import Settings
from conftest import LINKS_SQL, write_test_data

EAGLE_NESTS = "oai:catalogue.data.gov.bc.ca:eagle-nests"
SALMON = "oai:catalogue.hakai.org:salmon"
FOREST = "oai:catalogue.data.gov.bc.ca:forest-plots"
GOLDEN = "oai:datadryad.org:doi:10.5061/dryad.golden"
COPY = "oai:open.canada.ca:eagle-copy"
BOTH = {"frontend_group": ["bc_data", "pending"]}


def ids_of(body):
    """The ids of a list response body's results, in order."""
    return [result["id"] for result in body["results"]]


# --- The species filter ----------------------------------------------------

def test_the_species_filter_keeps_the_datasets_linked_to_it(client):
    """Only BC data by default; the pending dataset too when asked for."""
    body = client.get("/api/datasets", params={"species": "AAAA"}).json()
    assert ids_of(body) == [SALMON, EAGLE_NESTS] and body["total"] == 2
    assert ids_of(client.get("/api/datasets", params={"species": "AAAA", **BOTH}).json()) == [
        SALMON, GOLDEN, EAGLE_NESTS]


def test_the_species_filter_names_the_species(client):
    """The answer names the species, with its English name from BCSEE, so the page can show it."""
    body = client.get("/api/datasets", params={"species": "AAAA"}).json()
    assert body["filters"]["species"] == {"specieskey": "AAAA", "requested_key": "AAAA", "merged_from": None,
                                          "col_name": "Turdus migratorius", "english_name": "American Robin"}


def test_a_merged_key_filters_by_its_card(client):
    """OLD1 was merged into BBBB, so it gives BBBB's datasets and says so."""
    body = client.get("/api/datasets", params={"species": "OLD1"}).json()
    assert ids_of(body) == [FOREST]
    assert body["filters"]["species"]["specieskey"] == "BBBB"
    assert body["filters"]["species"]["merged_from"] == "OLD1"
    assert body["filters"]["species"]["english_name"] is None


def test_a_species_with_no_datasets_gives_none(client):
    """CCCC has a card but no linked dataset."""
    assert client.get("/api/datasets", params={"species": "CCCC"}).json()["total"] == 0


def test_the_species_filter_combines_with_the_others(client):
    """The species filter is applied with the search and the other filters, and in their counts."""
    body = client.get("/api/datasets", params={"species": "AAAA", "q": "eagle", "access": "open"}).json()
    assert ids_of(body) == [EAGLE_NESTS]
    counts = {option["value"]: option["count"] for option in body["options"]["access"]}
    assert counts == {"open": 1, "restricted": 1}
    assert client.get("/api/datasets", params={"species": "AAAA", "mappable": "true", **BOTH}).json()[
        "options"]["location"] == [{"value": "mappable", "count": 2}]


def test_an_unknown_species_gives_400(client):
    """A key with no card (ZZZZ is only in BCSEE) cannot be filtered on."""
    response = client.get("/api/datasets", params={"species": "ZZZZ"})
    assert response.status_code == 400 and "Unknown species 'ZZZZ'" in response.json()["detail"]


# --- The detail --------------------------------------------------------------

def test_the_detail_lists_the_linked_species_once_in_record_order(client):
    """Each species once, with every name as written that links to it; a name that is the card's name is marked."""
    body = client.get(f"/api/datasets/{GOLDEN}").json()
    assert [s["specieskey"] for s in body["species"]] == ["AAAA", "BBBB"]
    eagle = client.get(f"/api/datasets/{EAGLE_NESTS}").json()
    assert eagle["species"] == [{
        "specieskey": "AAAA", "col_name": "Turdus migratorius", "english_name": "American Robin",
        "names_as_written": [{"name": "Turdus migratorius", "same_as_shown": True},
                             {"name": "T. migratorius", "same_as_shown": False}]}]


def test_a_name_from_the_hand_checked_list_is_shown_as_written(client):
    """"american robins" is not a name of the card, so it is marked as different."""
    body = client.get(f"/api/datasets/{SALMON}").json()
    assert body["species"][0]["names_as_written"] == [{"name": "american robins", "same_as_shown": False}]


def test_the_names_that_do_not_link_are_listed(client):
    """The record's other names, as written and in order, so none is hidden."""
    assert client.get(f"/api/datasets/{EAGLE_NESTS}").json()["other_organisms"] == ["bald eagle"]
    assert client.get(f"/api/datasets/{SALMON}").json()["other_organisms"] == ["salmon"]
    forest = client.get(f"/api/datasets/{FOREST}").json()
    assert forest["other_organisms"] == [] and forest["species"][0]["specieskey"] == "BBBB"


def test_another_copy_has_the_shown_datasets_species(client):
    """The id of another copy gives the shown dataset's links."""
    assert client.get(f"/api/datasets/{COPY}").json()["species"][0]["specieskey"] == "AAAA"


def test_a_dataset_with_no_names_has_empty_lists(client):
    """The beagle dataset names no organisms."""
    body = client.get("/api/datasets/oai:canwin-datahub.ad.umanitoba.ca:beagle").json()
    assert (body["species"], body["other_organisms"]) == ([], [])


# --- The species card --------------------------------------------------------

@pytest.mark.parametrize("key, count", [("AAAA", 2), ("BBBB", 1), ("OLD1", 1), ("CCCC", 0)])
def test_the_card_counts_the_bc_data_datasets_the_filter_lists(client, key, count):
    """The card's count is BC data only, and the same datasets as the Datasets page filtered to it."""
    card = client.get(f"/api/species/{key}").json()
    assert card["mentioned_in_datasets"] == {"count": count, "frontend_group": ["bc_data"]}
    assert client.get("/api/datasets", params={"species": key}).json()["total"] == count


# --- Start-up checks -------------------------------------------------------

def refused(tmp_path, links_sql, match):
    """Write test data with this links file and check the API refuses it with match."""
    write_test_data(tmp_path, links_sql=links_sql)
    with pytest.raises(DataFilesError, match=match):
        open_data(Settings(data_dir=tmp_path))


def test_a_link_from_an_unknown_dataset_is_refused(tmp_path):
    """A dataset that is not on the site would be counted on a card."""
    refused(tmp_path, f"SELECT * REPLACE ('oai:nowhere:1' AS dataset_id) FROM ({LINKS_SQL})",
            "dataset ids have species links but are not datasets on the site: oai:nowhere:1")


def test_a_link_to_a_species_with_no_card_is_refused(tmp_path):
    """A merged key or a key with no card cannot be linked to."""
    sql = f"SELECT * REPLACE (CASE WHEN specieskey = 'BBBB' THEN 'OLD1' ELSE specieskey END AS specieskey) " \
          f"FROM ({LINKS_SQL})"
    refused(tmp_path, sql, "species keys have dataset links but no card: OLD1")


def test_a_species_on_a_name_that_does_not_link_is_refused(tmp_path):
    """A deferred or unmatched name must never carry a species."""
    sql = f"SELECT * REPLACE ('AAAA' AS specieskey) FROM ({LINKS_SQL}) WHERE name_as_written = 'bald eagle'"
    refused(tmp_path, f"SELECT * FROM ({LINKS_SQL}) WHERE name_as_written <> 'bald eagle' UNION ALL {sql}",
            "names have a species without a linking match status")


def test_two_names_at_one_position_are_refused(tmp_path):
    """The record order must be clear."""
    refused(tmp_path, f"SELECT * REPLACE (0::BIGINT AS position) FROM ({LINKS_SQL})",
            "two names at the same position")


def test_a_missing_links_file_is_named(tmp_path):
    """Without the links file, the API says which file is missing."""
    write_test_data(tmp_path)
    (tmp_path / "dataset_species_links.parquet").unlink()
    with pytest.raises(DataFilesError, match="dataset_species_links.parquet is missing"):
        open_data(Settings(data_dir=tmp_path))
