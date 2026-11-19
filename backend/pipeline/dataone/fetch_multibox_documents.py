"""
fetch_multibox_documents.py

Download the original metadata documents of the DataONE records that have
more than one bounding box, and read every box from them.

Why: DataONE's search index keeps only the FIRST box of a record in its four
box fields, and only the centre of every box in geohash_9 (checked on
2026-11-18 with exploration/dataone/check_dataone_bboxes.py). For the
latest records with more than one geohash_9 value, the only way to see every
box exactly is to read the original document. No text rule is used to choose
the records (Lucia, 2026-11-18): the list is every latest record with more
than one geohash_9 value, built from the versions file (mark_versions.py)
and saved as dataone_multibox_all.csv next to it.

Two modes:

  download (the default)
      For each id in the list, downloads
      https://cn.dataone.org/cn/v2/object/<url-encoded id> once and saves it
      exactly as received, gzipped, in originals/. originals/index.csv gets
      one row per document answered (id, file, HTTP status, bytes,
      formatId, seconds, when). Running the same command again skips every
      id the index already holds (saved, or answered 404), so a stopped run
      carries on where it stopped.

  --parse (no requests)
      Reads every saved document and writes originals_boxes.parquet, one row
      per box: id, box number in document order, north, south, east, west.
      EML: every boundingCoordinates inside a geographicCoverage, wherever it
      is in the document (dataset, entity or method level; the path is kept).
      ISO: every EX_GeographicBoundingBox. Other formats are listed in
      originals/parse_log.csv and skipped. The values are kept as written:
      a value that is not a number is null in its number column, its text
      is kept in <edge>_text, and the row is flagged.

Rules for talking to DataONE (set by Lucia for this task):
  - only the object endpoint above;
  - at most one request per second;
  - the BCBN User-Agent;
  - 403, 429 or any 5xx stops the run at once; so does any other status
    except 200 and 404, and a request that fails before an answer;
  - a 404 is logged and the run goes on, but more than 20 404s in total
    stop it;
  - at most REQUEST_LIMIT requests for this folder, counted from
    originals/requests_log.csv.

The originals folder must be ignored by git; this is checked with git
check-ignore before anything is written.

Run, from backend/:
    ../.venv/bin/python pipeline/dataone/fetch_multibox_documents.py              # download
    ../.venv/bin/python pipeline/dataone/fetch_multibox_documents.py --parse      # read the boxes
    ../.venv/bin/python pipeline/dataone/fetch_multibox_documents.py --check      # list and request limit only
"""

import argparse
import csv
import gzip
import hashlib
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from urllib.parse import quote

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import requests

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
HARVEST = os.path.join(BACKEND, "data", "dataone", "harvest_2026_11_18")
ID_LIST = os.path.join(HARVEST, "dataone_multibox_all.csv")
VERSIONS = os.path.join(HARVEST, "dataone_full_harvest_versions.parquet")
ORIGINALS = os.path.join(HARVEST, "originals")
INDEX = os.path.join(ORIGINALS, "index.csv")
REQUEST_LOG = os.path.join(ORIGINALS, "requests_log.csv")
PARSE_LOG = os.path.join(ORIGINALS, "parse_log.csv")
BOXES_OUT = os.path.join(HARVEST, "originals_boxes.parquet")

OBJECT = "https://cn.dataone.org/cn/v2/object/"
HEADERS = {"User-Agent": "BCBN-Dashboard (BC Biodiversity Network, UBC; "
                         "songyanf@student.ubc.ca)"}
MIN_GAP = 1.05          # seconds between the starts of two requests
REQUEST_LIMIT = 5400    # the most requests this folder may ever send
MAX_404 = 20            # more 404s than this stops the run
TIMEOUT = 300           # seconds to wait for one document

INDEX_COLUMNS = ["id", "file", "status", "bytes", "formatId", "seconds", "when"]
LOG_COLUMNS = ["when", "status", "seconds", "bytes", "url"]
EDGES = ["north", "south", "east", "west"]
# The element names of each edge, by format.
EML_EDGES = {"north": "northBoundingCoordinate", "south": "southBoundingCoordinate",
             "east": "eastBoundingCoordinate", "west": "westBoundingCoordinate"}
ISO_EDGES = {"north": "northBoundLatitude", "south": "southBoundLatitude",
             "east": "eastBoundLongitude", "west": "westBoundLongitude"}


class Stop(Exception):
    """The run must stop now: a refusal, too many 404s, the request limit, or an unexpected answer."""


_last = [0.0]


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

def check_ignored():
    """Stop unless git ignores the originals folder and the files written in it and next to it."""
    for path in (ORIGINALS, INDEX, os.path.join(ORIGINALS, "x.gz"), BOXES_OUT, ID_LIST):
        result = subprocess.run(["git", "check-ignore", "-q", path], cwd=BACKEND, capture_output=True, text=True)
        if result.returncode == 1:
            raise Stop(f"{path} is not ignored by git; nothing was written")
        if result.returncode != 0:
            raise Stop(f"git check-ignore could not check {path}: {result.stderr.strip()}")


def file_name(record_id):
    """Return the file name for one id: ids hold slashes and colons, so a hash of the id is used."""
    return hashlib.sha1(record_id.encode("utf-8")).hexdigest()[:24] + ".gz"


def append_row(path, columns, row):
    """Append one row to a CSV, writing the header first if the file is new."""
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        if new:
            writer.writeheader()
        writer.writerow(row)


def read_index():
    """Return the index as a DataFrame (empty, with its columns, when there is none yet)."""
    if not os.path.exists(INDEX):
        return pd.DataFrame(columns=INDEX_COLUMNS)
    return pd.read_csv(INDEX, dtype={"status": int}, keep_default_na=False)


def requests_sent():
    """Return how many requests the folder's request log says were sent, over every run."""
    if not os.path.exists(REQUEST_LOG):
        return 0
    with open(REQUEST_LOG, encoding="utf-8") as handle:
        return max(0, sum(1 for _ in handle) - 1)


def build_list():
    """
    Build the list of documents to download from the versions file: every
    record with version_status "latest" and more than one geohash_9 value,
    with no text filter. Write it to ID_LIST (id, datasource, number of
    geohash_9 values; sorted by id, so the same file gives the same list)
    and return it as a DataFrame with each record's formatId added.
    """
    table = pq.read_table(VERSIONS, columns=["id", "datasource", "formatId", "geohash_9", "version_status"])
    table = table.filter(pc.equal(table.column("version_status"), "latest"))
    counts = pc.fill_null(pc.list_value_length(table.column("geohash_9")), 0)
    table = table.filter(pc.greater(counts, 1))
    ids = pd.DataFrame({
        "id": table.column("id").to_pylist(),
        "datasource": [d.replace("urn:node:", "") for d in table.column("datasource").to_pylist()],
        "geohash_9_values": pc.list_value_length(table.column("geohash_9")).to_pylist(),
        "formatId": table.column("formatId").to_pylist(),
    }).sort_values("id").reset_index(drop=True)
    ids[["id", "datasource", "geohash_9_values"]].to_csv(ID_LIST + ".tmp", index=False, encoding="utf-8")
    os.replace(ID_LIST + ".tmp", ID_LIST)
    return ids


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def fetch(record_id):
    """
    Ask the object endpoint for one document and return (status, content,
    seconds). Keeps requests MIN_GAP seconds apart, refuses past
    REQUEST_LIMIT, logs every request sent, and raises Stop on 403, 429,
    any 5xx, any other status but 200 and 404, or a request that fails
    before an answer.
    """
    if requests_sent() >= REQUEST_LIMIT:
        raise Stop(f"the request limit of {REQUEST_LIMIT} for this folder is reached")
    wait = MIN_GAP - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    url = OBJECT + quote(record_id, safe="")
    start = time.time()
    _last[0] = start
    try:
        answer = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as error:
        append_row(REQUEST_LOG, LOG_COLUMNS, {"when": datetime.now().isoformat(timespec="seconds"),
                                              "status": f"error {type(error).__name__}",
                                              "seconds": f"{time.time() - start:.2f}", "bytes": 0, "url": url})
        raise Stop(f"the request for {record_id} failed before DataONE answered: {error}") from error
    seconds = time.time() - start
    append_row(REQUEST_LOG, LOG_COLUMNS, {"when": datetime.now().isoformat(timespec="seconds"),
                                          "status": answer.status_code, "seconds": f"{seconds:.2f}",
                                          "bytes": len(answer.content), "url": url})
    status = answer.status_code
    if status in (403, 429) or status >= 500:
        raise Stop(f"DataONE refused or failed: HTTP {status} for {record_id}\n{answer.text[:300]}")
    if status not in (200, 404):
        raise Stop(f"unexpected answer HTTP {status} for {record_id}\n{answer.text[:300]}")
    return status, answer.content, seconds


def save_document(record_id, content):
    """Save one document exactly as received, gzipped, through a temporary name; return its file name."""
    name = file_name(record_id)
    path = os.path.join(ORIGINALS, name)
    with gzip.open(path + ".tmp", "wb") as handle:
        handle.write(content)
    os.replace(path + ".tmp", path)
    return name


def download(only_ids=None):
    """
    Download every id not yet in the index (or only the ids given), one at
    a time, saving each document and its index row as soon as it arrives.
    Prints progress every 50 documents and a summary at the end.
    """
    os.makedirs(ORIGINALS, exist_ok=True)
    ids = build_list()
    if only_ids:
        ids = ids[ids["id"].isin(only_ids)]
        missing = set(only_ids) - set(ids["id"])
        if missing:
            raise Stop(f"not in {ID_LIST}: {sorted(missing)}")
    index = read_index()
    done = set(index["id"])
    not_found = int((index["status"] == 404).sum()) if len(index) else 0
    todo = ids[~ids["id"].isin(done)]
    print(f"{len(ids):,} ids in the list, {len(done):,} already answered, {len(todo):,} to download")
    started, fetched, total_bytes = time.time(), 0, 0
    for row in todo.itertuples():
        status, content, seconds = fetch(row.id)
        name = save_document(row.id, content) if status == 200 else ""
        append_row(INDEX, INDEX_COLUMNS, {"id": row.id, "file": name, "status": status, "bytes": len(content),
                                          "formatId": row.formatId, "seconds": f"{seconds:.2f}",
                                          "when": datetime.now().isoformat(timespec="seconds")})
        fetched += 1
        total_bytes += len(content)
        if status == 404:
            not_found += 1
            print(f"  404 for {row.id} ({not_found} so far)")
            if not_found > MAX_404:
                raise Stop(f"more than {MAX_404} documents answered 404; see {INDEX}")
        if fetched % 50 == 0:
            rate = (time.time() - started) / fetched
            print(f"  {len(done) + fetched:,} of {len(ids):,} answered; {total_bytes / 1e6:.1f} MB this run; "
                  f"about {rate * (len(todo) - fetched) / 60:.0f} minutes left")
    print(f"done: {fetched:,} documents this run, {total_bytes / 1e6:.1f} MB, "
          f"{(time.time() - started) / 60:.1f} minutes; {not_found} 404s in all")


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------

def local(tag):
    """Return an element's tag without its namespace."""
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def edge_text(element, name):
    """
    Return the text of the first descendant called `name` (any namespace),
    reading through an ISO gco:Decimal, stripped of spaces at both ends;
    None when there is no such element.
    """
    for child in element.iter():
        if local(child.tag) == name:
            return "".join(child.itertext()).strip()
    return None


def kind_of(root, format_id):
    """Return "EML", "ISO" or None (another format), from the formatId and the root element."""
    name = local(root.tag)
    if name == "eml" or "eml" in (format_id or ""):
        return "EML"
    if name in ("MD_Metadata", "MI_Metadata") or "isotc211" in (format_id or ""):
        return "ISO"
    return None


def path_of(element, parents, depth=5):
    """Return the tags above an element (up to `depth`), outermost first, joined with "/"."""
    names, node = [], element
    while node in parents and len(names) < depth:
        node = parents[node]
        names.append(local(node.tag))
    return "/".join(reversed(names))


def boxes_in(root, kind):
    """
    Return every box of one document as a list of dicts, in document order:
    the edges as numbers (None when missing or not a number), the edges'
    text when not a number, a flag, and where the box sits.
    """
    parents = {child: parent for parent in root.iter() for child in parent}
    if kind == "EML":
        wanted, names = "boundingCoordinates", EML_EDGES
    else:
        wanted, names = "EX_GeographicBoundingBox", ISO_EDGES
    found = []
    for element in root.iter():
        if local(element.tag) != wanted:
            continue
        if kind == "EML" and "geographicCoverage" not in path_of(element, parents, depth=1):
            continue
        row, problems = {}, []
        for edge in EDGES:
            text = edge_text(element, names[edge])
            row[f"{edge}_text"] = None
            if text is None or text == "":
                row[edge] = None
                problems.append(f"{edge} missing")
                continue
            try:
                row[edge] = float(text)
            except ValueError:
                row[edge] = None
                row[f"{edge}_text"] = text
                problems.append(f"{edge} not a number")
        row["flag"] = "; ".join(problems)
        row["path"] = path_of(element, parents)
        found.append(row)
    return found


def parse():
    """
    Read every saved document and write BOXES_OUT, one row per box; list
    documents that are another format or cannot be read in PARSE_LOG; and
    compare each document's box count with its geohash_9 count.
    """
    index = read_index()
    saved = index[index["status"] == 200]
    expected = build_list().set_index("id")["geohash_9_values"]
    rows, log = [], []
    for record in saved.itertuples():
        path = os.path.join(ORIGINALS, record.file)
        try:
            with gzip.open(path, "rb") as handle:
                root = ET.fromstring(handle.read())
        except (OSError, ET.ParseError) as error:
            log.append({"id": record.id, "formatId": record.formatId, "problem": f"cannot read: {error}"})
            continue
        kind = kind_of(root, record.formatId)
        if kind is None:
            log.append({"id": record.id, "formatId": record.formatId,
                        "problem": f"other format (root {local(root.tag)}), skipped"})
            continue
        boxes = boxes_in(root, kind)
        if not boxes:
            log.append({"id": record.id, "formatId": record.formatId, "problem": "no box found"})
        for number, box in enumerate(boxes, start=1):
            rows.append({"id": record.id, "format": kind, "box": number, **{e: box[e] for e in EDGES},
                         **{f"{e}_text": box[f"{e}_text"] for e in EDGES}, "flag": box["flag"], "path": box["path"]})
    schema = pa.schema([("id", pa.string()), ("format", pa.string()), ("box", pa.int64())]
                       + [(e, pa.float64()) for e in EDGES] + [(f"{e}_text", pa.string()) for e in EDGES]
                       + [("flag", pa.string()), ("path", pa.string())])
    table = pa.Table.from_pylist(rows, schema=schema)
    pq.write_table(table, BOXES_OUT + ".tmp", compression="zstd")
    os.replace(BOXES_OUT + ".tmp", BOXES_OUT)
    pd.DataFrame(log, columns=["id", "formatId", "problem"]).to_csv(PARSE_LOG, index=False, encoding="utf-8")
    frame = table.to_pandas()
    counts = frame.groupby("id").size()
    compare = pd.DataFrame({"boxes": counts, "geohash_9_values": expected.reindex(counts.index)})
    compare["distinct_boxes"] = frame.drop_duplicates(["id"] + EDGES).groupby("id").size()
    same = (compare["boxes"] == compare["geohash_9_values"]).sum()
    same_distinct = (compare["distinct_boxes"] == compare["geohash_9_values"]).sum()
    print(f"documents read: {len(saved):,}; with boxes: {len(counts):,}; logged and skipped: {len(log)}")
    print(f"boxes: {len(frame):,}; flagged: {int((frame['flag'] != '').sum())}")
    print(f"box count equals geohash_9 count: {same} of {len(compare)}; "
          f"distinct boxes equal it: {same_distinct} of {len(compare)}")
    print(f"wrote {BOXES_OUT}")
    return compare


def check_limit():
    """
    Print whether the request limit covers the whole list: the requests
    already logged plus one request for every id the index does not hold
    yet. Sends nothing.
    """
    ids = build_list()
    index = read_index()
    left = int((~ids["id"].isin(set(index["id"]))).sum())
    sent = requests_sent()
    need = sent + left
    print(f"list: {len(ids):,} ids ({ID_LIST}); already answered: {len(set(index['id'])):,}; "
          f"to download: {left:,}")
    print(f"requests logged: {sent:,}; needed in all: {need:,}; limit: {REQUEST_LIMIT:,}; "
          f"room to spare: {REQUEST_LIMIT - need:,}" + ("" if need <= REQUEST_LIMIT else "  <-- NOT ENOUGH"))


def main():
    """Check the folder is ignored by git, then download (default), parse (--parse) or check the limit (--check)."""
    parser = argparse.ArgumentParser(description="Download and read the multi-box DataONE documents.")
    parser.add_argument("--parse", action="store_true", help="read the saved documents; no requests")
    parser.add_argument("--ids", nargs="+", help="download only these ids (for tests)")
    parser.add_argument("--check", action="store_true",
                        help="build the list and check the request limit covers it; no requests")
    args = parser.parse_args()
    try:
        check_ignored()
        if args.check:
            check_limit()
        elif args.parse:
            compare = parse()
            # Show every document for a small test, else only the first 20 that disagree.
            differ = compare[compare["boxes"] != compare["geohash_9_values"]]
            print(compare.to_string() if len(compare) <= 20 else differ.head(20).to_string())
        else:
            download(args.ids)
    except Stop as stop:
        print(f"STOPPED: {stop}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
