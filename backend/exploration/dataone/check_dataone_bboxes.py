"""
check_dataone_bboxes.py

When an original metadata record has several bounding boxes, what does
DataONE put in the single northBoundCoord, southBoundCoord, eastBoundCoord
and westBoundCoord fields of its search index? The first box, the last, a
box around all of them, or something else?

How it finds out:
  1. Two searches (one for EML records, one for ISO records) for
     latest-version metadata records whose index box touches BC's bounding
     rectangle and that have geohash_9 values. The records with more than
     one geohash_9 value are the ones likely to have several boxes.
  2. About five of each format are picked, from as many datasources as
     possible, and the original document of each is downloaded once from
     the object endpoint.
  3. Every bounding box in the document is read (EML: boundingCoordinates;
     ISO: EX_GeographicBoundingBox) and compared with the index box.

Rules for talking to DataONE (set by Lucia for this check): only the
search API and, for these documents, the object endpoint; at most one
request per second; stop at once if DataONE refuses; at most 20 requests.
Every reply is cached in the output folder, so running the script again
sends nothing. Every request actually sent is in requests_log.csv.

Output: ~/Desktop/BCBN/Results/dataone_bbox_check_2026-11-18/
    documents/<n>.xml       the original documents
    candidates_<format>_round<n>.csv  the search results, with their geohash_9 counts
    bbox_check.csv          one row per record checked
    boxes.csv               one row per box found in a document

Run, from backend/:
    ../.venv/bin/python exploration/dataone/check_dataone_bboxes.py
"""

import csv
import hashlib
import json
import os
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from urllib.parse import quote

import pandas as pd
import requests

SOLR = "https://cn.dataone.org/cn/v2/query/solr/"
OBJECT = "https://cn.dataone.org/cn/v2/object/"
HEADERS = {"User-Agent": "BCBN-Dashboard (BC Biodiversity Network, UBC; "
                         "songyanf@student.ubc.ca)"}
MIN_GAP = 1.05          # seconds between the starts of two requests
MAX_REQUESTS = 20       # Lucia's limit for this check

OUT_DIR = os.path.expanduser("~/Desktop/BCBN/Results/dataone_bbox_check_2026-11-18")
CACHE_DIR = os.path.join(OUT_DIR, "cache")
DOC_DIR = os.path.join(OUT_DIR, "documents")
LOG_PATH = os.path.join(OUT_DIR, "requests_log.csv")

# BC's bounding rectangle, as in the probes.
BC_N, BC_S, BC_W, BC_E = 60.05, 48.20, -139.10, -114.00
TOUCHES_BC = (f"(southBoundCoord:[* TO {BC_N}] AND northBoundCoord:[{BC_S} TO *] "
              f"AND westBoundCoord:[* TO {BC_E}] AND eastBoundCoord:[{BC_W} TO *])")
BASE = "formatType:METADATA AND -obsoletedBy:* AND geohash_9:*"

# The formatId values of each format, as DataONE writes them.
FORMATS = {
    "EML": ["eml://ecoinformatics.org/eml-2.0.0", "eml://ecoinformatics.org/eml-2.0.1",
            "eml://ecoinformatics.org/eml-2.1.0", "eml://ecoinformatics.org/eml-2.1.1",
            "https://eml.ecoinformatics.org/eml-2.2.0"],
    "ISO": ["http://www.isotc211.org/2005/gmd", "http://www.isotc211.org/2005/gmd-noaa",
            "http://www.isotc211.org/2005/gmd-pangaea"],
}
FIELDS = "id,formatId,datasource,title,northBoundCoord,southBoundCoord,eastBoundCoord,westBoundCoord,geohash_9"
ROWS = 500              # candidates asked for per search
PICK = 5                # records checked per format in the first round

# The searches, in order: (format, datasources left out, records to check,
# fill with one-geohash records). The first round sorted by id gave EML
# records from ARCTIC only and a single ISO record with several geohashes,
# so the second round leaves those datasources out.
ROUNDS = [("EML", None, PICK, False), ("ISO", None, PICK, False),
          ("EML", ["ARCTIC"], 4, False), ("ISO", ["PANGAEA", "RW"], 4, True)]
TOLERANCE = 1e-3        # degrees; the index stores the edges as 32-bit floats


class Stop(Exception):
    """DataONE refused a request, or the request limit is reached: stop now."""


_last = [0.0]


def sent_so_far():
    """Return how many requests the log says were sent, over every run."""
    if not os.path.exists(LOG_PATH):
        return 0
    with open(LOG_PATH, encoding="utf-8") as handle:
        return max(0, sum(1 for _ in handle) - 1)


def fetch(kind, url, params=None):
    """
    Send one GET request politely and return (status, text).

    Keeps requests at least MIN_GAP seconds apart, refuses to go past
    MAX_REQUESTS, logs every request sent, and stops on 403, 429 or a 503
    with Retry-After (DataONE refusing). No retries.
    """
    if sent_so_far() >= MAX_REQUESTS:
        raise Stop(f"the limit of {MAX_REQUESTS} requests is reached")
    wait = MIN_GAP - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    start = time.time()
    _last[0] = start
    try:
        answer = requests.get(url, params=params, headers=HEADERS, timeout=180)
    except requests.RequestException as error:
        raise Stop(f"request failed before an answer: {error}") from error
    new = not os.path.exists(LOG_PATH)
    with open(LOG_PATH, "a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if new:
            writer.writerow(["when", "kind", "status", "seconds", "bytes", "url"])
        writer.writerow([datetime.now().isoformat(timespec="seconds"), kind, answer.status_code,
                         f"{time.time() - start:.2f}", len(answer.content), answer.url])
    if answer.status_code in (403, 429) or (answer.status_code == 503 and "Retry-After" in answer.headers):
        raise Stop(f"DataONE refused: HTTP {answer.status_code} for {answer.url}\n{answer.text[:300]}")
    return answer.status_code, answer.text


def cached(key, make):
    """Return the cached reply stored under key, or call make() once and cache what it returns."""
    path = os.path.join(CACHE_DIR, hashlib.sha1(key.encode()).hexdigest()[:20] + ".json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    value = make()
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(value, handle)
    return value


def search(format_name, exclude=None):
    """
    Return the candidate records of one format (a list of Solr documents),
    leaving out the datasources in exclude (a list of short names).
    """
    ids = " OR ".join(f'"{f}"' for f in FORMATS[format_name])
    q = f"{BASE} AND {TOUCHES_BC} AND formatId:({ids})"
    for name in exclude or []:
        q += f' AND -datasource:"urn:node:{name}"'
    params = {"q": q, "fl": FIELDS,
              "rows": ROWS, "wt": "json", "sort": "id asc"}

    def make():
        """Ask the search API once; stop on anything but 200."""
        status, text = fetch("solr", SOLR, params)
        if status != 200:
            raise Stop(f"search for {format_name} answered {status}: {text[:300]}")
        return json.loads(text)["response"]["docs"]
    return cached(json.dumps(params, sort_keys=True), make)


def pick(docs, n=PICK, fill_single=False):
    """
    Pick up to n records with more than one geohash_9 value, one per
    datasource first (in the order found), then more from any datasource.
    With fill_single, when there are too few of those, the rest are filled
    with one-geohash records from datasources not picked yet, to check
    whether one geohash really means one box.
    """
    several = [d for d in docs if len(d.get("geohash_9") or []) > 1]
    if fill_single:
        seen_sources = {d["datasource"] for d in several}
        for d in docs:
            if len(several) >= n:
                break
            if d not in several and d["datasource"] not in seen_sources:
                several.append(d)
                seen_sources.add(d["datasource"])
    chosen, seen = [], set()
    for d in several:
        if d["datasource"] not in seen and len(chosen) < n:
            chosen.append(d)
            seen.add(d["datasource"])
    for d in several:
        if len(chosen) >= n:
            break
        if d not in chosen:
            chosen.append(d)
    return chosen


def document(pid, number):
    """Download one original metadata document once and return its text (None if not 200)."""
    path = os.path.join(DOC_DIR, f"{number:02d}.xml")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    status, text = fetch("object", OBJECT + quote(pid, safe=""))
    if status != 200:
        print(f"  object {pid}: HTTP {status}")
        return None
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return text


def local(tag):
    """Return an element's tag without its namespace."""
    return tag.rsplit("}", 1)[-1]


def number_in(element, name):
    """
    Return the number inside the first descendant called `name` (ignoring
    namespaces), reading through an ISO gco:Decimal if there is one; None if
    missing or not a number.
    """
    for child in element.iter():
        if local(child.tag) == name:
            text = "".join(child.itertext()).strip()
            try:
                return float(text)
            except ValueError:
                return None
    return None


def boxes_in(text, format_name):
    """
    Return every bounding box in a document, in document order, as dicts
    with w, e, s, n and where (the path of tags above the box, shortened).
    """
    root = ET.fromstring(text.encode("utf-8"))
    parents = {child: parent for parent in root.iter() for child in parent}
    tag, names = (("boundingCoordinates", ("westBoundingCoordinate", "eastBoundingCoordinate",
                                           "southBoundingCoordinate", "northBoundingCoordinate"))
                  if format_name == "EML" else
                  ("EX_GeographicBoundingBox", ("westBoundLongitude", "eastBoundLongitude",
                                                "southBoundLatitude", "northBoundLatitude")))
    found = []
    for element in root.iter():
        if local(element.tag) != tag:
            continue
        w, e, s, n = (number_in(element, name) for name in names)
        path, node = [], element
        while node in parents:
            node = parents[node]
            path.append(local(node.tag))
        found.append({"w": w, "e": e, "s": s, "n": n, "where": "/".join(reversed(path[:4]))})
    return found


def same(box, index_box):
    """Return True when two boxes agree on all four edges within TOLERANCE."""
    return all(box[k] is not None and abs(box[k] - index_box[k]) <= TOLERANCE for k in "wesn")


def around(boxes):
    """Return the box around all the given boxes (no box crosses the 180 degree line here)."""
    full = [b for b in boxes if None not in (b["w"], b["e"], b["s"], b["n"])]
    return {"w": min(b["w"] for b in full), "e": max(b["e"] for b in full),
            "s": min(b["s"] for b in full), "n": max(b["n"] for b in full)}


def rule_for(boxes, index_box):
    """
    Return which rule the index box follows: "only box", "first box",
    "last box", "box around all", "box k of N" or "something else". Several
    can hold at once (first and around all when the first box holds the
    others); all that hold are listed, joined with " = ".
    """
    if not boxes:
        return "no box in the document"
    rules = []
    if len(boxes) == 1 and same(boxes[0], index_box):
        return "only box"
    if same(boxes[0], index_box):
        rules.append("first box")
    if same(boxes[-1], index_box):
        rules.append("last box")
    if same(around(boxes), index_box):
        rules.append("box around all")
    if not rules:
        middle = [i for i, b in enumerate(boxes) if same(b, index_box)]
        rules.append(f"box {middle[0] + 1} of {len(boxes)}" if middle else "something else")
    return " = ".join(rules)


def geohash(lat, lon, length=9):
    """Return the geohash of a point (the standard base-32 encoding)."""
    alphabet = "0123456789bcdefghjkmnpqrstuvwxyz"
    lat_range, lon_range, bits, code, even = [-90.0, 90.0], [-180.0, 180.0], 0, "", True
    value = 0
    while len(code) < length:
        rng, x = (lon_range, lon) if even else (lat_range, lat)
        mid = (rng[0] + rng[1]) / 2
        value = value * 2 + (x >= mid)
        rng[0 if x >= mid else 1] = mid
        even, bits = not even, bits + 1
        if bits == 5:
            code, bits, value = code + alphabet[value], 0, 0
    return code


def main():
    """Search, pick, download, compare, and write the two CSVs."""
    for folder in (OUT_DIR, CACHE_DIR, DOC_DIR):
        os.makedirs(folder, exist_ok=True)
    rows, box_rows, number = [], [], 0
    try:
        done = set()
        for round_number, (format_name, exclude, n, fill) in enumerate(ROUNDS):
            docs = search(format_name, exclude)
            several = [d for d in docs if len(d.get("geohash_9") or []) > 1]
            pd.DataFrame([{**{k: d.get(k) for k in FIELDS.split(",") if k != "geohash_9"},
                           "geohash_9_values": len(d.get("geohash_9") or [])} for d in docs]) \
              .to_csv(os.path.join(OUT_DIR, f"candidates_{format_name}_round{round_number}.csv"),
                      index=False, encoding="utf-8-sig")
            print(f"{format_name} without {exclude}: {len(docs)} candidates, "
                  f"{len(several)} with more than one geohash_9 value")
            for d in pick([d for d in docs if d["id"] not in done], n, fill):
                done.add(d["id"])
                text = document(d["id"], number)
                index_box = {"w": d["westBoundCoord"], "e": d["eastBoundCoord"],
                             "s": d["southBoundCoord"], "n": d["northBoundCoord"]}
                boxes = boxes_in(text, format_name) if text else []
                hashes = d.get("geohash_9") or []
                centres = [geohash((b["s"] + b["n"]) / 2, (b["w"] + b["e"]) / 2) for b in boxes
                           if None not in (b["w"], b["e"], b["s"], b["n"])]
                rows.append({
                    "n": number, "id": d["id"], "format": format_name, "formatId": d["formatId"],
                    "datasource": d["datasource"].replace("urn:node:", ""), "title": d["title"],
                    "boxes_in_document": len(boxes), "distinct_boxes": len({(b["w"], b["e"], b["s"], b["n"]) for b in boxes}),
                    "geohash_9_values": len(hashes), "distinct_geohash_9": len(set(hashes)),
                    "geohash_9_equal_box_centres": sorted(set(hashes)) == sorted(set(centres)),
                    "index_w": index_box["w"], "index_e": index_box["e"],
                    "index_s": index_box["s"], "index_n": index_box["n"],
                    "rule": rule_for(boxes, index_box) if text else "document not downloaded",
                    "box_paths": "; ".join(sorted({b["where"] for b in boxes})),
                })
                for i, b in enumerate(boxes, start=1):
                    box_rows.append({"record": number, "id": d["id"], "box": i, **b,
                                     "centre_geohash_9": centres[i - 1] if i <= len(centres) else None,
                                     "centre_in_index_geohash_9": (centres[i - 1] in hashes) if i <= len(centres) else None})
                number += 1
    except Stop as stop:
        print(f"STOPPED: {stop}", file=sys.stderr)
    pd.DataFrame(rows).to_csv(os.path.join(OUT_DIR, "bbox_check.csv"), index=False, encoding="utf-8-sig")
    pd.DataFrame(box_rows).to_csv(os.path.join(OUT_DIR, "boxes.csv"), index=False, encoding="utf-8-sig")
    print(pd.DataFrame(rows)[["n", "format", "datasource", "boxes_in_document", "geohash_9_values",
                              "geohash_9_equal_box_centres", "rule"]].to_string(index=False) if rows else "no rows")
    print(f"requests sent in all: {sent_so_far()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
