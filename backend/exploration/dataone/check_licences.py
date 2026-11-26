"""
check_licences.py (read only, 2026-11-25)

A licence check of the DataONE records that filter 3 shows on the site
(frontend_group bc_data or pending in data/dataone/filter3/filter3_final.parquet),
for deciding how the site shows each record's licence. It reads only files
already on this machine and sends no request. It changes no data file.
Outputs go to ~/Desktop/BCBN/Results/dataone_licence_check_2026-11-25/.

Where licence-like fields come from (step 1)
  index     every field of the harvest (dataone_full_harvest_versions.parquet,
            latest version), i.e. what DataONE's search index gave us.
  probe     the search index answers saved by probe_dataone_3.py in
            Results/dataone_probe_2026-11/cache/; some of those asked for
            rightsHolder and isPublic, which the harvest did not.
  original  the EML / ISO documents saved by fetch_multibox_documents.py
            (harvest originals/; only records with more than one box).
  dryad     the 5 schema.org JSON-LD documents saved by
            try_dryad_full_abstracts.py.
  A field is licence-like when its name contains licen, rights, access, use,
  constraint or copyright (any case). For the original documents the "field"
  is the element path (or path/@attribute). isPublic is added by hand: its
  name does not match, but it says whether the record is public.

Licence classes (step 2)
  CC0, CC BY 3.0, CC BY 4.0, CC BY-SA 4.0, CC BY-NC 4.0, CC BY-NC-SA 3.0,
  CC BY-NC-SA 4.0, CC BY-NC-ND 4.0, public domain (not CC0), custom terms,
  access restricted, placeholder, none. A licence outside this list that a
  value clearly names (another CC version or variant, or the Open Government
  Licence - Canada, an Open Data Commons licence) gets its own class with its exact name and version; the
  version is never dropped and 3.0 and 4.0 are never merged. "unsure" marks a
  value no fixed rule can map; it is left for a hand decision.

How one raw value is mapped (step 2); the first rule that holds wins
  V1 placeholder     the whole value, lower case with only letters and digits
                     kept, is in PLACEHOLDERS ("copyright", "otherRestrictions",
                     "restricted", "other", ...).
  V2 licence names   every licence the value names is collected: Creative
                     Commons links (creativecommons.org/licenses/<code>/<v>,
                     /publicdomain/zero/), SPDX ids and links, CC codes
                     (CC BY-NC 4.0, CC-BY-4.0, [CCBY-NC-SA3.0]), CC words with
                     a version (Creative Commons Attribution-NonCommercial
                     4.0), CC0 words (CC0, Creative Commons Zero, ...), public
                     domain words (only when no CC0 is named), the Open
                     Government Licence - Canada, and Open Data Commons links.
  V3 unsure          the value says its terms are "similar to" a licence.
  V4 unsure          the value names two or more different licences.
  V5 that licence    the value names exactly one licence.
  V6 unsure          the value names a CC licence but no version, or has
                     CC0's waiver words ("waived all rights") but no licence
                     name (the value looks cut).
  V7 access restr.   the value says the data are not public or must be
                     requested (RESTRICTED below).
  V8 placeholder     a short value that only says "public" or "no
                     restrictions" and names no licence.
  V9 custom terms    a link that names no licence (the repository's own terms).
  V10 custom terms   text with terms of use (cite, credit, permission,
                     commercial, warranty, liability, ...).
  V11 unsure         nothing above holds.

Rules for one licence per record (step 3), in this order
  R1 A real licence (a CC class, public domain, or another named licence)
     beats a placeholder; placeholders are set aside whenever anything else
     is there.
  R2 If the record's values name two different real licences (in two fields,
     or two values of one field), the record is "conflict" and no licence is
     picked.
  R3 A licenseDescription value that says the data are not public or must be
     requested (the V7 test) sets access_restricted = yes, whatever the
     licence says. Other fields never set it.
  Added for the cases the three rules leave open:
  R4 If any value is "unsure", the record is "unsure" (hand decision).
  R5 One real licence: that licence (it also beats custom terms and access
     restricted text; those are kept in classes_seen).
  R6 Else access restricted, else custom terms, else placeholder (only
     placeholders), else none (no licence value at all).

new_or_lunaris (as in the export plan of 2026-11-24)
  Lunaris copy     route lunaris or lunaris_either_yes, or a DOI or UUID match
                   in dataone_lunaris_match.parquet.
  new record       not a Lunaris copy, and the first copy of its same-DOI
                   group (groups over all filter 3 records, linked through any
                   DOI in the id or seriesId).
  DOI second copy  another new record of the same DOI group. The plan has not
                   yet decided which copy is shown; for this check the first
                   copy is the one in bc_data, then the newest dateUploaded,
                   then the smallest record_id.

Raw values are written exactly as harvested. A field holding one value is
written as that value; a field holding more than one is written as a JSON list.

Run, from backend/:
    ../.venv/bin/python exploration/dataone/check_licences.py
"""

import glob
import gzip
import html
import json
import os
import re
import sys
from collections import Counter, defaultdict
from urllib.parse import quote

import pandas as pd
import pyarrow.parquet as pq
from lxml import etree

BACKEND = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(BACKEND, "pipeline", "dataone"))
from match_lunaris import dataone_dois  # noqa: E402

HARVEST = os.path.join(BACKEND, "data", "dataone", "harvest_2026_11_18")
VERSIONS = os.path.join(HARVEST, "dataone_full_harvest_versions.parquet")
MATCH = os.path.join(HARVEST, "dataone_lunaris_match.parquet")
ORIGINALS = os.path.join(HARVEST, "originals")
FINAL = os.path.join(BACKEND, "data", "dataone", "filter3", "filter3_final.parquet")
RESULTS = os.path.expanduser("~/Desktop/BCBN/Results")
PROBE_CACHE = os.path.join(RESULTS, "dataone_probe_2026-11", "cache")
DRYAD = os.path.join(RESULTS, "dataone_dryad_abstract_test_2026-11-23")
OUT = os.path.join(RESULTS, "dataone_licence_check_2026-11-25")
VIEW = "https://search.dataone.org/view/"

SHOWN = ["bc_data", "pending"]
EXPECTED_SHOWN = 5_071
EXPECTED_NEW_DATASETS = 3_525
SEED = 20261125
INDEX_FIELDS = ["licenseName", "licenseDescription", "licenseUrl"]
LIKE = re.compile(r"licen|rights|access|use|constraint|copyright", re.I)
ADDED_BY_HAND = {"isPublic"}

# Original-document elements whose text is licence text (mapped in step 2).
ORIGINAL_TEXT = re.compile(r"^(intellectualRights|licensed|useLimitation|otherConstraints|useConstraints|"
                           r"accessConstraints|MD_LegalConstraints|MD_Constraints)$")
# Notes on licence-like fields that hold no licence text.
NOT_TEXT = {"userId": "a person or organisation id, not licence text",
            "access": "EML access list (who may read or change the record), not licence text",
            "rightsHolder": "the DataONE account that owns the record, not licence text",
            "isPublic": "true when anyone may read the record; says nothing about terms of use",
            "isAccessibleForFree": "Dryad's mark that the files are free to download, not licence text"}
# Dryad JSON-LD keys whose value is licence text (mapped in step 2).
DRYAD_TEXT = {"license"}

# Values that are not a licence (V1), compared with only letters and digits kept, lower case.
PLACEHOLDERS = {"copyright", "otherrestrictions", "restricted", "other", "license", "licence", "none",
                "unknown", "na", "notspecified", "notapplicable", "accessconstraints", "uselimitation",
                "intellectualpropertyrights", "copyrightotherrestrictions", "rights"}
# Short values that only say the data are public or unrestricted (V8).
PUBLIC_ONLY = re.compile(r"^(access ?level:? ?public|public|no restrictions?\.?|unrestricted\.?|"
                         r"none\.?|data (is|are) public(al)?ly available\.?|publicly available\.?|open access\.?)$")
# Text saying the data are not public or must be requested (V7 and the access_restricted flag).
RESTRICTED = re.compile(
    r"\bnot public\b|not (currently )?(available|accessible) (for|to the|to) public|"
    r"not (publicly|openly) available|can be requested|must be requested|\b(up)?on request\b|"
    r"request access|contact [^.]{0,80}\b(for|to (request|obtain|get)) (access|the data|these data)|"
    r"\b(usage|use|access)( is| are)? restricted to\b|strict sharing rules|(only )?available only to|only available to|members only|"
    r"closed access")
# Words that make a text terms of use (V10).
TERMS = re.compile(r"\bcit(e|ed|ing|ation)\b|acknowledg|\bcredit|permission|commercial|warrant|liabilit|"
                   r"disclaim|responsib|intellectual property|\bnorms\b|terms|conditions|reproduc|distribut|"
                   r"publication|co-?author|copyright|\brights?\b|licen[cs]|(not|never) to be used|may not be used")

CC_NAMES = {"by": "CC BY", "by-sa": "CC BY-SA", "by-nc": "CC BY-NC", "by-nd": "CC BY-ND",
            "by-nc-sa": "CC BY-NC-SA", "by-nc-nd": "CC BY-NC-ND"}
LISTED = ["CC0", "CC BY 3.0", "CC BY 4.0", "CC BY-SA 4.0", "CC BY-NC 4.0", "CC BY-NC-SA 3.0",
          "CC BY-NC-SA 4.0", "CC BY-NC-ND 4.0", "public domain (not CC0)", "custom terms",
          "access restricted", "placeholder", "none"]
NOT_REAL = {"custom terms", "access restricted", "placeholder", "unsure", "none"}

pd.set_option("display.width", 250)
pd.set_option("display.max_colwidth", 120)
pd.set_option("display.max_rows", 300)


# --- Small helpers ---------------------------------------------------------------

def as_list(value):
    """Return a harvested field as a list of its non-empty values, each exactly as harvested."""
    if value is None or (isinstance(value, float) and value != value):
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [str(v) for v in value if v is not None and str(v).strip()]


def raw_cell(values):
    """Return values for a CSV cell: one value as it is, more than one as a JSON list, none as ''."""
    if not values:
        return ""
    return values[0] if len(values) == 1 else json.dumps(values, ensure_ascii=False)


def plain(text):
    """Return text with HTML entities undone (repeatedly, for &amp;amp;...), lower case, spaces collapsed."""
    for _ in range(8):
        undone = html.unescape(text)
        if undone == text:
            break
        text = undone
    return re.sub(r"\s+", " ", text).strip().lower()


def squashed(text):
    """Return plain(text) with only letters, digits and dots kept, for values written without spaces."""
    return re.sub(r"[^a-z0-9.]", "", plain(text))


def dataone_link(record_id):
    """Return the DataONE page of a record."""
    return VIEW + quote(record_id, safe="")


class Groups:
    """Union-find over keys, for copy groups linked through any chain of shared DOIs."""

    def __init__(self):
        """Start with no groups."""
        self.parent = {}

    def find(self, item):
        """Return the group of an item."""
        while self.parent.setdefault(item, item) != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def join(self, a, b):
        """Put two items in one group."""
        self.parent[self.find(a)] = self.find(b)


# --- Mapping one raw value (step 2) ------------------------------------------------

def cc_class(code, version):
    """Return the class name of a Creative Commons code (by, by-nc-sa, ...) and version."""
    return f"{CC_NAMES[code]} {version}"


def code_from_parts(nc, extra):
    """Return the CC code from its NonCommercial part and its ShareAlike / NoDerivs part."""
    code = "by" + ("-nc" if nc else "")
    if extra:
        code += "-sa" if extra.startswith("s") else "-nd"
    return code


def licences_named(value):
    """
    Return (licences, versionless): the set of licence classes the value names
    (rule V2), and whether it names a CC licence without a version.
    """
    text, sq = plain(value), squashed(value)
    found, versionless = set(), False
    # Creative Commons and SPDX links.
    for code, version in re.findall(r"creativecommons\.org/licen[cs]es/(by(?:-nc)?(?:-sa|-nd)?)/(\d\.\d)", text):
        found.add(cc_class(code, version))
    if re.search(r"creativecommons\.org/(publicdomain/zero|share-your-work/public-domain/cc0)", text):
        found.add("CC0")
    if re.search(r"creativecommons\.org/publicdomain/mark", text):
        found.add("public domain (not CC0)")
    # CC codes, with or without separators (CC BY-NC 4.0, CC-BY-4.0, CCBY-NC-SA3.0, spdx CC-BY-SA-4.0).
    for nc, extra, version in re.findall(r"ccby(nc)?(sa|nd)?(\d\.\d)", sq):
        found.add(cc_class(code_from_parts(nc, extra), version))
    if re.search(r"ccby(nc)?(sa|nd)?(?!\d\.\d)", sq):
        versionless = True
    # CC words with a version close after them.
    words = r"creativecommons(?:licen[cs]e)?(?:by)?attribution(noncommercial)?(sharealike|noderivs|noderivatives)?"
    for m in re.finditer(words, sq):
        after = re.search(r"(\d\.\d)", sq[m.end():m.end() + 24])
        if after:
            found.add(cc_class(code_from_parts(m.group(1), m.group(2)), after.group(1)))
        else:
            versionless = True
    # CC0 words.
    if re.search(r"cc0|cczero|creativecommonszero|creativecommonsuniversal1.0publicdomain", sq):
        found.add("CC0")
    # Public domain words, only when no CC0 is named.
    if "CC0" not in found and re.search(r"public ?domain", text):
        found.add("public domain (not CC0)")
    # Open Data Commons licences.
    for code, version in re.findall(r"opendatacommons\.org/licen[cs]es/(by|odbl|pddl)/(\d\.\d)", text):
        found.add({"by": "ODC-By", "odbl": "ODbL", "pddl": "ODC PDDL"}[code] + " " + version)
    # Open Government Licence - Canada.
    if re.search(r"open[- ]government[- ]licen[cs]e[- ]+canada", text):
        version = re.search(r"open[- ]government[- ]licen[cs]e[- ]+canada[^0-9]{0,12}(\d\.\d)", text)
        found.add("Open Government Licence - Canada" + (f" {version.group(1)}" if version else " (no version)"))
    return found, versionless


def says_restricted(value):
    """Return True if the value says the data are not public or must be requested (V7, flag R3)."""
    return bool(RESTRICTED.search(plain(value)))


def map_value(value):
    """Return (class, rule) for one raw value, by rules V1 to V11 (see the top of the script)."""
    text = plain(value)
    if re.sub(r"[^a-z0-9]", "", text) in PLACEHOLDERS:
        return "placeholder", "V1 placeholder word"
    found, versionless = licences_named(value)
    if found and re.search(r"similar to", text):
        return "unsure", "V3 says similar to: " + " | ".join(sorted(found))
    if len(found) > 1:
        return "unsure", "V4 names more than one licence: " + " | ".join(sorted(found))
    if len(found) == 1:
        return next(iter(found)), "V5 names one licence"
    if versionless:
        return "unsure", "V6 CC licence without a version"
    if "waived all rights" in text:
        return "unsure", "V6 CC0 waiver words without a licence name"
    if says_restricted(value):
        return "access restricted", "V7 not public or must be requested"
    if len(text) <= 60 and PUBLIC_ONLY.match(text):
        return "placeholder", "V8 only says public or no restrictions"
    if re.match(r"^https?://\S+$", text):
        return "custom terms", "V9 link naming no licence"
    if len(text.split()) >= 3 and TERMS.search(text):
        return "custom terms", "V10 terms of use text"
    return "unsure", "V11 no rule holds"


# --- Reading ---------------------------------------------------------------------------

def read_records():
    """
    Return (all_final, shown): the filter 3 table joined with each record's
    latest harvested fields, and the shown records (bc_data or pending) with
    new_or_lunaris and doi_group.
    """
    final = pd.read_parquet(FINAL, columns=["record_id", "datasource", "route", "frontend_group"])
    names = pq.read_schema(VERSIONS).names
    v = pq.read_table(VERSIONS, filters=[("id", "in", list(final["record_id"]))]).to_pandas()
    v = v[v["version_status"] == "latest"].drop_duplicates("id").set_index("id")
    keep = [c for c in names if c not in ("id", "datasource")]
    table = final.join(v[keep], on="record_id")
    table["shown"] = table["frontend_group"].isin(SHOWN)

    # Same-DOI groups over all filter 3 records.
    groups = Groups()
    for rid, sid in zip(table["record_id"], table["seriesId"]):
        groups.find(rid)
        for doi in dataone_dois(rid, sid):
            groups.join(rid, "doi:" + doi)
    table["doi_group"] = table["record_id"].map(groups.find)

    # Lunaris copies, as in the export plan.
    match = pd.read_parquet(MATCH, columns=["id", "lunaris_match_by"]).set_index("id")
    strong = []
    for rid in table["record_id"]:
        ways = match.at[rid, "lunaris_match_by"] if rid in match.index else []
        strong.append(any(set(w.split("+")) & {"doi", "uuid"} for w in ways))
    table["copy_of_lunaris"] = table["route"].isin(["lunaris", "lunaris_either_yes"]) | pd.Series(strong, index=table.index)

    shown = table[table["shown"]].copy()
    shown["new_or_lunaris"] = "Lunaris copy"
    new = shown[~shown["copy_of_lunaris"]].copy()
    new["_bc"] = (new["frontend_group"] != "bc_data").astype(int)
    new["_date"] = pd.to_datetime(new["dateUploaded"], errors="coerce", utc=True)
    new = new.sort_values(["doi_group", "_bc", "_date", "record_id"], ascending=[True, True, False, True])
    first = ~new["doi_group"].duplicated()
    shown.loc[new.index[first], "new_or_lunaris"] = "new record"
    shown.loc[new.index[~first], "new_or_lunaris"] = "DOI second copy"
    return table, shown


def read_probe(ids):
    """
    Return (fields, values): every field name seen in the saved probe answers,
    and for each shown record the distinct values of each field (as strings).
    """
    fields, values = set(), defaultdict(lambda: defaultdict(list))
    for path in sorted(glob.glob(os.path.join(PROBE_CACHE, "*.json"))):
        with open(path, encoding="utf-8") as f:
            answer = json.load(f)
        for doc in ((answer.get("reply") or {}).get("response") or {}).get("docs", []):
            fields.update(doc.keys())
            if doc.get("id") in ids:
                for key, val in doc.items():
                    for one in (val if isinstance(val, list) else [val]):
                        if str(one) not in values[doc["id"]][key]:
                            values[doc["id"]][key].append(str(one))
    return fields, values


def element_path(el):
    """Return the path of an XML element as local names joined by '/'."""
    names = [etree.QName(a).localname for a in reversed(list(el.iterancestors()))]
    return "/".join(names + [etree.QName(el).localname])


def read_originals(ids):
    """
    Return (docs, fields, texts) for the shown records with a saved original
    document: docs maps id to format; fields maps id to the set of licence-like
    element paths it fills; texts maps id to [(path, raw value)] for the
    elements that hold licence text (ORIGINAL_TEXT).
    """
    index = pd.read_csv(os.path.join(ORIGINALS, "index.csv"), dtype=str)
    index = index[(index["status"] == "200") & index["id"].isin(ids)]
    docs, fields, texts = {}, defaultdict(set), defaultdict(list)
    for rid, file, fmt in zip(index["id"], index["file"], index["formatId"]):
        with gzip.open(os.path.join(ORIGINALS, file)) as f:
            root = etree.fromstring(f.read())
        docs[rid] = fmt
        for el in root.iter():
            if not isinstance(el.tag, str):
                continue
            name = etree.QName(el).localname
            path = element_path(el)
            if LIKE.search(name):
                fields[rid].add(path)
            for attr in el.attrib:
                if LIKE.search(etree.QName(attr).localname):
                    fields[rid].add(path + "/@" + etree.QName(attr).localname)
            if not ORIGINAL_TEXT.match(name):
                continue
            if name == "intellectualRights":
                texts[rid].append((path, "".join(el.itertext())))
                continue
            # Other licence elements: each leaf inside, its text or its code list value.
            for leaf in el.iter():
                if isinstance(leaf.tag, str) and len(leaf) == 0:
                    raw = leaf.text if leaf.text and leaf.text.strip() else leaf.get("codeListValue", "")
                    if raw.strip():
                        texts[rid].append((element_path(leaf), raw))
    return docs, fields, texts


def read_dryad(ids):
    """
    Return (docs, fields, texts) for the saved Dryad JSON-LD documents of shown
    records: docs maps id to 'JSON-LD'; fields maps id to the licence-like keys
    it fills; texts maps id to [(key, raw value)].
    """
    log = pd.read_csv(os.path.join(DRYAD, "requests_log.csv"), dtype=str)
    docs, fields, texts = {}, defaultdict(set), defaultdict(list)
    for pid in log.loc[log["status"] == "200", "pid"]:
        if pid not in ids:
            continue
        path = os.path.join(DRYAD, pid.replace(":", "_") + ".bin")
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        docs[pid] = "JSON-LD"
        for key, val in doc.items():
            if LIKE.search(key):
                fields[pid].add("dryad/" + key)
            if key in DRYAD_TEXT:
                for one in (val if isinstance(val, list) else [val]):
                    texts[pid].append(("dryad/" + key, one if isinstance(one, str) else json.dumps(one)))
    return docs, fields, texts


# --- Step 1 -------------------------------------------------------------------------------

def step1_fields(shown, probe_fields, probe, orig_docs, orig_fields, dryad_docs, dryad_fields):
    """Write licence_like_fields.csv: each licence-like field by datasource, with the records filling it."""
    by_ds = shown.groupby("datasource")["record_id"].apply(list).to_dict()
    rows = []

    def add(source, field, has_doc, fills, note, text):
        """Add one row per datasource (and one for all) for a field: records with a saved source, and filling it."""
        for ds, ids in sorted(by_ds.items()) + [("ALL", list(shown["record_id"]))]:
            rows.append({"source": source, "field": field, "datasource": ds, "records": len(ids),
                         "records_with_this_source": sum(has_doc(i) for i in ids),
                         "records_filling": sum(fills(i) for i in ids),
                         "holds_licence_text": text, "note": note})

    index_fields = [c for c in pq.read_schema(VERSIONS).names if LIKE.search(c)]
    values = shown.set_index("record_id")
    for f in index_fields:
        add("index", f, lambda i: True, lambda i, f=f: bool(as_list(values.at[i, f])), "", "yes")
    for f in sorted(probe_fields):
        if LIKE.search(f) or f in ADDED_BY_HAND:
            note = NOT_TEXT.get(f, "the same index field as the harvest; not mapped again")
            if f in ADDED_BY_HAND:
                note = "added by hand (name does not match); " + note
            add("probe", f, lambda i: i in probe, lambda i, f=f: bool(probe.get(i, {}).get(f)), note, "no")
    for p in sorted({p for paths in orig_fields.values() for p in paths}):
        last = p.split("/")[-1].lstrip("@")
        text = "yes" if ORIGINAL_TEXT.match(last) else "no"
        note = NOT_TEXT.get(last, "") if text == "no" else ""
        if text == "no" and not note and any(ORIGINAL_TEXT.match(x) for x in p.split("/")):
            note = "inside a licence element; its text is mapped"
            text = "yes"
        add("original", p, lambda i: i in orig_docs, lambda i, p=p: p in orig_fields.get(i, set()), note, text)
    for p in sorted({p for paths in dryad_fields.values() for p in paths}):
        key = p.split("/", 1)[1]
        add("dryad", p, lambda i: i in dryad_docs, lambda i, p=p: p in dryad_fields.get(i, set()),
            NOT_TEXT.get(key, ""), "yes" if key in DRYAD_TEXT else "no")
    out = pd.DataFrame(rows)
    out.to_csv(os.path.join(OUT, "licence_like_fields.csv"), index=False, encoding="utf-8-sig")
    print("\n1. licence-like fields (all shown records):")
    print(out[out["datasource"] == "ALL"].drop(columns=["datasource"]).to_string(index=False))
    return out


# --- Steps 2 and 3 ------------------------------------------------------------------------------

def record_values(shown, orig_texts, dryad_texts):
    """Return {record_id: [(field, raw value)]} for every licence text value of each shown record."""
    out = {}
    for r in shown.to_dict("records"):
        vals = [(f, v) for f in INDEX_FIELDS for v in as_list(r[f])]
        vals += orig_texts.get(r["record_id"], []) + dryad_texts.get(r["record_id"], [])
        out[r["record_id"]] = vals
    return out


def step2_value_map(values):
    """Write licence_value_map.csv: every distinct (field, raw value), its records, class and rule."""
    counts = Counter()
    for rid, vals in values.items():
        for pair in set(vals):
            counts[pair] += 1
    rows = []
    for (field, raw), n in counts.items():
        cls, rule = map_value(raw)
        rows.append({"field": field, "raw_value": raw, "records": n, "mapped_class": cls, "rule": rule,
                     "says_not_public_or_request": "yes" if says_restricted(raw) else "no",
                     "flag_applies": "yes" if field == "licenseDescription" and says_restricted(raw) else "no",
                     "class_in_your_list": "yes" if cls in LISTED else "no"})
    out = pd.DataFrame(rows).sort_values(["field", "records", "raw_value"], ascending=[True, False, True])
    out.to_csv(os.path.join(OUT, "licence_value_map.csv"), index=False, encoding="utf-8-sig")
    print(f"\n2. distinct (field, raw value) pairs mapped: {len(out)}")
    print(pd.crosstab(out["mapped_class"], out["field"], margins=True).to_string())
    return out


def decide(vals, value_map):
    """Return (class, rule, access_restricted, conflict, classes_seen) for one record, by rules R1 to R6."""
    classes = [value_map[(f, v)] for f, v in vals]
    seen = sorted(set(classes))
    flag = any(f == "licenseDescription" and says_restricted(v) for f, v in vals)
    real = sorted({c for c in classes if c not in NOT_REAL})
    if len(real) > 1:
        return "conflict", "R2 two real licences: " + " | ".join(real), flag, True, seen
    if "unsure" in classes:
        return "unsure", "R4 a value no rule maps", flag, False, seen
    if real:
        rule = "R5 one real licence" + (" (placeholder set aside, R1)" if "placeholder" in classes else "")
        return real[0], rule, flag, False, seen
    for cls in ("access restricted", "custom terms"):
        if cls in classes:
            return cls, "R6 " + cls + (" (placeholder set aside, R1)" if "placeholder" in classes else ""), \
                flag, False, seen
    if classes:
        return "placeholder", "R6 placeholders only", flag, False, seen
    return "none", "R6 no licence value", flag, False, seen


def step3_per_record(shown, values, value_map_table):
    """Write licence_per_record.csv: one licence per shown record, with the flag and the conflict mark."""
    value_map = {(f, v): c for f, v, c in zip(value_map_table["field"], value_map_table["raw_value"],
                                              value_map_table["mapped_class"])}
    rows = []
    for r in shown.to_dict("records"):
        vals = values[r["record_id"]]
        cls, rule, flag, conflict, seen = decide(vals, value_map)
        other = [(f, v) for f, v in vals if f not in INDEX_FIELDS]
        rows.append({"record_id": r["record_id"], "datasource": r["datasource"],
                     "frontend_group": r["frontend_group"], "new_or_lunaris": r["new_or_lunaris"],
                     "doi_group": r["doi_group"],
                     **{f: raw_cell(as_list(r[f])) for f in INDEX_FIELDS},
                     "other_licence_text": json.dumps(other, ensure_ascii=False) if other else "",
                     "mapped_class": cls, "access_restricted": "yes" if flag else "no",
                     "conflict": "yes" if conflict else "no", "rule": rule, "classes_seen": " | ".join(seen),
                     "title": r["title"], "link": dataone_link(r["record_id"])})
    out = pd.DataFrame(rows)
    out.drop(columns=["title", "link"]).to_csv(os.path.join(OUT, "licence_per_record.csv"), index=False,
                                               encoding="utf-8-sig")
    return out


# --- Step 4 ---------------------------------------------------------------------------------------

def class_order(classes):
    """Return classes in the order of the licence list, then any others alphabetically."""
    listed = [c for c in LISTED if c in classes]
    return listed + sorted(c for c in classes if c not in LISTED)


def counts_table(frame, table, column):
    """Return rows (table, column, class, records, % of column) for one column, plus its TOTAL row."""
    rows = []
    groups = [("all", frame)] if column is None else list(frame.groupby(column))
    for col, part in groups:
        total = len(part)
        for cls, n in part["mapped_class"].value_counts().items():
            rows.append({"table": table, "column": col, "mapped_class": cls, "records": int(n),
                         "pct_of_column": round(100 * n / total, 1)})
        rows.append({"table": table, "column": col, "mapped_class": "TOTAL", "records": total,
                     "pct_of_column": 100.0})
    return rows


def step4_summary(per):
    """Write licence_summary.csv and licence_checks.csv; return the 3,525 new datasets table."""
    datasets = per[per["new_or_lunaris"] == "new record"].copy()
    bc = per[per["new_or_lunaris"] != "Lunaris copy"].groupby("doi_group")["frontend_group"].agg(
        lambda g: "bc_data" if "bc_data" in set(g) else "pending")
    datasets["dataset_group"] = datasets["doi_group"].map(bc)
    rows = (counts_table(per, "records_5071", None) + counts_table(per, "records_by_datasource", "datasource")
            + counts_table(per, "records_by_frontend_group", "frontend_group")
            + counts_table(per, "records_by_new_or_lunaris", "new_or_lunaris")
            + counts_table(datasets, "new_datasets_3525", None)
            + counts_table(datasets, "new_datasets_by_frontend_group", "dataset_group"))
    flag = per["access_restricted"].value_counts()
    for value in ("yes", "no"):
        rows.append({"table": "access_restricted_flag_5071", "column": "all", "mapped_class": value,
                     "records": int(flag.get(value, 0)), "pct_of_column": round(100 * flag.get(value, 0) / len(per), 1)})
    summary = pd.DataFrame(rows)
    order = {c: n for n, c in enumerate(class_order(set(summary["mapped_class"]) - {"TOTAL"}) + ["TOTAL"])}
    summary["_o"] = summary["mapped_class"].map(order).fillna(-1)
    summary = summary.sort_values(["table", "column", "_o"]).drop(columns="_o")
    summary.to_csv(os.path.join(OUT, "licence_summary.csv"), index=False, encoding="utf-8-sig")

    # Checks: each column adds up to its TOTAL, and each table's columns add up to the whole.
    checks = []
    body = summary[summary["mapped_class"] != "TOTAL"]
    totals = summary[summary["mapped_class"] == "TOTAL"].set_index(["table", "column"])["records"]
    for (table, col), part in body.groupby(["table", "column"]):
        if table.startswith("access"):
            continue
        checks.append({"check": f"{table} / {col}: classes add up to the column total",
                       "value": f"{int(part['records'].sum())} = {int(totals[(table, col)])}",
                       "ok": int(part["records"].sum()) == int(totals[(table, col)])})
    for table, whole in [("records_5071", EXPECTED_SHOWN), ("records_by_datasource", EXPECTED_SHOWN),
                         ("records_by_frontend_group", EXPECTED_SHOWN), ("records_by_new_or_lunaris", EXPECTED_SHOWN),
                         ("new_datasets_3525", EXPECTED_NEW_DATASETS),
                         ("new_datasets_by_frontend_group", EXPECTED_NEW_DATASETS),
                         ("access_restricted_flag_5071", EXPECTED_SHOWN)]:
        part = summary[(summary["table"] == table) & (summary["mapped_class"] != "TOTAL")]
        checks.append({"check": f"{table}: all columns add up to {whole:,}", "value": int(part["records"].sum()),
                       "ok": int(part["records"].sum()) == whole})
    checks.append({"check": "shown records are unique", "value": per["record_id"].is_unique,
                   "ok": bool(per["record_id"].is_unique)})
    checks.append({"check": "one new dataset per DOI group of new records", "value": datasets["doi_group"].nunique(),
                   "ok": datasets["doi_group"].is_unique})
    # How often the two copies of a same-DOI pair of new records get different classes.
    second = per[per["new_or_lunaris"] == "DOI second copy"]
    first_class = datasets.set_index("doi_group")["mapped_class"]
    differ = second[second["mapped_class"] != second["doi_group"].map(first_class)]
    checks.append({"check": "DOI second copies whose class differs from the first copy's", "value": len(differ),
                   "ok": ""})
    check_table = pd.DataFrame(checks)
    check_table.to_csv(os.path.join(OUT, "licence_checks.csv"), index=False, encoding="utf-8-sig")
    print("\n4. summary (all 5,071 and the 3,525 new datasets):")
    print(summary[summary["table"].isin(["records_5071", "new_datasets_3525", "records_by_frontend_group",
                                         "access_restricted_flag_5071", "records_by_new_or_lunaris"])].to_string(index=False))
    print("   checks:")
    print(check_table.to_string(index=False))
    if len(differ):
        print("   second copies with another class:")
        print(differ[["record_id", "datasource", "mapped_class", "doi_group"]].assign(
            first=differ["doi_group"].map(first_class)).to_string(index=False))
    by_ds = summary[summary["table"] == "records_by_datasource"].pivot_table(
        index="column", columns="mapped_class", values="records", fill_value=0)
    print("\n   records by datasource:")
    print(by_ds.sort_values("TOTAL", ascending=False).to_string())
    return datasets


# --- Step 5 -------------------------------------------------------------------------------------------

def step5_no_licence(per, shown, probe, orig_docs, orig_fields, orig_texts, dryad_docs):
    """Write no_licence_field.csv: for records with no index licence field, what else step 1 has, by datasource."""
    blank = per[(per["licenseName"] == "") & (per["licenseDescription"] == "") & (per["licenseUrl"] == "")]
    rows = []
    for ds, part in blank.groupby("datasource"):
        ids = list(part["record_id"])
        with_text = [i for i in ids if orig_texts.get(i)]
        examples = []
        for i in (with_text + [i for i in ids if i not in with_text])[:3]:
            bits = [f"{p}={v!r}" for p, v in orig_texts.get(i, [])]
            bits += [f"probe {k}={v}" for k, v in probe.get(i, {}).items() if k in ("rightsHolder", "isPublic")]
            bits += [f"original {p}" for p in sorted(orig_fields.get(i, set())) if not ORIGINAL_TEXT.match(p.split('/')[-1])]
            title = shown.loc[shown["record_id"] == i, "title"].iloc[0]
            examples.append(f"{i} | {str(title)[:90]} | " + ("; ".join(bits) if bits else "no other licence-like field"))
        rights = Counter(v for i in ids for v in probe.get(i, {}).get("rightsHolder", []))
        public = Counter(v for i in ids for v in probe.get(i, {}).get("isPublic", []))
        rows.append({"datasource": ds, "records": len(ids),
                     "with_probe_answer": sum(i in probe for i in ids),
                     "with_original_document": sum(i in orig_docs for i in ids),
                     "with_dryad_document": sum(i in dryad_docs for i in ids),
                     "with_licence_text_elsewhere": len(with_text),
                     "fields_with_text": " | ".join(sorted({p for i in with_text for p, _ in orig_texts[i]})),
                     "probe_rightsHolder_values": json.dumps(dict(rights.most_common(5))),
                     "probe_isPublic_values": json.dumps(dict(public)),
                     **{f"example_{n + 1}": e for n, e in enumerate(examples)}})
    out = pd.DataFrame(rows).sort_values("records", ascending=False)
    out.to_csv(os.path.join(OUT, "no_licence_field.csv"), index=False, encoding="utf-8-sig")
    print(f"\n5. records with no licenseName, licenseDescription or licenseUrl: {len(blank)}")
    print(out.drop(columns=[c for c in out.columns if c.startswith("example")]).to_string(index=False))
    return out


# --- Step 6 -------------------------------------------------------------------------------------------------

def step6_examples(per):
    """Write licence_examples.csv: per class, and every conflict and access_restricted record (30 at most each)."""
    parts = []
    lists = [(f"class: {c}", per[per["mapped_class"] == c]) for c in class_order(set(per["mapped_class"]))]
    lists += [("conflict", per[per["conflict"] == "yes"]), ("access_restricted", per[per["access_restricted"] == "yes"])]
    for why, part in lists:
        pick = part if len(part) <= 30 else part.sample(n=30, random_state=SEED)
        parts.append(pick.sort_values("record_id").assign(listed_for=why, listed_of=f"{len(pick)} of {len(part)}"))
    out = pd.concat(parts)[["listed_for", "listed_of", "record_id", "datasource", "frontend_group", "new_or_lunaris",
                            "title", *INDEX_FIELDS, "other_licence_text", "mapped_class", "access_restricted",
                            "conflict", "rule", "link"]]
    out.to_csv(os.path.join(OUT, "licence_examples.csv"), index=False, encoding="utf-8-sig")
    print(f"\n6. examples written: {len(out)} rows")
    return out


def main():
    """Read everything, run steps 1 to 6, and print what the report needs."""
    os.makedirs(OUT, exist_ok=True)
    table, shown = read_records()
    if len(shown) != EXPECTED_SHOWN:
        sys.exit(f"expected {EXPECTED_SHOWN} shown records, found {len(shown)}")
    ids = set(shown["record_id"])
    print(f"shown records: {len(shown):,}; new_or_lunaris: {shown['new_or_lunaris'].value_counts().to_dict()}")
    probe_fields, probe = read_probe(ids)
    orig_docs, orig_fields, orig_texts = read_originals(ids)
    dryad_docs, dryad_fields, dryad_texts = read_dryad(ids)
    print(f"saved sources: probe answers {len(probe):,}, original documents {len(orig_docs)}, "
          f"Dryad documents {len(dryad_docs)}")

    step1_fields(shown, probe_fields, probe, orig_docs, orig_fields, dryad_docs, dryad_fields)
    values = record_values(shown, orig_texts, dryad_texts)
    value_map = step2_value_map(values)
    per = step3_per_record(shown, values, value_map)
    print("\n3. records:", per["mapped_class"].value_counts().to_dict())
    print("   rules:", per["rule"].str.split(":").str[0].value_counts().to_dict())
    print("   conflict:", int((per["conflict"] == "yes").sum()), " access_restricted:",
          int((per["access_restricted"] == "yes").sum()))
    step4_summary(per)
    step5_no_licence(per, shown, probe, orig_docs, orig_fields, orig_texts, dryad_docs)
    step6_examples(per)
    print(f"\nwritten to {OUT}")


if __name__ == "__main__":
    main()
