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
| `pipeline/` | The production pipeline: `download_bc_raw.py` (Layer 1), `build_bc_clean.py` (Layer 2), `build_species_summary.py` (Layer 3). These are what runs to produce the dashboard's data. Also the Lunaris side: `lunaris_harvest_only.py` harvests dataset metadata over OAI-PMH, and `lunaris_keywords.py` is the finalized biodiversity keyword filter (see below). |
| `tools/` | Small helper scripts. `list_gbif_columns.py` prints a snapshot's column names and types straight from the parquet schema on S3 (schema only, no scan). `read_parquet.py` dumps a parquet file to CSV for eyeballing. |
| `exploration/` | Analysis and validation, not part of the product build. `gbif_bc_filtered.py` counts what survives the filters straight from S3; `gbif_bc_drops.py` is its diagnostic companion, attributing drops to each individual filter (note: those per-filter counts overlap and must not be summed). |
| `exploration/lunaris/` | Tuning and validation for the Lunaris keyword filter, not part of the product build. All of these import `pipeline/lunaris_keywords.py`. `lunaris_check_false_positives.py` is the evidence check to run before changing the filter; `lunaris_analyze.py` reports word frequencies and kept/dropped counts; `lunaris_check_missed.py` looks for biodiversity datasets the filter drops; `lunaris_sample_for_review.py` draws a mixed sample to hand-review and `lunaris_semantic_review.py` scores that sample's labels against the filter. |
| `exploration/archive/` | Superseded early attempts, kept for history: `gbif_bc_boundingBox.py` (bounding box only, the first feasibility check) and `gbif_bc_polygon.py` (first exact-polygon version). Not maintained - read them for context, don't run them. |

## The Lunaris keyword filter

A second data source: dataset-level metadata harvested from
[Lunaris](https://lunaris.ca/) (123,479 records), filtered down to the ones
actually about biodiversity. `pipeline/lunaris_keywords.py` is the single
source of truth for that filter and runs in three stages:

1. **`mask_false_positives()`** blanks out phrases where a keyword is not
   being used biologically, *before* matching. Each of the 7 patterns has a
   documented record count behind it. The one that mattered most: Sea-Bird
   Scientific makes the CTD and oxygen sensors Ocean Networks Canada deploys,
   so `Sea-Bird` matched the `bird` keyword on 791 instrument-deployment
   records — 4.5% of everything the filter kept.
2. **Keyword matching** over `STRONG_KEYWORDS` plus 34 generated plural forms.
   The word-boundary regex is `(?![a-zA-Z])`, so a singular keyword never
   matched its own plural — `plant` did not match `plants`. Plurals are
   generated explicitly rather than with a trailing `s?` so each one stays
   reviewable.
3. **`NO_PLURAL`** withholds a plural where it is measurably more ambiguous
   than the singular, with the reason recorded: `trees` (decision trees,
   R-trees), `animals` (lab and livestock boilerplate), `occurrences`
   (occurrences of events), `abundances` (isotopic abundances), and others.

Effect on the full harvest, one stage at a time:

| Stage | Kept |
| --- | ---: |
| Original (no masks, singular only) | 17,392 |
| + Sea-Bird mask | 16,601 |
| + power / industrial / energy / invasive masks | 16,542 |
| + plural matching | 17,094 |
| + StatCan `percentage of plants` mask | **17,057** |

**Never change the keyword list or the masks without running
`exploration/lunaris/lunaris_check_false_positives.py` first.** A mask is only
safe if every record it flips from kept to dropped is genuinely
non-biodiversity; a plural is only worth adding if the records it newly keeps
are biodiversity rather than another sense of the word.

The filter is a **coarse first pass**, not the final word. Against 48
hand-labelled records it agrees 43/48, with 5 remaining false positives
(`fish` from the organisation name "Swim Drink Fish", plus `animal`,
`estuary`, `biomass` and `invasive` hits) and no misses. Those are left for a
later species-name pass rather than chased with more masks. Recall is the
untested side: the hand-labelled sample drew only 12 records from the ~106,000
the filter drops, so it cannot say how much biodiversity data is being lost.


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
