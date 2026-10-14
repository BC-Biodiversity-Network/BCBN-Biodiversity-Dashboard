"""
make_species_key_merge.py

Decide which pairs of specieskeys that share a species name are the same
species, and write them as a merge list for build_species_tables.py
(--key-merge). Only pairs that pass every rule are merged; anything else
stays as two separate species.

Input:  duplicate_species_lookup.csv, from
        backend/exploration/gbif/lookup_duplicate_species_keys.py: one row per
        key, with its classification (kingdom to species), record count, and
        the Catalogue of Life status and authorship.
        species_key_merge_manual.csv (next to this script): pairs checked by
        hand, with columns old_key, kept_key, species, evidence. It may be
        empty (header only).
Output, next to the input:
        species_key_merge.csv       one row per merged pair: old_key (the key
                                    not kept), kept_key, species, the status,
                                    record count and authorship of both keys,
                                    author_match, year_match, kept_reason and
                                    merge_reason, so a merge can be switched
                                    or undone later
        species_key_not_merged.csv  one row per pair kept apart: both keys,
                                    the rule that stopped it and, for a
                                    conflict, the levels and the two values
If species_key_merge.csv is already there from an earlier run, it is read
first, and the pairs newly merged, no longer merged, or merged with the
other key kept are printed.

A pair of keys is merged automatically only if all of these hold:
  1. Iron rule: kingdom, phylum, class, order, family, genus and species
     match letter for letter wherever both keys have a value. The species
     name must always match exactly.
  2. Empty values: taking the two keys together, at most one of those seven
     levels has an empty value (on one side or both), and that level is not
     genus or species. A key with no order and the other with no family is
     two levels, so that pair is not merged.
  3. Both keys were found in the lookup (no lookup error).
  The checks run in the order lookup, iron rule, empty values; a pair kept
  apart is reported under the first rule it fails.
Authors and years are not used to decide anything. Two columns record them
for review: author_match (yes when the validly publishing authors match
after normalising the spelling, see below; no otherwise, including when
either authorship is empty) and year_match (yes when both authorships give
the same year, no when both give a year and the years differ, empty when
either gives none).

Which key is kept: the accepted one over any other status; then the one
with more records; then the key that sorts first alphabetically, only so
that every run gives the same answer.

Pairs checked by hand go in species_key_merge_manual.csv. A manual row may
skip the iron rule and the empty-values rule, and its kept_key is used as
given, but both of its keys must be in the lookup with exactly the same
species name as each other and as the row's species column, or the script
stops with an error. A manual row replaces an automatic decision on the same
keys. The script also stops if the list would map a key twice, or map a key
onto a key that is itself mapped away, so the merge is always one step.

How author_match normalises the authors (the same normalising used before):
  - In "A ex B" only B counts (the author after the last "ex"), inside and
    outside brackets; in "A in B" only A counts.
  - The authors outside the brackets must all match, one to one; the
    bracketed (basionym) authors too when both sides have brackets. A
    zoological name written entirely in brackets, like "(Linnaeus, 1758)",
    has its bracketed authors as its main authors.
  - Case, accents ("Müller" = "Muller" = "Mueller"), spaces and punctuation
    ("DeGeer" = "De Geer"), initials before a surname ("O. F. Müller" =
    "Muller") or after it ("Herbst, J.F.W." = "Herbst") and years are
    ignored, an abbreviation matches the full name ("Kütz." = "Kuetzing",
    "L." = "Linnaeus"), and a name of several words matches its first word
    ("Nees von Esenbeck" = "Nees").

Run (from backend/):
    python pipeline/gbif/make_species_key_merge.py --lookup ~/bcbn/data/gbif_tables/duplicate_species_lookup.csv
"""

import argparse
import csv
import os
import re
import sys
import unicodedata
from collections import Counter, defaultdict

DEFAULT_LOOKUP = "~/bcbn/data/gbif_tables/duplicate_species_lookup.csv"
DEFAULT_MANUAL = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "species_key_merge_manual.csv")
MERGE_NAME = "species_key_merge.csv"
NOT_MERGED_NAME = "species_key_not_merged.csv"

# The seven levels the iron rule compares, from the top down.
LEVELS = ["kingdom", "phylum", "class", "order", "family", "genus", "species"]

# Levels that may never be empty on either key of a merged pair.
NEVER_EMPTY = {"genus", "species"}

# How many of the seven levels may be empty, taking both keys together.
MAX_EMPTY_LEVELS = 1

# The rules a pair can be stopped by, as written in species_key_not_merged.csv.
RULE_NOT_A_PAIR = "not exactly two keys"
RULE_LOOKUP = "lookup failed"
RULE_IRON = "iron rule"
RULE_EMPTY = "empty values"

# Standard author abbreviations too short to match by their first letters
# alone ("L." would otherwise also match "Lamarck"). Written as the full
# name; both sides go through the same normalising. Add more here.
AUTHOR_ABBREVIATIONS = {
    "l": "Linnaeus",
    "dc": "de Candolle",
    "lam": "Lamarck",
    "fr": "Fries",
    "sm": "Smith",
    "ach": "Acharius",
    "michx": "Michaux",
}

# An abbreviation at least this long matches any name that starts with it,
# so "Kütz." matches "Kuetzing" and "Brightw." matches "Brightwell".
MIN_PREFIX = 4

# Words and marks that separate authors within one part of an authorship.
JOINING_WORDS = r"\s*(?:,|&|\bet\b|\band\b)\s*"

# A year in an authorship.
YEAR = re.compile(r"\b(1[5-9]\d\d|20\d\d)\b")

# Small words at the start of a surname that are not the name itself, so
# the first real word of "De Geer" is "geer", not "de".
PARTICLES = {"de", "von", "van", "der", "den", "du", "la", "le", "da", "di",
             "del", "d", "ter", "ten", "zu", "y"}


def plain_letters(text):
    """Return text lowercased, without accents, with "ae", "oe" and "ue"
    written as their single vowel, so "Müller", "Muller" and "Mueller" (and
    "Kütz." and "Kuetz.") come out the same."""
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    for pair, vowel in (("ae", "a"), ("oe", "o"), ("ue", "u")):
        text = text.replace(pair, vowel)
    return text


def name_words(text):
    """Return the words of a name, normalised, without punctuation."""
    words = [re.sub(r"[^a-z]", "", w) for w in re.split(r"[\s.]+", plain_letters(text))]
    return [w for w in words if w]


def is_initials(text):
    """Say whether text is only initials, such as "J.F.W." or "C."."""
    words = name_words(text)
    return bool(words) and all(len(w) == 1 for w in words)


def one_author(text):
    """Return (name, first word, abbreviated) for one author, such as
    "O. F. Müller".

    Initials (single letters before a longer word) are dropped and the rest
    joined without spaces, so "De Geer" and "DeGeer" are both "degeer".
    The first word is the first one that is not a particle such as "de" or
    "von", so "Nees von Esenbeck" can match "Nees". abbreviated is True if
    the name ends with a full stop, as in "Kütz."."""
    abbreviated = text.strip().endswith(".")
    words = name_words(text)
    if any(len(w) > 1 for w in words):
        words = [w for w in words if len(w) > 1]
    first = next((w for w in words if w not in PARTICLES), words[0] if words else "")
    return "".join(words), first, abbreviated


def publishing_part(text):
    """Return the validly publishing part of one piece of an authorship:
    what follows the last "ex" ("Douglas ex Hook." gives "Hook."), and
    what comes before "in" ("Smith in Jones" gives "Smith")."""
    text = re.split(r"\bex\b", text, flags=re.IGNORECASE)[-1]
    return re.split(r"\bin\b", text, flags=re.IGNORECASE)[0]


def author_list(text):
    """Return the authors in one piece of an authorship (no brackets) as a
    list of (name, first word, abbreviated). Years are ignored. Initials
    written after a surname ("Herbst, J.F.W.", "Linnaeus, C.") are given
    names and are dropped; an author made only of initials ("L.") is
    kept."""
    text = YEAR.sub(" ", publishing_part(text or ""))
    parts = [p for p in re.split(JOINING_WORDS, text, flags=re.IGNORECASE)
             if p and p.strip(" ,.")]
    authors = []
    for part in parts:
        if authors and is_initials(part):
            continue
        author = one_author(part)
        if author[0]:
            authors.append(author)
    return authors


def parse_authorship(authorship):
    """Split an authorship into (main authors, basionym authors, years).

    The basionym authors are the ones in brackets, the main authors the ones
    after them: "(Mill.) Britton" gives main [Britton], basionym [Mill.].
    A name written entirely in brackets, as zoological names often are
    ("(Stephens, 1836)"), has its bracketed authors as main authors and no
    basionym. basionym is None when there are no brackets to compare.
    years is the set of years written anywhere in the authorship."""
    text = authorship or ""
    years = set(YEAR.findall(text))
    inside = " , ".join(re.findall(r"\(([^()]*)\)", text))
    outside = re.sub(r"\([^()]*\)", " ", text)
    main, basionym = author_list(outside), author_list(inside)
    if not main:
        main, basionym = basionym, []
    return main, (basionym or None), years


def full_name(name):
    """Return the normalised full name for a short standard abbreviation,
    or the name itself."""
    if name in AUTHOR_ABBREVIATIONS:
        return one_author(AUTHOR_ABBREVIATIONS[name])[0]
    return name


def same_author(a, b):
    """Say whether two (name, first word, abbreviated) authors are the same
    person: the same name or first word (after expanding a short standard
    abbreviation), or an abbreviation of at least MIN_PREFIX letters that
    the other name starts with."""
    forms_a = {full_name(a[0]), full_name(a[1])}
    forms_b = {full_name(b[0]), full_name(b[1])}
    if forms_a & forms_b:
        return True
    for short, forms, others in ((a, forms_a, forms_b), (b, forms_b, forms_a)):
        name, _, abbreviated = short
        if abbreviated and len(name) >= MIN_PREFIX and any(o.startswith(name) for o in others):
            return True
    return False


def all_authors_match(left, right):
    """Say whether two author lists name the same authors one to one, in
    any order."""
    if not left or len(left) != len(right):
        return False
    unused = list(right)
    for author in left:
        match = next((other for other in unused if same_author(author, other)), None)
        if match is None:
            return False
        unused.remove(match)
    return True


def is_empty(value):
    """Say whether a classification value is empty (missing or only spaces)."""
    return not (value or "").strip()


def iron_rule_conflicts(a, b):
    """Return the levels where both keys have a value and the values differ,
    as (level, value of a, value of b). The comparison is letter for letter."""
    return [(level, a[level], b[level]) for level in LEVELS
            if not is_empty(a[level]) and not is_empty(b[level]) and a[level] != b[level]]


def empty_levels(a, b):
    """Return the levels that are empty on either key (or both)."""
    return [level for level in LEVELS if is_empty(a[level]) or is_empty(b[level])]


def author_match(a, b):
    """Return "yes" if the two keys' validly publishing authors match after
    normalising (years ignored), otherwise "no". An empty authorship on
    either side gives "no"."""
    main_a, basionym_a, _ = parse_authorship(a["authorship"])
    main_b, basionym_b, _ = parse_authorship(b["authorship"])
    if not main_a or not main_b or not all_authors_match(main_a, main_b):
        return "no"
    if basionym_a and basionym_b and not all_authors_match(basionym_a, basionym_b):
        return "no"
    return "yes"


def year_match(a, b):
    """Return "yes" if both authorships give the same year(s), "no" if both
    give a year and they differ, and "" if either gives none."""
    years_a = parse_authorship(a["authorship"])[2]
    years_b = parse_authorship(b["authorship"])[2]
    if not years_a or not years_b:
        return ""
    return "yes" if years_a == years_b else "no"


def choose_kept(a, b):
    """Return (kept row, old row, kept_reason) for the two key rows of a pair:
    the accepted key first, then the one with more records, then the key that
    sorts first alphabetically."""
    def preference(row):
        """Sort order: accepted first, more records first, then the key."""
        return (0 if row["status"] == "accepted" else 1, -row["record_count"], row["specieskey"])

    kept, old = sorted([a, b], key=preference)
    if (kept["status"] == "accepted") != (old["status"] == "accepted"):
        why = "accepted status"
    elif kept["record_count"] != old["record_count"]:
        why = "more records"
    else:
        why = "same status and record count; first alphabetically"
    return kept, old, why


def decide(keys):
    """Decide one species name. Returns ("merge", details) with details
    {"merge_reason"}, or ("separate", details) with details {"rule",
    "reason", "conflicts", "empty"}."""
    if len(keys) != 2:
        return "separate", {"rule": RULE_NOT_A_PAIR, "conflicts": [], "empty": [],
                            "reason": f"{len(keys)} keys: " + ", ".join(k["specieskey"] for k in keys)}
    a, b = keys
    failed = [k for k in keys if k["error"]]
    if failed:
        return "separate", {"rule": RULE_LOOKUP, "conflicts": [], "empty": [],
                            "reason": "; ".join(f"{k['specieskey']}: {k['error']}" for k in failed)}
    conflicts = iron_rule_conflicts(a, b)
    if conflicts:
        return "separate", {"rule": RULE_IRON, "conflicts": conflicts, "empty": empty_levels(a, b),
                            "reason": "; ".join(f"{level} '{va}' vs '{vb}'"
                                                for level, va, vb in conflicts)}
    empty = empty_levels(a, b)
    if len(empty) > MAX_EMPTY_LEVELS or set(empty) & NEVER_EMPTY:
        why = (f"{len(empty)} levels empty" if len(empty) > MAX_EMPTY_LEVELS
               else f"{', '.join(sorted(set(empty) & NEVER_EMPTY))} empty")
        return "separate", {"rule": RULE_EMPTY, "conflicts": [], "empty": empty,
                            "reason": f"{why}: {', '.join(empty)}"}
    reason = "all seven levels match" if not empty else f"all levels match except {empty[0]}, empty"
    return "merge", {"merge_reason": f"automatic: {reason}"}


def read_lookup(path):
    """Read the lookup CSV and return (rows grouped by species name, rows by
    key). Stops if a needed column is missing."""
    with open(path, newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    needed = set(LEVELS) | {"specieskey", "record_count", "status", "authorship", "error"}
    missing = needed - set(rows[0]) if rows else set()
    if missing:
        raise SystemExit(f"{path} has no column(s) {sorted(missing)}")
    names = defaultdict(list)
    for row in rows:
        row["record_count"] = int(float(row["record_count"] or 0))
        names[row["species"]].append(row)
    return names, {row["specieskey"]: row for row in rows}


MERGE_COLUMNS = [
    "old_key", "kept_key", "species",
    "old_status", "kept_status", "old_record_count", "kept_record_count",
    "old_authorship", "kept_authorship", "author_match", "year_match",
    "kept_reason", "merge_reason",
]

NOT_MERGED_COLUMNS = [
    "species", "key_1", "status_1", "record_count_1", "authorship_1",
    "key_2", "status_2", "record_count_2", "authorship_2",
    "rule", "conflict_levels", "conflict_values_1", "conflict_values_2",
    "empty_levels", "author_match", "year_match", "reason",
]


def merge_row(kept, old, species, kept_reason, merge_reason):
    """Return one row of species_key_merge.csv."""
    return {
        "old_key": old["specieskey"], "kept_key": kept["specieskey"], "species": species,
        "old_status": old["status"], "kept_status": kept["status"],
        "old_record_count": old["record_count"], "kept_record_count": kept["record_count"],
        "old_authorship": old["authorship"], "kept_authorship": kept["authorship"],
        "author_match": author_match(old, kept), "year_match": year_match(old, kept),
        "kept_reason": kept_reason, "merge_reason": merge_reason,
    }


def not_merged_row(species, keys, details):
    """Return one row of species_key_not_merged.csv. For a conflict, the
    levels and the two keys' values are listed in the same order, joined
    with " | "."""
    row = {"species": species, "rule": details["rule"], "reason": details["reason"],
           "conflict_levels": " | ".join(c[0] for c in details["conflicts"]),
           "conflict_values_1": " | ".join(c[1] for c in details["conflicts"]),
           "conflict_values_2": " | ".join(c[2] for c in details["conflicts"]),
           "empty_levels": ", ".join(details["empty"])}
    for number, key in enumerate(keys[:2], start=1):
        row[f"key_{number}"] = key["specieskey"]
        row[f"status_{number}"] = key["status"]
        row[f"record_count_{number}"] = key["record_count"]
        row[f"authorship_{number}"] = key["authorship"]
    if len(keys) == 2:
        row["author_match"] = author_match(*keys)
        row["year_match"] = year_match(*keys)
    return row


def read_manual(path):
    """Read the hand-checked pairs, if the file exists, as a list of dicts.
    A file with only its header row gives an empty list."""
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        missing = {"old_key", "kept_key", "species", "evidence"} - set(reader.fieldnames or [])
        if missing:
            raise SystemExit(f"{path} has no column(s) {sorted(missing)}")
        return [row for row in reader if any((v or "").strip() for v in row.values())]


def check_manual(manual, by_key, path):
    """Stop with a clear error if a manual row's keys are not both in the
    lookup, or their species names are not exactly the same as each other
    and as the row's species column."""
    problems = []
    for row in manual:
        old, kept = by_key.get(row["old_key"]), by_key.get(row["kept_key"])
        if old is None or kept is None:
            absent = [k for k in (row["old_key"], row["kept_key"]) if k not in by_key]
            problems.append(f"{row['old_key']} -> {row['kept_key']}: key(s) {absent} not in "
                            f"the lookup, so the species names cannot be checked")
        elif not (old["species"] == kept["species"] == row["species"]):
            problems.append(f"{row['old_key']} -> {row['kept_key']}: species names differ: "
                            f"'{old['species']}' vs '{kept['species']}' (row says "
                            f"'{row['species']}')")
    if problems:
        raise SystemExit(f"{path}: manual rows refused, a manual merge must join two keys with "
                         f"exactly the same species name:\n  " + "\n  ".join(problems))


def check_one_step(merge):
    """Stop if a key is mapped twice, or mapped onto a key that is itself
    mapped away."""
    olds = [m["old_key"] for m in merge]
    twice = sorted({k for k in olds if olds.count(k) > 1})
    chained = sorted({m["kept_key"] for m in merge} & set(olds))
    if twice or chained:
        raise SystemExit(f"The merge list is not one step: mapped twice {twice}, "
                         f"mapped onto a key that is also mapped away {chained}")


def read_previous(path):
    """Return the pairs in an existing merge list as {species: row}, or None
    if there is no file yet. Works with the older files too, which had a
    single "reason" column."""
    if not os.path.exists(path):
        return None
    with open(path, newline="", encoding="utf-8-sig") as handle:
        return {r["species"]: r for r in csv.DictReader(handle)}


def write_csv(path, columns, rows):
    """Write rows (dicts) to a CSV with the given columns."""
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def main():
    """Write the merge list and the list of pairs kept apart, and print a
    summary."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("--lookup", default=DEFAULT_LOOKUP,
                        help=f"duplicate_species_lookup.csv (default {DEFAULT_LOOKUP})")
    parser.add_argument("--manual", default=DEFAULT_MANUAL,
                        help="the hand-checked pairs (default: species_key_merge_manual.csv "
                             "next to this script)")
    args = parser.parse_args()

    lookup_path = os.path.expanduser(args.lookup)
    folder = os.path.dirname(os.path.abspath(lookup_path))
    names, by_key = read_lookup(lookup_path)
    manual = read_manual(args.manual)
    check_manual(manual, by_key, args.manual)

    merge, separate = [], []
    for species in sorted(names):
        keys = names[species]
        verdict, details = decide(keys)
        if verdict == "merge":
            kept, old, kept_reason = choose_kept(*keys)
            merge.append(merge_row(kept, old, species, kept_reason, details["merge_reason"]))
        else:
            separate.append(not_merged_row(species, keys, details))

    # Hand-checked pairs replace any automatic decision on the same keys.
    for row in manual:
        pair = {row["old_key"], row["kept_key"]}
        replaced = [m for m in merge if {m["old_key"], m["kept_key"]} & pair]
        merge = [m for m in merge if m not in replaced]
        separate = [s for s in separate if not ({s.get("key_1"), s.get("key_2")} & pair)]
        merge.append(merge_row(by_key[row["kept_key"]], by_key[row["old_key"]], row["species"],
                               "chosen by hand", f"manual: {row['evidence']}"))
        for m in replaced:
            print(f"NOTE: manual row {row['old_key']} -> {row['kept_key']} replaces "
                  f"{m['old_key']} -> {m['kept_key']} ({m['merge_reason']})")
    check_one_step(merge)

    merge_path = os.path.join(folder, MERGE_NAME)
    separate_path = os.path.join(folder, NOT_MERGED_NAME)
    previous = read_previous(merge_path)
    merge.sort(key=lambda m: (m["species"], m["old_key"]))
    write_csv(merge_path, MERGE_COLUMNS, merge)
    write_csv(separate_path, NOT_MERGED_COLUMNS, separate)

    # Summary.
    auto = [m for m in merge if m["merge_reason"].startswith("automatic")]
    moved = sum(m["old_record_count"] for m in merge)
    print(f"Lookup:  {lookup_path} ({len(names):,} names, {len(by_key):,} keys)")
    print(f"Manual:  {args.manual} ({len(manual)} rows)")
    print(f"Written: {merge_path}")
    print(f"         {separate_path}\n")
    print(f"Merged: {len(merge):,} pairs ({len(auto):,} automatic, {len(manual)} manual), "
          f"{moved:,} records move to the kept key")
    for label, column in [("why merged", "merge_reason"), ("which key was kept", "kept_reason"),
                          ("author_match", "author_match"), ("year_match", "year_match")]:
        counts = Counter(m[column] or "(empty)" for m in auto)
        print(f"  {label}: " + "; ".join(f"{k} {n:,}" for k, n in counts.most_common()))

    print(f"\nKept apart: {len(separate):,} pairs")
    by_rule = defaultdict(list)
    for s in separate:
        by_rule[s["rule"]].append(s)
    for rule in [RULE_NOT_A_PAIR, RULE_LOOKUP, RULE_IRON, RULE_EMPTY]:
        print(f"  {rule}: {len(by_rule[rule]):,}")
    for rule in [RULE_NOT_A_PAIR, RULE_LOOKUP, RULE_IRON, RULE_EMPTY]:
        if by_rule[rule]:
            print(f"\n{rule} ({len(by_rule[rule])}):")
            for s in by_rule[rule]:
                print(f"  {s['species']}: {s['key_1']} vs {s['key_2']}: {s['reason']}")

    if previous is None:
        print(f"\nNo earlier {MERGE_NAME} to compare with.")
        return 0
    now = {m["species"]: m for m in merge}
    new = [now[s] for s in sorted(now) if s not in previous]
    gone = [previous[s] for s in sorted(previous) if s not in now]
    switched = [(previous[s], now[s]) for s in sorted(now)
                if s in previous and previous[s]["kept_key"] != now[s]["kept_key"]]
    print(f"\nCompared with the previous {MERGE_NAME} ({len(previous)} pairs):")
    print(f"  newly merged ({len(new)}):")
    for m in new:
        print(f"    {m['species']}: {m['old_key']} -> {m['kept_key']}  [{m['merge_reason']}; "
              f"author_match {m['author_match']}, year_match {m['year_match'] or '-'}]")
    print(f"  no longer merged ({len(gone)}):")
    for p in gone:
        why = next((s["rule"] + ": " + s["reason"] for s in separate if s["species"] == p["species"]), "")
        print(f"    {p['species']}: {p['old_key']} -> {p['kept_key']}  "
              f"(was: {p.get('merge_reason') or p.get('reason')}; now: {why})")
    print(f"  merged both times but the other key kept now ({len(switched)}):")
    for p, m in switched:
        print(f"    {m['species']}: kept {p['kept_key']} before, {m['kept_key']} now ({m['kept_reason']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
