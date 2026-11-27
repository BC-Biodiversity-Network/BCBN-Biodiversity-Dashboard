"""
settings.py

The API's settings, read from environment variables when the API starts.

BCBN_API_DATA_DIR
    The folder holding the API's data files (about 45 MB in all). The
    seven required ones, which db.py checks at start-up (the API refuses
    to start, listing every problem, if any is missing or lacks a column
    it reads):
        species_hex.parquet
        species_card_gbif.parquet
        lunaris_datasets.parquet
        bcsee_status_by_gbif_species.parquet
        dataset_species_links.parquet
        dataset_source_checks.parquet
        download_info.json
    And three optional reserve files, read by reserves.py, which go
    together: if any is missing or wrong, the API still starts, with the
    reserve layer and reminders off and one warning logged:
        reserves_outlines.geojson
        reserve_cells.parquet
        reserves_info.json
    Only this folder is needed to run the site, not the rest of
    backend/data/, which is pipeline work.
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

BCBN_DUCKDB_MEMORY_LIMIT
    The most memory DuckDB may use, as a number and a unit: "2GB" (the
    default), "1.5GB", "500MB", "2GiB". KB, MB, GB and TB count in 1000s,
    KiB, MiB, GiB and TiB in 1024s. Without a limit DuckDB would take up to
    80% of the machine's memory. It must be at least MIN_DUCKDB_MEMORY
    (100 MB), since the API cannot load its data with less; anything else,
    such as "2", "-1GB" or "lots", stops the API at start-up.

BCBN_DUCKDB_THREADS
    How many threads DuckDB may use: a whole number, 1 or more (default 2).
    Without it DuckDB would use one thread per CPU core.
"""

import os
import re
from dataclasses import dataclass
from pathlib import Path

# backend/api/settings.py -> backend/data/api
DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "api"

# DuckDB's limits when the environment does not set them (see the top of
# this file). 2 GB leaves most of an 8 GB server for everything else.
DEFAULT_DUCKDB_MEMORY_LIMIT = "2GB"
DEFAULT_DUCKDB_THREADS = 2

# The bytes in one of each memory unit DuckDB accepts, by unit in lower case.
MEMORY_UNITS = {"kb": 1000, "mb": 1000 ** 2, "gb": 1000 ** 3, "tb": 1000 ** 4,
                "kib": 1024, "mib": 1024 ** 2, "gib": 1024 ** 3, "tib": 1024 ** 4}

# The smallest memory limit allowed, in bytes. Below about this the API
# runs out of memory while it loads its data at start-up, with a message
# from DuckDB that does not say which setting to change.
MIN_DUCKDB_MEMORY = 100 * 1000 ** 2

# A memory limit as written: a number (decimals allowed), optional spaces,
# and a unit. A minus sign never matches, so "-1GB" is refused.
MEMORY_LIMIT_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*([a-z]+)", re.IGNORECASE)

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
    """The API's settings. data_dir is the folder with the data files
    (see BCBN_API_DATA_DIR at the top of this file); sensitive_max_res
    must be None until sensitive-species handling exists (see
    refuse_sensitive_setting). reserve_reminder_rule is
    RESERVE_REMINDER_RULE; it is a field only so the tests can try the
    other rule, and is not read from the environment. duckdb_memory_limit
    and duckdb_threads are DuckDB's limits (see duckdb_config)."""

    data_dir: Path = DEFAULT_DATA_DIR
    sensitive_max_res: int | None = None
    reserve_reminder_rule: str = RESERVE_REMINDER_RULE
    duckdb_memory_limit: str = DEFAULT_DUCKDB_MEMORY_LIMIT
    duckdb_threads: int = DEFAULT_DUCKDB_THREADS


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


def parse_duckdb_memory_limit(text):
    """Read the text of BCBN_DUCKDB_MEMORY_LIMIT and return the limit, with
    the spaces taken out ("2 GB" gives "2GB"). Empty or missing gives the
    default. Raises SettingsError if it is not a number and a unit DuckDB
    knows, or is under MIN_DUCKDB_MEMORY. DuckDB is never asked to read a
    value this has not checked: it takes "-1GB" to mean no limit at all."""
    if text is None or text.strip() == "":
        return DEFAULT_DUCKDB_MEMORY_LIMIT
    written = text.strip()
    found = MEMORY_LIMIT_PATTERN.fullmatch(written)
    if not found or found.group(2).lower() not in MEMORY_UNITS:
        raise SettingsError(
            f"BCBN_DUCKDB_MEMORY_LIMIT is {written!r}; it must be a number and a unit, "
            "such as 2GB, 1.5GB, 500MB or 2GiB (units: KB, MB, GB, TB, KiB, MiB, GiB, TiB).")
    number, unit = found.groups()
    if float(number) * MEMORY_UNITS[unit.lower()] < MIN_DUCKDB_MEMORY:
        raise SettingsError(
            f"BCBN_DUCKDB_MEMORY_LIMIT is {written!r}; it must be at least 100MB, "
            "or the API runs out of memory while it loads its data.")
    return f"{number}{unit}"


def parse_duckdb_threads(text):
    """Read the text of BCBN_DUCKDB_THREADS and return the number of
    threads. Empty or missing gives the default. Raises SettingsError
    unless it is a whole number of 1 or more."""
    if text is None or text.strip() == "":
        return DEFAULT_DUCKDB_THREADS
    written = text.strip()
    # Only the digits 0-9: isdigit() would also let through other scripts'
    # digits and superscripts, which int() cannot always read.
    if not re.fullmatch(r"[0-9]+", written) or int(written) < 1:
        raise SettingsError(
            f"BCBN_DUCKDB_THREADS is {written!r}; it must be a whole number, 1 or more, such as 2.")
    return int(written)


def refuse_bad_duckdb_limits(settings):
    """Stop the API if DuckDB's limits are not ones it accepts. This also
    covers Settings made in code, which do not go through the two parse
    functions: they are checked by the same rules."""
    if not isinstance(settings.duckdb_memory_limit, str):
        raise SettingsError(
            f"The DuckDB memory limit is {settings.duckdb_memory_limit!r}; it must be text such as '2GB'.")
    parse_duckdb_memory_limit(settings.duckdb_memory_limit)
    # bool is a kind of int in Python, so True would otherwise pass as 1.
    if (not isinstance(settings.duckdb_threads, int) or isinstance(settings.duckdb_threads, bool)
            or settings.duckdb_threads < 1):
        raise SettingsError(
            f"The DuckDB thread count is {settings.duckdb_threads!r}; it must be a whole number, 1 or more.")


def duckdb_config(settings):
    """Return the config to open every DuckDB connection with, so each one
    keeps to the memory and thread limits in settings."""
    return {"memory_limit": settings.duckdb_memory_limit, "threads": settings.duckdb_threads}


def load_settings(environ=None):
    """Read the settings from the environment (os.environ unless another
    mapping is given, which the tests use) and return a Settings. A bad
    value for any of them raises SettingsError, which stops the start-up."""
    if environ is None:
        environ = os.environ

    data_dir_text = environ.get("BCBN_API_DATA_DIR", "").strip()
    # expanduser lets the variable be written as ~/something.
    data_dir = Path(data_dir_text).expanduser() if data_dir_text else DEFAULT_DATA_DIR

    return Settings(
        data_dir=data_dir,
        sensitive_max_res=parse_sensitive_max_res(environ.get("BCBN_SENSITIVE_MAX_RES")),
        duckdb_memory_limit=parse_duckdb_memory_limit(environ.get("BCBN_DUCKDB_MEMORY_LIMIT")),
        duckdb_threads=parse_duckdb_threads(environ.get("BCBN_DUCKDB_THREADS")),
    )
