"""Tests for settings.py: where the data folder is, the sensitive-species
switch and DuckDB's memory and thread limits."""

from pathlib import Path

import pytest

from api.settings import (DEFAULT_DATA_DIR, DEFAULT_DUCKDB_MEMORY_LIMIT, DEFAULT_DUCKDB_THREADS, Settings,
                          SettingsError, duckdb_config, load_settings, parse_duckdb_memory_limit,
                          parse_duckdb_threads, parse_sensitive_max_res, refuse_bad_duckdb_limits)


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


def test_duckdb_limits_default_to_2gb_and_2_threads():
    """With nothing set, DuckDB gets 2GB and 2 threads, in loaded settings
    and in Settings made in code alike; empty text counts as unset."""
    assert DEFAULT_DUCKDB_MEMORY_LIMIT == "2GB" and DEFAULT_DUCKDB_THREADS == 2
    for settings in (load_settings({}), Settings(),
                     load_settings({"BCBN_DUCKDB_MEMORY_LIMIT": "  ", "BCBN_DUCKDB_THREADS": ""})):
        assert settings.duckdb_memory_limit == "2GB"
        assert settings.duckdb_threads == 2
        assert duckdb_config(settings) == {"memory_limit": "2GB", "threads": 2}


@pytest.mark.parametrize("text, expected", [
    ("4GB", "4GB"), ("1.5GB", "1.5GB"), ("500MB", "500MB"), ("2GiB", "2GiB"),
    (" 3 gb ", "3gb"), ("100MB", "100MB"), ("1TB", "1TB"), ("200000KB", "200000KB")])
def test_duckdb_memory_limit_from_environment(text, expected):
    """BCBN_DUCKDB_MEMORY_LIMIT replaces the default, spaces taken out; the
    unit may be in any case, and 100MB (the floor) is allowed."""
    assert load_settings({"BCBN_DUCKDB_MEMORY_LIMIT": text}).duckdb_memory_limit == expected


@pytest.mark.parametrize("text, expected", [("1", 1), ("4", 4), (" 8 ", 8), ("16", 16)])
def test_duckdb_threads_from_environment(text, expected):
    """BCBN_DUCKDB_THREADS replaces the default."""
    assert load_settings({"BCBN_DUCKDB_THREADS": text}).duckdb_threads == expected


@pytest.mark.parametrize("text", [
    "2", "GB", "lots", "-1GB", "+2GB", "2XB", "2 G B", "100%", ".5GB", "2.GB", "1e3MB", "2GB extra"])
def test_a_memory_limit_that_is_not_a_number_and_a_unit_stops_the_api(text):
    """Anything but a number and a unit DuckDB knows is refused with a
    message naming the variable; "-1GB" too, which DuckDB would take as no
    limit at all."""
    with pytest.raises(SettingsError, match="BCBN_DUCKDB_MEMORY_LIMIT.*number and a unit"):
        load_settings({"BCBN_DUCKDB_MEMORY_LIMIT": text})


@pytest.mark.parametrize("text", ["0GB", "0.09GB", "99MB", "50MiB", "1KB"])
def test_a_memory_limit_under_100mb_stops_the_api(text):
    """A limit too small for the API to load its data is refused at
    start-up, with a message that says so, rather than DuckDB running out
    of memory halfway through."""
    with pytest.raises(SettingsError, match="BCBN_DUCKDB_MEMORY_LIMIT.*at least 100MB"):
        parse_duckdb_memory_limit(text)


@pytest.mark.parametrize("text", ["0", "-1", "two", "1.5", "2.0", "+2", "²", "٣"])
def test_a_thread_count_that_is_not_a_whole_number_of_1_or_more_stops_the_api(text):
    """Zero, negative, words, decimals and digits other than 0-9 are all
    refused with a message naming the variable."""
    with pytest.raises(SettingsError, match="BCBN_DUCKDB_THREADS.*whole number, 1 or more"):
        load_settings({"BCBN_DUCKDB_THREADS": text})
    with pytest.raises(SettingsError):
        parse_duckdb_threads(text)


@pytest.mark.parametrize("changes, message", [
    ({"duckdb_memory_limit": "-1GB"}, "number and a unit"),
    ({"duckdb_memory_limit": "10MB"}, "at least 100MB"),
    ({"duckdb_memory_limit": 2_000_000_000}, "must be text"),
    ({"duckdb_threads": 0}, "thread count"),
    ({"duckdb_threads": "2"}, "thread count"),
    ({"duckdb_threads": True}, "thread count"),
    ({"duckdb_threads": 1.5}, "thread count")])
def test_bad_duckdb_limits_made_in_code_are_refused(changes, message):
    """Settings made in code, which skip the parse functions, are held to
    the same rules."""
    with pytest.raises(SettingsError, match=message):
        refuse_bad_duckdb_limits(Settings(**changes))


def test_good_duckdb_limits_made_in_code_pass():
    """The defaults and other good values pass the check."""
    refuse_bad_duckdb_limits(Settings())
    refuse_bad_duckdb_limits(Settings(duckdb_memory_limit="1.5GiB", duckdb_threads=1))
