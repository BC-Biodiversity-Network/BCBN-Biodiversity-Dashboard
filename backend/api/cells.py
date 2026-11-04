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
species, not by hexagon): about 10 to 30 ms at resolution 7 on a laptop,
and about 30 ms for the busiest resolution 4 hexagon (10,846 species).

Years follow the maps' rules (hexes.py): with no year_from and no year_to,
every record counts, including records with no year; with either one, only
records with a year in the range count, and the answer says how many of the
hexagon's records were left out.

Totals: records counts every record in the hexagon, including the one
record in the data with no specieskey, and species counts distinct
specieskeys, exactly as the overview map's hover box does for the same
hexagon and years.

Filter: q keeps the species with any searchable name (the same names as
the species search: the card name, English names, BCSEE labels, merged
names) holding q, written the search way (see search.py). matching gives
how many species pass, for paging.

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

Plain SQL only, so this runs on the cluster's DuckDB 0.10.3.
"""

import re

from api.hexes import HexesRequestError, year_range
from api.ranks import RankRequestError, path_filter, rank_label, require_rank
from api.search import MIN_QUERY_LENGTH, QueryTooShort, normalise
from api.sensitive import hidden_species
from api.species import NO_CARD, resolve_key, species_details

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


def cell_counts(cursor, column, cell, years, hidden, members=None):
    """Return, for one hexagon, [(specieskey, records)] for the years asked
    for (most records first, then by name), and (records with no year,
    records with a year outside the range) left out by the year filter.
    Hidden species are left out of all of them, and so is every species not
    in members, when given: (SQL selecting species keys, its parameters),
    the species of a rank. column is h3_r4 to h3_r7, checked by the caller."""
    year_filter = "AND h.year BETWEEN ? AND ?" if years else ""
    member_filter = f"AND h.specieskey IN ({members[0]})" if members else ""
    member_params = members[1] if members else []
    hidden_list = sorted(hidden)
    rows = cursor.execute(
        f"""SELECT h.specieskey, sum(h.occurrences)::BIGINT AS records,
                   any_value(c.col_name) AS col_name
            FROM species_hex AS h
            LEFT JOIN species_card AS c USING (specieskey)
            WHERE h.{column} = ? {year_filter}
              AND NOT list_contains(CAST(? AS VARCHAR[]), coalesce(h.specieskey, ''))
              {member_filter}
            GROUP BY 1
            ORDER BY records DESC, col_name, h.specieskey""",
        [cell, *(years or []), hidden_list, *member_params]).fetchall()
    if years is None:
        return [(key, records) for key, records, _ in rows], (0, 0)
    no_year, outside = cursor.execute(
        f"""SELECT coalesce(sum(h.occurrences) FILTER (WHERE h.year IS NULL), 0),
                   coalesce(sum(h.occurrences) FILTER (
                       WHERE h.year IS NOT NULL AND (h.year < ? OR h.year > ?)), 0)
            FROM species_hex AS h
            WHERE h.{column} = ?
              AND NOT list_contains(CAST(? AS VARCHAR[]), coalesce(h.specieskey, ''))
              {member_filter}""",
        [years[0], years[1], cell, hidden_list, *member_params]).fetchone()
    return [(key, records) for key, records, _ in rows], (no_year, outside)


def matching_keys(cursor, q):
    """Return the species keys with a searchable name holding q, written the
    search way. Stops with QueryTooShort if q has fewer than
    MIN_QUERY_LENGTH letters or digits."""
    normalised = normalise(cursor, q)
    if len(normalised.replace(" ", "")) < MIN_QUERY_LENGTH:
        raise QueryTooShort(f"The filter needs at least {MIN_QUERY_LENGTH} letters or digits; got {q!r}.")
    return {key for (key,) in cursor.execute(
        "SELECT DISTINCT specieskey FROM search_names WHERE norm LIKE '%' || ? || '%'",
        [normalised]).fetchall()}


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


def card_gbif_keys(cursor, card_key):
    """Return the card's own key and every key merged into it, in that order."""
    merged = cursor.execute("SELECT merged_keys FROM species_card WHERE specieskey = ?",
                            [card_key]).fetchone()[0]
    return [card_key, *(merged or [])]


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


def cell_species(data, cell, year_from=None, year_to=None, q=None, species=None, offset=0,
                 limit=DEFAULT_PAGE_SIZE, rank=None, rank_given=None):
    """Return the answer for one hexagon (see the top of this file). With a
    rank (and its path in rank_given, as for the rank card), the answer is
    about that rank's species only. Raises HexesRequestError for a bad cell
    or year range, QueryTooShort for a filter too short, UnknownSpecies for
    an unknown species key, RankRequestError for a request that does not
    name a rank or names a species too, and UnknownRank for a rank with no
    species."""
    cell = cell.strip().lower()
    res = cell_resolution(cell)
    years = year_range(year_from, year_to, data.meta["first_year"], data.meta["last_year"])
    hidden = hidden_species(data.settings, res)

    cursor = data.cursor()
    path, members = None, None
    if rank:
        if species:
            raise RankRequestError("give a species or a rank, not both.")
        path = require_rank(data, cursor, rank, rank_given or {})
        condition, params = path_filter(path)
        members = (f"SELECT c.specieskey FROM species_card AS c WHERE {condition}", params)
    counts, (no_year, outside) = cell_counts(cursor, f"h3_r{res}", cell, years, hidden, members)
    listed = [(key, records) for key, records in counts if key is not None]
    if q is not None and q.strip():
        keep = matching_keys(cursor, q)
        listed = [(key, records) for key, records in listed if key in keep]
    page = listed[offset:offset + limit]
    details = species_details(cursor, [key for key, _ in page])
    status_source = data.meta["statuses"]["status_source"]

    return {
        "cell": cell,
        "res": res,
        "year_from": year_from,
        "year_to": year_to,
        # The years actually counted (a missing bound filled in), or null
        # when every record is counted.
        "years_counted": list(years) if years else None,
        "records": sum(records for _, records in counts),
        "species": sum(1 for key, _ in counts if key is not None),
        "records_no_year_left_out": no_year,
        "records_outside_years": outside,
        "q": q,
        "matching": len(listed),
        "offset": offset,
        "limit": limit,
        # Where the English names and statuses come from (BCSEE); each
        # result's scientific name says its own source (name_source).
        "english_name_source": status_source,
        "results": [{"specieskey": key, **details.get(key, NO_CARD), "records": records}
                    for key, records in page],
        "species_asked_for": (asked_species(data, cursor, species, f"h3_r{res}", cell, years, counts, hidden)
                              if species else None),
        # The rank the answer is restricted to (its name and path), or null.
        "rank": rank_label(path) if path else None,
        "sensitive_max_res": data.settings.sensitive_max_res,
    }
