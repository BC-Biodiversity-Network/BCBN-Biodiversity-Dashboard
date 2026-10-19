"""
Ask a model about each record in the test set, and save what it says.

This script does NOT decide whether a record is biodiversity data. It only
collects facts about each record. The yes or no decision is worked out
afterwards, in code, by score_trial.py. The reason for splitting it that way:
the criteria are still being confirmed with Evan, and if the rules are baked
into the prompt then every rule change means paying to run the whole thing
again. Facts do not change when the rules do.

Prompt version 6 adds two things. First, a short set of worked examples, kept
in backend/pipeline/lunaris/llm/prompt_examples_v6.json, shows the model a filled in
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
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pandas as pd


# The question itself (instructions, worked examples, schema, how a record
# becomes prompt text) and the Ollama client live with the pipeline code in
# backend/pipeline/lunaris/llm/, so the Mac run here and the cluster run on Fir
# ask exactly the same thing. This folder is two levels below backend/.
LLM_CODE = Path(__file__).resolve().parents[2] / "pipeline" / "lunaris" / "llm"
sys.path.insert(0, str(LLM_CODE))

from prompt_v6 import (  # noqa: E402
    BASE_INSTRUCTIONS,
    EXAMPLES_FILE,
    FIELD_ORDER,
    FORM_VALUES,
    INSTRUCTIONS,
    PROMPT_VERSION,
    RESPONSE_SCHEMA,
    TOPIC_VALUES,
    YES_NO_NA,
    answer_to_row,
    build_prompt,
    cache_key,
    check_answer,
    error_row,
    format_examples,
    load_examples,
    tidy_list_field,
)
from ollama_client import (  # noqa: E402
    OLLAMA_NUM_CTX,
    OLLAMA_TIMEOUT,
    OllamaUnreachable,
    ask_ollama,
    plain_json_schema,
)


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
            rows.append(error_row(record_id, cached["error"]))
            continue

        # The row is built by the shared helper, so the cluster's merged CSV
        # has exactly the same columns as this one.
        rows.append(answer_to_row(record_id, args.model, cached))

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