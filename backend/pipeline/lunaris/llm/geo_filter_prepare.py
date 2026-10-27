"""
geo_filter_prepare.py

Make the input folder for a BC filter LLM run (geo_filter_run.py), on the
Mac. The run on Fir then needs no harvest, no rules file and no pyarrow:
everything it reads about the records is in this folder.

It writes, in --out:
  sample.csv        the records, numbered in order, with the same columns as
                    geo_pass1_trial.py's sample.csv (record_id, source,
                    title, reason, text_mentions_bc, evidence)
  records.jsonl.gz  one line per record: number, record_id, the harvest row
                    exactly as geo_pass1_trial.read_harvest returns it
                    (lists for arrays, NaN kept as NaN), and pass1_sha1, the
                    fingerprint of its pass 1 v3 prompt
  input.json        what was used: record list, rules file, harvest, counts,
                    prompt versions

Gzipped JSON lines, as for the v6 batches (make_batches.py), so the text
travels exactly as written. Before anything is written, every record's pass
1 prompt is built twice, from the harvest row and from its JSON copy, and
the two must be identical; if even one differs, nothing is written.

The records are either the ones listed in a CSV with a record_id column
(--records, in the file's order, such as the 150 labelled needs_llm
records), or every record of one class in the rules file (--rules-class
needs_llm, in the rules file's order, for the full run).

Run, from backend/:
    python pipeline/lunaris/llm/geo_filter_prepare.py \
        --records data/lunaris_bc_filter/label_set_2026_10/needs_llm_150.csv \
        --bc-boundary /Users/lucia/Desktop/BCBN/Results/bc_boundary_gbif.wkt \
        --out data/lunaris_bc_filter/fir_inputs/label150
"""

import argparse
import gzip
import json
import math
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
from bc_filter_rules import load_bc_shape  # noqa: E402
import geo_pass2_prompt_c4  # noqa: E402
import geo_prompt_v3  # noqa: E402
from geo_pass1_trial import read_harvest, sample_from_file  # noqa: E402
from prompt_v6 import prompt_sha1  # noqa: E402
from run_files import now_utc  # noqa: E402

SAMPLE_COLUMNS = ["record_id", "source", "title", "reason", "text_mentions_bc", "evidence"]


def plain(value):
    """
    Return a value as plain Python that JSON can hold: arrays and tuples as
    lists, numpy numbers as Python numbers, dicts value by value. Text,
    None and floats (NaN included) are kept as they are.
    """
    if isinstance(value, (np.ndarray, list, tuple)):
        return [plain(item) for item in value]
    if isinstance(value, dict):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, np.generic):
        return value.item()
    return value


def json_copy(record):
    """Return a record as it reads back from records.jsonl.gz: plain(), written to JSON and read again."""
    return json.loads(json.dumps(plain(record), ensure_ascii=False))


def same_value(a, b):
    """Return True if two plain values are equal, counting NaN as equal to NaN."""
    if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
        return True
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(same_value(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same_value(a[k], b[k]) for k in a)
    return a == b


def rules_class_sample(rules, name):
    """Return every record of one class in the rules file, in its order, with the trial sample's columns."""
    sample = rules[rules["bc_class"] == name].reset_index(drop=True)
    text_cols = ["text_title", "text_keywords", "text_abstract"]
    sample["text_mentions_bc"] = (sample[text_cols].fillna("") != "").any(axis=1)
    sample["reason"] = f"rules class {name}"
    return sample


def main():
    """Pick the records, check their prompts survive the JSON copy, and write the input folder."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--records", help="a CSV with a record_id column")
    choice.add_argument("--rules-class", help="every record of this class in the rules file, such as needs_llm")
    parser.add_argument("--rules", default="data/lunaris_bc_filter/rules_2026_10_v2.csv")
    parser.add_argument("--harvest", default="exploration/lunaris/harvest_2026_10_20/lunaris_full_harvest.parquet")
    parser.add_argument("--bc-boundary", required=True, help="bc_boundary_gbif.wkt")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    rules = pd.read_csv(args.rules, keep_default_na=False)
    if args.records:
        sample = sample_from_file(rules, args.records)
    else:
        sample = rules_class_sample(rules, args.rules_class)
    if sample["record_id"].duplicated().any():
        sys.exit("the record list holds the same record twice")
    shape = load_bc_shape(args.bc_boundary)
    harvest = read_harvest(args.harvest, sample["record_id"])
    missing = [r for r in sample["record_id"] if r not in harvest]
    if missing:
        sys.exit(f"{len(missing)} records are not in the harvest, such as {missing[0]}")

    lines, differ = [], []
    for number, record_id in enumerate(sample["record_id"]):
        row = harvest[record_id]
        copy = json_copy(row)
        prompt = geo_prompt_v3.build_prompt(row, shape)
        if not same_value(plain(row), copy) or geo_prompt_v3.build_prompt(copy, shape) != prompt:
            differ.append(record_id)
        lines.append({"number": number, "record_id": record_id, "record": plain(row),
                      "pass1_sha1": prompt_sha1(prompt)})
    if differ:
        sys.exit(f"{len(differ)} records do not survive the JSON copy unchanged, such as {differ[0]}; "
                 f"nothing written")

    os.makedirs(args.out, exist_ok=True)
    sample[SAMPLE_COLUMNS].to_csv(os.path.join(args.out, "sample.csv"), index_label="number")
    with gzip.open(os.path.join(args.out, "records.jsonl.gz"), "wt", encoding="utf-8") as handle:
        for line in lines:
            handle.write(json.dumps(line, ensure_ascii=False) + "\n")
    about = {"made": now_utc(), "records": len(sample), "record_list": args.records,
             "rules_class": args.rules_class, "rules": args.rules, "harvest": args.harvest,
             "pass1_prompt": f"v{geo_prompt_v3.GEO_PROMPT_VERSION}",
             "pass2_prompt": geo_pass2_prompt_c4.GEO_PASS2_VERSION}
    with open(os.path.join(args.out, "input.json"), "w", encoding="utf-8") as handle:
        json.dump(about, handle, indent=2)
        handle.write("\n")
    print(f"wrote {len(lines)} records to {args.out}; every pass 1 prompt is the same from the JSON copy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
