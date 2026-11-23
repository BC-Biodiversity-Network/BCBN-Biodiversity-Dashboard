"""
build_filter3_rules.py

Step 1 of DataONE filter 3 (was the data collected in BC?): for every
record filter 2 kept, decide whether the rules settle it or it goes to the
geography LLM, with Lucia's decisions of 2026-11-23. Nothing here asks a
model anything; every input is only read.

Which records: every record with keep_union true in
data/dataone/filter2_results/filter2_union.parquet (78,740).

How each is routed:
  lunaris  a record that reuses the Lunaris filter 2 result (reuse_lunaris)
           takes its Lunaris filter 3 class: final_class in
           data/lunaris_bc_filter/filter3_final_union/filter3_merged.csv
           (the Lunaris union table, with its hand decisions), of the
           Lunaris records it matched by DOI or UUID (the same records the
           filter 2 reuse used; dataone_lunaris_match.parquet). If those
           records do not all have the same class, or none has one, the
           record goes to the LLM instead (decision e).
  rule     every other record is put through the Lunaris BC filter rules as
           they are (bc_filter_rules.classify_record) and the Lunaris
           mapping (build_filter3_final.RULE_TO_CLASS): bc gives bc_only
           (a box 90% or more in BC, a box that is BC's own rectangle, or a
           point in BC), not_bc gives elsewhere (a box or point outside BC,
           including a box that only touches BC's edge).
  llm      the rest: what the rules leave as needs_llm, every record with no
           box, and, whatever the rules say (decision a), every record in
           the west_gt_east, sign_check and multi-box send_to_llm groups.
           For those the rule result is kept for comparison.
The records the rules read are assembled by filter3_records.py (DataONE's
index box, or every box of a multi-box record's original document; the
namedLocation field as the place field, which only the cross-check columns
use). No DataONE member repository is in the Lunaris catalogue list
(bc_filter_sources.csv); the script stops if a record's id ever gets a
catalogue vote, so a Lunaris catalogue rule can never decide a DataONE
record by accident.

Output (data/dataone/filter3/, ignored by git; the script checks with git
check-ignore and stops if a file is there unless --replace):
  filter3_rules.parquet  one row per kept record: id, datasource, group,
                         multibox, keep_source (filter 2), route, final_class
                         (empty for llm), route_reason, rule_class,
                         rule_decided_by, rule_evidence, box_class,
                         box_share, point_class, lunaris_ids,
                         lunaris_classes
and a report printed to the screen, with the label set comparison.

Run, from backend/:
    ../.venv/bin/python pipeline/dataone/build_filter3_rules.py
"""

import argparse
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(BACKEND, "pipeline", "lunaris"))
from bc_filter_rules import classify_record, load_rules  # noqa: E402
from build_filter3_final import RULE_TO_CLASS  # noqa: E402
from classify_multibox_records import BC_BOUNDARY, HARVEST, check_ignored  # noqa: E402
from filter3_records import read_geo_records  # noqa: E402
from match_lunaris import STRONG  # noqa: E402

UNION = os.path.join(BACKEND, "data", "dataone", "filter2_results", "filter2_union.parquet")
MATCH = os.path.join(HARVEST, "dataone_lunaris_match.parquet")
LUNARIS_FILTER3 = os.path.join(BACKEND, "data", "lunaris_bc_filter", "filter3_final_union", "filter3_merged.csv")
OUT_DIR = os.path.join(BACKEND, "data", "dataone", "filter3")
OUT = os.path.join(OUT_DIR, "filter3_rules.parquet")
RESULTS = os.path.expanduser("~/Desktop/BCBN/Results")
LABELS = [("first label set", os.path.join(RESULTS, "dataone_label_set_2026-11-19",
                                           "lucia_labels_final_and_comparison.csv"), "lucia_bc_class_final"),
          ("BC set, first 50", os.path.join(RESULTS, "dataone_bc_label_set_2026-11-23",
                                            "claude_bc_labels_first50.csv"), "bc_class"),
          ("BC set, strict 50", os.path.join(RESULTS, "dataone_bc_label_set_2026-11-23_strict",
                                             "claude_bc_labels_strict50.csv"), "bc_class")]

EXPECTED_KEPT = 78_740
# Groups that go to the LLM whatever the rules say (decision a).
ALWAYS_LLM = ("west_gt_east", "sign_check", "send_to_llm")
ROUTES = ["rule", "lunaris", "llm"]
COLUMNS = ["id", "datasource", "group", "multibox", "keep_source", "route", "final_class", "route_reason",
           "rule_class", "rule_decided_by", "rule_evidence", "box_class", "box_share", "point_class",
           "lunaris_ids", "lunaris_classes"]

pd.set_option("display.width", 250)
pd.set_option("display.max_colwidth", 90)


def stop(message):
    """Stop the script with a message that starts with STOPPED."""
    sys.exit(f"STOPPED: {message}")


# --- Reading -----------------------------------------------------------------

def read_kept():
    """Return the records filter 2 kept (keep_union true), with id, datasource, group, multibox and keep_source."""
    union = pd.read_parquet(UNION, columns=["id", "datasource", "group", "multibox", "keep_union", "keep_source",
                                            "reuse_lunaris"])
    kept = union[union["keep_union"].fillna(False).astype(bool)].copy()
    if kept["id"].duplicated().any():
        stop("a kept record appears twice in the filter 2 union")
    return kept.reset_index(drop=True)


def lunaris_classes(ids):
    """
    Return, for each given DataONE id, the Lunaris records it matched by DOI
    or UUID and their filter 3 classes (final_class in the Lunaris union
    table, "" for a Lunaris record not in it), as {id: (lunaris ids, classes)}.
    """
    match = pd.read_parquet(MATCH, columns=["id", "lunaris_ids", "lunaris_match_by"]).set_index("id")
    lunaris = pd.read_csv(LUNARIS_FILTER3, dtype=str, keep_default_na=False, usecols=["record_id", "final_class"])
    if lunaris["record_id"].duplicated().any():
        stop(f"{LUNARIS_FILTER3} lists a record twice")
    final = dict(zip(lunaris["record_id"], lunaris["final_class"]))
    found = {}
    for record_id in ids:
        row = match.loc[record_id]
        strong = [lid for lid, way in zip(row["lunaris_ids"], row["lunaris_match_by"])
                  if any(kind in way.split("+") for kind in STRONG)]
        found[record_id] = (strong, [final.get(lid, "") for lid in strong])
    return found


# --- Routing -------------------------------------------------------------------

def lunaris_route(classes):
    """
    Return (route, final_class, reason) for a record that reuses Lunaris:
    its Lunaris class when every DOI or UUID matched Lunaris record has the
    same one, otherwise the LLM.
    """
    distinct = sorted(set(classes) - {""})
    if len(distinct) == 1 and "" not in classes:
        return "lunaris", distinct[0], "Lunaris filter 3 class"
    if not distinct:
        return "llm", "", "no Lunaris filter 3 class"
    return "llm", "", "Lunaris copies disagree: " + " | ".join(distinct)


def rule_route(group, rule_class):
    """
    Return (route, final_class, reason) for a record the rules read: the LLM
    for the groups that always go there (decision a) and for no box; else
    the Lunaris class of a deciding rule (bc_only or elsewhere), or the LLM
    when the rules leave it as needs_llm.
    """
    if group in ALWAYS_LLM:
        return "llm", "", f"{group}: to the LLM whatever the rules say"
    if group == "no_box":
        return "llm", "", "no box"
    if rule_class in RULE_TO_CLASS:
        return "rule", RULE_TO_CLASS[rule_class], f"rules: {rule_class}"
    return "llm", "", "rules: needs_llm"


def rule_results(records, rules):
    """
    Return the Lunaris rules' result for each record (a dict by id) as a
    table indexed by id. The record's namedLocation stands in for the
    Lunaris place field (cross-check columns only). Stops if a catalogue
    vote ever decides a DataONE record.
    """
    rows = []
    for record_id, record in records.items():
        lunaris_form = {"id": record_id, "title": record["title"], "subjects": record["subjects"],
                        "abstract": record["abstract"], "places": record["named_location"],
                        "boxes": record["boxes"], "points": None}
        result = classify_record(lunaris_form, rules)
        if "source" in result["decided_by"].split("+"):
            stop(f"a Lunaris catalogue rule voted on {record_id} (source {result['source']})")
        rows.append(result)
    return pd.DataFrame(rows).set_index("record_id")


def build_table(kept, rules):
    """
    Return the routing table (COLUMNS), one row per kept record: Lunaris
    classes for the reused records, the rules for the rest, and the route.
    """
    reused = kept[kept["reuse_lunaris"]]
    others = kept[~kept["reuse_lunaris"]]
    from_lunaris = lunaris_classes(list(reused["id"]))
    results = rule_results(read_geo_records(list(others["id"])), rules)
    rows = []
    for row in kept.to_dict("records"):
        out = {name: row[name] for name in ("id", "datasource", "group", "multibox", "keep_source")}
        if row["reuse_lunaris"]:
            ids, classes = from_lunaris[row["id"]]
            out["route"], out["final_class"], out["route_reason"] = lunaris_route(classes)
            out.update(lunaris_ids=ids, lunaris_classes=classes)
        else:
            result = results.loc[row["id"]]
            out["route"], out["final_class"], out["route_reason"] = rule_route(row["group"], result["bc_class"])
            out.update(rule_class=result["bc_class"], rule_decided_by=result["decided_by"],
                       rule_evidence=result["evidence"], box_class=result["box_class"],
                       box_share=result["box_share"], point_class=result["point_class"],
                       lunaris_ids=[], lunaris_classes=[])
        rows.append(out)
    table = pd.DataFrame(rows).reindex(columns=COLUMNS)
    for column in ("rule_class", "rule_decided_by", "rule_evidence", "box_class", "point_class", "final_class"):
        table[column] = table[column].fillna("").astype(str)
    table["box_share"] = pd.to_numeric(table["box_share"])
    return table


def check_complete(table, kept_ids, expected=EXPECTED_KEPT):
    """Stop unless the table holds every kept record exactly once, and the expected number of them."""
    if table["id"].duplicated().any():
        stop(f"{int(table['id'].duplicated().sum())} records appear twice in the table")
    missing = set(kept_ids) - set(table["id"])
    extra = set(table["id"]) - set(kept_ids)
    if missing or extra:
        stop(f"{len(missing)} kept records are missing from the table and {len(extra)} are not kept records")
    if len(table) != expected:
        stop(f"{len(table):,} kept records, not {expected:,}")
    if not table["route"].isin(ROUTES).all():
        stop("a record has no route")


# --- Report --------------------------------------------------------------------

def read_labels():
    """Return every label as one table: set, id, label (empty and "unsure" labels left out)."""
    parts = []
    for name, path, column in LABELS:
        labels = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig", usecols=["id", column])
        labels = labels.rename(columns={column: "label"})
        parts.append(labels[~labels["label"].isin(["", "unsure"])].assign(set=name))
    return pd.concat(parts, ignore_index=True)


def label_comparison(table, labels):
    """Return the labelled records the rules or Lunaris decided, with their label and whether it agrees."""
    decided = table[table["route"] != "llm"].set_index("id")
    rows = labels[labels["id"].isin(decided.index)].copy()
    rows["route"] = rows["id"].map(decided["route"])
    rows["final_class"] = rows["id"].map(decided["final_class"])
    rows["group"] = rows["id"].map(decided["group"])
    rows["datasource"] = rows["id"].map(decided["datasource"])
    rows["reason"] = rows["id"].map(decided["rule_evidence"].where(decided["route"] == "rule",
                                                                    decided["route_reason"]))
    rows["agrees"] = rows["label"] == rows["final_class"]
    return rows


def report(table, labels):
    """Print the counts by route, class, group and datasource, and the label set comparison."""
    print(f"\n1. ROUTES ({len(table):,} kept records)")
    print(pd.crosstab(table["route"], table["final_class"].replace("", "(LLM)"), margins=True,
                      margins_name="total").to_string())
    print("\n   why records go to the LLM:")
    print(table.loc[table["route"] == "llm", "route_reason"].str.replace(r": .*disagree.*", ": copies disagree",
                                                                       regex=True).value_counts().to_string())
    print("\n2. BY GROUP")
    by_group = pd.crosstab([table["group"], table["multibox"]], table["route"]).reindex(columns=ROUTES, fill_value=0)
    print(by_group.to_string())
    always = table[table["group"].isin(ALWAYS_LLM) & (table["route"] != "lunaris")]
    print("\n   what the rules would have said for the groups sent to the LLM whatever (decision a; records "
          "reusing Lunaris take their Lunaris class instead):")
    print(pd.crosstab(always["group"], always["rule_class"]).to_string())
    print("\n3. BY DATASOURCE (top 15 by records)")
    by_source = pd.crosstab(table["datasource"], table["route"]).reindex(columns=ROUTES, fill_value=0)
    by_source["total"] = by_source.sum(axis=1)
    print(by_source.sort_values("total", ascending=False).head(15).to_string())
    print(f"\n   to the LLM in all: {int((table['route'] == 'llm').sum()):,}")

    compared = label_comparison(table, labels)
    print(f"\n4. LABELLED RECORDS DECIDED BY RULE OR LUNARIS: {len(compared)}")
    summary = compared.groupby(["set", "route"])["agrees"].agg(["sum", "count"]).rename(
        columns={"sum": "agree", "count": "labelled"})
    print(summary.to_string())
    wrong = compared[~compared["agrees"]]
    print(f"\n   every disagreement ({len(wrong)}):")
    for r in wrong.to_dict("records"):
        print(f"   - {r['set']} | {r['id'][:60]} | {r['datasource']} | {r['group']} | {r['route']}: "
              f"{r['final_class']} | label {r['label']} | {r['reason']}")
    return compared


def main():
    """Read, route, check and write the table, then print the report."""
    parser = argparse.ArgumentParser(description="DataONE filter 3, step 1: the rules.")
    parser.add_argument("--replace", action="store_true", help="write over an earlier output")
    parser.add_argument("--bc-boundary", default=BC_BOUNDARY, help="BC's outline as WKT (bc_boundary_gbif.wkt)")
    args = parser.parse_args()
    check_ignored(OUT)
    if os.path.exists(OUT) and not args.replace:
        stop(f"{os.path.relpath(OUT, BACKEND)} is already there; add --replace to write over it")
    kept = read_kept()
    table = build_table(kept, load_rules(args.bc_boundary))
    check_complete(table, kept["id"])
    report(table, read_labels())
    os.makedirs(OUT_DIR, exist_ok=True)
    table.to_parquet(OUT + ".tmp", index=False)
    os.replace(OUT + ".tmp", OUT)
    print(f"\nwrote {len(table):,} rows -> {os.path.relpath(OUT, BACKEND)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
