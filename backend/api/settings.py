"""
settings.py

The API's two settings, read from environment variables when the API
starts.

BCBN_API_DATA_DIR
    The folder holding the six files the API reads:
        species_card_gbif.parquet
        species_hex.parquet
        bcsee_status_by_gbif_species.parquet
        download_info.json
        lunaris_datasets.parquet
        dataset_species_links.parquet
    If it is not set, the API uses backend/data/api, found from where this
    file is, so it works on any machine without naming a home folder. That
    folder is covered by the data/ rule in .gitignore, so the files are
    never committed.

BCBN_SENSITIVE_MAX_RES
    Reserved for coarsening the locations of sensitive species. Which
    species, how coarse, and what happens to the card's extent box and
    photo link are not decided yet (waiting on Evan), so nothing is
    coarsened. It must be left empty: any value stops the API at start-up,
    so nobody can believe species are protected when they are not.
    /api/meta reports it (always null for now).
"""

import os
from dataclasses import dataclass
from pathlib import Path

# backend/api/settings.py -> backend/data/api
DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "api"

# The H3 resolutions the species tables hold (h3_r4 to h3_r7).
RESOLUTIONS = (4, 5, 6, 7)

# The rule for the reserve reminder in the hexagon and area answers and
# their CSV downloads (see reserves.py). This is the one place to change it.
#   "A"  the reminder at every hexagon size (what the site does now)
#   "B"  the reminder only for the two smallest sizes (res 6 and 7); at
#        larger sizes the list of reserve names is empty
# The map's outline layer (/api/reserves) is the same under both rules.
RESERVE_REMINDER_RULE = "A"

# The hexagon sizes (resolutions) each rule gives the reminder for.
RESERVE_REMINDER_RESOLUTIONS = {"A": (4, 5, 6, 7), "B": (6, 7)}


class SettingsError(ValueError):
    """A setting has a value the API cannot use."""


@dataclass(frozen=True)
class Settings:
    """The API's settings. data_dir is the folder with the six data
    files; sensitive_max_res must be None until sensitive-species handling
    exists (see refuse_sensitive_setting). reserve_reminder_rule is
    RESERVE_REMINDER_RULE; it is a field only so the tests can try the
    other rule, and is not read from the environment."""

    data_dir: Path = DEFAULT_DATA_DIR
    sensitive_max_res: int | None = None
    reserve_reminder_rule: str = RESERVE_REMINDER_RULE


def not_implemented_message(value):
    """The message given when BCBN_SENSITIVE_MAX_RES has a value."""
    return (f"BCBN_SENSITIVE_MAX_RES is set to {value!r}, but sensitive-species "
            "handling is not implemented yet: no species' locations would be "
            "coarsened. Leave it empty (unset) until the rule is decided.")


def parse_sensitive_max_res(text):
    """Read the text of BCBN_SENSITIVE_MAX_RES. Empty or missing gives None;
    any other value stops the API (see the top of this file)."""
    if text is None or text.strip() == "":
        return None
    raise SettingsError(not_implemented_message(text.strip()))


def refuse_sensitive_setting(settings):
    """Stop the API if settings ask for sensitive-species handling. This
    also covers Settings made in code, which do not go through
    parse_sensitive_max_res."""
    if settings.sensitive_max_res is not None:
        raise SettingsError(not_implemented_message(settings.sensitive_max_res))


def refuse_unknown_reserve_rule(settings):
    """Stop the API if the reserve reminder rule is not one it knows
    (RESERVE_REMINDER_RESOLUTIONS), so a typing mistake cannot quietly
    turn the reminder off."""
    if settings.reserve_reminder_rule not in RESERVE_REMINDER_RESOLUTIONS:
        raise SettingsError(
            f"The reserve reminder rule is {settings.reserve_reminder_rule!r}; it must be one of "
            + ", ".join(repr(rule) for rule in RESERVE_REMINDER_RESOLUTIONS) + ".")


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
