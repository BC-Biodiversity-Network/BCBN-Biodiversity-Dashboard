"""Tests for GET /api/meta."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from api.main import create_app


@pytest.fixture
def client(settings):
    """A test client for the API on the test data. Using it in a with
    block runs the start-up, which opens the data."""
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def test_meta_totals(client):
    """Species are counted once per card (distinct specieskey after the
    merge); records include those with no year."""
    meta = client.get("/api/meta").json()
    assert meta["species_count"] == 2
    assert meta["record_count"] == 8
    assert meta["records_no_year"] == 2
    assert (meta["first_year"], meta["last_year"]) == (2019, 2020)
    assert meta["resolutions"] == [4, 5, 6, 7]


def test_meta_sources(client):
    """The name and status sources come from the files, for the page's
    source labels."""
    meta = client.get("/api/meta").json()
    assert meta["names"] == {
        "col_release": "COL26.6 XR (ChecklistBank 315557)",
        "name_sources": [{"name_source": "Catalogue of Life, via GBIF",
                          "species_count": 2}],
    }
    assert meta["statuses"] == {
        "status_source": "BC Species and Ecosystems Explorer (BCSEE)",
        "status_source_date": "2026-05-05",
        "status_pulled_on": "2026-09-26",
        # ZZZZ has a status but no card, so it is not counted.
        "species_with_status": 1,
    }
    assert meta["gbif_download"]["doi_url"] == "https://doi.org/10.15468/dl.test"


def test_meta_sensitive_switch_off(client):
    """With the switch off the page is told so (null)."""
    assert client.get("/api/meta").json()["sensitive_max_res"] is None


def test_meta_sensitive_switch_on(settings):
    """With the switch on, meta reports the resolution cap."""
    with TestClient(create_app(replace(settings, sensitive_max_res=5))) as client:
        assert client.get("/api/meta").json()["sensitive_max_res"] == 5
