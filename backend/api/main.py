"""
main.py

The species search API: a small FastAPI app that reads the precomputed
species tables and the Lunaris datasets with DuckDB.

Run it from backend/ (the Vite dev server forwards /api to port 8000):

    uvicorn api.main:app --reload --port 8000

When it starts it checks the data folder (see settings.py and db.py) and
refuses to start if anything is missing or wrong, printing every problem.

Endpoints so far:
    GET /api/meta   where the data comes from and what it holds: the
                    Catalogue of Life release, the BCSEE source and dates,
                    the GBIF download to cite, species and record totals,
                    the year range, the map resolutions and the
                    sensitive-species setting.
    GET /api/reserves
                    the map's reserve layer: every First Nations reserve's
                    outline with its alcode and name only, and where the
                    outlines come from (source, licence, the note that they
                    are not to be used for defining boundaries, dates; see
                    reserves.py). It takes no parameters. Without the
                    reserve files it answers available false, with no
                    outlines. /api/hexes/{cell} and /api/area, and their
                    downloads, also name the reserves their hexagons touch
                    (reserve_names), under the rule in settings.py.
    GET /api/species/{key}
                    one species card (see species.py), with how many
                    BC data datasets mention the species. An old key that
                    was merged gives the kept key's card with
                    merged_from set; an unknown key gives 404.
    GET /api/search?q=&limit=&rank_limit=
                    species whose names (or key) match q, best first (see
                    search.py), and ranks (kingdom to genus) whose name
                    matches q, each labelled with its parent so homonyms
                    can be told apart (see rank_search.py). A query with
                    fewer than 2 letters or digits gives 400.
    GET /api/species/{key}/hexes?res=&year_from=&year_to=
                    the species map: records per H3 hexagon at res (4 to
                    7), optionally for a year range (see hexes.py). A bad
                    res or year range gives 400, an unknown key 404.
    GET /api/overview/hexes?res=&year_from=&year_to=
                    the overview map: records and distinct species per H3
                    hexagon at res (4 to 7), optionally for a year range
                    (see overview.py). A bad res or year range gives 400.
    GET /api/hexes/{cell}?year_from=&year_to=&q=&status=&sort=&species=&offset=&limit=
                    the species in one H3 hexagon (resolution 4 to 7, read
                    from the id): its records and species for a year
                    range, and one page of its species, optionally
                    filtered by name (q) and BC status (status, repeated:
                    red, blue, yellow, exotic, none), in the order sort
                    asks for (most_records, the default, fewest_records,
                    scientific or english; see species_list.py), with the
                    records of the species asked for (see cells.py).
                    Species the sensitive setting hides are left out (none
                    for now; see sensitive.py). A bad cell, year range,
                    filter, status or sort gives 400, an unknown species
                    404.
    GET /api/ranks/{rank}?kingdom=&phylum=&class=&order=&family=&genus=&year_from=&year_to=&q=&status=&sort=&offset=&limit=
                    one kingdom, phylum, class, order, family or genus,
                    named by its full path (one parameter per rank down to
                    it; a rank not given is missing in the path): its
                    species in BC, records and years, the next rank down,
                    and one page of its species, filtered and ordered as
                    for /api/hexes/{cell} (see ranks.py). With a year
                    range, every number on the card counts only those
                    years, as the maps do. Species the
                    sensitive setting hides are left out (none for now). A
                    request that does not name a rank, or a bad year
                    range, filter, status or sort, gives 400; a path no
                    species has, 404.
    GET /api/area?west=&south=&east=&north=&res=&year_from=&year_to=&q=&status=&sort=&offset=&limit=
                  &rank=&kingdom=...&genus=
                    the area search: every hexagon at res (4 to 7) that
                    overlaps the box, with the hexagon panel's totals and
                    species list for them together, each species with its
                    GBIF search inside the box (see area.py). A bad or
                    too small box (under MIN_AREA_HEXAGONS average
                    hexagons) or one over MAX_AREA_HEXAGONS hexagons, a bad
                    res, filter, status, sort or year range, or species=,
                    gives 400; a rank no species has, 404.
    GET /api/ranks/{rank}/hexes?kingdom=...&genus=&res=&year_from=&year_to=
                    the rank map: the overview map restricted to the rank's
                    species (records and distinct species per hexagon at
                    res 4 to 7, for a year range), in the overview's shape.
                    /api/hexes/{cell} also takes rank= and the same path, to
                    list that rank's species in one hexagon.
    GET /api/datasets?q=&sort=&catalogue=&final_class=&frontend_group=&year_from=
                     &year_to=&organism=&access=&mappable=&species=&page=&page_size=
                    Lunaris datasets matching the keyword q (title,
                    abstract, subjects) and the filters, a page at a time,
                    in the order sort asks for, with the total and the
                    counts for the filter options (see datasets.py).
                    frontend_group defaults to bc_data. species keeps
                    the datasets with a name linked to that species. A
                    query or organism with fewer than 2 letters or
                    digits, an unknown value or species,
                    sort=relevance with no q, or year_from after
                    year_to gives 400.
    GET /api/datasets/{id}
                    one Lunaris dataset with every field, including its
                    location, its linked species and the organisms it
                    names that do not link (see datasets.py). The id of another copy
                    gives the copy shown, with requested_copy set; an
                    unknown id gives 404.

    GET /api/export/datasets?q=&sort=&catalogue=&final_class=&frontend_group=
                            &year_from=&year_to=&organism=&access=&mappable=&species=
    GET /api/export/ranks/{rank}?kingdom=...&genus=&year_from=&year_to=&q=&status=&sort=
    GET /api/export/hexes/{cell}?year_from=&year_to=&q=&status=&sort=&rank=&kingdom=...&genus=
    GET /api/export/area?west=&south=&east=&north=&res=&year_from=&year_to=&q=&status=&sort=&rank=&kingdom=...
                    CSV downloads of the Datasets list, a rank card's species
                    list and a hexagon's species list: every row the list
                    keeps for the same options (no paging), in the same
                    order, with the same 400 and 404 answers (see
                    export.py). The hexagon download refuses species= (400).

Responses larger than 1 KB are gzipped for clients that accept it (the
species map of the robin at res 7 is about 355 KB, about 60 KB gzipped).
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.gzip import GZipMiddleware

from api.area import area_species
from api.cells import DEFAULT_PAGE_SIZE as CELL_PAGE_SIZE
from api.cells import MAX_PAGE_SIZE as CELL_MAX_PAGE_SIZE
from api.cells import UnknownSpecies, cell_species
from api.datasets import (DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, DatasetsRequestError, get_dataset,
                          list_datasets, prepare_datasets)
from api.db import open_data
from api.export import csv_file, export_area, export_datasets, export_date, export_hexagon, export_rank
from api.hexes import HexesRequestError, species_hexes
from api.ranks import DEFAULT_PAGE_SIZE as RANK_PAGE_SIZE
from api.ranks import MAX_PAGE_SIZE as RANK_MAX_PAGE_SIZE
from api.ranks import RANKS, RankRequestError, UnknownRank, prepare_rank_cards, rank_card, rank_hexes
from api.overview import overview_hexes
from api.rank_search import DEFAULT_RANK_LIMIT, MAX_RANK_LIMIT
from api.search import DEFAULT_LIMIT, MAX_LIMIT, QueryTooShort, prepare_search, search
from api.species_list import ListRequestError
from api.settings import load_settings
from api.species import build_species_card


def unknown_key(key):
    """The 404 error for a key that is neither a species card nor a key
    merged into one. Species BCSEE lists but with no records in BC have no
    card, so they get it too."""
    return HTTPException(
        status_code=404,
        detail=f"No species with key {key!r} in the BC data: it is not a "
               "species key with records in BC, nor a key merged into one.")


def unknown_rank(rank, given):
    """The 404 error for a rank path no species in the BC data has (or whose
    species are all hidden), spelling out the path asked for."""
    return HTTPException(
        status_code=404,
        detail=f"No species in the BC data has this classification down to {rank}: "
               + " > ".join(f"{name}={given.get(name) or '(missing)'}"
                            for name in RANKS[:RANKS.index(rank) + 1]))


def answer_datasets(work):
    """Return work(), a datasets list or its CSV download, with a bad
    request turned into the API's 400 answer, so both answer alike."""
    try:
        return work()
    except (QueryTooShort, DatasetsRequestError) as error:
        raise HTTPException(status_code=400, detail=str(error))


def answer_rank(rank, given, work):
    """Return work(), a rank card or its CSV download, with a bad request
    turned into the API's 400 answer and a rank no species has into its
    404, so both answer alike."""
    try:
        return work()
    except (RankRequestError, QueryTooShort, ListRequestError, HexesRequestError) as error:
        raise HTTPException(status_code=400, detail=str(error))
    except UnknownRank:
        raise unknown_rank(rank, given)


def answer_hexagon(rank, given, species, work):
    """Return work(), a hexagon's species or its CSV download, with a bad
    request turned into the API's 400 answer and an unknown species or
    rank into its 404, so both answer alike."""
    try:
        return work()
    except (HexesRequestError, QueryTooShort, RankRequestError, ListRequestError) as error:
        raise HTTPException(status_code=400, detail=str(error))
    except UnknownSpecies:
        raise unknown_key(species)
    except UnknownRank:
        raise unknown_rank(rank, given)


def refuse_species(request, what):
    """Stop with 400 when the request names a species: what (an area search,
    a hexagon download) is a list of species, not about one species."""
    if "species" in request.query_params:
        raise HTTPException(status_code=400, detail=(
            f"{what} is a list of species; it does not take species=."))


def csv_download(file_and_text):
    """Return a CSV download from (file name, CSV text): UTF-8 starting with
    a byte order mark (csv_file in export.py), saved by the browser under
    that name. The name only holds letters, digits, "-", "_" and ".", so it
    needs no quoting rules of its own."""
    name, text = file_and_text
    return Response(content=csv_file(text), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


def create_app(settings=None):
    """Build the API. settings defaults to the environment variables (see
    settings.py); the tests pass their own to point at small test files."""

    @asynccontextmanager
    async def lifespan(app):
        """Open the data when the API starts and close it when it stops.
        The settings are read here, not when this file is imported, so a
        bad setting or a missing file stops the start-up with a clear
        message."""
        app.state.data = open_data(settings or load_settings())
        try:
            prepare_search(app.state.data)
            prepare_rank_cards(app.state.data)
            prepare_datasets(app.state.data)
            yield
        finally:
            app.state.data.close()

    app = FastAPI(title="BCBN species API", lifespan=lifespan)
    # Gzip any response over 1 KB when the browser accepts it. Level 5
    # rather than Starlette's default 9: for the robin's map at res 7 it
    # gives 65 KB instead of 59 KB, in 6 ms instead of about 150 ms.
    app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=5)

    @app.get("/api/meta")
    def meta(request: Request):
        """Return where the data comes from and what it holds. The figures
        are worked out once at start-up, since they only change with the
        data files."""
        return request.app.state.data.meta

    @app.get("/api/reserves")
    def reserves(request: Request):
        """Return the reserve layer for the map: the outlines (alcode and
        name only) and their source, made once at start-up (reserves.py).
        No parameters: nothing here is chosen by reserve. About 1 MB, so
        the gzip set-up above sends it compressed (about 0.3 MB)."""
        return Response(content=request.app.state.data.reserves.layer, media_type="application/json")

    @app.get("/api/species/{key}")
    def species(key: str, request: Request):
        """Return the species card for key, following a merged key to the
        key it was merged into. A species with no records in BC has no
        card, even if BCSEE lists it, so it also gives 404."""
        card = build_species_card(request.app.state.data, key)
        if card is None:
            raise unknown_key(key)
        return card

    @app.get("/api/species/{key}/hexes")
    def hexes(key: str, request: Request, res: int = Query(...),
              year_from: int | None = None, year_to: int | None = None):
        """Return the species map for key at resolution res, for the years
        year_from to year_to if either is given (see hexes.py). A merged
        key gives the kept key's map with merged_from set."""
        try:
            result = species_hexes(request.app.state.data, key, res, year_from, year_to)
        except HexesRequestError as error:
            raise HTTPException(status_code=400, detail=str(error))
        if result is None:
            raise unknown_key(key)
        return result

    @app.get("/api/overview/hexes")
    def overview(request: Request, res: int = Query(...),
                 year_from: int | None = None, year_to: int | None = None):
        """Return the overview map at resolution res: records and distinct
        species per hexagon, for the years year_from to year_to if either is
        given (see overview.py)."""
        try:
            return overview_hexes(request.app.state.data, res, year_from, year_to)
        except HexesRequestError as error:
            raise HTTPException(status_code=400, detail=str(error))

    @app.get("/api/hexes/{cell}")
    def hexagon(cell: str, request: Request, year_from: int | None = None, year_to: int | None = None,
                q: str | None = Query(None, max_length=200), species: str | None = Query(None, max_length=50),
                rank: str | None = Query(None, max_length=20),
                status: list[str] = Query([], max_length=20), sort: str | None = Query(None, max_length=20),
                offset: int = Query(0, ge=0),
                limit: int = Query(CELL_PAGE_SIZE, ge=1, le=CELL_MAX_PAGE_SIZE)):
        """Return the species in one hexagon, a page at a time, filtered by
        name (q) and BC status (status) and in the order sort asks for, for
        the years year_from to year_to if either is given (see cells.py). species is a species key whose records in the hexagon
        are given on their own; rank, with the rank's path (one parameter
        per rank, as for /api/ranks/{rank}), keeps that rank's species
        only."""
        given = {name: request.query_params.get(name) for name in RANKS}
        return answer_hexagon(rank, given, species, lambda: cell_species(
            request.app.state.data, cell, year_from, year_to, q, species, offset, limit, rank, given,
            status, sort))

    @app.get("/api/area")
    def area(request: Request, west: str | None = Query(None, max_length=40),
             south: str | None = Query(None, max_length=40), east: str | None = Query(None, max_length=40),
             north: str | None = Query(None, max_length=40), res: str | None = Query(None, max_length=10),
             year_from: int | None = None, year_to: int | None = None,
             q: str | None = Query(None, max_length=200), rank: str | None = Query(None, max_length=20),
             status: list[str] = Query([], max_length=20), sort: str | None = Query(None, max_length=20),
             offset: int = Query(0, ge=0),
             limit: int = Query(CELL_PAGE_SIZE, ge=1, le=CELL_MAX_PAGE_SIZE)):
        """Return the species in every hexagon under a box, a page at a
        time, with the hexagon panel's filters, order and rank (see
        area.py). The edges and res are read as text, so a missing or
        non-numeric one gets area.py's own clear message (400)."""
        refuse_species(request, "The area search")
        given = {name: request.query_params.get(name) for name in RANKS}
        return answer_hexagon(rank, given, None, lambda: area_species(
            request.app.state.data, west, south, east, north, res, year_from, year_to, q, offset, limit, rank,
            given, status, sort))

    @app.get("/api/ranks/{rank}/hexes")
    def rank_map(rank: str, request: Request, res: int = Query(...),
                 year_from: int | None = None, year_to: int | None = None):
        """Return the rank map at resolution res: the records and distinct
        species of the rank's species per hexagon, for the years year_from
        to year_to if either is given (see ranks.py). The rank's path is
        read from the query string, as for the rank card."""
        given = {name: request.query_params.get(name) for name in RANKS}
        try:
            return rank_hexes(request.app.state.data, rank, given, res, year_from, year_to)
        except (HexesRequestError, RankRequestError) as error:
            raise HTTPException(status_code=400, detail=str(error))
        except UnknownRank:
            raise unknown_rank(rank, given)

    @app.get("/api/ranks/{rank}")
    def rank(rank: str, request: Request, year_from: int | None = None, year_to: int | None = None,
             q: str | None = Query(None, max_length=200),
             status: list[str] = Query([], max_length=20), sort: str | None = Query(None, max_length=20),
             offset: int = Query(0, ge=0),
             limit: int = Query(RANK_PAGE_SIZE, ge=1, le=RANK_MAX_PAGE_SIZE)):
        """Return the card for one rank, named by its full path: one query
        parameter per rank from kingdom down to it (see ranks.py). The rank
        names are read from the query string directly, since "class" and
        "order" cannot be Python parameter names. Its species list takes
        the same filters and order as a hexagon's (q, status, sort), and
        year_from and year_to make every number count only those years."""
        given = {name: request.query_params.get(name) for name in RANKS}
        return answer_rank(rank, given, lambda: rank_card(
            request.app.state.data, rank, given, offset, limit, q, status, sort, year_from, year_to))

    @app.get("/api/search")
    def species_search(request: Request, q: str = Query(..., max_length=200),
                       limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
                       rank_limit: int = Query(DEFAULT_RANK_LIMIT, ge=0, le=MAX_RANK_LIMIT)):
        """Return the species matching q, best first: a typed species key,
        then names equal to q, then names with a word starting with q, then
        names holding q anywhere; ties go to the species with more
        records. Also up to rank_limit ranks (kingdom to genus) whose name
        matches q, each labelled with its parent (see rank_search.py)."""
        try:
            return search(request.app.state.data, q, limit, rank_limit)
        except QueryTooShort as error:
            raise HTTPException(status_code=400, detail=str(error))

    @app.get("/api/datasets")
    def datasets(request: Request, q: str | None = Query(None, max_length=200),
                 catalogue: list[str] | None = Query(None),
                 final_class: list[str] | None = Query(None),
                 frontend_group: list[str] | None = Query(None),
                 sort: str | None = Query(None),
                 year_from: int | None = Query(None, ge=1000, le=9999),
                 year_to: int | None = Query(None, ge=1000, le=9999),
                 organism: str | None = Query(None, max_length=200),
                 access: list[str] | None = Query(None),
                 mappable: bool = Query(False),
                 species: str | None = Query(None, max_length=50),
                 page: int = Query(1, ge=1),
                 page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)):
        """Return one page of Lunaris datasets matching q and the filters, in
        the order sort asks for (relevance by default with q, else newest;
        see datasets.py). catalogue, final_class, frontend_group and access
        can each be given more than once; frontend_group defaults to
        bc_data. species is a species key."""
        return answer_datasets(lambda: list_datasets(
            request.app.state.data, q, catalogue, final_class, frontend_group, page, page_size, sort=sort,
            year_from=year_from, year_to=year_to, organism=organism, access=access, mappable=mappable,
            species=species))

    # The CSV downloads (see export.py). They live under /api/export/, so
    # no path of theirs can be taken for a dataset id below.
    @app.get("/api/export/datasets")
    def datasets_export(request: Request, q: str | None = Query(None, max_length=200),
                        catalogue: list[str] | None = Query(None),
                        final_class: list[str] | None = Query(None),
                        frontend_group: list[str] | None = Query(None),
                        sort: str | None = Query(None),
                        year_from: int | None = Query(None, ge=1000, le=9999),
                        year_to: int | None = Query(None, ge=1000, le=9999),
                        organism: str | None = Query(None, max_length=200),
                        access: list[str] | None = Query(None),
                        mappable: bool = Query(False),
                        species: str | None = Query(None, max_length=50)):
        """Return every Lunaris dataset /api/datasets would list for the
        same options, in the same order, as a CSV download (no paging)."""
        return csv_download(answer_datasets(lambda: export_datasets(
            request.app.state.data, export_date(), q, catalogue, final_class, frontend_group, sort,
            year_from, year_to, organism, access, mappable, species)))

    @app.get("/api/export/ranks/{rank}")
    def rank_export(rank: str, request: Request, year_from: int | None = None, year_to: int | None = None,
                    q: str | None = Query(None, max_length=200),
                    status: list[str] = Query([], max_length=20), sort: str | None = Query(None, max_length=20)):
        """Return every species on the list of the rank card
        /api/ranks/{rank} would give for the same path and options, in the
        same order, as a CSV download (no paging)."""
        given = {name: request.query_params.get(name) for name in RANKS}
        return csv_download(answer_rank(rank, given, lambda: export_rank(
            request.app.state.data, export_date(), rank, given, q, status, sort, year_from, year_to)))

    @app.get("/api/export/hexes/{cell}")
    def hexagon_export(cell: str, request: Request, year_from: int | None = None, year_to: int | None = None,
                       q: str | None = Query(None, max_length=200), rank: str | None = Query(None, max_length=20),
                       status: list[str] = Query([], max_length=20),
                       sort: str | None = Query(None, max_length=20)):
        """Return every species /api/hexes/{cell} would list for the same
        options (rank and its path included), in the same order, each with
        its GBIF link, as a CSV download (no paging). species= is refused:
        that panel is about one species, not a list."""
        refuse_species(request, "The hexagon download")
        given = {name: request.query_params.get(name) for name in RANKS}
        return csv_download(answer_hexagon(rank, given, None, lambda: export_hexagon(
            request.app.state.data, export_date(), cell, q, status, sort, year_from, year_to, rank, given)))

    @app.get("/api/export/area")
    def area_export(request: Request, west: str | None = Query(None, max_length=40),
                    south: str | None = Query(None, max_length=40), east: str | None = Query(None, max_length=40),
                    north: str | None = Query(None, max_length=40), res: str | None = Query(None, max_length=10),
                    year_from: int | None = None, year_to: int | None = None,
                    q: str | None = Query(None, max_length=200), rank: str | None = Query(None, max_length=20),
                    status: list[str] = Query([], max_length=20),
                    sort: str | None = Query(None, max_length=20)):
        """Return every species /api/area would list for the same box and
        options, in the same order, each with its GBIF link, as a CSV
        download (no paging). species= is refused, as for /api/area."""
        refuse_species(request, "The area download")
        given = {name: request.query_params.get(name) for name in RANKS}
        return csv_download(answer_hexagon(rank, given, None, lambda: export_area(
            request.app.state.data, export_date(), west, south, east, north, res, q, status, sort,
            year_from, year_to, rank, given)))

    # Dataset ids hold colons and slashes (oai:datadryad.org:doi:10.5061/...),
    # so the id takes the rest of the path.
    @app.get("/api/datasets/{dataset_id:path}")
    def dataset(dataset_id: str, request: Request):
        """Return one Lunaris dataset with every field. The id of another
        copy of a dataset gives the copy shown, with requested_copy set."""
        found = get_dataset(request.app.state.data, dataset_id)
        if found is None:
            raise HTTPException(
                status_code=404,
                detail=f"No Lunaris dataset with id {dataset_id!r} on the site: it is not a "
                       "dataset placed in BC or pending, nor another copy of one.")
        return found

    return app


# The app uvicorn runs (api.main:app).
app = create_app()
