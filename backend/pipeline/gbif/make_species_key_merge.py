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

A pair is merged automatically only if all of these hold:
  1. The name has exactly two keys, one starting with "V" and one not. (The
     V keys are names the Catalogue of Life merged in from another checklist
     to fill gaps.)
  2. Both keys were found in the Catalogue of Life.
  3. Same kingdom and same phylum.
  4. The same author or authors, after normalising the spelling: case,
     accents ("Müller" = "Muller" = "Mueller"), spaces and punctuation
     ("DeGeer" = "De Geer"), initials ("O. F. Müller" = "Muller"), years
     and brackets are ignored, and an abbreviation matches the full name
     ("Kütz." = "Kuetzing", "L." = "Linnaeus"). Pairs that only match after
     normalising are printed, so they can be checked.

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


def plain_letters(text):
    """Return text lowercased, without accents, with "ae", "oe" and "ue"
    written as their single vowel, so "Müller", "Muller" and "Mueller" (and
    "Kütz." and "Kuetz.") come out the same."""
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    for pair, vowel in (("ae", "a"), ("oe", "o"), ("ue", "u")):
        text = text.replace(pair, vowel)
    return text


def one_author(text):
    """Return (name, abbreviated) for one author, such as "O. F. Müller".

    Initials (single letters before a longer word) are dropped and the rest
    joined without spaces, so "De Geer" and "DeGeer" are both "degeer".
    abbreviated is True if the name ends with a full stop, as in "Kütz."."""
    abbreviated = text.strip().endswith(".")
    words = [w for w in re.split(r"[\s.]+", plain_letters(text)) if w]
    words = [re.sub(r"[^a-z]", "", w) for w in words]
    words = [w for w in words if w]
    if any(len(w) > 1 for w in words):
        words = [w for w in words if len(w) > 1]
    return "".join(words), abbreviated


def author_list(authorship):
    """Return the authors in an authorship as a list of (name, abbreviated),
    ignoring years and brackets. "(Stephens, 1836)" gives [("stephens",
    False)]."""
    text = re.sub(r"\d+", " ", authorship or "")
    text = text.replace("(", ",").replace(")", ",")
    parts = re.split(JOINING_WORDS, text, flags=re.IGNORECASE)
    authors = [one_author(p) for p in parts if p and p.strip(" ,.")]
    return [a for a in authors if a[0]]


def full_name(name):
    """Return the normalised full name for a short standard abbreviation,
    or the name itself."""
    if name in AUTHOR_ABBREVIATIONS:
        return one_author(AUTHOR_ABBREVIATIONS[name])[0]
    return name


def same_author(a, b):
    """Say whether two (name, abbreviated) authors are the same person."""
    name_a, short_a = a
    name_b, short_b = b
    if full_name(name_a) == full_name(name_b):
        return True
    for (short, name, other) in ((short_a, name_a, name_b), (short_b, name_b, name_a)):
        if short and len(name) >= MIN_PREFIX and other.startswith(name):
            return True
    return False


def same_authors(authorship_a, authorship_b):
    """Say whether two authorships name the same authors, in any order.
    Two empty authorships do not count as the same: there is nothing to
    compare."""
    left, right = author_list(authorship_a), author_list(authorship_b)
    if not left or len(left) != len(right):
        return False
    unused = list(right)
    for author in left:
        match = next((other for other in unused if same_author(author, other)), None)
        if match is None:
            return False
        unused.remove(match)
    return True


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


def decide(species, keys):
    """Decide one name. Returns ("merge", kept, old, reason, normalised) or
    ("separate", None, None, reason, False)."""
    keys_text = ", ".join(r["specieskey"] for r in keys)
    if len(keys) != 2 or sum(is_v_key(r["specieskey"]) for r in keys) != 1:
        return ("separate", None, None,
                f"not one V key and one other key ({keys_text})", False)
    if any(r["error"] for r in keys):
        return ("separate", None, None, "lookup failed: " + "; ".join(
            f"{r['specieskey']} {r['error']}" for r in keys if r["error"]), False)
    a, b = keys
    if a["kingdom"] != b["kingdom"] or a["phylum"] != b["phylum"]:
        return ("separate", None, None,
                f"different kingdom or phylum: {a['specieskey']} {a['kingdom']}/{a['phylum']}"
                f" vs {b['specieskey']} {b['kingdom']}/{b['phylum']}", False)
    if not same_authors(a["authorship"], b["authorship"]):
        return ("separate", None, None,
                f"different authors: {a['specieskey']} '{a['authorship']}' vs "
                f"{b['specieskey']} '{b['authorship']}'", False)
    normalised = a["authorship"].strip() != b["authorship"].strip()
    kept, old, why = choose_kept(keys, species)
    match = "same author after normalising" if normalised else "same author"
    return ("merge", kept, old, f"{match}; {why}", normalised)


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
    reasons = defaultdict(int)
    for m in auto:
        reasons[m["reason"].split("; ")[1]] += 1
    for why, n in sorted(reasons.items(), key=lambda kv: -kv[1]):
        print(f"  {why}: {n:,}")

    print(f"\nMerged only after normalising the authors ({len(normalised)}), please check:")
    for species, kept, old in normalised:
        print(f"  {species}: {old['specieskey']} '{old['authorship']}' -> "
              f"{kept['specieskey']} '{kept['authorship']}'")

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
