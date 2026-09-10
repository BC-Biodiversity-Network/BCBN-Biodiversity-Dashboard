# BCBN Biodiversity Dashboard - BC occurrence data

Data pipeline behind the BC Biodiversity Network (BCBN) dashboard. It takes
[GBIF](https://www.gbif.org/) occurrence records and produces a clean,
British-Columbia-only dataset for the dashboard to read.

The source is the GBIF cloud snapshot on AWS S3 (`gbif-open-data-us-east-1`),
which GBIF republishes monthly. Everything is queried with DuckDB reading
parquet directly - no database server, no full-archive download.

## The three-layer pipeline

The pipeline is split by layer so the expensive step (downloading), the step
whose rules keep changing (filtering), and the summaries built on top of it
are independent of each other.

```
GBIF S3 snapshot -> bc_raw.parquet -> bc_clean.parquet -> bc_species_summary.csv
                       (Layer 1)        (Layer 2)              (Layer 3)
```

**Layer 1 - `pipeline/download_bc_raw.py` -> `bc_raw.parquet`**
Downloads the raw BC slice of a GBIF snapshot to a local parquet file.
It clips to BC's *bounding box* only - a cheap lon/lat range check that lets
DuckDB skip most of the world using parquet row-group statistics. The box is
deliberately a bit larger than BC (it catches corners of WA/AK/AB), no
quality filters are applied, and all 50 columns are kept. The point is a
wide, unfiltered local copy, so later steps never have to re-download.

```
python pipeline/download_bc_raw.py --snapshot 2026-08-01 --out ~/bcbn/data/bc_raw.parquet
```

**Layer 2 - `pipeline/build_bc_clean.py` -> `bc_clean.parquet`**
Turns the raw slice into the clean product the dashboard consumes. It fetches
BC's official legal boundary (ABMS, marine-inclusive, so coastal records are
kept) from the BC Geographic Warehouse, clips the raw data from the bounding
box down to that *exact polygon* with `ST_Contains`, then applies Evan's
quality filters: keep `PRESENT` only, drop fossil and living specimens,
require a species-level ID and coordinates, and drop anything GBIF flagged
with one of 12 coordinate-quality issues.

```
python pipeline/build_bc_clean.py --raw ~/bcbn/data/bc_raw.parquet --out ~/bcbn/data/bc_clean.parquet
```

**Layer 3 - `pipeline/build_species_summary.py` -> `bc_species_summary.csv`**
Groups the clean product by species to give a per-species BC record count
(`n_bc`). The output columns - `kingdom, phylum, class, order, family, genus,
species, n_bc` - match Evan's `for_lucia.csv` so the two can be compared row
by row. Only `n_bc` can be computed here (not `n_notbc` or `n_total`), since
the clean product holds BC records only. The result is a few MB, small enough
to commit and share.

```
python pipeline/build_species_summary.py --clean ~/bcbn/data/bc_clean.parquet --out ~/bcbn/data/bc_species_summary.csv
```

Because Layers 2 and 3 read local files rather than S3, the filter rules can
be changed and the products rebuilt cheaply, with no re-download. The
**monthly refresh** is a re-run of Layer 1 with a new `--snapshot` date once
GBIF publishes a new snapshot, then Layers 2 and 3.

## Validation

The clean product holds **38,739,753 records** and **38,218 distinct
species**. The record count matches the earlier direct-from-S3 count in
`exploration/gbif_bc_filtered.py`, so the two-step raw-then-clean path gives
the same answer as filtering in one pass.

Compared species by species against Evan's `for_lucia.csv` (36,156 species):
about half of the shared species match exactly, and roughly 70% are within
10%. The main known difference is that this pipeline does **not yet apply
Evan's `countrycode` filter**, which makes our counts run slightly higher.

## Layout

| Folder | Contents |
| --- | --- |
| `pipeline/` | The production pipeline: `download_bc_raw.py` (Layer 1), `build_bc_clean.py` (Layer 2), `build_species_summary.py` (Layer 3). These are what runs to produce the dashboard's data. |
| `tools/` | Small helper scripts. `list_gbif_columns.py` prints a snapshot's column names and types straight from the parquet schema on S3 (schema only, no scan). `read_parquet.py` dumps a parquet file to CSV for eyeballing. |
| `exploration/` | Analysis and validation, not part of the product build. `gbif_bc_filtered.py` counts what survives the filters straight from S3; `gbif_bc_drops.py` is its diagnostic companion, attributing drops to each individual filter (note: those per-filter counts overlap and must not be summed). |
| `exploration/archive/` | Superseded early attempts, kept for history: `gbif_bc_boundingBox.py` (bounding box only, the first feasibility check) and `gbif_bc_polygon.py` (first exact-polygon version). Not maintained - read them for context, don't run them. |

## Running environment

The pipeline runs on the **ZCU cluster**. DuckDB is pinned there to **0.10.3**
for glibc compatibility, and the scripts are written to that version's limits:
no named (`$name`) parameters, and the BC boundary is stored as WKT text
rather than a `GEOMETRY` column, because 0.10.3 reads a `GEOMETRY` written to
parquet back as an unusable BLOB. Keep new code inside those constraints, or
the cluster run will fail even though it works locally.

Python dependencies: `duckdb`, `geopandas`, `shapely`, `requests`.

## Data files

**Data lives outside git.** The parquet products are tens of millions of rows
and are regenerated by the pipeline, so `data/` and `*.parquet` are
gitignored. On the cluster they live under `~/bcbn/data/`. Never commit them.
The Layer-3 species summary CSV is the exception - it's small enough to share.
