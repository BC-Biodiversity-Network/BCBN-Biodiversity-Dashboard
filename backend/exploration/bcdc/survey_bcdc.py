"""
Take the whole BC Data Catalogue down once, then answer questions locally.

Every survey we have done so far searched for words we thought of ourselves.
That can only ever confirm what it found. It cannot report what it missed,
because to know a package was missed you would have to already know it
exists, which is the thing the search was for.

This does the opposite. CKAN will hand over every package in the catalogue if
you ask it to match everything, so there is no reason to search at all for a
survey of this size. One pass gets the complete set. After that, "is there
anything about X in the catalogue" is a local question with a definite answer,
and every package that gets ruled out is ruled out by a rule we can state and
a count we can print.

What this covers: everything published in the BC Data Catalogue.
What it does not cover: data the province holds but does not publish there.
The Conservation Data Centre's restricted layers, for instance, are requested
by email and never appear in this catalogue at all.

Usage:

    python survey_bcdc.py
    python survey_bcdc.py --refresh
    python survey_bcdc.py --terms "Conservation Data Centre" "salmon"

The downloaded catalogue is cached, so only the first run is slow.
"""

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path

import requests


CKAN = "https://catalogue.data.gov.bc.ca/api/3/action"
HEADERS = {"User-Agent": "BCBN-Dashboard survey (UBC Biodiversity Research Centre)"}

# How many packages to ask for per request. CKAN usually refuses more than a
# thousand, and a smaller page is kinder to the server and easier to resume.
PAGE = 500

# Resource formats that mean an actual file you can download and parse.
FILE_FORMATS = {
    "CSV", "TSV", "TXT", "XLSX", "XLS", "JSON", "GEOJSON", "XML",
    "SHP", "GDB", "FGDB", "GPKG", "KML", "KMZ", "ZIP", "PARQUET", "E00",
}

# Formats that are a live service rather than a file. You can still get data
# out of these, but it takes a client and a request, not a download.
SERVICE_FORMATS = {
    "WMS", "WFS", "WCS", "ARCGIS_REST", "OGCAPI", "REST", "SOAP", "ATOM",
    "MULTIPLE",  # the BC Geographic Warehouse custom extract ordering page
}

# Anything else, including OTHER, HTML, PDF and ORACLE_SDE, is a link to a web
# page or an internal database view. Neither can be harvested.

# Which licences actually let us use the data.
#
# The first version of this asked whether the licence name contained the words
# "Open Government Licence". That quietly discarded 119 packages published
# under Statistics Canada's open licence, Elections BC's, and several others,
# every one of which we are free to use. A guess made out of a substring
# cannot tell you when it guessed wrong, so this is a written-down table.
#
# Order matters: the first phrase that appears in the licence name wins.
# Anything this table does not cover comes back as "unknown" and gets printed
# in full. A licence nobody has read yet should interrupt a person, not
# disappear into one pile or the other.
LICENCE_RULES = [
    # (text to look for in the lowercased licence name, verdict)
    ("open government licence", "open"),  # BC, Canada, TransLink, and the rest
    ("statistics canada open licence", "open"),
    ("elections bc open data licence", "open"),
    ("bc energy regulator open data licen", "open"),  # spelled "License" there
    ("open data licence for icbc", "open"),
    ("open data commons", "open"),
    ("open licence - university of northern british columbia", "open"),
    ("open data licence - office of the registrar of lobbyists", "open"),
    ("access only", "restricted"),
]


def fetch_all(refresh, cache_path):
    """
    Get every package in the catalogue, or reuse the copy already on disk.

    The query "*:*" is CKAN's way of saying match everything. Asking for it in
    pages of a few hundred is the whole trick: there is no search term, so
    there is nothing for a search term to miss.
    """
    if cache_path.exists() and not refresh:
        mb = cache_path.stat().st_size / 1e6
        print(f"  reusing {cache_path.name} ({mb:.1f} MB), "
              f"use --refresh to download again")
        with open(cache_path, encoding="utf-8") as fh:
            return json.load(fh)

    packages, start, total = [], 0, None
    while True:
        try:
            r = requests.get(f"{CKAN}/package_search",
                             params={"q": "*:*", "rows": PAGE, "start": start},
                             headers=HEADERS, timeout=120)
            r.raise_for_status()
            result = r.json()["result"]
        except Exception as exc:
            print(f"  page starting at {start} failed: "
                  f"{type(exc).__name__}: {exc}")
            print("  stopping here, the cache was not written")
            return None

        if total is None:
            total = result["count"]
            print(f"  the catalogue reports {total:,} packages")
        got = result["results"]
        if not got:
            break
        packages.extend(got)
        print(f"    {len(packages):,} of {total:,}")
        start += PAGE
        if start >= total:
            break
        time.sleep(0.3)

    # If these two disagree the download is incomplete, and every count below
    # would be quietly wrong. Better to say so than to carry on.
    if total is not None and len(packages) != total:
        print(f"  WARNING: got {len(packages):,} but the catalogue said "
              f"{total:,}. Something was skipped.")

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as fh:
        json.dump(packages, fh, ensure_ascii=False)
    print(f"  saved to {cache_path} "
          f"({cache_path.stat().st_size / 1e6:.1f} MB)")
    return packages


def package_text(pkg):
    """
    Squash everything readable about one package into a single lowercase line.

    Searching this locally is what replaces guessing at search terms. The
    whitespace is flattened on purpose: one real package is titled
    "CDC  BIOTICS Occurence Attributes" with two spaces, so a match on
    "CDC BIOTICS" would fail against the raw text for no good reason.
    """
    parts = [pkg.get("title") or "",
             pkg.get("notes") or "",
             (pkg.get("organization") or {}).get("title") or ""]
    for tag in pkg.get("tags") or []:
        parts.append(tag.get("display_name") or tag.get("name") or "")
    for res in pkg.get("resources") or []:
        parts.append(res.get("name") or "")
        parts.append(res.get("description") or "")
    return re.sub(r"\s+", " ", " ".join(parts)).lower()


def best_resource_kind(pkg):
    """
    Say the most useful thing this package offers: a file, a service, or none.

    A package can list six resources where five are web page links and one is
    a CSV. What matters is the best one, so this reports the best rather than
    the first or the most common.
    """
    kinds = set()
    for res in pkg.get("resources") or []:
        fmt = (res.get("format") or "").strip().upper()
        if fmt in FILE_FORMATS:
            kinds.add("file")
        elif fmt in SERVICE_FORMATS:
            kinds.add("service")
    if "file" in kinds:
        return "file"
    if "service" in kinds:
        return "service"
    return "none"


def licence_verdict(pkg):
    """
    Say whether this package's licence lets us use the data.

    Gives back one of three answers rather than two. "unknown" means no one
    has read that licence yet, so the package is kept out of both piles and
    named in the report instead of being quietly sorted into whichever one
    the code happened to fall through to.
    """
    name = (pkg.get("license_title") or "").strip().lower()
    if not name:
        return "unknown"
    for needle, verdict in LICENCE_RULES:
        if needle in name:
            return verdict
    return "unknown"


def is_open(pkg):
    """Say whether the licence lets us actually use the data."""
    return licence_verdict(pkg) == "open"


def licence_table(packages):
    """
    Print every distinct licence in the catalogue next to how it was judged.

    This is the part that makes the licence rule arguable. Every name the
    catalogue uses is listed with its count and its verdict, so a wrong call
    is something you can see and say is wrong, rather than something buried
    in a comparison halfway down the file.
    """
    counts = {}
    for pkg in packages:
        name = (pkg.get("license_title") or "(no licence given)").strip()
        counts[name] = counts.get(name, 0) + 1

    print()
    print("=" * 78)
    print("EVERY LICENCE, AND HOW THIS SCRIPT JUDGED IT")
    print("=" * 78)
    print()
    unknown = 0
    for name, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        fake = {"license_title": name}
        verdict = licence_verdict(fake)
        if verdict == "unknown":
            unknown += n
        mark = {"open": "USE ", "restricted": "    ",
                "unknown": "????"}[verdict]
        print(f"  {mark}  {n:5,}  {name[:62]}")
    print()
    if unknown:
        print(f"  {unknown:,} packages carry a licence nobody has read yet. They")
        print("  are left out of both piles below. Read the ones marked ????")
        print("  and add them to LICENCE_RULES so the next run can place them.")
    else:
        print("  Every licence in the catalogue has been classified.")


def tally(packages, key, label, top=None):
    """
    Count packages by one property and print the result largest first.

    This exists so that every grouping in the report is produced the same way,
    rather than four slightly different loops that could each be wrong in a
    different manner.
    """
    counts = {}
    for pkg in packages:
        k = key(pkg) or "(none)"
        counts[k] = counts.get(k, 0) + 1
    rows = sorted(counts.items(), key=lambda kv: -kv[1])
    print(f"\n  {label}")
    for name, n in rows[:top] if top else rows:
        print(f"    {str(name)[:52]:54s} {n:6,}")
    if top and len(rows) > top:
        others = sum(n for _, n in rows[top:])
        print(f"    {'(' + str(len(rows) - top) + ' more)':54s} {others:6,}")
    return counts


def overview(packages):
    """Describe the whole catalogue before anything has been filtered out."""
    print()
    print("=" * 78)
    print("THE WHOLE CATALOGUE")
    print("=" * 78)
    print(f"\n  {len(packages):,} packages")
    tally(packages, lambda p: (p.get("organization") or {}).get("title"),
          "by publishing organisation", top=15)
    tally(packages, lambda p: p.get("license_title"), "by licence", top=10)
    tally(packages, best_resource_kind, "by what you can actually get")


def funnel(packages):
    """
    Narrow the catalogue in named steps and print what each step removed.

    The point of doing it this way is that nothing disappears silently. Each
    line below is a rule anyone can disagree with, next to the number of
    packages it cost. A filter you cannot audit is a filter you are trusting
    on faith.
    """
    print()
    print("=" * 78)
    print("NARROWING IT DOWN")
    print("=" * 78)
    print()
    print(f"  {len(packages):6,}  everything in the catalogue")

    openly = [p for p in packages if licence_verdict(p) == "open"]
    shut = [p for p in packages if licence_verdict(p) == "restricted"]
    unread = [p for p in packages if licence_verdict(p) == "unknown"]
    print(f"  {len(openly):6,}  openly licensed        "
          f"(-{len(shut):,} restricted, -{len(unread):,} licence unread)")

    usable = [p for p in openly if best_resource_kind(p) != "none"]
    print(f"  {len(usable):6,}  and offers a file or a service "
          f"(-{len(openly) - len(usable):,} link only)")

    files = [p for p in usable if best_resource_kind(p) == "file"]
    print(f"  {len(files):6,}  of those, offers a real downloadable file")

    print()
    print("  Nothing here is about biodiversity yet. That last number is the")
    print("  honest size of the pile a person has to look through, and it is")
    print("  a complete pile rather than whatever some keywords happened to")
    print("  reach.")

    # The packages the licence filter removed are not rubbish, they are just
    # not ours yet. Written permission can be asked for, so the useful
    # question is which of them would actually give us something if it were
    # granted. A record whose only resource is an internal database view or a
    # link to a web form stays useless no matter what a lawyer says, so the
    # two obstacles have to be counted separately rather than lumped together.
    restricted = shut
    r_files = [p for p in restricted if best_resource_kind(p) == "file"]
    r_service = [p for p in restricted if best_resource_kind(p) == "service"]
    r_none = [p for p in restricted if best_resource_kind(p) == "none"]

    print()
    print("  Of the ones the licence filter removed:")
    print(f"    {len(r_files):6,}  have a real file, so permission alone "
          f"would unlock them")
    print(f"    {len(r_service):6,}  have a service, so permission would "
          f"probably be enough")
    print(f"    {len(r_none):6,}  have nothing to fetch either way, "
          f"permission or not")
    if unread:
        print()
        print(f"  {len(unread):,} more are in neither pile because their licence "
              f"has not been read.")
    return openly, usable, files, restricted, unread


def audit_terms(packages, terms):
    """
    Measure how much each search term would have missed, using the full set.

    Now that every package is on disk, a search term stops being a guess and
    becomes something testable. For each term this counts the packages whose
    text contains it, which is the true answer, and then asks the catalogue
    the same question and reports the difference.

    A term that finds fewer packages than exist is missing some. That is the
    number the earlier probe had no way to produce.
    """
    print()
    print("=" * 78)
    print("HOW GOOD WERE THE SEARCH TERMS")
    print("=" * 78)
    print()
    texts = [(pkg, package_text(pkg)) for pkg in packages]

    for term in terms:
        needle = re.sub(r"\s+", " ", term.strip('"')).lower()
        truth = {p["id"] for p, t in texts if needle in t}

        try:
            r = requests.get(f"{CKAN}/package_search",
                             params={"q": f'"{needle}"', "rows": 1000},
                             headers=HEADERS, timeout=120)
            r.raise_for_status()
            found = {p["id"] for p in r.json()["result"]["results"]}
            time.sleep(0.3)
        except Exception as exc:
            print(f"  {term}: could not ask the catalogue ({exc})")
            continue

        missed = truth - found
        extra = found - truth
        share = len(truth & found) / len(truth) * 100 if truth else 0.0
        print(f"  {term}")
        print(f"    packages whose text contains it   {len(truth):5,}")
        print(f"    the catalogue search returned     {len(found):5,}   "
              f"({share:.0f}% of the true set)")
        if missed:
            print(f"    MISSED by the search              {len(missed):5,}")
            by_id = {p["id"]: p for p, _ in texts}
            for pid in list(missed)[:6]:
                print(f"      - {by_id[pid]['title'][:66]}")
            if len(missed) > 6:
                print(f"      ... and {len(missed) - 6} more")
        if extra:
            print(f"    returned but text does not contain it "
                  f"{len(extra):5,}  (matched on a field we do not read)")
        print()


def write_review(packages, path, note):
    """
    Write one shortlist to a spreadsheet so a person can go through it.

    The whole survey is only worth anything if somebody actually reads the
    result, so this keeps to the few columns you need to make a call and puts
    the catalogue link in every row.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["title", "organisation", "licence", "offers", "formats",
                    "modified", "url", "about"])
        for pkg in sorted(packages,
                          key=lambda p: ((p.get("organization") or {})
                                         .get("title") or "", p.get("title") or "")):
            formats = sorted({(r.get("format") or "?").upper()
                              for r in pkg.get("resources") or []})
            w.writerow([
                pkg.get("title"),
                (pkg.get("organization") or {}).get("title"),
                pkg.get("license_title"),
                best_resource_kind(pkg),
                " ".join(formats),
                (pkg.get("metadata_modified") or "")[:10],
                f"https://catalogue.data.gov.bc.ca/dataset/{pkg.get('name')}",
                re.sub(r"\s+", " ", pkg.get("notes") or "")[:300],
            ])
    print(f"  wrote {path.name}  ({len(packages):,} rows, {note})")


def main():
    """Download the catalogue once, then report on it without searching."""
    parser = argparse.ArgumentParser(description=__doc__)
    here = Path(__file__).parent
    parser.add_argument("--cache-dir", default=str(here / "cache"),
                        help="Where the downloaded catalogue is kept. This "
                             "file is tens of megabytes, so add it to "
                             ".gitignore rather than committing it.")
    parser.add_argument("--out-dir", default=str(here),
                        help="Where the review spreadsheets are written.")
    parser.add_argument("--refresh", action="store_true",
                        help="Download the catalogue again even if a cached "
                             "copy is already here.")
    parser.add_argument("--terms", nargs="*", default=[
        "Conservation Data Centre",
        "species and ecosystems at risk",
        "CDC BIOTICS",
        "biodiversity",
        "species",
    ], help="Search terms to grade against the complete set.")
    args = parser.parse_args()

    cache = Path(args.cache_dir).expanduser() / "bcdc_all_packages.json"
    out = Path(args.out_dir).expanduser()

    print("=" * 78)
    print("DOWNLOADING THE CATALOGUE")
    print("=" * 78)
    print()
    packages = fetch_all(args.refresh, cache)
    if not packages:
        return 1

    overview(packages)
    licence_table(packages)
    openly, usable, files, restricted, unread = funnel(packages)
    audit_terms(packages, args.terms)

    print("=" * 78)
    print("SHORTLISTS TO READ")
    print("=" * 78)
    print()
    write_review(files, out / "bcdc_open_with_files.csv",
                 "open licence and a downloadable file")
    write_review([p for p in usable if best_resource_kind(p) == "service"],
                 out / "bcdc_open_services_only.csv",
                 "open licence but only a live service")
    # Written out too, because an earlier version of this script printed how
    # many packages the licence filter removed and then threw them away. A
    # count you cannot go and look at is not much better than an assumption.
    write_review([p for p in restricted if best_resource_kind(p) != "none"],
                 out / "bcdc_restricted_worth_asking_for.csv",
                 "restricted licence but there is really something there")
    if unread:
        write_review(unread, out / "bcdc_licence_unread.csv",
                     "licence nobody has classified yet, read these")

    print()
    print("=" * 78)
    print("WHAT THIS STILL DOES NOT TELL YOU")
    print("=" * 78)
    print()
    print("  Every number above is about the BC Data Catalogue. Data the")
    print("  province holds and does not publish there is invisible to this")
    print("  and to anything else that reads the catalogue. The Conservation")
    print("  Data Centre's secured occurrence layers are the case in point:")
    print("  they are requested from cdcdata@gov.bc.ca, by a person, in an")
    print("  email. No amount of crawling finds them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
