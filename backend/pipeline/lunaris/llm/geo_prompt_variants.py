"""
Variants of the BC filter's geography prompts (filter 3): pass 1 v3
(geo_prompt_v3.py) and pass 2 C4 (geo_pass2_prompt_c4.py), the way
prompt_variants.py makes variants of filter 2's prompt v6.

  lunaris  the prompts exactly as the Lunaris runs used them (the default):
           geo_prompt_v3.build_prompt and geo_pass2_prompt_c4.build_prompt,
           with geo_record_text.record_text
  dataone  for DataONE records (Lucia, 2026-11-23). The instructions of
           both passes are Lunaris's, with "from a Canadian research data
           catalogue" changed to "from a research data catalogue" (DataONE
           is not a Canadian catalogue); the record part is
           geo_record_text.dataone_record_text. The pass 2 box rule reads a
           west > east box only when both its readings agree
           (geo_record_text.dataone_box_shares).

Nothing else changes: the answer schemas, the Place list and its GeoNames
candidates (geo_pass2_places.places_text), the checks on each answer and
the classes code works out (geo_pass2_classes).

A run chooses its variant with geo_prompt_variant in its input folder's
input.json; a folder without it is lunaris, so every old input folder
works as before. geo_filter_run.py builds every prompt with the input's
variant and writes the variant and both instructions' fingerprints to the
run's run.json. The two prompt builders (build_pass1_prompt and
build_pass2_prompt) have no default variant: every caller names it, so a
caller that forgets fails at once instead of quietly building the Lunaris
prompt.
"""

import geo_pass2_places as places_part
import geo_pass2_prompt_c4 as pass2_prompt
import geo_prompt_v3 as pass1_prompt
from geo_record_text import dataone_box_shares, dataone_record_text
from prompt_v6 import prompt_sha1

DEFAULT_GEO_VARIANT = "lunaris"
GEO_VARIANTS = ("lunaris", "dataone")

LUNARIS_PHRASE = "from a Canadian research data catalogue"
DATAONE_PHRASE = "from a research data catalogue"


def replace_once(text, old, new):
    """Return text with old replaced by new; stop if old is not there exactly once."""
    if text.count(old) != 1:
        raise ValueError(f"expected exactly one {old!r} in the instructions, found {text.count(old)}")
    return text.replace(old, new)


# The instructions of each pass in each variant, built once when this file is loaded.
PASS1_INSTRUCTIONS = {"lunaris": pass1_prompt.INSTRUCTIONS,
                      "dataone": replace_once(pass1_prompt.INSTRUCTIONS, LUNARIS_PHRASE, DATAONE_PHRASE)}
PASS2_INSTRUCTIONS = {"lunaris": pass2_prompt.INSTRUCTIONS,
                      "dataone": replace_once(pass2_prompt.INSTRUCTIONS, LUNARIS_PHRASE, DATAONE_PHRASE)}


def check_variant(variant):
    """Return the variant if it is known; stop with a clear message otherwise."""
    if variant not in GEO_VARIANTS:
        raise ValueError(f"unknown geography prompt variant {variant!r}; known: {', '.join(GEO_VARIANTS)}")
    return variant


def variant_of_input(about):
    """Return the variant an input folder's input.json asks for (geo_prompt_variant), lunaris when missing."""
    return check_variant((about or {}).get("geo_prompt_variant", DEFAULT_GEO_VARIANT))


def pass1_instructions(variant=DEFAULT_GEO_VARIANT):
    """Return pass 1's instructions in a variant."""
    return PASS1_INSTRUCTIONS[check_variant(variant)]


def pass2_instructions(variant=DEFAULT_GEO_VARIANT):
    """Return pass 2's instructions in a variant."""
    return PASS2_INSTRUCTIONS[check_variant(variant)]


def fingerprints(variant=DEFAULT_GEO_VARIANT):
    """Return what a run records about its prompts: the variant and the sha1 of each pass's instructions."""
    return {"geo_prompt_variant": check_variant(variant),
            "pass1_instructions_sha1": prompt_sha1(pass1_instructions(variant)),
            "pass2_instructions_sha1": prompt_sha1(pass2_instructions(variant))}


def build_pass1_prompt(record, shape, variant):
    """
    Return a record's pass 1 prompt in a variant (required, no default).
    For lunaris this is exactly geo_prompt_v3.build_prompt(record, shape).
    """
    if check_variant(variant) == "lunaris":
        return pass1_prompt.build_prompt(record, shape)
    return pass1_instructions(variant) + "\n\n" + dataone_record_text(record, shape)


def build_pass2_prompt(record, shape, places, lookups, variant):
    """
    Return a record's pass 2 prompt in a variant (required, no default): the instructions, the
    record (box notes also giving the share outside BC), then the Place
    list. For lunaris this is exactly geo_pass2_prompt_c4.build_prompt.
    """
    if check_variant(variant) == "lunaris":
        return pass2_prompt.build_prompt(record, shape, places, lookups)
    return (pass2_instructions(variant) + "\n\n" + dataone_record_text(record, shape, box_outside=True) + "\n\n"
            + places_part.places_text(places, lookups))


def evidence_check(answer, prompt, variant=DEFAULT_GEO_VARIANT):
    """
    Check a pass 2 answer's evidence against the part of its prompt after
    the variant's instructions, as geo_pass2_prompt_c4.evidence_check does
    for Lunaris (which it is, for lunaris).
    """
    if check_variant(variant) == "lunaris":
        return pass2_prompt.evidence_check(answer, prompt)
    return places_part.evidence_in_text(answer, prompt[len(pass2_instructions(variant)):])


def box_shares(record, shape, variant=DEFAULT_GEO_VARIANT, lunaris_shares=None):
    """
    Return the boxes' shares in BC for the pass 2 box rule in a variant:
    lunaris_shares(record, shape) for lunaris (geo_pass2_trial.box_shares,
    passed in so this module does not import the trial script), and
    geo_record_text.dataone_box_shares for dataone.
    """
    if check_variant(variant) == "lunaris":
        return lunaris_shares(record, shape)
    return dataone_box_shares(record, shape)
