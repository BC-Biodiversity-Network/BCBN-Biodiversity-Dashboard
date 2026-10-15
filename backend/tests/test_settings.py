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


def test_sensitive_switch_is_off_by_default():
    """Unset or empty means coarsening is off."""
    assert load_settings({}).sensitive_max_res is None
    assert parse_sensitive_max_res("") is None
    assert parse_sensitive_max_res("   ") is None


def test_sensitive_switch_accepts_a_stored_resolution():
    """A resolution from 4 to 7 turns coarsening on at that resolution."""
    assert parse_sensitive_max_res("5") == 5
    assert parse_sensitive_max_res(" 7 ") == 7
    assert load_settings({"BCBN_SENSITIVE_MAX_RES": "4"}).sensitive_max_res == 4


@pytest.mark.parametrize("text", ["3", "8", "abc", "5.5", "off"])
def test_sensitive_switch_refuses_other_values(text):
    """Anything else stops the API instead of quietly meaning off."""
    with pytest.raises(SettingsError):
        parse_sensitive_max_res(text)
