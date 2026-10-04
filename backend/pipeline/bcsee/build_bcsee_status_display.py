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
    bcsee_gbif_overrides.csv   optional, columns element_code and
                               gbif_species_key. Written by a person after
                               checking a match by hand. Applied before
                               anything else: the element_code uses the key
                               in the file, and is used even if its join_ok
                               is false.

Rules:
  1. Only the statuses Red, Blue, Yellow and Exotic are shown. Every other
     bc_list value (Not Reviewed, Unknown, Accidental, No Status, Extinct,
     Other) is dropped, and a GBIF species with nothing left is left out.
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
     Exotic. Ties are broken by scientific_label, so every run gives the
     same order.

Output, one row per GBIF species:
    bcsee_status_by_gbif_species.parquet
        gbif_species_key, species_name (GBIF's accepted name, written like
        scientific_label), status_display (plain text for checking by eye,
        not for the front end), has_multiple (more than one entry shown),
        has_separate_bcsee_species (two or more species-level BCSEE
        entries, so there is no overall BC status), statuses (the distinct
        statuses in entry order), element_codes (every BCSEE element_code,
        in entry order), entries (the list of status entries)
    bcsee_status_by_gbif_species.csv
        the same table to open in a spreadsheet. The lists are written as
        text: statuses, element_codes and the entries in status_display
        joined with " | " (some english_names contain ";"), entries as JSON.

The script only reads its inputs and writes its two outputs and its own
answers file, so it is safe to run again at any time.

Usage:
    python build_bcsee_status_display.py
    python build_bcsee_status_display.py --data-dir ~/Desktop/BCBN/Results
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

# GBIF statuses that mean the matched name is a synonym.
SYNONYM_STATUSES = {"SYNONYM", "AMBIGUOUS_SYNONYM", "HOMOTYPIC_SYNONYM",
                    "HETEROTYPIC_SYNONYM", "PROPARTE_SYNONYM"}

# The rank markers BCSEE writes in plant and fungus names, by GBIF rank.
# Animal names have none, so they are left as GBIF writes them.
RANK_MARKERS = {"SUBSPECIES": "ssp.", "VARIETY": "var.", "FORM": "f."}
MARKED_KINGDOMS = {"Plantae", "Fungi"}

OUTPUT_NAME = "bcsee_status_by_gbif_species"


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


def read_entries(data):
    """
    Read the BCSEE entries that can be shown, one row per element_code.

    Applies the overrides, keeps rows with join_ok or an override, adds
    bc_list, english_name and the classification level from
    bcsee_status.parquet, and keeps only the statuses in SHOWN_STATUSES.
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

    used = match[match["join_ok"] | match["overridden"]]
    rows = used.merge(status, on="element_code", how="left", indicator=True)
    lost = rows.loc[rows["_merge"] == "left_only", "element_code"]
    if len(lost):
        raise ValueError(f"{len(lost)} element_codes are not in bcsee_status.parquet, "
                         f"for example {list(lost[:5])}. Rerun pull_bcsee.py and "
                         f"match_bcsee_gbif.py so the two files agree.")
    rows = rows.drop(columns="_merge")
    shown = rows[rows["bc_list"].isin(SHOWN_STATUSES)].reset_index(drop=True)
    print(f"  {len(used):,} BCSEE entries can be joined, {len(shown):,} have a "
          f"status that is shown ({', '.join(SHOWN_STATUSES)})")
    return shown


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


def add_missing_names(keys, names, key_path):
    """
    Look up the keys that are not in the saved answers, add them to names,
    and save GBIF's answers so the next run does not ask again.
    """
    missing = sorted({k for k in keys if k not in names})
    if not missing:
        return
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
        name = names[row["gbif_species_key"]]
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
        "is_species_level": row["classification_level"] == "Species",
        "is_gbif_synonym": bool(name) and name.get("status") in SYNONYM_STATUSES,
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
                            "element_codes": [entry["element_code"]]}
            continue
        kept = merged[same]
        for field in ("name_source", "is_species_level", "is_gbif_synonym"):
            if kept[field] != entry[field]:
                odd.append((kept["element_codes"][0], entry["element_code"], field))
        kept["is_species_level"] |= entry["is_species_level"]
        kept["is_gbif_synonym"] |= entry["is_gbif_synonym"]
        kept["element_codes"].append(entry["element_code"])
    for entry in merged.values():
        entry["element_codes"].sort()
    return list(merged.values()), odd


def order_key(entry):
    """Sort key: species-level first, then by status, then by name."""
    return (not entry["is_species_level"],
            SHOWN_STATUSES.index(entry["status"]),
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


def group_by_species(shown, names):
    """
    Gather the status entries of each GBIF species into one row.

    Returns the table and the list of odd merges from merge_same().
    """
    rows, odd = [], []
    for key, group in shown.groupby("gbif_species_key", sort=True):
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
            "statuses": list(dict.fromkeys(e["status"] for e in entries)),
            "element_codes": [c for e in entries for c in e["element_codes"]],
            "entries": entries,
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
                          **{k: v for k, v in e.items() if k != "element_codes"},
                          "element_codes": ";".join(e["element_codes"])}
                         for row in table.itertuples() for i, e in enumerate(row.entries)])
    species = table[["gbif_species_key", "species_name", "status_display",
                     "has_multiple", "has_separate_bcsee_species"]]

    tmp = parquet.with_suffix(".parquet.partial")
    con = duckdb.connect()
    con.register("flat", plain_text_columns(flat))
    con.register("species", plain_text_columns(species))
    con.execute(f"""
        COPY (
            SELECT s.gbif_species_key, s.species_name, s.status_display, s.has_multiple,
                   s.has_separate_bcsee_species, st.statuses, e.element_codes, e.entries
            FROM species s
            JOIN (
                SELECT gbif_species_key,
                       list(struct_pack(
                           status := status,
                           scientific_label := scientific_label,
                           name_source := name_source,
                           common_label := common_label,
                           is_species_level := is_species_level,
                           is_gbif_synonym := is_gbif_synonym,
                           element_codes := string_split(element_codes, ';'))
                           ORDER BY position) AS entries,
                       flatten(list(string_split(element_codes, ';')
                           ORDER BY position)) AS element_codes
                FROM flat GROUP BY gbif_species_key
            ) e USING (gbif_species_key)
            JOIN (
                SELECT gbif_species_key, list(status ORDER BY first_position) AS statuses
                FROM (SELECT gbif_species_key, status, min(position) AS first_position
                      FROM flat GROUP BY ALL)
                GROUP BY gbif_species_key
            ) st USING (gbif_species_key)
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


def report(table, shown, odd):
    """Print counts and anything a person should look at."""
    print(f"  {len(table):,} GBIF species with a status shown, "
          f"{int(table['has_multiple'].sum()):,} of them with more than one entry, "
          f"{int(table['has_separate_bcsee_species'].sum()):,} with two or more "
          f"species-level BCSEE entries")
    entries = [e for es in table["entries"] for e in es]
    merged = [e for e in entries if len(e["element_codes"]) > 1]
    print(f"  {len(entries):,} entries from {len(shown):,} BCSEE entries "
          f"({len(merged):,} merged)")
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
    args = parser.parse_args()
    data = Path(args.data_dir).expanduser()
    raw = data / "bcsee_raw"

    print("=" * 70)
    print("READING")
    print("=" * 70)
    print()
    shown = read_entries(data)
    key_path = raw / "gbif_key_answers.jsonl"
    names = saved_names(raw / "gbif_match_answers_v2.jsonl", key_path)
    needed = set(shown["gbif_species_key"]) | set(
        shown.loc[~shown["key_changed"], "gbif_usage_key"].dropna())
    add_missing_names(needed, names, key_path)

    print()
    print("=" * 70)
    print("GROUPING")
    print("=" * 70)
    print()
    table, odd = group_by_species(shown, names)
    report(table, shown, odd)

    print()
    print("=" * 70)
    print("WRITING")
    print("=" * 70)
    print()
    write_outputs(table, data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
