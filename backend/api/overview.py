"""
overview.py

The overview map for GET /api/overview/hexes: for every H3 hexagon at one
resolution, how many records it holds and how many distinct species,
optionally for a range of years.

Data: species_hex.parquet, one row per species, year and hexagon, with the
key merge already applied, so species are counted by specieskey after the
merge, as everywhere else on the site. The one record with no specieskey
counts in records but not in species (count(DISTINCT specieskey) skips it),
exactly as in the overview files the front end ships
(frontend/public/data/bc_hex_r4..r7.csv.gz, built by
pipeline/gbif/build_hex_aggregates.py with the same GROUP BY). With no year
filter the answer matches those files exactly, hexagon for hexagon.

Years follow the same rules as the species map (hexes.py): with no
year_from and no year_to, every record is counted, including records with
no year; with either one, only records with a year in the range are
counted, a missing bound is the first or last year in the whole dataset,
records with no year are left out, and the response says how many.

The cells come back as three lists of the same length (cells, occurrences,
distinct_species), which is much smaller than one object per cell.

Every request reads the whole of species_hex (it is sorted by species, not
by hexagon), which takes about 0.1 to 0.2 seconds on a laptop. See the
README for the measurements.

Plain SQL only, so this runs on the cluster's DuckDB 0.10.3.
"""

from api.hexes import check_resolution, year_range


def read_cells(cursor, res, years):
    """Return (cell, records, distinct species) for every hexagon at one
    resolution, sorted by cell, for the year range if there is one. res has
    been checked, so it is safe to put into the column name."""
    year_filter = "AND year BETWEEN ? AND ?" if years else ""
    return cursor.execute(
        f"""SELECT h3_r{res} AS cell,
                   sum(occurrences)::BIGINT AS records,
                   count(DISTINCT specieskey)::INTEGER AS species
            FROM species_hex
            WHERE h3_r{res} IS NOT NULL {year_filter}
            GROUP BY 1
            ORDER BY 1""",
        list(years) if years else []).fetchall()


def records_left_out(cursor, years):
    """Return (records with no year, records with a year outside the range)
    across the whole dataset, both 0 when there is no year range, because
    nothing is left out then."""
    if years is None:
        return 0, 0
    no_year, outside = cursor.execute(
        """SELECT coalesce(sum(occurrences) FILTER (WHERE year IS NULL), 0),
                  coalesce(sum(occurrences) FILTER (
                      WHERE year IS NOT NULL AND (year < ? OR year > ?)), 0)
           FROM species_hex""",
        list(years)).fetchone()
    return no_year, outside


def overview_hexes(data, res, year_from=None, year_to=None):
    """Return the overview map at resolution res, for the years year_from to
    year_to if either is given. Raises HexesRequestError (from hexes.py)
    for a bad resolution or year range."""
    check_resolution(res)
    years = year_range(year_from, year_to, data.meta["first_year"], data.meta["last_year"])

    cursor = data.cursor()
    cells = read_cells(cursor, res, years)
    no_year_left_out, outside_years = records_left_out(cursor, years)

    return {
        "res": res,
        "year_from": year_from,
        "year_to": year_to,
        # The years actually counted (a missing bound filled in), or null
        # when every record is counted.
        "years_counted": list(years) if years else None,
        "hexagon_count": len(cells),
        "records_shown": sum(records for _, records, _ in cells),
        "records_total": data.meta["record_count"],
        "records_no_year_left_out": no_year_left_out,
        "records_outside_years": outside_years,
        "cells": [cell for cell, _, _ in cells],
        "occurrences": [records for _, records, _ in cells],
        "distinct_species": [species for _, _, species in cells],
    }
