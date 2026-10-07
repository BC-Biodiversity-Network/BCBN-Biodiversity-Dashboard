"""
Ask a model about each record in the test set, and save what it says.

This script does NOT decide whether a record is biodiversity data. It only
collects facts about each record. The yes or no decision is worked out
afterwards, in code, by score_trial.py. The reason for splitting it that way:
the criteria are still being confirmed with Evan, and if the rules are baked
into the prompt then every rule change means paying to run the whole thing
again. Facts do not change when the rules do.

Prompt version 6 adds two things. First, a short set of worked examples, kept
in prompt_examples_v6.json next to this script, shows the model a filled in
answer for ten records of the kinds it got wrong before. Those records come
only from the rows nobody reviewed by hand, never from the 259 the scores are
measured on, so the answers being scored are not handed to the model. Second,
the "reason" field now comes first, so the model says what the record is
before it fills in the other fields, and the other fields follow from it.

Every answer is written to its own small file in a cache folder. If the run
stops half way, or the network drops, or you want to add more records later,
just run it again. Records already in the cache are skipped and cost nothing.

Usage:

    # See a prompt without calling the API or needing a key
    python exploration/llm/run_trial.py --dry-run --limit 2

    # Smoke test, 20 records
    python exploration/llm/run_trial.py --model gemini-3.5-flash-lite --limit 20

    # The full test set
    python exploration/llm/run_trial.py --model gemini-3.5-flash-lite

Running an open-source model through Ollama instead of Gemini:

The same prompt, schema and cache work with a model served by Ollama, on this
Mac or on a GPU node of the Digital Research Alliance cluster. Start the
server first (the Ollama app, or "ollama serve"), pull the model once with
"ollama pull qwen3.5:0.8b", then:

    # Smoke test, 5 records, on the local server
    python exploration/llm/run_trial.py --backend ollama --model qwen3.5:0.8b --limit 5

    # A server somewhere else, for example on a cluster node
    python exploration/llm/run_trial.py --backend ollama --model qwen3.5:0.8b \
        --ollama-url http://localhost:11434

Ollama has no quota and no per-minute limit, so there is no pacing. Every
answer is checked against the schema before it is cached, because a small
local model is more likely than Gemini to return something malformed. The
summary at the end prints the average seconds per record, which is what is
needed to estimate how long a full run takes on the cluster.

Unless --out is given, the results go to trial_<model>_v<prompt version>.csv,
with any character that is awkward in a file name, such as ":" or "/", turned
into "-". qwen3.5:0.8b on prompt v6 becomes trial_qwen3.5-0.8b_v6.csv.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
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
# without digging through this script. The file name carries the prompt
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


def load_records(path, limit, only_ids):
    """Read the test set and return the rows this run should work on."""
    frame = pd.read_csv(path, encoding="utf-8-sig")
    if only_ids:
        wanted = set(only_ids)
        frame = frame[frame["record_id"].astype(str).isin(wanted)]
    if limit:
        frame = frame.head(limit)
    return frame


class DailyQuotaReached(Exception):
    """
    Raised when the free tier's daily request allowance has run out.

    This is different from going too fast. Going too fast is fixed by waiting a
    few seconds. Running out for the day is only fixed by waiting until
    tomorrow, so there is no point retrying and the run should stop.
    """


def looks_like_daily_quota(error):
    """
    Work out whether a rate limit error is the daily one or the per minute one.

    Google sends back the same 429 code for both, so the only way to tell them
    apart is the wording of the message. The daily one names a per-day quota.
    """
    text = str(error).lower()
    if "429" not in text and "resource_exhausted" not in text:
        return False
    return "perday" in text.replace(" ", "") or "per day" in text or "daily" in text


class Pacer:
    """
    Keep the calls slow enough to stay inside the requests per minute limit.

    The free tier allows 15 requests a minute, which is one every four seconds.
    Firing them off as fast as possible would just collect rejections, so this
    waits before each call so that the gap since the previous one is long
    enough.
    """

    def __init__(self, requests_per_minute):
        """Store the pace, as the smallest gap allowed between two calls."""
        self.min_gap = 60.0 / requests_per_minute if requests_per_minute > 0 else 0
        self.last_call = 0.0

    def wait(self):
        """Sleep if the previous call was too recent."""
        if not self.min_gap:
            return
        gap = time.monotonic() - self.last_call
        if gap < self.min_gap:
            time.sleep(self.min_gap - gap)
        self.last_call = time.monotonic()


def ask_model(client, model, prompt):
    """
    Send one prompt and return the parsed answer plus the token counts.

    Retries a few times on failure, waiting longer each time, because going
    slightly too fast and brief network problems are both normal and not worth
    losing a run over. Running out of the daily allowance is not retried, it
    stops the run instead.
    """
    from google.genai import errors as genai_errors

    last_error = None
    for attempt in range(5):
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "response_json_schema": RESPONSE_SCHEMA,
                    "temperature": 0,
                },
            )
            usage = getattr(response, "usage_metadata", None)
            return {
                "answer": json.loads(response.text),
                "input_tokens": getattr(usage, "prompt_token_count", None),
                "output_tokens": getattr(usage, "candidates_token_count", None),
                # Some models charge for internal reasoning as output. Recording
                # it separately shows whether that is happening here.
                "thinking_tokens": getattr(usage, "thoughts_token_count", None),
            }
        except genai_errors.APIError as error:
            if looks_like_daily_quota(error):
                raise DailyQuotaReached(str(error))
            last_error = error
            wait = 2 ** attempt
            print(f"    call failed ({error}), waiting {wait}s")
            time.sleep(wait)
        except json.JSONDecodeError as error:
            # The schema should prevent this, but if the reply is not valid
            # JSON there is no point retrying, the record is just skipped.
            return {"error": f"reply was not valid JSON: {error}"}
    return {"error": f"gave up after 5 attempts: {last_error}"}


# How much text Ollama is allowed to hold for one request, in tokens: the
# prompt plus the answer. Ollama's own default is small and when a prompt is
# longer it cuts it without any error, so the model would be answering about
# half a record. The v6 instructions are about 3,500 tokens, and the longest
# records in the test set add about 7,000 more, so 8,192 is not enough for
# them. 16,384 leaves room for those and for the answer.
OLLAMA_NUM_CTX = 16384

# How long to wait for one answer before giving up on that call, in seconds.
# A small model on a laptop answers in a few seconds. A large model on a busy
# node can take much longer, so this is generous, and it can be raised with
# --ollama-timeout.
OLLAMA_TIMEOUT = 300


class OllamaUnreachable(Exception):
    """
    Raised when nothing answers at the Ollama address.

    That is not something a retry fixes. The server is not started, or the
    address is wrong, so the run stops with a message saying so instead of
    trying every record and failing each one.
    """


def plain_json_schema(schema):
    """
    Return a copy of the answer schema in plain JSON Schema, for Ollama.

    RESPONSE_SCHEMA is written for Gemini. It can carry keys only Gemini
    understands, such as propertyOrdering, and Gemini also accepts type names
    in capitals such as "STRING". Ollama wants standard JSON Schema, so those
    keys are dropped and type names are put in lower case. Everything else is
    kept as it is, including the order of the properties, so "reason" stays
    first. Ollama fills the fields in the order they appear here.
    """
    if isinstance(schema, dict):
        plain = {}
        for key, value in schema.items():
            if key == "propertyOrdering":
                continue
            if key == "type" and isinstance(value, str):
                plain[key] = value.lower()
            else:
                plain[key] = plain_json_schema(value)
        return plain
    if isinstance(schema, list):
        return [plain_json_schema(item) for item in schema]
    return schema


def ask_ollama(url, model, prompt, num_ctx=OLLAMA_NUM_CTX, timeout=OLLAMA_TIMEOUT):
    """
    Send one prompt to an Ollama server and return the answer and token counts.

    This plays the same part as ask_model does for Gemini and returns the same
    shape, so the rest of the script does not care which one answered. Another
    local server, such as vLLM, would be added as one more small function like
    this one.

    It uses Ollama's own chat endpoint, /api/chat, and asks for:
      the same prompt text Gemini gets, as a single user message
      format: the answer schema, so Ollama forces the reply into that shape
      think: false, so a model with a thinking mode answers straight away
          instead of first writing out long reasoning that is not wanted
      temperature 0, the same as for Gemini
      num_ctx: room for the whole prompt, see OLLAMA_NUM_CTX

    A call that times out or fails is tried once more. If the server cannot be
    reached at all, OllamaUnreachable is raised to stop the run. If the prompt
    filled the whole context, Ollama may have cut it, so the answer is
    returned as an error rather than trusted.
    """
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "think": False,
        "format": plain_json_schema(RESPONSE_SCHEMA),
        "options": {"temperature": 0, "num_ctx": num_ctx},
    }
    request = urllib.request.Request(
        url.rstrip("/") + "/api/chat",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )

    last_error = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                reply = json.loads(response.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as error:
            # The server is there but refused the request. A missing model is
            # the common case, and retrying will not pull it.
            detail = error.read().decode("utf-8", errors="replace")[:200]
            return {"error": f"Ollama said {error.code}: {detail}"}
        except urllib.error.URLError as error:
            if isinstance(error.reason, (ConnectionRefusedError, FileNotFoundError)):
                raise OllamaUnreachable(str(error.reason))
            last_error = error
        except (TimeoutError, OSError) as error:
            last_error = error
        if attempt == 0:
            print(f"    call failed ({last_error}), trying once more")
    else:
        return {"error": f"gave up after 2 attempts: {last_error}"}

    input_tokens = reply.get("prompt_eval_count")
    if input_tokens and input_tokens >= num_ctx - 16:
        return {"error": f"the prompt filled the whole context ({input_tokens} of "
                         f"{num_ctx} tokens) and may have been cut, raise --num-ctx"}
    content = reply.get("message", {}).get("content", "")
    try:
        answer = json.loads(content)
    except json.JSONDecodeError as error:
        return {"error": f"reply was not valid JSON: {error}"}
    return {
        "answer": answer,
        "input_tokens": input_tokens,
        "output_tokens": reply.get("eval_count"),
        # Ollama does not count reasoning separately. With think set to false
        # there should be none, so this is recorded as 0.
        "thinking_tokens": 0,
    }


def safe_file_name(text):
    """
    Turn a model name into something safe to put in a file name.

    Model names like qwen3.5:0.8b or a name with a slash in it would break a
    file name or put the file in another folder. Anything other than letters,
    digits, dots, dashes and underscores becomes a dash.
    """
    return re.sub(r"[^A-Za-z0-9._-]", "-", text)


def main():
    """Run the model over the test set, caching every answer to disk."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", default="exploration/llm/test_set_labelled_fixed.csv",
                        help="CSV holding the records to ask about")
    parser.add_argument("--backend", choices=["gemini", "ollama"], default="gemini",
                        help="Where the model runs. gemini is Google's API. ollama is an "
                             "Ollama server, on this machine or on a cluster node.")
    parser.add_argument("--model", default="gemini-3.1-flash-lite",
                        help="Model name. For gemini, check AI Studio for what is currently "
                             "available. For ollama, the name as pulled, for example qwen3.5:0.8b.")
    parser.add_argument("--ollama-url", default="http://localhost:11434",
                        help="Address of the Ollama server. Only used with --backend ollama.")
    parser.add_argument("--num-ctx", type=int, default=OLLAMA_NUM_CTX,
                        help="Ollama context size in tokens, prompt plus answer. Only used "
                             "with --backend ollama.")
    parser.add_argument("--ollama-timeout", type=int, default=OLLAMA_TIMEOUT,
                        help="Seconds to wait for one Ollama answer before trying again.")
    parser.add_argument("--cache", default="exploration/llm/cache",
                        help="Folder for the cached answers")
    parser.add_argument("--out", default=None,
                        help="Where to write the combined results. Defaults to a name based on the model.")
    parser.add_argument("--limit", type=int, default=0,
                        help="Only do the first N records. Use this for a smoke test.")
    parser.add_argument("--id", action="append", default=[],
                        help="Only do these record ids. Can be given more than once.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the prompts and stop. No API key needed, nothing is charged.")
    parser.add_argument("--in-price", type=float, default=0.0,
                        help="Input price per million tokens, for the cost line at the end")
    parser.add_argument("--out-price", type=float, default=0.0,
                        help="Output price per million tokens, for the cost line at the end")
    parser.add_argument("--rpm", type=int, default=14,
                        help="Requests per minute to stay under. The free tier allows 15, "
                             "so the default leaves a little room. Only used with --backend gemini.")
    args = parser.parse_args()

    records = load_records(args.records, args.limit, args.id)
    print(f"{len(records):,} records to process with {args.model}")

    if args.dry_run:
        for _, record in records.iterrows():
            print("\n" + "=" * 70)
            print(f"record_id: {record['record_id']}")
            print("=" * 70)
            print(build_prompt(record))
        print(f"\nDry run, nothing was sent anywhere.")
        return 0

    cache_dir = Path(args.cache)
    cache_dir.mkdir(parents=True, exist_ok=True)

    if args.backend == "ollama":
        # Nothing to install and no key. Ask the server for its version first,
        # so a server that is not running is caught before the first record.
        try:
            with urllib.request.urlopen(args.ollama_url.rstrip("/") + "/api/version",
                                        timeout=10) as response:
                version = json.loads(response.read().decode("utf-8")).get("version")
            print(f"Ollama {version} at {args.ollama_url}")
        except (urllib.error.URLError, OSError) as error:
            print(f"ERROR: could not reach Ollama ({error}).")
            print(f"Is Ollama running at {args.ollama_url}?")
            return 1

        def ask(prompt):
            """Ask the Ollama server about one prompt."""
            return ask_ollama(args.ollama_url, args.model, prompt,
                              num_ctx=args.num_ctx, timeout=args.ollama_timeout)
    else:
        # The client is only imported here so that --dry-run works without the
        # package installed and without a key.
        try:
            from google import genai
        except ImportError:
            print("ERROR: the SDK is missing. Install it with:  pip install google-genai")
            return 1
        # Read the key out of the .env file. find_dotenv walks up the folder tree
        # from this script, so the file can sit in exploration/llm, in backend, or
        # at the top of the repo.
        try:
            from dotenv import find_dotenv, load_dotenv
            found = find_dotenv()
            if found:
                load_dotenv(found)
                print(f"read {found}")
        except ImportError:
            pass

        if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
            print("ERROR: no API key found.")
            print("Put a line like this in backend/.env:")
            print("    GEMINI_API_KEY=your-key-here")
            print("If python-dotenv is not installed:  pip install python-dotenv")
            return 1
        client = genai.Client()

        def ask(prompt):
            """Ask Gemini about one prompt."""
            return ask_model(client, args.model, prompt)

    rows = []
    fresh_calls = 0
    total_in = 0
    total_out = 0
    total_thinking = 0
    failures = 0
    stopped_early = False
    # Seconds each fresh call took, to report an average at the end.
    call_seconds = []

    # The pacer exists for Gemini's free tier limit of 15 requests a minute. A
    # local Ollama server has no such limit, so there it does nothing.
    pacer = Pacer(args.rpm if args.backend == "gemini" else 0)
    if args.backend == "gemini" and args.rpm:
        minutes = len(records) / args.rpm
        print(f"pacing at {args.rpm} requests a minute, so a full pass takes "
              f"about {minutes:.1f} minutes if nothing is cached yet")

    # How often to print a progress line. On a short run every 25 records would
    # mean printing nothing at all until the end, which looks like the script
    # has hung. On a long run a line per record would be noise.
    report_every = 1 if len(records) <= 40 else 25

    for position, (_, record) in enumerate(records.iterrows(), start=1):
        record_id = str(record["record_id"])
        path = cache_dir / cache_key(record_id, args.model, PROMPT_VERSION)

        if path.exists():
            cached = json.loads(path.read_text(encoding="utf-8"))
        else:
            pacer.wait()
            started = time.monotonic()
            try:
                result = ask(build_prompt(record))
            except OllamaUnreachable as stop:
                print()
                print(f"ERROR: Ollama stopped answering at record {position:,} ({stop}).")
                print(f"Is Ollama running at {args.ollama_url}?")
                print("Everything done so far is cached. Run the same command again")
                print("once the server is back and it will carry on from here.")
                stopped_early = True
                break
            except DailyQuotaReached as stop:
                # The free tier allows a fixed number of requests a day. Nothing
                # is lost: everything done so far is already in the cache, and
                # running the same command tomorrow picks up where this left off.
                print()
                print(f"Daily quota used up at record {position:,} of {len(records):,}.")
                print(f"  {stop}")
                print("Everything done so far is cached. Run the same command again")
                print("tomorrow and it will carry on from here without repeating anything.")
                stopped_early = True
                break
            call_seconds.append(time.monotonic() - started)
            # A local model can return JSON that does not fit the schema, so
            # its answers are checked before they can be cached. Gemini
            # enforces the schema itself, so its answers are left as they were.
            if args.backend == "ollama" and "answer" in result:
                problem = check_answer(result["answer"])
                if problem:
                    result = {"error": f"answer does not fit the schema: {problem}"}
            if "error" in result:
                print(f"    {record_id}: {result['error'][:150]}")
            cached = {"record_id": record_id, "model": args.model,
                      "prompt_version": PROMPT_VERSION, **result}
            # Only a real answer goes in the cache. A failure is usually
            # temporary, such as the model being briefly overloaded, and
            # caching it would mean this record is never attempted again on a
            # later run. Leaving it out lets the next run pick it up.
            if "error" not in cached:
                path.write_text(json.dumps(cached, ensure_ascii=False, indent=1),
                                encoding="utf-8")
            fresh_calls += 1
            total_in += cached.get("input_tokens") or 0
            total_out += cached.get("output_tokens") or 0
            total_thinking += cached.get("thinking_tokens") or 0
            if position % report_every == 0 or position == len(records):
                note = ""
                if cached.get("thinking_tokens"):
                    note = f", {cached['thinking_tokens']} thinking"
                print(f"  {position:,} of {len(records):,}, "
                      f"{cached.get('input_tokens') or 0} in / "
                      f"{cached.get('output_tokens') or 0} out{note}")

        if "error" in cached:
            failures += 1
            rows.append({"record_id": record_id, "model_error": cached["error"]})
            continue

        answer = cached["answer"]
        rows.append({
            "record_id": record_id,
            "model": args.model,
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
        })

    out_path = args.out or (f"exploration/llm/trial_{safe_file_name(args.model)}"
                            f"_v{PROMPT_VERSION}.csv")
    pd.DataFrame(rows).to_csv(out_path, index=False, encoding="utf-8-sig")

    print()
    print("=" * 62)
    print(f"wrote {out_path}")
    print("=" * 62)
    print(f"records answered: {len(rows):,} of {len(records):,}")
    print(f"new API calls:    {fresh_calls:,}   (the rest came from the cache, free)")
    print(f"failed:           {failures:,}")
    if fresh_calls:
        print(f"tokens:           {total_in:,} in, {total_out:,} out")
        if total_thinking:
            # Reasoning tokens are billed as output. If this number is large the
            # real cost is much higher than the visible answers suggest.
            share = total_thinking / total_out * 100 if total_out else 0
            print(f"  of which thinking: {total_thinking:,}  ({share:.0f}% of output)")
        print(f"average per record: {total_in / fresh_calls:.0f} in, "
              f"{total_out / fresh_calls:.0f} out")
        seconds = sum(call_seconds) / len(call_seconds)
        print(f"time per record:  {seconds:.1f} seconds on average, "
              f"{min(call_seconds):.1f} to {max(call_seconds):.1f}")
        print(f"  at that pace, 900 records take about {seconds * 900 / 60:.0f} minutes "
              f"and 123,479 about {seconds * 123479 / 3600:.0f} hours, one at a time")
        if args.in_price or args.out_price:
            cost = total_in / 1e6 * args.in_price + total_out / 1e6 * args.out_price
            per_record = cost / fresh_calls
            print(f"cost this run:    ${cost:.4f}")
            print(f"per record:       ${per_record:.6f}")
            print(f"123,479 records would be about ${per_record * 123479:.2f}")

    if stopped_early:
        print()
        print("This run stopped early, so the file above is incomplete.")
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
