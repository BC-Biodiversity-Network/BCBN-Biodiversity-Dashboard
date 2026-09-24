"""
Second pass on DataONE, after the first one turned out to be measuring the
wrong thing.

The first probe counted records matching text:"British Columbia". That field
is a catch-all covering everything DataONE indexes, including the depositing
institution. Most of what it found was research deposited by people at UBC
and SFU, on subjects with no connection to the province: condensed matter
physics, US congressional voting, paediatric sepsis. The phrase was matching
"University of British Columbia" in a field we never looked at.

This pass asks the same question against the fields that actually describe
what a record is about, and separates the one repository that dominates the
count from everything else.

It also takes a random sample rather than the newest hundred. The first
sample was sorted by upload date, which is why the repository holding 82
percent of the matches did not appear in it at all.

Usage:

    python probe_dataone_2.py --out-dir exploration/dataone
"""

import argparse
import random
import sys
import time

import pandas as pd
import requests


SOLR = "https://cn.dataone.org/cn/v2/query/solr/"
HEADERS = {"User-Agent": "BCBN-Dashboard probe (UBC Biodiversity Research Centre)"}
PAUSE = 0.5

METADATA = "formatType:METADATA"
HAKAI = 'datasource:"urn:node:HAKAI_IYS"'

# Matching only the fields that say what a record is about, rather than the
# catch-all that also carries who deposited it.
ABOUT_BC = ('(title:"British Columbia" OR abstract:"British Columbia" '
            'OR keywords:"British Columbia")')
ANY_MENTION = 'text:"British Columbia"'

# A box that sits entirely inside the province, rather than merely touching it.
INSIDE_BC = ("northBoundCoord:[* TO 60.05] AND southBoundCoord:[48.20 TO *] "
             "AND eastBoundCoord:[* TO -114.00] AND westBoundCoord:[-139.10 TO *]")


def ask(params, rows=0):
    """Send one query and return the parsed reply, or None if it failed."""
    try:
        r = requests.get(SOLR, params={"wt": "json", "rows": rows, **params},
                         headers=HEADERS, timeout=120)
        r.raise_for_status()
        time.sleep(PAUSE)
        return r.json()
    except Exception as exc:
        print(f"  query failed: {type(exc).__name__}: {exc}")
        return None


def count(query):
    """Return how many records match, or None if the query failed."""
    d = ask({"q": query})
    return d["response"]["numFound"] if d else None


def line(label, n, of=None):
    """Print one count, with a share of a total where that is meaningful."""
    if n is None:
        print(f"  {label:54s}     failed")
    elif of:
        print(f"  {label:54s} {n:>9,}  {n / of * 100:5.1f}%")
    else:
        print(f"  {label:54s} {n:>9,}")


def content_versus_anywhere():
    """
    Compare matching on subject fields against matching anywhere.

    The gap between these two numbers is the size of the problem with the
    first probe. Anything counted by one and not the other mentions British
    Columbia somewhere that is not a description of the data.
    """
    print("=" * 76)
    print("SUBJECT FIELDS VERSUS ANYWHERE IN THE RECORD")
    print("=" * 76)
    print()
    anywhere = count(f"{METADATA} AND {ANY_MENTION}")
    about = count(f"{METADATA} AND {ABOUT_BC}")
    line("mentions BC anywhere (what the first probe counted)", anywhere)
    line("mentions BC in title, abstract or keywords", about, anywhere)
    if anywhere and about:
        line("mentions it only somewhere else", anywhere - about, anywhere)
        print()
        print("  That last line is mostly depositor affiliation. Those records")
        print("  are not about British Columbia in any sense we care about.")
    return anywhere, about


def with_and_without_hakai(about_total):
    """
    Split the count by whether it comes from the salmon programme.

    One repository holds most of the matches. Whether DataONE is worth
    harvesting depends almost entirely on what that one repository contains,
    so it has to be counted on its own.
    """
    print()
    print("=" * 76)
    print("THE SALMON PROGRAMME VERSUS EVERYTHING ELSE")
    print("=" * 76)
    print()
    hakai_all = count(f"{METADATA} AND {HAKAI}")
    hakai_bc = count(f"{METADATA} AND {HAKAI} AND {ABOUT_BC}")
    rest = count(f"{METADATA} AND {ABOUT_BC} AND -{HAKAI}")
    line("every Hakai IYS metadata record", hakai_all)
    line("Hakai records that name BC in a subject field", hakai_bc)
    line("BC records from everywhere else", rest, about_total)
    return rest


def how_many_sit_inside_bc(about_total):
    """
    Count records whose extent is entirely within the province.

    Overlapping the province is weak evidence, since a dataset covering all
    of Canada overlaps it. Sitting inside it is strong evidence.
    """
    print()
    print("=" * 76)
    print("HOW MANY ARE GEOGRAPHICALLY INSIDE BC")
    print("=" * 76)
    print()
    inside = count(f"{METADATA} AND {INSIDE_BC}")
    both = count(f"{METADATA} AND {ABOUT_BC} AND {INSIDE_BC}")
    line("box sits entirely inside BC", inside)
    line("names BC in a subject field and sits inside it", both, about_total)


def hakai_titles(out_dir):
    """
    Pull Hakai titles to see whether 36,000 records means 36,000 datasets.

    A programme that files one record per sampling station produces a very
    large count without producing very much that a person would want to find
    separately. Reading twenty titles settles it.
    """
    print()
    print("=" * 76)
    print("WHAT A HAKAI RECORD ACTUALLY IS")
    print("=" * 76)
    print()
    d = ask({"q": f"{METADATA} AND {HAKAI}", "fl": "id,title,northBoundCoord"},
            rows=20)
    if not d:
        return
    for doc in d["response"]["docs"]:
        print("  -", str(doc.get("title"))[:96])

    # If most titles are the same string with a number changed, the count is
    # one programme rather than thousands of datasets.
    d2 = ask({"q": f"{METADATA} AND {HAKAI}", "facet": "true",
              "facet.field": "title", "facet.limit": 8, "facet.mincount": 2})
    if d2:
        raw = d2["facet_counts"]["facet_fields"]["title"]
        pairs = [(raw[i], raw[i + 1]) for i in range(0, len(raw), 2)]
        if pairs:
            print()
            print("  titles that appear more than once:")
            for title, n in pairs:
                print(f"    {n:>6,}  {str(title)[:80]}")
        else:
            print()
            print("  every title is distinct, so these are not boilerplate copies")


def random_sample(out_dir, n_total):
    """
    Save a random sample rather than the most recent records.

    The first probe sorted by upload date, so it sampled only what happened
    to be new. Starting from a random offset gives a fairer view of the whole
    set.
    """
    print()
    print("=" * 76)
    print("A RANDOM SAMPLE OF THE RECORDS THAT NAME BC IN A SUBJECT FIELD")
    print("=" * 76)
    print()
    if not n_total:
        return
    fields = ("id,title,abstract,keywords,origin,datasource,formatId,"
              "northBoundCoord,southBoundCoord,eastBoundCoord,westBoundCoord")
    rows, want = [], 100
    for _ in range(5):
        if len(rows) >= want:
            break
        start = random.randint(0, max(0, n_total - 20))
        d = ask({"q": f"{METADATA} AND {ABOUT_BC}", "fl": fields, "start": start},
                rows=20)
        if d:
            rows.extend(d["response"]["docs"])
    if not rows:
        return
    frame = pd.json_normalize(rows).drop_duplicates(subset="id")
    path = f"{out_dir}/dataone_bc_sample_random.csv"
    frame.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"  wrote {len(frame)} records to {path}")
    print()
    print("  by repository:", frame["datasource"].value_counts().head(8).to_dict())


def main():
    """Run the second pass and leave a fairer sample behind."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default=".")
    parser.add_argument("--seed", type=int, default=20260924)
    args = parser.parse_args()
    random.seed(args.seed)

    _, about = content_versus_anywhere()
    if not about:
        print("\nCould not reach the index, stopping.")
        return 1
    with_and_without_hakai(about)
    how_many_sit_inside_bc(about)
    hakai_titles(args.out_dir)
    random_sample(args.out_dir, about)
    return 0


if __name__ == "__main__":
    sys.exit(main())
