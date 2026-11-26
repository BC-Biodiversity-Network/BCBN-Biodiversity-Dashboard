"""
score_filter3_test.py

Score DataONE filter 3 on the labelled records, and measure the label test
run's speed to plan the full run. A measurement only: nothing in pipeline/,
the run folder or the inputs is changed, and pipeline code never imports
this file.

What it scores, against three label sets (build_filter3_rules.LABELS):
  - the first label set, lucia_bc_class_final;
  - the BC set, first 50, bc_class (Claude's labels, delegated by Lucia);
  - the BC set, strict 50, bc_class (the same).
Labels "unsure" are skipped and counted; an empty label (a record the
first set never labelled for place) is not a label.

Each labelled record kept by filter 2 has one of three routes in
filter3_rules.parquet (build_filter3_rules.py):
  llm      the answer of the label test run on Fir (its merged files);
  rule     the class the Lunaris rules gave (bc_only or elsewhere);
  lunaris  the class of its Lunaris copy.
The measures and their definitions are those of score() in
pipeline/lunaris/filter3_checks.py:
  exact class      the model's class is the label;
  "includes BC"    a class in geo_pass2_score.SHOWN (bc_only,
                   canada_incl_bc, all_canada, bc_and_outside_canada,
                   global); elsewhere and unknown do not;
  missed BC        the label includes BC, the model's class does not
                   (score()'s hidden_wrongly);
  wrongly BC       the label does not include BC, the model's class does
                   (score()'s shown_wrongly);
  front end group  build_filter3_final.FRONTEND_GROUP: bc_only,
                   canada_incl_bc, bc_and_outside_canada go to bc_data;
                   all_canada, global to pending; elsewhere, unknown to
                   dropped. For the model's answer, as in
                   build_filter3_final.set_frontend_groups, an LLM
                   bc_and_outside_canada without in_bc yes goes to pending
                   (national plus outside, no BC named); a Lunaris-route
                   record takes its Lunaris copy's front end group. The
                   label's group is its class's.

It writes, to ~/Desktop/BCBN/Results/dataone_filter3_test_2026-11-23/:
  filter3_test_scores.csv         one row per label set and route, with the
                                  counts behind each rate
  filter3_test_disagreements.csv  one row per record and label set where
                                  the model's class or group differs from
                                  the label's
  filter3_test_timing.csv         seconds per record of the first job, by
                                  group and by repository, without the
                                  warm-up record
and prints the scores, the timing and the full run estimate.

Run, from backend/:
    ../.venv/bin/python exploration/dataone/score_filter3_test.py
"""

import argparse
import gzip
import json
import math
import os
import re
import sys
from datetime import datetime
from urllib.parse import quote

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, "..", ".."))
for folder in ("pipeline/dataone", "pipeline/lunaris", "pipeline/lunaris/llm"):
    sys.path.insert(0, os.path.join(BACKEND, folder))
import geo_prompt_variants  # noqa: E402
from bc_filter_rules import load_bc_shape  # noqa: E402
from build_filter3_final import FRONTEND_GROUP, GROUPS  # noqa: E402
from build_filter3_rules import LABELS, OUT as RULES  # noqa: E402
from build_label_set import VIEW  # noqa: E402
from classify_multibox_records import BC_BOUNDARY  # noqa: E402
from filter3_checks import score  # noqa: E402
from geo_filter_run import read_input, read_jsonl  # noqa: E402
from geo_pass2_score import SHOWN  # noqa: E402

RUN = os.path.join(BACKEND, "data", "dataone", "filter3_runs", "dataone_label_test_qwen3.6-35b-a3b_nofa")
TEST_INPUT = os.path.join(BACKEND, "data", "dataone", "filter3_input", "dataone_label_test")
FULL_INPUT = os.path.join(BACKEND, "data", "dataone", "filter3_input", "dataone_needs_llm")
LUNARIS_MERGED = os.path.join(BACKEND, "data", "lunaris_bc_filter", "filter3_final", "filter3_merged.csv")
OUT_DIR = os.path.expanduser("~/Desktop/BCBN/Results/dataone_filter3_test_2026-11-23")

# Labelled records to look at again once the full abstracts exist (Lucia, 2026-11-23),
# by label set and row. These are the rows marked in recheck_with_full_abstract.
RECHECK = {"BC set, first 50": {2, 8, 36, 44}, "BC set, strict 50": {14, 17, 25}}
RECHECK_MARK = "label to recheck once full abstracts exist"
ROUTES = ["llm", "rule", "lunaris"]
ALL_SETS = "all label sets"
FACTS = ["in_bc", "other_canada", "outside_canada", "canada_wide", "global"]

# The full run's records per group (filter3_rules.parquet, route llm), as Lucia gave them.
FULL_GROUP_NAMES = {"no_box": "no box", "touches_bc_partly": "needs_llm", "west_gt_east": "west > east",
                    "send_to_llm": "multi-box send_to_llm", "sign_check": "sign_check"}
# The job time limit is 3 hours; aim each part at about 2 hours.
TARGET_HOURS = 2.0
# The number of prompt-length bins for the estimate by length.
LENGTH_BINS = 6


# --- Labels -------------------------------------------------------------------

def read_labels():
    """
    Return the labels as one table (label_set, row, id, label, recheck) and,
    per label set, how many rows it has, how many carry a place label and
    how many of those are unsure (skipped).
    """
    rows, counts = [], {}
    for name, path, column in LABELS:
        table = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
        labelled = table[table[column].str.strip() != ""]
        unsure = labelled[column].str.strip() == "unsure"
        counts[name] = {"rows": len(table), "labelled": len(labelled), "unsure": int(unsure.sum())}
        for _, row in labelled[~unsure].iterrows():
            label = row[column].strip()
            if label not in FRONTEND_GROUP:
                sys.exit(f"STOPPED: {name} row {row['row']} has the label {label!r}, not one of the classes")
            rows.append({"label_set": name, "row": int(row["row"]), "id": row["id"], "label": label,
                         "recheck": RECHECK_MARK if int(row["row"]) in RECHECK.get(name, set()) else ""})
    return pd.DataFrame(rows), counts


def label_conflicts(labels):
    """Return the records labelled in more than one set whose labels differ, one row per record and set."""
    sets = labels.groupby("id")["label"].nunique()
    return labels[labels["id"].isin(sets[sets > 1].index)].sort_values(["id", "label_set"])


# --- The model's answers ----------------------------------------------------------

def place_text(places):
    """Return pass 1's places as one line: name (role), separated by semicolons."""
    return "; ".join(f"{p['name']} ({p['role']})" for p in places or [])


def lookup_text(results):
    """Return the GeoNames results of one record as one line: each place, what was found and how."""
    parts = []
    for result in results:
        s = result["summary"]
        parts.append(f"{result['query']['name']}: {s['category_text']} ({s['places_found']} found, {s['match']})")
    return "; ".join(parts)


def pass2_text(answer):
    """Return pass 2's answer as one line: the five facts, the reason and the evidence."""
    facts = ", ".join(f"{k} {answer[k]}" for k in FACTS)
    evidence = " | ".join(answer.get("evidence") or [])
    return f"{facts}. Reason: {answer.get('reason', '')} Evidence: {evidence}"


def llm_answers():
    """
    Return the label test run's merged answers as a table by record id:
    the model's class and front end group, and the pass 1 places, the
    GeoNames matches and the pass 2 answer as text. Stops if a record has
    no pass 2 answer.
    """
    pass1 = {line["record_id"]: line for line in read_jsonl(os.path.join(RUN, "pass1", "answers.jsonl"))}
    pass2 = read_jsonl(os.path.join(RUN, "pass2", "answers.jsonl"))
    lookups = {}
    for result in read_jsonl(os.path.join(RUN, "pass1", "lookup.jsonl")):
        lookups.setdefault(result["record"], []).append(result)
    rows = []
    for line in pass2:
        if "answer" not in line:
            sys.exit(f"STOPPED: record [{line['number']}] has no pass 2 answer in the merged files")
        decision = line["decision"]
        group = FRONTEND_GROUP[decision["class"]]
        if decision["class"] == "bc_and_outside_canada" and decision["in_bc_used"] != "yes":
            group = "pending"
        rows.append({"id": line["record_id"], "number": line["number"], "model_class": decision["class"],
                     "model_group": group, "pass1_places": place_text(pass1[line["record_id"]].get("places")),
                     "geonames_matches": lookup_text(lookups.get(line["number"], [])),
                     "pass2_answer": pass2_text(line["answer"]), "route_evidence": ""})
    return pd.DataFrame(rows).set_index("id")


def lunaris_groups():
    """Return the front end group of every Lunaris record (filter3_merged.csv), or None if it is not here."""
    if not os.path.exists(LUNARIS_MERGED):
        return None
    merged = pd.read_csv(LUNARIS_MERGED, dtype=str, keep_default_na=False, usecols=["record_id", "frontend_group"])
    return dict(zip(merged["record_id"], merged["frontend_group"]))


def other_route_answers(rules):
    """
    Return the rule and Lunaris route records as a table by record id, in
    the same columns as llm_answers: the class filter3_rules.parquet gives
    them, their front end group, and what decided them.
    """
    groups = lunaris_groups()
    rows = []
    for record_id, row in rules[rules["route"].isin(["rule", "lunaris"])].iterrows():
        group = FRONTEND_GROUP[row["final_class"]]
        if row["route"] == "lunaris":
            copies = list(row["lunaris_ids"])
            if groups is not None:
                found = {groups[c] for c in copies if c in groups}
                if len(found) == 1:
                    group = found.pop()
            evidence = f"Lunaris copy {', '.join(copies)}: {', '.join(row['lunaris_classes'])}"
        else:
            evidence = f"Lunaris rules: {row['rule_class']} ({row['rule_evidence']})"
        rows.append({"id": record_id, "number": None, "model_class": row["final_class"], "model_group": group,
                     "pass1_places": "", "geonames_matches": "", "pass2_answer": "", "route_evidence": evidence})
    return pd.DataFrame(rows).set_index("id")


# --- Scores -------------------------------------------------------------------------

def scored(labels, answers):
    """Return the labels joined to the answers of their route, only for records filter 2 kept."""
    joined = labels.join(answers, on="id", how="inner")
    joined["label_group"] = joined["label"].map(FRONTEND_GROUP)
    return joined


def score_row(name, route, table):
    """Return one scores row: score()'s counts, the counts behind each rate, and the rates."""
    s = score(list(table["label"]), list(table["model_class"]), list(table["model_group"]))
    label_bc = int(table["label"].isin(SHOWN).sum())
    label_bc_data = int((table["label_group"] == "bc_data").sum())
    n = s["n"]

    def rate(part, whole):
        """Return part / whole rounded, or empty when whole is 0."""
        return round(part / whole, 3) if whole else ""

    return {"label_set": name, "route": route, "records": n,
            "exact": s["exact"], "exact_rate": rate(s["exact"], n),
            "group_right": s["group_right"], "group_rate": rate(s["group_right"], n),
            "label_includes_bc": label_bc, "missed_bc": s["hidden_wrongly"],
            "missed_bc_rate": rate(s["hidden_wrongly"], label_bc),
            "label_no_bc": n - label_bc, "wrongly_bc": s["shown_wrongly"],
            "wrongly_bc_rate": rate(s["shown_wrongly"], n - label_bc),
            "label_bc_data": label_bc_data, "into_bc_data_wrongly": s["into_bc_data_wrongly"],
            "out_of_bc_data_wrongly": s["out_of_bc_data_wrongly"]}


def all_sets(table):
    """Return the records of every label set together, each record once (its first label set's label)."""
    return table.drop_duplicates("id", keep="first").assign(label_set=ALL_SETS)


def score_table(table):
    """Return the scores for each label set and all together, by route and for every route together."""
    rows = []
    for name, part in [*table.groupby("label_set", sort=False), (ALL_SETS, all_sets(table))]:
        for route in ROUTES:
            rows.append(score_row(name, route, part[part["route"] == route]))
        rows.append(score_row(name, "all routes", part))
    return pd.DataFrame(rows)


def disagreement_kind(label, model):
    """Return the kind of a disagreement: missed BC, wrongly BC, or another class difference."""
    if label in SHOWN and model not in SHOWN:
        return "missed BC"
    if model in SHOWN and label not in SHOWN:
        return "wrongly BC"
    return "other class" if label != model else "same class, other group"


def disagreements(table, titles):
    """Return one row per record and label set where the class or the front end group differs from the label."""
    rows = table[(table["label"] != table["model_class"]) | (table["label_group"] != table["model_group"])].copy()
    rows["kind"] = [disagreement_kind(a, b) for a, b in zip(rows["label"], rows["model_class"])]
    rows["title"] = rows["id"].map(titles)
    rows["link"] = [VIEW + quote(i, safe="") for i in rows["id"]]
    rows["run_number"] = rows["number"].map(lambda n: "" if pd.isna(n) else int(n))
    order = {"missed BC": 0, "wrongly BC": 1, "other class": 2, "same class, other group": 3}
    rows = rows.sort_values(["kind", "label_set", "row"], key=lambda c: c.map(order) if c.name == "kind" else c)
    return rows[["id", "label_set", "row", "route", "run_number", "kind", "label", "model_class", "label_group",
                 "model_group", "pass1_places", "geonames_matches", "pass2_answer", "route_evidence", "title",
                 "link", "recheck"]].rename(columns={"id": "record_id"})


# --- Timing and the full run estimate ---------------------------------------------

def log_times(path):
    """Return a job log's start and end times (its first 'start' and last 'end' lines) as datetimes."""
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    start = re.search(r"^start\s+(\S+)", text, re.MULTILINE).group(1)
    end = re.findall(r"^end\s+(\S+)", text, re.MULTILINE)[-1]
    return parse_time(start), parse_time(end)


def parse_time(text):
    """Return a UTC time such as 2026-11-23T21:45:32Z as a datetime."""
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ")


def first_job():
    """Return the first job's id, start and end, from logs/jobs.jsonl and its log."""
    job = read_jsonl(os.path.join(RUN, "logs", "jobs.jsonl"))[0]
    start, end = log_times(os.path.join(RUN, "logs", f"bc-filter_{job['job_id']}_0.out"))
    return job["job_id"], start, end


def job_lines(stem, folder, start, end):
    """Return one job's progress lines (those that finished between its start and end), by record number."""
    lines = {}
    for line in read_jsonl(os.path.join(RUN, folder, f"{stem}_part0.jsonl")):
        if start <= parse_time(line["finished"]) <= end:
            lines[line["number"]] = line
    return lines


def timing(rules, sample):
    """
    Return the first job's seconds per record (pass 1, lookup, pass 2, and
    their sum) with each record's group, repository and pass 1 input tokens.
    The seconds are those the run saved for the attempt it kept (a record
    asked twice also spent the first attempt). The first record's pass 1
    includes the model warming up; it is marked warm_up.
    """
    job, start, end = first_job()
    pass1 = job_lines("progress", "pass1", start, end)
    lookup = job_lines("lookup_progress", "pass1", start, end)
    pass2 = job_lines("progress", "pass2", start, end)
    rows = []
    for number, record_id in sample["record_id"].items():
        rule = rules.loc[record_id]
        rows.append({"number": number, "record_id": record_id, "group": rule["group"],
                     "repository": rule["datasource"], "pass1_s": pass1[number]["seconds"],
                     "pass1_tokens": pass1[number].get("input_tokens"), "lookup_s": lookup[number]["seconds"],
                     "pass2_s": pass2[number]["seconds"], "pass2_attempts": pass2[number]["attempts"],
                     "pass2_tokens": pass2[number].get("input_tokens"), "pass2_ok": "answer" in pass2[number]})
    table = pd.DataFrame(rows)
    table["total_s"] = table["pass1_s"] + table["lookup_s"] + table["pass2_s"]
    table["warm_up"] = table["number"] == min(pass1, key=lambda n: pass1[n]["finished"])
    return job, start, end, table


def summarise(table, by):
    """Return the mean and median seconds of pass 1, pass 2 and the total, and the record count, by a column."""
    rows = []
    for value, part in [*table.groupby(by, sort=True), ("all", table)]:
        rows.append({"by": by, "value": value, "records": len(part),
                     "pass1_mean": part["pass1_s"].mean(), "pass1_median": part["pass1_s"].median(),
                     "lookup_mean": part["lookup_s"].mean(),
                     "pass2_mean": part["pass2_s"].mean(), "pass2_median": part["pass2_s"].median(),
                     "total_mean": part["total_s"].mean(), "total_median": part["total_s"].median()})
    return pd.DataFrame(rows)


def full_lengths(shape):
    """Return each full-run record's group and pass 1 prompt length in characters (the dataone variant)."""
    rules = pd.read_parquet(RULES, columns=["id", "group", "datasource"]).set_index("id")
    rows = []
    with gzip.open(os.path.join(FULL_INPUT, "records.jsonl.gz"), "rt", encoding="utf-8") as handle:
        for text in handle:
            line = json.loads(text)
            prompt = geo_prompt_variants.build_pass1_prompt(line["record"], shape, "dataone")
            rows.append({"id": line["record_id"], "chars": len(prompt)})
    table = pd.DataFrame(rows).set_index("id")
    return table.join(rules)


def estimates(table, full, test_chars):
    """
    Return the full run's estimated seconds per record three ways, from the
    first job without its warm-up record: the test set's plain mean; the
    test set's mean per group weighted by the full run's group mix (no box
    split into Dryad and other repositories, since Dryad is most of the
    full run); and by pass 1 prompt length: the test records are cut into
    LENGTH_BINS bins of equal count by prompt characters, and each full-run
    record takes the mean seconds of the bin its length falls in (a record
    longer or shorter than every test record takes the end bin).
    """
    timed = table[~table["warm_up"]].copy()
    timed["dryad"] = timed["repository"] == "DRYAD"
    full = full.copy()
    full["dryad"] = full["datasource"] == "DRYAD"
    plain = timed["total_s"].mean()
    # Only no box is split by Dryad; every other group is one pool.
    full["pool"] = np.where(full["group"] == "no_box", np.where(full["dryad"], "no_box, Dryad", "no_box, other"),
                            full["group"])
    timed["pool"] = np.where(timed["group"] == "no_box", np.where(timed["dryad"], "no_box, Dryad", "no_box, other"),
                             timed["group"])
    weighted, used = 0.0, []
    for pool, part in full.groupby("pool"):
        mean = timed.loc[timed["pool"] == pool, "total_s"].mean()
        weighted += mean * len(part)
        used.append({"pool": pool, "full_records": len(part), "test_records": int((timed["pool"] == pool).sum()),
                     "test_mean_s": round(mean, 2)})
    timed["chars"] = timed["record_id"].map(test_chars)
    edges = np.quantile(timed["chars"], np.linspace(0, 1, LENGTH_BINS + 1))
    test_bin = np.clip(np.searchsorted(edges, timed["chars"], side="right") - 1, 0, LENGTH_BINS - 1)
    full_bin = np.clip(np.searchsorted(edges, full["chars"], side="right") - 1, 0, LENGTH_BINS - 1)
    bin_means = timed.groupby(test_bin)["total_s"].mean()
    bins = pd.DataFrame({"from_chars": edges[:-1].round(), "to_chars": edges[1:].round(),
                         "test_records": pd.Series(test_bin).value_counts().sort_index().values,
                         "test_mean_s": bin_means.round(2).values,
                         "full_records": pd.Series(full_bin).value_counts().reindex(range(LENGTH_BINS)).fillna(0)
                         .astype(int).values})
    by_length = bin_means.reindex(full_bin).mean()
    return {"plain": plain, "weighted": weighted / len(full), "by_length": by_length, "bins": bins,
            "used": pd.DataFrame(used), "test_chars_mean": timed["chars"].mean(),
            "full_chars_mean": full["chars"].mean()}


def parts_plan(seconds_per_record, records, overhead_s):
    """Return the smallest PARTS whose parts each take at most TARGET_HOURS, and the hours per part and in all."""
    parts = 1
    while overhead_s + math.ceil(records / parts) * seconds_per_record > TARGET_HOURS * 3600:
        parts += 1
    per_part = (overhead_s + math.ceil(records / parts) * seconds_per_record) / 3600
    return parts, per_part, parts * per_part


# --- Main -----------------------------------------------------------------------------

def main():
    """Score the label test run and the other routes, write the two CSV files, and print the timing and plan."""
    parser = argparse.ArgumentParser(description="Score DataONE filter 3 on the labelled records.")
    parser.add_argument("--out", default=OUT_DIR)
    args = parser.parse_args()
    labels, label_counts = read_labels()
    rules = pd.read_parquet(RULES).set_index("id", drop=False)
    sample, records = read_input(TEST_INPUT)
    answers = pd.concat([llm_answers(), other_route_answers(rules)])
    answers["route"] = answers.index.map(rules["route"])
    run_ids = set(sample["record_id"])
    llm_ids = set(rules.index[rules["route"] == "llm"]) & set(labels["id"])
    # The run holds every llm-route record of the label files, labelled or not, so it must
    # hold every scored one; the rest have no usable label (unsure, or no place label).
    if not llm_ids <= run_ids:
        sys.exit(f"STOPPED: {len(llm_ids - run_ids)} labelled llm-route records are not in the run")
    print(f"run records: {len(run_ids)}; with a usable label: {len(llm_ids)}; without (unsure or no place "
          f"label): {len(run_ids - llm_ids)}")
    table = scored(labels, answers)
    # Titles: the run's records from the input folder, the others from the label files.
    titles = {r: records[n]["record"]["title"] for n, r in sample["record_id"].items()}
    for record_id in set(table["id"]) - set(titles):
        titles[record_id] = labels_title(record_id)

    os.makedirs(args.out, exist_ok=True)
    scores = score_table(table)
    scores.to_csv(os.path.join(args.out, "filter3_test_scores.csv"), index=False)
    differ = disagreements(table, titles)
    differ.to_csv(os.path.join(args.out, "filter3_test_disagreements.csv"), index=False, encoding="utf-8-sig")

    print("labels:")
    for name, c in label_counts.items():
        kept = table[table["label_set"] == name]
        print(f"   {name}: {c['rows']} rows, {c['labelled']} with a place label, {c['unsure']} unsure (skipped), "
              f"{len(kept)} kept by filter 2 and scored, {c['labelled'] - c['unsure'] - len(kept)} not kept")
    conflicts = label_conflicts(labels)
    shared = labels[labels.duplicated("id", keep=False)]
    print(f"records in more than one label set: {shared['id'].nunique()}; with different labels: "
          f"{conflicts['id'].nunique()}")
    if len(conflicts):
        print(conflicts.to_string(index=False))
    print(scores.to_string(index=False))
    print(differ.groupby(["kind", "route"]).size().to_string())

    shape = load_bc_shape(BC_BOUNDARY)
    job, start, end, timed = timing(rules, sample)
    # Seconds by group and repository, without the warm-up record (its pass 1 includes loading the model).
    steady = timed[~timed["warm_up"]]
    summary = pd.concat([summarise(steady, "group"), summarise(steady, "repository")])
    summary.round(2).to_csv(os.path.join(args.out, "filter3_test_timing.csv"), index=False)
    print(f"\njob {job}: {start:%H:%M:%S} to {end:%H:%M:%S} UTC, {(end - start).total_seconds() / 60:.1f} minutes")
    warm = timed[timed["warm_up"]].iloc[0]
    print(f"warm-up record [{warm['number']}]: pass 1 {warm['pass1_s']} s")
    print(summary.round(2).to_string(index=False))
    asked = timed["pass1_s"].sum() + timed["lookup_s"].sum() + timed["pass2_s"].sum()
    overhead = (end - start).total_seconds() - asked
    print(f"seconds asking: {asked:.0f}; the rest of the job (staging, loading, warm-up not in the record "
          f"times, merging): {overhead:.0f} s")
    full = full_lengths(shape)
    test_chars = {r: len(geo_prompt_variants.build_pass1_prompt(records[n]["record"], shape, "dataone"))
                  for n, r in sample["record_id"].items()}
    e = estimates(timed, full, test_chars)
    print(e["used"].to_string(index=False))
    print(f"pass 1 prompt characters, mean: test {e['test_chars_mean']:.0f}, full {e['full_chars_mean']:.0f}")
    print(e["bins"].to_string(index=False))
    warm_up = float(warm["pass1_s"]) - timed.loc[~timed["warm_up"], "pass1_s"].median()
    for name in ("plain", "weighted", "by_length"):
        parts, per_part, total = parts_plan(e[name], len(full), overhead + warm_up)
        print(f"{name}: {e[name]:.2f} s per record; {len(full) * e[name] / 3600:.1f} hours asking; PARTS={parts}: "
              f"{per_part:.2f} h per part, {total:.1f} slice-hours")
    return 0


def labels_title(record_id):
    """Return a labelled record's title from the label files (for records not in the run)."""
    for _, path, _ in LABELS:
        table = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
        match = table.loc[table["id"] == record_id, "title"]
        if len(match):
            return match.iloc[0]
    return ""


if __name__ == "__main__":
    sys.exit(main())
