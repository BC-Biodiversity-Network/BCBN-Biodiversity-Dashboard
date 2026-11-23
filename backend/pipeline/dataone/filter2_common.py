"""
filter2_common.py

What the DataONE filter 2 scripts share: reading a merged trial CSV with
decide()'s verdict, the letter of the decide() rule that answered, finding
a run folder's merged CSV, the test run folders, and the id pattern used to
pair PANGAEA copies.

Used by pipeline/dataone/build_filter2_union.py and prepare_cut_rerun.py,
and by the exploration scripts exploration/dataone/score_filter2_test.py
and check_duplicates.py, which imported these from each other before.

decide() itself is not here. It stays in exploration/llm/score_trial.py,
the one place the biodiversity rule is written, and is imported from there
exactly as the Lunaris pipeline does (build_bc_filter_rules.py,
compare_filter2_35b.py): by adding exploration/llm to the path.
"""

import glob
import json
import os
import re
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(BACKEND, "exploration", "llm"))
sys.path.insert(0, os.path.join(BACKEND, "pipeline", "lunaris", "llm"))
from merge_results import safe_file_name  # noqa: E402
from score_trial import decide  # noqa: E402

# The DataONE filter 2 test runs: the 200 label set records, copied back from Fir.
FILTER2_RUNS = os.path.join(BACKEND, "data", "dataone", "filter2_runs")
TEST_RUNS = {"4b": os.path.join(FILTER2_RUNS, "fir_dataone_test200"),
             "35b": os.path.join(FILTER2_RUNS, "fir_dataone_test200_qwen3.6-35b-a3b_nofa")}


def rule_of(record):
    """
    Return the letter of the decide() rule that answers a record (a to i),
    checked in decide()'s order. Used only to say which rule fired; the
    answer itself always comes from decide().
    """
    topic = record.get("topic")
    if record.get("experimental_animals") == "yes":
        return "a"
    if record.get("extinct_only") == "yes":
        return "b"
    if topic == "physical_environment":
        return "c"
    if topic == "human_health":
        return "d"
    if topic == "administrative":
        return "e"
    if not bool(record.get("concerns_living_things")):
        return "f"
    if topic == "forestry_agriculture_fisheries":
        return "g"
    if topic == "land_and_boundaries":
        return "h"
    return "i"


def answer_of_rule(rule, record):
    """Return the answer a rule gives a record, to check rule_of against decide()."""
    if rule in "abcdef":
        return "no"
    if rule == "g":
        return "yes" if record.get("about_organisms_themselves") == "yes" else "no"
    if rule == "h":
        return "yes" if record.get("ecological_purpose_stated") == "yes" else "no"
    return "yes"


def read_trial(path):
    """
    Read one merged trial CSV and add decide()'s verdict and the rule that
    gave it, for the answered records. Returns the answered rows and the
    number of rows with a model_error (failed or not run), which are left
    out as score_trial.py leaves them out.

    The file is read in one go (low_memory=False), as the Lunaris pipeline
    reads its merged CSVs (build_bc_filter_rules.py), so a column's type is
    worked out from the whole file. Stops if rule_of() and decide() ever
    disagree, or if concerns_living_things holds anything but True or False
    in an answered row.
    """
    frame = pd.read_csv(path, encoding="utf-8-sig", low_memory=False)
    frame["record_id"] = frame["record_id"].astype(str)
    failed = int(frame["model_error"].notna().sum()) if "model_error" in frame.columns else 0
    if "model_error" in frame.columns:
        frame = frame[frame["model_error"].isna()].copy()
    if not frame["concerns_living_things"].map(lambda v: v is True or v is False).all():
        sys.exit(f"STOPPED: concerns_living_things holds something other than True or False in {path}")
    frame["verdict"] = frame.apply(decide, axis=1)
    frame["rule"] = frame.apply(rule_of, axis=1)
    check = [answer_of_rule(r, rec) for r, rec in zip(frame["rule"], frame.to_dict("records"))]
    if list(frame["verdict"]) != check:
        sys.exit(f"STOPPED: rule_of() does not follow decide() for some record in {path}")
    return frame, failed


def find_trial(run_dir):
    """
    Return the merged trial CSV of a run folder and its run.json, the CSV
    named as merge_results.py names it: trial_<label>_v<prompt version>_<run
    name>.csv with the label from run.json. Stops unless exactly one file matches.
    """
    with open(os.path.join(run_dir, "run.json"), encoding="utf-8") as handle:
        run = json.load(handle)
    found = glob.glob(os.path.join(run_dir, f"trial_{safe_file_name(run['label'])}_v{run['prompt_version']}_*.csv"))
    if len(found) != 1:
        sys.exit(f"STOPPED: {len(found)} merged trial files in {run_dir}")
    return found[0], run


def id_pattern(record_id):
    """Return hex32 (exactly 32 hexadecimal characters), sha256 (starts with "sha256:"), doi or other for one id."""
    if re.fullmatch(r"[0-9a-fA-F]{32}", record_id):
        return "hex32"
    if record_id.startswith("sha256:"):
        return "sha256"
    if record_id.lower().startswith("doi:"):
        return "doi"
    return "other"
