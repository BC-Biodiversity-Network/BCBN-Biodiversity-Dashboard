"""
Build the year-by-year conservation status history from BCSEE's archives.

BCSEE, the BC Species and Ecosystems Explorer, is the province's list of
species and ecological communities with their conservation status. Each year
the province exports the list and appends it to an archive file on the BC
Data Catalogue, one file for plants and animals and one for ecological
communities. This script turns those two archives into one table that shows
how each entity's status has changed over the years.

This is only the history. The current list comes from pull_bcsee.py, which
downloads the province's full up-to-date copy. The newest year in these
archives is always behind that copy, so do not use this table for "what is
the status now".

Like the rest of the pipeline this rebuilds its output from scratch every
run, so running it twice is the same as running it once.

Every column from both archives is kept, with tidy names, because nobody
has yet decided which fields the platform needs. The two files do not have
the same columns: ecological communities have no taxonomy or federal
listings, for example. Where a column exists in only one file, rows from the
other file are left blank in it.

Output:
    bcsee_history.parquet   one row per entity per year, every column from
                            both archives, plus snapshot_year and kind

Usage:
    python build_bcsee_history.py
    python build_bcsee_history.py --data-dir ~/Desktop/BCBN/Results
    python build_bcsee_history.py --refresh
"""

import argparse
import os
import re
import sys
from pathlib import Path

import duckdb
import pandas as pd
import requests


# The two published archive files. Both sit under one catalogue record,
# licensed Open Government Licence - British Columbia.
CATALOGUE = ("https://catalogue.data.gov.bc.ca/dataset/"
             "d3651b8c-f560-48f7-a34e-26b0afc77d84/resource")
SOURCES = {
    "plants_animals": (f"{CATALOGUE}/39aa3eb8-da10-49c5-8230-a3b5fd0006a9/"
                       "download/cdc_bcsee_history_plants_animals.csv"),
    "communities": (f"{CATALOGUE}/bbacd6fb-6708-4cf8-b353-0dc5ef75b7b9/"
                    "download/cdc_bcsee_history_eco_communities.csv"),
}
HEADERS = {"User-Agent": "BCBN-Dashboard (UBC Biodiversity Research Centre)"}

# The columns this script relies on, spelled as they appear in the archive
# files. Both files have all of these. If the province ever renames one, the
# script stops with a message naming it, rather than failing somewhere later
# with an error that looks like a bug in the code.
REQUIRED = ["Year", "Element.Code", "Scientific.Name",
            "BC.List", "Prov.Status", "Global.Status"]

# Three archive columns hold the same thing as a column in
# bcsee_status.parquet but under a different name. Renaming them here means
# the same field has the same name in both tables. Note that in the older
# years "sara" packs schedule, status and date into one value, for example
# "1-E (Jun 2003)", while recent years hold only the schedule number.
SAME_AS_STATUS = {
    "sara": "sara_schedule",
    "mbca": "migratory_bird_convention_act",
    "biogeoclimatic_units": "bgc",
}

# Columns to put first in the output, so the ones you look at most are on
# the left. Every other column follows in the order it appears in the files.
FIRST_COLUMNS = ["element_code", "kind", "scientific_name", "snapshot_year",
                 "bc_list", "prov_status", "global_status"]


def download(url, path):
    """
    Fetch one archive file, unless it is already on disk.

    The catalogue warns that these download links sometimes serve a web page
    instead of the data, so the first bytes are checked. A web page starts
    with an angle bracket, and catching that here turns a confusing error
    later into a clear message now.
    """
    if path.exists():
        print(f"  {path.name} already here ({path.stat().st_size / 1e6:.1f} MB), "
              f"use --refresh to fetch again")
        return True
    print(f"  fetching {path.name}")
    r = requests.get(url, headers=HEADERS, timeout=600, stream=True)
    r.raise_for_status()
    first = b""
    tmp = path.with_suffix(path.suffix + ".partial")
    with open(tmp, "wb") as fh:
        for chunk in r.iter_content(chunk_size=1 << 16):
            if not first:
                first = chunk[:64]
            fh.write(chunk)
    if first.lstrip()[:1] == b"<":
        print("    got a web page rather than a CSV, see the catalogue note "
              "about this download bug")
        tmp.unlink()
        return False
    os.replace(tmp, path)
    print(f"    {path.stat().st_size / 1e6:.1f} MB")
    return True


def tidy_name(name):
    """
    Turn an archive column heading into a short lowercase name.

    The archives separate words with dots, like "Element.Code". This turns
    that into "element_code", the same style pull_bcsee.py uses, so the two
    tables share names wherever they describe the same thing. Anything that
    is not a letter or a digit becomes a single underscore.
    """
    name = re.sub(r"[^0-9a-zA-Z]+", "_", str(name)).strip("_")
    return name.lower()


def read_archive(path, kind):
    """
    Read one archive file, keep every column, and tidy it into shape.

    Two things make a plain read wrong. The files are latin-1 rather than
    UTF-8, and the last several rows are a contact address and an
    explanation written into the Year column. Those rows parse as data and
    would otherwise appear as entities with no name, so any row whose Year
    is not a number is dropped.
    """
    try:
        d = pd.read_csv(path, low_memory=False, encoding="utf-8-sig", dtype=str)
    except UnicodeDecodeError:
        d = pd.read_csv(path, low_memory=False, encoding="latin-1", dtype=str)

    # Check before doing anything else, so a renamed column produces a clear
    # message instead of an error deep inside pandas.
    missing = [c for c in REQUIRED if c not in d.columns]
    if missing:
        raise ValueError(
            f"{path.name} is missing columns this script needs: {missing}. "
            f"The province may have renamed them. Open the file and check "
            f"its header row.")

    year = pd.to_numeric(d["Year"], errors="coerce")
    notes = int(year.isna().sum())
    d = d[year.notna()].copy()

    names = [tidy_name(c) for c in d.columns]
    repeated = sorted({n for n in names if names.count(n) > 1})
    if repeated:
        raise ValueError(f"two headings in {path.name} became the same "
                         f"name: {repeated}")
    d.columns = names

    # "year" becomes a whole number and is renamed, so it cannot be confused
    # with any date column and is clearly the year of the snapshot.
    d = d.rename(columns={"year": "snapshot_year", **SAME_AS_STATUS})
    d["snapshot_year"] = year[year.notna()].astype(int)
    d["kind"] = kind

    # A handful of rows carry a year and nothing else, not even a code. They
    # cannot be linked to any entity, so they are dropped and counted.
    blank = d["element_code"].isna() | (d["element_code"].str.strip() == "")
    d = d[~blank].copy()
    print(f"  {path.name}: {len(d):,} rows, {len(d.columns)} columns, "
          f"dropped {notes} note rows and {int(blank.sum())} blank rows, "
          f"{d['snapshot_year'].min()} to {d['snapshot_year'].max()}")
    return d


def tidy_text(frame):
    """
    Make the whitespace in every text column ordinary.

    Some ecological community names separate their parts with a
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


def write_parquet(frame, path):
    """
    Write the table, and do it so a failed run cannot leave a broken file.

    Writing to a neighbouring temporary file and renaming it afterwards means
    the output is always a whole file from some run: a rename within a
    folder either happens or does not.
    """
    tmp = path.with_suffix(path.suffix + ".partial")
    con = duckdb.connect()
    con.register("staging", frame)
    con.execute(f"COPY (SELECT * FROM staging) TO '{tmp}' "
                f"(FORMAT PARQUET, COMPRESSION ZSTD)")
    con.close()
    os.replace(tmp, path)
    print(f"  wrote {path.name}  ({len(frame):,} rows, "
          f"{path.stat().st_size / 1e6:.1f} MB)")


def report(history):
    """Print enough of the result that a mistake in it would be visible."""
    print()
    print("=" * 70)
    print("RESULT")
    print("=" * 70)
    print()
    print(f"  {len(history):,} rows, {len(history.columns)} columns, "
          f"{history['element_code'].nunique():,} entities, "
          f"{history['snapshot_year'].min()} to "
          f"{history['snapshot_year'].max()}")
    print(f"  {history['kind'].value_counts().to_dict()}")
    dupes = int(history.duplicated(["element_code", "snapshot_year"]).sum())
    print(f"  entity and year pairs that appear more than once: {dupes}")
    changed = history.groupby("element_code")["prov_status"].nunique()
    print(f"  entities whose provincial status changed at least once: "
          f"{int((changed > 1).sum()):,}")

    # A value never seen before, such as a new spelling of a list name,
    # shows up here as an extra line.
    latest = history["snapshot_year"].max()
    print()
    print(f"  by BC list, in {latest}")
    counts = history.loc[history["snapshot_year"] == latest, "bc_list"]
    for value, n in counts.value_counts(dropna=False).items():
        print(f"    {str(value):16s} {n:7,}")

    # Columns that exist in only one of the two files are blank for every
    # row from the other file. Listing them makes that expected gap visible.
    print()
    print("  columns only in one file (blank for the other kind)")
    for col in history.columns:
        filled = history.groupby("kind")[col].apply(lambda s: s.notna().any())
        if not filled.all():
            only = ", ".join(k for k, v in filled.items() if v)
            print(f"    {col:32s} {only}")


def main():
    """Download both archives, combine them, and write the history table."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", default="/home/songyanf/bcbn/data",
                        help="Where the downloads and the output go. Defaults "
                             "to the server's data folder, like the other "
                             "pipeline scripts. Not inside the repo.")
    parser.add_argument("--refresh", action="store_true",
                        help="Download the archive files again even if they "
                             "are already on disk.")
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
    history = pd.concat([
        read_archive(paths["plants_animals"], "species"),
        read_archive(paths["communities"], "ecological_community"),
    ], ignore_index=True)
    history = tidy_text(history)
    rest = [c for c in history.columns if c not in FIRST_COLUMNS]
    history = history[FIRST_COLUMNS + rest]

    print()
    print("=" * 70)
    print("WRITING")
    print("=" * 70)
    print()
    write_parquet(history, data / "bcsee_history.parquet")

    report(history)
    return 0


if __name__ == "__main__":
    sys.exit(main())
