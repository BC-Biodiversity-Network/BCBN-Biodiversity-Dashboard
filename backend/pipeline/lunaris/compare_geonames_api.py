"""
compare_geonames_api.py  (placeholder, not written yet)

Later: compare our local lookup (geonames_lookup.py) with the GeoNames
search API (https://secure.geonames.org/searchJSON) on a sample of place
names from pass 1, to see where the two disagree.

The plan, for when it is written:
  1. Draw a sample of names from a pass 1 run (a few hundred at most, to
     stay well inside the API's free daily limit).
  2. For each name, ask the API with name_equals (exact) and then with name
     and fuzzy=0.8 when that finds nothing, maxRows=10, orderby=relevance,
     and our lookup the same way.
  3. Compare: is the API's top place among our 10, do the two agree on in
     BC only / outside BC only / both / not found, and which places one
     finds and the other does not.
  4. Write a short report next to the pass 1 run. Nothing is downloaded
     beyond the API's answers for the sample.

The GeoNames username is read from the GEONAMES_USERNAME environment
variable. It is never written into this code, a log, an output file or a
commit; set it in your own shell before running:

    export GEONAMES_USERNAME=...   (your own account, not committed)
    python pipeline/lunaris/compare_geonames_api.py --pass1 data/lunaris_bc_filter/pass1_trial_v3
"""

import os
import sys


def api_username():
    """
    Return the GeoNames username from the GEONAMES_USERNAME environment
    variable, or stop with a message if it is not set. The name itself is
    never printed.
    """
    username = os.environ.get("GEONAMES_USERNAME", "").strip()
    if not username:
        sys.exit("Set GEONAMES_USERNAME in your shell first; it is never stored in code.")
    return username


def main():
    """Placeholder: says what this script will do and stops, without calling the API."""
    print("compare_geonames_api.py is a placeholder; see its docstring for the plan. Nothing was sent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
