"""
build_filter3_final.py

The last step of DataONE filter 3 (was the data collected in BC?): one row
per record filter 2 kept, with its final class and its front end group,
and the scores on the three label sets. Nothing here asks a model
anything; every input is only read.

What goes in:
  data/dataone/filter3/filter3_rules.parquet  (build_filter3_rules.py) the
      route of each of the 78,740 kept records: lunaris (6,883), rule
      (1,336) or llm (70,521)
  data/dataone/filter3_runs/dataone_needs_llm_qwen3.6-35b-a3b_nofa/  the
      full LLM run (job 5559473), copied back from Fir: run.json and the
      merged pass1/answers.jsonl and pass2/answers.jsonl
  data/dataone/filter3_input/dataone_needs_llm/  the run's input records,
      to work the boxes' shares in BC out again
  data/dataone/filter2_results/filter2_union.parquet  filter 2's answers,
      with the Lunaris decision of each DOI or UUID match
  data/dataone/harvest_2026_11_18/dataone_lunaris_match.parquet  which
      Lunaris records each DataONE record matched
  data/lunaris_bc_filter/filter3_final_union/filter3_merged.csv  the
      Lunaris filter 3 table (class and front end group of each record)
  pipeline/dataone/filter3_hand_decisions.csv  the hand decisions (in git)
  the three label sets (build_filter3_rules.LABELS), read fresh each run

How each record gets its class, in this order (the last that applies wins):
  1. By its route in filter3_rules.parquet:
       lunaris  the Lunaris filter 3 class of its Lunaris copy, as
                filter3_rules.parquet has it;
       rule     the Lunaris mapping of the rule result
                (build_filter3_final.RULE_TO_CLASS: bc gives bc_only,
                not_bc gives elsewhere);
       llm      the class code works out from the model's pass 2 facts:
                geo_pass2_classes.decide with the boxes' shares measured
                the DataONE way (geo_prompt_variants.box_shares, variant
                dataone). The shares and the decision are worked out again
                here and must equal the ones the run saved. model_in_bc,
                in_bc_used and in_bc_filled_by_box_rule are kept.
     Steps 1 and 2 of the Lunaris final_of (build_filter3_final.py) do
     this. Its step 3 (the Lunaris BC-only catalogues) can never apply: no
     DataONE repository is one of them, and the script stops if one is.
  2. Either copy says yes (Lucia, 2026-11-24). Of the records matched to
     Lunaris by DOI or UUID but with title similarity under 0.9 (they went
     through filter 2 themselves), 7 got another filter 2 answer than
     their Lunaris copy. A record is kept if either side said yes:
       - DataONE no, Lunaris yes: the record is added as kept, with the
         Lunaris filter 3 class of its copy, route "lunaris_either_yes"
         and decided_by EITHER_YES_DECIDED_BY;
       - DataONE yes, Lunaris no: the record stays as it is; its Lunaris
         record is listed so the Lunaris side can be fixed later (no
         Lunaris file is changed here).
  3. Hand decisions (filter3_hand_decisions.csv) come last: they replace
     whatever the route gave, with route "hand". hand_reason and
     decided_by are the file's words; route_before_hand and
     class_before_hand keep what the record had before.

The front end group is set exactly as the Lunaris pipeline does it
(build_filter3_final.set_frontend_groups and FRONTEND_GROUP): bc_only,
canada_incl_bc and bc_and_outside_canada are bc_data; all_canada and
global are pending; elsewhere and unknown are dropped; and a
bc_and_outside_canada record the model placed there without in_bc_used
yes goes to pending ("national plus outside, no BC named"). That rule only
looks at the model's own classes (decided by the llm route), as in
Lunaris. Every lunaris and lunaris_either_yes record must end in the same
group as its Lunaris copy; the script stops if one does not.

The checks before anything is written (the script stops if one fails):
every kept record (plus the added ones) appears exactly once, every class
is a filter 3 class and none is empty, and the run's records are exactly
the llm route's.

The scores, with score() of pipeline/lunaris/filter3_checks.py, as the
2026-11-23 report scored the label test: for each label set and route,
for every route together, and for all label sets together (each record
once, with the label of the first set that holds it). Labels "unsure" and
empty labels are skipped.

Outputs:
  data/dataone/filter3/filter3_final.parquet  one row per record (ignored
      by git; the script checks with git check-ignore, and stops if the
      file is there unless --replace)
  in --results (~/Desktop/BCBN/Results/dataone_filter3_final_2026-11-24/):
      filter3_final_counts.csv         records by route, by datasource (the
                                       10 biggest) and in all, per class and
                                       per front end group
      filter3_final_scores.csv         the scores, next to the 2026-11-23 ones
      filter3_final_disagreements.csv  every labelled record whose class or
                                       group differs from its label
and a report printed to the screen.

Run, from backend/:
    ../.venv/bin/python pipeline/dataone/build_filter3_final.py
"""

import argparse
import os
import sys
from urllib.parse import quote

import pandas as pd
import pyarrow.parquet as pq

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, "..", ".."))
for folder in ("pipeline/dataone", "pipeline/lunaris", "pipeline/lunaris/llm"):
    sys.path.insert(0, os.path.join(BACKEND, folder))
import build_filter3_final as lunaris_final  # noqa: E402  (pipeline/lunaris; the Lunaris functions)
import geo_prompt_variants  # noqa: E402
from bc_filter_rules import load_bc_shape  # noqa: E402
from build_filter3_rules import (LABELS, LUNARIS_FILTER3, MATCH, OUT as RULES, OUT_DIR, UNION,  # noqa: E402
                                 lunaris_classes, lunaris_route)
from build_label_set import VIEW  # noqa: E402
from classify_multibox_records import BC_BOUNDARY, VERSIONS, check_ignored  # noqa: E402
from filter3_checks import score  # noqa: E402
from geo_filter_run import read_input  # noqa: E402
from geo_pass2_classes import CLASSES, decide  # noqa: E402
from geo_pass2_score import SHOWN  # noqa: E402
from match_lunaris import LUNARIS_HARVEST, STRONG  # noqa: E402

# This file has the same name as the Lunaris one, so make sure the import above found that one.
if os.path.dirname(os.path.abspath(lunaris_final.__file__)) != os.path.join(BACKEND, "pipeline", "lunaris"):
    sys.exit(f"STOPPED: build_filter3_final was imported from {lunaris_final.__file__}, not pipeline/lunaris")

FRONTEND_GROUP = lunaris_final.FRONTEND_GROUP
GROUPS = lunaris_final.GROUPS

RUN = os.path.join(BACKEND, "data", "dataone", "filter3_runs", "dataone_needs_llm_qwen3.6-35b-a3b_nofa")
INPUT = os.path.join(BACKEND, "data", "dataone", "filter3_input", "dataone_needs_llm")
HAND_CSV = os.path.join(HERE, "filter3_hand_decisions.csv")
OUT = os.path.join(OUT_DIR, "filter3_final.parquet")
RESULTS = os.path.expanduser("~/Desktop/BCBN/Results/dataone_filter3_final_2026-11-24")
NOV23_SCORES = os.path.expanduser("~/Desktop/BCBN/Results/dataone_filter3_test_2026-11-23/filter3_test_scores.csv")

EXPECTED_KEPT = 78_740
EXPECTED_DISAGREEMENTS = 7
VARIANT = "dataone"
# The words that go with a record added by the either-yes rule (Lucia, 2026-11-24).
EITHER_YES_DECIDED_BY = "rule: kept if either copy says yes, approved by Lucia, 2026-11-24"
# The routes, in the order they are applied: the later ones replace the earlier.
ROUTES = ["lunaris", "rule", "llm", "lunaris_either_yes", "hand"]
# The routes whose class is their Lunaris copy's, so their group must be its group too.
LUNARIS_ROUTES = ("lunaris", "lunaris_either_yes")
# Two box shares this close are the same share (the run measured them on Fir).
SHARE_TOLERANCE = 1e-9
# The front end group rule of Lunaris looks only at the model's own classes.
LUNARIS_DECIDED_BY = {"llm": "llm"}
SCORE_ROUTES = ["llm", "rule", "lunaris", "lunaris_either_yes", "hand"]
ALL_SETS = "all label sets"
TOP_DATASOURCES = 10

pd.set_option("display.width", 250)
pd.set_option("display.max_colwidth", 90)


def stop(message):
    """Stop the script with a message that starts with STOPPED."""
    sys.exit(f"STOPPED: {message}")


# --- Reading -------------------------------------------------------------------

def read_routes(path=RULES):
    """Return filter3_rules.parquet, one row per kept record, indexed by id. Stop if a record is there twice."""
    rules = pd.read_parquet(path)
    if rules["id"].duplicated().any():
        stop(f"{path} lists a record twice")
    return rules.set_index("id", drop=False)


def read_titles(ids):
    """Return the title of each given latest DataONE record, by id. Stop if one is missing."""
    table = pq.read_table(VERSIONS, columns=["id", "title", "version_status"]).to_pandas()
    table = table[(table["version_status"] == "latest") & table["id"].isin(set(ids))].drop_duplicates("id")
    titles = table.set_index("id")["title"]
    missing = set(ids) - set(titles.index)
    if missing:
        stop(f"{len(missing)} records have no latest version, for example {sorted(missing)[:3]}")
    return titles


def read_hand(path=HAND_CSV):
    """
    Return the hand decisions as a table indexed by record id: final_class,
    reason, decided_by, decided_on, recorded_in. They are first read with
    the Lunaris reader (build_filter3_final.read_filter3_hand_decisions),
    which stops on a missing column, a record listed twice or a class
    filter 3 does not know.
    """
    lunaris_final.read_filter3_hand_decisions(path)
    hand = pd.read_csv(path, dtype=str, keep_default_na=False)
    return hand.set_index("record_id", drop=False)


def read_lunaris_table(path=LUNARIS_FILTER3):
    """Return the Lunaris filter 3 table's final class and front end group, by Lunaris record id."""
    table = pd.read_csv(path, dtype=str, keep_default_na=False, usecols=["record_id", "final_class",
                                                                         "frontend_group"])
    return table.set_index("record_id")


# --- The LLM route -----------------------------------------------------------------

def check_run(about, pass1, pass2, llm_ids):
    """Stop unless the run was asked in the dataone variant and holds exactly the llm route's records."""
    if about.get("geo_prompt_variant") != VARIANT:
        stop(f"the run's prompt variant is {about.get('geo_prompt_variant')}, not {VARIANT}")
    if set(pass1) != set(llm_ids) or set(pass2) != set(llm_ids):
        stop(f"the run has {len(pass1)} pass 1 and {len(pass2)} pass 2 records, and the llm route "
             f"{len(llm_ids)}; they are not the same records")


def same_shares(saved, measured):
    """Return True if two lists of box shares are the same: the same length, None in the same places, numbers close."""
    if len(saved) != len(measured):
        return False
    for a, b in zip(saved, measured):
        if (a is None) != (b is None) or (a is not None and abs(a - b) > SHARE_TOLERANCE):
            return False
    return True


def llm_decisions(pass2, records, shape):
    """
    Return, for each answered pass 2 record, the boxes' shares in BC and the
    decision, worked out again: the shares the DataONE way
    (geo_prompt_variants.box_shares, variant dataone) from the run's input
    record, then geo_pass2_classes.decide. Stop, listing them, if a record's
    shares or decision differ from the ones the run saved.
    """
    by_id = {item["record_id"]: item["record"] for item in records.values()}
    decisions, differ = {}, []
    for record_id, result in pass2.items():
        if "answer" not in result:
            continue
        shares = geo_prompt_variants.box_shares(by_id[record_id], shape, VARIANT)
        decision = decide(result["answer"], shares)
        if not same_shares(result.get("box_shares", []), shares) or decision != result.get("decision"):
            differ.append(record_id)
        decisions[record_id] = (shares, decision)
    if differ:
        stop(f"{len(differ)} records' box shares or decision differ from the run's, for example {differ[:3]}")
    return decisions


def llm_row(pass1, pass2, decision):
    """
    Return the LLM columns of one llm-route record: the Lunaris columns
    (build_filter3_final.llm_columns: the five facts, evidence, reason, the
    evidence check, any error), the run number, and from the decision
    worked out again llm_class, model_in_bc, in_bc_used and
    in_bc_filled_by_box_rule. decision is None for a record without an
    answer; its llm_class is then empty.
    """
    row = lunaris_final.llm_columns(pass1, pass2)
    # The Lunaris name for in_bc_filled_by_box_rule; the decision's own name is kept instead.
    row.pop("box_rule_filled", None)
    row["run_number"] = pass2["number"]
    if decision is None:
        row.update(llm_class="", model_in_bc="", in_bc_used="", in_bc_filled_by_box_rule=False)
        return row
    if row["llm_class"] != decision["class"]:
        stop(f"{pass2['record_id']}: llm_columns gives {row['llm_class']}, decide gives {decision['class']}")
    row.update(llm_class=decision["class"], model_in_bc=decision["model_in_bc"], in_bc_used=decision["in_bc_used"],
               in_bc_filled_by_box_rule=bool(decision["in_bc_filled_by_box_rule"]))
    return row


# --- The class of each record ---------------------------------------------------------

def route_class(route, rule_class, llm_class, lunaris_class, datasource, catalogues):
    """
    Return the class a route gives a record (step 1 at the top of the file):
    the Lunaris copy's class for lunaris; for rule and llm, the Lunaris
    final_of (RULE_TO_CLASS of the rule result, or the model's class).
    Stop if a record is from a Lunaris BC-only catalogue, which final_of
    would treat differently.
    """
    if datasource in catalogues:
        stop(f"{datasource} is a Lunaris BC-only catalogue; DataONE records should never be")
    if route == "lunaris":
        return lunaris_class
    if route == "rule":
        return lunaris_final.final_of(rule_class, llm_class, datasource, catalogues)[0]
    if route == "llm":
        return lunaris_final.final_of("needs_llm", llm_class, datasource, catalogues)[0]
    stop(f"unknown route {route}")


def base_table(rules, titles, llm, catalogues):
    """
    Return one row per record of filter3_rules.parquet with its route's
    class (route_class) and the columns that explain it: filter 2's keep
    source, the rule columns, the Lunaris copies, and for the llm route the
    LLM columns (llm, by id). Stop if a rule or lunaris class differs from
    the one filter3_rules.parquet holds.
    """
    rows = []
    for r in rules.to_dict("records"):
        row = {"record_id": r["id"], "datasource": r["datasource"], "title": titles[r["id"]],
               "group": r["group"], "multibox": r["multibox"], "filter2_keep_source": r["keep_source"],
               "route": r["route"], "route_reason": r["route_reason"], "rule_class": r["rule_class"],
               "rule_decided_by": r["rule_decided_by"], "rule_evidence": r["rule_evidence"],
               "box_class": r["box_class"], "box_share": r["box_share"], "point_class": r["point_class"],
               "lunaris_ids": " | ".join(r["lunaris_ids"]), "lunaris_classes": " | ".join(r["lunaris_classes"])}
        if r["route"] == "llm":
            row.update(llm[r["id"]])
        row["final_class"] = route_class(r["route"], r["rule_class"], row.get("llm_class", ""), r["final_class"],
                                         r["datasource"], catalogues)
        if r["route"] != "llm" and row["final_class"] != r["final_class"]:
            stop(f"{r['id']}: the {r['route']} route gives {row['final_class']}, filter3_rules.parquet "
                 f"{r['final_class']}")
        rows.append(row)
    return pd.DataFrame(rows)


def low_similarity_disagreements(union):
    """
    Return the records matched to Lunaris by DOI or UUID that did not reuse
    it (title similarity under 0.9) and whose filter 2 answer differs from
    the Lunaris decision: id, datasource, match_type, title_similarity,
    dataone_answer (yes, no or failed) and lunaris_answer. Only records with
    a Lunaris decision of yes or no count, as in build_filter2_union.py.
    """
    low = union[union["match_type"].isin(STRONG) & ~union["reuse_lunaris"].astype(bool)].copy()
    low["dataone_answer"] = low["keep_union"].map({True: "yes", False: "no"}).fillna("failed")
    low = low[low["lunaris_decision"].isin(["yes", "no"])]
    differ = low[low["dataone_answer"] != low["lunaris_decision"]]
    return differ.rename(columns={"lunaris_decision": "lunaris_answer"})[
        ["id", "datasource", "match_type", "title_similarity", "dataone_answer", "lunaris_answer"]
    ].sort_values("title_similarity").reset_index(drop=True)


def describe_disagreements(differ, copies, lunaris, titles, lunaris_titles):
    """
    Return the disagreements with what is needed to read them: the Lunaris
    record ids and their filter 3 class and group, both titles, and what
    the either-yes rule does with each. copies is lunaris_classes' result
    (ids and classes by DataONE id); lunaris the Lunaris table.
    """
    rows = []
    for d in differ.to_dict("records"):
        ids, classes = copies[d["id"]]
        if d["dataone_answer"] == "no" and d["lunaris_answer"] == "yes":
            action = "added as kept (lunaris_either_yes)"
        elif d["dataone_answer"] == "yes" and d["lunaris_answer"] == "no":
            action = "kept as it is; fix the Lunaris side later"
        else:
            stop(f"{d['id']}: filter 2 answer {d['dataone_answer']}, the rule only knows yes and no")
        rows.append({**d, "lunaris_ids": " | ".join(ids), "lunaris_filter3_class": " | ".join(c or "-" for c in classes),
                     "lunaris_frontend_group": " | ".join(lunaris["frontend_group"].get(i, "-") for i in ids),
                     "dataone_title": titles[d["id"]], "lunaris_titles": " | ".join(lunaris_titles.get(i, "") for i in ids),
                     "action": action})
    return pd.DataFrame(rows)


def either_yes_rows(described, union):
    """
    Return the rows to add for the records DataONE filter 2 left out but
    their Lunaris copy kept: kept, with the Lunaris filter 3 class of the
    copy (lunaris_route: every copy must have the same class), route
    lunaris_either_yes, decided_by EITHER_YES_DECIDED_BY. Stop if a copy
    has no class or the copies disagree.
    """
    rows = []
    for d in described[described["action"].str.startswith("added")].to_dict("records"):
        classes = d["lunaris_filter3_class"].split(" | ")
        route, cls, reason = lunaris_route(["" if c == "-" else c for c in classes])
        if route != "lunaris":
            stop(f"{d['id']}: its Lunaris copies give no single filter 3 class ({reason})")
        u = union.loc[d["id"]]
        rows.append({"record_id": d["id"], "datasource": d["datasource"], "title": d["dataone_title"],
                     "group": u["group"], "multibox": bool(u["multibox"]), "filter2_keep_source": u["keep_source"],
                     "route": "lunaris_either_yes",
                     "route_reason": (f"DataONE filter 2 said no, its Lunaris copy said yes (title similarity "
                                      f"{d['title_similarity']:.3f}): {reason}"),
                     "lunaris_ids": d["lunaris_ids"], "lunaris_classes": d["lunaris_filter3_class"],
                     "final_class": cls, "decided_by": EITHER_YES_DECIDED_BY})
    return pd.DataFrame(rows)


def apply_hand(table, hand):
    """
    Give the hand-decided records their class, in place: route "hand",
    hand_reason and decided_by word for word from the file, and
    route_before_hand and class_before_hand what they had before. Stop if
    the file names a record the table does not hold.
    """
    unknown = sorted(set(hand.index) - set(table["record_id"]))
    if unknown:
        stop(f"hand decisions for records not in the table: {unknown}")
    for column in ("hand_reason", "decided_by", "decided_on", "hand_recorded_in", "route_before_hand",
                   "class_before_hand"):
        if column not in table.columns:
            table[column] = ""
        table[column] = table[column].fillna("")
    for index in table.index[table["record_id"].isin(hand.index)]:
        h = hand.loc[table.at[index, "record_id"]]
        table.at[index, "route_before_hand"] = table.at[index, "route"]
        table.at[index, "class_before_hand"] = table.at[index, "final_class"]
        table.at[index, "route"] = "hand"
        table.at[index, "final_class"] = h["final_class"]
        table.at[index, "hand_reason"] = h["reason"]
        table.at[index, "decided_by"] = h["decided_by"]
        table.at[index, "decided_on"] = h["decided_on"]
        table.at[index, "hand_recorded_in"] = h["recorded_in"]
    return table


def set_groups(table):
    """
    Set each record's front end group and note, in place, with the Lunaris
    set_frontend_groups. It reads decided_by in the Lunaris sense: "llm"
    for the llm route (the only one its national-plus-outside rule looks
    at), the route's own name for every other route.
    """
    work = pd.DataFrame({"final_class": table["final_class"],
                         "decided_by": table["route"].map(lambda r: LUNARIS_DECIDED_BY.get(r, r)),
                         "in_bc_used": table.get("in_bc_used", pd.Series("", index=table.index)).fillna(""),
                         "note": ""}, index=table.index)
    lunaris_final.set_frontend_groups(work)
    table["frontend_group"] = work["frontend_group"]
    table["note"] = work["note"]
    return table


def check_lunaris_groups(table, lunaris):
    """Stop if a lunaris or lunaris_either_yes record's group is not the front end group of its Lunaris copies."""
    differ = []
    for row in table[table["route"].isin(LUNARIS_ROUTES)].itertuples():
        groups = {lunaris["frontend_group"].get(i, "") for i in row.lunaris_ids.split(" | ")}
        if groups != {row.frontend_group}:
            differ.append((row.record_id, row.frontend_group, sorted(groups)))
    if differ:
        stop(f"{len(differ)} records are in another group than their Lunaris copy, for example {differ[:3]}")


def check_complete(table, expected_ids):
    """Stop unless every expected record appears exactly once, with a filter 3 class, a group and a route."""
    if table["record_id"].duplicated().any():
        stop(f"{int(table['record_id'].duplicated().sum())} records appear more than once")
    missing = set(expected_ids) - set(table["record_id"])
    extra = set(table["record_id"]) - set(expected_ids)
    if missing or extra:
        stop(f"{len(missing)} records are missing and {len(extra)} are not expected")
    empty = table[~table["final_class"].isin(CLASSES)]
    if len(empty):
        stop(f"{len(empty)} records have no filter 3 class, for example {list(empty['record_id'][:3])}")
    if not table["frontend_group"].isin(GROUPS).all() or not table["route"].isin(ROUTES).all():
        stop("a record has no front end group or no route")


def build(rules, titles, llm, catalogues, either_yes, hand):
    """
    Return the final table: the route classes (base_table), then the
    either-yes rows added, then the hand decisions, then the front end
    groups, in that order.
    """
    table = base_table(rules, titles, llm, catalogues)
    if len(either_yes):
        table = pd.concat([table, either_yes], ignore_index=True)
    if "decided_by" not in table.columns:
        table["decided_by"] = ""
    table["decided_by"] = table["decided_by"].fillna("")
    apply_hand(table, hand)
    set_groups(table)
    return table


# --- Scores ---------------------------------------------------------------------------

def read_labels():
    """
    Return the labels as one table (label_set, row, id, label) and, per
    label set, how many rows it has, how many carry a place label and how
    many of those are unsure (skipped). The files are read fresh.
    """
    rows, counts = [], {}
    for name, path, column in LABELS:
        table = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
        labelled = table[table[column].str.strip() != ""]
        unsure = labelled[column].str.strip() == "unsure"
        counts[name] = {"rows": len(table), "labelled": len(labelled), "unsure": int(unsure.sum())}
        for row in labelled[~unsure].to_dict("records"):
            label = row[column].strip()
            if label not in FRONTEND_GROUP:
                stop(f"{name} row {row['row']} has the label {label!r}, not a filter 3 class")
            rows.append({"label_set": name, "row": int(row["row"]), "id": row["id"], "label": label})
    return pd.DataFrame(rows), counts


def scored(labels, table):
    """Return the labels joined to the final table (only records filter 3 holds), with the label's group."""
    columns = ["record_id", "route", "run_number", "final_class", "frontend_group", "title", "in_bc", "other_canada",
               "outside_canada", "canada_wide", "global", "reason", "hand_reason", "route_reason"]
    joined = labels.merge(table[[c for c in columns if c in table.columns]], left_on="id", right_on="record_id")
    joined["label_group"] = joined["label"].map(FRONTEND_GROUP)
    return joined


def score_row(name, route, part):
    """Return one scores row: score()'s counts, the counts behind each rate, and the rates."""
    s = score(list(part["label"]), list(part["final_class"]), list(part["frontend_group"]))
    label_bc = int(part["label"].isin(SHOWN).sum())
    n = s["n"]

    def rate(count, whole):
        """Return count / whole rounded, or empty when whole is 0."""
        return round(count / whole, 3) if whole else ""

    return {"label_set": name, "route": route, "records": n, "exact": s["exact"], "exact_rate": rate(s["exact"], n),
            "group_right": s["group_right"], "group_rate": rate(s["group_right"], n),
            "label_includes_bc": label_bc, "missed_bc": s["hidden_wrongly"],
            "missed_bc_rate": rate(s["hidden_wrongly"], label_bc), "label_no_bc": n - label_bc,
            "wrongly_bc": s["shown_wrongly"], "wrongly_bc_rate": rate(s["shown_wrongly"], n - label_bc),
            "label_bc_data": int((part["label_group"] == "bc_data").sum()),
            "into_bc_data_wrongly": s["into_bc_data_wrongly"], "out_of_bc_data_wrongly": s["out_of_bc_data_wrongly"]}


def score_table(joined):
    """Return the scores for each label set and all together, by route and for every route together."""
    every = joined.drop_duplicates("id", keep="first").assign(label_set=ALL_SETS)
    rows = []
    for name, part in [*joined.groupby("label_set", sort=False), (ALL_SETS, every)]:
        for route in SCORE_ROUTES:
            rows.append(score_row(name, route, part[part["route"] == route]))
        rows.append(score_row(name, "all routes", part))
    return pd.DataFrame(rows)


def with_nov23(scores, path=NOV23_SCORES):
    """Add the 2026-11-23 records, exact, group right, missed BC and wrongly BC next to each scores row."""
    if not os.path.exists(path):
        return scores
    old = pd.read_csv(path)[["label_set", "route", "records", "exact", "group_right", "missed_bc", "wrongly_bc"]]
    old = old.rename(columns={c: f"{c}_nov23" for c in ("records", "exact", "group_right", "missed_bc", "wrongly_bc")})
    return scores.merge(old, on=["label_set", "route"], how="left")


def disagreement_kind(label, answer):
    """Return the kind of a disagreement: missed BC, wrongly BC, another class, or the same class in another group."""
    if label in SHOWN and answer not in SHOWN:
        return "missed BC"
    if answer in SHOWN and label not in SHOWN:
        return "wrongly BC"
    return "other class" if label != answer else "same class, other group"


def disagreements(joined):
    """Return one row per record and label set whose final class or front end group differs from its label."""
    rows = joined[(joined["label"] != joined["final_class"]) | (joined["label_group"] != joined["frontend_group"])].copy()
    rows["kind"] = [disagreement_kind(a, b) for a, b in zip(rows["label"], rows["final_class"])]
    rows["link"] = [VIEW + quote(i, safe="") for i in rows["id"]]
    order = {"missed BC": 0, "wrongly BC": 1, "other class": 2, "same class, other group": 3}
    rows = rows.sort_values(["kind", "label_set", "row"], key=lambda c: c.map(order) if c.name == "kind" else c)
    columns = ["record_id", "label_set", "row", "route", "run_number", "kind", "label", "final_class", "label_group",
               "frontend_group", "in_bc", "other_canada", "outside_canada", "canada_wide", "global", "reason",
               "hand_reason", "route_reason", "title", "link"]
    return rows[[c for c in columns if c in rows.columns]]


# --- Counts -------------------------------------------------------------------------------

def counts_rows(table, by, label):
    """Return records per value of a column (route or datasource): per class, per front end group, and in all."""
    classes = pd.crosstab(table[by], table["final_class"]).reindex(columns=CLASSES, fill_value=0)
    groups = pd.crosstab(table[by], table["frontend_group"]).reindex(columns=GROUPS, fill_value=0)
    out = classes.join(groups)
    out["records"] = out[CLASSES].sum(axis=1)
    out.insert(0, "by", label)
    return out.reset_index().rename(columns={by: "value"})


def counts_table(table):
    """Return the counts CSV: by route, by the 10 biggest datasources, and the total row."""
    by_route = counts_rows(table, "route", "route").set_index("value").reindex(
        [r for r in ROUTES if r in set(table["route"])]).rename_axis("value").reset_index()
    biggest = list(table["datasource"].value_counts().head(TOP_DATASOURCES).index)
    by_source = counts_rows(table[table["datasource"].isin(biggest)], "datasource", "datasource (10 biggest)")
    by_source = by_source.set_index("value").reindex(biggest).rename_axis("value").reset_index()
    total = counts_rows(table.assign(all="all records"), "all", "total")
    return pd.concat([by_route, by_source, total], ignore_index=True)


# --- Main -----------------------------------------------------------------------------------

def report(table, counts, described, scores, differ, label_counts, joined):
    """Print the counts, the box rule count, the 7 disagreements and the scores."""
    print(f"\n1. RECORDS BY ROUTE x FINAL CLASS ({len(table):,} records)")
    print(pd.crosstab(table["route"], table["final_class"], margins=True, margins_name="total")
          .reindex(columns=CLASSES + ["total"], fill_value=0).to_string())
    print("\n   by route x front end group:")
    print(pd.crosstab(table["route"], table["frontend_group"], margins=True, margins_name="total")
          .reindex(columns=GROUPS + ["total"], fill_value=0).to_string())
    print(f"   the national-plus-outside rule moved {int((table['note'] != '').sum())} bc_and_outside_canada "
          f"records to pending")
    print(f"\n2. THE {TOP_DATASOURCES} BIGGEST DATASOURCES")
    print(counts[counts["by"] != "route"].to_string(index=False))
    llm = table[table["route_before_hand"].eq("llm") | table["route"].eq("llm")]
    filled = table["in_bc_filled_by_box_rule"].fillna(False).astype(bool)
    print(f"\n3. BOX RULE: in_bc filled in by the box rule for {int(filled.sum())} of {len(llm):,} llm-route "
          f"records ({int((filled & (table['route'] == 'llm')).sum())} still on the llm route)")
    print(f"\n4. THE {len(described)} LUNARIS DISAGREEMENTS")
    for d in described.to_dict("records"):
        print(f"   - {d['id']} | {d['datasource']} | similarity {d['title_similarity']:.3f} | DataONE "
              f"{d['dataone_answer']}, Lunaris {d['lunaris_answer']} | {d['lunaris_ids']} "
              f"(Lunaris filter 3 {d['lunaris_filter3_class']}, {d['lunaris_frontend_group']}) | {d['action']}\n"
              f"       DataONE: {d['dataone_title']}\n       Lunaris: {d['lunaris_titles']}")
    print("\n5. LABELS")
    for name, c in label_counts.items():
        kept = joined[joined["label_set"] == name]
        print(f"   {name}: {c['rows']} rows, {c['labelled']} with a place label, {c['unsure']} unsure (skipped), "
              f"{len(kept)} in filter 3 and scored")
    shown = scores[scores["records"] > 0]
    print(shown.to_string(index=False))
    print(f"\n   every disagreement ({len(differ)}):")
    for r in differ.to_dict("records"):
        print(f"   - {r['kind']} | {r['label_set']} row {r['row']} | {r['route']} | label {r['label']} "
              f"({r['label_group']}), filter 3 {r['final_class']} ({r['frontend_group']}) | {r['record_id'][:70]}")


def main():
    """Build the final table, check it, write it, score the label sets, and print the report."""
    parser = argparse.ArgumentParser(description="DataONE filter 3, the final table and the scores.")
    parser.add_argument("--replace", action="store_true", help="write over an earlier filter3_final.parquet")
    parser.add_argument("--bc-boundary", default=BC_BOUNDARY, help="BC's outline as WKT (bc_boundary_gbif.wkt)")
    parser.add_argument("--results", default=RESULTS, help="where the counts, scores and disagreements CSVs go")
    args = parser.parse_args()
    check_ignored(OUT)
    if os.path.exists(OUT) and not args.replace:
        stop(f"{os.path.relpath(OUT, BACKEND)} is already there; add --replace to write over it")

    rules = read_routes()
    if len(rules) != EXPECTED_KEPT:
        stop(f"filter3_rules.parquet holds {len(rules):,} records, not {EXPECTED_KEPT:,}")
    pass1, pass2, about = lunaris_final.read_run(RUN)
    llm_ids = list(rules.index[rules["route"] == "llm"])
    check_run(about, pass1, pass2, llm_ids)
    _, records = read_input(INPUT)
    decisions = llm_decisions(pass2, records, load_bc_shape(args.bc_boundary))
    llm = {i: llm_row(pass1[i], pass2[i], decisions.get(i, (None, None))[1]) for i in llm_ids}

    union = pd.read_parquet(UNION).set_index("id", drop=False)
    differ = low_similarity_disagreements(union)
    if len(differ) != EXPECTED_DISAGREEMENTS:
        stop(f"{len(differ)} Lunaris disagreements, not {EXPECTED_DISAGREEMENTS}")
    lunaris = read_lunaris_table()
    copies = lunaris_classes(list(differ["id"]))
    added_ids = list(differ.loc[differ["dataone_answer"] == "no", "id"])
    titles = read_titles(list(rules.index) + added_ids)
    lunaris_ids = sorted({i for ids, _ in copies.values() for i in ids})
    lunaris_titles = pd.read_parquet(LUNARIS_HARVEST, columns=["id", "title"], filters=[("id", "in", lunaris_ids)])
    lunaris_titles = lunaris_titles.drop_duplicates("id").set_index("id")["title"].to_dict()
    described = describe_disagreements(differ, copies, lunaris, titles, lunaris_titles)
    added = either_yes_rows(described, union)
    if set(added["record_id"]) & set(rules.index):
        stop("a record the either-yes rule adds is already in filter 3")

    table = build(rules, titles, llm, lunaris_final.bc_only_catalogues(), added, read_hand())
    check_lunaris_groups(table, lunaris)
    check_complete(table, list(rules.index) + list(added["record_id"]))

    labels, label_counts = read_labels()
    joined = scored(labels, table)
    scores = with_nov23(score_table(joined))
    wrong = disagreements(joined)
    counts = counts_table(table)
    report(table, counts, described, scores, wrong, label_counts, joined)

    table.to_parquet(OUT + ".tmp", index=False)
    os.replace(OUT + ".tmp", OUT)
    os.makedirs(args.results, exist_ok=True)
    counts.to_csv(os.path.join(args.results, "filter3_final_counts.csv"), index=False, encoding="utf-8-sig")
    scores.to_csv(os.path.join(args.results, "filter3_final_scores.csv"), index=False, encoding="utf-8-sig")
    wrong.to_csv(os.path.join(args.results, "filter3_final_disagreements.csv"), index=False, encoding="utf-8-sig")
    print(f"\nwrote {len(table):,} rows -> {os.path.relpath(OUT, BACKEND)}; CSVs -> {args.results}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
