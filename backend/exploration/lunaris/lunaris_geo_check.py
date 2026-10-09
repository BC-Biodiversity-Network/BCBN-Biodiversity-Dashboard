"""
lunaris_geo_check.py

Read-only check of how Lunaris records describe geography. The harvester
(backend/pipeline/lunaris/lunaris_harvest_only.py) reads geoLocationPlace,
geoLocationPoint and geoLocationBox but ignores geoLocationPolygon; this
counts how much that misses.

Two passes over the OAI-PMH endpoint:
  1. oai_datacite: per record, which geoLocation kinds appear, how many records
     have a polygon as their ONLY coordinates (no box, no point), and the same
     counts per datacentreSymbol (top 20). Up to 20 polygon records are saved
     as raw XML for inspection.
  2. aardvark: how many records have locn_geometry, how many of those are not
     an ENVELOPE (POLYGON, MULTIPOLYGON, ...), and how many have dct_spatial_sm.

Nothing is written except the polygon sample file.

Run:
    python lunaris_geo_check.py                 # first 10,000 of each
    python lunaris_geo_check.py --limit 0       # every record
    python lunaris_geo_check.py --skip-aardvark --limit 50000
"""

import argparse
import json
import os
import time
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict

from sickle import Sickle

LUNARIS_OAI = "https://www.lunaris.ca/oai/"

NS = {
    "datacite": "http://datacite.org/schema/kernel-4",
    "oai_dc_wrap": "http://schema.datacite.org/oai/oai-1.1/",
    "aardvark": "https://www.lunaris.ca/oai/aardvark/",
}

GEO_KINDS = ("place", "point", "box", "polygon")
GEO_TAGS = {
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


def iter_records(sickle, prefix, limit):
    """Yield up to `limit` records (0 = all), printing progress now and then."""
    records = sickle.ListRecords(metadataPrefix=prefix, ignore_deleted=True)
    try:
        total = int(records.resumption_token.complete_list_size)
        print(f"[{prefix}] Lunaris reports {total:,} records.")
    except Exception:
        pass
    for i, rec in enumerate(records, 1):
        yield rec
        if i % 2000 == 0:
            print(f"[{prefix}] {i:,} records read...")
        if limit and i >= limit:
            break


def pct(n, d):
    return f"{100 * n / d:5.1f}%" if d else "    -"


def check_datacite(sickle, limit, sample_path, max_samples=20):
    """Count geoLocation kinds per record and per datacentreSymbol."""
    n = 0
    unparsable = 0
    has = Counter()              # kind -> records with >= 1
    polygon_only = 0
    by_centre = defaultdict(Counter)   # centre -> Counter(total, kinds, polygon_only)
    samples = []

    for rec in iter_records(sickle, "oai_datacite", limit):
        n += 1
        try:
            tree = ET.fromstring(rec.raw)
        except ET.ParseError:
            unparsable += 1
            continue
        centre = tree.findtext(".//oai_dc_wrap:datacentreSymbol", namespaces=NS) or "(none)"
        c = by_centre[centre]
        c["total"] += 1

        found = {k: False for k in GEO_KINDS}
        for geo in tree.findall(".//datacite:geoLocation", NS):
            for kind, tag in GEO_TAGS.items():
                if geo.find(tag, NS) is not None:
                    found[kind] = True
        for kind in GEO_KINDS:
            if found[kind]:
                has[kind] += 1
                c[kind] += 1
        if found["polygon"] and not found["box"] and not found["point"]:
            polygon_only += 1
            c["polygon_only"] += 1
        if found["polygon"] and len(samples) < max_samples:
            samples.append(rec.raw)

    if samples:
        os.makedirs(os.path.dirname(sample_path) or ".", exist_ok=True)
        with open(sample_path, "w", encoding="utf-8") as f:
            f.write('<?xml version="1.0" encoding="UTF-8"?>\n<samples>\n')
            for raw in samples:
                f.write(raw)
                f.write("\n")
            f.write("</samples>\n")

    print(f"\n=== oai_datacite: {n:,} records ({unparsable} unparsable) ===")
    print(f"{'kind':<28}{'records':>10}{'share':>9}")
    for kind in GEO_KINDS:
        print(f"{'geoLocation' + kind.capitalize():<28}{has[kind]:>10,}{pct(has[kind], n):>9}")
    print(f"{'polygon only (no box/pt)':<28}{polygon_only:>10,}{pct(polygon_only, n):>9}")

    print("\nTop 20 datacentreSymbol by record count")
    hdr = f"{'datacentreSymbol':<38}{'total':>8}{'place':>8}{'point':>8}{'box':>8}{'poly':>8}{'polyOnly':>9}"
    print(hdr)
    print("-" * len(hdr))
    top = sorted(by_centre.items(), key=lambda kv: -kv[1]["total"])[:20]
    for centre, c in top:
        print(f"{centre[:37]:<38}{c['total']:>8,}{c['place']:>8,}{c['point']:>8,}"
              f"{c['box']:>8,}{c['polygon']:>8,}{c['polygon_only']:>9,}")

    poly_centres = sorted(((k, v) for k, v in by_centre.items() if v["polygon"]),
                          key=lambda kv: -kv[1]["polygon"])
    if poly_centres:
        print("\nAll datacentreSymbols with any polygon")
        for centre, c in poly_centres[:20]:
            print(f"  {centre[:50]:<52}poly={c['polygon']:,}  polyOnly={c['polygon_only']:,}"
                  f"  of {c['total']:,}")
    print(f"\nSaved {len(samples)} polygon record(s) to {sample_path}" if samples
          else "\nNo polygon records found; no sample file written.")


def check_aardvark(sickle, limit):
    """Count locn_geometry (and its non-ENVELOPE share) and dct_spatial_sm."""
    n = 0
    unparsable = 0
    geom = 0
    non_env = 0
    spatial = 0
    geom_types = Counter()
    non_env_by_provider = Counter()
    non_env_examples = []

    for rec in iter_records(sickle, "aardvark", limit):
        n += 1
        try:
            tree = ET.fromstring(rec.raw)
            body = tree.find(".//aardvark:aardvark", NS)
            doc = json.loads(body.text) if body is not None and body.text else {}
        except (ET.ParseError, json.JSONDecodeError):
            unparsable += 1
            continue

        if doc.get("dct_spatial_sm"):
            spatial += 1
        g = doc.get("locn_geometry")
        if not g:
            continue
        geom += 1
        gtype = g.strip().split("(")[0].strip().upper() or "(unknown)"
        geom_types[gtype] += 1
        if gtype != "ENVELOPE":
            non_env += 1
            non_env_by_provider[doc.get("schema_provider_s", "(none)")] += 1
            if len(non_env_examples) < 3:
                non_env_examples.append((rec.header.identifier, g[:120]))

    print(f"\n=== aardvark: {n:,} records ({unparsable} unparsable) ===")
    print(f"{'field':<28}{'records':>10}{'share':>9}")
    print(f"{'locn_geometry':<28}{geom:>10,}{pct(geom, n):>9}")
    print(f"{'  of which not ENVELOPE':<28}{non_env:>10,}{pct(non_env, geom):>9}  (of locn_geometry)")
    print(f"{'dct_spatial_sm':<28}{spatial:>10,}{pct(spatial, n):>9}")
    if geom_types:
        print("\nlocn_geometry types: " + ", ".join(f"{t}={c:,}" for t, c in geom_types.most_common()))
    if non_env_by_provider:
        print("Non-ENVELOPE by schema_provider_s (top 10):")
        for p, c in non_env_by_provider.most_common(10):
            print(f"  {p[:60]:<62}{c:,}")
    for ident, g in non_env_examples:
        print(f"  e.g. {ident}: {g}")


def main():
    parser = argparse.ArgumentParser(description="Count how Lunaris records describe geography (read-only).")
    parser.add_argument("--limit", type=int, default=10000,
                        help="records to read per format; 0 = all (default 10000)")
    parser.add_argument("--pause", type=float, default=1.0,
                        help="seconds to wait before each OAI request (default 1.0)")
    parser.add_argument("--samples", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "polygon_samples.xml"),
        help="where to save up to 20 polygon records as XML")
    parser.add_argument("--skip-datacite", action="store_true")
    parser.add_argument("--skip-aardvark", action="store_true")
    args = parser.parse_args()

    sickle = PoliteSickle(LUNARIS_OAI, pause=args.pause, max_retries=5, timeout=120)
    if not args.skip_datacite:
        check_datacite(sickle, args.limit, args.samples)
    if not args.skip_aardvark:
        check_aardvark(sickle, args.limit)


if __name__ == "__main__":
    main()
