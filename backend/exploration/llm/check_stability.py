"""
Ask the model the same question several times and see whether the answer moves.

temperature is set to 0 in run_trial.py, which turns off the random part of how
the model picks its words. That is not the same as a guarantee that two runs
give the same answer, so this checks it rather than assuming either way.

A handful of records are asked about repeatedly. Two of them are deliberately
easy, as a control: if even those move, something bigger is wrong. The rest are
records where the two models disagreed with each other or with the labels, so
they are where movement is most likely.

The prompt and the schema are imported from run_trial.py rather than copied, so
this measures the real thing and cannot drift away from it.

This script never reads or writes the shared cache. Every call is a fresh one,
which is the whole point.

Usage:

    python exploration/llm/check_stability.py --model gemini-3.5-flash-lite
    python exploration/llm/check_stability.py --model gemini-3.1-flash-lite --repeats 5
"""

import argparse
import json
import os
import sys
from collections import Counter

import pandas as pd

# Same folder as this script, so this picks up the real prompt.
import run_trial


# Rows to test, by the "row" column of the labelled sheet.
#
#   1, 18  easy controls. A waste collection schedule and an E. coli study.
#          Both models were confident and correct on these.
#    7     the record two runs of the same model disagreed on.
#   10     a 1961 topographic map. 3.1 said it concerns living things because
#          the legend mentions wooded areas, 3.5 said it does not.
#   13     an estuarine study where the models gave different organism_level.
DEFAULT_ROWS = [1, 7, 10, 13, 18]

# The fields worth watching. organisms is handled separately because it is a
# list and small wording differences there matter less.
WATCHED = [
    "concerns_living_things",
    "organism_level",
    "topic",
    "about_organisms_themselves",
    "ecological_purpose_stated",
    "form",
]


def summarise(values):
    """
    Turn the answers from several runs into one short readable line.

    If every run said the same thing, show that value. If they disagreed, show
    each value with how many times it came up, so the split is visible.
    """
    counts = Counter(values)
    if len(counts) == 1:
        return f"same every time: {values[0]}"
    parts = [f"{value} x{n}" for value, n in counts.most_common()]
    return "DIFFERED: " + ", ".join(parts)


def main():
    """Ask about a few records repeatedly and report what stayed the same."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", default="exploration/llm/test_set_labelled_fixed.csv")
    parser.add_argument("--model", default="gemini-3.5-flash-lite")
    parser.add_argument("--repeats", type=int, default=5,
                        help="How many times to ask about each record")
    parser.add_argument("--row", action="append", type=int, default=[],
                        help="Row number to test. Can be given more than once. "
                             "Defaults to a fixed set of five.")
    parser.add_argument("--rpm", type=int, default=14)
    args = parser.parse_args()

    wanted = args.row or DEFAULT_ROWS

    try:
        from dotenv import find_dotenv, load_dotenv
        found = find_dotenv()
        if found:
            load_dotenv(found)
    except ImportError:
        pass
    if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
        print("ERROR: no API key found. Put GEMINI_API_KEY in backend/.env")
        return 1
    try:
        from google import genai
    except ImportError:
        print("ERROR: the SDK is missing.  pip install google-genai")
        return 1

    sheet = pd.read_csv(args.records, encoding="utf-8-sig")
    records = sheet[sheet["row"].isin(wanted)]
    if len(records) != len(wanted):
        missing = sorted(set(wanted) - set(records["row"]))
        print(f"WARNING: these rows are not in the file: {missing}")

    total = len(records) * args.repeats
    print(f"{args.model}: {len(records)} records, {args.repeats} times each, "
          f"{total} calls in total")
    print(f"at {args.rpm} a minute that is about {total / args.rpm:.1f} minutes")
    print()

    client = genai.Client()
    pacer = run_trial.Pacer(args.rpm)

    unstable_rows = 0
    all_answers = []

    for _, record in records.iterrows():
        row = int(record["row"])
        print("=" * 68)
        print(f"row {row}: {str(record['title'])[:60]}")
        print(f"your label: is_biodiversity = {record['is_biodiversity']}")
        print("=" * 68)

        prompt = run_trial.build_prompt(record)
        answers = []
        for attempt in range(args.repeats):
            pacer.wait()
            try:
                result = run_trial.ask_model(client, args.model, prompt)
            except run_trial.DailyQuotaReached as stop:
                print(f"\nDaily quota used up. {stop}")
                return 2
            if "error" in result:
                print(f"  call {attempt + 1} failed: {result['error']}")
                continue
            answers.append(result["answer"])

        if not answers:
            print("  every call failed, nothing to compare")
            continue

        moved = False
        for field in WATCHED:
            line = summarise([a[field] for a in answers])
            if line.startswith("DIFFERED"):
                moved = True
            print(f"  {field:28s} {line}")

        # organisms is a list, so compare the sets rather than the exact text.
        organism_sets = ["; ".join(sorted(a["organisms"])) for a in answers]
        line = summarise(organism_sets)
        if line.startswith("DIFFERED"):
            moved = True
            print(f"  {'organisms':28s} DIFFERED across runs:")
            for value, n in Counter(organism_sets).most_common():
                print(f"      x{n}: {value[:90] or '(empty)'}")
        else:
            print(f"  {'organisms':28s} same every time")

        if moved:
            unstable_rows += 1
        print()
        all_answers.append({"row": row, "moved": moved, "answers": answers})

    print("=" * 68)
    print(f"{unstable_rows} of {len(all_answers)} records gave a different answer "
          f"on at least one field")
    if unstable_rows == 0:
        print("On this sample the model repeated itself exactly, so a single run")
        print("of the full set can be treated as stable.")
    else:
        print("The model does not always repeat itself, so a score from one run")
        print("carries some noise of its own, on top of the sampling noise.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
