"""
Find out how much of DataONE is worth harvesting for a BC biodiversity platform.

This does not build anything. It answers four questions so we can decide
whether DataONE is worth a harvester at all:

  1. How big is it, and how much of it is metadata rather than data files
  2. How many records look like they are about British Columbia, by text
  3. How many overlap British Columbia geographically, by bounding box
  4. Which repositories those records come from

DataONE indexes every object its member repositories hold, and most of those
objects are data files rather than metadata records. Only the metadata
records are comparable to a Lunaris record, so everything below filters to
those first.

Two ways of asking "is this about BC" are counted separately on purpose. A
record can say British Columbia in its title and carry no coordinates, or
carry a bounding box over BC and never write the name down. Neither one is
the whole answer, and the gap between them is itself worth knowing, because
it tells us whether a geographic filter can lean on coordinates or has to
read text.

Usage:

    python probe_dataone.py
    python probe_dataone.py --out-dir exploration/dataone
"""

import argparse
import json
import sys
import time
from urllib.parse import urlencode

import pandas as pd
import requests


SOLR = "https://cn.dataone.org/cn/v2/query/solr/"

# British Columbia's bounding box, in degrees. Generous on purpose: the point
# here is to find candidates, not to decide them.
BC_NORTH, BC_SOUTH = 60.05, 48.20
BC_WEST, BC_EAST = -139.10, -114.00

# Be polite to a shared public service.
HEADERS = {"User-Agent": "BCBN-Dashboard probe (UBC Biodiversity Research Centre)"}
PAUSE = 0.5


def ask(params, rows=0):
    """
    Send one query to the DataONE search index and return the parsed reply.

    Every call goes through here so the politeness pause and the error
    handling live in one place. A failure returns None rather than raising,
    because one broken query should not lose the rest of the report.
    """
    p = {"wt": "json", "rows": rows, **params}
    try:
        r = requests.get(SOLR, params=p, headers=HEADERS, timeout=120)
        r.raise_for_status()
        time.sleep(PAUSE)
        return r.json()
    except Exception as exc:
        print(f"  query failed: {type(exc).__name__}: {exc}")
        print(f"  {SOLR}?{urlencode(p)}")
        return None


def count(query):
    """Return how many records match a query, or None if the query failed."""
    d = ask({"q": query})
    return d["response"]["numFound"] if d else None


def show(label, n, of=None):
    """Print one count as a line, with a share of a total where that helps."""
    if n is None:
        print(f"  {label:52s}  failed")
    elif of:
        print(f"  {label:52s} {n:>10,}   {n / of * 100:5.1f}%")
    else:
        print(f"  {label:52s} {n:>10,}")


def probe_schema():
    """
    Pull one metadata record and list the fields it actually carries.

    The field names below are the ones DataONE documents, but an index can
    drift from its documentation. Printing a real record first means a
    missing field shows up here as an obvious gap rather than later as a
    count of zero that looks like a finding.
    """
    print("=" * 74)
    print("WHAT A METADATA RECORD LOOKS LIKE")
    print("=" * 74)
    d = ask({"q": "formatType:METADATA"}, rows=1)
    if not d or not d["response"]["docs"]:
        print("  could not fetch a sample record")
        return set()
    doc = d["response"]["docs"][0]
    fields = sorted(doc)
    print(f"\n  {len(fields)} fields on this record:\n")
    for i in range(0, len(fields), 3):
        print("   " + "".join(f"{f:26s}" for f in fields[i:i + 3]))

    wanted = ["title", "abstract", "keywords", "origin", "datasource",
              "northBoundCoord", "southBoundCoord", "eastBoundCoord",
              "westBoundCoord", "formatId", "dateUploaded"]
    missing = [f for f in wanted if f not in doc]
    if missing:
        print(f"\n  NOT on this record, so the counts below may undercount: {missing}")
        print("  (a field can be absent from one record and present on others)")
    return set(fields)


def size_of_the_index():
    """Count the whole index and the metadata slice of it."""
    print()
    print("=" * 74)
    print("HOW BIG IS IT")
    print("=" * 74)
    print()
    everything = count("*:*")
    metadata = count("formatType:METADATA")
    show("every object DataONE indexes", everything)
    show("metadata records (comparable to a Lunaris record)", metadata, everything)
    return metadata


def bc_by_text(metadata_total):
    """
    Count records that name British Columbia somewhere in their text.

    Three spellings are counted separately. The French one matters because
    Canadian federal records are often bilingual, and the bare "BC" one is
    counted only to show how noisy it is, since those two letters appear in
    plenty of unrelated contexts.
    """
    print()
    print("=" * 74)
    print("HOW MANY SAY BRITISH COLUMBIA")
    print("=" * 74)
    print()
    named = count('formatType:METADATA AND text:"British Columbia"')
    french = count('formatType:METADATA AND text:"Colombie-Britannique"')
    either = count('formatType:METADATA AND (text:"British Columbia" '
                   'OR text:"Colombie-Britannique")')
    show('"British Columbia"', named, metadata_total)
    show('"Colombie-Britannique"', french, metadata_total)
    show("either spelling", either, metadata_total)
    return either


def bc_by_coordinates(metadata_total):
    """
    Count records whose bounding box overlaps British Columbia.

    Two boxes overlap when neither sits entirely on one side of the other, so
    the test is: this record starts south of BC's top edge, ends north of its
    bottom edge, starts west of its right edge, and ends east of its left
    edge. All four have to hold.

    Records with no coordinates at all are counted first, because they are
    the ceiling on what any coordinate based filter can ever reach.
    """
    print()
    print("=" * 74)
    print("HOW MANY OVERLAP BC GEOGRAPHICALLY")
    print("=" * 74)
    print()
    has_box = count("formatType:METADATA AND northBoundCoord:[-90 TO 90]")
    show("carry a bounding box at all", has_box, metadata_total)

    overlap = (f"formatType:METADATA"
               f" AND southBoundCoord:[* TO {BC_NORTH}]"
               f" AND northBoundCoord:[{BC_SOUTH} TO *]"
               f" AND westBoundCoord:[* TO {BC_EAST}]"
               f" AND eastBoundCoord:[{BC_WEST} TO *]")
    n = count(overlap)
    show("overlap the BC bounding box", n, metadata_total)
    if has_box and n:
        show("  as a share of those that have a box", n, has_box)
    print()
    print("  Note: a record covering all of Canada, or the whole Pacific, also")
    print("  overlaps BC by this test. Treat it as an upper bound.")
    return overlap, n


def how_much_do_the_two_agree(spatial_query):
    """
    Count records found by both tests, and by only one of them.

    If the two barely overlap, a geographic filter cannot be built on
    coordinates alone, because most BC records would never be seen.
    """
    print()
    print("=" * 74)
    print("DO THE TWO TESTS FIND THE SAME RECORDS")
    print("=" * 74)
    print()
    named = '(text:"British Columbia" OR text:"Colombie-Britannique")'
    both = count(f"{spatial_query} AND {named}")
    text_only = count(f'formatType:METADATA AND {named} '
                      f'AND -({spatial_query.replace("formatType:METADATA AND ", "")})')
    show("found by both", both)
    show("named BC but no overlapping box", text_only)
    print()
    print("  A large text-only number means coordinates are not enough on their")
    print("  own and the geographic filter has to read the text as well.")


def which_repositories(spatial_query):
    """
    List the repositories holding the BC-looking records.

    DataONE is a federation, so this says who we would actually be
    harvesting from, and whether any of them is one we already cover.
    """
    print()
    print("=" * 74)
    print("WHICH REPOSITORIES THEY COME FROM")
    print("=" * 74)
    print()
    named = ('formatType:METADATA AND (text:"British Columbia" '
             'OR text:"Colombie-Britannique")')
    d = ask({"q": named, "facet": "true", "facet.field": "datasource",
             "facet.limit": 25, "facet.mincount": 1})
    if not d:
        return None
    raw = d["facet_counts"]["facet_fields"]["datasource"]
    rows = [{"datasource": raw[i], "records": raw[i + 1]}
            for i in range(0, len(raw), 2)]
    frame = pd.DataFrame(rows)
    for _, r in frame.iterrows():
        print(f"  {r['records']:>8,}  {r['datasource']}")
    return frame


def sample_records(out_dir):
    """
    Save a hundred BC-looking records so a person can read them.

    Counts say how many. Only reading the records says whether they are the
    kind of thing the platform should hold.
    """
    print()
    print("=" * 74)
    print("A SAMPLE TO READ")
    print("=" * 74)
    print()
    fields = ("id,title,abstract,keywords,origin,datasource,formatId,"
              "northBoundCoord,southBoundCoord,eastBoundCoord,westBoundCoord,"
              "dateUploaded")
    d = ask({"q": 'formatType:METADATA AND (text:"British Columbia" '
                  'OR text:"Colombie-Britannique")',
             "fl": fields, "sort": "dateUploaded desc"}, rows=100)
    if not d:
        return None
    frame = pd.json_normalize(d["response"]["docs"])
    path = f"{out_dir}/dataone_bc_sample.csv"
    frame.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"  wrote {len(frame)} records to {path}")
    print(f"  columns: {list(frame.columns)}")
    return frame


def main():
    """Run every probe in turn and leave a sample file behind."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default=".",
                        help="Where to write the sample CSV and the facet table")
    args = parser.parse_args()

    probe_schema()
    metadata_total = size_of_the_index()
    if not metadata_total:
        print("\nCould not reach the index, stopping.")
        return 1

    bc_by_text(metadata_total)
    spatial_query, _ = bc_by_coordinates(metadata_total)
    how_much_do_the_two_agree(spatial_query)

    facets = which_repositories(spatial_query)
    if facets is not None:
        facets.to_csv(f"{args.out_dir}/dataone_bc_repositories.csv",
                      index=False, encoding="utf-8-sig")

    sample_records(args.out_dir)

    print()
    print("=" * 74)
    print("WHAT TO DO WITH THIS")
    print("=" * 74)
    print()
    print("  Read the sample file before deciding anything. The counts say how")
    print("  many records mention BC. They do not say whether those records are")
    print("  biodiversity data, or whether we already have them through Lunaris.")
    print()
    print("  The next question after this one is overlap with Lunaris, which")
    print("  needs a join on DOI or title against the Lunaris harvest.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
