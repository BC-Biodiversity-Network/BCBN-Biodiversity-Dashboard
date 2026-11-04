"""
sensitive.py

The one place the per-hexagon species list (cells.py) asks which species it
must not list at a resolution, so that the sensitive-species rule, once it
is decided, covers per-hexagon lists as well as the maps.

The rule is not decided yet (waiting on Evan): which species, how coarse,
and what the card shows. BCBN_SENSITIVE_MAX_RES must be empty for now
(settings.py stops the API at start-up if it has a value), so no species is
hidden anywhere. When the rule exists, hidden_species is where it goes:
for example, the keys of the sensitive species whenever res is finer than
BCBN_SENSITIVE_MAX_RES. cells.py leaves those species out of the list, the
totals and the species asked for, so a hidden species is not revealed by a
count either.
"""

from api.settings import SettingsError, not_implemented_message


def hidden_species(settings, res):
    """Return the species keys that must not be listed per hexagon at
    resolution res, as a frozenset. With BCBN_SENSITIVE_MAX_RES empty (the
    only value allowed for now) there are none. A value stops here too, as
    at start-up, so a setting can never be taken for a protection that does
    not exist."""
    if settings.sensitive_max_res is None:
        return frozenset()
    raise SettingsError(not_implemented_message(settings.sensitive_max_res))
