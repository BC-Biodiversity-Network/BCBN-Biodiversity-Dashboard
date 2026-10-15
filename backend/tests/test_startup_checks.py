"""Tests for the checks db.py runs when the API starts: a missing or wrong
data file must stop the start-up with a message naming every problem."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from api.db import DataFilesError, open_data
from api.main import create_app
from api.settings import Settings, SettingsError
from conftest import BCSEE_SQL, CARD_SQL, DOWNLOAD_INFO, write_test_data


def test_good_data_opens(settings):
    """The test data passes every check."""
    data = open_data(settings)
    data.close()


def test_missing_folder(tmp_path):
    """A folder that does not exist says which setting to change."""
    with pytest.raises(DataFilesError, match="BCBN_API_DATA_DIR"):
        open_data(Settings(data_dir=tmp_path / "not_there"))


def test_every_missing_file_is_listed(settings, data_dir):
    """With two files gone, both are named, not just the first."""
    (data_dir / "species_hex.parquet").unlink()
    (data_dir / "download_info.json").unlink()
    with pytest.raises(DataFilesError) as raised:
        open_data(settings)
    assert "species_hex.parquet is missing" in str(raised.value)
    assert "download_info.json is missing" in str(raised.value)


def test_missing_column(tmp_path):
    """A card file without a column the API reads is refused."""
    write_test_data(tmp_path, card_sql=f"SELECT * EXCLUDE (merged_keys) FROM ({CARD_SQL})")
    with pytest.raises(DataFilesError, match="species_card_gbif.parquet has no column.*merged_keys"):
        open_data(Settings(data_dir=tmp_path))


def test_missing_download_field(tmp_path):
    """download_info.json without a DOI is refused, so the page always has
    the download to cite."""
    info = {key: value for key, value in DOWNLOAD_INFO.items() if key != "doi_url"}
    write_test_data(tmp_path, download_info=info)
    with pytest.raises(DataFilesError, match="no value for doi_url"):
        open_data(Settings(data_dir=tmp_path))


def test_two_col_releases_are_refused(tmp_path):
    """Cards from two Catalogue of Life releases mean mixed builds."""
    card = (f"SELECT * REPLACE (CASE WHEN specieskey = 'BBBB' THEN 'other release' "
            f"ELSE col_release END AS col_release) FROM ({CARD_SQL})")
    write_test_data(tmp_path, card_sql=card)
    with pytest.raises(DataFilesError, match="exactly one col_release"):
        open_data(Settings(data_dir=tmp_path))


def test_two_bcsee_dates_are_refused(tmp_path):
    """Statuses from two BCSEE exports mean mixed builds."""
    bcsee = (f"SELECT * REPLACE (CASE WHEN gbif_species_key = 'ZZZZ' THEN '2025-01-01' "
             f"ELSE status_source_date END AS status_source_date) FROM ({BCSEE_SQL})")
    write_test_data(tmp_path, bcsee_sql=bcsee)
    with pytest.raises(DataFilesError, match="exactly one status_source"):
        open_data(Settings(data_dir=tmp_path))


def test_merged_key_with_its_own_card_is_refused(tmp_path):
    """An old key that still has a card would give two answers."""
    card = (f"SELECT * REPLACE (CASE WHEN specieskey = 'BBBB' THEN ['AAAA'] "
            f"ELSE merged_keys END AS merged_keys) FROM ({CARD_SQL})")
    write_test_data(tmp_path, card_sql=card)
    with pytest.raises(DataFilesError, match="still have a card of their own: AAAA"):
        open_data(Settings(data_dir=tmp_path))


def test_merged_key_on_two_cards_is_refused(tmp_path):
    """An old key merged into two different cards would give two answers."""
    card = (f"SELECT * REPLACE (CASE WHEN specieskey = 'CCCC' THEN ['OLD1'] "
            f"ELSE merged_keys END AS merged_keys) FROM ({CARD_SQL})")
    write_test_data(tmp_path, card_sql=card)
    with pytest.raises(DataFilesError, match="more than one card: OLD1"):
        open_data(Settings(data_dir=tmp_path))


def test_repeated_card_key_is_refused(tmp_path):
    """Two cards with the same key would give two answers."""
    write_test_data(tmp_path, card_sql=f"{CARD_SQL} UNION ALL SELECT * FROM ({CARD_SQL}) WHERE specieskey = 'AAAA'")
    with pytest.raises(DataFilesError, match="1 repeated specieskey"):
        open_data(Settings(data_dir=tmp_path))


def test_sensitive_setting_stops_start_up(settings):
    """Settings made in code with a sensitive resolution are refused too,
    both by open_data and by the API's own start-up."""
    asking = replace(settings, sensitive_max_res=5)
    with pytest.raises(SettingsError, match="not implemented yet"):
        open_data(asking)
    with pytest.raises(SettingsError, match="not implemented yet"):
        with TestClient(create_app(asking)):
            pass
