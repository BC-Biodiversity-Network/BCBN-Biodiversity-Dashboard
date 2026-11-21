"""
prepare_filter2_input.py

Write the DataONE records for filter 2 as cluster run folders, in exactly
the format the Lunaris filter 2 runs used, so the same Fir job scripts
(pipeline/lunaris/llm/fir/) run them.

The format is that of pipeline/lunaris/llm/make_batches.py. A run folder
holds run.json, empty results/, errors/ and logs/ folders, and
inputs/batch_NNNN.jsonl.gz. Each line of a batch is one JSON object with
position, record_id, title, subjects, abstract and prompt_sha1. Only
title, subjects and abstract go into the prompt. run_batch.py on Fir
rebuilds each prompt and checks it against prompt_sha1 before asking the
model.

The prompt (decided by Lucia on 2026-11-20): prompt v6 with the variant
v6_dataone (pipeline/lunaris/llm/prompt_variants.py). It is v6 word for
word, with the same worked examples, schema and decide(), except that the
first sentence no longer says the catalogue is Canadian. run.json records
prompt_variant and that variant's instructions_sha1, so run_batch.py on
Fir builds the same prompt.

The fields are filled from DataONE's own fields:
  record_id   the DataONE id, unchanged
  title       title: HTML entities decoded once (html.unescape), then
              clean_html (lunaris_harvest_only.py): the listed HTML tags
              removed, whitespace collapsed, None if empty
  subjects    keywords, each decoded and cleaned the same way; empty ones
              dropped. A record with no keywords gets an empty list, as
              every Lunaris record without subjects had, so the prompt
              shows an empty "Subjects:" line just as it did for Lunaris
  abstract    abstract, decoded and cleaned the same way
The decoding is there because the Lunaris text was decoded once when its
XML was parsed, and DataONE's text arrives as JSON with entities such as
"&amp;" still in it. Only the text sent to the model changes; the
harvested files are only read. Then make_batches.cell_text turns each field
into the stored text (a list is joined with ", ", an empty list becomes
"[]"), as for the Lunaris harvest. Nothing else is changed: language tags
such as "@en", garbled characters and abstracts DataONE cut short are left
as they are.

Records left out: a record with no title, no abstract and no keywords
after cleaning is not sent to the model. Such records are listed in
filter2_input/no_text_records.csv (id, datasource, group, reason "no
text"), so they are recorded, not lost.

Two runs, in backend/data/dataone/filter2_input/ (git ignores it; checked
before writing):
  dataone_test200              the label set records, whatever their group
                               (~/Desktop/BCBN/Results/
                               dataone_label_set_2026-11-19/label_set.csv),
                               in label set order, one batch
  dataone_harvest_2026_11_18   every record with to_filter2 true in
                               dataone_groups.parquet, minus those with
                               reuse_lunaris true in
                               dataone_lunaris_match.parquet (248,983 on
                               2026-11-20), less the records with no
                               text, in the harvest file's order, batches
                               of 3,000 as in the Lunaris full run
Both name qwen3.5:4b (label qwen3.5:4b-fir), like the Lunaris 4b runs. The
qwen3.6:35b-a3b run folders are made from them on Fir with
new_model_run.py, as for Lunaris (it keeps prompt_variant).

An existing run folder is never written over, unless --replace is given;
even then only a folder that has never been run (empty results/ and
errors/) is removed first.

After writing, every batch is read back: the counts, unique ids, positions,
and every prompt rebuilt with the run's variant must match its prompt_sha1.

Run, from backend/:
    ../.venv/bin/python pipeline/dataone/prepare_filter2_input.py [--replace]
"""

import argparse
import gzip
import html
import json
import os
import shutil
import sys
from pathlib import Path

import pandas as pd
import pyarrow.compute as pc
import pyarrow.parquet as pq

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(BACKEND, "pipeline", "lunaris"))
sys.path.insert(0, os.path.join(BACKEND, "pipeline", "lunaris", "llm"))
from classify_multibox_records import HARVEST, VERSIONS, check_ignored  # noqa: E402
from lunaris_harvest_only import clean_html  # noqa: E402
from make_batches import DEFAULT_LABEL, DEFAULT_MODEL, LONG_PROMPT_CHARS, cell_text, file_sha1  # noqa: E402
from prompt_v6 import PROMPT_VERSION, prompt_sha1  # noqa: E402
from prompt_variants import build_prompt_for, instructions_sha1, variant_of_run  # noqa: E402
from run_files import batch_name, batch_numbers, inputs_path, load_run, now_utc, read_inputs  # noqa: E402

GROUPS = os.path.join(HARVEST, "dataone_groups.parquet")
MATCH = os.path.join(HARVEST, "dataone_lunaris_match.parquet")
LABEL_SET = os.path.expanduser("~/Desktop/BCBN/Results/dataone_label_set_2026-11-19/label_set.csv")
OUT_DIR = os.path.join(BACKEND, "data", "dataone", "filter2_input")
TEST_RUN = os.path.join(OUT_DIR, "dataone_test200")
FULL_RUN = os.path.join(OUT_DIR, "dataone_harvest_2026_11_18")
NO_TEXT_CSV = os.path.join(OUT_DIR, "no_text_records.csv")
PROMPT_VARIANT = "v6_dataone"
EXPECTED_TEST = 200
EXPECTED_FULL = 248_983     # selected before the no-text records are left out
FULL_BATCH_SIZE = 3000      # the Lunaris full runs' batch size (data/lunaris_llm_runs/harvest_2026_10_20/run.json)
TEXT_COLUMNS = ["title", "subjects", "abstract"]
LINE_KEYS = ["position", "record_id", "title", "subjects", "abstract", "prompt_sha1"]


def decode(text):
    """Decode HTML entities once (html.unescape); a value that is not text is returned as it is."""
    return html.unescape(text) if isinstance(text, str) else text


def clean_text(text):
    """Decode entities once, then clean as Lunaris did (clean_html); None for a missing or empty value."""
    return clean_html(decode(text)) if isinstance(text, str) else None


def clean_keywords(keywords):
    """
    Return a record's keywords as Lunaris subjects were made, after
    decoding: each cleaned, empty ones dropped; an empty list when there
    are none.
    """
    if keywords is None:
        return []
    return [c for c in (clean_text(k) for k in keywords if isinstance(k, str)) if c]


def record_of(record_id, title, abstract, keywords):
    """Return one record with the three prompt fields cleaned and stored as make_batches stores them."""
    return {"record_id": str(record_id),
            "title": cell_text(clean_text(title)),
            "subjects": cell_text(clean_keywords(keywords)),
            "abstract": cell_text(clean_text(abstract))}


def has_no_text(record):
    """True when a record has no title, no abstract and no keywords after cleaning."""
    return record["title"] is None and record["abstract"] is None and record["subjects"] == "[]"


def read_latest(ids):
    """Return the latest records' id, title, abstract and keywords for the given ids, in the harvest file's order."""
    filters = [("version_status", "=", "latest"), ("id", "in", sorted(ids))]
    table = pq.read_table(VERSIONS, columns=["id", "title", "abstract", "keywords"], filters=filters)
    if table.num_rows and pc.count_distinct(table.column("id")).as_py() != table.num_rows:
        sys.exit("STOPPED: an id appears twice among the latest records")
    return table.to_pandas()


def full_ids():
    """Return the ids selected for the full run: to_filter2 true, minus reuse_lunaris true. Stops if the count is not as expected."""
    groups = pd.read_parquet(GROUPS, columns=["id", "to_filter2"])
    match = pd.read_parquet(MATCH, columns=["id", "reuse_lunaris"])
    reuse = set(match.loc[match["reuse_lunaris"], "id"])
    ids = set(groups.loc[groups["to_filter2"], "id"]) - reuse
    if len(ids) != EXPECTED_FULL:
        sys.exit(f"STOPPED: {len(ids):,} records selected for the full run, not {EXPECTED_FULL:,}")
    return ids


def test_ids():
    """Return the label set ids in label set order. Stops unless there are exactly 200 distinct ids."""
    labels = pd.read_csv(LABEL_SET, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    ids = labels["id"].tolist()
    if len(ids) != EXPECTED_TEST or len(set(ids)) != EXPECTED_TEST:
        sys.exit(f"STOPPED: the label set has {len(ids)} rows and {len(set(ids))} distinct ids, not {EXPECTED_TEST}")
    return ids


def build_records(frame, order=None):
    """
    Return (records to send, ids left out for having no text), in file
    order or in the given id order.
    """
    if order is not None:
        frame = frame.set_index("id").loc[order].reset_index()
    records, no_text = [], []
    for r in frame.itertuples(index=False):
        record = record_of(r.id, r.title, r.abstract, r.keywords)
        if has_no_text(record):
            no_text.append(r.id)
        else:
            records.append(record)
    return records, no_text


def decoding_report(frame):
    """
    Print how many fields decoding changed (title, abstract, keywords
    lists) and two before and after examples, the after side as the model
    gets it.
    """
    changed = {"title": 0, "abstract": 0, "keywords (records)": 0, "keywords (single keywords)": 0}
    examples = {}   # the first changed title and the first changed abstract
    for r in frame.itertuples(index=False):
        for field in ("title", "abstract"):
            value = getattr(r, field)
            if isinstance(value, str) and decode(value) != value:
                changed[field] += 1
                examples.setdefault(field, (r.id, value, clean_text(value)))
        keywords = [k for k in (r.keywords if r.keywords is not None else []) if isinstance(k, str)]
        n = sum(decode(k) != k for k in keywords)
        changed["keywords (single keywords)"] += n
        changed["keywords (records)"] += n > 0
    print("   decoding changed: " + "; ".join(f"{k} {v:,}" for k, v in changed.items()))
    for field, (record_id, before, after) in examples.items():
        print(f"   example {record_id} ({field})\n      before: {before[:240]!r}\n      after:  {after[:240]!r}")
    return changed


def remove_unrun(run_dir):
    """
    Remove one of this script's own run folders before writing it again, but
    only if it has never been run: results/ and errors/ must be empty.
    """
    run_dir = Path(run_dir)
    if run_dir.resolve().parent != Path(OUT_DIR).resolve() or not (run_dir / "run.json").exists():
        sys.exit(f"STOPPED: {run_dir} is not a run folder of this script")
    for folder in ("results", "errors"):
        if any((run_dir / folder).iterdir()):
            sys.exit(f"STOPPED: {run_dir}/{folder} is not empty: this run has been used. Not removing it.")
    shutil.rmtree(run_dir)


def write_run(run_dir, records, batch_size, about_source, replace):
    """
    Write one run folder in make_batches.py's format, with the DataONE
    prompt variant, and return its run.json as a dict. Stops if the folder
    already holds a run (unless replace) or an id repeats.
    """
    run_dir = Path(run_dir)
    if (run_dir / "run.json").exists() or (run_dir / "inputs").exists():
        if not replace:
            sys.exit(f"STOPPED: {run_dir} already holds a run; pass --replace to write it again")
        remove_unrun(run_dir)
    ids = [r["record_id"] for r in records]
    if len(set(ids)) != len(ids):
        sys.exit("STOPPED: the same record_id appears more than once")
    empty = long_prompts = 0
    for position, record in enumerate(records):
        prompt = build_prompt_for(record, PROMPT_VARIANT)
        record["position"] = position
        record["prompt_sha1"] = prompt_sha1(prompt)
        # Counted exactly as make_batches.py counts them.
        if not any(record[c] and record[c].strip() not in ("", "[]") for c in TEXT_COLUMNS):
            empty += 1
        if len(prompt) > LONG_PROMPT_CHARS:
            long_prompts += 1
    (run_dir / "inputs").mkdir(parents=True)
    for folder in ["results", "errors", "logs"]:
        (run_dir / folder).mkdir()
    batches = 0
    for start in range(0, len(records), batch_size):
        # mtime=0 keeps the gzip bytes the same from one make to the next, as in make_batches.py.
        with gzip.GzipFile(inputs_path(run_dir, batches), "wb", mtime=0) as raw:
            for record in records[start:start + batch_size]:
                line = {key: record[key] for key in LINE_KEYS}
                raw.write((json.dumps(line, ensure_ascii=False) + "\n").encode("utf-8"))
        batches += 1
    about = {
        "created": now_utc(),
        "source": os.path.relpath(VERSIONS, BACKEND),
        "source_kind": "dataone_versions_parquet",
        "source_sha1": file_sha1(VERSIONS),
        "rows_in_source": about_source["rows"],
        "duplicate_rows_dropped": 0,
        "records": len(records),
        "batch_size": batch_size,
        "batches": batches,
        "prompt_version": PROMPT_VERSION,
        "prompt_variant": PROMPT_VARIANT,
        "instructions_sha1": instructions_sha1(PROMPT_VARIANT),
        "model": DEFAULT_MODEL,
        "label": DEFAULT_LABEL,
        "records_with_no_text": empty,
        "prompts_over_chars": {"limit": LONG_PROMPT_CHARS, "count": long_prompts},
        "dataone": about_source["dataone"],
    }
    (run_dir / "run.json").write_text(json.dumps(about, indent=1) + "\n", encoding="utf-8")
    return about


def verify_run(run_dir, expected_ids):
    """
    Read every batch back and stop unless: the ids are exactly the expected
    ones, none repeats, positions run 0, 1, 2, ... in order, and every
    prompt rebuilt with the run's own variant (read from its run.json, as
    run_batch.py does) matches its prompt_sha1.
    """
    variant = variant_of_run(load_run(run_dir))
    seen = []
    for number in batch_numbers(run_dir):
        for line in read_inputs(run_dir, number):
            if list(line) != LINE_KEYS:
                sys.exit(f"STOPPED: {batch_name(number)} has a line with keys {list(line)}")
            if prompt_sha1(build_prompt_for(line, variant)) != line["prompt_sha1"]:
                sys.exit(f"STOPPED: the prompt of {line['record_id']} does not match its fingerprint")
            if line["position"] != len(seen):
                sys.exit(f"STOPPED: position {line['position']} where {len(seen)} was expected")
            seen.append(line["record_id"])
    if len(seen) != len(set(seen)):
        sys.exit(f"STOPPED: {run_dir} has repeated ids")
    if set(seen) != set(expected_ids):
        sys.exit(f"STOPPED: {run_dir} does not hold exactly the expected records")
    return len(seen)


def write_no_text(ids):
    """Write the records left out for having no text to NO_TEXT_CSV: id, datasource, group, reason."""
    groups = pd.read_parquet(GROUPS, columns=["id", "datasource", "group"]).set_index("id")
    table = pd.DataFrame({"id": sorted(set(ids))})
    table["datasource"] = table["id"].map(groups["datasource"])
    table["group"] = table["id"].map(groups["group"])
    table["reason"] = "no text"
    table.to_csv(NO_TEXT_CSV + ".tmp", index=False, encoding="utf-8-sig")
    os.replace(NO_TEXT_CSV + ".tmp", NO_TEXT_CSV)
    print(f"wrote {len(table):,} records with no text -> {NO_TEXT_CSV} "
          f"({table['datasource'].value_counts().head(6).to_dict()})")


def summary(run_dir, about, left_out):
    """Print what one run folder holds: records, batches, size, variant, records left out, long prompts."""
    size = sum(p.stat().st_size for p in (Path(run_dir) / "inputs").iterdir())
    print(f"wrote {run_dir}")
    print(f"   {about['records']:,} records in {about['batches']} batch(es) of up to {about['batch_size']:,} "
          f"({batch_name(0)} to {batch_name(about['batches'] - 1)}), {size / 1e6:.1f} MB")
    print(f"   model {about['model']} (label {about['label']}), prompt v{about['prompt_version']} "
          f"variant {about['prompt_variant']}, instructions sha1 {about['instructions_sha1']}")
    print(f"   left out for having no text: {left_out:,}; records with no text in the run: "
          f"{about['records_with_no_text']:,}; prompts over {LONG_PROMPT_CHARS:,} characters: "
          f"{about['prompts_over_chars']['count']:,}")


def main():
    """Check the output folder, write both runs and the no-text list, read the runs back and print a summary."""
    parser = argparse.ArgumentParser(description="Write the DataONE filter 2 run folders (Lunaris format).")
    parser.add_argument("--replace", action="store_true",
                        help="write the run folders again (only folders that have never been run)")
    args = parser.parse_args()
    check_ignored(OUT_DIR)
    sources = {"groups": os.path.relpath(GROUPS, BACKEND), "groups_sha1": file_sha1(GROUPS),
               "lunaris_match": os.path.relpath(MATCH, BACKEND), "lunaris_match_sha1": file_sha1(MATCH)}
    no_text_ids = []

    ids = test_ids()
    frame = read_latest(ids)
    if len(frame) != EXPECTED_TEST:
        sys.exit(f"STOPPED: only {len(frame)} of the {EXPECTED_TEST} label set ids are latest records")
    records, no_text = build_records(frame, order=ids)
    no_text_ids += no_text
    print("TEST RUN")
    decoding_report(frame)
    about = write_run(TEST_RUN, records, EXPECTED_TEST,
                      {"rows": EXPECTED_TEST, "dataone": {**sources, "selection": "label set, less no-text records",
                                                            "label_set": LABEL_SET}}, args.replace)
    verify_run(TEST_RUN, [r["record_id"] for r in records])
    summary(TEST_RUN, about, len(no_text))

    ids = full_ids()
    frame = read_latest(ids)
    if len(frame) != EXPECTED_FULL:
        sys.exit(f"STOPPED: {len(frame):,} of the {EXPECTED_FULL:,} ids are latest records")
    records, no_text = build_records(frame)
    no_text_ids += no_text
    if len(records) + len(no_text) != EXPECTED_FULL:
        sys.exit("STOPPED: the records written and left out do not add up to the selection")
    print("\nFULL RUN")
    decoding_report(frame)
    about = write_run(FULL_RUN, records, FULL_BATCH_SIZE,
                      {"rows": EXPECTED_FULL, "dataone": {**sources, "selection": "to_filter2 and not "
                                                            "reuse_lunaris, less no-text records"}}, args.replace)
    verify_run(FULL_RUN, [r["record_id"] for r in records])
    summary(FULL_RUN, about, len(no_text))

    print()
    write_no_text(no_text_ids)
    print("read back: ids, counts, positions and every prompt fingerprint check out")
    return 0


if __name__ == "__main__":
    sys.exit(main())
