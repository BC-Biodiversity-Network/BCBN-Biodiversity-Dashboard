"""
Split the records for a cluster run into batch files, one batch per Slurm job.

Run this on the Mac. It reads either the labelled test set (a CSV) or a full
Lunaris harvest (a parquet file), and writes a run folder: run.json plus
inputs/batch_0000.jsonl.gz and so on. That folder is then copied to Fir.

Every record in a batch file carries the fingerprint (sha1) of the exact
prompt it makes. run_batch.py on the cluster rebuilds the prompt and checks
the fingerprint before asking the model, so the cluster is guaranteed to ask
the same question the Mac would.

The batch files are gzipped JSON lines rather than parquet, so the cluster
only needs plain Python and pandas to read them, and the text travels exactly
as written, with no type guessing on the way back in.

Test set: the records are read the same way run_trial.py reads them, and the
fingerprints are checked against run_trial.py's own prompts before anything is
written. If even one differs, nothing is written.

Harvest: an id that appears more than once is kept once, the row with the
latest datestamp (the first in the file on a tie), the same rule
refresh_test_set_text.py uses. The records keep the harvest's file order.
Subject lists are joined with ", ", which is what run_trial.py's tidying makes
of them after a CSV round trip.

Usage, from backend/:

    # the 900-record test set, one batch
    python pipeline/lunaris/llm/make_batches.py \
        --records exploration/llm/test_set_reviewed_harvest_2026_10.csv \
        --run-dir data/lunaris_llm_runs/test900

    # the full October harvest, 4,000 records per batch
    python pipeline/lunaris/llm/make_batches.py \
        --harvest exploration/lunaris/harvest_2026_10/lunaris_full_harvest.parquet \
        --run-dir data/lunaris_llm_runs/harvest_2026_10
"""

import argparse
import gzip
import hashlib
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prompt_v6 import INSTRUCTIONS, PROMPT_VERSION, build_prompt, prompt_sha1  # noqa: E402
from run_files import batch_name, inputs_path, now_utc  # noqa: E402

TEXT_COLUMNS = ["title", "subjects", "abstract"]

# The model as Ollama names it, and the name the merged CSV gives it. The
# label is different from the Mac's "qwen3.5:4b" so score_trial.py can load
# the Mac and the cluster results side by side without one replacing the other.
DEFAULT_MODEL = "qwen3.5:4b"
DEFAULT_LABEL = "qwen3.5:4b-fir"

DEFAULT_BATCH_SIZE = 4000

# A prompt longer than this many characters may not fit in Ollama's 16,384
# token context. On the 900 test prompts a token was 3.4 to 4.3 characters,
# so 55,000 characters can reach about 16,000 tokens. Such records are only
# counted here; run_batch.py reports them as context_full and they are retried
# with a larger --num-ctx.
LONG_PROMPT_CHARS = 55000


def file_sha1(path):
    """Return the sha1 of a file's bytes, to record exactly which source was used."""
    digest = hashlib.sha1()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def cell_text(value):
    """
    Turn one cell into the text stored in the batch file, or None if empty.

    Missing values become None, which build_prompt leaves out just as it
    leaves out a missing CSV cell. A list of subjects, as the harvest parquet
    holds them, is joined with ", ", skipping blank entries. Anything else is
    turned into text with str(), the same as build_prompt does.

    An empty list becomes "[]". That is how the test set stored it, and
    build_prompt turns it into an empty "Subjects:" line. Keeping that means
    the full run's prompts are built exactly like the ones that were tested.
    """
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    if hasattr(value, "__len__") and not isinstance(value, str):
        items = [str(item) for item in value if str(item).strip()]
        return ", ".join(items) if items else "[]"
    return str(value)


def records_from_test_set(path):
    """
    Read the labelled test set and return its records as a list of dicts.

    The CSV is read exactly as run_trial.py reads it. Before returning, the
    prompt each stored record makes is compared with the prompt run_trial.py
    would make from the CSV row itself, and the script stops if any differ.
    """
    frame = pd.read_csv(path, encoding="utf-8-sig")
    records = []
    mismatches = 0
    for _, row in frame.iterrows():
        record = {"record_id": str(row["record_id"])}
        for column in TEXT_COLUMNS:
            record[column] = cell_text(row.get(column))
        if build_prompt(record) != build_prompt(row):
            mismatches += 1
        records.append(record)
    if mismatches:
        raise SystemExit(f"ERROR: {mismatches} stored records would make a different prompt "
                         f"than run_trial.py makes from {path}. Nothing was written.")
    print(f"checked: all {len(records):,} prompts are identical to run_trial.py's")
    return records, len(frame), 0


def records_from_harvest(path):
    """
    Read a Lunaris harvest parquet and return one record per id, as dicts.

    Duplicate ids keep the row with the latest datestamp, the first in the
    file on a tie, and the kept rows stay in the harvest's file order. Returns
    the records, the number of rows read and the number of duplicate rows
    dropped.
    """
    harvest = pd.read_parquet(path, columns=["id", "datestamp"] + TEXT_COLUMNS)
    rows_read = len(harvest)
    # A stable sort keeps the file order among rows with the same datestamp,
    # and sorting the index afterwards puts the kept rows back in file order.
    newest = (harvest.sort_values("datestamp", ascending=False, kind="stable", na_position="last")
                     .drop_duplicates("id")
                     .sort_index())
    records = []
    for row in newest.itertuples(index=False):
        record = {"record_id": str(row.id)}
        for column in TEXT_COLUMNS:
            record[column] = cell_text(getattr(row, column))
        records.append(record)
    return records, rows_read, rows_read - len(newest)


def main():
    """Write the run folder: run.json and one gzipped JSON lines file per batch."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--records", help="the labelled test set CSV")
    source.add_argument("--harvest", help="a lunaris_full_harvest.parquet")
    parser.add_argument("--run-dir", required=True, help="the run folder to create")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
                        help=f"records per batch, one batch per Slurm job (default {DEFAULT_BATCH_SIZE})")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"the model as Ollama names it (default {DEFAULT_MODEL})")
    parser.add_argument("--label", default=DEFAULT_LABEL,
                        help=f"what the merged CSV puts in its model column (default {DEFAULT_LABEL})")
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    if (run_dir / "run.json").exists() or (run_dir / "inputs").exists():
        print(f"ERROR: {run_dir} already holds a run. Pick a new folder name.")
        return 1

    if args.records:
        source_path, source_kind = args.records, "test_set_csv"
        records, rows_read, duplicates = records_from_test_set(source_path)
    else:
        source_path, source_kind = args.harvest, "harvest_parquet"
        records, rows_read, duplicates = records_from_harvest(source_path)

    ids = [record["record_id"] for record in records]
    if len(set(ids)) != len(ids):
        print("ERROR: the same record_id appears more than once. Nothing was written.")
        return 1

    # Add each record's position in the whole run and its prompt fingerprint.
    empty = 0
    long_prompts = 0
    for position, record in enumerate(records):
        prompt = build_prompt(record)
        record["position"] = position
        record["prompt_sha1"] = prompt_sha1(prompt)
        if not any(record[column] and record[column].strip() not in ("", "[]") for column in TEXT_COLUMNS):
            empty += 1
        if len(prompt) > LONG_PROMPT_CHARS:
            long_prompts += 1

    (run_dir / "inputs").mkdir(parents=True)
    for folder in ["results", "errors", "logs"]:
        (run_dir / folder).mkdir()

    batches = 0
    for start in range(0, len(records), args.batch_size):
        chunk = records[start:start + args.batch_size]
        # mtime=0 keeps the gzip bytes the same from one make to the next.
        with gzip.GzipFile(inputs_path(run_dir, batches), "wb", mtime=0) as raw:
            for record in chunk:
                line = {key: record[key] for key in
                        ["position", "record_id", "title", "subjects", "abstract", "prompt_sha1"]}
                raw.write((json.dumps(line, ensure_ascii=False) + "\n").encode("utf-8"))
        batches += 1

    about = {
        "created": now_utc(),
        "source": str(source_path),
        "source_kind": source_kind,
        "source_sha1": file_sha1(source_path),
        "rows_in_source": rows_read,
        "duplicate_rows_dropped": duplicates,
        "records": len(records),
        "batch_size": args.batch_size,
        "batches": batches,
        "prompt_version": PROMPT_VERSION,
        "instructions_sha1": prompt_sha1(INSTRUCTIONS),
        "model": args.model,
        "label": args.label,
        "records_with_no_text": empty,
        "prompts_over_chars": {"limit": LONG_PROMPT_CHARS, "count": long_prompts},
    }
    (run_dir / "run.json").write_text(json.dumps(about, indent=1) + "\n", encoding="utf-8")

    size = sum(path.stat().st_size for path in (run_dir / "inputs").iterdir())
    print(f"wrote {run_dir}")
    print(f"  {len(records):,} records from {rows_read:,} rows"
          + (f" ({duplicates:,} duplicate rows dropped)" if duplicates else ""))
    print(f"  {batches} batches of up to {args.batch_size:,}: "
          f"{batch_name(0)} to {batch_name(batches - 1)}, {size / 1e6:.1f} MB")
    print(f"  model {args.model}, labelled {args.label} in the merged CSV, prompt v{PROMPT_VERSION}")
    print(f"  {empty:,} records have no title, subjects or abstract (they are still asked)")
    print(f"  {long_prompts:,} prompts are over {LONG_PROMPT_CHARS:,} characters and may need "
          f"a larger --num-ctx")
    return 0


if __name__ == "__main__":
    sys.exit(main())
