"""
cells.py

The species in one hexagon, for GET /api/hexes/{cell}: the hexagon's
records and species for a range of years, and one page of its species,
most records first, optionally filtered by name. The map's side panel
shows it when a hexagon is clicked.

Data: species_hex.parquet, one row per species, year and hexagon, with the
key merge already applied, so species are counted by specieskey after the
merge, as on the maps. The hexagon's resolution is read from its id: an H3
cell id is 15 hexadecimal digits starting with "8" and then the
resolution, so "872800b30ffffff" is at resolution 7. No H3 package is
needed. Every request reads the whole of species_hex (it is sorted by
species, not by hexagon) and the names and statuses of every species in the
hexagon, to filter, sort and count the list: on a laptop about 15 ms for a
quiet resolution 7 hexagon, 56 ms for the busiest (3,282 species), and
about 120 ms for the busiest resolution 4 hexagon (10,846 species).

Years follow the maps' rules (hexes.py): with no year_from and no year_to,
every record counts, including records with no year; with either one, only
records with a year in the range count, and the answer says how many of the
hexagon's records were left out.

Totals: records counts every record in the hexagon, including the one
record in the data with no specieskey, and species counts distinct
specieskeys, exactly as the overview map's hover box does for the same
hexagon and years.

The list: filtered by name (q, the same names as the species search) and
by BC status (status), in the order asked for (sort), one page at a time,
with matching (how many species pass the filters, for paging) and
status_counts, as on the rank card (see species_list.py). The totals are
always the whole hexagon's.

Species asked for (species=): for the species map's panel, which is about
that species alone: its records in this hexagon for the years (0 if none),
its records per year (years_counted's range, or every year with no range),
the first and last year among them, its records with no year in this
hexagon (counted in records only with no year range, as on the maps), and
gbif_keys, its own key and every key merged into its card, so a GBIF search
for them covers the same records. An old merged key gives the kept key,
with merged_from.

Rank (rank= with the rank's path, one parameter per rank, as for the rank
card in ranks.py): for a rank map's panel, the answer is about that rank's
species only: the list, the totals and the no-date counts. A rank whose
card would not be found (no species, or all hidden) gives UnknownRank. A
species and a rank cannot both be asked for.

Sensitive species: the species sensitive.hidden_species gives for this
resolution are left out of the list, the totals and the species asked for
(its names stay, everything else about it is null: records, per year, first
and last year, no-year records and gbif_keys; none for now; see
sensitive.py), so that the rule, once decided, covers
these lists too.

The same counting serves a set of hexagons: the area search (area.py)
asks hexagons_list for every hexagon under a box, so its totals, list,
filters and hidden species work exactly as for one hexagon. A species in
several of the hexagons is still one species, and each record is in only
one hexagon at a resolution, so it is counted once.

Reserves: reserve_names lists the First Nations reserves the hexagon
touches, sorted, for the reserve reminder (see reserves.py). It is looked
up from the hexagon id alone and never joined to the records; under rule B
in settings.py it is empty at the larger sizes.

Plain SQL only, so this runs on the cluster's DuckDB 0.10.3.
"""

import re

from api.hexes import HexesRequestError, year_range
from api.ranks import RankRequestError, path_filter, rank_label, require_rank
from api.reserves import reserve_names
from api.search import matching_keys
from api.sensitive import hidden_species
from api.species import resolve_key, species_details
from api.species_list import check_list_options, list_items, species_page

# An H3 cell id at resolution 4 to 7: "8", the resolution, 13 more digits.
CELL_ID = re.compile(r"^8([4-7])[0-9a-f]{13}$")

# How many species one page holds by default, and at most.
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


class UnknownSpecies(LookupError):
    """The species asked for is neither a card key nor a key merged into one."""


def cell_resolution(cell):
    """Return the resolution of an H3 cell id (4 to 7), or stop with a clear
    message if it is not one."""
    found = CELL_ID.match(cell)
    if not found:
        raise HexesRequestError(
            f"cell must be an H3 hexagon id at resolution 4 to 7 (15 hexadecimal digits "
            f"starting with 84 to 87, such as 872800b30ffffff); got {cell!r}.")
    return int(found.group(1))


def cell_counts(cursor, column, cells, years, hidden, members=None):
    """Return, for a list of hexagons together (one hexagon, or every
    hexagon under an area's box), [(specieskey, records, name, name source,
    BCSEE entries, BCSEE statuses)] for the years asked for (most records
    first, then by name), each species once with its records in all of
    them, and (records with no year, records with a year outside the range)
    left out by the year filter.
    Hidden species are left out of all of them, and so is every species not
    in members, when given: (SQL selecting species keys, its parameters),
    the species of a rank. column is h3_r4 to h3_r7, checked by the caller."""
    year_filter = "AND h.year BETWEEN ? AND ?" if years else ""
    member_filter = f"AND h.specieskey IN ({members[0]})" if members else ""
    member_params = members[1] if members else []
    hidden_list = sorted(hidden)
    rows = cursor.execute(
        f"""SELECT h.specieskey, sum(h.occurrences)::BIGINT AS records,
                   any_value(c.col_name) AS col_name, any_value(c.name_source),
                   any_value(b.entries), any_value(b.statuses)
            FROM species_hex AS h
            LEFT JOIN species_card AS c USING (specieskey)
            LEFT JOIN bcsee_status AS b ON b.gbif_species_key = h.specieskey
            WHERE h.{column} IN (SELECT unnest(CAST(? AS VARCHAR[]))) {year_filter}
              AND NOT list_contains(CAST(? AS VARCHAR[]), coalesce(h.specieskey, ''))
              {member_filter}
            GROUP BY 1
            ORDER BY records DESC, col_name, h.specieskey""",
        [list(cells), *(years or []), hidden_list, *member_params]).fetchall()
    if years is None:
        return rows, (0, 0)
    no_year, outside = cursor.execute(
        f"""SELECT coalesce(sum(h.occurrences) FILTER (WHERE h.year IS NULL), 0),
                   coalesce(sum(h.occurrences) FILTER (
                       WHERE h.year IS NOT NULL AND (h.year < ? OR h.year > ?)), 0)
            FROM species_hex AS h
            WHERE h.{column} IN (SELECT unnest(CAST(? AS VARCHAR[])))
              AND NOT list_contains(CAST(? AS VARCHAR[]), coalesce(h.specieskey, ''))
              {member_filter}""",
        [years[0], years[1], list(cells), hidden_list, *member_params]).fetchone()
    return rows, (no_year, outside)


# What the block for the species asked for holds about its records, all
# null when the species is hidden at this resolution.
HIDDEN_RECORDS = {"records": None, "records_per_year": None, "first_year": None, "last_year": None,
                  "records_no_year": None, "gbif_keys": None}


def records_by_year(cursor, column, cell, card_key):
    """Return [(year, records)] for one species in one hexagon, every year
    with records in order, the records with no year as year None (last)."""
    return cursor.execute(
        f"""SELECT year, sum(occurrences)::BIGINT
            FROM species_hex
            WHERE {column} = ? AND specieskey = ?
            GROUP BY year
            ORDER BY year NULLS LAST""", [cell, card_key]).fetchall()


def cards_gbif_keys(cursor, card_keys):
    """Return, for each card key given, the card's own key and every key
    merged into it, in that order, as {card key: [keys]}, read in one query,
    so a CSV download of thousands of species does not ask once per
    species. A key with no card is left out."""
    rows = cursor.execute(
        """SELECT specieskey, merged_keys FROM species_card
           WHERE list_contains(CAST(? AS VARCHAR[]), specieskey)""", [list(card_keys)]).fetchall()
    return {key: [key, *(merged or [])] for key, merged in rows}


def card_gbif_keys(cursor, card_key):
    """Return the card's own key and every key merged into it, in that order."""
    return cards_gbif_keys(cursor, [card_key])[card_key]


def asked_species(data, cursor, key, column, cell, years, counts, hidden):
    """Return the block for the species asked for: its kept key, the key
    asked for, merged_from, its names and its records in this hexagon (see
    the top of this file; HIDDEN_RECORDS if it is hidden here, so a hidden
    species is not revealed). Raises UnknownSpecies for a key with no card."""
    card_key, merged_from = resolve_key(key, data.merged_keys)
    details = species_details(cursor, [card_key])
    if card_key not in details:
        raise UnknownSpecies(key)
    block = {"specieskey": card_key, "requested_key": key, "merged_from": merged_from, **details[card_key]}
    if card_key in hidden:
        return {**block, **HIDDEN_RECORDS}
    by_year = records_by_year(cursor, column, cell, card_key)
    per_year = [{"year": year, "records": records} for year, records in by_year
                if year is not None and (years is None or years[0] <= year <= years[1])]
    return {
        **block,
        "records": dict(counts).get(card_key, 0),
        "records_per_year": per_year,
        "first_year": per_year[0]["year"] if per_year else None,
        "last_year": per_year[-1]["year"] if per_year else None,
        "records_no_year": sum(records for year, records in by_year if year is None),
        "gbif_keys": card_gbif_keys(cursor, card_key),
    }


def hexagons_list(data, cursor, res, cells, years, hidden, q, statuses, sort, offset, limit, path):
    """Return what the answer for one hexagon and for an area share, for
    the hexagons in cells at resolution res: (counts, part), counts being
    [(specieskey, records)] of every species there (for the species asked
    for), and part the totals for the years (records, species, and the
    records a year range left out), the list's options, how many species
    pass its filters, the status counts, one page of species (offset,
    limit; limit None for all of them) and the rank. years is
    (from, to) or None for every record; hidden the species to leave out;
    path the rank's path, or None for every species. statuses and sort are
    as check_list_options returns them."""
    members = None
    if path:
        condition, params = path_filter(path)
        members = (f"SELECT c.specieskey FROM species_card AS c WHERE {condition}", params)
    rows, (no_year, outside) = cell_counts(cursor, f"h3_r{res}", cells, years, hidden, members)
    counts = [(key, records) for key, records, *_ in rows]
    keep = matching_keys(cursor, q) if q is not None and q.strip() else None
    listed = species_page(list_items([row for row in rows if row[0] is not None]),
                          keep, statuses, sort, offset, limit)
    return counts, {
        "records": sum(records for _, records in counts),
        "species": sum(1 for key, _ in counts if key is not None),
        "records_no_year_left_out": no_year,
        "records_outside_years": outside,
        "q": q,
        "status": statuses,
        "sort": sort,
        "matching": listed["matching"],
        "status_counts": listed["status_counts"],
        "offset": offset,
        "limit": limit,
        # Where the English names and statuses come from (BCSEE); each
        # result's scientific name says its own source (name_source).
        "english_name_source": data.meta["statuses"]["status_source"],
        "results": listed["results"],
        # The rank the answer is restricted to (its name and path), or null.
        "rank": rank_label(path) if path else None,
        "sensitive_max_res": data.settings.sensitive_max_res,
    }


def cell_species(data, cell, year_from=None, year_to=None, q=None, species=None, offset=0,
                 limit=DEFAULT_PAGE_SIZE, rank=None, rank_given=None, statuses=None, sort=None):
    """Return the answer for one hexagon (see the top of this file), its
    list filtered by name (q) and BC status (statuses) and in the order
    asked for (sort). With a rank (and its path in rank_given, as for the
    rank card), the answer is about that rank's species only. Raises
    HexesRequestError for a bad cell or year range, ListRequestError for an
    unknown sort or status, QueryTooShort for a filter too short,
    UnknownSpecies for an unknown species key, RankRequestError for a
    request that does not name a rank or names a species too, and
    UnknownRank for a rank with no species. limit None lists every species
    from offset on, for the CSV download (export.py)."""
    sort, statuses = check_list_options(sort, statuses)
    cell = cell.strip().lower()
    res = cell_resolution(cell)
    years = year_range(year_from, year_to, data.meta["first_year"], data.meta["last_year"])
    hidden = hidden_species(data.settings, res)

    cursor = data.cursor()
    path = None
    if rank:
        if species:
            raise RankRequestError("give a species or a rank, not both.")
        path = require_rank(data, cursor, rank, rank_given or {})
    counts, part = hexagons_list(data, cursor, res, [cell], years, hidden, q, statuses, sort, offset, limit,
                                 path)

    return {
        "cell": cell,
        "res": res,
        "year_from": year_from,
        "year_to": year_to,
        # The years actually counted (a missing bound filled in), or null
        # when every record is counted.
        "years_counted": list(years) if years else None,
        # The names of the reserves this hexagon touches, sorted, for the
        # reminder (reserves.py; empty when there are none or the rule
        # gives none at this size).
        "reserve_names": reserve_names(data.reserves, data.settings.reserve_reminder_rule, res, [cell]),
        **part,
        "species_asked_for": (asked_species(data, cursor, species, f"h3_r{res}", cell, years, counts, hidden)
                              if species else None),
    }
