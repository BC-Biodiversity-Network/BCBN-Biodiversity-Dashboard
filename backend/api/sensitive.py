"""
sensitive.py

The one place the per-hexagon species list (cells.py) and the rank card
(ranks.py) ask which species they must not show, so that the
sensitive-species rule, once it is decided, covers those lists as well as
the maps. A list tied to a hexagon asks with the hexagon's resolution; a
list with no location (a rank card) asks with none.

The rule is not decided yet (waiting on Evan): which species, how coarse,
and what the card shows. BCBN_SENSITIVE_MAX_RES must be empty for now
(settings.py stops the API at start-up if it has a value), so no species is
hidden anywhere. When the rule exists, hidden_species is where it goes:
for example, the keys of the sensitive species whenever res is finer than
BCBN_SENSITIVE_MAX_RES, and whatever the rule decides for lists with no
location (res None), such as a genus with one species. cells.py leaves those
species out of the list, the totals and the species asked for, and ranks.py
out of the card's list, totals and children, so a hidden species is not
revealed by a count either.
"""

from api.settings import SettingsError, not_implemented_message


def hidden_species(settings, res):
    """Return the species keys that must not be shown, as a frozenset: in a
    list for a hexagon at resolution res, or in a list with no location
    (res None, a rank card). With BCBN_SENSITIVE_MAX_RES empty (the only
    value allowed for now) there are none. A value stops here too, as
    at start-up, so a setting can never be taken for a protection that does
    not exist."""
    if settings.sensitive_max_res is None:
        return frozenset()
    raise SettingsError(not_implemented_message(settings.sensitive_max_res))
