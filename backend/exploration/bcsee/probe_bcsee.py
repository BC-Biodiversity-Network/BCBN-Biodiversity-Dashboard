"""
Find out what the BC Species and Ecosystems Explorer actually publishes.

BCSEE is the province's record of conservation status for species and
ecological communities in British Columbia. It is not a catalogue of
datasets like Lunaris or DataONE. One record is one species or one
ecological community, so nothing in it needs filtering for relevance.

The data is published through the BC Data Catalogue, which runs CKAN. This
script asks that catalogue what BCSEE packages exist and what files each one
offers, and stops there. It downloads nothing, because the point of this
pass is to find out what the download options are before committing to one.

Usage:

    python probe_bcsee.py

The survey is written next to this script by default, because it is a record
of what was checked rather than data the pipeline consumes.
"""

import argparse
import json
import sys
import time
from pathlib import Path

import requests


CKAN = "https://catalogue.data.gov.bc.ca/api/3/action"
HEADERS = {"User-Agent": "BCBN-Dashboard probe (UBC Biodiversity Research Centre)"}

# A catalogue search only finds the words you happen to think of, so this
# uses several phrasings. The quoted ones are exact phrase matches, which are
# far more precise than loose words: "conservation status" on its own turns up
# heritage sites and park planning documents.
#
# This is still not a guarantee. One package is titled "CDC BIOTICS Occurence
# Attributes", misspelled, and no correctly spelled search will ever reach it.
# That is why the run finishes by counting how many packages each owning
# organisation holds in total, so the gap between "matched" and "exists" is
# visible rather than assumed away.
SEARCHES = [
    '"Conservation Data Centre"',
    '"species and ecosystems at risk"',
    "species and ecosystems explorer",
    "CDC BIOTICS",
    "conservation data centre",
    "BC Conservation Data Centre occurrences",
    "red blue list species",
    "ecological communities conservation status",
]


def call(action, params):
    """
    Call one CKAN endpoint and return its result, or None if it failed.

    CKAN wraps everything in {"success": bool, "result": ...}, so the
    unwrapping happens here rather than at every call site.
    """
    try:
        r = requests.get(f"{CKAN}/{action}", params=params, headers=HEADERS,
                         timeout=60)
        r.raise_for_status()
        body = r.json()
        time.sleep(0.4)
        return body.get("result") if body.get("success") else None
    except Exception as exc:
        print(f"    call failed: {type(exc).__name__}: {exc}")
        return None


def search(term, rows=100):
    """
    Return the packages the catalogue finds for one search phrase.

    The row limit matters more than it looks. At the default of 10 a phrase
    matching 40 packages returns 10 and says nothing about the other 30, so
    the caller would quietly work from a third of the answer.
    """
    res = call("package_search", {"q": term, "rows": rows})
    return res["results"] if res else []


def coverage(packages):
    """
    Say how much of each owning organisation the search actually reached.

    Everything above finds packages by matching words. This asks a different
    question: of all the packages these organisations publish, how many did
    the search see? A low share is a warning that the phrasings are missing
    things, which is the one weakness word matching cannot detect on its own.
    """
    print()
    print("=" * 76)
    print("HOW MUCH OF EACH ORGANISATION THIS REACHED")
    print("=" * 76)
    print()
    mine = {}
    for pkg in packages:
        org = (pkg.get("organization") or {}).get("name")
        if org:
            mine[org] = mine.get(org, 0) + 1
    for org, matched in sorted(mine.items(), key=lambda kv: -kv[1]):
        res = call("package_search", {"fq": f"organization:{org}", "rows": 0})
        total = res["count"] if res else None
        if total:
            print(f"  {org:42s} matched {matched:3d} of {total:4d}")
        else:
            print(f"  {org:42s} matched {matched:3d} of ?")
    print()
    print("  A small share is normal: these organisations publish far more")
    print("  than conservation data. It is only a problem if something")
    print("  relevant is sitting in the part that was never looked at.")


def describe(pkg):
    """
    Print one package and the files it offers.

    The files are the point. A package can look promising and then turn out
    to offer nothing but a link to a web form, which cannot be harvested.
    """
    print(f"\n  {pkg.get('title')}")
    print(f"    name       {pkg.get('name')}")
    print(f"    org        {(pkg.get('organization') or {}).get('title')}")
    print(f"    licence    {pkg.get('license_title')}")
    print(f"    modified   {pkg.get('metadata_modified', '')[:10]}")
    notes = (pkg.get("notes") or "").replace("\n", " ").strip()
    if notes:
        print(f"    about      {notes[:220]}")

    resources = pkg.get("resources") or []
    if not resources:
        print("    files      none listed")
        return
    print(f"    files      {len(resources)}")
    for r in resources:
        fmt = (r.get("format") or "?").upper()
        size = r.get("size")
        size_s = f"{int(size)/1e6:.1f} MB" if size else ""
        print(f"      [{fmt:>8s}] {str(r.get('name'))[:58]:58s} {size_s}")
        url = r.get("url") or ""
        if url:
            print(f"                 {url[:110]}")


def main():
    """Search the catalogue for BCSEE data and report what is downloadable."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default=str(Path(__file__).parent),
                        help="Where to write the survey. Defaults to this "
                             "script's own directory, so the record lands "
                             "beside the script whatever directory you run "
                             "it from.")
    args = parser.parse_args()

    print("=" * 76)
    print("WHAT THE BC DATA CATALOGUE HAS")
    print("=" * 76)

    seen, keep = set(), []
    for term in SEARCHES:
        print(f"\n--- searching: {term}")
        hits = search(term)
        if not hits:
            print("    nothing, or the call failed")
            continue
        for pkg in hits:
            if pkg["id"] in seen:
                continue
            seen.add(pkg["id"])
            keep.append(pkg)
            describe(pkg)

    print()
    print("=" * 76)
    print(f"{len(keep)} distinct packages found")
    print("=" * 76)
    print()
    print("  Look for a CSV or a file geodatabase holding one row per species.")
    print("  A package whose only resource is a link to a web application is")
    print("  not harvestable and should be ruled out now rather than later.")

    coverage(keep)

    if keep:
        path = Path(args.out_dir) / "bcsee_catalogue_packages.json"
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(keep, fh, ensure_ascii=False, indent=1)
        print(f"\n  full records written to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
