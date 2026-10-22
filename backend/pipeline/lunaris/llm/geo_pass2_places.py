"""
The part of the pass 2 prompt that follows the record (pass 2 version C,
geo_pass2_prompt_c.py), and two checks on the model's answer.

After the record text (geo_record_text.record_text), the model sees the
places pass 1 kept, each with its GeoNames candidates from the lookup
(../geonames_lookup.py). The rule throughout is the one pass 1 follows:
everything as it is, and anything we add on a line of its own marked as a
note from us.

  - The places pass 1 kept (as_written, name, role), with a note that a
    model found them and may have made mistakes.
  - For each place, at most MAX_CANDIDATES GeoNames candidates, listed as
    GeoNames has them, each code with its name in brackets from GeoNames'
    own code files, and how many places there were in all. Places added by
    hand are marked as ours, with their note. A place with no candidates
    says "No GeoNames match", or that it was too generic to look up; notes
    say how a name was found when not exactly as given. A name listed twice
    (for two roles) refers back.
  - Affiliations are listed without candidates, with a note saying why.
  - None of our own reading of the candidates (in BC only, both and so on).

This text was first written for the earlier trial versions A and A2 (moved
to backend/exploration/lunaris/bc_filter_2026_10/old_pass2_prompts/); it is
kept here unchanged, so version C's prompts are byte for byte the same.

The checks: evidence_not_in_places (evidence the model cites that pass 1
did not find) and consistency_flags (a location that looks inconsistent
with the model's own reason; it only flags, never changes an answer).
"""

import re

PLACES_HEADER = "Place names found in the record in an earlier step:"

PLACES_NOTE = ("Note from us: a language model found these place names and their roles in an earlier step, "
               "so the list may contain mistakes (a missed place, a misread name, or a name that is not a "
               "place).")

NO_PLACES = "Note from us: the earlier step found no place names in this record."

ROLE_TEXT = {"study_location": "study location", "affiliation": "affiliation",
             "indigenous_nation_or_territory": "Indigenous nation or territory", "other": "other mention"}

MAX_CANDIDATES = 5

# The note before the places: where the candidates and the code names come
# from, the limit of 5 candidates per name, and that affiliations have none.
CANDIDATES_NOTE = ("Note from us: below each place name are the places with that name in GeoNames, listed as "
                   "GeoNames gives them, in the order our lookup ranks them, at most 5 for each name. The "
                   "names in brackets after each code come from GeoNames' own code files "
                   "(admin1CodesASCII, countryInfo, featureCodes_en), not from us. Entries marked \"added by "
                   "us\" are not from GeoNames. Places whose role is affiliation are listed without "
                   "candidates.")

AFFILIATION_NOTE = ("  Note from us: GeoNames candidates are not shown for an affiliation, because an "
                    "affiliation is not where the data were collected.")


def with_name(code, name):
    """Write a code with its name in brackets ("CA (Canada)"), or the code alone when it has no name."""
    return f"{code} ({name})" if name else code


def candidate_lines(number, candidate):
    """
    Return the lines for one GeoNames candidate: its own fields as GeoNames
    has them, codes with their names in brackets. A candidate found through
    a name we added by hand is marked as ours, with our note; a hand-added
    region (no GeoNames id) is marked as not from GeoNames at all.
    """
    c = candidate
    if c["geonameid"] is None:
        return [f"  Candidate {number}: added by us, not from GeoNames: \"{c['main_name']}\", a region in "
                f"{with_name(c['country_code'], c['country_name'])}, province or state "
                f"{with_name(c['admin1_code'], c['admin1_name'])}.",
                f"  Note from us: we added this place by hand: {c.get('hand_note', '')}"]
    matched = f"matched name \"{c['matched_name']}\" ({c['name_type']} name"
    if c["language"]:
        matched += f", language {c['language']}"
    if c["is_historic"]:
        matched += ", historic"
    matched += ")"
    if c["matched_from"] == "hand":
        matched = f"matched through the name \"{c['matched_name']}\", which we added by hand (not in GeoNames)"
    country = with_name(c["country_code"], c["country_name"]) if c["country_code"] else "none"
    admin1 = with_name(c["admin1_code"], c["admin1_name"]) if c["admin1_code"] else "none"
    second = ", ".join(with_name(code, name) for code, name in zip(c["cc2"].split(","), c["cc2_names"])) \
        if c["cc2"] else "none"
    lines = [f"  Candidate {number}: GeoNames id {c['geonameid']}; main name \"{c['main_name']}\"; {matched}; "
             f"country {country}; province or state {admin1}; feature class "
             f"{with_name(c['feature_class'], c['feature_class_name'])}, feature code "
             f"{with_name(c['feature_code'], c['feature_code_name'])}; population {c['population'] or 0}; "
             f"second countries {second}"
             + (f"; spelling similarity {c['similarity']:.2f}" if "similarity" in c else "")]
    if c["matched_from"] == "hand":
        lines.append(f"  Note from us: we added this name by hand: {c.get('hand_note', '')}")
    return lines


def search_notes(summary):
    """
    Return notes on how the lookup searched for a name when it did not find
    it exactly as given (spelling, plural, direction word, as_written). These
    say how we searched, not what we think of the result.
    """
    notes = []
    if summary["matched_on"] == "as_written":
        notes.append("Note from us: found by the name as written in the record, not by the standard name.")
    if summary["plural_removed"]:
        notes.append("Note from us: found after removing the plural from the last word.")
    if summary["direction_word_removed"]:
        notes.append(f"Note from us: nothing has this exact name; found after removing the word "
                     f"\"{summary['direction_word_removed']}\" at its start.")
    if summary["match"] == "approximate":
        notes.append("Note from us: no GeoNames place has exactly this name; these have a similar spelling.")
    return notes



def place_lines(number, place, lookup, shown):
    """
    Return the lines for one place from pass 1: the place as pass 1 gave
    it, then at most MAX_CANDIDATES GeoNames candidates with the count of
    all of them (or why there are none). An affiliation has no candidates; a
    note says why. shown maps a name whose candidates are already listed to
    its place number, so a name listed again (for another role) refers back.
    """
    lines = ["", f"Place {number}: as written \"{place['as_written']}\"; name \"{place['name']}\"; "
                 f"role {ROLE_TEXT.get(place['role'], place['role'])}"]
    if place["role"] == "affiliation":
        lines.append(AFFILIATION_NOTE)
        return lines
    key = (place["name"], place["as_written"])
    if key in shown:
        lines.append(f"  GeoNames places: the same as for place {shown[key]} above.")
        return lines
    shown[key] = number
    summary, candidates = lookup["summary"], lookup["candidates"][:MAX_CANDIDATES]
    if summary["match"] == "too generic":
        lines.append("  Note from us: not looked up in GeoNames, because the name is too general on its own "
                     "(a word such as \"lake\" or \"river\", or only general and direction words).")
        return lines
    if not candidates:
        lines.append("  No GeoNames match.")
        return lines
    found = summary["places_found"]
    count = f"{len(candidates)} of {found} shown" if found > len(candidates) else f"{found} in all"
    lines.append(f"  GeoNames places with this name ({count}):")
    lines += ["  " + note for note in search_notes(summary)]
    for index, candidate in enumerate(candidates, start=1):
        lines += candidate_lines(index, candidate)
    return lines


def places_text(places, lookups):
    """
    Return the part of the prompt after the record: the places pass 1 kept,
    each with its GeoNames candidates (none for affiliations). lookups holds
    one lookup result per place, in the same order.
    """
    if not places:
        return PLACES_HEADER + "\n" + NO_PLACES
    lines = [PLACES_HEADER, PLACES_NOTE, CANDIDATES_NOTE]
    shown = {}
    for number, (place, lookup) in enumerate(zip(places, lookups), start=1):
        lines += place_lines(number, place, lookup, shown)
    return "\n".join(lines)



def evidence_not_in_places(answer, places):
    """
    Return the evidence names that are neither a name nor an as_written of
    any place pass 1 kept (ignoring case and spacing): names the model took
    from somewhere else.
    """
    def squash(text):
        """Lower-case a text and collapse its spacing, for matching."""
        return " ".join(text.lower().split())

    known = {squash(p["name"]) for p in places} | {squash(p["as_written"]) for p in places}
    return [e for e in answer["evidence"] if squash(e) not in known]



# Phrases in a reason that say the location cannot be told.
UNSURE = re.compile(
    r"\b(unknown|unclear|not clear|uncertain|ambiguous|cannot be determined|can ?not determine|"
    r"can't (?:be )?determine|impossible to (?:tell|determine)|not possible to (?:tell|determine)|"
    r"(?:does|do|did) not (?:say|state|specify|mention|indicate|name)|doesn't (?:say|state|specify|mention)|"
    r"not (?:specified|stated|mentioned|given|indicated)|no (?:information|indication|place names?|location))\b",
    re.IGNORECASE)

# Phrases that put the data outside BC.
OUTSIDE_BC = re.compile(r"\b(outside (?:of )?(?:BC|British Columbia)|not in (?:BC|British Columbia))\b",
                        re.IGNORECASE)

# Phrases that put the data in BC.
IN_BC = re.compile(r"\b(?:collected|conducted|sampled|located|surveyed|taken) (?:\w+ ){0,3}in "
                   r"(?:BC|British Columbia)\b", re.IGNORECASE)



def consistency_flags(answer):
    """
    Return short flags for an answer whose location looks inconsistent with
    its own reason, or an empty list. It only flags: the answer is never
    changed. The checks are word matches on the reason (UNSURE, OUTSIDE_BC,
    IN_BC), so they catch plain wording and miss anything said another way:

      reason_says_unsure: the reason says the location is unknown, unclear
        or not stated, but the location is not unknown;
      reason_says_outside_bc: the reason says outside BC or not in BC, but
        the location is in_bc;
      reason_says_in_bc: the reason says the data were collected (or
        conducted, sampled ...) in BC, but the location is elsewhere or
        unknown.
    """
    reason, location = answer["reason"], answer["location"]
    flags = []
    if location != "unknown" and UNSURE.search(reason):
        flags.append("reason_says_unsure")
    if location == "in_bc" and OUTSIDE_BC.search(reason):
        flags.append("reason_says_outside_bc")
    if location in ("elsewhere", "unknown") and IN_BC.search(reason) and not OUTSIDE_BC.search(reason):
        flags.append("reason_says_in_bc")
    return flags
