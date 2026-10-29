"""
db.py

Opens the API's data: checks that the five files are there and have the
columns the API reads, that every species key has one answer and that
every Lunaris dataset id has one answer, opens one DuckDB connection over
them, and works out the figures /api/meta returns and the lookup from
merged (old) keys to kept keys.

The three small files (the species cards, the BCSEE statuses and the
Lunaris datasets) are copied into memory when the API starts. species_hex is read in place (a DuckDB
view over read_parquet): it is sorted by species key, so a request for one
species only reads the few parquet row groups that hold it. Either way it
stays fast without a database server.

Each request should use its own cursor (connection.cursor()), which is
DuckDB's way of sharing one database between threads.

Only plain SQL is used, so this also runs on the cluster's DuckDB 0.10.3.
"""

import json

import duckdb

from api.settings import RESOLUTIONS, refuse_sensitive_setting

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
        "top_datasets", "merged_keys", "merged_record_count", "merged_names",
    ],
    "species_hex.parquet": [
        "specieskey", "year", "h3_r4", "h3_r5", "h3_r6", "h3_r7", "occurrences",
    ],
    "bcsee_status_by_gbif_species.parquet": [
        "gbif_species_key", "status_display", "statuses", "entries",
        "status_source", "status_source_date", "status_pulled_on",
    ],
    # Every column: the dataset endpoint returns them all.
    "lunaris_datasets.parquet": [
        "id", "title", "abstract", "subjects", "publisher", "places", "issued",
        "source", "source_name", "link", "link_type", "licences", "final_class", "frontend_group",
        "location_type", "location_source", "boxes", "points", "looked_up_places",
        "box_swapped", "box_zero_width", "box_most_of_world", "box_matches_bc_rectangle",
        "box_matches_canada_extent", "box_shared_by", "mappable",
        "organisms_4b", "organisms_35b", "organism_level_4b", "organism_level_35b",
        "organisms", "organism_level", "copy_count", "other_copies",
    ],
}

# The fields of download_info.json the API passes on, for citing the GBIF
# download the data came from.
DOWNLOAD_INFO_FILE = "download_info.json"
REQUIRED_DOWNLOAD_FIELDS = ["download_key", "doi", "doi_url", "date"]

# The BCSEE statuses the site shows, the same list as SHOWN_STATUSES in
# build_bcsee_status_display.py. Other BCSEE values (Not Reviewed, Unknown,
# Accidental, No Status, Extinct, Other) are not shown as a status.
SHOWN_STATUSES = ["Red", "Blue", "Yellow", "Exotic"]

# The front end groups and classes a Lunaris dataset can have. The export
# only writes these (the "dropped" group and the elsewhere and unknown
# classes are left out), and the dataset filters take only these.
DATASET_FRONTEND_GROUPS = ["bc_data", "pending"]
DATASET_FINAL_CLASSES = ["bc_only", "canada_incl_bc", "bc_and_outside_canada", "all_canada", "global"]

# The name each parquet file is given inside DuckDB.
VIEW_NAMES = {
    "species_card_gbif.parquet": "species_card",
    "species_hex.parquet": "species_hex",
    "bcsee_status_by_gbif_species.parquet": "bcsee_status",
    "lunaris_datasets.parquet": "lunaris_datasets",
}

# The files copied into memory when the API starts, rather than read from
# disk on every request. The card file is sorted by species name, not key,
# so finding one key in it on disk means reading the whole file (about
# 30 ms); in memory it takes under 2 ms. Both files are small (about 4 MB
# and 0.5 MB). species_hex stays on disk: it is ten times larger and sorted
# by key, so DuckDB only reads the part that holds the species asked for.
# The Lunaris datasets (about 3 MB) are searched as a whole on every
# request, so they are kept in memory too.
LOADED_INTO_MEMORY = {"species_card_gbif.parquet", "bcsee_status_by_gbif_species.parquet",
                      "lunaris_datasets.parquet"}


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
    empty list means all of them are fine."""
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


def check_keys(con):
    """Return a list of problems with the species keys: every card key
    must appear once, and every old key in merged_keys must belong to
    exactly one card and must not be a card key itself. Otherwise a
    request for that key would have more than one answer."""
    problems = []

    rows, keys = con.execute(
        "SELECT count(*), count(DISTINCT specieskey) FROM species_card").fetchone()
    if rows != keys:
        problems.append(
            f"species_card_gbif.parquet has {rows - keys} repeated specieskey(s)")

    repeated = con.execute(
        """SELECT old_key FROM (
               SELECT unnest(merged_keys) AS old_key FROM species_card)
           GROUP BY old_key HAVING count(*) > 1
           ORDER BY old_key""").fetchall()
    if repeated:
        problems.append(
            "these merged keys belong to more than one card: "
            + ", ".join(row[0] for row in repeated))

    also_cards = con.execute(
        """SELECT DISTINCT m.old_key FROM (
               SELECT unnest(merged_keys) AS old_key FROM species_card) AS m
           JOIN species_card AS c ON c.specieskey = m.old_key
           ORDER BY m.old_key""").fetchall()
    if also_cards:
        problems.append(
            "these merged keys still have a card of their own: "
            + ", ".join(row[0] for row in also_cards))

    return problems


def check_datasets(con):
    """Return a list of problems with the Lunaris datasets: every id must
    appear once, and the id of another copy must belong to one dataset and
    must not be a shown id itself, or a request for it would have more than
    one answer. Every dataset must also have a front end group and class
    the dataset endpoints know (DATASET_FRONTEND_GROUPS and
    DATASET_FINAL_CLASSES)."""
    problems = []
    rows, ids = con.execute(
        "SELECT count(*), count(DISTINCT id) FROM lunaris_datasets").fetchone()
    if rows != ids:
        problems.append(f"lunaris_datasets.parquet has {rows - ids} repeated id(s)")

    copies = """SELECT c.id AS copy_id FROM (
                    SELECT unnest(other_copies) AS c FROM lunaris_datasets)"""
    repeated = con.execute(
        f"SELECT copy_id FROM ({copies}) GROUP BY copy_id HAVING count(*) > 1 ORDER BY 1").fetchall()
    if repeated:
        problems.append("these copy ids belong to more than one dataset: "
                        + ", ".join(row[0] for row in repeated))
    also_shown = con.execute(
        f"""SELECT DISTINCT copy_id FROM ({copies}) AS c
            JOIN lunaris_datasets AS d ON d.id = c.copy_id ORDER BY 1""").fetchall()
    if also_shown:
        problems.append("these copy ids are also shown as datasets: "
                        + ", ".join(row[0] for row in also_shown))

    for column, allowed in (("frontend_group", DATASET_FRONTEND_GROUPS),
                            ("final_class", DATASET_FINAL_CLASSES)):
        found = [row[0] for row in con.execute(
            f"SELECT DISTINCT {column} FROM lunaris_datasets ORDER BY 1").fetchall()]
        unknown = [value for value in found if value not in allowed]
        if unknown:
            problems.append(f"lunaris_datasets.parquet has {column} value(s) the API does not "
                            f"know: {', '.join(str(v) for v in unknown)}")
    return problems


def read_merged_keys(con):
    """Return a dict from each old (merged-away) key to the key it was
    merged into. It has a few hundred entries, so it is kept in memory and
    a request for an old key needs no extra query."""
    return dict(con.execute(
        """SELECT old_key, specieskey FROM (
               SELECT specieskey, unnest(merged_keys) AS old_key
               FROM species_card)""").fetchall())


def bcsee_includes_species_without_status(con):
    """True if the BCSEE file has rows for species whose BCSEE entries have
    no shown status (none of Red, Blue, Yellow, Exotic).

    build_bcsee_status_display.py currently leaves those species out, so a
    species with no row may still be in BCSEE (as Not Reviewed, Unknown and
    so on). Only when the file includes them does "no row" really mean
    "not in BCSEE"."""
    shown = ", ".join(sql_text(status) for status in SHOWN_STATUSES)
    count = con.execute(
        f"""SELECT count(*) FROM bcsee_status
            WHERE NOT list_has_any(coalesce(statuses, []::VARCHAR[]), [{shown}])""").fetchone()[0]
    return count > 0


def create_views(con, data_dir):
    """Give each parquet file a short name inside DuckDB (species_card,
    species_hex, bcsee_status), so queries never repeat the file paths.
    The files in LOADED_INTO_MEMORY become in-memory tables; the others
    stay views that read the file on disk."""
    for file_name, view_name in VIEW_NAMES.items():
        kind = "TABLE" if file_name in LOADED_INTO_MEMORY else "VIEW"
        con.execute(
            f"CREATE {kind} {view_name} AS "
            f"SELECT * FROM read_parquet({sql_text(data_dir / file_name)})")


def build_meta(con, download_info, sensitive_max_res, includes_without_status):
    """Work out the figures /api/meta returns. They only change when the
    data files change, so this runs once when the API starts.
    includes_without_status is bcsee_includes_species_without_status().

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

    # Only species with a card count here; BCSEE species with no records in
    # BC have no card and are not shown. species_in_bcsee counts every card
    # species with a BCSEE row; species_with_status only those with a shown
    # status (Red, Blue, Yellow or Exotic), not Not Reviewed, Unknown, ...
    shown = ", ".join(sql_text(status) for status in SHOWN_STATUSES)
    species_in_bcsee, species_with_status = con.execute(
        f"""SELECT count(*),
                   count(*) FILTER (WHERE list_has_any(
                       coalesce(b.statuses, []::VARCHAR[]), [{shown}]))
            FROM species_card AS c
            JOIN bcsee_status AS b ON b.gbif_species_key = c.specieskey""").fetchone()

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
            "species_in_bcsee": species_in_bcsee,
            "species_with_status": species_with_status,
            "includes_species_without_status": includes_without_status,
        },
        "gbif_download": download_info,
    }


class ApiData:
    """The API's open data, made by open_data() when the API starts:
    connection    the DuckDB connection
    settings      the Settings it was opened with
    meta          the /api/meta figures
    merged_keys   a dict from each old (merged-away) key to its kept key
    bcsee_includes_without_status
                  whether a species with no BCSEE row is really not in
                  BCSEE (see bcsee_includes_species_without_status)
    search_keys   every card key and merged key in lower case, to the card
                  key and merged_from; filled in by search.prepare_search()
    dataset_copies
                  the id of every other copy of a Lunaris dataset, to the
                  id shown; filled in by datasets.prepare_datasets()
    dataset_catalogues
                  each catalogue (source id) of the datasets, to its
                  readable name; filled in by datasets.prepare_datasets()"""

    def __init__(self, connection, settings, meta, merged_keys,
                 bcsee_includes_without_status):
        """Keep everything the endpoints need."""
        self.connection = connection
        self.settings = settings
        self.meta = meta
        self.merged_keys = merged_keys
        self.bcsee_includes_without_status = bcsee_includes_without_status
        self.search_keys = {}
        self.dataset_copies = {}
        self.dataset_catalogues = {}

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
    start rather than serve wrong or partial answers. A value for the
    sensitive-species setting stops it first, with SettingsError."""
    refuse_sensitive_setting(settings)

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
            problems += check_keys(con)
            problems += check_datasets(con)

        if problems:
            raise DataFilesError(
                f"The API cannot start; problems with the data in {data_dir}:\n  - "
                + "\n  - ".join(problems))

        includes_without_status = bcsee_includes_species_without_status(con)
        meta = build_meta(con, download_info, settings.sensitive_max_res,
                          includes_without_status)
        merged_keys = read_merged_keys(con)
    except BaseException:
        con.close()
        raise

    return ApiData(con, settings, meta, merged_keys, includes_without_status)
