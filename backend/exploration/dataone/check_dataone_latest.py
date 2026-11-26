"""
check_dataone_latest.py

Read-only counts on the DataONE records that mark_versions.py left as
"latest". Nothing is marked or changed here, and nothing is downloaded.

  3. Repeated titles: within one datasource, latest records whose titles
     are the same once lower-cased and trimmed of spaces at both ends. How
     many such groups, how many records they hold, by datasource, and 10
     groups to read (id, dateUploaded, origin, start of the abstract), to
     judge whether they are versions of one dataset or different datasets
     that share a title. (Evan suggested keeping the most recent when titles
     repeat; this only counts.)
  4. Multi-box records (more than one geohash_9 value) among the latest
     records that the BC text rules of check_dataone_harvest.py do not
     catch: how many original documents would have to be read to see every
     box, and how long at one request per second. The list of their ids is
     written next to the versions file (dataone_multibox_documents.csv),
     after checking with git check-ignore that it is ignored by git.

Check 3 runs only with --titles: Lucia decided on 2026-11-18 not to use
repeated titles to mark anything (copies are handled later, after the BC
and biodiversity filters, with the Lunaris copy-group step).

Output: printed tables, and CSVs in
~/Desktop/BCBN/Results/dataone_versions_2026-11-18/.

Run, from backend/:
    ../.venv/bin/python exploration/dataone/check_dataone_latest.py            # check 4
    ../.venv/bin/python exploration/dataone/check_dataone_latest.py --titles   # checks 3 and 4
"""

import argparse
import os
import subprocess
import sys

import pandas as pd
import pyarrow.compute as pc
import pyarrow.parquet as pq

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_dataone_harvest import (WIDENINGS, bc_text_matchers, caught_by_bc_text,  # noqa: E402
                                   geohash_centre, near_bc, text_of)

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MARKED = os.path.join(BACKEND, "data", "dataone", "harvest_2026_11_18", "dataone_full_harvest_versions.parquet")
MULTIBOX_CSV = os.path.join(os.path.dirname(MARKED), "dataone_multibox_documents.csv")
OUT_DIR = os.path.expanduser("~/Desktop/BCBN/Results/dataone_versions_2026-11-18")
GROUPS_TO_SHOW = 10
SECONDS_PER_DOCUMENT = 1.0


def short(value, length):
    """Return a text (or a list, joined with "; ") cut to `length` characters, "" when missing."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    if not isinstance(value, str):
        value = "; ".join(str(v) for v in value)
    value = " ".join(value.split())
    return value if len(value) <= length else value[:length] + "..."


def repeated_titles(latest):
    """
    Check 3: count the groups of latest records with the same datasource and
    the same title (lower case, trimmed), print the tables and 10 groups,
    and write every group to a CSV.
    """
    latest = latest[latest["title"].notna()].copy()
    latest["title_key"] = latest["title"].str.strip().str.lower()
    latest["ds"] = latest["datasource"].str.replace("urn:node:", "", regex=False)
    sizes = latest.groupby(["ds", "title_key"])["id"].transform("size")
    repeated = latest[sizes > 1].copy()
    repeated["group_size"] = sizes[sizes > 1]
    groups = repeated.groupby(["ds", "title_key"]).size()
    print("3. REPEATED TITLES AMONG LATEST RECORDS (same datasource, same title)")
    print(pd.DataFrame([
        {"what": "latest records with a title", "value": len(latest)},
        {"what": "groups with more than one record", "value": len(groups)},
        {"what": "records in those groups", "value": int(groups.sum())},
        {"what": "records beyond one per group", "value": int(groups.sum() - len(groups))},
        {"what": "largest group", "value": int(groups.max()) if len(groups) else 0},
    ]).to_string(index=False))
    by_ds = pd.DataFrame({"groups": groups.groupby(level=0).size(), "records": groups.groupby(level=0).sum(),
                          "latest_records": latest["ds"].value_counts()}).dropna()
    by_ds = by_ds.astype(int).sort_values("records", ascending=False)
    by_ds["share_of_latest_%"] = (100 * by_ds["records"] / by_ds["latest_records"]).round(1)
    print("\n   by datasource (top 10):")
    print(by_ds.head(10).to_string())
    print("\n   group sizes:", groups.value_counts().sort_index().head(8).to_dict())
    print("   largest groups:")
    print(groups.sort_values(ascending=False).head(5).to_string())

    os.makedirs(OUT_DIR, exist_ok=True)
    keep = repeated.sort_values(["ds", "title_key", "dateUploaded"])
    keep = keep.assign(origin_short=keep["origin"].map(lambda v: short(v, 120)),
                       abstract_100=keep["abstract"].map(lambda v: short(v, 100)))
    keep[["ds", "title_key", "group_size", "id", "dateUploaded", "origin_short", "abstract_100"]].to_csv(
        os.path.join(OUT_DIR, "3_repeated_title_groups.csv"), index=False, encoding="utf-8-sig")
    by_ds.rename_axis("datasource").reset_index().to_csv(
        os.path.join(OUT_DIR, "3_repeated_titles_by_datasource.csv"), index=False, encoding="utf-8-sig")

    # Ten groups to read: one from each of the ten datasources with the most
    # repeated records, picked with a fixed seed, so the sample is not all
    # PANGAEA.
    shown = []
    for ds in by_ds.head(GROUPS_TO_SHOW).index:
        keys = groups.loc[ds].sample(1, random_state=7).index
        shown.append((ds, keys[0]))
    print("\n   ten groups to read:")
    rows = []
    for number, (ds, key) in enumerate(shown, start=1):
        members = keep[(keep["ds"] == ds) & (keep["title_key"] == key)]
        print(f"\n   [{number}] {ds}: \"{short(members['title'].iloc[0], 110)}\" ({len(members)} records)")
        for r in members.head(6).itertuples():
            print(f"       {short(r.id, 70)} | {r.dateUploaded[:10]} | {short(r.origin, 50)} | {r.abstract_100}")
            rows.append({"group": number, "ds": ds, "title": members["title"].iloc[0], "id": r.id,
                         "dateUploaded": r.dateUploaded, "origin": short(r.origin, 200), "abstract_100": r.abstract_100})
        if len(members) > 6:
            print(f"       ... and {len(members) - 6} more")
    pd.DataFrame(rows).to_csv(os.path.join(OUT_DIR, "3_ten_groups.csv"), index=False, encoding="utf-8-sig")


def multi_box_documents(latest):
    """
    Check 4: latest records with more than one geohash_9 value that the BC
    text rules do not catch, and how many of them have a box centre near BC
    (6 and 15 degrees around BC's rectangle) or anywhere (rule c, every one).
    """
    several = latest[latest["geohash_9"].map(lambda v: v is not None and len(v) > 1)].copy()
    bc_re, places_re = bc_text_matchers()
    several["caught"] = [caught_by_bc_text(text_of(t, a, k), bc_re, places_re)
                         for t, a, k in zip(several["title"], several["abstract"], several["keywords"])]
    rest = several[~several["caught"]]
    centres = rest["geohash_9"].map(lambda codes: [geohash_centre(c) for c in codes])
    rows = []
    for name, widen in list(WIDENINGS.items()) + [("c_anywhere", None)]:
        count = len(rest) if widen is None else int(centres.map(
            lambda pts, w=widen: any(near_bc(la, lo, w) for la, lo in pts)).sum())
        rows.append({"rule": name, "documents_to_download": count,
                     "minutes_at_1_per_second": round(count * SECONDS_PER_DOCUMENT / 60, 1)})
    write_multibox_list(rest)
    print("\n4. MULTI-BOX RECORDS AMONG LATEST RECORDS")
    print(f"   latest records with more than one geohash_9 value: {len(several):,}; "
          f"caught by the BC text rules: {int(several['caught'].sum()):,}; not caught: {len(rest):,}")
    table = pd.DataFrame(rows)
    print(table.to_string(index=False))
    table.to_csv(os.path.join(OUT_DIR, "4_documents_to_download.csv"), index=False, encoding="utf-8-sig")


def write_multibox_list(rest):
    """
    Write the ids of the multi-box latest records the BC text rules do not
    catch (the documents rule c would download) to MULTIBOX_CSV, with their
    datasource and number of geohash_9 values. Stops if git does not ignore
    the file.
    """
    result = subprocess.run(["git", "check-ignore", "-q", MULTIBOX_CSV], cwd=BACKEND)
    if result.returncode != 0:
        sys.exit(f"STOPPED: {MULTIBOX_CSV} is not ignored by git (or git could not tell); nothing was written")
    out = pd.DataFrame({"id": rest["id"], "datasource": rest["datasource"].str.replace("urn:node:", "", regex=False),
                        "geohash_9_values": rest["geohash_9"].map(len)})
    out.sort_values(["datasource", "id"]).to_csv(MULTIBOX_CSV, index=False, encoding="utf-8")
    print(f"   wrote {len(out):,} ids -> {MULTIBOX_CSV}")


def main():
    """Read the latest records of the marked harvest and run check 4 (and check 3 with --titles)."""
    parser = argparse.ArgumentParser(description="Read-only counts on the latest DataONE records.")
    parser.add_argument("--titles", action="store_true", help="also count repeated titles (check 3)")
    args = parser.parse_args()
    columns = ["id", "datasource", "title", "abstract", "keywords", "origin", "dateUploaded", "geohash_9"]
    table = pq.read_table(MARKED, columns=columns + ["version_status"])
    latest = table.filter(pc.equal(table.column("version_status"), "latest")).select(columns).to_pandas()
    print(f"latest records: {len(latest):,}\n")
    os.makedirs(OUT_DIR, exist_ok=True)
    if args.titles:
        repeated_titles(latest)
    multi_box_documents(latest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
