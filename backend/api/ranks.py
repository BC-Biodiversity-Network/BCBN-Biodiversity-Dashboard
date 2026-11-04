"""
ranks.py

The rank card for GET /api/ranks/{rank} (one kingdom, phylum, class,
order, family or genus, with its species in BC), the rank map for
GET /api/ranks/{rank}/hexes (the overview map restricted to the rank's
species), and the helpers cells.py uses to restrict a hexagon's list to a
rank.

A rank is named by its full path, never by its name alone, because names
repeat: Prunella is a genus of birds (Prunellidae) and of plants
(Lamiaceae). The address gives the rank and one parameter per rank down to
it, for example

    /api/ranks/genus?kingdom=Plantae&phylum=Tracheophyta&class=Magnoliopsida
        &order=Lamiales&family=Lamiaceae&genus=Prunella

A rank above it that is not given (or given empty) is a missing rank in the
path: a species GBIF gives no order for is found under its family with no
order parameter, and it is not the same family as one with an order. The
rank's own name must be given; a rank below it must not be. Names are
compared exactly, as stored.

Data: species_card_gbif.parquet, one row per species after the key merge.
The classification (kingdom to genus) is GBIF's, as it came with the
records; the species names are the Catalogue of Life's. A species with a
missing rank is still counted at the ranks it has. A key merged into a card
counts under the card's classification, never its own.

The card holds: the path (each rank with its name, null when missing); the
species, records, records with no year, and first and last year of its
species; the next rank down (its children: each name with its species and
records, null for species missing that rank; none for a genus); and one
page of its species, most records first (offset, limit), with their names,
BCSEE statuses and records.

The rank map gives, for every hexagon at one resolution, the records and
distinct species of the rank's species, for a year range as on the other
maps (see overview.py, whose answer it copies: cells, occurrences and
distinct_species as three lists, with the same totals), so the front end can
draw it as the overview's two views. Each request reads the whole of
species_hex, as the overview does.

Sensitive species: sensitive.hidden_species, asked with no resolution (a
card has no location), gives the species to leave out of the card: out of
the list, the totals and the children. A rank whose species are all hidden
is not found (404), the same as one with no species; whether a rank exists
is always decided this way, so its card, map and hexagon lists are found or
not found together. The map and the hexagon lists then ask with their
resolution and leave those species out of every count.

Plain SQL only, so this runs on the cluster's DuckDB 0.10.3.
"""

from api.hexes import check_resolution, year_range
from api.sensitive import hidden_species
from api.species import species_details

# The ranks a rank card can be about, highest first.
RANKS = ("kingdom", "phylum", "class", "order", "family", "genus")

# How many species one page of the card holds by default, and at most.
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200

# Where the classification and the names come from, for the page to say.
CLASSIFICATION_SOURCE = "GBIF"
NAME_SOURCE = "Catalogue of Life, via GBIF"


class RankRequestError(ValueError):
    """A request that does not name a rank: an unknown rank, no name for it,
    or a name given for a rank below it."""


class UnknownRank(LookupError):
    """No species in BC (none that may be shown) has this classification."""


def rank_path(rank, given):
    """Return the path of a rank as [(rank, name or None)], from the kingdom
    down to the rank, from the names given (a mapping of rank to name; a
    missing or empty name is a missing rank). Raises RankRequestError for an
    unknown rank, a rank with no name, or a name given below the rank."""
    if rank not in RANKS:
        raise RankRequestError(f"rank must be one of {', '.join(RANKS)}; got {rank!r}.")
    depth = RANKS.index(rank)
    below = [lower for lower in RANKS[depth + 1:] if (given.get(lower) or "").strip()]
    if below:
        raise RankRequestError(f"a {rank} card takes the ranks down to {rank} only; got {', '.join(below)}.")
    path = [(name_rank, (given.get(name_rank) or "").strip() or None) for name_rank in RANKS[:depth + 1]]
    if path[-1][1] is None:
        raise RankRequestError(f"give the {rank}'s name as {rank}=...")
    return path


def path_filter(path):
    """Return (SQL condition, parameters) matching the species with exactly
    this path: each rank equal to its name, or missing where the name is
    None. The rank names come from RANKS, so they are safe to put in the
    SQL."""
    condition = " AND ".join(f'c."{rank}" IS NOT DISTINCT FROM ?' for rank, _ in path)
    return condition, [name for _, name in path]


def members_condition(path, hidden):
    """Return (SQL condition, parameters) on species_card (as c) for the
    species with this path that are not hidden."""
    condition, params = path_filter(path)
    return (f"{condition} AND NOT list_contains(CAST(? AS VARCHAR[]), c.specieskey)",
            [*params, sorted(hidden)])


def require_rank(data, cursor, rank, given):
    """Return the path of the rank asked for, if it has species that may be
    shown on its card (see the top of this file). Raises RankRequestError
    for a request that does not name a rank, and UnknownRank otherwise."""
    path = rank_path(rank, given)
    condition, params = members_condition(path, hidden_species(data.settings, None))
    if not cursor.execute(f"SELECT count(*) FROM species_card AS c WHERE {condition}", params).fetchone()[0]:
        raise UnknownRank(rank)
    return path


def rank_label(path):
    """Return how an answer names its rank: the rank, its name and its path."""
    return {"rank": path[-1][0], "name": path[-1][1],
            "path": [{"rank": name_rank, "name": name} for name_rank, name in path]}


def read_members(cursor, path, child, hidden):
    """Return the species with this path that are not hidden, most records
    first: [(key, records, records with no year, first year, last year,
    child rank's name or None)]."""
    condition, params = members_condition(path, hidden)
    child_column = f'c."{child}"' if child else "NULL"
    return cursor.execute(
        f"""SELECT c.specieskey, c.record_count, c.records_no_year, c.first_year, c.last_year,
                   {child_column} AS child
            FROM species_card AS c
            WHERE {condition}
            ORDER BY c.record_count DESC, c.col_name, c.specieskey""",
        params).fetchall()


def children_of(members):
    """Return the next rank down as [{name, species_count, record_count}],
    most records first, then by name, the species missing that rank (name
    None) last among equals."""
    groups = {}
    for _, records, _, _, _, child in members:
        count, total = groups.get(child, (0, 0))
        groups[child] = (count + 1, total + records)
    ordered = sorted(groups.items(), key=lambda item: (-item[1][1], item[0] is None, item[0] or ""))
    return [{"name": name, "species_count": count, "record_count": total} for name, (count, total) in ordered]


def rank_card(data, rank, given, offset=0, limit=DEFAULT_PAGE_SIZE):
    """Return the card for one rank (see the top of this file). Raises
    RankRequestError for a request that does not name a rank, and
    UnknownRank when no species (that may be shown) has this path."""
    path = rank_path(rank, given)
    depth = RANKS.index(rank)
    child = RANKS[depth + 1] if depth + 1 < len(RANKS) else None
    hidden = hidden_species(data.settings, None)

    cursor = data.cursor()
    members = read_members(cursor, path, child, hidden)
    if not members:
        raise UnknownRank(rank)
    page = members[offset:offset + limit]
    details = species_details(cursor, [row[0] for row in page])
    first_years = [row[3] for row in members if row[3] is not None]
    last_years = [row[4] for row in members if row[4] is not None]

    return {
        **rank_label(path),
        "species_count": len(members),
        "record_count": sum(row[1] for row in members),
        "records_no_year": sum(row[2] for row in members),
        "first_year": min(first_years) if first_years else None,
        "last_year": max(last_years) if last_years else None,
        "children_rank": child,
        "children": children_of(members) if child else None,
        "offset": offset,
        "limit": limit,
        "members": [{"specieskey": key, **details[key], "record_count": records}
                    for key, records, *_ in page],
        "classification_source": CLASSIFICATION_SOURCE,
        "name_source": NAME_SOURCE,
        "english_name_source": data.meta["statuses"]["status_source"],
        "sensitive_max_res": data.settings.sensitive_max_res,
    }


def rank_hexes(data, rank, given, res, year_from=None, year_to=None):
    """Return the rank map at resolution res (see the top of this file), for
    the years year_from to year_to if either is given. Raises
    RankRequestError, UnknownRank, or HexesRequestError (from hexes.py) for
    a bad resolution or year range."""
    check_resolution(res)
    years = year_range(year_from, year_to, data.meta["first_year"], data.meta["last_year"])
    cursor = data.cursor()
    path = require_rank(data, cursor, rank, given)
    condition, params = members_condition(path, hidden_species(data.settings, res))
    members = f"SELECT c.specieskey FROM species_card AS c WHERE {condition}"

    year_filter = "AND h.year BETWEEN ? AND ?" if years else ""
    cells = cursor.execute(
        f"""SELECT h.h3_r{res} AS cell, sum(h.occurrences)::BIGINT AS records,
                   count(DISTINCT h.specieskey)::INTEGER AS species
            FROM species_hex AS h
            WHERE h.specieskey IN ({members}) AND h.h3_r{res} IS NOT NULL {year_filter}
            GROUP BY 1
            ORDER BY 1""",
        [*params, *(years or [])]).fetchall()
    total, no_year, outside = cursor.execute(
        f"""SELECT coalesce(sum(h.occurrences), 0),
                   coalesce(sum(h.occurrences) FILTER (WHERE h.year IS NULL), 0),
                   coalesce(sum(h.occurrences) FILTER (
                       WHERE h.year IS NOT NULL AND (h.year < ? OR h.year > ?)), 0)
            FROM species_hex AS h
            WHERE h.specieskey IN ({members})""",
        [years[0] if years else 0, years[1] if years else 0, *params]).fetchone()

    return {
        **rank_label(path),
        "res": res,
        "year_from": year_from,
        "year_to": year_to,
        # The years actually counted (a missing bound filled in), or null
        # when every record is counted.
        "years_counted": list(years) if years else None,
        "hexagon_count": len(cells),
        "records_shown": sum(records for _, records, _ in cells),
        "records_total": total,
        # With no year range nothing is left out.
        "records_no_year_left_out": no_year if years else 0,
        "records_outside_years": outside if years else 0,
        "cells": [cell for cell, _, _ in cells],
        "occurrences": [records for _, records, _ in cells],
        "distinct_species": [species for _, _, species in cells],
        "sensitive_max_res": data.settings.sensitive_max_res,
    }
