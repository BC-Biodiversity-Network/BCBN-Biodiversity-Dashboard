"""
datasets.py

Lunaris datasets for GET /api/datasets (the list: keyword search, filters,
paging, and the counts the filter options need) and GET
/api/datasets/{id} (one dataset with every field, including its location).

The data is lunaris_datasets.parquet, written by
pipeline/lunaris/export_lunaris_datasets.py: one row per copy group of
Lunaris records that filter 3 placed in BC (frontend_group bc_data) or
left for review (pending), showing the most recent copy. We do not host
the data; each dataset has a link to the original site.

Keyword search: the query and the text are written the same way as in
the species search (the search_norm macro of search.py: lower case, no
accents, punctuation turned into spaces). Every word of the query must
start a word somewhere in the title, the abstract or the subjects, so
"eagle" finds "eagles" and "Bald Eagle" but not "beagle". Datasets with
every word in the title come first; then the most recently issued; then by
title.

Filters: catalogue (the source id, as in the counts), final_class and
frontend_group. Each can be given more than once (?catalogue=a&catalogue=b)
and a dataset matching any of the values passes. frontend_group defaults
to bc_data, so pending datasets only show when asked for.

The counts for the filter options (options in the response) follow the
search: each filter's counts use the query and the other filters, but not
its own, so ticking one catalogue still shows how many the others have.

Plain SQL only, so this runs on the cluster's DuckDB 0.10.3.
"""

from api.db import DATASET_FINAL_CLASSES, DATASET_FRONTEND_GROUPS
from api.search import MIN_QUERY_LENGTH, QueryTooShort, normalise

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

# The values the filters take (db.py checks every dataset has one of them
# when the API starts), and the front end group shown when none is asked for.
FRONTEND_GROUPS = DATASET_FRONTEND_GROUPS
FINAL_CLASSES = DATASET_FINAL_CLASSES
DEFAULT_FRONTEND_GROUPS = ["bc_data"]

# How long the abstract preview in the list is, at most, in characters.
PREVIEW_LENGTH = 300

# The search text of each dataset, written the search way once when the API
# starts: the title alone (for ranking) and the title, abstract and subjects
# together (for finding).
DATASET_INDEX_SQL = """
    CREATE TABLE dataset_search AS
    SELECT id,
           ' ' || search_norm(title) AS title_norm,
           ' ' || search_norm(concat_ws(' ', title, abstract,
                                        list_aggregate(subjects, 'string_agg', ' '))) AS text_norm
    FROM lunaris_datasets
"""

# The fields each result in the list has (the detail endpoint has them all).
# licences are as harvested: a name can be long, and some catalogues put
# HTML in it, so the front end must show it as plain text.
LIST_FIELDS = ["id", "title", "abstract", "subjects", "publisher", "issued", "source", "source_name",
               "link", "link_type", "licences", "final_class", "frontend_group", "location_type",
               "organism_level", "copy_count"]


class DatasetsRequestError(ValueError):
    """A filter value the datasets do not have."""


def prepare_datasets(data):
    """
    Build what the dataset endpoints need, once, when the API starts (after
    prepare_search, which makes the search_norm macro): the dataset_search
    table, and a lookup from each other copy's id to the id shown for its
    group, so a link to any copy finds the dataset.
    """
    con = data.connection
    con.execute(DATASET_INDEX_SQL)
    data.dataset_copies = dict(con.execute(
        "SELECT c.id, d.id FROM (SELECT id, unnest(other_copies) AS c FROM lunaris_datasets) AS d").fetchall())
    data.dataset_catalogues = dict(con.execute(
        "SELECT DISTINCT source, source_name FROM lunaris_datasets ORDER BY source").fetchall())


def check_values(name, given, allowed, hint=None):
    """Raise DatasetsRequestError if any value given is not one of allowed.
    The message lists the allowed values, or gives hint instead when there
    are too many to list."""
    unknown = [value for value in given if value not in allowed]
    if unknown:
        raise DatasetsRequestError(
            f"Unknown {name} {', '.join(repr(v) for v in unknown)}; "
            + (hint or f"it can be one of: {', '.join(allowed)}."))


def where_clause(words, filters, leave_out=None):
    """
    Return (SQL condition, parameters) for the search words and the filters
    (a dict from column to its list of values), leaving out the filter named
    leave_out (for that filter's own counts). The words only hold letters,
    digits and single spaces once written the search way, so they are safe
    inside LIKE patterns.
    """
    parts, params = ["TRUE"], []
    for word in words:
        parts.append("s.text_norm LIKE '% ' || ? || '%'")
        params.append(word)
    for column, values in filters.items():
        if column != leave_out and values:
            parts.append(f"list_contains(CAST(? AS VARCHAR[]), d.{column})")
            params.append(values)
    return " AND ".join(parts), params


def preview(text):
    """Return the start of an abstract for the list: up to PREVIEW_LENGTH characters, cut at a space, with "..." when cut."""
    if not text or len(text) <= PREVIEW_LENGTH:
        return text
    cut = text[:PREVIEW_LENGTH].rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:.") + "..."


def option_counts(cursor, words, filters, column, allowed=None, names=None):
    """
    Return the counts for one filter's options: each value with how many
    datasets match the query and the other filters. A value that is
    selected, or in allowed, is listed even when its count is 0. With
    names (a dict from value to readable name), each value also has its
    name.
    """
    condition, params = where_clause(words, filters, leave_out=column)
    counts = dict(cursor.execute(
        f"""SELECT d.{column}, count(*)
            FROM lunaris_datasets AS d JOIN dataset_search AS s USING (id)
            WHERE {condition}
            GROUP BY d.{column}""", params).fetchall())
    for value in list(allowed or []) + list(filters.get(column) or []):
        counts.setdefault(value, 0)
    options = []
    for value, count in counts.items():
        option = {"value": value, "count": count}
        if names is not None:
            option["name"] = names.get(value, value)
        options.append(option)
    if allowed:
        order = {value: n for n, value in enumerate(allowed)}
        return sorted(options, key=lambda o: order.get(o["value"], len(order)))
    return sorted(options, key=lambda o: (-o["count"], o.get("name") or o["value"]))


def list_datasets(data, query=None, catalogue=None, final_class=None, frontend_group=None,
                  page=1, page_size=DEFAULT_PAGE_SIZE):
    """
    Return one page of datasets matching the query and filters, best first,
    with the total, the filters used and the counts for the filter options.
    Raises QueryTooShort for a query with fewer than MIN_QUERY_LENGTH
    letters or digits, and DatasetsRequestError for an unknown filter value.
    """
    cursor = data.cursor()
    frontend_group = frontend_group or list(DEFAULT_FRONTEND_GROUPS)
    check_values("frontend_group", frontend_group, FRONTEND_GROUPS)
    check_values("final_class", final_class or [], FINAL_CLASSES)
    check_values("catalogue", catalogue or [], data.dataset_catalogues,
                 hint="use a value from options.catalogue (a source id such as catalogue.data.gov.bc.ca).")

    normalised = None
    words = []
    if query is not None and query.strip():
        normalised = normalise(cursor, query)
        if len(normalised.replace(" ", "")) < MIN_QUERY_LENGTH:
            raise QueryTooShort(
                f"Search needs at least {MIN_QUERY_LENGTH} letters or digits; got {query!r}.")
        words = normalised.split()

    filters = {"source": catalogue or [], "final_class": final_class or [], "frontend_group": frontend_group}
    condition, params = where_clause(words, filters)
    total = cursor.execute(
        f"""SELECT count(*) FROM lunaris_datasets AS d JOIN dataset_search AS s USING (id)
            WHERE {condition}""", params).fetchone()[0]

    # Datasets with every word in the title first (tier 0).
    in_title = " AND ".join(["s.title_norm LIKE '% ' || ? || '%'"] * len(words)) or "TRUE"
    columns = ", ".join(f"d.{field}" for field in LIST_FIELDS)
    rows = cursor.execute(
        f"""SELECT {columns}
            FROM lunaris_datasets AS d JOIN dataset_search AS s USING (id)
            WHERE {condition}
            ORDER BY CASE WHEN {in_title} THEN 0 ELSE 1 END, d.issued DESC NULLS LAST, d.title, d.id
            LIMIT ? OFFSET ?""",
        params + words + [page_size, (page - 1) * page_size]).fetchall()

    results = []
    for row in rows:
        result = dict(zip(LIST_FIELDS, row))
        result["abstract_preview"] = preview(result.pop("abstract"))
        results.append(result)

    return {
        "query": query,
        "normalised_query": normalised,
        "filters": {"catalogue": filters["source"], "final_class": filters["final_class"],
                    "frontend_group": frontend_group},
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": -(-total // page_size),
        "results": results,
        "options": {
            "catalogue": option_counts(cursor, words, filters, "source", names=data.dataset_catalogues),
            "final_class": option_counts(cursor, words, filters, "final_class", allowed=FINAL_CLASSES),
            "frontend_group": option_counts(cursor, words, filters, "frontend_group", allowed=FRONTEND_GROUPS),
        },
    }


def get_dataset(data, dataset_id):
    """
    Return one dataset with every field, or None for an unknown id. The id
    of another copy of a dataset gives the copy that is shown, with
    requested_copy set to the id asked for.
    """
    requested_copy = None
    shown_id = data.dataset_copies.get(dataset_id)
    if shown_id is not None:
        requested_copy, dataset_id = dataset_id, shown_id
    cursor = data.cursor()
    cursor.execute("SELECT * FROM lunaris_datasets WHERE id = ?", [dataset_id])
    row = cursor.fetchone()
    if row is None:
        return None
    dataset = dict(zip([column[0] for column in cursor.description], row))
    dataset["requested_copy"] = requested_copy
    return dataset
