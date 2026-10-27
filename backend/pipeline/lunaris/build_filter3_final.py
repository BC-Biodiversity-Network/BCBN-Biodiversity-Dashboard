"""
build_filter3_final.py

The last step of the Lunaris BC filter (filter 3): put the step 1 rule
results and the full LLM run together into one table, one row per record,
apply the fixes we decided on, and write the checks for a person to read.

What goes in (read only; nothing in these is ever changed):
  --rules     step 1's output (rules_2026_10_v2.csv): every record the
              biodiversity filter kept, with bc, not_bc or needs_llm
  --run       the full LLM run folder copied back from Fir
              (fir_runs/needs_llm_qwen3.6-35b-a3b_nofa): pass1/answers.jsonl
              and pass2/answers.jsonl, one line per needs_llm record
  --harvest   the Lunaris harvest, for each record's text as harvested
  --labels    the checked label set (final_label), for the accuracy check
  --label-run the earlier 150-record Fir run, to compare with

How each record gets its final class:
  1. Step 1 said bc: the class is bc_only. Step 1 said not_bc: the class is
     elsewhere. These are decided by a rule ("rule").
  2. Step 1 said needs_llm: the class is the one code worked out from the
     model's five facts in pass 2 ("llm"); it is kept as llm_class too.
  3. A record from one of the 16 catalogues that only hold BC data
     (bc_filter_sources.csv, class bc) always goes to the front end: if the
     model's class is elsewhere, unknown, all_canada or global, the final
     class becomes bc_only ("bc_only_catalogue"), and llm_class keeps what
     the model's facts said.
  4. Some records were checked by hand ("hand"); they take the class the
     person gave, and so does every copy in their copy group (with the note
     "hand check applied via copy <record id>"); llm_class and rule_class
     stay as they were, and before_hand keeps the class before. If two hand
     checks in one copy group disagree, the script stops and lists them:
       - the DFO juvenile salmon trawl series (7 datasets, each in CanWIN and
         on open.canada.ca, 14 records; DFO_PROPOSALS);
       - 3 Hakai records about places outside BC (HAKAI_HAND). The Hakai
         catalogue is mostly the BC coast but also holds Hakai's own
         projects elsewhere; these 3 were found by listing Hakai records
         whose text names a place outside BC and none in BC (kept as
         hakai_outside_bc_candidates.csv, written by an earlier version of
         this script), and checked against their original sources.

Each class belongs to one front end group:
  bc_data   bc_only, canada_incl_bc, bc_and_outside_canada (shown)
  pending   all_canada, global, and the bc_and_outside_canada records the
            model placed there through canada_wide without saying in_bc
            yes ("national plus outside, no BC named"); the supervisor
            decides later
  dropped   elsewhere, unknown (not BC data; the rows stay in the table)

Copies of one dataset (the same UUID at the end of the record id, or the
same title and the same non-empty abstract, linked as in
build_label_sample.py) are never removed. They share a dup_group_id, and
one row per group is marked dup_show (see pick_shown for the rule).

With --previous (an earlier filter3_merged.csv), the report also shows
what changed since that run.

Outputs (--out, ignored by git):
  filter3_merged.csv          the merged table, one row per record
  bc_catalogue_changes.csv    the records the BC-only catalogue fix changed
  dfo_trawl_handcheck.csv     the DFO trawl series hand-check list
  hakai_handcheck.csv         the 3 Hakai records checked by hand
  duplicate_conflicts.csv     copy groups whose members differ in class or group
  filter3_checks.xlsx         samples with the record text as harvested
  filter3_final_report.md     the report: counts, checks and accuracy

Run, from backend/:
    python pipeline/lunaris/build_filter3_final.py \\
        --bc-boundary /Users/lucia/Desktop/BCBN/Results/bc_boundary_gbif.wkt
"""

import argparse
import json
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "llm"))
from bc_filter_rules import ELSEWHERE, as_text, bc_mentions, read_bc_places, read_sources  # noqa: E402
from build_label_sample import UUID_AT_END, dataset_keys  # noqa: E402
from geo_pass2_classes import CLASSES  # noqa: E402

DEFAULT_RULES = "data/lunaris_bc_filter/rules_2026_10_v2.csv"
DEFAULT_RUN = "data/lunaris_bc_filter/fir_runs/needs_llm_qwen3.6-35b-a3b_nofa"
DEFAULT_HARVEST = "exploration/lunaris/harvest_2026_10_20/lunaris_full_harvest.parquet"
DEFAULT_LABELS = "/Users/lucia/Desktop/BCBN/Results/label_set_2026_10/label_set_final_2026-10-26.csv"
DEFAULT_LABEL_RUN = "data/lunaris_bc_filter/fir_runs/label150_qwen3.6-35b-a3b_nofa"
DEFAULT_OUT = "data/lunaris_bc_filter/filter3_final"

# The front end group of each class (decided with the supervisor).
FRONTEND_GROUP = {
    "bc_only": "bc_data", "canada_incl_bc": "bc_data", "bc_and_outside_canada": "bc_data",
    "all_canada": "pending", "global": "pending",
    "elsewhere": "dropped", "unknown": "dropped",
}
GROUPS = ["bc_data", "pending", "dropped"]

# What step 1's classes become.
RULE_TO_CLASS = {"bc": "bc_only", "not_bc": "elsewhere"}

# The model's classes that the BC-only catalogue rule turns into bc_only.
BC_CATALOGUE_FORCED = ("elsewhere", "unknown", "all_canada", "global")
# The Hakai catalogue (BC-only, with a few of Hakai's own projects elsewhere).
HAKAI = "catalogue.hakai.org"
# The note on the bc_and_outside_canada records moved to pending.
NOTE_NATIONAL = "national plus outside, no BC named"

# The model's five facts, in the order the prompt asks for them.
FACTS = ["in_bc", "other_canada", "outside_canada", "canada_wide", "global"]

# The DFO juvenile salmon trawl series: the UUID of each dataset and the part
# of the series it covers. Each is in CanWIN and on open.canada.ca.
DFO_TRAWL_SERIES = {
    "52d04040-37fe-4936-aa2e-d46c0d5a066e": "umbrella record (the whole series)",
    "f8cafe80-862a-4881-9f16-d25b63d02830": "Central and North BC",
    "aa13418a-12f3-40d1-bfa8-83c7b8c795b0": "Vancouver Island Shelf",
    "711c33d3-32eb-4729-9a39-681fe6f6bca2": "Salish Sea",
    "64e77518-2ca2-41d9-9dae-2f159a360c57": "Southern USA",
    "2908c31f-170c-4681-b1fb-43dd6340f68d": "Alaska",
    "be0d5eb3-bff4-4f4e-bc5d-f3b3dbbee1c1": "Oceanic Waters",
}
# Who checked the DFO trawl series classes by hand, and when.
DFO_CHECKED_BY = "Lucia"
DFO_CHECKED_ON = "2026-10-27"
# The 3 Hakai records about places outside BC, checked by hand against their
# original sources (outside facts, not the harvested text): record id, the
# class, and why.
HAKAI_HAND = {
    "oai:catalogue.hakai.org:ca-cioos_35900174-c64a-493e-9a7e-390ac7e997e4": (
        "elsewhere", "Source page: collected in Cumberland Sound, Nunavut (about 64.7 to 66.5 N); no BC."),
    "oai:catalogue.hakai.org:ca-cioos_9a018fe0-8bd1-41d1-af8d-079960b71473": (
        "bc_and_outside_canada", "Paper methods: 2023 field samples from five sites in fjords on the central "
        "coast of British Columbia; experiments in Washington; Alaska data re-analysed."),
    "oai:catalogue.hakai.org:ca-cioos_c074bff6-408b-443a-bdaf-4713f0eadb95": (
        "unknown", "Source page: a guidance document on mapping kelp, not collected data; covers Alaska to Baja "
        "California; no BC data stated."),
}
HAKAI_CHECKED_BY = "Lucia"
HAKAI_CHECKED_ON = "2026-10-27"
HAKAI_SOURCE_CHECKED = "external"
# The class for each part, and why: proposed by Claude, confirmed by Lucia.
DFO_PROPOSALS = {
    "Central and North BC": (
        "bc_only", "Surveys in the central and north coast and inlets of BC, from Queen Charlotte Sound to "
        "Haida Gwaii and Hecate Strait. The model said bc_and_outside_canada because the shared opening "
        "sentence of the series names Washington, Oregon and Alaska."),
    "Vancouver Island Shelf": (
        "bc_only", "Tows on the shelf of north and west Vancouver Island and its sounds and inlets. The "
        "outside places come only from the shared opening sentence of the series."),
    "Salish Sea": (
        "bc_and_outside_canada", "Strait of Georgia (BC) plus the Strait of Juan de Fuca and Puget Sound "
        "(Washington State): BC and the USA."),
    "Southern USA": (
        "elsewhere", "Shelf and slope waters off Washington and Oregon only. BC is named only in the shared "
        "opening sentence and the keywords of the series."),
    "Alaska": (
        "elsewhere", "Shelf and slope waters of Alaska only. BC is named only in the shared opening sentence "
        "of the series."),
    "Oceanic Waters": (
        "bc_and_outside_canada", "Continental slope waters deeper than 400 m out to the Gulf of Alaska, plus "
        "slope tows from the other survey regions (which include BC) and the 2022 International Year of "
        "Salmon survey. Its box (166 W to 123 W, 40 N to 60 N) takes in BC's offshore waters but is about "
        "90% outside BC. Least sure of the seven: the part itself does not name BC, so elsewhere is also "
        "defensible if only parts that name BC waters should count."),
    "umbrella record (the whole series)": (
        "bc_and_outside_canada", "The whole series: coastal waters of BC, Washington, Oregon and Alaska, and "
        "the high seas of the Gulf of Alaska."),
}


# --- Reading ---------------------------------------------------------------

def read_rules(path):
    """Read step 1's output: one row per record, with its class, the rules that decided it, and the cross-check columns."""
    rules = pd.read_csv(path, low_memory=False)
    rules["record_id"] = rules["record_id"].astype(str)
    if rules["record_id"].duplicated().any():
        raise SystemExit(f"ERROR: {path} lists some records more than once")
    return rules


def read_jsonl_by_id(path):
    """Read a JSON lines file of results and return them by record id. The file is only read."""
    results = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            result = json.loads(line)
            results[str(result["record_id"])] = result
    return results


def read_run(run_dir):
    """
    Read a geo_filter_run.py run folder: the merged pass 1 and pass 2
    answers, by record id, and run.json. The folder is only read, never
    written to.
    """
    pass1 = read_jsonl_by_id(os.path.join(run_dir, "pass1", "answers.jsonl"))
    pass2 = read_jsonl_by_id(os.path.join(run_dir, "pass2", "answers.jsonl"))
    with open(os.path.join(run_dir, "run.json"), encoding="utf-8") as handle:
        about = json.load(handle)
    return pass1, pass2, about


def read_harvest(path, wanted):
    """
    Return the harvest rows of the wanted records, one per record id: the row
    with the latest datestamp, as step 1 and make_batches.py use it. Stop if
    a record is missing.
    """
    columns = ["id", "datestamp", "doi", "title", "subjects", "abstract", "publisher", "places", "points", "boxes"]
    harvest = pd.read_parquet(path, columns=columns)
    harvest = harvest[harvest["id"].isin(wanted)]
    harvest = harvest.sort_values("datestamp", ascending=False, kind="stable").drop_duplicates("id")
    missing = set(wanted) - set(harvest["id"])
    if missing:
        raise SystemExit(f"ERROR: {len(missing)} records are not in {path}, for example {sorted(missing)[:3]}")
    return harvest.set_index("id")


def bc_only_catalogues(path=None):
    """Return the catalogues that only hold BC data (class bc in bc_filter_sources.csv)."""
    sources = read_sources(path) if path else read_sources()
    return sorted(name for name, kind in sources.items() if kind == "bc")


# --- One record ------------------------------------------------------------

def text_or_empty(value):
    """Return a harvest text field as a string, with a missing value as an empty string."""
    return "" if value is None or (isinstance(value, float) and pd.isna(value)) else str(value)


def uuid_of(record_id):
    """Return the UUID at the end of a record id (lower case), or an empty string if it has none."""
    found = UUID_AT_END.search(str(record_id))
    return found.group(1).lower() if found else ""


def step1_rule_name(rule_class, decided_by, box_class):
    """
    Return the name of the step 1 rule (or rules, joined with "+") that
    decided a record, in plain words: "box" (a box inside BC, or outside BC
    for not_bc), "rectangle box" (BC's own bounding rectangle), "point" or
    "catalogue". Empty for a needs_llm record, which no rule decided.
    """
    if rule_class not in RULE_TO_CLASS:
        return ""
    names = []
    for rule in str(decided_by).split("+"):
        if rule == "box":
            names.append("rectangle box" if rule_class == "bc" and box_class == "bc_wide" else "box")
        elif rule == "source":
            names.append("catalogue")
        elif rule == "point":
            names.append("point")
    return "+".join(names)


def llm_columns(pass1, pass2):
    """
    Return the columns that come from the LLM run for one record: the
    model's five facts, its evidence and reason, the class code worked out
    (llm_class), whether the box rule filled in in_bc, the evidence check,
    and any error. pass1 and pass2 are the record's results, or None.
    """
    if pass2 is None:
        return {}
    columns = {"llm_error": pass2.get("error", "") or (pass1 or {}).get("error", ""),
               "pass2_input_tokens": pass2.get("input_tokens"), "attempts": pass2.get("attempts")}
    if pass1 is not None and "places" in pass1:
        columns["pass1_places"] = "; ".join(p["name"] for p in pass1["places"])
    if "answer" not in pass2:
        return columns
    answer, decision, check = pass2["answer"], pass2["decision"], pass2.get("evidence_check", {})
    columns.update({fact: answer[fact] for fact in FACTS})
    unmatched = check.get("unmatched", [])
    columns.update({
        "in_bc_used": decision["in_bc_used"],
        "evidence": " | ".join(answer["evidence"]),
        "reason": answer["reason"],
        "llm_class": decision["class"],
        "box_rule_filled": bool(decision["in_bc_filled_by_box_rule"]),
        "box_shares": ", ".join(f"{s:.3f}" for s in pass2.get("box_shares", []) if s is not None),
        "evidence_items": check.get("items", 0),
        "evidence_found": check.get("loose", 0),
        "evidence_check": ("no evidence given" if not check.get("items") else
                           "all found" if not unmatched else f"{len(unmatched)} of {check['items']} not found"),
        "evidence_not_found": " | ".join(unmatched),
        "consistency_flags": ", ".join(pass2.get("consistency_flags", [])),
    })
    return columns


def final_of(rule_class, llm_class, source, catalogues):
    """
    Return (final_class, decided_by, note) for one record, by steps 1 to 3
    at the top of the file: a rule class first, else the model's class,
    with the BC-only catalogue fix on top. (The hand check, step 4, comes
    after; see apply_hand_checks.)
    """
    if rule_class in RULE_TO_CLASS:
        return RULE_TO_CLASS[rule_class], "rule", ""
    if source in catalogues and llm_class in BC_CATALOGUE_FORCED:
        return "bc_only", "bc_only_catalogue", (f"BC-only catalogue {source}: the model's facts gave "
                                                f"{llm_class}; set to bc_only")
    return llm_class, "llm", ""


# --- The table -------------------------------------------------------------

def merge(rules, pass1, pass2, harvest, catalogues):
    """
    Return the merged table: one row per step 1 record, with the step 1
    columns, the LLM columns for needs_llm records, the final class, its
    front end group and how it was decided. Stop if the run's records are
    not exactly step 1's needs_llm records, or a needs_llm record has no
    class from the model.
    """
    table = record_rows(rules, pass1, pass2, harvest, catalogues)
    return whole_set_steps(table, harvest)


def record_rows(rules, pass1, pass2, harvest, catalogues):
    """
    Return one row per step 1 record with what can be decided from the
    record alone: the step 1 and LLM columns, and the final class by steps 1
    to 3 at the top of the file (rule, model, BC-only catalogue). The steps
    that look at other records (copy groups, hand checks, front end groups)
    are in whole_set_steps. Stop as merge says.
    """
    needs = set(rules.loc[rules["bc_class"] == "needs_llm", "record_id"])
    if set(pass2) != needs or set(pass1) != needs:
        raise SystemExit(f"ERROR: the run has {len(pass2)} pass 2 records and step 1 has {len(needs)} needs_llm "
                         f"records, and they are not the same records")
    rows = []
    for rule in rules.itertuples(index=False):
        record_id = rule.record_id
        text = harvest.loc[record_id]
        row = {
            "record_id": record_id, "uuid": uuid_of(record_id), "doi": text_or_empty(text["doi"]),
            "title": text_or_empty(text["title"]), "source": rule.source,
            "publisher": text_or_empty(text["publisher"]),
            "rule_class": rule.bc_class,
            "rule_name": step1_rule_name(rule.bc_class, rule.decided_by, rule.box_class),
            "rule_decided_by": rule.decided_by, "rule_evidence": rule.evidence,
            "box_class": rule.box_class, "box_share": rule.box_share,
            "rule_text_bc": ", ".join(str(v) for v in (rule.text_title, rule.text_keywords, rule.text_abstract)
                                      if isinstance(v, str) and v),
            "bc_only_catalogue": rule.source in catalogues,
        }
        row.update(llm_columns(pass1.get(record_id), pass2.get(record_id)))
        if rule.bc_class == "needs_llm" and not row.get("llm_class"):
            raise SystemExit(f"ERROR: {record_id} needs the LLM but has no class from the model "
                             f"({row.get('llm_error')}); look at it before going on")
        final, decided_by, note = final_of(rule.bc_class, row.get("llm_class", ""), rule.source, catalogues)
        row.update(final_class=final, decided_by=decided_by, note=note)
        rows.append(row)
    return pd.DataFrame(rows)


def whole_set_steps(table, harvest):
    """
    Apply, in place and in this order, the steps that look across records:
    copy groups, the hand checks (passed on to whole copy groups), and the
    front end groups. Return the table.
    """
    assign_copy_groups(table, harvest)
    apply_hand_checks(table)
    set_frontend_groups(table)
    assert set(table["final_class"]) <= set(CLASSES)
    return table


def add_note(old, new):
    """Return a note with another note added after it ("; " between them)."""
    return f"{old}; {new}" if old else new


def direct_hand_checks(table):
    """
    Return the records checked by hand themselves, as {record id: (class,
    note)}: the DFO trawl series (by UUID, from DFO_PROPOSALS) and the 3
    Hakai records (by record id, from HAKAI_HAND).
    """
    checks = {}
    for row in table[table["uuid"].isin(DFO_TRAWL_SERIES)].itertuples():
        part = DFO_TRAWL_SERIES[row.uuid]
        checks[row.record_id] = (DFO_PROPOSALS[part][0], f"DFO trawl series, {part}: checked by hand "
                                                         f"({DFO_CHECKED_BY}, {DFO_CHECKED_ON})")
    for row in table[table["record_id"].isin(HAKAI_HAND)].itertuples():
        checks[row.record_id] = (HAKAI_HAND[row.record_id][0], f"Hakai record outside BC: checked by hand against "
                                                               f"the original source ({HAKAI_CHECKED_BY}, "
                                                               f"{HAKAI_CHECKED_ON})")
    return checks


def hand_check_conflicts(table, checks):
    """
    Return the copy groups in which hand checks disagree, as a list of
    (group id, [(record id, class), ...]); empty when they all agree.
    """
    found = []
    groups = table.set_index("record_id")["dup_group_id"]
    by_group = {}
    for record_id, (cls, _) in checks.items():
        if groups[record_id]:
            by_group.setdefault(groups[record_id], []).append((record_id, cls))
    for group, members in sorted(by_group.items()):
        if len({cls for _, cls in members}) > 1:
            found.append((group, sorted(members)))
    return found


def apply_hand_checks(table):
    """
    Give the hand-checked records their class, in place, and pass it on to
    every copy in their copy group (run after assign_copy_groups). Each gets
    decided_by "hand" and a note; a copy's note says which record it took
    the check from ("hand check applied via copy <record id>"). before_hand
    keeps the class and how it was decided before, for every record the hand
    check changed; llm_class and rule_class are left as they were. Stop,
    listing them, if two hand checks in one copy group disagree.
    """
    checks = direct_hand_checks(table)
    conflicts = hand_check_conflicts(table, checks)
    if conflicts:
        lines = [f"  {group}: " + "; ".join(f"{r} {c}" for r, c in members) for group, members in conflicts]
        raise SystemExit("ERROR: hand checks disagree within a copy group; decide these first:\n" + "\n".join(lines))
    via_copy = {}
    for record_id in sorted(checks):
        group = table.loc[table["record_id"] == record_id, "dup_group_id"].iloc[0]
        if group:
            for copy in table.loc[table["dup_group_id"] == group, "record_id"]:
                if copy not in checks and copy not in via_copy:
                    via_copy[copy] = (checks[record_id][0], f"hand check applied via copy {record_id}")
    table["before_hand"] = ""
    for record_id, (cls, note) in {**checks, **via_copy}.items():
        index = table.index[table["record_id"] == record_id][0]
        table.at[index, "before_hand"] = f"{table.at[index, 'final_class']} ({table.at[index, 'decided_by']})"
        table.at[index, "final_class"] = cls
        table.at[index, "decided_by"] = "hand"
        table.at[index, "note"] = add_note(table.at[index, "note"], note)


def is_national_plus_outside(row):
    """
    Return True for a bc_and_outside_canada record the model placed there
    through canada_wide without saying in_bc yes: national plus outside,
    with no BC named. Only the model's own classes count (decided_by llm).
    """
    return (row["final_class"] == "bc_and_outside_canada" and row["decided_by"] == "llm"
            and row.get("in_bc_used") != "yes")


def set_frontend_groups(table):
    """
    Set each record's front end group, in place: the group of its final
    class, except that the national-plus-outside records go to pending, with
    a note.
    """
    table["frontend_group"] = table["final_class"].map(FRONTEND_GROUP)
    for index in table.index:
        if is_national_plus_outside(table.loc[index]):
            table.at[index, "frontend_group"] = "pending"
            table.at[index, "note"] = add_note(table.at[index, "note"], NOTE_NATIONAL)


def place_words(record, places_pattern):
    """
    Return the BC places and the places outside BC that a record's text
    names, as two strings: its title, abstract, keywords and place field
    read with step 1's lists (bc_filter_rules: BC by name, B.C., BC and the
    places in bc_places.csv; outside BC the other provinces, territories,
    nearby waters, countries and regions in ELSEWHERE).
    """
    text = " ; ".join(as_text(record[f]) for f in ("title", "abstract", "subjects", "places"))
    inside = bc_mentions(text, places_pattern)
    outside = list(dict.fromkeys(ELSEWHERE.findall(text)))
    return ", ".join(inside), ", ".join(outside)


def copy_classes(table, record_id):
    """
    Return the classes of a record's copies that are not from a BC-only
    catalogue, as "catalogue class (how decided)" joined with "; ", or an
    empty string: the class before any hand check (before_hand), and the
    class now when a hand check changed it. For a record only its catalogue
    decided, a copy elsewhere is the nearest result there is.
    """
    group = table.loc[table["record_id"] == record_id, "dup_group_id"].iloc[0]  # run after add_duplicates
    if not group:
        return ""
    others = table[(table["dup_group_id"] == group) & (table["record_id"] != record_id)
                   & ~table["bc_only_catalogue"]]
    return "; ".join(f"{o.source} {o.before_hand}, now {o.final_class} (hand)" if o.before_hand else
                     f"{o.source} {o.final_class} ({o.decided_by})" for o in others.itertuples())


def pick_shown(members):
    """
    Return the record id to show on the front end for one copy group (a
    DataFrame of its rows). The rule: the copy in the best front end group
    (bc_data, then pending, then dropped), so a dataset any copy places in
    BC is shown; among those, the lowest record id, as build_label_sample.py
    keeps one copy (so the choice is the same every run).
    """
    rank = members["frontend_group"].map(GROUPS.index)
    best = members[rank == rank.min()]
    return min(best["record_id"])


def assign_copy_groups(table, harvest):
    """
    Add the copy group columns to the table, in place: dup_group_id (G0001
    and on, for groups of two or more; empty for a record with no copy) and
    dup_group_size. No row is removed.
    """
    texts = pd.DataFrame({"record_id": table["record_id"], "title": table["title"],
                          "abstract": table["record_id"].map(harvest["abstract"])})
    keys = table["record_id"].map(dataset_keys(texts))
    sizes = keys.map(keys.value_counts())
    numbered = {key: f"G{n:04d}" for n, key in enumerate(sorted(keys[sizes > 1].unique()), start=1)}
    table["dup_group_id"] = keys.map(numbered).fillna("")
    table["dup_group_size"] = sizes.astype(int)
    return table


def add_duplicates(table, harvest):
    """
    Mark the one row per copy group to show on the front end, in place:
    dup_show is True for that row (pick_shown) and for every record with no
    copy. The copy groups come from merge (assign_copy_groups); they are
    worked out here if missing. No row is removed.
    """
    if "dup_group_id" not in table.columns:
        assign_copy_groups(table, harvest)
    grouped = table[table["dup_group_id"] != ""]
    shown = {group: pick_shown(members) for group, members in grouped.groupby("dup_group_id")}
    table["dup_show"] = [group == "" or shown[group] == record_id
                         for group, record_id in zip(table["dup_group_id"], table["record_id"])]
    return table


def same_harvest_text(record_ids, harvest):
    """Return True if every record has the same title, abstract, keywords, places, points and boxes in the harvest."""
    fields = ["title", "abstract", "subjects", "places", "points", "boxes"]
    texts = {json.dumps([harvest.loc[r, f].tolist() if hasattr(harvest.loc[r, f], "tolist") else harvest.loc[r, f]
                         for f in fields], default=str, ensure_ascii=False) for r in record_ids}
    return len(texts) == 1


def duplicate_conflicts(table, harvest):
    """
    Return the rows of every copy group whose members differ in final_class
    or in frontend_group, with whether the copies' harvested text is the
    same (if it is not, the model read different records), and the number
    of groups that differ in each.
    """
    grouped = table[table["dup_group_id"] != ""].groupby("dup_group_id")
    differ_class = grouped["final_class"].nunique()
    differ_group = grouped["frontend_group"].nunique()
    wanted = set(differ_class[differ_class > 1].index) | set(differ_group[differ_group > 1].index)
    rows = table[table["dup_group_id"].isin(wanted)].copy()
    rows["differs_in"] = rows["dup_group_id"].map(
        lambda g: "frontend_group" if differ_group[g] > 1 else "final_class only")
    same = {g: same_harvest_text(m["record_id"], harvest) for g, m in rows.groupby("dup_group_id")}
    rows["same_harvest_text"] = rows["dup_group_id"].map(same)
    columns = ["dup_group_id", "differs_in", "same_harvest_text", "record_id", "source", "title", "decided_by",
               "rule_class", "llm_class", "final_class", "frontend_group", "dup_show"]
    return rows.sort_values(["dup_group_id", "record_id"])[columns], int((differ_class > 1).sum()), \
        int((differ_group > 1).sum())


def hakai_handcheck(table, harvest, catalogues):
    """
    Return the hand-check list for the 3 Hakai records (HAKAI_HAND): record
    id, UUID, title, the outside-BC place words in its text (place_words),
    the class the catalogue rule gave it, its copies' classes, the class
    checked by hand and why, who checked it and when, and that the check
    used outside sources. Run it after add_duplicates.
    """
    pattern = read_bc_places()
    rows = []
    for row in table[table["record_id"].isin(HAKAI_HAND)].itertuples(index=False):
        rows.append({"record_id": row.record_id, "uuid": row.uuid, "title": row.title,
                     "outside_bc_place_words": place_words(harvest.loc[row.record_id], pattern)[1],
                     "catalogue_class": final_of(row.rule_class, row.llm_class or "", row.source, catalogues)[0],
                     "copy_class": copy_classes(table, row.record_id), "final_class": row.final_class,
                     "reason": HAKAI_HAND[row.record_id][1], "checked_by": HAKAI_CHECKED_BY,
                     "date": HAKAI_CHECKED_ON, "source_checked": HAKAI_SOURCE_CHECKED})
    return pd.DataFrame(rows)


def dfo_handcheck(table, harvest):
    """
    Return the hand-check list for the DFO trawl series: one row per record
    of its 7 datasets (14 records), with the first 300 characters of the
    abstract as harvested, the model's class, the class checked by hand and
    why, and who checked it and when.
    """
    rows = []
    for row in table[table["uuid"].isin(DFO_TRAWL_SERIES)].itertuples(index=False):
        part = DFO_TRAWL_SERIES[row.uuid]
        proposed, reason = DFO_PROPOSALS[part]
        rows.append({"record_id": row.record_id, "uuid": row.uuid, "catalogue": row.source, "title": row.title,
                     "series_part": part, "abstract_first_300": str(harvest.loc[row.record_id, "abstract"])[:300],
                     "llm_class": row.llm_class, "proposed_class": proposed, "reason": reason,
                     "checked_by": DFO_CHECKED_BY, "date": DFO_CHECKED_ON})
    order = list(DFO_PROPOSALS)
    checked = pd.DataFrame(rows)
    checked["part_order"] = checked["series_part"].map(order.index)
    return checked.sort_values(["part_order", "catalogue"]).drop(columns="part_order")


# --- Main ------------------------------------------------------------------

def main():
    """Build the merged table and the checks, and write them with the report."""
    import filter3_checks as checks

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("--rules", default=DEFAULT_RULES)
    parser.add_argument("--run", default=DEFAULT_RUN)
    parser.add_argument("--harvest", default=DEFAULT_HARVEST)
    parser.add_argument("--labels", default=DEFAULT_LABELS)
    parser.add_argument("--label-run", default=DEFAULT_LABEL_RUN)
    parser.add_argument("--bc-boundary", required=True, help="bc_boundary_gbif.wkt, to measure box shares")
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--previous", help="an earlier filter3_merged.csv, to report what changed")
    args = parser.parse_args()
    if os.path.abspath(args.out).startswith(os.path.abspath(args.run)):
        raise SystemExit("ERROR: --out must not be inside the run folder; the run folder is only read")

    rules = read_rules(args.rules)
    pass1, pass2, about = read_run(args.run)
    harvest = read_harvest(args.harvest, set(rules["record_id"]))
    catalogues = bc_only_catalogues()
    table = merge(rules, pass1, pass2, harvest, catalogues)
    add_duplicates(table, harvest)
    conflicts, n_class, n_group = duplicate_conflicts(table, harvest)
    handcheck = dfo_handcheck(table, harvest)
    changes = table[table["decided_by"] == "bc_only_catalogue"][["record_id", "source", "title", "llm_class"]]
    hakai = hakai_handcheck(table, harvest, catalogues)

    os.makedirs(args.out, exist_ok=True)
    table.to_csv(os.path.join(args.out, "filter3_merged.csv"), index=False, encoding="utf-8")
    changes.to_csv(os.path.join(args.out, "bc_catalogue_changes.csv"), index=False, encoding="utf-8")
    handcheck.to_csv(os.path.join(args.out, "dfo_trawl_handcheck.csv"), index=False, encoding="utf-8")
    hakai.to_csv(os.path.join(args.out, "hakai_handcheck.csv"), index=False, encoding="utf-8")
    conflicts.to_csv(os.path.join(args.out, "duplicate_conflicts.csv"), index=False, encoding="utf-8")

    inputs = {"rules": args.rules, "run": args.run, "harvest": args.harvest, "labels": args.labels,
              "label_run": args.label_run, "sources": os.path.join(HERE, "bc_filter_sources.csv"),
              "bc_boundary": args.bc_boundary, "about": about, "previous": args.previous}
    report = checks.run_checks(table, rules, pass1, pass2, harvest, catalogues, conflicts, (n_class, n_group),
                               changes, handcheck, hakai, inputs, args.out)
    path = os.path.join(args.out, "filter3_final_report.md")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(report)
    print(report)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
