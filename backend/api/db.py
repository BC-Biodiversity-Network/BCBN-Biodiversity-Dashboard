"""
db.py

Opens the API's data: checks that the four files are there and have the
columns the API reads, opens one DuckDB connection over them, and works
out the figures /api/meta returns.

The parquet files are read in place (DuckDB views over read_parquet), not
loaded into memory. A request for one species only reads the few parquet
row groups that hold it, so this stays fast without a database server.

Each request should use its own cursor (connection.cursor()), which is
DuckDB's way of sharing one database between threads.

Only plain SQL is used, so this also runs on the cluster's DuckDB 0.10.3.
"""

import json

import duckdb

from api.settings import RESOLUTIONS

# The columns the API reads from each parquet file, so a file from an older
# build is caught when the API starts rather than halfway through a request.
REQUIRED_COLUMNS = {
    "species_card_gbif.parquet": [
        "specieskey", "scientificname", "col_name", "col_authorship",
        "name_source", "col_release",
        "kingdom", "phylum", "class", "order", "family", "genus",
        "record_count", "records_no_year", "first_year", "last_year",
        "n_datasets", "min_lat", "max_lat", "min_lon", "max_lon",
        "image_identifier", "image_references", "image_creator",
        "image_rightsholder", "image_license_code",
        "top_datasets", "merged_keys", "merged_record_count",
    ],
    "species_hex.parquet": [
        "specieskey", "year", "h3_r4", "h3_r5", "h3_r6", "h3_r7", "occurrences",
    ],
    "bcsee_status_by_gbif_species.parquet": [
        "gbif_species_key", "status_display", "statuses", "entries",
        "status_source", "status_source_date", "status_pulled_on",
    ],
}

# The fields of download_info.json the API passes on, for citing the GBIF
# download the data came from.
DOWNLOAD_INFO_FILE = "download_info.json"
REQUIRED_DOWNLOAD_FIELDS = ["download_key", "doi", "doi_url", "date"]

# The name each parquet file is given inside DuckDB.
VIEW_NAMES = {
    "species_card_gbif.parquet": "species_card",
    "species_hex.parquet": "species_hex",
    "bcsee_status_by_gbif_species.parquet": "bcsee_status",
}


class DataFilesError(RuntimeError):
    """The data folder is missing a file, or a file is not what the API
    expects. The message lists every problem found, not just the first."""


def sql_text(text):
    """Write text as a SQL string literal, doubling any single quote, so a
    folder name with a quote in it cannot break the query."""
    return "'" + str(text).replace("'", "''") + "'"


def check_parquet_files(con, data_dir):
    """Return a list of problems with the parquet files in data_dir: files
    that are missing, cannot be read, or lack a column the API reads. An
    empty list means all three are fine."""
    problems = []

    for file_name, columns in REQUIRED_COLUMNS.items():
        path = data_dir / file_name
        if not path.is_file():
            problems.append(f"{file_name} is missing from {data_dir}")
            continue

        try:
            described = con.execute(
                f"DESCRIBE SELECT * FROM read_parquet({sql_text(path)})").fetchall()
        except duckdb.Error as error:
            problems.append(f"{file_name} could not be read: {error}")
            continue

        # DESCRIBE gives one row per column; the first field is its name.
        present = {row[0] for row in described}
        missing = [column for column in columns if column not in present]
        if missing:
            problems.append(f"{file_name} has no column(s) {', '.join(missing)}")

    return problems


def read_download_info(data_dir):
    """Read download_info.json and return (fields, problems): fields is a
    dict of the fields the API passes on, and problems is a list of what
    is wrong (empty when the file is fine)."""
    path = data_dir / DOWNLOAD_INFO_FILE
    if not path.is_file():
        return None, [f"{DOWNLOAD_INFO_FILE} is missing from {data_dir}"]

    try:
        with open(path, encoding="utf-8") as handle:
            info = json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        return None, [f"{DOWNLOAD_INFO_FILE} could not be read: {error}"]

    missing = [field for field in REQUIRED_DOWNLOAD_FIELDS if not info.get(field)]
    if missing:
        return None, [f"{DOWNLOAD_INFO_FILE} has no value for {', '.join(missing)}"]

    return {field: info[field] for field in REQUIRED_DOWNLOAD_FIELDS}, []


def check_single_values(con):
    """Return a list of problems with values that must be the same on every
    row: the Catalogue of Life release on the cards, and the BCSEE source
    and dates on the statuses. Two different values would mean files from
    different builds were mixed, and the page would then show a source
    label that is wrong for some species."""
    problems = []

    releases = con.execute(
        "SELECT DISTINCT col_release FROM species_card ORDER BY 1").fetchall()
    if len(releases) != 1 or releases[0][0] is None:
        problems.append(
            "species_card_gbif.parquet should have exactly one col_release, "
            f"found {[row[0] for row in releases]}")

    sources = con.execute(
        """SELECT DISTINCT status_source, status_source_date, status_pulled_on
           FROM bcsee_status ORDER BY 1, 2, 3""").fetchall()
    if len(sources) != 1 or None in sources[0]:
        problems.append(
            "bcsee_status_by_gbif_species.parquet should have exactly one "
            "status_source, status_source_date and status_pulled_on, "
            f"found {sources}")

    return problems


def create_views(con, data_dir):
    """Give each parquet file a short name inside DuckDB (species_card,
    species_hex, bcsee_status), so queries never repeat the file paths."""
    for file_name, view_name in VIEW_NAMES.items():
        con.execute(
            f"CREATE VIEW {view_name} AS "
            f"SELECT * FROM read_parquet({sql_text(data_dir / file_name)})")


def build_meta(con, download_info, sensitive_max_res):
    """Work out the figures /api/meta returns. They only change when the
    data files change, so this runs once when the API starts.

    Every species count is distinct specieskey after the merge, which is
    one card row per species."""
    species_count = con.execute("SELECT count(*) FROM species_card").fetchone()[0]

    record_count, records_no_year, first_year, last_year = con.execute(
        """SELECT coalesce(sum(occurrences), 0),
                  coalesce(sum(occurrences) FILTER (WHERE year IS NULL), 0),
                  min(year), max(year)
           FROM species_hex""").fetchone()

    col_release = con.execute(
        "SELECT DISTINCT col_release FROM species_card").fetchone()[0]

    # Normally every card says "Catalogue of Life, via GBIF". Listing each
    # value with its count shows at a glance if any key was not found there.
    name_sources = [
        {"name_source": name_source, "species_count": count}
        for name_source, count in con.execute(
            """SELECT name_source, count(*) FROM species_card
               GROUP BY name_source ORDER BY count(*) DESC, name_source""").fetchall()
    ]

    status_source, status_source_date, status_pulled_on = con.execute(
        """SELECT DISTINCT status_source, status_source_date, status_pulled_on
           FROM bcsee_status""").fetchone()

    # Only statuses that reach a species card count here; BCSEE species
    # with no records in BC have no card and are not shown.
    species_with_status = con.execute(
        """SELECT count(*) FROM species_card AS c
           JOIN bcsee_status AS b ON b.gbif_species_key = c.specieskey""").fetchone()[0]

    return {
        "species_count": species_count,
        "record_count": record_count,
        "records_no_year": records_no_year,
        "first_year": first_year,
        "last_year": last_year,
        "resolutions": list(RESOLUTIONS),
        "sensitive_max_res": sensitive_max_res,
        "names": {
            "col_release": col_release,
            "name_sources": name_sources,
        },
        "statuses": {
            "status_source": status_source,
            "status_source_date": status_source_date,
            "status_pulled_on": status_pulled_on,
            "species_with_status": species_with_status,
        },
        "gbif_download": download_info,
    }


class ApiData:
    """The API's open data: the DuckDB connection and the /api/meta
    figures. Made by open_data() when the API starts."""

    def __init__(self, connection, meta):
        """Keep the connection and the meta figures."""
        self.connection = connection
        self.meta = meta

    def cursor(self):
        """Return a new cursor for one request. Each request gets its own,
        so requests running at the same time do not get in each other's
        way."""
        return self.connection.cursor()

    def close(self):
        """Close the connection when the API stops."""
        self.connection.close()


def open_data(settings):
    """Check the data folder, open it and return an ApiData.

    Every check runs before anything is returned, and if any fails this
    raises DataFilesError listing all the problems, so the API refuses to
    start rather than serve wrong or partial answers."""
    data_dir = settings.data_dir
    if not data_dir.is_dir():
        raise DataFilesError(
            f"The data folder {data_dir} does not exist. Copy the files into "
            "it or set BCBN_API_DATA_DIR to the folder that has them.")

    con = duckdb.connect()
    try:
        problems = check_parquet_files(con, data_dir)
        download_info, download_problems = read_download_info(data_dir)
        problems += download_problems

        # The value checks need readable files with the right columns.
        if not problems:
            create_views(con, data_dir)
            problems += check_single_values(con)

        if problems:
            raise DataFilesError(
                f"The API cannot start; problems with the data in {data_dir}:\n  - "
                + "\n  - ".join(problems))

        meta = build_meta(con, download_info, settings.sensitive_max_res)
    except BaseException:
        con.close()
        raise

    return ApiData(con, meta)
