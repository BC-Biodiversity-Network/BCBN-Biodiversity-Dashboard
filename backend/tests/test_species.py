"""Tests for GET /api/species/{key}. The client fixture and the test species
are in conftest.py."""

from fastapi.testclient import TestClient

from api.main import create_app
from api.settings import Settings
from conftest import BCSEE_WITH_NO_STATUS_SQL, write_test_data

BCSEE_SOURCE = {
    "status_source": "BC Species and Ecosystems Explorer (BCSEE)",
    "status_source_date": "2026-05-05",
    "status_pulled_on": "2026-09-26",
}


def test_card_name_and_counts(client):
    """The name block is the Catalogue of Life's, with its source and
    release for the label, and the counts are the card's own."""
    card = client.get("/api/species/AAAA").json()
    assert card["specieskey"] == "AAAA"
    assert card["requested_key"] == "AAAA"
    assert card["merged_from"] is None
    assert card["name"] == {
        "col_name": "Turdus migratorius",
        "col_authorship": "Linnaeus, 1766",
        "scientificname": "Turdus migratorius Linnaeus, 1766",
        "name_source": "Catalogue of Life, via GBIF",
        "col_release": "COL26.6 XR (ChecklistBank 315557)",
    }
    assert card["records"] == {
        "record_count": 5, "records_no_year": 2, "first_year": 2020,
        "last_year": 2020, "n_datasets": 1, "merged_keys": [],
        "merged_record_count": 0,
    }
    assert card["taxonomy"]["order"] == "Passeriformes"
    assert card["top_datasets"] == [
        {"datasetkey": "d1", "title": "Test dataset", "records": 5}]


def test_subgenus_name_is_returned_as_stored(client):
    """A name with a subgenus in brackets is not changed; whether the title
    shows the subgenus is up to the page."""
    card = client.get("/api/species/CCCC").json()
    assert card["name"]["col_name"] == "Bombus (Pyrobombus) vosnesenskii"


def test_bcsee_listed_species(client):
    """A species with a shown status: every status entry carries the BCSEE
    source and dates, and the English name says it comes from BCSEE."""
    card = client.get("/api/species/AAAA").json()
    assert card["english_name"] == "American Robin"
    assert card["english_name_source"] == "BC Species and Ecosystems Explorer (BCSEE)"

    bcsee = card["bcsee"]
    assert bcsee["state"] == "listed"
    assert bcsee["statuses"] == ["Yellow"]
    for field, value in BCSEE_SOURCE.items():
        assert bcsee[field] == value
    assert bcsee["entries"] == [{
        "status": "Yellow",
        "scientific_label": "Turdus migratorius",
        "scientific_label_source": "GBIF",
        "common_label": "American Robin",
        "is_species_level": True,
        "is_gbif_synonym": False,
        "element_codes": ["ABPBJ20170"],
        **BCSEE_SOURCE,
    }]


def test_species_not_in_the_bcsee_file_is_unknown(client):
    """The current BCSEE file leaves out species without a shown status,
    so a species missing from it may still be in BCSEE: the state is
    unknown, not "not in BCSEE". The source and dates are still there."""
    bcsee = client.get("/api/species/BBBB").json()["bcsee"]
    assert bcsee["state"] == "unknown"
    assert bcsee["statuses"] == []
    assert bcsee["entries"] == []
    for field, value in BCSEE_SOURCE.items():
        assert bcsee[field] == value


def test_not_in_bcsee_and_no_status_are_different(tmp_path):
    """Once the BCSEE file also lists species without a shown status, a
    species with no row is "not in BCSEE", and one whose entries have no
    shown status is "in BCSEE but no status"."""
    write_test_data(tmp_path, bcsee_sql=BCSEE_WITH_NO_STATUS_SQL)
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        not_in = client.get("/api/species/BBBB").json()
        assert not_in["bcsee"]["state"] == "not_in_bcsee"
        assert not_in["english_name"] is None

        no_status = client.get("/api/species/CCCC").json()
        assert no_status["bcsee"]["state"] == "in_bcsee_no_status"
        assert no_status["bcsee"]["statuses"] == []
        assert no_status["bcsee"]["entries"][0]["status"] == "Not Reviewed"
        assert no_status["english_name"] == "Yellow-faced Bumble Bee"

        # A listed species is still listed.
        assert client.get("/api/species/AAAA").json()["bcsee"]["state"] == "listed"


def test_merged_key_gives_the_kept_card(client):
    """An old key that was merged gives the kept key's card and says which
    key was asked for."""
    response = client.get("/api/species/OLD1")
    assert response.status_code == 200
    card = response.json()
    assert card["specieskey"] == "BBBB"
    assert card["requested_key"] == "OLD1"
    assert card["merged_from"] == "OLD1"
    assert card["records"]["merged_keys"] == ["OLD1"]
    assert card["records"]["merged_record_count"] == 1


def test_unknown_key_is_404(client):
    """A key that is neither a card nor a merged key gives 404 with a
    message naming the key."""
    response = client.get("/api/species/NOPE")
    assert response.status_code == 404
    assert "'NOPE'" in response.json()["detail"]


def test_bcsee_only_species_is_404(client):
    """A species BCSEE lists but with no records in BC has no card."""
    assert client.get("/api/species/ZZZZ").status_code == 404


def test_inaturalist_image_uses_medium_size(client):
    """An iNaturalist photo is returned at medium size, with its full
    credit: creator, licence label and link, and the link to the original."""
    assert client.get("/api/species/AAAA").json()["image"] == {
        "url": "https://inaturalist-open-data.s3.amazonaws.com/photos/1/medium.jpg",
        "page_url": "https://www.inaturalist.org/photos/1",
        "creator": "A. Person",
        "rightsholder": "A. Person",
        "license_code": "CC_BY_4_0",
        "license_label": "CC BY 4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "no_derivatives": False,
    }


def test_image_without_creator_is_not_returned(client):
    """An image with no creator cannot be credited, so there is none."""
    assert client.get("/api/species/CCCC").json()["image"] is None


def test_card_without_image(client):
    """A card with no image at all has image null."""
    assert client.get("/api/species/BBBB").json()["image"] is None


def test_nd_image_is_flagged_and_not_resized(client):
    """An ND image from another site keeps its own URL and is flagged, so
    the page never crops it."""
    image = client.get("/api/species/DDDD").json()["image"]
    assert image["url"] == "https://images.example.org/carex/original.jpg"
    assert image["license_label"] == "CC BY-NC-ND 4.0"
    assert image["no_derivatives"] is True


def test_extent_is_the_cards_own(client):
    """The extent box is the card's own; nothing is coarsened."""
    assert client.get("/api/species/AAAA").json()["extent"] == {
        "min_lat": 49.1, "max_lat": 49.2, "min_lon": -123.2, "max_lon": -123.1,
    }
