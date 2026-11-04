"""Tests for the place lookup's stage 3 checks (pipeline/lunaris/place_pick_checks.py):
the quote check (exact text, pieces joined with "..." only in order), the
outcome of the two orders, which agreed places are drawn, the Water Hub
distances, the list of lakes at 1:1,000,000, and a whole run of the script
on a made-up input and run folder."""

import gzip
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "pipeline" / "lunaris"))
sys.path.insert(0, str(BACKEND / "tests"))
import place_pick_checks as checks  # noqa: E402
from test_place_drawing_rules import CGN_HEADER, cgn_row  # noqa: E402
from test_place_pick_prompt import ITEM  # noqa: E402

RECORD = checks.record_section(ITEM)


def test_the_record_section_is_what_the_prompt_showed_without_our_notes():
    assert RECORD.startswith("Record\nTitle: Susan Lake at Recreation Site\nDescription: The Columbia")
    assert RECORD.endswith("Places in the record: (none)")
    assert "Catalogue" not in RECORD and "Note from us" not in RECORD


def test_an_exact_quote_passes_and_a_changed_one_does_not():
    assert checks.quote_check("Susan Lake at Recreation Site", RECORD) == (True, 1, "")
    assert checks.quote_check("Upper Columbia; Lake", RECORD)[0]
    assert not checks.quote_check("susan lake at recreation site", RECORD)[0]
    assert not checks.quote_check("Susan Lake, BC", RECORD)[0]
    assert checks.quote_check("", RECORD) == (False, 0, "empty quote")
    # Our notes are not the record.
    assert not checks.quote_check("collected only in BC", RECORD)[0]


def test_spaces_and_line_breaks_count_as_one_space():
    assert checks.quote_check("Recreation Site Description:  The Columbia", RECORD)[0]


def test_pieces_joined_with_dots_pass_only_when_all_are_in_the_record_in_order():
    assert checks.quote_check("Susan Lake at Recreation Site ... collects plankton samples", RECORD) == (True, 2, "")
    assert checks.quote_check("Susan Lake…CSISS", RECORD) == (True, 2, "")
    ok, pieces, problem = checks.quote_check("collects plankton samples ... Susan Lake at Recreation Site", RECORD)
    assert (ok, pieces) == (False, 2) and "out of order" in problem
    ok, _, problem = checks.quote_check("Susan Lake ... near Revelstoke", RECORD)
    assert not ok and "piece 2 of 2 not in the record" in problem
    assert checks.quote_check("... Susan Lake ...", RECORD) == (True, 1, "")


def test_distance_in_km():
    assert checks.distance_km(49.0, -123.0, 49.0, -123.0) == 0
    assert round(checks.distance_km(51.5703, -117.3978, 51.56768, -117.38848), 1) == 0.7


def answer(order, geonameid, quote="Susan Lake at Recreation Site", none_because=None, reason="The title names it."):
    """Return one line of answers.jsonl for Susan Lake."""
    return {"dataset_id": ITEM["dataset_id"], "name": "Susan Lake", "order": order, "geonameid": geonameid,
            "candidate_number": 0 if geonameid is None else 1, "none_because": none_because, "quote": quote,
            "reason": reason}


def cgn(tmp_path, rows):
    """Write and read a small Canadian names file."""
    path = tmp_path / "cgn.csv"
    path.write_text("﻿" + "\n".join([CGN_HEADER] + rows) + "\n", encoding="utf-8")
    return checks.drawing.read_cgn(str(path))


NO_HAND_LIST = (frozenset(), frozenset())


def row_for(tmp_path, lookup, shuffled, rows=()):
    """Return names.csv's row for Susan Lake with these two answers."""
    by_order = {a["order"]: a for a in (lookup, shuffled) if a}
    return checks.name_row(ITEM, "Susan Lake", by_order, {1: "H", 2: "H", 3: "H"}, cgn(tmp_path, list(rows)),
                           NO_HAND_LIST)


SUSAN_CGN = cgn_row("Susan Lake", "LAKE", 51.5703, -117.3978, 50000)


def test_the_same_place_in_both_orders_with_good_quotes_is_drawn(tmp_path):
    row = row_for(tmp_path, answer("lookup", 2), answer("shuffled", 2), [SUSAN_CGN])
    assert (row["outcome"], row["drawing_rule"], row["drawn"], row["why"]) == ("same_place", "lake", True, "drawn")
    assert row["place"] == "Susan Lake" and row["latitude"] == 51.5703


def test_two_different_places_or_a_missing_answer_are_not_drawn(tmp_path):
    row = row_for(tmp_path, answer("lookup", 1), answer("shuffled", 2), [SUSAN_CGN])
    assert (row["outcome"], row["drawn"]) == ("orders_disagree", False)
    assert row["lookup_answer"].startswith("Susan Lake (lake, British Columbia, Canada; 1)")
    row = row_for(tmp_path, answer("lookup", 2), None, [SUSAN_CGN])
    assert (row["outcome"], row["drawn"]) == ("an_order_has_no_answer", False)


def test_both_none_gives_both_reasons(tmp_path):
    row = row_for(tmp_path, answer("lookup", None, none_because="ambiguous"),
                  answer("shuffled", None, none_because="not_in_list"))
    assert (row["outcome"], row["why"], row["drawn"]) == ("both_none", "both_none: ambiguous / not_in_list", False)


def test_a_quote_not_in_the_record_keeps_an_agreed_place_off_the_map(tmp_path):
    row = row_for(tmp_path, answer("lookup", 2), answer("shuffled", 2, quote="Susan Lake near Revelstoke"),
                  [SUSAN_CGN])
    assert (row["outcome"], row["drawn"], row["why"]) == ("same_place", False, "quote_not_in_record")
    assert not row["shuffled_quote_ok"] and row["lookup_quote_ok"]


def test_an_agreed_place_the_drawing_rules_refuse_is_not_drawn(tmp_path):
    row = row_for(tmp_path, answer("lookup", 2), answer("shuffled", 2))
    assert (row["drawing_rule"], row["drawn"], row["why"]) == ("lake_no_size", False, "not_drawn: lake_no_size")


def test_a_reason_naming_the_catalogue_is_flagged(tmp_path):
    row = row_for(tmp_path, answer("lookup", 1, reason="The record is from a BC catalogue, so the BC lake."),
                  answer("shuffled", 2))
    assert row["lookup_reason_names_catalogue"] and not row["shuffled_reason_names_catalogue"]


def test_check_points_give_every_chosen_place_with_its_distance(tmp_path):
    item = dict(ITEM, dataset_id="oai:canwin-datahub.ad.umanitoba.ca:susan-lake-at-recreation-site")
    row = checks.name_row(item, "Susan Lake", {"lookup": dict(answer("lookup", 1), dataset_id=item["dataset_id"]),
                                               "shuffled": dict(answer("shuffled", 2), dataset_id=item["dataset_id"])},
                          {1: "H", 2: "H"}, cgn(tmp_path, [SUSAN_CGN]), NO_HAND_LIST)
    points = pd.DataFrame([{"record_id": "susan-lake-at-recreation-site", "page_latitude": 51.56768,
                            "page_longitude": -117.38848, "usable_as_check": "yes"},
                           {"record_id": "other", "page_latitude": 50.0, "page_longitude": -115.0,
                            "usable_as_check": "no"}])
    rows = checks.check_point_rows([row], {item["dataset_id"]: item}, points)
    assert [(r["geonameid"], r["chosen_in"], r["drawn"]) for r in rows] == [(1, "lookup", False),
                                                                             (2, "shuffled", False)]
    assert rows[1]["km_from_page_point"] == 0.7 and rows[0]["km_from_page_point"] > 200


def test_lakes_at_one_million_are_listed_with_the_hand_list_marked(tmp_path):
    def lake(geonameid, name, lat, lon):
        return {"geonameid": geonameid, "name": name, "matched_name": name, "feature": "lake", "feature_code": "LK",
                "admin1": "British Columbia", "country": "Canada", "country_code": "CA", "latitude": lat,
                "longitude": lon, "population": 0, "kind": "in_bc", "large": False}
    item = {"dataset_id": "a", "names": [{"name": "Lakes", "candidates": [
        lake(1, "Mara Lake", 50.78, -119.0), lake(6048449, "Lake Koocanusa", 49.2181, -115.2458),
        lake(3, "Susan Lake", 51.5703, -117.3978)]}]}
    table = cgn(tmp_path, [cgn_row("Mara Lake", "LAKE", 50.78, -119.0, 1000000),
                           cgn_row("Lake Koocanusa", "LAKE", 49.2181, -115.2458, 1000000), SUSAN_CGN])
    names = [{"outcome": "same_place", "geonameid": 1}]
    rows = checks.lake_rows({"a": item}, names, table, checks.drawing.read_never_drawn())
    assert [(r["lake"], r["rule"], r["records_listing_it"], r["records_choosing_it_in_both_orders"]) for r in rows] == [
        ("Lake Koocanusa", "lake_hand_list", 1, 0), ("Mara Lake", "lake", 1, 1)]


def test_the_whole_script_on_a_made_up_run(tmp_path, monkeypatch):
    """main reads the input and run folders, adds feature classes from a GeoNames table, and writes every file."""
    folder = tmp_path / "in"
    folder.mkdir()
    with gzip.open(folder / "items.jsonl.gz", "wt", encoding="utf-8") as handle:
        handle.write(json.dumps({"number": 0, "dataset_id": ITEM["dataset_id"], "item": ITEM, "units": []}) + "\n")
    run = tmp_path / "run"
    run.mkdir()
    (run / "answers.jsonl").write_text("".join(json.dumps(a) + "\n" for a in (
        answer("lookup", 2), answer("shuffled", 2, quote="Susan Lake ... CSISS"))), encoding="utf-8")
    geonames = tmp_path / "geonames.parquet"
    pd.DataFrame({"geonameid": [2, 2], "feature_class": ["H", "H"]}).to_parquet(geonames)
    table = tmp_path / "cgn.csv"
    table.write_text("﻿" + "\n".join([CGN_HEADER, SUSAN_CGN]) + "\n", encoding="utf-8")
    drafts = tmp_path / "drafts.csv"
    drafts.write_text("dataset_id,name,draft_reading,draft_quote,draft_note\n"
                      f"{ITEM['dataset_id']},Susan Lake,right,Susan Lake at Recreation Site,\n", encoding="utf-8")
    out = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", ["place_pick_checks.py", "--input", str(folder), "--run", str(run), "--out",
                                      str(out), "--geonames", str(geonames), "--cgn", str(table), "--drafts",
                                      str(drafts)])
    assert checks.main() == 0
    names = pd.read_csv(out / "names.csv")
    assert names[["name", "outcome", "drawn"]].values.tolist() == [["Susan Lake", "same_place", True]]
    hand = pd.read_csv(out / "hand_check.csv", keep_default_na=False)
    assert hand.loc[0, "draft_reading"] == "right" and "lucia_reading" in hand.columns
    report = (out / "report.txt").read_text()
    assert "joined with \"...\": 1; every piece in the record, in order: 1; not: 0" in report
    assert (out / "check_points.csv").exists() and (out / "lakes_at_one_million.csv").exists()
    with pytest.raises(SystemExit, match="already exists"):
        checks.main()
    # With --sample drawn, only names that would be drawn go in the hand check.
    (run / "answers.jsonl").write_text("".join(json.dumps(a) + "\n" for a in (
        answer("lookup", 2), answer("shuffled", 1))), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["place_pick_checks.py", "--input", str(folder), "--run", str(run), "--out",
                                      str(tmp_path / "out2"), "--geonames", str(geonames), "--cgn", str(table),
                                      "--sample", "drawn"])
    assert checks.main() == 0
    assert len(pd.read_csv(tmp_path / "out2" / "hand_check.csv")) == 0


def test_the_report_lists_every_park_the_park_rule_removes():
    """A park both orders chose is listed by name in the report, with the record it came from."""
    row = {"dataset_id": "a", "title": "Black Oystercatcher - Gwaii Haanas", "name": "Gwaii Haanas",
           "outcome": "same_place", "drawn": False, "why": "not_drawn: park_or_area", "drawing_rule": "park_or_area",
           "scale": None, "place": "Gwaii Haanas National Park Reserve", "feature": "park",
           "admin1": "British Columbia", "country": "Canada"}
    for order in ("lookup", "shuffled"):
        row.update({f"{order}_quote_ok": True, f"{order}_quote_pieces": 1, f"{order}_quote_problem": "",
                    f"{order}_quote": "Gwaii Haanas", f"{order}_reason_names_catalogue": False})
    lines = checks.report_lines([row], [], [], "run")
    assert "Removed by the park rule (GeoNames class L: parks, reserves and other areas, never drawn): 1" in lines
    assert ('  Gwaii Haanas National Park Reserve (park, British Columbia), from "Gwaii Haanas" in Black '
            'Oystercatcher - Gwaii Haanas') in lines


def test_a_failed_prompt_leaves_its_names_undrawn_and_is_listed(tmp_path, monkeypatch):
    """A prompt in failed.csv is not asked again: its names are not drawn, and the report lists it with its names."""
    folder = tmp_path / "in"
    folder.mkdir()
    units = [{"unit": f"0000_{order}_0", "order": order, "chunk": 0, "indexes": [0]} for order in ("lookup", "shuffled")]
    with gzip.open(folder / "items.jsonl.gz", "wt", encoding="utf-8") as handle:
        handle.write(json.dumps({"number": 0, "dataset_id": ITEM["dataset_id"], "item": ITEM, "units": units}) + "\n")
    run = tmp_path / "run"
    run.mkdir()
    (run / "answers.jsonl").write_text(json.dumps(answer("lookup", 2)) + "\n", encoding="utf-8")
    (run / "failed.csv").write_text("unit,dataset_id,error\n0000_shuffled_0," + ITEM["dataset_id"]
                                    + ",\"answer does not fit: P1: the quote is 504 characters, over 400\"\n",
                                    encoding="utf-8")
    geonames = tmp_path / "geonames.parquet"
    pd.DataFrame({"geonameid": [2], "feature_class": ["H"]}).to_parquet(geonames)
    table = tmp_path / "cgn.csv"
    table.write_text("﻿" + "\n".join([CGN_HEADER, SUSAN_CGN]) + "\n", encoding="utf-8")
    out = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", ["place_pick_checks.py", "--input", str(folder), "--run", str(run), "--out",
                                      str(out), "--geonames", str(geonames), "--cgn", str(table)])
    assert checks.main() == 0
    names = pd.read_csv(out / "names.csv")
    assert names[["outcome", "why", "drawn"]].values.tolist() == [["prompt_failed", "prompt_failed: shuffled", False]]
    report = (out / "report.txt").read_text()
    assert "Prompts that failed on Fir (not asked again; their names are not drawn): 1" in report
    assert ("  0000_shuffled_0 oai:canwin:susan-lake: answer does not fit: P1: the quote is 504 characters, over 400; "
            "names: Susan Lake") in report
