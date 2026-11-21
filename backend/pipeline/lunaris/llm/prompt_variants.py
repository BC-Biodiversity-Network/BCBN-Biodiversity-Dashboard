"""
Variants of prompt v6: the same question with a different first sentence.

prompt_v6.py is not changed. A variant takes v6's instructions (the
written instructions plus the worked examples) and replaces one sentence;
everything else stays the same: the worked examples, the answer schema,
how a record is turned into text, PROMPT_VERSION, and the yes or no rule
applied afterwards (score_trial.py, decide()).

  v6          prompt v6 exactly (the default, and what every Lunaris run used)
  v6_dataone  for DataONE records (approved by Lucia on 2026-11-20). v6's
              first sentence says the records come from "a Canadian research
              data catalogue", which is wrong for DataONE, so it becomes
              "You are reading metadata records from a research data catalogue."

A run chooses its variant with the prompt_variant field of run.json. A run
folder without the field is v6, so every old run folder works as before.
run_batch.py on Fir rebuilds each prompt with the run's variant, and its
fingerprint check (prompt_sha1) then passes only if the Mac and Fir built
the same text.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prompt_v6 import INSTRUCTIONS, build_prompt, prompt_sha1  # noqa: E402

DEFAULT_VARIANT = "v6"

V6_FIRST_SENTENCE = "You are reading metadata records from a Canadian research data catalogue."
DATAONE_FIRST_SENTENCE = "You are reading metadata records from a research data catalogue."


def replace_once(text, old, new):
    """Return text with old replaced by new; stop if old is not there exactly once."""
    if text.count(old) != 1:
        raise ValueError(f"expected exactly one {old!r} in the v6 instructions, found {text.count(old)}")
    return text.replace(old, new)


# The instructions of each variant, built once when this file is loaded.
VARIANTS = {
    "v6": INSTRUCTIONS,
    "v6_dataone": replace_once(INSTRUCTIONS, V6_FIRST_SENTENCE, DATAONE_FIRST_SENTENCE),
}


def instructions_for(variant):
    """Return the instructions of a variant; stop with a clear message for an unknown one."""
    if variant not in VARIANTS:
        raise ValueError(f"unknown prompt variant {variant!r}; known: {', '.join(VARIANTS)}")
    return VARIANTS[variant]


def instructions_sha1(variant):
    """Return the fingerprint of a variant's instructions, as run.json records it."""
    return prompt_sha1(instructions_for(variant))


def variant_of_run(run):
    """Return the prompt variant a run asks for (run.json's prompt_variant), v6 when the field is missing."""
    variant = run.get("prompt_variant", DEFAULT_VARIANT)
    instructions_for(variant)
    return variant


def build_prompt_for(record, variant=DEFAULT_VARIANT):
    """
    Return the prompt for one record under a variant. For v6 this is
    exactly prompt_v6.build_prompt(record). For another variant, v6's
    prompt is built and its instructions, at the very start, are swapped
    for the variant's, so the record part is built by the same code.
    """
    prompt = build_prompt(record)
    if variant == DEFAULT_VARIANT:
        return prompt
    instructions = instructions_for(variant)
    if not prompt.startswith(INSTRUCTIONS):
        raise ValueError("the v6 prompt does not start with the v6 instructions")
    return instructions + prompt[len(INSTRUCTIONS):]
