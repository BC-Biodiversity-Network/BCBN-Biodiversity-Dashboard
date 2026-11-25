"""Tests for DataONE filter 3's final table (pipeline/dataone/build_filter3_final.py).

What they check, on made-up records:
  - the route order: a lunaris record takes its Lunaris class, a rule
    record the Lunaris mapping of its rule result, an llm record the class
    from the model's facts (never its rule result); the either-yes records
    are added after, and the hand decisions come last;
  - hand decisions win over every route, keep their words, and must name
    records the table holds;
  - the either-yes rule: only DOI or UUID matches that did not reuse
    Lunaris count; DataONE no and Lunaris yes is added with the Lunaris
    class, DataONE yes and Lunaris no stays as it is;
  - the front end groups, as the Lunaris pipeline sets them;
  - one row per record, each with a class.
The last tests check the hand decisions file in git, and the real output
when it is on this machine.

The DataONE script has the same file name as the Lunaris
build_filter3_final.py, so it is loaded here under another module name,
after the Lunaris one is imported."""

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

BACKEND = Path(__file__).resolve().parents[1]
for folder in ("pipeline/dataone", "pipeline/lunaris/llm", "pipeline/lunaris"):
    sys.path.insert(0, str(BACKEND / folder))
import build_filter3_final as lunaris_final  # noqa: E402  (the Lunaris one: pipeline/lunaris is first)
from geo_pass2_classes import decide  # noqa: E402

assert Path(lunaris_final.__file__).parent == BACKEND / "pipeline" / "lunaris"
SPEC = importlib.util.spec_from_file_location("dataone_build_filter3_final",
                                              BACKEND / "pipeline" / "dataone" / "build_filter3_final.py")
f3 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(f3)

FACTS_NO = {"in_bc": "no", "other_canada": "no", "outside_canada": "yes", "canada_wide": "no", "global": "no"}


# --- Made-up inputs ------------------------------------------------------------------

def rule_row(record_id, route, rule_class="", final_class="", lunaris=("",)):
    """Return one made-up filter3_rules.parquet row."""
    return {"id": record_id, "datasource": "KNB", "group": "touches_bc_partly", "multibox": False,
            "keep_source": "both", "route": route, "route_reason": f"{route} reason", "rule_class": rule_class,
            "rule_decided_by": "box" if rule_class else "", "rule_evidence": "", "box_class": "", "box_share": None,
            "point_class": "", "lunaris_ids": [f"L-{record_id}"] if route == "lunaris" else [],
            "lunaris_classes": list(lunaris) if route == "lunaris" else [], "final_class": final_class}


def pass2_result(record_id, facts, number=1):
    """Return a made-up pass 2 result with an answer, as geo_filter_run.py saves it (no boxes)."""
    answer = {**facts, "evidence": ["a quote"], "reason": "made up"}
    return {"number": number, "record_id": record_id, "answer": answer, "decision": decide(answer, []),
            "box_shares": [], "evidence_check": {"items": 1, "loose": 1, "unmatched": []}, "attempts": 1,
            "input_tokens": 100}


def llm_columns_of(record_id, facts, number=1):
    """Return the LLM columns of a made-up answered record, as main builds them."""
    result = pass2_result(record_id, facts, number)
    return f3.llm_row({"record_id": record_id, "places": []}, result, result["decision"])


def failed_llm(record_id, number=9):
    """Return the LLM columns of a made-up record whose pass 2 failed."""
    return f3.llm_row({"record_id": record_id, "error": "timed out"},
                      {"number": number, "record_id": record_id, "error": "pass 1 has no answer"}, None)


def hand_table(rows):
    """Return made-up hand decisions, as read_hand returns them, from (record id, class, reason) tuples."""
    columns = ["record_id", "final_class", "reason", "decided_by", "decided_on", "recorded_in"]
    table = pd.DataFrame([{"record_id": r, "final_class": c, "reason": why, "decided_by": "Lucia, 2026-11-24",
                           "decided_on": "2026-11-24", "recorded_in": "a file, row 1"} for r, c, why in rows],
                         columns=columns)
    return table.set_index("record_id", drop=False)


def made_up_table(hand=(), either_yes=None):
    """Return the final table of four made-up records (one per route, and a failed llm one)."""
    rules = pd.DataFrame([
        rule_row("lun", "lunaris", rule_class="bc", final_class="global", lunaris=("global",)),
        rule_row("rul", "rule", rule_class="not_bc", final_class="elsewhere"),
        rule_row("llm", "llm", rule_class="bc"),
        rule_row("bad", "llm", rule_class="needs_llm"),
    ]).set_index("id", drop=False)
    titles = pd.Series({i: f"title {i}" for i in rules.index})
    llm = {"llm": llm_columns_of("llm", FACTS_NO), "bad": failed_llm("bad")}
    return f3.build(rules, titles, llm, [], either_yes if either_yes is not None else pd.DataFrame(),
                    hand_table(hand))


# --- The route order -----------------------------------------------------------------

def test_each_route_gives_its_own_class():
    assert f3.route_class("lunaris", "bc", "", "global", "KNB", []) == "global"
    assert f3.route_class("rule", "bc", "", "", "KNB", []) == "bc_only"
    assert f3.route_class("rule", "not_bc", "", "", "KNB", []) == "elsewhere"
    # An llm record's class is the model's, whatever the rules said about it.
    assert f3.route_class("llm", "bc", "elsewhere", "", "KNB", []) == "elsewhere"
    assert f3.route_class("llm", "not_bc", "bc_only", "", "KNB", []) == "bc_only"


def test_a_lunaris_bc_only_catalogue_stops_the_script():
    with pytest.raises(SystemExit):
        f3.route_class("llm", "", "elsewhere", "", "catalogue.hakai.org", ["catalogue.hakai.org"])


def test_the_table_takes_each_routes_class():
    table = made_up_table(hand=[("bad", "elsewhere", "read by hand")]).set_index("record_id")
    assert table.loc["lun", "final_class"] == "global" and table.loc["lun", "route"] == "lunaris"
    assert table.loc["rul", "final_class"] == "elsewhere" and table.loc["rul", "route"] == "rule"
    assert table.loc["llm", "final_class"] == "elsewhere" and table.loc["llm", "route"] == "llm"
    assert table.loc["llm", "model_in_bc"] == "no" and not table.loc["llm", "in_bc_filled_by_box_rule"]


def test_a_rule_class_that_differs_from_the_rules_file_stops_the_script():
    rules = pd.DataFrame([rule_row("rul", "rule", rule_class="bc", final_class="elsewhere")]).set_index("id",
                                                                                                         drop=False)
    with pytest.raises(SystemExit):
        f3.base_table(rules, pd.Series({"rul": "t"}), {}, [])


def test_the_box_rule_fill_is_kept_from_the_decision():
    facts = {"in_bc": "unclear", "other_canada": "no", "outside_canada": "no", "canada_wide": "no", "global": "no"}
    answer = {**facts, "evidence": [], "reason": "r"}
    result = {"number": 1, "record_id": "x", "answer": answer, "decision": decide(answer, [0.8]),
              "box_shares": [0.8], "evidence_check": {}}
    row = f3.llm_row(None, result, result["decision"])
    assert row["llm_class"] == "bc_only" and row["model_in_bc"] == "unclear" and row["in_bc_used"] == "yes"
    assert row["in_bc_filled_by_box_rule"] is True


def test_saved_shares_must_match_the_measured_ones():
    assert f3.same_shares([0.5, None], [0.5 + 1e-12, None])
    assert not f3.same_shares([0.5, None], [0.4, None])
    assert not f3.same_shares([None], [0.1]) and not f3.same_shares([0.1], [0.1, 0.2])


# --- Hand decisions -----------------------------------------------------------------

def test_hand_decisions_win_over_every_route():
    hand = [("lun", "bc_only", "Lunaris was wrong"), ("rul", "bc_only", "the rule was wrong"),
            ("llm", "bc_and_outside_canada", "the model was wrong"), ("bad", "unknown", "it failed")]
    table = made_up_table(hand=hand).set_index("record_id")
    for record_id, cls, why in hand:
        assert table.loc[record_id, "route"] == "hand" and table.loc[record_id, "final_class"] == cls
        assert table.loc[record_id, "hand_reason"] == why
        assert table.loc[record_id, "decided_by"] == "Lucia, 2026-11-24"
    assert table.loc["lun", "route_before_hand"] == "lunaris" and table.loc["lun", "class_before_hand"] == "global"
    assert table.loc["bad", "route_before_hand"] == "llm" and table.loc["bad", "class_before_hand"] == ""


def test_hand_decisions_for_a_record_not_in_the_table_stop_the_script():
    with pytest.raises(SystemExit):
        made_up_table(hand=[("bad", "elsewhere", "r"), ("nowhere", "elsewhere", "r")])


def test_a_hand_decision_also_wins_over_an_either_yes_record():
    added = either_yes_frame()
    table = made_up_table(hand=[("bad", "elsewhere", "r"), ("add", "bc_only", "checked")], either_yes=added)
    row = table.set_index("record_id").loc["add"]
    assert row["route"] == "hand" and row["route_before_hand"] == "lunaris_either_yes"
    assert row["final_class"] == "bc_only"


# --- The either-yes rule --------------------------------------------------------------

def union_frame():
    """Return a made-up filter 2 union: the low-similarity matches and some that must not count."""
    rows = [
        ("no_yes", "doi", False, False, "yes", 0.5),      # DataONE no, Lunaris yes: added
        ("yes_no", "uuid", False, True, "no", 0.8),       # DataONE yes, Lunaris no: listed
        ("agree", "doi", False, True, "yes", 0.7),        # the same answer: not a disagreement
        ("reused", "doi", True, None, "yes", 0.95),       # reused Lunaris: not sent to filter 2
        ("title", "title_only", False, False, "yes", 1.0),  # matched by title only: not a copy
        ("mixed", "doi", False, False, "mixed", 0.3),     # no single Lunaris decision
    ]
    table = pd.DataFrame(rows, columns=["id", "match_type", "reuse_lunaris", "keep_union", "lunaris_decision",
                                        "title_similarity"])
    table["keep_union"] = table["keep_union"].astype("boolean")
    table = table.assign(datasource="DRYAD", group="no_box", multibox=False, keep_source="neither")
    return table.set_index("id", drop=False)


def test_only_low_similarity_copies_with_another_answer_count():
    differ = f3.low_similarity_disagreements(union_frame())
    assert list(differ["id"]) == ["no_yes", "yes_no"]
    assert list(differ["dataone_answer"]) == ["no", "yes"] and list(differ["lunaris_answer"]) == ["yes", "no"]


def described_frame(classes=("unknown",)):
    """Return the two made-up disagreements, described, with the Lunaris copy of no_yes holding classes."""
    differ = f3.low_similarity_disagreements(union_frame())
    copies = {"no_yes": (["L1"] * len(classes), list(classes)), "yes_no": (["L2"], [""])}
    lunaris = pd.DataFrame({"final_class": ["unknown"], "frontend_group": ["dropped"]}, index=["L1"])
    return f3.describe_disagreements(differ, copies, lunaris, pd.Series({"no_yes": "A", "yes_no": "B"}),
                                     {"L1": "Lunaris A", "L2": "Lunaris B"})


def either_yes_frame():
    """Return one made-up either-yes row for the record id "add"."""
    added = f3.either_yes_rows(described_frame(), union_frame())
    return added.assign(record_id="add")


def test_dataone_no_and_lunaris_yes_is_added_with_the_lunaris_class():
    described = described_frame()
    assert list(described["action"]) == ["added as kept (lunaris_either_yes)",
                                         "kept as it is; fix the Lunaris side later"]
    added = f3.either_yes_rows(described, union_frame())
    assert list(added["record_id"]) == ["no_yes"]
    row = added.iloc[0]
    assert row["route"] == "lunaris_either_yes" and row["final_class"] == "unknown"
    assert row["decided_by"] == "rule: kept if either copy says yes, approved by Lucia, 2026-11-24"


def test_copies_without_one_class_stop_the_either_yes_rule():
    with pytest.raises(SystemExit):
        f3.either_yes_rows(described_frame(("unknown", "global")), union_frame())
    with pytest.raises(SystemExit):
        f3.either_yes_rows(described_frame(("",)), union_frame())


def test_a_failed_filter_2_answer_stops_the_rule():
    union = union_frame()
    union.loc["no_yes", "keep_union"] = pd.NA
    differ = f3.low_similarity_disagreements(union)
    with pytest.raises(SystemExit):
        f3.describe_disagreements(differ, {"no_yes": (["L1"], ["unknown"]), "yes_no": (["L2"], [""])},
                                  pd.DataFrame(columns=["final_class", "frontend_group"]),
                                  pd.Series({"no_yes": "A", "yes_no": "B"}), {})


def test_the_either_yes_record_ends_in_its_lunaris_group():
    table = made_up_table(hand=[("bad", "elsewhere", "r")], either_yes=either_yes_frame())
    row = table.set_index("record_id").loc["add"]
    assert row["frontend_group"] == "dropped"
    lunaris = pd.DataFrame({"final_class": ["global", "unknown"], "frontend_group": ["pending", "dropped"]},
                           index=["L-lun", "L1"])
    f3.check_lunaris_groups(table, lunaris)
    lunaris.loc["L1", "frontend_group"] = "bc_data"
    with pytest.raises(SystemExit):
        f3.check_lunaris_groups(table, lunaris)


# --- Front end groups ------------------------------------------------------------------

def group_of(route, cls, in_bc_used=""):
    """Return the front end group and note set_groups gives one made-up record."""
    table = pd.DataFrame({"route": [route], "final_class": [cls], "in_bc_used": [in_bc_used]})
    f3.set_groups(table)
    return table.at[0, "frontend_group"], table.at[0, "note"]


@pytest.mark.parametrize("cls", ["bc_only", "canada_incl_bc", "all_canada", "bc_and_outside_canada", "global",
                                 "elsewhere", "unknown"])
def test_each_class_goes_to_the_lunaris_group(cls):
    assert group_of("lunaris", cls)[0] == lunaris_final.FRONTEND_GROUP[cls]
    assert group_of("llm", cls, "yes")[0] == lunaris_final.FRONTEND_GROUP[cls]


def test_bc_and_outside_canada_without_in_bc_yes_goes_to_pending():
    assert group_of("llm", "bc_and_outside_canada", "no") == ("pending", lunaris_final.NOTE_NATIONAL)
    assert group_of("llm", "bc_and_outside_canada", "unclear")[0] == "pending"
    assert group_of("llm", "bc_and_outside_canada", "yes") == ("bc_data", "")


@pytest.mark.parametrize("route", ["hand", "lunaris", "lunaris_either_yes", "rule"])
def test_only_the_models_own_classes_can_go_to_pending_that_way(route):
    assert group_of(route, "bc_and_outside_canada", "")[0] == "bc_data"


# --- One row per record -------------------------------------------------------------------

def test_every_record_appears_once_with_a_class():
    table = made_up_table(hand=[("bad", "elsewhere", "r")])
    f3.check_complete(table, ["lun", "rul", "llm", "bad"])
    assert len(table) == 4 and table["record_id"].is_unique


def test_a_failed_record_without_a_hand_decision_stops_the_script():
    with pytest.raises(SystemExit):
        f3.check_complete(made_up_table(), ["lun", "rul", "llm", "bad"])


def test_a_record_twice_missing_or_extra_stops_the_script():
    table = made_up_table(hand=[("bad", "elsewhere", "r")])
    with pytest.raises(SystemExit):
        f3.check_complete(pd.concat([table, table.iloc[[0]]], ignore_index=True), ["lun", "rul", "llm", "bad"])
    with pytest.raises(SystemExit):
        f3.check_complete(table, ["lun", "rul", "llm", "bad", "gone"])
    with pytest.raises(SystemExit):
        f3.check_complete(table, ["lun", "rul", "llm"])


# --- The hand decisions file and the real output ---------------------------------------------

def test_the_hand_decisions_file_is_readable_and_has_the_filter_2_layout():
    hand = f3.read_hand()
    filter2 = pd.read_csv(BACKEND / "pipeline" / "dataone" / "filter2_hand_decisions.csv", nrows=0)
    assert list(hand.columns) == [("final_class" if c == "kept" else c) for c in filter2.columns]
    assert len(hand) == 16 and hand["record_id"].is_unique
    assert set(hand["final_class"]) <= set(lunaris_final.FRONTEND_GROUP)


def test_the_written_table_has_one_row_per_record():
    if not Path(f3.OUT).exists():
        pytest.skip("filter3_final.parquet is not on this machine")
    table = pd.read_parquet(f3.OUT)
    rules = pd.read_parquet(f3.RULES, columns=["id"])
    assert table["record_id"].is_unique and set(rules["id"]) <= set(table["record_id"])
    assert len(table) == len(rules) + int((table["route"] == "lunaris_either_yes").sum())
    assert table["final_class"].isin(lunaris_final.FRONTEND_GROUP).all()
    hand = f3.read_hand()
    by_id = table.set_index("record_id")
    for row in hand.itertuples():
        assert by_id.loc[row.record_id, "route"] == "hand"
        assert by_id.loc[row.record_id, "final_class"] == row.final_class
        assert by_id.loc[row.record_id, "hand_reason"] == row.reason
