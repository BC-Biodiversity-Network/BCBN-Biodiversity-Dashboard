"""
Ask a model about each record in the test set, and save what it says.

This script does NOT decide whether a record is biodiversity data. It only
collects facts about each record. The yes or no decision is worked out
afterwards, in code, by scoring_rules.py. The reason for splitting it that way:
the criteria are still being confirmed with Evan, and if the rules are baked
into the prompt then every rule change means paying to run the whole thing
again. Facts do not change when the rules do.

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
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

import pandas as pd


# Bump this when the prompt or the schema changes. It is part of the cache key,
# so raising it makes the script call the model again instead of reusing old
# answers that were produced by a different question.
PROMPT_VERSION = 3


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


# The shape the answer has to come back in. The Gemini API enforces this, so
# the reply is always valid JSON with these exact fields.
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "concerns_living_things": {
            "type": "boolean",
            "description": "True if the record is about living organisms or the ecosystems they live in.",
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
            "description": "Only for forestry, agriculture or fisheries records. yes if about the organisms, no if about tenure, volumes, prices or licensing, not_applicable for every other kind of record.",
        },
        "ecological_purpose_stated": {
            "type": "string",
            "enum": YES_NO_NA,
            "description": "Only for boundaries, land cover or maps. yes if an ecological or conservation purpose is stated, no if not, not_applicable for every other kind of record.",
        },
        "form": {"type": "string", "enum": FORM_VALUES},
        "language": {
            "type": "string",
            "description": "Main language of the record, for example english or french.",
        },
        "reason": {
            "type": "string",
            "description": "One short sentence explaining the answers above.",
        },
    },
    "required": [
        "concerns_living_things",
        "organisms",
        "organism_level",
        "topic",
        "about_organisms_themselves",
        "ecological_purpose_stated",
        "form",
        "language",
        "reason",
    ],
}


INSTRUCTIONS = """You are reading metadata records from a Canadian research data catalogue. \
For each record, report facts about what it contains. Do not decide whether it belongs in a \
biodiversity database, that decision is made separately.

Answer only from what the record actually says. Do not use background knowledge to fill in \
things the record does not state. Records may be in English or French.

Field notes:

concerns_living_things: true if the record is about living organisms or the ecosystems they \
live in. Physical measurements with no living subject, such as water temperature, bathymetry, \
wave height or weather, are false unless the record itself mentions living things.

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
part of the picture rather than the point. A study of a lake's ecology is this, not organisms.
  forestry_agriculture_fisheries: the subject is growing, harvesting or managing organisms as a \
resource, including crops, timber and fish stocks.
  land_and_boundaries: park and protected area boundaries, land cover, cadastral data, \
topographic maps.
  physical_environment: physical or chemical measurements with no living subject, such as water \
temperature, bathymetry, weather, geophysical surveys.
  administrative: government records such as budgets, schedules, addresses, election boundaries.
  human_health: human medical or health records.
  other: none of the above fits.

about_organisms_themselves: answer this ONLY when topic is forestry_agriculture_fisheries. yes \
when the record is about the organisms, for example tree species composition, crop trials, fish \
populations. no when it is about tenure boundaries, harvest volumes, farm income, prices or \
licensing. For every other topic answer not_applicable.

ecological_purpose_stated: answer this ONLY when topic is land_and_boundaries. yes when the \
record states an ecological or conservation purpose. no when it is a plain administrative or \
cartographic product. For every other topic answer not_applicable.

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
    """
    parts = [INSTRUCTIONS, "", "Record:", ""]
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


def main():
    """Run the model over the test set, caching every answer to disk."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", default="exploration/llm/test_set_labelled_fixed.csv",
                        help="CSV holding the records to ask about")
    parser.add_argument("--model", default="gemini-3.5-flash-lite",
                        help="Model name. Check AI Studio for what is currently available.")
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
                             "so the default leaves a little room.")
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

    rows = []
    fresh_calls = 0
    total_in = 0
    total_out = 0
    total_thinking = 0
    failures = 0
    stopped_early = False

    pacer = Pacer(args.rpm)
    if args.rpm:
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
            try:
                result = ask_model(client, args.model, build_prompt(record))
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
            cached = {"record_id": record_id, "model": args.model,
                      "prompt_version": PROMPT_VERSION, **result}
            path.write_text(json.dumps(cached, ensure_ascii=False, indent=1), encoding="utf-8")
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
            "form": answer["form"],
            "language": answer["language"],
            "model_reason": answer["reason"],
            "input_tokens": cached.get("input_tokens"),
            "output_tokens": cached.get("output_tokens"),
            "thinking_tokens": cached.get("thinking_tokens"),
        })

    out_path = args.out or f"exploration/llm/trial_{args.model.replace('/', '_')}.csv"
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
