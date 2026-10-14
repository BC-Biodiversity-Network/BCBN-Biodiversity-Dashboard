"""
make_species_key_merge.py

Decide which pairs of specieskeys that share a species name are the same
species, and write them as a merge list for build_species_tables.py
(--key-merge). Only pairs we are sure about are merged; anything uncertain
stays as two separate species.

Input:  duplicate_species_lookup.csv, from
        backend/exploration/gbif/lookup_duplicate_species_keys.py: one row per
        key, with the Catalogue of Life status and authorship of each key.
        species_key_merge_manual.csv (optional, next to this script): pairs
        checked by hand, with columns old_key, kept_key, species, evidence.
Output, next to the input:
        species_key_merge.csv       old_key, kept_key, species, reason
        species_key_not_merged.csv  species, keys, reason (kept separate)
If species_key_merge.csv is already there from an earlier run, it is read
first and the pairs newly merged (or no longer merged) are printed.

A pair is merged automatically only if all of these hold:
  1. The name has exactly two keys, one starting with "V" and one not. (The
     V keys are names the Catalogue of Life merged in from another checklist
     to fill gaps.)
  2. Both keys were found in the Catalogue of Life.
  3. Same kingdom, and same phylum. A missing phylum on either key counts
     as the same when the family is the same.
  4. At least one author in common, after normalising the spelling: case,
     accents ("Müller" = "Muller" = "Mueller"), spaces and punctuation
     ("DeGeer" = "De Geer"), initials before a surname ("O. F. Müller" =
     "Muller") or after it ("Herbst, J.F.W." = "Herbst"), years and brackets
     are ignored, an abbreviation matches the full name ("Kütz." =
     "Kuetzing", "L." = "Linnaeus"), and a name of several words matches
     its first word ("Nees von Esenbeck" = "Nees"). Authors in brackets and
     on both sides of "ex" all count, so "Parry ex Engelm." and "Engelm."
     share an author.
     If either key has no authorship, there is nothing to compare, and the
     pair is merged when the genus and the family are the same instead.
  The reason column says which of these let the pair through: "same
  author", "same author after normalising" (every author matches), "shares
  an author" (only some do), or "one authorship empty, same genus and
  family", with "; phylum missing, same family" added when rule 3 needed
  it. Pairs that only match after normalising are printed, so they can be
  checked.

Which key is kept: the accepted one over a provisionally accepted one; if
both have the same status, the one whose genus is the first word of the
species name; if still tied, the one with more records; then the key that
does not start with "V".

Pairs checked by hand go in species_key_merge_manual.csv and are added with
reason "manual". A manual row replaces an automatic decision on the same
key. The script stops if the list would map a key twice, or map a key onto
a key that is itself mapped away, so the merge is always one step.

Run (from backend/):
    python pipeline/gbif/make_species_key_merge.py --lookup ~/bcbn/data/gbif_tables/duplicate_species_lookup.csv
"""

import argparse
import csv
import os
import re
import sys
import unicodedata
from collections import defaultdict

DEFAULT_LOOKUP = "~/bcbn/data/gbif_tables/duplicate_species_lookup.csv"
DEFAULT_MANUAL = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "species_key_merge_manual.csv")
MERGE_NAME = "species_key_merge.csv"
NOT_MERGED_NAME = "species_key_not_merged.csv"

# Statuses in order of preference for the key that is kept.
STATUS_ORDER = {"accepted": 0, "provisionally accepted": 1}

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

# Words that join authors rather than being part of a name.
JOINING_WORDS = r"\s*(?:,|&|\bet\b|\band\b|\bex\b|\bin\b)\s*"

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


def author_list(authorship):
    """Return the authors in an authorship as a list of (name, first word,
    abbreviated), ignoring years and brackets, and counting authors in
    brackets and on both sides of "ex". Initials written after a surname
    ("Herbst, J.F.W.", "Linnaeus, C.") are given names and are dropped; an
    authorship made only of initials ("L.") is kept."""
    text = re.sub(r"\d+", " ", authorship or "")
    text = text.replace("(", ",").replace(")", ",")
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


def shares_an_author(left, right):
    """Say whether two author lists have at least one author in common."""
    return any(same_author(a, b) for a in left for b in right)


def is_v_key(key):
    """Return True for a key starting with "V"."""
    return key.upper().startswith("V")


def choose_kept(pair, species):
    """Return (kept row, old row, why) for a pair of key rows."""
    genus = species.split()[0] if species else ""

    def preference(row):
        return (STATUS_ORDER.get(row["status"], 2),
                0 if row["genus"] == genus else 1,
                -row["record_count"],
                1 if is_v_key(row["specieskey"]) else 0,
                row["specieskey"])

    kept, old = sorted(pair, key=preference)
    if STATUS_ORDER.get(kept["status"], 2) != STATUS_ORDER.get(old["status"], 2):
        why = f"kept the {kept['status']} key"
    elif (kept["genus"] == genus) != (old["genus"] == genus):
        why = "kept the key whose genus matches the name"
    elif kept["record_count"] != old["record_count"]:
        why = "kept the key with more records"
    else:
        why = "kept the key that does not start with V"
    return kept, old, why


def read_lookup(path):
    """Read the lookup CSV and return its rows grouped by species name."""
    with open(path, newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    needed = {"species", "specieskey", "kingdom", "phylum", "genus",
              "record_count", "status", "authorship", "error"}
    missing = needed - set(rows[0]) if rows else set()
    if missing:
        raise SystemExit(f"{path} has no column(s) {sorted(missing)}")
    names = defaultdict(list)
    for row in rows:
        row["record_count"] = int(float(row["record_count"] or 0))
        names[row["species"]].append(row)
    return names


def same_place(a, b):
    """Return (same, note): whether two keys are in the same kingdom and
    phylum, and a note when a missing phylum was let through because the
    family is the same."""
    if a["kingdom"] != b["kingdom"]:
        return False, ""
    if a["phylum"] == b["phylum"]:
        return True, ""
    if (not a["phylum"] or not b["phylum"]) and a["family"] and a["family"] == b["family"]:
        return True, "phylum missing, same family"
    return False, ""


def decide(species, keys):
    """Decide one name. Returns ("merge", kept, old, reason, normalised) or
    ("separate", None, None, reason, False). normalised is True when the
    authorships are written differently but every author matches."""
    keys_text = ", ".join(r["specieskey"] for r in keys)
    if len(keys) != 2 or sum(is_v_key(r["specieskey"]) for r in keys) != 1:
        return ("separate", None, None,
                f"not one V key and one other key ({keys_text})", False)
    if any(r["error"] for r in keys):
        return ("separate", None, None, "lookup failed: " + "; ".join(
            f"{r['specieskey']} {r['error']}" for r in keys if r["error"]), False)
    a, b = keys
    same, place_note = same_place(a, b)
    if not same:
        return ("separate", None, None,
                f"different kingdom or phylum: {a['specieskey']} {a['kingdom']}/"
                f"{a['phylum'] or '(no phylum)'} {a['family']} vs {b['specieskey']} "
                f"{b['kingdom']}/{b['phylum'] or '(no phylum)'} {b['family']}", False)

    left, right = author_list(a["authorship"]), author_list(b["authorship"])
    normalised = False
    if not left or not right:
        if not (a["genus"] and a["genus"] == b["genus"]
                and a["family"] and a["family"] == b["family"]):
            return ("separate", None, None,
                    f"one authorship empty and genus or family differ: {a['specieskey']} "
                    f"{a['genus']}/{a['family']} vs {b['specieskey']} {b['genus']}/{b['family']}",
                    False)
        match = "one authorship empty, same genus and family"
    elif a["authorship"].strip() == b["authorship"].strip():
        match = "same author"
    elif all_authors_match(left, right):
        match = "same author after normalising"
        normalised = True
    elif shares_an_author(left, right):
        match = "shares an author"
    else:
        return ("separate", None, None,
                f"no author in common: {a['specieskey']} '{a['authorship']}' vs "
                f"{b['specieskey']} '{b['authorship']}'", False)
    if place_note:
        match += f"; {place_note}"
    kept, old, why = choose_kept(keys, species)
    return ("merge", kept, old, f"{match}; {why}", normalised)


def read_previous(path):
    """Return the pairs in an existing merge list as {old_key: (kept_key,
    species, reason)}, or None if there is no file yet."""
    if not os.path.exists(path):
        return None
    with open(path, newline="", encoding="utf-8-sig") as handle:
        return {r["old_key"]: (r["kept_key"], r["species"], r["reason"])
                for r in csv.DictReader(handle)}


def read_manual(path):
    """Read the hand-checked pairs, if the file exists, as a list of dicts."""
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    missing = {"old_key", "kept_key", "species", "evidence"} - set(rows[0]) if rows else set()
    if missing:
        raise SystemExit(f"{path} has no column(s) {sorted(missing)}")
    return rows


def check_one_step(merge):
    """Stop if a key is mapped twice, or mapped onto a key that is itself
    mapped away."""
    olds = [m["old_key"] for m in merge]
    twice = sorted({k for k in olds if olds.count(k) > 1})
    chained = sorted({m["kept_key"] for m in merge} & set(olds))
    if twice or chained:
        raise SystemExit(f"The merge list is not one step: mapped twice {twice}, "
                         f"mapped onto a key that is also mapped away {chained}")


def main():
    """Write the merge list and the list of names kept separate."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("--lookup", default=DEFAULT_LOOKUP,
                        help=f"duplicate_species_lookup.csv (default {DEFAULT_LOOKUP})")
    parser.add_argument("--manual", default=DEFAULT_MANUAL,
                        help="the hand-checked pairs (default: species_key_merge_manual.csv "
                             "next to this script)")
    args = parser.parse_args()

    lookup_path = os.path.expanduser(args.lookup)
    folder = os.path.dirname(os.path.abspath(lookup_path))
    names = read_lookup(lookup_path)

    merge, separate, normalised = [], [], []
    for species in sorted(names):
        keys = names[species]
        verdict, kept, old, reason, after_normalising = decide(species, keys)
        if verdict == "merge":
            merge.append({"old_key": old["specieskey"], "kept_key": kept["specieskey"],
                          "species": species, "reason": reason})
            if after_normalising:
                normalised.append((species, kept, old))
        else:
            separate.append({"species": species,
                             "keys": ", ".join(r["specieskey"] for r in keys),
                             "reason": reason})

    # Hand-checked pairs replace any automatic decision on the same key.
    manual = read_manual(args.manual)
    for row in manual:
        replaced = [m for m in merge if m["old_key"] in (row["old_key"], row["kept_key"])]
        merge = [m for m in merge if m not in replaced]
        separate = [s for s in separate if s["species"] != row["species"]]
        merge.append({"old_key": row["old_key"], "kept_key": row["kept_key"],
                      "species": row["species"], "reason": "manual"})
        for m in replaced:
            print(f"NOTE: manual row {row['old_key']} -> {row['kept_key']} replaces "
                  f"{m['old_key']} -> {m['kept_key']} ({m['reason']})")
    check_one_step(merge)

    merge_path = os.path.join(folder, MERGE_NAME)
    previous = read_previous(merge_path)
    with open(merge_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["old_key", "kept_key", "species", "reason"])
        writer.writeheader()
        writer.writerows(sorted(merge, key=lambda m: (m["species"], m["old_key"])))
    separate_path = os.path.join(folder, NOT_MERGED_NAME)
    with open(separate_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["species", "keys", "reason"])
        writer.writeheader()
        writer.writerows(separate)

    # Summary.
    records = {r["specieskey"]: r["record_count"] for keys in names.values() for r in keys}
    moved = sum(records.get(m["old_key"], 0) for m in merge)
    auto = [m for m in merge if m["reason"] != "manual"]
    print(f"Lookup:  {lookup_path} ({len(names):,} names)")
    print(f"Manual:  {args.manual} ({len(manual)} rows)")
    print(f"Written: {merge_path}")
    print(f"         {separate_path}\n")
    print(f"Merged: {len(merge):,} pairs ({len(auto):,} automatic, {len(manual)} manual), "
          f"{moved:,} records move to the kept key")
    kept_v = sum(is_v_key(m["kept_key"]) for m in auto)
    print(f"  the V key is the one kept in {kept_v:,} of the automatic pairs")
    matches, keeps = defaultdict(int), defaultdict(int)
    for m in auto:
        parts = m["reason"].split("; ")
        matches["; ".join(parts[:-1])] += 1
        keeps[parts[-1]] += 1
    print("  why merged:")
    for why, n in sorted(matches.items(), key=lambda kv: -kv[1]):
        print(f"    {why}: {n:,}")
    print("  which key was kept:")
    for why, n in sorted(keeps.items(), key=lambda kv: -kv[1]):
        print(f"    {why}: {n:,}")

    print(f"\nMerged only after normalising the authors ({len(normalised)}), please check:")
    for species, kept, old in normalised:
        print(f"  {species}: {old['specieskey']} '{old['authorship']}' -> "
              f"{kept['specieskey']} '{kept['authorship']}'")

    if previous is not None:
        now = {m["old_key"]: m for m in merge}
        new = [now[k] for k in sorted(now, key=lambda k: now[k]["species"]) if k not in previous]
        gone = [(k, *previous[k]) for k in sorted(previous, key=lambda k: previous[k][1])
                if k not in now]
        print(f"\nNewly merged compared with the previous {MERGE_NAME} ({len(new)}), "
              f"please check:")
        by_old = {r["specieskey"]: r for keys in names.values() for r in keys}
        for m in new:
            old, kept = by_old.get(m["old_key"], {}), by_old.get(m["kept_key"], {})
            print(f"  {m['species']}: {m['old_key']} '{old.get('authorship', '')}' -> "
                  f"{m['kept_key']} '{kept.get('authorship', '')}'  [{m['reason']}]")
        print(f"No longer merged ({len(gone)}):")
        for old_key, kept_key, species, reason in gone:
            print(f"  {species}: {old_key} -> {kept_key}  (was: {reason})")
    else:
        print(f"\nNo earlier {MERGE_NAME} to compare with.")

    print(f"\nKept separate ({len(separate):,}):")
    kinds = defaultdict(list)
    for s in separate:
        kinds[s["reason"].split(":")[0].split(" (")[0]].append(s)
    for kind, rows in sorted(kinds.items(), key=lambda kv: -len(kv[1])):
        print(f"  {kind}: {len(rows):,}")
        for s in rows:
            print(f"    {s['species']}: {s['reason']}")

    if manual:
        print(f"\nManual rows ({len(manual)}):")
        for row in manual:
            print(f"  {row['species']}: {row['old_key']} -> {row['kept_key']}  ({row['evidence']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
