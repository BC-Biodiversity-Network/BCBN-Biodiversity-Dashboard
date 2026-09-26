"""
Pull the current BC Species and Ecosystems Explorer list into one table.

BCSEE is the province's list of every species and ecological community it
tracks, with each one's conservation status (red, blue or yellow list,
provincial and global ranks, federal listings). It is a species list, not
observation data: one row is one species, with no locations or dates seen.

The province publishes a ready-made copy of the whole list at a fixed web
address, "Summary Export All". It is the same 93 columns you get by exporting
from the BCSEE website by hand, and when checked against a hand export on
2026-09-26 the names and conservation statuses matched for every species.
Using the fixed copy means no clicking through the website, so this script
can be rerun whenever a fresh copy is wanted.

The copy is not live. The province regenerates it from time to time and
writes the date inside the file. That date is kept in the output, so anyone
using the table can see how old it is.

One thing the Excel file loses: the website shows many dates as a month only,
like "Apr 2019", but the Excel file stores them as full dates on the first of
the month, "2019-04-01". So a day of 01 in a date column may just mean the
province only recorded the month.

Output:
    bcsee_status.parquet   one row per species or community, all 93 columns
                           with tidy names, plus the date the province made
                           the file and the date we downloaded it

Usage:
    python pull_bcsee.py
    python pull_bcsee.py --data-dir ~/Desktop/BCBN/Results
    python pull_bcsee.py --refresh
"""

import argparse
import datetime as dt
import os
import re
import sys
from pathlib import Path

import duckdb
import pandas as pd
import requests


SOURCE_URL = "https://www.env.gov.bc.ca/atrisk/help/SummaryExportAll.xlsx"
HEADERS = {"User-Agent": "BCBN-Dashboard (UBC Biodiversity Research Centre)"}

DATA_SHEET = "summaryExport"
INFO_SHEET = "Metadata"

# If the province's copy is older than this, the script still runs but says
# so loudly. Statuses change a few times a year, so a copy this old may be
# missing recent changes.
STALE_AFTER_DAYS = 180


def download(path, refresh):
    """
    Fetch the province's file, unless a copy is already on disk.

    An Excel file is really a zip archive, and every zip file begins with the
    two letters "PK". Checking for them catches the case where the website
    hands back an error page instead of the file, which would otherwise
    surface later as a confusing message from the Excel reader.
    """
    if path.exists() and not refresh:
        print(f"  using the copy already here: {path} "
              f"({path.stat().st_size / 1e6:.1f} MB)")
        print("  add --refresh to download it again")
        return True

    print(f"  downloading {SOURCE_URL}")
    r = requests.get(SOURCE_URL, headers=HEADERS, timeout=300)
    r.raise_for_status()
    if not r.content.startswith(b"PK"):
        print("  what came back is not an Excel file, so nothing was saved")
        print(f"  it starts with: {r.content[:80]!r}")
        return False

    # Save under a temporary name first and rename at the end, so a download
    # that dies halfway never leaves a broken file behind with the real name.
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".partial")
    tmp.write_bytes(r.content)
    os.replace(tmp, path)
    print(f"  saved {path} ({path.stat().st_size / 1e6:.1f} MB)")
    modified = r.headers.get("Last-Modified")
    if modified:
        print(f"  the website says the file was last changed {modified}")
    return True


def read_generated_date(path):
    """
    Find the date the province made this copy, from the file's Metadata sheet.

    The sheet holds a line like "Generated May 5 2026". If the wording ever
    changes and no date can be found, this returns None rather than
    guessing, and the report says the date is unknown.
    """
    info = pd.read_excel(path, sheet_name=INFO_SHEET, header=None, dtype=str)
    for cell in info[0].dropna():
        found = re.search(r"Generated\s+([A-Za-z]+\s+\d{1,2},?\s+\d{4})", cell)
        if found:
            text = found.group(1).replace(",", "")
            for fmt in ("%B %d %Y", "%b %d %Y"):
                try:
                    return dt.datetime.strptime(text, fmt).date()
                except ValueError:
                    pass
    return None


def tidy_name(name):
    """
    Turn a column heading into a short lowercase name with underscores.

    For example "Class (English)" becomes "class_english" and
    "Habitats (Type / Subtype / Dependence)" becomes
    "habitats_type_subtype_dependence". Anything that is not a letter or a
    digit becomes a single underscore.
    """
    name = re.sub(r"[^0-9a-zA-Z]+", "_", str(name)).strip("_")
    return name.lower()


def tidy_value(value):
    """
    Clean one cell so the same thing is always written the same way.

    Three things are fixed. Non-breaking spaces, which look like normal
    spaces but do not match them, become normal spaces. Runs of spaces and
    line breaks become a single space. Dates, which the Excel reader turns
    into "2020-03-01 00:00:00", lose the meaningless midnight on the end.
    An empty cell becomes a true blank rather than an empty string.
    """
    if pd.isna(value):
        return None
    text = str(value).replace(" ", " ")
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^(\d{4}-\d{2}-\d{2}) 00:00:00$", r"\1", text)
    return text or None


def read_species(path):
    """
    Read the species sheet and put it into a clean, predictable shape.

    Everything is read as text on purpose. Letting the reader guess types
    means a code like "0010" can turn into the number 10, and the guesses
    change between pandas versions. Text in, text out avoids both.

    Rows with no Element Code are dropped: the province's file has a couple
    of blank lines in it, and a species with no code cannot be linked to
    anything anyway.
    """
    frame = pd.read_excel(path, sheet_name=DATA_SHEET, dtype=str)
    before = len(frame)

    names = [tidy_name(c) for c in frame.columns]
    repeated = sorted({n for n in names if names.count(n) > 1})
    if repeated:
        raise ValueError(f"two headings became the same name: {repeated}")
    frame.columns = names

    frame = frame.apply(lambda col: col.map(tidy_value))
    frame = frame[frame["element_code"].notna()].copy()
    print(f"  read {before:,} rows, kept {len(frame):,} "
          f"(dropped {before - len(frame)} with no Element Code)")

    dupes = frame["element_code"][frame["element_code"].duplicated()]
    if len(dupes):
        raise ValueError(f"these Element Codes appear more than once: "
                         f"{sorted(dupes.unique())[:10]}")
    return frame


def write_parquet(frame, path):
    """
    Write the table, and do it so a failed run cannot leave a broken file.

    The table goes to a temporary file first and is renamed only once it is
    complete. A rename either happens or it does not, so the output is
    always a whole file from some run, never half of one.
    """
    tmp = path.with_suffix(path.suffix + ".partial")
    con = duckdb.connect()
    con.register("staging", frame)
    con.execute(f"COPY (SELECT * FROM staging) TO '{tmp}' "
                f"(FORMAT PARQUET, COMPRESSION ZSTD)")
    con.close()
    os.replace(tmp, path)
    print(f"  wrote {path} ({len(frame):,} rows, "
          f"{path.stat().st_size / 1e6:.1f} MB)")


def report(frame, generated):
    """Print enough of the result that a problem with it would be visible."""
    print()
    print("=" * 70)
    print("RESULT")
    print("=" * 70)
    print()
    print(f"  {len(frame):,} species and communities, "
          f"{len(frame.columns)} columns")
    if generated:
        age = (dt.date.today() - generated).days
        print(f"  the province made this copy on {generated} ({age} days ago)")
        if age > STALE_AFTER_DAYS:
            print(f"  WARNING: that is more than {STALE_AFTER_DAYS} days old. "
                  f"Recent status changes may be missing.")
    else:
        print("  WARNING: could not find the date the province made this copy")
    print()
    print("  by BC list")
    for value, n in frame["bc_list"].value_counts(dropna=False).items():
        print(f"    {str(value):16s} {n:7,}")
    print()
    print("  by kind")
    for value, n in frame["name_category"].value_counts(dropna=False).items():
        print(f"    {str(value)[:40]:42s} {n:7,}")


def main():
    """Download the province's copy, clean it, and write one table."""
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", default="/home/songyanf/bcbn/data",
                        help="Where the download and the output go. Defaults "
                             "to the server's data folder, like the other "
                             "pipeline scripts. Not inside the repo.")
    parser.add_argument("--refresh", action="store_true",
                        help="Download the file again even if a copy is "
                             "already on disk.")
    args = parser.parse_args()

    data = Path(args.data_dir).expanduser()
    raw = data / "bcsee_raw" / "SummaryExportAll.xlsx"

    print("=" * 70)
    print("DOWNLOAD")
    print("=" * 70)
    print()
    if not download(raw, args.refresh):
        return 1

    print()
    print("=" * 70)
    print("READ AND CLEAN")
    print("=" * 70)
    print()
    try:
        generated = read_generated_date(raw)
        frame = read_species(raw)
    except ImportError:
        print("  reading Excel files needs the openpyxl package:")
        print("      pip install openpyxl")
        return 1

    frame["source"] = "BCSEE Summary Export All"
    frame["source_generated"] = generated.isoformat() if generated else None
    frame["pulled_on"] = dt.date.today().isoformat()

    print()
    write_parquet(frame, data / "bcsee_status.parquet")
    report(frame, generated)
    return 0


if __name__ == "__main__":
    sys.exit(main())
