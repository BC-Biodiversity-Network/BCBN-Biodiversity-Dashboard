"""
Match every BCSEE species name to its GBIF species key.

BCSEE gives each species its own code (element_code) and GBIF gives each
species its own key (speciesKey). The two systems were built separately,
so nothing links them yet. This script asks GBIF's name matching service,
one BCSEE name at a time, "which of your species is this?", and saves the
answers as a lookup table from element_code to speciesKey.

Which checklist: GBIF's version 2 matching service, against the Catalogue
of Life checklist (CHECKLIST_KEY). bc_clean.parquet comes from GBIF's
monthly cloud snapshot, which files every observation under a Catalogue
of Life key, a short code such as 63Z5D for Abies amabilis. The older
matching service, and version 2 without a checklist, answer with GBIF's
old backbone numbers instead (2685524 for the same tree), which appear
nowhere in the snapshot. Catalogue of Life keys can change between
releases, so rerun this script with --refresh whenever a new GBIF snapshot
is downloaded.

Everything else from the Conservation Data Centre can then reach GBIF
through that one table: the status history and the public occurrence areas
both carry the same element_code.

What is sent: each name is asked about in up to two steps.

  1. The scientific name, plus BCSEE's kingdom, phylum, class, order and
     family as hints. The hints keep GBIF from matching a name to a
     look-alike in the wrong group. With GBIF's older version 1 service,
     a name alone matched a water mite to a wasp and a bee to an orchid;
     version 2 did not repeat these in testing, but the risk is the same.
  2. If that answer is not an exact match, the name alone. Hints can do
     harm too: where BCSEE files a species in a different family or
     kingdom than GBIF, GBIF prefers a similar name inside the hinted
     family over the exact name elsewhere (Irpex lacteus comes back as
     Irpex lacer). The name-only answer replaces the first one only if it
     is an exact match, its kingdom agrees with BCSEE's group, and, for
     animals, its class agrees with BCSEE's class (or its order, when GBIF
     gives no class). Column gbif_answer_from says which answer was kept.

The kingdom, class, order and family of an answer come from its
classification list, and gbif_species_key is the key of the SPECIES entry
in that list.

What is not sent: the 632 ecological communities and 2 ecological systems.
GBIF has no such thing, so they can never match.

Names are sent exactly as BCSEE writes them. For a population such as
"Oncorhynchus tshawytscha pop. 36", GBIF drops the "pop. 36" by itself and
returns the species, marked as a match at a higher rank.

Which GBIF key to join on: use gbif_species_key, and only in rows where
join_ok is true. For a subspecies it is the key of the species it belongs
to, and for an outdated name GBIF has already pointed it at the name it
accepts today. GBIF observations almost never record a subspecies, so
joining on the subspecies key would miss nearly everything.

join_ok is true only for an exact match, or for a population, subspecies
or variety that GBIF matched to its species, and never for a placeholder
name such as "Cottus sp. 9" or "Steiroxys cf. strepens". Close-spelling
matches (VARIANT, and the rarer CANONICAL and AMBIGUOUS) are left out for
now, even at confidence 100: most are the same species spelled with a
different Latin ending, but some can be a different species, and the two
cannot yet be told apart without a person checking. needs_review marks
those rows, and the few other answers that look odd, for that check.

GBIF's answers are saved as they arrive, so a run that stops halfway picks
up where it left off, and a second run asks GBIF only about names that
changed. Use --refresh to ask about every name again.

Output:
    bcsee_gbif_match.parquet   one row per BCSEE species, subspecies,
                               variety or population, with what GBIF matched
                               it to, how sure GBIF was, the checklist used
                               (gbif_checklist_key), and the join_ok and
                               needs_review flags

Usage:
    python match_bcsee_gbif.py
    python match_bcsee_gbif.py --data-dir ~/Desktop/BCBN/Results
    python match_bcsee_gbif.py --refresh
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import requests


MATCH_URL = "https://api.gbif.org/v2/species/match"

# The Catalogue of Life checklist, the one GBIF's cloud snapshot (and so
# bc_clean.parquet) uses for its species keys. Without it, GBIF answers
# with its old backbone numbers, which cannot be joined to the snapshot.
CHECKLIST_KEY = "7ddf754f-d193-4cc9-b351-99906754a03b"
HEADERS = {"User-Agent": "BCBN-Dashboard (UBC Biodiversity Research Centre)"}

# The kinds of BCSEE entry that are sent to GBIF. Everything else is an
# ecological community or ecological system, which GBIF does not have.
LEVELS_TO_MATCH = ["Species", "Subspecies", "Variety", "Population"]

# BCSEE column name on the left, the name GBIF expects on the right.
# Checked against GBIF's matching service source code on 2026-09-27.
HINTS = {"kingdom": "kingdom", "phylum": "phylum", "class": "class",
         "order": "order", "family": "family"}

# The GBIF kingdom each BCSEE group belongs to, used to check a name-only
# answer. BCSEE's own kingdom column cannot be used, because it files
# lichens and slime molds under Plantae.
KINGDOM_BY_GROUP = {
    "Vascular Plant": "Plantae", "Bryophyte": "Plantae",
    "Fungus": "Fungi", "Lichen": "Fungi",
    "Invertebrate Animal": "Animalia", "Vertebrate Animal": "Animalia",
    "Protozoan": "Protozoa",
}
ANIMAL_GROUPS = {"Invertebrate Animal", "Vertebrate Animal"}

# BCSEE class names that the Catalogue of Life writes differently, with
# the classes each may appear as there. Without this, a name-only answer
# for any fish, soft coral, turtle, shark, lamprey or earthworm would fail
# the class check. A class not listed here must match exactly. For the few
# answers with no class at all, the order is compared instead. Checked
# against the first answers on 2026-09-27; recheck when the checklist
# changes.
CLASS_NAMES = {
    "Actinopterygii": {"Teleostei", "Chondrostei"},
    "Anthozoa": {"Anthozoa", "Octocorallia"},
    "Chelonia": {"Reptilia"},
    "Chondrichthyes": {"Elasmobranchii", "Holocephali"},
    "Oligochaeta": {"Clitellata"},
    "Petromyzontida": {"Petromyzonti"},
}

# A BCSEE name for an undescribed or uncertain species: "Cottus sp. 9",
# "Lillipathes sp. B", "Steiroxys cf. strepens", "Micrargus nr.
# herbigradus", "Neopasites aff. fulviventris", "Probole alienaria
# complex". GBIF has no entry for these, so any species it returns is a
# different one. "complex" counts only after a full two-word name, because
# it can also be a real species name (Lasconotus complex). The rule follows
# naming convention, not a list, so the names it catches (printed on every
# run) must be checked by a person whenever BCSEE is updated.
PLACEHOLDER = re.compile(r" sp\.\s*\S| aff\. | cf\. | nr\. |^\S+ \S+ complex\b")

# BCSEE levels below species. GBIF has no separate entry for most of them
# and returns the species instead, which is the right number to join on.
BELOW_SPECIES = {"Population", "Subspecies", "Variety"}

# How many questions to have waiting for GBIF at the same time. One at a
# time would take over an hour for 25,000 names. Four keeps it to roughly a
# quarter of that without putting much load on GBIF's service.
WORKERS = 4

# How many times to try one name again after a network error, and how long
# to wait before each new try (in seconds, growing each time).
RETRIES = 3
WAIT = 2

# The fields kept from each GBIF answer, as flatten() names them on the
# left and the output column on the right. Any field GBIF leaves out
# becomes a blank.
KEEP = {
    "matchType": "gbif_match_type",
    "status": "gbif_status",
    "rank": "gbif_rank",
    "confidence": "gbif_confidence",
    "usageKey": "gbif_usage_key",
    "acceptedUsageKey": "gbif_accepted_usage_key",
    "speciesKey": "gbif_species_key",
    "scientificName": "gbif_scientific_name",
    "canonicalName": "gbif_canonical_name",
    "kingdom": "gbif_kingdom",
    "family": "gbif_family",
    "note": "gbif_note",
}

# The output columns that hold whole numbers. Every other column is text,
# including the Catalogue of Life keys, which are short codes like 63Z5D.
NUMBER_COLUMNS = {"gbif_confidence"}

# Match types that mean "a name like this one", not the name itself. They
# are never joined until a person has checked them.
#   VARIANT    a spelling GBIF counts as the same name (often only the
#              Latin ending differs)
#   CANONICAL  matched on the bare name, ignoring author or rank
#   AMBIGUOUS  several names fit and GBIF could not choose
REVIEW_MATCH_TYPES = {"VARIANT", "CANONICAL", "AMBIGUOUS"}

# Statuses that mean the name may stand for more than one species, so its
# species key may be the wrong one. Treated like a close spelling: never
# joined until a person has checked it.
#   AMBIGUOUS_SYNONYM  the name points to more than one species
#   MISAPPLIED         the name was once used by mistake for another species
REVIEW_STATUSES = {"AMBIGUOUS_SYNONYM", "MISAPPLIED"}

# Each thread gets its own connection to GBIF, because one connection shared
# between threads can mix up their answers.
local = threading.local()


def read_bcsee(path):
    """
    Read the BCSEE list and keep only the entries GBIF could know about.

    Returns the rows to send and a description of what was left out, such
    as "632 ecological communities and 2 ecological systems", for the
    report.
    """
    if not path.exists():
        raise FileNotFoundError(f"{path} not found. Run pull_bcsee.py first.")
    con = duckdb.connect()
    rows = con.execute(
        f"SELECT element_code, scientific_name, classification_level, "
        f"name_category, kingdom, phylum, class, \"order\", family "
        f"FROM '{path}'").df()
    con.close()
    keep = rows["classification_level"].isin(LEVELS_TO_MATCH)
    left = rows.loc[~keep, "classification_level"]
    communities = int(left.str.contains("Community").sum())
    left_out = (f"{communities:,} ecological communities and "
                f"{len(left) - communities:,} ecological systems")
    print(f"  {len(rows):,} entries in {path.name}, sending {int(keep.sum()):,}, "
          f"leaving out {left_out}")
    return rows[keep].reset_index(drop=True), left_out


def question(row):
    """
    Build what is sent to GBIF for one BCSEE entry.

    Blank hints are left out rather than sent empty, because an empty hint
    could be read as "this species has no family".
    """
    params = {"scientificName": row["scientific_name"],
              "checklistKey": CHECKLIST_KEY}
    for ours, theirs in HINTS.items():
        value = row[ours]
        if isinstance(value, str) and value.strip():
            params[theirs] = value.strip()
    return params


def cache_key(element_code, params):
    """
    Label one question so its answer can be found again later.

    The label contains the code and everything sent, the checklist
    included. If BCSEE changes a name or a hint in a later pull, or the
    checklist changes, the label changes too, so that entry is asked again
    instead of reusing an answer to a different question.
    """
    return element_code + "|" + json.dumps(params, sort_keys=True)


def load_cache(path):
    """
    Read the answers saved by earlier runs, one per line.

    A line that cannot be read, which can happen if a run was stopped in the
    middle of writing it, is skipped and that name is simply asked again.
    """
    answers = {}
    if not path.exists():
        return answers
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                item = json.loads(line)
                answers[item["key"]] = item["answer"]
            except (json.JSONDecodeError, KeyError):
                continue
    return answers


def ask_gbif(params):
    """
    Send one question to GBIF and return its answer.

    A network error or a server error is tried again a few times, waiting a
    little longer each time. "Too many requests" (429) is tried again too,
    after the number of seconds GBIF asks for in Retry-After if it sends
    one. Any other 4xx error means the question itself is wrong, so it is
    not tried again. If every try fails the error is passed up, and that
    name is left out of the saved answers so the next run tries again.
    """
    if not hasattr(local, "session"):
        local.session = requests.Session()
        local.session.headers.update(HEADERS)
    for attempt in range(RETRIES + 1):
        wait = WAIT * (attempt + 1)
        try:
            r = local.session.get(MATCH_URL, params=params, timeout=60)
            if r.status_code == 429 and r.headers.get("Retry-After", "").isdigit():
                wait = int(r.headers["Retry-After"])
            r.raise_for_status()
            return r.json()
        except requests.HTTPError as err:
            status = err.response.status_code
            if (400 <= status < 500 and status != 429) or attempt == RETRIES:
                raise
        except (requests.RequestException, ValueError):
            if attempt == RETRIES:
                raise
        time.sleep(wait)


def ask_missing(questions, answers, fh):
    """
    Ask GBIF every question in a {key: params} dict that has no saved answer.

    Answers are added to the answers dict and written to the save file the
    moment they arrive, by this one thread only, so two threads never write
    to the file at once. Returns how many questions got no answer.
    """
    todo = [(k, p) for k, p in questions.items() if k not in answers]
    print(f"  {len(questions) - len(todo):,} already answered in earlier runs, "
          f"{len(todo):,} to ask GBIF")
    failed = 0
    if not todo:
        return failed
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        jobs = {pool.submit(ask_gbif, params): key for key, params in todo}
        for done, job in enumerate(as_completed(jobs), start=1):
            key = jobs[job]
            try:
                answers[key] = job.result()
                fh.write(json.dumps({"key": key, "answer": answers[key]}) + "\n")
                fh.flush()
            except Exception as err:
                failed += 1
                if failed <= 5:
                    print(f"    could not get an answer for {key.split('|')[0]}: {err}")
            if done % 1000 == 0 or done == len(todo):
                print(f"    {done:,} of {len(todo):,} asked")
    return failed


def flatten(answer):
    """
    Pull the fields this script uses out of one GBIF version 2 answer.

    The matched name is in "usage", the accepted name for a synonym in
    "acceptedUsage", and matchType and confidence in "diagnostics".
    Kingdom, class, order and family come from the "classification" list,
    which for a synonym is the accepted name's. The species key is the key
    of the SPECIES entry in that list: for a subspecies it is the species
    it belongs to, and for a genus-level match there is none. A no-match
    answer has only "diagnostics", so every other field comes back blank.
    """
    usage = answer.get("usage") or {}
    diagnostics = answer.get("diagnostics") or {}
    ranks = {c.get("rank"): c for c in answer.get("classification") or []}
    notes = list(diagnostics.get("issues") or [])
    if diagnostics.get("note"):
        notes.append(diagnostics["note"])
    return {
        "matchType": diagnostics.get("matchType"),
        "confidence": diagnostics.get("confidence"),
        "status": usage.get("status"),
        "rank": usage.get("rank"),
        "usageKey": usage.get("key"),
        "acceptedUsageKey": (answer.get("acceptedUsage") or {}).get("key"),
        "speciesKey": ranks.get("SPECIES", {}).get("key"),
        "scientificName": usage.get("name"),
        "canonicalName": usage.get("canonicalName"),
        "kingdom": ranks.get("KINGDOM", {}).get("name"),
        "class": ranks.get("CLASS", {}).get("name"),
        "order": ranks.get("ORDER", {}).get("name"),
        "family": ranks.get("FAMILY", {}).get("name"),
        "note": "; ".join(notes) or None,
    }


def check_name_only(row, answer):
    """
    Decide whether a name-only answer, as flatten() returns it, can
    replace the answer with hints.

    Returns None if it can, or the reason it cannot: "not exact",
    "kingdom", or "class" (the class, or the order when GBIF gives no
    class, does not agree with BCSEE's).
    """
    if answer.get("matchType") != "EXACT":
        return "not exact"
    if answer.get("kingdom") != KINGDOM_BY_GROUP.get(row["name_category"]):
        return "kingdom"
    if row["name_category"] in ANIMAL_GROUPS:
        if answer.get("class"):
            if answer["class"] not in CLASS_NAMES.get(row["class"], {row["class"]}):
                return "class"
        elif answer.get("order"):
            if answer["order"] != row["order"]:
                return "class"
        else:
            return "class"
    return None


def match_all(rows, cache_path, refresh):
    """
    Get GBIF's answer for every row, asking only about names not saved yet.

    Every name is asked with hints first. Names without an exact match are
    then asked with the name alone, and that answer is kept if it passes
    check_name_only. Returns the rows with GBIF's answers, one record per
    name asked a second time (for the report), and the number of questions
    that got no answer.
    """
    answers = {} if refresh else load_cache(cache_path)
    firsts = [question(r) for _, r in rows.iterrows()]
    first_keys = [cache_key(code, p) for code, p in zip(rows["element_code"], firsts)]

    with open(cache_path, "w" if refresh else "a", encoding="utf-8") as fh:
        print("  first question, name with hints")
        failed = ask_missing(dict(zip(first_keys, firsts)), answers, fh)

        # A name whose first question had no hints would get the same
        # question again, so it is not asked twice.
        second_keys = {}
        for i, (params, key) in enumerate(zip(firsts, first_keys)):
            answer = answers.get(key)
            name_only = {"scientificName": params["scientificName"],
                         "checklistKey": CHECKLIST_KEY}
            if (answer is None or flatten(answer)["matchType"] == "EXACT"
                    or name_only == params):
                continue
            second_keys[i] = (cache_key(rows.at[i, "element_code"], name_only), name_only)
        print()
        print("  second question, name only, for names without an exact match")
        failed += ask_missing(dict(second_keys.values()), answers, fh)
    if failed:
        print(f"  {failed} questions got no answer. Run the script again to retry them.")

    for out in KEEP.values():
        rows[out] = None
    rows["gbif_answer_from"] = None
    second = []
    for i, key in enumerate(first_keys):
        if key not in answers:
            continue
        answer = flatten(answers[key])
        source = "with_hints"
        if i in second_keys and second_keys[i][0] in answers:
            other = flatten(answers[second_keys[i][0]])
            reason = check_name_only(rows.loc[i], other)
            second.append({"row": i, "first": answer, "second": other,
                           "rejected": reason})
            if reason is None:
                answer, source = other, "name_only"
        for field, out in KEEP.items():
            rows.at[i, out] = answer.get(field)
        rows.at[i, "gbif_answer_from"] = source

    # Set each column's type by hand. Left to guess, a column that happens
    # to be blank in every row (such as the note) is saved as a number
    # column, and the next run with a real note in it no longer fits.
    for field, out in KEEP.items():
        if out in NUMBER_COLUMNS:
            rows[out] = rows[out].astype("Int64")
        else:
            rows[out] = rows[out].astype("string")
    rows["gbif_answer_from"] = rows["gbif_answer_from"].astype("string")
    rows["gbif_checklist_key"] = CHECKLIST_KEY
    return rows, second, failed


def add_flags(rows):
    """
    Add join_ok and needs_review. GBIF's answers are left as they are, so
    it stays visible why a row cannot be joined.

    join_ok: safe to join to GBIF observations on gbif_species_key. The
    match is exact, or GBIF gave the species for a BCSEE level below
    species, the name is not a placeholder, and its status is not one of
    REVIEW_STATUSES.

    needs_review: a person should look at it. Every close-spelling match
    (REVIEW_MATCH_TYPES), every answer with a status in REVIEW_STATUSES,
    every placeholder name GBIF gave a species key anyway, and a BCSEE
    species that GBIF matched to a species "at a higher rank".
    """
    has_key = rows["gbif_species_key"].notna().to_numpy()
    match = rows["gbif_match_type"].fillna("").to_numpy()
    rank = rows["gbif_rank"].fillna("").to_numpy()
    level = rows["classification_level"]
    unclear = rows["gbif_status"].isin(REVIEW_STATUSES).to_numpy()
    placeholder = rows["scientific_name"].str.contains(PLACEHOLDER).to_numpy()
    species_above = (match == "HIGHERRANK") & (rank == "SPECIES")
    right_match = (match == "EXACT") | (species_above & level.isin(BELOW_SPECIES).to_numpy())

    rows["join_ok"] = has_key & right_match & ~placeholder & ~unclear
    rows["needs_review"] = (np.isin(match, list(REVIEW_MATCH_TYPES))
                            | unclear
                            | (placeholder & has_key)
                            | (species_above & (level == "Species").to_numpy()))
    return rows


def plain_text_columns(frame):
    """
    Return a copy of the table with every text column in the oldest,
    most widely understood text format, and blanks as true blanks.

    pandas 3 stores text in a new format. Older copies of DuckDB, like the
    0.10.3 on the lab server, do not recognise it and stop with "Data type
    'str' not recognized". Converting text columns back to the old general
    format first lets any DuckDB version read the table.
    """
    out = frame.copy()
    for col in out.columns:
        if pd.api.types.is_string_dtype(out[col].dtype):
            values = out[col].astype(object)
            out[col] = values.where(values.notna(), None)
    return out


def write_parquet(frame, path):
    """
    Write the table, and do it so a failed run cannot leave a broken file.

    The table goes to a temporary file first and is renamed only once it is
    complete, so the output is always a whole file from some run.
    """
    tmp = path.with_suffix(path.suffix + ".partial")
    con = duckdb.connect()
    con.register("staging", plain_text_columns(frame))
    con.execute(f"COPY (SELECT * FROM staging) TO '{tmp}' "
                f"(FORMAT PARQUET, COMPRESSION ZSTD)")
    con.close()
    os.replace(tmp, path)
    print(f"  wrote {path} ({len(frame):,} rows, "
          f"{path.stat().st_size / 1e6:.1f} MB)")


def report(frame, second, left_out):
    """
    Print how well the names matched, in a form that can go to Evan.

    The numbers answer three questions: how many BCSEE entries can be joined
    to GBIF observations at all, where the ones that cannot are, and where
    more than one BCSEE entry lands on the same GBIF species.
    """
    answered = frame[frame["gbif_match_type"].notna()]
    usable = answered["gbif_species_key"].notna()

    print()
    print("=" * 70)
    print("RESULT")
    print("=" * 70)
    print()
    # These count BCSEE entries, not species: several entries (such as the
    # populations of one salmon species) can point to the same GBIF species.
    print(f"  {len(frame):,} BCSEE entries sent to GBIF ({left_out} "
          f"left out), {len(answered):,} answered")
    join_ok = frame["join_ok"]
    print(f"  {int(join_ok.sum()):,} BCSEE entries can be joined to GBIF observations "
          f"(join_ok, {join_ok.mean():.1%} of those sent)")

    # Each row left out is counted once, under the first reason that fits.
    print()
    print("  not joined, by reason")
    left = frame[~join_ok]
    no_key = left["gbif_species_key"].isna()
    fuzzy = ~no_key & left["gbif_match_type"].isin(REVIEW_MATCH_TYPES)
    unclear = ~no_key & ~fuzzy & left["gbif_status"].isin(REVIEW_STATUSES)
    placeholder = (~no_key & ~fuzzy & ~unclear
                   & left["scientific_name"].str.contains(PLACEHOLDER))
    reasons = [("no species key", no_key.sum()),
               ("close spelling", fuzzy.sum()),
               ("ambiguous or misapplied", unclear.sum()),
               ("placeholder name", placeholder.sum())]
    reasons.append(("other", len(left) - sum(n for _, n in reasons)))
    for label, n in reasons:
        print(f"    {label:24s} {int(n):7,}")

    print()
    print("  how the name matched")
    print("    EXACT = same name, VARIANT = a spelling GBIF counts as the same")
    print("    name (often only the Latin ending differs), CANONICAL = matched on")
    print("    the bare name, ignoring author or rank, AMBIGUOUS = several names")
    print("    fit and GBIF could not choose, HIGHERRANK = only a level above")
    print("    (genus, or the species for a population), NONE = no match,")
    print("    UNSUPPORTED = a name GBIF can never match, such as a placeholder")
    for value, n in answered["gbif_match_type"].value_counts(dropna=False).items():
        print(f"    {str(value):24s} {n:7,}")

    print()
    print("  what GBIF thinks of the name")
    print("    ACCEPTED = current name, PROVISIONALLY_ACCEPTED = treated as")
    print("    current but doubtful, SYNONYM = outdated name that GBIF points to")
    print("    a newer one, AMBIGUOUS_SYNONYM = a name that points to more than")
    print("    one species, MISAPPLIED = a name once used by mistake for another")
    print("    species, BARE_NAME = a name with no taxonomic use, <NA> = no match")
    for value, n in answered["gbif_status"].value_counts(dropna=False).items():
        print(f"    {str(value):24s} {n:7,}")

    print()
    print("  higher rank matches, by BCSEE level")
    higher = answered[answered["gbif_match_type"] == "HIGHERRANK"]
    table = higher.groupby(["classification_level", "gbif_rank"]).size()
    for (level, rank), n in table.items():
        rank = str(rank).lower()
        article = "an" if rank[0] in "aeiou" else "a"
        print(f"    {level:12s} matched to {article + ' ' + rank:15s} {n:7,}")

    print()
    print("  BCSEE entries that can be joined (join_ok), by group")
    groups = frame.groupby("name_category")["join_ok"]
    for name, s in groups.agg(["sum", "count"]).sort_values("count",
                                                             ascending=False).iterrows():
        print(f"    {str(name)[:32]:34s} {int(s['sum']):6,} of {int(s['count']):6,}"
              f"  ({s['sum'] / s['count']:.0%})")

    # Several BCSEE entries on one GBIF species is expected for subspecies
    # and populations, which all point to their species. Two BCSEE entries
    # that are both full species landing on one GBIF species is different:
    # BCSEE splits what GBIF lumps together, and joining would give one GBIF
    # observation two statuses.
    print()
    species_only = answered[(answered["classification_level"] == "Species")
                            & usable]
    shared = species_only.groupby("gbif_species_key").size()
    print(f"  GBIF species that more than one BCSEE species points to: "
          f"{int((shared > 1).sum()):,}")
    for key in shared[shared > 1].index[:5]:
        names = species_only.loc[species_only["gbif_species_key"] == key,
                                 "scientific_name"].tolist()
        print(f"    {key}: {', '.join(names)}")

    report_second(frame, second)
    report_placeholders(frame)
    report_review(frame)


def report_placeholders(frame):
    """
    List every BCSEE name the PLACEHOLDER rule treats as a placeholder, so a
    person can check that none of them is a real species.
    """
    names = frame.loc[frame["scientific_name"].str.contains(PLACEHOLDER),
                      "scientific_name"].sort_values()
    print()
    print(f"  names treated as placeholders (check none is a real species): "
          f"{len(names):,}")
    for name in names:
        print(f"    {name}")


def report_review(frame):
    """
    List every row a person should look at before it can be joined.
    """
    review = frame[frame["needs_review"]].sort_values("scientific_name")
    print()
    print(f"  needs review: {len(review):,}")
    print("    BCSEE name -> GBIF name [match type, confidence]")
    for _, row in review.iterrows():
        print(f"    {row['scientific_name']} -> {row['gbif_canonical_name']} "
              f"[{row['gbif_match_type']}, {row['gbif_confidence']}]")


def report_second(frame, second):
    """
    Print what the name-only question changed.

    Every replaced answer is listed, and so is every answer turned down by
    the class or order check, with both sides' class and order, so that a
    naming difference missing from CLASS_NAMES is easy to spot.
    """
    replaced = [s for s in second if s["rejected"] is None]
    print()
    print("  second question, name only, for names without an exact match")
    print(f"    asked                               {len(second):7,}")
    print(f"    answer replaced                     {len(replaced):7,}")
    for reason in ["not exact", "kingdom", "class"]:
        n = sum(s["rejected"] == reason for s in second)
        label = "class or order" if reason == "class" else reason
        print(f"    kept first, name-only {label:14s}{n:7,}")

    print()
    print("  replaced answers: BCSEE name, GBIF name with hints -> name only")
    for s in replaced:
        old = s["first"].get("scientificName") or "(no match)"
        print(f"    {frame.at[s['row'], 'scientific_name']}: "
              f"{old} -> {s['second'].get('scientificName')}")

    print()
    print("  kept first, name-only class or order disagreed:")
    print("    BCSEE name [BCSEE class / order] -> GBIF name [GBIF class / order]")
    for s in second:
        if s["rejected"] != "class":
            continue
        row, other = frame.loc[s["row"]], s["second"]
        print(f"    {row['scientific_name']} [{row['class']} / {row['order']}] -> "
              f"{other.get('scientificName')} "
              f"[{other.get('class')} / {other.get('order')}]")


def main():
    """Read the BCSEE list, ask GBIF about every name, and write the table."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", default="/home/songyanf/bcbn/data",
                        help="Where bcsee_status.parquet is and where the "
                             "output goes. Defaults to the server's data "
                             "folder, like the other pipeline scripts.")
    parser.add_argument("--refresh", action="store_true",
                        help="Ask GBIF about every name again, ignoring "
                             "answers saved by earlier runs.")
    args = parser.parse_args()

    data = Path(args.data_dir).expanduser()
    raw = data / "bcsee_raw"
    raw.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("READING")
    print("=" * 70)
    print()
    rows, left_out = read_bcsee(data / "bcsee_status.parquet")

    print()
    print("=" * 70)
    print("ASKING GBIF")
    print("=" * 70)
    print()
    rows, second, failed = match_all(rows, raw / "gbif_match_answers_v2.jsonl",
                                     args.refresh)
    rows = add_flags(rows)
    rows["matched_on"] = dt.date.today().isoformat()

    print()
    print("=" * 70)
    print("WRITING")
    print("=" * 70)
    print()
    write_parquet(rows, data / "bcsee_gbif_match.parquet")
    report(rows, second, left_out)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
