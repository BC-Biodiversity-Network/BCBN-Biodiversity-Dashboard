"""
match_lunaris.py

Match the DataONE records that go to filter 2 against the Lunaris harvest,
so the Lunaris results can be reused instead of running the LLM again on
the same datasets. Reads only; writes one file.

Inputs:
  dataone_groups.parquet                 (build_dataone_groups.py) the records
                                         with to_filter2 true
  dataone_full_harvest_versions.parquet  id, seriesId, webUrl, title, dates
  Lunaris harvest   exploration/lunaris/harvest_2026_10_20/lunaris_full_harvest.parquet
                    (the harvest both filter 2 runs and the export read; an
                    id that appears twice is kept once)
  Lunaris results   data/lunaris_bc_filter/filter3_final_union/filter3_merged.csv
                    (the table export_lunaris_datasets.py reads). It holds
                    one row for every record filter 2 kept under the union
                    rule: kept by the 4b run or the 35b run, plus the hand
                    yes decisions, less the hand no decisions
                    (build_filter3_union.keep_sources). So a Lunaris record
                    in this table has filter 2 "yes" (filter2_keep_source
                    says which run: both, 4b_only, 35b_only), and any other
                    record of the harvest has "no". The table also gives
                    final_class and frontend_group.

How records are matched. Every match is kept, not only the first:
  a. DOI. DataONE: the DOIs in id and in seriesId (also the DOI hidden in a
     Research Workspace id such as 10.24431_rw1k59m_20210915T201228Z).
     Lunaris: its doi column. Both normalised with dataone_common.norm_doi.
  b. UUID. The UUIDs in DataONE's id, seriesId or webUrl against the UUIDs
     in the Lunaris id or doi column (where Lunaris keeps the landing page
     link for records without a DOI), as lunaris_keys and match_lunaris do
     in the third DataONE probe (exploration/dataone/probe_dataone_3.py).
  c. Title. The same normalised title (dataone_common.norm_title, with a
     trailing language tag such as "@en" dropped), at least 25 characters.
     Only reported; never used to skip the LLM.
match_type is the first that found anything, in that order (doi, uuid,
title_only), or none. The lists say every Lunaris record found and how.

Output: dataone_lunaris_match.parquet next to the groups file, one row per
DataONE record with to_filter2 true:
  id, datasource, group, match_type,
  lunaris_ids, lunaris_match_by   every Lunaris record found, and how
                                  ("doi", "uuid", "doi+uuid", "title", ...)
  lunaris_sources, lunaris_filter2, lunaris_filter2_runs,
  lunaris_final_class, lunaris_frontend_group
                                  for each Lunaris record, in the same order
  lunaris_filter2_summary         over the DOI and UUID matches (over the
                                  title matches for title_only): yes, no or
                                  mixed; empty for none
  title_similarity                the best title similarity between the
                                  record and its Lunaris matches: over the
                                  DOI and UUID matches only (a title match
                                  always has the same title, so it would
                                  hide a wrong DOI match), over the title
                                  matches for title_only, empty for none.
                                  Both titles go through
                                  dataone_common.norm_for_similarity and
                                  the score is dataone_common.title_similarity
  reuse_lunaris                   True when the record may reuse the Lunaris
                                  filter 2 result instead of going to
                                  filter 2: a DOI or UUID match with
                                  title_similarity of 0.9 or more (rule
                                  approved by Lucia on 2026-11-20). Every
                                  other record goes to filter 2
The script checks with git check-ignore that git ignores the file, and
stops if it exists unless --replace.

Run, from backend/:
    ../.venv/bin/python pipeline/dataone/match_lunaris.py
"""

import argparse
import os
import sys

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(BACKEND, "pipeline", "lunaris"))
from check_harvest import source_of  # noqa: E402
from classify_multibox_records import HARVEST, SECONDS_PER_RECORD, VERSIONS, check_ignored  # noqa: E402
from dataone_common import (RW_STYLE, UUID_RE, norm_doi, norm_for_similarity, norm_title,  # noqa: E402
                             strip_lang, title_similarity)

GROUPS = os.path.join(HARVEST, "dataone_groups.parquet")
OUT = os.path.join(HARVEST, "dataone_lunaris_match.parquet")
LUNARIS_HARVEST = os.path.join(BACKEND, "exploration", "lunaris", "harvest_2026_10_20", "lunaris_full_harvest.parquet")
LUNARIS_RESULTS = os.path.join(BACKEND, "data", "lunaris_bc_filter", "filter3_final_union", "filter3_merged.csv")
MIN_TITLE = 25          # shorter titles ("Water quality") are too generic to match
STRONG = ("doi", "uuid")
EXAMPLES = 3
REUSE_MIN_SIMILARITY = 0.9  # a DOI or UUID match reuses Lunaris only from this title similarity up

LIST_COLUMNS = ["lunaris_ids", "lunaris_match_by", "lunaris_sources", "lunaris_filter2", "lunaris_filter2_runs",
                "lunaris_final_class", "lunaris_frontend_group"]


def check_output(replace):
    """Stop before any work unless git ignores OUT and OUT does not exist yet (or --replace)."""
    check_ignored(OUT)
    if os.path.exists(OUT) and not replace:
        sys.exit(f"STOPPED: {OUT} already exists; pass --replace to write over it")


def as_text(value):
    """Return a cell as one string: a list joined with spaces, "" when missing."""
    if value is None or (isinstance(value, float) and value != value):
        return ""
    if not isinstance(value, str) and hasattr(value, "__iter__"):
        return " ".join(str(v) for v in value if v is not None)
    return str(value)


def uuids_in(*values):
    """Return the set of UUIDs (lower case) written in any of the values."""
    found = set()
    for value in values:
        found.update(u.lower() for u in UUID_RE.findall(as_text(value)))
    return found


def dataone_dois(record_id, series_id):
    """Return the set of normalised DOIs in a DataONE record's id and seriesId."""
    found = set()
    match = RW_STYLE.match(str(record_id))
    if match:
        found.add(f"{match.group(1)}/{match.group(2)}".lower())
    for value in (record_id, series_id):
        doi = norm_doi(value)
        if doi:
            found.add(doi)
    return found


def read_dataone():
    """Return the DataONE records with to_filter2 true, with the fields used for matching and reporting."""
    groups = pd.read_parquet(GROUPS)
    groups = groups[groups["to_filter2"]].drop(columns=["to_filter2", "reason"])
    columns = ["id", "seriesId", "webUrl", "title", "pubDate", "dateUploaded"]
    fields = pq.read_table(VERSIONS, columns=columns, filters=[("id", "in", groups["id"].tolist())]).to_pandas()
    fields = fields.drop_duplicates("id")
    records = groups.merge(fields, on="id", how="left", validate="one_to_one")
    if records["title"].isna().all() or len(records) != len(groups):
        sys.exit("STOPPED: the groups file and the versions file do not match")
    return records


def read_lunaris():
    """
    Return the Lunaris records (one row per id) with source, filter 2
    decision and runs, final class, front end group, and the DOI, UUIDs and
    normalised title used for matching.
    """
    harvest = pd.read_parquet(LUNARIS_HARVEST, columns=["id", "datestamp", "doi", "title"])
    harvest = harvest.drop_duplicates("id").set_index("id")
    results = pd.read_csv(LUNARIS_RESULTS, dtype=str, keep_default_na=False,
                          usecols=["record_id", "final_class", "frontend_group", "filter2_keep_source"])
    results = results.set_index("record_id")
    if not results.index.isin(harvest.index).all():
        sys.exit("STOPPED: the Lunaris results name records that are not in the Lunaris harvest")
    lunaris = harvest.join(results, how="left").fillna({"final_class": "", "frontend_group": "",
                                                        "filter2_keep_source": ""})
    lunaris["source"] = [source_of(i) for i in lunaris.index]
    lunaris["filter2"] = (lunaris["filter2_keep_source"] != "").map({True: "yes", False: "no"})
    lunaris["doi_norm"] = lunaris["doi"].map(norm_doi)
    lunaris["uuids"] = [uuids_in(i, d) for i, d in zip(lunaris.index, lunaris["doi"])]
    lunaris["title_norm"] = lunaris["title"].map(norm_title)
    return lunaris


def build_keys(lunaris):
    """Return three dicts from a key (DOI, UUID, title) to the list of Lunaris ids that carry it."""
    keys = {"doi": {}, "uuid": {}, "title": {}}
    for record_id, doi, uuids, title in zip(lunaris.index, lunaris["doi_norm"], lunaris["uuids"],
                                            lunaris["title_norm"]):
        if doi:
            keys["doi"].setdefault(doi, []).append(record_id)
        for uuid in uuids:
            keys["uuid"].setdefault(uuid, []).append(record_id)
        if len(title) >= MIN_TITLE:
            keys["title"].setdefault(title, []).append(record_id)
    return keys


def match_one(record, keys):
    """
    Return {Lunaris id: set of ways it was found} for one DataONE record,
    from its DOIs, its UUIDs and its title.
    """
    found = {}
    for doi in dataone_dois(record["id"], record["seriesId"]):
        for lunaris_id in keys["doi"].get(doi, []):
            found.setdefault(lunaris_id, set()).add("doi")
    for uuid in uuids_in(record["id"], record["seriesId"], record["webUrl"]):
        for lunaris_id in keys["uuid"].get(uuid, []):
            found.setdefault(lunaris_id, set()).add("uuid")
    title = norm_title(strip_lang(record["title"]))
    if len(title) >= MIN_TITLE:
        for lunaris_id in keys["title"].get(title, []):
            found.setdefault(lunaris_id, set()).add("title")
    return found


def summary_of(decisions):
    """Return yes, no or mixed for a list of filter 2 decisions; "" for an empty list."""
    distinct = set(decisions)
    if not distinct:
        return ""
    return distinct.pop() if len(distinct) == 1 else "mixed"


def best_similarity(title, lunaris_titles):
    """
    Return the best title similarity between a DataONE title and a list of
    Lunaris titles, after norm_for_similarity; NaN when the list is empty
    or every comparison has an empty title.
    """
    mine = norm_for_similarity(title)
    scores = [title_similarity(mine, norm_for_similarity(t)) for t in lunaris_titles]
    scores = [x for x in scores if x == x]
    return max(scores) if scores else float("nan")


def match_all(records, lunaris):
    """Return the output table: one row per DataONE record, with every Lunaris match and its results."""
    keys = build_keys(lunaris)
    rows = []
    for record in records.to_dict("records"):
        found = match_one(record, keys)
        ways = set().union(*found.values()) if found else set()
        match_type = "doi" if "doi" in ways else "uuid" if "uuid" in ways else "title_only" if ways else "none"
        ids = sorted(found)
        row = {"id": record["id"], "datasource": record["datasource"], "group": record["group"],
               "match_type": match_type, "lunaris_ids": ids,
               "lunaris_match_by": ["+".join(w for w in ("doi", "uuid", "title") if w in found[i]) for i in ids]}
        for column, field in (("lunaris_sources", "source"), ("lunaris_filter2", "filter2"),
                              ("lunaris_filter2_runs", "filter2_keep_source"),
                              ("lunaris_final_class", "final_class"), ("lunaris_frontend_group", "frontend_group")):
            row[column] = [lunaris.at[i, field] for i in ids]
        counted = [d for i, d in zip(ids, row["lunaris_filter2"])
                   if (found[i] & set(STRONG)) or match_type == "title_only"]
        row["lunaris_filter2_summary"] = summary_of(counted)
        compared = [i for i in ids if (found[i] & set(STRONG)) or match_type == "title_only"]
        row["title_similarity"] = best_similarity(record["title"], [lunaris.at[i, "title"] for i in compared])
        row["reuse_lunaris"] = bool(match_type in STRONG and row["title_similarity"] >= REUSE_MIN_SIMILARITY)
        rows.append(row)
    return pd.DataFrame(rows)


def write(result):
    """Write the match table to OUT, through a temporary file."""
    schema = pa.schema([(c, pa.string()) for c in ["id", "datasource", "group", "match_type"]]
                       + [(c, pa.list_(pa.string())) for c in LIST_COLUMNS]
                       + [("lunaris_filter2_summary", pa.string()), ("title_similarity", pa.float64()),
                          ("reuse_lunaris", pa.bool_())])
    table = pa.Table.from_pandas(result[schema.names], schema=schema, preserve_index=False)
    pq.write_table(table, OUT + ".tmp", compression="zstd")
    os.replace(OUT + ".tmp", OUT)
    print(f"\nwrote {len(result):,} rows -> {OUT}")


def strong_ids(row):
    """Return the Lunaris ids a DataONE row matched by DOI or UUID."""
    return [i for i, way in zip(row["lunaris_ids"], row["lunaris_match_by"]) if "doi" in way or "uuid" in way]


def report(result, records, lunaris):
    """Print the counts, the many-to-many cases, the title_only examples and filter 2's new load."""
    order = ["doi", "uuid", "title_only", "none"]
    print("MATCH TYPES")
    print(result["match_type"].value_counts().reindex(order, fill_value=0).to_string())
    print(f"   total: {len(result):,}")
    print("\nBY DATASOURCE (top 10 by matched records)")
    table = pd.crosstab(result["datasource"], result["match_type"]).reindex(columns=order, fill_value=0)
    table["matched"] = table[["doi", "uuid", "title_only"]].sum(axis=1)
    print(table.sort_values("matched", ascending=False).head(10).to_string())

    strong = result[result["match_type"].isin(STRONG)]
    print("\nLUNARIS FILTER 2 DECISION, DOI OR UUID MATCHES")
    print(pd.crosstab(strong["match_type"], strong["lunaris_filter2_summary"], margins=True).to_string())
    print("   front end group of the Lunaris records matched by DOI or UUID (records kept by filter 2):")
    groups = pd.Series([g for _, r in strong.iterrows() for i, g in zip(r["lunaris_ids"], r["lunaris_frontend_group"])
                        if i in strong_ids(r) and g != ""])
    print("   " + str(groups.value_counts().to_dict()))

    title_of = records.set_index("id")
    strong = strong.assign(strong_ids=strong.apply(strong_ids, axis=1))
    many = strong[strong["strong_ids"].map(len) > 1]
    print(f"\nDataONE RECORDS MATCHING MORE THAN ONE LUNARIS RECORD (DOI or UUID): {len(many):,}")
    for _, r in many.head(EXAMPLES).iterrows():
        print(f"   {r['id']} ({r['datasource']}) -> " + "; ".join(
            f"{i} [{lunaris.at[i, 'filter2']}]" for i in r["strong_ids"]))
    pairs = strong[["id", "strong_ids"]].explode("strong_ids")
    per_lunaris = pairs.groupby("strong_ids")["id"].apply(list)
    shared = per_lunaris[per_lunaris.map(len) > 1]
    print(f"LUNARIS RECORDS MATCHED BY MORE THAN ONE DataONE RECORD: {len(shared):,} "
          f"(holding {int(shared.map(len).sum()):,} DataONE records)")
    for lunaris_id, ids in shared.head(EXAMPLES).items():
        print(f"   {lunaris_id} <- " + "; ".join(f"{i} ({title_of.at[i, 'datasource']})" for i in ids[:4])
              + (f" ... and {len(ids) - 4} more" if len(ids) > 4 else ""))

    titles = result[result["match_type"] == "title_only"].sample(10, random_state=11)
    print("\nTEN title_only EXAMPLES")
    for _, r in titles.iterrows():
        d = title_of.loc[r["id"]]
        i = r["lunaris_ids"][0]
        print(f"   DataONE {r['id']} | {r['datasource']} | pub {str(d['pubDate'])[:10]} | {str(d['title'])[:100]}")
        print(f"   Lunaris {i} | {lunaris.at[i, 'source']} | datestamp {str(lunaris.at[i, 'datestamp'])[:10]} | "
              f"filter2 {lunaris.at[i, 'filter2']} | {str(lunaris.at[i, 'title'])[:100]}"
              + (f"   (+{len(r['lunaris_ids']) - 1} more)" if len(r["lunaris_ids"]) > 1 else ""))

    strong = result[result["match_type"].isin(STRONG)]
    below = strong[~strong["reuse_lunaris"]]
    print(f"\nTITLE CHECK OF THE DOI OR UUID MATCHES (reuse Lunaris from {REUSE_MIN_SIMILARITY} up)")
    print(pd.crosstab(strong["match_type"], strong["reuse_lunaris"], margins=True).to_string())
    print(f"   not reused: {len(below):,} records; their Lunaris filter 2 summary: "
          f"{below['lunaris_filter2_summary'].value_counts().to_dict()}; by datasource: "
          f"{below['datasource'].value_counts().to_dict()}")
    skipped = int(result["reuse_lunaris"].sum())
    left = len(result) - skipped
    print(f"\nFILTER 2: {len(result):,} records now; skipping the {skipped:,} that reuse Lunaris leaves {left:,}, "
          f"{left * SECONDS_PER_RECORD / 3600:.1f} slice-hours at {SECONDS_PER_RECORD} s per record")


def main():
    """Match, report and write."""
    parser = argparse.ArgumentParser(description="Match the DataONE records that go to filter 2 against Lunaris.")
    parser.add_argument("--replace", action="store_true", help="write over an existing output file")
    args = parser.parse_args()
    pd.set_option("display.width", 220)
    check_output(args.replace)
    records = read_dataone()
    lunaris = read_lunaris()
    print(f"DataONE records with to_filter2 true: {len(records):,}; Lunaris records: {len(lunaris):,}, "
          f"filter 2 yes: {int((lunaris['filter2'] == 'yes').sum()):,}\n")
    result = match_all(records, lunaris)
    report(result, records, lunaris)
    write(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
