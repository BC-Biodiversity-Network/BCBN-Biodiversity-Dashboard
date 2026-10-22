"""
Pass 1 of the Lunaris BC filter's geography step: geography prompt version 3.

Like versions 1 and 2, pass 1 only finds the place names in a record and says
what role each one plays; it does not decide where the data is from. The
record is shown exactly as before (geo_record_text.record_text).

Version 2 showed that more rules made the 4B model worse: it guessed the
region and country it was told not to guess, and the long "not a place" list
made it drop real places, even whole keyword lists. Version 3 is simpler:

  - Each place has only as_written, name and role. within_region and
    within_country are gone; pass 2 sees the full record anyway.
  - Every example in the instructions is a made-up place ("Lake Examplia",
    "Fakeville"), so an example that leaks into an answer cannot match a
    real place. No real province, state or country is named in an example.
  - The "not a place" guidance is two sentences, plus one saying that a
    keyword which is itself a place name always counts.
  - The BC reading rule is kept (nothing leaked from it in version 2).

After each answer our code (clean_places) drops any place whose as_written
is not in the record's own text, and removes duplicates. The trial script
also lists any name that needs a person's look (geo_answer_checks).
"""

from geo_answer_checks import drop_duplicates, written_in_record
from geo_record_text import record_text

# Bump this when the instructions, the schema or the record layout changes.
GEO_PROMPT_VERSION = 3

ROLES = ["study_location", "affiliation", "indigenous_nation_or_territory", "other"]

# The answer: a list of places, each with these fields, in this order.
PLACE_FIELDS = ["as_written", "name", "role"]
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "places": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "as_written": {"type": "string"},
                    "name": {"type": "string"},
                    "role": {"type": "string", "enum": ROLES},
                },
                "required": PLACE_FIELDS,
            },
        },
    },
    "required": ["places"],
}


# Every place in the examples below is made up, so that if one is copied into
# an answer it cannot match a real place. Only the BC reading rule names real
# places, because the records themselves use those forms.
INSTRUCTIONS = """You are reading one metadata record from a Canadian research data catalogue. \
Your only task is to list every place name written in the record and say what role each one \
plays. Do not decide where the data comes from; that is decided later, by other means.

The record is shown field by field. Lines that begin with "Note from us:" are explanations we \
added, not part of the record: never take a place name from them, and never turn coordinates \
(numbers) into place names.

Look for place names in the title, the keywords, the abstract, the place-name field and the \
publisher: countries, provinces, states, regions, cities, towns, parks, lakes, rivers, seas, \
bays, islands, mountains and other named geographic features. A keyword that is itself a place \
name, such as "EXAMPLIA", "North Placeholder Ocean" or "Lac-des-Exemples", always counts and \
must be listed.

Names of species, ships, satellites, programmes, projects, models, organisations and agencies \
are not places, even when they contain a place word. But a place inside an address or an \
institution's name is a place: "University of Fakeville" gives Fakeville, as an affiliation.

For each place name, give:
  as_written: the name exactly as it appears in the record, with the same spelling and capitals.
  name: the place itself in its usual standard form, with nothing added after it. Spell out \
abbreviations ("L. Examplia" becomes "Lake Examplia", "Mt. Placeholder" becomes "Mount \
Placeholder") and give the English name for a name in another language ("Rivière Imaginaire" \
becomes "Imaginaire River"). Keep the place itself: do not swap it for a bigger or different place.
  role: one of
    study_location: where the data was collected, or the place the data is about.
    affiliation: the address of an author, institution, university, agency or repository, or a \
place inside an institution's name.
    indigenous_nation_or_territory: an Indigenous nation, people or territory, such as "the \
Placeholder First Nation".
    other: any other mention, such as a passing reference, a meeting place or a comparison.

Reading the record:
- "BC", "B.C.", "C.-B." and "Colombie-Britannique" mean British Columbia, Canada.
- "Vancouver, Washington" and "Vancouver, WA" are the city in Washington State, USA, a different \
place from the Canadian city of the same name.
- List each place once for each role it plays.
- If the record names no places, return an empty list. Do not invent places.

Answer with JSON only, in this form:
{"places": [{"as_written": "...", "name": "...", "role": "..."}]}"""


def check_answer(answer):
    """
    Check that an answer fits the schema. Return None if it does, or a short
    description of the first problem found.

    The answer must be an object with exactly one field, places, holding a
    list; each item must have exactly as_written, name and role, all text,
    with role one of ROLES.
    """
    if not isinstance(answer, dict):
        return "the answer is not a JSON object"
    if list(answer) != ["places"]:
        return f"the answer should have only a places field, got {list(answer)}"
    if not isinstance(answer["places"], list):
        return "places is not a list"
    for number, place in enumerate(answer["places"], start=1):
        if not isinstance(place, dict) or sorted(place) != sorted(PLACE_FIELDS):
            return f"place {number} should have exactly {PLACE_FIELDS}, got {place!r}"
        if not all(isinstance(place[f], str) for f in PLACE_FIELDS):
            return f"place {number}: every field should be text"
        if place["role"] not in ROLES:
            return f"place {number}: role {place['role']!r} is not one of {ROLES}"
    return None


def clean_places(places, prompt):
    """
    Return (kept, dropped) for an answer's places. A place is dropped if its
    as_written is not found in the record's own text (the prompt without our
    notes); the rest are kept once per name and role, in the order given.
    """
    dropped = [p for p in places if not written_in_record(p["as_written"], prompt)]
    found = [p for p in places if written_in_record(p["as_written"], prompt)]
    return drop_duplicates(found, lambda p: p["name"]), dropped


def build_prompt(record, shape):
    """
    Turn one record into the text sent to the model: the instructions, then
    the record as geo_record_text.record_text lays it out.
    """
    return INSTRUCTIONS + "\n\n" + record_text(record, shape)
