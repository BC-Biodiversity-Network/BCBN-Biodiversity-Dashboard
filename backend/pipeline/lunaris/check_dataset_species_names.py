"""
check_dataset_species_names.py

Checks the rows of the hand-checked name list for species matching
(dataset_species_names_manual.csv, read by match_dataset_species.py) by
fixed rules, and fills in each row's status (confirmed, deferred or
rejected), evidence, checked_by and checked_date. Only confirmed rows are
ever used by the matcher.

Each row is one of four kinds, checked in this order:
  review      the names listed in FOR_REVIEW: a subspecies, variety,
              population or form linked to the species card, a common
              name BCSEE does not have, or a Latin gender ending that
              differs from the card's name. Always deferred.
  plural      rows whose reason starts "Plural:". Confirmed only if the
              name without its final "s" or "es" is exactly (as the matcher
              writes names) a species-level BCSEE English name of that
              species; otherwise deferred.
  common      the other rows whose name is closer to one of the species'
              English names than to its scientific names. Confirmed only if
              the name differs from a species-level BCSEE English name of
              the species only by spaces, hyphens, apostrophes, letter case,
              a final "s" or "es", or a US instead of a Canadian spelling
              (gray for grey, harbor for harbour). A name that only matches
              a BCSEE entry below the species (a variety or population), or
              differs in any other way, is deferred.
  scientific  the rest. The name as written in the datasets is looked up
              in the GBIF species API: the name match (with the species'
              kingdom as a hint) and the exact name in the GBIF Backbone.
              - GBIF has that exact name as its own accepted taxon, or as a
                synonym, of a species other than ours: rejected.
              - GBIF has no taxon with exactly that name (or it is our
                species' own name with an authority, or a synonym of our
                species), and its match goes to our species: confirmed.
              - GBIF matches the name only above species level (match type
                HIGHERRANK) or not at all (NONE): the species was chosen
                from the spelling alone, so deferred for a person to check.
              - Anything else (the match goes to another species, or the
                answers disagree): deferred.
Species names are compared without subgenus and without letter case.

GBIF answers are kept in a cache file (DEFAULT_CACHE), so the check can be
run again without calling GBIF; new calls wait CALL_DELAY seconds apart.

Run, from backend/:
    python pipeline/lunaris/check_dataset_species_names.py
"""

import argparse
import json
import os
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import match_dataset_species as ms  # noqa: E402

DEFAULT_MATCHES = ms.DEFAULT_OUT
DEFAULT_CACHE = "data/lunaris_species_match/gbif_name_checks.json"
CHECKED_BY = "Lucia"
CHECKED_DATE = "2026-10-29"

GBIF_MATCH = "https://api.gbif.org/v1/species/match"
GBIF_NAMES = "https://api.gbif.org/v1/species"
GBIF_BACKBONE = "d7dddbf4-2cf0-4f39-9b2a-bb099caae36c"
# Seconds between two calls to GBIF, to be polite to the API.
CALL_DELAY = 1.0

# Rows always deferred, with why (step 5 of the request of 2026-10-29).
NEEDS_REVIEW = "needs human review: subspecies, variety, population or form linked to the species card"
NOT_IN_BCSEE = "common name not in BCSEE"
GENDER = "needs human review: the Latin gender ending differs from the card's name"
FOR_REVIEW = {
    "kokanee": f"{NEEDS_REVIEW} (kokanee is the lake-resident form of sockeye salmon)",
    "roosevelt elk": f"{NEEDS_REVIEW} (Roosevelt elk is a subspecies of elk)",
    "coastal douglas fir": f"{NEEDS_REVIEW} (coast Douglas-fir is a variety)",
    "nooksackdace": f"{NEEDS_REVIEW} (Nooksack Dace is a population of longnose dace)",
    "bull kelp": NOT_IN_BCSEE,
    "giant kelp": NOT_IN_BCSEE,
    "nereocystis luetkeana": f"{GENDER} (luetkeana, card luetkeanus)",
    "dictyna brevitarsa": f"{GENDER} (brevitarsa, card brevitarsus)",
    "scironis sima": f"{GENDER} (sima, card simus)",
}

# US spellings and their Canadian forms, word by word.
US_TO_CANADIAN = {"gray": "grey", "harbor": "harbour", "color": "colour", "colored": "coloured"}


# --- Reading ---------------------------------------------------------------

def species_facts(con):
    """
    Return {specieskey: facts} for every card: its name (col_name), kingdom,
    species-level BCSEE English names (english), English names of BCSEE
    entries below the species (entry_english) and scientific names as the
    matcher writes them (scientific).
    """
    facts = {key: {"col_name": name, "kingdom": kingdom or "", "english": [], "entry_english": [],
                   "scientific": []}
             for key, name, kingdom in con.execute("SELECT specieskey, col_name, kingdom FROM species_card").fetchall()}
    for key, field, name, norm in con.execute("SELECT specieskey, field, name, norm FROM search_names").fetchall():
        if field == "english_name":
            facts[key]["english"].append(name)
        elif field == "bcsee_entry_english_name":
            facts[key]["entry_english"].append(name)
        else:
            facts[key]["scientific"].append(norm)
    return facts


def written_forms(matches_path):
    """Return {name as the matcher writes it: the most frequent form written in the datasets}."""
    table = pd.read_parquet(matches_path, columns=["name", "name_norm"])
    return table.groupby("name_norm")["name"].agg(lambda names: names.value_counts().index[0]).to_dict()


# --- Words -----------------------------------------------------------------

def plain(text):
    """Return text in lower case with accents removed (as the matcher's search_norm does)."""
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


def search_way(text):
    """Return text written the way the matcher writes names (search_norm), in Python."""
    words = re.sub(r"[^a-z0-9]+", " ", re.sub(r"['’‘`]", "", plain(text))).split()
    return " ".join(word for word in words if word != "x")


def squashed(text):
    """Return text with only its letters and digits, in lower case: spaces, hyphens and apostrophes gone."""
    return re.sub(r"[^a-z0-9]", "", re.sub(r"['’‘`]", "", plain(text)))


def to_canadian(name):
    """Return a name with US spellings written the Canadian way, and the words changed."""
    words = name.split()
    changed = [f"{word} for {US_TO_CANADIAN[word]}" for word in words if word in US_TO_CANADIAN]
    return " ".join(US_TO_CANADIAN.get(word, word) for word in words), changed


def species_name_key(name):
    """Return a species name for comparing: without a subgenus in brackets, an authority or letter case."""
    words = re.sub(r"\([^)]*\)", " ", name or "").split()
    return " ".join(words[:2]).lower()


def levenshtein(a, b):
    """Return how many single-letter changes turn a into b."""
    previous = list(range(len(b) + 1))
    for i, letter_a in enumerate(a, 1):
        current = [i]
        for j, letter_b in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (letter_a != letter_b)))
        previous = current
    return previous[-1]


# --- The four kinds of row -------------------------------------------------

def row_kind(row, facts):
    """Return the kind of a row: review, plural, common or scientific (see the top of this file)."""
    name = row["name_as_written"]
    if name in FOR_REVIEW:
        return "review"
    if row["reason"].startswith("Plural:"):
        return "plural"
    species = facts[row["specieskey"]]
    english = [search_way(n) for n in species["english"] + species["entry_english"]]
    to_english = min((levenshtein(name, n) for n in english), default=10 ** 6)
    to_scientific = min((levenshtein(name, n) for n in species["scientific"]), default=10 ** 6)
    return "common" if to_english < to_scientific else "scientific"


def check_plural(name, species):
    """Return (status, evidence) for a plural row: confirmed if the singular is a species-level BCSEE English name."""
    for cut in ("s", "es"):
        if name.endswith(cut):
            for english in species["english"]:
                if search_way(english) == name[: -len(cut)]:
                    return "confirmed", f"singular '{name[: -len(cut)]}' is BCSEE's English name '{english}'"
    return "deferred", "the singular is not a species-level BCSEE English name of this species"


def common_difference(name, english):
    """
    Return the differences between a name (as the matcher writes it) and a
    BCSEE English name, as a list of words, if they are only spaces,
    hyphens, apostrophes, letter case, a final s or es, and US spellings;
    else None.
    """
    canadian, us_words = to_canadian(name)
    target = squashed(english)
    for cut in ("", "s", "es"):
        if cut and not canadian.endswith(cut):
            continue
        base = canadian[: -len(cut)] if cut else canadian
        if squashed(base) != target:
            continue
        differences = [f"US spelling ({', '.join(us_words)})"] if us_words else []
        if cut:
            differences.append(f"a final {cut}")
        if base != search_way(english):
            differences.append("spaces")
        written = english.lower()
        if "-" in written or "'" in written or "’" in written:
            differences.append("hyphens or apostrophes")
        if english != english.lower():
            differences.append("letter case")
        return differences
    return None


def check_common(name, species):
    """Return (status, evidence) for a common-name row (see the top of this file)."""
    for english in species["english"]:
        differences = common_difference(name, english)
        if differences is not None:
            return "confirmed", f"BCSEE English name '{english}'; differs only by {', '.join(differences)}"
    for english in species["entry_english"]:
        if common_difference(name, english) is not None:
            return "deferred", (f"BCSEE name '{english}' is on an entry below the species (subspecies, variety "
                                f"or population), not the species itself")
    if not species["english"]:
        entries = ", ".join(f"'{n}'" for n in species["entry_english"]) or "none"
        return "deferred", f"the species has no species-level BCSEE English name (entries below it: {entries})"
    names = ", ".join(f"'{n}'" for n in species["english"])
    return "deferred", (f"differs from the species' BCSEE English name(s) ({names}) by more than spaces, hyphens, "
                        f"apostrophes, case, a final s/es or US spelling")


def gbif_target(record):
    """Return the species name a GBIF name record stands for: its own for an accepted name, the accepted one for a synonym."""
    return species_name_key(record.get("species") or record.get("accepted") or record.get("canonicalName"))


def check_scientific(name_norm, written, our_name, match, exact):
    """
    Return (status, evidence) for a scientific row from the GBIF answers:
    match (the name match) and exact (the Backbone records found for the
    name). See the top of this file for the rules.
    """
    ours = species_name_key(our_name)
    query = species_name_key(match.get("canonicalName")) if match.get("matchType") == "EXACT" else None
    same_name = [r for r in exact if search_way(r.get("canonicalName", "")) in {name_norm, query}
                 and r.get("rank") in {"SPECIES", "SUBSPECIES", "VARIETY", "FORM"}]
    other = [r for r in same_name if gbif_target(r) != ours]
    if other:
        r = other[0]
        kind = "its own accepted taxon" if r.get("taxonomicStatus") in {"ACCEPTED", "DOUBTFUL"} else \
            f"a {r.get('taxonomicStatus', '').lower()} of {gbif_target(r).capitalize()}"
        return "rejected", f"GBIF: '{written}' is {kind} (key {r.get('key')}, {r.get('scientificName')})"
    match_target = species_name_key(match.get("species") or match.get("canonicalName"))
    match_type = match.get("matchType", "NONE")
    usage = f"usageKey {match.get('usageKey')}"
    if same_name:
        return "confirmed", (f"GBIF: '{written}' is {our_name} itself or its synonym "
                             f"(key {same_name[0].get('key')}, {same_name[0].get('scientificName')})")
    if match_type == "EXACT" and match_target == ours:
        return "confirmed", (f"GBIF: '{written}' matches {match.get('scientificName')} exactly ({usage}): "
                             f"the card's species, with its authority")
    if match_type == "FUZZY" and match_target == ours:
        return "confirmed", (f"GBIF: no taxon with exactly that name; fuzzy match to {match.get('scientificName')} "
                             f"({usage}; species {match.get('species')})")
    if match_type == "HIGHERRANK":
        return "deferred", (f"GBIF matched only at {match.get('rank', 'a higher').lower()} level "
                            f"({match.get('canonicalName')}), so the species was chosen from spelling alone; "
                            f"needs human review")
    if match_type == "NONE":
        return "deferred", "GBIF found no match, so the species was chosen from spelling alone; needs human review"
    return "deferred", (f"GBIF: no exact name, but its {match_type.lower()} match goes to another species: "
                        f"{match.get('scientificName')} ({usage})")


# --- GBIF ------------------------------------------------------------------

class GbifCache:
    """GBIF answers by request address, read from and written to a JSON file; new calls wait CALL_DELAY apart."""

    def __init__(self, path, offline=False, opener=None):
        """Read the cache file at path (if it exists). With offline, a missing answer stops the run."""
        self.path, self.offline = path, offline
        self.opener = opener or self.fetch
        self.answers = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
        self.calls = 0

    @staticmethod
    def fetch(url):
        """Return the JSON answer of one GBIF request."""
        request = urllib.request.Request(url, headers={"User-Agent": "BCBN dashboard name check"})
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)

    def get(self, url):
        """Return the answer for url, from the cache or from GBIF (then saved to the cache)."""
        if url not in self.answers:
            if self.offline:
                raise SystemExit(f"ERROR: no cached GBIF answer for {url}")
            if self.calls:
                time.sleep(CALL_DELAY)
            self.answers[url] = self.opener(url)
            self.calls += 1
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as handle:
                json.dump(self.answers, handle, indent=1, sort_keys=True)
        return self.answers[url]


def gbif_answers(cache, written, kingdom):
    """Return (match, exact records) from GBIF for a name as written, with the kingdom as a hint."""
    match_url = f"{GBIF_MATCH}?" + urllib.parse.urlencode(
        {"name": written, "kingdom": kingdom, "strict": "false", "verbose": "true"})
    names_url = f"{GBIF_NAMES}?" + urllib.parse.urlencode({"name": written, "datasetKey": GBIF_BACKBONE, "limit": 50})
    return cache.get(match_url), cache.get(names_url).get("results", [])


# --- The whole list --------------------------------------------------------

def check_rows(rows, facts, written, cache):
    """Return the rows with status, evidence, checked_by and checked_date filled, and the kind of each row."""
    checked, kinds = [], []
    for row in rows:
        name, species = row["name_as_written"], facts[row["specieskey"]]
        kind = row_kind(row, facts)
        if kind == "review":
            status, evidence = "deferred", FOR_REVIEW[name]
        elif kind == "plural":
            status, evidence = check_plural(name, species)
        elif kind == "common":
            status, evidence = check_common(name, species)
        else:
            form = written.get(name, name.capitalize())
            match, exact = gbif_answers(cache, form, species["kingdom"])
            status, evidence = check_scientific(name, form, species["col_name"], match, exact)
        done = status != "deferred"
        checked.append({**row, "status": status, "evidence": evidence,
                        "checked_by": CHECKED_BY if done else "", "checked_date": CHECKED_DATE if done else ""})
        kinds.append(kind)
    return checked, kinds


def main():
    """Check every row of the list, write the list back with the results, and print a summary."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("--manual", default=ms.DEFAULT_MANUAL)
    parser.add_argument("--matches", default=DEFAULT_MATCHES, help="the match table, for the names as written")
    parser.add_argument("--cache", default=DEFAULT_CACHE)
    parser.add_argument("--offline", action="store_true", help="use only cached GBIF answers")
    args = parser.parse_args()

    con = ms.open_species(ms.DEFAULT_SPECIES, ms.DEFAULT_BCSEE)
    facts = species_facts(con)
    rows = ms.read_manual_names(args.manual)
    cache = GbifCache(args.cache, offline=args.offline)
    checked, kinds = check_rows(rows, facts, written_forms(args.matches), cache)
    table = pd.DataFrame(checked, columns=ms.MANUAL_COLUMNS)
    table.to_csv(args.manual, index=False)
    print(f"checked {len(table)} rows; {cache.calls} new GBIF calls (cache {args.cache})")
    print(pd.crosstab(pd.Series(kinds, name="kind"), table["status"], margins=True, margins_name="total").to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
