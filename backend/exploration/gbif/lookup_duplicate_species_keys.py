"""
lookup_duplicate_species_keys.py

Look up the species names that have two specieskeys in bc_occurrence, to
decide whether the two keys are the same species (and can be merged) or two
different species that happen to share a name (homonyms).

Input:  duplicate_species_names.csv, one row per (species, specieskey), with
        columns species, specieskey, kingdom, phylum, class, order, family,
        genus, record_count.
Output: duplicate_species_lookup.csv next to it, one row per key, with what
        the Catalogue of Life says about that key. A summary is printed at the
        end, to paste back.

Where the answers come from:
  The specieskeys in bc_occurrence are Catalogue of Life ids (like TMBRQ),
  not GBIF backbone numbers, because the GBIF download was made with the
  Catalogue of Life checklist (CHECKLIST_KEY, the same as
  match_bcsee_gbif.py). GBIF's own species API answers for these keys, but it
  does not say which source checklist a name came from. ChecklistBank, which
  publishes the Catalogue of Life, does. So each key is looked up in
  ChecklistBank, in the exact Catalogue of Life release GBIF uses: the script
  asks GBIF's registry which release that is when it starts, and prints it.

  For each key it records the status (accepted, provisionally accepted,
  synonym, ...), rank, authorship, the accepted key if it is a synonym, and
  the "sector": the part of the Catalogue of Life a name came in with. A
  sector has a mode ("attach" for a whole group taken from one checklist,
  "merge" for names added from another checklist to fill gaps) and a source
  checklist, whose title is looked up too.

  Keys starting with "V" are the ones the question is about: on the examples
  checked by hand (VL2GD) they are names merged in from another checklist,
  marked provisionally accepted. The summary reports what the "V" keys and
  the other keys actually are, so that is checked rather than assumed.

Being gentle with the APIs: one request at a time with a short pause
between them, and a failed request is tried again a few times, waiting
longer each time. Answers are saved to a cache file next to the output, so
a run that stops can be started again without asking for the same key twice.

Only the standard library is used.

Run (from backend/):
    python exploration/gbif/lookup_duplicate_species_keys.py
    python exploration/gbif/lookup_duplicate_species_keys.py --input ~/bcbn/data/gbif_tables/duplicate_species_names.csv
"""

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict

# The Catalogue of Life checklist the GBIF download used (as in
# backend/pipeline/bcsee/match_bcsee_gbif.py).
CHECKLIST_KEY = "7ddf754f-d193-4cc9-b351-99906754a03b"
GBIF_DATASET_URL = "https://api.gbif.org/v1/dataset/"
CLB_URL = "https://api.checklistbank.org/dataset/"
HEADERS = {"User-Agent": "BCBN-Dashboard (UBC Biodiversity Research Centre)"}

# Seconds to wait between requests, and how many times to try a failed
# request again (waiting RETRY_WAIT, then twice that, and so on).
PAUSE = 0.3
RETRIES = 4
RETRY_WAIT = 3

DEFAULT_INPUT = "~/bcbn/data/gbif_tables/duplicate_species_names.csv"
OUTPUT_NAME = "duplicate_species_lookup.csv"
CACHE_NAME = "duplicate_species_lookup_cache.jsonl"

OUTPUT_COLUMNS = [
    "species", "specieskey", "kingdom", "phylum", "class", "order", "family",
    "genus", "record_count", "status", "rank", "scientific_name", "authorship",
    "accepted_key", "accepted_name", "sector_key", "sector_mode",
    "source_dataset_key", "source_dataset_title", "identifiers", "error",
]


def get_json(url):
    """Return the JSON at url, or None if it does not exist (404).

    Waits PAUSE seconds first. A network error, a server error or "too many
    requests" is tried again up to RETRIES times, waiting longer each time
    (or as long as the server asks, if it says). Any other error stops the
    run, because it means the request itself is wrong.
    """
    time.sleep(PAUSE)
    request = urllib.request.Request(url, headers=HEADERS)
    for attempt in range(RETRIES + 1):
        try:
            with urllib.request.urlopen(request, timeout=30) as reply:
                return json.loads(reply.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            if error.code != 429 and error.code < 500:
                raise
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


def checklistbank_release():
    """Return the ChecklistBank dataset key of the Catalogue of Life release
    GBIF uses for CHECKLIST_KEY, from GBIF's registry entry."""
    entry = get_json(GBIF_DATASET_URL + CHECKLIST_KEY)
    for identifier in (entry or {}).get("identifiers", []):
        if identifier.get("type") == "CLB_DATASET_KEY":
            return identifier["identifier"]
    raise RuntimeError(f"GBIF's registry entry for {CHECKLIST_KEY} names no "
                       f"ChecklistBank dataset (CLB_DATASET_KEY)")


def read_input(path):
    """Read the duplicate names CSV and return its rows as dicts, with
    record_count as an int. Stops if a needed column is missing."""
    with open(path, newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    needed = ["species", "specieskey", "kingdom", "phylum", "record_count"]
    missing = [c for c in needed if rows and c not in rows[0]]
    if missing:
        raise SystemExit(f"{path} has no column(s) {missing}")
    for row in rows:
        row["record_count"] = int(float(row["record_count"] or 0))
    return rows


def load_cache(path):
    """Return the answers saved by an earlier run, by key."""
    answers = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                saved = json.loads(line)
                answers[saved["key"]] = saved
    return answers


def look_up(release, key, sectors, datasets):
    """Look up one key in the ChecklistBank release. Returns a dict of the
    output columns that come from the API. sectors and datasets are caches,
    filled as new ones are met, so each is only asked for once."""
    usage = get_json(f"{CLB_URL}{release}/nameusage/{urllib.parse.quote(key)}")
    if usage is None:
        return {"error": "not in this Catalogue of Life release"}
    name = usage.get("name") or {}
    accepted = usage.get("accepted") or {}
    found = {
        "status": usage.get("status"),
        "rank": name.get("rank"),
        "scientific_name": name.get("scientificName"),
        "authorship": name.get("authorship"),
        "accepted_key": accepted.get("id"),
        "accepted_name": (accepted.get("name") or {}).get("scientificName"),
        "sector_key": usage.get("sectorKey"),
        "identifiers": " ".join(usage.get("identifier") or []),
    }
    sector_key = usage.get("sectorKey")
    if sector_key is not None:
        if sector_key not in sectors:
            sector = get_json(f"{CLB_URL}{release}/sector/{sector_key}") or {}
            sectors[sector_key] = (sector.get("mode"), sector.get("subjectDatasetKey"))
        mode, source = sectors[sector_key]
        found["sector_mode"] = mode
        found["source_dataset_key"] = source
        if source is not None:
            if source not in datasets:
                datasets[source] = (get_json(f"{CLB_URL}{source}") or {}).get("title")
            found["source_dataset_title"] = datasets[source]
    return found


def author_words(authorship):
    """Return the authors' names in an authorship, cut to their first four
    letters, so "Brightwell" and "Brightw." count as the same author and the
    year is ignored."""
    words = re.findall(r"[^\W\d_]+", (authorship or "").lower())
    return {w[:4] for w in words if w not in {"ex", "in", "et", "and", "von", "van", "de"}}


def is_v_key(key):
    """Return True for a key starting with "V"."""
    return key.upper().startswith("V")


def summarise(rows):
    """Print the summary to paste back, from the per-key rows."""
    pairs = defaultdict(list)
    for row in rows:
        pairs[row["species"]].append(row)
    sizes = Counter(len(keys) for keys in pairs.values())
    total_records = sum(r["record_count"] for r in rows)
    errors = [r for r in rows if r.get("error")]

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"names: {len(pairs):,}, keys: {len(rows):,}, records on these keys: "
          f"{total_records:,}")
    print(f"keys per name: {dict(sorted(sizes.items()))}")
    if errors:
        print(f"keys that could not be looked up: {len(errors)} "
              f"(e.g. {', '.join(r['specieskey'] for r in errors[:5])})")

    # 1. The "one normal key and one V key" pattern.
    v_pairs = {s: keys for s, keys in pairs.items()
               if len(keys) == 2 and sum(is_v_key(r["specieskey"]) for r in keys) == 1}
    print(f"\n1. Names with one V key and one other key: {len(v_pairs):,} of {len(pairs):,}")
    for label, pick in [("V keys", True), ("other keys", False)]:
        chosen = [r for keys in v_pairs.values() for r in keys if is_v_key(r["specieskey"]) == pick]
        print(f"   {label}: status {dict(Counter(r.get('status') or '(none)' for r in chosen))}")
        print(f"   {'':{len(label)}}  sector mode {dict(Counter(r.get('sector_mode') or '(none)' for r in chosen))}")
        sources = Counter(r.get("source_dataset_title") or "(none)" for r in chosen)
        print(f"   {'':{len(label)}}  top sources {sources.most_common(5)}")
    others = {s: keys for s, keys in pairs.items() if s not in v_pairs}
    if others:
        print(f"   names not following that pattern: {len(others):,}, e.g. "
              + "; ".join(f"{s} ({', '.join(r['specieskey'] for r in keys)})"
                          for s, keys in list(others.items())[:5]))

    # 2. Real homonyms: different kingdom or phylum, or different authors.
    strong, weak = [], []
    for species, keys in pairs.items():
        if len({(r["kingdom"], r["phylum"]) for r in keys}) > 1:
            strong.append((species, keys))
        else:
            authors = [author_words(r.get("authorship")) for r in keys]
            if all(authors) and not set.intersection(*authors):
                weak.append((species, keys))
    print(f"\n2. Look like real homonyms (different kingdom or phylum): {len(strong)}")
    for species, keys in strong:
        print(f"   {species}: " + " vs ".join(
            f"{r['specieskey']} {r['kingdom']}/{r['phylum']} {r.get('authorship') or ''} "
            f"({r['record_count']:,} records)" for r in keys))
    print(f"   Same kingdom and phylum but no author in common (check by hand): {len(weak)}")
    for species, keys in weak[:15]:
        print(f"   {species}: " + " vs ".join(
            f"{r['specieskey']} {r.get('authorship') or '?'} ({r['record_count']:,})" for r in keys))
    if len(weak) > 15:
        print(f"   ... and {len(weak) - 15} more in the output CSV")

    # 3. Pairs where the V key has more records.
    v_bigger = []
    for species, keys in v_pairs.items():
        v = next(r for r in keys if is_v_key(r["specieskey"]))
        other = next(r for r in keys if not is_v_key(r["specieskey"]))
        if v["record_count"] > other["record_count"]:
            v_bigger.append((species, v, other))
    print(f"\n3. V key has more records than the other key: {len(v_bigger)}")
    for species, v, other in sorted(v_bigger, key=lambda t: -t[1]["record_count"])[:20]:
        print(f"   {species}: {v['specieskey']} {v['record_count']:,} vs "
              f"{other['specieskey']} {other['record_count']:,}")
    if len(v_bigger) > 20:
        print(f"   ... and {len(v_bigger) - 20} more")

    # 4. Do V keys point at the other key as their accepted name?
    pointing = 0
    synonyms = 0
    for keys in v_pairs.values():
        v = next(r for r in keys if is_v_key(r["specieskey"]))
        other = next(r for r in keys if not is_v_key(r["specieskey"]))
        if v.get("accepted_key"):
            synonyms += 1
            pointing += v["accepted_key"] == other["specieskey"]
    any_pointing = sum(
        1 for keys in pairs.values() for r in keys
        if r.get("accepted_key") and r["accepted_key"] in {k["specieskey"] for k in keys})
    print(f"\n4. V keys that are synonyms: {synonyms} of {len(v_pairs)}; "
          f"pointing at the other key of their pair: {pointing}")
    print(f"   any key (V or not) whose accepted key is the other key of its pair: {any_pointing}")

    # 5. Records on V keys.
    v_records = sum(r["record_count"] for r in rows if is_v_key(r["specieskey"]))
    share = 100 * v_records / total_records if total_records else 0
    print(f"\n5. Records on V keys: {v_records:,} ({share:.1f}% of the records on these keys)")

    # 6. Suggested merge rule, worked out from the numbers above.
    # A pair with a key that could not be looked up is left for checking by
    # hand, since nothing is known about that key.
    strong_names = {s for s, _ in strong}
    weak_names = {s for s, _ in weak}
    unknown = {s for s, keys in pairs.items() if any(r.get("error") for r in keys)}
    mergeable = {s: k for s, k in v_pairs.items()
                 if s not in strong_names | weak_names | unknown}
    merge_records = sum(next(r for r in k if is_v_key(r["specieskey"]))["record_count"]
                        for k in mergeable.values())
    print("\n6. Suggested merge rule:")
    print(f"   - For a name with one V key and one other key in the same kingdom and phylum,")
    print(f"     with an author in common, count the V key's records under the other key")
    print(f"     ({len(mergeable)} names, {merge_records:,} records move).")
    print(f"   - Keep names with different kingdoms or phyla apart, as two species "
          f"({len(strong)} names).")
    print(f"   - Check by hand the names whose authors differ ({len(weak)}), the names "
          f"that don't follow the V pattern ({len(others)}) and the names with a key")
    print(f"     that could not be looked up ({len(unknown)}).")
    print("   - Keep the merge as a key-to-key mapping (V key -> other key), applied when")
    print("     the species tables are built, so bc_occurrence keeps GBIF's original keys.")


def main():
    """Look up every key in the input, write the output CSV and print the
    summary."""
    parser = argparse.ArgumentParser(
        description="Look up species names with two specieskeys in the "
        "Catalogue of Life, and summarise whether they can be merged."
    )
    parser.add_argument("--input", default=DEFAULT_INPUT,
                        help=f"The duplicate names CSV (default {DEFAULT_INPUT}).")
    args = parser.parse_args()

    input_path = os.path.expanduser(args.input)
    folder = os.path.dirname(os.path.abspath(input_path))
    output_path = os.path.join(folder, OUTPUT_NAME)
    cache_path = os.path.join(folder, CACHE_NAME)

    rows = read_input(input_path)
    release = checklistbank_release()
    print(f"Input:   {input_path} ({len(rows):,} keys)")
    print(f"Checklist: GBIF {CHECKLIST_KEY} = ChecklistBank dataset {release}")
    print(f"Output:  {output_path}")

    cache = load_cache(cache_path)
    cache = {k: v for k, v in cache.items() if v.get("release") == release}
    todo = [r["specieskey"] for r in rows if r["specieskey"] not in cache]
    print(f"{len(rows) - len(todo):,} keys already in the cache, {len(todo):,} to look up")

    sectors, datasets = {}, {}
    started = time.time()
    with open(cache_path, "a", encoding="utf-8") as handle:
        for number, key in enumerate(todo, start=1):
            found = look_up(release, key, sectors, datasets)
            saved = {"key": key, "release": release, **found}
            handle.write(json.dumps(saved, ensure_ascii=False) + "\n")
            handle.flush()
            cache[key] = saved
            if number % 50 == 0 or number == len(todo):
                print(f"  {number:,} of {len(todo):,} looked up ({time.time() - started:.0f}s)")

    for row in rows:
        row.update({k: v for k, v in cache[row["specieskey"]].items()
                    if k not in ("key", "release")})
    with open(output_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    summarise(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
