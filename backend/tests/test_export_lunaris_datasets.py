"""Tests for pipeline/lunaris/export_lunaris_datasets.py, on made-up
records: which copy is shown (best front end group, then the latest Issued
date, then the datestamp, then the lowest id), which groups are exported,
links, licences, box flags (BC's rectangle and the Canada-wide extent
among them), the location type, the pass 1 places with our lookup, the
organisms of both runs, and the rows built for a copy group."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "pipeline" / "lunaris"))
import export_lunaris_datasets as ex  # noqa: E402

NAMES = {"catalogue.data.gov.bc.ca": "BC Data Catalogue", "open.canada.ca": "Open Government Portal",
         "datadryad.org": "Dryad"}
# Stands in for BC's outline: is_bc_rectangle only reads its rectangle
# (west, south, east, north), here the one of bc_boundary_gbif.wkt.
BC = SimpleNamespace(rectangle=(-139.12771, 48.20636, -114.02091, 60.02025))


def union_rows(rows):
    """Return made-up union rows (record_id, source, frontend_group, final_class, dup_group_id, dup_show) with group_key."""
    table = pd.DataFrame(rows, columns=["record_id", "source", "frontend_group", "final_class", "dup_group_id",
                                        "dup_show"])
    table["group_key"] = table["dup_group_id"].where(table["dup_group_id"] != "", table["record_id"])
    return table


def facts_for(rows):
    """Return made-up DataCite facts by id from (id, issued, datestamp, identifier, identifier_type) tuples, with no licences."""
    facts = pd.DataFrame(rows, columns=["id", "issued", "datestamp", "identifier", "identifier_type"]).set_index("id")
    facts["licences"] = [[] for _ in range(len(facts))]
    return facts


# --- Which copy is shown, and which groups are exported ---------------------

def test_the_latest_issued_copy_in_the_best_group_is_shown():
    """A newer copy in a worse front end group is not shown; within the best group the newest is."""
    members = union_rows([("a", "x", "bc_data", "bc_only", "G1", "True"),
                          ("b", "x", "bc_data", "bc_only", "G1", "False"),
                          ("c", "x", "dropped", "elsewhere", "G1", "False")])
    facts = facts_for([("a", "2018-01-01", "2025-01-01T00:00:00Z", "", ""),
                       ("b", "2021-06-01", "2025-01-01T00:00:00Z", "", ""),
                       ("c", "2026-01-01", "2026-01-01T00:00:00Z", "", "")])
    assert ex.pick_shown(members, facts) == "b"


def test_ties_go_to_the_datestamp_then_the_lowest_id():
    """Same Issued date: the later datestamp; same datestamp too: the lowest id."""
    members = union_rows([("b", "x", "pending", "global", "G1", ""), ("a", "x", "pending", "global", "G1", ""),
                          ("c", "x", "pending", "global", "G1", "")])
    facts = facts_for([("a", "2020-01-01", "2025-01-01T00:00:00Z", "", ""),
                       ("b", "2020-01-01", "2025-01-01T00:00:00Z", "", ""),
                       ("c", "2020-01-01", "2024-01-01T00:00:00Z", "", "")])
    assert ex.pick_shown(members, facts) == "a"
    facts.loc["c", "datestamp"] = "2026-01-01T00:00:00Z"
    assert ex.pick_shown(members, facts) == "c"


def test_only_groups_whose_best_copy_is_bc_data_or_pending_are_exported():
    """A group with one bc_data copy is exported whole; a dropped-only group is not."""
    table = union_rows([("a", "x", "bc_data", "bc_only", "G1", "True"), ("b", "x", "dropped", "elsewhere", "G1", ""),
                        ("c", "x", "dropped", "unknown", "", "True"), ("d", "x", "pending", "global", "", "True")])
    assert ex.exported_groups(table)["record_id"].tolist() == ["a", "b", "d"]


# --- One record ------------------------------------------------------------

def test_links():
    """A DOI goes through doi.org; a URL is kept; anything else gives no link."""
    assert ex.link_of("10.5061/dryad.x", "DOI") == ("https://doi.org/10.5061/dryad.x", "doi")
    assert ex.link_of("https://open.canada.ca/data/en/dataset/x", "URL") == (
        "https://open.canada.ca/data/en/dataset/x", "url")
    assert ex.link_of("local-id", "URL") == ("", "")


@pytest.mark.parametrize("box, flags", [
    ({"w": -124.0, "e": -123.0, "s": 49.0, "n": 50.0}, set()),
    ({"w": -124.0, "e": -123.0, "s": 50.0, "n": 49.0}, {"swapped"}),
    ({"w": -123.0, "e": -124.0, "s": 49.0, "n": 50.0}, {"swapped"}),
    ({"w": 170.0, "e": -170.0, "s": 50.0, "n": 60.0}, set()),
    ({"w": -123.0, "e": -123.0, "s": 49.0, "n": 49.0}, {"zero_width"}),
    ({"w": -180.0, "e": 180.0, "s": -90.0, "n": 90.0}, {"most_of_world"}),
    ({"w": -180.0, "e": 180.0, "s": 0.0, "n": 80.0}, set()),
    ({"w": -139.5, "e": -113.5, "s": 48.0, "n": 60.0}, {"bc_rectangle"}),
    ({"w": -139.06, "e": -114.03, "s": 60.0, "n": 48.3}, {"bc_rectangle", "swapped"}),
    ({"w": -140.5, "e": -113.5, "s": 48.0, "n": 60.0}, set()),
    ({"w": -141.003, "e": -52.6174, "s": 41.6755, "n": 83.1139}, {"canada_default"}),
    ({"w": -141.02, "e": -52.62, "s": 41.68, "n": 83.14}, {"canada_default"}),
    ({"w": -140.0, "e": -50.0, "s": 42.0, "n": 82.0}, set()),
])
def test_box_flags(box, flags):
    """Swapped corners, zero width, most of the world, BC's rectangle (within 1 degree, as in
    step 1) and the Canada-wide extent (within 0.5 degrees); a box over the 180 degree line is not swapped."""
    assert ex.box_flags(box, BC) == flags


def test_a_box_over_the_180_degree_line_is_never_a_standard_box():
    """Like step 1's BC rectangle, the Canada-wide extent never crosses the 180 degree line."""
    assert not ex.is_canada_default({"w": 141.0, "e": -52.6, "s": 41.7, "n": 83.1})


def test_the_location_type_is_the_first_that_applies():
    """A box, then a point, then a looked-up place with coordinates, then none."""
    place = {"latitude": 49.0, "large_area": False, "role": "study_location"}
    no_coordinates = {"latitude": None, "large_area": False, "role": "study_location"}
    assert ex.location_type([{"w": 1}], [{"lat": 1}], [place]) == ("box", "harvested")
    assert ex.location_type([], [{"lat": 1}], [place]) == ("point", "harvested")
    assert ex.location_type([], [], [no_coordinates, place]) == ("looked_up_place", "our_geonames_lookup")
    assert ex.location_type([], [], [no_coordinates]) == ("none", "")


def candidate(name, lat, lon, feature_class="H", feature_code="LK"):
    """Return a made-up GeoNames candidate."""
    return {"geonameid": 1, "main_name": name, "feature_class": feature_class, "feature_code": feature_code,
            "country_code": "CA", "admin1_name": "British Columbia", "latitude": lat, "longitude": lon}


def lookup(query, candidates, kinds):
    """Return a made-up lookup line for a place name."""
    return {"query": {"name": query, "as_written": query}, "candidates": candidates,
            "summary": {"looked_up": query, "category": "both", "candidate_kinds": kinds}}


def test_a_place_prefers_the_best_ranked_candidate_in_bc_and_keeps_the_top_one():
    """A BC candidate lower down is used over a top candidate outside BC; the top one is kept for checking."""
    found = ex.place_from_lookup(
        {"name": "Tern Lake", "as_written": "Tern Lake", "role": "study_location"},
        lookup("Tern Lake", [candidate("No coordinates", None, None), candidate("Tern Lake", 50.0, -95.0),
                             candidate("Tern Lake BC", 52.0, -125.0), candidate("Tern Lake BC 2", 53.0, -126.0)],
               ["region", "outside_bc", "in_bc", "in_bc"]))
    assert (found["geonames_name"], found["latitude"], found["kind"], found["choice"]) == (
        "Tern Lake BC", 52.0, "in_bc", "best_in_bc")
    assert (found["top_candidate"]["geonames_name"], found["top_candidate"]["kind"]) == ("Tern Lake", "outside_bc")
    assert found["any_candidate_in_bc"] is True and found["large_area"] is False


def test_with_no_candidate_in_bc_the_top_candidate_is_used():
    """No BC candidate: the lookup's top candidate with coordinates, choice "top"; no candidate at all: empty."""
    found = ex.place_from_lookup({"name": "Goose Island"}, lookup(
        "Goose Island", [candidate("Goose Island", 47.0, -53.0, "T", "ISL"), candidate("Goose Island", 60.0, -64.0)],
        ["outside_bc", "outside_bc"]))
    assert (found["latitude"], found["kind"], found["choice"], found["any_candidate_in_bc"]) == (
        47.0, "outside_bc", "top", False)
    empty = ex.place_from_lookup({"name": "Arctic"}, lookup("Arctic", [], []))
    assert (empty["latitude"], empty["choice"], empty["large_area"]) == (None, "", False)
    assert empty["top_candidate"]["geonames_id"] is None


@pytest.mark.parametrize("feature_class, feature_code, large", [
    ("A", "PCLI", True), ("A", "PCLD", True), ("A", "ADM1", True), ("L", "CONT", True), ("L", "RGN", True),
    ("H", "OCN", True), ("H", "SEA", True), ("T", "MTS", True),
    ("A", "ADM2", False), ("P", "PPL", False), ("T", "ISL", False), ("H", "LK", False), ("H", "BAY", False),
    ("H", "PCLI", False), (None, None, False),
])
def test_large_areas(feature_class, feature_code, large):
    """Countries, provinces and states, continents, regions, oceans, seas and ranges are large; the class must match too."""
    assert ex.is_large_area(feature_class, feature_code) is large


def test_only_places_that_can_be_drawn_make_a_looked_up_location():
    """A large area, an author's institution or a place with no coordinates alone gives location none."""
    small = {"latitude": 49.0, "large_area": False, "role": "study_location"}
    assert ex.location_type([], [], [{**small, "large_area": True}, {**small, "role": "affiliation"},
                                     {**small, "latitude": None}]) == ("none", "")
    assert ex.location_type([], [], [{**small, "large_area": True}, small]) == ("looked_up_place", "our_geonames_lookup")


def write_run(folder, places, lookups):
    """Write a made-up pass 1 run: answers.jsonl (record 0) and lookup.jsonl."""
    (folder / "pass1").mkdir(parents=True)
    answer = {"number": 0, "record_id": "oai:x:1", "places": places}
    (folder / "pass1" / "answers.jsonl").write_text(json.dumps(answer) + "\n")
    (folder / "pass1" / "lookup.jsonl").write_text("".join(json.dumps({**l, "record": 0}) + "\n" for l in lookups))
    return str(folder)


def test_pass1_places_are_read_in_order_and_must_line_up(tmp_path):
    """Each lookup line belongs to the place at the same position; a mismatch stops."""
    places = [{"name": "Tern Lake", "as_written": "Tern Lake", "role": "study_location"}]
    run = write_run(tmp_path / "good", places, [lookup("Tern Lake", [candidate("Tern Lake", 50.0, -95.0)],
                                                       ["outside_bc"])])
    assert ex.read_pass1_places([run])["oai:x:1"][0]["geonames_name"] == "Tern Lake"
    bad = write_run(tmp_path / "bad", places, [lookup("Other Lake", [], [])])
    with pytest.raises(SystemExit, match="do not line up"):
        ex.read_pass1_places([bad])


def test_organisms_of_both_runs():
    """Names split on semicolons; both runs' names once each; the level takes the most specific answer."""
    assert ex.organism_list("bald eagle; Haliaeetus leucocephalus; ") == ["bald eagle", "Haliaeetus leucocephalus"]
    assert ex.merged_names(["Bald eagle", "kelp"], ["bald eagle", "Nereocystis"]) == ["Bald eagle", "kelp",
                                                                                     "Nereocystis"]
    assert ex.combined_level("group", "species") == "species"
    assert ex.combined_level("none", "group") == "group"
    assert ex.combined_level("", "none") == "none"
    assert ex.combined_level("", "") == ""


def test_datacite_facts_read_the_issued_date_and_identifier():
    """The Issued date and the identifier with its type, from the raw OAI XML."""
    xml = ('<record xmlns="http://www.openarchives.org/OAI/2.0/"><metadata>'
           '<resource xmlns="http://datacite.org/schema/kernel-4">'
           '<identifier identifierType="DOI">10.5061/dryad.x</identifier>'
           '<dates><date dateType="Issued">2024-09-16</date></dates></resource></metadata></record>')
    assert ex.datacite_facts(xml) == ("2024-09-16", "10.5061/dryad.x", "DOI", [])
    assert ex.datacite_facts('<record xmlns="http://www.openarchives.org/OAI/2.0/"/>') == ("", "", "", [])


def test_datacite_facts_keep_every_rights_entry_as_harvested():
    """Each rightsList entry in order, its name and URI untouched (spaces kept), None when missing or empty."""
    xml = ('<record xmlns="http://www.openarchives.org/OAI/2.0/"><metadata>'
           '<resource xmlns="http://datacite.org/schema/kernel-4"><rightsList>'
           '<rights rightsURI="https://creativecommons.org/licenses/by/4.0/"> CC BY 4.0 </rights>'
           '<rights rightsURI="info:eu-repo/semantics/openAccess"/>'
           '<rights>CC0 1.0</rights><rights rightsURI=""></rights>'
           '</rightsList></resource></metadata></record>')
    assert ex.datacite_facts(xml)[3] == [
        {"name": " CC BY 4.0 ", "uri": "https://creativecommons.org/licenses/by/4.0/"},
        {"name": None, "uri": "info:eu-repo/semantics/openAccess"},
        {"name": "CC0 1.0", "uri": None},
        {"name": None, "uri": None},
    ]


def test_access_rights_are_not_counted_as_licences():
    """An entry with no name and an info:eu-repo URI is an access right; the printed label is short."""
    assert ex.is_access_right({"name": None, "uri": "info:eu-repo/semantics/restrictedAccess"})
    assert not ex.is_access_right({"name": "CC0 1.0", "uri": "info:eu-repo/semantics/openAccess"})
    assert not ex.is_access_right({"name": None, "uri": "https://spdx.org/licenses/CC0-1.0.html"})
    assert ex.licence_label({"name": None, "uri": "https://spdx.org/licenses/CC0-1.0.html"}) == (
        "https://spdx.org/licenses/CC0-1.0.html")
    assert len(ex.licence_label({"name": "<h2>Long</h2> " * 20, "uri": None})) == 60


# --- The rows --------------------------------------------------------------

def made_up_export():
    """Return the inputs of build_rows for one copy group of two and one record with no copy."""
    members = union_rows([("oai:catalogue.data.gov.bc.ca:a", "catalogue.data.gov.bc.ca", "bc_data", "bc_only", "G1", "True"),
                          ("oai:open.canada.ca:a", "open.canada.ca", "bc_data", "bc_only", "G1", "False"),
                          ("oai:datadryad.org:doi:10.1/d", "datadryad.org", "pending", "global", "", "True")])
    facts = facts_for([("oai:catalogue.data.gov.bc.ca:a", "2019-01-01", "2025-01-01T00:00:00Z",
                        "https://catalogue.data.gov.bc.ca/dataset/a", "URL"),
                       ("oai:open.canada.ca:a", "2023-01-01", "2025-01-01T00:00:00Z",
                        "https://open.canada.ca/data/en/dataset/a", "URL"),
                       ("oai:datadryad.org:doi:10.1/d", "2020-01-01", "2025-01-01T00:00:00Z", "10.1/d", "DOI")])
    harvest = pd.DataFrame([
        {"id": "oai:catalogue.data.gov.bc.ca:a", "title": "A", "abstract": "Old copy", "subjects": [],
         "publisher": "BC", "places": [], "points": [], "boxes": [{"w": -124.0, "e": -123.0, "s": 50.0, "n": 49.0}]},
        {"id": "oai:open.canada.ca:a", "title": "A", "abstract": "New copy", "subjects": ["Fish"],
         "publisher": "Canada", "places": ["Fraser River"], "points": [{"lat": 49.2, "lon": -122.9}], "boxes": []},
        {"id": "oai:datadryad.org:doi:10.1/d", "title": "D", "abstract": float("nan"), "subjects": None,
         "publisher": "Dryad", "places": None, "points": None, "boxes": None},
    ]).set_index("id")
    answers_4b = pd.DataFrame({"organisms": ["salmon", ""], "organism_level": ["group", ""]},
                              index=["oai:open.canada.ca:a", "oai:datadryad.org:doi:10.1/d"])
    answers_35b = pd.DataFrame({"organisms": ["Oncorhynchus nerka; salmon"], "organism_level": ["species"]},
                               index=["oai:open.canada.ca:a"])
    return members, facts, harvest, answers_4b, answers_35b


def test_build_rows_shows_the_newest_copy_with_the_other_listed():
    """The newer open.canada.ca copy is shown, its point used, the BC copy listed with its link."""
    members, facts, harvest, a4, a35 = made_up_export()
    rows = {row["id"]: row for row in ex.build_rows(members, facts, harvest, {}, a4, a35, NAMES, BC)}
    shown = rows["oai:open.canada.ca:a"]
    assert (shown["source_name"], shown["link_type"], shown["location_type"], shown["copy_count"]) == (
        "Open Government Portal", "url", "point", 2)
    assert shown["other_copies"] == [{"id": "oai:catalogue.data.gov.bc.ca:a", "source": "catalogue.data.gov.bc.ca",
                                      "source_name": "BC Data Catalogue",
                                      "link": "https://catalogue.data.gov.bc.ca/dataset/a"}]
    assert shown["box_swapped"] is False and shown["places"] == ["Fraser River"]
    assert (shown["organisms"], shown["organism_level"]) == (["salmon", "Oncorhynchus nerka"], "species")
    assert ex.changed_from_dup_show(members, list(rows.values())) == 1


def test_build_rows_handles_missing_fields_and_answers():
    """Missing text, lists and filter 2 answers give None, empty lists and empty levels."""
    members, facts, harvest, a4, a35 = made_up_export()
    row = {r["id"]: r for r in ex.build_rows(members, facts, harvest, {}, a4, a35, NAMES, BC)}[
        "oai:datadryad.org:doi:10.1/d"]
    assert (row["abstract"], row["subjects"], row["location_type"]) == (None, [], "none")
    assert (row["link"], row["organism_level"], row["organisms_35b"]) == ("https://doi.org/10.1/d", "", [])
    assert row["other_copies"] == [] and row["copy_count"] == 1


def test_build_rows_carries_the_licences_and_standard_box_flags():
    """The shown copy's own licences and box flags; the box is kept as harvested."""
    members, facts, harvest, a4, a35 = made_up_export()
    licences = [{"name": "CC0 1.0", "uri": None}, {"name": None, "uri": "info:eu-repo/semantics/openAccess"}]
    facts.at["oai:datadryad.org:doi:10.1/d", "licences"] = licences
    canada = {"w": -141.003, "e": -52.6174, "s": 41.6755, "n": 83.1139}
    harvest.at["oai:datadryad.org:doi:10.1/d", "boxes"] = [canada]
    row = {r["id"]: r for r in ex.build_rows(members, facts, harvest, {}, a4, a35, NAMES, BC)}[
        "oai:datadryad.org:doi:10.1/d"]
    assert row["licences"] == licences and row["boxes"] == [canada]
    assert (row["box_canada_default"], row["box_bc_rectangle"], row["location_type"]) == (True, False, "box")


def test_only_standard_boxes():
    """True only when every box is BC's rectangle or the Canada-wide extent and there is no point."""
    bc = {"w": -139.5, "e": -113.5, "s": 48.0, "n": 60.0}
    canada = {"w": -141.0, "e": -52.6, "s": 41.7, "n": 83.1}
    small = {"w": -124.0, "e": -123.0, "s": 49.0, "n": 50.0}
    assert ex.only_standard_boxes({"boxes": [bc, canada], "points": [], "shape": BC})
    assert not ex.only_standard_boxes({"boxes": [bc, small], "points": [], "shape": BC})
    assert not ex.only_standard_boxes({"boxes": [bc], "points": [{"lat": 49.0, "lon": -123.0}], "shape": BC})
    assert not ex.only_standard_boxes({"boxes": [], "points": [], "shape": BC})


def test_a_catalogue_with_no_readable_name_stops():
    """Every catalogue must have a name in lunaris_catalogue_names.csv."""
    members, facts, harvest, a4, a35 = made_up_export()
    with pytest.raises(SystemExit, match="no readable name.*datadryad.org"):
        ex.build_rows(members, facts, harvest, {}, a4, a35, {k: v for k, v in NAMES.items() if k != "datadryad.org"},
                      BC)


def test_the_rows_fit_the_parquet_schema():
    """build_rows' rows can be written with SCHEMA, in its column order, looked-up places included."""
    members, facts, harvest, a4, a35 = made_up_export()
    place = ex.place_from_lookup({"name": "Canada", "as_written": "Canada", "role": "study_location"}, lookup(
        "Canada", [candidate("Canada", 60.0, -96.0, "A", "PCLI")], ["contains_bc"]))
    rows = ex.build_rows(members, facts, harvest, {"oai:datadryad.org:doi:10.1/d": [place]}, a4, a35, NAMES, BC)
    table = ex.pa.Table.from_pylist(rows, schema=ex.SCHEMA)
    assert table.num_rows == 2
    written = {r["id"]: r for r in table.to_pylist()}["oai:datadryad.org:doi:10.1/d"]["looked_up_places"][0]
    assert (written["large_area"], written["top_candidate"]["feature_code"]) == (True, "PCLI")
    # A dataset whose only looked-up place is a large area has nothing to draw.
    assert {r["id"]: r["location_type"] for r in rows}["oai:datadryad.org:doi:10.1/d"] == "none"


def test_the_names_file_has_every_name_once():
    """lunaris_catalogue_names.csv names each source once, with a non-empty name."""
    names = ex.read_catalogue_names()
    assert all(names.values()) and names["catalogue.data.gov.bc.ca"] == "BC Data Catalogue"
