"""Checks that GET /api/overview/hexes with no year filter gives exactly the
numbers in the overview files the front end ships
(frontend/public/data/bc_hex_r4..r7.csv.gz), for every hexagon at every
resolution: the same hexagons, the same records and the same distinct
species.

This needs the real data in backend/data/api, which is not committed, so it
is skipped where that folder is missing (the other tests use small test
data instead)."""

import csv
import gzip
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from api.settings import RESOLUTIONS, Settings

BACKEND = Path(__file__).resolve().parents[1]
REAL_DATA = BACKEND / "data" / "api"
FRONTEND_DATA = BACKEND.parent / "frontend" / "public" / "data"

pytestmark = pytest.mark.skipif(
    not (REAL_DATA / "species_hex.parquet").is_file(),
    reason="the real data is not in backend/data/api")


@pytest.fixture(scope="module")
def client():
    """A test client on the real data (opened once for all resolutions)."""
    settings = Settings(data_dir=REAL_DATA, sensitive_max_res=None)
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def read_overview_file(res):
    """Read one shipped overview file as {cell: (records, species)}."""
    with gzip.open(FRONTEND_DATA / f"bc_hex_r{res}.csv.gz", "rt", encoding="utf-8") as handle:
        return {row["h3_cell"]: (int(row["occurrences"]), int(row["distinct_species"]))
                for row in csv.DictReader(handle)}


@pytest.mark.parametrize("res", RESOLUTIONS)
def test_no_filter_matches_the_shipped_file(client, res):
    """Every hexagon, record count and species count matches the file."""
    response = client.get("/api/overview/hexes", params={"res": res})
    assert response.status_code == 200, response.text
    result = response.json()
    from_api = {cell: (records, species) for cell, records, species
                in zip(result["cells"], result["occurrences"], result["distinct_species"])}
    from_file = read_overview_file(res)

    assert len(from_api) == len(from_file) == result["hexagon_count"]
    differing = [cell for cell in from_file.keys() | from_api.keys()
                 if from_file.get(cell) != from_api.get(cell)]
    assert differing == [], f"{len(differing)} hexagons differ, e.g. {differing[:5]}"
    assert result["records_shown"] == result["records_total"]
