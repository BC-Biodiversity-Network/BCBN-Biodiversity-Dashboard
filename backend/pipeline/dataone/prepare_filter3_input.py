"""
prepare_filter3_input.py

Step 2 of DataONE filter 3: make the input folders for the geography LLM
run on Fir (pipeline/lunaris/llm/geo_filter_run.py), on the Mac, in exactly
the format of the Lunaris inputs (geo_filter_prepare.py), with the DataONE
prompt variant. The run on Fir then needs no harvest and no pyarrow:
everything it reads about the records is in the folder.

Which records: those filter3_rules.parquet (build_filter3_rules.py) sends to
the LLM (route llm), assembled as geography prompt records by
filter3_records.read_geo_records.

Two folders, in data/dataone/filter3_input/ (ignored by git; checked):
  dataone_label_test  every record of the three label sets with route llm,
                      to score filter 3: the first label set
                      (lucia_labels_final_and_comparison.csv), then the BC
                      first 50 and the BC strict 50
                      (claude_bc_labels_first50.csv, _strict50.csv), each
                      in its row order
  dataone_needs_llm   every record with route llm, in the rules table's order

Each folder holds, as geo_filter_prepare.py writes them:
  sample.csv        the records, numbered: record_id, source (the DataONE
                    member repository), title, reason (why the record goes
                    to the LLM), text_mentions_bc (the Lunaris BC word check
                    on title, keywords and abstract), evidence (what the
                    rules said)
  records.jsonl.gz  one line per record: number, record_id, the record (as
                    read_geo_records gives it) and pass1_sha1, the
                    fingerprint of its pass 1 prompt in the dataone variant
  input.json        what was used, with "geo_prompt_variant": "dataone" and
                    both passes' instructions fingerprints
Before anything is written, every record's pass 1 prompt is built twice,
from the record and from its JSON copy, and the two must be identical; and
after writing, the folder is read back as geo_filter_run.py reads it and
every prompt is rebuilt and checked against its fingerprint. The script
stops if a folder already holds files, unless --replace.

Run, from backend/:
    ../.venv/bin/python pipeline/dataone/prepare_filter3_input.py
"""

import argparse
import gzip
import json
import os
import shutil
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, "..", ".."))
LLM = os.path.join(BACKEND, "pipeline", "lunaris", "llm")
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(LLM))
sys.path.insert(0, LLM)
import geo_prompt_variants  # noqa: E402
from bc_filter_rules import bc_mentions, load_bc_shape, read_bc_places  # noqa: E402
from build_filter3_rules import LABELS, OUT as RULES  # noqa: E402
from classify_multibox_records import BC_BOUNDARY, check_ignored  # noqa: E402
from filter3_records import read_geo_records  # noqa: E402
from geo_filter_prepare import SAMPLE_COLUMNS, json_copy, plain, same_value  # noqa: E402
from geo_filter_run import read_input, read_variant  # noqa: E402
from prompt_v6 import prompt_sha1  # noqa: E402
from make_batches import file_sha1  # noqa: E402
from run_files import now_utc  # noqa: E402

VARIANT = "dataone"
OUT_DIR = os.path.join(BACKEND, "data", "dataone", "filter3_input")
TEST = os.path.join(OUT_DIR, "dataone_label_test")
FULL = os.path.join(OUT_DIR, "dataone_needs_llm")
EXPECTED_FULL = 70_521


def stop(message):
    """Stop the script with a message that starts with STOPPED."""
    sys.exit(f"STOPPED: {message}")


def read_rules():
    """Return the rules table (filter3_rules.parquet) indexed by id, after checking no id repeats."""
    rules = pd.read_parquet(RULES)
    if rules["id"].duplicated().any():
        stop("filter3_rules.parquet lists a record twice")
    return rules.set_index("id", drop=False)


def label_test_ids(rules):
    """
    Return the ids for the label test folder, in order (each label set in
    its file's row order, a record only once), and how many come from each
    set: every labelled record whose route is llm.
    """
    ids, counts, seen = [], {}, set()
    for name, path, _ in LABELS:
        labels = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
        if "row" in labels.columns:
            labels = labels.sort_values("row", key=lambda c: c.astype(int))
        wanted = [i for i in labels["id"] if i in rules.index and rules.at[i, "route"] == "llm" and i not in seen]
        counts[name] = (len(labels), int(labels["id"].isin(rules.index).sum()), len(wanted))
        ids += wanted
        seen.update(wanted)
    return ids, counts


def text_mentions_bc(record, places):
    """Return True if the Lunaris BC word check finds BC in the record's title, keywords or abstract."""
    parts = [record.get("title"), "; ".join(str(s) for s in record.get("subjects") or []), record.get("abstract")]
    return any(bc_mentions(p if isinstance(p, str) else "", places) for p in parts)


def build_lines(ids, rules, shape):
    """
    Return (sample, lines) for the given ids: the sample table and the
    records.jsonl.gz lines, each with its pass 1 fingerprint in the dataone
    variant. Stops if a record does not survive the JSON copy unchanged, or
    its pass 1 prompt from the copy differs.
    """
    records = read_geo_records(ids)
    places = read_bc_places()
    rows, lines, differ = [], [], []
    for number, record_id in enumerate(ids):
        record = records[record_id]
        copy = json_copy(record)
        prompt = geo_prompt_variants.build_pass1_prompt(record, shape, VARIANT)
        if not same_value(plain(record), copy) or geo_prompt_variants.build_pass1_prompt(copy, shape, VARIANT) != prompt:
            differ.append(record_id)
        rule = rules.loc[record_id]
        rows.append({"record_id": record_id, "source": rule["datasource"], "title": record["title"] or "",
                     "reason": rule["route_reason"], "text_mentions_bc": text_mentions_bc(record, places),
                     "evidence": rule["rule_evidence"]})
        lines.append({"number": number, "record_id": record_id, "record": plain(record),
                      "pass1_sha1": prompt_sha1(prompt)})
    if differ:
        stop(f"{len(differ)} records do not survive the JSON copy unchanged, such as {differ[0]}; nothing written")
    return pd.DataFrame(rows, columns=SAMPLE_COLUMNS), lines


def write_folder(folder, sample, lines, about, replace):
    """Write sample.csv, records.jsonl.gz and input.json to a new folder (or one never run, with replace)."""
    if os.path.isdir(folder) and os.listdir(folder):
        if not replace:
            stop(f"{os.path.relpath(folder, BACKEND)} already holds files; add --replace to write it again")
        shutil.rmtree(folder)
    os.makedirs(folder)
    sample.to_csv(os.path.join(folder, "sample.csv"), index_label="number")
    with gzip.open(os.path.join(folder, "records.jsonl.gz"), "wt", encoding="utf-8") as handle:
        for line in lines:
            handle.write(json.dumps(line, ensure_ascii=False) + "\n")
    with open(os.path.join(folder, "input.json"), "w", encoding="utf-8") as handle:
        json.dump(about, handle, indent=2)
        handle.write("\n")


def verify_folder(folder, ids, shape):
    """
    Read a folder back as geo_filter_run.py reads it and stop unless it holds
    exactly ids, in order, once each, its variant is dataone, and every pass
    1 prompt rebuilds to its fingerprint. Returns the longest pass 1 prompts
    as (characters, record id) pairs, longest first.
    """
    sample, records = read_input(folder)
    if read_variant(folder) != VARIANT:
        stop(f"{folder} is not a {VARIANT} input")
    found = [records[n]["record_id"] for n in sorted(records)]
    if found != list(ids) or len(set(found)) != len(found):
        stop(f"{folder} does not hold exactly the chosen records, once each, in order")
    lengths = []
    for number in sorted(records):
        prompt = geo_prompt_variants.build_pass1_prompt(records[number]["record"], shape, VARIANT)
        if prompt_sha1(prompt) != records[number]["pass1_sha1"]:
            stop(f"{folder}: the pass 1 prompt of {records[number]['record_id']} does not match its fingerprint")
        lengths.append((len(prompt), records[number]["record_id"]))
    return sorted(lengths, reverse=True)


def about_folder(name, ids, extra):
    """Return input.json for a folder: what was used, the variant and the instructions fingerprints."""
    return {"made": now_utc(), "records": len(ids), "name": name, "rules": os.path.relpath(RULES, BACKEND),
            "rules_sha1": file_sha1(RULES), "pass1_prompt": "v3", "pass2_prompt": "C4",
            **geo_prompt_variants.fingerprints(VARIANT), **extra}


def folder_size(folder):
    """Return the size of a folder's files in bytes."""
    return sum(os.path.getsize(os.path.join(folder, f)) for f in os.listdir(folder))


def main():
    """Choose the records, write both input folders, read them back and print what they hold."""
    parser = argparse.ArgumentParser(description="Make the DataONE filter 3 input folders for Fir.")
    parser.add_argument("--replace", action="store_true", help="write the folders again (only if never run)")
    parser.add_argument("--bc-boundary", default=BC_BOUNDARY)
    args = parser.parse_args()
    for folder in (TEST, FULL):
        check_ignored(os.path.join(folder, "records.jsonl.gz"))
    shape = load_bc_shape(args.bc_boundary)
    rules = read_rules()
    full_ids = list(rules.loc[rules["route"] == "llm", "id"])
    if len(full_ids) != EXPECTED_FULL:
        stop(f"{len(full_ids):,} records go to the LLM, not {EXPECTED_FULL:,}")
    test_ids, counts = label_test_ids(rules)
    for name, folder, ids, extra in (
            ("dataone_label_test", TEST, test_ids, {"label_sets": {n: {"rows": c[0], "kept": c[1], "route_llm": c[2]}
                                                                   for n, c in counts.items()}}),
            ("dataone_needs_llm", FULL, full_ids, {"selection": "route llm in filter3_rules.parquet"})):
        sample, lines = build_lines(ids, rules, shape)
        write_folder(folder, sample, lines, about_folder(name, ids, extra), args.replace)
        longest = verify_folder(folder, ids, shape)
        print(f"wrote {os.path.relpath(folder, BACKEND)}: {len(ids):,} records, {folder_size(folder) / 1e6:.1f} MB; "
              f"every pass 1 prompt rebuilds to its fingerprint (variant {VARIANT})")
        print(f"   longest pass 1 prompts (characters): {[(n, i[:40]) for n, i in longest[:5]]}")
        if name == "dataone_label_test":
            for set_name, (rows, kept, llm) in counts.items():
                print(f"   {set_name}: {rows} rows, {kept} kept by filter 2, {llm} with route llm")
    return 0


if __name__ == "__main__":
    sys.exit(main())
