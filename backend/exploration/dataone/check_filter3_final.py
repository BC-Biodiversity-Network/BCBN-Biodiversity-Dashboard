"""
check_filter3_final.py

Read only. Checks A, B, C and G on the DataONE filter 3 final table
(data/dataone/filter3/filter3_final.parquet, build_filter3_final.py,
2026-11-24). Nothing in pipeline/ or data/ is changed; the outputs go to
~/Desktop/BCBN/Results/dataone_filter3_checks_2026-11-24/.

A. Dryad abstracts cut by DataONE. The test is the one of
   build_bc_label_set.py (its abstract_cut_by_dataone column): the
   harvested abstract, with trailing spaces removed, ends with "..."
   (CUT_MARK). prepare_filter2_input.py has no test of its own: it leaves
   cut abstracts as they are. Counts of Dryad records by route x
   final_class x cut; for the llm route, the share of unknown among cut and
   not-cut records, for Dryad and for every other repository.
   -> dryad_truncation_counts.csv
B. The llm route's global records: counts by datasource, how many have
   in_bc yes, and a random sample of 30 (seed 20261124, drawn from the
   records sorted by record_id) with the record's fields as harvested, the
   pass 1 places, the five pass 2 facts, reason, evidence and a link.
   -> global_sample30.csv
C. The sign_check group: counts by route, final_class and frontend_group,
   and one row per record with the box as written (DataONE's index box)
   and with its longitudes' signs changed (new west = -east, new east =
   -west, as build_dataone_groups.py tests it), the class, group, reason
   and evidence; includes_bc marks a final class that includes BC
   (geo_pass2_score.SHOWN).   -> sign_check_180.csv
G. One record, sha256:ba74b97d... (HAKAI_IYS): its harvested fields and
   boxes, both filter 2 answers, the filter 3 answers, and the filter 2
   answers of its Lunaris copy.   -> record_ba74b97d.txt

"As harvested": a text field as it is, a list as JSON (filter3_checks.as_harvested).

Run, from backend/:
    ../.venv/bin/python exploration/dataone/check_filter3_final.py
"""

import json
import os
import sys
from urllib.parse import quote

import pandas as pd
import pyarrow.parquet as pq

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, "..", ".."))
for folder in ("pipeline/dataone", "pipeline/lunaris/llm", "pipeline/lunaris"):
    sys.path.insert(0, os.path.join(BACKEND, folder))
from build_bc_label_set import CUT_MARK  # noqa: E402
from build_label_set import VIEW  # noqa: E402
from classify_multibox_records import BOXES, EDGES, INDEX_EDGES, VERSIONS  # noqa: E402
from filter2_common import find_trial, read_trial  # noqa: E402
from filter3_checks import as_harvested  # noqa: E402
from geo_pass2_score import SHOWN  # noqa: E402
from match_lunaris import LUNARIS_HARVEST  # noqa: E402

FINAL = os.path.join(BACKEND, "data", "dataone", "filter3", "filter3_final.parquet")
RUN = os.path.join(BACKEND, "data", "dataone", "filter3_runs", "dataone_needs_llm_qwen3.6-35b-a3b_nofa")
FILTER2_RUNS = {"4b": os.path.join(BACKEND, "data", "dataone", "filter2_runs", "fir_dataone_harvest_2026_11_18"),
                "35b": os.path.join(BACKEND, "data", "dataone", "filter2_runs",
                                    "fir_dataone_harvest_2026_11_18_qwen3.6-35b-a3b_nofa")}
LUNARIS_RUNS = os.path.join(BACKEND, "data", "lunaris_llm_runs")
LUNARIS_TRIALS = {"4b": os.path.join(LUNARIS_RUNS, "harvest_2026_10_20", "trial_qwen3.5-4b-fir_v6_harvest_2026_10_20.csv"),
                  "35b": os.path.join(LUNARIS_RUNS, "fir_harvest_2026_10_20_qwen3.6-35b-a3b_nofa",
                                      "trial_qwen3.6-35b-a3b-fir-nofa_v6_harvest_2026_10_20_qwen3.6-35b-a3b_nofa.csv")}
OUT_DIR = os.path.expanduser("~/Desktop/BCBN/Results/dataone_filter3_checks_2026-11-24")

SEED = 20261124
SAMPLE = 30
TEXT_FIELDS = ["title", "abstract", "keywords", "namedLocation", "placeKey", "site"]
FACTS = ["in_bc", "other_canada", "outside_canada", "canada_wide", "global"]
FILTER2_FACTS = ["concerns_living_things", "organisms", "organism_level", "topic", "about_organisms_themselves",
                 "ecological_purpose_stated", "experimental_animals", "extinct_only", "form", "language",
                 "verdict", "rule", "model_reason"]
RECORD_G = "sha256:ba74b97d20e94418659794233e7f07e338ef69a4120c81d42d2fa617cc9b2d6c"
LUNARIS_G = "oai:catalogue.cioos.ca:ca-cioos_5e8ae69e-16d5-46f8-986f-14694b26a001"

pd.set_option("display.width", 250)
pd.set_option("display.max_colwidth", 80)


# --- Reading ---------------------------------------------------------------------

def read_final():
    """Return the filter 3 final table, indexed by record id."""
    return pd.read_parquet(FINAL).set_index("record_id", drop=False).rename_axis(None)


def read_harvested(ids, columns):
    """Return the given columns of the given latest records, as harvested, by id."""
    table = pq.read_table(VERSIONS, columns=["id", "version_status"] + columns,
                          filters=[("id", "in", list(ids))]).to_pandas()
    table = table[table["version_status"] == "latest"].drop_duplicates("id").set_index("id")
    missing = set(ids) - set(table.index)
    if missing:
        sys.exit(f"STOPPED: {len(missing)} records have no latest version, for example {sorted(missing)[:3]}")
    return table.drop(columns="version_status")


def read_pass1_places():
    """Return each record's pass 1 places from the run's merged answers, as "name (role)" joined with "; "."""
    places = {}
    with open(os.path.join(RUN, "pass1", "answers.jsonl"), encoding="utf-8") as handle:
        for line in handle:
            result = json.loads(line)
            places[result["record_id"]] = "; ".join(f"{p['name']} ({p['role']})" for p in result.get("places", []))
    return places


def link(record_id):
    """Return the DataONE page of a record, as build_label_set.py builds it."""
    return VIEW + quote(record_id, safe="")


def is_cut(abstract):
    """Return True if an abstract ends with "..." after trailing spaces: DataONE cut it (build_bc_label_set.py)."""
    return isinstance(abstract, str) and abstract.rstrip().endswith(CUT_MARK)


# --- A. Dryad abstracts cut by DataONE -------------------------------------------

def check_a(final):
    """
    Return the Dryad counts by route x final_class x cut, and print them
    with the share of unknown among the llm route's cut and not-cut
    records, for Dryad and for every other repository.
    """
    abstracts = read_harvested(final.index, ["abstract"])["abstract"]
    table = final[["record_id", "datasource", "route", "final_class"]].copy()
    table["cut"] = table["record_id"].map(abstracts).map(is_cut).map({True: "yes", False: "no"})
    dryad = table[table["datasource"] == "DRYAD"]
    counts = dryad.groupby(["route", "final_class", "cut"]).size().rename("records").reset_index()
    print(f"\nA. DRYAD ABSTRACTS CUT BY DATAONE: {len(dryad):,} Dryad records in filter 3, "
          f"{int((dryad['cut'] == 'yes').sum()):,} cut")
    print(pd.crosstab([dryad["route"], dryad["final_class"]], dryad["cut"], margins=True,
                      margins_name="total").to_string())
    llm = table[table["route"] == "llm"]
    rows = []
    for name, part in (("DRYAD", llm[llm["datasource"] == "DRYAD"]), ("other repositories",
                                                                      llm[llm["datasource"] != "DRYAD"])):
        for cut in ("yes", "no"):
            p = part[part["cut"] == cut]
            unknown = int((p["final_class"] == "unknown").sum())
            rows.append({"repositories": name, "cut": cut, "llm_records": len(p), "unknown": unknown,
                         "unknown_share": round(unknown / len(p), 3) if len(p) else ""})
    shares = pd.DataFrame(rows)
    print("\n   llm route, share of unknown:")
    print(shares.to_string(index=False))
    both = int(((llm["datasource"] == "DRYAD") & (llm["cut"] == "yes") & (llm["final_class"] == "unknown")).sum())
    print(f"   llm-route Dryad records that are cut and unknown (a possible text fetch): {both:,}")
    print(f"   all cut records in filter 3 by repository (top 5): "
          f"{table[table['cut'] == 'yes']['datasource'].value_counts().head(5).to_dict()}")
    summary = shares.assign(route="llm", final_class="(share of unknown)").rename(columns={"llm_records": "records"})
    return pd.concat([counts.assign(repositories="DRYAD"), summary], ignore_index=True)[
        ["repositories", "route", "final_class", "cut", "records", "unknown", "unknown_share"]]


# --- B. The llm route's global records -------------------------------------------------

def check_b(final, places):
    """Print the llm route's global records by datasource and with in_bc yes, and return the sample of 30."""
    glob = final[(final["route"] == "llm") & (final["final_class"] == "global")]
    by_source = pd.crosstab(glob["datasource"], glob["in_bc"]).reindex(columns=["yes", "unclear", "no"], fill_value=0)
    by_source["total"] = by_source.sum(axis=1)
    print(f"\nB. LLM-ROUTE GLOBAL: {len(glob):,} records, in_bc yes {int((glob['in_bc'] == 'yes').sum())}")
    print("   by datasource (in_bc answers), top 15:")
    print(by_source.sort_values("total", ascending=False).head(15).to_string())
    print(f"   all {len(by_source)} datasources: {by_source['total'].sort_values(ascending=False).to_dict()}")
    sample = glob.sort_values("record_id").sample(n=SAMPLE, random_state=SEED).sort_values("record_id")
    fields = read_harvested(sample.index, TEXT_FIELDS)
    rows = []
    # Rows as dicts: "global" is a Python word, so itertuples would rename that column.
    for r in sample.to_dict("records"):
        row = {"record_id": r["record_id"], "datasource": r["datasource"]}
        row.update({f: as_harvested(fields.at[r["record_id"], f]) for f in TEXT_FIELDS})
        row["pass1_places"] = places.get(r["record_id"], "")
        row.update({f: r[f] for f in FACTS})
        row.update(reason=r["reason"], evidence=r["evidence"], link=link(r["record_id"]))
        rows.append(row)
    return pd.DataFrame(rows), by_source.reset_index()


# --- C. The sign_check group ------------------------------------------------------------

def check_c(final):
    """Print the sign_check records' counts and return one row per record with its box both ways."""
    sign = final[final["group"] == "sign_check"]
    print(f"\nC. SIGN_CHECK: {len(sign)} records in filter 3")
    print(pd.crosstab([sign["route"], sign["final_class"]], sign["frontend_group"], margins=True,
                      margins_name="total").to_string())
    if sign["multibox"].any():
        print(f"   NOTE: {int(sign['multibox'].sum())} are multi-box; their box here is the index box (the first)")
    boxes = read_harvested(sign.index, ["title"] + list(INDEX_EDGES.values()))
    rows = []
    for r in sign.sort_values("record_id").itertuples():
        b = {e: boxes.at[r.record_id, INDEX_EDGES[e]] for e in EDGES}
        rows.append({"record_id": r.record_id, "datasource": r.datasource, "title": r.title, "link": link(r.record_id),
                     "north": b["north"], "south": b["south"], "east": b["east"], "west": b["west"],
                     "flipped_east": -b["west"], "flipped_west": -b["east"],
                     "route": r.route, "final_class": r.final_class, "frontend_group": r.frontend_group,
                     "includes_bc": "yes" if r.final_class in SHOWN else "",
                     "in_bc": r.in_bc, "reason": r.reason, "evidence": r.evidence,
                     "lunaris_ids": r.lunaris_ids})
    out = pd.DataFrame(rows)
    print(f"   final class includes BC: {int((out['includes_bc'] == 'yes').sum())} "
          f"({out.loc[out['includes_bc'] == 'yes', 'final_class'].value_counts().to_dict()})")
    return out


# --- G. One record ---------------------------------------------------------------------------

def trial_row(path, record_id):
    """Return one record's row of a merged filter 2 trial CSV, with decide()'s verdict and rule, or None."""
    frame, _ = read_trial(path)
    rows = frame[frame["record_id"] == record_id]
    return None if rows.empty else rows.iloc[0]


def facts_text(row):
    """Return a filter 2 answer's facts, verdict, rule and reason as lines."""
    if row is None:
        return ["   (no answer)"]
    return [f"   {f}: {row[f]}" for f in FILTER2_FACTS]


def check_g(final, places):
    """Return the text for record G: its fields, both filter 2 answers, the filter 3 answers, and its Lunaris copy's."""
    lines = [f"RECORD {RECORD_G}", f"link: {link(RECORD_G)}", ""]
    fields = read_harvested([RECORD_G], ["datasource"] + TEXT_FIELDS + list(INDEX_EDGES.values()))
    for f in ["datasource"] + TEXT_FIELDS + list(INDEX_EDGES.values()):
        lines.append(f"{f}: {as_harvested(fields.at[RECORD_G, f])}")
    boxes = pd.read_parquet(BOXES)
    boxes = boxes[boxes["id"] == RECORD_G]
    lines.append(f"boxes in the original document (originals_boxes.parquet): {len(boxes)}")
    for b in boxes.itertuples():
        lines.append(f"   N {b.north} S {b.south} E {b.east} W {b.west} flag {b.flag!r}")
    lines.append("")
    for name, run_dir in FILTER2_RUNS.items():
        path, _ = find_trial(run_dir)
        lines.append(f"FILTER 2, {name} ({os.path.relpath(path, BACKEND)}):")
        lines.extend(facts_text(trial_row(path, RECORD_G)))
    r = final.loc[RECORD_G]
    lines += ["", f"FILTER 3 (route {r['route']}, run number {r['run_number']:.0f}):",
              f"   pass 1 places: {places.get(RECORD_G, '')}"]
    lines += [f"   {f}: {r[f]}" for f in FACTS]
    lines += [f"   in_bc_used: {r['in_bc_used']}; box rule filled: {r['in_bc_filled_by_box_rule']}; box shares: "
              f"{r['box_shares']}", f"   final_class: {r['final_class']}; frontend_group: {r['frontend_group']}",
              f"   reason: {r['reason']}", f"   evidence: {r['evidence']}", ""]
    lunaris = pd.read_parquet(LUNARIS_HARVEST, columns=["id", "title", "datestamp"], filters=[("id", "==", LUNARIS_G)])
    lines.append(f"LUNARIS COPY {LUNARIS_G}")
    lines.append(f"   title: {lunaris.sort_values('datestamp')['title'].iloc[-1] if len(lunaris) else '(not in harvest)'}")
    for name, path in LUNARIS_TRIALS.items():
        lines.append(f"LUNARIS FILTER 2, {name} ({os.path.relpath(path, BACKEND)}):")
        lines.extend(facts_text(trial_row(path, LUNARIS_G)))
    # The Lunaris decision the DataONE match file carries, and any Lunaris hand decision.
    match = pd.read_parquet(os.path.join(os.path.dirname(VERSIONS), "dataone_lunaris_match.parquet"),
                            columns=["id", "lunaris_ids", "lunaris_match_by", "lunaris_filter2_summary",
                                     "title_similarity", "reuse_lunaris"]).set_index("id")
    m = match.loc[RECORD_G]
    lines.append(f"MATCH FILE: lunaris_ids {list(m['lunaris_ids'])}, by {list(m['lunaris_match_by'])}, Lunaris "
                 f"filter 2 {m['lunaris_filter2_summary']!r}, title similarity {m['title_similarity']:.3f}, "
                 f"reuse_lunaris {m['reuse_lunaris']}")
    hand = pd.read_csv(os.path.join(BACKEND, "pipeline", "lunaris", "filter2_hand_decisions.csv"), dtype=str,
                       keep_default_na=False)
    found = hand[hand["record_id"] == LUNARIS_G]
    lines.append(f"LUNARIS FILTER 2 HAND DECISION: {found.to_dict('records') if len(found) else 'none'}")
    return "\n".join(lines)


def main():
    """Run checks A, B, C and G, print what they find and write their files."""
    os.makedirs(OUT_DIR, exist_ok=True)
    final = read_final()
    places = read_pass1_places()
    check_a(final).to_csv(os.path.join(OUT_DIR, "dryad_truncation_counts.csv"), index=False, encoding="utf-8-sig")
    sample, by_source = check_b(final, places)
    sample.to_csv(os.path.join(OUT_DIR, "global_sample30.csv"), index=False, encoding="utf-8-sig")
    by_source.to_csv(os.path.join(OUT_DIR, "global_by_datasource.csv"), index=False, encoding="utf-8-sig")
    check_c(final).to_csv(os.path.join(OUT_DIR, "sign_check_180.csv"), index=False, encoding="utf-8-sig")
    text = check_g(final, places)
    with open(os.path.join(OUT_DIR, "record_ba74b97d.txt"), "w", encoding="utf-8") as handle:
        handle.write(text + "\n")
    print("\nG.\n" + text)
    print(f"\nwrote {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
