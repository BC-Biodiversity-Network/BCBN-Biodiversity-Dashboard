"""
compare_filter2_35b.py

Compare filter 2's keep set from the qwen3.6:35b-a3b full run with the
current keep set from the qwen3.5:4b full run (the one filter 3 was built
on), and work out what filter 3 needs for the records that changed.

Nothing here asks a model anything, and nothing is submitted to Fir. Every
input is only read; every output is a new file in --out, and the script
stops if --out already holds files.

How the two keep sets are made:
  4b   exactly as filter 3's step 1 made it: biodiversity_ids() from
       build_bc_filter_rules.py (decide() on every answered record, plus the
       3 failed records counted as yes by hand). It must hold the same
       record ids as filter3_merged.csv, or the script stops.
  35b  the same decide() on every answered record, then the hand decisions
       in filter2_hand_decisions.csv: a record decided by hand is kept or
       not kept as written there, whatever the model said. A failed record
       with no hand decision stops the script, so nobody's failure is kept
       or dropped silently.

Inputs:
  --answers-4b   the 4b full run's merged trial CSV
  --answers-35b  the 35b full run's merged trial CSV
  --hand         the hand decisions (filter2_hand_decisions.csv beside this file)
  --filter3      filter 3's final table, filter3_merged.csv
  --rules-v2     filter 3 step 1's rules file, rules_2026_10_v2.csv
  --harvest      the Lunaris harvest (lunaris_full_harvest.parquet)
  --bc-boundary  BC's outline as WKT (bc_boundary_gbif.wkt), read from disk
  --labels       the test set labels (test_set_reviewed_harvest_2026_10.csv)
  --test900-35b  the 35b run on the 900 test records, for the score check

Outputs, in --out:
  keep_groups.csv               every record kept by either run: its group
                                (both, only_35b, only_4b), catalogue and both
                                verdicts
  changed_by_catalogue.csv      the two changed groups counted by catalogue
  changed_samples.xlsx          30 records from each changed group (fixed
                                seed), text as harvested, both answers
  filter3_reused_kept_by_both.csv  the filter 3 rows of the records kept by
                                both, unchanged
  no_longer_kept_filter3.csv    the filter 3 rows of the records only 4b kept,
                                with the 35b answer
  no_longer_kept_bc_data.csv    those of them in front end group bc_data
  newly_kept_copies.csv         newly kept records that are copies of a
                                record filter 3 already holds
  rules_newly_kept_35b.csv      step 1 rules (rules v2 code) on the newly
                                kept records, same columns as rules v2
  fir_input_needs_llm/          the Fir input folder for their needs_llm
                                records, made by geo_filter_prepare.py
  reviewed_compare.csv          the reviewed test records: test900 and full
                                run verdicts side by side
  filter2_35b_compare_report.md the report

Run, from backend/:
    python pipeline/lunaris/compare_filter2_35b.py \\
        --bc-boundary /Users/lucia/Desktop/BCBN/Results/bc_boundary_gbif.wkt \\
        --out data/lunaris_llm_runs/filter2_35b_compare
"""

import argparse
import io
import json
import os
import random
import subprocess
import sys
from collections import Counter

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(BACKEND, "exploration", "llm"))
from bc_filter_rules import classify_record, load_rules  # noqa: E402
from build_bc_filter_rules import biodiversity_ids, read_records  # noqa: E402
from build_label_sample import dataset_keys  # noqa: E402
from check_harvest import source_of  # noqa: E402
from filter3_checks import as_harvested, excel_safe  # noqa: E402
from score_table import table as score_rows  # noqa: E402
from score_trial import decide  # noqa: E402

RUN_4B = "data/lunaris_llm_runs/harvest_2026_10_20/trial_qwen3.5-4b-fir_v6_harvest_2026_10_20.csv"
RUN_35B = ("data/lunaris_llm_runs/fir_harvest_2026_10_20_qwen3.6-35b-a3b_nofa/"
           "trial_qwen3.6-35b-a3b-fir-nofa_v6_harvest_2026_10_20_qwen3.6-35b-a3b_nofa.csv")
TEST900_35B = ("data/lunaris_llm_runs/fir_test900_qwen3.6-35b-a3b_nofa/"
               "trial_qwen3.6-35b-a3b-fir-nofa_v6_test900_qwen3.6-35b-a3b_nofa.csv")
HAND_CSV = os.path.join(HERE, "filter2_hand_decisions.csv")
FILTER3 = "data/lunaris_bc_filter/filter3_final/filter3_merged.csv"
RULES_V2 = "data/lunaris_bc_filter/rules_2026_10_v2.csv"
HARVEST = "exploration/lunaris/harvest_2026_10_20/lunaris_full_harvest.parquet"
LABELS = "exploration/llm/test_set_reviewed_harvest_2026_10.csv"
PREPARE = os.path.join(HERE, "llm", "geo_filter_prepare.py")

# The columns of a step 1 rules file, in the order build_bc_filter_rules.py
# writes them.
RULES_COLUMNS = ["record_id", "source", "title", "bc_class", "decided_by", "evidence",
                 "box_class", "box_share", "box_cover", "box_size", "point_class",
                 "place_hint", "place_words", "text_title", "text_keywords", "text_abstract"]
# The model's facts that decide() reads, then the ones shown for reading.
FACTS = ["concerns_living_things", "topic", "about_organisms_themselves", "ecological_purpose_stated",
         "experimental_animals", "extinct_only", "organisms", "organism_level", "form"]
GROUP_NAMES = ["both", "only_35b", "only_4b"]
SAMPLE_SIZE = 30
SEED = 20261027


# --- Reading ---------------------------------------------------------------

def read_answers(path):
    """
    Return a run's merged trial CSV, one row per record (indexed by record
    id), with a verdict column: decide()'s yes or no for an answered record,
    "failed" for a record with a model_error. Read the way
    build_bc_filter_rules.py reads it, so decide() sees the same values.
    """
    answers = pd.read_csv(path, encoding="utf-8-sig", low_memory=False)
    answers["record_id"] = answers["record_id"].astype(str)
    if answers["record_id"].duplicated().any():
        raise SystemExit(f"ERROR: {path} holds a record id twice")
    failed = answers["model_error"].notna() if "model_error" in answers.columns else pd.Series(False, answers.index)
    answers["verdict"] = "failed"
    answers.loc[~failed, "verdict"] = answers[~failed].apply(decide, axis=1)
    return answers.set_index("record_id", drop=False)


def read_hand_decisions(path=HAND_CSV):
    """
    Return the hand decisions, indexed by record id. Stops if a record is
    listed twice or kept is anything other than yes or no.
    """
    hand = pd.read_csv(path, dtype=str, keep_default_na=False)
    missing = {"record_id", "kept", "reason", "decided_by", "decided_on"} - set(hand.columns)
    if missing:
        raise SystemExit(f"ERROR: {path} has no column {sorted(missing)}")
    if hand["record_id"].duplicated().any():
        raise SystemExit(f"ERROR: {path} lists a record twice")
    wrong = hand[~hand["kept"].isin(["yes", "no"])]
    if len(wrong):
        raise SystemExit(f"ERROR: kept must be yes or no in {path}, not {wrong['kept'].iloc[0]!r}")
    return hand.set_index("record_id", drop=False)


def read_filter3(path=FILTER3):
    """Return filter 3's final table with every value as the text in the file, indexed by record id."""
    table = pd.read_csv(path, dtype=str, keep_default_na=False)
    return table.set_index("record_id", drop=False)


# --- The keep sets ---------------------------------------------------------

def keep_set(answers, hand):
    """
    Return the records a run keeps, and how they were reached, as (ids,
    counts). Answered records go through decide(); then each hand decision
    is applied as written, whatever the model said. Stops if a failed record
    has no hand decision, or a hand decision names a record the run does not
    hold.
    """
    unknown = set(hand.index) - set(answers.index)
    if unknown:
        raise SystemExit(f"ERROR: hand decisions for records not in the run, for example {sorted(unknown)[:3]}")
    failed = set(answers.index[answers["verdict"] == "failed"])
    undecided = failed - set(hand.index)
    if undecided:
        raise SystemExit(f"ERROR: {len(undecided)} records have no answer and no hand decision, for example "
                         f"{sorted(undecided)[:3]}. Look at them and add them to the hand decisions.")
    model_yes = set(answers.index[answers["verdict"] == "yes"])
    hand_yes = set(hand.index[hand["kept"] == "yes"])
    hand_no = set(hand.index[hand["kept"] == "no"])
    kept = (model_yes - hand_no) | hand_yes
    counts = {"answered": int((answers["verdict"] != "failed").sum()), "failed": len(failed),
              "model_yes": len(model_yes), "hand_yes_added": len(hand_yes - model_yes),
              "hand_no_removed": len(hand_no & model_yes), "kept": len(kept)}
    return kept, counts


def four_b_keep(path, filter3):
    """
    Return the 4b keep set exactly as filter 3's step 1 made it
    (biodiversity_ids), after checking it holds the same records as
    filter 3's final table.
    """
    kept, _, _ = biodiversity_ids(path)
    if kept != set(filter3.index):
        raise SystemExit(f"ERROR: the 4b keep set ({len(kept):,}) is not the set of records in filter 3 "
                         f"({len(filter3):,})")
    return kept


def keep_groups(kept_4b, kept_35b):
    """Return each record either run keeps, with its group: both, only_35b or only_4b."""
    groups = {}
    for record_id in kept_4b | kept_35b:
        if record_id in kept_4b and record_id in kept_35b:
            groups[record_id] = "both"
        else:
            groups[record_id] = "only_4b" if record_id in kept_4b else "only_35b"
    return pd.Series(groups, name="group").sort_index()


def by_catalogue(groups):
    """Return the records of each group counted by catalogue, the catalogues with most changes first."""
    # Plain arrays, so the two are paired by position.
    catalogues = [source_of(record_id) for record_id in groups.index]
    counts = pd.crosstab(pd.Series(catalogues), pd.Series(groups.values)).reindex(columns=GROUP_NAMES, fill_value=0)
    counts["changed"] = counts["only_35b"] + counts["only_4b"]
    counts["net"] = counts["only_35b"] - counts["only_4b"]
    counts.index.name = "catalogue"
    return counts.sort_values(["changed", "both"], ascending=False)


# --- Reading the answers side by side --------------------------------------

def plain(value):
    """Return a value as short text, with a missing value as an empty string."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value)


def answer_text(answers, hand, record_id):
    """
    Return a record's answer in one short phrase: decide()'s verdict, or
    "failed", and the hand decision when there is one.
    """
    verdict = answers.loc[record_id, "verdict"]
    if record_id in hand.index:
        return f"{verdict} (hand decision: kept {hand.loc[record_id, 'kept']})"
    return verdict


def facts_text(answers, record_id):
    """Return the model's facts for a record as "name: value" pairs, or an empty string for a failed record."""
    row = answers.loc[record_id]
    if row["verdict"] == "failed":
        return ""
    return "; ".join(f"{name}: {plain(row[name])}" for name in FACTS)


def facts_that_differ(answers_a, answers_b, record_id):
    """Return the names of the facts two runs gave differently for a record, joined with commas."""
    a, b = answers_a.loc[record_id], answers_b.loc[record_id]
    if "failed" in (a["verdict"], b["verdict"]):
        return "one run failed"
    return ", ".join(name for name in FACTS if plain(a[name]) != plain(b[name]))


def sample_ids(ids, size=SAMPLE_SIZE, seed=SEED):
    """
    Return up to size record ids drawn at random with a fixed seed, sorted,
    so the same group gives the same sample every run. A group no larger
    than size is returned whole.
    """
    ordered = sorted(ids)
    if len(ordered) <= size:
        return ordered
    return sorted(random.Random(seed).sample(ordered, size))


def side_by_side(record_ids, answers_4b, answers_35b, hand, harvest):
    """
    Return one row per record for reading: id, catalogue, title, abstract and
    subjects as harvested, and each run's verdict, facts and reason.
    """
    rows = []
    for record_id in record_ids:
        text = harvest.loc[record_id]
        rows.append({
            "record_id": record_id, "catalogue": source_of(record_id),
            "title": as_harvested(text["title"]), "abstract": as_harvested(text["abstract"]),
            "subjects": as_harvested(text["subjects"]),
            "answer_4b": answer_text(answers_4b, hand, record_id), "facts_4b": facts_text(answers_4b, record_id),
            "reason_4b": plain(answers_4b.loc[record_id, "model_reason"]),
            "answer_35b": answer_text(answers_35b, hand, record_id),
            "facts_35b": facts_text(answers_35b, record_id),
            "reason_35b": plain(answers_35b.loc[record_id, "model_reason"]),
            "facts_that_differ": facts_that_differ(answers_4b, answers_35b, record_id)})
    return pd.DataFrame(rows)


# --- Filter 3 --------------------------------------------------------------

def rules_rows(records, rules):
    """
    Return step 1's rules applied to harvest records (as read_records gives
    them), one row per record with the rules file's columns, exactly as
    build_bc_filter_rules.py builds them.
    """
    rows = []
    for record in records.to_dict("records"):
        row = classify_record(record, rules)
        row["title"] = record["title"]
        rows.append(row)
    return pd.DataFrame(rows, columns=RULES_COLUMNS)


def as_file_text(frame):
    """
    Return a table as it reads back from a CSV: every value as text, missing
    as empty, sorted by record id. Two tables that give the same result here
    would write the same values.
    """
    buffer = io.StringIO()
    frame.to_csv(buffer, index=False)
    buffer.seek(0)
    back = pd.read_csv(buffer, dtype=str, keep_default_na=False)
    return back.sort_values("record_id").reset_index(drop=True)


def rules_differences(result, path):
    """
    Return how many records step 1 now classifies differently from the rules
    file at path (any column), after checking both hold the same records and
    columns.
    """
    old = pd.read_csv(path, dtype=str, keep_default_na=False).sort_values("record_id").reset_index(drop=True)
    new = as_file_text(result)
    if list(old.columns) != list(new.columns) or list(old["record_id"]) != list(new["record_id"]):
        raise SystemExit(f"ERROR: the rules rerun does not hold the same records and columns as {path}")
    return int((old != new).any(axis=1).sum())


def copy_keys(record_ids, harvest):
    """
    Return each record's copy group key (dataset_keys from
    build_label_sample.py, as filter 3 groups copies), from the harvest's
    title and abstract.
    """
    texts = pd.DataFrame({"record_id": list(record_ids),
                          "title": [harvest.loc[r, "title"] for r in record_ids],
                          "abstract": [harvest.loc[r, "abstract"] for r in record_ids]})
    return dataset_keys(texts)


def newly_kept_copies(newly_kept, kept_by_both, filter3, harvest):
    """
    Return the newly kept records that are copies of a record filter 3 keeps
    (one row per pair), with that record's final class, how it was decided
    and its copy group, so filter 3 can give the copy the same treatment.
    """
    keys = copy_keys(sorted(newly_kept | kept_by_both), harvest)
    members = {}
    for record_id in kept_by_both:
        members.setdefault(keys[record_id], []).append(record_id)
    rows = []
    for record_id in sorted(newly_kept):
        for copy in sorted(members.get(keys[record_id], [])):
            old = filter3.loc[copy]
            rows.append({"record_id": record_id, "catalogue": source_of(record_id), "copy_of": copy,
                         "copy_final_class": old["final_class"], "copy_decided_by": old["decided_by"],
                         "copy_frontend_group": old["frontend_group"], "copy_dup_group_id": old["dup_group_id"],
                         "title": as_harvested(harvest.loc[record_id, "title"])})
    return pd.DataFrame(rows, columns=["record_id", "catalogue", "copy_of", "copy_final_class", "copy_decided_by",
                                       "copy_frontend_group", "copy_dup_group_id", "title"])


def group_effects(no_longer, filter3, kept_by_both):
    """
    Return what removing the no-longer-kept records does to filter 3's copy
    groups: how many groups lose a member, how many lose the row shown on
    the front end while other copies stay, and how many disappear entirely.
    """
    gone = filter3.loc[sorted(no_longer)]
    gone = gone[gone["dup_group_id"] != ""]
    staying = filter3.loc[sorted(kept_by_both)]
    staying_groups = set(staying.loc[staying["dup_group_id"] != "", "dup_group_id"])
    shown_lost = gone[(gone["dup_show"] == "True") & gone["dup_group_id"].isin(staying_groups)]
    return {"groups_losing_a_copy": gone["dup_group_id"].nunique(),
            "groups_losing_their_shown_row": shown_lost["dup_group_id"].nunique(),
            "groups_gone_entirely": len(set(gone["dup_group_id"]) - staying_groups)}


def prepare_fir_input(rules_path, harvest_path, bc_boundary, out_dir):
    """
    Make the Fir input folder for the needs_llm records of a rules file with
    geo_filter_prepare.py, run unchanged, exactly as for the first BC
    filter run (--rules-class needs_llm). Return what its input.json says.
    """
    if os.path.exists(out_dir):
        raise SystemExit(f"ERROR: {out_dir} already exists; nothing is overwritten")
    # Paths are passed as given, so input.json records them as the first run's does.
    subprocess.run([sys.executable, PREPARE, "--rules-class", "needs_llm", "--rules", rules_path,
                    "--harvest", harvest_path, "--bc-boundary", bc_boundary, "--out", out_dir], check=True)
    with open(os.path.join(out_dir, "input.json"), encoding="utf-8") as handle:
        return json.load(handle)


# --- The reviewed test records ---------------------------------------------

def reviewed_scores(labels, trials):
    """
    Score runs on the hand-reviewed test records exactly as score_table.py
    does: each run alone (on every reviewed record it answered), then all
    together (on the records every run answered). Return a list of
    (title, records scored, rows).
    """
    results = []
    for name, path in trials.items():
        rows, scored = score_rows(labels, {name: path}, reviewed_only=True)
        results.append((f"{name} alone", scored, rows))
    rows, scored = score_rows(labels, trials, reviewed_only=True)
    results.append(("together, on the records every run answered", scored, rows))
    return results


def reviewed_compare(labels, answers_test, answers_full):
    """
    Return the reviewed test records side by side: the label, the test900
    verdict and the full run verdict, whether the prompt was the same length
    (input tokens), and which facts differ. Records missing from a run are
    marked as such.
    """
    reviewed = labels[labels["reviewed_by_hand"] == True]  # noqa: E712 (the column holds True and False)
    rows = []
    for record_id, truth in zip(reviewed["record_id"], reviewed["is_biodiversity"]):
        in_full = record_id in answers_full.index
        row = {"record_id": record_id, "is_biodiversity": truth,
               "test900": answers_test.loc[record_id, "verdict"] if record_id in answers_test.index else "not run",
               "full_run": answers_full.loc[record_id, "verdict"] if in_full else "not in the harvest"}
        if in_full and record_id in answers_test.index:
            row["same_input_tokens"] = answers_test.loc[record_id, "input_tokens"] == answers_full.loc[
                record_id, "input_tokens"]
            row["facts_that_differ"] = facts_that_differ(answers_test, answers_full, record_id)
        rows.append(row)
    return pd.DataFrame(rows)


# --- Report ----------------------------------------------------------------

def markdown(header, rows):
    """Return a Markdown table from a header and rows of text, with any | in a cell written as /."""
    cell = lambda value: str(value).replace("|", "/").replace("\n", " ")  # noqa: E731
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(cell(v) for v in row) + " |" for row in rows]
    return "\n".join(lines)


def score_table_md(rows):
    """Return score_table.py's rows as a Markdown table."""
    lines = [["run", "precision", "recall", "keeps (weighted)", "junk kept", "real lost"]]
    for r in rows:
        lines.append([r["run"], f"{r['precision']:.1%}", f"{r['recall']:.1%}", f"{r['keeps']:,.0f}",
                      f"{r['junk_kept']:,.0f}", f"{r['real_lost']:,.0f}"])
    return markdown(lines[0], lines[1:])


def write_report(path, facts):
    """Write the Markdown report from the numbers gathered in main."""
    f = facts
    g = f["group_counts"]
    lines = ["# Filter 2 with qwen3.6:35b-a3b: keep set compared with 4b, and what filter 3 needs", "",
             f"Made {f['made']}. Nothing was committed, nothing was run on Fir, and no existing output was "
             f"changed: every file below is new.", "", "## Inputs", ""]
    lines += [f"- `{name}`: {value}" for name, value in f["inputs"].items()]
    k35, k4 = f["counts_35b"], f["counts_4b"]
    lines += ["", "## 1. Keep sets", "",
              f"- 4b (current, filter 3 was built on it): {k4['kept']:,} kept ({k4['model_yes']:,} by decide() plus "
              f"{k4['failed_kept']} failed records counted as yes by hand). Same record ids as "
              f"filter3_merged.csv.",
              f"- 35b (new): **{k35['kept']:,} kept**. decide() said yes to {k35['model_yes']:,} of "
              f"{k35['answered']:,} answered records. The 3 earlier hand yes records stay kept (the 35b model "
              f"answered them itself, and said yes to all 3, so the hand decisions add {k35['hand_yes_added']}); "
              f"the {k35['failed']} failed record is not kept, by hand decision. Hand decisions removed "
              f"{k35['hand_no_removed']} records the model had kept.",
              f"- Change: {k35['kept'] - k4['kept']:+,} records.", "",
              markdown(["group", "records"], [["kept by both", f"{g['both']:,}"],
                                              ["kept only by 35b (new)", f"{g['only_35b']:,}"],
                                              ["kept only by 4b (no longer kept)", f"{g['only_4b']:,}"]]), "",
              "Hand decisions (`pipeline/lunaris/filter2_hand_decisions.csv`): the 3 earlier yes records "
              "(Lucia, 2026-10-21) and the new no for "
              "oai:borealisdata.ca-dataverse-manitoba:700505_703695_703696 (Lucia, 2026-10-27).", "",
              "### The changed groups by catalogue", "",
              "All catalogues with at least one change (full list in `changed_by_catalogue.csv`).", ""]
    cat = f["by_catalogue"]
    changed = cat[cat["changed"] > 0]
    lines += [markdown(["catalogue", "kept by both", "only 35b", "only 4b", "net"],
                       [[name, f"{r.both:,}", f"{r.only_35b:,}", f"{r.only_4b:,}", f"{r.net:+,}"]
                        for name, r in changed.iterrows()]), ""]
    lines += ["### What changed in the answers", "",
              "Which of decide()'s facts differ between the two runs, counted over each changed group "
              "(a record can count under several facts).", ""]
    for group in ["only_35b", "only_4b"]:
        top = f["differ_counts"][group].most_common()
        lines += [f"- {group}: " + ", ".join(f"{name} {n:,}" for name, n in top)]
    lines += ["", "## 2. Samples to read", "",
              f"`changed_samples.xlsx`: {SAMPLE_SIZE} records from each changed group, drawn with seed {SEED}, "
              "one sheet per group. Columns: record_id, catalogue, title, abstract and subjects as harvested, "
              "then for each run its verdict, its facts and its reason, and which facts differ. "
              f"Excel fixes: {f['excel']['replaced']} cells had control characters replaced by a space, "
              f"{f['excel']['cut']} cells cut at Excel's limit.", ""]
    fe = f["frontend_no_longer"]
    lines += ["## 3. Filter 3 impact", "",
              f"### Kept by both ({g['both']:,})", "",
              f"Their filter 3 rows are reused unchanged: `filter3_reused_kept_by_both.csv` "
              f"({g['both']:,} rows, every value as in filter3_merged.csv).", "",
              "Two things in those rows depend on other records and may need a rerun of filter 3's merge "
              "step (not done here):", "",
              f"- Copy groups: {f['groups']['groups_losing_a_copy']:,} copy groups lose a copy that is no longer "
              f"kept; in {f['groups']['groups_losing_their_shown_row']:,} of them the copy that goes was the "
              f"one shown on the front end (dup_show), so another copy must be shown; "
              f"{f['groups']['groups_gone_entirely']:,} groups go entirely.",
              f"- New copies: {f['new_copies_records']:,} newly kept records are copies of {f['new_copies_of']:,} "
              f"records filter 3 already holds (`newly_kept_copies.csv`). Copies of a record decided by hand "
              f"among them: {f['new_copies_of_hand']} (such a copy would take the hand class through the copy "
              f"group rule).",
              "", f"### No longer kept ({g['only_4b']:,})", "",
              markdown(["frontend_group", "records"], [[name, f"{fe.get(name, 0):,}"]
                                                        for name in ["bc_data", "pending", "dropped"]]), "",
              f"Hand-checked records among them: {f['hand_no_longer']}.", "",
              f"The {fe.get('bc_data', 0):,} bc_data records are listed in Appendix A and in "
              f"`no_longer_kept_bc_data.csv` (with the facts of both runs). They break down as follows.", ""]
    bc = f["bc_data_no_longer"]
    topics = bc["facts_35b"].str.extract(r"topic: ([^;]*)")[0].fillna("(failed)")
    for name, values in [("final_class in filter 3", bc["final_class"]), ("catalogue", bc["source"]),
                         ("topic the 35b model gave", topics)]:
        counts = values.value_counts()
        lines += [f"- By {name}: " + ", ".join(f"{key} {n:,}" for key, n in counts.items())]
    lines += [""]
    s1 = f["step1_counts"]
    lines += [f"### Newly kept ({g['only_35b']:,})", "",
              "Step 1 rules (rules v2, the same code: `bc_filter_rules.classify_record`, run as "
              "build_bc_filter_rules.py runs it) on the newly kept records, written to "
              "`rules_newly_kept_35b.csv`:", "",
              markdown(["bc_class", "records"], [[name, f"{s1.get(name, 0):,}"]
                                                 for name in ["bc", "not_bc", "needs_llm"]]), "",
              f"Same code check: rerunning the rules on all {f['rules_check_records']:,} records of rules v2 "
              f"gives {f['rules_check_differences']} records that differ from `rules_2026_10_v2.csv` in any "
              f"column.", "",
              f"Fir input folder for the needs_llm records, made by geo_filter_prepare.py exactly as the first "
              f"run (`--rules-class needs_llm`, rules file above): `{f['fir_input']}`, "
              f"**{f['fir_about']['records']:,} records**, pass 1 prompt {f['fir_about']['pass1_prompt']}, "
              f"pass 2 prompt {f['fir_about']['pass2_prompt']}. Every pass 1 prompt was checked to be the same "
              f"from the JSON copy. Nothing was submitted.", "",
              "## 4. The reviewed test records", "",
              f"{f['reviewed_total']} records were reviewed by hand; {f['reviewed_in_full']} of them are in the "
              f"2026-10-20 harvest and so in the full run (the other {f['reviewed_total'] - f['reviewed_in_full']} "
              "are not in that harvest). Scored exactly as score_table.py (decide(), weighted to the corpus):", ""]
    for title, scored, rows in f["scores"]:
        lines += [f"{title} ({scored} records):", "", score_table_md(rows), ""]
    rc = f["reviewed_diff"]
    if rc["tokens_differ"]:
        why = (f"On {rc['tokens_differ']} of them the prompt length in tokens differs, so the record text the "
               f"model saw was not the same; the rest is run-to-run variation of the same prompt.")
    else:
        why = ("On all of them the prompt had the same length in tokens in both runs, which points to the same "
               "prompt: run-to-run variation, not a change in the record text.")
    lines += [f"On the {rc['both']} records both runs answered, the test900 and full run verdicts differ on "
              f"{rc['verdict_differs']} (in `reviewed_compare.csv`): the full run says no to {rc['real_lost']} "
              f"records labelled biodiversity and yes to {rc['junk_added']} labelled not biodiversity. {why} Each "
              "record carries a corpus weight, so a handful of records moves the weighted scores by several "
              "points.", ""]
    lines += ["## Appendix A. The bc_data records no longer kept", "",
              markdown(["record_id", "final_class", "decided_by", "title", "35b reason"],
                       [[r.record_id, r.final_class, r.decided_by, r.title[:90], r.reason_35b[:140]]
                        for r in bc.itertuples()]), ""]
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


# --- Main ------------------------------------------------------------------

def main():
    """Build both keep sets, compare them, work out filter 3's part, and write the files and the report."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("--answers-4b", default=RUN_4B)
    parser.add_argument("--answers-35b", default=RUN_35B)
    parser.add_argument("--hand", default=HAND_CSV)
    parser.add_argument("--filter3", default=FILTER3)
    parser.add_argument("--rules-v2", default=RULES_V2)
    parser.add_argument("--harvest", default=HARVEST)
    parser.add_argument("--bc-boundary", required=True, help="BC's outline as WKT (bc_boundary_gbif.wkt)")
    parser.add_argument("--labels", default=LABELS)
    parser.add_argument("--test900-35b", default=TEST900_35B)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    # Never write over earlier results.
    if os.path.exists(args.out) and os.listdir(args.out):
        raise SystemExit(f"ERROR: {args.out} already holds files; give a new folder")
    os.makedirs(args.out, exist_ok=True)
    out = lambda name: os.path.join(args.out, name)  # noqa: E731

    # 1. Both keep sets and the three groups.
    filter3 = read_filter3(args.filter3)
    hand = read_hand_decisions(args.hand)
    answers_4b, answers_35b = read_answers(args.answers_4b), read_answers(args.answers_35b)
    kept_4b = four_b_keep(args.answers_4b, filter3)
    _, model_yes_4b, failed_kept_4b = biodiversity_ids(args.answers_4b)
    kept_35b, counts_35b = keep_set(answers_35b, hand)
    groups = keep_groups(kept_4b, kept_35b)
    ids = {name: set(groups.index[groups == name]) for name in GROUP_NAMES}
    print(f"4b keeps {len(kept_4b):,}, 35b keeps {len(kept_35b):,}; "
          + ", ".join(f"{name} {len(ids[name]):,}" for name in GROUP_NAMES))
    pd.DataFrame({"record_id": groups.index, "catalogue": groups.index.map(source_of), "group": groups.values,
                  "verdict_4b": [answer_text(answers_4b, hand, r) for r in groups.index],
                  "verdict_35b": [answer_text(answers_35b, hand, r) for r in groups.index]}
                 ).to_csv(out("keep_groups.csv"), index=False)
    catalogues = by_catalogue(groups)
    catalogues.to_csv(out("changed_by_catalogue.csv"))

    # 2. Samples of the changed groups, with the record text as harvested.
    harvest = read_records(args.harvest, kept_4b | kept_35b).set_index("id", drop=False)
    counter = Counter()
    with pd.ExcelWriter(out("changed_samples.xlsx")) as writer:
        for group in ["only_35b", "only_4b"]:
            sheet = side_by_side(sample_ids(ids[group]), answers_4b, answers_35b, hand, harvest)
            excel_safe(sheet, counter).to_excel(writer, sheet_name=f"{group}_sample", index=False)
    differ_counts = {group: Counter(name for r in ids[group]
                                    for name in facts_that_differ(answers_4b, answers_35b, r).split(", ") if name)
                     for group in ["only_35b", "only_4b"]}

    # 3. Filter 3: reuse, no longer kept, newly kept.
    filter3.loc[sorted(ids["both"])].to_csv(out("filter3_reused_kept_by_both.csv"), index=False)
    no_longer = filter3.loc[sorted(ids["only_4b"])].copy()
    no_longer["verdict_35b"] = [answer_text(answers_35b, hand, r) for r in no_longer.index]
    no_longer["facts_35b"] = [facts_text(answers_35b, r) for r in no_longer.index]
    no_longer["reason_35b"] = [plain(answers_35b.loc[r, "model_reason"]) for r in no_longer.index]
    no_longer["facts_4b"] = [facts_text(answers_4b, r) for r in no_longer.index]
    no_longer.to_csv(out("no_longer_kept_filter3.csv"), index=False)
    bc_data = no_longer[no_longer["frontend_group"] == "bc_data"]
    bc_data[["record_id", "source", "title", "final_class", "decided_by", "dup_group_id", "dup_show",
             "verdict_35b", "facts_35b", "reason_35b", "facts_4b"]].to_csv(out("no_longer_kept_bc_data.csv"),
                                                                        index=False)
    copies = newly_kept_copies(ids["only_35b"], ids["both"], filter3, harvest)
    copies.to_csv(out("newly_kept_copies.csv"), index=False)

    rules = load_rules(args.bc_boundary)
    # The same code check: rules v2's records again, compared column by column.
    check = rules_rows(harvest.loc[sorted(kept_4b)].reset_index(drop=True), rules)
    differences = rules_differences(check, args.rules_v2)
    newly = rules_rows(harvest.loc[sorted(ids["only_35b"])].reset_index(drop=True), rules)
    newly.to_csv(out("rules_newly_kept_35b.csv"), index=False, encoding="utf-8")
    fir_input = out("fir_input_needs_llm")
    fir_about = prepare_fir_input(out("rules_newly_kept_35b.csv"), args.harvest, args.bc_boundary, fir_input)

    # 4. The reviewed test records.
    labels = pd.read_csv(args.labels, encoding="utf-8-sig")
    labels["record_id"] = labels["record_id"].astype(str)
    scores = reviewed_scores(labels, {"35b test900": args.test900_35b, "35b full run": args.answers_35b})
    compare = reviewed_compare(labels, read_answers(args.test900_35b), answers_35b)
    compare.to_csv(out("reviewed_compare.csv"), index=False)
    both_ran = compare[compare["full_run"].isin(["yes", "no"]) & compare["test900"].isin(["yes", "no"])]
    differs = both_ran[both_ran["test900"] != both_ran["full_run"]]

    write_report(out("filter2_35b_compare_report.md"), {
        "made": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"),
        "inputs": {"4b answers": args.answers_4b, "35b answers": args.answers_35b, "hand decisions": args.hand,
                   "filter 3 table": args.filter3, "rules v2": args.rules_v2, "harvest": args.harvest,
                   "BC outline": args.bc_boundary, "test labels": args.labels, "35b test900": args.test900_35b},
        "counts_4b": {"kept": len(kept_4b), "model_yes": model_yes_4b, "failed_kept": failed_kept_4b},
        "counts_35b": counts_35b, "group_counts": {name: len(ids[name]) for name in GROUP_NAMES},
        "by_catalogue": catalogues, "differ_counts": differ_counts, "excel": counter,
        "groups": group_effects(ids["only_4b"], filter3, ids["both"]),
        "new_copies_records": copies["record_id"].nunique(), "new_copies_of": copies["copy_of"].nunique(),
        "new_copies_of_hand": int(copies.loc[copies["copy_decided_by"] == "hand", "record_id"].nunique()),
        "frontend_no_longer": no_longer["frontend_group"].value_counts().to_dict(),
        "hand_no_longer": ", ".join(no_longer.index[no_longer["decided_by"] == "hand"]) or "none",
        "bc_data_no_longer": bc_data, "step1_counts": newly["bc_class"].value_counts().to_dict(),
        "rules_check_records": len(check), "rules_check_differences": differences,
        "fir_input": fir_input,
        "fir_about": fir_about, "scores": scores,
        "reviewed_total": len(compare), "reviewed_in_full": int(compare["full_run"].isin(["yes", "no", "failed"]).sum()),
        "reviewed_diff": {"both": len(both_ran), "verdict_differs": len(differs),
                          "tokens_differ": int((~differs["same_input_tokens"].astype(bool)).sum()),
                          "real_lost": int(((differs["is_biodiversity"] == "yes") & (differs["full_run"] == "no")).sum()),
                          "junk_added": int(((differs["is_biodiversity"] == "no") & (differs["full_run"] == "yes")).sum())}})
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
