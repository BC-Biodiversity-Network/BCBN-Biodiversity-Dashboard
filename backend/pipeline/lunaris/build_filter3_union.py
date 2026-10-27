"""
build_filter3_union.py

Filter 3 for the union keep set: a record is kept by filter 2 if the 4b run
or the 35b run keeps it, plus the hand decisions (the 3 yes, the Manitoba
search strategy no). That is the 18,440 records filter 3 already holds plus
the 1,928 only the 35b run keeps.

How the table is made:
  1. The 18,440: their rows in filter3_final/filter3_merged.csv are reused
     as they are, for everything that depends on the record alone (step 1,
     the LLM run, and the final class by rule, model and BC-only catalogue,
     worked out again from the row with build_filter3_final.final_of and
     checked against what the row says).
  2. The 1,928 new: step 1's rules (rules_newly_kept_35b.csv: bc, not_bc,
     needs_llm) and, for the 1,247 needs_llm, the Fir run
     needs_llm_new35b_qwen3.6-35b-a3b_nofa, turned into rows by
     build_filter3_final.record_rows, the same code as the first build.
  3. Then over the whole union, exactly as build_filter3_final.py: copy
     groups, the hand checks passed on to whole copy groups (DFO, Hakai),
     the front end groups (with the national-plus-outside rule) and the
     shown copy of each group.
  4. filter2_keep_source says which run kept the record: both, 4b_only or
     35b_only.

Before anything is written, a self-check: steps 1 and 3 on the 18,440
alone must give filter3_merged.csv again, value for value. If they do not,
the script stops, because then the union would not be built the same way.

The script stops with a clear message, before reading anything large, if
the Fir run folder is missing, or does not hold exactly the 1,247 records in
both passes, or was run with another model, prompt or flash attention
setting. It writes into a new folder and stops if that folder already holds
files.

Outputs (--out, ignored by git):
  filter3_merged.csv          the union table, one row per record
  bc_catalogue_changes.csv    records the BC-only catalogue rule changed
  dfo_trawl_handcheck.csv     the DFO trawl series hand-check list
  hakai_handcheck.csv         the 3 Hakai records checked by hand
  duplicate_conflicts.csv     copy groups whose members differ in class or group
  changes_vs_filter3_final.csv  the 18,440 whose class, group, decided_by,
                              note or shown copy is not what filter3_final says
  filter3_union_report.md     counts, split by filter2_keep_source

Run, from backend/, once the Fir run folder is copied back:
    python pipeline/lunaris/build_filter3_union.py

Only the self-check, without the Fir run:
    python pipeline/lunaris/build_filter3_union.py --self-check-only
"""

import argparse
import io
import json
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_filter3_final as f3  # noqa: E402
import compare_filter2_35b as cmp  # noqa: E402

DEFAULT_OLD = "data/lunaris_bc_filter/filter3_final/filter3_merged.csv"
DEFAULT_RULES_NEW = "data/lunaris_llm_runs/filter2_35b_compare/rules_newly_kept_35b.csv"
DEFAULT_RUN = "data/lunaris_bc_filter/fir_runs/needs_llm_new35b_qwen3.6-35b-a3b_nofa"
DEFAULT_OUT = "data/lunaris_bc_filter/filter3_final_union"
RUN_NAME = "needs_llm_new35b_qwen3.6-35b-a3b_nofa"
# What the Fir run must have been run with, as the first full run.
EXPECTED_RUN = {"model": "qwen3.6:35b-a3b", "pass1_prompt": "v3", "pass2_prompt": "C4"}
EXPECTED_FLASH_ATTENTION = "off"
# The columns worked out over the whole set; every other column of a reused
# row is kept as it is.
WHOLE_SET_COLUMNS = ["final_class", "decided_by", "note", "dup_group_id", "dup_group_size", "before_hand",
                     "frontend_group", "dup_show"]
# The columns compared with filter3_final for the 18,440 (dup_group_id is
# numbered again over the union, so its size is compared instead).
COMPARED = ["final_class", "decided_by", "frontend_group", "dup_show", "dup_group_size", "note", "before_hand"]
SOURCES = ["both", "4b_only", "35b_only"]


# --- The Fir run -----------------------------------------------------------

def jsonl_ids(path):
    """Return the record ids in a JSON lines file, in order (a list, so a repeated id shows)."""
    with open(path, encoding="utf-8") as handle:
        return [str(json.loads(line)["record_id"]) for line in handle if line.strip()]


def check_run(run_dir, needs_ids):
    """
    Stop with a clear message unless the Fir run folder is there and
    complete: pass 1 and pass 2 answers for exactly the needs_llm records
    (once each), run with the expected model and prompts, flash attention
    off. Read only.
    """
    expected = len(needs_ids)
    copy_back = (f"Copy it back when the run is done (Mac, from backend/):\n    scp -r "
                 f"'songyanf@fir.alliancecan.ca:/scratch/songyanf/lunaris_llm/bcf/runs/{RUN_NAME}' "
                 f"data/lunaris_bc_filter/fir_runs/")
    if not os.path.isdir(run_dir):
        raise SystemExit(f"STOP: the Fir run folder {run_dir} is not there yet.\n{copy_back}")
    for part in ["pass1", "pass2"]:
        path = os.path.join(run_dir, part, "answers.jsonl")
        if not os.path.exists(path):
            raise SystemExit(f"STOP: {path} is missing: the run's merged files are not written yet (the last "
                             f"part to finish writes them once every record has a result from both passes). "
                             f"Check the run on Fir with geo_filter_status.py, then copy it back again.")
        ids = jsonl_ids(path)
        if len(ids) != expected or len(set(ids)) != len(ids) or set(ids) != set(needs_ids):
            raise SystemExit(f"STOP: {path} has {len(ids)} answers ({len(set(ids))} records); it must have "
                             f"exactly the {expected} needs_llm records, once each.")
    with open(os.path.join(run_dir, "run.json"), encoding="utf-8") as handle:
        about = json.load(handle)
    wrong = {k: about.get(k) for k, v in EXPECTED_RUN.items() if about.get(k) != v}
    if wrong:
        raise SystemExit(f"STOP: {run_dir}/run.json says {wrong}; this build expects {EXPECTED_RUN}.")
    flash_path = os.path.join(run_dir, "flash_attention.txt")
    flash = open(flash_path, encoding="utf-8").readline().strip() if os.path.exists(flash_path) else "(no record)"
    if flash != EXPECTED_FLASH_ATTENTION:
        raise SystemExit(f"STOP: {flash_path} says flash attention {flash}; this build expects "
                         f"{EXPECTED_FLASH_ATTENTION}.")
    return about


# --- The keep sets ---------------------------------------------------------

def keep_sources(kept_4b, kept_35b, hand):
    """
    Return the union keep set as a Series, record id to which run kept it
    (both, 4b_only or 35b_only): every record either run keeps, without
    the hand decisions' no, with their yes. Stops if a hand yes is kept by
    neither run (there would be no source to name).
    """
    hand_yes = set(hand.index[hand["kept"] == "yes"])
    hand_no = set(hand.index[hand["kept"] == "no"])
    if hand_yes - (kept_4b | kept_35b):
        raise SystemExit(f"ERROR: hand yes records kept by neither run: {sorted(hand_yes - (kept_4b | kept_35b))}")
    union = sorted((kept_4b | kept_35b) - hand_no)
    return pd.Series(["both" if r in kept_4b and r in kept_35b else "4b_only" if r in kept_4b else "35b_only"
                      for r in union], index=union, name="filter2_keep_source")


# --- Rows ------------------------------------------------------------------

def reused_rows(old, catalogues):
    """
    Return the reused filter 3 rows ready for the whole-set steps: every
    column kept as it is, except those the whole-set steps work out, which
    are dropped, and final_class, decided_by and note, worked out again
    from the row by final_of (rule, model, BC-only catalogue). Stops if that
    is not what the row says it had before the whole-set steps (its class,
    or its before_hand when a hand check changed it).
    """
    rows = old.drop(columns=WHOLE_SET_COLUMNS).copy()
    finals = [f3.final_of(r.rule_class, r.llm_class if isinstance(r.llm_class, str) else "", r.source, catalogues)
              for r in old.itertuples()]
    rows["final_class"] = [f[0] for f in finals]
    rows["decided_by"] = [f[1] for f in finals]
    rows["note"] = [f[2] for f in finals]
    before = [b if isinstance(b, str) and b else f"{c} ({d})"
              for b, c, d in zip(old["before_hand"], old["final_class"], old["decided_by"])]
    again = [f"{c} ({d})" for c, d in zip(rows["final_class"], rows["decided_by"])]
    differ = [r for r, b, a in zip(old["record_id"], before, again) if b != a]
    if differ:
        raise SystemExit(f"ERROR: {len(differ)} filter 3 rows do not match final_of, for example {differ[:3]}")
    return rows


def finish(table, harvest):
    """Apply the whole-set steps and mark the shown copies, exactly as build_filter3_final.py's main does."""
    f3.whole_set_steps(table, harvest)
    f3.add_duplicates(table, harvest)
    return table


def as_text(frame):
    """Return a table as it reads back from a CSV, every value as text, so two tables can be compared value for value."""
    buffer = io.StringIO()
    frame.to_csv(buffer, index=False)
    buffer.seek(0)
    return pd.read_csv(buffer, dtype=str, keep_default_na=False)


def self_check(old, harvest, catalogues):
    """
    Rebuild filter 3 from the reused rows alone and return the record ids
    whose row comes out different from filter3_merged.csv in any column
    (empty when the rebuild gives the file again).
    """
    rebuilt = finish(reused_rows(old, catalogues), harvest)[list(old.columns)]
    a, b = as_text(old), as_text(rebuilt)
    if list(a["record_id"]) != list(b["record_id"]):
        return sorted(set(a["record_id"]) ^ set(b["record_id"])) or list(a["record_id"])
    return list(a.loc[(a != b).any(axis=1), "record_id"])


def build_union(old, new_rows, sources, harvest, catalogues):
    """
    Return the union table: the reused rows and the new rows together, the
    whole-set steps over all of them, and filter2_keep_source last. Stops
    if the rows are not exactly the union keep set.
    """
    table = pd.concat([reused_rows(old, catalogues), new_rows], ignore_index=True)
    if table["record_id"].duplicated().any() or set(table["record_id"]) != set(sources.index):
        raise SystemExit("ERROR: the rows are not exactly the union keep set")
    finish(table, harvest)
    table = table[list(old.columns)].copy()
    table["filter2_keep_source"] = table["record_id"].map(sources)
    return table


def changes_vs_old(table, old):
    """
    Return the reused records whose compared columns (COMPARED) differ from
    filter3_final, one row per record, with the old and new values of the
    columns that changed.
    """
    a = as_text(old).set_index("record_id")
    b = as_text(table[table["record_id"].isin(old["record_id"])][list(old.columns)]).set_index("record_id").loc[a.index]
    rows = []
    for record_id in a.index[(a[COMPARED] != b[COMPARED]).any(axis=1)]:
        changed = [c for c in COMPARED if a.at[record_id, c] != b.at[record_id, c]]
        row = {"record_id": record_id, "source": a.at[record_id, "source"], "title": a.at[record_id, "title"],
               "changed": ", ".join(changed)}
        for column in changed:
            row[f"old_{column}"], row[f"new_{column}"] = a.at[record_id, column], b.at[record_id, column]
        rows.append(row)
    return pd.DataFrame(rows)


# --- Report ----------------------------------------------------------------

def split_table(table, column, order):
    """Return a Markdown table of a column's values counted by filter2_keep_source, with totals."""
    counts = pd.crosstab(table[column], table["filter2_keep_source"]).reindex(index=order, columns=SOURCES,
                                                                               fill_value=0)
    rows = [[name] + [f"{counts.at[name, s]:,}" for s in SOURCES] + [f"{counts.loc[name].sum():,}"]
            for name in order]
    rows.append(["total"] + [f"{counts[s].sum():,}" for s in SOURCES] + [f"{counts.values.sum():,}"])
    return cmp.markdown([column] + SOURCES + ["all"], rows)


def report(table, changes, rules_new, about, inputs, n_conflict):
    """Return the union report as Markdown."""
    shown = table[table["dup_show"]]
    lines = ["# Filter 3 for the union keep set (4b or 35b)", "",
             "Built by build_filter3_union.py: the 18,440 filter 3 rows reused, the 1,928 records only the 35b run "
             "keeps added (step 1 rules, and the Fir run for their needs_llm records), then copy groups, hand "
             "checks, front end groups and shown copies worked out again over the whole union, exactly as in "
             "build_filter3_final.py. Self-check passed: the same steps on the 18,440 alone give "
             "filter3_final/filter3_merged.csv again, value for value.", "", "## Inputs", ""]
    lines += [f"- {name}: `{value}`" for name, value in inputs.items()]
    lines += [f"- Fir run: model {about.get('model')} ({about.get('model_digest', '')[:12]}), Ollama "
              f"{about.get('ollama_version')}, pass 1 {about.get('pass1_prompt')}, pass 2 {about.get('pass2_prompt')}, "
              f"made {about.get('created')}", "",
              "## The new records at step 1", "",
              cmp.markdown(["bc_class", "records"], [[k, f"{v:,}"] for k, v in
                                                     rules_new["bc_class"].value_counts().items()]), "",
              "## Final classes, all rows", "",
              split_table(table, "final_class", list(f3.FRONTEND_GROUP)), "",
              "## Front end groups, all rows", "", split_table(table, "frontend_group", f3.GROUPS), "",
              "## Front end groups, one row per copy group (dup_show)", "",
              split_table(shown, "frontend_group", f3.GROUPS), "",
              "## How the final class was decided", "",
              split_table(table, "decided_by", sorted(table["decided_by"].unique())), "",
              f"Copy groups whose members differ in class or front end group: {n_conflict[0]:,} in class, "
              f"{n_conflict[1]:,} in front end group (`duplicate_conflicts.csv`).", "",
              f"## The 18,440 against filter3_final", "",
              f"{len(changes):,} of them differ from filter3_final in {', '.join(COMPARED)} "
              f"(`changes_vs_filter3_final.csv`)."]
    if len(changes):
        counted = changes["changed"].str.split(", ").explode().value_counts()
        lines += ["", cmp.markdown(["column", "records"], [[k, f"{v:,}"] for k, v in counted.items()])]
    lines += [""]
    return "\n".join(lines)


# --- Main ------------------------------------------------------------------

def main():
    """Check the Fir run, build the union table, and write it with the lists and the report."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("--old", default=DEFAULT_OLD, help="filter3_final/filter3_merged.csv")
    parser.add_argument("--rules-new", default=DEFAULT_RULES_NEW, help="rules_newly_kept_35b.csv")
    parser.add_argument("--run", default=DEFAULT_RUN, help="the Fir run folder for the new needs_llm records")
    parser.add_argument("--answers-4b", default=cmp.RUN_4B)
    parser.add_argument("--answers-35b", default=cmp.RUN_35B)
    parser.add_argument("--hand", default=cmp.HAND_CSV)
    parser.add_argument("--harvest", default=f3.DEFAULT_HARVEST)
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--self-check-only", action="store_true",
                        help="only rebuild filter3_final from its own rows and compare; needs no Fir run")
    args = parser.parse_args()

    rules_new = f3.read_rules(args.rules_new)
    needs = set(rules_new.loc[rules_new["bc_class"] == "needs_llm", "record_id"])
    if not args.self_check_only:
        # First the quick checks, so a missing run or a used folder stops at once.
        if os.path.isdir(args.out) and os.listdir(args.out):
            raise SystemExit(f"STOP: {args.out} already holds files; move them away or give --out a new folder.")
        about = check_run(args.run, needs)
        if os.path.abspath(args.out).startswith(os.path.abspath(args.run)):
            raise SystemExit("ERROR: --out must not be inside the run folder; the run folder is only read")

    old = pd.read_csv(args.old, low_memory=False)
    catalogues = f3.bc_only_catalogues()
    filter3 = cmp.read_filter3(args.old)
    hand = cmp.read_hand_decisions(args.hand)
    kept_4b = cmp.four_b_keep(args.answers_4b, filter3)
    kept_35b, _ = cmp.keep_set(cmp.read_answers(args.answers_35b), hand)
    sources = keep_sources(kept_4b, kept_35b, hand)
    if set(rules_new["record_id"]) != set(sources.index[sources == "35b_only"]):
        raise SystemExit(f"ERROR: {args.rules_new} does not hold exactly the records only the 35b run keeps")
    harvest = f3.read_harvest(args.harvest, set(sources.index))

    differ = self_check(old, harvest, catalogues)
    if differ:
        raise SystemExit(f"STOP: the self-check failed: rebuilding filter 3 from its own rows changes "
                         f"{len(differ)} rows, for example {differ[:3]}. Nothing written.")
    print(f"self-check passed: the whole-set steps on the {len(old):,} reused rows give {args.old} again")
    if args.self_check_only:
        return 0

    pass1, pass2, _ = f3.read_run(args.run)
    new_rows = f3.record_rows(rules_new, pass1, pass2, harvest, catalogues)
    table = build_union(old, new_rows, sources, harvest, catalogues)
    conflicts, n_class, n_group = f3.duplicate_conflicts(table, harvest)
    changes = changes_vs_old(table, old)
    inputs = {"filter 3 rows reused": args.old, "step 1 rules for the new records": args.rules_new,
              "Fir run": args.run, "4b answers": args.answers_4b, "35b answers": args.answers_35b,
              "hand decisions": args.hand, "harvest": args.harvest}

    os.makedirs(args.out, exist_ok=True)
    out = lambda name: os.path.join(args.out, name)  # noqa: E731
    table.to_csv(out("filter3_merged.csv"), index=False, encoding="utf-8")
    table[table["decided_by"] == "bc_only_catalogue"][["record_id", "source", "title", "llm_class",
                                                       "filter2_keep_source"]].to_csv(
        out("bc_catalogue_changes.csv"), index=False, encoding="utf-8")
    f3.dfo_handcheck(table, harvest).to_csv(out("dfo_trawl_handcheck.csv"), index=False, encoding="utf-8")
    f3.hakai_handcheck(table, harvest, catalogues).to_csv(out("hakai_handcheck.csv"), index=False, encoding="utf-8")
    conflicts.to_csv(out("duplicate_conflicts.csv"), index=False, encoding="utf-8")
    changes.to_csv(out("changes_vs_filter3_final.csv"), index=False, encoding="utf-8")
    text = report(table, changes, rules_new, about, inputs, (n_class, n_group))
    with open(out("filter3_union_report.md"), "w", encoding="utf-8") as handle:
        handle.write(text)
    print(text)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
