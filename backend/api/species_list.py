"""
species_list.py

The species lists of the rank card (ranks.py) and of a hexagon's panel
(cells.py), which work the same way: a filter by name, a filter by BC
status, an order, and one page at a time.

Name filter (q): the species with any searchable name holding q, written
the search way: the same names as the species search (the card name,
English names, BCSEE labels, merged names; see search.py), found by
cells.matching_keys.

Status filter (status, any of red, blue, yellow, exotic, none): the species
with any of the statuses asked for among their shown BCSEE statuses (Red,
Blue, Yellow, Exotic: the chips in the list), and "none" for the species
with none of them. The shown statuses are those of every BCSEE entry on the
species' key, a subspecies, variety or population included, so Canada Goose
(Yellow, with its occidentalis subspecies Red) counts as Red and as Yellow.
Several statuses keep the species with any of them.

Order (sort): most_records (the default: most records first), fewest_records,
scientific (the Catalogue of Life name, A to Z) or english (the English name
from BCSEE, A to Z, the species without one last, by scientific name).
Names are compared ignoring case. Ties go to the scientific name, then the
key, so the order never changes between pages.

Counts: matching is how many species pass both filters (for paging);
status_counts gives, for each status option, how many species pass the
name filter and have that status, so each checkbox can say how many it
would show. They follow the name filter (and whatever the list itself is
restricted to: the years of a hexagon, the hidden species left out), not
the status filter, so a count does not drop to 0 when its box is not
ticked.

The rows come from the caller, already without the species sensitive.py
hides there, so nothing here can reveal one.
"""

from api.species import NO_CARD, details_from_row

# The orders a list can be in; the first is the default.
SORTS = ("most_records", "fewest_records", "scientific", "english")
DEFAULT_SORT = SORTS[0]

# The status filter's options: each shown status, lower case, and "none".
STATUS_OPTIONS = ("red", "blue", "yellow", "exotic", "none")


class ListRequestError(ValueError):
    """An unknown sort or status."""


def check_list_options(sort, statuses):
    """Return (sort, statuses) as used here: the sort (the default when not
    given) and the status options asked for, lower case, without repeats,
    in STATUS_OPTIONS order. Raises ListRequestError for an unknown one."""
    sort = sort or DEFAULT_SORT
    if sort not in SORTS:
        raise ListRequestError(f"sort must be one of {', '.join(SORTS)}; got {sort!r}.")
    asked = {status.strip().lower() for status in statuses or [] if status.strip()}
    unknown = sorted(asked - set(STATUS_OPTIONS))
    if unknown:
        raise ListRequestError(f"status must be among {', '.join(STATUS_OPTIONS)}; got {', '.join(unknown)}.")
    return sort, [option for option in STATUS_OPTIONS if option in asked]


def status_options_of(details):
    """Return the status options a species counts for: its shown statuses,
    lower case, or ["none"] when it has none."""
    return [status.lower() for status in details["statuses"]] or ["none"]


def sort_key(sort):
    """Return the key that puts (key, records, details) items in the order
    asked for (see the top of this file)."""
    def name(details):
        return (details["col_name"] or "").casefold()

    if sort == "fewest_records":
        return lambda item: (item[1], name(item[2]), item[0])
    if sort == "scientific":
        return lambda item: (name(item[2]), item[0])
    if sort == "english":
        return lambda item: (item[2]["english_name"] is None, (item[2]["english_name"] or "").casefold(),
                             name(item[2]), item[0])
    return lambda item: (-item[1], name(item[2]), item[0])


def list_items(rows):
    """Return [(key, records, details)] from rows of (key, records,
    col_name, name_source, BCSEE entries, BCSEE statuses), as ranks.py and
    cells.py read them (a key with no card gets NO_CARD; only a guard)."""
    return [(key, records, details_from_row(col_name, name_source, entries, statuses)
             if col_name is not None else dict(NO_CARD))
            for key, records, col_name, name_source, entries, statuses in rows]


def species_page(items, keep, statuses, sort, offset, limit):
    """Return one page of a species list and its counts (see the top of this
    file). items is [(key, records, details)], every species of the list;
    keep is the set of keys the name filter keeps, or None for no name
    filter; statuses and sort are as check_list_options returns them.
    Returns {matching, status_counts, results}, each result {specieskey,
    col_name, name_source, english_name, statuses, records}."""
    named = [item for item in items if keep is None or item[0] in keep]
    status_counts = {option: 0 for option in STATUS_OPTIONS}
    for _, _, details in named:
        for option in status_options_of(details):
            status_counts[option] += 1
    wanted = set(statuses)
    shown = [item for item in named if not wanted or wanted & set(status_options_of(item[2]))]
    shown.sort(key=sort_key(sort))
    return {
        "matching": len(shown),
        "status_counts": status_counts,
        "results": [{"specieskey": key, **details, "records": records}
                    for key, records, details in shown[offset:offset + limit]],
    }

