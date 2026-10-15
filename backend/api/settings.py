"""
settings.py

The API's two settings, read from environment variables when the API
starts.

BCBN_API_DATA_DIR
    The folder holding the four files the API reads:
        species_card_gbif.parquet
        species_hex.parquet
        bcsee_status_by_gbif_species.parquet
        download_info.json
    If it is not set, the API uses backend/data/api, found from where this
    file is, so it works on any machine without naming a home folder. That
    folder is covered by the data/ rule in .gitignore, so the files are
    never committed.

BCBN_SENSITIVE_MAX_RES
    The switch for coarsening the locations of sensitive species. Empty or
    unset means off, which is the setting for now. A number from 4 to 7
    turns it on: the species map for a sensitive species is never finer
    than that H3 resolution, and the card's extent box is coarsened to
    match. Which species count as sensitive is not decided yet (waiting on
    Evan), so this only builds the switch; the endpoints that use it
    (the species card and the species map) both read it from here.
"""

import os
from dataclasses import dataclass
from pathlib import Path

# backend/api/settings.py -> backend/data/api
DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "api"

# The H3 resolutions the species tables hold (h3_r4 to h3_r7).
RESOLUTIONS = (4, 5, 6, 7)


class SettingsError(ValueError):
    """A setting has a value the API cannot use."""


@dataclass(frozen=True)
class Settings:
    """The API's settings. data_dir is the folder with the four data
    files; sensitive_max_res is None when coarsening is off, or the finest
    H3 resolution a sensitive species may be shown at."""

    data_dir: Path = DEFAULT_DATA_DIR
    sensitive_max_res: int | None = None


def parse_sensitive_max_res(text):
    """Turn the text of BCBN_SENSITIVE_MAX_RES into a resolution or None.

    Empty or missing text means off (None). Anything else must be one of
    the stored resolutions, 4 to 7; any other value stops the API, because
    a mistyped privacy setting must never quietly mean "off"."""
    if text is None or text.strip() == "":
        return None

    try:
        value = int(text.strip())
    except ValueError:
        raise SettingsError(
            f"BCBN_SENSITIVE_MAX_RES must be empty (off) or one of "
            f"{list(RESOLUTIONS)}, not {text!r}") from None

    if value not in RESOLUTIONS:
        raise SettingsError(
            f"BCBN_SENSITIVE_MAX_RES must be empty (off) or one of "
            f"{list(RESOLUTIONS)}, not {value}")

    return value


def load_settings(environ=None):
    """Read the settings from the environment (os.environ unless another
    mapping is given, which the tests use) and return a Settings."""
    if environ is None:
        environ = os.environ

    data_dir_text = environ.get("BCBN_API_DATA_DIR", "").strip()
    # expanduser lets the variable be written as ~/something.
    data_dir = Path(data_dir_text).expanduser() if data_dir_text else DEFAULT_DATA_DIR

    return Settings(
        data_dir=data_dir,
        sensitive_max_res=parse_sensitive_max_res(environ.get("BCBN_SENSITIVE_MAX_RES")),
    )
