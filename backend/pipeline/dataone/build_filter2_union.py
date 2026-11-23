"""
build_filter2_union.py

Filter 2's keep set for DataONE, built the way Lunaris's was: a record is
kept if either model's facts give "yes" under score_trial.decide()
(exploration/llm/score_trial.py, imported and not changed). Nothing here asks
a model anything; every input is only read.

Inputs (backend/, all git-ignored):
  data/dataone/filter2_runs/fir_dataone_harvest_2026_11_18/
      the qwen3.5:4b full run, copied back from Fir, with its merged CSV
  data/dataone/filter2_runs/fir_dataone_harvest_2026_11_18_qwen3.6-35b-a3b_nofa/
      the qwen3.6:35b-a3b full run (flash attention off), the same way
  data/dataone/harvest_2026_11_18/
      dataone_groups.parquet           group and datasource of each record
      dataone_multibox_groups.parquet  which records have several boxes
      dataone_lunaris_match.parquet    reuse_lunaris and the Lunaris decision
      dataone_full_harvest_versions.parquet  title, abstract (for the lists)
  data/dataone/filter2_input/no_text_records.csv  the records left out of
      filter 2 because they have no text (only counted here)
  data/dataone/filter2_runs/fir_dataone_cut_rerun_2026_11_23/ and
  data/dataone/filter2_runs/fir_dataone_cut_rerun_2026_11_23_qwen3.6-35b-a3b_nofa/
      the reruns of the cut prompts with a larger context (made by
      prepare_cut_rerun.py), once merged and copied back; optional
  pipeline/dataone/filter2_hand_decisions.csv  the hand decisions (in git)

Checks before anything is written (the script stops if one fails):
  - each run folder's run.json names the expected model, prompt variant
    v6_dataone and its instructions fingerprint, and 248,306 records; the
    35b run's flash_attention.txt says off;
  - each merged CSV holds exactly the run folder's input records, once each;
  - the valid answers in the run folder's results/ (read with run_files,
    as merge_results.py reads them) are exactly the CSV's answered records,
    and all come from one model digest, the one the .about.json names;
  - both runs hold the same records; none of them reuses Lunaris; the run
    records, the Lunaris reuse records and the no-text records together
    are exactly the records sent to filter 2.

Prompts Ollama cut. A prompt longer than the context (num_ctx, 16,384
tokens) is not refused by Ollama 0.40: it is cut to half the context
plus 2 tokens (8,194), keeping the end, so the instructions at the start
are lost. run_batch.py's context_full check (input_tokens close to
num_ctx) does not see this, so these records were "answered" with the
schema forced but without the instructions; their reasons read "The user
provided a long list of ...". An answer whose input_tokens is exactly
num_ctx // 2 + 2 is treated here as failed (failure "prompt_cut"), not as
an answer (run_batch.cut_by_ollama, which run_batch.py itself now uses
when asking). In the full runs the script stops if an answer used another
context size than 16,384, where the rule was seen.

The reruns. prepare_cut_rerun.py made one run folder per model with the
260 records whose prompt was cut in either full run, to be asked with
NUM_CTX=65536. When a rerun folder is there (merged, with its .about.json),
it is checked the same way as a full run (same model, prompt variant and
fingerprint, the same model digest as the full run, exactly those 260
records), and for each of its records its result replaces the full run's:
an answer becomes the record's answer; a record the rerun could not answer
stays failed with the rerun's reason (context_full, bad_json, or not_run
for a record the rerun has not reached). rerun_<model> says the result
came from the rerun; cut_<model> keeps saying the full run's prompt was
cut. If a rerun folder is not there, the script says so and the cut
records stay failed (prompt_cut).

decide() (exploration/llm/score_trial.py, as the Lunaris pipeline uses it)
gives yes or no; rule_of() gives the letter of the rule that answered, a to
i; both are applied by filter2_common.read_trial, which stops if the two
ever disagree.

keep_source:
  both, 4b_only, 35b_only, neither   from the answers there are. A record
                that failed in one model only takes the other model's
                answer (its failure_<model> column says so).
  failed        failed in both models: keep_union is empty, and the record
                is listed in failed_both.csv for a decision.
  lunaris       reuses the Lunaris filter 2 decision (reuse_lunaris true);
                keep_union is that decision, the model columns are empty.
  hand          decided by hand in filter2_hand_decisions.csv: keep_union
                is the file's kept (yes or no), whatever the models said;
                hand_reason is its reason and before_hand the keep_source
                the record had before (failed, 4b_only and so on).

The hand decisions are applied last, over the whole union, as Lunaris
applies pipeline/lunaris/filter2_hand_decisions.csv
(compare_filter2_35b.keep_set): a hand yes is kept and a hand no is left
out, whatever either model said. The file has the same columns and is
read by the same function (compare_filter2_35b.read_hand_decisions, which
stops on a repeated id or a kept other than yes or no). The script stops if
the file names a record that is not in the union. A record that failed in
both models and has a hand decision is no longer failed.

Outputs (data/dataone/filter2_results/, ignored by git; the script stops
if a file is already there unless --replace is given):
  filter2_union.parquet            one row per record (run + Lunaris reuse)
  failed_both.csv                  records that failed in both models
  lunaris_low_similarity_check.csv the DOI or UUID matches to Lunaris with
                                   title similarity below 0.9 (sent to
                                   filter 2), with both answers and titles
and a report printed to the screen.

Run, from backend/:
    ../.venv/bin/python pipeline/dataone/build_filter2_union.py
"""

import argparse
import glob
import json
import os
import sys
from collections import Counter
from urllib.parse import quote

import pandas as pd
import pyarrow.parquet as pq

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(BACKEND, "pipeline", "lunaris"))
sys.path.insert(0, os.path.join(BACKEND, "pipeline", "lunaris", "llm"))
from build_label_set import VIEW  # noqa: E402
from compare_filter2_35b import read_hand_decisions  # noqa: E402
from filter2_common import FILTER2_RUNS, TEST_RUNS, find_trial, id_pattern, read_trial  # noqa: E402
from filter3_checks import excel_safe  # noqa: E402
from match_lunaris import LUNARIS_HARVEST, REUSE_MIN_SIMILARITY, STRONG, dataone_dois  # noqa: E402
from prompt_variants import instructions_sha1  # noqa: E402
from run_batch import cut_by_ollama  # noqa: E402
from run_files import batch_numbers, load_run, read_inputs, valid_results  # noqa: E402

HARVEST = os.path.join(BACKEND, "data", "dataone", "harvest_2026_11_18")
VERSIONS = os.path.join(HARVEST, "dataone_full_harvest_versions.parquet")
GROUPS = os.path.join(HARVEST, "dataone_groups.parquet")
MULTIBOX = os.path.join(HARVEST, "dataone_multibox_groups.parquet")
MATCH = os.path.join(HARVEST, "dataone_lunaris_match.parquet")
NO_TEXT = os.path.join(BACKEND, "data", "dataone", "filter2_input", "no_text_records.csv")
RUN_DIRS = {"4b": os.path.join(FILTER2_RUNS, "fir_dataone_harvest_2026_11_18"),
            "35b": os.path.join(FILTER2_RUNS, "fir_dataone_harvest_2026_11_18_qwen3.6-35b-a3b_nofa")}
# The reruns of the cut prompts, as copied back from Fir (fir_ + the folder name on Fir).
RERUN_DIRS = {"4b": os.path.join(FILTER2_RUNS, "fir_dataone_cut_rerun_2026_11_23"),
              "35b": os.path.join(FILTER2_RUNS, "fir_dataone_cut_rerun_2026_11_23_qwen3.6-35b-a3b_nofa")}
MODELS = {"4b": "qwen3.5:4b", "35b": "qwen3.6:35b-a3b"}
FLASH_ATTENTION = {"35b": "off"}    # the 4b run left it to Ollama (auto), as the Lunaris 4b run did
PROMPT_VARIANT = "v6_dataone"
HAND_CSV = os.path.join(HERE, "filter2_hand_decisions.csv")
OUT_DIR = os.path.join(BACKEND, "data", "dataone", "filter2_results")
OUT_UNION = os.path.join(OUT_DIR, "filter2_union.parquet")
OUT_FAILED = os.path.join(OUT_DIR, "failed_both.csv")
OUT_LOW = os.path.join(OUT_DIR, "lunaris_low_similarity_check.csv")

EXPECTED_RUN = 248_306
EXPECTED_RERUN = 260
EXPECTED_REUSE = 25_089
EXPECTED_NO_TEXT = 677
EXPECTED_TO_FILTER2 = 274_072
CHECKED_NUM_CTX = 16_384            # the only context size the cut rule was seen at
ABSTRACT_START = 300

# Plain names of the groups, in the order the report shows them.
GROUP_NAMES = {"no_box": "no box", "touches_bc_partly": "partial overlap", "west_gt_east": "west > east",
               "inside_bc": "inside BC", "touches_bc_all_inside": "inside BC (all boxes)",
               "sign_check": "sign_check", "send_to_llm": "multi-box, sent to LLM"}
SOURCES = ["both", "4b_only", "35b_only", "neither", "failed", "lunaris", "hand"]

pd.set_option("display.width", 250)
pd.set_option("display.max_colwidth", 70)


# --- Checking the run folders ----------------------------------------------

def stop(message):
    """Stop the script with a message that starts with STOPPED."""
    sys.exit(f"STOPPED: {message}")


def check_run_json(name, run_dir, records):
    """Return run.json after checking model, prompt variant, fingerprint, record count and flash attention."""
    run = load_run(run_dir)
    expected = {"model": MODELS[name], "prompt_variant": PROMPT_VARIANT,
                "instructions_sha1": instructions_sha1(PROMPT_VARIANT), "records": records}
    for key, value in expected.items():
        if run.get(key) != value:
            stop(f"{name}: run.json says {key} = {run.get(key)!r}, not {value!r}")
    if name in FLASH_ATTENTION:
        with open(os.path.join(run_dir, "flash_attention.txt"), encoding="utf-8") as handle:
            setting = handle.readline().strip()
        if setting != FLASH_ATTENTION[name]:
            stop(f"{name}: flash attention was {setting!r}, not {FLASH_ATTENTION[name]!r}")
    return run


def read_run_folder(name, run_dir, run, expected):
    """
    Return the run folder's input ids (in order) and its valid answers
    ({record_id: results line}), read batch by batch with run_files
    exactly as merge_results.py reads them. Stops unless the inputs hold
    expected distinct records.
    """
    ids, answers = [], {}
    for number in batch_numbers(run_dir):
        records = read_inputs(run_dir, number)
        ids.extend(record["record_id"] for record in records)
        answers.update(valid_results(run_dir, number, run, records))
    if len(ids) != expected or len(set(ids)) != expected:
        stop(f"{name}: the inputs hold {len(ids):,} records ({len(set(ids)):,} distinct), not {expected:,}")
    return ids, answers


def check_digest(name, run_dir, answers, about):
    """Stop unless every valid answer comes from one model digest, the one the .about.json names."""
    digests = Counter(line.get("model_digest") for line in answers.values())
    if len(digests) != 1 or about.get("model_digest") not in digests:
        stop(f"{name}: answers come from digests {dict(digests)}, the .about.json names {about.get('model_digest')}")
    return about["model_digest"]


def read_merged(name, run_dir, ids, answers, complete):
    """
    Return the run's merged CSV with decide()'s verdict and rule_of()'s rule
    for each answered record, after checking it against the run folder: the
    same records as the inputs, and the same answered records as results/.
    read_trial (filter2_common.py) leaves out the failed rows and stops if
    rule_of() and decide() disagree. With complete, every record must have
    been run (no "not run yet" rows); a rerun may be merged part way
    (merge_results.py --allow-incomplete).
    """
    path, _ = find_trial(run_dir)
    all_ids = pd.read_csv(path, encoding="utf-8-sig", usecols=["record_id"], dtype=str)["record_id"]
    if list(all_ids) != ids:
        stop(f"{name}: {os.path.basename(path)} does not hold the run's input records in their order")
    answered, failed = read_trial(path)
    if set(answered["record_id"]) != set(answers):
        stop(f"{name}: the merged CSV's answered records are not the valid answers in results/")
    with open(path[:-len(".csv")] + ".about.json", encoding="utf-8") as handle:
        about = json.load(handle)
    if (about["answered"], about["failed"] + about["not_run"]) != (len(answered), failed):
        stop(f"{name}: the .about.json counts do not match the merged CSV")
    if complete and about["not_run"]:
        stop(f"{name}: {about['not_run']} records have not been run")
    return path, answered.set_index("record_id"), about


def read_run(name, run_dir, expected, full=True):
    """
    Read one run folder, check it, and return (table, facts): table has one
    row per input record (indexed by id) with answer (yes, no or failed),
    rule, failure (bad_json, prompt_cut, context_full or another error kind;
    not_run for a record a part-merged rerun has not reached; empty when
    answered), input_tokens and cut (the prompt was cut by Ollama); facts is
    what the report says about the run. full is True for the full runs,
    which must be complete and answered at a 16,384 token context only.
    """
    run = check_run_json(name, run_dir, expected)
    ids, answers = read_run_folder(name, run_dir, run, expected)
    path, answered, about = read_merged(name, run_dir, ids, answers, complete=full)
    digest = check_digest(name, run_dir, answers, about)
    sizes = {line.get("num_ctx") for line in answers.values()}
    if full and sizes != {CHECKED_NUM_CTX}:
        stop(f"{name}: answers used context sizes {sorted(sizes)}; the cut rule was only checked at {CHECKED_NUM_CTX}")
    cut = {i for i, line in answers.items() if cut_by_ollama(line.get("input_tokens"), line.get("num_ctx"))}
    errors = last_error_kinds(run_dir)
    table = pd.DataFrame({"id": ids})
    table["answer"] = table["id"].map(answered["verdict"]).fillna("failed")
    table["rule"] = table["id"].map(answered["rule"]).fillna("")
    table["failure"] = table["id"].map(errors).fillna("")
    table.loc[table["id"].isin(cut), ["answer", "rule", "failure"]] = ["failed", "", "prompt_cut"]
    if not full:
        # A record a part-merged rerun has not reached has no error line yet.
        table.loc[(table["answer"] == "failed") & (table["failure"] == ""), "failure"] = "not_run"
    if ((table["answer"] == "failed") != (table["failure"] != "")).any():
        stop(f"{name}: a failed record has no error line, or an answered one has one")
    table["input_tokens"] = table["id"].map({i: line.get("input_tokens") for i, line in answers.items()})
    table["cut"] = table["id"].isin(cut)
    facts = {"file": os.path.relpath(path, BACKEND), "model": run["model"], "digest": digest,
             "answered": len(answered), "bad answers": len(ids) - len(answered), "prompt_cut": len(cut),
             "num_ctx": sorted(sizes), "failures": dict(Counter(table.loc[table["failure"] != "", "failure"]))}
    return table.set_index("id"), facts


def model_answers(name):
    """Read and check one model's full run; return (table, facts) as read_run does."""
    return read_run(name, RUN_DIRS[name], EXPECTED_RUN)


def rerun_state(run_dir):
    """
    Return why a rerun folder cannot be read yet, or None if it can: it must
    be there, with run.json, one merged trial CSV and its .about.json.
    """
    if not os.path.isdir(run_dir):
        return "not there yet"
    if not os.path.exists(os.path.join(run_dir, "run.json")):
        return "has no run.json"
    trials = glob.glob(os.path.join(run_dir, "trial_*.csv"))
    if not trials:
        return "not merged yet (no trial_*.csv; run merge_results.py on Fir and copy it back)"
    if len(trials) == 1 and not os.path.exists(trials[0][:-len(".csv")] + ".about.json"):
        return "has a merged CSV but no .about.json"
    return None


def read_rerun(name, cut_ids, full_digest):
    """
    Return (table, facts) for one model's rerun, or (None, reason) if its
    folder cannot be read yet. Stops if the rerun is there but is not a
    rerun of the cut records: run.json must name the full run in rerun_of,
    the records must be exactly cut_ids (the prompts cut in either full
    run), and the answers must come from the full run's model digest.
    """
    run_dir = RERUN_DIRS[name]
    reason = rerun_state(run_dir)
    if reason:
        return None, reason
    run = load_run(run_dir)
    if not str((run.get("rerun_of") or {}).get("run", "")).endswith("dataone_harvest_2026_11_18"):
        stop(f"{name}: {run_dir} is not a rerun of dataone_harvest_2026_11_18 (run.json has no rerun_of)")
    table, facts = read_run(name, run_dir, len(cut_ids), full=False)
    if set(table.index) != set(cut_ids):
        stop(f"{name}: the rerun does not hold exactly the {len(cut_ids)} records cut in the full runs")
    if facts["digest"] != full_digest:
        stop(f"{name}: the rerun's model digest {facts['digest'][:12]} is not the full run's {full_digest[:12]}")
    return table, facts


def apply_rerun(full, rerun):
    """
    Return a copy of one model's full run table with the rerun's result
    (answer, rule, failure, input_tokens) in place of the full run's for
    every record the rerun holds, and rerun True for those records. The
    full run's cut column is kept. With no rerun (None) the table comes
    back unchanged, with rerun False everywhere.
    """
    out = full.copy()
    out["rerun"] = False
    if rerun is None:
        return out
    ids = rerun.index
    if not set(ids) <= set(out.index):
        stop("the rerun holds records that are not in the full run")
    for column in ("answer", "rule", "failure", "input_tokens"):
        out.loc[ids, column] = rerun[column]
    out.loc[ids, "rerun"] = True
    return out


def last_error_kinds(run_dir):
    """Return {record_id: kind} of each record's last error line in errors/ (bad_json and so on)."""
    kinds = {}
    for path in sorted(os.listdir(os.path.join(run_dir, "errors"))):
        with open(os.path.join(run_dir, "errors", path), encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    error = json.loads(line)
                    kinds[error["record_id"]] = error.get("kind") or "error"
    return kinds


# --- The union --------------------------------------------------------------

def keep_of(answer):
    """Return True for yes, False for no and None (unknown) for failed."""
    return {"yes": True, "no": False}.get(answer)


def source_of(keep_4b, keep_35b):
    """Return keep_source from the two models' keep values (None = that model failed)."""
    if keep_4b is None and keep_35b is None:
        return "failed"
    if keep_4b and keep_35b:
        return "both"
    if keep_4b:
        return "4b_only"
    if keep_35b:
        return "35b_only"
    return "neither"


def read_context():
    """
    Return the groups, multi-box ids, Lunaris match table and no-text ids,
    after checking the counts: reuse records, run records and no-text
    records must together be exactly the records sent to filter 2.
    """
    groups = pd.read_parquet(GROUPS)
    groups = groups[groups["to_filter2"]].set_index("id")
    if len(groups) != EXPECTED_TO_FILTER2:
        stop(f"{len(groups):,} records go to filter 2 in the groups file, not {EXPECTED_TO_FILTER2:,}")
    multibox = set(pd.read_parquet(MULTIBOX, columns=["id"])["id"])
    match = pd.read_parquet(MATCH).set_index("id")
    no_text = set(pd.read_csv(NO_TEXT, dtype=str, usecols=["id"])["id"])
    if int(match["reuse_lunaris"].sum()) != EXPECTED_REUSE or len(no_text) != EXPECTED_NO_TEXT:
        stop("the reuse or no-text counts are not the expected ones")
    return groups, multibox, match, no_text


def build_union(answers, groups, multibox, match, no_text):
    """
    Return the union table: one row per run record and per Lunaris reuse
    record, with id, datasource, group, multibox, both models' answers,
    rules and failures, keep_4b, keep_35b, keep_union and keep_source, the
    Lunaris match fields, and at the end cut_<model> (the full run's prompt
    was cut) and rerun_<model> (the result comes from the rerun), false
    for the Lunaris reuse records.
    """
    a4, a35 = answers["4b"], answers["35b"]
    if list(a4.index) != list(a35.index):
        stop("the two runs do not hold the same records in the same order")
    run_ids = set(a4.index)
    reuse_ids = set(match.index[match["reuse_lunaris"]])
    if run_ids & reuse_ids or run_ids & no_text or reuse_ids & no_text:
        stop("a record is in more than one of: the runs, the Lunaris reuse, the no-text list")
    if run_ids | reuse_ids | no_text != set(groups.index):
        stop("the runs, the Lunaris reuse and the no-text list are not exactly the records sent to filter 2")

    run = pd.DataFrame(index=a4.index)
    for name, table in answers.items():
        run[f"answer_{name}"] = table["answer"]
        run[f"rule_{name}"] = table["rule"]
        run[f"failure_{name}"] = table["failure"]
        run[f"keep_{name}"] = table["answer"].map(keep_of)
        run[f"cut_{name}"] = table["cut"]
        run[f"rerun_{name}"] = table["rerun"]
    run["keep_source"] = [source_of(k4, k35) for k4, k35 in zip(run["keep_4b"], run["keep_35b"])]
    run["keep_union"] = run["keep_source"].map(lambda s: None if s == "failed" else s != "neither")

    reuse = pd.DataFrame(index=sorted(reuse_ids))
    decisions = match.loc[reuse.index, "lunaris_filter2_summary"]
    if not decisions.isin(["yes", "no"]).all():
        stop("a Lunaris reuse record has no single yes or no decision")
    reuse["keep_union"] = decisions.map({"yes": True, "no": False})
    reuse["keep_source"] = "lunaris"

    union = pd.concat([run, reuse])
    union.index.name = "id"
    union = union.reset_index()
    union["datasource"] = union["id"].map(groups["datasource"])
    union["group"] = union["id"].map(groups["group"])
    union["multibox"] = union["id"].isin(multibox)
    union["reuse_lunaris"] = union["id"].map(match["reuse_lunaris"]).fillna(False).astype(bool)
    union["match_type"] = union["id"].map(match["match_type"]).fillna("none")
    union["title_similarity"] = union["id"].map(match["title_similarity"])
    union["lunaris_decision"] = union["id"].map(match["lunaris_filter2_summary"]).fillna("")
    for column in ("keep_4b", "keep_35b", "keep_union"):
        union[column] = union[column].astype("boolean")
    for column in ("answer_4b", "rule_4b", "failure_4b", "answer_35b", "rule_35b", "failure_35b"):
        union[column] = union[column].fillna("")
    columns = ["id", "datasource", "group", "multibox", "answer_4b", "rule_4b", "failure_4b", "answer_35b",
               "rule_35b", "failure_35b", "keep_4b", "keep_35b", "keep_union", "keep_source", "reuse_lunaris",
               "match_type", "title_similarity", "lunaris_decision", "cut_4b", "rerun_4b", "cut_35b", "rerun_35b",
               "hand_reason", "before_hand"]
    # Filled in by apply_hand.
    union["hand_reason"] = ""
    union["before_hand"] = ""
    for column in ("cut_4b", "rerun_4b", "cut_35b", "rerun_35b"):
        union[column] = union[column].fillna(False).astype(bool)
    return union[columns]


# --- Hand decisions -----------------------------------------------------------

def apply_hand(union, hand):
    """
    Return a copy of the union with the hand decisions applied: for each
    record in hand (read_hand_decisions' table, indexed by record id),
    keep_union becomes its kept, keep_source "hand", hand_reason its reason
    and before_hand the keep_source it had. The model columns are left as
    the models answered. Stops if a hand decision names a record that is
    not in the union.
    """
    out = union.copy()
    unknown = sorted(set(hand.index) - set(out["id"]))
    if unknown:
        stop(f"{len(unknown)} hand decisions in {os.path.relpath(HAND_CSV, BACKEND)} name records that are not "
             f"in the union, for example {unknown[:3]}")
    rows = out["id"].isin(hand.index)
    decided = out.loc[rows, "id"]
    out.loc[rows, "before_hand"] = out.loc[rows, "keep_source"]
    out.loc[rows, "keep_union"] = decided.map(hand["kept"]).map({"yes": True, "no": False}).astype("boolean")
    out.loc[rows, "keep_source"] = "hand"
    out.loc[rows, "hand_reason"] = decided.map(hand["reason"])
    return out


# --- Writing ----------------------------------------------------------------

def read_texts(ids):
    """Return title, abstract and seriesId of the given latest records, indexed by id."""
    table = pq.read_table(VERSIONS, columns=["id", "seriesId", "title", "abstract", "version_status"],
                          filters=[("id", "in", sorted(ids))]).to_pandas()
    table = table[table["version_status"] == "latest"].drop_duplicates("id").set_index("id")
    if set(ids) - set(table.index):
        stop("some records are not in the versions file")
    return table


def one_line(value, limit=None):
    """Return text on one line (whitespace collapsed), cut to limit characters if given."""
    text = " ".join(str(value).split()) if isinstance(value, str) else ""
    return text[:limit] if limit else text


def write_csv(frame, path):
    """Write a CSV that Excel opens (utf-8 with BOM, excel_safe), through a temporary file."""
    counter = Counter()
    excel_safe(frame, counter).to_csv(path + ".tmp", index=False, encoding="utf-8-sig")
    os.replace(path + ".tmp", path)


def next_step(failure_4b, failure_35b):
    """
    Return what to do with a record that failed in both models, from the two
    failures: ask again with a larger context when a prompt was too long,
    finish the rerun when it has not reached the record, otherwise decide
    by hand.
    """
    failures = {failure_4b, failure_35b}
    if "prompt_cut" in failures:
        return "rerun with NUM_CTX=65536 (prompt was cut)"
    if "context_full" in failures:
        return "rerun with a larger context (context_full in the rerun)"
    if "not_run" in failures:
        return "finish the rerun"
    return "hand decision"


def failed_both(union):
    """
    Return the records that failed in both models and have no hand
    decision, with title, abstract start, link and what to do next. With
    none, an empty table with the same columns (written as a header only).
    """
    columns = ["id", "datasource", "group", "failure_4b", "failure_35b", "next_step", "title", "abstract_start", "link"]
    failed = union[union["keep_source"] == "failed"].copy()
    if failed.empty:
        return pd.DataFrame(columns=columns)
    texts = read_texts(failed["id"])
    failed["title"] = failed["id"].map(lambda i: one_line(texts.at[i, "title"]))
    failed["abstract_start"] = failed["id"].map(lambda i: one_line(texts.at[i, "abstract"], ABSTRACT_START))
    failed["link"] = VIEW + failed["id"].map(lambda i: quote(i, safe=""))
    failed["next_step"] = [next_step(f4, f35) for f4, f35 in zip(failed["failure_4b"], failed["failure_35b"])]
    return failed[columns]


def write_outputs(union, failed, low, replace):
    """Write the union parquet and the two CSVs, after checking none is there yet (unless replace)."""
    os.makedirs(OUT_DIR, exist_ok=True)
    there = [p for p in (OUT_UNION, OUT_FAILED, OUT_LOW) if os.path.exists(p)]
    if there and not replace:
        stop(f"{os.path.relpath(there[0], BACKEND)} is already there; add --replace to write over it")
    union.to_parquet(OUT_UNION + ".tmp", index=False)
    os.replace(OUT_UNION + ".tmp", OUT_UNION)
    write_csv(failed, OUT_FAILED)
    write_csv(low, OUT_LOW)
    for path, n in ((OUT_UNION, len(union)), (OUT_FAILED, len(failed)), (OUT_LOW, len(low))):
        print(f"wrote {n:,} rows -> {os.path.relpath(path, BACKEND)}")


# --- Report -----------------------------------------------------------------

def report_counts(union, facts):
    """Print each run's facts and how many records each model, both, one and the union keep."""
    print("\nRUNS")
    for name, f in facts.items():
        print(f"   {name}: {f['file']}\n       model {f['model']}, digest {f['digest'][:12]}; answered {f['answered']:,}, "
              f"bad answers {f['bad answers']}, prompts cut by Ollama {f['prompt_cut']}")
    # The models' own result: before_hand for a record decided by hand.
    model_source = union["before_hand"].where(union["keep_source"] == "hand", union["keep_source"])
    run = union[model_source != "lunaris"].assign(model_source=model_source)
    lunaris = union[model_source == "lunaris"]
    hand = run[run["keep_source"] == "hand"]
    rows = [("kept by 4b", int(run["keep_4b"].sum())), ("kept by 35b", int(run["keep_35b"].sum())),
            ("kept by both", int((run["model_source"] == "both").sum())),
            ("kept by 4b only", int((run["model_source"] == "4b_only").sum())),
            ("kept by 35b only", int((run["model_source"] == "35b_only").sum())),
            ("kept by neither", int((run["model_source"] == "neither").sum())),
            ("failed in both", int((run["model_source"] == "failed").sum())),
            ("  of them: prompt cut in at least one", int(((run["model_source"] == "failed") & (
                (run["failure_4b"] == "prompt_cut") | (run["failure_35b"] == "prompt_cut"))).sum())),
            ("decided by hand: kept", int(hand["keep_union"].sum())),
            ("decided by hand: not kept", int((~hand["keep_union"].astype(bool)).sum())),
            ("  of them: failed in both before", int((hand["before_hand"] == "failed").sum())),
            ("  of them: overriding a model yes", int(hand["before_hand"].isin(["both", "4b_only", "35b_only"]).sum())),
            ("failed in both, no hand decision", int((run["keep_source"] == "failed").sum())),
            ("failed in 4b only (35b answer used)", int(((run["answer_4b"] == "failed") & (run["answer_35b"] != "failed")).sum())),
            ("failed in 35b only (4b answer used)", int(((run["answer_35b"] == "failed") & (run["answer_4b"] != "failed")).sum())),
            ("union kept, filter 2 records", int(run["keep_union"].sum())),
            ("Lunaris reuse records", len(lunaris)), ("  of them kept", int(lunaris["keep_union"].sum())),
            ("union kept, all", int(union["keep_union"].sum()))]
    print(f"\n1. COUNTS ({len(run):,} filter 2 records, {len(lunaris):,} reusing Lunaris)")
    print(pd.DataFrame(rows, columns=["what", "records"]).to_string(index=False))


def report_reruns(union, reruns):
    """
    Print, for each model, whether its rerun of the cut prompts was read and
    what it gave: how many of its records it answered, the failures left,
    and how the union now stands on the records whose prompt was cut.
    """
    print("\nRERUNS OF THE CUT PROMPTS")
    for name, info in reruns.items():
        folder = os.path.relpath(RERUN_DIRS[name], BACKEND)
        if isinstance(info, str):
            print(f"   {name}: {folder} {info}; its {int(union[f'cut_{name}'].sum())} cut records stay failed "
                  f"(prompt_cut)")
            continue
        used = union[union[f"rerun_{name}"]]
        answered = used[used[f"answer_{name}"] != "failed"]
        print(f"   {name}: {info['file']}\n       digest {info['digest'][:12]} (the full run's), context "
              f"{info['num_ctx']}; answered {len(answered)} of {len(used)} "
              f"(yes {int((answered[f'answer_{name}'] == 'yes').sum())}, "
              f"no {int((answered[f'answer_{name}'] == 'no').sum())}); still failing: {info['failures'] or 'none'}")
    cut = union[union["cut_4b"] | union["cut_35b"]]
    print(f"   the {len(cut)} records cut in either full run now: {cut['keep_source'].value_counts().to_dict()}")


def report_groups(union):
    """Print the union's kept records by group (with multi-box split out) and by datasource (top 15)."""
    kept = union[union["keep_union"].fillna(False)].copy()
    kept["group_name"] = kept["group"].map(GROUP_NAMES).fillna(kept["group"])
    kept["from"] = kept["reuse_lunaris"].map({True: "lunaris", False: "filter2"})
    table = pd.crosstab([kept["group_name"], kept["multibox"].map({True: "multi-box", False: "one box or none"})],
                        kept["from"], margins=True, margins_name="total")
    all_records = union.assign(group_name=union["group"].map(GROUP_NAMES))
    sent = all_records.groupby(["group_name", all_records["multibox"].map(
        {True: "multi-box", False: "one box or none"})]).size()
    table["of records in group"] = [sent.get(i, len(union)) if i[0] != "total" else len(union) for i in table.index]
    print("\n2a. UNION KEPT, BY GROUP")
    print(table.to_string())
    print("\n2b. UNION KEPT, BY DATASOURCE (top 15)")
    by_source = pd.crosstab(kept["datasource"], kept["from"])
    by_source["total"] = by_source.sum(axis=1)
    by_source["of records"] = union.groupby("datasource").size().reindex(by_source.index)
    print(by_source.sort_values("total", ascending=False).head(15).to_string())


def report_test_runs(union):
    """Print how often the full runs give the 200 label set records the same answers as the test runs."""
    test = {}
    for name, run_dir in TEST_RUNS.items():
        path, _ = find_trial(run_dir)
        frame, failed = read_trial(path)
        if failed:
            stop(f"the {name} test run has failed records")
        test[name] = frame.set_index("record_id")["verdict"]
    ids = sorted(test["4b"].index)
    full = union.set_index("id").reindex(ids)
    in_run = full["keep_source"].notna() & ~full["reuse_lunaris"].fillna(False).astype(bool)
    print(f"\n3. RUN TO RUN: THE {len(ids)} LABEL SET RECORDS ({int(in_run.sum())} in the full runs, "
          f"{int(full['reuse_lunaris'].fillna(False).astype(bool).sum())} reuse Lunaris)")
    full = full[in_run]
    test_union = ((test["4b"] == "yes") | (test["35b"] == "yes")).map({True: "yes", False: "no"}).reindex(full.index)
    full_union = full["keep_union"].map({True: "yes", False: "no"})
    rows = []
    for name, ours, theirs in (("4b", full["answer_4b"], test["4b"].reindex(full.index)),
                               ("35b", full["answer_35b"], test["35b"].reindex(full.index)),
                               ("union", full_union, test_union)):
        rows.append({"model": name, "same": int((ours == theirs).sum()), "different": int((ours != theirs).sum()),
                     "of": len(full)})
    print(pd.DataFrame(rows).to_string(index=False))
    differ = full.index[(full["answer_4b"] != test["4b"].reindex(full.index))
                        | (full["answer_35b"] != test["35b"].reindex(full.index))]
    for i in differ:
        print(f"   {i[:60]}: 4b test {test['4b'][i]} full {full.at[i, 'answer_4b']}; "
              f"35b test {test['35b'][i]} full {full.at[i, 'answer_35b']}")


def lunaris_titles(match_row, titles):
    """Return the DOI or UUID matched Lunaris records of one match row as "decision | id | title" lines."""
    lines = []
    for lid, way, decision in zip(match_row["lunaris_ids"], match_row["lunaris_match_by"], match_row["lunaris_filter2"]):
        if any(kind in way for kind in STRONG):
            lines.append(f"{decision} | {lid} | {one_line(titles.get(lid, ''))}")
    return lines


def low_similarity(union, match):
    """
    Return the DOI or UUID matches with title similarity below 0.9 (sent to
    filter 2 instead of reusing Lunaris), with both answers, the union, the
    Lunaris decision and both titles, and print how often they agree.
    """
    low = union[union["match_type"].isin(STRONG) & ~union["reuse_lunaris"]].copy()
    rows = match.loc[low["id"]]
    lunaris_ids = sorted({i for ids in rows["lunaris_ids"] for i in ids})
    titles = pd.read_parquet(LUNARIS_HARVEST, columns=["id", "title"], filters=[("id", "in", lunaris_ids)])
    titles = titles.drop_duplicates("id").set_index("id")["title"].to_dict()
    texts = read_texts(low["id"])
    low["dataone_title"] = low["id"].map(lambda i: one_line(texts.at[i, "title"]))
    low["lunaris_matches"] = [" || ".join(lunaris_titles(match.loc[i], titles)) for i in low["id"]]
    low["union"] = low["keep_union"].map({True: "yes", False: "no"}).fillna("failed")
    low["link"] = VIEW + low["id"].map(lambda i: quote(i, safe=""))
    print(f"\n4. DOI OR UUID MATCHES WITH TITLE SIMILARITY BELOW {REUSE_MIN_SIMILARITY}: {len(low)} records")
    print("   Lunaris decision:", low["lunaris_decision"].value_counts().to_dict())
    decided = low[low["lunaris_decision"].isin(["yes", "no"])]
    rows_out = []
    for name, column in (("4b", "answer_4b"), ("35b", "answer_35b"), ("union", "union")):
        answered = decided[decided[column] != "failed"]
        same = int((answered[column] == answered["lunaris_decision"]).sum())
        rows_out.append({"answer": name, "agrees": same, "disagrees": len(answered) - same, "of": len(answered)})
    print(pd.DataFrame(rows_out).to_string(index=False))
    print(pd.crosstab(decided["lunaris_decision"].rename("Lunaris"), decided["union"].rename("union")).to_string())
    disagree = decided[decided["union"] != decided["lunaris_decision"]].sort_values("title_similarity")
    print(f"\n   every disagreement ({len(disagree)}), lowest similarity first:")
    for r in disagree.to_dict("records"):
        print(f"   - {r['id'][:70]} | {r['datasource']} | {r['match_type']} | similarity {r['title_similarity']:.2f}")
        print(f"     union {r['union']} (4b {r['answer_4b']}, 35b {r['answer_35b']}); Lunaris {r['lunaris_decision']}")
        print(f"     DataONE: {r['dataone_title'][:150]}")
        for line in r["lunaris_matches"].split(" || "):
            print(f"     Lunaris: {line[:170]}")
    mixed = low[~low["lunaris_decision"].isin(["yes", "no"])]
    for r in mixed.to_dict("records"):
        print(f"   (Lunaris decision {r['lunaris_decision']!r}) {r['id'][:70]}: union {r['union']}")
    columns = ["id", "datasource", "group", "match_type", "title_similarity", "lunaris_decision", "answer_4b",
               "answer_35b", "union", "dataone_title", "lunaris_matches", "link"]
    return low[columns].sort_values("title_similarity")


def report_pangaea_pairs(union):
    """
    Print how often the union gives the same answer to both copies of a
    PANGAEA pair: a DOI held by exactly two latest PANGAEA records, one
    with a 32-hex id and one with a sha256 id, both sent to the models.
    """
    table = pq.read_table(VERSIONS, columns=["id", "seriesId", "datasource", "version_status"]).to_pandas()
    table = table[(table["version_status"] == "latest") & (table["datasource"].str.endswith("PANGAEA"))]
    table["pattern"] = table["id"].map(id_pattern)
    table["dois"] = [sorted(dataone_dois(i, s)) for i, s in zip(table["id"], table["seriesId"])]
    by_doi = table[["id", "pattern", "dois"]].explode("dois").dropna(subset=["dois"]).drop_duplicates(["id", "dois"])
    shape = by_doi.groupby("dois")["pattern"].agg(lambda p: tuple(sorted(p)))
    pair_dois = shape.index[shape == ("hex32", "sha256")]
    pairs = by_doi[by_doi["dois"].isin(pair_dois)].pivot(index="dois", columns="pattern", values="id")
    pairs = pairs.drop_duplicates()
    answer = union.set_index("id")
    in_run = answer.index[~answer["reuse_lunaris"]]
    both = pairs[pairs["hex32"].isin(in_run) & pairs["sha256"].isin(in_run)]
    hex_keep = answer.loc[both["hex32"], "keep_union"].to_numpy()
    sha_keep = answer.loc[both["sha256"], "keep_union"].to_numpy()
    known = ~(pd.isna(hex_keep) | pd.isna(sha_keep))
    same = int((hex_keep[known] == sha_keep[known]).sum())
    print(f"\n5. PANGAEA PAIRS (one DOI, one 32-hex id and one sha256 id): {len(pairs):,}; "
          f"both copies sent to the models: {len(both):,}")
    print(f"   union answer the same for both copies: {same:,} of {int(known.sum()):,}; "
          f"different: {int(known.sum()) - same:,}; a copy failed: {int((~known).sum())}")
    if len(both):
        rows = pd.crosstab(pd.Series(hex_keep[known], name="hex32 kept"), pd.Series(sha_keep[known], name="sha256 kept"))
        print(rows.to_string())


def main():
    """Read and check both full runs and any reruns, build the union, write it and print the report."""
    parser = argparse.ArgumentParser(description="Build DataONE filter 2's union keep set.")
    parser.add_argument("--replace", action="store_true", help="write over earlier outputs")
    parser.add_argument("--hand", default=HAND_CSV, help="the hand decisions (filter2_hand_decisions.csv)")
    args = parser.parse_args()
    answers, facts, reruns = {}, {}, {}
    for name in RUN_DIRS:
        answers[name], facts[name] = model_answers(name)
    # The records whose prompt was cut in either full run: the ones the reruns ask again.
    cut_ids = {i for table in answers.values() for i in table.index[table["cut"]]}
    if len(cut_ids) != EXPECTED_RERUN:
        stop(f"{len(cut_ids)} records were cut in the full runs, not {EXPECTED_RERUN}")
    for name in RUN_DIRS:
        rerun, reruns[name] = read_rerun(name, cut_ids, facts[name]["digest"])
        answers[name] = apply_rerun(answers[name], rerun)
    groups, multibox, match, no_text = read_context()
    union = build_union(answers, groups, multibox, match, no_text)
    union = apply_hand(union, read_hand_decisions(args.hand))
    failed = failed_both(union)
    report_counts(union, facts)
    report_reruns(union, reruns)
    report_groups(union)
    report_test_runs(union)
    low = low_similarity(union, match)
    report_pangaea_pairs(union)
    print()
    write_outputs(union, failed, low, args.replace)
    return 0


if __name__ == "__main__":
    sys.exit(main())
