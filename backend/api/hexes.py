"""
hexes.py

The species map for GET /api/species/{key}/hexes: how many records one
species has in each H3 hexagon at one resolution, optionally for a range
of years.

Data: species_hex.parquet, one row per species, year and hexagon, with the
key merge already applied (so an old key's records are counted under its
kept key). An old merged key gives the kept key's map, with merged_from,
like the card and search.

Years: with no year_from and no year_to, every record is counted, including
records with no year. With either one, only records with a year in the
range are counted (a missing bound is the first or last year in the whole
dataset); records with no year cannot be placed in a range, so they are
left out, and the response says how many. The years allowed are those of
the whole dataset (/api/meta first_year to last_year), not of the species,
so one year control works for every species.

The cells come back as two lists of the same length, cells and
occurrences, which is about half the size of one object per cell. Colour
bins are not worked out here; they belong to the front end.

Plain SQL only, so this runs on the cluster's DuckDB 0.10.3.
"""

from api.settings import RESOLUTIONS
from api.species import resolve_key


class HexesRequestError(ValueError):
    """A request the map cannot answer: a resolution that is not stored,
    or a year range that is backwards or outside the data."""


def check_resolution(res):
    """Stop with a clear message unless res is one of the stored
    resolutions (4 to 7)."""
    if res not in RESOLUTIONS:
        raise HexesRequestError(
            f"res must be one of {', '.join(str(r) for r in RESOLUTIONS)}; got {res}.")


def year_range(year_from, year_to, first_year, last_year):
    """Return the (from, to) years to count, or None when no year filter
    was asked for. A missing bound becomes the first or last year in the
    data. Stops with a clear message if a year is outside first_year to
    last_year (the whole dataset's) or if year_from is after year_to."""
    if year_from is None and year_to is None:
        return None
    for name, year in (("year_from", year_from), ("year_to", year_to)):
        if year is not None and not first_year <= year <= last_year:
            raise HexesRequestError(
                f"{name} must be between {first_year} and {last_year}, the years "
                f"in the data; got {year}.")
    low = first_year if year_from is None else year_from
    high = last_year if year_to is None else year_to
    if low > high:
        raise HexesRequestError(
            f"year_from ({low}) must not be after year_to ({high}).")
    return low, high


def count_records(cursor, card_key, years):
    """Return (records in total, records with no year, records with a year
    outside the range) for one species. With no year range, the last two
    are 0, because nothing is left out."""
    total, no_year, outside = cursor.execute(
        """SELECT coalesce(sum(occurrences), 0),
                  coalesce(sum(occurrences) FILTER (WHERE year IS NULL), 0),
                  coalesce(sum(occurrences) FILTER (
                      WHERE year IS NOT NULL AND (year < ? OR year > ?)), 0)
           FROM species_hex
           WHERE specieskey = ?""",
        [years[0] if years else 0, years[1] if years else 0, card_key]).fetchone()
    if years is None:
        return total, 0, 0
    return total, no_year, outside


def read_cells(cursor, card_key, res, years):
    """Return the (cell, records) pairs for one species at one resolution,
    sorted by cell, for the year range if there is one. res has been
    checked, so it is safe to put into the column name."""
    year_filter = "AND year BETWEEN ? AND ?" if years else ""
    return cursor.execute(
        f"""SELECT h3_r{res} AS cell, sum(occurrences)::BIGINT AS records
            FROM species_hex
            WHERE specieskey = ? {year_filter}
            GROUP BY 1
            ORDER BY 1""",
        [card_key, *years] if years else [card_key]).fetchall()


def species_hexes(data, key, res, year_from=None, year_to=None):
    """Return the map for one species, or None if the key is neither a card
    key nor a key merged into one (BCSEE-only species have no card either).
    Raises HexesRequestError for a bad resolution or year range."""
    check_resolution(res)
    years = year_range(year_from, year_to, data.meta["first_year"], data.meta["last_year"])

    card_key, merged_from = resolve_key(key, data.merged_keys)
    cursor = data.cursor()
    if cursor.execute("SELECT 1 FROM species_card WHERE specieskey = ?",
                      [card_key]).fetchone() is None:
        return None

    cells = read_cells(cursor, card_key, res, years)
    total, no_year_left_out, outside_years = count_records(cursor, card_key, years)
    shown = sum(records for _, records in cells)

    return {
        "specieskey": card_key,
        "requested_key": key,
        "merged_from": merged_from,
        "res": res,
        "year_from": year_from,
        "year_to": year_to,
        # The years actually counted (a missing bound filled in), or null
        # when every record is counted.
        "years_counted": list(years) if years else None,
        "hexagon_count": len(cells),
        "records_shown": shown,
        "records_total": total,
        "records_no_year_left_out": no_year_left_out,
        "records_outside_years": outside_years,
        "cells": [cell for cell, _ in cells],
        "occurrences": [records for _, records in cells],
    }
