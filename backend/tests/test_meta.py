"""Tests for GET /api/meta. The client fixture is in conftest.py."""

from fastapi.testclient import TestClient

from api.main import create_app
from api.settings import Settings
from conftest import BCSEE_WITH_NO_STATUS_SQL, write_test_data


def test_meta_totals(client):
    """Species are counted once per card (distinct specieskey after the
    merge); records include those with no year."""
    meta = client.get("/api/meta").json()
    assert meta["species_count"] == 4
    assert meta["record_count"] == 10
    assert meta["records_no_year"] == 2
    assert (meta["first_year"], meta["last_year"]) == (2018, 2021)
    assert meta["resolutions"] == [4, 5, 6, 7]


def test_meta_sources(client):
    """The name and status sources come from the files, for the page's
    source labels."""
    meta = client.get("/api/meta").json()
    assert meta["names"] == {
        "col_release": "COL26.6 XR (ChecklistBank 315557)",
        "name_sources": [{"name_source": "Catalogue of Life, via GBIF",
                          "species_count": 4}],
    }
    assert meta["statuses"] == {
        "status_source": "BC Species and Ecosystems Explorer (BCSEE)",
        "status_source_date": "2026-05-05",
        "status_pulled_on": "2026-09-26",
        # ZZZZ has a status but no card, so it is not counted.
        "species_in_bcsee": 1,
        "species_with_status": 1,
        # The test BCSEE file, like the real one, leaves out species with
        # no shown status.
        "includes_species_without_status": False,
    }
    assert meta["gbif_download"]["doi_url"] == "https://doi.org/10.15468/dl.test"


def test_meta_reports_the_sensitive_setting(client):
    """The sensitive setting is reported, and is always null for now."""
    assert client.get("/api/meta").json()["sensitive_max_res"] is None


def test_meta_counts_species_without_a_status_separately(tmp_path):
    """With a BCSEE file that also lists species without a shown status,
    species_in_bcsee counts them but species_with_status does not."""
    write_test_data(tmp_path, bcsee_sql=BCSEE_WITH_NO_STATUS_SQL)
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        statuses = client.get("/api/meta").json()["statuses"]
    # AAAA is Yellow; CCCC is in BCSEE as Not Reviewed only.
    assert statuses["species_in_bcsee"] == 2
    assert statuses["species_with_status"] == 1
    assert statuses["includes_species_without_status"] is True
