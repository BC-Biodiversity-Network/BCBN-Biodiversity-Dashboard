"""
try_dryad_full_abstracts.py

Find out whether DataONE's original document for a Dryad record holds the
full abstract, before deciding to fetch about 37,000 of them (Lucia,
2026-11-23). DataONE's search index cut the abstracts of 51,218 Dryad
records at about 1,000 characters (they end with "...").

Five Dryad records from the BC label sets, whose index abstracts are cut:
  claude_bc_labels_first50.csv   rows 2, 8, 36
  claude_bc_labels_strict50.csv  rows 17, 25

Web access, for this test only: DataONE's object endpoint,
https://cn.dataone.org/cn/v2/object/<pid>, at most 5 requests in all,
counted from this folder's request log so a rerun never sends more; at
least MIN_GAP seconds apart; the BCBN User-Agent. A 403, a 429, any 5xx,
any other status but 200 and 404, or a request that fails before an answer
stops the script at once (a 404 is not a refusal). A document already saved
is not fetched again.

For each record: the document is saved unchanged in OUT_DIR; the script
says what format it is and where the abstract is (field name), compares it
with the index abstract (lengths, same start, ends with "..."), and prints
the text the index left out.

Run, from backend/:
    ../.venv/bin/python exploration/dataone/try_dryad_full_abstracts.py
"""

import csv
import html
import json
import os
import re
import sys
import time
from datetime import datetime
from urllib.parse import quote

import pandas as pd
import pyarrow.parquet as pq
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, "..", ".."))
VERSIONS = os.path.join(BACKEND, "data", "dataone", "harvest_2026_11_18", "dataone_full_harvest_versions.parquet")
RESULTS = os.path.expanduser("~/Desktop/BCBN/Results")
LABELS = [(os.path.join(RESULTS, "dataone_bc_label_set_2026-11-23", "claude_bc_labels_first50.csv"), ["2", "8", "36"]),
          (os.path.join(RESULTS, "dataone_bc_label_set_2026-11-23_strict", "claude_bc_labels_strict50.csv"),
           ["17", "25"])]
OUT_DIR = os.path.join(RESULTS, "dataone_dryad_abstract_test_2026-11-23")
REQUEST_LOG = os.path.join(OUT_DIR, "requests_log.csv")
LOG_COLUMNS = ["when", "pid", "status", "content_type", "bytes", "seconds", "url"]

OBJECT = "https://cn.dataone.org/cn/v2/object/"
HEADERS = {"User-Agent": "BCBN-Dashboard (BC Biodiversity Network, UBC; songyanf@student.ubc.ca)"}
REQUEST_LIMIT = 5
MIN_GAP = 1.05      # seconds between the starts of two requests
TIMEOUT = 120       # seconds to wait for one document
CUT_MARK = "..."

_last_start = [0.0]


class Stop(Exception):
    """Raised to stop at once: DataONE refused, failed, or the request limit is reached."""


def requests_sent():
    """Return how many requests the log says this folder has sent."""
    if not os.path.exists(REQUEST_LOG):
        return 0
    with open(REQUEST_LOG, encoding="utf-8") as handle:
        return sum(1 for _ in csv.DictReader(handle))


def log_request(row):
    """Append one request to the log, writing the header first if the log is new."""
    new = not os.path.exists(REQUEST_LOG)
    with open(REQUEST_LOG, "a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=LOG_COLUMNS)
        if new:
            writer.writeheader()
        writer.writerow(row)


def file_name(pid, content_type):
    """Return the file name a document is saved under: the pid made safe, and an extension from its type."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", pid)
    kind = (content_type or "").split(";")[0].strip().lower()
    extension = ".jsonld" if "json" in kind else ".xml" if "xml" in kind else ".bin"
    return safe + extension


def saved_document(pid):
    """Return the path of a document already saved for pid, or None."""
    if not os.path.isdir(OUT_DIR):
        return None
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", pid)
    for name in os.listdir(OUT_DIR):
        if os.path.splitext(name)[0] == safe:
            return os.path.join(OUT_DIR, name)
    return None


def fetch(pid):
    """
    Ask the object endpoint for one document, at most REQUEST_LIMIT requests
    in all and MIN_GAP seconds apart; log the request; save a 200 answer
    unchanged. Return (status, path or None). Raise Stop on a refusal, a
    failure or an unexpected status.
    """
    if requests_sent() >= REQUEST_LIMIT:
        raise Stop(f"the limit of {REQUEST_LIMIT} requests is reached; nothing more is asked")
    wait = MIN_GAP - (time.time() - _last_start[0])
    if wait > 0:
        time.sleep(wait)
    url = OBJECT + quote(pid, safe="")
    start = _last_start[0] = time.time()
    row = {"when": datetime.now().isoformat(timespec="seconds"), "pid": pid, "url": url}
    try:
        answer = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as error:
        log_request({**row, "status": f"error {type(error).__name__}", "content_type": "", "bytes": 0,
                     "seconds": f"{time.time() - start:.2f}"})
        raise Stop(f"the request for {pid} failed before DataONE answered: {error}") from error
    content_type = answer.headers.get("Content-Type", "")
    log_request({**row, "status": answer.status_code, "content_type": content_type, "bytes": len(answer.content),
                 "seconds": f"{time.time() - start:.2f}"})
    status = answer.status_code
    if status in (403, 429) or status >= 500:
        raise Stop(f"DataONE refused or failed: HTTP {status} for {pid}\n{answer.text[:300]}")
    if status == 404:
        return status, None
    if status != 200:
        raise Stop(f"unexpected answer HTTP {status} for {pid}\n{answer.text[:300]}")
    path = os.path.join(OUT_DIR, file_name(pid, content_type))
    with open(path + ".tmp", "wb") as handle:
        handle.write(answer.content)
    os.replace(path + ".tmp", path)
    return status, path


def chosen_records():
    """Return the five records as (label file name, row, pid, title)."""
    chosen = []
    for path, rows in LABELS:
        table = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
        for row in rows:
            match = table[table["row"] == row]
            if len(match) != 1 or match.iloc[0]["datasource"] != "DRYAD":
                sys.exit(f"STOPPED: row {row} of {os.path.basename(path)} is not one Dryad record")
            chosen.append((os.path.basename(path), row, match.iloc[0]["id"], match.iloc[0]["title"]))
    return chosen


def index_abstracts(pids):
    """Return the abstract DataONE's index holds for each pid (the latest record), by pid."""
    table = pq.read_table(VERSIONS, columns=["id", "abstract", "version_status"],
                          filters=[("id", "in", list(pids))]).to_pandas()
    table = table[table["version_status"] == "latest"].drop_duplicates("id")
    return dict(zip(table["id"], table["abstract"].fillna("")))


def text_of(value):
    """Return a JSON-LD value as text: a plain string, the @value of an object, or list items joined."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return text_of(value.get("@value", ""))
    if isinstance(value, list):
        return "\n".join(text_of(v) for v in value)
    return ""


def find_abstract(path):
    """
    Return (format, field, text) for a saved document: the field that holds
    the abstract and its text. JSON-LD (science-on-schema.org): the
    Dataset's "description"; XML: the first element named abstract or
    description. Returns an empty text when none is found.
    """
    raw = open(path, "rb").read()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        data = None
    if data is not None:
        nodes = data.get("@graph", [data]) if isinstance(data, dict) else data
        context = data.get("@context") if isinstance(data, dict) else None
        kind = "JSON-LD" + (f" (@context {json.dumps(context)[:80]})" if context else "")
        for node in nodes:
            if isinstance(node, dict) and "description" in node:
                types = node.get("@type")
                return kind, f"description (in the node of @type {types})", text_of(node["description"])
        return kind, "none found", ""
    text = raw.decode("utf-8", errors="replace")
    found = re.search(r"<(?:\w+:)?(abstract|description)\b[^>]*>(.*?)</(?:\w+:)?\1>", text, re.S)
    if found:
        return "XML", found.group(1), html.unescape(re.sub(r"<[^>]+>", " ", found.group(2)))
    return "unknown", "none found", ""


def squash(text):
    """Return text with runs of spaces and line breaks made single spaces."""
    return " ".join(text.split())


def left_out(index_text, full_text):
    """
    Return (same start, the text the index left out). The index abstract
    without its "..." is looked for at the start of the full text (spacing
    ignored); if it is not the exact start, its last 40 characters are
    looked for instead, and what follows them is returned.
    """
    stem = squash(index_text)
    if stem.endswith(CUT_MARK):
        stem = stem[:-len(CUT_MARK)].rstrip()
    full = squash(full_text)
    if full.startswith(stem):
        return True, full[len(stem):].strip()
    tail = stem[-40:]
    at = full.find(tail)
    return False, (full[at + len(tail):].strip() if at >= 0 else "")


def main():
    """Fetch (or reuse) the five documents, compare each abstract with the index's and print the report."""
    os.makedirs(OUT_DIR, exist_ok=True)
    chosen = chosen_records()
    index = index_abstracts([pid for _, _, pid, _ in chosen])
    for label_file, row, pid, title in chosen:
        print(f"\n===== {label_file} row {row}: {pid}\n      {title}")
        path = saved_document(pid)
        if path is None:
            try:
                status, path = fetch(pid)
            except Stop as error:
                print(f"STOPPED: {error}")
                break
            if status == 404:
                print("   404: DataONE has no object with this pid")
                continue
            print(f"   fetched, HTTP 200, saved as {os.path.basename(path)} ({os.path.getsize(path):,} bytes)")
        else:
            print(f"   already saved as {os.path.basename(path)}; not fetched again")
        kind, field, full = find_abstract(path)
        same_start, rest = left_out(index.get(pid, ""), full)
        print(f"   format: {kind}\n   abstract field: {field}")
        print(f"   index abstract: {len(index.get(pid, '')):,} characters, ends with '...': "
              f"{squash(index.get(pid, '')).endswith(CUT_MARK)}")
        print(f"   document abstract: {len(full):,} characters, ends with '...': {squash(full).endswith(CUT_MARK)}, "
              f"starts with the index text: {same_start}")
        print(f"   left out by the index ({len(rest):,} characters):\n   {rest if rest else '(nothing)'}")
    print(f"\nrequests sent from this folder in all: {requests_sent()} (limit {REQUEST_LIMIT}); log: {REQUEST_LOG}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
