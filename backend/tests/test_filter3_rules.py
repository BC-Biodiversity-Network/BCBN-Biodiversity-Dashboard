"""Tests for DataONE filter 3's rules step (pipeline/dataone/build_filter3_rules.py).

The routing: a record reusing Lunaris takes its Lunaris class unless its
Lunaris copies disagree; the west_gt_east, sign_check and send_to_llm
groups and every record with no box go to the LLM; otherwise the Lunaris
rules decide (bc gives bc_only, not_bc gives elsewhere) or leave it to the
LLM. The Lunaris rules read DataONE records as they are, and a Lunaris
catalogue rule can never decide one. The last test checks the real output
when it is on this machine."""

import sys
from pathlib import Path

import pandas as pd
import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "pipeline" / "dataone"))
import build_filter3_rules as f3  # noqa: E402
from bc_filter_rules import load_rules  # noqa: E402

BOUNDARY = Path(f3.BC_BOUNDARY)
OUTPUT = Path(f3.OUT)


@pytest.fixture(scope="module")
def rules():
    """The Lunaris BC filter rules with BC's real outline."""
    if not BOUNDARY.exists():
        pytest.skip("bc_boundary_gbif.wkt is not on this machine")
    return load_rules(str(BOUNDARY))


def record(record_id="doi:10.0000/x", boxes=()):
    """Return a made-up DataONE record as filter3_records.read_geo_records gives it."""
    return {"id": record_id, "repository": "PANGAEA", "title": "Fish", "subjects": ["fish"], "abstract": "Fish.",
            "named_location": None, "place_key": None, "site": None,
            "boxes": [dict(zip("wesn", b)) for b in boxes], "unreadable_boxes": 0}


# --- Routing -------------------------------------------------------------------

def test_a_reused_record_takes_its_lunaris_class_unless_the_copies_disagree():
    assert f3.lunaris_route(["bc_only"]) == ("lunaris", "bc_only", "Lunaris filter 3 class")
    assert f3.lunaris_route(["unknown", "unknown"])[:2] == ("lunaris", "unknown")
    route, final, reason = f3.lunaris_route(["unknown", "global"])
    assert (route, final) == ("llm", "") and reason == "Lunaris copies disagree: global | unknown"
    assert f3.lunaris_route([])[:2] == ("llm", "") and f3.lunaris_route(["bc_only", ""])[0] == "llm"


@pytest.mark.parametrize("group", ["west_gt_east", "sign_check", "send_to_llm"])
@pytest.mark.parametrize("rule_class", ["bc", "not_bc", "needs_llm"])
def test_three_groups_always_go_to_the_llm(group, rule_class):
    assert f3.rule_route(group, rule_class)[:2] == ("llm", "")


def test_the_lunaris_rule_to_class_mapping_is_used():
    assert f3.rule_route("inside_bc", "bc")[:2] == ("rule", "bc_only")
    assert f3.rule_route("touches_bc_partly", "not_bc")[:2] == ("rule", "elsewhere")
    assert f3.rule_route("touches_bc_partly", "needs_llm")[:2] == ("llm", "")
    assert f3.rule_route("no_box", "needs_llm") == ("llm", "", "no box")


def test_every_kept_record_must_appear_once():
    table = pd.DataFrame({"id": ["a", "b"], "route": ["rule", "llm"]})
    f3.check_complete(table, ["a", "b"], expected=2)
    with pytest.raises(SystemExit):
        f3.check_complete(pd.DataFrame({"id": ["a", "a"], "route": ["rule", "llm"]}), ["a"], expected=2)
    with pytest.raises(SystemExit):
        f3.check_complete(table, ["a", "b", "c"], expected=2)
    with pytest.raises(SystemExit):
        f3.check_complete(table, ["a", "b"], expected=3)


# --- The Lunaris rules on DataONE records ------------------------------------

def test_the_lunaris_rules_read_dataone_records(rules):
    records = {
        "in": record("in", [(-123.5, -123.0, 49.5, 50.0)]),            # inside BC
        "edge": record("edge", [(-114.06, -110.0, 49.0, 52.0)]),       # Alberta, touching BC's edge
        "point": record("point", [(-123.1, -123.1, 49.25, 49.25)]),    # a point in Vancouver
        "round": record("round", [(180.0, -180.0, -90.0, 90.0)]),      # all the way round
        "none": record("none", []),
    }
    results = f3.rule_results(records, rules)
    assert results.loc["in", "bc_class"] == "bc" and results.loc["point", "bc_class"] == "bc"
    assert results.loc["edge", "bc_class"] == "not_bc"
    assert results.loc["round", "bc_class"] == "needs_llm" and results.loc["none", "bc_class"] == "needs_llm"


def test_a_lunaris_catalogue_can_never_decide_a_dataone_record(rules):
    hakai = "oai:catalogue.hakai.org:x"     # an id the Lunaris catalogue list knows (BC only)
    with pytest.raises(SystemExit):
        f3.rule_results({hakai: record(hakai)}, rules)


# --- The real output ------------------------------------------------------------

def test_the_written_table_follows_the_decisions():
    if not OUTPUT.exists():
        pytest.skip("filter3_rules.parquet is not on this machine")
    table = pd.read_parquet(OUTPUT)
    assert len(table) == f3.EXPECTED_KEPT and table["id"].is_unique
    rule = table[table["route"] == "rule"]
    assert set(rule["final_class"]) <= {"bc_only", "elsewhere"}
    assert (rule["final_class"] == rule["rule_class"].map(f3.RULE_TO_CLASS)).all()
    not_reused = table["route"] != "lunaris"
    assert not (table["group"].isin(f3.ALWAYS_LLM) & not_reused & (table["route"] != "llm")).any()
    assert not ((table["group"] == "no_box") & not_reused & (table["route"] != "llm")).any()
    assert (table.loc[table["route"] == "llm", "final_class"] == "").all()
    lunaris = table[table["route"] == "lunaris"]
    assert (lunaris["final_class"] == lunaris["lunaris_classes"].map(lambda c: c[0])).all()
