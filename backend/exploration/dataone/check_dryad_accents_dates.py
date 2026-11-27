"""
check_dryad_accents_dates.py (read only, 2026-11-27)

Two checks on DataONE's Dryad records, for deciding how the Datasets page
shows the new Dryad records (the ones not in Lunaris), which come from
DataONE.

Part 1. Broken accents
  Some DataONE text holds UTF-8 bytes that were read one byte at a time as
  Latin-1 or Windows-1252, so "’" shows as "â\\x80\\x99" and "é" as "Ã©".
  1. Trace: follows 5 affected records through every saved step, from the
     Dryad document stored on DataONE (saved 2026-11-23 for 2 of them), the
     search API answer, the harvest pages, the joined harvest, the versions
     file, the filter 2 and filter 3 input files, to the filter 3 final
     table. At each step it looks at the stored bytes, not at how Python
     prints the text.
  2. Asks DataONE's search API (the Solr endpoint the harvest used) once
     for each of the 5 records, with fl=* so every index field comes back:
     at most 5 requests in all, at least 1.05 s apart, with the harvest's
     User-Agent. Each answer is saved unchanged (bytes and headers), and a
     second run reads the saved answers and sends nothing. A 403, a 429, a
     503 or any other answer than 200 or 404 stops the script.
  3. Counts broken records by datasource and by field in the 5,071 shown
     records and in the 3,525 new records (rule below), and compares the
     rule with a simple pattern rule.
  4. Checks whether the broken text can be turned back: whole value as
     Latin-1 to UTF-8, and run by run; and, for the 594 Dryad pairs, whether
     the fixed DataONE text equals the Lunaris text.

  The "broken" rule (BROKEN_CHAR below)
    Every character is turned into one byte: a character below U+0100 into
    its own code (the Latin-1 reading), one of the 27 Windows-1252 extra
    characters (€ ‚ ƒ „ … † ‡ ˆ ‰ Š ‹ Œ Ž ‘ ’ “ ” • – — ˜ ™ š › œ ž Ÿ) into its
    Windows-1252 byte, anything else into "?". A broken character is a run
    of 2 to 4 of these bytes that is one valid UTF-8 character of two or
    more bytes (lead byte C2 to F4, then 1 to 3 bytes 80 to BF). Plain
    ASCII never matches, and real accented text almost never does, because
    in real text an accented letter is not followed by symbols such as © ¨ ¼
    or by invisible control characters.
    kind: latin1 when the run holds an invisible control character U+0080
    to U+009F (the bytes were read as Latin-1), cp1252 when it holds one
    of the 27 extra characters, either when every character is in U+00A0 to
    U+00FF (both readings give the same text).
    May miss: text where a byte was lost or replaced on the way (for
    example "Swainson?s", "Swainsonâs" or the replacement character U+FFFD),
    since nothing is left to decode; these are counted apart as "damaged"
    signs (DAMAGED below), which can also catch real text (French "â").
    May wrongly catch: real text with a letter from U+00C2 to U+00F4
    followed by one to three characters from U+0080 to U+00BF (for example
    "Ã" then "±"); not seen in the checks against Lunaris below.

  The simple pattern rule (SIMPLE below), for comparison: "Ã" followed by
  any character, "â€", or "Â". It misses Latin-1 breakage of curly quotes
  and dashes ("â" then an invisible control), the most common Dryad kind,
  and it catches real "Â".

Part 2. Dryad dates
  5. For the Dryad records among the 3,525 new records: how often pubDate,
     dateUploaded and dateModified (the three date fields the harvest took)
     are filled, and which date fields the 5 full (fl=*) answers hold.
  6. For the 594 Dryad Lunaris-DataONE pairs: each DataONE date field (its
     date part, as given, in UTC) against the Lunaris issued date: same day,
     within 7 days, within 30 days, same calendar year, more than 365 days
     apart, and which way; the 10 biggest differences of each field.

Outputs go to ~/Desktop/BCBN/Results/dataone_dryad_check_2026-11-27/.
The script changes no data file.

Run, from backend/:
    ../.venv/bin/python exploration/dataone/check_dryad_accents_dates.py
"""

import csv
import gzip
import html
import json
import os
import re
import sys
import time
import unicodedata
from datetime import date, datetime

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import requests
from requests.utils import get_encoding_from_headers, guess_json_utf

# --- Places and settings ---------------------------------------------------------

BACKEND = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
DATA = os.path.join(BACKEND, "data", "dataone")
HARVEST = os.path.join(DATA, "harvest_2026_11_18")
PAGES = os.path.join(HARVEST, "pages")
JOINED = os.path.join(HARVEST, "dataone_full_harvest.parquet")
VERSIONS = os.path.join(HARVEST, "dataone_full_harvest_versions.parquet")
FILTER2_INPUT = os.path.join(DATA, "filter2_input", "dataone_harvest_2026_11_18", "inputs")
FILTER3_INPUT = os.path.join(DATA, "filter3_input", "dataone_needs_llm", "records.jsonl.gz")
FILTER3_FINAL = os.path.join(DATA, "filter3", "filter3_final.parquet")
LUNARIS = os.path.join(BACKEND, "data", "api", "lunaris_datasets.parquet")
RESULTS = os.path.expanduser("~/Desktop/BCBN/Results")
PER_RECORD = os.path.join(RESULTS, "dataone_licence_check_2026-11-25", "licence_per_record.csv")
PAIRS = os.path.join(RESULTS, "dataone_lunaris_copies_2026-11-25", "copy_pairs.csv")
DRYAD_ORIGINALS = os.path.join(RESULTS, "dataone_dryad_abstract_test_2026-11-23")
OUT = os.path.join(RESULTS, "dataone_dryad_check_2026-11-27")
ANSWERS = os.path.join(OUT, "solr_answers")
LOG = os.path.join(OUT, "requests_log.csv")

SOLR = "https://cn.dataone.org/cn/v2/query/solr/"
HEADERS = {"User-Agent": "BCBN-Dashboard (BC Biodiversity Network, UBC; songyanf@student.ubc.ca)"}
REQUEST_LIMIT = 5
MIN_GAP = 1.05
TIMEOUT = 120

EXPECTED_SHOWN = 5_071
EXPECTED_NEW = 3_525
EXPECTED_DRYAD_PAIRS = 594

# The two new records whose Dryad documents were saved on 2026-11-23 (both broken in the abstract).
WITH_ORIGINAL = ["sha256:19902005bad7cbb9152cdb8fa453bbf3b1b23d773e96cbeac016d899fb35d399",
                 "sha256:e4c0b92076ec6140316b74c280603d7106a37f5014136fa61721adb8f6eb151e"]

# Text fields that are checked; ids, dates, codes and box numbers are left out.
TEXT_FIELDS = ["title", "abstract", "keywords", "origin", "namedLocation", "placeKey", "site",
               "contactOrganization", "licenseName", "licenseDescription", "licenseUrl", "webUrl",
               "scientificName", "kingdom", "phylum", "class", "order", "family", "genus", "species"]
MAIN_FIELDS = ["title", "abstract", "keywords", "origin"]
DATE_FIELDS = ["pubDate", "dateUploaded", "dateModified"]
# Dryad's records were loaded into DataONE in two big batches (2,763 latest
# records uploaded in 2019-09 and 24,138 in 2020-06, see
# dryad_uploads_by_month.csv), so an upload date before this day says when
# the batch ran, not when the dataset was published.
BULK_LOAD_END = "2020-07-01"

# The 27 Windows-1252 characters that are not in Latin-1, and their bytes.
CP1252_EXTRA = {}
for _b in range(0x80, 0xA0):
    try:
        CP1252_EXTRA[bytes([_b]).decode("cp1252")] = _b
    except UnicodeDecodeError:
        pass

# One UTF-8 character of 2 to 4 bytes, as bytes.
UTF8_MULTI = re.compile(rb"[\xc2-\xdf][\x80-\xbf]|[\xe0-\xef][\x80-\xbf]{2}|[\xf0-\xf4][\x80-\xbf]{3}")
# The simple pattern rule, for comparison.
SIMPLE = re.compile("Ã.|â€|Â", re.S)
# Signs of text that was damaged and cannot be decoded (counted apart).
DAMAGED = re.compile("�|[\u0080-\u009f]|[ÃÂâ](?=[\\s?'\"])")

pd.set_option("display.width", 250)
pd.set_option("display.max_colwidth", 80)
pd.set_option("display.max_rows", 200)


# --- Small helpers ---------------------------------------------------------------

def as_list(value):
    """Return a field value as a list of strings: [] when missing, [text] for one text, the list for a list."""
    if value is None or (isinstance(value, float) and value != value):
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


def as_bytes_one_per_char(text):
    """Return text as bytes, one byte per character: Latin-1 code, Windows-1252 byte, or '?' for anything else."""
    out = bytearray()
    for ch in text:
        code = ord(ch)
        if code < 0x100:
            out.append(code)
        elif ch in CP1252_EXTRA:
            out.append(CP1252_EXTRA[ch])
        else:
            out.append(0x3F)
    return bytes(out)


def broken_runs(text):
    """
    Return the broken characters in text, as a list of (start, end, broken
    run, likely correct character, kind). See the rule in the module
    docstring.
    """
    found = []
    mapped = as_bytes_one_per_char(text)
    for m in UTF8_MULTI.finditer(mapped):
        try:
            fixed = m.group(0).decode("utf-8")
        except UnicodeDecodeError:
            continue
        run = text[m.start():m.end()]
        if any("\u0080" <= ch <= "\u009f" for ch in run):
            kind = "latin1"
        elif any(ch in CP1252_EXTRA for ch in run):
            kind = "cp1252"
        else:
            kind = "either"
        found.append((m.start(), m.end(), run, fixed, kind))
    return found


def fix_runs(text):
    """Return text with every broken run replaced by its UTF-8 character, repeated until nothing changes (at most 3 rounds), and the number of rounds that changed something."""
    rounds = 0
    for _ in range(3):
        runs = broken_runs(text)
        if not runs:
            break
        rounds += 1
        pieces, last = [], 0
        for start, end, _run, fixed, _kind in runs:
            pieces.append(text[last:start])
            pieces.append(fixed)
            last = end
        pieces.append(text[last:])
        text = "".join(pieces)
    return text, rounds


def fix_whole(text):
    """Return text encoded as Latin-1 and decoded as UTF-8, or None when that fails."""
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return None


def utf8_hex(text):
    """Return the UTF-8 bytes of text as spaced hex, for example 'c3 a2 c2 80 c2 99'."""
    return text.encode("utf-8").hex(" ")


def around(text, start, end, width=30):
    """Return text from width characters before start to width after end, with invisible controls shown as \\x.."""
    piece = text[max(0, start - width):end + width]
    return show(piece)


def show(text):
    """Return text with invisible control characters U+0080 to U+009F written as \\x.., so they can be seen."""
    return re.sub("[\u0080-\u009f]", lambda m: f"\\x{ord(m.group(0)):02x}", text)


def spaces(text):
    """Return text with runs of white space made one space, trimmed."""
    return re.sub(r"\s+", " ", text or "").strip()


def write_csv(frame, name):
    """Write a table to the output folder as UTF-8 with a BOM (so Excel reads the accents)."""
    frame.to_csv(os.path.join(OUT, name), index=False, encoding="utf-8-sig")


# --- The record sets ---------------------------------------------------------------

def read_sets():
    """Return the 5,071 shown records (licence_per_record.csv: id, datasource, new_or_lunaris) and the 594 Dryad pairs."""
    per = pd.read_csv(PER_RECORD, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    if len(per) != EXPECTED_SHOWN:
        sys.exit(f"expected {EXPECTED_SHOWN} shown records, found {len(per)}")
    new = per[per["new_or_lunaris"] == "new record"]
    if len(new) != EXPECTED_NEW:
        sys.exit(f"expected {EXPECTED_NEW} new records, found {len(new)}")
    pairs = pd.read_csv(PAIRS, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    pairs = pairs[pairs["datasource"] == "DRYAD"]
    if len(pairs) != EXPECTED_DRYAD_PAIRS:
        sys.exit(f"expected {EXPECTED_DRYAD_PAIRS} Dryad pairs, found {len(pairs)}")
    print(f"Sets: {len(per):,} shown, {len(new):,} new, {(per['new_or_lunaris'] == 'Lunaris copy').sum():,} Lunaris "
          f"copies, {(per['new_or_lunaris'] == 'DOI second copy').sum():,} DOI second copies; {len(pairs)} Dryad pairs")
    return per[["record_id", "datasource", "new_or_lunaris"]], pairs


def read_versions(ids):
    """Return the latest-version rows of the given ids from the versions file, indexed by id."""
    columns = ["id"] + TEXT_FIELDS + DATE_FIELDS + ["obsoletes", "version_status"]
    v = pq.read_table(VERSIONS, columns=columns, filters=[("id", "in", list(ids))]).to_pandas()
    v = v[v["version_status"] == "latest"].drop_duplicates("id").set_index("id")
    return v


# --- Part 1, step 3 and 4: counting and fixing ------------------------------------

def scan_records(per, versions):
    """
    Return one row per record and field with any broken run, simple-pattern
    hit or damaged sign: counts, kinds, whether each fix works, and the
    first broken run with its likely correct text.
    """
    rows = []
    for rid, ds, group in per[["record_id", "datasource", "new_or_lunaris"]].itertuples(index=False):
        r = versions.loc[rid]
        for field in TEXT_FIELDS:
            values = as_list(r[field])
            runs_all, simple_hits, damaged_values, whole_fail, rounds_max, left_after = [], 0, 0, 0, 0, 0
            first = None
            unescaped_only = 0
            for value in values:
                runs = broken_runs(value)
                runs_all += runs
                if SIMPLE.search(value):
                    simple_hits += 1
                if not runs and broken_runs(html.unescape(value)):
                    unescaped_only += 1
                fixed, rounds = fix_runs(value)
                rounds_max = max(rounds_max, rounds)
                if DAMAGED.search(fixed):
                    damaged_values += 1
                if runs:
                    if fix_whole(value) is None:
                        whole_fail += 1
                    if broken_runs(fixed) or SIMPLE.search(fixed) or re.search("[\u0080-\u009f�]", fixed):
                        left_after += 1
                    if first is None:
                        s, e, run, good, _k = runs[0]
                        first = (around(value, s, e), around(fixed, s, s + len(good)), run, good)
            if not (runs_all or simple_hits or damaged_values or unescaped_only):
                continue
            kinds = sorted({k for *_x, k in runs_all})
            rows.append({"record_id": rid, "datasource": ds, "new_or_lunaris": group, "field": field,
                         "values": len(values), "broken_runs": len(runs_all),
                         "broken": "yes" if runs_all else "no", "kinds": "+".join(kinds),
                         "simple_rule": "yes" if simple_hits else "no",
                         "broken_only_after_html_unescape": unescaped_only,
                         "damaged_signs_after_fix": damaged_values,
                         "fix_rounds": rounds_max,
                         "whole_value_latin1_fails": whole_fail,
                         "values_still_broken_after_run_fix": left_after,
                         "first_broken_run": show(first[2]) if first else "",
                         "first_run_utf8_bytes": utf8_hex(first[2]) if first else "",
                         "likely_correct": first[3] if first else "",
                         "broken_text": first[0] if first else "",
                         "fixed_text": first[1] if first else ""})
    return pd.DataFrame(rows)


def counts_by_datasource_field(found, records, label):
    """Return a table: per datasource, records in the set, records broken in each main field, in any other field, and in any field."""
    b = found[found["broken"] == "yes"]
    b = b[b["record_id"].isin(records["record_id"])]
    out = []
    for ds, part in records.groupby("datasource"):
        bp = b[b["datasource"] == ds]
        row = {"set": label, "datasource": ds, "records": len(part)}
        for field in MAIN_FIELDS:
            row[field] = bp.loc[bp["field"] == field, "record_id"].nunique()
        row["other_fields"] = bp.loc[~bp["field"].isin(MAIN_FIELDS), "record_id"].nunique()
        row["any_field"] = bp["record_id"].nunique()
        row["share_any"] = round(row["any_field"] / len(part), 3) if len(part) else 0
        out.append(row)
    table = pd.DataFrame(out)
    table = table[table["any_field"] > 0].sort_values("any_field", ascending=False)
    total = {"set": label, "datasource": "ALL", "records": len(records)}
    for field in MAIN_FIELDS:
        total[field] = b.loc[b["field"] == field, "record_id"].nunique()
    total["other_fields"] = b.loc[~b["field"].isin(MAIN_FIELDS), "record_id"].nunique()
    total["any_field"] = b["record_id"].nunique()
    total["share_any"] = round(total["any_field"] / len(records), 3)
    return pd.concat([table, pd.DataFrame([total])], ignore_index=True)


def other_field_counts(found, records, label):
    """Return records broken per field for the fields outside the four main ones."""
    b = found[(found["broken"] == "yes") & found["record_id"].isin(records["record_id"])
              & ~found["field"].isin(MAIN_FIELDS)]
    t = b.groupby(["datasource", "field"])["record_id"].nunique().reset_index(name="records_broken")
    t.insert(0, "set", label)
    return t


def rule_comparison(found, records, label):
    """Return how the broken rule and the simple pattern rule agree, per record (any field)."""
    f = found[found["record_id"].isin(records["record_id"])]
    per = f.groupby("record_id").agg(rule=("broken", lambda s: (s == "yes").any()),
                                     simple=("simple_rule", lambda s: (s == "yes").any()),
                                     damaged=("damaged_signs_after_fix", "sum"),
                                     html_only=("broken_only_after_html_unescape", "sum"))
    return pd.DataFrame([{"set": label,
                          "broken_rule": int(per["rule"].sum()),
                          "simple_rule": int(per["simple"].sum()),
                          "both": int((per["rule"] & per["simple"]).sum()),
                          "broken_rule_only": int((per["rule"] & ~per["simple"]).sum()),
                          "simple_rule_only": int((~per["rule"] & per["simple"]).sum()),
                          "records_with_damaged_signs_after_fix": int((per["damaged"] > 0).sum()),
                          "records_broken_only_after_html_unescape": int(((per["html_only"] > 0) & ~per["rule"]).sum())}])


def fix_summary(found, records, label):
    """Return, per datasource, how many broken records each fix fails on."""
    b = found[(found["broken"] == "yes") & found["record_id"].isin(records["record_id"])]
    rows = []
    for ds, part in list(b.groupby("datasource")) + [("ALL", b)]:
        per = part.groupby("record_id").agg(whole=("whole_value_latin1_fails", "sum"),
                                            left=("values_still_broken_after_run_fix", "sum"),
                                            damaged=("damaged_signs_after_fix", "sum"),
                                            rounds=("fix_rounds", "max"),
                                            kinds=("kinds", lambda s: "+".join(sorted(set("+".join(s).split("+"))))))
        rows.append({"set": label, "datasource": ds, "broken_records": len(per),
                     "kind_latin1": int(per["kinds"].str.contains("latin1").sum()),
                     "kind_cp1252": int(per["kinds"].str.contains("cp1252").sum()),
                     "whole_value_latin1_fails": int((per["whole"] > 0).sum()),
                     "run_fix_leaves_broken": int((per["left"] > 0).sum()),
                     "damaged_signs_after_run_fix": int((per["damaged"] > 0).sum()),
                     "needs_two_rounds": int((per["rounds"] > 1).sum())})
    return pd.DataFrame(rows)


def loose_words(text):
    """Return text for comparing with Lunaris: HTML entities undone, Unicode composed (NFC), curly quotes straight, commas dropped, spaces collapsed, lower case."""
    text = unicodedata.normalize("NFC", html.unescape(text or ""))
    text = text.translate(str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"'}))
    return spaces(text.replace(",", " ")).casefold()


def loose_text(text):
    """Return loose_words(text) with every space taken out (Lunaris adds spaces where Dryad had HTML tags)."""
    return re.sub(r"\s+", "", loose_words(text))


def name_key(names):
    """Return a list of names as a sorted list of their sorted words (so 'Medina, Iliana' and 'Iliana Medina' match)."""
    return sorted(" ".join(sorted(loose_words(n).split())) for n in names)


def check_against_lunaris(pairs, versions):
    """
    For the 594 Dryad pairs, check each broken run against the Lunaris copy
    (which was harvested from Dryad itself). For every broken run in the
    DataONE title, abstract, creators and keywords, the fixed character with
    up to 12 characters on each side (also fixed) is looked for in the
    Lunaris title, abstract, creators and subjects (all through loose_text,
    since Lunaris makes curly quotes straight, adds spaces where HTML tags
    were and writes names 'Last, First'). A run also counts as found with 5 characters on each side.
    Also, for whole fields: the fixed title (without 'Data from: ') equals
    the Lunaris title, and the fixed creators equal the Lunaris creators as
    names. both_sides_found_in_lunaris: the 12 characters before and the
    12 after are each found in Lunaris, so the passage is there and the run
    can be checked. Return (one row per run, one row per pair and field).
    """
    lun = pq.read_table(LUNARIS, columns=["id", "title", "abstract", "creators", "subjects"]).to_pandas().set_index("id")
    runs_rows, field_rows = [], []
    for rid, lid in zip(pairs["record_id"], pairs["lunaris_id"]):
        d, l = versions.loc[rid], lun.loc[lid]
        haystack = loose_text(" | ".join([l["title"] or "", l["abstract"] or ""] + as_list(l["creators"]) + as_list(l["subjects"])))
        for field in ["title", "abstract", "origin", "keywords"]:
            for value in as_list(d[field]):
                for s, e, run, good, kind in broken_runs(value):
                    found = "no"
                    for width in (12, 5):
                        needle = fix_runs(value[max(0, s - width):s])[0] + good + fix_runs(value[e:e + width])[0]
                        if loose_text(needle) in haystack:
                            found = f"yes ({width} each side)"
                            break
                    broken_there = loose_text(value[max(0, s - 5):e + 5]) in haystack
                    left = loose_text(fix_runs(value[max(0, s - 12):s])[0])
                    right = loose_text(fix_runs(value[e:e + 12])[0])
                    passage = "yes" if (left in haystack and right in haystack) else "no"
                    runs_rows.append({"record_id": rid, "lunaris_id": lid, "field": field, "broken_run": show(run),
                                      "likely_correct": good, "kind": kind, "fixed_found_in_lunaris": found,
                                      "broken_found_in_lunaris": "yes" if broken_there else "no",
                                      "both_sides_found_in_lunaris": passage,
                                      "dataone_piece": around(value, s, e, 20)})
        lt = re.sub(r"^\s*data from:\s*", "", l["title"] or "", flags=re.I)
        dt = d["title"] or ""
        if broken_runs(dt):
            fixed = re.sub(r"^\s*data from:\s*", "", fix_runs(dt)[0], flags=re.I)
            field_rows.append({"record_id": rid, "field": "title", "same_before_fix": loose_text(dt) == loose_text(lt),
                               "same_after_fix": loose_text(fixed) == loose_text(lt), "dataone": show(dt),
                               "fixed": fixed, "lunaris": lt})
        names = as_list(d["origin"])
        if any(broken_runs(n) for n in names):
            fixed = [fix_runs(n)[0] for n in names]
            field_rows.append({"record_id": rid, "field": "origin",
                               "same_before_fix": name_key(names) == name_key(as_list(l["creators"])),
                               "same_after_fix": name_key(fixed) == name_key(as_list(l["creators"])),
                               "dataone": show(" | ".join(names))[:300], "fixed": " | ".join(fixed)[:300],
                               "lunaris": " | ".join(as_list(l["creators"]))[:300]})
    return pd.DataFrame(runs_rows), pd.DataFrame(field_rows)


def pick_examples(found, pairs, lunaris_texts, n=10):
    """Return n examples of broken text and fixed text side by side: Dryad pairs (with the Lunaris text) first, then Dryad new records (creators with an accented letter when there is one), then one per other datasource."""
    b = found[(found["broken"] == "yes")].sort_values(["record_id", "field"])
    pair_ids = set(pairs["record_id"])
    picks = []
    dryad_pairs = b[(b["datasource"] == "DRYAD") & b["record_id"].isin(pair_ids)]
    letter = b["first_broken_run"].str.startswith("Ã")  # an accented letter such as é, rather than a quote or dash
    for field in ["title", "origin", "abstract", "keywords"]:
        part = dryad_pairs[dryad_pairs["field"] == field]
        picks += (part[letter] if field == "origin" and letter[part.index].any() else part).head(1).to_dict("records")
    dryad_new = b[(b["datasource"] == "DRYAD") & (b["new_or_lunaris"] == "new record")]
    for field in ["title", "abstract", "origin"]:
        part = dryad_new[dryad_new["field"] == field]
        picks += (part[letter] if field == "origin" and letter[part.index].any() else part).head(1).to_dict("records")
    for ds, part in b[b["datasource"] != "DRYAD"].groupby("datasource"):
        if len(picks) >= n:
            break
        picks += part.head(1).to_dict("records")
    rows = []
    for p in picks[:n]:
        rows.append({"record_id": p["record_id"], "datasource": p["datasource"], "set": p["new_or_lunaris"],
                     "field": p["field"], "broken_run": p["first_broken_run"], "run_bytes": p["first_run_utf8_bytes"],
                     "likely_correct": p["likely_correct"], "broken_text": p["broken_text"], "fixed_text": p["fixed_text"],
                     "lunaris_text": lunaris_texts.get((p["record_id"], p["field"]), "")})
    return pd.DataFrame(rows)


# --- Part 1, steps 1 and 2: the trace and the 5 requests -----------------------------

def requests_sent():
    """Return how many requests this check has sent (rows in its log)."""
    if not os.path.exists(LOG):
        return 0
    with open(LOG, encoding="utf-8") as handle:
        return max(0, sum(1 for _ in handle) - 1)


_last_start = [0.0]


def ask_solr(rid, number):
    """
    Return (status, raw bytes, headers) of the search API answer for one
    record, asking DataONE only if no answer is saved yet. Stops on a
    refusal or an unexpected answer, and before a 6th request.
    """
    body_path = os.path.join(ANSWERS, f"answer_{number}.json")
    head_path = os.path.join(ANSWERS, f"answer_{number}.headers.json")
    if os.path.exists(head_path):
        with open(head_path, encoding="utf-8") as handle:
            saved = json.load(handle)
        body = open(body_path, "rb").read() if os.path.exists(body_path) else b""
        return saved["status"], body, saved["headers"]
    if requests_sent() >= REQUEST_LIMIT:
        sys.exit(f"the limit of {REQUEST_LIMIT} requests is reached; nothing more is asked")
    wait = MIN_GAP - (time.time() - _last_start[0])
    if wait > 0:
        time.sleep(wait)
    params = {"q": f'id:"{rid}"', "fl": "*", "rows": 1, "wt": "json"}
    start = _last_start[0] = time.time()
    try:
        answer = requests.get(SOLR, params=params, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as error:
        log_request(rid, f"error {type(error).__name__}", 0, "", SOLR, time.time() - start)
        sys.exit(f"the request for {rid} failed before DataONE answered: {error}")
    seconds = time.time() - start
    log_request(rid, answer.status_code, len(answer.content), answer.headers.get("Content-Type", ""), answer.url, seconds)
    status = answer.status_code
    if status in (403, 429) or status >= 500:
        sys.exit(f"DataONE refused or failed: HTTP {status} for {rid}; stopped\n{answer.text[:300]}")
    if status not in (200, 404):
        sys.exit(f"unexpected answer HTTP {status} for {rid}; stopped\n{answer.text[:300]}")
    os.makedirs(ANSWERS, exist_ok=True)
    if status == 200:
        with open(body_path, "wb") as handle:
            handle.write(answer.content)
    with open(head_path, "w", encoding="utf-8") as handle:
        json.dump({"record_id": rid, "status": status, "url": answer.url, "headers": dict(answer.headers)}, handle, indent=1)
    return status, answer.content, dict(answer.headers)


def log_request(rid, status, size, content_type, url, seconds):
    """Add one row to requests_log.csv (when, record, status, bytes, content type, seconds, url)."""
    new = not os.path.exists(LOG)
    os.makedirs(OUT, exist_ok=True)
    with open(LOG, "a", newline="", encoding="utf-8") as handle:
        w = csv.writer(handle)
        if new:
            w.writerow(["when", "record_id", "status", "bytes", "content_type", "seconds", "url"])
        w.writerow([datetime.now().isoformat(timespec="seconds"), rid, status, size, content_type, f"{seconds:.2f}", url])


def decode_like_harvest(body, headers):
    """Return the answer parsed the way harvest_dataone.py line 195 (requests' Response.json) did, and the encoding used."""
    encoding = get_encoding_from_headers({k.lower(): v for k, v in headers.items()}) if headers else None
    if not encoding:
        encoding = guess_json_utf(body)
    return json.loads(body.decode(encoding)), encoding


def pick_trace_records(found, pairs):
    """Return the 5 traced records: the 2 with a saved Dryad document, 1 more new Dryad record and 2 Dryad pairs, each with a broken title (by id order)."""
    b = found[(found["broken"] == "yes") & (found["datasource"] == "DRYAD") & (found["field"] == "title")]
    b = b.sort_values("record_id")
    new = [r for r in b.loc[b["new_or_lunaris"] == "new record", "record_id"] if r not in WITH_ORIGINAL][:1]
    two_pairs = [r for r in b["record_id"] if r in set(pairs["record_id"])][:2]
    return WITH_ORIGINAL + new + two_pairs


def first_broken(text):
    """Return (shown run, its UTF-8 bytes, a short piece around it) for the first broken run in text, or empty strings."""
    runs = broken_runs(text or "")
    if not runs:
        return "", "", ""
    s, e, run, _good, _k = runs[0]
    return show(run), utf8_hex(run), around(text, s, e, 20)


def parquet_raw(path, rid, field):
    """Return (stored UTF-8 bytes as hex of the first broken run, the decoded text) of one field of one record in a parquet file, read as binary."""
    t = pq.read_table(path, columns=["id", field], filters=[("id", "=", rid)])
    if t.num_rows == 0:
        return None, None
    col = t.column(field)
    if pa.types.is_list(col.type):
        text = " | ".join(v for v in col.to_pylist()[0] or [])
        return None, text
    raw = pc.cast(col, pa.binary()).to_pylist()[0]
    if raw is None:
        return b"", ""
    return raw, raw.decode("utf-8")


def trace(ids, found):
    """
    Follow the traced records through every saved step and return one row
    per record and step: whether the field is broken there, the first broken
    run, its bytes as stored, and a note on what was read.
    """
    rows = []
    field_of = {}
    for rid in ids:
        f = found[(found["record_id"] == rid) & (found["broken"] == "yes")]
        field_of[rid] = "abstract" if rid in WITH_ORIGINAL else "title"

    def add(rid, step, text, stored, note):
        """Add one trace row."""
        run, run_hex, piece = first_broken(text) if text is not None else ("", "", "")
        rows.append({"record_id": rid, "field": field_of[rid], "step": step,
                     "found": "no" if text is None else "yes", "broken": "yes" if run else ("" if text is None else "no"),
                     "first_broken_run": run, "run_as_utf8": run_hex, "stored_bytes": stored, "piece": piece, "note": note})

    # Step a: the Dryad document stored on DataONE (cn/v2/object), saved unchanged on 2026-11-23.
    for rid in ids:
        path = os.path.join(DRYAD_ORIGINALS, rid.replace(":", "_") + ".bin")
        if not os.path.exists(path):
            add(rid, "a. Dryad document on DataONE (saved 2026-11-23)", None, "", "not saved for this record")
            continue
        raw = open(path, "rb").read()
        esc = re.search(rb"\\u00[c-f][0-9a-f](\\u00[89ab][0-9a-f]){1,3}", raw)
        doc = json.loads(raw.decode("utf-8"))
        desc = doc.get("description")
        text = " ".join(desc) if isinstance(desc, list) else (desc or "")
        nonascii = sum(1 for x in raw if x > 0x7F)
        add(rid, "a. Dryad document on DataONE (saved 2026-11-23)", text,
            esc.group(0).decode("ascii") if esc else "",
            f"{len(raw)} bytes, {nonascii} non-ASCII bytes: the broken characters are written as JSON escapes, "
            "so the document itself names the wrong characters")

    # Step b: the search API answer now (raw bytes, at most 5 requests).
    answers = {}
    for number, rid in enumerate(ids, 1):
        status, body, headers = ask_solr(rid, number)
        if status != 200:
            add(rid, "b. search API answer (2026-11-27)", None, "", f"HTTP {status}")
            continue
        parsed, encoding = decode_like_harvest(body, headers)
        docs = parsed["response"]["docs"]
        doc = docs[0] if docs else {}
        answers[rid] = (doc, body, headers, encoding)
        text = doc.get(field_of[rid]) or ""
        run, _h, _p = first_broken(text)
        stored = ""
        if run:
            first = broken_runs(text)[0][2]
            stored = utf8_hex(first) if first.encode("utf-8") in body else "(not found as UTF-8 bytes in the answer)"
        strict_same = json.loads(body.decode("utf-8", errors="strict")) == parsed
        add(rid, "b. search API answer (2026-11-27)", text, stored,
            f"Content-Type {headers.get('Content-Type', '')!r}; requests reads it as {encoding}; strict UTF-8 reading "
            f"gives {'the same' if strict_same else 'different'} text; {len(body)} bytes")

    # Steps c to e: the harvest pages, the joined harvest and the versions file (text read as raw bytes).
    page_of = {}
    for name in sorted(os.listdir(PAGES)):
        if not name.endswith(".parquet"):
            continue
        t = pq.read_table(os.path.join(PAGES, name), columns=["id"], filters=[("id", "in", ids)])
        for rid in t.column("id").to_pylist():
            page_of[rid] = name
    for rid in ids:
        for step, path in [("c. harvest page " + page_of.get(rid, "?"), os.path.join(PAGES, page_of.get(rid, "none"))),
                           ("d. dataone_full_harvest.parquet", JOINED),
                           ("e. dataone_full_harvest_versions.parquet", VERSIONS)]:
            if not os.path.exists(path):
                add(rid, step, None, "", "no page holds this record")
                continue
            raw, text = parquet_raw(path, rid, field_of[rid])
            stored = ""
            if text:
                runs = broken_runs(text)
                if runs:
                    stored = runs[0][2].encode("utf-8").hex(" ") if runs[0][2].encode("utf-8") in raw else "(not found)"
            same_api = ""
            if rid in answers and text is not None:
                same_api = "; same text as the search API answer now" if (answers[rid][0].get(field_of[rid]) or "") == text \
                    else "; DIFFERENT from the search API answer now"
            add(rid, step, text, stored, "text column read as bytes, decoded as UTF-8" + same_api)

    # Step f: the filter 2 input (the prompt text sent to the model).
    wanted = set(ids)
    seen = {}
    for name in sorted(os.listdir(FILTER2_INPUT)):
        with gzip.open(os.path.join(FILTER2_INPUT, name), "rb") as handle:
            for line in handle:
                if not wanted - set(seen):
                    break
                for rid in wanted - set(seen):
                    if rid.encode() in line:
                        seen[rid] = (name, line)
    for rid in ids:
        if rid not in seen:
            add(rid, "f. filter 2 input", None, "", "not in the filter 2 input (Lunaris copy: filter 2 reused Lunaris)")
            continue
        name, line = seen[rid]
        rec = json.loads(line.decode("utf-8"))
        text = rec.get(field_of[rid]) or rec.get("description") or rec.get("abstract") or ""
        if field_of[rid] == "abstract" and not text:
            text = " ".join(str(v) for k, v in rec.items() if k not in ("title", "subjects"))
        esc = "\\u00" in line.decode("utf-8")
        add(rid, f"f. filter 2 input {name}", text, "", f"JSON line; broken characters stored as {'JSON escapes' if esc else 'UTF-8 bytes'}")

    # Step g: the filter 3 input (the record text for the model).
    seen = {}
    with gzip.open(FILTER3_INPUT, "rt", encoding="utf-8") as handle:
        for line in handle:
            rec = json.loads(line)
            if rec["record_id"] in wanted:
                seen[rec["record_id"]] = rec
    for rid in ids:
        if rid not in seen:
            add(rid, "g. filter 3 input", None, "", "not in the filter 3 model input (filter 3 took the Lunaris class or a rule)")
            continue
        record = seen[rid]["record"]
        record = record if isinstance(record, dict) else json.loads(record)
        add(rid, "g. filter 3 input", str(record.get(field_of[rid], "")) or json.dumps(record, ensure_ascii=False), "",
            "records.jsonl.gz, record field")

    # Step h: the filter 3 final table (it keeps the title only).
    final = pq.read_table(FILTER3_FINAL, columns=["record_id", "title", "frontend_group", "route"],
                          filters=[("record_id", "in", ids)]).to_pandas().set_index("record_id")
    for rid in ids:
        if field_of[rid] != "title":
            title = final.loc[rid, "title"] if rid in final.index else None
            run, _h, _p = first_broken(title or "")
            add(rid, "h. filter 3 final (title only kept)", None, "",
                f"no abstract column; its title is {'broken' if run else 'not broken'}; group {final.loc[rid, 'frontend_group']}, route {final.loc[rid, 'route']}")
            continue
        add(rid, "h. filter 3 final", final.loc[rid, "title"], "",
            f"group {final.loc[rid, 'frontend_group']}, route {final.loc[rid, 'route']}")
    order = {s: i for i, s in enumerate(["a", "b", "c", "d", "e", "f", "g", "h"])}
    out = pd.DataFrame(rows)
    out["_o"] = out["step"].str[0].map(order)
    out["_r"] = out["record_id"].map({r: i for i, r in enumerate(ids)})
    return out.sort_values(["_r", "_o"]).drop(columns=["_o", "_r"]), answers, page_of


def same_page_check(page_name, versions_ids=None):
    """Return, for one harvest page, per datasource: records with non-ASCII text, with broken runs, and with correct accents only (decoded in the same call)."""
    t = pq.read_table(os.path.join(PAGES, page_name), columns=["id", "datasource", "title", "abstract"]).to_pandas()
    rows = []
    for ds, part in t.groupby("datasource"):
        text = (part["title"].fillna("") + " " + part["abstract"].fillna(""))
        has_non_ascii = text.map(lambda s: any(ord(c) > 127 for c in s))
        broken = text.map(lambda s: bool(broken_runs(s)))
        rows.append({"page": page_name, "datasource": ds, "records": len(part),
                     "with_non_ascii": int(has_non_ascii.sum()), "with_broken_runs": int(broken.sum()),
                     "non_ascii_and_not_broken": int((has_non_ascii & ~broken).sum())})
    return pd.DataFrame(rows).sort_values("records", ascending=False)


# --- Part 2: dates -------------------------------------------------------------------

def day_of(text):
    """Return the date part (YYYY-MM-DD) of a date text as a date, or None."""
    if not isinstance(text, str) or len(text) < 10:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def date_fill(per, versions, answers):
    """Return how often each harvested date field is filled for the new Dryad records, and the date fields in the 5 full answers."""
    new_dryad = per[(per["datasource"] == "DRYAD") & (per["new_or_lunaris"] == "new record")]["record_id"]
    v = versions.loc[list(new_dryad)]
    rows = []
    for field in DATE_FIELDS:
        filled = v[field].map(lambda x: isinstance(x, str) and x.strip() != "")
        years = v.loc[filled, field].str[:4]
        rows.append({"field": field, "new_dryad_records": len(v), "filled": int(filled.sum()),
                     "earliest": v.loc[filled, field].min() if filled.any() else "",
                     "latest": v.loc[filled, field].max() if filled.any() else "",
                     "by_year": json.dumps(years.value_counts().sort_index().to_dict()) if filled.any() else ""})
    fill = pd.DataFrame(rows)
    seen = []
    for rid, (doc, _b, _h, _e) in answers.items():
        for k, val in doc.items():
            if re.search(r"date|time|year|issued|publi", k, re.I) or (isinstance(val, str) and re.match(r"^\d{4}-\d\d-\d\dT", val)):
                seen.append({"record_id": rid, "field": k, "value": json.dumps(val)[:120]})
    return fill, pd.DataFrame(seen), v


def compare_dates(pairs, versions, answers):
    """Return one row per Dryad pair with each DataONE date field's difference in days from Lunaris issued (positive = DataONE later)."""
    rows = []
    for rid, issued in zip(pairs["record_id"], pairs["issued_lunaris"]):
        row = {"record_id": rid, "issued_lunaris": issued}
        lday = day_of(issued)
        for field in DATE_FIELDS:
            val = versions.loc[rid, field]
            row[field] = val if isinstance(val, str) else ""
            dday = day_of(val)
            row[field + "_days"] = (dday - lday).days if (dday and lday) else None
            row[field + "_same_year"] = (dday.year == lday.year) if (dday and lday) else None
        rows.append(row)
    return pd.DataFrame(rows)


def date_buckets(table):
    """Return, per DataONE date field, counts of pairs by how far the date is from Lunaris issued, and which way."""
    rows = []
    for field in DATE_FIELDS:
        d = table[field + "_days"].dropna()
        sy = table[field + "_same_year"].dropna()
        a = d.abs()
        rows.append({"field": field, "pairs": len(table), "both_filled": len(d),
                     "same_day": int((a == 0).sum()), "within_7_days": int((a <= 7).sum()),
                     "within_30_days": int((a <= 30).sum()), "same_calendar_year": int(sy.astype(bool).sum()),
                     "more_than_365_days": int((a > 365).sum()),
                     "dataone_earlier": int((d < 0).sum()), "dataone_later": int((d > 0).sum()),
                     "median_days": float(d.median()) if len(d) else None,
                     "min_days": int(d.min()) if len(d) else None, "max_days": int(d.max()) if len(d) else None})
    return pd.DataFrame(rows)


def upload_group(uploaded, obsoletes):
    """Return a record's dateUploaded group: first version on DataONE or a later one, uploaded before or after the bulk loads."""
    first = "first version" if not isinstance(obsoletes, str) or not obsoletes else "later version"
    when = "after bulk loads" if isinstance(uploaded, str) and uploaded >= BULK_LOAD_END else "in or before bulk loads"
    return f"{first}, {when}"


def dateuploaded_by_group(table, versions, per):
    """
    Return (for the Dryad pairs, dateUploaded against Lunaris issued per
    upload group; for the new Dryad records, how many fall in each group).
    """
    t = table.copy()
    t["group"] = [upload_group(versions.loc[r, "dateUploaded"], versions.loc[r, "obsoletes"]) for r in t["record_id"]]
    rows = []
    for g, part in sorted(t.groupby("group"), key=lambda x: x[0]):
        d = part["dateUploaded_days"].dropna()
        a = d.abs()
        rows.append({"group": g, "pairs": len(part), "same_day": int((a == 0).sum()), "within_7_days": int((a <= 7).sum()),
                     "within_30_days": int((a <= 30).sum()),
                     "same_calendar_year": int(part["dateUploaded_same_year"].astype(bool).sum()),
                     "more_than_365_days": int((a > 365).sum()), "dataone_earlier": int((d < 0).sum()),
                     "dataone_earlier_by_364_to_366_days": int(d.between(-366, -364).sum()),
                     "dataone_later": int((d > 0).sum()), "median_days": float(d.median())})
    new_ids = per[(per["datasource"] == "DRYAD") & (per["new_or_lunaris"] == "new record")]["record_id"]
    new_groups = pd.Series([upload_group(versions.loc[r, "dateUploaded"], versions.loc[r, "obsoletes"]) for r in new_ids])
    return pd.DataFrame(rows), new_groups.value_counts().rename_axis("group").reset_index(name="new_dryad_records")


def uploads_by_month():
    """Return the number of latest Dryad records in the harvest uploaded in each month (to show the bulk loads)."""
    v = pq.read_table(VERSIONS, columns=["dateUploaded", "version_status"],
                      filters=[("datasource", "=", "urn:node:DRYAD")]).to_pandas()
    v = v[v["version_status"] == "latest"]
    return v["dateUploaded"].str[:7].value_counts().sort_index().rename_axis("month").reset_index(name="records")


def biggest(table, field, n=10):
    """Return the n pairs whose DataONE date in field is furthest from Lunaris issued."""
    t = table.dropna(subset=[field + "_days"]).copy()
    t["abs_days"] = t[field + "_days"].abs()
    t = t.sort_values(["abs_days", "record_id"], ascending=[False, True]).head(n)
    return t[["record_id", "issued_lunaris", field, field + "_days"]].assign(field_name=field)


# --- Main ------------------------------------------------------------------------------

def main():
    """Run both parts, print short tables, and write the outputs."""
    os.makedirs(OUT, exist_ok=True)
    per, pairs = read_sets()
    versions = read_versions(per["record_id"])
    if len(versions) != EXPECTED_SHOWN:
        sys.exit(f"expected {EXPECTED_SHOWN} latest rows, found {len(versions)}")

    print("\n== Part 1, step 3: scanning the text fields ==")
    found = scan_records(per, versions)
    write_csv(found, "broken_by_record_field.csv")
    new = per[per["new_or_lunaris"] == "new record"]
    by_all = counts_by_datasource_field(found, per, "5071 shown")
    by_new = counts_by_datasource_field(found, new, "3525 new")
    write_csv(pd.concat([by_all, by_new]), "broken_by_datasource_field.csv")
    write_csv(pd.concat([other_field_counts(found, per, "5071 shown"), other_field_counts(found, new, "3525 new")]),
              "broken_other_fields.csv")
    print(by_all.to_string(index=False)); print(); print(by_new.to_string(index=False))
    rules = pd.concat([rule_comparison(found, per, "5071 shown"), rule_comparison(found, new, "3525 new")])
    write_csv(rules, "rule_comparison.csv")
    print("\nRule comparison:\n" + rules.to_string(index=False))
    kinds = found[found["broken"] == "yes"].groupby(["datasource", "kinds"])["record_id"].nunique().reset_index(name="records")
    print("\nKinds (records, any field):\n" + kinds.to_string(index=False))

    print("\n== Part 1, step 4: can the text be turned back? ==")
    fixes = pd.concat([fix_summary(found, per, "5071 shown"), fix_summary(found, new, "3525 new")])
    write_csv(fixes, "fix_summary.csv")
    print(fixes.to_string(index=False))
    vs_runs, vs_fields = check_against_lunaris(pairs, versions)
    write_csv(vs_runs, "fix_check_against_lunaris_runs.csv")
    write_csv(vs_fields, "fix_check_against_lunaris_fields.csv")
    s = vs_runs.groupby("field").agg(broken_runs=("record_id", "size"), records=("record_id", "nunique"),
                                     fixed_found=("fixed_found_in_lunaris", lambda x: x.str.startswith("yes").sum()),
                                     broken_found=("broken_found_in_lunaris", lambda x: (x == "yes").sum()),
                                     passage_in_lunaris=("both_sides_found_in_lunaris", lambda x: (x == "yes").sum()))
    checkable = vs_runs[vs_runs["both_sides_found_in_lunaris"] == "yes"]
    s["fixed_found_where_passage_in_lunaris"] = checkable.groupby("field")["fixed_found_in_lunaris"].apply(
        lambda x: x.str.startswith("yes").sum()).reindex(s.index).fillna(0).astype(int)
    s.loc["ALL"] = [len(vs_runs), vs_runs["record_id"].nunique(),
                    vs_runs["fixed_found_in_lunaris"].str.startswith("yes").sum(),
                    (vs_runs["broken_found_in_lunaris"] == "yes").sum(), len(checkable),
                    checkable["fixed_found_in_lunaris"].str.startswith("yes").sum()]
    f = vs_fields.groupby("field").agg(broken=("record_id", "size"), same_before=("same_before_fix", "sum"),
                                       same_after=("same_after_fix", "sum"))
    print("\nDryad pairs, each broken run: fixed text found in the Lunaris copy?\n" + s.to_string())
    print("\nDryad pairs, whole field after the fix equals Lunaris (quotes, spaces, name order set aside)\n" + f.to_string())
    write_csv(s.reset_index(), "fix_check_against_lunaris_summary.csv")
    lunaris_texts = {(r["record_id"], r["field"]): r["lunaris"] for r in vs_fields.to_dict("records")}
    for r in vs_runs.drop_duplicates(["record_id", "field"]).to_dict("records"):
        lunaris_texts.setdefault((r["record_id"], r["field"]), "fixed text found in Lunaris: " + r["fixed_found_in_lunaris"])
    examples = pick_examples(found, pairs, lunaris_texts)
    write_csv(examples, "broken_examples.csv")
    print("\nExamples:\n" + examples[["datasource", "field", "broken_run", "likely_correct", "broken_text", "fixed_text"]].to_string(index=False))

    print("\n== Part 1, steps 1 and 2: trace and the search API ==")
    ids = pick_trace_records(found, pairs)
    t, answers, page_of = trace(ids, found)
    write_csv(t, "trace.csv")
    print(t[["record_id", "step", "found", "broken", "first_broken_run", "run_as_utf8", "stored_bytes"]]
          .assign(record_id=t["record_id"].str[:15]).to_string(index=False))
    pages = pd.concat([same_page_check(p) for p in sorted(set(page_of.values()))])
    write_csv(pages, "same_page_check.csv")
    print("\nSame harvest pages, decoded in one call:\n" + pages[pages["with_non_ascii"] > 0].to_string(index=False))
    print(f"\nRequests sent by this check so far: {requests_sent()} (limit {REQUEST_LIMIT})")

    print("\n== Part 2, step 5: date fields of the new Dryad records ==")
    fill, seen, _v = date_fill(per, versions, answers)
    write_csv(fill, "dryad_new_date_fill.csv")
    write_csv(seen, "solr_full_answer_date_fields.csv")
    print(fill.to_string(index=False))
    print("\nDate-like fields in the 5 full answers (fl=*):\n" + seen.to_string(index=False))
    every_field = sorted({k for doc, *_r in answers.values() for k in doc})
    print("\nAll fields in the 5 full answers: " + ", ".join(every_field))

    print("\n== Part 2, step 6: Dryad pairs, DataONE dates against Lunaris issued ==")
    table = compare_dates(pairs, versions, answers)
    write_csv(table, "dryad_pair_dates.csv")
    buckets = date_buckets(table)
    write_csv(buckets, "dryad_date_buckets.csv")
    print(buckets.to_string(index=False))
    big = pd.concat([biggest(table, f) for f in DATE_FIELDS if table[f + "_days"].notna().any()])
    write_csv(big, "dryad_date_biggest.csv")
    print("\nBiggest differences:\n" + big.to_string(index=False))
    months = uploads_by_month()
    write_csv(months, "dryad_uploads_by_month.csv")
    print("\nMonths with more than 1,500 Dryad uploads (all latest Dryad records in the harvest):\n"
          + months[months["records"] > 1500].to_string(index=False))
    groups, new_groups = dateuploaded_by_group(table, versions, per)
    write_csv(groups, "dryad_dateuploaded_by_group.csv")
    write_csv(new_groups, "dryad_new_records_by_upload_group.csv")
    print("\ndateUploaded against Lunaris issued, by upload group:\n" + groups.to_string(index=False))
    print("\nNew Dryad records by upload group:\n" + new_groups.to_string(index=False))
    # Lunaris issued against the full answers' date fields, for the 2 traced pairs.
    extra = []
    issued = dict(zip(pairs["record_id"], pairs["issued_lunaris"]))
    for rid, (doc, *_r) in answers.items():
        if rid in issued:
            for k, val in doc.items():
                dday = day_of(val) if isinstance(val, str) else None
                if dday:
                    extra.append({"record_id": rid, "field": k, "value": val, "issued_lunaris": issued[rid],
                                  "days": (dday - day_of(issued[rid])).days})
    write_csv(pd.DataFrame(extra), "solr_full_answer_dates_vs_lunaris.csv")
    print("\nFull answers of the traced pairs, every date field against Lunaris issued:\n" + pd.DataFrame(extra).to_string(index=False))


if __name__ == "__main__":
    main()
