"""
datasets.py

Lunaris datasets for GET /api/datasets (the list: keyword search, sort,
filters, paging, and the counts the filter options need) and GET
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
"eagle" finds "eagles" and "Bald Eagle" but not "beagle".

Sort (sort): relevance (datasets with every word in the title first, then
the most recently issued; only with a search, and the default then),
newest (the default without a search), oldest, or title (A to Z, ignoring
case and accents). Datasets with no issued date go last; ties are broken
by id, so paging is stable.

Filters, each also in the counts:
  catalogue, final_class, frontend_group
            the source id, class and front end group; each can be given
            more than once (?catalogue=a&catalogue=b) and a dataset
            matching any of the values passes. frontend_group defaults to
            bc_data, so pending datasets only show when asked for.
  year_from, year_to
            the year of the issued date (when the record was published,
            not when the data were collected), either end open. Datasets
            with no issued date are left out only when one is given.
  organism  a name that must start a word of one of the organism names
            stored for the dataset (both filter 2 runs), written the
            search way; several words must follow each other in one name.
  access    open or restricted, from the record's access right
            (info:eu-repo/semantics/openAccess or restrictedAccess).
  mappable  true for only the datasets the map can draw something for
            (the export's mappable column).

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

# The orders the list can be sorted in, and the access rights a record can
# give, with the DataCite rights URI of each.
SORTS = ["relevance", "newest", "oldest", "title"]
ACCESS_URIS = {
    "open": "info:eu-repo/semantics/openAccess",
    "restricted": "info:eu-repo/semantics/restrictedAccess",
}
ACCESS_VALUES = list(ACCESS_URIS)

# How long the abstract preview in the list is, at most, in characters.
PREVIEW_LENGTH = 300

# What each dataset is searched and filtered on, worked out once when the
# API starts: the title alone (for ranking and the title sort) and the
# title, abstract and subjects together (for finding), written the search
# way; the year of the issued date; and the access right.
DATASET_INDEX_SQL = f"""
    CREATE TABLE dataset_search AS
    SELECT d.id,
           ' ' || search_norm(d.title) AS title_norm,
           ' ' || search_norm(concat_ws(' ', d.title, d.abstract,
                                        list_aggregate(d.subjects, 'string_agg', ' '))) AS text_norm,
           TRY_CAST(substr(NULLIF(d.issued, ''), 1, 4) AS INTEGER) AS year,
           coalesce(a.access, '') AS access
    FROM lunaris_datasets AS d
    LEFT JOIN (
        SELECT id,
               CASE WHEN bool_or(rights.uri = '{ACCESS_URIS["open"]}') THEN 'open'
                    WHEN bool_or(rights.uri = '{ACCESS_URIS["restricted"]}') THEN 'restricted' END AS access
        FROM (SELECT id, unnest(licences) AS rights FROM lunaris_datasets)
        GROUP BY id
    ) AS a USING (id)
"""

# One row per organism name of each dataset, written the search way, so a
# search for a name never runs across two names.
DATASET_ORGANISMS_SQL = """
    CREATE TABLE dataset_organism_names AS
    SELECT id, ' ' || search_norm(name) AS name_norm
    FROM (SELECT id, unnest(organisms) AS name FROM lunaris_datasets)
"""

# The fields each result in the list has (the detail endpoint has them all).
# licences are as harvested: a name can be long, and some catalogues put
# HTML in it, so the front end must show it as plain text.
LIST_FIELDS = ["id", "title", "abstract", "subjects", "publisher", "issued", "source", "source_name",
               "link", "link_type", "licences", "final_class", "frontend_group", "location_type",
               "organism_level", "organisms", "mappable", "copy_count"]

# How each sort orders the list; {in_title} is the condition that every
# search word is in the title. Every order ends with the id, so it is stable.
ORDER_BY = {
    "relevance": "CASE WHEN {in_title} THEN 0 ELSE 1 END, NULLIF(d.issued, '') DESC NULLS LAST, d.id",
    "newest": "NULLIF(d.issued, '') DESC NULLS LAST, d.id",
    "oldest": "NULLIF(d.issued, '') ASC NULLS LAST, d.id",
    "title": "s.title_norm ASC NULLS LAST, d.id",
}

# The tables every list query reads.
FROM_DATASETS = "lunaris_datasets AS d JOIN dataset_search AS s USING (id)"


class DatasetsRequestError(ValueError):
    """A filter value the datasets do not have, or a sort or filter that cannot be used."""


def prepare_datasets(data):
    """
    Build what the dataset endpoints need, once, when the API starts (after
    prepare_search, which makes the search_norm macro): the dataset_search
    and dataset_organism_names tables, a lookup from each other copy's id to
    the id shown for its group (so a link to any copy finds the dataset),
    and the readable name of each catalogue.
    """
    con = data.connection
    con.execute(DATASET_INDEX_SQL)
    con.execute(DATASET_ORGANISMS_SQL)
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


def build_conditions(words, organism, filters):
    """
    Return the conditions of a list request, by name, each as (SQL,
    parameters): the search words ("query"), the organism phrase
    ("organism"), and each filter in filters (a dict from name to its
    value; empty values are left out). The words and the phrase only hold
    letters, digits and single spaces once written the search way, so they
    are safe inside LIKE patterns.
    """
    conditions = {}
    if words:
        conditions["query"] = (" AND ".join(["s.text_norm LIKE '% ' || ? || '%'"] * len(words)), list(words))
    if organism:
        conditions["organism"] = (
            "d.id IN (SELECT id FROM dataset_organism_names WHERE name_norm LIKE '% ' || ? || '%')", [organism])
    for name, column in (("catalogue", "d.source"), ("final_class", "d.final_class"),
                         ("frontend_group", "d.frontend_group"), ("access", "s.access")):
        if filters.get(name):
            conditions[name] = (f"list_contains(CAST(? AS VARCHAR[]), {column})", [filters[name]])
    year_from, year_to = filters.get("year_from"), filters.get("year_to")
    if year_from is not None or year_to is not None:
        conditions["published"] = ("s.year BETWEEN ? AND ?",
                                   [year_from if year_from is not None else -1,
                                    year_to if year_to is not None else 99999])
    if filters.get("mappable"):
        conditions["mappable"] = ("d.mappable", [])
    return conditions


def where_clause(conditions, leave_out=None):
    """Return (SQL condition, parameters) joining every condition but the one named leave_out (for that filter's own counts)."""
    parts, params = ["TRUE"], []
    for name, (sql, values) in conditions.items():
        if name != leave_out:
            parts.append(f"({sql})")
            params.extend(values)
    return " AND ".join(parts), params


def preview(text):
    """Return the start of an abstract for the list: up to PREVIEW_LENGTH characters, cut at a space, with "..." when cut."""
    if not text or len(text) <= PREVIEW_LENGTH:
        return text
    cut = text[:PREVIEW_LENGTH].rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:.") + "..."


def grouped_counts(cursor, conditions, leave_out, column):
    """Return {value: count} of the datasets matching every condition but leave_out, grouped by column."""
    condition, params = where_clause(conditions, leave_out)
    return dict(cursor.execute(
        f"SELECT {column}, count(*) FROM {FROM_DATASETS} WHERE {condition} GROUP BY {column}", params).fetchall())


def option_counts(cursor, conditions, leave_out, column, selected=(), allowed=None, names=None):
    """
    Return the counts for one filter's options: each value with how many
    datasets match the query and the other filters. A value that is
    selected, or in allowed, is listed even when its count is 0; with
    allowed, only those values are listed, in that order. With names (a
    dict from value to readable name), each value also has its name.
    """
    counts = grouped_counts(cursor, conditions, leave_out, column)
    if allowed is not None:
        counts = {value: counts.get(value, 0) for value in allowed}
    for value in selected:
        counts.setdefault(value, 0)
    options = []
    for value, count in counts.items():
        option = {"value": value, "count": count}
        if names is not None:
            option["name"] = names.get(value, value)
        options.append(option)
    if allowed is not None:
        return options
    return sorted(options, key=lambda o: (-o["count"], o.get("name") or o["value"]))


def published_counts(cursor, conditions):
    """Return the counts for the Published filter: each issued year with its datasets, and how many have no issued date."""
    counts = grouped_counts(cursor, conditions, "published", "s.year")
    return {"years": [{"year": year, "count": counts[year]} for year in sorted(y for y in counts if y is not None)],
            "no_date": counts.get(None, 0)}


def normalised_words(cursor, text, what):
    """Return text written the search way, or None when it is empty; raise QueryTooShort when it is too short."""
    if text is None or not text.strip():
        return None
    normalised = normalise(cursor, text)
    if len(normalised.replace(" ", "")) < MIN_QUERY_LENGTH:
        raise QueryTooShort(f"{what} needs at least {MIN_QUERY_LENGTH} letters or digits; got {text!r}.")
    return normalised


def list_datasets(data, query=None, catalogue=None, final_class=None, frontend_group=None,
                  page=1, page_size=DEFAULT_PAGE_SIZE, sort=None, year_from=None, year_to=None,
                  organism=None, access=None, mappable=False):
    """
    Return one page of datasets matching the query and filters, in the order
    sort asks for (see the top of this file), with the total, the sort and
    filters used and the counts for the filter options. Raises QueryTooShort
    for a query or organism with fewer than MIN_QUERY_LENGTH letters or
    digits, and DatasetsRequestError for an unknown value, relevance without
    a query, or year_from after year_to.
    """
    cursor = data.cursor()
    frontend_group = frontend_group or list(DEFAULT_FRONTEND_GROUPS)
    check_values("frontend_group", frontend_group, FRONTEND_GROUPS)
    check_values("final_class", final_class or [], FINAL_CLASSES)
    check_values("catalogue", catalogue or [], data.dataset_catalogues,
                 hint="use a value from options.catalogue (a source id such as catalogue.data.gov.bc.ca).")
    check_values("access", access or [], ACCESS_VALUES)
    if year_from is not None and year_to is not None and year_from > year_to:
        raise DatasetsRequestError(f"year_from ({year_from}) is after year_to ({year_to}).")

    normalised = normalised_words(cursor, query, "Search")
    words = normalised.split() if normalised else []
    organism_phrase = normalised_words(cursor, organism, "The organism search")
    sort = sort or ("relevance" if words else "newest")
    check_values("sort", [sort], SORTS)
    if sort == "relevance" and not words:
        raise DatasetsRequestError("sort=relevance needs a search (q); use newest, oldest or title.")

    filters = {"catalogue": catalogue or [], "final_class": final_class or [], "frontend_group": frontend_group,
               "access": access or [], "year_from": year_from, "year_to": year_to, "mappable": mappable}
    conditions = build_conditions(words, organism_phrase, filters)
    condition, params = where_clause(conditions)
    total = cursor.execute(f"SELECT count(*) FROM {FROM_DATASETS} WHERE {condition}", params).fetchone()[0]

    in_title = " AND ".join(["s.title_norm LIKE '% ' || ? || '%'"] * len(words)) or "TRUE"
    order_params = list(words) if sort == "relevance" else []
    columns = ", ".join(f"d.{field}" for field in LIST_FIELDS)
    rows = cursor.execute(
        f"""SELECT {columns} FROM {FROM_DATASETS}
            WHERE {condition}
            ORDER BY {ORDER_BY[sort].format(in_title=in_title)}
            LIMIT ? OFFSET ?""",
        params + order_params + [page_size, (page - 1) * page_size]).fetchall()

    results = []
    for row in rows:
        result = dict(zip(LIST_FIELDS, row))
        result["abstract_preview"] = preview(result.pop("abstract"))
        results.append(result)

    mappable_count = grouped_counts(cursor, conditions, "mappable", "d.mappable").get(True, 0)
    return {
        "query": query,
        "normalised_query": normalised,
        "sort": sort,
        "filters": {"catalogue": filters["catalogue"], "final_class": filters["final_class"],
                    "frontend_group": frontend_group, "year_from": year_from, "year_to": year_to,
                    "organism": organism if organism_phrase else None, "access": filters["access"],
                    "mappable": bool(mappable)},
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": -(-total // page_size),
        "results": results,
        "options": {
            "catalogue": option_counts(cursor, conditions, "catalogue", "d.source", selected=filters["catalogue"],
                                       names=data.dataset_catalogues),
            "final_class": option_counts(cursor, conditions, "final_class", "d.final_class",
                                         allowed=FINAL_CLASSES),
            "frontend_group": option_counts(cursor, conditions, "frontend_group", "d.frontend_group",
                                            allowed=FRONTEND_GROUPS),
            "access": option_counts(cursor, conditions, "access", "s.access", allowed=ACCESS_VALUES),
            "published": published_counts(cursor, conditions),
            "location": [{"value": "mappable", "count": mappable_count}],
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
