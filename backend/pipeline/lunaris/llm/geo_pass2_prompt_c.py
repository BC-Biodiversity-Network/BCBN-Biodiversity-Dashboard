"""
Pass 2 of the Lunaris BC filter's geography step: prompt version C.

Version C asks the model for facts, not a class: whether data were collected
in BC, in other parts of Canada, outside Canada, across all of Canada, or
worldwide. Code then works out the class from those facts
(geo_pass2_classes.decide), so the classes can change without changing
the prompt.

Everything after the instructions (the record text, our labelled notes and
the GeoNames candidate lines) is built by geo_record_text.py and
geo_pass2_places.py, exactly as in the earlier trial version A3, with no
summary lines. The instructions are A3's with only what the new fields need
rewritten: the answer section, the box rules (now saying what each case
means for in_bc), the examples and the answer format. The rules on
affiliations, GeoNames matches, shared names, facts and general knowledge
are A3's word for word. (A3 and the other earlier versions were moved to
backend/exploration/lunaris/bc_filter_2026_10/old_pass2_prompts/.)

The class code works out from the answer, including the box rule that may
fill in in_bc, is in geo_pass2_classes.py (decide).
"""

import geo_pass2_places as places_part
from geo_pass2_classes import CLASSES, FACTS
from geo_record_text import record_text

# Bump this when the instructions, the schema or the layout changes.
GEO_PASS2_VERSION = "C1"

# The "locations" a trial reports for this version are the classes code works out.
LOCATIONS = CLASSES

# The reason comes first, so the model writes it before its answers.
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "reason": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string"}},
        **{fact: {"type": "string", "enum": values} for fact, values in FACTS.items()},
    },
    "required": ["reason", "evidence", *FACTS],
}

# The instructions, section by section. KEEP_IN_MIND and FACTS_RULES are
# version A3's sections word for word.
KEEP_IN_MIND = """Keep in mind:
- Only where the data was collected, or the place the data is about, counts. An affiliation \
(the address of an author, university, agency, museum or repository) is not where the data \
was collected.
- The place names in step 2 were found by a language model and may contain mistakes. Check \
them against the record.
- "No GeoNames match" does not mean a place is outside BC: GeoNames does not know every place.
- Different places can share a name. When a name has several GeoNames places, use the \
record's context (the other places, the subject, the boxes and points) to decide which one \
is meant.
- A single place can lie partly in BC and partly outside, such as a river or a mountain range \
that crosses a border; GeoNames may list a second country for it. Use the context to decide \
which part the data comes from.

"""

FACTS_RULES = """Facts:
- Only say what the record and the GeoNames lists here show. Never say that GeoNames or our \
lookup shows something it does not show. If a place has no GeoNames match, say it had no match.
- You may use your own general knowledge of geography, but then the reason must say so, \
starting that part with "general knowledge:".
- Do not make up facts about the record.

"""

INTRO = """You are reading one metadata record from a Canadian research data catalogue. \
Your task is to say where the record's data were collected, in particular whether in British \
Columbia (BC), Canada.

You are given three things, in this order:
1. The record, field by field, exactly as the catalogue holds it.
2. The place names found in the record in an earlier step, each with its role.
3. For each place name, the places with that name in GeoNames, a public database of place \
names, as GeoNames lists them.
Lines that begin with "Note from us" are explanations we added. They are not part of the record.

Answer five questions about where the data were collected:
  in_bc: "yes", "no" or "unclear". Were data collected in BC, at least partly?
  other_canada: "yes" or "no". Were data collected in other Canadian provinces or territories? \
Only "yes" if the record names them or clearly says so.
  outside_canada: "yes" or "no". Were data collected outside Canada? Only "yes" if the record \
names such places or clearly says so.
  canada_wide: "yes" or "no". Does the record say the data cover all of Canada?
  global: "yes" or "no". Does the record say the data are worldwide or global?
Unclear is a good answer for in_bc: if the record does not say where the data were collected, \
answer unclear and do not guess.

"""

BOXES = """Boxes and points:
- Our note on a bounding box says how much of it lies in BC. That only tells you the box overlaps \
BC. It does not tell you where inside the box the data were collected. Decide from the title, \
abstract, keywords and place names.
- If at least half of a box lies in BC and the record names no place outside BC where the data \
were collected, in_bc is yes.
- A box that only partly touches BC is not by itself evidence for in_bc: if the record names only \
places outside BC, in_bc is no; if nothing in the record says where, in_bc is unclear.
- If the record names a place in BC where the data were collected, in_bc is yes, even when only a \
small share of the box lies in BC.

"""

EXAMPLES = """Examples, with made-up places:
- A survey of "Lake Examplia" when the only GeoNames place with that name is in BC: in_bc yes.
- Samples from "Fakeville", with Fakevilles in BC and in another country, a box wholly outside \
BC, and nothing else about BC: in_bc no, outside_canada yes.
- A dataset by researchers at the "University of Placeholder" that names no place where the data \
were collected: in_bc unclear, wherever the university is.
- Maps "across Canada": canada_wide yes.

Answer with JSON only, in this form, writing the reason first:
{"reason": "...", "evidence": ["...", "..."], "in_bc": "...", "other_canada": "...", \
"outside_canada": "...", "canada_wide": "...", "global": "..."}
reason: one short sentence. evidence: the place names from step 2 that your answers rely on, \
written as in that list (an empty list if none). The answers must agree with your reason: if your \
reason says it is unclear whether data were collected in BC, in_bc is unclear."""

INSTRUCTIONS = INTRO + KEEP_IN_MIND + BOXES + FACTS_RULES + EXAMPLES

places_text = places_part.places_text
evidence_not_in_places = places_part.evidence_not_in_places


def build_prompt(record, shape, places, lookups):
    """
    Turn one record into the text sent to the model: C's instructions, the
    record (geo_record_text), then the places with their GeoNames candidates
    (geo_pass2_places).
    """
    return INSTRUCTIONS + "\n\n" + record_text(record, shape) + "\n\n" + places_text(places, lookups)


def check_answer(answer):
    """
    Check that an answer fits the schema. Return None if it does, or a short
    description of the first problem found.
    """
    if not isinstance(answer, dict):
        return "the answer is not a JSON object"
    if sorted(answer) != sorted(RESPONSE_SCHEMA["required"]):
        return f"the answer should have exactly {RESPONSE_SCHEMA['required']}, got {sorted(answer)}"
    if not isinstance(answer["reason"], str):
        return "reason is not text"
    if not isinstance(answer["evidence"], list) or not all(isinstance(e, str) for e in answer["evidence"]):
        return "evidence is not a list of texts"
    for fact, values in FACTS.items():
        if answer[fact] not in values:
            return f"{fact} {answer[fact]!r} is not one of {values}"
    return None



def consistency_flags(answer):
    """
    Return flags for an answer whose in_bc looks inconsistent with its own
    reason, using the word checks in geo_pass2_places.consistency_flags on
    in_bc read as a location: yes as in_bc, no as elsewhere, unclear as
    unknown. It only flags; the answer is never changed. It catches plain
    wording only.
    """
    location = {"yes": "in_bc", "no": "elsewhere", "unclear": "unknown"}[answer["in_bc"]]
    return places_part.consistency_flags({"reason": answer["reason"], "location": location})
