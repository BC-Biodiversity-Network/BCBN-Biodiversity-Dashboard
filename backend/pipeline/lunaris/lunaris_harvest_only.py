"""
lunaris_harvest_only.py

Harvest ALL Lunaris records once and save to a local parquet, so every
later analysis (keyword tuning, filtering) reads this file instead of
re-harvesting. Harvest is the slow part; do it once.

Extracts per record: id, datestamp (when Lunaris last changed the record),
doi, title, subjects, abstract (HTML-cleaned), publisher, and geo info
(places/points/boxes). Where a record has its title or abstract in more than
one language, the English one is preferred.

Also keeps the raw XML of every record exactly as Lunaris sent it, so a field
we don't extract today can be read later without harvesting again.

Saves in batches as it goes, so if it is interrupted you can re-run it and it
carries on from the batches already saved.

Output:
    <outdir>/shards/harvest_batch_*.parquet      (one file per batch)
    <outdir>/raw_shards/raw_batch_*.parquet      (raw XML, same batch numbers)
    <outdir>/lunaris_full_harvest.parquet        (all batches joined together)
    <outdir>/lunaris_raw_xml.parquet             (all raw batches joined)

Run:
    python lunaris_harvest_only.py --outdir harvest
    python lunaris_harvest_only.py --outdir harvest_test --limit 200   (quick test)
"""

import argparse
import os
import re
import glob
import xml.etree.ElementTree as ET

import pandas as pd
import pyarrow.parquet as pq
from sickle import Sickle

LUNARIS_OAI = "https://www.lunaris.ca/oai/"

# Lunaris describes its records in the DataCite format. Every field lookup
# below has to quote this label or it finds nothing.
NS = {"datacite": "http://datacite.org/schema/kernel-4"}

# The language tag on a title or description (xml:lang="en"). ElementTree
# spells the built-in "xml:" prefix out in full, so this is the attribute name
# it has to be looked up by.
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"

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


def is_english(elem):
    """Say whether a title or description is tagged as English.

    Takes an XML element and returns True if its xml:lang starts with "en"
    (so "en", "en-CA" and "en-US" all count). Untagged text is not assumed to
    be English.
    """
    return elem.get(XML_LANG, "").lower().startswith("en")


def pick_abstract(tree):
    """Pick the best abstract out of a record's description fields.

    A record can carry several descriptions -- often the same abstract in
    English and French, plus short notes. Prefer, in order:
      1. an English description Lunaris labelled as the abstract,
      2. an abstract in any language,
      3. any non-empty description at all.
    Where several are left at a step, take the longest -- in practice that is
    the real abstract rather than a one-line note. Returns None if there is
    nothing usable.
    """
    cands = []
    for d in tree.findall(".//datacite:description", NS):
        text = clean_html(d.text)
        if text:
            cands.append((d.get("descriptionType", ""), is_english(d), text))
    if not cands:
        return None
    en_abs = [t for (dt, en, t) in cands if dt == "Abstract" and en]
    any_abs = [t for (dt, _, t) in cands if dt == "Abstract"]
    pool = en_abs or any_abs or [t for (_, _, t) in cands]
    return max(pool, key=len)


def pick_title(tree):
    """Pick the record's title, preferring English.

    Bilingual records list the title once per language, and the French one
    sometimes comes first. Take the first title tagged English if there is
    one, otherwise the first title. Returns None if no title has any text
    left after cleaning.
    """
    cands = []
    for t in tree.findall(".//datacite:title", NS):
        text = clean_html(t.text)
        if text:
            cands.append((is_english(t), text))
    if not cands:
        return None
    english = [text for (en, text) in cands if en]
    return english[0] if english else cands[0][1]


def normalize_record(record):
    """Turn one record, as Lunaris sent it, into a single flat row.

    Returns a row holding id, datestamp, doi, title, subjects, abstract,
    publisher, places, points and boxes, or None if the record is too malformed to read
    -- one bad record should not end a 123,000-record harvest.

    Lunaris supplies whatever it happens to have, so every field is optional:
    the row starts out empty and each lookup is checked before it is used.
    """
    try:
        tree = ET.fromstring(record.raw)
    except ET.ParseError:
        return None
    rec = {"id": record.header.identifier,
           "datestamp": record.header.datestamp, "doi": None, "title": None,
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
    rec["title"] = pick_title(tree)
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


def raw_row(record):
    """Keep one record exactly as Lunaris sent it.

    Returns a row holding id, datestamp and the record's full XML text, with
    nothing parsed or cleaned, so a field we don't extract today can be read
    later without harvesting again. Unlike normalize_record this never fails,
    so even a record too malformed to read is kept.
    """
    return {"id": record.header.identifier,
            "datestamp": record.header.datestamp,
            "raw_xml": record.raw}


def save_batch(batch, raw_batch, shard_dir, raw_dir, batch_num):
    """Write one batch of rows and its raw XML to their two batch files.

    Both files get the same batch number so they stay in step. The raw file
    is written first: resuming counts the main batch files, so if the run
    dies between the two writes, the batch is simply fetched and written again
    rather than left with no raw XML. Raw XML is long, repetitive text, so it
    is compressed with zstd, which shrinks it far more than the default.
    """
    pd.DataFrame(raw_batch).to_parquet(
        os.path.join(raw_dir, f"raw_batch_{batch_num}.parquet"), compression="zstd")
    pd.DataFrame(batch).to_parquet(
        os.path.join(shard_dir, f"harvest_batch_{batch_num}.parquet"))


def join_raw_batches(raw_dir, raw_path):
    """Join every raw XML batch file into one file, one batch at a time.

    Does the same job as the pandas join used for the main file, but the raw
    XML of a full harvest is several times larger, so instead of loading every
    batch at once this copies them across one by one to keep memory use low.
    Returns the number of records written.
    """
    raw_files = sorted(glob.glob(os.path.join(raw_dir, "raw_batch_*.parquet")),
                       key=batch_number)
    writer, n = None, 0
    for f in raw_files:
        table = pq.read_table(f)
        if writer is None:
            writer = pq.ParquetWriter(raw_path, table.schema, compression="zstd")
        writer.write_table(table)
        n += table.num_rows
    if writer is not None:
        writer.close()
    return n


def batch_number(path):
    """Read the batch number out of a batch file's name.

    Used to put batch files in harvest order: sorting the names as text would
    put batch 10 before batch 2.
    """
    return int(re.search(r"_(\d+)\.parquet$", path).group(1))


def main():
    """Harvest every Lunaris record to parquet, resuming if interrupted.

    Saves batch files as it goes plus one merged file under --outdir (and
    the same again for the raw XML), and refuses to start if the merged file
    is already there, so a finished harvest is never re-fetched by accident.
    --limit stops after that many records, for a quick test run.
    """
    parser = argparse.ArgumentParser(description="Harvest all Lunaris records to parquet.")
    parser.add_argument("--outdir", default="harvest")
    parser.add_argument("--limit", type=int, default=None,
                        help="stop after this many records (for testing); default: all")
    args = parser.parse_args()

    shard_dir = os.path.join(args.outdir, "shards")
    raw_dir = os.path.join(args.outdir, "raw_shards")
    os.makedirs(shard_dir, exist_ok=True)
    os.makedirs(raw_dir, exist_ok=True)
    full_path = os.path.join(args.outdir, "lunaris_full_harvest.parquet")
    raw_path = os.path.join(args.outdir, "lunaris_raw_xml.parquet")

    # A finished harvest is the expensive artefact here, so never overwrite
    # it implicitly - deleting it is an explicit choice to re-harvest.
    if os.path.exists(full_path):
        print(f"{full_path} already exists. Delete it to re-harvest.")
        return

    # Resume: every finished batch holds exactly BATCH_SIZE fetched records
    # (readable or not), so counting the files tells us how much an earlier
    # run already fetched.
    existing = sorted(glob.glob(os.path.join(shard_dir, "harvest_batch_*.parquet")))
    start_batch = len(existing)
    already = start_batch * BATCH_SIZE
    if already:
        print(f"Found {start_batch} shards ({already} records). Resuming.")
    # The raw XML has to cover the same batches, or the raw file would quietly
    # be missing records. That happens if these batches were saved by the old
    # version of this script, before it kept the raw XML; start a fresh
    # --outdir in that case.
    n_raw = len(glob.glob(os.path.join(raw_dir, "raw_batch_*.parquet")))
    if n_raw < start_batch:
        print(f"Only {n_raw} raw XML batches for {start_batch} shards. "
              f"Use a fresh --outdir to harvest with raw XML.")
        return

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
    # BATCH_SIZE records fetched, so an interruption costs at most one batch.
    # `processed` counts records seen; `raw_batch` keeps every record's XML,
    # while `batch` holds only the readable ones. Batches are cut on records
    # fetched, not readable rows, so that resuming skips exactly what was
    # saved -- counting readable rows would re-fetch a few records as
    # duplicates whenever one couldn't be read. `unreadable` counts the
    # records this run could not read; they are kept in the raw XML only.
    batch, raw_batch, batch_num, processed = [], [], start_batch, already
    unreadable = 0
    while args.limit is None or processed < args.limit:
        try:
            rec = records.next()
        except StopIteration:
            break
        raw_batch.append(raw_row(rec))
        data = normalize_record(rec)
        if data:
            batch.append(data)
        else:
            unreadable += 1
        processed += 1
        if len(raw_batch) >= BATCH_SIZE:
            save_batch(batch, raw_batch, shard_dir, raw_dir, batch_num)
            print(f"Saved shard {batch_num} ({processed} processed)")
            batch, raw_batch, batch_num = [], [], batch_num + 1
    if args.limit is not None and processed >= args.limit:
        print(f"Stopped at --limit {args.limit}.")
    # Save the last, part-full batch -- too small to have been written by the
    # loop above.
    if raw_batch:
        save_batch(batch, raw_batch, shard_dir, raw_dir, batch_num)
        print(f"Saved final shard {batch_num} ({processed} processed)")

    # Join every batch file, from this run and any earlier one, into the
    # single file every other script reads. Batches are put in harvest order
    # by number, so the rows come out in the order Lunaris sent them.
    shard_files = sorted(glob.glob(os.path.join(shard_dir, "harvest_batch_*.parquet")),
                         key=batch_number)
    full = pd.concat([pd.read_parquet(f) for f in shard_files], ignore_index=True)
    full.to_parquet(full_path)
    print(f"\nDone. {len(full):,} records -> {full_path}")
    n_raw = join_raw_batches(raw_dir, raw_path)
    print(f"Raw XML of {n_raw:,} records -> {raw_path}")
    print(f"Unreadable records this run: {unreadable:,} (in the raw XML only)")


if __name__ == "__main__":
    main()
