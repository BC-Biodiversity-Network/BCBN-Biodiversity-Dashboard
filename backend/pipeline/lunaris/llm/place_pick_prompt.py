"""
The prompt of the new place lookup's model step (version P1), planned
2026-11-03: for each place name the rules left to the model
(../place_lookup_rules.py), which listed GeoNames place the record means,
or that none should be used and why, with a quote copied unchanged from
the record.

The model only says which place is meant. Whether that place can be drawn
on the map (not a large area, and the rules for rivers and lakes) is
decided afterwards by code, so those rules can change without changing the
prompt.

A prompt is the instructions below, then the record exactly as the
catalogue holds it (title, description, keywords, its own place list), the
catalogue and our earlier reading of where the data are, then the place
names to decide, each with its numbered GeoNames places. A record with many
names is split into prompts of at most MAX_NAMES names each, every prompt
with the whole record.

Each record is asked twice: once with every name's places in the lookup's
own order and once in a shuffled order (the same shuffle every time, from
the record id and the name), so an answer that only follows the order of
the list shows up as a disagreement. Only places both answers agree on can
be drawn.
"""

import hashlib
import random

# Bump this when the instructions, the schema or the layout changes.
PLACE_PICK_VERSION = "P1"

# The orders each record is asked in.
ORDERS = ("lookup", "shuffled")

# At most this many place names per prompt; a record with more is split.
MAX_NAMES = 8

# Why no place is used, as the model may answer.
NONE_REASONS = ("not_a_place", "not_a_study_place", "too_broad", "not_in_list", "ambiguous")

# The longest quote accepted, in characters (the instructions ask for 300).
LONGEST_QUOTE = 400

# Our earlier reading of where a record's data are (filter 3's class), in words.
CLASS_WORDS = {
    "bc_only": "only in BC",
    "canada_incl_bc": "in BC and in other parts of Canada",
    "bc_and_outside_canada": "in BC and outside Canada",
    "all_canada": "across all of Canada",
    "global": "worldwide",
}

# The reason comes first, so the model writes it before its answer.
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "places": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "place": {"type": "string"},
                    "reason": {"type": "string"},
                    "quote": {"type": "string"},
                    "candidate": {"type": "integer"},
                    "none_because": {"type": "string", "enum": ["", *NONE_REASONS]},
                },
                "required": ["place", "reason", "quote", "candidate", "none_because"],
            },
        },
    },
    "required": ["places"],
}

INSTRUCTIONS = """You are helping to put research datasets on a map of British Columbia (BC), Canada. Below is one dataset's record from a data catalogue and some place names found in it. For each place name, decide which of its listed GeoNames places the record means, or that none of them should be used.

You are given:
1. The record, exactly as the catalogue holds it: its title, description, keywords and its own list of places.
2. The catalogue it comes from, and our earlier reading of where its data are. Our reading may be wrong.
3. The place names to decide, labelled P1, P2 and so on. Another language model found them in the record, so a name may be misread or may not be a place at all. Each has a numbered list of places from GeoNames, a public database of place names, with the kind of place, the province or state, the country, and the latitude and longitude.
Lines starting with "Note from us" are our explanations, not part of the record.

For each place name, answer:
  place: its label, such as "P1".
  reason: one short sentence.
  quote: the words of the record that show which place it means, or why none is used, copied exactly (at most 300 characters). Do not change spelling, punctuation or capitals.
  candidate: the number of the listed place the record means, or 0 if none should be used.
  none_because: "" when candidate is a number; when candidate is 0, one of:
    "not_a_place": the name is not a place (for example a species, a habitat, an organisation or a person).
    "not_a_study_place": the record names the place for another reason than where its data were collected or what they describe (for example an author's address, a funder, a comparison, the end of a range, or a reference value).
    "too_broad": the name means a whole region, coast, ocean, mountain range or another large area, not one place.
    "not_in_list": the place the record means is not one of the listed places.
    "ambiguous": the record does not make clear which of the listed places it means.

Keep in mind:
- Many places share a name. Choose a place only if the record makes clear it means that one: for example it names the province or state, a nearby place, a park, a lake or river system, or a study area that only one of the listed places fits. Do not choose a place only because it is in BC or because it comes first in the list.
- Choose a place only if the data were collected there or describe it.
- A wrong place on the map is worse than no place. When in doubt, answer 0 with "ambiguous".
- Do not make up facts.

Answer with JSON only, one entry for each place name, in order:
{"places": [{"place": "P1", "reason": "...", "quote": "...", "candidate": 2, "none_because": ""}]}"""


def record_text(item):
    """Return the record part of a prompt: the record as the catalogue holds it, then the catalogue and our reading."""
    lines = ["Record", f"Title: {item['title'] or ''}", f"Description: {item['abstract'] or ''}",
             f"Keywords: {'; '.join(item['keywords']) if item['keywords'] else '(none)'}",
             f"Places in the record: {' | '.join(item['place_list']) if item['place_list'] else '(none)'}", "",
             f"Catalogue: {item['catalogue']}"]
    reading = CLASS_WORDS.get(item["final_class"])
    if reading:
        lines.append(f"Note from us: our earlier reading is that the data were collected {reading}. "
                     f"It may be wrong.")
    others = [entry["name"] for entry in item["names"]
              if entry["decision"] != "model" and "affiliation" not in entry["roles"]]
    if others:
        lines.append(f"Note from us: other place names found in the record: {'; '.join(others)}.")
    return "\n".join(lines)


def candidate_order(item, entry, order):
    """
    Return a name's candidates in the order asked: the lookup's own
    ("lookup"), or shuffled by a seed made from the record id and the name
    ("shuffled"), so the same prompt is built every time.
    """
    candidates = list(entry["candidates"])
    if order == "shuffled":
        seed = int(hashlib.sha1(f"{item['dataset_id']}|{entry['name']}".encode("utf-8")).hexdigest()[:16], 16)
        random.Random(seed).shuffle(candidates)
    return candidates


def candidate_line(number, candidate):
    """Return one numbered GeoNames place: its name (and the name it matched on), kind, where, and coordinates."""
    name = candidate["name"]
    if candidate.get("matched_name") and candidate["matched_name"].lower() != name.lower():
        name += f" (also called {candidate['matched_name']})"
    where = ", ".join(part for part in (candidate["admin1"], candidate["country"]) if part)
    text = f"  {number}. {name}, {candidate['feature'] or 'place'}, {where or 'no country'}"
    if candidate["latitude"] is not None and candidate["longitude"] is not None:
        text += f" ({candidate['latitude']:.4f}, {candidate['longitude']:.4f})"
    if candidate.get("population"):
        text += f", population {candidate['population']:,}"
    return text


def name_text(label, entry, candidates):
    """Return one place name to decide: its label, the name and how the record writes it, its places and our notes."""
    lines = [f"{label}: {entry['name']} (written in the record as \"{entry['as_written']}\")"]
    if entry.get("match") == "contains":
        lines.append("Note from us: GeoNames has no place with exactly this name. These places have its words "
                     "in their own name.")
    elif entry.get("direction_word_removed"):
        lines.append(f"Note from us: GeoNames has no place with exactly this name. These places are named "
                     f"without \"{entry['direction_word_removed']}\".")
    elif entry.get("plural_removed"):
        lines.append("Note from us: GeoNames has no place with exactly this name. These places have the name "
                     "without its final s.")
    lines += [candidate_line(number, candidate) for number, candidate in enumerate(candidates, start=1)]
    omitted = entry.get("omitted_by_country") or {}
    if omitted:
        counts = ", ".join(f"{count:,} with country code {code}" for code, count in omitted.items())
        lines.append(f"Note from us: {sum(omitted.values()):,} more places with this name are not listed ({counts}). "
                     f"If the record means one of them, answer not_in_list.")
    return "\n".join(lines)


def model_names(item):
    """Return the indexes, in item["names"], of the names left to the model."""
    return [i for i, entry in enumerate(item["names"]) if entry["decision"] == "model"]


def chunks(indexes):
    """Return the names' indexes in groups of at most MAX_NAMES, in order."""
    return [indexes[start:start + MAX_NAMES] for start in range(0, len(indexes), MAX_NAMES)]


def build_prompt(item, indexes, order):
    """
    Return (text, candidate_ids) for one prompt: the instructions, the
    record, and the names at indexes, labelled P1, P2 ... in order, each with
    its places in the order asked. candidate_ids gives, for each name, the
    GeoNames id of each numbered place, so an answer can be read back.
    """
    blocks, candidate_ids = [], []
    for position, index in enumerate(indexes, start=1):
        entry = item["names"][index]
        candidates = candidate_order(item, entry, order)
        blocks.append(name_text(f"P{position}", entry, candidates))
        candidate_ids.append([candidate["geonameid"] for candidate in candidates])
    text = (INSTRUCTIONS + "\n\n" + record_text(item) + "\n\nPlace names to decide\n\n" + "\n\n".join(blocks))
    return text, candidate_ids


def units(item):
    """
    Return every prompt one record needs: for each order and each group of
    names, {order, chunk, indexes}. The prompts themselves are built with
    build_prompt when asked.
    """
    return [{"order": order, "chunk": chunk, "indexes": indexes}
            for order in ORDERS for chunk, indexes in enumerate(chunks(model_names(item)))]


def check_answer(answer, candidate_ids):
    """
    Check that an answer fits the schema and this prompt: one entry per
    place name, labelled P1, P2 ... in order; a candidate number in the list
    or 0; none_because given exactly when the candidate is 0; a quote that is
    text of at most LONGEST_QUOTE characters. Return None, or the first
    problem found.
    """
    if not isinstance(answer, dict) or sorted(answer) != ["places"] or not isinstance(answer["places"], list):
        return "the answer is not {\"places\": [...]}"
    places = answer["places"]
    if len(places) != len(candidate_ids):
        return f"{len(places)} entries for {len(candidate_ids)} place names"
    for position, (entry, ids) in enumerate(zip(places, candidate_ids), start=1):
        if not isinstance(entry, dict) or sorted(entry) != sorted(RESPONSE_SCHEMA["properties"]["places"]["items"]
                                                                  ["required"]):
            return f"entry {position} does not have exactly the five fields"
        if entry["place"] != f"P{position}":
            return f"entry {position} is labelled {entry['place']!r}, not 'P{position}'"
        if not isinstance(entry["candidate"], int) or not 0 <= entry["candidate"] <= len(ids):
            return f"P{position}: candidate {entry['candidate']!r} is not 0 to {len(ids)}"
        if (entry["candidate"] == 0) != (entry["none_because"] in NONE_REASONS):
            return f"P{position}: candidate {entry['candidate']} with none_because {entry['none_because']!r}"
        if not isinstance(entry["quote"], str) or not isinstance(entry["reason"], str):
            return f"P{position}: quote or reason is not text"
        if len(entry["quote"]) > LONGEST_QUOTE:
            return f"P{position}: the quote is {len(entry['quote'])} characters, over {LONGEST_QUOTE}"
    return None


def read_answer(item, unit, answer, candidate_ids):
    """
    Return one decision per name of a checked answer: the record's id, the
    name, the order asked, the GeoNames id chosen (None for 0; and the
    candidate's place in the list), none_because, the quote and the reason.
    """
    decisions = []
    for index, entry, ids in zip(unit["indexes"], answer["places"], candidate_ids):
        chosen = entry["candidate"]
        decisions.append({"dataset_id": item["dataset_id"], "name": item["names"][index]["name"],
                          "order": unit["order"], "candidate_number": chosen,
                          "geonameid": ids[chosen - 1] if chosen else None,
                          "none_because": entry["none_because"] or None, "quote": entry["quote"],
                          "reason": entry["reason"]})
    return decisions
