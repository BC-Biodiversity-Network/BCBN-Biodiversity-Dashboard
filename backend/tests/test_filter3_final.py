"""Tests for the last step of the Lunaris BC filter (pipeline/lunaris/
build_filter3_final.py and filter3_checks.py), on made-up records: step 1's
bc and not_bc become bc_only and elsewhere, needs_llm records take the class
from the model's facts, the BC-only catalogue rule turns elsewhere, unknown,
all_canada and global into bc_only, the DFO trawl series takes its
hand-checked class, national-plus-outside records go to pending, every class
has a front end group, copies are grouped without removing a row and one
per group is shown, the Hakai records checked by hand take their class,
the run folder is only read, and the checks sort and score as described."""

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "pipeline" / "lunaris"))
sys.path.insert(0, str(BACKEND / "pipeline" / "lunaris" / "llm"))
import build_filter3_final as f3  # noqa: E402
import filter3_checks as checks  # noqa: E402
from geo_pass2_classes import CLASSES  # noqa: E402

UUID_A = "11111111-2222-3333-4444-555555555555"


def facts(in_bc="yes", other="no", outside="no", wide="no", world="no"):
    """Return the model's five facts."""
    return {"in_bc": in_bc, "other_canada": other, "outside_canada": outside, "canada_wide": wide, "global": world}


def pass2_result(record_id, cls, answer, evidence=("Victoria",), unmatched=(), filled=False):
    """Return a pass 2 result as geo_filter_run.py writes it."""
    return {"record_id": record_id, "prompt_sha1": "p2" + record_id, "attempts": 1, "input_tokens": 1000,
            "answer": dict(answer, reason="because", evidence=list(evidence)), "box_shares": [0.6],
            "decision": {"model_in_bc": answer["in_bc"], "in_bc_used": "yes" if filled else answer["in_bc"],
                         "in_bc_filled_by_box_rule": filled, "class": cls},
            "evidence_check": {"items": len(evidence), "exact": len(evidence) - len(unmatched),
                               "loose": len(evidence) - len(unmatched), "unmatched": list(unmatched)},
            "consistency_flags": []}


def made_up():
    """Return made-up step 1 rows, pass 1 and pass 2 results, and harvest rows for six records."""
    rules = pd.DataFrame([
        {"record_id": "oai:x:bc1", "source": "x", "bc_class": "bc", "decided_by": "box", "box_class": "bc_wide"},
        {"record_id": "oai:x:nb1", "source": "x", "bc_class": "not_bc", "decided_by": "box+source",
         "box_class": "outside"},
        {"record_id": f"oai:canwin:{UUID_A}", "source": "canwin", "bc_class": "needs_llm", "decided_by": "none",
         "box_class": None},
        {"record_id": f"oai:open.canada.ca:{UUID_A}", "source": "open.canada.ca", "bc_class": "needs_llm",
         "decided_by": "none", "box_class": None},
        {"record_id": "oai:catalogue.hakai.org:h1", "source": "catalogue.hakai.org", "bc_class": "needs_llm",
         "decided_by": "none", "box_class": "huge"},
        {"record_id": "oai:catalogue.hakai.org:h2", "source": "catalogue.hakai.org", "bc_class": "needs_llm",
         "decided_by": "none", "box_class": "huge"},
    ]).assign(evidence="e", box_share=None, text_title="", text_keywords="", text_abstract="")
    pass2 = {
        f"oai:canwin:{UUID_A}": pass2_result(f"oai:canwin:{UUID_A}", "all_canada", facts("unclear", wide="yes")),
        f"oai:open.canada.ca:{UUID_A}": pass2_result(f"oai:open.canada.ca:{UUID_A}", "bc_only", facts(),
                                                     unmatched=("Victoria",)),
        "oai:catalogue.hakai.org:h1": pass2_result("oai:catalogue.hakai.org:h1", "elsewhere", facts("no")),
        "oai:catalogue.hakai.org:h2": pass2_result("oai:catalogue.hakai.org:h2", "global", facts(world="yes")),
    }
    pass1 = {r: {"record_id": r, "prompt_sha1": "p1", "places": [{"name": "Victoria"}], "attempts": 1}
             for r in pass2}
    harvest = pd.DataFrame([{"id": r, "doi": None, "title": f"title {r[-3:]}", "abstract": "abstract",
                             "publisher": "p", "subjects": ["fish"], "places": [], "points": None,
                             "boxes": [{"w": -125.0, "e": -123.0, "s": 48.0, "n": 50.0}]}
                            for r in rules["record_id"]]).set_index("id")
    harvest.loc["oai:x:nb1", "abstract"] = "another abstract"
    return rules, pass1, pass2, harvest


def merged():
    """Return the made-up records merged, with copy groups added."""
    rules, pass1, pass2, harvest = made_up()
    table = f3.merge(rules, pass1, pass2, harvest, ["catalogue.hakai.org"])
    return f3.add_duplicates(table, harvest), harvest


# --- The final class -------------------------------------------------------

def test_every_class_has_a_front_end_group():
    """The seven classes map to bc_data, pending or dropped, as decided."""
    assert set(f3.FRONTEND_GROUP) == set(CLASSES)
    assert {c for c, g in f3.FRONTEND_GROUP.items() if g == "bc_data"} == {
        "bc_only", "canada_incl_bc", "bc_and_outside_canada"}
    assert {c for c, g in f3.FRONTEND_GROUP.items() if g == "pending"} == {"all_canada", "global"}


def test_step1_rule_names():
    """Step 1's rules are named in plain words, with BC's own rectangle told apart from a box inside BC."""
    assert f3.step1_rule_name("bc", "box", "inside") == "box"
    assert f3.step1_rule_name("bc", "box+point", "bc_wide") == "rectangle box+point"
    assert f3.step1_rule_name("not_bc", "box+source", "outside") == "box+catalogue"
    assert f3.step1_rule_name("needs_llm", "none", None) == ""


def test_the_bc_catalogue_rule():
    """A BC-only catalogue record the model put elsewhere, unknown, all_canada or global becomes bc_only."""
    cats = ["catalogue.hakai.org"]
    assert f3.final_of("bc", "", "x", cats) == ("bc_only", "rule", "")
    assert f3.final_of("not_bc", "", "catalogue.hakai.org", cats)[:2] == ("elsewhere", "rule")
    for cls in ("elsewhere", "unknown", "all_canada", "global"):
        final, how, note = f3.final_of("needs_llm", cls, "catalogue.hakai.org", cats)
        assert (final, how) == ("bc_only", "bc_only_catalogue") and cls in note
    for cls in ("canada_incl_bc", "bc_and_outside_canada"):
        assert f3.final_of("needs_llm", cls, "catalogue.hakai.org", cats) == (cls, "llm", "")
    assert f3.final_of("needs_llm", "unknown", "x", cats) == ("unknown", "llm", "")


def test_merge_gives_one_row_per_record_with_the_llm_columns():
    """Every record gets one row; llm_class keeps the model's class when the catalogue fix changes the final class."""
    table, _ = merged()
    assert len(table) == 6 and table["record_id"].is_unique
    by_id = table.set_index("record_id")
    assert by_id.loc["oai:x:bc1", ["final_class", "decided_by", "rule_name"]].tolist() == [
        "bc_only", "rule", "rectangle box"]
    assert by_id.loc["oai:x:nb1", "final_class"] == "elsewhere"
    hakai = by_id.loc["oai:catalogue.hakai.org:h1"]
    assert (hakai["llm_class"], hakai["final_class"], hakai["decided_by"]) == ("elsewhere", "bc_only",
                                                                                "bc_only_catalogue")
    global_one = by_id.loc["oai:catalogue.hakai.org:h2"]
    assert (global_one["llm_class"], global_one["final_class"], global_one["frontend_group"]) == (
        "global", "bc_only", "bc_data")
    canada = by_id.loc[f"oai:open.canada.ca:{UUID_A}"]
    assert canada["uuid"] == UUID_A and canada["evidence_check"] == "1 of 1 not found"
    assert canada["in_bc"] == "yes" and canada["evidence"] == "Victoria"


def test_merge_stops_if_the_run_is_not_step_1s_needs_llm_records():
    """A run missing a needs_llm record stops the merge rather than leaving a class out."""
    rules, pass1, pass2, harvest = made_up()
    pass2.pop("oai:catalogue.hakai.org:h2")
    with pytest.raises(SystemExit):
        f3.merge(rules, pass1, pass2, harvest, [])


# --- Copies ----------------------------------------------------------------

def test_copies_are_grouped_and_one_is_shown():
    """Records sharing a UUID, or a title and abstract, form a group; no row goes; the bc_data copy is shown."""
    table, harvest = merged()
    group = table[table["uuid"] == UUID_A]
    assert group["dup_group_id"].nunique() == 1 and group["dup_group_id"].iloc[0].startswith("G")
    assert group.set_index("source")["dup_show"].to_dict() == {"canwin": False, "open.canada.ca": True}
    alone = table[table["record_id"] == "oai:x:nb1"]
    assert alone["dup_group_id"].iloc[0] == "" and alone["dup_show"].iloc[0]
    conflicts, n_class, n_group = f3.duplicate_conflicts(table, harvest)
    assert n_group >= 1 and set(conflicts["record_id"]) >= set(group["record_id"])


def test_pick_shown_falls_back_to_the_lowest_record_id():
    """Within the best front end group, the lowest record id is shown."""
    members = pd.DataFrame({"record_id": ["oai:b", "oai:a", "oai:c"],
                            "frontend_group": ["pending", "pending", "dropped"]})
    assert f3.pick_shown(members) == "oai:a"


def test_national_plus_outside_goes_to_pending():
    """bc_and_outside_canada through canada_wide without in_bc yes keeps its class, goes to pending, with a note."""
    rules, pass1, pass2, harvest = made_up()
    record_id = f"oai:canwin:{UUID_A}"
    pass2[record_id] = pass2_result(record_id, "bc_and_outside_canada", facts("unclear", outside="yes", wide="yes"))
    pass2["oai:catalogue.hakai.org:h1"] = pass2_result("oai:catalogue.hakai.org:h1", "bc_and_outside_canada",
                                                       facts("unclear", outside="yes", wide="yes"))
    table = f3.merge(rules, pass1, pass2, harvest, []).set_index("record_id")
    row = table.loc[record_id]
    assert (row["final_class"], row["frontend_group"], row["note"]) == (
        "bc_and_outside_canada", "pending", f3.NOTE_NATIONAL)
    assert table.loc[f"oai:open.canada.ca:{UUID_A}", "frontend_group"] == "bc_data"


# --- The DFO trawl series --------------------------------------------------

def test_the_dfo_trawl_series_takes_the_hand_checked_class():
    """The hand-checked class becomes final_class, decided_by is hand, llm_class stays; the list is signed."""
    assert set(f3.DFO_PROPOSALS) == set(f3.DFO_TRAWL_SERIES.values())
    assert all(cls in CLASSES for cls, _ in f3.DFO_PROPOSALS.values())
    rules, pass1, pass2, harvest = made_up()
    uuid = "aa13418a-12f3-40d1-bfa8-83c7b8c795b0"
    record_id = f"oai:open.canada.ca:{uuid}"
    rules = pd.concat([rules, rules.iloc[[3]].assign(record_id=record_id)], ignore_index=True)
    pass2[record_id] = pass2_result(record_id, "bc_and_outside_canada", facts(outside="yes"))
    pass1[record_id] = pass1[f"oai:open.canada.ca:{UUID_A}"]
    harvest.loc[record_id] = harvest.loc["oai:x:bc1"]
    table = f3.merge(rules, pass1, pass2, harvest, [])
    checked = f3.dfo_handcheck(table, harvest)
    assert checked[["series_part", "proposed_class", "checked_by", "date"]].values.tolist() == [
        ["Vancouver Island Shelf", "bc_only", "Lucia", "2026-10-27"]]
    row = table.set_index("record_id").loc[record_id]
    assert (row["final_class"], row["decided_by"], row["llm_class"], row["frontend_group"]) == (
        "bc_only", "hand", "bc_and_outside_canada", "bc_data")


# --- Hakai records outside BC ------------------------------------------------

def test_hakai_records_are_set_by_hand_with_their_copies():
    """A Hakai record in HAKAI_HAND takes its checked class (decided_by hand), and so does its CIOOS copy."""
    whale = "oai:catalogue.hakai.org:ca-cioos_35900174-c64a-493e-9a7e-390ac7e997e4"
    copy = "oai:catalogue.cioos.ca:ca-cioos_35900174-c64a-493e-9a7e-390ac7e997e4"
    rules, pass1, pass2, harvest = made_up()
    extra = pd.DataFrame([
        {"record_id": whale, "source": "catalogue.hakai.org", "bc_class": "bc", "decided_by": "source",
         "box_class": None},
        {"record_id": copy, "source": "catalogue.cioos.ca", "bc_class": "not_bc", "decided_by": "box",
         "box_class": "outside"}]).assign(evidence="e", box_share=None, text_title="", text_keywords="",
                                           text_abstract="")
    rules = pd.concat([rules, extra], ignore_index=True)
    for record_id in (whale, copy):
        harvest.loc[record_id] = harvest.loc["oai:x:bc1"]
        harvest.loc[record_id, "title"] = "Bowhead Whale Drone Data Collection - Cumberland Sound - Nunavut"
    table = f3.add_duplicates(f3.merge(rules, pass1, pass2, harvest, ["catalogue.hakai.org"]), harvest)
    by_id = table.set_index("record_id")
    assert by_id.loc[whale, ["final_class", "decided_by", "rule_class", "frontend_group"]].tolist() == [
        "elsewhere", "hand", "bc", "dropped"]
    assert by_id.loc[copy, ["final_class", "decided_by", "rule_class", "before_hand"]].tolist() == [
        "elsewhere", "hand", "not_bc", "elsewhere (rule)"]
    assert by_id.loc[copy, "note"] == f"hand check applied via copy {whale}"
    checked = f3.hakai_handcheck(table, harvest, ["catalogue.hakai.org"]).iloc[0]
    assert checked["catalogue_class"] == "bc_only" and "Nunavut" in checked["outside_bc_place_words"]
    assert checked["copy_class"] == "catalogue.cioos.ca elsewhere (rule), now elsewhere (hand)"
    assert (checked["checked_by"], checked["date"], checked["source_checked"]) == ("Lucia", "2026-10-27", "external")
    assert checked["reason"].startswith("Source page:")


def test_a_hand_check_sets_every_copy_and_disagreeing_checks_stop(monkeypatch):
    """A hand check passes to its copies (llm_class kept); two hand checks that disagree in one group stop the merge."""
    canwin, canada = f"oai:canwin:{UUID_A}", f"oai:open.canada.ca:{UUID_A}"
    monkeypatch.setattr(f3, "HAKAI_HAND", {canwin: ("unknown", "checked")})
    rules, pass1, pass2, harvest = made_up()
    table = f3.merge(rules, pass1, pass2, harvest, []).set_index("record_id")
    assert table.loc[canada, ["final_class", "decided_by", "llm_class", "frontend_group", "note"]].tolist() == [
        "unknown", "hand", "bc_only", "dropped", f"hand check applied via copy {canwin}"]
    assert table.loc[canwin, ["final_class", "decided_by", "llm_class"]].tolist() == ["unknown", "hand", "all_canada"]
    assert table.loc["oai:x:bc1", "decided_by"] == "rule"
    monkeypatch.setattr(f3, "HAKAI_HAND", {canwin: ("unknown", "checked"), canada: ("bc_only", "checked")})
    with pytest.raises(SystemExit) as stop:
        f3.merge(rules, pass1, pass2, harvest, [])
    assert "disagree" in str(stop.value) and canwin in str(stop.value) and canada in str(stop.value)
    monkeypatch.setattr(f3, "HAKAI_HAND", {canwin: ("unknown", "checked"), canada: ("unknown", "checked")})
    table = f3.merge(rules, pass1, pass2, harvest, []).set_index("record_id")
    assert "via copy" not in table.loc[canada, "note"]


# --- The run folder is only read --------------------------------------------

def test_reading_the_run_writes_nothing(tmp_path):
    """read_run only reads: the folder's files and their bytes are the same afterwards."""
    run = tmp_path / "run"
    for name in ("pass1", "pass2"):
        (run / name).mkdir(parents=True)
        (run / name / "answers.jsonl").write_text(json.dumps({"record_id": "r", "answer": {}}) + "\n")
    (run / "run.json").write_text(json.dumps({"model": "m"}))
    before = {p: p.read_bytes() for p in run.rglob("*") if p.is_file()}
    pass1, pass2, about = f3.read_run(str(run))
    assert set(pass1) == {"r"} and about["model"] == "m"
    assert before == {p: p.read_bytes() for p in run.rglob("*") if p.is_file()}


# --- Checks ----------------------------------------------------------------

def test_unknown_reasons_come_from_the_facts_first():
    """The facts decide first; then the reason's words; then whether any evidence was given."""
    row = {"other_canada": "yes", "outside_canada": "yes", "reason": "", "evidence": ""}
    assert checks.unknown_reason(row).startswith("names other provinces and places outside")
    row = {"other_canada": "no", "outside_canada": "no", "reason": "A laboratory experiment on mice.", "evidence": "x"}
    assert checks.unknown_reason(row).startswith("lab")
    row = {"other_canada": "no", "outside_canada": "no", "reason": "UBC is only an affiliation.", "evidence": "UBC"}
    assert checks.unknown_reason(row).startswith("only an affiliation")
    row = {"other_canada": "no", "outside_canada": "no", "reason": "Nothing says where.", "evidence": ""}
    assert checks.unknown_reason(row).startswith("no place at all")


def test_bc_and_outside_routes(tmp_path):
    """bc_and_outside_canada records are split by whether in_bc was yes or only canada_wide was."""
    table = pd.DataFrame([
        {"record_id": "a", "source": "s", "title": "t", "final_class": "bc_and_outside_canada", "decided_by": "llm",
         **facts(outside="yes"), "in_bc_used": "yes", "evidence": "", "reason": ""},
        {"record_id": "b", "source": "s", "title": "t", "final_class": "bc_and_outside_canada", "decided_by": "llm",
         **facts("unclear", outside="yes", wide="yes"), "in_bc_used": "unclear", "evidence": "", "reason": ""},
    ])
    harvest = pd.DataFrame([{"id": r, "title": "t", "abstract": "a", "subjects": None, "places": None,
                             "points": None, "boxes": None} for r in "ab"]).set_index("id")
    via_bc, via_canada, rows = checks.bc_outside_routes(table, harvest)
    assert via_bc == 1 and list(via_canada["record_id"]) == ["b"] and rows["harvest_abstract"].tolist() == ["a"]


def test_scores_as_geo_pass2_score_and_by_group():
    """Exact, shown and hidden wrongly as geo_pass2_score.py counts them, and the new group scores."""
    s = checks.score(["bc_only", "all_canada", "unknown", "elsewhere"],
                     ["canada_incl_bc", "unknown", "all_canada", "elsewhere"])
    assert (s["exact"], s["shown_wrongly"], s["hidden_wrongly"]) == (1, 1, 1)
    assert (s["group_right"], s["into_bc_data_wrongly"], s["out_of_bc_data_wrongly"]) == (2, 0, 0)


def test_excel_safe_keeps_text_and_counts_changes():
    """Text Excel can store is kept exactly; refused control characters are replaced and counted."""
    from collections import Counter
    counter = Counter()
    frame = pd.DataFrame({"a": ["plain é text", "bad\x01char"], "n": [1, 2]})
    out = checks.excel_safe(frame, counter)
    assert out["a"].tolist() == ["plain é text", "bad char"] and counter["replaced"] == 1


def test_as_harvested_keeps_strings_and_writes_lists_as_json():
    """A string is returned as it is; lists and arrays as JSON; missing as empty."""
    import numpy as np
    assert checks.as_harvested("  as is\n") == "  as is\n"
    assert checks.as_harvested(np.array(["Fish", "Pêche"], dtype=object)) == '["Fish", "Pêche"]'
    assert checks.as_harvested(None) == ""
