"""
Pass 2 of the Lunaris BC filter's geography step: prompt version C4, the
version the filter uses.

C4 asks the model for facts, not a class: whether data were collected in BC,
in other parts of Canada, outside Canada, across all of Canada, or
worldwide. Code then works out the class from those facts, first applying
the box rule that may read an "unclear" in_bc as "yes"
(geo_pass2_classes.decide), so the classes can change without changing the
prompt.

The prompt is the instructions below, then the record
(geo_record_text.record_text, with box notes that also give the share of
each box outside BC), then the Place list with its GeoNames candidates
(geo_pass2_places.places_text).

C4 began as version C3, a minimal prompt, with four changes agreed after
C3's trial: item 2 of the intro names "the Place list"; evidence is short
phrases copied exactly from the record, the Place list or our notes, and
code checks them against the prompt (evidence_check); the sentence "If your
reason says it is unclear whether data were collected in BC, in_bc is
unclear."; and the box note's outside share. The earlier versions (A to C3)
are in backend/exploration/lunaris/bc_filter_2026_10/old_pass2_prompts/.
"""

import geo_pass2_places as places_part
from geo_pass2_classes import CLASSES, FACTS
from geo_record_text import record_text

# Bump this when the instructions, the schema or the layout changes.
GEO_PASS2_VERSION = "C4"

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

INSTRUCTIONS = """You are reading one metadata record from a Canadian research data catalogue. Say where its data were collected, especially whether in British Columbia (BC), Canada.

You are given:
1. The record, exactly as the catalogue holds it.
2. The Place list: the place names in the record, each with a role (study location, affiliation, Indigenous nation or territory, or other mention). Another language model made this list, so it may contain mistakes; check it against the record.
3. For each place name, the GeoNames entries with that name. GeoNames is a public database of place names.
Lines starting with "Note from us" are our explanations, not part of the record.

Answer five questions:
  in_bc: "yes", "no" or "unclear". Were any of the data collected in BC?
  other_canada: "yes" or "no". Were any data collected in Canadian provinces or territories other than BC?
  outside_canada: "yes" or "no". Were any data collected outside Canada?
  canada_wide: "yes" or "no". Does the record say the data cover all of Canada?
  global: "yes" or "no". Does the record say the data are worldwide or global?
If the record does not say where the data were collected, answer in_bc "unclear". Do not guess.

Keep in mind:
- Only where the data were collected, or the place the data describe, counts. The address of an author or organisation is not where the data were collected.
- No GeoNames match does not mean a place is outside BC.
- When several places share a name, use the record's context to decide which one is meant.
- Our note on a bounding box says how much of it lies in BC. That only shows the box overlaps BC, not where in the box the data were collected. Decide from the text.
- Do not make up facts.

Answer with JSON only, writing the reason first:
{"reason": "...", "evidence": ["...", "..."], "in_bc": "...", "other_canada": "...", "outside_canada": "...", "canada_wide": "...", "global": "..."}
reason: one short sentence. evidence: short phrases your answers rely on, copied exactly from the record, the Place list or our notes (an empty list if none). Your answers must agree with your reason. If your reason says it is unclear whether data were collected in BC, in_bc is unclear."""

places_text = places_part.places_text


def build_prompt(record, shape, places, lookups):
    """
    Turn one record into the text sent to the model: C4's instructions, the
    record with box notes that also give the share outside BC, then the
    Place list with its GeoNames candidates.
    """
    return (INSTRUCTIONS + "\n\n" + record_text(record, shape, box_outside=True) + "\n\n"
            + places_text(places, lookups))


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


def evidence_check(answer, prompt):
    """
    Check the answer's evidence against the prompt it was given: each item
    must appear in the part after the instructions (geo_pass2_places
    .evidence_in_text). It only checks; the answer is never changed.
    """
    return places_part.evidence_in_text(answer, prompt[len(INSTRUCTIONS):])
