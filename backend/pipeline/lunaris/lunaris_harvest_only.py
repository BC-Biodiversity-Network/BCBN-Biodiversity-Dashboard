"""
lunaris_harvest_only.py

Harvest ALL Lunaris records once and save to a local parquet, so every
later analysis (keyword tuning, filtering) reads this file instead of
re-harvesting. Harvest is the slow part; do it once.

Extracts per record: id, doi, title, subjects, abstract (HTML-cleaned),
publisher, and geo info (places/points/boxes).

Saves in batches as it goes, so if it is interrupted you can re-run it and it
carries on from the batches already saved.

Output:
    <outdir>/shards/harvest_batch_*.parquet   (one file per batch)
    <outdir>/lunaris_full_harvest.parquet     (all batches joined together)

Run:
    python lunaris_harvest_only.py --outdir harvest
"""

import argparse
import os
import re
import glob
import xml.etree.ElementTree as ET

import pandas as pd
from sickle import Sickle

LUNARIS_OAI = "https://www.lunaris.ca/oai/"

# Lunaris describes its records in the DataCite format. Every field lookup
# below has to quote this label or it finds nothing.
NS = {"datacite": "http://datacite.org/schema/kernel-4"}

# Records per batch file. Resuming counts the files and multiplies by this, so
# changing it breaks a half-finished harvest.
BATCH_SIZE = 5000


# Only these are treated as markup. Matching anything between angle brackets is
# too greedy: it deleted the package name from the real title "Circuit diagrams
# with < q|pic >". A tag name has to follow "<" immediately, which is what keeps
# that title safe even though "q" is itself a tag name.
HTML_TAG = re.compile(
    r"</?(?:a|b|big|blockquote|br|code|div|em|font|h[1-6]|hr|i|img|italic|li"
    r"|ol|p|pre|q|small|span|strong|sub|sup|table|tbody|td|th|thead|tr|u|ul)"
    r"(?:\s[^<>]*)?/?>",
    re.IGNORECASE,
)


def clean_html(text):
    """Strip the web markup out of a description and tidy the spacing.

    Takes a raw string or None, and returns the readable text, or None if
    there was nothing left once the markup was removed. Text that merely looks
    like a tag, such as a package name in angle brackets, is left alone.
    """
    if not text:
        return None
    return re.sub(r"\s+", " ", HTML_TAG.sub(" ", text)).strip() or None


def pick_abstract(tree):
    """Pick the best abstract out of a record's description fields.

    A record can carry several descriptions, so prefer whichever one Lunaris
    labelled as the abstract, and fall back to any non-empty description.
    Where several are left, take the longest -- in practice that is the real
    abstract rather than a one-line note. Returns None if there is nothing
    usable.
    """
    descs = tree.findall(".//datacite:description", NS)
    cands = [(d.get("descriptionType", ""), clean_html(d.text))
             for d in descs if clean_html(d.text)]
    if not cands:
        return None
    abs_only = [t for (dt, t) in cands if dt == "Abstract"]
    pool = abs_only if abs_only else [t for (_, t) in cands]
    return max(pool, key=len)


def normalize_record(record):
    """Turn one record, as Lunaris sent it, into a single flat row.

    Returns a row holding id, doi, title, subjects, abstract, publisher,
    places, points and boxes, or None if the record is too malformed to read
    -- one bad record should not end a 123,000-record harvest.

    Lunaris supplies whatever it happens to have, so every field is optional:
    the row starts out empty and each lookup is checked before it is used.
    """
    try:
        tree = ET.fromstring(record.raw)
    except ET.ParseError:
        return None
    rec = {"id": record.header.identifier, "doi": None, "title": None,
           "subjects": [], "abstract": None, "publisher": None,
           "places": [], "points": [], "boxes": []}

    # DOI is the identifier we want, but not every Lunaris record has one;
    # fall back to the URL identifier so the record stays addressable.
    doi = tree.find('.//datacite:identifier[@identifierType="DOI"]', NS)
    if doi is not None:
        rec["doi"] = doi.text
    else:
        url_id = tree.find('.//datacite:identifier[@identifierType="URL"]', NS)
        if url_id is not None:
            rec["doi"] = url_id.text

    # Titles and subjects get the same cleaning as the abstract. Lunaris sends
    # italics as escaped markup, so without this a title arrives reading
    # "genotypes of <em>Ipomoea hederacea</em>" and that is what anyone
    # reviewing the data would see.
    title = tree.find(".//datacite:title", NS)
    if title is not None:
        rec["title"] = clean_html(title.text)
    # A record can carry many subjects (keywords, disciplines, both
    # languages), so collect all of them - the keyword filter searches the
    # whole list, not just the first. Subjects that are nothing but markup
    # clean down to nothing and are dropped.
    rec["subjects"] = [c for c in (clean_html(s.text)
                                   for s in tree.findall(".//datacite:subject", NS))
                       if c]
    rec["abstract"] = pick_abstract(tree)
    pub = tree.find(".//datacite:publisher", NS)
    if pub is not None:
        rec["publisher"] = pub.text

    # Location is optional and can repeat: a record may list several places,
    # each with any mix of a name, a single point, and a bounding box. Collect
    # all three kinds.
    for geo in tree.findall(".//datacite:geoLocation", NS):
        place = geo.findtext("datacite:geoLocationPlace", namespaces=NS)
        if place:
            rec["places"].append(place)
        point = geo.find("datacite:geoLocationPoint", NS)
        if point is not None:
            lat = point.findtext("datacite:pointLatitude", namespaces=NS)
            lon = point.findtext("datacite:pointLongitude", namespaces=NS)
            if lat and lon:
                rec["points"].append({"lat": float(lat), "lon": float(lon)})
        box = geo.find("datacite:geoLocationBox", NS)
        if box is not None:
            # A box missing one of its four edges can't be turned into
            # numbers; skip it rather than store half a box.
            try:
                rec["boxes"].append({
                    "w": float(box.findtext("datacite:westBoundLongitude", namespaces=NS)),
                    "e": float(box.findtext("datacite:eastBoundLongitude", namespaces=NS)),
                    "s": float(box.findtext("datacite:southBoundLatitude", namespaces=NS)),
                    "n": float(box.findtext("datacite:northBoundLatitude", namespaces=NS)),
                })
            except (TypeError, ValueError):
                pass
    return rec


def main():
    """Harvest every Lunaris record to parquet, resuming if interrupted.

    Saves batch files as it goes plus one merged file under --outdir, and
    refuses to start if the merged file is already there, so a finished
    harvest is never re-fetched by accident.
    """
    parser = argparse.ArgumentParser(description="Harvest all Lunaris records to parquet.")
    parser.add_argument("--outdir", default="harvest")
    args = parser.parse_args()

    shard_dir = os.path.join(args.outdir, "shards")
    os.makedirs(shard_dir, exist_ok=True)
    full_path = os.path.join(args.outdir, "lunaris_full_harvest.parquet")

    # A finished harvest is the expensive artefact here, so never overwrite
    # it implicitly - deleting it is an explicit choice to re-harvest.
    if os.path.exists(full_path):
        print(f"{full_path} already exists. Delete it to re-harvest.")
        return

    # Resume: every finished batch file holds exactly BATCH_SIZE records, so
    # counting the files tells us how much an earlier run already fetched.
    existing = sorted(glob.glob(os.path.join(shard_dir, "harvest_batch_*.parquet")))
    start_batch = len(existing)
    already = start_batch * BATCH_SIZE
    if already:
        print(f"Found {start_batch} shards ({already} records). Resuming.")

    # Start asking Lunaris for records. It usually also reports how many there
    # are in total, which is only used to print progress, so don't give up if
    # that number is missing.
    sickle = Sickle(LUNARIS_OAI)
    records = sickle.ListRecords(metadataPrefix="oai_datacite")
    try:
        total = int(records.resumption_token.complete_list_size)
        print(f"Lunaris reports {total:,} total records.")
    except Exception:
        pass

    # Skip what we already have. Lunaris always starts sending from its very
    # first record and there is no way to ask it to begin partway through, so
    # resuming means fetching and throwing away what earlier runs saved.
    for _ in range(already):
        try:
            records.next()
        except StopIteration:
            break

    # Main loop: fetch a record, flatten it, and write a batch file every
    # BATCH_SIZE records, so an interruption costs at most one batch.
    # `processed` counts records seen; `batch` holds only the readable ones.
    batch, batch_num, processed = [], start_batch, already
    while True:
        try:
            rec = records.next()
        except StopIteration:
            break
        data = normalize_record(rec)
        if data:
            batch.append(data)
        processed += 1
        if len(batch) >= BATCH_SIZE:
            pd.DataFrame(batch).to_parquet(
                os.path.join(shard_dir, f"harvest_batch_{batch_num}.parquet"))
            print(f"Saved shard {batch_num} ({processed} processed)")
            batch, batch_num = [], batch_num + 1
    # Save the last, part-full batch -- too small to have been written by the
    # loop above.
    if batch:
        pd.DataFrame(batch).to_parquet(
            os.path.join(shard_dir, f"harvest_batch_{batch_num}.parquet"))
        print(f"Saved final shard {batch_num} ({processed} processed)")

    # Join every batch file, from this run and any earlier one, into the
    # single file every other script reads.
    shard_files = sorted(glob.glob(os.path.join(shard_dir, "harvest_batch_*.parquet")))
    full = pd.concat([pd.read_parquet(f) for f in shard_files], ignore_index=True)
    full.to_parquet(full_path)
    print(f"\nDone. {len(full):,} records -> {full_path}")


if __name__ == "__main__":
    main()
