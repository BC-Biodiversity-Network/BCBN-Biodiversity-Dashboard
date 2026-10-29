"""Tests for the Lunaris dataset endpoints (api/datasets.py): the list's
keyword search, sort, filters, paging and option counts, the detail by id
(including another copy's id and an id with a slash), the errors, and the
start-up checks on the dataset file. The test datasets are described at
the top of conftest.py."""

import pytest

from fastapi.testclient import TestClient

from api.datasets import PREVIEW_LENGTH, preview
from api.db import DataFilesError, open_data
from api.main import create_app
from api.settings import Settings
from conftest import DATASETS_SQL, write_test_data

EAGLE_NESTS = "oai:catalogue.data.gov.bc.ca:eagle-nests"
SALMON = "oai:catalogue.hakai.org:salmon"
FOREST = "oai:catalogue.data.gov.bc.ca:forest-plots"
BEAGLE = "oai:canwin-datahub.ad.umanitoba.ca:beagle"
GOLDEN = "oai:datadryad.org:doi:10.5061/dryad.golden"
COPY = "oai:open.canada.ca:eagle-copy"


def ids(response):
    """The ids of a list response's results, in order."""
    return [result["id"] for result in response.json()["results"]]


def ids_of(body):
    """The ids of a list response body's results, in order."""
    return [result["id"] for result in body["results"]]


def counts(response, name):
    """One filter's option counts from a list response, as {value: count}."""
    return {option["value"]: option["count"] for option in response.json()["options"][name]}


# --- The list --------------------------------------------------------------

def test_the_default_list_is_bc_data_newest_first(client):
    """With no query or filter, only bc_data datasets, the most recently issued first."""
    response = client.get("/api/datasets")
    assert response.status_code == 200
    assert ids(response) == [SALMON, EAGLE_NESTS, FOREST]
    body = response.json()
    assert (body["total"], body["pages"], body["page"], body["filters"]["frontend_group"]) == (3, 1, 1, ["bc_data"])


def test_a_keyword_searches_title_abstract_and_subjects(client):
    """eagle is in a title, an abstract ("eagles") and a subject; the title match comes first."""
    assert ids(client.get("/api/datasets", params={"q": "eagle"})) == [EAGLE_NESTS, SALMON, FOREST]


def test_a_keyword_must_start_a_word(client):
    """eagle does not find Beagle, even among pending datasets."""
    response = client.get("/api/datasets", params={"q": "eagle", "frontend_group": ["bc_data", "pending"]})
    assert BEAGLE not in ids(response) and GOLDEN in ids(response)


def test_every_word_must_match_and_case_and_accents_do_not_matter(client):
    """Two words must both be found; letter case and accents are ignored."""
    assert ids(client.get("/api/datasets", params={"q": "BALD Éagle"})) == [EAGLE_NESTS]
    assert ids(client.get("/api/datasets", params={"q": "eagle trees"})) == []


def test_filters_take_several_values(client):
    """A dataset matching any value of a filter passes; the filters combine."""
    response = client.get("/api/datasets", params={"final_class": ["bc_only", "canada_incl_bc"]})
    assert ids(response) == [SALMON, EAGLE_NESTS]
    response = client.get("/api/datasets", params={"catalogue": "catalogue.data.gov.bc.ca",
                                                   "final_class": "bc_and_outside_canada"})
    assert ids(response) == [FOREST]
    response = client.get("/api/datasets", params={"frontend_group": "pending"})
    assert ids(response) == [GOLDEN, BEAGLE]


def test_option_counts_leave_out_their_own_filter(client):
    """Ticking one catalogue still counts the others; class counts follow the catalogue."""
    response = client.get("/api/datasets", params={"catalogue": "catalogue.hakai.org"})
    assert counts(response, "catalogue") == {"catalogue.data.gov.bc.ca": 2, "catalogue.hakai.org": 1}
    assert counts(response, "final_class") == {"bc_only": 0, "canada_incl_bc": 1, "bc_and_outside_canada": 0,
                                               "all_canada": 0, "global": 0}
    assert counts(response, "frontend_group") == {"bc_data": 1, "pending": 0}
    names = {o["value"]: o["name"] for o in response.json()["options"]["catalogue"]}
    assert names["catalogue.data.gov.bc.ca"] == "BC Data Catalogue"


def test_option_counts_follow_the_query(client):
    """With a query, the counts are of the datasets the query finds."""
    response = client.get("/api/datasets", params={"q": "eagle"})
    assert counts(response, "frontend_group") == {"bc_data": 3, "pending": 1}
    assert list(counts(response, "final_class")) == ["bc_only", "canada_incl_bc", "bc_and_outside_canada",
                                                     "all_canada", "global"]


def test_a_selected_catalogue_with_no_match_is_still_listed(client):
    """A selected catalogue stays in the options with count 0 and its name."""
    response = client.get("/api/datasets", params={"catalogue": "datadryad.org"})
    assert response.json()["total"] == 0
    option = [o for o in response.json()["options"]["catalogue"] if o["value"] == "datadryad.org"]
    assert option == [{"value": "datadryad.org", "count": 0, "name": "Dryad"}]


def test_paging(client):
    """Pages hold page_size results; a page past the end is empty but keeps the total."""
    response = client.get("/api/datasets", params={"page_size": 2, "page": 2})
    assert ids(response) == [FOREST]
    assert (response.json()["total"], response.json()["pages"]) == (3, 2)
    response = client.get("/api/datasets", params={"page_size": 2, "page": 5})
    assert ids(response) == [] and response.json()["total"] == 3


def test_a_result_has_the_list_fields_and_a_preview(client):
    """Results carry what a list needs, with the abstract as a preview."""
    result = client.get("/api/datasets", params={"q": "bald"}).json()["results"][0]
    assert result["abstract_preview"] == "Nest locations of Haliaeetus leucocephalus on the coast."
    assert "abstract" not in result and "boxes" not in result
    assert (result["source_name"], result["link_type"], result["location_type"], result["copy_count"]) == (
        "BC Data Catalogue", "url", "box", 2)
    assert [licence["name"] for licence in result["licences"]] == ["Open Government Licence - British Columbia", None]
    assert "box_matches_bc_rectangle" not in result


def test_preview_cuts_at_a_space():
    """A long abstract is cut at a space before PREVIEW_LENGTH, with ..."""
    text = "word " * 100
    cut = preview(text)
    assert cut.endswith("...") and len(cut) <= PREVIEW_LENGTH + 3 and not cut[:-3].endswith(" ")
    assert preview("short") == "short" and preview(None) is None


@pytest.mark.parametrize("params, message", [
    ({"q": "e"}, "at least 2 letters"),
    ({"final_class": "elsewhere"}, "Unknown final_class 'elsewhere'"),
    ({"frontend_group": "dropped"}, "Unknown frontend_group 'dropped'"),
    ({"catalogue": "nowhere.example"}, "use a value from options.catalogue"),
])
def test_bad_requests_give_400(client, params, message):
    """A too-short query or an unknown filter value gives 400 with a clear message."""
    response = client.get("/api/datasets", params=params)
    assert response.status_code == 400 and message in response.json()["detail"]


def test_a_page_size_over_the_limit_is_refused(client):
    """page_size is at most MAX_PAGE_SIZE, page at least 1."""
    assert client.get("/api/datasets", params={"page_size": 101}).status_code == 422
    assert client.get("/api/datasets", params={"page": 0}).status_code == 422


def test_a_blank_query_lists_everything(client):
    """q given but empty is the same as no query."""
    assert ids(client.get("/api/datasets", params={"q": " "})) == [SALMON, EAGLE_NESTS, FOREST]


# --- Sort --------------------------------------------------------------------

BOTH = {"frontend_group": ["bc_data", "pending"]}


def test_the_default_sort_is_newest_without_a_search_and_relevance_with_one(client):
    """Without q the newest first (no issued date last); with q the title matches first."""
    body = client.get("/api/datasets").json()
    assert (body["sort"], ids_of(body)) == ("newest", [SALMON, EAGLE_NESTS, FOREST])
    body = client.get("/api/datasets", params={"q": "eagle"}).json()
    assert (body["sort"], ids_of(body)) == ("relevance", [EAGLE_NESTS, SALMON, FOREST])


@pytest.mark.parametrize("sort, expected", [
    ("newest", [SALMON, GOLDEN, EAGLE_NESTS, BEAGLE, FOREST]),
    ("oldest", [BEAGLE, EAGLE_NESTS, GOLDEN, SALMON, FOREST]),
    ("title", [EAGLE_NESTS, BEAGLE, FOREST, GOLDEN, SALMON]),
])
def test_each_sort(client, sort, expected):
    """Newest and oldest by the issued date, no date always last; title A to Z ignoring case."""
    assert ids(client.get("/api/datasets", params={**BOTH, "sort": sort})) == expected


def test_a_search_can_be_sorted_another_way(client):
    """With q, newest first instead of the title matches first."""
    assert ids(client.get("/api/datasets", params={"q": "eagle", "sort": "newest"})) == [SALMON, EAGLE_NESTS, FOREST]


def test_ties_are_broken_by_id(tmp_path):
    """Datasets with the same issued date and title come in id order, so paging is stable."""
    write_test_data(tmp_path, datasets_sql=f"SELECT * REPLACE ('2020-01-01' AS issued, 'Same' AS title) "
                                           f"FROM ({DATASETS_SQL})")
    with TestClient(create_app(Settings(data_dir=tmp_path))) as same:
        for sort in ["newest", "oldest", "title"]:
            assert ids(same.get("/api/datasets", params={**BOTH, "sort": sort})) == sorted(
                [SALMON, GOLDEN, EAGLE_NESTS, BEAGLE, FOREST])


# --- Filters ---------------------------------------------------------------

def test_published_years_leave_out_datasets_with_no_date_only_when_set(client):
    """year_from and year_to are inclusive, either end open; no issued date only counts with no range."""
    assert ids(client.get("/api/datasets", params={"year_from": 2020})) == [SALMON, EAGLE_NESTS]
    assert ids(client.get("/api/datasets", params={"year_to": 2020})) == [EAGLE_NESTS]
    assert ids(client.get("/api/datasets", params={"year_from": 2020, "year_to": 2020})) == [EAGLE_NESTS]
    published = client.get("/api/datasets", params={"year_from": 2021}).json()["options"]["published"]
    assert published == {"years": [{"year": 2020, "count": 1}, {"year": 2022, "count": 1}], "no_date": 1}


@pytest.mark.parametrize("organism, expected", [
    ("eagle", [GOLDEN, EAGLE_NESTS]),
    ("LEUCO", [EAGLE_NESTS]),
    ("Golden Eagle", [GOLDEN]),
    ("cephalus", []),
    ("eagle aquila", []),
])
def test_organism_names_match_the_start_of_words(client, organism, expected):
    """Case-insensitive, from the start of a word, the words following each other in one name."""
    assert ids(client.get("/api/datasets", params={**BOTH, "organism": organism})) == expected


def test_access_filter_and_counts(client):
    """Open or restricted from the record's access right; a dataset with none is in neither."""
    response = client.get("/api/datasets", params={"access": "restricted"})
    assert ids(response) == [SALMON]
    assert counts(response, "access") == {"open": 1, "restricted": 1}
    assert counts(client.get("/api/datasets", params=BOTH), "access") == {"open": 3, "restricted": 1}


def test_only_datasets_the_map_can_draw(client):
    """mappable keeps the datasets with something to draw; the count leaves out its own filter."""
    response = client.get("/api/datasets", params={"mappable": "true"})
    assert ids(response) == [SALMON, EAGLE_NESTS]
    assert response.json()["options"]["location"] == [{"value": "mappable", "count": 2}]
    assert client.get("/api/datasets", params=BOTH).json()["options"]["location"][0]["count"] == 3


def test_filters_combine_and_are_echoed(client):
    """Every filter together, and the response says which were used."""
    body = client.get("/api/datasets", params={**BOTH, "organism": "eagle", "access": "open", "mappable": "true",
                                                "year_from": 2015, "sort": "oldest"}).json()
    assert ids_of(body) == [EAGLE_NESTS]
    assert body["filters"] == {"catalogue": [], "final_class": [], "frontend_group": ["bc_data", "pending"],
                               "year_from": 2015, "year_to": None, "organism": "eagle", "access": ["open"],
                               "mappable": True}


@pytest.mark.parametrize("params, message", [
    ({"sort": "relevance"}, "sort=relevance needs a search"),
    ({"sort": "random"}, "Unknown sort 'random'"),
    ({"year_from": 2022, "year_to": 2020}, "year_from (2022) is after year_to (2020)"),
    ({"access": "closed"}, "Unknown access 'closed'"),
    ({"organism": "e"}, "organism search needs at least 2 letters"),
])
def test_bad_sort_and_filters_give_400(client, params, message):
    """A sort or filter that cannot be used gives 400 with a clear message."""
    response = client.get("/api/datasets", params=params)
    assert response.status_code == 400 and message in response.json()["detail"]


# --- The detail ------------------------------------------------------------

def test_the_detail_has_every_field(client):
    """Every column of the file, the location included, and requested_copy None."""
    body = client.get(f"/api/datasets/{EAGLE_NESTS}").json()
    assert body["boxes"] == [{"w": -139.5, "e": -113.5, "s": 48.0, "n": 60.0}]
    assert (body["box_matches_bc_rectangle"], body["box_matches_canada_extent"], body["box_shared_by"]) == (
        True, False, [191])
    assert body["licences"][1] == {"name": None, "uri": "info:eu-repo/semantics/openAccess"}
    assert body["organisms"] == ["bald eagle", "Haliaeetus leucocephalus"]
    assert body["other_copies"][0]["id"] == COPY and body["requested_copy"] is None
    assert {"abstract", "looked_up_places", "box_swapped", "organism_level_35b", "location_source"} <= set(body)


def test_an_id_with_a_slash_is_found(client):
    """Dryad ids hold a slash; the whole rest of the path is the id."""
    response = client.get(f"/api/datasets/{GOLDEN}")
    assert response.status_code == 200 and response.json()["id"] == GOLDEN


def test_a_looked_up_place_comes_with_its_coordinates(client):
    """A dataset placed by our lookup has the place's name and coordinates."""
    body = client.get(f"/api/datasets/{BEAGLE}").json()
    assert (body["location_type"], body["location_source"]) == ("looked_up_place", "our_geonames_lookup")
    assert body["looked_up_places"][0]["geonames_name"] == "Winnipeg"


def test_another_copy_gives_the_shown_dataset(client):
    """The id of another copy gives the copy shown, with requested_copy set."""
    body = client.get(f"/api/datasets/{COPY}").json()
    assert (body["id"], body["requested_copy"]) == (EAGLE_NESTS, COPY)


def test_an_unknown_id_gives_404(client):
    """An id that is neither a dataset nor another copy gives 404."""
    assert client.get("/api/datasets/oai:nowhere:1").status_code == 404


# --- Start-up checks -------------------------------------------------------

def refused(tmp_path, datasets_sql, match):
    """Write test data with this dataset file and check the API refuses it with match."""
    write_test_data(tmp_path, datasets_sql=datasets_sql)
    with pytest.raises(DataFilesError, match=match):
        open_data(Settings(data_dir=tmp_path))


def test_a_repeated_id_is_refused(tmp_path):
    """Two rows with one id would give two answers."""
    refused(tmp_path, f"{DATASETS_SQL} UNION ALL SELECT * FROM ({DATASETS_SQL}) WHERE id LIKE '%salmon'",
            "1 repeated id")


def test_a_copy_id_that_is_also_shown_is_refused(tmp_path):
    """A copy id that is also a dataset's id would give two answers."""
    sql = f"SELECT * REPLACE (CASE WHEN id = '{SALMON}' THEN '{COPY}' ELSE id END AS id) FROM ({DATASETS_SQL})"
    refused(tmp_path, sql, "copy ids are also shown as datasets: " + COPY)


def test_an_unknown_frontend_group_is_refused(tmp_path):
    """A dropped dataset in the file means the export went wrong."""
    sql = f"SELECT * REPLACE ('dropped' AS frontend_group) FROM ({DATASETS_SQL})"
    refused(tmp_path, sql, "frontend_group value.*dropped")


def test_a_missing_dataset_file_is_named(tmp_path):
    """Without the dataset file, the API says which file is missing."""
    write_test_data(tmp_path)
    (tmp_path / "lunaris_datasets.parquet").unlink()
    with pytest.raises(DataFilesError, match="lunaris_datasets.parquet is missing"):
        open_data(Settings(data_dir=tmp_path))
