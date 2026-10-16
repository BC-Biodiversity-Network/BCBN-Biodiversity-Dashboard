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

**Layer 1 - `backend/pipeline/gbif/legacy/download_bc_raw.py` -> `bc_raw.parquet`**
Downloads the raw BC slice of a GBIF snapshot to a local parquet file.
It clips to BC's *bounding box* only - a cheap lon/lat range check that lets
DuckDB skip most of the world using parquet row-group statistics. The box is
deliberately a bit larger than BC (it catches corners of WA/AK/AB), no
quality filters are applied, and all 50 columns are kept. The point is a
wide, unfiltered local copy, so later steps never have to re-download.

```
cd backend
python pipeline/gbif/legacy/download_bc_raw.py --snapshot 2026-08-01 --out ~/bcbn/data/bc_raw.parquet
```

**Layer 2 - `backend/pipeline/gbif/build_bc_clean.py` -> `bc_clean.parquet`**
Turns the raw slice into the clean product the dashboard consumes. It fetches
BC's official legal boundary (ABMS, marine-inclusive, so coastal records are
kept) from the BC Geographic Warehouse, clips the raw data from the bounding
box down to that *exact polygon* with `ST_Contains`, then applies Evan's
quality filters: keep `PRESENT` only, drop fossil and living specimens,
require a species-level ID and coordinates, and drop anything GBIF flagged
with one of 12 coordinate-quality issues.

```
cd backend
python pipeline/gbif/build_bc_clean.py --raw ~/bcbn/data/bc_raw.parquet --out ~/bcbn/data/bc_clean.parquet
```

**Layer 3 - `backend/pipeline/gbif/build_species_summary.py` -> `bc_species_summary.csv`**
Groups the occurrence records by species to give a per-species BC record
count (`n_bc`). The output columns - `kingdom, phylum, class, order, family,
genus, species, n_bc` - match Evan's `for_lucia.csv` so the two can be
compared row by row. Only `n_bc` can be computed here (not `n_notbc` or
`n_total`), since the input holds BC records only. The result is a few MB,
small enough to commit and share. It now reads `bc_occurrence.parquet` from
`build_dwca_tables.py` (see below) instead of `bc_clean.parquet`, with the
same output.

```
cd backend
python pipeline/gbif/build_species_summary.py --occurrence ~/bcbn/data/dwca/bc_occurrence.parquet --out ~/bcbn/data/bc_species_summary.csv
```

Because Layers 2 and 3 read local files rather than S3, the filter rules can
be changed and the products rebuilt cheaply, with no re-download. The
**monthly refresh** is a re-run of Layer 1 with a new `--snapshot` date once
GBIF publishes a new snapshot, then Layers 2 and 3.

## Building from a GBIF Darwin Core Archive download

This path is replacing the three layers above. The first step,
`request_gbif_download.py`, replaces `download_bc_raw.py`, which is kept for
now.

**`backend/pipeline/gbif/request_gbif_download.py` -> `<key>.zip`, `<key>.download.json`**
Asks GBIF for a Darwin Core Archive download of every PRESENT record inside
the polygon in `bc_boundary_gbif.wkt` (from `make_gbif_boundary.py`). The
download uses the Catalogue of Life taxonomy (the same checklist as
`match_bcsee_gbif.py`) and includes the Multimedia extension. The script saves
the download key as soon as GBIF returns it, checks the status every
`--poll-minutes` (default 5) until the download is ready, which can take
hours for all of BC, and then downloads the zip. `<key>.download.json`, saved
next to the zip, holds the key, the DOI, the record count and the request
sent, and the Catalogue of Life release (`col_release`) GBIF tied to the
checklist at submission. `--dry-run` only prints the request. `--key <key>` picks up an existing
download (no new request, just wait and download), so a stopped run can
continue. It doesn't download a zip that is already complete.

Submitting needs your GBIF.org account in three environment variables, which
the script never prints or saves: `GBIF_USER` (your username, not your
email), `GBIF_PWD` and `GBIF_EMAIL` (where GBIF sends the "download ready"
email). `--dry-run` and `--key` don't need them. GBIF only lets one user run
a few downloads at once.

```
cd backend
python pipeline/gbif/request_gbif_download.py --dry-run
python pipeline/gbif/request_gbif_download.py --outdir ~/bcbn/data
python pipeline/gbif/request_gbif_download.py --key <download key> --outdir ~/bcbn/data
```

**`backend/pipeline/gbif/build_dwca_tables.py` -> `bc_occurrence`, `bc_media`, `bc_datasets`, `bc_species_image`, `download_info.json`**
Builds the dashboard's tables from a GBIF download in Darwin Core Archive
format (the zip GBIF makes for a download request, filtered with
`bc_boundary_gbif.wkt`), instead of from the S3 snapshot. Unlike the
snapshot, the download carries a citable DOI, the media items and the
metadata of every dataset.

- `bc_occurrence.parquet`: one row per record. The records go through
  `build_bc_clean.py`'s own `build_clean()`, so the filters and the exact
  boundary clip are the same code that makes `bc_clean`. It has the 48
  `bc_clean` columns that a download contains (all but
  `verbatimscientificnameauthorship` and `publishingorgkey`), with the same
  names and types, plus `references` (the link to the original record) and
  `h3_r4` to `h3_r7`, each record's H3 cell, binned straight from the
  coordinates at each resolution (never rolled up from a finer cell).
  `build_species_tables.py` groups by these columns to make `species_hex`,
  from which `build_hex_aggregates.py` makes the map's hexagon tables.
- `bc_media.parquet`: one row per media item (images, sound, video) of a kept
  record, numbered within the record by `media_order` in file order, with a
  short `license_code` (`CC0_1_0`, `CC_BY_4_0`, `CC_BY_NC_4_0`, ...) parsed
  from the license value. A license that names no version, like
  "(cc-by-sa)", gets a code with no version (`CC_BY_SA`). One that can't be
  read leaves `license_code` empty, and the script prints those values.
- `bc_datasets.parquet`: one row per dataset in the archive: title,
  citation, license and DOI from its metadata, publisher, and how many of its
  records are in `bc_occurrence` (0 if all were filtered out).
- `bc_species_image.parquet`: one image per species, picked only among the
  StillImages that pass the same credit check as the API: an image URL, a
  link to the original, a creator and a readable licence
  (`backend/shared/image_credit.py`). Among those, the best licence wins,
  then the most recent record. The licences, preferred in this order: CC0
  or the Public Domain Mark, CC BY, CC BY-SA, CC BY-ND, CC BY-NC, CC
  BY-NC-SA, CC BY-NC-ND. A CC licence must name its version: CC BY asks for
  a link to the licence, and without a version that link would be a guess.
  Beaty Museum images provided without a licence keep their place in the
  ranking (rank 8), but
  fail the check, so none is picked for now. `license_rank` and
  `license_source` say which case applies. The selection is in
  `build_species_image.py`, which this script calls, and which can also
  redo it on its own from the existing tables (see below).
- `download_info.json`: the download key, DOI, date and query. The DOI comes
  from the GBIF download API when the archive doesn't include it.

DuckDB can't read inside a zip, so the script extracts `occurrence.txt` and
`multimedia.txt`, one at a time, to a temporary folder in `--workdir`. It
never extracts `verbatim.txt`. Each text file is deleted as soon as it has
been copied to parquet, and the folder is deleted at the end, even if the run
fails. Every step streams: DuckDB spills to the temporary folder when it
reaches `--memory-limit`, and the Python passes go a batch at a time.

For a full BC download (about 43M records, `occurrence.txt` about 50 GB):

- **Disk:** about 60 GB free in `--workdir`. The peak comes while
  `occurrence.txt` (50 GB) and its parquet copy (about 4 GB) are both there.
  Allow up to 15 GB more if DuckDB spills a lot. `--outdir` needs about 3 GB
  for the tables.
- **Memory:** `--memory-limit` plus about 1.5 GB for Python. With
  `--memory-limit 8GB`, ask for a 12 GB job.
- **Time:** from the test download scaled up, roughly an hour on DuckDB
  0.10.3. This is a rough guess; the script prints the elapsed time at each
  step.

```
cd backend
python pipeline/gbif/build_dwca_tables.py --zip ~/bcbn/data/gbif_dwca/0009771-260928105237408.zip --outdir ~/bcbn/data/gbif_tables \
    --workdir ~/bcbn/data/tmp_dwca --memory-limit 8GB --threads 8
```

The steps run in this order:

```
request_gbif_download.py -> build_dwca_tables.py
    (-> build_species_image.py, only to pick the images again without the zip)
    -> fetch_col_names.py (once per Catalogue of Life release)
    -> make_species_key_merge.py
    -> build_species_tables.py --key-merge --col-names
    -> build_hex_aggregates.py
```

**`backend/pipeline/gbif/build_species_image.py` -> `bc_species_image.parquet`**
Picks the species images again from the tables `build_dwca_tables.py` already
wrote (`bc_occurrence`, `bc_media`, `bc_datasets`), with the same code, so the
50 GB download does not have to be unpacked again. The new table is written as
`bc_species_image.tmp.parquet` and only replaces the real one when every check
passes: one row per species, every picked image passes the credit check, every
species with a passing StillImage has one, and no species whose current image
passes loses it or gets a different one. It prints how many species gained,
lost or changed their image and, with `--key-merge`, how many species cards
will have an image. Rerun `build_species_tables.py` afterwards so the cards
use the new images.

```
cd backend
python pipeline/gbif/build_species_image.py --tables ~/bcbn/data/gbif_tables \
    --key-merge ~/bcbn/data/gbif_tables/species_key_merge.csv \
    --memory-limit 8GB --threads 8 --temp-dir ~/tmp/duckdb_species_image
```

The key merge is applied in one place only, `build_species_tables.py`, and
the overview map is built from its output, so every species count on the
site (overview hexagons and species cards) is distinct specieskey after the
merge.

**`backend/pipeline/gbif/fetch_col_names.py` -> `col_names_<release>.parquet`, `col_release.json`**
Our specieskeys are Catalogue of Life ids, because the download is made with
the Catalogue of Life checklist. GBIF ties that checklist to one ChecklistBank
release at a time; `request_gbif_download.py` records which one in
`<key>.download.json` when it submits the request, and `build_dwca_tables.py`
copies it into `download_info.json` as `col_release`. The current download
was made before that was recorded; it used release **315557** (COL26.6 XR,
2026-06-19), checked by finding the download's keys in that release and not
in the ones before or after it. This script downloads that release's flat
ColDP export from ChecklistBank (133 MB,
`https://api.checklistbank.org/dataset/315557/export.zip?format=ColDP`), reads
its one table straight out of the zip and keeps every name's id, status,
rank, name, authorship and classification (about 8 million names, a 145 MB
parquet). It is only needed again when the download uses a new release. The
download and the parquet are not committed.

```
cd backend
python pipeline/gbif/fetch_col_names.py --release 315557 --outdir ~/bcbn/data/col
```

**`backend/pipeline/gbif/make_species_key_merge.py` -> `species_key_merge.csv`, `species_key_not_merged.csv`**
Some species names have two specieskeys in the Catalogue of Life. This script
reads `duplicate_species_lookup.csv` (from
`backend/exploration/gbif/lookup_duplicate_species_keys.py`) and decides
which pairs are merged. A pair merges automatically only if:

1. Iron rule: kingdom, phylum, class, order, family, genus and species match
   letter for letter wherever both keys have a value. The species name must
   always match exactly.
2. Empty values: taking both keys together, at most one of those seven
   levels is empty (on one side or both), and never genus or species.
3. Both keys were found in the lookup.

Authors and years are not used to decide anything; `author_match` and
`year_match` columns record them for review. The key kept is the accepted
one, then the one with more records, then the one that sorts first
alphabetically. `species_key_merge.csv` keeps the status, record count and
authorship of both keys, so a merge can be switched or undone;
`species_key_not_merged.csv` says which rule stopped each pair and, for a
conflict, the level and the two values.

**The one exception to the iron rule** is a hand-checked row in
`backend/pipeline/gbif/species_key_merge_manual.csv` (columns `old_key`,
`kept_key`, `old_name`, `old_authorship`, `kept_name`, `kept_authorship`,
`col_synonym_id`, `reason`, `checked_by`, `checked_date`). It is accepted only
if all of these hold, checked by the script against the Catalogue of Life
file given with `--col-names`:

1. The Catalogue of Life lists the old key's exact name and exact authorship
   as a synonym (status "synonym", not "ambiguous synonym") whose parent is
   the kept key, and that synonym's id is `col_synonym_id`. Comparing the
   synonym's authorship with the old key's keeps out a different species
   that only shares the name (Lasius niger listed under Tetramorium
   caespitum with another author). The two keys' own authorships are not
   compared, since a plant moved to another genus gets a new combining
   author (Rhytidiadelphus triquetrus (Hedw.) Warnst. and Hylocomiadelphus
   triquetrus (Hedw.) Ochyra & Stebel).
2. `reason`, `checked_by` and `checked_date` are filled in.
3. Both keys are in the Catalogue of Life file, the kept key is accepted
   there, and the names and authorships in the row are exactly its own.

Any row that fails stops the script with every problem listed. The rules
are listed once, in `MANUAL_ROW_RULES`, so they are easy to change.
`build_species_tables.py` checks rule 1 again, with the same code, before it
merges two keys whose species names differ. A manual row's record counts are
left blank in `species_key_merge.csv`; `build_species_tables.py` prints the
records moved. The file holds one row: Cooper's Hawk, `VP2T6` Accipiter
cooperii (Bonaparte, 1828) into `CVW5J` Astur cooperii (Bonaparte, 1828),
through synonym `64FV9`.

```
cd backend
python pipeline/gbif/make_species_key_merge.py \
    --lookup ~/bcbn/data/gbif_tables/duplicate_species_lookup.csv \
    --col-names ~/bcbn/data/col/315557/col_names_315557.parquet
```

**`backend/pipeline/gbif/build_species_tables.py` -> `species_hex.parquet`, `species_card_gbif.parquet`**
`species_hex` holds the records per species, year and hexagon (h3_r4 to
h3_r7). The clean filter keeps only records with a species name, so almost
every record has a specieskey (1 record in the October 2026 build does not;
it is kept with specieskey NULL). `species_card_gbif` holds one
row per species for the species card. With `--key-merge
species_key_merge.csv`, each old key's records are counted under its kept
key. The card's name and authorship are the Catalogue of Life's own for the
(kept) key, from `--col-names`, never the occurrence records' names (which can
be a subspecies, another spelling or a BOLD id); `name_source` says
"Catalogue of Life, via GBIF" and `col_release` names the release, so the
page can label the author. A key missing from that file is a FAIL. As a
safety check it stops, before building anything, if a merge row's
two keys do not have exactly one species name each in `bc_occurrence`, and
the two names are not the same, unless the Catalogue of Life lists the old
key's name and authorship as a synonym of the kept key (rule 1 above). The
card's `merged_names` lists each merged key whose Catalogue of Life name is
not the card's own, with that name and authorship (`key`, `name`,
`authorship`), and is empty for a same-name merge. Its checks compare the totals with `bc_occurrence`, and, for
each resolution, the per-hexagon occurrences and distinct specieskey worked
out straight from the records with `species_hex` grouped the same way.

```
cd backend
python pipeline/gbif/build_species_tables.py --tables ~/bcbn/data/gbif_tables \
    --outdir ~/bcbn/data/gbif_tables/species_merged \
    --key-merge ~/bcbn/data/gbif_tables/species_key_merge.csv \
    --col-names ~/bcbn/data/col/315557/col_names_315557.parquet --col-release 315557 \
    --memory-limit 8GB --threads 8 --temp-dir ~/tmp/duckdb_species
```

**`backend/pipeline/gbif/build_hex_aggregates.py` -> `bc_hex_r{4..7}.parquet`, `bc_hex_r{4..7}.csv.gz`**
Counts the records and distinct species in each hexagon at resolutions 4 to
7 for the overview map, by grouping `species_hex.parquet` on its `h3_r4` to
`h3_r7` columns: `occurrences` is the sum of the records (including the rare
record with no specieskey) and `distinct_species` is the number of distinct
specieskey, after the merge. It computes no H3 itself, so it runs on DuckDB
0.10.3 and needs no h3 package. The gzipped CSVs are what the map loads.
Their columns are `h3_cell`, `occurrences` and `distinct_species`, and the
same data always gives the same bytes. Its checks: each resolution's total
occurrences equal the total in `species_hex`, one row per hexagon, and each
CSV holds exactly its parquet's rows.

The pipeline runs on the server, so `--frontend-dir` there is a folder
outside the repository. The CSVs are copied by hand to the Mac, compared with
the committed ones, copied into `frontend/public/data` and committed from the
Mac.

```
cd backend
python pipeline/gbif/build_hex_aggregates.py \
    --species-hex ~/bcbn/data/gbif_tables/species_merged/species_hex.parquet \
    --outdir ~/bcbn/data/gbif_tables/hex \
    --frontend-dir ~/bcbn/data/gbif_tables/hex_frontend_csv
```

On the server, `~/bcbn/data/gbif_tables/hex/` already holds the bc_hex parquet
files and the two robin test files (`bc_hex_r5_turdus_migratorius.parquet`,
`bc_hex_r6_turdus_migratorius.parquet`) from the old version of this script.
`bc_hex_r4.parquet` to `bc_hex_r7.parquet` there are replaced after the checks
pass. The script no longer writes the robin files, so those two stay there
until they are deleted by hand.

**Outputs are only replaced when every check passes.** `build_species_tables.py`
and `build_hex_aggregates.py` write each output under a temporary name in the
same folder first (`species_hex.tmp.parquet`, `bc_hex_r5.tmp.csv.gz`, ...),
run all their checks on those, and only then move them onto the real names.
If a check fails, the previous files are left untouched, the temporary files
stay for inspection, the script says which files were not replaced, and it
exits with status 1. The checks are only printed, never written into the
tables.

## BCSEE statuses on the species cards

`backend/pipeline/bcsee/build_bcsee_status_display.py` gathers every BCSEE
entry that points to the same species key into
`bcsee_status_by_gbif_species.parquet`, one row per species key: entries with
a shown status (Red, Blue, Yellow, Exotic) and entries that are in BCSEE
without one (Not Reviewed, Unknown, Accidental, No Status), so the site can
tell "in BCSEE but no status" from "not in BCSEE". Extinct entries are left
out, as the GBIF cleaning leaves out extinct taxa, and so is the one Other
entry for now; the run lists them. `statuses` holds only the shown statuses
(empty for a species without one), and every entry keeps its own BCSEE value
as its `status`, so a Not Reviewed subspecies is never mixed up with its
species' status. Every entry also keeps BCSEE's own `classification_level`
(Species, Subspecies, Variety or Population), so the card can tell a separate
BCSEE species from a subspecies. Run it with the same species key merge as
`build_species_tables.py`, so the statuses join to the species cards:

```
cd backend
python pipeline/bcsee/build_bcsee_status_display.py --data-dir ~/bcbn/data \
    --key-merge ~/bcbn/data/gbif_tables/species_key_merge.csv --col-release 315557
```

The species names come from GBIF's saved answers; a name not saved yet is
looked up with GBIF's API. `--col-release` is only needed then: before any
lookup, the run checks that GBIF's Catalogue of Life checklist still points
at that release (the one the rest of the data uses), and stops without
asking or writing anything if not.

With `--key-merge` (off by default), every entry matched to an old key is
moved to its kept key, right after the hand-checked overrides, so it covers
both. Each row and each entry keep `matched_species_keys`, the key or keys
before the merge. Entries that end up the same are combined, keeping all
their element_codes; entries with different statuses on one kept key are
kept side by side, never one picked over the other, and the run prints them
for review. With the current merge list, two shown statuses move
(Massalongia carnosa, Yellow, from `VL5W8` to `737CH`, and Cooper's Hawk,
Accipiter cooperii, Yellow, from `VP2T6` to `CVW5J`).

**Extra keys.** GBIF matches each BCSEE entry to one species key. When the
same species also has a second key under another name, with the records on
it, a hand-checked row in `backend/pipeline/bcsee/bcsee_extra_keys.csv`
(tracked in git) says that the BCSEE entry also applies to that key. The
keys are never merged: the entry stays on its own key, and a copy is added
on the extra key after the overrides and the key merge, with
`also_applied_from_key` set to the key it came from (empty on every other
entry). Columns: `element_code`, `extra_gbif_species_key`,
`bcsee_scientific_name`, `extra_gbif_scientific_name`, `reason`,
`checked_by`, `checked_date`. The run stops, listing every problem, if a
code is not a shown BCSEE entry or its name does not match, the entry is not
at species level, the extra key is the entry's own key or an old key of the
merge, already has a BCSEE entry or is named by two rows, or GBIF's name for
it is not the one in the row; it prints every row applied with both names.
The list holds only its header now. Its first row, Cooper's Hawk
(`ABNKC12040` also on `CVW5J`), was removed when the species key merge put
`VP2T6` into `CVW5J`: the status now follows the merge, and the checks would
refuse the old row, since the entry is already on `CVW5J`.

`backend/pipeline/bcsee/bcsee_extra_key_candidates.csv` lists 104 more pairs
found the same way (93 `strong`, 11 `medium`, column `tier`), waiting for
review, with a `refused_by_current_checks` column for the 13 the checks
above would refuse. Nothing reads it: a pair takes effect only once its
first four columns are copied into `bcsee_extra_keys.csv` with a reason and
who checked it.

Every row also says where the statuses come from, for the front end to show
next to each status: `status_source` ("BC Species and Ecosystems Explorer
(BCSEE)"), `status_source_date` (the date the province made the BCSEE export,
2026-05-05 now) and `status_pulled_on` (the date `pull_bcsee.py` downloaded
it, 2026-09-26 now).

**Licence.** The BCSEE export that `pull_bcsee.py` downloads
(`SummaryExportAll.xlsx`) is under the Open Government Licence – British
Columbia (version 2.0). Evidence, checked 2026-10-15: BCSEE's export page,
https://a100.gov.bc.ca/pub/eswp/jsp/export.jsp, links "Summary Export All"
and says "Note: The data contained in the Results Export in BCSEE are
provided under the Open Government License – BC"; the file's own Metadata
sheet says "Open Government License– BC" ("Generated May 5 2026"). The BC
Data Catalogue record "BC Species and Ecosystems Explorer" is marked "Access
Only", which appears to be about the BCSEE application rather than the
exported data. As the licence asks, the site shows its attribution statement,
"Contains information licensed under the Open Government Licence – British
Columbia.", linked to the licence
(https://www2.gov.bc.ca/gov/content/data/policy-standards/data-policies/open-data/open-government-licence-bc),
in the page footer and in the Sources section of every species card.

The BCSEE match and the merge list must use the same Catalogue of Life
release. Both use 315557 now, but nothing records which release the match
used, so this is not checked yet: rerun `match_bcsee_gbif.py` and
`make_species_key_merge.py` together whenever the GBIF download moves to a
new release.

## Validation

The clean product holds **38,739,753 records** and **38,218 distinct
species**. The record count matches the earlier direct-from-S3 count in
`backend/exploration/gbif_bc_filtered.py`, so the two-step raw-then-clean path gives
the same answer as filtering in one pass.

Compared species by species against Evan's `for_lucia.csv` (36,156 species):
about half of the shared species match exactly, and roughly 70% are within
10%. The main known difference is that this pipeline does **not yet apply
Evan's `countrycode` filter**, which makes our counts run slightly higher.

These figures predate a fix to the coordinate-issue filter in October 2026.
GBIF stores a record with no issue flags at all as `issue = NULL`, and the
old filter dropped those records (60,385 rows in `bc_raw`). The filter now
keeps them, so a rebuilt clean product will hold somewhat more records.

## Layout

The Python moved under `backend/` in September 2026. **Run the pipeline
scripts from inside `backend/`, not from the repo root.** Several of them take
default paths like `data/...` that are relative to the working directory, so
running them from anywhere else quietly writes to the wrong place.

| Folder | Contents |
| --- | --- |
| `backend/pipeline/` | The production pipeline, one folder per data source (below). These are what runs to produce the dashboard's data. Run them from `backend/`, e.g. `python pipeline/gbif/build_dwca_tables.py`. A script that needs another folder's code adds that folder to its import path itself. |
| `backend/pipeline/gbif/` | GBIF occurrence data. `request_gbif_download.py` requests a GBIF Darwin Core Archive download inside `bc_boundary_gbif.wkt` and downloads the zip. `build_dwca_tables.py` builds the occurrence, media, dataset and species image tables from that download, using the filter in `build_bc_clean.py` (described above); the species image is picked by `build_species_image.py`, which it calls and which can also run on its own from those tables. `make_species_key_merge.py` decides which duplicate species keys to merge, `build_species_tables.py` builds `species_hex` and the species cards with that merge applied, and `build_hex_aggregates.py` builds the map's hexagon tables from `species_hex`. `build_species_summary.py` (Layer 3) builds the species summary from `bc_occurrence.parquet`. `build_bc_clean.py` (Layer 2) still holds the filter and the boundary code that `build_dwca_tables.py` and `make_gbif_boundary.py` import. `make_gbif_boundary.py` writes BC's boundary as `bc_boundary_gbif.wkt`, a small polygon (266 points) to paste into a GBIF download filter: it takes the same ABMS boundary as `build_bc_clean.py`, pushes it out 2 km and simplifies it, so it still covers all of BC (about 1.2% extra area along the edges, which `build_bc_clean.py` clips away), and writes the points counter-clockwise. `request_gbif_download.py` imports its checklist key from `../bcsee/match_bcsee_gbif.py`. |
| `backend/pipeline/gbif/legacy/` | `download_bc_raw.py` (Layer 1), which copied BC's bounding box out of GBIF's S3 snapshot. Replaced by `request_gbif_download.py` and no longer used; kept until the Darwin Core Archive path has fully replaced it. |
| `backend/pipeline/bcsee/` | The BC Species and Ecosystems Explorer (BCSEE). `pull_bcsee.py` downloads the province's "Summary Export All" copy of the BCSEE list and writes `bcsee_status.parquet`, the current conservation status of every species and ecological community; `match_bcsee_gbif.py` matches every BCSEE species name to its GBIF species key using GBIF's name matching service and writes `bcsee_gbif_match.parquet`. It needs `bcsee_status.parquet` from `pull_bcsee.py` first, and its rows should be joined to GBIF observations only where `join_ok` is true; `build_bcsee_status_display.py` gathers every BCSEE entry that points to the same GBIF species (with a Red, Blue, Yellow or Exotic status, or in BCSEE without one) into `bcsee_status_by_gbif_species.parquet` (plus a CSV copy), one row per GBIF species with a list of status entries for the front end. It needs `bcsee_gbif_match.parquet` from `match_bcsee_gbif.py` first, applies the hand-checked keys in `bcsee_gbif_overrides.csv` if that file exists, adds the BCSEE entries listed in `bcsee_extra_keys.csv` (next to the script) to their extra keys, and with `--key-merge` moves statuses from merged-away species keys to the kept key (see "BCSEE statuses on the species cards" below); `build_bcsee_history.py` turns BCSEE's yearly archives on the BC Data Catalogue into `bcsee_history.parquet`, one row per species or community per year from 2011. |
| `backend/pipeline/lunaris/` | Lunaris dataset metadata. `lunaris_harvest_only.py` harvests dataset metadata over OAI-PMH, `lunaris_keywords.py` is the finalized biodiversity keyword filter, `build_lunaris_candidates.py` applies it to the harvest, and `build_lunaris_taxa.py` adds the taxon columns (both described below). |
| `backend/api/` | The species search API the front end calls (see "Running the species API" below). `settings.py` reads the data folder and the (not yet implemented) sensitive-species setting from environment variables, `db.py` checks and opens the data files with DuckDB, `species.py` builds the species card, `search.py` runs species search, `hexes.py` builds the species map, `images.py` decides whether an image can be shown with full credit, and `main.py` is the FastAPI app. |
| `backend/shared/` | Code used by both the API and the pipeline. `image_credit.py` is the one definition of when a species image can be shown (image URL, link to the original, creator, readable licence), in Python for the API and as SQL for `build_species_image.py`. |
| `backend/tests/` | Tests for the API, `shared/`, and the pipeline steps behind the species cards (`build_bcsee_status_display.py`, `build_species_image.py`). `conftest.py` writes a tiny copy of the API's data files, so the tests never need the real data. |
| `backend/tools/` | Small helper scripts. `list_gbif_columns.py` prints a snapshot's column names and types straight from the parquet schema on S3 (schema only, no scan). `read_parquet.py` dumps a parquet file to CSV for eyeballing. |
| `backend/exploration/` | Analysis and validation, not part of the product build. `gbif_bc_filtered.py` counts what survives the filters straight from S3; `gbif_bc_drops.py` is its diagnostic companion, attributing drops to each individual filter (note: those per-filter counts overlap and must not be summed). |
| `backend/exploration/lunaris/` | Tuning and validation for the Lunaris keyword filter, not part of the product build. All of these import `backend/pipeline/lunaris/lunaris_keywords.py`. `lunaris_check_false_positives.py` is the evidence check to run before changing the filter; `lunaris_analyze.py` reports word frequencies and kept/dropped counts; `lunaris_check_missed.py` looks for biodiversity datasets the filter drops; `lunaris_sample_for_review.py` draws a mixed sample to hand-review and `lunaris_semantic_review.py` scores that sample's labels against the filter. |
| `backend/exploration/llm/` | The trial that measures the keyword filter against a model, not part of the product build. `build_test_set.py` draws the labelling sample, `fix_stratum.py` repairs the labelled sheet, `test_key.py` checks the API key, `run_trial.py` runs the model and caches its answers, `check_stability.py` tests reproducibility and `cost_estimate.py` projects the cost of a full pass (described below). |
| `backend/exploration/bcdc/` | Survey of the BC Data Catalogue, not part of the product build. `survey_bcdc.py` downloads the whole BC Data Catalogue once and sorts every package by licence and by whether it has anything to download. |
| `backend/exploration/bcsee/` | Finding the BCSEE and Conservation Data Centre sources, not part of the product build. `find_cdc.py` finds Conservation Data Centre and BCSEE packages in that cached catalogue; `probe_bcsee.py` is an earlier keyword search of the live catalogue for BCSEE packages. |
| `backend/exploration/images/` | Checking the species card images, not part of the product build. `count_passing_images.py` (read-only, runs on the server's DuckDB 0.10.3) counts how many species would have a card photo if the image were picked only among images with a URL, a link to the original, a creator and a readable licence, what a rights-holder or GBIF-record-page fallback would add, and where the species with only failing images get their images from. |
| `backend/exploration/archive/` | Superseded early attempts, kept for history: `gbif_bc_boundingBox.py` (bounding box only, the first feasibility check) and `gbif_bc_polygon.py` (first exact-polygon version). Not maintained - read them for context, don't run them. |

## The Lunaris keyword filter

A second data source: dataset-level metadata harvested from
[Lunaris](https://lunaris.ca/) (123,479 records), filtered down to the ones
actually about biodiversity. `backend/pipeline/lunaris/lunaris_keywords.py` is the single
source of truth for that filter and runs in three stages:

1. **`mask_false_positives()`** blanks out phrases where a keyword is not
   being used biologically, *before* matching. Each of the 8 patterns has a
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
| + StatCan `percentage of plants` mask | 17,057 |
| + `forest`, `marine`, `freshwater` and the random-forest mask | **18,931** |

The last row is the current kept count, and it is what
`backend/data/lunaris_biodiv_candidates.parquet` holds.

**Never change the keyword list or the masks without running
`backend/exploration/lunaris/lunaris_check_false_positives.py` first.** A mask is only
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

## The Lunaris taxon extraction

The species-name pass the keyword filter defers to. Two production steps run
after the harvest:

```
lunaris_full_harvest.parquet -> lunaris_biodiv_candidates.parquet -> lunaris_taxa.parquet
       (123,479 records)              (18,931 candidates)              (+ 7 taxon columns)
```

**`backend/pipeline/lunaris/build_lunaris_candidates.py` -> `backend/data/lunaris_biodiv_candidates.parquet`**
Applies `lunaris_keywords.py` to the full harvest and writes the records that
pass, plus a `matched_keywords` column recording which keywords fired.

```
cd backend
python pipeline/lunaris/build_lunaris_candidates.py
```

**`backend/pipeline/lunaris/build_lunaris_taxa.py` -> `backend/data/lunaris_taxa.parquet`**
This is **extraction, not filtering**: all 18,931 candidates come out again,
with seven columns added saying which taxon names each record mentions. Names
come from the Layer-3 species summary (`bc_species_summary.csv`), matched
case-sensitively against the original text, because capitals are what separate
the genus *Beta* from "beta diversity".

```
cd backend
python pipeline/lunaris/build_lunaris_taxa.py
```

| Column | What it holds |
| --- | --- |
| `species_found` | Full binomials from the BC species list |
| `species_off_list` | Binomials whose genus is on the list but the species is not |
| `genus_qualified` | A genus name next to a qualifier, e.g. `Salmo sp.` |
| `family_found` | Family names |
| `higher_taxa_found` | Order, class, phylum, kingdom |
| `genus_bare` | A genus name on its own — **not counted as a find** |
| `common_name_found` | Common names, mined from this corpus (below) |

`genus_bare` is written out because it is occasionally useful, but a record
with nothing else is treated as having matched nothing. Its commonest entries
are *Beta*, *Argentina*, *Pan*, *Cancer* and *Barbara* — ordinary words that
happen to also be genus names. The "matched nothing" figures the script prints
are reported both ways so the difference stays visible.

### The common-name map

`backend/data/lunaris_common_name_map.csv` (2,456 rows) is built by the same script and
holds `common_name, scientific_name, times_seen, example`. It is mined from the
corpus itself: wherever the text reads `common name (Scientific name)` or the
reverse, the pair is recorded, with the sentence it came from kept in `example`
so any entry can be checked.

**The map has two uses with opposite thresholds.** Tagging records wants
`times_seen >= 2`, because a pair seen once is usually a mining accident.
Dashboard search synonyms want everything, because a synonym used once is still
a synonym someone might type. So **the map keeps every pair and the threshold is
applied at use time** — `common_name_tier()` filters to `times_seen >= 2`,
search should not.

The map holds 2,419 distinct common names across its 2,456 pairs. **610** of them
were seen at least twice, and **577** of those are enabled for tagging. The
remaining 33 are one-word names that did not survive the checks below, plus the
handful dropped outright. Multi-word names are taken
as they come; one-word names are allowlisted individually, because hand-judging
showed a one-word name is **3.5x** more likely to be wrong than a multi-word one
("Trout Lake" is a place, not a fish). A short `NOT_ORGANISM_NAMES` list drops
entries that name a place or a way of life rather than a creature; those leave
the map as well, since they are no use to search either.

`coffee` and `potato` are currently in `DISABLED_PENDING_SCOPE`, switched off
while it is undecided whether crop records belong in the dashboard. They stay in
the map, because a crop name is still a valid search synonym. **To turn them back
on, empty that set** in `backend/pipeline/lunaris/build_lunaris_taxa.py` and re-run.

### How far the common-name tier has been checked

Every record rescued by `common_name_found` alone — i.e. where no scientific-name
tier fired — has been read and judged by hand: 1,102 records across two rounds,
of which **1,063** survive the current rules. Each was called RIGHT (the record
really is about that organism), WRONG MATCH (a matching bug) or SCOPE (right
organism, but the record is not biodiversity data).

Current matching precision is **95.8%**: 45 of 1,063 are wrong matches.

The verdicts are kept at **`Results/Claude outputs/tier7_verdicts.csv`** and are
reusable — any proposed new rule can be scored against them for how many wrong
matches it removes and how many right ones it destroys, instead of being argued
about. Rules that sounded sensible and scored zero have been rejected on that
basis.

**Outputs of the taxon step**

| File | What it is |
| --- | --- |
| `backend/data/lunaris_taxa.parquet` | All 18,931 candidates with the seven taxon columns |
| `backend/data/lunaris_common_name_map.csv` | The mined common-name map, 2,456 rows |
| `backend/data/lunaris_no_taxon.csv` | The records that matched nothing, for review |

## The LLM check on the keyword filter

`backend/exploration/llm/` is a trial that measures the Lunaris keyword
filter against a model. Until now the filter has only ever been checked
against itself — every review sample was drawn from the records it keeps —
so recall stayed untested. The trial labels both sides of the split instead.

The scripts, in the order they are used:

**`build_test_set.py`** samples 900 records and writes a CSV for a human to
label: 500 drawn at random from the 18,931 the keyword filter kept, and 400
from the 104,548 it dropped. The dropped half is what makes recall
measurable.

**`fix_stratum.py`** repairs the stratum column in the labelled sheet and
joins the filter 2 tier columns back on by record id, adding `filter2_found`
and `filter2_organism`.

**`test_key.py`** sends one small request to check the API key and the model
name before a long run.

**`run_trial.py`** asks the model about each record and caches every answer
to disk.

**`check_stability.py`** asks the same records repeatedly to see whether the
answers are reproducible.

**`cost_estimate.py`** counts tokens and projects the cost of a full pass.

**The model is never asked whether a record is biodiversity data.**
`run_trial.py` asks only for facts: whether the record concerns living
things, which organisms it names and at what level (species, group or none),
what the record is mainly about, and what form it takes. The yes-or-no
decision is applied afterwards in code, so changing the criteria does not
mean paying to run everything again.

**Every answer is cached in its own file**, keyed by record id, model name
and `PROMPT_VERSION`, so an interrupted run resumes at no cost. **Changing
the prompt or the schema requires bumping `PROMPT_VERSION`** — otherwise the
old answers are silently reused and the change has no effect. That is the
easiest mistake to make here.

```
cd backend
python exploration/llm/test_key.py
python exploration/llm/run_trial.py --dry-run --limit 2
python exploration/llm/run_trial.py --model gemini-3.5-flash-lite
```

The key goes in `backend/.env` as `GEMINI_API_KEY`, which is gitignored.
Requires `google-genai` and `python-dotenv`.

The trial runs inside the Gemini free tier, which allows 500 requests a day
per model, so 900 records takes two days and costs nothing. A full pass over
all 123,479 harvested records would need billing. The response cache is
gitignored, because it is regenerable at no cost.

## Running environment

The pipeline runs on the **ZCU cluster**. DuckDB is pinned there to **0.10.3**
for glibc compatibility, and the scripts are written to that version's limits:
no named (`$name`) parameters, and the BC boundary is stored as WKT text
rather than a `GEOMETRY` column, because 0.10.3 reads a `GEOMETRY` written to
parquet back as an unusable BLOB. Keep new code inside those constraints, or
the cluster run will fail even though it works locally.

DuckDB's h3 extension only exists from DuckDB 1.0 on, so
`build_dwca_tables.py` computes H3 cells with the `h3` Python package
instead. It wraps the same H3 library, and the cells came out identical on
the test download. `build_species_tables.py` and `build_hex_aggregates.py`
only group by those stored cells, so they need neither the extension nor the
package.

Python dependencies: `duckdb`, `geopandas`, `shapely`, `requests`, `pandas`, `openpyxl`, `pyarrow`, `h3`.

## Running the species API

Species search and the species cards come from a small API in `backend/api/`
(FastAPI and DuckDB). It reads four files, copied by hand from the server
and the BCSEE build into `backend/data/api/` (git-ignored like the rest of
`backend/data/`):

| File | Comes from |
| --- | --- |
| `species_card_gbif.parquet`, `species_hex.parquet` | `~/bcbn/data/gbif_tables/species_merged/` on the server (`build_species_tables.py`) |
| `bcsee_status_by_gbif_species.parquet` | `build_bcsee_status_display.py --key-merge` |
| `download_info.json` | `~/bcbn/data/gbif_tables/` on the server (`build_dwca_tables.py`); the GBIF download DOI to cite |

```
pip install -r backend/requirements-api.txt
cd backend
uvicorn api.main:app --reload --port 8000
python -m pytest
```

Two environment variables change its behaviour:

- `BCBN_API_DATA_DIR`: the folder with the four files, if not
  `backend/data/api/`.
- `BCBN_SENSITIVE_MAX_RES`: reserved for coarsening sensitive species'
  locations, which is not implemented: which species, how coarse, and what
  happens to the card's extent box and photo link are not decided yet. It
  must be left empty. Any value stops the API at start-up with a message
  saying so, so nobody thinks species are protected when they are not.
  `/api/meta` reports it (always null for now).

At start-up the API checks that every file is there with the columns it
reads, that the cards have one Catalogue of Life release and the statuses
one BCSEE source and date, and refuses to start if not, listing every
problem.

`GET /api/meta` returns where the data comes from and what it holds: the
Catalogue of Life release, the BCSEE source and dates, the GBIF download,
species and record totals, the year range, the map resolutions and the
sensitive-species setting.

`GET /api/species/{key}` returns one species card (`backend/api/species.py`):
the Catalogue of Life name and authorship with `name_source` and
`col_release`, the taxonomy, record counts, the extent box, the image, the
top datasets and the BCSEE statuses. An old key that was merged gives the
kept key's card with `merged_from` set; any other key without a card,
including a species BCSEE lists but with no records in BC, gives 404.
`name.merged_names` lists each key merged in under another Catalogue of Life
name (a synonym merge, such as Accipiter cooperii into Astur cooperii) as
`key`, `col_name` and `col_authorship`, from the card's `merged_names`
column; a name that differs only by a subgenus in brackets is the same name
and is left out, so it is empty for every same-name merge.

- Every BCSEE status entry, and the BCSEE block itself, carries
  `status_source`, `status_source_date` and `status_pulled_on`. The English
  name is the species-level BCSEE common name, with `english_name_source`.
  The block's `state` is `listed`, `in_bcsee_no_status`, `not_in_bcsee` or
  `unknown`. `unknown` only comes from a BCSEE file built before
  `build_bcsee_status_display.py` kept species without a shown status (it
  then cannot tell the two cases apart); `/api/meta` says which kind of
  file it has (`includes_species_without_status`), and counts
  `species_in_bcsee` and `species_with_status` (Red, Blue, Yellow or Exotic
  only) separately.
- An image is returned only with its URL, a link to the original, a
  creator and a readable licence (code, label and link); otherwise `image`
  is null. The check is `backend/shared/image_credit.py`, the same one
  `build_species_image.py` uses to pick the image, so a picked image is
  always shown. Images are always shown unchanged
  apart from resizing (iNaturalist photos use the medium size), with
  creator, licence and a link to the original. ND images are flagged with
  `no_derivatives` and are never cropped.
- The extent box is the card's own; no species' locations are coarsened
  (see `BCBN_SENSITIVE_MAX_RES` above).

`GET /api/search?q=&limit=` finds species cards (`backend/api/search.py`),
best first, up to `limit` (default 20, at most 50). It searches the card's
Catalogue of Life name (also without its subgenus, so "Culex pipiens" finds
"Culex (Culex) pipiens"), the BCSEE English name of the species, the English
names of its other BCSEE entries, the BCSEE scientific labels, the name of a
key merged in under another name (`merged_name`, as in `name.merged_names`
on the card, so "Accipiter cooperii" finds Astur cooperii), and an exact
species key in any letter case (an old merged key gives the kept card with
`merged_from`). Names and query are compared case-insensitively, without
accents, apostrophes or punctuation, and with a hybrid sign or a lone "x"
ignored ("Dahlia pinnata" finds "Dahlia × pinnata"). There is no typo
tolerance. A typed key comes first, then names equal to the query, then names
with a word starting with it, then names holding it anywhere; ties go to the
species with more records, and each species appears once. Each result says
which name matched (`match`) and lists its names in order (`display_names`:
the card name, the English name, then the merged name or BCSEE name that
matched), each with its source; an English name that belongs to one BCSEE entry rather than the
whole species is marked `bcsee_entry_english_name`. Species BCSEE lists but
with no records in BC have no card and are never found. A query with fewer
than 2 letters or digits gives 400.

`GET /api/species/{key}/hexes?res=&year_from=&year_to=` gives the species map
(`backend/api/hexes.py`): the records of one species per H3 hexagon at `res`
(required, 4 to 7), from `species_hex.parquet`, counted by specieskey after the
merge (an old merged key gives the kept key's map with `merged_from`). The
hexagons come as two lists of the same length, `cells` and `occurrences`.
With no year filter every record is counted, including those with no year, so
the counts add up to the card's `record_count` at every resolution. With
`year_from` and/or `year_to` (inclusive; a missing bound is the first or last
year in the data), only records with a year in the range are counted;
`records_no_year_left_out` and `records_outside_years` say how many were left
out, and `records_shown` + those two = `records_total`. The years allowed are
those of the whole dataset (`/api/meta` `first_year` to `last_year`), so one
year control works for every species. `hexagon_count` and `records_shown`
give the summary. A `res` that is not stored, a year outside the data, or
`year_from` after `year_to` gives 400; an unknown key, or a species BCSEE
lists but with no records in BC, gives 404. Colour bins are left to the front
end. Responses over 1 KB are gzipped for browsers that accept it: the robin's
map at res 7 is 355 KB, 63 KB gzipped.

`GET /api/overview/hexes?res=&year_from=&year_to=` gives the overview map
(`backend/api/overview.py`): for every hexagon at `res`, the records
(`occurrences`) and the distinct species (`distinct_species`, by specieskey
after the merge), as three lists of the same length with `cells`. It follows
the species map's rules exactly: `res` is required (4 to 7), the years allowed
are the whole dataset's, with no years every record counts (including those
with no year), and with years records with no year are left out and counted in
`records_no_year_left_out`, with `records_outside_years`, `records_shown` and
`records_total` as above. The one record with no specieskey counts in records
but not in species. With no year filter the answer is exactly the shipped
`frontend/public/data/bc_hex_r4..r7.csv.gz`, hexagon for hexagon;
`backend/tests/test_overview_matches_files.py` checks this against the real
data (it is skipped where `backend/data/api` is missing).

Each request reads the whole of `species_hex` (it is sorted by species, not
hexagon). Measured on the Mac, through HTTP with gzip, best of three:

| res | all years | 2000 to 2005 | 2000 to 2020 |
| --- | --- | --- | --- |
| 4 | 0.08 s, 5 KB | 0.05 s, 4 KB | 0.07 s, 5 KB |
| 5 | 0.10 s, 28 KB | 0.06 s, 13 KB | 0.08 s, 22 KB |
| 6 | 0.16 s, 110 KB | 0.08 s, 33 KB | 0.13 s, 77 KB |
| 7 | 0.36 s, 351 KB | 0.10 s, 63 KB | 0.24 s, 211 KB |

The same SQL on DuckDB 0.10.3 (the cluster's version) gives the same answers
in 0.08 to 0.27 s once the file has been read once. So no precomputed table is
needed. The page still uses the shipped files for all years and calls this
endpoint only when a year range is set (see "Running the front end").

## Running the front end

The map lives in `frontend/`. It is a Vite + React app that draws the hexagon
aggregates over an OpenFreeMap basemap with deck.gl, with a species search box
that calls the species API.

Two terminals, the API first:

```
# terminal 1: the species API (see "Running the species API")
cd backend
../.venv/bin/python -m uvicorn api.main:app --reload --port 8000

# terminal 2: the front end
cd frontend
npm install        # only the first time, or after package.json changes
npm run dev
```

Then open `http://localhost:5173`. The dev server passes every request for
`/api` on to the API at `http://127.0.0.1:8000` (`frontend/vite.config.js`; set
`BCBN_API_DEV_URL` for another address). `npm run build` writes a production
bundle to `frontend/dist/`, and `npm run preview` serves that build. The built
site calls the API at `/api` on its own address; where the API will run in
production is not decided yet, so that one setting is in `frontend/src/api.js`
(`VITE_API_BASE` when building).

The overview map reads `frontend/public/data/bc_hex_r4.csv.gz` to
`bc_hex_r7.csv.gz`, one per zoom tier, which are produced on the server by
`backend/pipeline/gbif/build_hex_aggregates.py`, copied to the Mac by hand and
committed. They are plain files, so the overview map works with no API behind
it; only the species search and the year filter need the API, and say so if
they cannot reach it.

While a species card is open, the map shows that species' own hexagons
instead of the overview (`GET /api/species/{key}/hexes?res=N`). It uses the same zoom tiers as the overview (resolution 4 to 7,
`frontend/src/zoomTiers.js`). Each resolution is fetched the first time its
tier is reached and then kept, so zooming back costs nothing. An answer that
arrives after the user has moved on is kept but not shown. When a species is
picked, the map moves to fit its hexagons beside the card, no closer than zoom
8. Closing the card brings back the overview at the current view. If the
species map cannot load, the legend says so and the card still works. The
legend gives the species name, the colours, the records and hexagons at the
current resolution ("964,801 records in 17,412 hexagons"), and the note that
the maps show recording effort. The hover box gives the number of records of
that species in the hexagon.

One year control, under the scale toggle (`frontend/src/YearFilter.jsx`),
applies to whichever map is shown. It starts on all years, so the site looks
as it always has until it is changed. It is two year boxes and an "All years"
button: typing is the main way in, because the data runs from 1784 but almost
all of it is from after 2000, so a slider would make the useful years hard to
hit. The boxes are applied on Enter or when the control is left, not on every
key; an empty box is an open end; a year outside the data or a backwards
range gets a message instead of a request. With a range set, the overview
comes from `GET /api/overview/hexes` instead of the shipped files, and the
species map is asked for that range. Answers are kept per map, range and
resolution, so going back to a range costs nothing, and a late answer for an
earlier range is never shown. While a new range loads, the previous map stays
up with "Loading hexagons...". The legend then gives the range and the totals
("467,303 records in 522 hexagons, 2000 to 2020") and how many records with no
date were left out. The range stays when a species card is opened or closed.
The colour bins stay fixed, so maps for different years can be compared.

A note under the year boxes says the most recent years are incomplete,
because records reach GBIF late. Which years it names is set in
`frontend/src/yearRange.js` (`INCOMPLETE_FROM_YEAR`, now 2025), from the
records per year in the download of 4 October 2026: 3.92 million in 2023,
4.18 million in 2024, then 0.68 million in 2025 and 0.48 million in 2026.
Recheck it after every new GBIF download.

All three maps use fixed log-scale bins (`frontend/src/colours.js`). Each bin
starts at the number shown in the legend, and the last bin has no upper limit:

| Map | Bins start at | Colours |
| --- | --- | --- |
| Overview, species | 1, 3, 10, 30, 100, 300, 1K, 3K+ | 8 blues |
| Overview, records | 1, 10, 100, 1K, 10K, 100K, 1M+ | 7 oranges |
| One species | 1, 3, 10, 30, 100, 300, 1K, 3K+ | 8 pinks to purples |

The blue and orange ramps are the earlier 9-step ramps, resampled into 8 and
7 evenly spaced steps (by CIEDE2000 colour difference), so neighbouring bins
stay about equally far apart.

All three maps draw hexagons nearly solid, like the Biodiversité Québec Atlas:
opacity rises from 0.85 in the first bin to 0.95 in the last. With the white
outlines below keeping hexagons apart, near-solid fills make each colour look
the way the legend shows it, and every colour comparison came out better than
at the earlier 0.55 to 0.82. Place names are drawn above the hexagons, so
they stay readable; the terrain and lakes under the hexagons do not show.

The species map goes from saturated pink through magenta and purple to a very
dark purple, in the style of the Biodiversité Québec Atlas "Number of
species" map. It was chosen after comparing single-hue ramps and established
palettes (viridis, inferno, ColorBrewer YlOrRd and YlGnBu). It never looks
like either overview, and its darkest step stops short of black so it still
reads as a colour. Its first bin is deliberately strong, a saturated pink
rather than a pale tint, because most species are rare: the 1-to-2-record bin
is the colour most species maps are mostly made of, and a lone hexagon of a
rare species has to be easy to find on forest, parks and water.

Every hexagon on all three maps has a 1-pixel white outline, so each one
stands apart from its neighbours. The width is in screen pixels, so it stays
thin at every zoom. Each zoom tier starts with hexagons about 30 pixels
across, so the line is always a thin edge. Only when zooming out past the
opening view, where hexagons shrink below 20 pixels, does it fade out, and it
is gone at 8 pixels, so the far-out views do not turn into a white mesh.

The legend draws each swatch with its bin's opacity over the basemap's land
colour, with a thin white line between swatches like the outlines, so it
matches the map.

The map code is split by job: `HexMap.jsx` (the map, its layers and moving to
a species), `Legend.jsx`, `colours.js` (bins and colours), `zoomTiers.js`
(which resolution each zoom shows), `speciesHexes.js` (fetching and keeping
the species map and the overview for a year range), `YearFilter.jsx` and
`yearRange.js` (the year control and its rules) and `hexTooltip.js` (the
hover boxes). `App.jsx` loads the overview files and holds which species is
open and which years are shown.

The search box (`frontend/src/SearchBox.jsx`) searches a quarter of a second
after the last key press, and only once there are at least 2 letters or
digits, the API's own rule. The arrow keys move through the results, Enter
picks one and Escape closes the list. Each result shows the scientific name
first, with its authorship, then the BCSEE English name (labelled as BCSEE),
then any BCSEE entry name that matched (labelled as an entry name, never as the
species' English name), then the family and record count, which tell apart two
cards with the same scientific name. No conservation status is shown in the
list. Picking a species opens its card.

The species card (`frontend/src/SpeciesCard.jsx`, from `GET /api/species/{key}`)
shows, top to bottom:

- the Catalogue of Life name exactly as stored (a subgenus in brackets
  included) in italics, with its authorship exactly as in the Catalogue of
  Life file and a "?" that explains it, using the card's own authorship and
  name, with a sentence on what parentheses mean only when the authorship has
  them (cards with no authorship, 454 of 38,992, show no "?"); under it,
  for a key merged in under another name, "Previously known as:" with that
  name and authorship (placeholder wording, to be decided); the
  classification from kingdom to genus; and the common name labelled "Common name from
  BCSEE". When BCSEE lists separate species on the key, no common name is
  shown here; each BCSEE entry shows its own.
- the photo, only when the API sends one, always whole (never cropped, as ND
  licences require), with creator, the copyright holder for CC BY licences,
  the licence link and a link to the original; otherwise "No fully credited
  photo available".
- the BC conservation status: each BCSEE entry as "BCSEE: …" (a chip that
  opens BCSEE's own definition of the value on hover, keyboard focus or a
  tap, from `frontend/src/bcseeDefinitions.js`) with its scientific name and
  common name. What the statuses apply to (the whole species, a subspecies,
  variety or population, or a separate BCSEE species, using BCSEE's own
  `classification_level`) is said once at the top when every entry is the
  same, and under each entry when they differ. "Not in BCSEE" has its own
  message.
- the records: count, years, how many have no date, datasets and the
  largest datasets (linked to GBIF), and how many came from merged keys and
  why each was merged: the same species name, or a name the Catalogue of
  Life lists as a synonym of this species (placeholder wording). A card
  opened from a typed old key says the same.
- a Sources section: the Catalogue of Life release with its official
  citation and licence, where the classification comes from, BCSEE cited the
  way the Conservation Data Centre asks, the GBIF download cited the way GBIF
  asks, and the species key (`frontend/src/sources.js`).

It does not show the extent box (waiting for the sensitive-species decision).
A footer below the map on every page (`frontend/src/SiteFooter.jsx`) says
where the data comes from, that BCBN does not set conservation statuses, and
that the sources do not endorse the site.

**Front-end tests.** `npm test` (in `frontend/`) runs the tests once with
Vitest 4.1, React Testing Library 16.3 and jsdom 26.1, all dev dependencies, so
none of it goes into the built site. They use fake API answers, so the API does
not need to be running. They live next to the code as `src/*.test.js(x)`:
for example `api.test.js` (the letter-and-digit count, checked against the
API's own rule, and how failures are reported) and `SearchBox.test.jsx` (the
250 ms wait, the 2-character minimum, stale answers, keyboard use, labels and
messages).

**Adding npm packages: use npm 11 for the lock file.** npm 10.9 stops with
"Cannot read properties of null (reading 'edgesOut')" when a package is added,
because of a peer version conflict inside deck.gl's own packages
(`@loaders.gl/core`). Do not use `--legacy-peer-deps` to get around it: it
removes `@deck.gl/layers` and other packages the map needs. Instead, let npm 11
write `package.json` and the lock file, then install from them as usual:

```
cd frontend
npx -y npm@11 install --package-lock-only --save-dev <package>@<version>
npm ci
```

This Mac now runs Node 26.12 and npm 11.20 (npm 11 is the version the steps
above call for), which also clears the EBADENGINE warning Vitest gave under
Node 23.3.

**maplibre-gl is pinned to 5.x on purpose.** Version 6 is incompatible with
`react-map-gl` 8.1.3: the basemap style never finishes loading, so no tiles are
requested and the map stays blank with no error in the console. deck.gl's
interleaved renderer also fails against 6.x, reading a `_nearZ` property that
version no longer exposes. Do not upgrade maplibre-gl past 5.x without checking
that tiles still draw.

## Data files

**Data lives outside git.** The parquet products are tens of millions of rows
and are regenerated by the pipeline, so `data/` and `*.parquet` are
gitignored. On the cluster they live under `~/bcbn/data/`. Never commit them.
The Layer-3 species summary CSV is the exception - it's small enough to share.

The Lunaris products follow the same rule: `lunaris_full_harvest.parquet`,
`lunaris_biodiv_candidates.parquet`, `lunaris_taxa.parquet` and the 17 MB
`lunaris_no_taxon.csv` are all regenerated and stay out of git.
`lunaris_common_name_map.csv` is 330 KB, so it can be shared the same way the
species summary is.
