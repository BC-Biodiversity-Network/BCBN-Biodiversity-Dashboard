"""
compare_lunaris_copies.py (read only, 2026-11-25)

Compares the 1,080 DataONE records that filter 3 shows (bc_data or pending)
and that are copies of a Lunaris record, with the Lunaris copy the site shows
now. This is for deciding which copy the Datasets page should show. It reads
only files already on this machine, sends no request and changes no data
file. Outputs go to ~/Desktop/BCBN/Results/dataone_lunaris_copies_2026-11-25/.

The pairs (step 1)
  The 1,080 are the rows of the licence check's licence_per_record.csv
  (Results/dataone_licence_check_2026-11-25/) with new_or_lunaris
  "Lunaris copy". The script checks this set against a fresh build from the
  filter 3 final table and the match file (route lunaris or
  lunaris_either_yes, or a DOI or UUID match), and stops if they differ.
  Each record's Lunaris records are the ids in dataone_lunaris_match.parquet
  whose lunaris_match_by holds doi or uuid. Each is looked up in the site's
  Lunaris table (data/api/lunaris_datasets.parquet): either it is the shown
  copy of its copy group, or it is in a shown row's other_copies. The record
  is compared with that shown copy. shown_copy_check says which case holds:
    matches_shown_copy        one of the matched ids is the shown copy
    matches_other_copy        the matched ids are only other copies of one
                              shown dataset (it is compared with the shown one)
    several_shown_datasets    the matched ids lead to more than one shown
                              dataset; compared with the first in id order
                              that is itself matched, else the first
    not_on_site               no matched id is on the site (nothing to
                              compare)
  matched_several_lunaris is "yes" when the record matches more than one
  Lunaris record.

The comparisons (step 2), on the data exactly as harvested
  DataONE side: dataone_full_harvest_versions.parquet (latest version):
  title, abstract, keywords, origin (creators), pubDate, the box fields and
  geohash_9; for records with a saved original document, every box in
  originals_boxes.parquet; the licence class from licence_per_record.csv.
  Lunaris side: lunaris_datasets.parquet (its title, abstract, subjects,
  creators, issued, licences, boxes and points are as harvested), and the
  licences BCBN found in November 2026 (dataset_source_checks.parquet).

  title      same / same_loose (only spaces or capital letters differ) /
             different; title_similarity is difflib's ratio (0 to 1) of the
             two titles with spaces collapsed and lower case.
  abstract   same / same_spaces (only runs of spaces differ) / cut_dataone or
             cut_lunaris (that side, with spaces collapsed and a final "..."
             or "…" taken off, is the start of the other) / different /
             only_dataone / only_lunaris / neither. Also each length (in
             characters, as harvested), the longer side, whether the
             DataONE abstract ends with "..." or "…", and
             abstract_same_after_cleanup: the two are the same once HTML
             entities and tags, backslashes before quotes, spaces and capital
             letters are set aside (a check only; the result above does not
             use it). abstract_language_* is a rough guess (en, fr or ?)
             from common words.
  keywords   DataONE keywords against Lunaris subjects, as sets of exact
             strings: same / dataone_has_more / lunaris_has_more (one set
             holds the other) / different / only_dataone / only_lunaris /
             neither. keywords_after_fixes is the same test after spaces at the
             ends, capital letters and broken accents are set aside.
  location   every box and point on each side; a point is a box with no
             width or height. same: every shape on each side has a shape on
             the other within 0.01 degrees on all four edges;
             different_some_shared; different; only_dataone; only_lunaris;
             neither. DataONE's search index keeps only the first box, so a
             record with more than one geohash_9 value and no saved original
             may be missing boxes (dataone_boxes_incomplete).
  licence    the DataONE class (licence check) against the Lunaris licence
             as harvested, put in the same classes by the licence check's own
             rules (map_value and decide in check_licences.py) on the text the
             Datasets page shows for each entry (api/dataset_licences.py
             licence_text, which joins a link split across name and URI), after
             leaving out entries the page leaves out (an access right
             info:eu-repo/semantics/..., or the name "None" with no URI).
             none and placeholder count as missing. same / different /
             only_dataone / only_lunaris / neither. licence_result_with_checked
             does the same with the licence the page shows now (the record's
             own, else the one BCBN checked).
  creators   DataONE origin against Lunaris creators: same (same list, same
             order) / same_loose (same names once order, spaces, capital
             letters, punctuation and "Last, First" order are set aside) /
             different / only_dataone / only_lunaris / neither.
  pub_date   DataONE pubDate (its date part) against Lunaris issued: same /
             same_year / different / only_dataone / only_lunaris / neither.
  source     the DataONE datasource and the Lunaris catalogue (not compared).

  Checks on the differences (they do not change the results above)
  dataone_broken_accents, lunaris_broken_accents
             "yes" when the side's title, abstract or creators hold UTF-8
             text that was read as Windows-1252 somewhere on the way
             ("ExnerovÃ¡" for "Exnerová"); fix_accents undoes it run by run.
  title_difference, abstract_difference, creators_difference
             for a pair whose result is different (title: also same_loose;
             creators: also same_loose): which of these, applied to both
             sides, makes it the same (or, for the abstract, a cut), in the
             fewest steps: taking off a leading "Data from: ", fixing broken
             accents, making curly quotes straight (’ ‘ “ ” to ' and "), and
             the cleanup (HTML entities and tags, backslashes before quotes,
             every space taken out, capital letters); "other" when none of
             them does. A step that changes neither side is not counted.
  abstract_after_fixes
             the abstract result again after all four fixes on both sides,
             plus cut_dataone_then_more (see after_fixes). Note: cut_lunaris
             only says the Lunaris abstract is the start of the DataONE one;
             it may be that DataONE adds more parts, not that Lunaris is cut.

  all_same_strict: every field's result is "same" (title, abstract,
  keywords, location, licence, creators, pub_date). all_same_or_both_missing
  also counts "neither" as same. all_same_loose also counts same_loose and
  same_spaces.

Run, from backend/:
    ../.venv/bin/python exploration/dataone/compare_lunaris_copies.py
"""

import html
import itertools
import json
import os
import re
import sys
from difflib import SequenceMatcher
from urllib.parse import quote

import pandas as pd
import pyarrow.parquet as pq

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import check_licences as lic  # noqa: E402  (the licence check's own value and record rules)
sys.path.insert(0, lic.BACKEND)
from api.dataset_licences import licence_text  # noqa: E402  (the text the Datasets page shows for a licence)

BACKEND = lic.BACKEND
HARVEST = lic.HARVEST
VERSIONS = lic.VERSIONS
MATCH = lic.MATCH
FINAL = lic.FINAL
BOXES = os.path.join(HARVEST, "originals_boxes.parquet")
LUNARIS = os.path.join(BACKEND, "data", "api", "lunaris_datasets.parquet")
CHECKED = os.path.join(BACKEND, "data", "api", "dataset_source_checks.parquet")
RESULTS = lic.RESULTS
PER_RECORD = os.path.join(RESULTS, "dataone_licence_check_2026-11-25", "licence_per_record.csv")
OUT = os.path.join(RESULTS, "dataone_lunaris_copies_2026-11-25")
VIEW = lic.VIEW

EXPECTED_PAIRS = 1_080
PLAN_SHOWN_COPY = 1_076
TOLERANCE = 0.01
SEED = 20261125
MISSING_CLASSES = {"none", "placeholder"}
FIELDS = ["title", "abstract", "keywords", "location", "licence", "creators", "pub_date"]
# The other per-pair columns the summary counts (filled with "not_on_site" for a pair with no Lunaris side).
EXTRA_ITEMS = ["keywords_after_fixes", "title_difference", "abstract_difference", "abstract_after_fixes", "abstract_longer",
               "dataone_abstract_ends_with_dots", "abstract_same_after_cleanup", "creators_difference",
               "dataone_broken_accents", "lunaris_broken_accents", "dataone_boxes_incomplete",
               "licence_result_with_checked"]
ACCESS_RIGHTS_PREFIX = "info:eu-repo/semantics/"

# Common short words, for the rough language guess.
EN_WORDS = {"the", "and", "of", "to", "in", "is", "for", "with", "this", "are", "on", "from", "data", "that"}
FR_WORDS = {"le", "la", "les", "des", "et", "du", "de", "pour", "dans", "est", "une", "sur", "données", "par"}

pd.set_option("display.width", 250)
pd.set_option("display.max_colwidth", 100)
pd.set_option("display.max_rows", 300)


# --- Small helpers ---------------------------------------------------------------

def as_list(value):
    """Return a field as a list of non-empty strings, exactly as harvested."""
    return lic.as_list(value)


def text_of(value):
    """Return a text field as a string ('' when missing), exactly as harvested."""
    if value is None or (isinstance(value, float) and value != value):
        return ""
    return str(value)


def spaces(text):
    """Return text with runs of white space made one space, and trimmed."""
    return re.sub(r"\s+", " ", text).strip()


def loose(text):
    """Return text with spaces collapsed and in lower case."""
    return spaces(text).casefold()


def cleaned(text):
    """Return text with HTML entities and tags undone, backslashes before quotes dropped, every space taken out, lower case."""
    for _ in range(8):
        undone = html.unescape(text)
        if undone == text:
            break
        text = undone
    text = re.sub(r"</?[a-zA-Z][^>]*>", " ", text)
    text = re.sub(r"\\+([\"'])", r"\1", text)
    return re.sub(r"\s+", "", text).casefold()


def straight_quotes(text):
    """Return text with curly quotes made straight (’ ‘ to ' and “ ” to ")."""
    return text.translate(str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"'}))


# A run of characters that looks like UTF-8 bytes shown as Windows-1252 text.
MOJIBAKE_RUN = re.compile("[\u00c2-\u00f4][\u0080-\u00bf\u0152\u0153\u0160\u0161\u0178\u017d\u017e"
                          "\u0192\u02c6\u02dc\u2013\u2014\u2018-\u201e\u2020-\u2022\u2026\u2030\u2039"
                          "\u203a\u20ac\u2122]+")


def fix_accents(text):
    """Return text with each run of UTF-8 read as Windows-1252 decoded again; runs that do not decode are kept."""
    def fix(match):
        """Return one run decoded again, or the run itself when it is not broken UTF-8."""
        run = match.group(0)
        try:
            return run.encode("cp1252").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            try:
                return run.encode("latin-1").decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                return run
    return MOJIBAKE_RUN.sub(fix, text)


def has_broken_accents(*texts):
    """Return True if fixing broken accents changes any of the texts."""
    return any(fix_accents(t) != t for t in texts)


def strip_data_from(text):
    """Return text without a leading 'Data from: ' (Dryad's old title prefix)."""
    return re.sub(r"^\s*data from:\s*", "", text, flags=re.I)


def difference_kind(d, l, same):
    """
    Return which fixes, applied to both sides, make same(d, l) true:
    'Data from: prefix', 'broken accents', 'curly quotes', 'cleanup' (HTML,
    backslashes, spaces, capital letters), joined with ' + ', in the fewest
    steps, each changing at least one side; else 'other'.
    """
    steps = [("Data from: prefix", strip_data_from), ("broken accents", fix_accents),
             ("curly quotes", straight_quotes), ("cleanup", cleaned)]
    for size in (1, 2, 3, 4):
        for chosen in itertools.combinations(steps, size):
            a, b, useful = d, l, True
            for _name, step in chosen:
                a2, b2 = step(a), step(b)
                useful = useful and (a2, b2) != (a, b)
                a, b = a2, b2
            if useful and same(a, b):
                return " + ".join(name for name, _step in chosen)
    return "other"


def all_fixes(text):
    """Return text after all four fixes: 'Data from: ' taken off, accents fixed, quotes made straight, cleanup."""
    return cleaned(straight_quotes(fix_accents(strip_data_from(text))))


def after_fixes(d, l):
    """
    Return the abstract result after all four fixes on both sides, with one
    more result: cut_dataone_then_more when the DataONE abstract, up to its
    first '...' or '…' (at least 100 characters in), is the start of the
    Lunaris one, and more text follows the '...' (Dryad joins other parts).
    """
    a, b = all_fixes(d), all_fixes(l)
    result = compare_abstract(a, b)
    if result != "different":
        return result
    cut = re.search(r"\.\.\.|…", a)
    if cut and cut.start() >= 100 and b.startswith(a[:cut.start()]):
        return "cut_dataone_then_more"
    return result


def without_dots(text):
    """Return text with a final '...' or '…' (and the spaces before it) taken off."""
    return re.sub(r"\s*(\.\.\.|…)$", "", text)


def language(text):
    """Return a rough language guess for a text: 'en', 'fr' or '?' (from common short words)."""
    words = re.findall(r"[a-zàâçéèêëîïôûùüÿœ]+", text.casefold())
    en = sum(w in EN_WORDS for w in words)
    fr = sum(w in FR_WORDS for w in words)
    if fr >= 3 and fr > en:
        return "fr"
    if en >= 3:
        return "en"
    return "?"


def presence(has_d, has_l):
    """Return the result when at least one side is missing, or None when both sides have a value."""
    if has_d and has_l:
        return None
    if has_d:
        return "only_dataone"
    if has_l:
        return "only_lunaris"
    return "neither"


def dumps(value):
    """Return a value as JSON text for a CSV cell ('' for an empty list)."""
    return json.dumps(value, ensure_ascii=False) if value else ""


# --- Step 1: the pairs ------------------------------------------------------------------

def read_pairs():
    """
    Return the 1,080 DataONE records with the Lunaris records they match and
    the shown copy each is compared with, after checking the set against a
    fresh build from the filter 3 final table and the match file.
    """
    per = pd.read_csv(PER_RECORD, dtype=str, keep_default_na=False)
    copies = per[per["new_or_lunaris"] == "Lunaris copy"].copy()
    if len(copies) != EXPECTED_PAIRS:
        sys.exit(f"expected {EXPECTED_PAIRS} Lunaris copies in licence_per_record.csv, found {len(copies)}")

    # The same set, built again from the filter 3 final table and the match file.
    final = pd.read_parquet(FINAL, columns=["record_id", "route", "frontend_group"])
    shown = final[final["frontend_group"].isin(lic.SHOWN)]
    match = pd.read_parquet(MATCH, columns=["id", "lunaris_ids", "lunaris_match_by"]).set_index("id")
    strong = {}
    for rid in shown["record_id"]:
        if rid in match.index:
            ids = [i for i, w in zip(match.at[rid, "lunaris_ids"], match.at[rid, "lunaris_match_by"])
                   if set(w.split("+")) & {"doi", "uuid"}]
            ways = [w for w in match.at[rid, "lunaris_match_by"] if set(w.split("+")) & {"doi", "uuid"}]
            if ids:
                strong[rid] = (ids, ways)
    fresh = set(shown.loc[shown["route"].isin(["lunaris", "lunaris_either_yes"]), "record_id"]) | set(strong)
    if fresh != set(copies["record_id"]):
        sys.exit(f"the fresh build gives {len(fresh)} records; differs from licence_per_record.csv in "
                 f"{len(fresh ^ set(copies['record_id']))}")
    print(f"1. Lunaris copies: {len(copies):,} from licence_per_record.csv; the fresh build from the filter 3 "
          f"table and the match file gives the same {len(fresh):,} records")

    # Where each Lunaris id sits on the site: the shown copy of which dataset.
    lunaris = pd.read_parquet(LUNARIS)
    shown_of = {}
    for sid, others in zip(lunaris["id"], lunaris["other_copies"]):
        shown_of[sid] = sid
        for other in others:
            shown_of.setdefault(other["id"], sid)
    shown_ids = set(lunaris["id"])

    rows = []
    for r in copies.to_dict("records"):
        ids, ways = strong.get(r["record_id"], ([], []))
        targets = sorted({shown_of[i] for i in ids if i in shown_of})
        matched_shown = [t for t in targets if t in ids]
        if not targets:
            check, target = "not_on_site", ""
        elif len(targets) > 1:
            check, target = "several_shown_datasets", (matched_shown or targets)[0]
        elif targets[0] in ids:
            check, target = "matches_shown_copy", targets[0]
        else:
            check, target = "matches_other_copy", targets[0]
        rows.append({"record_id": r["record_id"], "matched_lunaris_ids": dumps(ids), "match_by": dumps(ways),
                     "matched_several_lunaris": "yes" if len(ids) > 1 else "no",
                     "shown_copy_check": check, "lunaris_id": target,
                     "shown_datasets_reached": len(targets)})
    pairs = pd.DataFrame(rows)
    counts = pairs["shown_copy_check"].value_counts().to_dict()
    n_shown = int((pairs["shown_copy_check"] == "matches_shown_copy").sum()
                  + ((pairs["shown_copy_check"] == "several_shown_datasets")
                     & pairs.apply(lambda p: p["lunaris_id"] in json.loads(p["matched_lunaris_ids"] or "[]"), axis=1)).sum())
    print(f"   shown copy check: {counts}; a matched id is the shown copy for {n_shown:,} "
          f"(the export plan said {PLAN_SHOWN_COPY:,})")
    print(f"   records matching more than one Lunaris record: {int((pairs['matched_several_lunaris'] == 'yes').sum())}")
    return copies, pairs, lunaris, shown_ids


# --- Reading each side -------------------------------------------------------------------

def read_dataone(ids):
    """Return the DataONE fields of the records (latest version), indexed by id, with their boxes."""
    columns = ["id", "datasource", "title", "abstract", "keywords", "origin", "pubDate", "northBoundCoord",
               "southBoundCoord", "eastBoundCoord", "westBoundCoord", "geohash_9", "version_status"]
    v = pq.read_table(VERSIONS, columns=columns, filters=[("id", "in", list(ids))]).to_pandas()
    v = v[v["version_status"] == "latest"].drop_duplicates("id").set_index("id")
    boxes = pq.read_table(BOXES, columns=["id", "box", "north", "south", "east", "west"],
                          filters=[("id", "in", list(ids))]).to_pandas()
    from_original = {}
    for rid, part in boxes.sort_values(["id", "box"]).groupby("id"):
        part = part.dropna(subset=["north", "south", "east", "west"])
        from_original[rid] = [(float(n), float(s), float(e), float(w))
                              for n, s, e, w in zip(part["north"], part["south"], part["east"], part["west"])]
    shapes, source = {}, {}
    for rid, r in v.iterrows():
        if rid in from_original:
            shapes[rid], source[rid] = from_original[rid], "original"
            continue
        edges = [r["northBoundCoord"], r["southBoundCoord"], r["eastBoundCoord"], r["westBoundCoord"]]
        if all(e is not None and e == e for e in edges):
            shapes[rid], source[rid] = [tuple(float(e) for e in edges)], "index"
        else:
            shapes[rid], source[rid] = [], ""
    v["shapes"] = pd.Series(shapes)
    v["box_from"] = pd.Series(source)
    v["geohash_count"] = v["geohash_9"].map(lambda g: len(as_list(g)))
    return v


def lunaris_shapes(boxes, points):
    """Return a Lunaris record's boxes and points as (north, south, east, west) shapes."""
    shapes = [(float(b["n"]), float(b["s"]), float(b["e"]), float(b["w"])) for b in (boxes if boxes is not None else [])]
    shapes += [(float(p["lat"]), float(p["lat"]), float(p["lon"]), float(p["lon"]))
               for p in (points if points is not None else [])]
    return shapes


def licence_entries(entries):
    """Return a Lunaris record's licence entries as the page counts them (access rights and the 'None' name left out)."""
    kept = []
    for e in (entries if entries is not None else []):
        name, uri = e.get("name"), e.get("uri")
        if not name and uri and uri.startswith(ACCESS_RIGHTS_PREFIX):
            continue
        if name == "None" and not uri:
            continue
        kept.append({"name": name, "uri": uri})
    return kept


def access_rights(entries):
    """Return a Lunaris record's access rights (the info:eu-repo URIs), as their last part."""
    return [e["uri"][len(ACCESS_RIGHTS_PREFIX):] for e in (entries if entries is not None else [])
            if not e.get("name") and (e.get("uri") or "").startswith(ACCESS_RIGHTS_PREFIX)]


def licence_class(entries):
    """
    Return (class, rule) for Lunaris licence entries: each entry's text as the
    Datasets page shows it (licence_text: HTML read, a link split across name
    and URI joined again), put in a class by the licence check's value rules
    (V1-V11) and record rules (R1-R6).
    """
    vals = [("lunaris_licence", licence_text(e)) for e in entries]
    vals = [(f, v) for f, v in vals if v and v.strip()]
    value_map = {pair: lic.map_value(pair[1])[0] for pair in vals}
    cls, rule, _flag, _conflict, _seen = lic.decide(vals, value_map)
    return cls, rule


# --- Step 2: the comparisons ---------------------------------------------------------------

def compare_title(d, l):
    """Return (result, similarity) for two titles."""
    if d == l and d:
        result = "same"
    else:
        result = presence(bool(d.strip()), bool(l.strip())) or ("same_loose" if loose(d) == loose(l) else "different")
    sim = round(SequenceMatcher(None, loose(d), loose(l)).ratio(), 3) if d.strip() and l.strip() else ""
    return result, sim


def compare_abstract(d, l):
    """Return the abstract result for two abstracts (see the top of the script)."""
    missing = presence(bool(d.strip()), bool(l.strip()))
    if missing:
        return missing
    if d == l:
        return "same"
    sd, sl = spaces(d), spaces(l)
    if sd == sl:
        return "same_spaces"
    cd, cl = without_dots(sd), without_dots(sl)
    if len(cd) < len(sl) and sl.startswith(cd):
        return "cut_dataone"
    if len(cl) < len(sd) and sd.startswith(cl):
        return "cut_lunaris"
    return "different"


def compare_keywords(d, l):
    """Return the keywords result for two keyword lists, as sets of exact strings."""
    sd, sl = set(d), set(l)
    missing = presence(bool(sd), bool(sl))
    if missing:
        return missing
    if sd == sl:
        return "same"
    if sl < sd:
        return "dataone_has_more"
    if sd < sl:
        return "lunaris_has_more"
    return "different"


def near(a, b):
    """Return True if two shapes are within TOLERANCE degrees on all four edges."""
    return all(abs(x - y) <= TOLERANCE for x, y in zip(a, b))


def compare_location(d, l):
    """Return the location result for two lists of shapes."""
    missing = presence(bool(d), bool(l))
    if missing:
        return missing
    d_found = [any(near(a, b) for b in l) for a in d]
    l_found = [any(near(b, a) for a in d) for b in l]
    if all(d_found) and all(l_found):
        return "same"
    if any(d_found):
        return "different_some_shared"
    return "different"


def compare_licence(d_class, l_class):
    """Return the licence result for the DataONE class and a Lunaris class."""
    has_d, has_l = d_class not in MISSING_CLASSES, l_class not in MISSING_CLASSES
    missing = presence(has_d, has_l)
    if missing:
        return missing
    return "same" if d_class == l_class else "different"


def name_key(name):
    """Return a creator name as its sorted lower-case words, so 'Smith, John' and 'John Smith' agree."""
    return " ".join(sorted(re.findall(r"[^\W\d_]+", name.casefold())))


def compare_creators(d, l):
    """Return the creators result for two lists of creator names."""
    missing = presence(bool(d), bool(l))
    if missing:
        return missing
    if list(d) == list(l):
        return "same"
    if sorted(name_key(n) for n in d) == sorted(name_key(n) for n in l):
        return "same_loose"
    return "different"


def compare_dates(d, l):
    """Return the publication date result for DataONE pubDate and Lunaris issued (their date parts)."""
    dd, ll = d[:10], l[:10]
    missing = presence(bool(dd.strip()), bool(ll.strip()))
    if missing:
        return missing
    if dd == ll:
        return "same"
    return "same_year" if dd[:4] == ll[:4] else "different"


def compare_all(copies, pairs, lunaris):
    """Return copy_pairs: one row per pair, with both sides' values and each field's result."""
    dataone = read_dataone(set(pairs["record_id"]))
    lun = lunaris.set_index("id")
    checked = pd.read_parquet(CHECKED, columns=["dataset_id", "licences"]).set_index("dataset_id")["licences"]
    per = copies.set_index("record_id")
    rows = []
    for p in pairs.to_dict("records"):
        rid, lid = p["record_id"], p["lunaris_id"]
        d = dataone.loc[rid]
        row = dict(p)
        row["datasource"] = per.at[rid, "datasource"]
        row["frontend_group"] = per.at[rid, "frontend_group"]
        if not lid:
            row.update({c: "not_on_site" for c in [f"{f}_result" for f in FIELDS] + EXTRA_ITEMS})
            rows.append(row)
            continue
        l = lun.loc[lid]
        row["lunaris_source"], row["lunaris_source_name"] = l["source"], l["source_name"]

        # Title.
        td, tl = text_of(d["title"]), text_of(l["title"])
        row["title_dataone"], row["title_lunaris"] = td, tl
        row["title_result"], row["title_similarity"] = compare_title(td, tl)
        row["title_difference"] = "" if row["title_result"] in ("same", "only_dataone", "only_lunaris", "neither") \
            else difference_kind(td, tl, lambda a, b: a == b)

        # Abstract.
        ad, al = text_of(d["abstract"]), text_of(l["abstract"])
        row["abstract_result"] = compare_abstract(ad, al)
        row["abstract_length_dataone"], row["abstract_length_lunaris"] = len(ad), len(al)
        row["abstract_longer"] = "dataone" if len(ad) > len(al) else "lunaris" if len(al) > len(ad) else "equal"
        row["dataone_abstract_ends_with_dots"] = "yes" if re.search(r"(\.\.\.|…)\s*$", ad) else "no"
        row["abstract_same_after_cleanup"] = "yes" if ad.strip() and al.strip() and cleaned(ad) == cleaned(al) else "no"
        row["abstract_language_dataone"], row["abstract_language_lunaris"] = language(ad), language(al)
        row["abstract_difference"] = "" if row["abstract_result"] != "different" else \
            difference_kind(ad, al, lambda a, b: compare_abstract(a, b) in ("same", "same_spaces", "cut_dataone",
                                                                             "cut_lunaris"))
        row["abstract_after_fixes"] = after_fixes(ad, al)

        # Keywords.
        kd, kl = as_list(d["keywords"]), as_list(l["subjects"])
        row["keywords_dataone"], row["keywords_lunaris"] = dumps(kd), dumps(kl)
        row["keywords_result"] = compare_keywords(kd, kl)
        row["keywords_shared_ignoring_case"] = len({k.casefold() for k in kd} & {k.casefold() for k in kl})
        row["keywords_after_fixes"] = compare_keywords([loose(fix_accents(k)) for k in kd],
                                                       [loose(fix_accents(k)) for k in kl])

        # Location.
        sd, sl = d["shapes"], lunaris_shapes(l["boxes"], l["points"])
        row["shapes_dataone"], row["shapes_lunaris"] = dumps([list(s) for s in sd]), dumps([list(s) for s in sl])
        row["dataone_box_from"], row["dataone_geohash_count"] = d["box_from"], int(d["geohash_count"])
        row["dataone_boxes_incomplete"] = "yes" if d["box_from"] != "original" and d["geohash_count"] > 1 else "no"
        row["location_result"] = compare_location(sd, sl)

        # Licence.
        own = licence_entries(l["licences"])
        own_class, own_rule = licence_class(own)
        chk = licence_entries(checked.get(lid)) if lid in checked.index else []
        chk_class = licence_class(chk)[0] if chk else "none"
        page_class = own_class if own else chk_class
        row["licence_dataone_class"] = per.at[rid, "mapped_class"]
        row["licence_dataone_access_restricted"] = per.at[rid, "access_restricted"]
        for f in ("licenseName", "licenseDescription", "licenseUrl"):
            row[f"dataone_{f}"] = per.at[rid, f]
        row["licences_lunaris"] = dumps(own)
        row["licence_lunaris_class"], row["licence_lunaris_rule"] = own_class, own_rule
        row["lunaris_access_rights"] = " | ".join(access_rights(l["licences"]))
        row["licences_lunaris_checked"] = dumps(chk)
        row["licence_lunaris_checked_class"] = chk_class if chk else ""
        row["licence_lunaris_page_class"] = page_class
        row["licence_result"] = compare_licence(row["licence_dataone_class"], own_class)
        row["licence_result_with_checked"] = compare_licence(row["licence_dataone_class"], page_class)

        # Creators and publication date.
        cd, cl = as_list(d["origin"]), as_list(l["creators"])
        row["creators_dataone"], row["creators_lunaris"] = dumps(cd), dumps(cl)
        row["creators_result"] = compare_creators(cd, cl)
        row["creators_difference"] = "" if row["creators_result"] in ("same", "only_dataone", "only_lunaris", "neither") \
            else ("order or name form" if row["creators_result"] == "same_loose" else
                  "broken accents" if compare_creators([fix_accents(n) for n in cd], cl) in ("same", "same_loose")
                  else "other")
        row["dataone_broken_accents"] = "yes" if has_broken_accents(td, ad, *cd) else "no"
        row["lunaris_broken_accents"] = "yes" if has_broken_accents(tl, al, *cl) else "no"
        pd_, il = text_of(d["pubDate"]), text_of(l["issued"])
        row["pub_date_dataone"], row["issued_lunaris"] = pd_, il
        row["pub_date_result"] = compare_dates(pd_, il)

        row["abstract_dataone"], row["abstract_lunaris"] = ad, al
        row["dataone_link"] = VIEW + quote(rid, safe="")
        row["lunaris_link"] = l["link"]
        rows.append(row)
    out = pd.DataFrame(rows)
    results = out[[f"{f}_result" for f in FIELDS]]
    out["all_same_strict"] = (results == "same").all(axis=1).map({True: "yes", False: "no"})
    out["all_same_or_both_missing"] = results.isin(["same", "neither"]).all(axis=1).map({True: "yes", False: "no"})
    out["all_same_loose"] = results.isin(["same", "neither", "same_loose", "same_spaces"]).all(axis=1).map(
        {True: "yes", False: "no"})
    return out


# --- Step 3: summary ----------------------------------------------------------------------------

def datasource_group(pairs):
    """Return each pair's DataONE datasource, or 'rest' outside the 5 biggest."""
    top = pairs["datasource"].value_counts().head(5).index
    return pairs["datasource"].where(pairs["datasource"].isin(top), "rest")


def summary_rows(pairs, column, columns):
    """Return (field, result, column, records, % of column) rows for each field, and the all-same counts."""
    rows = []
    items = [f"{f}_result" for f in FIELDS] + EXTRA_ITEMS + ["shown_copy_check", "matched_several_lunaris",
                                                              "all_same_strict", "all_same_or_both_missing",
                                                              "all_same_loose"]
    for col in columns:
        part = pairs if col == "all" else pairs[column == col]
        for item in items:
            for result, n in part[item].value_counts().items():
                rows.append({"field": item.replace("_result", ""), "result": result, "column": col,
                             "records": int(n), "pct_of_column": round(100 * n / len(part), 1)})
            rows.append({"field": item.replace("_result", ""), "result": "TOTAL", "column": col,
                         "records": len(part), "pct_of_column": 100.0})
    return rows


def step3_summary(pairs):
    """Write copy_summary.csv (all 1,080, and by DataONE datasource) and check that every table adds up."""
    groups = datasource_group(pairs)
    columns = ["all"] + list(groups.value_counts().index)
    summary = pd.DataFrame(summary_rows(pairs, groups, columns))
    summary.to_csv(os.path.join(OUT, "copy_summary.csv"), index=False, encoding="utf-8-sig")
    problems = []
    for (field, col), part in summary.groupby(["field", "column"]):
        body = int(part.loc[part["result"] != "TOTAL", "records"].sum())
        total = int(part.loc[part["result"] == "TOTAL", "records"].iloc[0])
        if body != total:
            problems.append((field, col, body, total))
    for field, part in summary[summary["result"] != "TOTAL"].groupby("field"):
        by_col = part[part["column"] != "all"]["records"].sum()
        if by_col != len(pairs):
            problems.append((field, "by datasource", int(by_col), len(pairs)))
    print(f"\n3. summary tables add up: {not problems} {problems if problems else ''}")
    wide = summary[summary["result"] != "TOTAL"].pivot_table(index=["field", "result"], columns="column",
                                                             values="records", fill_value=0, sort=False)
    print(wide[columns].to_string())
    return summary


# --- Step 4: examples -------------------------------------------------------------------------------

def step4_examples(pairs):
    """Write copy_examples.csv: 10 pairs showing different kinds of difference."""
    kinds = [
        ("broken accents on DataONE", (pairs["dataone_broken_accents"] == "yes")
         & (pairs["title_difference"].str.contains("broken accents"))),
        ("Lunaris title has 'Data from:'", pairs["title_difference"] == "Data from: prefix"),
        ("different language", (pairs["abstract_language_dataone"] != pairs["abstract_language_lunaris"])
         & pairs["abstract_language_dataone"].isin(["en", "fr"]) & pairs["abstract_language_lunaris"].isin(["en", "fr"])),
        ("DataONE abstract cut", pairs["abstract_result"] == "cut_dataone"),
        ("Lunaris abstract shorter (not a cut)", (pairs["abstract_result"] == "different")
         & (pairs["abstract_longer"] == "dataone")),
        ("DataONE abstract longer only from escapes or HTML", (pairs["abstract_result"] == "different")
         & (pairs["abstract_same_after_cleanup"] == "yes")),
        ("licence only on DataONE", pairs["licence_result"] == "only_dataone"),
        ("licence only on Lunaris", pairs["licence_result"] == "only_lunaris"),
        ("different licence", pairs["licence_result"] == "different"),
        ("box only on Lunaris", pairs["location_result"] == "only_lunaris"),
        ("box only on DataONE", pairs["location_result"] == "only_dataone"),
        ("boxes differ", pairs["location_result"].isin(["different", "different_some_shared"])),
        ("title different", pairs["title_result"] == "different"),
        ("compared with another copy than the one matched", pairs["shown_copy_check"] != "matches_shown_copy"),
    ]
    rows, used = [], set()
    for kind, mask in kinds:
        pool = pairs[mask & ~pairs["record_id"].isin(used)]
        if pool.empty:
            continue
        pick = pool.sample(n=1, random_state=SEED).iloc[0]
        used.add(pick["record_id"])
        rows.append({"kind": kind, "pairs_of_this_kind": int(mask.sum()), **{c: pick.get(c, "") for c in [
            "record_id", "lunaris_id", "datasource", "lunaris_source", "shown_copy_check", "title_dataone",
            "title_lunaris", "title_result", "abstract_result", "abstract_length_dataone",
            "abstract_length_lunaris", "abstract_language_dataone", "abstract_language_lunaris",
            "licence_dataone_class", "licence_lunaris_class", "licence_lunaris_page_class", "location_result",
            "shapes_dataone", "shapes_lunaris", "creators_result", "pub_date_result", "dataone_link",
            "lunaris_link"]}})
        if len(rows) == 10:
            break
    out = pd.DataFrame(rows)
    out.to_csv(os.path.join(OUT, "copy_examples.csv"), index=False, encoding="utf-8-sig")
    print(f"\n4. examples: {len(out)}")
    print(out[["kind", "pairs_of_this_kind", "record_id", "lunaris_id"]].to_string(index=False))
    return out


def main():
    """Find the pairs, compare them, and write the pairs, the summary and the examples."""
    os.makedirs(OUT, exist_ok=True)
    copies, pairs, lunaris, _shown_ids = read_pairs()
    others = pairs[pairs["shown_copy_check"] != "matches_shown_copy"]
    if len(others):
        print("   not matching the shown copy directly:")
        print(others[["record_id", "shown_copy_check", "matched_lunaris_ids", "lunaris_id"]].to_string(index=False))
    out = compare_all(copies, pairs, lunaris)
    front = ["record_id", "datasource", "frontend_group", "lunaris_id", "lunaris_source", "lunaris_source_name",
             "matched_lunaris_ids", "match_by", "matched_several_lunaris", "shown_datasets_reached",
             "shown_copy_check"] + [f"{f}_result" for f in FIELDS] + ["all_same_strict", "all_same_or_both_missing",
                                                                       "all_same_loose"]
    out = out[front + [c for c in out.columns if c not in front]]
    out.to_csv(os.path.join(OUT, "copy_pairs.csv"), index=False, encoding="utf-8-sig")
    print(f"\n2. pairs written: {len(out):,}")
    step3_summary(out)
    step4_examples(out)
    print(f"\nwritten to {OUT}")


if __name__ == "__main__":
    main()
