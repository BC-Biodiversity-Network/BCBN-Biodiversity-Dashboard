"""
export_dataset_source_checks.py

Writes backend/data/api/dataset_source_checks.parquet: what the November
2026 source check found for each shown Lunaris dataset it checked, for the
dataset page and the Access filter. One row per checked dataset; a field is
filled only when it was actually checked (null otherwise).

What it reads (read only):
  --table     the source check's one-row-per-dataset table
              (Results/lunaris_source_check_2026-11/source_check_table.csv)
  --not-checked  its list of the datasets it could not check (not_checked.csv)
  --hand      Lucia's hand checks (hand_check_2026-11-02.csv): the 24
              datasets the API check could not reach (Polar Data Catalogue,
              Abacus, cIRcle) and the 28 FRDR datasets, which the API check
              could only read through DataCite. A hand check replaces the
              API check's row for the same dataset.
  --datasets  lunaris_datasets.parquet, for the shown ids, links and catalogue
              names

The columns:
  dataset_id         the shown dataset's id
  catalogue_checked  the readable name of the catalogue checked: the
                     dataset's own catalogue, or, for a CanWIN copy of an
                     Open Government Portal record, the Open Government Portal
  checked_on         the day of the check (2026-11-02)
  checked_how        "source API", "DataCite" (FRDR and DataStream, whose
                     own sites were not asked) or "by hand"
  access             the source's access in one of ACCESS_VALUES, or null
                     when the source does not say (or was not asked)
  access_until       the date for "embargoed" and "some_files_locked", else
                     null
  access_note        what the source gave, in its own words where there is
                     one (for example "download_audience Government")
  access_matches_record  whether the source agrees with the record's own
                     access right (open with open; any not-open value with
                     restricted); null when either is missing
  licences           the source's licences, as {name, uri} like the
                     record's own rights entries, or null when not checked
                     or the source gave none. Zenodo's API gives only a
                     licence id (such as "cc-by-4.0"); when Zenodo's own
                     DataCite record of the dataset names the same licence,
                     its name and link are used instead (else the id)
  removed            true when the source no longer has the dataset (gone,
                     deleted or private), false when it does, null when not
                     checked

Run, from backend/:
    python pipeline/lunaris/export_dataset_source_checks.py
It stops if the output file exists already (move it aside first).
"""

import argparse
import html
import os
import re
import sys

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

RESULTS = "/Users/lucia/Desktop/BCBN/Results/lunaris_source_check_2026-11"
DEFAULT_TABLE = os.path.join(RESULTS, "source_check_table.csv")
DEFAULT_NOT_CHECKED = os.path.join(RESULTS, "not_checked.csv")
DEFAULT_HAND = os.path.join(RESULTS, "hand_check_2026-11-02.csv")
DEFAULT_DATASETS = "data/api/lunaris_datasets.parquet"
DEFAULT_OUT = "data/api/dataset_source_checks.parquet"
CHECKED_ON = "2026-11-02"

# The access values, and whether each counts as open for the Access filter
# (every other value counts as restricted). db.py checks the file against
# the same list.
ACCESS_VALUES = {
    "open": True, "restricted": False, "on_request": False, "embargoed": False, "not_downloadable": False,
    "government_only": False, "government_bceid_only": False, "named_users_only": False, "ubc_only": False,
    "limited": False, "no_files": False, "some_files_locked": False,
}
# The values with a date (access_until).
DATED = {"embargoed", "some_files_locked"}

# The source check's own access words, and the BC Data Catalogue's
# download_audience values behind its "restricted".
CHECK_ACCESS = {"open": "open", "restricted": "restricted", "request only": "on_request",
                "embargoed": "embargoed", "not downloadable": "not_downloadable", "no files": "no_files",
                "some files locked": "some_files_locked"}
BC_AUDIENCE = {"Government": "government_only", "Government and Business BCeID": "government_bceid_only",
               "Named users": "named_users_only"}

# Lucia's hand checks: the access each catalogue's rows get. Polar Data
# Catalogue records say "Access Constraints: Public" or "Limited"; FRDR
# pages (every FRDR collection's name ends in "(FRDR)") start "Open:" when
# they open without logging in, as all 28 did (2 of them had come out of an
# embargo, which their notes date).
HAND_ACCESS = {"Abacus (Statistics Canada public files)": "ubc_only", "Abacus Open Data": "ubc_only",
               "cIRcle (UBC Library)": "open"}
POLAR = "Polar Data Catalogue"
POLAR_ACCESS = {"Public": "open", "Limited": "limited"}
FRDR_SUFFIX = "(FRDR)"

LICENCE = pa.struct([("name", pa.string()), ("uri", pa.string())])
SCHEMA = pa.schema([
    ("dataset_id", pa.string()), ("catalogue_checked", pa.string()), ("checked_on", pa.string()),
    ("checked_how", pa.string()), ("access", pa.string()), ("access_until", pa.string()),
    ("access_note", pa.string()), ("access_matches_record", pa.bool_()), ("licences", pa.list_(LICENCE)),
    ("removed", pa.bool_()),
])
ACCESS_PREFIX = "info:eu-repo/semantics/"
URL = re.compile(r"https?://[^\s()<>'\"|]+")
OPEN_CANADA_NAME = "Open Government Portal (Government of Canada)"


def record_access(licences):
    """Return "open" or "restricted" from a record's own access right, or None when it has none."""
    codes = {entry["uri"][len(ACCESS_PREFIX):] for entry in licences
             if not entry["name"] and (entry["uri"] or "").startswith(ACCESS_PREFIX)}
    if "openAccess" in codes:
        return "open"
    return "restricted" if "restrictedAccess" in codes else None


def matches_record(record, access):
    """Return whether a checked access agrees with the record's: open with open, any other value with restricted."""
    if record is None or access is None:
        return None
    return ACCESS_VALUES[access] == (record == "open")


def read_licence_text(text):
    """
    Return the licences of one source check cell ("name uri | name uri", as
    the check wrote them, HTML already removed) as [{name, uri}]: the uri is
    the first web address of each part, the name the rest of its text.
    """
    licences = []
    for part in (text or "").split(" | "):
        part = html.unescape(part).strip()
        if not part:
            continue
        links = URL.findall(part)
        name = " ".join(URL.sub(" ", part).split())
        licences.append({"name": name or None, "uri": links[0] if links else None})
    return licences


def zenodo_licences(row):
    """Return the licence of Zenodo's own DataCite record when it is the one Zenodo's id names, else the id."""
    own = read_licence_text(row["source_licence"])
    datacite = read_licence_text(row["datacite_licence"])
    if len(own) == 1 and len(datacite) == 1 and same_licence(own[0]["name"] or "", datacite[0]):
        return datacite
    return own


def same_licence(licence_id, entry):
    """
    Return True when a DataCite licence entry is the licence a Zenodo id
    names: a Creative Commons id ("cc-by-nc-sa-4.0") must have its path
    (creativecommons.org/licenses/by-nc-sa/4.0) in the link; any other id
    ("mit-license", "eupl-1.2") its first word in the name or link.
    """
    text = f"{entry['name'] or ''} {entry['uri'] or ''}".lower()
    creative = re.match(r"^cc-([a-z-]+)-(\d\.\d)$", licence_id)
    if creative:
        return f"creativecommons.org/licenses/{creative.group(1)}/{creative.group(2)}" in text
    if licence_id.startswith("cc"):
        return False
    return bool(licence_id) and licence_id.split("-")[0] in text


def api_rows(table, shown):
    """Return the rows for the datasets the API check reached (every row of the table not in not_checked)."""
    rows = []
    for _, row in table.iterrows():
        source = row["source_access"]
        access = CHECK_ACCESS.get(source)
        note = None
        if source == "restricted" and row["catalogue"] == "BC Data Catalogue":
            audience = re.search(r"download_audience ([^,]+)", row["note"])
            note = f"download_audience {audience.group(1)}" if audience else None
            access = BC_AUDIENCE.get(audience.group(1), "restricted") if audience else "restricted"
        elif access is not None and row["note"]:
            note = row["note"]
        until = None
        if access in DATED:
            found = re.match(r"^until (\d{4}-\d{2}-\d{2})$", row["source_embargo"])
            until = found.group(1) if found else None
        if row["source_licence"] in ("", "(not given)"):
            licences = None
        elif row["platform"] == "Zenodo":
            licences = zenodo_licences(row)
        else:
            licences = read_licence_text(row["source_licence"])
        removed = {"yes": False, "no": True}.get(row["source_exists"])
        on_open_canada = row["checked_with"].startswith("open.canada.ca")
        how = "DataCite" if row["checked_with"].startswith("DataCite only") else "source API"
        rows.append({
            "dataset_id": row["id"], "catalogue_checked": OPEN_CANADA_NAME if on_open_canada else row["catalogue"],
            "checked_on": CHECKED_ON, "checked_how": how, "access": access, "access_until": until,
            "access_note": note if access else None,
            "access_matches_record": matches_record(record_access(shown.at[row["id"], "licences"]), access),
            "licences": licences, "removed": removed,
        })
    return rows


def hand_access(row):
    """Return the access a hand check found: by catalogue, from Polar's access constraint, or open for FRDR."""
    if row["catalogue"] == POLAR:
        return POLAR_ACCESS[re.match(r"^(\w+)", row["source_access"]).group(1)]
    if row["catalogue"].endswith(FRDR_SUFFIX):
        if not row["source_access"].startswith("Open"):
            raise SystemExit(f"ERROR: an FRDR hand check that does not say Open: {row['link_checked']}")
        return "open"
    return HAND_ACCESS[row["catalogue"]]


def hand_licence(text):
    """
    Return a hand check's licence as {name, uri}: the first web address in
    it, and the text before a closing note in brackets ("(termsOfUse in
    Dataverse JSON)"), unless the brackets hold the licence's short name
    ("(CC BY 4.0)").
    """
    links = URL.findall(text)
    note = re.search(r"\s*\(([^()]*)\)\s*$", text)
    name = text[:note.start()] if note and not note.group(1).startswith("CC") else text
    return {"name": name.strip(), "uri": links[0] if links else None}


def hand_rows(hand, shown):
    """Return the rows of Lucia's hand checks, matched to the shown datasets by their link."""
    by_link = {link.replace("http://", "https://"): rid for rid, link in shown["link"].items()}
    rows = []
    for _, row in hand.iterrows():
        rid = by_link.get(row["link_checked"].replace("http://", "https://"))
        if rid is None:
            raise SystemExit(f"ERROR: no shown dataset has the link {row['link_checked']}")
        access = hand_access(row)
        rows.append({
            "dataset_id": rid, "catalogue_checked": shown.at[rid, "source_name"], "checked_on": CHECKED_ON,
            "checked_how": "by hand", "access": access, "access_until": None, "access_note": row["source_access"],
            "access_matches_record": matches_record(record_access(shown.at[rid, "licences"]), access),
            "licences": [hand_licence(row["source_licence"])],
            "removed": not row["still_exists"].lower().startswith("yes"),
        })
    return rows


def build(table, not_checked, hand, shown):
    """
    Return the output rows: the API check's checked datasets, each replaced
    by the hand check of the same dataset when there is one, then the hand
    checks of datasets the API check did not reach.
    """
    table = table[~table["id"].isin(not_checked["id"])]
    unknown = sorted(set(table["id"]) - set(shown.index))
    if unknown:
        raise SystemExit(f"ERROR: the table has datasets the site does not show, for example {unknown[:3]}")
    by_hand = hand_rows(hand, shown)
    ids = [row["dataset_id"] for row in by_hand]
    if len(ids) != len(set(ids)):
        raise SystemExit("ERROR: a dataset is checked twice by hand")
    return [row for row in api_rows(table, shown) if row["dataset_id"] not in set(ids)] + by_hand


def main():
    """Read the check's files and the shown datasets, and write the parquet file."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("--table", default=DEFAULT_TABLE)
    parser.add_argument("--not-checked", default=DEFAULT_NOT_CHECKED)
    parser.add_argument("--hand", default=DEFAULT_HAND)
    parser.add_argument("--datasets", default=DEFAULT_DATASETS)
    parser.add_argument("--out", default=DEFAULT_OUT)
    args = parser.parse_args()
    if os.path.exists(args.out):
        raise SystemExit(f"ERROR: {args.out} already exists; move it aside first")

    read = lambda path: pd.read_csv(path, dtype=str, keep_default_na=False)  # noqa: E731
    shown = pd.read_parquet(args.datasets, columns=["id", "link", "source_name", "licences"]).set_index("id")
    rows = build(read(args.table), read(args.not_checked), read(args.hand), shown)
    pq.write_table(pa.Table.from_pylist(rows, schema=SCHEMA), args.out)

    frame = pd.DataFrame(rows)
    print(f"wrote {args.out}: {len(frame):,} of {len(shown):,} shown datasets checked")
    print("access:", frame["access"].value_counts(dropna=False).to_dict())
    print("access differs from the record:", (frame["access_matches_record"] == False).sum())  # noqa: E712
    print("removed:", int(frame["removed"].sum()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
