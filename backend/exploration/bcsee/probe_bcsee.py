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

# Several phrasings, because a catalogue search is only as good as the words
# you happen to use, and the official title may not be the obvious one.
SEARCHES = [
    "species and ecosystems explorer",
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


def search(term):
    """Return the packages the catalogue finds for one search phrase."""
    res = call("package_search", {"q": term, "rows": 12})
    return res["results"] if res else []


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

    if keep:
        path = Path(args.out_dir) / "bcsee_catalogue_packages.json"
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(keep, fh, ensure_ascii=False, indent=1)
        print(f"\n  full records written to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
