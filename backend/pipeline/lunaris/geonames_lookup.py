"""
geonames_lookup.py

Look up a place name from the geography step's pass 1 (geo_prompt_v3) in a
local copy of GeoNames, and return two separate things:

  candidates  up to 10 GeoNames places the name can mean, exactly as
              GeoNames has them, with the name of each code next to it
              ("02" with "British Columbia", "STM" with "stream");
  summary     our own reading of those places, by our rules (GeoNames gives
              nothing like it): in BC only, spans the BC border, both,
              contains BC, outside BC only, ocean or sea with no country,
              not found, or too generic (see summarise).

Pass 2 can be given the candidates alone, or the candidates and the summary.

The table is built once on the Mac by build_geonames_lookup.py and copied to
Fir. It is one parquet file with one row per name (main, ASCII and alternate
names), sorted by its normalised name, so DuckDB reads only the small part
of the file a query needs and never loads the table into memory. The code
names come from a small JSON file built next to it from GeoNames' code files.
The lookup needs only DuckDB (1.5.3 on Fir) and the Python standard library.

Matching follows the GeoNames search API
(https://img.geonames.org/export/geonames-search.html) where it can. It
matches names only, like the name parameter (never the country or other
fields, unlike q), returns at most 10 places, like maxRows, and tries, in
this order, stopping at the first step that finds anything (find_rows):

  1. the name, exactly (normalised), over main and alternate names, like
     name_equals;
  2. the model's as_written, exactly;
  3. the name with a final plural removed ("Kootenays" to "Kootenay"), only
     now, so "Rocky Mountains" finds the mountains before the town of Rocky
     Mountain. A name made only of generic and direction words ("Western
     provinces") stops here as too generic, so it never reaches the
     singular "Western Province" of Sri Lanka;
  4. approximately, like fuzzy: a similarity of at least 0.8, word by word,
     only for names of at least 6 letters;
  5. once more, steps 1, 3 and 4 without a leading direction word
     ("Southern Vancouver Island" to "Vancouver Island"), marked as such.

Ordering of the places returned (the API orders by "relevance", which it does
not define; we spell ours out, rank_key):
  1. places that agree with the comma hint ("Vancouver, British Columbia"
     hints British Columbia) come first, when there is a hint;
  2. for approximate matches, the most similar name next;
  3. then how the name matched (match_strength): main, ASCII or hand-added
     names and alternate names with no language, in English or French, or
     abbreviations first; then alternate names in other languages; then
     historic names (Hong Kong's Danish name "Victoria" must not lead);
  4. then, when the name has a feature word ("Range", "River", "Lake",
     "Strait of ..."; geonames_feature_words.csv), places of that kind of
     feature first;
  5. then the larger population (most features other than towns have none);
  6. then feature importance (feature_tier): countries, provinces, states,
     capitals, continents and oceans; then other populated places and
     divisions, large water bodies, regions, mountain ranges and
     archipelagos; then other lakes, rivers, parks, mountains and islands;
     then everything else (buildings, roads, spots, undersea features);
  7. then places in Canada, then in the United States, then the rest
     (country_order), so that among equals the North American place comes
     first (the Rocky Mountains of North America before Australia's);
  8. then the lower GeoNames id, so the order is always the same.

Normalising (normalise) is the same function for the table and the queries.
It is used only for matching: names are never changed, and the results show
GeoNames' names and the model's as_written and name exactly as they were.

Hand-added entries (geonames_hand_entries.csv) are of three kinds:
  region           a BC region GeoNames does not have (no geonames_id); it
                   becomes a candidate of its own, marked "hand";
  alias            another name for a GeoNames place (geonames_id given):
                   the name finds that place;
  spans_bc_border  a GeoNames place (geonames_id given) that crosses the BC
                   border although GeoNames does not show it.

Place names from GeoNames (https://www.geonames.org/), licensed under CC BY
4.0 (https://creativecommons.org/licenses/by/4.0/). Filtered and reformatted
for the Lunaris BC filter.

Run, from backend/:
    python pipeline/lunaris/geonames_lookup.py --pass1 data/lunaris_bc_filter/pass1_trial_v3
    python pipeline/lunaris/geonames_lookup.py --bench 1000 --memory-limit 2GB --threads 2
"""

import argparse
import csv
import json
import os
import random
import re
import resource
import sys
import time
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(os.path.dirname(HERE))
DEFAULT_TABLE = os.path.join(BACKEND, "data", "geonames", "geonames_lookup.parquet")
DEFAULT_CODES = os.path.join(BACKEND, "data", "geonames", "geonames_codes.json")
DEFAULT_HAND = os.path.join(HERE, "geonames_hand_entries.csv")
HAND_KINDS = ("region", "alias", "spans_bc_border")
ABBREVIATIONS_CSV = os.path.join(HERE, "geonames_abbreviations.csv")
GENERIC_WORDS_CSV = os.path.join(HERE, "geonames_generic_words.csv")
FEATURE_WORDS_CSV = os.path.join(HERE, "geonames_feature_words.csv")
CONTAINS_BC_CSV = os.path.join(HERE, "geonames_contains_bc.csv")

MAX_ROWS = 10             # like the API's maxRows
FUZZY_THRESHOLD = 0.8     # like the API's fuzzy: the least similarity accepted
MIN_FUZZY_LENGTH = 6      # shorter names (letters and digits only) are not matched approximately
FUZZY_PREFIX = 3          # approximate matches must share the first 3 characters (see approximate_rows)

# Words that only say which part of a place is meant. A name that finds
# nothing is tried once more without one of these at its start.
DIRECTION_WORDS = {"southern", "northern", "eastern", "western", "central", "north", "south", "east",
                   "west", "upper", "lower"}

# BC's bounding box (west, east, south, north, in degrees) and the margin
# around it used to decide that a feature crossing the Canada-US border
# crosses BC's part of it (see spans_bc_border).
BC_BOX = (-139.06, -114.05, 48.30, 60.00)
BC_BOX_MARGIN = 1.0

# The summary's categories, in the order of the precedence in summarise.
CATEGORIES = {
    "in_bc_only": "in BC only",
    "spans_bc_border": "spans the BC border",
    "both": "both (different places with this name, in and outside BC)",
    "contains_bc": "contains BC",
    "outside_bc_only": "outside BC only",
    "ocean_no_country": "ocean or sea, no country",
    "not_found": "not found",
    "too_generic": "too generic",
}
KINDS = ["in_bc", "spans_bc_border", "contains_bc", "outside_bc", "ocean_no_country"]

# The columns of the table, in order. build_geonames_lookup.py writes them.
COLUMNS = ["norm", "name", "main_name", "name_type", "language", "is_historic", "geonameid",
           "feature_class", "feature_code", "country_code", "cc2", "admin1_code",
           "population", "latitude", "longitude", "region", "source"]

# Features a comma hint can name: countries and their first-level divisions.
HINT_FEATURES = ("ADM1", "PCLI", "PCLD", "PCLF", "PCLS", "PCL", "PCLIX", "PCLH", "TERR")

# Letters that do not come apart into a plain letter and an accent.
SPECIAL_LETTERS = str.maketrans({"ø": "o", "Ø": "O", "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE",
                                 "ß": "ss", "ł": "l", "Ł": "L", "đ": "d", "Đ": "D", "þ": "th",
                                 "Þ": "Th", "ð": "d", "Ð": "D", "ı": "i"})
# Full stops and apostrophes are removed outright ("B.C." becomes "bc",
# "Nlaka'pamux" becomes "nlakapamux"); other punctuation becomes a space.
DROPPED = re.compile(r"[.'’ʼ‘`´]")
SPACED = re.compile(r"[\W_]+")


def read_csv_rows(path):
    """Return the rows of a small CSV file as dicts."""
    with open(path, encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


ABBREVIATIONS = {row["abbreviation"].strip().lower(): row["expansion"].strip().lower()
                 for row in read_csv_rows(ABBREVIATIONS_CSV)}
GENERIC_WORDS = {row["word"].strip().lower() for row in read_csv_rows(GENERIC_WORDS_CSV)}
FEATURE_WORDS = {row["word"].strip().lower(): set(row["feature_codes"].split())
                 for row in read_csv_rows(FEATURE_WORDS_CSV)}
CONTAINS_BC = {int(row["geonameid"]) for row in read_csv_rows(CONTAINS_BC_CSV)}


def singular(word):
    """
    Return a simple singular of a word: drop a final "s" from words of four
    letters or more ("kootenays" to "kootenay", "islands" to "island"), but
    not from endings that are rarely plurals ("ss", "us", "is").
    """
    if len(word) >= 4 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def without_plural(norm):
    """Return a normalised name with its last word made singular ("rocky mountains" to "rocky mountain")."""
    words = norm.split()
    return " ".join(words[:-1] + [singular(words[-1])]) if words else norm


def normalise(text):
    """
    Return the form of a name used only for matching. The name itself is
    never changed; this returns a new text.

    Steps: plain letters for accented ones ("Île" to "ile"), lower case,
    common abbreviations spelled out from geonames_abbreviations.csv
    ("Kalamalka L." to "kalamalka lake"), a leading "the" dropped, full stops
    and apostrophes removed, other punctuation turned into spaces, and spaces
    tidied. Plurals are kept; without_plural removes one when a lookup needs it.
    """
    if not text:
        return ""
    text = text.translate(SPECIAL_LETTERS)
    if not text.isascii():
        text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    words = [ABBREVIATIONS.get(word.rstrip(",;:"), word) for word in text.lower().split()]
    if len(words) > 1 and words[0] == "the":
        words = words[1:]
    return " ".join(SPACED.sub(" ", DROPPED.sub("", " ".join(words))).split())


def split_comma(text):
    """
    Split "Vancouver, British Columbia" into the place to look up
    ("Vancouver") and the hints after it (["British Columbia"]). A name
    without a comma has no hints.
    """
    parts = [part.strip() for part in (text or "").split(",")]
    return parts[0], [part for part in parts[1:] if part]


def is_too_generic(norm):
    """Return True if a normalised name is empty or a single generic word, such as "lake" or "rivers"."""
    return not norm or (" " not in norm and (norm in GENERIC_WORDS or singular(norm) in GENERIC_WORDS))


def is_generic_phrase(norm):
    """
    Return True if every word of a normalised name of two or more words is a
    generic word or a direction word ("western provinces", "north coast").
    """
    words = norm.split()
    return len(words) > 1 and all(w in GENERIC_WORDS or singular(w) in GENERIC_WORDS or w in DIRECTION_WORDS
                                  for w in words)


def feature_codes_of(norm):
    """
    Return the GeoNames feature codes (such as "H.STM") that a name's feature
    word points to: its last word ("Fraser River") or, failing that, its
    first ("Strait of Georgia", "Mount Robson"). Empty if it has none.
    """
    words = norm.split()
    if not words:
        return set()
    return FEATURE_WORDS.get(words[-1]) or FEATURE_WORDS.get(words[0]) or set()


def feature_tier(feature_class, feature_code):
    """
    Return how important a kind of feature is, for ordering places of equal
    population: 1 is most important, 4 least. See the module docstring.
    """
    if feature_code in ("PCLI", "PCLD", "PCLF", "PCLS", "PCL", "PCLIX", "ADM1", "TERR", "CONT", "OCN",
                        "PPLC", "PPLA"):
        return 1
    if feature_class in ("P", "A"):
        return 2
    if feature_code in ("SEA", "GULF", "BAY", "BAYS", "STRT", "SD", "FJD", "ESTY", "CHN", "RGN", "RGNE",
                        "MTS", "ISLS", "PEN", "PLAT", "HAND"):
        return 2
    if feature_class in ("H", "L", "T"):
        return 3
    return 4


def similarity_of(a, b):
    """Return 1 minus the edit distance between a and b divided by the longer length (1 means identical)."""
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, start=1):
        current = [i]
        for j, char_b in enumerate(b, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (char_a != char_b)))
        previous = current
    return 1 - previous[-1] / max(len(a), len(b), 1)


def words_match(query, candidate):
    """
    Return True if two normalised names match word by word: the same number
    of words, short words (three letters or fewer) identical, longer ones at
    least FUZZY_THRESHOLD similar.
    """
    query_words, candidate_words = query.split(), candidate.split()
    if len(query_words) != len(candidate_words):
        return False
    return all(q == c if min(len(q), len(c)) <= 3 else similarity_of(q, c) >= FUZZY_THRESHOLD
               for q, c in zip(query_words, candidate_words))


def match_strength(place):
    """
    Return how strongly a place matched by the name it matched on, 0 the
    strongest: its main, ASCII or hand-added name, or an alternate name with
    no language, in English or French, or an abbreviation 0; an alternate
    name in another language 1; a historic name 2.
    """
    if place["is_historic"]:
        return 2
    if place["name_type"] in ("main", "ascii", "hand"):
        return 0
    return 0 if (place["language"] or "") in ("", "en", "fr", "abbr") else 1


def read_hand_entries(path):
    """
    Return the hand-added entries from geonames_hand_entries.csv as dicts:
    name, its normalised form, the GeoNames id it points to (None for a
    region), its kind (region, alias or spans_bc_border) and the note. Stop
    with an error on an unknown kind, a region with an id, or an alias or
    spans_bc_border entry without one.
    """
    entries = []
    for row in read_csv_rows(path):
        geonames_id = (row["geonames_id"] or "").strip()
        kind = row["kind"].strip()
        if kind not in HAND_KINDS or (kind == "region") != (not geonames_id):
            raise ValueError(f"{path}: {row['name']!r} has kind {kind!r} and geonames_id {geonames_id!r}; "
                             f"a region has no id, an alias or spans_bc_border entry needs one")
        entries.append({"name": row["name"], "norm": normalise(row["name"]),
                        "geonames_id": int(geonames_id) if geonames_id else None,
                        "kind": kind, "note": row["note"] or ""})
    return entries


def hand_region(entry):
    """Return a hand-added BC region (one GeoNames does not have) as a place in the table's shape."""
    return {"norm": entry["norm"], "name": entry["name"], "main_name": entry["name"], "name_type": "hand",
            "language": "", "is_historic": False, "geonameid": None, "feature_class": "L",
            "feature_code": "HAND", "country_code": "CA", "cc2": "", "admin1_code": "02", "population": 0,
            "latitude": None, "longitude": None, "region": "bc", "source": "hand", "hand_note": entry["note"]}


def in_bc_box(latitude, longitude):
    """Return True if a point lies within BC_BOX_MARGIN degrees of BC's bounding box."""
    if latitude is None or longitude is None:
        return False
    west, east, south, north = BC_BOX
    return (west - BC_BOX_MARGIN <= longitude <= east + BC_BOX_MARGIN
            and south - BC_BOX_MARGIN <= latitude <= north + BC_BOX_MARGIN)


class GeoNamesLookup:
    """
    A connection to the lookup table, ready for queries.

    table: the parquet file from build_geonames_lookup.py.
    hand_csv: geonames_hand_entries.csv, or None to leave the hand-added
      entries out.
    codes: the code names from build_geonames_lookup.py (geonames_codes.json),
      or None to show codes without names.
    memory_limit and threads: DuckDB's limits; a Fir job asks for 2 CPUs and
      8 GB, and the lookup needs far less.
    """

    def __init__(self, table=DEFAULT_TABLE, hand_csv=DEFAULT_HAND, codes=DEFAULT_CODES, memory_limit="2GB",
                 threads=2):
        """Open DuckDB on the table (read from disk, not loaded), and load the code names and hand entries."""
        import duckdb

        self.con = duckdb.connect()
        self.con.execute(f"SET memory_limit = '{memory_limit}'")
        self.con.execute(f"SET threads = {int(threads)}")
        # Keep the file's metadata in memory between queries, rather than
        # reading it again each time; this halves the time per query.
        self.con.execute("SET parquet_metadata_cache = true")
        path = table.replace("'", "''")
        self.con.execute(f"CREATE VIEW geonames AS SELECT * FROM read_parquet('{path}')")
        self.codes = {}
        if codes and os.path.exists(codes):
            with open(codes, encoding="utf-8") as handle:
                self.codes = json.load(handle)

        entries = read_hand_entries(hand_csv) if hand_csv else []
        self.hand_entries = entries
        self.hand_regions = [hand_region(e) for e in entries if e["kind"] == "region"]
        self.spans_ids = {e["geonames_id"] for e in entries if e["kind"] == "spans_bc_border"}
        # An alias finds its GeoNames place: the place's main row, with the
        # hand name as the name that matched. Finding the rows by id reads
        # the whole table once (two or three seconds when the lookup opens).
        ids = sorted({e["geonames_id"] for e in entries if e["kind"] != "region"})
        by_id = {}
        if ids:
            marks = ", ".join("?" for _ in ids)
            found = self.rows_where(f"geonameid IN ({marks}) AND name_type = 'main'", ids)
            by_id = {r["geonameid"]: r for r in found}
        self.hand_aliases = [dict(by_id[e["geonames_id"]], norm=e["norm"], name=e["name"], name_type="hand",
                                  language="", is_historic=False, hand_note=e["note"])
                             for e in entries if e["kind"] == "alias" and e["geonames_id"] in by_id]
        self.missing_hand_ids = [e for e in entries if e["geonames_id"] and e["geonames_id"] not in by_id]

    def rows_where(self, condition, parameters, extra=""):
        """Return the table rows meeting a condition, as dicts, plus any extra columns asked for."""
        columns = ", ".join(COLUMNS) + extra
        cursor = self.con.execute(f"SELECT {columns} FROM geonames WHERE {condition}", parameters)
        names = [d[0] for d in cursor.description]
        return [dict(zip(names, row)) for row in cursor.fetchall()]

    def hand_rows(self):
        """Return every hand-added row: the BC regions and the other names for GeoNames places."""
        return self.hand_regions + self.hand_aliases

    def exact_rows(self, norm):
        """Return every row whose normalised name is exactly norm: GeoNames' and the hand-added ones."""
        rows = self.rows_where("norm = ?", [norm])
        return rows + [dict(row) for row in self.hand_rows() if row["norm"] == norm]

    def approximate_rows(self, norm):
        """
        Return the rows whose normalised name is close to norm, each with its
        similarity: 1 minus the edit distance divided by the longer length, at
        least FUZZY_THRESHOLD.

        To keep this fast on a table of tens of millions of names, only names
        that share norm's first FUZZY_PREFIX characters and have a length that
        could reach the threshold are compared. A slip in the first three
        characters is therefore not found; the API's fuzzy search has no such
        limit.

        As in the API, whose fuzzy search (Lucene) works on each word, every
        word must also match on its own (words_match). Without this, "Cascade
        Crest" matched "Cascade Creek" and "Pacific Canada" a hotel called
        "Pacifico Lunada".
        """
        if len(norm.replace(" ", "")) < MIN_FUZZY_LENGTH:
            return []
        prefix = norm[:FUZZY_PREFIX]
        after = prefix[:-1] + chr(ord(prefix[-1]) + 1)
        shortest, longest = int(len(norm) * FUZZY_THRESHOLD + 0.999), int(len(norm) / FUZZY_THRESHOLD)
        similarity = "1 - levenshtein(norm, ?) / greatest(length(norm), ?)"
        # The similarity's own two values come first: it is in the SELECT,
        # before the WHERE.
        rows = self.rows_where(
            f"norm >= ? AND norm < ? AND length(norm) BETWEEN ? AND ? AND {similarity} >= ?",
            [norm, len(norm), prefix, after, shortest, longest, norm, len(norm), FUZZY_THRESHOLD],
            extra=f", {similarity} AS similarity")
        for row in self.hand_rows():
            score = similarity_of(norm, row["norm"])
            if score >= FUZZY_THRESHOLD:
                rows.append(dict(row, similarity=score))
        return [row for row in rows if words_match(norm, row["norm"])]

    def hint_places(self, hint):
        """
        Return what a comma hint names, as a set of (country_code,
        admin1_code) pairs, admin1_code None for a whole country. The hint is
        looked up like any name, but only countries and first-level divisions
        count ("British Columbia", "BC", "Massachusetts", "USA").

        When a hint names places in more than one country and one of them is
        Canada, only the Canadian ones are kept: the records come from a
        Canadian catalogue, and the instructions tell the model that "BC"
        means British Columbia ("BC" is also Baja California in GeoNames).
        """
        marks = ", ".join("?" for _ in HINT_FEATURES)
        rows = self.rows_where(f"norm = ? AND feature_code IN ({marks})", [normalise(hint), *HINT_FEATURES])
        places = {(r["country_code"], r["admin1_code"] if r["feature_code"] == "ADM1" else None) for r in rows}
        canadian = {p for p in places if p[0] == "CA"}
        return canadian or places

    def find_rows(self, name, as_written):
        """
        Find the rows for one place, trying the steps in the module docstring
        in order and stopping at the first that finds anything. Return
        (rows, how), how saying what was looked up and which step found it.
        """
        looked_up = split_comma(name)[0]
        norm = normalise(looked_up)
        how = {"looked_up": looked_up, "match": "none", "matched_on": "", "plural_removed": False,
               "direction_word_removed": ""}
        if is_too_generic(norm):
            how["match"] = "too generic"
            return [], how
        written = normalise(split_comma(as_written)[0]) if as_written else ""

        def attempt(norm, direction=""):
            """Steps 1 to 4 (step 2 only for the full name); return the rows found, filling in how."""
            rows = self.exact_rows(norm)
            if rows:
                how.update(match="exact", matched_on="name")
                return rows
            if not direction and written and written != norm and not is_too_generic(written):
                rows = self.exact_rows(written)
                if rows:
                    how.update(match="exact", matched_on="as_written", looked_up=split_comma(as_written)[0])
                    return rows
            if is_generic_phrase(norm):
                how["match"] = "too generic"
                return []
            if without_plural(norm) != norm:
                rows = self.exact_rows(without_plural(norm))
                if rows:
                    how.update(match="exact", matched_on="name", plural_removed=True)
                    return rows
            rows = self.approximate_rows(norm)
            if rows:
                how.update(match="approximate", matched_on="name")
            return rows

        rows = attempt(norm)
        words = norm.split()
        if not rows and how["match"] != "too generic" and len(words) > 1 and words[0] in DIRECTION_WORDS:
            rest = " ".join(words[1:])
            if not is_too_generic(rest):
                rows = attempt(rest, direction=words[0])
                if rows:
                    how["direction_word_removed"] = looked_up.split()[0]
        return rows, how

    def lookup(self, name, as_written=None, max_rows=MAX_ROWS):
        """
        Look up one place from pass 1. Return a dict of three separate parts:

          query: name and as_written as the model gave them, unchanged
          candidates: up to max_rows GeoNames places, best first, exactly as
            GeoNames has them, with code names (see candidate_record)
          summary: our reading of every place found, not only the first ten
            (see summarise)
        """
        rows, how = self.find_rows(name, as_written)
        hints = split_comma(name)[1] or (split_comma(as_written)[1] if as_written else [])
        places = best_row_per_place(rows)

        # A place agrees with the hints if it lies in every hint that names
        # a known country or province ("Montgomery, New York, USA").
        recognised = [s for s in (self.hint_places(hint) for hint in hints) if s]
        for place in places:
            place["agrees_with_hint"] = bool(recognised) and all(
                any(place["country_code"] == country and (admin1 is None or place["admin1_code"] == admin1)
                    for country, admin1 in s) for s in recognised)
            place["kind"] = self.place_kind(place)
        codes = feature_codes_of(normalise(how["looked_up"]))
        places.sort(key=lambda place: rank_key(place, codes))

        hint_check = "no hint"
        if hints:
            hint_check = ("not recognised" if not recognised else
                          "agrees" if any(p["agrees_with_hint"] for p in places) else "disagrees")
        summary = summarise(places, how)
        summary.update(hints=hints, hint_check=hint_check,
                       candidate_kinds=[place["kind"] for place in places[:max_rows]])
        return {"query": {"name": name, "as_written": as_written},
                "candidates": [self.candidate_record(place) for place in places[:max_rows]],
                "summary": summary}

    def place_kind(self, place):
        """
        Return our reading of one place, in this order:
          contains_bc: one of the large areas in geonames_contains_bc.csv
            (Canada, North America and so on);
          spans_bc_border: see spans_bc_border;
          in_bc: GeoNames puts it in BC (or it is a hand-added BC region);
          ocean_no_country: water with no country (oceans, seas, some bays);
          outside_bc: anything else.
        """
        if place["geonameid"] in CONTAINS_BC:
            return "contains_bc"
        if self.spans_bc_border(place):
            return "spans_bc_border"
        if place["region"] == "bc":
            return "in_bc"
        if not place["country_code"] and place["feature_class"] == "H":
            return "ocean_no_country"
        return "outside_bc"

    def spans_bc_border(self, place):
        """
        Return True if one single place lies partly in BC and partly outside.
        GeoNames gives each place one country, plus any others it crosses in
        its second-country field (cc2). We decide, in this order:
          - a hand-added entry of kind spans_bc_border says so, as for the
            Columbia River, which GeoNames gives only to the US;
          - GeoNames puts it in BC and its second countries name another
            country (Juan de Fuca Strait);
          - GeoNames puts it outside Canada, its second countries include
            Canada, and its GeoNames point lies within one degree of BC's
            bounding box, so the border it crosses is BC's and not another
            province's (Strait of Georgia, Salish Sea, Judson Lake; not
            Grassy Lake on the Montana-Alberta border, nor Mount Saint Elias
            on the Alaska-Yukon border).
        A place whose GeoNames point is far from BC but that still reaches it
        (the Rocky Mountains' point is in Wyoming) needs a hand entry; the
        Rocky Mountains have one.
        """
        if place["geonameid"] in self.spans_ids:
            return True
        others = [c for c in (place["cc2"] or "").split(",") if c]
        if place["region"] == "bc":
            return any(c != "CA" for c in others)
        return place["country_code"] != "CA" and "CA" in others and in_bc_box(place["latitude"],
                                                                              place["longitude"])

    def candidate_record(self, place):
        """
        Return one candidate as it goes to pass 2: GeoNames' own fields
        exactly as GeoNames has them, each code with its name from GeoNames'
        code files next to it, and how it matched. Nothing in it is our own
        reading (that is in the summary). A hand-added BC region, which
        GeoNames does not have, is marked matched_from "hand" and has no
        GeoNames id.
        """
        names = self.codes
        country, admin1 = place["country_code"] or "", place["admin1_code"] or ""
        feature = f"{place['feature_class'] or ''}.{place['feature_code'] or ''}"
        record = {
            "geonameid": place["geonameid"],
            "main_name": place["main_name"],
            "matched_name": place["name"],
            "matched_from": "hand" if place["name_type"] == "hand" else "geonames",
            "name_type": place["name_type"],
            "language": place["language"] or "",
            "is_historic": bool(place["is_historic"]),
            "country_code": country,
            "country_name": names.get("countries", {}).get(country, ""),
            "admin1_code": admin1,
            "admin1_name": names.get("admin1", {}).get(f"{country}.{admin1}", ""),
            "feature_class": place["feature_class"] or "",
            "feature_class_name": names.get("feature_classes", {}).get(place["feature_class"] or "", ""),
            "feature_code": place["feature_code"] or "",
            "feature_code_name": names.get("feature_codes", {}).get(feature, ""),
            "population": place["population"],
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "cc2": place["cc2"] or "",
            "cc2_names": [names.get("countries", {}).get(c, "") for c in (place["cc2"] or "").split(",") if c],
        }
        if place.get("hand_note"):
            record["hand_note"] = place["hand_note"]
        if "similarity" in place:
            record["similarity"] = round(place["similarity"], 3)
        return record


def best_row_per_place(rows):
    """
    Return one row per place (GeoNames id, or the name for a hand-added
    region): the best way that place matched. A closer approximate match
    beats a weaker one; then the stronger match by match_strength (so a
    current name beats a historic one); then a main name beats an ASCII
    name, which beats an alternate, which beats a hand-added name.
    """
    order = {"main": 0, "ascii": 1, "alternate": 2, "hand": 3}
    best = {}
    for row in rows:
        key = row["geonameid"] if row["geonameid"] is not None else "hand:" + row["norm"]
        score = (-row.get("similarity", 1), match_strength(row), order[row["name_type"]])
        if key not in best or score < best[key][0]:
            best[key] = (score, row)
    return [dict(row) for _, row in best.values()]


def country_order(place):
    """Return 0 for a place in Canada, 1 for one in the United States and 2 for the rest, for breaking ties."""
    return {"CA": 0, "US": 1}.get(place["country_code"] or "", 2)


def rank_key(place, feature_codes):
    """
    The ordering of the places returned; see the module docstring.
    feature_codes come from the name's feature word.
    """
    kind_of_feature = f"{place['feature_class']}.{place['feature_code']}"
    return (not place.get("agrees_with_hint", False), -place.get("similarity", 1), match_strength(place),
            bool(feature_codes) and kind_of_feature not in feature_codes,
            -(place["population"] or 0), feature_tier(place["feature_class"], place["feature_code"]),
            country_order(place), place["geonameid"] or 0)


def summarise(places, how):
    """
    Return our summary of every place found (GeoNames gives nothing like
    it). Each place has a kind (GeoNamesLookup.place_kind); the category is
    decided from the kinds present, in this order:

      not_found / too_generic: nothing to read;
      both: at least one place wholly in BC and at least one other place
        with the same name that is not (outside, spanning the border,
        containing BC, or an ocean): different places sharing a name;
      in_bc_only: every place is in BC;
      spans_bc_border: no place wholly in BC, and at least one single place
        partly in BC and partly outside. Namesakes elsewhere are counted in
        kinds but do not turn this into "both", because "both" means a BC
        place and a non-BC place with the same name, and here the BC part is
        the spanning place itself;
      contains_bc: no place in or across BC, and at least one large area
        that includes BC (Canada, North America ...);
      ocean_no_country: none of the above, and at least one ocean or sea
        with no country (it may or may not touch BC);
      outside_bc_only: everything else.

    Also returned: how many places were found, how many of each kind, how
    the name matched, whether a historic name matched, how many places were
    found only through a hand-added entry (hand_places), and whether all were.
    """
    kinds = {kind: sum(p["kind"] == kind for p in places) for kind in KINDS}
    if how["match"] == "too generic":
        category = "too_generic"
    elif not places:
        category = "not_found"
    elif kinds["in_bc"] and len(places) > kinds["in_bc"]:
        category = "both"
    elif kinds["in_bc"]:
        category = "in_bc_only"
    elif kinds["spans_bc_border"]:
        category = "spans_bc_border"
    elif kinds["contains_bc"]:
        category = "contains_bc"
    elif kinds["ocean_no_country"]:
        category = "ocean_no_country"
    else:
        category = "outside_bc_only"
    return {"category": category, "category_text": CATEGORIES[category], "places_found": len(places),
            "kinds": kinds, "looked_up": how["looked_up"], "match": how["match"],
            "matched_on": how["matched_on"], "plural_removed": how["plural_removed"],
            "direction_word_removed": how["direction_word_removed"],
            "historic": any(p["is_historic"] for p in places),
            "hand_places": sum(p["name_type"] == "hand" for p in places),
            "only_hand": bool(places) and all(p["name_type"] == "hand" for p in places)}


def candidate_line(candidate):
    """Write one candidate on one line, codes with their names, for reports."""
    def coded(code, name):
        """A code with its name in brackets, or just the code."""
        return f"{code} ({name})" if name else code

    parts = [candidate["main_name"] or "",
             coded(f"{candidate['feature_class']}.{candidate['feature_code']}", candidate["feature_code_name"])]
    where = [coded(candidate["country_code"], candidate["country_name"]) if candidate["country_code"] else "no country"]
    if candidate["admin1_code"] and candidate["admin1_code"] != "00":
        where.append(coded(candidate["admin1_code"], candidate["admin1_name"]))
    parts.append(" ".join(where))
    if candidate["cc2"]:
        parts.append(f"cc2 {candidate['cc2']}")
    if candidate["population"]:
        parts.append(f"pop {candidate['population']:,}")
    parts.append(f"id {candidate['geonameid']}" if candidate["geonameid"] else "hand-added region")
    text = " | ".join(parts)
    if candidate["matched_name"] != candidate["main_name"]:
        text += f" | via {candidate['matched_from']} {candidate['name_type']} name {candidate['matched_name']!r}"
        text += f" [{candidate['language']}]" if candidate["language"] else ""
        text += " (historic)" if candidate["is_historic"] else ""
    if "similarity" in candidate:
        text += f" | similarity {candidate['similarity']:.2f}"
    return text


def result_lines(result):
    """Write one lookup result as report lines: the query, our summary, and the top candidate."""
    summary, query = result["summary"], result["query"]
    notes = []
    if summary["match"] == "approximate":
        notes.append("approximate")
    if summary["matched_on"] == "as_written":
        notes.append("matched on as_written")
    if summary["plural_removed"]:
        notes.append("found after removing the plural")
    if summary["direction_word_removed"]:
        notes.append(f"found after removing the direction word {summary['direction_word_removed']!r}")
    if summary["historic"]:
        notes.append("a historic name matched")
    if summary["hints"]:
        notes.append(f"hint {', '.join(summary['hints'])!r}: {summary['hint_check']}")
    if summary["only_hand"]:
        notes.append("found only through a hand-added entry")
    counts = ", ".join(f"{kind} {n}" for kind, n in summary["kinds"].items() if n)
    head = f"{query['as_written']!r} -> {query['name']!r}: {summary['category_text'].upper()}"
    if summary["places_found"]:
        head += f" ({summary['places_found']} places: {counts})"
    lines = [head + ("; " + "; ".join(notes) if notes else "")]
    if result["candidates"]:
        lines.append(f"      top [{summary['candidate_kinds'][0]}]: " + candidate_line(result["candidates"][0]))
    return lines


def read_pass1(folder):
    """
    Return the places pass 1 kept in a trial folder, as (record number,
    place) pairs: the cleaned places where the run kept them, else the
    model's answer.
    """
    pairs = []
    with open(os.path.join(folder, "answers.jsonl"), encoding="utf-8") as handle:
        for number, line in enumerate(handle):
            result = json.loads(line)
            if "error" in result:
                continue
            for place in result.get("places", result["answer"]["places"]):
                pairs.append((number, place))
    return pairs


def run_pass1(lookup, folder):
    """
    Look up every place pass 1 kept in a trial folder. Write lookup.txt (for
    reading) and lookup.jsonl (one line per place: record, query, candidates
    and summary as separate fields) there, and return the results and lines.
    """
    results = []
    with open(os.path.join(folder, "lookup.jsonl"), "w", encoding="utf-8") as out:
        for number, place in read_pass1(folder):
            result = lookup.lookup(place["name"], place["as_written"])
            result["record"] = number
            results.append(result)
            out.write(json.dumps(result, ensure_ascii=False) + "\n")
    lines = report_lines(results)
    with open(os.path.join(folder, "lookup.txt"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return results, lines


def report_lines(results):
    """
    Return the lines of lookup.txt for lookup results that carry their
    record number ("record"): each place's result, then the counts. Shared
    with geo_filter_run.py, which writes lookup.txt from saved results.
    """
    lines = []
    for result in results:
        body = result_lines(result)
        lines.append(f"[{result['record']:02d}] {body[0]}")
        lines += body[1:]
    tally = {}
    for result in results:
        tally[result["summary"]["category"]] = tally.get(result["summary"]["category"], 0) + 1
    lines += ["", f"{len(results)} places looked up: " + ", ".join(
        f"{CATEGORIES[k]} {v}" for k, v in sorted(tally.items(), key=lambda kv: -kv[1]))]
    summaries = [r["summary"] for r in results]
    lines.append(f"approximate: {sum(s['match'] == 'approximate' for s in summaries)}; "
                 f"matched on as_written: {sum(s['matched_on'] == 'as_written' for s in summaries)}; "
                 f"plural removed: {sum(s['plural_removed'] for s in summaries)}; "
                 f"direction word removed: {sum(bool(s['direction_word_removed']) for s in summaries)}; "
                 f"historic name: {sum(s['historic'] for s in summaries)}; "
                 f"found only through a hand-added entry: {sum(s['only_hand'] for s in summaries)}")
    return lines


def bench_names(lookup, count, seed):
    """
    Return count names to time the lookup with: real GeoNames main names
    drawn at random (Canada and elsewhere), a fifth of them with one letter
    changed so that the approximate search runs too.
    """
    rows = lookup.con.execute(
        f"SELECT name FROM (SELECT name FROM geonames WHERE name_type = 'main') "
        f"USING SAMPLE reservoir({int(count)} ROWS) REPEATABLE ({int(seed)})").fetchall()
    names = [row[0] for row in rows]
    rng = random.Random(seed)
    for i in range(0, len(names), 5):
        word = names[i]
        if len(word) > 6:
            spot = rng.randrange(3, len(word))
            names[i] = word[:spot] + rng.choice("aeiourst") + word[spot + 1:]
    return names


def peak_memory_mb():
    """Return this process's peak memory use so far, in MB (macOS reports bytes, Linux kilobytes)."""
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / 1e6 if sys.platform == "darwin" else peak / 1e3


def main():
    """Run the lookup on a pass 1 trial folder, or time it on many names."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("--table", default=DEFAULT_TABLE)
    parser.add_argument("--codes", default=DEFAULT_CODES, help="geonames_codes.json")
    parser.add_argument("--hand", default=DEFAULT_HAND, help="geonames_hand_entries.csv ('' to leave out)")
    parser.add_argument("--pass1",
                        help="a pass 1 trial folder (answers.jsonl); writes lookup.txt and lookup.jsonl there")
    parser.add_argument("--bench", type=int, help="time this many lookups")
    parser.add_argument("--seed", type=int, default=20261022)
    parser.add_argument("--memory-limit", default="2GB")
    parser.add_argument("--threads", type=int, default=2)
    args = parser.parse_args()

    import duckdb

    started = time.monotonic()
    lookup = GeoNamesLookup(args.table, args.hand or None, args.codes, args.memory_limit, args.threads)
    print(f"DuckDB {duckdb.__version__}; opened in {time.monotonic() - started:.2f} s; "
          f"{len(lookup.hand_regions)} hand-added regions and {len(lookup.hand_aliases)} hand-added names "
          f"for GeoNames places loaded")
    for entry in lookup.missing_hand_ids:
        print(f"warning: hand-added {entry['name']!r} points to GeoNames id {entry['geonames_id']}, "
              f"which is not in the table")
    if args.pass1:
        _, lines = run_pass1(lookup, args.pass1)
        print("\n".join(lines))
    if args.bench:
        names = bench_names(lookup, args.bench, args.seed)
        started = time.monotonic()
        results = [lookup.lookup(name) for name in names]
        seconds = time.monotonic() - started
        print(f"{len(names)} lookups in {seconds:.1f} s ({1000 * seconds / len(names):.1f} ms each); "
              f"approximate {sum(r['summary']['match'] == 'approximate' for r in results)}, "
              f"not found {sum(r['summary']['category'] == 'not_found' for r in results)}; "
              f"peak memory {peak_memory_mb():,.0f} MB (DuckDB limit {args.memory_limit}, "
              f"{args.threads} threads)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
