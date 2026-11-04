"""
rank_search.py

The ranks in the species search (GET /api/search): kingdoms, phyla,
classes, orders, families and genera whose name matches the query, each
with a label that tells homonyms apart.

What is searched: every rank path in the species cards (ranks.py names a
rank by its full path, never its name alone), built once when the API
starts into the table rank_names, with the species and records under each
path. The species sensitive.hidden_species gives for lists with no location
(asked with no resolution, as for the rank card) are left out first, so a
rank whose species are all hidden is never found and no count includes a
hidden species. The setting is fixed while the API runs, so the table is
built once.

How names are compared: as in the species search (the search_norm macro in
search.py). Ranking: a name equal to the query first, then a name with a
word that starts with it, then a name holding it anywhere; ties go to the
rank with more records, then the higher rank, then by name.

Labels: "Genus Prunella (Lamiaceae)": the rank, its name, and its nearest
parent that has a name. Prunella is also a genus of birds, "Genus Prunella
(Prunellidae)". When the nearest parent does not tell a rank from another
of the same rank and name (GBIF sometimes files one genus under two orders,
or once with no order), the label walks up the path from the parent until it
does, naming a missing rank as "no order": "Genus Lachesilla
(Lachesillidae, Psocodea)" and "Genus Lachesilla (Lachesillidae, no
order)". A kingdom has no parent: "Kingdom Animalia".

Plain SQL only, so this runs on the cluster's DuckDB 0.10.3.
"""

from api.ranks import RANKS
from api.sensitive import hidden_species

# How many ranks a search returns by default, and at most.
DEFAULT_RANK_LIMIT = 5
MAX_RANK_LIMIT = 20

# How a rank is written in a label.
RANK_WORDS = {"kingdom": "Kingdom", "phylum": "Phylum", "class": "Class", "order": "Order",
              "family": "Family", "genus": "Genus"}

# The path columns of rank_names, quoted ("order" is a SQL word).
PATH_COLUMNS = ", ".join(f'"{rank}"' for rank in RANKS)


def rank_index_select(depth):
    """Return the SELECT for every path down to the rank at depth (0 for
    kingdom), with the species and records under it, leaving out the hidden
    species (the one parameter, a list of keys). Ranks below it are NULL."""
    rank = RANKS[depth]
    columns = ", ".join(f'"{name}"' if index <= depth else f'NULL::VARCHAR AS "{name}"'
                        for index, name in enumerate(RANKS))
    group = ", ".join(f'"{name}"' for name in RANKS[:depth + 1])
    return f"""SELECT '{rank}' AS rank, {depth} AS depth, {columns}, "{rank}" AS name,
                      count(*)::BIGINT AS species_count, sum(record_count)::BIGINT AS record_count
               FROM species_card
               WHERE "{rank}" IS NOT NULL AND NOT list_contains(CAST(? AS VARCHAR[]), specieskey)
               GROUP BY {group}"""


def prepare_rank_search(data):
    """Build the rank_names table on data's connection, once, when the API
    starts (after search.prepare_search has made the search_norm macro)."""
    con = data.connection
    hidden = sorted(hidden_species(data.settings, None))
    selects = " UNION ALL ".join(rank_index_select(depth) for depth in range(len(RANKS)))
    con.execute(f"CREATE TABLE rank_names AS SELECT *, search_norm(name) AS norm FROM ({selects})",
                [hidden] * len(RANKS))


# The rank search: the same tiers as the species search.
RANK_SEARCH_SQL = f"""
    WITH q AS (SELECT search_norm(?) AS q)
    SELECT r.rank, {", ".join(f'r."{rank}"' for rank in RANKS)}, r.name, r.species_count, r.record_count,
           CASE WHEN r.norm = q.q THEN 0
                WHEN r.norm LIKE q.q || '%' OR r.norm LIKE '% ' || q.q || '%' THEN 1
                ELSE 2 END AS tier
    FROM rank_names AS r, q
    WHERE r.norm LIKE '%' || q.q || '%'
    ORDER BY tier, r.record_count DESC, r.depth, r.name, {", ".join(f'r."{rank}" NULLS FIRST' for rank in RANKS)}
    LIMIT ?
"""

# How well a name matched, by tier number (as in search.py).
MATCH_KINDS = {0: "exact", 1: "word_start", 2: "contains"}


def ancestors(path):
    """Return a path's ancestors from the parent upward, as [(rank, name or None)]."""
    return list(reversed(path[:-1]))


def written(ancestor):
    """An ancestor as a label writes it: its name, or "no <rank>" when it is missing."""
    rank, name = ancestor
    return name if name else f"no {rank}"


def parent_words(path, siblings):
    """Return the words in a label's brackets for a rank with this path,
    told apart from siblings (the paths of every other rank with the same
    rank and name): its nearest named parent, or, when that is shared, its
    ancestors from the parent upward until they differ (see the top of this
    file). [] for a kingdom."""
    above = ancestors(path)
    named = [name for _, name in above if name]
    if not named:
        return []
    if all(next((name for _, name in ancestors(other) if name), None) != named[0] for other in siblings):
        return [named[0]]
    for count in range(1, len(above) + 1):
        mine = above[:count]
        if all(ancestors(other)[:count] != mine for other in siblings) and any(name for _, name in mine):
            return [written(ancestor) for ancestor in mine]
    return [written(ancestor) for ancestor in above]


def rank_label(path, siblings):
    """Return a rank's label: "Genus Prunella (Lamiaceae)" (see the top of this file)."""
    rank, name = path[-1]
    words = parent_words(path, siblings)
    return f"{RANK_WORDS[rank]} {name}" + (f" ({', '.join(words)})" if words else "")


def read_siblings(cursor, rows):
    """Return {(rank, name): [path, ...]}: every path in rank_names with the
    rank and name of one of the rows, each as [(rank, name or None)] down to
    the rank."""
    wanted = sorted({f"{row[0]}|{row[7]}" for row in rows})
    if not wanted:
        return {}
    found = {}
    for rank, *names, name in cursor.execute(
            f"""SELECT rank, {PATH_COLUMNS}, name FROM rank_names
                WHERE list_contains(CAST(? AS VARCHAR[]), rank || '|' || name)""", [wanted]).fetchall():
        depth = RANKS.index(rank)
        found.setdefault((rank, name), []).append(list(zip(RANKS[:depth + 1], names[:depth + 1])))
    return found


def search_ranks(cursor, query, limit=DEFAULT_RANK_LIMIT):
    """Return up to limit ranks matching query, best first (see the top of
    this file), each with its path, label, species and records, and how it
    matched. The query has been checked by search.search."""
    if limit <= 0:
        return []
    rows = cursor.execute(RANK_SEARCH_SQL, [query, limit]).fetchall()
    siblings = read_siblings(cursor, rows)
    results = []
    for rank, *names, name, species_count, record_count, tier in rows:
        depth = RANKS.index(rank)
        path = list(zip(RANKS[:depth + 1], names[:depth + 1]))
        others = [other for other in siblings.get((rank, name), []) if other != path]
        results.append({
            "rank": rank,
            "name": name,
            "path": [{"rank": path_rank, "name": path_name} for path_rank, path_name in path],
            "label": rank_label(path, others),
            "species_count": species_count,
            "record_count": record_count,
            "match": {"kind": MATCH_KINDS[tier]},
        })
    return results
