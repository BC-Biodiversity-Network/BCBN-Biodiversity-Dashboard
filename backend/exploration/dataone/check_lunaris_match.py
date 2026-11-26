"""
check_lunaris_match.py

Read only (writes one review CSV outside the repo). Checks that the
DataONE records matched to Lunaris by DOI or UUID
(dataone_lunaris_match.parquet: 25,638 by DOI, 37 by UUID) really are the
same datasets, before filter 2 skips them.

A pair is one DataONE record and one Lunaris record it was matched to by
DOI or UUID (a Lunaris record found only by title is not a pair here).
Its type is "doi" if the DOI matched (also when the UUID matched too),
else "uuid".

Titles are compared after normalising: lower case, a trailing language
tag such as "@en" dropped, punctuation turned into spaces, whitespace
collapsed. The score is difflib's SequenceMatcher ratio (0 to 1). Both
come from pipeline/dataone/dataone_common.py (norm_for_similarity,
title_similarity).
Bands: identical (equal after normalising), very similar (0.9 or more),
somewhat similar (0.6 to 0.9), different (below 0.6). For pairs below
0.9 the abstracts are also compared: the first 300 characters of each
normalised abstract.

Where each key came from is worked out again with the functions of
match_lunaris.py: DataONE's DOI from its id (also the Research Workspace
id form) or seriesId; its UUID from id, seriesId or webUrl. Lunaris's DOI
from its doi column; its UUID from its id or its doi column (which holds
the landing page link when a record has no DOI).

The Lunaris link is the one our site shows (export_lunaris_datasets.py
link_of: https://doi.org/<DOI> when the DataCite identifier is a DOI, else
the URL identifier). The DataONE link is built as in build_label_set.py.

Output: ~/Desktop/BCBN/Results/dataone_lunaris_match_check_2026-11-20/
pairs_to_review.csv. The match file is only read.

Run, from backend/:
    ../.venv/bin/python exploration/dataone/check_lunaris_match.py
"""

import os
import sys
from urllib.parse import quote

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, "..", ".."))
os.chdir(BACKEND)  # the Lunaris export's default paths are relative to backend/
sys.path.insert(0, os.path.join(BACKEND, "pipeline", "dataone"))
sys.path.insert(0, os.path.join(BACKEND, "pipeline", "lunaris"))
from build_label_set import VIEW  # noqa: E402
from classify_multibox_records import VERSIONS  # noqa: E402
from dataone_common import RW_STYLE, norm_doi  # noqa: E402
# The title normalisation and similarity live in the pipeline (moved there on 2026-11-20).
from dataone_common import norm_for_similarity as norm, title_similarity as score  # noqa: E402
from export_lunaris_datasets import DEFAULT_RAW_XML, link_of, read_catalogue_names, read_datacite  # noqa: E402
from match_lunaris import LUNARIS_HARVEST, OUT as MATCH, uuids_in  # noqa: E402
from check_harvest import source_of  # noqa: E402

OUT_DIR = os.path.expanduser("~/Desktop/BCBN/Results/dataone_lunaris_match_check_2026-11-20")
OUT = os.path.join(OUT_DIR, "pairs_to_review.csv")
EXPECTED = {"doi": 25_638, "uuid": 37}
ABSTRACT_CHARS = 300
REVIEW_MAX, LOWEST, RANDOM_REST = 300, 100, 200
SEED = 20261120
BANDS = ["identical", "very similar (>=0.9)", "somewhat (0.6-0.9)", "different (<0.6)"]

pd.set_option("display.width", 250)
pd.set_option("display.max_colwidth", 80)


def band(a, b, ratio):
    """Return the band name of a pair of normalised titles and their ratio."""
    if a and a == b:
        return "identical"
    if ratio != ratio:
        return "different (<0.6)"
    if ratio >= 0.9:
        return "very similar (>=0.9)"
    if ratio >= 0.6:
        return "somewhat (0.6-0.9)"
    return "different (<0.6)"


def read_pairs():
    """
    Return one row per DOI or UUID pair from the match file: DataONE id,
    datasource, pair type, the way it was found, and the Lunaris id and
    filter 2 decision. Stops if the record counts are not the expected ones.
    """
    match = pd.read_parquet(MATCH)
    counts = match["match_type"].value_counts()
    for kind, n in EXPECTED.items():
        if int(counts.get(kind, 0)) != n:
            sys.exit(f"STOPPED: {int(counts.get(kind, 0)):,} {kind} matches, not {n:,}")
    strong = match[match["match_type"].isin(EXPECTED)]
    rows = []
    for r in strong.itertuples(index=False):
        for lid, way, f2 in zip(r.lunaris_ids, r.lunaris_match_by, r.lunaris_filter2):
            if "doi" in way or "uuid" in way:
                rows.append({"dataone_id": r.id, "datasource": r.datasource, "record_match_type": r.match_type,
                             "pair_type": "doi" if "doi" in way else "uuid", "found_by": way,
                             "lunaris_id": lid, "lunaris_filter2": f2})
    return pd.DataFrame(rows)


def dataone_key_sources(record):
    """Return {key: [fields]} for one DataONE record: its DOIs and UUIDs and the fields that hold each."""
    found = {}
    rw = RW_STYLE.match(str(record["id"]))
    if rw:
        found.setdefault(f"{rw.group(1)}/{rw.group(2)}".lower(), []).append("id (Research Workspace form)")
    for field in ("id", "seriesId"):
        doi = norm_doi(record[field])
        if doi:
            found.setdefault(doi, []).append(field)
    for field in ("id", "seriesId", "webUrl"):
        for uuid in uuids_in(record[field]):
            found.setdefault(uuid, []).append(field)
    return found


def lunaris_key_sources(record):
    """
    Return {key: [fields]} for one Lunaris record. The doi column is named
    "doi (a DOI)" or "doi (a link)" depending on what it holds.
    """
    found = {}
    doi_text = record["doi"] if isinstance(record["doi"], str) else ""
    kind = "doi (a link)" if doi_text.startswith(("http://", "https://")) else "doi (a DOI)"
    doi = norm_doi(doi_text)
    if doi:
        found.setdefault(doi, []).append(kind)
    for uuid in uuids_in(record["lunaris_id"]):
        found.setdefault(uuid, []).append("id")
    for uuid in uuids_in(doi_text):
        found.setdefault(uuid, []).append(kind)
    return found


def add_records(pairs):
    """Add both sides' titles, abstracts, key fields, catalogue, links and the keys that matched."""
    dataone = pq.read_table(VERSIONS, columns=["id", "seriesId", "webUrl", "title", "abstract", "version_status"],
                            filters=[("id", "in", sorted(set(pairs["dataone_id"])))]).to_pandas()
    dataone = dataone[dataone["version_status"] == "latest"].drop_duplicates("id").set_index("id")
    lunaris = pd.read_parquet(LUNARIS_HARVEST, columns=["id", "doi", "title", "abstract"],
                              filters=[("id", "in", sorted(set(pairs["lunaris_id"])))])
    lunaris = lunaris.drop_duplicates("id").set_index("id")
    missing = set(pairs["dataone_id"]) - set(dataone.index) | set(pairs["lunaris_id"]) - set(lunaris.index)
    if missing:
        sys.exit(f"STOPPED: {len(missing)} ids of the match file are missing from the harvests")
    facts = read_datacite(DEFAULT_RAW_XML, set(pairs["lunaris_id"]))
    names = read_catalogue_names()
    p = pairs.copy()
    for column in ("seriesId", "webUrl", "title", "abstract"):
        p[f"d_{column}"] = p["dataone_id"].map(dataone[column])
    for column in ("doi", "title", "abstract"):
        p[f"l_{column}"] = p["lunaris_id"].map(lunaris[column])
    p["catalogue"] = p["lunaris_id"].map(source_of)
    p["catalogue_name"] = p["catalogue"].map(names).fillna("")
    p["dataone_link"] = [VIEW + quote(i, safe="") for i in p["dataone_id"]]
    p["lunaris_link"] = [link_of(facts.at[i, "identifier"], facts.at[i, "identifier_type"])[0] for i in p["lunaris_id"]]
    p["lunaris_identifier_type"] = p["lunaris_id"].map(facts["identifier_type"])
    keys, d_from, l_from = [], [], []
    for r in p.itertuples(index=False):
        d = dataone_key_sources({"id": r.dataone_id, "seriesId": r.d_seriesId, "webUrl": r.d_webUrl})
        lu = lunaris_key_sources({"lunaris_id": r.lunaris_id, "doi": r.l_doi})
        shared = sorted(set(d) & set(lu))
        keys.append("; ".join(shared))
        d_from.append("; ".join(sorted({f for k in shared for f in d[k]})))
        l_from.append("; ".join(sorted({f for k in shared for f in lu[k]})))
    p["matched_key"], p["dataone_key_from"], p["lunaris_key_from"] = keys, d_from, l_from
    if (p["matched_key"] == "").any():
        sys.exit(f"STOPPED: {int((p['matched_key'] == '').sum())} pairs share no DOI or UUID when worked out again")
    return p


def add_scores(p):
    """Add the normalised titles, the title score and band, and for pairs below 0.9 the abstract score."""
    p["d_title_n"], p["l_title_n"] = p["d_title"].map(norm), p["l_title"].map(norm)
    p["title_score"] = [1.0 if a and a == b else score(a, b) for a, b in zip(p["d_title_n"], p["l_title_n"])]
    p["band"] = [band(a, b, s) for a, b, s in zip(p["d_title_n"], p["l_title_n"], p["title_score"])]
    low = p["title_score"].fillna(0) < 0.9
    p["abstract_score"] = np.nan
    p.loc[low, "abstract_score"] = [score(norm(a)[:ABSTRACT_CHARS], norm(b)[:ABSTRACT_CHARS])
                                    for a, b in zip(p.loc[low, "d_abstract"], p.loc[low, "l_abstract"])]
    p["abstract_note"] = ""
    p.loc[low & (p["d_abstract"].map(norm) == ""), "abstract_note"] = "DataONE abstract empty"
    p.loc[low & (p["l_abstract"].map(norm) == ""), "abstract_note"] = \
        np.where(p.loc[low & (p["l_abstract"].map(norm) == ""), "abstract_note"] == "",
                 "Lunaris abstract empty", "both abstracts empty")
    return p


def report_counts(p):
    """Points 1 and 2: pairs by title band, by pair type and by datasource; abstract scores of the pairs below 0.9."""
    print(f"pairs: {len(p):,} (DataONE records: {p['dataone_id'].nunique():,}; "
          f"by record match type {p.drop_duplicates('dataone_id')['record_match_type'].value_counts().to_dict()})")
    print(f"   found by: {p['found_by'].value_counts().to_dict()}")
    print("\n1. TITLE SIMILARITY BY PAIR TYPE")
    print(pd.crosstab(p["pair_type"], p["band"], margins=True, margins_name="total").reindex(
        columns=BANDS + ["total"], fill_value=0).to_string())
    print("\n   BY DataONE DATASOURCE")
    table = pd.crosstab(p["datasource"], p["band"], margins=True, margins_name="total").reindex(
        columns=BANDS + ["total"], fill_value=0)
    print(table.sort_values("total", ascending=False).to_string())
    print(f"   empty title on a side: DataONE {int((p['d_title_n'] == '').sum())}, "
          f"Lunaris {int((p['l_title_n'] == '').sum())}")
    print("\n   records whose Lunaris pairs fall in different bands: "
          f"{int((p.groupby('dataone_id')['band'].nunique() > 1).sum())}")
    low = p[p["title_score"].fillna(0) < 0.9].copy()
    print(f"\n2. ABSTRACTS OF THE {len(low):,} PAIRS BELOW 0.9 (first {ABSTRACT_CHARS} normalised characters)")
    low["abstract_band"] = pd.cut(low["abstract_score"], [-0.01, 0.6, 0.9, 0.999999, 1.0],
                                  labels=["<0.6", "0.6-0.9", "0.9-<1", "1 (same)"]).astype(str)
    low.loc[low["abstract_score"].isna(), "abstract_band"] = low.loc[low["abstract_score"].isna(), "abstract_note"]
    print(pd.crosstab([low["pair_type"], low["band"]], low["abstract_band"], margins=True,
                      margins_name="total").to_string())
    return low


def report_sources(p, low):
    """Point 3: where the DOI or UUID came from on each side, for every UUID pair and for DOI pairs below 0.9."""
    print("\n3. WHERE THE MATCHED KEY CAME FROM")
    for label, part in (("all UUID pairs", p[p["pair_type"] == "uuid"]),
                        ("DOI pairs below 0.9", low[low["pair_type"] == "doi"]),
                        ("all DOI pairs (for comparison)", p[p["pair_type"] == "doi"])):
        print(f"   {label}: {len(part):,}")
        table = part.groupby(["dataone_key_from", "lunaris_key_from"]).size().rename("pairs").reset_index()
        print(table.sort_values("pairs", ascending=False).to_string(index=False))
    uuid = p[p["pair_type"] == "uuid"]
    print("\n   the UUID pairs one by one:")
    for r in uuid.sort_values("title_score").itertuples(index=False):
        print(f"   {r.datasource} {r.dataone_id[:55]} [{r.dataone_key_from}] -> {r.catalogue} {r.lunaris_id[:55]} "
              f"[{r.lunaris_key_from}] | title {r.title_score:.2f}")


def report_examples(low):
    """Point 5: the 10 least similar pairs."""
    print("\n5. TEN LEAST SIMILAR PAIRS (title score, then abstract score)")
    worst = low.sort_values(["title_score", "abstract_score"], na_position="first").head(10)
    for r in worst.itertuples(index=False):
        print(f"   [{r.pair_type}] title {r.title_score:.2f}, abstract "
              f"{'n/a' if r.abstract_score != r.abstract_score else f'{r.abstract_score:.2f}'} {r.abstract_note}")
        print(f"      DataONE {r.datasource} {r.dataone_id} | {' '.join(str(r.d_title).split())[:110]}")
        print(f"      Lunaris {r.catalogue} {r.lunaris_id} | {' '.join(str(r.l_title).split())[:110]}")
        print(f"      key {r.matched_key} (DataONE {r.dataone_key_from}; Lunaris {r.lunaris_key_from}); "
              f"Lunaris filter 2 {r.lunaris_filter2}")


def write_review(low):
    """
    Point 4: write the pairs below 0.9 to OUT; if there are more than 300,
    all of the lowest 100 and a random 200 of the rest.
    """
    ordered = low.sort_values(["title_score", "abstract_score"], na_position="first")
    if len(ordered) > REVIEW_MAX:
        lowest = ordered.head(LOWEST)
        rest = ordered.iloc[LOWEST:].sample(RANDOM_REST, random_state=SEED)
        chosen = pd.concat([lowest.assign(picked="lowest 100"), rest.assign(picked="random of the rest")])
    else:
        chosen = ordered.assign(picked="all below 0.9")
    out = pd.DataFrame({
        "picked": chosen["picked"], "match_type": chosen["pair_type"], "found_by": chosen["found_by"],
        "dataone_id": chosen["dataone_id"], "datasource": chosen["datasource"], "dataone_title": chosen["d_title"],
        "lunaris_id": chosen["lunaris_id"], "catalogue": chosen["catalogue"],
        "catalogue_name": chosen["catalogue_name"], "lunaris_title": chosen["l_title"],
        "title_score": chosen["title_score"].round(3), "abstract_score": chosen["abstract_score"].round(3),
        "abstract_note": chosen["abstract_note"], "matched_key": chosen["matched_key"],
        "dataone_key_from": chosen["dataone_key_from"], "lunaris_key_from": chosen["lunaris_key_from"],
        "lunaris_filter2": chosen["lunaris_filter2"], "dataone_link": chosen["dataone_link"],
        "lunaris_link": chosen["lunaris_link"]})
    os.makedirs(OUT_DIR, exist_ok=True)
    out.to_csv(OUT + ".tmp", index=False, encoding="utf-8-sig")
    os.replace(OUT + ".tmp", OUT)
    print(f"\n4. wrote {len(out)} pairs -> {OUT} ({out['picked'].value_counts().to_dict()})")
    print(f"   Lunaris link empty: {int((out['lunaris_link'] == '').sum())}")


def main():
    """Read the pairs, compare them, print the report and write the review file."""
    pairs = add_scores(add_records(read_pairs()))
    low = report_counts(pairs)
    report_sources(pairs, low)
    write_review(low)
    report_examples(low)
    print(f"\nLunaris filter 2 decision of the pairs below 0.9: {low['lunaris_filter2'].value_counts().to_dict()}; "
          f"of all pairs: {pairs['lunaris_filter2'].value_counts().to_dict()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
