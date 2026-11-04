"""
place_pick_prepare.py

Build the input folder of the place lookup's model step (prompt P1,
place_pick_prompt.py) from stage 1's model_items.jsonl
(../place_lookup_rules.py), on the Mac. The folder is copied to Fir, where
place_pick_run.py rebuilds every prompt and checks it against the
fingerprint made here before anything is asked.

Writes, into --out (which must not exist yet):
  items.jsonl.gz      one line per record: its number, dataset id, the item
                      from stage 1, and its prompts (units: order, chunk,
                      the names' indexes, and the prompt's fingerprint)
  sample.csv          one row per record: number, dataset id, title, how
                      many names the model decides, and how many prompts
  prompt_sizes.txt    how long the prompts are, in characters and in rough
                      tokens (characters / 3.5), and the longest ones
  examples/           a few whole prompts to read (the first record's, the
                      longest, and one with a contains search)

Run, from backend/:
    python pipeline/lunaris/llm/place_pick_prepare.py \\
        --items data/lunaris_place_lookup/stage1/model_items.jsonl \\
        --out data/lunaris_place_lookup/fir_inputs/places_p1
"""

import argparse
import gzip
import json
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import place_pick_prompt as prompt  # noqa: E402
from prompt_v6 import prompt_sha1  # noqa: E402

# A rough count of tokens from characters, for the size report only.
CHARACTERS_PER_TOKEN = 3.5


def unit_name(number, unit):
    """Return a prompt's name, used for its progress line and its file: record number, order and chunk."""
    return f"{number:04d}_{unit['order']}_{unit['chunk']}"


def prepared(number, item):
    """Return one line of items.jsonl.gz: the record's number, id and item, and each prompt with its fingerprint."""
    units = []
    for unit in prompt.units(item):
        text, _ = prompt.build_prompt(item, unit["indexes"], unit["order"])
        units.append(dict(unit, unit=unit_name(number, unit), sha1=prompt_sha1(text), characters=len(text)))
    return {"number": number, "dataset_id": item["dataset_id"], "item": item, "units": units}


def size_lines(lines):
    """Return prompt_sizes.txt's lines: the spread of prompt lengths and the ten longest."""
    sizes = pd.DataFrame([{"unit": u["unit"], "characters": u["characters"]} for line in lines for u in line["units"]])
    sizes["tokens"] = (sizes["characters"] / CHARACTERS_PER_TOKEN).round().astype(int)
    out = [f"{len(sizes):,} prompts for {len(lines):,} records ({prompt.PLACE_PICK_VERSION}, orders "
           f"{', '.join(prompt.ORDERS)}, at most {prompt.MAX_NAMES} names each)", ""]
    out += sizes[["characters", "tokens"]].describe(percentiles=[0.5, 0.9, 0.99]).round().astype(int).to_string().splitlines()
    out += ["", "The ten longest:"] + sizes.nlargest(10, "characters").to_string(index=False).splitlines()
    return out


def main():
    """Read stage 1's items, build and fingerprint every prompt, and write the input folder."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("--items", required=True, help="stage 1's model_items.jsonl")
    parser.add_argument("--out", required=True, help="the input folder to write")
    args = parser.parse_args()
    if os.path.exists(args.out):
        raise SystemExit(f"ERROR: {args.out} already exists; move it aside first")

    with open(args.items, encoding="utf-8") as handle:
        items = [json.loads(line) for line in handle]
    lines = [prepared(number, item) for number, item in enumerate(items)]
    os.makedirs(os.path.join(args.out, "examples"))
    with gzip.open(os.path.join(args.out, "items.jsonl.gz"), "wt", encoding="utf-8") as handle:
        for line in lines:
            handle.write(json.dumps(line, ensure_ascii=False) + "\n")
    pd.DataFrame([{"number": line["number"], "dataset_id": line["dataset_id"], "title": line["item"]["title"],
                   "names": len(prompt.model_names(line["item"])), "prompts": len(line["units"])}
                  for line in lines]).to_csv(os.path.join(args.out, "sample.csv"), index=False)
    report = size_lines(lines)
    with open(os.path.join(args.out, "prompt_sizes.txt"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(report) + "\n")

    longest = max(((u["characters"], line["number"], u) for line in lines for u in line["units"]
                   if u["order"] == "lookup"), key=lambda found: found[:2])
    contains = next((line["number"] for line in lines
                     if any(entry.get("match") == "contains" for entry in line["item"]["names"])), None)
    for label, number in (("first", 0), ("longest", longest[1]), ("contains", contains)):
        if number is None:
            continue
        line = lines[number]
        unit = line["units"][0] if label != "longest" else longest[2]
        text, _ = prompt.build_prompt(line["item"], unit["indexes"], unit["order"])
        with open(os.path.join(args.out, "examples", f"{label}_{unit['unit']}.txt"), "w", encoding="utf-8") as handle:
            handle.write(text)
    print("\n".join(report))
    print(f"\nwrote {args.out}: items.jsonl.gz, sample.csv, prompt_sizes.txt and examples/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
