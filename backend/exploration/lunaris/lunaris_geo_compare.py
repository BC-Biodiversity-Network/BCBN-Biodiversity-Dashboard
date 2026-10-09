"""
lunaris_geo_compare.py

Read-only comparison of geography in the live Lunaris feed against our saved
harvest (harvest/lunaris_full_harvest.parquet). lunaris_geo_check.py found a
box on ~35% of the first 10,000 live records, while the September harvest has
a box or point on only ~12% of its records; this checks which of these is the
case:
  - the live records are mostly new (not in our harvest), or
  - the same records have geography live that our harvest didn't read.

Steps:
  1. Fetch the first --limit oai_datacite records (cached as JSON lines so a
     re-run doesn't hit the server again; --refetch to force).
  2. Match them by OAI identifier against the harvest parquet.
  3. Report overlap, the box/point/place share for shared vs new records, and
     shared records that have geography live but none in the harvest, with
     examples showing the live geoLocation XML.

Run:
    python lunaris_geo_compare.py
    python lunaris_geo_compare.py --limit 20000 --cache /tmp/live.jsonl
"""

import argparse
import json
import os
import time
import xml.etree.ElementTree as ET

import pandas as pd
from sickle import Sickle

LUNARIS_OAI = "https://www.lunaris.ca/oai/"
HERE = os.path.dirname(os.path.abspath(__file__))

NS = {
    "datacite": "http://datacite.org/schema/kernel-4",
    "wrap": "http://schema.datacite.org/oai/oai-1.1/",
}
KINDS = {
    "place": "datacite:geoLocationPlace",
    "point": "datacite:geoLocationPoint",
    "box": "datacite:geoLocationBox",
    "polygon": "datacite:geoLocationPolygon",
}


class PoliteSickle(Sickle):
    """Sickle that waits before every HTTP request (one per page of records)."""

    def __init__(self, *args, pause=1.0, **kwargs):
        super().__init__(*args, **kwargs)
        self.pause = pause

    def harvest(self, **kwargs):
        time.sleep(self.pause)
        return super().harvest(**kwargs)


def summarize_live(rec):
    """Pull id, title, centre, geo flags and raw geoLocation XML from one record."""
    row = {"id": rec.header.identifier, "title": None, "centre": None,
           "geo_xml": [], **{k: False for k in KINDS}}
    try:
        tree = ET.fromstring(rec.raw)
    except ET.ParseError:
        return row
    row["title"] = tree.findtext(".//datacite:title", namespaces=NS)
    row["centre"] = tree.findtext(".//wrap:datacentreSymbol", namespaces=NS)
    for geo in tree.findall(".//datacite:geoLocation", NS):
        row["geo_xml"].append(ET.tostring(geo, encoding="unicode"))
        for kind, tag in KINDS.items():
            if geo.find(tag, NS) is not None:
                row[kind] = True
    return row


def fetch_live(limit, pause, cache):
    if os.path.exists(cache):
        rows = [json.loads(line) for line in open(cache, encoding="utf-8")]
        if len(rows) >= limit:
            print(f"Using cached {len(rows):,} live records from {cache}")
            return rows[:limit]
    sickle = PoliteSickle(LUNARIS_OAI, pause=pause, max_retries=5, timeout=120)
    rows = []
    os.makedirs(os.path.dirname(cache) or ".", exist_ok=True)
    with open(cache, "w", encoding="utf-8") as f:
        for i, rec in enumerate(sickle.ListRecords(metadataPrefix="oai_datacite",
                                                  ignore_deleted=True), 1):
            row = summarize_live(rec)
            rows.append(row)
            f.write(json.dumps(row) + "\n")
            if i % 2000 == 0:
                print(f"  {i:,} live records read...")
            if i >= limit:
                break
    return rows


def nonempty(x):
    return x is not None and len(x) > 0


def pct(n, d):
    return f"{100 * n / d:5.1f}%" if d else "    -"


def main():
    p = argparse.ArgumentParser(description="Compare live Lunaris geography with our harvest (read-only).")
    p.add_argument("--limit", type=int, default=10000)
    p.add_argument("--pause", type=float, default=1.0)
    p.add_argument("--harvest", default=os.path.join(HERE, "harvest", "lunaris_full_harvest.parquet"))
    p.add_argument("--cache", default=os.path.join(HERE, "harvest", "live_geo_sample.jsonl"),
                   help="JSON-lines cache of the live records")
    p.add_argument("--refetch", action="store_true")
    args = p.parse_args()

    if args.refetch and os.path.exists(args.cache):
        os.remove(args.cache)
    live = pd.DataFrame(fetch_live(args.limit, args.pause, args.cache))
    harv = pd.read_parquet(args.harvest, columns=["id", "places", "points", "boxes"])
    for c in ("places", "points", "boxes"):
        harv["h_" + c] = harv[c].apply(nonempty)
    harv = harv[["id", "h_places", "h_points", "h_boxes"]].drop_duplicates("id")

    m = live.merge(harv, on="id", how="left", indicator=True)
    # The left join leaves the harvest flags as object dtype; ~ on Python bools
    # gives -1/-2 (both truthy), so force real booleans before any masking.
    for c in ("h_places", "h_points", "h_boxes"):
        m[c] = m[c].fillna(False).astype(bool)
    both = m[m["_merge"] == "both"]
    new = m[m["_merge"] == "left_only"]
    n = len(m)

    print(f"\nLive records: {n:,}   harvest records: {len(harv):,}")
    print(f"  in our harvest: {len(both):,} ({pct(len(both), n)})")
    print(f"  new (not in harvest): {len(new):,} ({pct(len(new), n)})")

    print(f"\n{'share with ...':<22}{'both (live)':>13}{'both (harv)':>13}{'new (live)':>12}")
    for kind, hcol in (("box", "h_boxes"), ("point", "h_points"), ("place", "h_places")):
        print(f"{kind:<22}{pct(both[kind].sum(), len(both)):>13}"
              f"{pct(both[hcol].sum(), len(both)):>13}{pct(new[kind].sum(), len(new)):>12}")
    any_live = both["box"] | both["point"]
    any_harv = both["h_boxes"] | both["h_points"]
    print(f"{'box or point':<22}{pct(any_live.sum(), len(both)):>13}"
          f"{pct(any_harv.sum(), len(both)):>13}{pct((new['box'] | new['point']).sum(), len(new)):>12}")

    print("\nShared records with geography live but none in harvest:")
    for kind, hcol in (("box", "h_boxes"), ("point", "h_points"), ("place", "h_places")):
        k = (both[kind] & ~both[hcol]).sum()
        print(f"  {kind:<6} live, no {kind} in harvest: {k:,}")
    print(f"  the reverse (in harvest, not live): box={(~both['box'] & both['h_boxes']).sum():,} "
          f"point={(~both['point'] & both['h_points']).sum():,} "
          f"place={(~both['place'] & both['h_places']).sum():,}")

    missing = both[(both["box"] | both["point"] | both["place"])
                   & ~(both["h_boxes"] | both["h_points"] | both["h_places"])]
    print(f"  any geo live, none at all in harvest: {len(missing):,}")
    if len(missing):
        print("  by datacentreSymbol: " + ", ".join(
            f"{c}={v:,}" for c, v in missing["centre"].value_counts().head(10).items()))
        print("\nExamples:")
        for _, r in missing.head(5).iterrows():
            print(f"\n- {r['id']}\n  title: {r['title']}")
            for g in r["geo_xml"][:3]:
                print("  " + g[:600])

    print("\nNew records by datacentreSymbol (top 10): " + ", ".join(
        f"{c}={v:,}" for c, v in new["centre"].value_counts().head(10).items()))
    print("New records with a box, by datacentreSymbol (top 10): " + ", ".join(
        f"{c}={v:,}" for c, v in new[new["box"]]["centre"].value_counts().head(10).items()))


if __name__ == "__main__":
    main()
