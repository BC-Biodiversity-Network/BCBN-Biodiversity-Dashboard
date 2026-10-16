"""
Build one table of BC conservation statuses per GBIF species, for the
front end to show on a species page.

BCSEE can list one GBIF species more than once: the species itself, its
subspecies, varieties or populations, and sometimes two BCSEE species that
GBIF treats as one. Each BCSEE entry keeps its own status. This script
gathers every entry that points to the same GBIF species into one row, as
a list of status entries the front end can lay out and style.

Inputs, all in --data-dir:
    bcsee_gbif_match.parquet   from match_bcsee_gbif.py. Only rows with
                               join_ok are used, joined on gbif_species_key.
    bcsee_status.parquet       from pull_bcsee.py, for bc_list, english_name
                               and the classification level.
    bcsee_raw/gbif_match_answers_v2.jsonl
                               GBIF's saved answers from match_bcsee_gbif.py,
                               for the GBIF names. Names not found there are
                               looked up by key with GBIF's API, and those
                               answers are saved in
                               bcsee_raw/gbif_key_answers.jsonl so each key
                               is only asked about once.
    species_key_merge.csv      optional, only with --key-merge: the species
                               key merge from make_species_key_merge.py
                               (old_key, kept_key, ...). Read with the same
                               one-step check as build_species_tables.py.
    bcsee_gbif_overrides.csv   optional, columns element_code and
                               gbif_species_key. Written by a person after
                               checking a match by hand. Applied before
                               anything else: the element_code uses the key
                               in the file, and is used even if its join_ok
                               is false.

Input next to this script (tracked in git, not in --data-dir):
    bcsee_extra_keys.csv       hand-checked rows saying "this BCSEE entry
                               also applies to this other key", with columns
                               element_code, extra_gbif_species_key,
                               bcsee_scientific_name,
                               extra_gbif_scientific_name, reason,
                               checked_by and checked_date. Required; it may
                               hold only the header. See "Extra keys" below.
                               --extra-keys reads another file instead.

Rules:
  1. Entries with a shown status (Red, Blue, Yellow, Exotic) and entries
     that are in BCSEE without one (Not Reviewed, Unknown, Accidental, No
     Status) are kept, so the site can tell "in BCSEE but no status" from
     "not in BCSEE". Extinct entries are left out, as the GBIF cleaning
     leaves out extinct taxa, and so is the one Other entry, until it is
     decided; the run lists both. A species whose entries are all without
     a shown status gets a row with an empty statuses list, and every
     entry keeps its own BCSEE value as its status, so a Not Reviewed
     subspecies is never mixed up with its species' Yellow.
  2. Each BCSEE entry becomes one status entry:
       status            the bc_list value
       scientific_label  GBIF's name for the entry when GBIF matched the
                         entry to its own entry. When GBIF only had a
                         higher rank, such as the species for a subspecies
                         or population it does not list, the BCSEE
                         scientific name. A below-species entry that GBIF
                         matched to a species counts as higher rank even
                         when GBIF calls it EXACT (this happens for
                         "Thymallus arcticus - South Beringia lineage").
                         An overridden entry whose key was changed gets
                         GBIF's name for the new key.
                         GBIF's names are written the way BCSEE writes
                         them: "ssp." or "var." before the last word for
                         plants and fungi (GBIF's short names leave it
                         out), and "x" for a hybrid ("Mentha x piperita",
                         "x Elyhordeum macounii").
       name_source       "GBIF" or "BCSEE", where scientific_label came from
       common_label      the whole BCSEE english_name, or empty
       classification_level
                         BCSEE's own level for the entry: Species,
                         Subspecies, Variety or Population. Unlike
                         is_species_level, it is kept as it is when BCSEE
                         lists separate species on one GBIF species, so the
                         page can tell a separate BCSEE species from a
                         subspecies.
       is_species_level  true for the species-level BCSEE entry, so the
                         front end can show a "species overall" tag. Only
                         set when the GBIF species has exactly one
                         species-level entry: with two or more, BCSEE
                         treats them as separate species and none of them
                         is the status of the species overall.
       is_gbif_synonym   true if GBIF calls the matched name a synonym. Only
                         set when scientific_label is GBIF's name, because
                         the tag is shown next to that label, and not when
                         it is the same as species_name.
       element_codes     the BCSEE element_codes behind the entry
  3. Entries that end up the same (status, scientific_label and
     common_label) are merged into one, keeping all their element_codes.
     BCSEE lists Tricholoma zelleri twice, for example, both Yellow.
  4. Order: species-level BCSEE entries first, then Red, Blue, Yellow,
     Exotic, then Not Reviewed, Unknown, Accidental, No Status (any other
     value last). Ties are broken by scientific_label, so every run gives
     the same order.

The species key merge (--key-merge, off by default):
  The site shows one species card per kept_key of the merge, and none for
  an old_key. So, right after the overrides are applied, every entry whose
  key is an old_key is moved to its kept_key; this covers matched keys and
  overridden keys alike. The key before the merge is kept as
  matched_species_key, so every move can be traced. A kept key that gains
  entries is grouped as usual: entries that end up the same are combined by
  rule 3, keeping all their element_codes, and entries with different
  statuses are kept side by side, never one picked over the other (each is
  BCSEE's own assessment). The run prints the entries moved, the kept keys
  that gained entries and any kept key with different statuses, for review.

  The match and the merge list must use the same Catalogue of Life release
  (both 315557 now). Nothing records the release the match used, so that is
  not checked yet: rerun match_bcsee_gbif.py and make_species_key_merge.py
  together whenever the GBIF download moves to a new release.

Extra keys (bcsee_extra_keys.csv):
  GBIF matches each BCSEE entry to one key. Sometimes the same species also
  has a second key under another name, with most of the records on it. (The
  first case, Cooper's Hawk, was BCSEE's Accipiter cooperii matched to VP2T6
  while most records were on Astur cooperii, CVW5J; the two keys are now
  merged by the species key merge instead, so the list is empty.) Each row
  of bcsee_extra_keys.csv
  says that one BCSEE entry also applies to one more key, after a person has
  checked it. The keys are never merged: the entry stays on its own key and
  a copy is added on the extra key, after the overrides and the key merge.
  The copy keeps the entry's own name and matched_species_keys, and has
  also_applied_from_key set to the key it was copied from (empty on every
  other entry), so it can always be told apart. Before anything is applied,
  the run stops with every problem listed if a row's element_code is not a
  BCSEE entry that is shown, its bcsee_scientific_name is not BCSEE's name
  for it, the entry is not at species level, the extra key is the entry's
  own key or an old key of the merge, the extra key already has a BCSEE
  entry (or two rows point to it), or GBIF's name for the extra key is not
  extra_gbif_scientific_name. Every row applied is printed with both names.

Output, one row per GBIF species:
    bcsee_status_by_gbif_species.parquet
        gbif_species_key, species_name (GBIF's accepted name, written like
        scientific_label), status_display (plain text for checking by eye,
        not for the front end), has_multiple (more than one entry shown),
        has_separate_bcsee_species (two or more species-level BCSEE
        entries, so there is no overall BC status), statuses (the distinct
        shown statuses, Red, Blue, Yellow or Exotic, in entry order; empty
        for a species in BCSEE without one), element_codes (every BCSEE element_code,
        in entry order), entries (the list of status entries; each also
        has matched_species_keys, the key or keys its element_codes were
        matched to before the key merge, and also_applied_from_key, see
        "Extra keys"), matched_species_keys (every such
        key on the row), and where the statuses come from, for the front end
        to show next to every status: status_source ("BC Species and
        Ecosystems Explorer (BCSEE)"), status_source_date (the date the
        province made the BCSEE export) and status_pulled_on (the date
        pull_bcsee.py downloaded it)
    bcsee_status_by_gbif_species.csv
        the same table to open in a spreadsheet. The lists are written as
        text: statuses, element_codes and the entries in status_display
        joined with " | " (some english_names contain ";"), entries as JSON.

The script only reads its inputs and writes its two outputs and its own
answers file, so it is safe to run again at any time.

Usage:
    python build_bcsee_status_display.py
    python build_bcsee_status_display.py --data-dir ~/Desktop/BCBN/Results
    python build_bcsee_status_display.py --data-dir ~/bcbn/data \
        --key-merge ~/bcbn/data/gbif_tables/species_key_merge.csv --col-release 315557

--col-release is only needed when some GBIF names are not in the saved
answers yet. Before looking any up, the run checks that GBIF's Catalogue of
Life checklist (CHECKLIST_KEY) still points at that release, and stops
without asking or writing anything if it does not.
"""

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import duckdb
import pandas as pd
import requests


# The species key merge list is read by the same function as in
# build_species_tables.py, so both apply exactly the same one-step check.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gbif"))
from build_species_tables import read_key_merge  # noqa: E402

# The label the front end shows next to every status, so it is clear the
# statuses are the province's, not BCBN's.
STATUS_SOURCE = "BC Species and Ecosystems Explorer (BCSEE)"

MATCH_URL = "https://api.gbif.org/v2/species/match"

# The Catalogue of Life checklist used by match_bcsee_gbif.py. Used here
# to look up a GBIF key that is not in the saved answers.
CHECKLIST_KEY = "7ddf754f-d193-4cc9-b351-99906754a03b"
HEADERS = {"User-Agent": "BCBN-Dashboard (UBC Biodiversity Research Centre)"}

# How many key lookups to have waiting for GBIF at the same time, and how
# many times to try one again after a network error, waiting a little
# longer (in seconds) before each new try. Same as in match_bcsee_gbif.py.
WORKERS = 4
RETRIES = 3
WAIT = 2

# The statuses shown, in the order they are listed.
SHOWN_STATUSES = ["Red", "Blue", "Yellow", "Exotic"]

# BCSEE values that mean the species is in BCSEE but has no shown status.
# Their entries are kept (rule 1) and listed after the shown statuses.
NO_STATUS_VALUES = ["Not Reviewed", "Unknown", "Accidental", "No Status"]

# Every BCSEE value that is kept, in the order entries are listed. Extinct
# and Other are not here, so their entries are left out.
KEPT_VALUES = SHOWN_STATUSES + NO_STATUS_VALUES

# GBIF statuses that mean the matched name is a synonym.
SYNONYM_STATUSES = {"SYNONYM", "AMBIGUOUS_SYNONYM", "HOMOTYPIC_SYNONYM",
                    "HETEROTYPIC_SYNONYM", "PROPARTE_SYNONYM"}

# The rank markers BCSEE writes in plant and fungus names, by GBIF rank.
# Animal names have none, so they are left as GBIF writes them.
RANK_MARKERS = {"SUBSPECIES": "ssp.", "VARIETY": "var.", "FORM": "f."}
MARKED_KINGDOMS = {"Plantae", "Fungi"}

OUTPUT_NAME = "bcsee_status_by_gbif_species"

# The hand-checked extra keys, kept in git next to this script.
EXTRA_KEYS_FILE = Path(__file__).resolve().parent / "bcsee_extra_keys.csv"
EXTRA_KEY_COLUMNS = ["element_code", "extra_gbif_species_key", "bcsee_scientific_name",
                     "extra_gbif_scientific_name", "reason", "checked_by", "checked_date"]


def read_overrides(path, match):
    """
    Read the hand-checked keys, if the file exists.

    Returns a mapping from element_code to gbif_species_key, empty if there
    is no file. Stops with an error if the file is malformed, names an
    element_code twice or one that is not in the match table, or leaves a
    key blank, rather than guessing what was meant.
    """
    if not path.exists():
        print(f"  no {path.name}, using the matched keys as they are")
        return {}
    overrides = pd.read_csv(path, dtype=str, keep_default_na=False)
    missing = {"element_code", "gbif_species_key"} - set(overrides.columns)
    if missing:
        raise ValueError(f"{path.name} is missing the column(s) {sorted(missing)}")
    overrides = overrides.apply(lambda col: col.str.strip())
    problems = []
    twice = overrides["element_code"][overrides["element_code"].duplicated()]
    if len(twice):
        problems.append(f"element_codes listed twice: {sorted(set(twice))}")
    unknown = set(overrides["element_code"]) - set(match["element_code"])
    if unknown:
        problems.append(f"element_codes not in the match table: {sorted(unknown)}")
    blank = overrides.loc[overrides["gbif_species_key"] == "", "element_code"]
    if len(blank):
        problems.append(f"element_codes with a blank key: {sorted(blank)}")
    if problems:
        raise ValueError(f"{path.name}: " + "; ".join(problems))
    print(f"  {len(overrides):,} overrides read from {path.name}")
    return dict(zip(overrides["element_code"], overrides["gbif_species_key"]))


def read_entries(data, merge_pairs=None):
    """
    Read the BCSEE entries that can be shown, one row per element_code.

    Applies the overrides, then the species key merge (merge_pairs, from
    read_key_merge, or None for no merge), keeps rows with join_ok or an
    override, adds
    bc_list, english_name and the classification level from
    bcsee_status.parquet, and keeps only the values in KEPT_VALUES. The
    entries left out (Extinct, Other) are printed, so they can be checked.
    """
    con = duckdb.connect()
    match = con.execute(
        f"SELECT element_code, scientific_name, gbif_species_key, gbif_usage_key, "
        f"gbif_match_type, gbif_rank, gbif_status, join_ok "
        f"FROM '{data / 'bcsee_gbif_match.parquet'}'").df()
    status = con.execute(
        f"SELECT element_code, bc_list, english_name, classification_level "
        f"FROM '{data / 'bcsee_status.parquet'}'").df()
    con.close()

    overrides = read_overrides(data / "bcsee_gbif_overrides.csv", match)
    match["overridden"] = match["element_code"].isin(overrides)
    match["key_changed"] = False
    if overrides:
        new_key = match["element_code"].map(overrides)
        changed = match["overridden"] & (new_key != match["gbif_species_key"])
        match["key_changed"] = changed
        match.loc[match["overridden"], "gbif_species_key"] = new_key[match["overridden"]]
        print(f"  {int(changed.sum()):,} of them change the matched key, "
              f"{int((match['overridden'] & ~match['join_ok']).sum()):,} "
              f"bring in an entry without join_ok")

    # The species key merge, after the overrides so it covers both: an entry
    # on an old_key moves to its kept_key, the key with a species card.
    # matched_species_key keeps the key before the merge.
    match["matched_species_key"] = match["gbif_species_key"]
    if merge_pairs:
        kept_for = {old: kept for old, kept, _ in merge_pairs}
        moved = match["gbif_species_key"].isin(kept_for)
        match.loc[moved, "gbif_species_key"] = match.loc[moved, "gbif_species_key"].map(kept_for)
        joinable = moved & (match["join_ok"] | match["overridden"])
        print(f"  key merge: {len(kept_for):,} pairs; {int(moved.sum()):,} matched entries "
              f"were on an old key, {int(joinable.sum()):,} of them joinable, now on the kept key")

    used = match[match["join_ok"] | match["overridden"]]
    rows = used.merge(status, on="element_code", how="left", indicator=True)
    lost = rows.loc[rows["_merge"] == "left_only", "element_code"]
    if len(lost):
        raise ValueError(f"{len(lost)} element_codes are not in bcsee_status.parquet, "
                         f"for example {list(lost[:5])}. Rerun pull_bcsee.py and "
                         f"match_bcsee_gbif.py so the two files agree.")
    rows = rows.drop(columns="_merge")
    kept = rows[rows["bc_list"].isin(KEPT_VALUES)].reset_index(drop=True)
    n_shown = int(kept["bc_list"].isin(SHOWN_STATUSES).sum())
    print(f"  {len(used):,} BCSEE entries can be joined: {n_shown:,} with a shown "
          f"status ({', '.join(SHOWN_STATUSES)}), {len(kept) - n_shown:,} in BCSEE "
          f"without one ({', '.join(NO_STATUS_VALUES)})")
    left_out = rows[~rows["bc_list"].isin(KEPT_VALUES)]
    print(f"  {len(left_out):,} entries left out (their BCSEE value is not kept):")
    for _, r in left_out.iterrows():
        print(f"    {r['element_code']} {r['scientific_name']} ({r['bc_list']}), "
              f"key {r['gbif_species_key']}")
    return kept


def read_extra_keys(path):
    """
    Read bcsee_extra_keys.csv (see "Extra keys" at the top).

    Returns the rows as a table of text. Stops with an error if the file is
    missing, lacks a column, leaves a value blank or lists the same
    element_code and key twice, rather than guessing what was meant. A file
    with only the header gives an empty table.
    """
    if not path.exists():
        raise FileNotFoundError(f"{path} not found. It is kept in git next to this "
                                f"script; it may hold only the header line.")
    extra = pd.read_csv(path, dtype=str, keep_default_na=False)
    missing = [c for c in EXTRA_KEY_COLUMNS if c not in extra.columns]
    if missing:
        raise ValueError(f"{path.name} is missing the column(s) {missing}")
    extra = extra[EXTRA_KEY_COLUMNS].apply(lambda col: col.str.strip())
    problems = []
    blank = extra[(extra == "").any(axis=1)]
    for _, r in blank.iterrows():
        empty = [c for c in EXTRA_KEY_COLUMNS if r[c] == ""]
        problems.append(f"row for {r['element_code'] or '?'} has no {', '.join(empty)}")
    twice = extra[extra.duplicated(["element_code", "extra_gbif_species_key"])]
    for _, r in twice.iterrows():
        problems.append(f"{r['element_code']} -> {r['extra_gbif_species_key']} is listed twice")
    if problems:
        raise ValueError(f"{path.name}: " + "; ".join(problems))
    print(f"  {len(extra):,} extra keys read from {path.name}")
    return extra


def check_extra_keys(extra, kept, bcsee_names, old_keys):
    """
    Check the rows of bcsee_extra_keys.csv against the BCSEE entries, before
    any GBIF name is looked up.

    kept is the table from read_entries(), after the overrides and the key
    merge; bcsee_names maps every element_code in bcsee_status.parquet to
    BCSEE's scientific name; old_keys are the merged-away keys. Stops with
    every problem listed (see "Extra keys" at the top for the rules).
    """
    problems = []
    entries = kept.set_index("element_code")
    has_entry = set(kept["gbif_species_key"])
    targets = extra["extra_gbif_species_key"]
    for _, r in extra.iterrows():
        code, key = r["element_code"], r["extra_gbif_species_key"]
        if code not in bcsee_names:
            problems.append(f"{code} is not in bcsee_status.parquet")
            continue
        if bcsee_names[code] != r["bcsee_scientific_name"]:
            problems.append(f"{code} is {bcsee_names[code]} in BCSEE, "
                            f"not {r['bcsee_scientific_name']}")
        if code not in entries.index:
            problems.append(f"{code} is not shown on any key (not joinable, or its "
                            f"BCSEE value is not kept)")
            continue
        own = entries.loc[code]
        if own["classification_level"] != "Species":
            problems.append(f"{code} is a {own['classification_level']} entry; only "
                            f"species-level entries can be added to another key")
        if key == own["gbif_species_key"]:
            problems.append(f"{code} is already on {key}")
        elif key in has_entry:
            problems.append(f"{key} already has a BCSEE entry of its own; adding "
                            f"{code} could clash with it, so it was not added")
        if key in old_keys:
            problems.append(f"{key} is an old key of the species key merge and has "
                            f"no card; name its kept key instead")
        if (targets == key).sum() > 1:
            problems.append(f"{key} is the extra key of more than one row")
    if problems:
        raise ValueError("bcsee_extra_keys.csv: " + "; ".join(dict.fromkeys(problems)))


def check_extra_key_names(extra, names):
    """
    Check that GBIF's name for every extra key is the name written in the
    row (extra_gbif_scientific_name) and is a species, so a mistyped key
    that happens to exist is caught. names is the saved_names() mapping,
    with any missing names already looked up.
    """
    problems = []
    for _, r in extra.iterrows():
        key = r["extra_gbif_species_key"]
        name = names.get(key)
        if name is None:
            problems.append(f"GBIF has no name for {key}")
        elif name.get("canonicalName") != r["extra_gbif_scientific_name"]:
            problems.append(f"{key} is {name.get('canonicalName')} in GBIF, "
                            f"not {r['extra_gbif_scientific_name']}")
        elif name.get("rank") != "SPECIES":
            problems.append(f"{key} is a {name.get('rank')}, not a species")
    if problems:
        raise ValueError("bcsee_extra_keys.csv: " + "; ".join(problems))


def apply_extra_keys(kept, extra, names):
    """
    Add a copy of each listed BCSEE entry on its extra key, and return the
    new table. The entry itself stays on its own key: nothing is moved.
    also_applied_from_key is the key copied from on a copy, and empty on
    every other row. Prints every row applied, with both names.
    """
    kept = kept.copy()
    kept["also_applied_from_key"] = ""
    copies = []
    for _, r in extra.iterrows():
        own = kept[(kept["element_code"] == r["element_code"])
                   & (kept["also_applied_from_key"] == "")].iloc[0]
        copy = own.copy()
        copy["also_applied_from_key"] = own["gbif_species_key"]
        copy["gbif_species_key"] = r["extra_gbif_species_key"]
        copies.append(copy)
        print(f"  extra key: {own['element_code']} {own['scientific_name']} "
              f"({own['bc_list']}) on {own['gbif_species_key']} "
              f"{names[own['gbif_species_key']]['canonicalName']}, also on "
              f"{r['extra_gbif_species_key']} {r['extra_gbif_scientific_name']}")
    out = pd.concat([kept, pd.DataFrame(copies)], ignore_index=True) if copies else kept
    # The entry must still be on its own key: the list adds, it never moves.
    for _, r in extra.iterrows():
        left = out[(out["element_code"] == r["element_code"])
                   & (out["also_applied_from_key"] == "")]
        if len(left) != 1:
            raise ValueError(f"{r['element_code']} is no longer on its own key")
    return out


def read_jsonl(path):
    """
    Read a file of saved GBIF answers, one {"key", "answer"} per line.

    A line that cannot be read, which can happen if a run was stopped in
    the middle of writing it, is skipped.
    """
    if not path.exists():
        return []
    items = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                item = json.loads(line)
                items.append(item["answer"])
            except (json.JSONDecodeError, KeyError):
                continue
    return items


def kingdom_of(answer):
    """Return the kingdom in an answer's classification list, or None."""
    for item in answer.get("classification") or []:
        if item.get("rank") == "KINGDOM":
            return item.get("name")
    return None


def saved_names(match_path, key_path):
    """
    Collect every GBIF name in the saved answers, by GBIF key.

    Each name is GBIF's "usage" record (full name, short name, rank and
    status) with the kingdom added. The matched name of every answer is
    used, and for a synonym also the accepted name, which GBIF sends in
    "acceptedUsage". The classification list is not used, because its
    names leave out the hybrid sign.
    """
    names = {}
    for answer in read_jsonl(match_path) + read_jsonl(key_path):
        kingdom = kingdom_of(answer)
        for field in ("usage", "acceptedUsage"):
            usage = answer.get(field)
            if usage and usage.get("key"):
                names.setdefault(usage["key"], {**usage, "kingdom": kingdom})
    return names


def ask_key(key):
    """
    Look up one GBIF key with GBIF's API and return the whole answer.

    A network error or a server error is tried again a few times,
    waiting a little longer each time. Any
    other error, or an answer for a different key, stops the run.
    """
    for attempt in range(RETRIES + 1):
        try:
            reply = requests.get(MATCH_URL, headers=HEADERS, timeout=30,
                                 params={"usageKey": key, "checklistKey": CHECKLIST_KEY})
            if reply.status_code >= 500 or reply.status_code == 429:
                raise requests.ConnectionError(f"GBIF answered {reply.status_code}")
            reply.raise_for_status()
            break
        except (requests.ConnectionError, requests.Timeout):
            if attempt == RETRIES:
                raise
            time.sleep(WAIT * (attempt + 1))
    answer = reply.json()
    if (answer.get("usage") or {}).get("key") != key:
        raise ValueError(f"GBIF gave no name for key {key}")
    return answer


def release_problem(expected, current):
    """
    Say why GBIF names must not be looked up now, or return None if they may.

    CHECKLIST_KEY is GBIF's pointer to the Catalogue of Life, and GBIF moves
    it to each new release. Our keys and names come from one release (the
    one the GBIF download used), so a name looked up after GBIF has moved
    on could differ from the rest. expected is the ChecklistBank release
    given with --col-release (None if not given); current is what
    lookup_col_release() in request_gbif_download.py found GBIF using now
    (None if that lookup failed).
    """
    if expected is None:
        return ("--col-release was not given; pass the ChecklistBank release the "
                "rest of the data uses (315557 for the current download)")
    if current is None:
        return "could not find out which Catalogue of Life release GBIF uses now"
    if current["checklistbank_key"] != expected:
        return (f"GBIF now uses {current['alias']} (ChecklistBank "
                f"{current['checklistbank_key']}) for the Catalogue of Life, not "
                f"{expected}, so the names could differ from the rest of the data")
    return None


def add_missing_names(keys, names, key_path, expected_release):
    """
    Look up the keys that are not in the saved answers, add them to names,
    and save GBIF's answers so the next run does not ask again.

    Before asking anything, checks that GBIF still uses expected_release
    (see release_problem) and stops if not. Nothing is checked or asked
    when every name is already saved.
    """
    missing = sorted({k for k in keys if k not in names})
    if not missing:
        return

    # Imported here so the GBIF registry is only contacted when a lookup is
    # really needed. request_gbif_download.py asks GBIF with the same
    # checklist key (both take it from match_bcsee_gbif.py's value).
    from request_gbif_download import lookup_col_release
    problem = release_problem(expected_release,
                              lookup_col_release("before looking up GBIF names"))
    if problem:
        raise ValueError(f"{len(missing):,} GBIF names would be looked up, but {problem}. "
                         f"Nothing was looked up or written.")

    print(f"  Catalogue of Life release checked: GBIF uses {expected_release}")
    print(f"  looking up {len(missing):,} GBIF keys not in the saved answers")
    with ThreadPoolExecutor(WORKERS) as pool:
        answers = list(pool.map(ask_key, missing))
    with open(key_path, "a", encoding="utf-8") as fh:
        for key, answer in zip(missing, answers):
            fh.write(json.dumps({"key": key, "answer": answer}, ensure_ascii=False) + "\n")
            names[key] = {**answer["usage"], "kingdom": kingdom_of(answer)}


def written_like_bcsee(name):
    """
    Write one GBIF name record the way BCSEE writes names.

    Starts from GBIF's short name (canonicalName), adds "x" where GBIF's
    full name has the hybrid sign "×" (before the genus for a hybrid genus,
    otherwise after it), and for plants and fungi adds the rank marker
    ("ssp.", "var.", "f.") before the last word of a name below species.
    """
    words = name["canonicalName"].split()
    full = name.get("name") or ""
    if full.startswith("×"):
        words.insert(0, "x")
    elif "×" in full:
        words.insert(1, "x")
    marker = RANK_MARKERS.get(name.get("rank"))
    if marker and name.get("kingdom") in MARKED_KINGDOMS and len(words) >= 3:
        words.insert(len(words) - 1, marker)
    return " ".join(words)


def gbif_has_own_entry(row):
    """
    Say whether GBIF matched this BCSEE entry to an entry of its own.

    It has not when GBIF answered at a higher rank, or when a subspecies,
    variety or population came back as a species (GBIF can call that EXACT
    after dropping words it does not understand, such as "- South Beringia
    lineage").
    """
    if row["gbif_match_type"] == "HIGHERRANK":
        return False
    return (row["classification_level"] == "Species") == (row["gbif_rank"] == "SPECIES")


def describe(row, names):
    """
    Turn one BCSEE entry into one status entry, as the docstring at the top
    describes. Returns a dictionary of the entry's fields. is_gbif_synonym
    here is GBIF's view of the name only; group_by_species() clears it when
    the name is the species name itself.
    """
    if row["key_changed"]:
        # The overridden key itself, not the kept key it may have been
        # merged into, so the entry keeps its own name.
        name = names[row["matched_species_key"]]
    elif gbif_has_own_entry(row):
        name = names[row["gbif_usage_key"]]
    else:
        name = None
    english = row["english_name"]
    return {
        "status": row["bc_list"],
        "scientific_label": written_like_bcsee(name) if name else row["scientific_name"],
        "name_source": "GBIF" if name else "BCSEE",
        "common_label": english.strip() if isinstance(english, str) else "",
        "classification_level": row["classification_level"],
        "is_species_level": row["classification_level"] == "Species",
        "is_gbif_synonym": bool(name) and name.get("status") in SYNONYM_STATUSES,
        "matched_species_keys": [row["matched_species_key"]],
        # The key this entry was copied from by bcsee_extra_keys.csv, or empty.
        "also_applied_from_key": row.get("also_applied_from_key", "") or "",
        "element_code": row["element_code"],
    }


def merge_same(entries):
    """
    Merge entries of one GBIF species that show the same status,
    scientific_label and common_label, keeping all their element_codes.

    Returns the merged entries and a list of merges where the entries
    disagreed on name_source or a tag, for the report. When they disagree,
    a tag is kept if any of the merged entries has it.
    """
    merged, odd = {}, []
    for entry in entries:
        same = (entry["status"], entry["scientific_label"], entry["common_label"])
        if same not in merged:
            merged[same] = {**{k: v for k, v in entry.items() if k != "element_code"},
                            "matched_species_keys": list(entry["matched_species_keys"]),
                            "element_codes": [entry["element_code"]]}
            continue
        kept = merged[same]
        for field in ("name_source", "classification_level", "is_species_level",
                      "is_gbif_synonym"):
            if kept[field] != entry[field]:
                odd.append((kept["element_codes"][0], entry["element_code"], field))
        kept["is_species_level"] |= entry["is_species_level"]
        kept["is_gbif_synonym"] |= entry["is_gbif_synonym"]
        kept["element_codes"].append(entry["element_code"])
        kept["matched_species_keys"] += entry["matched_species_keys"]
    for entry in merged.values():
        entry["element_codes"].sort()
        entry["matched_species_keys"] = sorted(set(entry["matched_species_keys"]))
    return list(merged.values()), odd


def order_key(entry):
    """Sort key: species-level first, then by status in KEPT_VALUES order
    (shown statuses before the ones without a status), then by name. A
    status not in KEPT_VALUES sorts after all of them instead of failing."""
    status = entry["status"]
    rank = KEPT_VALUES.index(status) if status in KEPT_VALUES else len(KEPT_VALUES)
    return (not entry["is_species_level"],
            rank,
            entry["scientific_label"],
            entry["common_label"])


def display_text(entries, separator):
    """
    Write the entries as one line of plain text, for checking by eye:
    "Status: scientific_label (BCSEE name) [common_label]", joined with
    the separator given.
    """
    parts = []
    for e in entries:
        text = f"{e['status']}: {e['scientific_label']}"
        if e["name_source"] == "BCSEE":
            text += " (BCSEE name)"
        if e["common_label"]:
            text += f" [{e['common_label']}]"
        parts.append(text)
    return separator.join(parts)


def group_by_species(kept, names):
    """
    Gather the status entries of each GBIF species into one row.

    Returns the table and the list of odd merges from merge_same().
    """
    rows, odd = [], []
    for key, group in kept.groupby("gbif_species_key", sort=True):
        species_name = written_like_bcsee(names[key])
        entries, group_odd = merge_same([describe(r, names) for _, r in group.iterrows()])
        odd += group_odd
        # Sort while is_species_level still marks every species-level BCSEE
        # entry, so they come first even when the flag is cleared below.
        entries.sort(key=order_key)
        separate = sum(e["is_species_level"] for e in entries) >= 2
        for e in entries:
            if separate:
                e["is_species_level"] = False
            if e["scientific_label"] == species_name:
                e["is_gbif_synonym"] = False
        rows.append({
            "gbif_species_key": key,
            "species_name": species_name,
            "status_display": display_text(entries, "; "),
            "has_multiple": len(entries) > 1,
            "has_separate_bcsee_species": separate,
            # Only the shown statuses; entries without one keep their
            # BCSEE value in their own status field.
            "statuses": list(dict.fromkeys(
                e["status"] for e in entries if e["status"] in SHOWN_STATUSES)),
            "element_codes": [c for e in entries for c in e["element_codes"]],
            "entries": entries,
            "matched_species_keys": sorted(set(group["matched_species_key"])),
        })
    return pd.DataFrame(rows), odd


def write_outputs(table, data):
    """
    Write the table as Parquet and as a CSV copy.

    Each file goes to a temporary name first and is renamed only once it
    is complete, so a failed run cannot leave a broken file. The Parquet
    file is built by DuckDB from flat text columns, so the list columns
    come out the same with any DuckDB version, including 0.10.3 on the lab
    server.
    """
    parquet = data / f"{OUTPUT_NAME}.parquet"
    csv = data / f"{OUTPUT_NAME}.csv"

    # One row per entry, with the entry's position, and the element_codes
    # joined into text so DuckDB reads every column as plain text or true/false.
    flat = pd.DataFrame([{"gbif_species_key": row.gbif_species_key, "position": i,
                          **{k: v for k, v in e.items()
                             if k not in ("element_codes", "matched_species_keys")},
                          "element_codes": ";".join(e["element_codes"]),
                          "matched_species_keys": ";".join(e["matched_species_keys"])}
                         for row in table.itertuples() for i, e in enumerate(row.entries)])
    species = table[["gbif_species_key", "species_name", "status_display",
                     "has_multiple", "has_separate_bcsee_species", "statuses",
                     "matched_species_keys", "status_source", "status_source_date",
                     "status_pulled_on"]].copy()
    species["matched_species_keys"] = species["matched_species_keys"].map(";".join)
    # statuses comes from group_by_species(), which keeps only the shown
    # statuses. It must not be rebuilt from the entries here, because the
    # entries also hold Not Reviewed, Unknown and so on.
    species["statuses"] = species["statuses"].map(";".join)

    tmp = parquet.with_suffix(".parquet.partial")
    con = duckdb.connect()
    con.register("flat", plain_text_columns(flat))
    con.register("species", plain_text_columns(species))
    con.execute(f"""
        COPY (
            SELECT s.gbif_species_key, s.species_name, s.status_display, s.has_multiple,
                   s.has_separate_bcsee_species,
                   -- An empty text gives [''] when split, so blanks are
                   -- filtered out to leave an empty list.
                   list_filter(string_split(coalesce(s.statuses, ''), ';'), x -> x <> '')
                       AS statuses,
                   e.element_codes, e.entries,
                   string_split(s.matched_species_keys, ';') AS matched_species_keys,
                   s.status_source, s.status_source_date, s.status_pulled_on
            FROM species s
            JOIN (
                SELECT gbif_species_key,
                       list(struct_pack(
                           status := status,
                           scientific_label := scientific_label,
                           name_source := name_source,
                           common_label := common_label,
                           classification_level := classification_level,
                           is_species_level := is_species_level,
                           is_gbif_synonym := is_gbif_synonym,
                           matched_species_keys := string_split(matched_species_keys, ';'),
                           also_applied_from_key := also_applied_from_key,
                           element_codes := string_split(element_codes, ';'))
                           ORDER BY position) AS entries,
                       flatten(list(string_split(element_codes, ';')
                           ORDER BY position)) AS element_codes
                FROM flat GROUP BY gbif_species_key
            ) e USING (gbif_species_key)
            ORDER BY s.species_name, s.gbif_species_key
        ) TO '{tmp}' (FORMAT PARQUET, COMPRESSION ZSTD)""")
    con.close()
    os.replace(tmp, parquet)
    print(f"  wrote {parquet} ({len(table):,} rows, {parquet.stat().st_size / 1e6:.1f} MB)")

    # The CSV copy, with the lists written as text. " | " separates items,
    # because some english_names contain ";".
    text = table.sort_values(["species_name", "gbif_species_key"]).copy()
    text["status_display"] = text["entries"].map(lambda e: display_text(e, " | "))
    text["statuses"] = text["statuses"].map(" | ".join)
    text["element_codes"] = text["element_codes"].map(" | ".join)
    text["matched_species_keys"] = text["matched_species_keys"].map(" | ".join)
    text["entries"] = text["entries"].map(lambda e: json.dumps(e, ensure_ascii=False))
    tmp = csv.with_suffix(".csv.partial")
    text.to_csv(tmp, index=False)
    os.replace(tmp, csv)
    print(f"  wrote {csv}")


def plain_text_columns(frame):
    """
    Return a copy of the table with every text column in the oldest,
    most widely understood text format, and blanks as true blanks.

    pandas 3 stores text in a new format that older copies of DuckDB, like
    the 0.10.3 on the lab server, do not recognise. Same as in
    match_bcsee_gbif.py.
    """
    out = frame.copy()
    for col in out.columns:
        if pd.api.types.is_string_dtype(out[col].dtype):
            values = out[col].astype(object)
            out[col] = values.where(values.notna(), None)
    return out


def read_bcsee_names(data):
    """Return BCSEE's scientific name for every element_code in
    bcsee_status.parquet, for checking bcsee_extra_keys.csv."""
    con = duckdb.connect()
    rows = con.execute(
        f"SELECT element_code, scientific_name FROM '{data / 'bcsee_status.parquet'}'").fetchall()
    con.close()
    return dict(rows)


def read_status_source(data):
    """
    Return (status_source_date, status_pulled_on) from bcsee_status.parquet:
    the date the province made the BCSEE export (source_generated) and the
    date pull_bcsee.py downloaded it (pulled_on). Either is "" if unknown.
    Stops if the file mixes more than one pull, since then no single date
    describes it.
    """
    con = duckdb.connect()
    pulls = con.execute(
        f"SELECT DISTINCT source_generated, pulled_on FROM '{data / 'bcsee_status.parquet'}'"
    ).fetchall()
    con.close()
    if len(pulls) != 1:
        raise ValueError(f"bcsee_status.parquet mixes {len(pulls)} pulls: {pulls}. "
                         f"Rerun pull_bcsee.py.")
    generated, pulled = pulls[0]
    return (str(generated) if generated else ""), (str(pulled) if pulled else "")


def report_merge(table, kept):
    """
    Print what the species key merge did: the entries moved to a kept key,
    the kept keys that gained entries, and every kept key whose entries now
    have different statuses (BCSEE values), listed in full for review.
    kept is the table of BCSEE entries from read_entries().
    """
    # Copies from bcsee_extra_keys.csv sit on another key too, but they were
    # not moved by the merge, so they are left out here.
    own = kept[kept["also_applied_from_key"] == ""] if "also_applied_from_key" in kept else kept
    moved = own[own["matched_species_key"] != own["gbif_species_key"]]
    print(f"  key merge: {len(moved):,} entries moved to their kept key")
    for _, r in moved.iterrows():
        print(f"    {r['element_code']} {r['scientific_name']} ({r['bc_list']}): "
              f"{r['matched_species_key']} -> {r['gbif_species_key']}")
    gained = table[table.apply(
        lambda row: any(k != row["gbif_species_key"]
                        for e in row["entries"] if not e["also_applied_from_key"]
                        for k in e["matched_species_keys"]), axis=1)]
    print(f"  key merge: {len(gained):,} kept keys gained entries")
    both = gained[gained["matched_species_keys"].map(len) > 1]
    # Compare every entry's own value, not only the shown statuses, so a
    # Yellow and a Not Reviewed on one kept key are also listed for review.
    different = both[both["entries"].map(lambda es: len({e["status"] for e in es}) > 1)]
    print(f"  key merge: {len(both):,} kept keys now hold entries from both keys of a pair; "
          f"{len(different):,} of them with different statuses (kept side by side)")
    for row in different.itertuples():
        print(f"    REVIEW {row.gbif_species_key} {row.species_name}:")
        for e in row.entries:
            print(f"      {e['status']:7} {e['scientific_label']}  "
                  f"(from {', '.join(e['matched_species_keys'])}; {', '.join(e['element_codes'])})")


def report(table, kept, odd):
    """Print counts and anything a person should look at. kept is the
    table of BCSEE entries from read_entries()."""
    with_status = int((table["statuses"].map(len) > 0).sum())
    print(f"  {len(table):,} GBIF species in BCSEE: {with_status:,} with a shown "
          f"status, {len(table) - with_status:,} without one; "
          f"{int(table['has_multiple'].sum()):,} with more than one entry, "
          f"{int(table['has_separate_bcsee_species'].sum()):,} with two or more "
          f"species-level BCSEE entries")
    entries = [e for es in table["entries"] for e in es]
    merged = [e for e in entries if len(e["element_codes"]) > 1]
    print(f"  {len(entries):,} entries from {len(kept):,} BCSEE entries "
          f"({len(merged):,} merged)")
    mixed = table[(table["statuses"].map(len) > 0) & table["entries"].map(
        lambda es: any(e["status"] not in SHOWN_STATUSES for e in es))]
    print(f"  {len(mixed):,} species with a shown status also have entries without "
          f"one (each keeps its own BCSEE value)")
    for e in merged:
        print(f"    merged {', '.join(e['element_codes'])}: "
              f"{e['status']} {e['scientific_label']}")
    for first, second, field in odd:
        print(f"    merged {first} and {second} although their {field} differs")
    bcsee = sum(e["name_source"] == "BCSEE" for e in entries)
    synonyms = sum(e["is_gbif_synonym"] for e in entries)
    print(f"  {bcsee:,} entries show the BCSEE name, {synonyms:,} are tagged as "
          f"GBIF synonyms")


def main():
    """Read the matched BCSEE entries, group them by GBIF species, and write the table."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", default="/home/songyanf/bcbn/data",
                        help="Where the inputs are and where the output goes. "
                             "Defaults to the server's data folder, like the "
                             "other pipeline scripts.")
    parser.add_argument("--key-merge", default=None,
                        help="species_key_merge.csv from make_species_key_merge.py. When "
                             "given, entries on an old_key move to its kept_key, the key "
                             "with a species card. Off by default.")
    parser.add_argument("--col-release", type=int, default=None,
                        help="The ChecklistBank release of the Catalogue of Life the rest "
                             "of the data uses (315557 for the current download). Needed "
                             "only when a GBIF name has to be looked up: the run then "
                             "checks GBIF still uses this release, and stops if not.")
    parser.add_argument("--extra-keys", default=str(EXTRA_KEYS_FILE),
                        help="The hand-checked list of BCSEE entries that also apply to "
                             "another key. Defaults to bcsee_extra_keys.csv next to "
                             "this script.")
    args = parser.parse_args()
    data = Path(args.data_dir).expanduser()
    raw = data / "bcsee_raw"

    print("=" * 70)
    print("READING")
    print("=" * 70)
    print()
    merge_pairs = read_key_merge(Path(args.key_merge).expanduser()) if args.key_merge else []
    if args.key_merge:
        print(f"  key merge list: {args.key_merge} ({len(merge_pairs):,} pairs)")
    kept = read_entries(data, merge_pairs)
    extra = read_extra_keys(Path(args.extra_keys).expanduser())
    check_extra_keys(extra, kept, read_bcsee_names(data), {old for old, _, _ in merge_pairs})
    status_source_date, status_pulled_on = read_status_source(data)
    key_path = raw / "gbif_key_answers.jsonl"
    names = saved_names(raw / "gbif_match_answers_v2.jsonl", key_path)
    # Names needed: each row's (kept) key, each entry's own GBIF name, and
    # the overridden key of an overridden entry, which describe() uses.
    needed = set(kept["gbif_species_key"]) | set(
        kept.loc[~kept["key_changed"], "gbif_usage_key"].dropna()) | set(
        kept.loc[kept["key_changed"], "matched_species_key"]) | set(
        extra["extra_gbif_species_key"])
    add_missing_names(needed, names, key_path, args.col_release)
    check_extra_key_names(extra, names)
    kept = apply_extra_keys(kept, extra, names)

    print()
    print("=" * 70)
    print("GROUPING")
    print("=" * 70)
    print()
    table, odd = group_by_species(kept, names)
    table["status_source"] = STATUS_SOURCE
    table["status_source_date"] = status_source_date
    table["status_pulled_on"] = status_pulled_on
    report(table, kept, odd)
    if args.key_merge:
        report_merge(table, kept)
    print(f"  source label: {STATUS_SOURCE}, made by the province {status_source_date or '?'}, "
          f"pulled {status_pulled_on or '?'}")

    print()
    print("=" * 70)
    print("WRITING")
    print("=" * 70)
    print()
    write_outputs(table, data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
