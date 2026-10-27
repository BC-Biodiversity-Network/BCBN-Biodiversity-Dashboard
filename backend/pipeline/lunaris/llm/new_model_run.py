"""
new_model_run.py

Make a new run folder that asks the same records as an existing one, with
another model. The input batches (inputs/batch_NNNN.jsonl.gz, made by
make_batches.py on the Mac) are copied byte for byte and checked by their
sha1, so the new run asks exactly the same prompts; run.json is the same
except for the model and its label, plus a note of where it was made from.
The new folder gets empty results/, errors/ and logs/ folders.

It never changes or removes anything in the existing folder, and refuses to
write into a folder that already exists. It needs only the Python standard
library, so it runs on the Fir login node after "module load python/3.12".

Run, on Fir:
    module load python/3.12
    python $B/f2code/new_model_run.py $B/runs/test900 $B/runs/test900_qwen3.6-35b-a3b_nofa \\
        --model qwen3.6:35b-a3b --label qwen3.6:35b-a3b-fir-nofa
"""

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone


def sha1_of(path):
    """Return the sha1 of a file's bytes."""
    digest = hashlib.sha1()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def make_run(source, target, model, label):
    """
    Make the new run folder (see the top of the file) and return its run.json
    as a dict. Stop if the source has no run.json or inputs, if the target
    exists, or if a copied batch does not match its source.
    """
    if not os.path.exists(os.path.join(source, "run.json")):
        sys.exit(f"ERROR: {source} has no run.json")
    batches = sorted(name for name in os.listdir(os.path.join(source, "inputs")) if name.endswith(".jsonl.gz"))
    if not batches:
        sys.exit(f"ERROR: {source}/inputs has no batch files")
    if os.path.exists(target):
        sys.exit(f"ERROR: {target} already exists; choose a new folder name")
    with open(os.path.join(source, "run.json"), encoding="utf-8") as handle:
        run = json.load(handle)
    if run.get("batches") is not None and run["batches"] != len(batches):
        sys.exit(f"ERROR: run.json says {run['batches']} batches, but {source}/inputs has {len(batches)}")

    os.makedirs(os.path.join(target, "inputs"))
    for name in ("results", "errors", "logs"):
        os.makedirs(os.path.join(target, name))
    for name in batches:
        source_file = os.path.join(source, "inputs", name)
        target_file = os.path.join(target, "inputs", name)
        shutil.copyfile(source_file, target_file)
        if sha1_of(source_file) != sha1_of(target_file):
            sys.exit(f"ERROR: the copy of {name} does not match its source")
    run.update({
        "model": model,
        "label": label,
        "made_from": {"run": os.path.abspath(source), "model": run.get("model"), "label": run.get("label"),
                      "inputs_copied": len(batches),
                      "copied": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
    })
    with open(os.path.join(target, "run.json"), "w", encoding="utf-8") as handle:
        json.dump(run, handle, indent=1)
        handle.write("\n")
    return run


def main():
    """Make the new run folder and say what it holds."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("source", help="an existing run folder (with run.json and inputs/)")
    parser.add_argument("target", help="the new run folder; must not exist yet")
    parser.add_argument("--model", required=True, help="the Ollama model, such as qwen3.6:35b-a3b")
    parser.add_argument("--label", required=True, help="the label in the merged CSV's model column")
    args = parser.parse_args()
    run = make_run(args.source, args.target, args.model, args.label)
    print(f"made {args.target}: {run['records']:,} records in {run['made_from']['inputs_copied']} batch(es), "
          f"copied byte for byte from {args.source}; model {args.model}, label {args.label}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
