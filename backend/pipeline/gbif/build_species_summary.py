"""
build_species_summary.py

Build a species-level summary from the BC occurrence records. For each
species, count how many records fall in BC.

Input:  bc_occurrence.parquet   (from build_dwca_tables.py: inside BC's
                                 polygon, quality-filtered, one row per
                                 record)
Output: bc_species_summary.csv  (one row per species, with n_bc)

The output columns match Evan's for_lucia.csv so the two can be compared
species by species:
    kingdom, phylum, class, order, family, genus, species, n_bc

We can only compute n_bc here (records inside BC), not n_notbc or n_total,
because bc_occurrence only keeps BC records. n_bc is what we need to
validate against Evan's numbers.

The result is small (tens of thousands of rows, a few MB), so unlike the
occurrence parquet it's fine to commit and share.

Only plain SQL is used, so it runs on the cluster's DuckDB 0.10.3.

Run:
    python build_species_summary.py --occurrence ~/bcbn/data/dwca/bc_occurrence.parquet --out ~/bcbn/data/bc_species_summary.csv
"""

import argparse

import duckdb


def build_summary(occurrence_path, out_path):
    """Write the species summary CSV and return how many rows it has."""
    con = duckdb.connect()

    # Group the records by full taxonomy down to species, and count
    # how many records each species has in BC. Order by taxonomy so the
    # output lines up with Evan's for_lucia.csv for easy comparison.
    query = f"""
        COPY (
            SELECT
              kingdom, phylum, class, "order", family, genus, species,
              count(*) AS n_bc
            FROM read_parquet('{occurrence_path}')
            GROUP BY kingdom, phylum, class, "order", family, genus, species
            ORDER BY kingdom, phylum, class, "order", family, genus, species
        ) TO '{out_path}' (FORMAT CSV, HEADER)
    """
    print("Building species summary (grouping records by species)...")
    con.execute(query)

    # How many distinct species ended up in the summary?
    n_species = con.execute(
        f"SELECT count(*) FROM read_csv('{out_path}')"
    ).fetchone()[0]
    con.close()
    return n_species


def main():
    """Read the occurrence records and write the species summary."""
    parser = argparse.ArgumentParser(
        description="Build a per-species BC record-count summary from bc_occurrence.parquet."
    )
    parser.add_argument(
        "--occurrence",
        default="/home/songyanf/bcbn/data/dwca/bc_occurrence.parquet",
        help="Path to bc_occurrence.parquet from build_dwca_tables.py.",
    )
    parser.add_argument(
        "--out",
        default="/home/songyanf/bcbn/data/bc_species_summary.csv",
        help="Where to write the species summary CSV.",
    )
    args = parser.parse_args()

    print(f"Occurrences: {args.occurrence}")
    print(f"Output:      {args.out}")

    n_species = build_summary(args.occurrence, args.out)

    print(f"\nDone. Distinct species: {n_species:,}")
    print(f"File: {args.out}")


if __name__ == "__main__":
    main()
