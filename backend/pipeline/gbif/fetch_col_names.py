"""
fetch_col_names.py

Download one Catalogue of Life release from ChecklistBank and keep the name
and authorship of every name in it, so build_species_tables.py can show each
species card with the Catalogue of Life's own name for its specieskey.

Our specieskeys are Catalogue of Life ids, because the GBIF download is made
with the Catalogue of Life checklist. GBIF ties that checklist to one
ChecklistBank release at a time (315557, COL26.6 XR, for the current
download); request_gbif_download.py records which one in
<key>.download.json, and build_dwca_tables.py copies it into
download_info.json. This script needs that release key.

What it does:
  1. Downloads ChecklistBank's flat ColDP export of the release (about
     133 MB for 315557):
         https://api.checklistbank.org/dataset/<release>/export.zip?format=ColDP
     It holds a single table, dataset-<release>.tsv (about 1.5 GB
     unzipped), with one row per name: id, parent, status, rank, name,
     authorship and the classification. A zip already there at the right
     size is not downloaded again.
  2. Reads that table straight out of the zip, a batch at a time (the
     1.5 GB file is never written to disk), and keeps the columns below.
  3. Saves the release's own details (alias, version, issue date) from
     ChecklistBank.

Output, in --outdir/<release>/:
    coldp_<release>.zip        the download (kept, so a rerun is quick)
    col_names_<release>.parquet  one row per name:
        id, parent_id, status, rank, scientific_name, authorship,
        kingdom, phylum, class, order, family, genus, species
    col_release.json           key, alias, version, issued, title, the
                               export URL and when it was downloaded

The parquet is written to a temporary name first and only replaces the real
file when it has been checked: it has rows, every id is filled in and no id
appears twice. If a check fails, the previous file is left as it was.

Run order (this step is only needed once per Catalogue of Life release):
    request_gbif_download.py -> build_dwca_tables.py -> fetch_col_names.py
        -> make_species_key_merge.py -> build_species_tables.py --key-merge
        -> build_hex_aggregates.py

Run (from backend/):
    python pipeline/gbif/fetch_col_names.py --release 315557 --outdir ~/bcbn/data/col
"""

import argparse
import json
import os
import sys
import time
import urllib.request
import zipfile
from datetime import datetime, timezone

import duckdb
import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

from safe_outputs import clear_temporary, replace_outputs, report_not_replaced, temporary_path

CLB_URL = "https://api.checklistbank.org/dataset/"
HEADERS = {"User-Agent": "BCBN-Dashboard (UBC Biodiversity Research Centre)"}

# The columns kept from the export, with the names used in the parquet.
KEPT_COLUMNS = {
    "col:ID": "id",
    "col:parentID": "parent_id",
    "col:status": "status",
    "col:rank": "rank",
    "col:scientificName": "scientific_name",
    "col:authorship": "authorship",
    "col:kingdom": "kingdom",
    "col:phylum": "phylum",
    "col:class": "class",
    "col:order": "order",
    "col:family": "family",
    "col:genus": "genus",
    "col:species": "species",
}


def export_url(release):
    """Return the ChecklistBank address of a release's flat ColDP export."""
    return f"{CLB_URL}{release}/export.zip?format=ColDP"


def release_details(release):
    """Return ChecklistBank's description of the release: key, alias,
    version, issued date and title."""
    request = urllib.request.Request(f"{CLB_URL}{release}", headers=HEADERS)
    with urllib.request.urlopen(request, timeout=60) as reply:
        dataset = json.loads(reply.read().decode("utf-8"))
    return {k: dataset.get(k) for k in ("key", "alias", "version", "issued", "title")}


def download(url, path):
    """Download url to path, unless a file of the same size is already
    there. Writes to path + ".part" and renames it when complete, so a
    stopped download never looks finished. Returns the size in bytes."""
    head = urllib.request.Request(url, headers=HEADERS, method="HEAD")
    with urllib.request.urlopen(head, timeout=120) as reply:
        size = int(reply.headers.get("Content-Length") or 0)
    if size and os.path.exists(path) and os.path.getsize(path) == size:
        print(f"  already downloaded ({size / 1e6:,.0f} MB)")
        return size
    print(f"  downloading {size / 1e6:,.0f} MB ...")
    part = path + ".part"
    started = time.time()
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=120) as reply:
        with open(part, "wb") as out:
            while True:
                block = reply.read(1 << 20)
                if not block:
                    break
                out.write(block)
    got = os.path.getsize(part)
    if size and got != size:
        raise RuntimeError(f"Downloaded {got:,} bytes but expected {size:,}; try again")
    os.replace(part, path)
    print(f"  done in {time.time() - started:.0f}s")
    return got


def write_names(zip_path, release, out_path):
    """Read dataset-<release>.tsv out of the zip a batch at a time and write
    the KEPT_COLUMNS to a parquet. Returns the number of rows."""
    member = f"dataset-{release}.tsv"
    with zipfile.ZipFile(zip_path) as archive:
        if member not in archive.namelist():
            raise SystemExit(f"{zip_path} has no {member}; it holds {archive.namelist()[:5]}")
        with archive.open(member) as table:
            # The export is tab separated with no quoting, so quotes inside
            # names (and square brackets in authorships) are read as they
            # are. Every column is read as text.
            reader = pacsv.open_csv(
                table,
                read_options=pacsv.ReadOptions(block_size=64 << 20),
                parse_options=pacsv.ParseOptions(delimiter="\t", quote_char=False,
                                                 escape_char=False),
                convert_options=pacsv.ConvertOptions(
                    include_columns=list(KEPT_COLUMNS),
                    column_types={c: pa.string() for c in KEPT_COLUMNS},
                    null_values=[""], strings_can_be_null=True),
            )
            schema = pa.schema([(name, pa.string()) for name in KEPT_COLUMNS.values()])
            rows = 0
            with pq.ParquetWriter(out_path, schema, compression="zstd") as writer:
                for batch in reader:
                    table_batch = pa.Table.from_batches([batch]).rename_columns(
                        [KEPT_COLUMNS[c] for c in batch.schema.names])
                    writer.write_table(table_batch.select(list(KEPT_COLUMNS.values())))
                    rows += batch.num_rows
    return rows


def check_names(parquet_path):
    """Return (name, passed, detail) results for the names parquet: it has
    rows, every id is filled in, and no id appears twice. Also returns the
    count per status, for the summary."""
    con = duckdb.connect()
    rows, ids, empty = con.execute(
        f"""SELECT count(*), count(DISTINCT id), count(*) - count(id)
            FROM read_parquet('{parquet_path}')"""
    ).fetchone()
    statuses = con.execute(
        f"""SELECT coalesce(status, '(none)'), count(*) FROM read_parquet('{parquet_path}')
            GROUP BY 1 ORDER BY 2 DESC"""
    ).fetchall()
    con.close()
    return [
        ("the file has names", rows > 0, f"{rows:,} rows"),
        ("every name has an id", empty == 0, f"{empty:,} without an id"),
        ("no id appears twice", ids == rows - empty, f"{ids:,} distinct ids in {rows:,} rows"),
    ], statuses


def main():
    """Download the release, write the names parquet under a temporary name,
    check it, and replace the real file only if every check passes."""
    parser = argparse.ArgumentParser(
        description="Download a Catalogue of Life release (ChecklistBank flat ColDP export) "
        "and keep each name's id, rank, status, name and authorship."
    )
    parser.add_argument("--release", type=int, required=True,
                        help="The ChecklistBank key of the release, as recorded in "
                             "download_info.json (e.g. 315557).")
    parser.add_argument("--outdir", default="~/bcbn/data/col",
                        help="Folder to keep releases in; this release goes in "
                             "<outdir>/<release>/ (default ~/bcbn/data/col).")
    args = parser.parse_args()

    folder = os.path.join(os.path.expanduser(args.outdir), str(args.release))
    os.makedirs(folder, exist_ok=True)
    zip_path = os.path.join(folder, f"coldp_{args.release}.zip")
    names_path = os.path.join(folder, f"col_names_{args.release}.parquet")
    release_path = os.path.join(folder, "col_release.json")
    clear_temporary([names_path])

    details = release_details(args.release)
    print(f"Release: {details['key']} {details['alias']} ({details['version']}, "
          f"issued {details['issued']})")
    print(f"Folder:  {folder}\n")
    url = export_url(args.release)
    print(f"Export:  {url}")
    download(url, zip_path)

    t = time.time()
    rows = write_names(zip_path, args.release, temporary_path(names_path))
    print(f"\nRead {rows:,} names in {time.time() - t:.0f}s")
    results, statuses = check_names(temporary_path(names_path))
    print("  by status: " + "; ".join(f"{s} {n:,}" for s, n in statuses))

    print("\nChecks:")
    for name, passed, detail in results:
        print(f"  {'PASS' if passed else 'FAIL'}  {name}: {detail}")
    if not all(passed for _, passed, _ in results):
        report_not_replaced([names_path])
        return 1

    replace_outputs([names_path])
    details.update({
        "export_url": url,
        "downloaded": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "names_file": os.path.basename(names_path),
        "rows": rows,
    })
    with open(release_path, "w", encoding="utf-8") as handle:
        json.dump(details, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    print(f"\nAll checks passed; wrote:\n  {names_path} ({os.path.getsize(names_path) / 1e6:,.0f} MB)"
          f"\n  {release_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
