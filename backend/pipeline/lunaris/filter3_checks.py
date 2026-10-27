"""
filter3_checks.py

The checks on filter 3's merged table (build_filter3_final.py), written for
a person to read: counts, samples drawn with a fixed seed, the accuracy on
the labelled records, and the final counts. run_checks writes the samples
to an Excel file, with each record's text exactly as harvested, and
returns the report as Markdown.

Nothing here changes the merged table, the run folder or any input.
"""

import json
import os
import re
import sys
from collections import Counter

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "llm"))
from build_filter3_final import (  # noqa: E402
    BC_CATALOGUE_FORCED, DFO_CHECKED_BY, DFO_CHECKED_ON, FACTS, FRONTEND_GROUP, GROUPS, HAKAI, HAKAI_CHECKED_BY,
    HAKAI_CHECKED_ON, NOTE_NATIONAL, read_jsonl_by_id)
from geo_pass2_classes import CLASSES  # noqa: E402
from geo_pass2_score import SHOWN  # noqa: E402
from ollama_client import OLLAMA_NUM_CTX  # noqa: E402

# The seed for every sample, so the same records come out every time.
SEED = 20261027
# Prompts above this share of the context are reported as close to the limit.
NEAR_LIMIT = 0.8

# Words in the model's reason that point to a lab, a computer or a
# literature study rather than data collected somewhere (for splitting the
# unknown records; it only sorts them, it decides nothing).
NOT_FIELD = re.compile(
    r"laborator|\blab\b|in vitro|greenhouse|growth chamber|aquari|captiv|simulat|computational|"
    r"software|algorithm|machine learning|meta-analys|systematic review|literature|synthesis|"
    r"not biodiversity|museum|herbarium specimens|genom|sequenc|phylogen|experiment", re.IGNORECASE)
# Words in the reason that say the only place named is an address or publisher.
ADDRESS_ONLY = re.compile(r"affiliat|publisher|address|institution|author", re.IGNORECASE)

# Excel refuses some control characters; they are shown as this instead (and counted).
EXCEL_BAD = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


# --- Record text as harvested ----------------------------------------------

def as_harvested(value):
    """Return a harvest field as text: a string as it is, a list or array as JSON, missing as empty."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    if isinstance(value, str):
        return value
    if hasattr(value, "tolist"):
        value = value.tolist()
    return json.dumps(value, ensure_ascii=False, default=str)


def with_text(rows, harvest):
    """Return rows (a DataFrame with record_id) with the record's title, abstract, keywords, places, points and boxes as harvested."""
    text = harvest.loc[rows["record_id"]]
    out = rows.copy()
    for column in ["title", "abstract", "subjects", "places", "points", "boxes"]:
        out[f"harvest_{column}"] = [as_harvested(v) for v in text[column]]
    return out


def excel_safe(frame, counter):
    """
    Return a copy of a DataFrame that Excel can store: control characters
    Excel refuses are replaced by a space (counter["replaced"] counts the
    cells changed), and a cell longer than Excel's 32,767 characters is cut
    (counter["cut"] counts those). Everything else is kept exactly.
    """
    out = frame.copy()
    for column in out.columns:
        # Text columns can be of type object or of pandas' string type.
        if not (out[column].dtype == object or pd.api.types.is_string_dtype(out[column])):
            continue
        values = []
        for value in out[column]:
            if isinstance(value, str):
                if EXCEL_BAD.search(value):
                    value = EXCEL_BAD.sub(" ", value)
                    counter["replaced"] += 1
                if len(value) > 32767:
                    value = value[:32767]
                    counter["cut"] += 1
            values.append(value)
        out[column] = values
    return out


# --- Checks ----------------------------------------------------------------

def step1_counts(table):
    """Return the counts of step 1's bc and not_bc records, by the rule that decided them."""
    decided = table[table["rule_class"].isin(["bc", "not_bc"])]
    return decided.groupby(["rule_class", "rule_name"]).size().rename("records").reset_index()


def failures(pass1, pass2):
    """Return the failed records of each pass, the retried ones, and the prompts close to or at the context limit."""
    failed = {name: sorted(r for r, result in results.items() if "error" in result)
              for name, results in (("pass 1", pass1), ("pass 2", pass2))}
    retried = {name: sorted(r for r, result in results.items() if result.get("attempts", 1) > 1)
               for name, results in (("pass 1", pass1), ("pass 2", pass2))}
    context_full = sorted(r for results in (pass1, pass2) for r, result in results.items()
                          if "filled the whole context" in result.get("error", ""))
    tokens = {name: [result.get("input_tokens") or 0 for result in results.values()]
              for name, results in (("pass 1", pass1), ("pass 2", pass2))}
    near = {name: sorted((result.get("input_tokens") or 0, r) for r, result in results.items()
                         if (result.get("input_tokens") or 0) > NEAR_LIMIT * OLLAMA_NUM_CTX)
            for name, results in (("pass 1", pass1), ("pass 2", pass2))}
    return failed, retried, context_full, {k: max(v) for k, v in tokens.items()}, near


def box_fill_sample(table, harvest, size=25):
    """Return the records whose in_bc the box rule filled in, and a sample of them with the record text and each box's share in and outside BC."""
    filled = table[table["box_rule_filled"] == True]  # noqa: E712 (the column holds True, False and empty)
    sample = filled.sample(n=min(size, len(filled)), random_state=SEED).sort_values("record_id")
    sample = sample.assign(
        box_share_in_bc=sample["box_shares"],
        box_share_outside_bc=sample["box_shares"].map(
            lambda s: ", ".join(f"{1 - float(x):.3f}" for x in s.split(", ")) if s else ""))
    columns = ["record_id", "source", "box_share_in_bc", "box_share_outside_bc", *FACTS, "in_bc_used",
               "evidence", "reason", "llm_class", "final_class", "frontend_group"]
    return filled, with_text(sample[columns], harvest)


def unknown_reason(row):
    """
    Return why a record came out unknown, from the model's facts first and
    then the words of its reason (a rough sort, for reading only):
    places in other provinces only, places outside Canada only, both, a lab
    or non-field study, only an address or publisher named, no place at all,
    or some places but not enough to say.
    """
    other, outside = row["other_canada"] == "yes", row["outside_canada"] == "yes"
    if other and outside:
        return "names other provinces and places outside Canada; BC unclear"
    if other:
        return "names other provinces only; BC unclear"
    if outside:
        return "names places outside Canada only (such as western North America); BC unclear"
    if NOT_FIELD.search(str(row["reason"])):
        return "lab, computer, literature or other non-field study (by the reason's words)"
    if ADDRESS_ONLY.search(str(row["reason"])):
        return "only an affiliation, address or publisher named (by the reason's words)"
    if not row["evidence"]:
        return "no place at all, no evidence given"
    return "some text quoted, but no place that says where"


def unknown_check(table, harvest, size=30):
    """Return the unknown records' reasons counted, and all of them or a sample of 30 (if over 100) with the record text."""
    unknown = table[table["final_class"] == "unknown"].copy()
    unknown["unknown_why"] = unknown.apply(unknown_reason, axis=1)
    counts = unknown["unknown_why"].value_counts()
    shown = unknown if len(unknown) <= 100 else unknown.sample(n=size, random_state=SEED)
    columns = ["record_id", "source", "unknown_why", *FACTS, "evidence", "reason", "llm_class", "final_class"]
    return counts, len(unknown), with_text(shown.sort_values("record_id")[columns], harvest)


def bc_outside_routes(table, harvest):
    """
    Return how the bc_and_outside_canada records got there: through outside
    and in_bc yes, or through outside and canada_wide yes without in_bc yes
    (these may be national plus outside rather than clearly BC); and the
    second group with the record text.
    """
    rows = table[(table["final_class"] == "bc_and_outside_canada") & (table["decided_by"] == "llm")]
    via_bc = rows[rows["in_bc_used"] == "yes"]
    via_canada = rows[rows["in_bc_used"] != "yes"]
    columns = ["record_id", "source", *FACTS, "in_bc_used", "evidence", "reason", "final_class"]
    return len(via_bc), via_canada, with_text(via_canada.sort_values("record_id")[columns], harvest)


def catalogue_check(table, harvest, source, size=20):
    """Return one catalogue's counts per final class, and a sample of its bc_data and pending records with the record text."""
    rows = table[table["source"] == source]
    counts = rows["final_class"].value_counts().reindex(CLASSES, fill_value=0)
    pool = rows[rows["frontend_group"].isin(["bc_data", "pending"])]
    sample = pool.sample(n=min(size, len(pool)), random_state=SEED).sort_values("record_id")
    columns = ["record_id", "source", "decided_by", "rule_name", *FACTS, "evidence", "reason", "llm_class",
               "final_class", "frontend_group"]
    return counts, with_text(sample[columns], harvest)


def evidence_totals(table):
    """Return the evidence items given, found in the record, and not found, over all answered records."""
    answered = table[table["llm_class"].notna() & (table["llm_class"] != "")]
    items = int(answered["evidence_items"].sum())
    found = int(answered["evidence_found"].sum())
    records_missing = int((answered["evidence_check"].str.contains("not found", na=False)).sum())
    return items, found, items - found, records_missing


# --- Accuracy on the labelled records ---------------------------------------

def score(truth, answers, answer_groups=None):
    """
    Return the scores of a list of answers against the labels, as
    geo_pass2_score.py scores them: 7-class right, shown on the site but
    should not be, not shown but should be (SHOWN: every class but
    elsewhere and unknown); plus the new front end groups: group right,
    put in bc_data wrongly, kept out of bc_data wrongly. answer_groups gives
    each answer's front end group when it is not just its class's group (the
    merged table's frontend_group); the label's group is its class's.
    """
    pairs = list(zip(truth, answers))
    if answer_groups is None:
        answer_groups = [FRONTEND_GROUP.get(a, "failed") for a in answers]
    group = [(FRONTEND_GROUP[t], g) for t, g in zip(truth, answer_groups)]
    return {
        "n": len(pairs),
        "exact": sum(a == t for t, a in pairs),
        "shown_wrongly": sum(a in SHOWN and t not in SHOWN for t, a in pairs),
        "hidden_wrongly": sum(t in SHOWN and a not in SHOWN for t, a in pairs),
        "group_right": sum(a == t for t, a in group),
        "into_bc_data_wrongly": sum(a == "bc_data" and t != "bc_data" for t, a in group),
        "out_of_bc_data_wrongly": sum(t == "bc_data" and a != "bc_data" for t, a in group),
    }


def compare_runs(ids, full2, earlier2, full1, earlier1):
    """
    Compare the full run with the earlier 150-record run on the same
    records: for each record whose class differs, whether the pass 1 prompt,
    the places pass 1 kept and the pass 2 prompt were the same, and the
    attempts. Return the differing rows and the counts of identical prompts.
    """
    rows = []
    same = Counter()
    for record_id in ids:
        a, b = full2[record_id], earlier2[record_id]
        p1_same = full1[record_id].get("prompt_sha1") == earlier1[record_id].get("prompt_sha1")
        places_same = full1[record_id].get("places") == earlier1[record_id].get("places")
        p2_same = a.get("prompt_sha1") == b.get("prompt_sha1")
        same_facts = "answer" in a and "answer" in b and all(a["answer"][f] == b["answer"][f] for f in FACTS)
        same.update(pass1_prompt=p1_same, pass1_places=places_same, pass2_prompt=p2_same,
                    pass2_facts=same_facts, pass2_facts_same_prompt=same_facts and p2_same,
                    pass2_answer=a.get("answer") == b.get("answer"))
        class_a = a["decision"]["class"] if "decision" in a else "failed"
        class_b = b["decision"]["class"] if "decision" in b else "failed"
        if class_a != class_b:
            rows.append({"record_id": record_id, "full_run_class": class_a, "label150_run_class": class_b,
                         "pass1_prompt_same": p1_same, "pass1_places_same": places_same, "pass2_prompt_same": p2_same,
                         "full_facts": {f: a["answer"][f] for f in FACTS} if "answer" in a else None,
                         "label150_facts": {f: b["answer"][f] for f in FACTS} if "answer" in b else None,
                         "attempts": (a.get("attempts"), b.get("attempts"))})
    return pd.DataFrame(rows), same


def label_check(table, labels_path, label_run, pass1, pass2):
    """
    Score the merged table on the label set: the 150 needs_llm records (the
    model's class as llm_class and the final class), compared with the
    earlier 150-record run; and the 50 rule-labelled records against step 1.
    Return a dict of results.
    """
    labels = pd.read_csv(labels_path, keep_default_na=False)
    by_id = table.set_index("record_id")
    llm = labels[labels["group"] == "needs_llm"].sort_values("label_id")
    earlier2 = read_jsonl_by_id(os.path.join(label_run, "pass2", "answers.jsonl"))
    earlier1 = read_jsonl_by_id(os.path.join(label_run, "pass1", "answers.jsonl"))
    truth = list(llm["final_label"])
    ids = list(llm["record_id"])
    earlier = [earlier2[r]["decision"]["class"] if "decision" in earlier2[r] else "failed" for r in ids]
    differ, same = compare_runs(ids, pass2, earlier2, pass1, earlier1)
    wrong = llm.assign(llm_class=[by_id.loc[r, "llm_class"] for r in ids],
                       final_class=[by_id.loc[r, "final_class"] for r in ids], label150_run=earlier)
    wrong = wrong[(wrong["llm_class"] != wrong["final_label"]) | (wrong["label150_run"] != wrong["final_label"])]
    ruled = labels[labels["group"] != "needs_llm"].sort_values("label_id")
    ruled = ruled.assign(final_class=[by_id.loc[r, "final_class"] for r in ruled["record_id"]],
                         rule_name=[by_id.loc[r, "rule_name"] for r in ruled["record_id"]])
    return {
        "n": len(llm),
        "full_llm": score(truth, [by_id.loc[r, "llm_class"] for r in ids]),
        "full_final": score(truth, [by_id.loc[r, "final_class"] for r in ids],
                            [by_id.loc[r, "frontend_group"] for r in ids]),
        "earlier": score(truth, earlier),
        "differ": differ, "same": same, "wrong": wrong,
        "external": llm[llm["true_label_external"] != ""],
        "ruled": ruled, "ruled_score": score(list(ruled["final_label"]), list(ruled["final_class"]),
                                             [by_id.loc[r, "frontend_group"] for r in ruled["record_id"]]),
    }


# --- Final counts ----------------------------------------------------------

def final_counts(table):
    """Return the counts per final class and per front end group, for all rows and after keeping one row per copy group, and by decided_by."""
    shown = table[table["dup_show"]]
    by_class = pd.DataFrame({"all rows": table["final_class"].value_counts(),
                             "one per copy group": shown["final_class"].value_counts()}).reindex(CLASSES).fillna(0)
    by_group = pd.DataFrame({"all rows": table["frontend_group"].value_counts(),
                             "one per copy group": shown["frontend_group"].value_counts()}).reindex(GROUPS).fillna(0)
    class_by = pd.crosstab(table["final_class"], table["decided_by"]).reindex(CLASSES).fillna(0)
    group_by = pd.crosstab(table["frontend_group"], table["decided_by"]).reindex(GROUPS).fillna(0)
    shown_class_by = pd.crosstab(shown["final_class"], shown["decided_by"]).reindex(CLASSES).fillna(0)
    shown_group_by = pd.crosstab(shown["frontend_group"], shown["decided_by"]).reindex(GROUPS).fillna(0)
    return by_class.astype(int), by_group.astype(int), class_by.astype(int), group_by.astype(int), \
        shown_class_by.astype(int), shown_group_by.astype(int)


def changes_since(table, previous_path):
    """
    Compare the merged table with an earlier one (filter3_merged.csv from
    an earlier run): return the earlier table, and the records whose final
    class, front end group or decided_by changed, with both values.
    """
    before = pd.read_csv(previous_path, keep_default_na=False, low_memory=False)
    before["dup_show"] = before["dup_show"].astype(str) == "True"
    joined = table.merge(before[["record_id", "final_class", "frontend_group", "decided_by", "dup_show"]],
                         on="record_id", suffixes=("", "_before"), how="left", validate="one_to_one")
    changed = joined[(joined["final_class"] != joined["final_class_before"])
                     | (joined["frontend_group"] != joined["frontend_group_before"])
                     | (joined["decided_by"] != joined["decided_by_before"])]
    columns = ["record_id", "source", "title", "llm_class", "final_class_before", "final_class",
               "frontend_group_before", "frontend_group", "decided_by_before", "decided_by", "note"]
    return before, changed[columns]


def with_change(now, before):
    """Return a counts table with a change column after each column: now minus before."""
    out = pd.DataFrame(index=now.index)
    for column in now.columns:
        out[column] = now[column]
        out[f"change ({column})"] = [f"{v:+,}" if v else "0" for v in now[column] - before[column]]
    return out


# --- Report ----------------------------------------------------------------

def md_table(frame, index=True):
    """Return a DataFrame as a Markdown table, with thousands separators for whole numbers."""
    frame = frame.reset_index() if index else frame
    head = "| " + " | ".join(str(c) for c in frame.columns) + " |"
    rule = "|" + "|".join("---" for _ in frame.columns) + "|"
    body = ["| " + " | ".join(f"{v:,}" if isinstance(v, (int,)) and not isinstance(v, bool) else
                              str(v).replace("|", "/").replace("\n", " ") for v in row) + " |"
            for row in frame.itertuples(index=False)]
    return "\n".join([head, rule, *body])


def pct(part, whole):
    """Return part of whole as 'part of whole (share%)'."""
    return f"{part} of {whole} ({part / whole:.1%})" if whole else f"{part} of 0"


def score_line(name, s):
    """Return one Markdown table row of scores."""
    return (f"| {name} | {pct(s['exact'], s['n'])} | {s['shown_wrongly']} | {s['hidden_wrongly']} | "
            f"{pct(s['group_right'], s['n'])} | {s['into_bc_data_wrongly']} | {s['out_of_bc_data_wrongly']} |")


def run_checks(table, rules, pass1, pass2, harvest, catalogues, conflicts, conflict_counts, changes, handcheck,
               hakai, inputs, out):
    """
    Run every check, write the samples to filter3_checks.xlsx in out, and
    return the report as Markdown. handcheck and hakai are the DFO and Hakai
    hand-check lists.
    """
    counter = Counter()
    sheets = {}
    lines = ["# Filter 3 final: merged table and checks", "",
             "Written by `backend/pipeline/lunaris/build_filter3_final.py` (and `filter3_checks.py`). For "
             "review; nothing is committed. This run applies Lucia's decisions of 2026-10-27: the DFO trawl "
             "classes, the wider BC-only catalogue rule, and pending for the national-plus-outside records. The "
             "3 Hakai records outside BC are set by hand.", "",
             "## Inputs used", ""]
    about = inputs["about"]
    lines += [f"- Step 1 v2 rules: `{inputs['rules']}` ({len(rules):,} records: "
              + ", ".join(f"{k} {v:,}" for k, v in rules["bc_class"].value_counts().items()) + ")",
              f"- Full LLM run: `{inputs['run']}` (read only): {about['model']} digest "
              f"{about['model_digest'][:12]}, Ollama {about['ollama_version']}, pass 1 {about['pass1_prompt']}, "
              f"pass 2 {about['pass2_prompt']}, context {about['num_ctx']:,}; merged files `pass1/answers.jsonl` "
              f"({len(pass1):,} lines) and `pass2/answers.jsonl` ({len(pass2):,} lines)",
              f"- Harvest (record text): `{inputs['harvest']}`",
              f"- Labels: `{inputs['labels']}` (final_label; true_label_external not scored)",
              f"- Earlier 150-record run: `{inputs['label_run']}`",
              f"- BC-only catalogues: `{inputs['sources']}`, class bc: {len(catalogues)} catalogues",
              f"- BC outline for box shares: `{inputs['bc_boundary']}` (the shares were measured during the run)",
              ""]

    # The short list of things to look at comes first; it is filled in at the end.
    summary_at = len(lines)

    # 1. Step 1.
    lines += ["## 1. Merged table", "",
              f"`filter3_merged.csv`: {len(table):,} rows, one per record ({table['record_id'].nunique():,} "
              f"distinct ids). Step 1 bc became bc_only and not_bc became elsewhere, by the rule that decided them:",
              "", md_table(step1_counts(table), index=False), ""]

    # 2. BC-only catalogues.
    bc_cat = table[table["bc_only_catalogue"]]
    read = bc_cat[bc_cat["rule_class"] == "needs_llm"]
    lines += ["## 2. BC-only catalogues", "",
              f"{len(bc_cat):,} records come from the {len(catalogues)} BC-only catalogues: "
              f"{(bc_cat['rule_class'] == 'bc').sum():,} decided bc by step 1, {len(read):,} sent to the LLM (step "
              f"1's rules disagreed, or the record has a huge box). The rule: a record from these catalogues whose "
              f"LLM class is {', '.join(BC_CATALOGUE_FORCED)} becomes bc_only (decided_by bc_only_catalogue; "
              f"llm_class kept). Records this rule changed: {len(changes)} (`bc_catalogue_changes.csv`). BC-only "
              f"catalogue records in bc_data: {(bc_cat['frontend_group'] == 'bc_data').sum():,} of {len(bc_cat):,}"
              f" (outside bc_data: " + (", ".join(f"{r.final_class} ({r.decided_by})" for r in
                                                 bc_cat[bc_cat['frontend_group'] != 'bc_data'].itertuples()) or "none")
              + ").", "",
              "The records the model read:", "",
              md_table(read.assign(title=read["title"].str[:70])[
                  ["record_id", "title", "rule_evidence", "llm_class", "final_class", "frontend_group"]],
                  index=False), ""]
    sheets["bc_catalogue_changes"] = with_text(table.loc[changes.index, ["record_id", "source", *FACTS, "evidence",
                                                                         "reason", "llm_class", "final_class"]],
                                               harvest)
    only_catalogue = bc_cat[(bc_cat["rule_name"] == "catalogue") & (bc_cat["rule_text_bc"] == "")]
    lines += [f"{len(only_catalogue)} records were decided bc by the catalogue alone (no box or point) and their "
              f"title, keywords and abstract name no BC place step 1 knows ("
              + ", ".join(f"{k} {v}" for k, v in only_catalogue["source"].value_counts().items())
              + "); sheet bc_catalogue_no_bc_words.", ""]
    sheets["bc_catalogue_no_bc_words"] = with_text(only_catalogue[["record_id", "source", "rule_evidence",
                                                                   "final_class", "dup_group_id"]], harvest)

    # 2a. Hakai records outside BC, checked by hand.
    lines += ["### 2a. Hakai records outside BC (checked by hand)", "",
              f"The {HAKAI} catalogue stays on the BC-only list. {len(hakai)} of its records, found by listing Hakai "
              f"records whose text names a place outside BC and none in BC (`hakai_outside_bc_candidates.csv`, "
              f"kept as the record of how they were found), were checked by {HAKAI_CHECKED_BY} on "
              f"{HAKAI_CHECKED_ON} against their original sources (outside facts, not the harvested text) and "
              f"set by hand (`hakai_handcheck.csv`), together with their copies in other catalogues (section 3). "
              f"There is no automatic rule for this. copy_class shows each copy's class before the hand check, and "
              f"its class now.", "",
              md_table(hakai.assign(title=hakai["title"].str[:60])[
                  ["title", "outside_bc_place_words", "catalogue_class", "copy_class", "final_class", "reason"]],
                  index=False), ""]
    sheets["hakai_handcheck"] = hakai

    # 3. DFO trawl series.
    lines += ["## 3. Hand checks: the DFO trawl series, and copies of hand-checked records", "",
              f"`dfo_trawl_handcheck.csv`: {len(handcheck)} records (7 datasets, each in CanWIN and on "
              f"open.canada.ca), checked by {DFO_CHECKED_BY} on {DFO_CHECKED_ON}. Their final_class is the checked "
              f"class and decided_by is hand; llm_class keeps the model's "
              + ", ".join(f"{k} ({v})" for k, v in handcheck["llm_class"].value_counts().items()) + ".", "",
              md_table(handcheck.drop_duplicates("uuid")[["series_part", "llm_class", "proposed_class"]]
                       .rename(columns={"proposed_class": "final_class"}), index=False), ""]
    sheets["dfo_trawl_handcheck"] = handcheck
    via_copy = table[table["note"].str.contains("hand check applied via copy", na=False)]
    lines += [f"Hand checks apply to the whole copy group: every copy of a hand-checked record takes its class "
              f"(decided_by hand, note \"hand check applied via copy <record id>\"; before_hand keeps the class "
              f"before). Two hand checks never disagree within a group (the script stops if they do). Copies set "
              f"this way: {len(via_copy)}.", ""]
    if len(via_copy):
        lines += [md_table(via_copy.assign(title=via_copy["title"].str[:60], took_from=via_copy["note"].str.extract(
            r"via copy (\S+)")[0])[["record_id", "title", "before_hand", "final_class", "frontend_group",
                                    "took_from"]], index=False), ""]
    sheets["hand_check_via_copy"] = via_copy[["record_id", "source", "title", "before_hand", "final_class",
                                              "frontend_group", "note"]]

    # 4. Duplicates.
    groups = table[table["dup_group_id"] != ""]
    n_class, n_group = conflict_counts
    lines += ["## 4. Copies (duplicates)", "",
              f"{groups['dup_group_id'].nunique():,} copy groups with {len(groups):,} records "
              f"(the label sample script found 2,983). No row is removed. dup_show marks one row per group: the "
              f"copy in the best front end group (bc_data, then pending, then dropped), and among those the "
              f"lowest record id.", "",
              f"- groups whose members differ in final_class: {n_class}",
              f"- groups whose members differ in frontend_group: {n_group} (all in `duplicate_conflicts.csv`, "
              f"{len(conflicts)} rows, and in the Excel file)", ""]
    differ_group = conflicts[conflicts["differs_in"] == "frontend_group"]
    per_group = conflicts.groupby("dup_group_id").agg(
        differs_in=("differs_in", "first"), same_text=("same_harvest_text", "first"),
        sources=("source", lambda v: " + ".join(sorted(set(v)))),
        how=("decided_by", lambda v: " + ".join(sorted(set(v)))))
    lines += [f"Of the {len(per_group)} groups that differ, the number with the same harvested text in every "
              f"copy is {int(per_group['same_text'].sum())} (title, abstract, keywords, places, points, boxes); in the others the "
              f"copies' text differs, so the model or the rules read different records. The catalogues involved:",
              "", md_table(per_group.groupby(["differs_in", "sources"]).size().rename("groups")
                           .sort_values(ascending=False).reset_index(), index=False), "",
              "When copies differ, dup_show picks the copy in bc_data (or pending), so the dataset is shown if any "
              "copy places it in BC. The groups that differ in front end group are listed in Appendix A.", ""]
    appendix = ["## Appendix A. Copy groups whose members differ in front end group", "",
                "Each copy: catalogue, final class (how decided); [shown] marks dup_show; (same text) marks groups "
                "whose copies have the same harvested text.", ""]
    for gid, members in differ_group.groupby("dup_group_id"):
        cells = "; ".join(f"{m.source} {m.final_class} ({m.decided_by})" + (" [shown]" if m.dup_show else "")
                          for m in members.itertuples())
        same_text = " (same text)" if members["same_harvest_text"].iloc[0] else ""
        appendix.append(f"- {gid}{same_text} {str(members['title'].iloc[0])[:80]}: {cells}")
    appendix.append("")
    sheets["duplicate_conflicts"] = conflicts

    # 5. Failures.
    failed, retried, context_full, longest, near = failures(pass1, pass2)
    lines += ["## 5. Failures and context-full records", "",
              f"- failed: pass 1 {len(failed['pass 1'])}, pass 2 {len(failed['pass 2'])}"
              + (f" ({failed})" if any(failed.values()) else ""),
              f"- needed a second attempt: pass 1 {len(retried['pass 1'])}, pass 2 {len(retried['pass 2'])}",
              f"- context full: {len(context_full)}" + (f" ({context_full})" if context_full else ""),
              f"- longest prompt: pass 1 {longest['pass 1']:,} tokens, pass 2 {longest['pass 2']:,} tokens, of "
              f"{OLLAMA_NUM_CTX:,}; above {NEAR_LIMIT:.0%} of the context: pass 1 {len(near['pass 1'])}, pass 2 "
              f"{len(near['pass 2'])}"
              + (" (" + ", ".join(f"{r} {t:,}" for t, r in near["pass 2"]) + ")" if near["pass 2"] else ""), ""]

    # 6. Box rule.
    filled, box_sample = box_fill_sample(table, harvest)
    lines += ["## 6. Box rule fills", "",
              f"Records whose in_bc the box rule read as yes: {len(filled)} (unclear, no other Canada, no outside "
              f"Canada, a box at least half in BC). Their classes: "
              + ", ".join(f"{k} {v}" for k, v in filled["final_class"].value_counts().items())
              + (f". Sample of {len(box_sample)} (seed {SEED})" if len(filled) > len(box_sample) else
                 f". There are fewer than 25, so all {len(filled)} are")
              + " in the Excel file, sheet box_rule_sample, with each box's share in and outside BC.", ""]
    sheets["box_rule_sample"] = box_sample

    # 7. Unknown.
    reasons, n_unknown, unknown_rows = unknown_check(table, harvest)
    lines += ["## 7. unknown", "",
              f"{n_unknown:,} records are unknown (the model said in_bc unclear, and nothing else placed them). "
              f"Why, from the facts first and then the words of the model's reason (a rough sort):", "",
              md_table(reasons.rename("records").rename_axis("why").to_frame()), "",
              f"{'All of them' if n_unknown <= 100 else f'A sample of {len(unknown_rows)} (seed {SEED})'} in the "
              f"Excel file, sheet unknown_sample.", ""]
    sheets["unknown_sample"] = unknown_rows

    # 8. bc_and_outside_canada.
    n_via_bc, via_canada, via_canada_rows = bc_outside_routes(table, harvest)
    total_bo = int((table["final_class"] == "bc_and_outside_canada").sum())
    by_how = table.loc[table["final_class"] == "bc_and_outside_canada", "decided_by"].value_counts()
    lines += ["## 8. bc_and_outside_canada", "",
              f"{total_bo} records (" + ", ".join(f"{k} {v}" for k, v in by_how.items()) + "); of the model's own:",
              f"- outside Canada and in_bc yes: {n_via_bc}",
              f"- outside Canada and canada_wide yes, without in_bc yes: {len(via_canada)}. Their final_class "
              f"stays bc_and_outside_canada, but their front end group is pending, with the note \"{NOTE_NATIONAL}\" "
              f"(listed below and in sheet bc_outside_via_canada_wide)", ""]
    if len(via_canada):
        lines += [md_table(via_canada.assign(title=via_canada["title"].str[:80])[
            ["record_id", "source", "title", "in_bc", "other_canada"]], index=False), ""]
    sheets["bc_outside_via_canada_wide"] = via_canada_rows

    # 9. CanWIN and Polar Data.
    lines += ["## 9. CanWIN and Polar Data Catalogue", ""]
    counts = {}
    for name, source in (("CanWIN", "canwin-datahub.ad.umanitoba.ca"), ("Polar Data", "polardata.ca")):
        counts[name], sheets[f"{name.lower().replace(' ', '_')}_sample"] = catalogue_check(table, harvest, source)
    lines += [md_table(pd.DataFrame(counts).rename_axis("final_class")), "",
              f"Samples among bc_data and pending (seed {SEED}; all of them when there are fewer than 20): CanWIN "
              f"{len(sheets['canwin_sample'])} (sheet canwin_sample), Polar Data {len(sheets['polar_data_sample'])} "
              f"(sheet polar_data_sample).", ""]

    # 10. Accuracy.
    acc = label_check(table, inputs["labels"], inputs["label_run"], pass1, pass2)
    head = ("| run | 7-class | shown, should not be | not shown, should be | group (bc_data / pending / dropped) "
            "| into bc_data wrongly | out of bc_data wrongly |")
    lines += ["## 10. Accuracy on the labelled records", "",
              f"The {acc['n']} labelled needs_llm records, scored as geo_pass2_score.py scores them (7 classes; "
              f"shown means any class but elsewhere and unknown), and with the new front end groups:", "",
              head, "|---|---|---|---|---|---|---|",
              score_line("full run, model's class (llm_class)", acc["full_llm"]),
              score_line("full run, final_class", acc["full_final"]),
              score_line("earlier 150-record run (label150_qwen3.6-35b-a3b_nofa)", acc["earlier"]), ""]
    same, differ = acc["same"], acc["differ"]
    same_prompt_differ = differ[differ["pass2_prompt_same"]] if len(differ) else differ
    lines += [f"Why the two runs differ, on the same {acc['n']} records:", "",
              f"- Prompt bytes: the pass 1 prompt is identical for {same['pass1_prompt']} of {acc['n']}. Pass 1's "
              f"places are identical for {same['pass1_places']}; for the others the model listed different places "
              f"from the same prompt, so their pass 2 prompt differs too (pass 2 prompt identical for "
              f"{same['pass2_prompt']}).",
              f"- With an identical pass 2 prompt, the five facts are identical for {same['pass2_facts_same_prompt']} "
              f"of {same['pass2_prompt']} (the whole answer, reason wording included, for {same['pass2_answer']}). "
              f"{len(same_prompt_differ)} of the {len(differ)} records whose class differs had an identical pass 2 "
              f"prompt.",
              "- Sampling: both runs used the same code and settings (temperature 0, thinking off, context 16,384, "
              "flash attention off, same model digest). Retries: none in either run (every record answered on the "
              "first attempt).",
              "- So the difference is run-to-run variation of the model's output on the same input, not prompt "
              "bytes, settings or retries. At temperature 0 this can still happen on a GPU: the server reuses the "
              "cached start of the previous prompt, and which records came before differs between a 150-record "
              "run and a 12,431-record run in 8 parts, which changes the arithmetic slightly. The net effect "
              f"here is one record: {acc['full_llm']['exact']} against {acc['earlier']['exact']} right.", ""]
    if len(differ):
        lines += [md_table(differ, index=False), ""]
    lines += [f"Records either run gets wrong are in sheet label150_wrong ({len(acc['wrong'])}).", ""]
    if len(acc["external"]):
        lines += ["Records with an outside label (reported, not scored): "
                  + "; ".join(f"{r.label_id} final_label {r.final_label}, external {r.true_label_external}, "
                              f"full run {table.set_index('record_id').loc[r.record_id, 'final_class']}"
                              for r in acc["external"].itertuples()), ""]
    ruled, rs = acc["ruled"], acc["ruled_score"]
    hand_ids = set(table.loc[table["decided_by"] == "hand", "record_id"])
    disagree_hand = ruled[(ruled["final_label"] != ruled["final_class"]) & ruled["record_id"].isin(hand_ids)]
    lines += [f"The {rs['n']} rule-labelled records (25 rules_bc, 25 rules_not_bc) against their final class (step "
              f"1's bc as bc_only, not_bc as elsewhere, unless set by hand): 7-class {pct(rs['exact'], rs['n'])}, "
              f"group {pct(rs['group_right'], rs['n'])}. Scored against final_label; true_label_external is shown, "
              f"not scored."
              + (" " + " and ".join(disagree_hand["label_id"]) + " differ because their hand check used outside "
                 "sources (the source page, the paper's methods), while the labels were judged from the record "
                 "only; true_label_external gives the outside answer." if len(disagree_hand) else ""), ""]
    disagree = ruled[ruled["final_label"] != ruled["final_class"]]
    if len(disagree):
        lines += [md_table(disagree.assign(title=disagree["title"].str[:70])[
            ["label_id", "record_id", "title", "group", "rule_name", "final_label", "true_label_external",
             "final_class"]], index=False), ""]
    sheets["label150_wrong"] = with_text(acc["wrong"][["label_id", "record_id", "final_label", "llm_class",
                                                       "final_class", "label150_run"]], harvest)
    sheets["rule_labelled_disagree"] = disagree

    # 11. Evidence.
    items, found, missing, records_missing = evidence_totals(table)
    lines += ["## 11. Evidence check", "",
              f"Evidence items the model quoted in pass 2: {items:,}. Found in the record (ignoring case and "
              f"spacing): {found:,} ({found / items:.1%}); not found: {missing:,}, in {records_missing:,} records "
              f"(sheet evidence_not_found).", ""]
    sheets["evidence_not_found"] = with_text(
        table[table["evidence_check"].str.contains("not found", na=False)][
            ["record_id", "source", "evidence_not_found", "evidence", "llm_class", "final_class"]], harvest)

    # 12. Final counts.
    by_class, by_group, class_by, group_by, shown_class_by, shown_group_by = final_counts(table)
    lines += ["## 12. Final counts", "",
              f"All {len(table):,} rows, and one row per copy group ({int(table['dup_show'].sum()):,} rows)"
              + (f"; each change is against the earlier run `{inputs['previous']}`" if inputs.get("previous") else "")
              + ":", ""]
    changed = None
    if inputs.get("previous"):
        before, changed = changes_since(table, inputs["previous"])
        old = final_counts(before)
        lines += [md_table(with_change(by_class, old[0]).rename_axis("final_class")), "",
                  md_table(with_change(by_group, old[1]).rename_axis("frontend_group")), "",
                  f"Records whose final class, front end group or decided_by changed: {len(changed)} "
                  f"(sheet changes_since_previous):", "",
                  md_table(changed.groupby(["final_class_before", "final_class", "frontend_group_before",
                                            "frontend_group", "decided_by"]).size().rename("records")
                           .reset_index(), index=False), ""]
        sheets["changes_since_previous"] = changed
    else:
        lines += [md_table(by_class.rename_axis("final_class")), "",
                  md_table(by_group.rename_axis("frontend_group")), ""]
    lines += [
              "By decided_by, all rows:", "",
              md_table(class_by), "", md_table(group_by), "",
              "By decided_by, one row per copy group:", "",
              md_table(shown_class_by), "", md_table(shown_group_by), ""]

    # The Excel file.
    path = os.path.join(out, "filter3_checks.xlsx")
    guide = pd.DataFrame({"sheet": list(sheets), "rows": [len(s) for s in sheets.values()]})
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        guide.to_excel(writer, sheet_name="sheets", index=False)
        for name, frame in sheets.items():
            excel_safe(frame, counter).to_excel(writer, sheet_name=name[:31], index=False)
    lines += ["## Files", "",
              "In `backend/data/lunaris_bc_filter/filter3_final/` (ignored by git): `filter3_merged.csv`, "
              "`bc_catalogue_changes.csv`, `dfo_trawl_handcheck.csv`, `hakai_handcheck.csv`, "
              "`duplicate_conflicts.csv`, `filter3_checks.xlsx` and this report; `hakai_outside_bc_candidates.csv` "
              "is from the previous run, kept as the record of how the Hakai records were found.", "",
              f"In the Excel file the record text is as harvested (title and abstract as they are; keywords, "
              f"places, points and boxes as JSON lists). Cells changed to fit Excel: {counter['replaced']} with "
              f"control characters replaced by a space, {counter['cut']} cut at 32,767 characters.", ""]
    summary = ["## Things to look at first", "",
               f"- Failures: pass 1 {len(failed['pass 1'])}, pass 2 {len(failed['pass 2'])}; second attempts: "
               f"{len(retried['pass 1']) + len(retried['pass 2'])}; context full: {len(context_full)} (section 5).",
               f"- Accuracy on the 150 labelled records: {pct(acc['full_llm']['exact'], acc['n'])} exact, "
               f"{pct(acc['full_llm']['group_right'], acc['n'])} by front end group; the earlier 150-record run had "
               f"{pct(acc['earlier']['exact'], acc['n'])}. The gap is run-to-run variation on identical prompts "
               f"(section 10).",
               f"- BC-only catalogue rule: records changed {len(changes)}; BC-only catalogue records in bc_data: "
               f"{(bc_cat['frontend_group'] == 'bc_data').sum():,} of {len(bc_cat):,} (section 2).",
                f"- Hakai records outside BC: {len(hakai)} set by hand against their original sources: "
               + ", ".join(f"{k} {v}" for k, v in hakai["final_class"].value_counts().items()) + " (section 2a).",
               f"- Records whose in_bc the box rule filled in: {len(filled)} (section 6).",
               f"- {len(via_canada)} bc_and_outside_canada records (national plus outside, no BC named) are now in "
               f"pending (section 8).",
               f"- Copies: {n_group} groups put copies in different front end groups, almost all CanWIN against "
               f"open.canada.ca, whose harvested text differs (section 4, Appendix A).",
               f"- DFO trawl series: {len(handcheck)} records set by hand (section 3).",
               f"- Hand checks pass on to every copy in the copy group: {len(via_copy)} more records set this way ("
               + ", ".join(f"{k} {v}" for k, v in via_copy["source"].value_counts().items()) + "; section 3).",
               "- Labels L052 and L070 (Hakai): final_label unchanged; true_label_external gives the outside "
               "answer, which matches the hand check (section 10).",
               (f"- Changes since the earlier run: {len(changed)} records (section 12)." if changed is not None
                else "- No earlier run given to compare with."), ""]
    lines[summary_at:summary_at] = summary
    return "\n".join(lines + appendix)
