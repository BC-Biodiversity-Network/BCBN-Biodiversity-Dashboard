"""
Build the BC Species and Ecosystems Explorer reference tables.

BCSEE is the province's record of conservation status for every species and
ecological community it tracks. Unlike GBIF it is not observation data, so
nothing here is filtered for relevance: every row is in scope by definition.

The province has no bulk export of the live Explorer. What it does publish
is a compilation, where each year the Explorer's contents are exported and
appended to a file. Species and communities come as two separate files with
different column vocabularies.

That shape suits "how did this status change over time" and not "what is the
status of this species", so this writes two tables rather than one:

    bcsee_current.parquet    one row per species or community, latest year,
                             species and communities in one shape
    bcsee_history.parquet    one row per entity per year, only the fields
                             that change, for showing a trend

Like the rest of the pipeline this rebuilds its outputs from scratch every
run, so running it twice is the same as running it once.

Usage:

    python build_bcsee.py
    python build_bcsee.py --data-dir ~/bcbn/data --refresh
"""

import argparse
import os
import sys
from pathlib import Path

import duckdb
import pandas as pd
import requests


# The two published files. Both sit under one catalogue record, licensed
# Open Government Licence - British Columbia.
CATALOGUE = ("https://catalogue.data.gov.bc.ca/dataset/"
             "d3651b8c-f560-48f7-a34e-26b0afc77d84/resource")
SOURCES = {
    "plants_animals": (f"{CATALOGUE}/39aa3eb8-da10-49c5-8230-a3b5fd0006a9/"
                       "download/cdc_bcsee_history_plants_animals.csv"),
    "communities": (f"{CATALOGUE}/bbacd6fb-6708-4cf8-b353-0dc5ef75b7b9/"
                    "download/cdc_bcsee_history_eco_communities.csv"),
}
HEADERS = {"User-Agent": "BCBN-Dashboard (UBC Biodiversity Research Centre)"}

# One shape for both kinds. A field one kind has and the other does not is
# left empty rather than dropped, so a caller never has to know which of the
# two source files a row came from.
COLUMNS = [
    "bcsee_id", "kind", "scientific_name", "english_name",
    "scientific_synonyms", "english_synonyms",
    "rank_level", "category", "group_name",
    "kingdom", "phylum", "class", "order", "family",
    "global_status", "prov_status", "bc_list",
    "cosewic", "sara", "sara_status", "cites", "mbca", "frpa",
    "origin", "presence", "endemic", "has_cdc_map",
    "bec_units", "species_code",
    "status_review_date", "status_change_date",
    "snapshot_year", "source",
]

HISTORY_COLUMNS = ["bcsee_id", "kind", "scientific_name", "snapshot_year",
                   "prov_status", "global_status", "bc_list"]


def download(url, path):
    """
    Fetch one published file, unless it is already on disk.

    The catalogue warns that these download links sometimes serve a web page
    instead of the data, so the first bytes are checked. A page starts with
    an angle bracket, and catching that here turns a confusing parse error
    into a clear message.
    """
    if path.exists():
        print(f"  {path.name} already here ({path.stat().st_size / 1e6:.1f} MB), "
              f"use --refresh to fetch again")
        return True
    print(f"  fetching {path.name}")
    r = requests.get(url, headers=HEADERS, timeout=600, stream=True)
    r.raise_for_status()
    first = b""
    with open(path, "wb") as fh:
        for chunk in r.iter_content(chunk_size=1 << 16):
            if not first:
                first = chunk[:64]
            fh.write(chunk)
    if first.lstrip()[:1] == b"<":
        print(f"    got a web page rather than a CSV, see the catalogue note "
              f"about this download bug")
        path.unlink()
        return False
    print(f"    {path.stat().st_size / 1e6:.1f} MB")
    return True


def read_published(path):
    """
    Read one published file and drop the notes stuck on the end of it.

    Two things make a plain read wrong. The files are latin-1 rather than
    UTF-8, and the last several rows are a contact address and an
    explanation written into the Year column. Those rows parse as data and
    would otherwise appear as species with no name.

    This is why the read uses pandas rather than DuckDB like the GBIF
    scripts do. DuckDB 0.10.3 assumes UTF-8 and would refuse the file.
    """
    try:
        d = pd.read_csv(path, low_memory=False, encoding="utf-8-sig")
    except UnicodeDecodeError:
        d = pd.read_csv(path, low_memory=False, encoding="latin-1")
    year = pd.to_numeric(d["Year"], errors="coerce")
    notes = int((~year.notna()).sum())
    d = d[year.notna()].copy()
    d["Year"] = year[year.notna()].astype(int)
    print(f"  {path.name}: {len(d):,} rows, dropped {notes} note rows, "
          f"{d['Year'].min()} to {d['Year'].max()}")
    return d


def tidy_text(frame):
    """
    Make the whitespace in every text column ordinary.

    Nine of the ecological community names separate their parts with a
    non-breaking space. It looks identical to a normal space, so nobody
    notices, and then a join against any other species list quietly misses
    those rows.

    The test below is for what a column is NOT. Pandas 2 gives text columns
    the dtype "object" and pandas 3 gives them "str", so checking for either
    name skips every column on the other version, cleans nothing, and
    reports no error.
    """
    from pandas.api import types as pdt
    for col in frame.columns:
        if not (pdt.is_numeric_dtype(frame[col])
                or pdt.is_datetime64_any_dtype(frame[col])
                or pdt.is_bool_dtype(frame[col])):
            frame[col] = (frame[col]
                          .astype("string")
                          .str.replace(" ", " ", regex=False)
                          .str.replace(r"\s+", " ", regex=True)
                          .str.strip()
                          .replace({"": None}))
    return frame


def shape_species(d):
    """Put the plants and animals file into the shared column shape."""
    out = pd.DataFrame(index=d.index)
    out["bcsee_id"] = d["Element.Code"]
    out["kind"] = "species"
    out["scientific_name"] = d["Scientific.Name"]
    out["english_name"] = d["English.Name"]
    out["scientific_synonyms"] = d["Scientific.Name.Synonyms"]
    out["english_synonyms"] = d["English.Name.Synonyms"]
    out["rank_level"] = d["Classification.Level"]
    out["category"] = d["Name.Category"]
    out["group_name"] = d["Class.English"]
    for c in ["Kingdom", "Phylum", "Class", "Order", "Family"]:
        out[c.lower()] = d[c]
    out["global_status"] = d["Global.Status"]
    out["prov_status"] = d["Prov.Status"]
    out["bc_list"] = d["BC.List"]
    out["cosewic"] = d["COSEWIC"]
    out["sara"] = d["SARA"]
    out["sara_status"] = d["SARA.Status"]
    out["cites"] = d["CITES"]
    out["mbca"] = d["MBCA"]
    out["frpa"] = d["Provincial.FRPA"]
    out["origin"] = d["Origin"]
    out["presence"] = d["Presence"]
    out["endemic"] = d["Endemic"]
    out["has_cdc_map"] = d["CDC.Maps"]
    out["bec_units"] = None
    out["species_code"] = d["Species.Code"]
    out["status_review_date"] = d["Prov.Status.Review.Date"]
    out["status_change_date"] = d["Prov.Status.Change.Date"]
    out["snapshot_year"] = d["Year"]
    out["source"] = "BCSEE"
    return out.reindex(columns=COLUMNS)


def shape_communities(d):
    """
    Put the ecological communities file into the same shape.

    Communities carry no taxonomy and no legal designations, but they do
    carry biogeoclimatic unit codes, which place them on the province's
    existing ecosystem maps without any coordinates being involved.
    """
    out = pd.DataFrame(index=d.index)
    out["bcsee_id"] = d["Element.Code"]
    out["kind"] = "ecological_community"
    out["scientific_name"] = d["Scientific.Name"]
    out["english_name"] = d["English.Name"]
    out["category"] = d["Name.Category"]
    out["group_name"] = d["Ecosystem.Group"]
    out["global_status"] = d["Global.Status"]
    out["prov_status"] = d["Prov.Status"]
    out["bc_list"] = d["BC.List"]
    out["frpa"] = d["Provincial.FRPA"]
    out["endemic"] = d["Endemic"]
    out["has_cdc_map"] = d["CDC.Maps"]
    out["bec_units"] = d["Biogeoclimatic.Units"]
    out["status_review_date"] = d["Prov.Status.Review.Date"]
    out["status_change_date"] = d["Prov.Status.Change.Date"]
    out["snapshot_year"] = d["Year"]
    out["source"] = "BCSEE"
    return out.reindex(columns=COLUMNS)


def write_parquet(con, frame, path):
    """
    Write one table, and do it so a failed run cannot leave a broken file.

    The other pipeline scripts write straight to the output path. If one of
    those dies partway the file is left half written, and the next script
    reads it as though it were complete. Writing to a neighbouring temporary
    file and renaming it afterwards avoids that: a rename within a directory
    either happens or does not, so the output is always a whole file from
    some run.
    """
    tmp = path.with_suffix(path.suffix + ".partial")
    con.register("staging", frame)
    con.execute(f"COPY (SELECT * FROM staging) TO '{tmp}' "
                f"(FORMAT PARQUET, COMPRESSION ZSTD)")
    con.unregister("staging")
    os.replace(tmp, path)
    print(f"  wrote {path.name}  ({len(frame):,} rows, "
          f"{path.stat().st_size / 1e6:.1f} MB)")


def report(current, history):
    """Print enough of the result that a mistake in it would be visible."""
    print()
    print("=" * 70)
    print("RESULT")
    print("=" * 70)
    print()
    print(f"  current: {len(current):,} rows  {current['kind'].value_counts().to_dict()}")
    print(f"  snapshot year: {sorted(current['snapshot_year'].unique())}")
    print(f"  duplicate ids: {int(current['bcsee_id'].duplicated().sum())}")
    print(f"  missing scientific names: {int(current['scientific_name'].isna().sum())}")
    print()
    print("  by BC list")
    for k, v in current["bc_list"].value_counts(dropna=False).items():
        print(f"    {str(k):16s} {v:6,}")
    changed = history.groupby("bcsee_id")["prov_status"].nunique()
    print()
    print(f"  history: {len(history):,} rows, {history['bcsee_id'].nunique():,} entities, "
          f"{history['snapshot_year'].min()} to {history['snapshot_year'].max()}")
    print(f"  entities whose provincial status changed at least once: "
          f"{int((changed > 1).sum()):,}")


def main():
    """Download, clean, reshape and write both tables."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="/home/songyanf/bcbn/data",
                        help="Where the downloaded CSVs and the parquet "
                             "outputs live. Not in the repo.")
    parser.add_argument("--refresh", action="store_true",
                        help="Download the source files again even if they "
                             "are already on disk")
    args = parser.parse_args()

    data = Path(args.data_dir).expanduser()
    raw = data / "bcsee_raw"
    raw.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("SOURCE FILES")
    print("=" * 70)
    print()
    paths = {}
    for key, url in SOURCES.items():
        path = raw / f"cdc_bcsee_history_{key}.csv"
        if args.refresh and path.exists():
            path.unlink()
        if not download(url, path):
            return 1
        paths[key] = path

    print()
    print("=" * 70)
    print("READING")
    print("=" * 70)
    print()
    species = shape_species(read_published(paths["plants_animals"]))
    communities = shape_communities(read_published(paths["communities"]))
    both = tidy_text(pd.concat([species, communities], ignore_index=True))

    # Each file can stop at a different year, so take the latest year each
    # kind reaches rather than one year across both.
    latest = both.groupby("kind")["snapshot_year"].transform("max")
    current = both[both["snapshot_year"] == latest].copy()
    history = both[HISTORY_COLUMNS].copy()

    print()
    print("=" * 70)
    print("WRITING")
    print("=" * 70)
    print()
    con = duckdb.connect()
    write_parquet(con, current, data / "bcsee_current.parquet")
    write_parquet(con, history, data / "bcsee_history.parquet")
    con.close()

    report(current, history)
    return 0


if __name__ == "__main__":
    sys.exit(main())
