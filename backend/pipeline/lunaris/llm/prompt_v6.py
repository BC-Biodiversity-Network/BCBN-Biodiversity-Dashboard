"""
The question the Lunaris LLM filter asks about each record: prompt version 6.

Everything that decides what the model is asked lives here: the written
instructions, the worked examples (in prompt_examples_v6.json next to this
file), the answer schema, the check that an answer fits that schema, and how
one record is turned into prompt text.

It is shared by two callers:

  exploration/llm/run_trial.py   the Mac run, Gemini or Ollama, one record at
                                 a time with a cache folder
  run_batch.py (this folder)     the cluster run on Fir, one batch per job

Both import from this one file, so the question cannot drift apart between the
Mac and the cluster. The text below was moved here from run_trial.py word for
word. Changing anything that ends up in the prompt means raising
PROMPT_VERSION, which also gives the answers new cache keys.

This file does not decide whether a record is biodiversity data. It only
defines the facts asked for. The yes or no decision is made in code afterwards
(score_trial.py, decide()).
"""

import hashlib
import json
import re
from pathlib import Path

import pandas as pd


# Bump this when the prompt or the schema changes. It is part of the cache key,
# so raising it makes the script call the model again instead of reusing old
# answers that were produced by a different question.
#   v4 added experimental_animals and extinct_only
#   v5 narrowed experimental_animals to lab model organisms and farmed animals
#   v6 added the worked examples and moved reason to the front
PROMPT_VERSION = 6


# What the model is allowed to put in the topic field. Keeping this to a fixed
# list means the scoring step can rely on the values instead of guessing.
TOPIC_VALUES = [
    "organisms",
    "habitat_or_ecosystem",
    "forestry_agriculture_fisheries",
    "land_and_boundaries",
    "physical_environment",
    "administrative",
    "human_health",
    "other",
]

FORM_VALUES = ["dataset", "report", "policy", "other"]

# Three way answers. A plain true or false cannot carry "this question does not
# apply to this record", and lumping that in with false makes the answer
# impossible to read afterwards.
YES_NO_NA = ["yes", "no", "not_applicable"]


# The order the fields come back in. reason is first on purpose: the model
# writes its answer from left to right, so asking for the explanation first
# means the other fields are filled in after it has said what the record is,
# and can follow from that, rather than the reason being made up afterwards to
# fit answers already given.
FIELD_ORDER = [
    "reason",
    "concerns_living_things",
    "organisms",
    "organism_level",
    "topic",
    "about_organisms_themselves",
    "ecological_purpose_stated",
    "experimental_animals",
    "extinct_only",
    "form",
    "language",
]

# The shape the answer has to come back in. The Gemini API enforces this, so
# the reply is always valid JSON with these exact fields.
#
# propertyOrdering is what makes the API return the fields in FIELD_ORDER.
# Without it the order is not guaranteed, and reason could land anywhere.
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "reason": {
            "type": "string",
            "description": "Write this first. One short sentence saying what the record mainly contains and why the answers that follow are what they are.",
        },
        "concerns_living_things": {
            "type": "boolean",
            "description": "True if the record is about living organisms or the ecosystems they live in. Physical and chemical measurements are false even if they mention an ecosystem, unless organisms are sampled or counted.",
        },
        "organisms": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Organism names the record mentions, exactly as written. Empty if none.",
        },
        "organism_level": {
            "type": "string",
            "enum": ["species", "group", "none"],
            "description": "species if a particular kind is named, group if only a broad category like fish or lichens, none if nothing.",
        },
        "topic": {
            "type": "string",
            "enum": TOPIC_VALUES,
            "description": "What the record is mainly about. Exactly one.",
        },
        "about_organisms_themselves": {
            "type": "string",
            "enum": YES_NO_NA,
            "description": "Only for forestry, agriculture or fisheries records. yes if about the organisms, including catch broken down by species, no if about tenure, volumes with no species breakdown, prices or licensing, not_applicable for every other kind of record.",
        },
        "ecological_purpose_stated": {
            "type": "string",
            "enum": YES_NO_NA,
            "description": "Only for boundaries, land use, zoning or maps. yes if an ecological or conservation purpose is stated, no if not, not_applicable for every other kind of record.",
        },
        "experimental_animals": {
            "type": "string",
            "enum": ["yes", "no"],
            "description": "yes only if the organisms are standard lab model organisms used as lab tools, or the record is an experiment run on farmed animals. no for lab experiments on wild or native species, and for everything else.",
        },
        "extinct_only": {
            "type": "string",
            "enum": ["yes", "no"],
            "description": "yes if the only organisms the record is about are extinct. no otherwise.",
        },
        "form": {"type": "string", "enum": FORM_VALUES},
        "language": {
            "type": "string",
            "description": "Main language of the record, for example english or french.",
        },
    },
    "required": FIELD_ORDER,
    "propertyOrdering": FIELD_ORDER,
}


# The written instructions. The worked examples are added to the end of these
# further down, once they have been loaded from their file.
BASE_INSTRUCTIONS = """You are reading metadata records from a Canadian research data catalogue. \
For each record, report facts about what it contains. Do not decide whether it belongs in a \
biodiversity database, that decision is made separately.

Answer only from what the record actually says. Do not use background knowledge to fill in \
things the record does not state. Records may be in English or French.

Field notes, in the order the answer gives them:

reason: write this first, before any other field. One short sentence saying what the record \
mainly contains and why. Then fill in every other field so that it agrees with the reason.

concerns_living_things: true if the record is about living organisms or the ecosystems they \
live in. Physical and chemical measurements are false, such as water quality, weather, ocean \
currents, temperature, conductivity, irradiance, bathymetry, wave height, and carbon or \
greenhouse gas fluxes. They stay false even when the record mentions an ecosystem, a forest or \
another living setting, unless the record actually samples or counts organisms.

organisms: copy the names exactly as the record writes them, scientific or common, English or \
French. If the record only says something broad like "fish" or "lichens", put that.

organism_level: species when a particular kind is named such as Cyanocitta stelleri or Steller's \
Jay. group when only a broad category is named such as birds, fish, benthic invertebrates. none \
when no living thing is named at all.

topic: pick the single category that best describes what the record is MAINLY about. Do not \
choose a category just because living things are mentioned somewhere.
  organisms: the subject is particular organisms, their populations, behaviour, diet, genetics \
or distribution.
  habitat_or_ecosystem: the subject is a place, a water body or an ecosystem, and organisms are \
part of the picture rather than the point. A study of a lake's ecology is this, not organisms. \
Vegetation cover, forest cover, tree canopy and similar maps or layers that describe living \
plants also go here, not under land_and_boundaries.
  forestry_agriculture_fisheries: the subject is growing, harvesting or managing organisms as a \
resource, including crops, timber and fish stocks.
  land_and_boundaries: park and protected area boundaries, general land use, zoning, parcel \
and cadastral data, administrative boundaries, topographic maps.
  physical_environment: physical or chemical measurements with no living subject, such as water \
temperature, bathymetry, weather, geophysical surveys.
  administrative: government records such as budgets, schedules, addresses, election boundaries.
  human_health: human medical or health records.
  other: none of the above fits.

about_organisms_themselves: answer this ONLY when topic is forestry_agriculture_fisheries. yes \
when the record is about the organisms, for example tree species composition, crop trials, fish \
populations. Catch or harvest statistics broken down by species or species group also count as \
yes, because they say which organisms were taken and how many. no when it is about tenure \
boundaries, harvest volumes or values with no breakdown by species, farm income, prices or \
licensing. For every other topic answer not_applicable.

ecological_purpose_stated: answer this ONLY when topic is land_and_boundaries. yes when the \
record states an ecological or conservation purpose. no when it is a plain administrative or \
cartographic product. For every other topic answer not_applicable.

experimental_animals: yes only in two cases. First, when the organisms are standard lab model \
organisms used as lab tools: lab mice and rats, fruit flies, C. elegans, zebrafish lab lines, \
and lab populations of E. coli or yeast. Second, when the record is an experiment run on farmed \
animals, such as a broiler chicken leg health trial. no when the experiment is on a wild or \
native species, even if it is done in a lab: cultures of wild algae, fish or invertebrates \
exposed to a drug or pollutant, and other ecotoxicology or behaviour experiments on such species \
are all no. Also no for wild organisms, field surveys, crop or plant variety trials, aquaculture \
stock and other farmed organisms that are not the subject of an animal experiment. Answer yes or \
no for every record.

extinct_only: yes when the only organisms the record is about are extinct, such as fossils or \
ancient DNA of extinct species or populations. no when any living species is part of the \
subject, or when no organisms are named. Answer yes or no for every record.

form: what the record itself is.
  dataset: data. This includes research data deposited alongside a published paper. Such a \
record usually carries the paper's own title, sometimes starting with "Data from:" or \
"Replication data for:", and it often sits in a data repository such as Dryad, Borealis or a \
Dataverse. A paper-like title does NOT make it a report. If what is being released is the data, \
it is a dataset.
  report: a written document meant to be read, such as a technical report, an assessment or a \
government report.
  policy: policy or legislative documents.
  other: anything else."""


# The worked examples live in their own file so they can be read and checked
# without digging through this code. The file name carries the prompt
# version, so a later version with different examples gets a new file and
# this one stays as a record of exactly what v6 was shown.
EXAMPLES_FILE = Path(__file__).with_name("prompt_examples_v6.json")


def check_answer(answer):
    """
    Check that an answer fits the schema. Return None if it does, or a short
    description of the first problem found.

    Gemini enforces the schema on its side, so its answers always pass. A
    small local model can still slip, even with Ollama forcing the shape, so
    every Ollama answer goes through this before it is cached. The worked
    examples are checked with it too.

    The checks are: every field is there and there are no extra ones,
    concerns_living_things is true or false, organisms is a list of text, the
    other fields are text, and every field with a fixed list of values uses one
    of them.
    """
    if not isinstance(answer, dict):
        return "the answer is not a JSON object"
    missing = [field for field in FIELD_ORDER if field not in answer]
    if missing:
        return f"missing fields: {', '.join(missing)}"
    extra = [field for field in answer if field not in FIELD_ORDER]
    if extra:
        return f"unexpected fields: {', '.join(extra)}"
    properties = RESPONSE_SCHEMA["properties"]
    for field in FIELD_ORDER:
        value = answer[field]
        kind = properties[field]["type"]
        if kind == "boolean" and not isinstance(value, bool):
            return f"{field} should be true or false, got {value!r}"
        if kind == "array" and not (isinstance(value, list)
                                    and all(isinstance(item, str) for item in value)):
            return f"{field} should be a list of text, got {value!r}"
        if kind == "string" and not isinstance(value, str):
            return f"{field} should be text, got {value!r}"
        allowed = properties[field].get("enum")
        if allowed and value not in allowed:
            return f"{field} = {value!r} is not one of {allowed}"
    return None


def load_examples(path):
    """
    Read the worked examples and check each answer is a valid answer.

    Every example answer has to have exactly the fields in FIELD_ORDER, in
    that order, with values the schema allows. An example that breaks the
    schema would teach the model to break it too, so a bad file stops the
    script here rather than being sent.
    """
    examples = json.loads(Path(path).read_text(encoding="utf-8"))
    for example in examples:
        answer = example["answer"]
        where = f"example row {example.get('row')} in {path}"
        if list(answer) != FIELD_ORDER:
            raise ValueError(f"{where}: fields must be exactly {FIELD_ORDER} in that order")
        problem = check_answer(answer)
        if problem:
            raise ValueError(f"{where}: {problem}")
    return examples


def format_examples(examples):
    """
    Turn the worked examples into text to put after the instructions.

    Each example is shown the same way a real record is, followed by the full
    answer as JSON. The heading says plainly that these are examples and not
    records to classify, so the model does not answer them again.
    """
    lines = [
        "Worked examples. These show the kind of answer expected, with every field filled in "
        "and the reason first. They are examples only and are not part of the records to classify.",
    ]
    for number, example in enumerate(examples, start=1):
        lines += ["", f"Example {number}", "Record:"]
        for label, key in [("Title", "title"), ("Subjects", "subjects"), ("Abstract", "abstract")]:
            if example.get(key):
                lines.append(f"{label}: {example[key]}")
        lines.append("Answer: " + json.dumps(example["answer"], ensure_ascii=False))
    lines += ["", "End of examples."]
    return "\n".join(lines)


INSTRUCTIONS = BASE_INSTRUCTIONS + "\n\n" + format_examples(load_examples(EXAMPLES_FILE))


def tidy_list_field(text):
    """
    Clean up a field that was saved as a list and then written into a CSV.

    When a list of keywords is written to a CSV it comes back as one string
    that still has the brackets and quotes in it, like

        ['environnement' 'Une ville verte']

    Sending that to the model as is would mean the brackets and quotes are
    part of the question. This pulls the pieces back out and joins them with
    commas.
    """
    text = text.strip()
    if not (text.startswith("[") and text.endswith("]")):
        return text
    inside = text[1:-1]
    pieces = re.findall(r"'([^']*)'|\"([^\"]*)\"", inside)
    values = [a or b for a, b in pieces if (a or b).strip()]
    if not values:
        return inside.strip()
    return ", ".join(values)


def build_prompt(record):
    """
    Turn one record into the text sent to the model.

    Only three fields go in: the title, the subject keywords and the abstract.
    Nothing about what the keyword filter decided is included, so the model
    cannot be influenced by it.

    The record is headed "Record to classify" rather than plain "Record", so
    it cannot be mistaken for one more of the worked examples above it.
    """
    parts = [INSTRUCTIONS, "", "Record to classify:", ""]
    for label, column in [("Title", "title"), ("Subjects", "subjects"), ("Abstract", "abstract")]:
        value = record.get(column)
        if value is None or (isinstance(value, float) and pd.isna(value)):
            continue
        text = str(value).strip()
        if not text or text.lower() == "nan":
            continue
        if column == "subjects":
            text = tidy_list_field(text)
        parts.append(f"{label}: {text}")
    return "\n".join(parts)


def cache_key(record_id, model, prompt_version):
    """
    Build the file name for one cached answer.

    The model name and the prompt version are part of it, so answers from
    different models, or from an older version of the question, never get
    mistaken for each other.
    """
    raw = f"{prompt_version}|{model}|{record_id}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20] + ".json"


def prompt_sha1(prompt):
    """
    Return a short fingerprint of one prompt's exact text.

    Two prompts with the same fingerprint are the same down to the last
    character. The batch files made on the Mac carry the fingerprint of every
    record's prompt, and the cluster run checks it before asking the model, so
    a difference in how the text was built on the cluster (another pandas
    version, say) is caught instead of quietly changing the question.
    """
    return hashlib.sha1(prompt.encode("utf-8")).hexdigest()


def answer_to_row(record_id, model, cached):
    """
    Turn one stored answer into a row of the results CSV.

    cached is what was saved for the record: the answer itself under "answer"
    plus the token counts. model is what goes in the model column. The Mac run
    and the cluster merge both build their rows here, so the two CSVs have
    exactly the same columns in the same order, which is what score_trial.py
    expects.
    """
    answer = cached["answer"]
    return {
        "record_id": record_id,
        "model": model,
        "concerns_living_things": answer["concerns_living_things"],
        "organisms": "; ".join(answer["organisms"]),
        "organism_level": answer["organism_level"],
        "topic": answer["topic"],
        "about_organisms_themselves": answer["about_organisms_themselves"],
        "ecological_purpose_stated": answer["ecological_purpose_stated"],
        "experimental_animals": answer["experimental_animals"],
        "extinct_only": answer["extinct_only"],
        "form": answer["form"],
        "language": answer["language"],
        "model_reason": answer["reason"],
        "input_tokens": cached.get("input_tokens"),
        "output_tokens": cached.get("output_tokens"),
        "thinking_tokens": cached.get("thinking_tokens"),
    }


def error_row(record_id, error):
    """Return the results CSV row for a record that has no valid answer."""
    return {"record_id": record_id, "model_error": error}
