"""
harvest_dataone.py

Harvest every latest-version metadata record from DataONE's public search
index, so every later step (removing old versions, the BC sieve, the LLM
filters) reads local files instead of asking DataONE again.

What is asked for: formatType:METADATA AND -obsoletedBy:*, with no BC
filter, a page of 10,000 records at a time, sorted by id with Solr's
cursorMark paging (the only paging that stays correct over a million rows).

What is kept: the fields in FIELDS, exactly as DataONE returns them. Nothing
is cleaned, renamed or reordered; a field DataONE leaves out of a record is
stored as null. The only column added is harvest_date, the day the page was
fetched.

Each page is written to its own parquet file as soon as it arrives, and
progress.json records the cursor that fetched it and the cursor for the
next page. Running the same command again carries on from the last saved
page. When the last page has been fetched, every page is joined into
dataone_full_harvest.parquet.

Rules for talking to DataONE (set by Lucia):
  - only the public search API, https://cn.dataone.org/cn/v2/query/solr/;
  - at most one request per second;
  - a User-Agent naming the project and a contact;
  - stop at once if DataONE refuses or blocks a request, and never retry
    another way;
  - at most REQUEST_LIMIT requests per harvest folder (all runs together),
    counted from the folder's requests_log.csv.

The output folder must be ignored by git (the harvest is large and is
never committed); the script checks this with git check-ignore before it
writes anything, and stops if it is not.

Output, in --outdir:
    pages/page_00000.parquet ...   one file per page, in fetch order
    progress.json                  the query, the fields and every page's cursors
    requests_log.csv               one line per request actually sent
    nan_box_values.csv             every box edge DataONE sent as the text "NaN"
                                   (page, id, field, original value), stored as NaN
    dataone_full_harvest.parquet   every page joined, once the harvest is complete

Run, from backend/:
    python pipeline/dataone/harvest_dataone.py --outdir data/dataone/harvest_2026_11_18
    python pipeline/dataone/harvest_dataone.py --outdir data/dataone/harvest_test --max-pages 2 --request-limit 3
"""

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from datetime import datetime

import pyarrow as pa
import pyarrow.parquet as pq
import requests

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

SOLR = "https://cn.dataone.org/cn/v2/query/solr/"
HEADERS = {"User-Agent": "BCBN-Dashboard (BC Biodiversity Network, UBC; "
                         "songyanf@student.ubc.ca)"}

MIN_GAP = 1.05          # seconds between the starts of two requests, a little over one
REQUEST_LIMIT = 200     # the most requests one harvest folder may ever send
TIMEOUT = 300           # seconds to wait for one page before giving up
ROWS = 10000            # records per page; DataONE gives no more than this
SORT = "id asc"         # cursorMark paging needs a sort on the unique key

METADATA = "formatType:METADATA"
LATEST = "-obsoletedBy:*"


def AND(*parts):
    """Join query parts with AND, leaving out empty ones."""
    return " AND ".join(p for p in parts if p)


QUERY = AND(METADATA, LATEST)

# The fields asked for, grouped by how DataONE's index stores them (its field
# description: a single text value, several text values, or a number). The
# first eleven are the probe's short fields. pubDate, dateUploaded and
# dateModified come back as text, so they are stored as text, as returned.
#
# The four box fields hold only the FIRST box of the original record (checked
# on 2026-11-18 with exploration/dataone/check_dataone_bboxes.py), while
# geohash_9 holds one value per box, the centre of each box, so it is the
# only sign in the index that a record has more boxes. The taxon fields are
# the names DataONE found in the record, one value per name.
SINGLE_TEXT = ["id", "seriesId", "datasource", "formatId", "title", "abstract",
               "pubDate", "dateUploaded", "dateModified", "obsoletes"]
TAXON_FIELDS = ["scientificName", "kingdom", "phylum", "class", "order", "family", "genus", "species"]
LIST_TEXT = ["keywords", "origin", "namedLocation", "placeKey", "site", "webUrl",
             "licenseName", "licenseDescription", "licenseUrl", "contactOrganization",
             "geohash_9"] + TAXON_FIELDS
NUMBERS = ["northBoundCoord", "southBoundCoord", "eastBoundCoord", "westBoundCoord"]

# The order of the columns: the probe's short fields first, then the rest.
FIELDS = ["id", "seriesId", "datasource", "formatId", "title", "abstract", "keywords",
          "northBoundCoord", "southBoundCoord", "eastBoundCoord", "westBoundCoord",
          "origin", "pubDate", "dateUploaded", "dateModified", "obsoletes",
          "namedLocation", "placeKey", "site", "webUrl",
          "licenseName", "licenseDescription", "licenseUrl", "contactOrganization",
          "geohash_9"] + TAXON_FIELDS

SCHEMA = pa.schema(
    [(name, pa.string() if name in SINGLE_TEXT
      else pa.list_(pa.string()) if name in LIST_TEXT
      else pa.float64()) for name in FIELDS]
    + [("harvest_date", pa.string())])

LOG_COLUMNS = ["when", "status", "seconds", "bytes", "records", "url"]

# The only unexpected box value let through: DataONE sometimes sends a box
# edge as the text "NaN" instead of a number (first seen on 2026-11-18, in
# record sha256:907bb5b1...). It is stored as a numeric NaN and listed in
# NAN_CSV in the harvest folder.
NAN_TEXT = "NaN"
NAN_CSV = "nan_box_values.csv"
NAN_COLUMNS = ["page", "id", "field", "original_value"]


# ---------------------------------------------------------------------------
# Talking to DataONE
# ---------------------------------------------------------------------------

class Stop(Exception):
    """Something means the harvest must stop now: a refusal, an unexpected
    answer, the request limit, or a record we do not know how to store."""


_last_request = [0.0]


def requests_sent(log_path):
    """Return how many requests the folder's log says were sent, over every run."""
    if not os.path.exists(log_path):
        return 0
    with open(log_path, encoding="utf-8") as handle:
        return max(0, sum(1 for _ in handle) - 1)


def log_request(log_path, status, seconds, size, records, url):
    """Append one line to the request log for a request that was actually sent."""
    new = not os.path.exists(log_path)
    with open(log_path, "a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if new:
            writer.writerow(LOG_COLUMNS)
        writer.writerow([datetime.now().isoformat(timespec="seconds"), status, f"{seconds:.2f}",
                         size, records, url])


def fetch_page(cursor, log_path, limit):
    """
    Ask DataONE for one page of records, starting at cursor, and return the
    parsed answer.

    Waits so that two requests never start less than MIN_GAP seconds apart,
    and refuses to send a request once the folder's log holds `limit` of
    them. Any answer other than 200 stops the harvest: 403, 429 or a 503
    with Retry-After means DataONE refused or wants us to slow down, and
    anything else means the query or the service is not as expected. There
    are no retries.
    """
    if requests_sent(log_path) >= limit:
        raise Stop(f"the request limit of {limit} for this folder is reached "
                   f"(see {log_path}); nothing more was sent")
    wait = MIN_GAP - (time.time() - _last_request[0])
    if wait > 0:
        time.sleep(wait)
    params = {"q": QUERY, "fl": ",".join(FIELDS), "rows": ROWS, "sort": SORT,
              "cursorMark": cursor, "wt": "json"}
    start = time.time()
    _last_request[0] = start
    try:
        answer = requests.get(SOLR, params=params, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as error:
        log_request(log_path, f"error {type(error).__name__}", time.time() - start, 0, 0, SOLR)
        raise Stop(f"the request failed before DataONE answered: {error}") from error
    seconds = time.time() - start
    if answer.status_code != 200:
        log_request(log_path, answer.status_code, seconds, len(answer.content), 0, answer.url)
        refused = answer.status_code in (403, 429) or (
            answer.status_code == 503 and "Retry-After" in answer.headers)
        kind = "refused or blocked the request" if refused else "gave an unexpected answer"
        raise Stop(f"DataONE {kind}: HTTP {answer.status_code}\n{answer.text[:500]}")
    reply = answer.json()
    log_request(log_path, 200, seconds, len(answer.content), len(reply["response"]["docs"]), answer.url)
    reply["_seconds"] = seconds
    reply["_bytes"] = len(answer.content)
    return reply


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

def check_ignored(outdir):
    """
    Stop unless git ignores the output folder and the files that go in it.

    Uses git check-ignore from the repository the script lives in. Exit code
    0 means ignored, 1 means not ignored; anything else means git could not
    tell, which also stops the harvest.
    """
    repo = os.path.dirname(os.path.abspath(__file__))
    for path in (outdir, os.path.join(outdir, "pages", "page_00000.parquet"),
                 os.path.join(outdir, "progress.json"), os.path.join(outdir, "requests_log.csv"),
                 os.path.join(outdir, NAN_CSV)):
        result = subprocess.run(["git", "check-ignore", "-q", os.path.abspath(path)], cwd=repo,
                                capture_output=True, text=True)
        if result.returncode == 1:
            raise Stop(f"{path} is not ignored by git; nothing was written")
        if result.returncode != 0:
            raise Stop(f"git check-ignore could not check {path}: {result.stderr.strip()}")


def write_json(path, data):
    """Write a JSON file through a temporary name, so a crash never leaves half a file."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=1)
    os.replace(tmp, path)


def new_progress():
    """Return the progress record of a harvest that has not started."""
    return {"query": QUERY, "fields": FIELDS, "rows": ROWS, "sort": SORT,
            "started": datetime.now().isoformat(timespec="seconds"), "complete": False, "pages": []}


def read_progress(path):
    """
    Return the saved progress of this folder, or a new one if there is none.

    Stops if the saved harvest asked a different question (query, fields,
    page size or sort), because its pages could not be continued with this
    script's cursor.
    """
    if not os.path.exists(path):
        return new_progress()
    with open(path, encoding="utf-8") as handle:
        progress = json.load(handle)
    for key in ("query", "fields", "rows", "sort"):
        if progress[key] != new_progress()[key]:
            raise Stop(f"{path} was made with another {key}; start a new folder instead")
    return progress


def cell(record, name, nan_found=None):
    """
    Return one field of one record as it will be stored, unchanged.

    A missing field is None. Stops on a value whose type does not match the
    field description (say a list where a single text was expected), rather
    than change it to fit. The one exception: a box edge that comes back as
    the exact text "NaN" (DataONE's own "not a number") is stored as a
    numeric NaN, which means the same and stays apart from a missing field
    (null). Each such value is added to nan_found (a list) as (record id,
    field, original value), so it can be traced later.
    """
    value = record.get(name)
    if value is None:
        return None
    if name in SINGLE_TEXT and isinstance(value, str):
        return value
    if name in LIST_TEXT and isinstance(value, list) and all(isinstance(v, str) for v in value):
        return value
    if name in NUMBERS and isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if name in NUMBERS and value == NAN_TEXT:
        if nan_found is not None:
            nan_found.append((record.get("id"), name, value))
        return float("nan")
    raise Stop(f"record {record.get('id')!r}: field {name} came back as "
               f"{type(value).__name__} ({str(value)[:80]}), which this script does not store")


def page_table(docs, harvest_date, nan_found=None):
    """
    Return one page of records as an Arrow table in SCHEMA's columns.

    Stops if a record holds a field that was not asked for, so nothing is
    dropped without notice. Box edges stored as NaN are added to nan_found
    (see cell).
    """
    for record in docs:
        extra = set(record) - set(FIELDS)
        if extra:
            raise Stop(f"record {record.get('id')!r} holds fields that were not asked for: {sorted(extra)}")
    columns = {name: [cell(record, name, nan_found) for record in docs] for name in FIELDS}
    columns["harvest_date"] = [harvest_date] * len(docs)
    return pa.table(columns, schema=SCHEMA)


def record_nan_values(outdir, page, found):
    """
    Write the box values stored as NaN for one page to NAN_CSV, one row each
    (page, id, field, original value). Rows already there for the same page
    (from a page fetched again after a stop) are replaced, not repeated. The
    file is written through a temporary name and always exists once a page
    has been saved, even if it only holds its header.
    """
    path = os.path.join(outdir, NAN_CSV)
    rows = []
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as handle:
            rows = [r for r in csv.DictReader(handle) if int(r["page"]) != page]
    rows += [{"page": page, "id": rid, "field": field, "original_value": value} for rid, field, value in found]
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=NAN_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, path)


def page_path(outdir, number):
    """Return where page number `number` is saved."""
    return os.path.join(outdir, "pages", f"page_{number:05d}.parquet")


def save_page(table, path):
    """Write one page through a temporary name, so a crash never leaves half a file."""
    tmp = path + ".tmp"
    pq.write_table(table, tmp, compression="zstd")
    os.replace(tmp, path)


def join_pages(outdir, progress):
    """
    Join every saved page, in fetch order, into dataone_full_harvest.parquet.

    Copies one page at a time to keep memory low. Prints the rows written and
    how many ids appear more than once (they are kept: this step changes
    nothing). Returns the path written.
    """
    out = os.path.join(outdir, "dataone_full_harvest.parquet")
    tmp = out + ".tmp"
    writer, rows, seen, repeated = None, 0, set(), 0
    for page in progress["pages"]:
        table = pq.read_table(os.path.join(outdir, page["file"]))
        for record_id in table.column("id").to_pylist():
            repeated += record_id in seen
            seen.add(record_id)
        if writer is None:
            writer = pq.ParquetWriter(tmp, SCHEMA, compression="zstd")
        writer.write_table(table)
        rows += table.num_rows
    if writer is None:
        raise Stop("there are no pages to join")
    writer.close()
    os.replace(tmp, out)
    print(f"joined {len(progress['pages'])} pages: {rows:,} rows, {repeated:,} repeated ids -> {out}")
    return out


# ---------------------------------------------------------------------------
# The harvest
# ---------------------------------------------------------------------------

def harvest(outdir, max_pages=None, limit=REQUEST_LIMIT):
    """
    Fetch pages until the last one, or until max_pages new pages in this run,
    saving each page and the progress as soon as it arrives. A run that is
    stopped carries on from the last saved page next time.
    """
    progress_path = os.path.join(outdir, "progress.json")
    log_path = os.path.join(outdir, "requests_log.csv")
    progress = read_progress(progress_path)
    if progress["complete"]:
        print("this harvest is already complete")
        return progress
    os.makedirs(os.path.join(outdir, "pages"), exist_ok=True)
    fetched = 0
    while max_pages is None or fetched < max_pages:
        pages = progress["pages"]
        cursor = pages[-1]["next_cursor"] if pages else "*"
        reply = fetch_page(cursor, log_path, limit)
        docs = reply["response"]["docs"]
        next_cursor = reply.get("nextCursorMark")
        if not next_cursor:
            raise Stop("DataONE's answer has no nextCursorMark; cursor paging is not working")
        if docs:
            number = len(pages)
            path = page_path(outdir, number)
            nan_found = []
            save_page(page_table(docs, datetime.now().date().isoformat(), nan_found), path)
            record_nan_values(outdir, number, nan_found)
            if nan_found:
                print(f"  page {number}: {len(nan_found)} box values were the text \"NaN\", stored as NaN")
            pages.append({"page": number, "file": os.path.relpath(path, outdir), "cursor": cursor,
                          "next_cursor": next_cursor, "records": len(docs),
                          "num_found": reply["response"]["numFound"],
                          "fetched": datetime.now().isoformat(timespec="seconds"),
                          "seconds": round(reply["_seconds"], 2), "bytes": reply["_bytes"]})
            fetched += 1
            total = sum(p["records"] for p in pages)
            print(f"page {number}: {len(docs):,} records in {reply['_seconds']:.1f} s; "
                  f"{total:,} of {reply['response']['numFound']:,} so far")
        if next_cursor == cursor:
            # Solr's sign that there is nothing after this cursor.
            progress["complete"] = True
            progress["finished"] = datetime.now().isoformat(timespec="seconds")
        write_json(progress_path, progress)
        if progress["complete"]:
            break
        if not docs:
            raise Stop("DataONE returned an empty page but a new cursor; stopping to look at it")
    if progress["complete"]:
        found = {p["num_found"] for p in progress["pages"]}
        if len(found) > 1:
            print(f"note: the number of matching records changed during the harvest: {sorted(found)}")
        join_pages(outdir, progress)
    else:
        print(f"stopped after {fetched} new pages; run the same command again to carry on")
    return progress


def main():
    """Read the options, check the folder is ignored by git, and harvest."""
    parser = argparse.ArgumentParser(description="Harvest DataONE's latest metadata records.")
    parser.add_argument("--outdir", required=True, help="harvest folder, e.g. data/dataone/harvest_2026_11_18")
    parser.add_argument("--max-pages", type=int, default=None,
                        help="fetch at most this many new pages in this run (for tests)")
    parser.add_argument("--request-limit", type=int, default=REQUEST_LIMIT,
                        help=f"the most requests this folder may ever send (default {REQUEST_LIMIT})")
    args = parser.parse_args()
    try:
        check_ignored(args.outdir)
        harvest(args.outdir, args.max_pages, args.request_limit)
    except Stop as stop:
        print(f"STOPPED: {stop}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
