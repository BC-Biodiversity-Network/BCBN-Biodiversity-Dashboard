"""Tests for settings.py: where the data folder is and the sensitive-species
switch."""

from pathlib import Path

import pytest

from api.settings import (DEFAULT_DATA_DIR, SettingsError, load_settings,
                          parse_sensitive_max_res)


def test_default_data_dir_is_backend_data_api():
    """With nothing set, the data folder is backend/data/api in this
    repository, not a home folder."""
    settings = load_settings({})
    assert settings.data_dir == DEFAULT_DATA_DIR
    assert settings.data_dir.parts[-3:] == ("backend", "data", "api")


def test_data_dir_from_environment():
    """BCBN_API_DATA_DIR replaces the default, and ~ is expanded."""
    settings = load_settings({"BCBN_API_DATA_DIR": "~/somewhere/api"})
    assert settings.data_dir == Path.home() / "somewhere" / "api"


def test_sensitive_setting_is_empty_by_default():
    """Unset or empty gives None, the only value the API accepts for now."""
    assert load_settings({}).sensitive_max_res is None
    assert parse_sensitive_max_res("") is None
    assert parse_sensitive_max_res("   ") is None


@pytest.mark.parametrize("text", ["5", "4", "7", "3", "abc", "off"])
def test_sensitive_setting_with_any_value_stops_the_api(text):
    """Any value at all stops the API, with a message saying sensitive-
    species handling is not implemented, so nobody thinks species are
    protected when they are not."""
    with pytest.raises(SettingsError, match="not implemented yet"):
        load_settings({"BCBN_SENSITIVE_MAX_RES": text})
