"""
reserves.py

First Nations reserves, for two things only (option A of the proposal,
November 2026):

  1. The map layer, GET /api/reserves: every reserve's outline with its
     alcode and name, and where the outlines come from. It takes no
     parameters.
  2. The reserve reminder: given hexagons a visitor has already chosen (one
     hexagon, or every hexagon under a drawn box), the sorted names of the
     reserves they touch. The hexagon and area answers carry these names
     (reserve_names), and their CSV downloads add a note to header line 2.

Privacy rules (from the proposal):
  - Nothing takes a reserve as input: no endpoint, parameter, filter or
    download is about a reserve. The only way in is a hexagon or a box.
  - reserve_cells is never joined to records, species or datasets. It is
    read once into a Python dict from cell id to alcodes, on a DuckDB
    connection of its own that is closed straight away, so it is never a
    table the API's SQL could reach. Its only use is reserve_names().

Data (written by backend/pipeline/reserves/build_reserves.py into the API's
data folder):
    reserves_outlines.geojson   the outlines, properties alcode and name
    reserve_cells.parquet       res, h3_cell, alcode: every cell at every
                                hexagon size that touches a reserve
    reserves_info.json          the source, licence and boundary note

The three files go together. If any is missing or not what is expected,
the API still starts: the layer answers with available false and no
outlines, every list of reserve names is empty, and one message saying
why is logged at start-up.

Which hexagon sizes get the reminder is RESERVE_REMINDER_RULE in
settings.py (rule A: all of them; rule B: only res 6 and 7).
"""

import json
import logging

import duckdb

from api.settings import RESERVE_REMINDER_RESOLUTIONS

OUTLINES_FILE = "reserves_outlines.geojson"
CELLS_FILE = "reserve_cells.parquet"
INFO_FILE = "reserves_info.json"

# The columns reserve_cells.parquet must have.
CELLS_COLUMNS = ["res", "h3_cell", "alcode"]

# The fields of reserves_info.json the layer passes on, and must be there.
INFO_FIELDS = ["source_title", "source_publisher", "licence", "licence_url", "boundary_note",
               "shapefile", "latest_revdate", "build_date"]

logger = logging.getLogger(__name__)


class ReserveFilesError(ValueError):
    """A reserve file is missing or not what the API expects."""


class ReserveData:
    """The reserves as the API holds them:
    available    True when all three files were read and agree
    names        {alcode: name}
    cells        {(res, h3_cell): (alcode, ...)}
    layer        the bytes of the /api/reserves answer, made once
    problem      why the files were not used, or None"""

    def __init__(self, names=None, cells=None, layer=None, problem=None):
        """Keep what was read; with nothing given, the reserves are off."""
        self.names = names or {}
        self.cells = cells or {}
        self.problem = problem
        self.available = problem is None and layer is not None
        self.layer = layer if self.available else unavailable_layer()


def unavailable_layer():
    """Return the bytes of the /api/reserves answer when the reserve files
    could not be used: no outlines and no source."""
    return json.dumps({"available": False, "source": None, "outlines": None}).encode("utf-8")


def read_outlines(path):
    """Read the outlines file and return (names {alcode: name}, features),
    each feature rebuilt with only alcode and name as properties, so nothing
    else can reach the layer even if the file held more. Raises
    ReserveFilesError if the file is not a FeatureCollection of features
    with an alcode, a name and a geometry, or repeats an alcode."""
    with open(path, encoding="utf-8") as handle:
        collection = json.load(handle)
    if collection.get("type") != "FeatureCollection" or not isinstance(collection.get("features"), list):
        raise ReserveFilesError(f"{OUTLINES_FILE} is not a GeoJSON FeatureCollection")
    names, features = {}, []
    for feature in collection["features"]:
        properties = feature.get("properties") or {}
        alcode, name = properties.get("alcode"), properties.get("name")
        if not isinstance(alcode, str) or not isinstance(name, str) or not feature.get("geometry"):
            raise ReserveFilesError(f"{OUTLINES_FILE} has a feature without an alcode, a name or a shape")
        if alcode in names:
            raise ReserveFilesError(f"{OUTLINES_FILE} repeats alcode {alcode}")
        names[alcode] = name
        features.append({"type": "Feature", "properties": {"alcode": alcode, "name": name},
                         "geometry": feature["geometry"]})
    return names, features


def read_cells(path, names):
    """Read reserve_cells.parquet into {(res, h3_cell): (alcode, ...)} on a
    DuckDB connection of its own, closed before returning, so the file is
    never a table the API's queries could join. Raises ReserveFilesError if
    the columns are wrong or an alcode has no outline (it would have no
    name to show)."""
    con = duckdb.connect()
    try:
        # A quote in the folder name is doubled, so it cannot break the query.
        quoted_path = str(path).replace("'", "''")
        source = f"read_parquet('{quoted_path}')"
        columns = [row[0] for row in con.execute(f"DESCRIBE SELECT * FROM {source}").fetchall()]
        if columns != CELLS_COLUMNS:
            raise ReserveFilesError(f"{CELLS_FILE} has columns {columns}, not {CELLS_COLUMNS}")
        rows = con.execute(f"SELECT res, lower(h3_cell), alcode FROM {source} ORDER BY 1, 2, 3").fetchall()
    finally:
        con.close()
    cells = {}
    for res, cell, alcode in rows:
        if alcode not in names:
            raise ReserveFilesError(f"{CELLS_FILE} names alcode {alcode}, which has no outline")
        cells.setdefault((int(res), cell), []).append(alcode)
    return {key: tuple(alcodes) for key, alcodes in cells.items()}


def read_info(path):
    """Read reserves_info.json and return it. Raises ReserveFilesError if a
    field the site shows (INFO_FIELDS) is missing."""
    with open(path, encoding="utf-8") as handle:
        info = json.load(handle)
    missing = [field for field in INFO_FIELDS if field not in info]
    if missing:
        raise ReserveFilesError(f"{INFO_FILE} has no {', '.join(missing)}")
    return info


def load_reserves(data_dir):
    """Read the three reserve files from data_dir and return a ReserveData.
    If any is missing or wrong, log one message saying why and return a
    ReserveData with the reserves off, so the rest of the API still works."""
    missing = [name for name in (OUTLINES_FILE, CELLS_FILE, INFO_FILE) if not (data_dir / name).is_file()]
    try:
        if missing:
            raise ReserveFilesError(f"{', '.join(missing)} not found in {data_dir}")
        names, features = read_outlines(data_dir / OUTLINES_FILE)
        cells = read_cells(data_dir / CELLS_FILE, names)
        info = read_info(data_dir / INFO_FILE)
    except (OSError, ValueError, duckdb.Error) as error:
        # ReserveFilesError and json's errors are both ValueErrors.
        problem = str(error)
        logger.warning("Reserve outlines and reminders are OFF: %s. The rest of the API works; "
                       "build the files with backend/pipeline/reserves/build_reserves.py.", problem)
        return ReserveData(problem=problem)

    answer = {"available": True, "source": info,
              "outlines": {"type": "FeatureCollection", "features": features}}
    # Made once at start-up: the outlines only change when the files do.
    layer = json.dumps(answer, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return ReserveData(names=names, cells=cells, layer=layer)


def reserve_names(reserves, rule, res, cells):
    """Return the sorted names of the reserves touched by the hexagons in
    cells (at resolution res, which the visitor already chose), each name
    once. The list is empty when the rule gives no reminder at this size
    or the reserves are off. Sorted ignoring case, so "Alkali Lake" and
    "ALKALI LAKE 1" would sit together."""
    if not reserves.available or res not in RESERVE_REMINDER_RESOLUTIONS[rule]:
        return []
    found = {reserves.names[alcode] for cell in cells for alcode in reserves.cells.get((res, cell), ())}
    return sorted(found, key=lambda name: (name.casefold(), name))
