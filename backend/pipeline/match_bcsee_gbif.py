"""
Match every BCSEE species name to its GBIF species number.

BCSEE gives each species its own code (element_code) and GBIF gives each
species its own number (speciesKey). The two systems were built separately,
so nothing links them yet. This script asks GBIF's name matching service,
one BCSEE name at a time, "which of your species is this?", and saves the
answers as a lookup table from element_code to speciesKey.

Everything else from the Conservation Data Centre can then reach GBIF
through that one table: the status history and the public occurrence areas
both carry the same element_code.

What is sent: each name is asked about in up to two steps.

  1. The scientific name, plus BCSEE's kingdom, phylum, class, order and
     family as hints. The hints keep GBIF from matching a name to a
     look-alike in the wrong group (a water mite to a wasp, a bee to an
     orchid).
  2. If that answer is not an exact match, the name alone. Hints can do
     harm too: where BCSEE files a species in a different family or
     kingdom than GBIF, GBIF prefers a similar name inside the hinted
     family over the exact name elsewhere (Irpex lacteus came back as
     Irpex lacer). The name-only answer replaces the first one only if it
     is an exact match, its kingdom agrees with BCSEE's group, and, for
     animals, its class agrees with BCSEE's class (or its order, when GBIF
     gives no class). Column gbif_answer_from says which answer was kept.

What is not sent: the 632 ecological communities and 2 ecological systems.
GBIF has no such thing, so they can never match.

Names are sent exactly as BCSEE writes them. For a population such as
"Oncorhynchus tshawytscha pop. 36", GBIF drops the "pop. 36" by itself and
returns the species, marked as a match at a higher rank.

Which GBIF number to join on: use gbif_species_key, and only in rows where
join_ok is true. For a subspecies it is the number of the species it
belongs to, and for an outdated name GBIF has already pointed it at the
name it accepts today. GBIF observations almost never record a subspecies,
so joining on the subspecies number would miss nearly everything.

join_ok is true only for an exact match, or for a population, subspecies
or variety that GBIF matched to its species, and never for a placeholder
name such as "Cottus sp. 9" or "Steiroxys cf. strepens". Close-spelling
(FUZZY) matches are left out for now, even at confidence 100: most are the
same species spelled with a different Latin ending, but some are a
different species (Russula albida came back as Russula alpium), and the
two cannot yet be told apart without a person checking. needs_review marks
those rows, and the few other answers that look odd, for that check.

GBIF's answers are saved as they arrive, so a run that stops halfway picks
up where it left off, and a second run asks GBIF only about names that
changed. Use --refresh to ask about every name again.

Output:
    bcsee_gbif_match.parquet   one row per BCSEE species, subspecies,
                               variety or population, with what GBIF matched
                               it to, how sure GBIF was, and the join_ok and
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
import requests


MATCH_URL = "https://api.gbif.org/v1/species/match"
HEADERS = {"User-Agent": "BCBN-Dashboard (UBC Biodiversity Research Centre)"}

# The kinds of BCSEE entry that are sent to GBIF. Everything else is an
# ecological community or ecological system, which GBIF does not have.
LEVELS_TO_MATCH = ["Species", "Subspecies", "Variety", "Population"]

# BCSEE column name on the left, the name GBIF expects on the right.
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

# BCSEE class names that GBIF writes differently, with the GBIF classes
# each may appear as. Without this, a name-only answer for any reptile,
# turtle, shark, lamprey or earthworm would fail the class check. A class
# not listed here must match GBIF's exactly. GBIF gives ray-finned fish no
# class at all; for those the order is compared instead.
CLASS_NAMES = {
    "Chelonia": {"Testudines"},
    "Reptilia": {"Squamata"},
    "Oligochaeta": {"Clitellata"},
    "Petromyzontida": {"Petromyzonti"},
    "Chondrichthyes": {"Elasmobranchii", "Holocephali"},
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

# The fields kept from each GBIF answer, GBIF's name on the left and the
# output column on the right. Any field GBIF leaves out becomes a blank.
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

# The output columns that hold whole numbers. Every other column is text.
NUMBER_COLUMNS = {"gbif_confidence", "gbif_usage_key",
                  "gbif_accepted_usage_key", "gbif_species_key"}

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
    params = {"name": row["scientific_name"]}
    for ours, theirs in HINTS.items():
        value = row[ours]
        if isinstance(value, str) and value.strip():
            params[theirs] = value.strip()
    return params


def cache_key(element_code, params):
    """
    Label one question so its answer can be found again later.

    The label contains the code and everything sent. If BCSEE changes a
    name or a hint in a later pull, the label changes too, so that entry is
    asked again instead of reusing an answer to a different question.
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


def check_name_only(row, answer):
    """
    Decide whether a name-only answer can replace the answer with hints.

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
            name_only = {"name": params["name"]}
            if answer is None or answer.get("matchType") == "EXACT" or name_only == params:
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
        answer = answers.get(key)
        if answer is None:
            continue
        source = "with_hints"
        if i in second_keys and second_keys[i][0] in answers:
            other = answers[second_keys[i][0]]
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
    return rows, second, failed


def add_flags(rows):
    """
    Add join_ok and needs_review. GBIF's answers are left as they are, so
    it stays visible why a row cannot be joined.

    join_ok: safe to join to GBIF observations on gbif_species_key. The
    match is exact, or GBIF gave the species for a BCSEE level below
    species, and the name is not a placeholder.

    needs_review: a person should look at it. Every close-spelling match,
    every placeholder name GBIF gave a species number anyway, and a BCSEE
    species that GBIF matched to a species "at a higher rank".
    """
    has_key = rows["gbif_species_key"].notna().to_numpy()
    match = rows["gbif_match_type"].fillna("").to_numpy()
    rank = rows["gbif_rank"].fillna("").to_numpy()
    level = rows["classification_level"]
    placeholder = rows["scientific_name"].str.contains(PLACEHOLDER).to_numpy()
    species_above = (match == "HIGHERRANK") & (rank == "SPECIES")
    right_match = (match == "EXACT") | (species_above & level.isin(BELOW_SPECIES).to_numpy())

    rows["join_ok"] = has_key & right_match & ~placeholder
    rows["needs_review"] = ((match == "FUZZY")
                            | (placeholder & has_key)
                            | (species_above & (level == "Species").to_numpy()))
    return rows


def write_parquet(frame, path):
    """
    Write the table, and do it so a failed run cannot leave a broken file.

    The table goes to a temporary file first and is renamed only once it is
    complete, so the output is always a whole file from some run.
    """
    tmp = path.with_suffix(path.suffix + ".partial")
    con = duckdb.connect()
    con.register("staging", frame)
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
    fuzzy = ~no_key & (left["gbif_match_type"] == "FUZZY")
    placeholder = (~no_key & ~fuzzy
                   & left["scientific_name"].str.contains(PLACEHOLDER))
    for label, n in [("no species number", no_key.sum()),
                     ("close spelling (FUZZY)", fuzzy.sum()),
                     ("placeholder name", placeholder.sum()),
                     ("other", len(left) - no_key.sum() - fuzzy.sum()
                      - placeholder.sum())]:
        print(f"    {label:24s} {int(n):7,}")

    print()
    print("  how the name matched")
    print("    EXACT = same name, FUZZY = close spelling, HIGHERRANK = only a")
    print("    level above (genus, or the species for a population), NONE = no match")
    for value, n in answered["gbif_match_type"].value_counts(dropna=False).items():
        print(f"    {str(value):20s} {n:7,}")

    print()
    print("  what GBIF thinks of the name")
    print("    ACCEPTED = current name, SYNONYM = outdated name that GBIF")
    print("    points to a newer one, HETEROTYPIC_SYNONYM = a synonym first")
    print("    described as a separate species and later merged into another,")
    print("    DOUBTFUL = GBIF is unsure the name is valid")
    for value, n in answered["gbif_status"].value_counts(dropna=False).items():
        print(f"    {str(value):20s} {n:7,}")

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
    rows, second, failed = match_all(rows, raw / "gbif_match_answers.jsonl",
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
