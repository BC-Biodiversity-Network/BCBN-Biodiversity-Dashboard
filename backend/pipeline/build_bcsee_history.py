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

Output:
    bcsee_history.parquet   one row per entity per year, with the fields
                            that change over time

Usage:
    python build_bcsee_history.py
    python build_bcsee_history.py --data-dir ~/Desktop/BCBN/Results
    python build_bcsee_history.py --refresh
"""

import argparse
import os
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

# The archive column each output column comes from. The two files use the
# same names for these particular fields, which is why one mapping serves
# both. element_code matches the column of the same name in
# bcsee_status.parquet, so the two tables can be joined on it.
COLUMNS = {
    "element_code": "Element.Code",
    "scientific_name": "Scientific.Name",
    "snapshot_year": "Year",
    "bc_list": "BC.List",
    "prov_status": "Prov.Status",
    "global_status": "Global.Status",
}


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


def read_archive(path, kind):
    """
    Read one archive file and keep only the columns the history needs.

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

    year = pd.to_numeric(d["Year"], errors="coerce")
    notes = int(year.isna().sum())
    d = d[year.notna()].copy()

    out = pd.DataFrame({new: d[old] for new, old in COLUMNS.items()})
    out["snapshot_year"] = year[year.notna()].astype(int)
    out["kind"] = kind

    # A handful of rows carry a year and nothing else, not even a code. They
    # cannot be linked to any entity, so they are dropped and counted.
    blank = out["element_code"].isna() | (out["element_code"].str.strip() == "")
    out = out[~blank].copy()
    print(f"  {path.name}: {len(out):,} rows, dropped {notes} note rows and "
          f"{int(blank.sum())} blank rows, "
          f"{out['snapshot_year'].min()} to {out['snapshot_year'].max()}")
    return out


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
    print(f"  {len(history):,} rows, {history['element_code'].nunique():,} "
          f"entities, {history['snapshot_year'].min()} to "
          f"{history['snapshot_year'].max()}")
    print(f"  {history['kind'].value_counts().to_dict()}")
    dupes = int(history.duplicated(["element_code", "snapshot_year"]).sum())
    print(f"  entity and year pairs that appear more than once: {dupes}")
    changed = history.groupby("element_code")["prov_status"].nunique()
    print(f"  entities whose provincial status changed at least once: "
          f"{int((changed > 1).sum()):,}")


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
    history = history[["element_code", "kind", "scientific_name",
                       "snapshot_year", "bc_list", "prov_status",
                       "global_status"]]

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
