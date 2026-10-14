"""
fetch_key_evidence.py

Collect evidence for the pairs of specieskeys that make_species_key_merge.py
kept apart, so each pair can be checked by hand: what the Catalogue of Life
says about each key, and which BC records GBIF files under it.

Input:  species_key_not_merged.csv   from make_species_key_merge.py: one row
                                     per pair kept apart (species, key_1,
                                     key_2, ...); pair ids P001, P002, ...
        or species_key_merge.csv      the pairs that were merged (old_key,
                                     kept_key, species, reason); pair ids
                                     M001, M002, ..., so they never clash
                                     with the P ids
        duplicate_species_lookup.csv  from lookup_duplicate_species_keys.py,
                                     in the same folder: our own record count
                                     per key in bc_occurrence
        bc_boundary_gbif.wkt          the BC polygon the GBIF download used
Output: next to the input, one row per key, with the same columns for
        both inputs: key_evidence.csv for the pairs kept apart,
        key_evidence_merged.csv for the merged pairs (or --out).

What is collected for each key:
  From ChecklistBank, in the Catalogue of Life release GBIF uses
  (COL_RELEASE): the status, name, authorship and rank; the reference the
  name was published in and its nomenclatural status, when the Catalogue of
  Life has them; and the source record behind the key (its id, the source
  checklist and its title, and any issues ChecklistBank flagged).

  From GBIF: the BC records filed under the key, counted by verbatim
  scientific name, dataset and basis of record (top 5 of each). Our keys
  are Catalogue of Life ids, so GBIF only finds them when the search names
  the Catalogue of Life checklist (checklistKey); without it, GBIF reads the
  key as one of its old backbone numbers and finds nothing. "BC" is the
  same filter the download used: occurrenceStatus PRESENT and inside
  bc_boundary_gbif.wkt. That polygon is too long to put in a web address,
  so the search is sent as a POST with the filter in the body.

How these counts relate to ours: bc_occurrence also applies quality filters
and the exact BC coastline, and GBIF keeps adding records, so GBIF's count
is usually close to, but not exactly, our record count (both are written
out). GBIF's count of verbatim names writes them in lower case, and leaves
out records whose publisher gave no scientific name (some herbaria only
give genus and epithet); those are counted as "(no verbatim name)".

Every answer is saved in --cache, so a rerun asks for nothing it already
has. There is a short pause before each new request, and a failed request
is tried again a few times, waiting longer each time.

Only the standard library is used.

Run (from backend/):
    python exploration/species_keys/fetch_key_evidence.py
    python exploration/species_keys/fetch_key_evidence.py \
        --pairs ~/Desktop/BCBN/Results/merge_lists/species_key_merge.csv
"""

import argparse
import csv
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request

# The Catalogue of Life checklist the GBIF download used, and the
# ChecklistBank release GBIF serves for it (from GBIF's registry entry).
CHECKLIST_KEY = "7ddf754f-d193-4cc9-b351-99906754a03b"
COL_RELEASE = 315557

CLB_URL = "https://api.checklistbank.org/dataset/"
GBIF_SEARCH_URL = "https://api.gbif.org/v1/occurrence/search/predicate"
GBIF_DATASET_URL = "https://api.gbif.org/v1/dataset/"
HEADERS = {"User-Agent": "BCBN-Dashboard (UBC Biodiversity Research Centre)"}

# Seconds to wait before each new request, and how many times to try a
# failed one again (waiting RETRY_WAIT, then twice that, and so on).
PAUSE = 0.3
RETRIES = 4
RETRY_WAIT = 3

# How many values to list per count, and how many verbatim names to ask
# GBIF for, so the records with no verbatim name can be worked out.
TOP = 5
FACET_LIMIT = 100

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PAIRS = "~/Desktop/BCBN/Results/merge_lists/species_key_not_merged.csv"
DEFAULT_BOUNDARY = "~/Desktop/BCBN/Results/bc_boundary_gbif.wkt"
DEFAULT_CACHE = os.path.join(HERE, "cache")
# Output file name and pair id prefix for each kind of input file.
OUTPUT_NAME = "key_evidence.csv"
MERGED_OUTPUT_NAME = "key_evidence_merged.csv"

OUTPUT_COLUMNS = [
    "pair_id", "species", "key", "other_key", "our_record_count",
    "clb_status", "clb_name", "clb_authorship", "clb_rank", "nom_status",
    "published_in", "source_record_id", "source_dataset_key",
    "source_dataset_title", "source_issues",
    "gbif_bc_count", "top_verbatim_names", "no_verbatim_name_count",
    "top_datasets", "top_basis_of_record", "error",
]


class Fetcher:
    """Asks the APIs for JSON, saving every answer in a cache folder."""

    def __init__(self, cache_dir):
        """Use (and create if needed) cache_dir for saved answers."""
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        self.requests = 0

    def get(self, url, body=None):
        """Return the JSON answer for url (a POST when body is given), or
        None if it does not exist (404). A saved answer is used if there is
        one; a 404 is saved too, so it is not asked again."""
        name = hashlib.sha1((url + "\n" + json.dumps(body, sort_keys=True)).encode()).hexdigest()
        path = os.path.join(self.cache_dir, name + ".json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as handle:
                return json.load(handle)["answer"]
        answer = self.ask(url, body)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"url": url, "body": body, "answer": answer}, handle, ensure_ascii=False)
        return answer

    def ask(self, url, body):
        """Send one request, after a short pause, and return its JSON, or
        None for a 404. A network error, a server error or "too many
        requests" is tried again up to RETRIES times; any other error stops
        the run, because it means the request itself is wrong."""
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = dict(HEADERS, **({"Content-Type": "application/json"} if data else {}))
        request = urllib.request.Request(url, data=data, headers=headers,
                                         method="POST" if data else "GET")
        for attempt in range(RETRIES + 1):
            time.sleep(PAUSE)
            self.requests += 1
            try:
                with urllib.request.urlopen(request, timeout=90) as reply:
                    return json.loads(reply.read().decode("utf-8"))
            except urllib.error.HTTPError as error:
                if error.code == 404:
                    return None
                if error.code != 429 and error.code < 500:
                    detail = error.read().decode("utf-8", errors="replace")[:200]
                    raise RuntimeError(f"{url} answered {error.code}: {detail}")
                wait = RETRY_WAIT * (attempt + 1)
                asked = error.headers.get("Retry-After", "")
                if asked.isdigit():
                    wait = max(wait, int(asked))
                problem = f"HTTP {error.code}"
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                wait = RETRY_WAIT * (attempt + 1)
                problem = str(error)
            if attempt == RETRIES:
                raise RuntimeError(f"gave up on {url} after {RETRIES + 1} tries: {problem}")
            print(f"    {problem}, trying again in {wait}s")
            time.sleep(wait)


def read_keys(pairs_path):
    """Return (keys, number of pairs, merged) for a pairs file.

    keys has one dict per key, in file order, with the pair id, species and
    the other key of the pair. The file can be species_key_not_merged.csv
    (key_1, key_2 and more_keys; pair ids P001, P002, ...) or
    species_key_merge.csv (old_key and kept_key; pair ids M001, M002, ...).
    merged says which it was."""
    with open(pairs_path, newline="", encoding="utf-8-sig") as handle:
        pairs = list(csv.DictReader(handle))
    merged = bool(pairs) and "old_key" in pairs[0]
    prefix = "M" if merged else "P"
    keys = []
    for number, pair in enumerate(pairs, start=1):
        if merged:
            pair_keys = [pair["old_key"], pair["kept_key"]]
        else:
            pair_keys = [pair["key_1"], pair["key_2"]]
            for extra in filter(None, (pair.get("more_keys") or "").split(" | ")):
                pair_keys.append(extra.split(":")[0].strip())
        for key in pair_keys:
            others = [k for k in pair_keys if k != key]
            keys.append({"pair_id": f"{prefix}{number:03d}", "species": pair["species"],
                         "key": key, "other_key": ", ".join(others)})
    return keys, len(pairs), merged


def read_our_counts(lookup_path):
    """Return our bc_occurrence record count per key, from the lookup CSV
    (empty if the file is not there)."""
    if not os.path.exists(lookup_path):
        return {}
    with open(lookup_path, newline="", encoding="utf-8-sig") as handle:
        return {r["specieskey"]: r["record_count"] for r in csv.DictReader(handle)}


def checklistbank_facts(fetch, key):
    """Return the ChecklistBank columns for one key, or an error note if the
    key is not in the release."""
    usage = fetch.get(f"{CLB_URL}{COL_RELEASE}/nameusage/{key}")
    if usage is None:
        return {"error": f"not in Catalogue of Life release {COL_RELEASE}"}
    name = usage.get("name") or {}
    facts = {
        "clb_status": usage.get("status"),
        "clb_name": name.get("scientificName"),
        "clb_authorship": name.get("authorship"),
        "clb_rank": name.get("rank"),
        "nom_status": name.get("nomStatus"),
    }

    # The reference the name was published in, as a citation, when the
    # Catalogue of Life records one.
    reference_id = name.get("publishedInId")
    if reference_id:
        reference = fetch.get(f"{CLB_URL}{COL_RELEASE}/reference/{reference_id}") or {}
        citation = reference.get("citation") or reference_id
        page = name.get("publishedInPage")
        facts["published_in"] = f"{citation} p. {page}" if page else citation

    # The record in the source checklist this key was built from.
    source = fetch.get(f"{CLB_URL}{COL_RELEASE}/nameusage/{key}/source") or {}
    facts["source_record_id"] = source.get("sourceId")
    facts["source_dataset_key"] = source.get("sourceDatasetKey")
    facts["source_issues"] = "; ".join(source.get("issues") or [])
    if source.get("sourceDatasetKey") is not None:
        dataset = fetch.get(f"{CLB_URL}{source['sourceDatasetKey']}") or {}
        facts["source_dataset_title"] = dataset.get("title")
    return facts


def top_text(counts, label=lambda name: name):
    """Write a list of {"name", "count"} as "name (count); ..." for the top
    TOP entries."""
    return "; ".join(f"{label(c['name'])} ({c['count']:,})" for c in counts[:TOP])


def gbif_facts(fetch, key, wkt):
    """Return the GBIF columns for one key: its BC record count and the top
    verbatim names, datasets and bases of record."""
    predicate = {"type": "and", "predicates": [
        {"type": "equals", "key": "TAXON_KEY", "value": key, "checklistKey": CHECKLIST_KEY},
        {"type": "equals", "key": "OCCURRENCE_STATUS", "value": "PRESENT"},
        {"type": "within", "geometry": wkt},
    ]}
    body = {"predicate": predicate, "limit": 0, "facetLimit": FACET_LIMIT,
            "facets": ["VERBATIM_SCIENTIFIC_NAME", "DATASET_KEY", "BASIS_OF_RECORD"]}
    answer = fetch.get(GBIF_SEARCH_URL, body) or {}
    facets = {f["field"]: f["counts"] for f in answer.get("facets", [])}
    count = answer.get("count", 0)

    names = facets.get("VERBATIM_SCIENTIFIC_NAME", [])
    no_name = count - sum(c["count"] for c in names) if len(names) < FACET_LIMIT else ""

    def dataset_title(dataset_key):
        """Return a GBIF dataset's title, or its key if there is none."""
        return (fetch.get(GBIF_DATASET_URL + dataset_key) or {}).get("title") or dataset_key

    return {
        "gbif_bc_count": count,
        "top_verbatim_names": top_text(names),
        "no_verbatim_name_count": no_name,
        "top_datasets": top_text(facets.get("DATASET_KEY", []), dataset_title),
        "top_basis_of_record": top_text(facets.get("BASIS_OF_RECORD", [])),
    }


def main():
    """Fetch the evidence for every key in the pairs file and write
    key_evidence.csv."""
    parser = argparse.ArgumentParser(description="Collect ChecklistBank and GBIF "
                                     "evidence for species keys kept apart.")
    parser.add_argument("--pairs", default=DEFAULT_PAIRS,
                        help=f"species_key_not_merged.csv (default {DEFAULT_PAIRS})")
    parser.add_argument("--boundary", default=DEFAULT_BOUNDARY,
                        help=f"bc_boundary_gbif.wkt (default {DEFAULT_BOUNDARY})")
    parser.add_argument("--out", default=None,
                        help=f"the output CSV (default: {OUTPUT_NAME}, or {MERGED_OUTPUT_NAME} "
                             f"for species_key_merge.csv, next to --pairs)")
    parser.add_argument("--cache", default=DEFAULT_CACHE,
                        help="folder for the saved API answers (default: cache/ next to "
                             "this script)")
    args = parser.parse_args()

    pairs_path = os.path.expanduser(args.pairs)
    folder = os.path.dirname(os.path.abspath(pairs_path))
    with open(os.path.expanduser(args.boundary), encoding="utf-8") as handle:
        wkt = handle.read().strip()

    keys, n_pairs, merged = read_keys(pairs_path)
    default_name = MERGED_OUTPUT_NAME if merged else OUTPUT_NAME
    out_path = os.path.expanduser(args.out) if args.out else os.path.join(folder, default_name)
    ours = read_our_counts(os.path.join(folder, "duplicate_species_lookup.csv"))
    fetch = Fetcher(args.cache)
    print(f"Pairs:  {pairs_path} ({n_pairs} pairs, {len(keys)} keys)")
    print(f"Cache:  {args.cache}")
    print(f"Output: {out_path}")

    started = time.time()
    rows = []
    for number, entry in enumerate(keys, start=1):
        row = {**entry, "our_record_count": ours.get(entry["key"], "")}
        row.update(checklistbank_facts(fetch, entry["key"]))
        row.update(gbif_facts(fetch, entry["key"], wkt))
        rows.append(row)
        if number % 20 == 0 or number == len(keys):
            print(f"  {number} of {len(keys)} keys ({fetch.requests} new requests, "
                  f"{time.time() - started:.0f}s)")

    with open(out_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    no_clb = sum(1 for r in rows if r.get("error"))
    no_gbif = sum(1 for r in rows if not r["gbif_bc_count"])
    neither = sum(1 for r in rows if r.get("error") and not r["gbif_bc_count"])
    print(f"\nKeys: {len(rows)}")
    print(f"  not in the Catalogue of Life release: {no_clb}")
    print(f"  no BC records in GBIF: {no_gbif}")
    print(f"  neither: {neither}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
