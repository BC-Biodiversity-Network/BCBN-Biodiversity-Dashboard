"""
check_harvest.py

Check a finished Lunaris harvest before anything is built from it, and
compare it with an earlier one.

A harvest can finish cleanly and still be wrong: in October 2026 a feed
problem left only 67 Dryad records where September had 5,851, and nothing
stopped. This prints the numbers that show that kind of problem:

  records fetched (raw XML rows) against records read, so the unreadable ones
  unique ids, duplicate rows, and how many ids appear more than once
  the Dryad count (ids starting oai:datadryad.org)
  records with no title, no abstract, or no subjects
  the datestamp range (when Lunaris last changed a record)
  with --compare, the same against the earlier harvest, and a table of
      record counts by source, the sources that changed most listed first

With --live it also asks the Lunaris feed how many records it holds in all
and in the Dryad set, to set against what was fetched. That is two small
requests; nothing is downloaded.

It only reads the harvest files. It can be pointed at a harvest folder (the
--outdir of lunaris_harvest_only.py) or at a lunaris_full_harvest.parquet.

At the end it lists warnings, such as unreadable records or a source that
shrank sharply. A warning is something to look at, not necessarily a fault.

Run, from backend/:
    python pipeline/lunaris/check_harvest.py exploration/lunaris/harvest_2026_10_20 \
        --compare exploration/lunaris/harvest_2026_10 --live
"""

import argparse
import os
import re
import sys
import urllib.request

import pandas as pd

LUNARIS_OAI = "https://www.lunaris.ca/oai/"
DRYAD_PREFIX = "oai:datadryad.org"
DRYAD_SET = "datadryad.org"

# A source is listed as "changed" when its count moved by at least this many
# records and by at least this share of its earlier count. Sources that
# appear or disappear are always listed.
MIN_CHANGE = 50
MIN_SHARE = 0.10

# A source is also a warning when it disappears with at least this many
# records, or loses at least half its records and at least MIN_CHANGE.
MIN_GONE_WARNING = 20


def harvest_files(path):
    """
    Return the paths of a harvest's main file and raw XML file.

    path can be the harvest folder or the main parquet itself. The raw XML
    file is None when the harvest has none (the September 2026 one predates
    it).
    """
    if os.path.isdir(path):
        main = os.path.join(path, "lunaris_full_harvest.parquet")
        raw = os.path.join(path, "lunaris_raw_xml.parquet")
    else:
        main = path
        raw = os.path.join(os.path.dirname(path), "lunaris_raw_xml.parquet")
    if not os.path.exists(main):
        raise SystemExit(f"ERROR: {main} not found. Has the harvest finished? "
                         f"(lunaris_harvest_only.py writes it only at the very end.)")
    return main, (raw if os.path.exists(raw) else None)


def source_of(record_id):
    """
    Return the source of a record from its id, for example datadryad.org.

    Lunaris ids look like oai:<source>:<local id>. A few sources use other
    forms, for which the part before the first colon or underscore is used.
    """
    text = str(record_id)
    if text.startswith("oai:"):
        return text[4:].split(":")[0]
    return text.split(":")[0].split("_")[0]


def is_blank(value):
    """Return True if a cell holds no text: missing, empty, or an empty list."""
    if value is None:
        return True
    if isinstance(value, float) and pd.isna(value):
        return True
    if hasattr(value, "__len__") and not isinstance(value, str):
        return not any(str(item).strip() for item in value)
    return not str(value).strip()


def summarise(path):
    """
    Read one harvest and return its numbers as a dict, plus its counts by source.

    Only the columns needed are read, so this stays quick on a full harvest.
    """
    # pyarrow is imported here, where it is used, rather than at the top:
    # bc_filter_rules imports this file only for source_of, and the BC
    # filter's job on Fir has no pyarrow (on Alliance clusters it comes from
    # the arrow module, which that job does not load).
    import pyarrow.parquet as pq

    main, raw = harvest_files(path)
    available = pq.read_schema(main).names
    columns = [c for c in ["id", "datestamp", "title", "abstract", "subjects"] if c in available]
    frame = pd.read_parquet(main, columns=columns)
    ids = frame["id"].astype(str)
    counts = ids.value_counts()
    numbers = {
        "path": main,
        "fetched": pq.ParquetFile(raw).metadata.num_rows if raw else None,
        "rows": len(frame),
        "unique_ids": int(ids.nunique()),
        "duplicate_rows": int(len(frame) - ids.nunique()),
        "ids_more_than_once": int((counts > 1).sum()),
        "dryad": int(ids.str.startswith(DRYAD_PREFIX).sum()),
        "no_title": int(frame["title"].map(is_blank).sum()) if "title" in frame else None,
        "no_abstract": int(frame["abstract"].map(is_blank).sum()) if "abstract" in frame else None,
        "no_subjects": int(frame["subjects"].map(is_blank).sum()) if "subjects" in frame else None,
        "first_datestamp": frame["datestamp"].min() if "datestamp" in frame else None,
        "last_datestamp": frame["datestamp"].max() if "datestamp" in frame else None,
    }
    numbers["unreadable"] = numbers["fetched"] - numbers["rows"] if numbers["fetched"] is not None else None
    return numbers, ids.map(source_of).value_counts()


def live_counts():
    """
    Ask the Lunaris feed how many records it holds, in all and in the Dryad set.

    Uses ListIdentifiers, whose first page states the full count
    (completeListSize). Returns (total, dryad), each None if the feed did not
    say.
    """
    def size(query):
        """Return completeListSize for one ListIdentifiers query, or None."""
        try:
            with urllib.request.urlopen(f"{LUNARIS_OAI}?{query}", timeout=180) as reply:
                text = reply.read().decode("utf-8", errors="replace")
        except OSError as error:
            print(f"  could not ask the feed: {error}")
            return None
        found = re.search(r'completeListSize="(\d+)"', text)
        return int(found.group(1)) if found else None
    base = "verb=ListIdentifiers&metadataPrefix=oai_datacite"
    return size(base), size(f"{base}&set={DRYAD_SET}")


def number(value):
    """Format a count with thousands separators, or '-' when there is none."""
    return "-" if value is None else f"{value:,}" if isinstance(value, int) else str(value)


def main():
    """Print the checks for the new harvest, and the comparison if one is asked for."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("new", help="the harvest to check: its folder or its lunaris_full_harvest.parquet")
    parser.add_argument("--compare", help="an earlier harvest to compare with")
    parser.add_argument("--live", action="store_true",
                        help="also ask the Lunaris feed for its own record counts")
    parser.add_argument("--all-sources", action="store_true",
                        help="print every source in the table, not only the ones that changed")
    args = parser.parse_args()

    new, new_sources = summarise(args.new)
    old, old_sources = summarise(args.compare) if args.compare else (None, None)
    warnings = []

    rows = [
        ("records fetched (raw XML)", "fetched"),
        ("records read", "rows"),
        ("unreadable (raw only)", "unreadable"),
        ("unique ids", "unique_ids"),
        ("duplicate rows", "duplicate_rows"),
        ("ids that appear more than once", "ids_more_than_once"),
        ("Dryad records", "dryad"),
        ("no title", "no_title"),
        ("no abstract", "no_abstract"),
        ("no subjects", "no_subjects"),
        ("first datestamp", "first_datestamp"),
        ("last datestamp", "last_datestamp"),
    ]
    print(f"new: {new['path']}")
    if old:
        print(f"old: {old['path']}")
    print()
    print(f"{'':32s} {'new':>22s}" + (f" {'old':>22s} {'change':>9s}" if old else ""))
    for label, key in rows:
        line = f"{label:32s} {number(new[key]):>22s}"
        if old:
            change = (new[key] - old[key] if isinstance(new[key], int) and isinstance(old[key], int)
                      else None)
            line += f" {number(old[key]):>22s} {('' if change is None else f'{change:+,}'):>9s}"
        print(line)

    if args.live:
        total, dryad = live_counts()
        print()
        print(f"the feed now reports {number(total)} records, {number(dryad)} in the Dryad set")
        if total is not None and new["fetched"] is not None and abs(total - new["fetched"]) > 0.01 * total:
            warnings.append(f"fetched {new['fetched']:,} records but the feed reports {total:,}")
        if dryad is not None and abs(dryad - new["dryad"]) > 0.05 * dryad:
            warnings.append(f"{new['dryad']:,} Dryad records in the harvest but {dryad:,} in the feed's Dryad set")

    # Counts by source, the biggest changes first.
    if old:
        table = pd.concat({"old": old_sources, "new": new_sources}, axis=1).fillna(0).astype(int)
        table["change"] = table["new"] - table["old"]
        table = table.reindex(table["change"].abs().sort_values(ascending=False, kind="stable").index)
        share = table["change"] / table["old"].where(table["old"] > 0)
        changed = ((table["old"] == 0) | (table["new"] == 0)
                   | ((table["change"].abs() >= MIN_CHANGE) & (share.abs() >= MIN_SHARE)))
        shown = table if args.all_sources else table[changed]
        print()
        print(f"RECORDS BY SOURCE: {len(table)} sources, {int(changed.sum())} changed by at least "
              f"{MIN_CHANGE} records and {MIN_SHARE:.0%}, or appeared or disappeared"
              + ("" if args.all_sources else " (--all-sources lists every one)"))
        print(f"  {'source':60s} {'old':>8s} {'new':>8s} {'change':>8s} {'share':>7s}")
        for source in shown.index:
            before, after, change = (int(table.at[source, column]) for column in ["old", "new", "change"])
            label = "new" if before == 0 else "gone" if after == 0 else f"{share[source]:+.0%}"
            print(f"  {source[:60]:60s} {before:8,} {after:8,} {change:+8,} {label:>7s}")
            # Dryad has its own warning below, so it is not repeated here.
            if source == DRYAD_SET:
                continue
            if (after == 0 and before >= MIN_GONE_WARNING) or (
                    before > 0 and after > 0 and share[source] <= -0.5 and -change >= MIN_CHANGE):
                warnings.append(f"{source} went from {before:,} to {after:,} records")
        if old["dryad"] and new["dryad"] < 0.5 * old["dryad"]:
            warnings.append(f"Dryad fell from {old['dryad']:,} to {new['dryad']:,}")

    if new["unreadable"]:
        warnings.append(f"{new['unreadable']:,} records could not be read (kept in the raw XML only)")
    if new["fetched"] is None:
        warnings.append("no raw XML file, so unreadable records cannot be counted")
    if new["first_datestamp"] is None:
        warnings.append("no datestamp column (an old harvest); make_batches.py needs it")

    print()
    if warnings:
        print("WARNINGS, worth a look before building anything from this harvest:")
        for warning in warnings:
            print(f"  - {warning}")
    else:
        print("No warnings.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
