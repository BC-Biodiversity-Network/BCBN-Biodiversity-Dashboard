"""
Third pass on DataONE: measure what a harvest of BC records would look like,
before anything is built.

The second pass (probe_dataone_2.py) showed that text:"British Columbia"
also matches author affiliations, so here only the title, abstract and
keywords count as saying a record is "about BC". This pass answers nine
questions, one function each (q1_versions ... q9_three_groups):

  1. how many metadata records there are, and how many are the latest version
  2. how many look like BC by name, by bounding box, and by place name
  3. what the HAKAI_IYS records are
  4. what the identifiers look like (DOI, sha256, other)
  5. how many BC candidates are already in our Lunaris harvest
  6. which fields the BC candidates fill, and where the licence is
  7. a random sample of BC candidates that Lunaris does not have
  8. how to find BC records that have no bounding box
  9. how big the "surely BC", "surely not BC" and "not sure" groups are

Rules for talking to DataONE, set by Lucia for this probe:
  - only the public search API (cn/v2/query/solr) and, for a small sample,
    the object endpoint (cn/v2/object);
  - at most one request per second;
  - stop at once if DataONE refuses or blocks a request;
  - keep the total under about 500 requests.

Every reply is kept in a cache folder next to the outputs, so running the
script again reads the cache and sends nothing. Every request actually sent
is written to requests_log.csv, which is the honest request count.

Outputs go to ~/Desktop/BCBN/Results/dataone_probe_2026-11/ (outside the
repo).

Usage (from backend/):

    python exploration/dataone/probe_dataone_3.py            # every question
    python exploration/dataone/probe_dataone_3.py --only q1 q2
"""

import argparse
import csv
import hashlib
import json
import os
import random
import re
import sys
import time
from datetime import datetime
from urllib.parse import quote

import pandas as pd
import requests

# The Lunaris keyword filter, used only for comparison in question 9.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "pipeline", "lunaris"))
import lunaris_keywords  # noqa: E402


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

SOLR = "https://cn.dataone.org/cn/v2/query/solr/"
OBJECT = "https://cn.dataone.org/cn/v2/object/"
HEADERS = {"User-Agent": "BCBN-Dashboard (BC Biodiversity Network, UBC; "
                         "songyanf@student.ubc.ca)"}

MIN_GAP = 1.05          # seconds between two requests, a little over one
MAX_REQUESTS = 490      # hard stop, under the ~500 Lucia allowed

OUT_DIR = os.path.expanduser("~/Desktop/BCBN/Results/dataone_probe_2026-11")
CACHE_DIR = os.path.join(OUT_DIR, "cache")
LOG_PATH = os.path.join(OUT_DIR, "requests_log.csv")

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
LUNARIS_HARVEST = os.path.join(
    BACKEND, "exploration/lunaris/harvest_2026_10_20/lunaris_full_harvest.parquet")
LUNARIS_RAW_XML = os.path.join(
    BACKEND, "exploration/lunaris/harvest_2026_10_20/lunaris_raw_xml.parquet")
BC_PLACES = os.path.join(BACKEND, "pipeline/lunaris/bc_places.csv")

# ---------------------------------------------------------------------------
# Query pieces
# ---------------------------------------------------------------------------

METADATA = "formatType:METADATA"
LATEST = "-obsoletedBy:*"
# The base set for everything after question 1: latest-version metadata
# records. Question 1 decides whether archived records are left out as well
# (see BASE below, which q1 does not use).
NOT_ARCHIVED = "-archived:true"

# British Columbia's bounding box, the same generous box as the first two
# probes, so the numbers can be compared.
BC_N, BC_S, BC_W, BC_E = 60.05, 48.20, -139.10, -114.00

# Canada's bounding box, a little loose, used only to split off boxes that
# cover the whole country or more.
CA_N, CA_S, CA_W, CA_E = 70.0, 42.5, -140.0, -53.0

HAS_BOX = "northBoundCoord:[-90 TO 90]"

# A box entirely inside BC.
INSIDE_BC = (f"(northBoundCoord:[* TO {BC_N}] AND southBoundCoord:[{BC_S} TO *] "
             f"AND eastBoundCoord:[* TO {BC_E}] AND westBoundCoord:[{BC_W} TO *])")

# A box that overlaps BC: it is not entirely north, south, east or west of it.
OVERLAPS_BC = (f"(southBoundCoord:[* TO {BC_N}] AND northBoundCoord:[{BC_S} TO *] "
               f"AND westBoundCoord:[* TO {BC_E}] AND eastBoundCoord:[{BC_W} TO *])")

# A box that covers all of BC (BC sits inside it).
COVERS_BC = (f"(northBoundCoord:[{BC_N} TO *] AND southBoundCoord:[* TO {BC_S}] "
             f"AND westBoundCoord:[* TO {BC_W}] AND eastBoundCoord:[{BC_E} TO *])")

# A box that covers all of Canada (the loose box above) or more.
COVERS_CANADA = (f"(northBoundCoord:[{CA_N} TO *] AND southBoundCoord:[* TO {CA_S}] "
                 f"AND westBoundCoord:[* TO {CA_W}] AND eastBoundCoord:[{CA_E} TO *])")

# A box that crosses the 180th meridian is written with west greater than
# east. The overlap test above assumes west is less than east, so these are
# counted separately (question 2) to see whether they matter.

# The fields that say what a record is about. "keywords" in DataONE's index
# is an exact-value string field (keywords:"British Columbia" matches only a
# keyword that is exactly those two words), so phrase searches go to its
# tokenised copy, keywordsText, instead.
SUBJECT_FIELDS = ("title", "abstract", "keywordsText")


def in_subject(phrase):
    """A Solr clause matching a phrase in the title, abstract or keywords."""
    p = phrase.replace('"', '\\"')
    return "(" + " OR ".join(f'{f}:"{p}"' for f in SUBJECT_FIELDS) + ")"


def any_in_subject(phrases):
    """A Solr clause matching any of several phrases in the subject fields."""
    return "(" + " OR ".join(in_subject(p) for p in phrases) + ")"


BC_NAMED = any_in_subject(["British Columbia", "Colombie-Britannique"])
FIVE_PLACES = ["Vancouver Island", "Fraser River", "Haida Gwaii", "Okanagan",
               "Salish Sea"]

# Question 9, group (ii): broad regions that include BC.
BROAD_REGIONS = ["Pacific Northwest", "Northeast Pacific", "North East Pacific",
                 "NE Pacific", "Salish Sea", "Cascadia", "Columbia Basin",
                 "Western Canada", "Canada", "North America", "Gulf of Alaska",
                 "Rocky Mountains"]
# Question 9, group (iii): names that can mean BC or somewhere else. Most come
# from the 'no' rows of bc_places.csv. "Golden" is left out on purpose: as a
# word it matches golden eagles and golden algae, not the town.
AMBIGUOUS_NAMES = ["Victoria", "Vancouver", "Georgia", "Columbia River",
                   "Peace River", "Strait of Juan de Fuca", "Sunshine Coast",
                   "Vernon", "Richmond", "Surrey"]

SHORT_FIELDS = ("id,seriesId,datasource,formatId,title,abstract,keywords,"
                "northBoundCoord,southBoundCoord,eastBoundCoord,westBoundCoord")


# ---------------------------------------------------------------------------
# Talking to DataONE
# ---------------------------------------------------------------------------

class Blocked(Exception):
    """DataONE refused or blocked a request; the whole probe must stop."""


class Budget(Exception):
    """The request budget is spent; the whole probe must stop."""


_last_request = [0.0]
_sent = [0]


def _log(kind, url, status, seconds, size):
    """Append one line per request actually sent to requests_log.csv."""
    new = not os.path.exists(LOG_PATH)
    with open(LOG_PATH, "a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["when", "kind", "status", "seconds", "bytes", "url"])
        w.writerow([datetime.now().isoformat(timespec="seconds"), kind, status,
                    f"{seconds:.2f}", size, url])


def requests_sent_so_far():
    """How many requests the log says were sent, over every run."""
    if not os.path.exists(LOG_PATH):
        return 0
    with open(LOG_PATH, encoding="utf-8") as fh:
        return max(0, sum(1 for _ in fh) - 1)


def _fetch(kind, url, params=None):
    """
    Send one GET request, politely, and return (status, text, seconds).

    Waits so that two requests are never closer than MIN_GAP seconds,
    refuses to go past MAX_REQUESTS over all runs, and raises Blocked when
    DataONE answers in a way that means it does not want the request
    (403, 429, or a 503 that says to slow down).
    """
    if requests_sent_so_far() >= MAX_REQUESTS:
        raise Budget(f"request budget of {MAX_REQUESTS} reached")
    wait = MIN_GAP - (time.time() - _last_request[0])
    if wait > 0:
        time.sleep(wait)
    start = time.time()
    _last_request[0] = start
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=180)
    except requests.RequestException as exc:
        _log(kind, url, f"error {type(exc).__name__}", time.time() - start, 0)
        raise Blocked(f"request failed before an answer: {exc}") from exc
    seconds = time.time() - start
    _sent[0] += 1
    _log(kind, r.url, r.status_code, seconds, len(r.content))
    if r.status_code in (403, 429) or (r.status_code == 503 and "Retry-After" in r.headers):
        raise Blocked(f"DataONE answered {r.status_code} for {r.url}\n{r.text[:500]}")
    return r.status_code, r.text, seconds


def _cache_path(key):
    """Where the cached reply for one request lives."""
    h = hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]
    return os.path.join(CACHE_DIR, h + ".json")


def ask(q, rows=0, **extra):
    """
    Run one Solr query and return the parsed JSON, or None if Solr rejected
    the query itself (a 400, which means our syntax, not a block).

    Replies are cached on disk by their parameters, so the same question
    asked twice costs one request.
    """
    params = {"q": q, "wt": "json", "rows": rows, **extra}
    key = json.dumps(params, sort_keys=True)
    path = _cache_path(key)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            cached = json.load(fh)
        return cached["reply"]
    status, text, seconds = _fetch("solr", SOLR, params)
    reply = None
    if status == 200:
        reply = json.loads(text)
        reply["_seconds"] = seconds
    else:
        print(f"  Solr said {status} for q={q[:120]}...  {text[:200]}")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"params": params, "status": status, "reply": reply}, fh)
    return reply


def count(q, **extra):
    """How many records match a query, or None if the query was rejected."""
    d = ask(q, **extra)
    return d["response"]["numFound"] if d else None


def facet(q, field="datasource", limit=-1, **extra):
    """
    Count the records matching a query by the values of one field.

    Returns (total, {value: count}) or (None, {}) if the query was rejected.
    """
    d = ask(q, facet="true", **{"facet.field": field, "facet.limit": limit,
                                "facet.mincount": 1}, **extra)
    if not d:
        return None, {}
    raw = d["facet_counts"]["facet_fields"][field]
    return d["response"]["numFound"], {raw[i]: raw[i + 1] for i in range(0, len(raw), 2)}


def fetch_all(q, fl, label, page=1000, sort="id asc"):
    """
    Download every record matching a query, with cursorMark paging.

    The pages are cached like every other reply, so a second run is free.
    Prints the number of pages so the request cost is visible.
    """
    docs, cursor, pages = [], "*", 0
    while True:
        d = ask(q, rows=page, fl=fl, sort=sort, cursorMark=cursor)
        if d is None:
            raise RuntimeError(f"paging failed for {label}")
        docs.extend(d["response"]["docs"])
        pages += 1
        nxt = d.get("nextCursorMark")
        if not nxt or nxt == cursor:
            break
        cursor = nxt
    print(f"  {label}: {len(docs):,} records in {pages} pages")
    return docs


def fetch_object(pid):
    """
    Download one metadata document from the object endpoint, cached.

    Returns the document text, or None if DataONE did not return it.
    """
    path = _cache_path("object:" + pid)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)["text"]
    status, text, _ = _fetch("object", OBJECT + quote(pid, safe=""))
    keep = text if status == 200 else None
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"pid": pid, "status": status, "text": keep}, fh)
    return keep


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def AND(*parts):
    """Join query parts with AND, leaving out empty ones."""
    return " AND ".join(p for p in parts if p)


def save(frame, name):
    """Write a table to the output folder and say where it went."""
    path = os.path.join(OUT_DIR, name)
    frame.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"  -> {path}")
    return path


def heading(text):
    """Print a section heading."""
    print()
    print("=" * 78)
    print(text)
    print("=" * 78)


def short_ds(ds):
    """Drop the 'urn:node:' prefix from a datasource name."""
    return str(ds).replace("urn:node:", "")


DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"'<>?&#]+", re.IGNORECASE)

# DOIs of funders (Crossref Funder Registry), which name who paid, not the dataset.
FUNDER_PREFIX = "10.13039/"


def norm_doi(value):
    """
    Normalise one DOI for matching: lower case, no 'doi:' or resolver
    prefix, no trailing punctuation. Returns None if there is no DOI in it.
    """
    if value is None or (isinstance(value, float) and value != value):
        return None
    m = DOI_RE.search(str(value))
    if not m:
        return None
    return m.group(0).lower().rstrip(".,;)]}")


def norm_title(value):
    """Normalise a title for a rough match: lower case, letters and digits only."""
    if not isinstance(value, str):
        return ""
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def text_of(doc):
    """The title, abstract and keywords of one Solr record as one string."""
    parts = []
    for f in ("title", "abstract", "keywords"):
        v = doc.get(f)
        if isinstance(v, list):
            parts.append(" ".join(map(str, v)))
        elif v:
            parts.append(str(v))
    return " ".join(parts)


def box_of(doc):
    """(north, south, east, west) of one Solr record, or None if it has no box."""
    try:
        box = (float(doc["northBoundCoord"]), float(doc["southBoundCoord"]),
               float(doc["eastBoundCoord"]), float(doc["westBoundCoord"]))
    except (KeyError, TypeError, ValueError):
        return None
    # A record read back from a table has NaN where it had no box.
    return None if any(v != v for v in box) else box


def classify_box(box):
    """
    Say where one bounding box sits relative to BC's box: 'none', 'inside',
    'overlaps', 'overlaps (180th meridian)' or 'outside'. The same tests as
    the Solr clauses, done here for records already downloaded.
    """
    if box is None:
        return "none"
    n, s, e, w = box
    if s <= BC_N and n >= BC_S:
        if w > e:  # crosses the 180th meridian
            if e >= BC_W or w <= BC_E:
                return "overlaps (180th meridian)"
            return "outside"
        if n <= BC_N and s >= BC_S and e <= BC_E and w >= BC_W:
            return "inside"
        if w <= BC_E and e >= BC_W:
            return "overlaps"
    return "outside"


def landing_page(pid):
    """A link to the dataset page on search.dataone.org."""
    return "https://search.dataone.org/view/" + quote(pid, safe="")


# ---------------------------------------------------------------------------
# Question 1: versions and archived records
# ---------------------------------------------------------------------------

def count_ids(ids, extra=None, chunk=40):
    """
    Count how many of a list of ids are in the index (optionally also
    matching an extra clause). Asked in chunks, because DataONE rejects a
    URL longer than about 8,000 characters (414).
    """
    total = 0
    for i in range(0, len(ids), chunk):
        part = " OR ".join(f'"{x}"' for x in ids[i:i + chunk])
        n = count(AND(f"id:({part})", extra))
        if n is None:
            return None
        total += n
    return total


def follow_chain_back(pid, steps=4):
    """
    Walk a version chain backwards from one record through 'obsoletes'.

    Returns the list of ids met, newest first, at most steps + 1 long.
    """
    chain = [pid]
    current = pid
    for _ in range(steps):
        d = ask(f'id:"{current}"', rows=1, fl="id,obsoletes,obsoletedBy")
        docs = d["response"]["docs"] if d else []
        older = docs[0].get("obsoletes") if docs else None
        if not older:
            break
        chain.append(older)
        current = older
    return chain


def random_page(q, fl, n_total, rows=100, seed=1):
    """
    One page of records from a random place in a query's results, sorted by
    id so the page is not biased towards new uploads.
    """
    start = random.Random(seed).randint(0, max(0, n_total - rows))
    d = ask(q, rows=rows, fl=fl, sort="id asc", start=start)
    return d["response"]["docs"] if d else []


def chain_checks(latest_total):
    """
    Measure the two ways the latest-version filter can go wrong, on random
    samples rather than the newest records:

      over-keep: a latest record replaces an older one, but the older one was
                 never marked obsoletedBy, so both pass the filter;
      vanish:    an obsoleted record names a newer version that is not in the
                 public index, so no version of that dataset passes.

    Also counts distinct seriesId values among latest records, to see
    whether seriesId would catch the doubles.
    """
    out = []
    replacing = count(AND(METADATA, LATEST, "obsoletes:*"))
    docs = random_page(AND(METADATA, LATEST, "obsoletes:*"), "id,obsoletes", replacing,
                       rows=200, seed=11)
    older = sorted({d["obsoletes"] for d in docs if d.get("obsoletes")})
    both = count_ids(older, LATEST)
    present = count_ids(older)
    out.append({"check": "over-keep: older version also passes the filter",
                "sampled": len(older), "older_version_in_index": present,
                "bad": both,
                "note": "random page of latest records that replace an older one"})
    print(f"  over-keep: of {len(older)} older versions, {present} are indexed and "
          f"{both} still pass -obsoletedBy:*")

    obsoleted = count(AND(METADATA, "obsoletedBy:*"))
    docs = random_page(AND(METADATA, "obsoletedBy:*"), "id,obsoletedBy", obsoleted,
                       rows=200, seed=12)
    newer = sorted({d["obsoletedBy"] for d in docs})
    present = count_ids(newer)
    out.append({"check": "vanish: newer version not in the public index",
                "sampled": len(newer), "older_version_in_index": None,
                "bad": len(newer) - (present or 0),
                "note": f"{present} of the newer versions are indexed"})
    print(f"  vanish: of {len(newer)} newer versions named by a random page of "
          f"obsoleted records, {present} are in the index")

    d = ask(AND(METADATA, LATEST), **{"json.facet": json.dumps(
        {"series": "unique(seriesId)"})})
    distinct = d.get("facets", {}).get("series") if d else None
    with_series = count(AND(METADATA, LATEST, "seriesId:*"))
    out.append({"check": "latest records with a seriesId, and distinct seriesIds",
                "sampled": with_series, "older_version_in_index": None,
                "bad": (with_series - distinct) if distinct else None,
                "note": f"distinct seriesId: {distinct}"})
    print(f"  seriesId: {with_series:,} latest records carry one, {distinct} distinct")
    return out


def q1_versions():
    """
    Count metadata records, latest versions and archived records, and check
    on a few examples that the 'latest version' filter leaves one record per
    dataset.
    """
    heading("1. VERSIONS AND ARCHIVED RECORDS")
    rows = []

    def add(label, q):
        """Count one query and keep it for the table."""
        n = count(q)
        rows.append({"what": label, "count": n, "query": q})
        print(f"  {label:62s} {n if n is not None else 'rejected':>12,}")
        return n

    total = add("every object in the index", "*:*")
    meta = add("metadata records", METADATA)
    latest = add("metadata, latest version (-obsoletedBy:*)", AND(METADATA, LATEST))
    add("metadata, obsoleted (obsoletedBy:*)", AND(METADATA, "obsoletedBy:*"))
    add("metadata, archived:true", AND(METADATA, "archived:true"))
    add("metadata, archived:false", AND(METADATA, "archived:false"))
    add("metadata, latest and archived", AND(METADATA, LATEST, "archived:true"))
    add("metadata, latest and not archived", AND(METADATA, LATEST, NOT_ARCHIVED))
    add("metadata, latest, with a seriesId", AND(METADATA, LATEST, "seriesId:*"))
    add("metadata, latest, that replace an older one (obsoletes:*)",
        AND(METADATA, LATEST, "obsoletes:*"))
    save(pd.DataFrame(rows), "q1_counts.csv")

    # Archived and latest, by datasource: who archives records.
    _, arch = facet(AND(METADATA, LATEST, "archived:true"))
    _, lat = facet(AND(METADATA, LATEST))
    by_ds = pd.DataFrame({"latest": pd.Series(lat), "latest_archived": pd.Series(arch)}
                         ).fillna(0).astype(int).sort_values("latest", ascending=False)
    by_ds.index = [short_ds(i) for i in by_ds.index]
    by_ds.index.name = "datasource"
    save(by_ds.reset_index(), "q1_latest_by_datasource.csv")

    # Examples: the latest version of four datasets from four datasources,
    # each replacing at least two older versions. For each, the filter must
    # return one record out of the whole chain.
    print("\n  version chains:")
    d = ask(AND(METADATA, LATEST, "obsoletes:*", NOT_ARCHIVED), rows=200,
            fl="id,datasource,title,obsoletes", sort="dateUploaded desc")
    picked, seen_ds = [], set()
    for doc in d["response"]["docs"] if d else []:
        if doc["datasource"] in seen_ds:
            continue
        chain = follow_chain_back(doc["id"], steps=3)
        if len(chain) >= 3:
            picked.append((doc, chain))
            seen_ds.add(doc["datasource"])
        if len(picked) == 4:
            break
    ex = []
    for doc, chain in picked:
        ids = " OR ".join(f'"{c}"' for c in chain)
        every = count(f"id:({ids})")
        kept = count(f"id:({ids}) AND {LATEST}")
        ex.append({"datasource": short_ds(doc["datasource"]),
                   "title": doc.get("title"), "versions_found": len(chain),
                   "indexed": every, "left_by_latest_filter": kept,
                   "ids_newest_first": " | ".join(chain)})
        print(f"    {short_ds(doc['datasource']):12s} {len(chain)} versions, "
              f"{every} indexed, {kept} left: {str(doc.get('title'))[:60]}")
    save(pd.DataFrame(ex), "q1_version_examples.csv")

    # Broken chains: an obsoleted record whose newer version is not in the
    # index would vanish from a latest-only harvest. Check 100 of them.
    d = ask(AND(METADATA, "obsoletedBy:*"), rows=100, fl="id,obsoletedBy,datasource",
            sort="dateUploaded desc")
    newer = sorted({doc["obsoletedBy"] for doc in d["response"]["docs"]}) if d else []
    found = ask("id:(" + " OR ".join(f'"{n}"' for n in newer) + ")",
                rows=len(newer), fl="id,obsoletedBy,formatType")
    hits = found["response"]["docs"] if found else []
    still_old = sum(1 for h in hits if h.get("obsoletedBy"))
    print(f"\n  newest 100 obsoleted records: of {len(newer)} newer versions named by "
          f"obsoletedBy, {len(hits)} are in the index ({still_old} of those are "
          f"themselves obsoleted)")

    checks = chain_checks(latest)
    save(pd.DataFrame(checks), "q1_chain_checks.csv")
    return {"meta": meta, "latest": latest, "total": total,
            "broken_chain_check": (len(newer), len(hits))}


# ---------------------------------------------------------------------------
# Question 2: BC candidates, rule by rule
# ---------------------------------------------------------------------------

BASE = AND(METADATA, LATEST)

# The rules of question 2, as Solr clauses.
RULE_A = BC_NAMED
RULE_B = INSIDE_BC
RULE_C_PLAIN = f"({OVERLAPS_BC} AND -{INSIDE_BC})"
RULE_C_CANADA = f"({RULE_C_PLAIN} AND {COVERS_CANADA})"
RULE_C_REGION = f"({RULE_C_PLAIN} AND {COVERS_BC} AND -{COVERS_CANADA})"
RULE_C_EDGE = f"({RULE_C_PLAIN} AND -{COVERS_BC})"
RULE_D = any_in_subject(FIVE_PLACES)

# A box that crosses the 180th meridian is written with west greater than
# east, and the plain overlap test misses it. It overlaps BC when its
# latitudes reach BC and its longitudes, which run from west through 180 to
# east, reach BC's: east at or past BC's west edge, or west at or before
# BC's east edge. Solr cannot compare two fields in an ordinary clause, so
# the west > east part goes in a filter query (WRAPS).
WRAPS = {"fq": "{!frange l=0 incl=false}sub(westBoundCoord,eastBoundCoord)"}
RULE_C_WRAP = (f"({HAS_BOX} AND southBoundCoord:[* TO {BC_N}] AND northBoundCoord:[{BC_S} TO *]"
               f" AND (eastBoundCoord:[{BC_W} TO *] OR westBoundCoord:[* TO {BC_E}]))")
WRAP_CLAUSE = '_query_:"{!frange l=0 incl=false}sub(westBoundCoord,eastBoundCoord)"'
# The same test written inside the query itself, so it can be OR-ed with others.
RULE_C_WRAP_INLINE = f"({RULE_C_WRAP} AND {WRAP_CLAUSE})"
# Rule c as asked: overlaps BC but not inside it, including boxes that cross
# the 180th meridian.
RULE_C = f"({RULE_C_PLAIN} OR {RULE_C_WRAP_INLINE})"

RULES = [
    ("a: BC named in title, abstract or keywords", RULE_A),
    ("a, old way: keywords exact value instead of keywordsText",
     '(title:"British Columbia" OR abstract:"British Columbia" OR keywords:"British Columbia"'
     ' OR title:"Colombie-Britannique" OR abstract:"Colombie-Britannique"'
     ' OR keywords:"Colombie-Britannique")'),
    ("a, title only", '(title:"British Columbia" OR title:"Colombie-Britannique")'),
    ("a, abstract only field", '(abstract:"British Columbia" OR abstract:"Colombie-Britannique")'),
    ("a, keywords only field", '(keywordsText:"British Columbia" OR keywordsText:"Colombie-Britannique")'),
    ("any box at all", HAS_BOX),
    ("b: box entirely inside BC", RULE_B),
    ("b, of which a point (north = south)", RULE_B,
     {"fq": "{!frange l=0 u=0}sub(northBoundCoord,southBoundCoord)"}),
    ("c: box overlaps BC, not inside (c1 to c4)", RULE_C),
    ("c without c4 (west < east boxes only)", RULE_C_PLAIN),
    ("c1: covers all of Canada or more", RULE_C_CANADA),
    ("c2: covers all of BC, smaller than Canada", RULE_C_REGION),
    ("c3: crosses the BC border (partly in BC)", RULE_C_EDGE),
    ("box crosses the 180th meridian (west > east), any place", HAS_BOX,
     {"fq": "{!frange l=0 incl=false}sub(westBoundCoord,eastBoundCoord)"}),
    ("box crosses the 180th meridian and its latitudes reach BC",
     f"({HAS_BOX} AND southBoundCoord:[* TO {BC_N}] AND northBoundCoord:[{BC_S} TO *])",
     {"fq": "{!frange l=0 incl=false}sub(westBoundCoord,eastBoundCoord)"}),
    ("c4: crosses the 180th meridian and overlaps BC (missed by c)", RULE_C_WRAP, WRAPS),
    ("c4, and BC named", f"({RULE_C_WRAP} AND {RULE_A})", WRAPS),
    ("d: any of the five places", RULE_D),
] + [(f"d: {p}", in_subject(p)) for p in FIVE_PLACES] + [
    ("d beyond a: five places, BC not named", f"({RULE_D} AND -{RULE_A})"),
] + [(f"d beyond a: {p}", f"({in_subject(p)} AND -{RULE_A})") for p in FIVE_PLACES] + [
    ("a and b", f"({RULE_A} AND {RULE_B})"),
    ("a and c", f"({RULE_A} AND {RULE_C})"),
    ("b, not a", f"({RULE_B} AND -{RULE_A})"),
    ("a, no box at all", f"({RULE_A} AND -{HAS_BOX})"),
    ("a, box entirely outside BC", f"({RULE_A} AND {HAS_BOX} AND -{OVERLAPS_BC})"),
    ("a or b", f"({RULE_A} OR {RULE_B})"),
    ("a or b or c", f"({RULE_A} OR {RULE_B} OR {RULE_C})"),
    ("a or b or c2 or c3 (c without Canada-wide and 180th-meridian boxes)",
     f"({RULE_A} OR {RULE_B} OR {RULE_C_REGION} OR {RULE_C_EDGE})"),
    ("a or b or d", f"({RULE_A} OR {RULE_B} OR {RULE_D})"),
    ("extra: BC named only in site/place fields (siteText, placeKey)",
     '((siteText:"British Columbia" OR placeKey:"British Columbia") AND -' + RULE_A + ')'),
]


def q2_bc_rules():
    """
    Count latest-version metadata records for each BC rule, alone and
    combined, with the counts by datasource for every rule.
    """
    heading("2. BC CANDIDATES, RULE BY RULE (latest versions only)")
    rows, by_ds = [], {}
    for rule in RULES:
        label, clause = rule[0], rule[1]
        extra = rule[2] if len(rule) > 2 else {}
        n, ds = facet(AND(BASE, clause), **extra)
        rows.append({"rule": label, "count": n, "query": clause})
        by_ds[label] = ds
        print(f"  {label:62s} {n if n is not None else 'rejected':>10}")
    save(pd.DataFrame(rows), "q2_rules.csv")
    table = pd.DataFrame(by_ds).fillna(0).astype(int)
    table.index = [short_ds(i) for i in table.index]
    table.index.name = "datasource"
    table = table.sort_values("a or b or c", ascending=False)
    save(table.reset_index(), "q2_rules_by_datasource.csv")
    cols = ["a: BC named in title, abstract or keywords", "b: box entirely inside BC",
            "c: box overlaps BC, not inside (c1 to c4)", "a or b", "a or b or c"]
    print()
    print(table[cols].head(25).rename(columns=lambda c: c.split(":")[0]).to_string())


# ---------------------------------------------------------------------------
# Question 3: HAKAI_IYS
# ---------------------------------------------------------------------------

HAKAI = 'datasource:"urn:node:HAKAI_IYS"'


def title_pattern(title):
    """A title with every number replaced by #, to spot titles that differ only by a number."""
    return re.sub(r"\d+(?:[.,]\d+)?", "#", str(title or "")).strip()


def q3_hakai():
    """
    Find out what the HAKAI_IYS records are: how many there are by version,
    what twenty titles say, whether titles repeat with only a number
    changed, and how many land in the BC candidates.
    """
    heading("3. HAKAI_IYS")
    rows = []
    for label, q in [("every Hakai IYS metadata record, any version", AND(METADATA, HAKAI)),
                     ("latest version", AND(BASE, HAKAI)),
                     ("obsoleted", AND(METADATA, HAKAI, "obsoletedBy:*")),
                     ("any version, BC named (what probe 2 counted, with keywordsText)",
                      AND(METADATA, HAKAI, RULE_A)),
                     ("latest, a", AND(BASE, HAKAI, RULE_A)),
                     ("latest, b", AND(BASE, HAKAI, RULE_B)),
                     ("latest, c", AND(BASE, HAKAI, RULE_C)),
                     ("latest, a or b", AND(BASE, HAKAI, f"({RULE_A} OR {RULE_B})")),
                     ("latest, a or b or c", AND(BASE, HAKAI, f"({RULE_A} OR {RULE_B} OR {RULE_C})")),
                     ("latest, no box", AND(BASE, HAKAI, f"-{HAS_BOX}"))]:
        n = count(q)
        rows.append({"what": label, "count": n})
        print(f"  {label:66s} {n:>8,}")

    # Versions per dataset: the seriesIds with the most versions.
    _, series = facet(AND(METADATA, HAKAI), field="seriesId", limit=10)
    print("\n  seriesIds with the most versions:", list(series.values())[:10])
    n_series = ask(AND(METADATA, HAKAI), **{"json.facet": json.dumps(
        {"series": "unique(seriesId)"})})
    distinct = n_series.get("facets", {}).get("series") if n_series else None
    rows.append({"what": "distinct seriesId over every version", "count": distinct})
    print(f"  distinct seriesId over every version: {distinct}")
    save(pd.DataFrame(rows), "q3_hakai_counts.csv")

    # Every latest record, with the fields needed to read them.
    latest = fetch_all(AND(BASE, HAKAI), SHORT_FIELDS + ",dateUploaded,obsoletes",
                       "Hakai IYS latest records")
    frame = pd.DataFrame(latest)
    frame["title_pattern"] = frame["title"].map(title_pattern)
    save(frame.drop(columns=["abstract"], errors="ignore"), "q3_hakai_latest_records.csv")
    print("\n  twenty titles (random, latest versions):")
    for t in frame["title"].sample(min(20, len(frame)), random_state=3):
        print("   -", str(t)[:110])
    patterns = frame["title_pattern"].value_counts()
    save(patterns.rename_axis("title_with_numbers_as_#").reset_index(name="records"),
         "q3_hakai_title_patterns.csv")
    print(f"\n  {len(frame)} latest titles, {frame['title'].nunique()} distinct, "
          f"{len(patterns)} distinct once numbers are replaced by #")
    print("  most repeated patterns:")
    for pat, n in patterns.head(8).items():
        print(f"    {n:>4}  {pat[:100]}")

    # What the obsoleted versions are: one random page of them.
    old = random_page(AND(METADATA, HAKAI, "obsoletedBy:*"), "id,title,seriesId,dateUploaded",
                      count(AND(METADATA, HAKAI, "obsoletedBy:*")), rows=500, seed=31)
    oldf = pd.DataFrame(old)
    print(f"\n  a random page of {len(oldf)} obsoleted versions: {oldf['title'].nunique()} "
          f"distinct titles, {oldf['seriesId'].nunique()} distinct seriesIds")
    save(oldf, "q3_hakai_obsoleted_sample.csv")


# ---------------------------------------------------------------------------
# Question 8b, first half: how big a page DataONE gives, and how fast
# ---------------------------------------------------------------------------

def q8b_paging():
    """
    Test, with a handful of requests, the largest page DataONE returns, that
    cursorMark paging works, and how long one page of short fields takes.
    Then estimate what fetching the short fields of every latest-version
    metadata record would cost at one request per second.
    """
    heading("8b. PAGING TEST (short fields of latest-version metadata)")
    rows = []
    first = {}
    for size in (1000, 5000, 10000, 25000, 50000):
        d = ask(BASE, rows=size, fl=SHORT_FIELDS, sort="id asc", cursorMark="*")
        if not d:
            rows.append({"rows_asked": size, "rows_returned": None})
            continue
        got = len(d["response"]["docs"])
        nbytes = len(json.dumps(d["response"]["docs"]).encode("utf-8"))
        rows.append({"rows_asked": size, "rows_returned": got,
                     "seconds": round(d.get("_seconds", float("nan")), 2),
                     "bytes_json": nbytes, "next_cursor": bool(d.get("nextCursorMark"))})
        first[size] = d
        print(f"  rows={size:>6}: returned {got:>6}, {d.get('_seconds', 0):5.1f} s, "
              f"{nbytes / 1e6:5.1f} MB")
    # Second page with the cursor from the 1000-row page: must not repeat ids.
    if 1000 in first and first[1000].get("nextCursorMark"):
        d2 = ask(BASE, rows=1000, fl=SHORT_FIELDS, sort="id asc",
                 cursorMark=first[1000]["nextCursorMark"])
        ids1 = {x["id"] for x in first[1000]["response"]["docs"]}
        ids2 = [x["id"] for x in d2["response"]["docs"]] if d2 else []
        repeat = len(ids1 & set(ids2))
        follows = bool(ids2) and min(ids2) > max(ids1)
        print(f"  cursorMark page 2: {len(ids2)} records, {repeat} repeated from page 1, "
              f"continues in id order: {follows}")
        rows.append({"rows_asked": "1000, page 2 by cursorMark", "rows_returned": len(ids2),
                     "seconds": round(d2.get("_seconds", float("nan")), 2) if d2 else None,
                     "repeated_from_page_1": repeat, "continues_in_order": follows})
    save(pd.DataFrame(rows), "q8b_paging_test.csv")
    return rows


# ---------------------------------------------------------------------------
# The BC candidate set, downloaded once and reused by questions 4 to 7
# ---------------------------------------------------------------------------

TAXON_FIELDS = ["kingdom", "phylum", "class", "order", "family", "genus", "species",
                "scientificName"]
LICENCE_FIELDS = ["licenseName", "licenseUrl", "licenseDescription"]
CANDIDATE_FIELDS = (SHORT_FIELDS + ",origin,pubDate,dateUploaded,obsoletes,identifier,"
                    "namedLocation,placeKey,site,webUrl,rightsHolder,isPublic,"
                    + ",".join(TAXON_FIELDS + LICENCE_FIELDS))
RULE_ABC = f"({RULE_A} OR {RULE_B} OR {RULE_C})"
_candidates = {}


def candidates():
    """
    Every latest-version metadata record matching a or b or c, with columns
    saying which rules it matches (in_a, in_b, in_c, in_ab).

    Rule membership comes from asking DataONE for the ids of a and of b, not
    from re-implementing Solr's text matching here.
    """
    if "frame" in _candidates:
        return _candidates["frame"]
    docs = fetch_all(AND(BASE, RULE_ABC), CANDIDATE_FIELDS, "BC candidates (a or b or c)",
                     page=10000)
    frame = pd.DataFrame(docs)
    ids_a = {d["id"] for d in fetch_all(AND(BASE, RULE_A), "id", "ids of a", page=10000)}
    ids_b = {d["id"] for d in fetch_all(AND(BASE, RULE_B), "id", "ids of b", page=10000)}
    frame["in_a"] = frame["id"].isin(ids_a)
    frame["in_b"] = frame["id"].isin(ids_b)
    frame["box_class"] = [classify_box(box_of(r)) for r in frame.to_dict("records")]
    frame["in_c"] = frame["box_class"].isin(["overlaps", "overlaps (180th meridian)"])
    frame["in_ab"] = frame["in_a"] | frame["in_b"]
    frame["ds"] = frame["datasource"].map(short_ds)
    _candidates["frame"] = frame
    return frame


# ---------------------------------------------------------------------------
# Question 4: identifiers
# ---------------------------------------------------------------------------

RW_STYLE = re.compile(r"^(10\.\d{4,9})_([A-Za-z0-9.]+)_\d{8}T\d{6}Z?$")


def id_kind(pid):
    """Sort one DataONE id into doi, sha256, or another kind (named by its look)."""
    p = str(pid)
    if p.lower().startswith("doi:"):
        return "doi"
    if p.startswith("sha256:"):
        return "sha256"
    if RW_STYLE.match(p):
        return "other: DOI with _ for / and a timestamp (RW)"
    if re.match(r"^10\.\d{4,9}/", p):
        return "other: bare DOI (10.xxx/...)"
    if re.match(r"^https?://(dx\.)?doi\.org/", p, re.I):
        return "other: DOI as a doi.org link"
    if p.startswith("urn:uuid:"):
        return "other: urn:uuid"
    if re.match(r"^\{?[0-9A-Fa-f-]{36}\}?$", p):
        return "other: bare uuid"
    if re.match(r"^[0-9a-f]{32}$", p):
        return "other: 32-hex (md5-like)"
    if p.startswith("ark:"):
        return "other: ark"
    if p.startswith("http"):
        return "other: web address"
    return "other: local name"


def doi_of_record(row):
    """
    The DOI of one DataONE record, from its id or its seriesId, normalised,
    with where it came from. Returns (doi, source) or (None, None).
    """
    m = RW_STYLE.match(str(row.get("id")))
    if m:
        return f"{m.group(1)}/{m.group(2)}".lower(), "id (RW style)"
    for field in ("id", "seriesId"):
        v = row.get(field)
        # A DOI written as doi:..., as a bare 10.xxx/..., or inside any
        # resolver address (doi.org, dx.doi.org, doi.pangaea.de, ...).
        if isinstance(v, str) and DOI_RE.search(v):
            d = norm_doi(v)
            if d:
                return d, field
    return None, None


SERIES_IS_DOI = ('(seriesId:doi\\:* OR seriesId:https\\://doi.org/* '
                 'OR seriesId:http\\://doi.org/* OR seriesId:http\\://dx.doi.org/* '
                 'OR seriesId:https\\://dx.doi.org/*)')


def dois_in_document(text):
    """Every distinct DOI written in one metadata document, normalised, in order."""
    out = []
    for m in DOI_RE.finditer(text or ""):
        d = m.group(0).lower().rstrip(".,;)]}")
        if d.startswith(FUNDER_PREFIX):
            continue
        if d not in out:
            out.append(d)
    return out


def where_doi_sits(text, doi):
    """
    Name the element of a metadata document that holds a given DOI (the
    element name around its first appearance), or None.
    """
    if not doi or not text:
        return None
    i = text.lower().find(doi.split("/", 1)[1][:20].lower())
    if i < 0:
        return None
    before = text[max(0, i - 400):i]
    tags = re.findall(r"<([A-Za-z_][\w:.-]*)[^<>]*>[^<]*$", before)
    return tags[-1] if tags else "?"


def q4_identifiers():
    """
    Count id kinds by datasource (all latest records, and the BC candidates),
    and for ten sha256 records from different datasources find where the DOI
    is, in the seriesId or inside the metadata document.
    """
    heading("4. IDENTIFIERS")
    _, total = facet(BASE)
    _, doi = facet(AND(BASE, "id:doi\\:*"))
    _, sha = facet(AND(BASE, "id:sha256\\:*"))
    _, ser = facet(AND(BASE, SERIES_IS_DOI))
    table = pd.DataFrame({"latest": pd.Series(total), "id_doi": pd.Series(doi),
                          "id_sha256": pd.Series(sha), "seriesId_is_doi": pd.Series(ser)}
                         ).fillna(0).astype(int)
    table["id_other"] = table["latest"] - table["id_doi"] - table["id_sha256"]
    table.index = [short_ds(i) for i in table.index]
    table = table.sort_values("latest", ascending=False)
    table.loc["ALL"] = table.sum()
    save(table.rename_axis("datasource").reset_index(), "q4_id_kinds_all_latest.csv")
    print(table.loc[["ALL"]].to_string())

    frame = candidates().copy()
    frame["id_kind"] = frame["id"].map(id_kind)
    found = frame.apply(lambda r: doi_of_record(r.to_dict()), axis=1)
    frame["doi"] = [f[0] for f in found]
    frame["doi_from"] = [f[1] for f in found]
    frame["id_kind3"] = frame["id_kind"].where(frame["id_kind"].isin(["doi", "sha256"]), "other")
    cand = pd.crosstab(frame["ds"], frame["id_kind3"])
    cand["doi_in_id_or_seriesId"] = frame.groupby("ds")["doi"].apply(lambda x: x.notna().sum())
    cand["total"] = frame.groupby("ds").size()
    cand = cand.sort_values("total", ascending=False)
    cand.loc["ALL"] = cand.sum()
    save(cand.rename_axis("datasource").reset_index(), "q4_id_kinds_bc_candidates.csv")
    other = frame[frame["id_kind3"] == "other"].groupby(["ds", "id_kind"]).size()
    save(other.rename("records").reset_index(), "q4_other_id_kinds_bc_candidates.csv")
    print(cand.head(15).to_string())

    # Ten sha256 records from ten datasources: read the metadata document.
    sha = frame[frame["id_kind"] == "sha256"]
    order = sha["ds"].value_counts().index.tolist()
    picks = []
    for i, ds in enumerate(order):
        part = sha[sha["ds"] == ds]
        # Prefer a record whose seriesId is not a DOI, where one exists,
        # because those are the ones where the document is the only hope.
        no_doi = part[part["doi"].isna()]
        picks.append(("seriesId has no DOI (preferred)" if len(no_doi) else "random",
                      (no_doi if len(no_doi) else part).sample(1, random_state=i).iloc[0]))
        if len(picks) == 10:
            break
    # And six whose seriesId is a DOI, to see whether the document agrees.
    with_doi = sha[sha["doi"].notna()]
    for i, ds in enumerate(with_doi["ds"].value_counts().index[:6]):
        part = with_doi[with_doi["ds"] == ds]
        picks.append(("seriesId is a DOI", part.sample(1, random_state=100 + i).iloc[0]))
    rows = []
    for why, r in picks:
        text = fetch_object(r["id"])
        in_doc = dois_in_document(text)
        rows.append({
            "picked_because": why,
            "datasource": r["ds"], "id": r["id"], "seriesId": r.get("seriesId"),
            "formatId": r["formatId"], "doi_from_seriesId": r["doi"],
            "dois_in_document": " | ".join(in_doc[:6]),
            "n_dois_in_document": len(in_doc),
            "seriesId_doi_in_document": (r["doi"] in in_doc) if r["doi"] else None,
            "element_holding_first_doi": where_doi_sits(text, in_doc[0]) if in_doc else None,
            "document_fetched": text is not None,
        })
        print(f"  {r['ds']:12s} seriesId DOI: {str(r['doi']):34s} in document: "
              f"{len(in_doc)} DOI(s) {in_doc[:2]}")
    save(pd.DataFrame(rows), "q4_sha256_doi_check.csv")
    _candidates["with_doi"] = frame
    return frame


# ---------------------------------------------------------------------------
# Question 5: overlap with Lunaris
# ---------------------------------------------------------------------------

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
# DataONE datasources whose records Lunaris also harvests (by another road).
LUNARIS_ALSO = ["BOREALIS", "CANWIN", "DRYAD", "PANGAEA", "HAKAI_IYS"]
_lunaris = {}


def lunaris_keys():
    """
    Load the latest parsed Lunaris harvest and build three sets of match keys:
    normalised DOIs, dataset UUIDs (from ids and landing-page addresses),
    and normalised titles of at least 25 characters (shorter titles such as
    "Water quality" are too generic to match on).

    Lunaris keeps the DOI in its 'doi' column; for sources without a DOI
    (CanWIN, CIOOS, Hakai, open-data portals) that column holds the landing
    page address instead, which is where the UUIDs come from.
    """
    if _lunaris:
        return _lunaris
    lun = pd.read_parquet(LUNARIS_HARVEST, columns=["id", "doi", "title", "publisher"])
    lun["doi_norm"] = lun["doi"].map(norm_doi)
    uuids = {}
    for col in ("id", "doi"):
        for v, pub in zip(lun[col].astype(str), lun["publisher"]):
            for u in UUID_RE.findall(v):
                uuids.setdefault(u.lower(), pub)
    titles = {}
    for t, pub in zip(lun["title"], lun["publisher"]):
        nt = norm_title(t)
        if len(nt) >= 25:
            titles.setdefault(nt, pub)
    _lunaris.update({
        "frame": lun,
        "doi": dict(zip(lun["doi_norm"].dropna(), lun.loc[lun["doi_norm"].notna(), "publisher"])),
        "uuid": uuids, "title": titles})
    print(f"  Lunaris harvest: {len(lun):,} records, {len(_lunaris['doi']):,} DOIs, "
          f"{len(uuids):,} UUIDs, {len(titles):,} titles of 25+ characters")
    return _lunaris


def strip_lang(title):
    """Drop a trailing language tag such as '@en' that some sources add to titles."""
    return re.sub(r"@[a-z]{2}$", "", str(title or ""))


def match_lunaris(frame):
    """
    Add columns saying whether each DataONE record matches Lunaris by DOI,
    else by UUID, else by title, and which Lunaris publisher it matched.
    """
    keys = lunaris_keys()
    by_doi, by_uuid, by_title, pub = [], [], [], []
    for r in frame.to_dict("records"):
        d = r.get("doi")
        hit_d = d in keys["doi"] if d else False
        uu = set()
        for col in ("id", "seriesId", "webUrl"):
            v = r.get(col)
            v = " ".join(v) if isinstance(v, list) else str(v or "")
            uu.update(u.lower() for u in UUID_RE.findall(v))
        hit_u = next((u for u in uu if u in keys["uuid"]), None)
        nt = norm_title(strip_lang(r.get("title")))
        hit_t = len(nt) >= 25 and nt in keys["title"]
        by_doi.append(hit_d)
        by_uuid.append(bool(hit_u) and not hit_d)
        by_title.append(hit_t and not hit_d and not hit_u)
        pub.append(keys["doi"].get(d) if hit_d else keys["uuid"].get(hit_u) if hit_u
                   else keys["title"].get(nt) if hit_t else None)
    frame = frame.copy()
    frame["match_doi"] = by_doi
    frame["match_uuid_only"] = by_uuid
    frame["match_title_only"] = by_title
    frame["match_any"] = frame["match_doi"] | frame["match_uuid_only"] | frame["match_title_only"]
    frame["lunaris_publisher"] = pub
    return frame


def overlap_table(frame):
    """Counts by datasource: records, with a DOI, and matched by each key."""
    g = frame.groupby("ds")
    t = pd.DataFrame({
        "records": g.size(),
        "with_doi": g["doi"].apply(lambda x: x.notna().sum()),
        "match_doi": g["match_doi"].sum(),
        "match_uuid_only": g["match_uuid_only"].sum(),
        "match_title_only": g["match_title_only"].sum(),
        "match_any": g["match_any"].sum()}).sort_values("records", ascending=False)
    t.loc["ALL"] = t.sum()
    return t.astype(int)


def q5_lunaris():
    """
    Match the BC candidates against the latest Lunaris harvest by DOI (and,
    separately, by UUID and by title), by datasource, and read ten that
    do not match from datasources Lunaris also covers.
    """
    heading("5. OVERLAP WITH LUNARIS")
    frame = _candidates.get("with_doi")
    if frame is None:
        frame = q4_identifiers()
    frame = match_lunaris(frame)
    _candidates["matched"] = frame
    for label, part in [("a or b", frame[frame["in_ab"]]), ("a or b or c", frame)]:
        t = overlap_table(part)
        name = "q5_overlap_" + label.replace(" ", "_") + ".csv"
        save(t.rename_axis("datasource").reset_index(), name)
        print(f"\n  {label}:")
        print(t[(t["match_any"] > 0) | (t.index == "ALL")].to_string())

    also = frame[frame["ds"].isin(LUNARIS_ALSO)]
    t = overlap_table(also)
    t["not_matched"] = t["records"] - t["match_any"]
    save(t.rename_axis("datasource").reset_index(), "q5_lunaris_sources_unmatched.csv")
    print("\n  datasources Lunaris also covers (a or b or c):")
    print(t.to_string())

    # Ten unmatched records from those datasources, spread over them.
    miss = also[~also["match_any"]]
    picks = []
    for i, ds in enumerate(LUNARIS_ALSO):
        part = miss[miss["ds"] == ds]
        picks.append(part.sample(min(len(part), 2), random_state=i))
    picks = pd.concat(picks)
    # For each, does Lunaris hold the same DOI prefix, and a similar title?
    keys = lunaris_keys()
    lun = keys["frame"]
    rows = []
    for r in picks.to_dict("records"):
        prefix = (r["doi"] or "").split("/")[0] + "/" if r.get("doi") else None
        same_prefix = sum(1 for d in keys["doi"] if prefix and d.startswith(prefix))
        words = [w for w in norm_title(strip_lang(r["title"])).split() if len(w) > 4][:5]
        similar = lun[lun["title"].fillna("").str.lower().map(
            lambda t: sum(w in t for w in words) >= max(3, len(words) - 1))] if words else lun.iloc[:0]
        rows.append({"datasource": r["ds"], "id": r["id"], "seriesId": r.get("seriesId"),
                     "doi": r.get("doi"), "title": r["title"],
                     "rules": ",".join(k for k in ("in_a", "in_b", "in_c") if r[k]),
                     "lunaris_dois_with_same_prefix": same_prefix,
                     "lunaris_similar_titles": " | ".join(similar["title"].head(3).astype(str)),
                     "lunaris_similar_publishers": " | ".join(similar["publisher"].head(3).astype(str)),
                     "link": landing_page(r["id"])})
    save(pd.DataFrame(rows), "q5_unmatched_10.csv")
    for r in rows:
        print(f"  {r['datasource']:10s} {str(r['doi'])[:32]:32s} prefix in Lunaris: "
              f"{r['lunaris_dois_with_same_prefix']:>5}  {str(r['title'])[:70]}")
        if r["lunaris_similar_titles"]:
            print(f"             similar in Lunaris: {r['lunaris_similar_titles'][:110]}")
    return frame


# ---------------------------------------------------------------------------
# Question 6: which fields the BC candidates fill, and where the licence is
# ---------------------------------------------------------------------------

LICENCE_WORDS = re.compile(
    r"<([\w:.-]*(?:licen[cs]e|rights|useConstraints|accessConstraints|useLimitation|"
    r"otherConstraints|intellectualRights|distributionLiability|useconst|accconst|distliab)"
    r"[\w:.-]*)[^>]*>"
    r'|"(license|licence|conditionsOfAccess|usageInfo|copyrightNotice)"\s*:',
    re.IGNORECASE)


def filled(series):
    """True where a value is present and not empty (lists count when non-empty)."""
    return series.map(lambda v: isinstance(v, list) and len(v) > 0
                      or isinstance(v, str) and v.strip() != ""
                      or isinstance(v, (int, float, bool)) and v == v)


def licence_places(text):
    """
    The element or property names in one metadata document that look like
    licence or rights information, and a short excerpt of the first one.
    """
    if not text:
        return [], ""
    names, first = [], None
    for m in LICENCE_WORDS.finditer(text):
        name = m.group(1) or m.group(2)
        if name not in names:
            names.append(name)
        if first is None:
            first = m.start()
    excerpt = re.sub(r"<[^>]+>", " ", text[first:first + 400]) if first is not None else ""
    return names, re.sub(r"\s+", " ", excerpt)[:250]


def q6_fields():
    """
    Count how often the BC candidates fill each field, list the licence
    values, and read metadata documents from datasources that leave the
    licence fields empty, to see where the licence is.
    """
    heading("6. FIELDS OF THE BC CANDIDATES")
    frame = _candidates.get("matched")
    if frame is None:
        frame = q5_lunaris()
    frame = frame.copy()
    frame["box"] = frame["northBoundCoord"].notna()
    frame["any_taxon"] = pd.concat([filled(frame[c]) for c in TAXON_FIELDS if c in frame],
                                   axis=1).any(axis=1)
    frame["any_licence"] = pd.concat([filled(frame[c]) for c in LICENCE_FIELDS], axis=1).any(axis=1)
    cols = (["title", "abstract", "keywords", "origin", "pubDate", "box", "any_taxon"]
            + TAXON_FIELDS + LICENCE_FIELDS + ["any_licence", "rightsHolder", "namedLocation",
                                               "placeKey", "site", "webUrl"])
    rows = []
    for label, part in [("a or b", frame[frame["in_ab"]]), ("a or b or c", frame)]:
        for c in cols:
            n = int(part[c].sum()) if part[c].dtype == bool else int(filled(part[c]).sum())
            rows.append({"set": label, "field": c, "filled": n, "of": len(part),
                         "share": round(n / len(part), 3)})
    t = pd.DataFrame(rows)
    save(t, "q6_fields_filled.csv")
    print(t.pivot(index="field", columns="set", values="filled").reindex(cols).to_string())

    # Licence by datasource, and the most common licence values.
    lic = frame.groupby("ds").agg(records=("id", "size"), any_licence=("any_licence", "sum"),
                                  licenseName=("licenseName", lambda x: filled(x).sum()),
                                  licenseUrl=("licenseUrl", lambda x: filled(x).sum()),
                                  licenseDescription=("licenseDescription",
                                                      lambda x: filled(x).sum()))
    lic = lic.sort_values("records", ascending=False)
    save(lic.rename_axis("datasource").reset_index(), "q6_licence_by_datasource.csv")
    print("\n" + lic.head(20).to_string())
    names = frame["licenseName"].dropna().map(lambda v: " | ".join(v) if isinstance(v, list) else v)
    save(names.value_counts().rename_axis("licenseName").reset_index(name="records"),
         "q6_licence_values.csv")
    print("\n  most common licenseName values:")
    for v, n in names.value_counts().head(10).items():
        print(f"    {n:>6}  {str(v)[:90]}")

    # Documents: the sixteen read for question 4 (schema.org, sha256 ids),
    # plus one from each of the largest datasources whose records have no
    # licence in the index, so ten or more datasources are covered.
    docs = pd.read_csv(os.path.join(OUT_DIR, "q4_sha256_doi_check.csv"))
    picks = [(r["datasource"], r["id"]) for r in docs.to_dict("records")]
    have = {ds for ds, _ in picks}
    empty = lic[(lic["any_licence"] < lic["records"] * 0.5)].index.tolist()
    for i, ds in enumerate(empty):
        if ds in have:
            continue
        part = frame[(frame["ds"] == ds) & ~frame["any_licence"]]
        if len(part):
            picks.append((ds, part.sample(1, random_state=i)["id"].iloc[0]))
            have.add(ds)
        if len([1 for d in have if d in empty]) >= 6:
            break
    out = []
    by_id = frame.set_index("id")
    for ds, pid in picks:
        text = fetch_object(pid)
        names_found, excerpt = licence_places(text)
        rec = by_id.loc[pid] if pid in by_id.index else {}
        out.append({"datasource": ds, "id": pid,
                    "formatId": rec.get("formatId") if len(rec) else None,
                    "licenseName_in_index": rec.get("licenseName") if len(rec) else None,
                    "licence_elements_in_document": " | ".join(names_found),
                    "first_excerpt": excerpt})
        print(f"  {ds:12s} {str(rec.get('formatId'))[:38]:38s} {' | '.join(names_found)[:70]}")
    save(pd.DataFrame(out), "q6_licence_in_documents.csv")


# ---------------------------------------------------------------------------
# Question 7: a random sample of BC candidates Lunaris does not have
# ---------------------------------------------------------------------------

def as_cell(v, limit=None):
    """Turn a list or text value into one CSV cell, cut to a length if asked."""
    if isinstance(v, list):
        v = "; ".join(map(str, v))
    v = "" if v is None or (isinstance(v, float) and v != v) else str(v)
    v = re.sub(r"\s+", " ", v).strip()
    return v[:limit] + ("..." if limit and len(v) > limit else "")


def q7_sample():
    """
    Save 100 random BC candidates (a or b or c) that match Lunaris by no
    key, with what a person needs to judge them.
    """
    heading("7. RANDOM SAMPLE OF BC CANDIDATES NOT IN LUNARIS")
    frame = _candidates.get("matched")
    if frame is None:
        frame = q5_lunaris()
    miss = frame[~frame["match_any"]]
    pick = miss.sample(100, random_state=20261117)
    rows = []
    for r in pick.to_dict("records"):
        b = box_of(r)
        rows.append({
            "id": r["id"], "datasource": r["ds"], "title": as_cell(r.get("title")),
            "abstract_short": as_cell(r.get("abstract"), 300),
            "keywords": as_cell(r.get("keywords"), 200),
            "box_n_s_e_w": ", ".join(f"{x:g}" for x in b) if b else "",
            "box_class": r["box_class"],
            "rules": ",".join(k[3:] for k in ("in_a", "in_b", "in_c") if r[k]),
            "doi": r.get("doi") or "",
            "link": landing_page(r["id"]),
            "doi_link": f"https://doi.org/{r['doi']}" if r.get("doi") else ""})
    out = pd.DataFrame(rows).sort_values(["datasource", "title"])
    save(out, "q7_sample_not_in_lunaris.csv")
    print(f"  {len(miss):,} candidates match nothing in Lunaris; 100 drawn")
    print("  by datasource:", out["datasource"].value_counts().to_dict())
    print("  by rule:", out["rules"].value_counts().to_dict())


# ---------------------------------------------------------------------------
# Question 8a: BC place names from the Lunaris list
# ---------------------------------------------------------------------------

def bc_place_names():
    """
    The BC place names Lunaris uses (bc_places.csv): the 'yes' names, and
    the 'special' one (Vancouver), which counts unless followed by Island
    (listed separately) or by Washington or WA.
    """
    places = pd.read_csv(BC_PLACES)
    yes = places.loc[places["use"] == "yes", "name"].tolist()
    special = places.loc[places["use"] == "special", "name"].tolist()
    return yes, special


VANCOUVER_OK = re.compile(r"\bVancouver\b(?!\s*(?:Island|,?\s*(?:Washington|WA)\b))")


def place_hits(text, names):
    """Which of the names appear in a text as whole words, ignoring case."""
    low = text.lower()
    return [n for n in names if re.search(r"(?<![a-z])" + re.escape(n.lower()) + r"(?![a-z])", low)]


def q8a_places():
    """
    Search title, abstract and keywords for the Lunaris BC place names, in
    several queries so no URL is too long, and count what this adds beyond
    rule a (and beyond a or b), by datasource. Prints ten of the extra
    records to read.
    """
    heading("8a. BC PLACE NAMES FROM THE LUNARIS LIST")
    yes, special = bc_place_names()
    print(f"  {len(yes)} 'yes' names and {len(special)} special ({', '.join(special)})")
    docs = {}
    chunk = 12
    for i in range(0, len(yes), chunk):
        names = yes[i:i + chunk]
        got = fetch_all(AND(BASE, any_in_subject(names), f"-{RULE_A}"),
                        SHORT_FIELDS, f"names {i + 1}-{i + len(names)}, not a", page=10000)
        for d in got:
            docs[d["id"]] = d
    yes_ids = set(docs)
    # Vancouver: fetch every mention, then keep only those the Lunaris rule keeps.
    van = fetch_all(AND(BASE, any_in_subject(special), f"-{RULE_A}"), SHORT_FIELDS,
                    "Vancouver, not a", page=10000)
    van_kept = [d for d in van if VANCOUVER_OK.search(text_of(d))]
    for d in van_kept:
        docs.setdefault(d["id"], d)
    print(f"  Vancouver mentions (not a): {len(van):,}; kept by the Lunaris rule: {len(van_kept):,}")

    frame = pd.DataFrame(list(docs.values()))
    frame["ds"] = frame["datasource"].map(short_ds)
    frame["box_class"] = [classify_box(box_of(r)) for r in frame.to_dict("records")]
    frame["names_found"] = [", ".join(place_hits(text_of(r), yes)
                                      + (["Vancouver"] if VANCOUVER_OK.search(text_of(r)) else []))
                            for r in frame.to_dict("records")]
    frame["in_b"] = frame["box_class"] == "inside"
    frame["from_vancouver_only"] = ~frame["id"].isin(yes_ids)
    t = frame.groupby("ds").agg(extra_beyond_a=("id", "size"),
                                of_which_not_b=("in_b", lambda x: (~x).sum()),
                                of_which_vancouver_only=("from_vancouver_only", "sum"))
    t = t.sort_values("extra_beyond_a", ascending=False)
    t.loc["ALL"] = t.sum()
    save(t.rename_axis("datasource").reset_index(), "q8a_places_extra_by_datasource.csv")
    print(t.head(20).to_string())
    print("\n  by box:", frame["box_class"].value_counts().to_dict())

    # Which names bring in the most, as matched here (close to Solr's match).
    per_name = pd.Series([n for s in frame["names_found"] for n in s.split(", ") if n]
                         ).value_counts()
    save(per_name.rename_axis("name").reset_index(name="records"), "q8a_places_per_name.csv")
    print("\n  names bringing the most extra records:", per_name.head(12).to_dict())

    keep = frame.drop(columns=["abstract"]).copy()
    keep["abstract_short"] = frame["abstract"].map(lambda v: as_cell(v, 300))
    keep["link"] = keep["id"].map(landing_page)
    save(keep, "q8a_places_extra_records.csv")
    ten = frame.sample(10, random_state=88)
    print("\n  ten extra records to read:")
    for r in ten.to_dict("records"):
        print(f"   [{r['ds']}] ({r['names_found']}; box {r['box_class']}) {str(r['title'])[:100]}")
    save(keep[keep["id"].isin(ten["id"])], "q8a_ten_to_read.csv")
    _candidates["places_extra"] = frame
    return frame


# ---------------------------------------------------------------------------
# Question 9: surely BC, surely not BC, not sure
# ---------------------------------------------------------------------------

def phrase_re(phrases):
    """A whole-word, any-case matcher for a list of phrases."""
    return re.compile(r"(?<![a-z])(?:" + "|".join(re.escape(p.lower()) for p in phrases)
                      + r")(?![a-z])")


BC_NAME_RE = phrase_re(["British Columbia", "Colombie-Britannique"])
BROAD_RE = phrase_re(BROAD_REGIONS)
AMBIG_RE = phrase_re(AMBIGUOUS_NAMES)
BC_OUTER = None


def bc_polygon():
    """BC's boundary from the front end (ABMS pushed out 2 km), as a shapely polygon."""
    global BC_OUTER
    if BC_OUTER is None:
        from shapely import wkt
        path = os.path.join(BACKEND, "..", "frontend", "src", "geo", "bc_boundary_gbif.wkt")
        with open(path, encoding="utf-8") as fh:
            BC_OUTER = wkt.loads(fh.read())
    return BC_OUTER


def in_bc_polygon(box):
    """'point in BC', 'box in BC', 'touches BC' or 'not in BC' for a box inside BC's rectangle."""
    from shapely.geometry import Point, box as make_box
    n, s_, e, w = box
    poly = bc_polygon()
    if n == s_ and e == w:
        return "point in BC" if poly.contains(Point(w, s_)) else "not in BC"
    b = make_box(w, s_, e, n)
    if poly.contains(b):
        return "box in BC"
    return "touches BC" if poly.intersects(b) else "not in BC"


def local_group(doc, place_names):
    """
    Put one downloaded record in a group with the same rules as the Solr
    counts, done on our side: 'surely BC', 'surely not BC', 'i', 'ii',
    'iii' or 'iv'. Used for the random sample only.
    """
    text = text_of(doc).lower()
    named = bool(BC_NAME_RE.search(text))
    place = bool(place_hits(text, place_names)) or bool(VANCOUVER_OK.search(text_of(doc)))
    where = classify_box(box_of(doc))
    if named or place or where == "inside":
        return "surely BC"
    if where == "outside":
        return "surely not BC"
    if where.startswith("overlaps"):
        return "i"
    if BROAD_RE.search(text):
        return "ii"
    if AMBIG_RE.search(text):
        return "iii"
    return "iv"


def ds_series(d):
    """A facet result as a pandas Series keyed by short datasource name."""
    return pd.Series({short_ds(k): v for k, v in d.items()}, dtype="int64")


def q9_three_groups(sample_pages=60, page_rows=500):
    """
    Size the three groups the first sieve would make, over every
    latest-version metadata record, by datasource:

      surely BC      BC named, a BC place from our list, or a box inside BC;
      surely not BC  a box entirely outside BC, and no BC name or place;
      not sure       (i) a box overlapping BC but not inside it;
                     (ii) no box, a broad region that includes BC;
                     (iii) no box, a name that can mean BC or elsewhere;
                     (iv) no box and none of these words.

    Box and name parts are counted exactly by Solr. The place-name part is
    taken from the records question 8a downloaded (the name list is too
    long for one URL). The share passing the Lunaris keyword filter comes
    from a random sample, since the filter has to run on our side.
    """
    heading("9. SURELY BC, SURELY NOT BC, NOT SURE (latest-version metadata)")
    places = _candidates.get("places_extra")
    if places is None:
        places = q8a_places()
    names, _ = bc_place_names()

    def f(*parts):
        """Facet counts by datasource for BASE plus some clauses."""
        _, d = facet(AND(BASE, *parts))
        return ds_series(d)

    no_box = f"-{HAS_BOX}"
    outside = f"({HAS_BOX} AND -{RULE_B} AND -{RULE_C})"
    all_ = f()
    a = f(RULE_A)
    inside_not_a = f(RULE_B, f"-{RULE_A}")
    out_not_a = f(outside, f"-{RULE_A}")
    over_not_a = f(RULE_C, f"-{RULE_A}")
    nobox_not_a = f(no_box, f"-{RULE_A}")
    broad_raw = f(no_box, f"-{RULE_A}", any_in_subject(BROAD_REGIONS))
    ambig_raw = f(no_box, f"-{RULE_A}", f"-{any_in_subject(BROAD_REGIONS)}",
                  any_in_subject(AMBIGUOUS_NAMES))

    # Records found only by a place name (not a), from question 8a, by box.
    pl = places.copy()
    pl_text = [text_of(r).lower() for r in pl.to_dict("records")]
    pl["broad"] = [bool(BROAD_RE.search(t)) for t in pl_text]
    pl["ambig"] = [bool(AMBIG_RE.search(t)) and not b for t, b in zip(pl_text, pl["broad"])]

    def by_ds(mask):
        """Count the place-only records under a mask, by datasource."""
        return pl[mask].groupby("ds").size()

    p_inside = by_ds(pl["box_class"] == "inside")
    p_out = by_ds(pl["box_class"] == "outside")
    p_over = by_ds(pl["box_class"].str.startswith("overlaps"))
    p_none = by_ds(pl["box_class"] == "none")
    p_none_broad = by_ds((pl["box_class"] == "none") & pl["broad"])
    p_none_ambig = by_ds((pl["box_class"] == "none") & pl["ambig"])

    t = pd.DataFrame({"all_latest": all_}).fillna(0)
    z = lambda x: x.reindex(t.index).fillna(0)  # noqa: E731
    t["surely BC"] = z(a) + z(inside_not_a) + z(p_out) + z(p_over) + z(p_none)
    t["surely not BC"] = z(out_not_a) - z(p_out)
    t["i overlaps"] = z(over_not_a) - z(p_over)
    t["ii broad region"] = z(broad_raw) - z(p_none_broad)
    t["iii ambiguous name"] = z(ambig_raw) - z(p_none_ambig)
    t["iv nothing"] = (z(nobox_not_a) - z(p_none) - t["ii broad region"]
                       - t["iii ambiguous name"])
    t = t.astype(int)
    t["check_sum"] = t.drop(columns=["all_latest"]).sum(axis=1) - t["all_latest"]
    t["to_LLM (surely BC + i..iii)"] = (t["surely BC"] + t["i overlaps"] + t["ii broad region"]
                                        + t["iii ambiguous name"])
    t = t.sort_values("all_latest", ascending=False)
    t.loc["ALL"] = t.sum()
    save(t.rename_axis("datasource").reset_index(), "q9_groups_by_datasource.csv")
    print(t.loc[["ALL"]].T.to_string())
    print("\n  largest datasources:")
    print(t.drop(index="ALL").head(15).drop(columns=["check_sum"]).to_string())

    # A stricter variant: a place name with a box outside BC is not BC
    # (question 8a found Florida's Gulf Islands, a ship named Prince
    # Rupert), so it goes to "surely not BC" instead.
    strict = t.loc["ALL"].copy()
    strict["surely BC"] -= int(p_out.sum())
    strict["surely not BC"] += int(p_out.sum())
    strict["to_LLM (surely BC + i..iii)"] -= int(p_out.sum())

    # Inside BC's rectangle is not inside BC: test the b records against
    # BC's boundary.
    cand = candidates()
    bb = cand[cand["in_b"]].copy()
    bb["poly"] = [in_bc_polygon(box_of(r)) for r in bb.to_dict("records")]
    poly = bb["poly"].value_counts()
    print("\n  of the records with a box inside BC's rectangle (b):", poly.to_dict())

    # A refined variant: a box counts as surely BC only when it is inside
    # BC's boundary (not just its rectangle); boxes in the rectangle that
    # only touch or miss the boundary (sea, Alaska, Washington) go to (i);
    # a place name with a box outside BC goes to "surely not BC".
    b_out = int((~bb["in_a"] & ~bb["poly"].isin(["point in BC", "box in BC"])).sum())
    refined = t.loc["ALL"].copy()
    refined["surely BC"] -= b_out + int(p_out.sum())
    refined["i overlaps"] += b_out
    refined["surely not BC"] += int(p_out.sum())
    refined["to_LLM (surely BC + i..iii)"] = (refined["surely BC"] + refined["i overlaps"]
                                              + refined["ii broad region"]
                                              + refined["iii ambiguous name"])

    # Random sample for the Lunaris keyword filter and the 8b size estimate.
    total = int(t.loc["ALL", "all_latest"])
    rng = random.Random(909)
    sample, nbytes = [], 0
    for k in range(sample_pages):
        start = rng.randint(0, total - page_rows)
        d = ask(BASE, rows=page_rows, fl=SHORT_FIELDS, sort="id asc", start=start)
        if d:
            sample.extend(d["response"]["docs"])
            nbytes += len(json.dumps(d["response"]["docs"]).encode("utf-8"))
    sf = pd.DataFrame(sample).drop_duplicates("id")
    sf["group"] = [local_group(r, names) for r in sf.to_dict("records")]
    sf["lunaris_keywords"] = [lunaris_keywords.is_biodiversity(lunaris_keywords.build_haystack(
        r.get("title"), r.get("keywords"), r.get("abstract"))) for r in sf.to_dict("records")]
    share = sf.groupby("group").agg(sample=("id", "size"), keyword_pass=("lunaris_keywords", "mean"))
    groups = ["surely BC", "surely not BC", "i", "ii", "iii", "iv"]
    share = share.reindex(groups)
    exact = t.loc["ALL", ["surely BC", "surely not BC", "i overlaps", "ii broad region",
                          "iii ambiguous name", "iv nothing"]].values
    share["exact_count"] = exact
    share["sample_share"] = (share["sample"] / share["sample"].sum()).round(4)
    share["exact_share"] = (share["exact_count"] / total).round(4)
    share["est_pass_keywords"] = (share["exact_count"] * share["keyword_pass"]).round()
    save(share.rename_axis("group").reset_index(), "q9_groups_with_lunaris_keywords.csv")
    print("\n" + share.to_string())
    pd.DataFrame([{"variant": "as defined", **t.loc["ALL"].to_dict()},
                  {"variant": "place name with box outside BC counted as not BC",
                   **strict.to_dict()},
                  {"variant": "refined: box must be inside BC's boundary; place name with "
                              "box outside BC is not BC", **refined.to_dict()}]).to_csv(os.path.join(OUT_DIR, "q9_groups_variants.csv"),
                                                index=False, encoding="utf-8-sig")
    poly.rename_axis("b_box_against_BC_boundary").reset_index(name="records").to_csv(
        os.path.join(OUT_DIR, "q9_b_against_bc_boundary.csv"), index=False, encoding="utf-8-sig")

    per_rec = nbytes / max(1, len(sample))
    est = {"records": total, "pages_of_10000": -(-total // 10000),
           "bytes_per_record_sample": round(per_rec), "est_json_MB": round(per_rec * total / 1e6),
           "est_minutes_at_1_per_s": round(-(-total // 10000) * (MIN_GAP + 1.5) / 60, 1),
           "sample_records": len(sf)}
    save(pd.DataFrame([est]), "q8b_full_set_estimate.csv")
    print("\n  8b estimate:", est)
    return t


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------

QUESTIONS = {}


def main():
    """Run the questions asked for, in order, and report the request count."""
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", nargs="*", help="run only these questions (q1 ... q9)")
    args = parser.parse_args()
    os.makedirs(CACHE_DIR, exist_ok=True)
    before = requests_sent_so_far()
    wanted = args.only or list(QUESTIONS)
    try:
        for name in wanted:
            QUESTIONS[name]()
    except Blocked as exc:
        print(f"\nSTOPPED: DataONE refused or blocked a request.\n{exc}")
        return 2
    except Budget as exc:
        print(f"\nSTOPPED: {exc}")
        return 3
    finally:
        print(f"\nrequests sent this run: {requests_sent_so_far() - before}; "
              f"over all runs: {requests_sent_so_far()}")
    return 0


QUESTIONS.update({"q1": q1_versions, "q2": q2_bc_rules, "q3": q3_hakai, "q4": q4_identifiers,
                  "q5": q5_lunaris, "q6": q6_fields, "q7": q7_sample, "q8a": q8a_places,
                  "q8b": q8b_paging, "q9": q9_three_groups})

if __name__ == "__main__":
    sys.exit(main())
