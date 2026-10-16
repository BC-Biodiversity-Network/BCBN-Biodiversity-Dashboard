"""
search.py

Species search for GET /api/search.

What is searched, for every species with a card (species BCSEE lists but
with no records in BC have no card and are never found):
    col_name                    the card's Catalogue of Life name; a name
                                with a subgenus ("Culex (Culex) pipiens")
                                is also searchable without it ("Culex
                                pipiens")
    english_name                the English name of the species' own
                                species-level BCSEE entry (the same one the
                                card shows)
    bcsee_entry_english_name    the English name of any other BCSEE entry
                                on the species: a subspecies, a population,
                                or a separate BCSEE species on the same key
                                ("small-flowered fiddleneck"). It finds the
                                card, but it is not the species' English
                                name, and the result says so.
    bcsee_scientific_label      the scientific label of each BCSEE entry
    merged_name                 the Catalogue of Life name of a key merged
                                into the card under another name (a
                                synonym, such as Accipiter cooperii for
                                Astur cooperii); names that differ only by a
                                subgenus are left out, as on the card
    the species key             typed exactly (any letter case); an old key
                                that was merged finds the kept key's card,
                                with merged_from, as on the card endpoint

How names are compared: names and the query are both written the same way
first (the SQL macro search_norm): lower case, accents removed, apostrophes
dropped, every other character that is not a letter or digit turned into a
space (so "Douglas-fir" and "Dahlia × pinnata" become "douglas fir" and
"dahlia pinnata"), and a lone "x" between words dropped (BCSEE writes
hybrids as "Dahlia x pinnata"). There is no typo tolerance: a word has to
be spelled right.

Ranking: a name equal to the query first, then a name with a word that
starts with the query, then a name that holds the query anywhere. Ties go
to the species with more records. A typed species key comes before all of
them. Each species appears once, under its best-matching name; between
names that match equally well, the card name wins, then the English name,
then a merged name, then a BCSEE scientific label, then a BCSEE entry's
English name.

Every result lists its names in the agreed order (display_names): the card
name, then the English name, then the merged name or BCSEE name that
matched if it is neither of those, each with where it comes from.

Plain SQL only, so this runs on the cluster's DuckDB 0.10.3.
"""

from api.species import english_name

# Shortest query, after it is written the search way, that is searched.
MIN_QUERY_LENGTH = 2

# How many results a search returns by default, and at most.
DEFAULT_LIMIT = 20
MAX_LIMIT = 50

# How a name and a query are written before they are compared (see the top
# of this file). The query goes through the same macro, so the two can
# never be written differently.
SEARCH_NORM_MACRO = r"""
    CREATE MACRO search_norm(t) AS trim(regexp_replace(regexp_replace(
        ' ' || regexp_replace(
            regexp_replace(lower(strip_accents(coalesce(t, ''))), '[''’‘`]', '', 'g'),
            '[^a-z0-9]+', ' ', 'g') || ' ',
        ' x ', ' ', 'g'), ' +', ' ', 'g'))
"""

# One row per searchable name: the species, which field it is, the name as
# it is shown, for an entry's English name the entry's scientific label,
# and the name written the search way.
SEARCH_INDEX_SQL = r"""
    CREATE TABLE search_names AS
    WITH entries AS (
        SELECT b.gbif_species_key AS specieskey, unnest(b.entries) AS e
        FROM bcsee_status AS b
        WHERE b.gbif_species_key IN (SELECT specieskey FROM species_card)
    ),
    names AS (
        SELECT specieskey, 'col_name' AS field, col_name AS name,
               CAST(NULL AS VARCHAR) AS entry_label, col_name AS match_text
        FROM species_card
        UNION ALL
        -- The same name without its subgenus: "Culex (Culex) pipiens" is
        -- also found as "Culex pipiens".
        SELECT specieskey, 'col_name', col_name, NULL,
               regexp_replace(col_name, '\s*\([^)]*\)', '', 'g')
        FROM species_card
        WHERE col_name LIKE '%(%'
        UNION ALL
        SELECT specieskey,
               CASE WHEN e.is_species_level THEN 'english_name'
                    ELSE 'bcsee_entry_english_name' END,
               trim(e.common_label),
               CASE WHEN e.is_species_level THEN NULL ELSE e.scientific_label END,
               e.common_label
        FROM entries
        WHERE trim(coalesce(e.common_label, '')) <> ''
        UNION ALL
        SELECT specieskey, 'bcsee_scientific_label', trim(e.scientific_label), NULL,
               e.scientific_label
        FROM entries
        WHERE trim(coalesce(e.scientific_label, '')) <> ''
        UNION ALL
        -- A merged key's own name, when it is not the card's name (leaving
        -- out names that differ only by a subgenus, as the card does).
        SELECT specieskey, 'merged_name', m.name, NULL, m.name
        FROM (SELECT specieskey, col_name, unnest(merged_names) AS m FROM species_card)
        WHERE regexp_replace(m.name, '\s*\([^)]*\)', '', 'g')
              <> regexp_replace(col_name, '\s*\([^)]*\)', '', 'g')
    )
    SELECT DISTINCT specieskey, field, name, entry_label, search_norm(match_text) AS norm
    FROM names
    WHERE search_norm(match_text) <> ''
"""

# The name search. The query only holds letters, digits and single spaces
# once written the search way, so it is safe inside LIKE patterns.
SEARCH_SQL = """
    WITH q AS (SELECT search_norm(?) AS q),
    hits AS (
        SELECT n.specieskey, n.field, n.name, n.entry_label,
               CASE WHEN n.norm = q.q THEN 0
                    WHEN n.norm LIKE q.q || '%' OR n.norm LIKE '% ' || q.q || '%' THEN 1
                    ELSE 2 END AS tier,
               CASE n.field WHEN 'col_name' THEN 0 WHEN 'english_name' THEN 1
                            WHEN 'merged_name' THEN 2 WHEN 'bcsee_scientific_label' THEN 3
                            ELSE 4 END AS field_order
        FROM search_names AS n, q
        WHERE n.norm LIKE '%' || q.q || '%'
    ),
    best AS (
        SELECT * FROM hits
        QUALIFY row_number() OVER (
            PARTITION BY specieskey ORDER BY tier, field_order, name) = 1
    )
    SELECT b.specieskey, b.field, b.name, b.entry_label, b.tier
    FROM best AS b
    JOIN species_card AS c USING (specieskey)
    WHERE NOT list_contains(CAST(? AS VARCHAR[]), b.specieskey)
    ORDER BY b.tier, c.record_count DESC, c.col_name, b.specieskey
    LIMIT ?
"""

# What each result shows besides the match, for the species found.
DETAILS_SQL = """
    SELECT c.specieskey, c.col_name, c.col_authorship, c.name_source, c.record_count,
           c.kingdom, c.class, c.family, b.entries
    FROM species_card AS c
    LEFT JOIN bcsee_status AS b ON b.gbif_species_key = c.specieskey
    WHERE list_contains(CAST(? AS VARCHAR[]), c.specieskey)
"""

# How well a name matched, by tier number.
MATCH_KINDS = {0: "exact", 1: "word_start", 2: "contains"}


class QueryTooShort(ValueError):
    """The query has fewer than MIN_QUERY_LENGTH letters or digits."""


def prepare_search(data):
    """Build what search needs, once, when the API starts: the search_norm
    macro and the search_names table (on data's connection), and a lookup
    from each key in lower case to (card key, merged_from) for typed keys."""
    con = data.connection
    con.execute(SEARCH_NORM_MACRO)
    con.execute(SEARCH_INDEX_SQL)

    keys = {}
    for (key,) in con.execute("SELECT specieskey FROM species_card").fetchall():
        keys[key.lower()] = (key, None)
    for old_key, kept_key in data.merged_keys.items():
        keys[old_key.lower()] = (kept_key, old_key)
    data.search_keys = keys


def normalise(cursor, query):
    """Write a query the search way (the search_norm macro)."""
    return cursor.execute("SELECT search_norm(?)", [query]).fetchone()[0]


def display_names(details, match, name_source, status_source):
    """The names to show for a result, in the agreed order: the card name,
    the English name, then the merged name or BCSEE name that matched if
    it is neither. Each says which field it is and where it comes from: a
    merged name from the card's name source (the Catalogue of Life), a
    BCSEE name from BCSEE."""
    names = [{"text": details["col_name"], "field": "col_name", "source": name_source}]
    english = english_name(details["entries"])
    if english:
        names.append({"text": english, "field": "english_name", "source": status_source})

    if match["field"] == "merged_name":
        shown = {name["text"].lower() for name in names}
        if match["text"].lower() not in shown:
            names.append({"text": match["text"], "field": "merged_name", "source": name_source})
    if match["field"] in ("bcsee_scientific_label", "bcsee_entry_english_name"):
        shown = {name["text"].lower() for name in names}
        if match["text"].lower() not in shown:
            extra = {"text": match["text"], "field": match["field"], "source": status_source}
            if match["field"] == "bcsee_entry_english_name":
                extra["bcsee_entry_scientific_label"] = match["bcsee_entry_scientific_label"]
            names.append(extra)
    return names


def build_result(details, match, merged_from, status_source):
    """Turn one species' details and its match into a search result."""
    english = english_name(details["entries"])
    return {
        "specieskey": details["specieskey"],
        "merged_from": merged_from,
        "col_name": details["col_name"],
        "col_authorship": details["col_authorship"],
        "name_source": details["name_source"],
        "english_name": english,
        "english_name_source": status_source if english else None,
        "record_count": details["record_count"],
        "kingdom": details["kingdom"],
        "class": details["class"],
        "family": details["family"],
        "match": match,
        "display_names": display_names(details, match, details["name_source"],
                                       status_source),
    }


def search(data, query, limit=DEFAULT_LIMIT):
    """Search the species for query and return the response: the query,
    the query written the search way, and up to limit results, best first.
    Raises QueryTooShort if the query has fewer than MIN_QUERY_LENGTH
    letters or digits."""
    cursor = data.cursor()
    normalised = normalise(cursor, query)
    if len(normalised.replace(" ", "")) < MIN_QUERY_LENGTH:
        raise QueryTooShort(
            f"Search needs at least {MIN_QUERY_LENGTH} letters or digits; "
            f"got {query!r}.")

    # A typed key (or an old merged key) comes first.
    found = []
    typed = data.search_keys.get(query.strip().lower())
    if typed:
        card_key, merged_from = typed
        found.append((card_key, {"field": "merged_key" if merged_from else "specieskey",
                                 "text": query.strip(), "kind": "key",
                                 "bcsee_entry_scientific_label": None}, merged_from))

    rows = cursor.execute(
        SEARCH_SQL, [query, [key for key, _, _ in found], limit - len(found)]
    ).fetchall() if limit > len(found) else []
    for key, field, name, entry_label, tier in rows:
        found.append((key, {"field": field, "text": name, "kind": MATCH_KINDS[tier],
                            "bcsee_entry_scientific_label": entry_label}, None))

    columns = ["specieskey", "col_name", "col_authorship", "name_source", "record_count",
               "kingdom", "class", "family", "entries"]
    details = {row[0]: dict(zip(columns, row)) for row in cursor.execute(
        DETAILS_SQL, [[key for key, _, _ in found]]).fetchall()}

    status_source = data.meta["statuses"]["status_source"]
    return {
        "query": query,
        "normalised_query": normalised,
        "results": [build_result(details[key], match, merged_from, status_source)
                    for key, match, merged_from in found],
    }
