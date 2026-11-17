"""Tests for api/dataset_licences.py: the licence column of the Datasets CSV
must say what the dataset's detail page says. The rule is checked on its
own, then the same entries are run through the page's own code
(shownLicences in frontend/src/datasetText.js, with node and jsdom for the
HTML) and the two must agree, on made-up entries and, where the real data
is in backend/data/api, on every dataset the site shows. The hand-decided
list (api/licence_hand_decisions.csv) is checked too: its format, how the
API uses it, and that the API refuses to start when it does not fit the
data."""

import json
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

import api.dataset_licences
from api.dataset_licences import (HAND_DECISION_COLUMNS, hand_decision_problems, licence_text, licence_texts,
                                  plain_text, read_hand_decisions, shown_licences)
from api.db import DataFilesError
from api.main import create_app
from api.settings import Settings
from conftest import write_licence_decisions
from frontend_node import FRONTEND, NODE_READY, front_end_module, run_node

# jsdom gives node the DOMParser the page reads HTML with.
JSDOM = FRONTEND / "node_modules" / "jsdom" / "lib" / "api.js"

# Rights entries of every kind the records hold, each { name, uri } as
# stored (made up, but shaped like real ones from November 2026).
ENTRIES = [
    # A plain licence with its address.
    {"name": "Open Government Licence - Canada", "uri": "https://open.canada.ca/en/open-government-licence-canada"},
    # A name only.
    {"name": "CC-BY 4.0", "uri": None},
    # Access rights: never a licence.
    {"name": None, "uri": "info:eu-repo/semantics/openAccess"},
    {"name": "", "uri": " info:eu-repo/semantics/embargoedAccess"},
    # The "None" placeholder: no licence.
    {"name": "None", "uri": None},
    # A URI only, on spdx.org (named by its id) and elsewhere.
    {"name": None, "uri": "https://spdx.org/licenses/CC0-1.0.html"},
    {"name": None, "uri": "https://example.org/a-licence"},
    # An address the page never links (not http or https).
    {"name": "Some licence", "uri": "javascript:alert(1)"},
    # An HTML link split across the name and the URI, as ArcGIS hubs send it
    # (the Street Tree record of Maple Ridge).
    {"name": "<a href='",
     "uri": "https://opengov.mapleridge.ca/pages/open-government-licence' rel='nofollow ugc' "
            "target='_blank'><font size='4'><b>Open Government Licence</b></font></a>"},
    # A split one with no link in it.
    {"name": "<b>", "uri": "Licence text</b>"},
    # An entity in text with no tag: kept as written, as on the page.
    {"name": "Terms &amp; conditions", "uri": None},
    # A whole licence in HTML, with a heading, entities, block tags and a
    # text far longer than the page's short name.
    {"name": "<h2>Statistics Canada Open Licence</h2>\n<p>This licence &amp; its terms&nbsp;apply.</p>"
             "<ul><li>One</li><li>Two</li></ul>" + "<p>More words here.</p>" * 20,
     "uri": None},
]


def test_access_rights_and_the_none_placeholder_are_not_licences():
    assert licence_text({"name": None, "uri": "info:eu-repo/semantics/openAccess"}) is None
    assert licence_text({"name": "None", "uri": None}) is None
    # With a link, "None" is a name like any other, as on the page.
    assert licence_text({"name": "None", "uri": "https://example.org/x"}) == "None (https://example.org/x)"


def test_each_licence_is_its_full_plain_text_and_its_link():
    texts = licence_texts(ENTRIES)
    assert texts[:3] == ["Open Government Licence - Canada (https://open.canada.ca/en/open-government-licence-canada)",
                         "CC-BY 4.0",
                         "CC0-1.0 (https://spdx.org/licenses/CC0-1.0.html)"]
    # A URI only that is not on spdx.org is written once, not twice.
    assert texts[3] == "https://example.org/a-licence"
    assert texts[4] == "Some licence"
    assert texts[5] == ("Open Government Licence "
                        "(https://opengov.mapleridge.ca/pages/open-government-licence)")
    assert texts[6] == "Licence text"
    assert texts[7] == "Terms &amp; conditions"
    # HTML taken out, entities read, never shortened.
    assert texts[8].startswith("Statistics Canada Open Licence This licence & its terms apply. One Two More")
    assert texts[8].endswith("More words here.") and len(texts[8]) > 300
    assert len(texts) == 9


def test_the_record_comes_first_and_the_check_only_when_it_has_none():
    own = [{"name": "Record licence", "uri": None}, {"name": None, "uri": "info:eu-repo/semantics/openAccess"}]
    checked = [{"name": "Checked licence", "uri": None}]
    access_only = [{"name": None, "uri": "info:eu-repo/semantics/restrictedAccess"}]
    assert shown_licences(own, checked) == ["Record licence"]
    assert shown_licences(access_only, checked) == ["Checked licence"]
    assert shown_licences([{"name": "None", "uri": None}], checked) == ["Checked licence"]
    assert shown_licences([], None) == [] and shown_licences(None, None) == []


def test_plain_text_reads_html_as_the_page_does():
    assert plain_text("a<br>b") == "a b"
    # Text with no tag is not read as HTML, so an entity stays as written,
    # as on the page; with a tag, it is read.
    assert plain_text("  Tom &amp; Jerry \n\t ") == "Tom &amp; Jerry"
    assert plain_text("<i>Tom</i> &amp; Jerry") == "Tom & Jerry"
    assert plain_text("no tags, 3 < 4") == "no tags, 3 < 4"
    assert plain_text(None) == ""


# --- The page and the CSV agree (node) -------------------------------------------

# Whether node can run the page's code with jsdom.
NODE_WITH_JSDOM = NODE_READY and JSDOM.exists()


def page_licences(folder, cases):
    """Run the page's own rule (shownLicences in datasetText.js, with jsdom
    for the HTML) on each case, (record entries, checked entries, record set
    aside), and return each case's licences written as the CSV writes them:
    the page's full text (fullText, else its name) and its link. The cases
    go to node in a file, as there can be thousands."""
    folder = Path(folder)
    (folder / "cases.json").write_text(json.dumps(cases), encoding="utf-8")
    script = f"""
import {{ readFileSync }} from 'node:fs'
import {{ JSDOM }} from {json.dumps(JSDOM.as_uri())}
globalThis.DOMParser = new JSDOM('').window.DOMParser
const {{ shownLicences }} = await import({json.dumps(front_end_module("datasetText.js"))})
const cases = JSON.parse(readFileSync({json.dumps(str(folder / "cases.json"))}, 'utf8'))
const written = cases.map(([own, checked, setAside]) => shownLicences(own, checked, setAside).licences
  .map((licence) => {{
    const text = licence.fullText ?? licence.name
    return licence.uri && licence.uri !== text ? `${{text}} (${{licence.uri}})` : text
  }}))
console.log(JSON.stringify(written))
"""
    return json.loads(run_node(folder, script))


@pytest.mark.skipif(not NODE_WITH_JSDOM, reason="needs node and the front end's packages")
def test_the_licences_match_the_detail_page_run_by_node(tmp_path):
    """The page's shownLicences on each entry (alone, and as a record's own
    list with a checked list, with and without the record set aside) gives
    the same licences as this file."""
    cases = [([entry], None, False) for entry in ENTRIES] + [
        ([ENTRIES[2]], [ENTRIES[1]], False),             # access right only: the check's licence
        ([ENTRIES[4]], [ENTRIES[8]], False),             # "None" only: the check's licence
        ([ENTRIES[0], ENTRIES[2]], [ENTRIES[1]], False),  # the record's own wins
        ([ENTRIES[0], ENTRIES[2]], [ENTRIES[1]], True),   # unless it is set aside
        ([ENTRIES[0]], None, True),                      # set aside with nothing checked: none
        (ENTRIES, None, False),                          # several, in order
    ]
    page = page_licences(tmp_path, cases)
    assert page == [shown_licences(own, checked, set_aside) for own, checked, set_aside in cases]
    # The record's own wins; set aside, the checked one shows; set aside with
    # nothing checked, nothing shows.
    assert page[-4][0].startswith("Open Government Licence - Canada")
    assert page[-3] == ["CC-BY 4.0"] and page[-2] == []


# --- The real data -------------------------------------------------------------------

# The real data, which is not committed: these tests are skipped without it.
REAL_DATA = Path(__file__).resolve().parents[1] / "data" / "api"
HAS_REAL_DATA = (REAL_DATA / "lunaris_datasets.parquet").is_file()

# The real hand-decided list (the conftest fixture points the API elsewhere).
REAL_LIST = Path(api.dataset_licences.__file__).resolve().parent / "licence_hand_decisions.csv"


def real_cases():
    """Return every shown dataset of the real data as (id, record entries,
    checked entries, record set aside by the real list), by id."""
    decisions = read_hand_decisions(REAL_LIST)
    con = duckdb.connect()
    rows = con.execute(f"""
        SELECT d.id, d.licences, s.licences
        FROM '{REAL_DATA / "lunaris_datasets.parquet"}' AS d
        LEFT JOIN '{REAL_DATA / "dataset_source_checks.parquet"}' AS s ON s.dataset_id = d.id
        ORDER BY d.id""").fetchall()
    con.close()
    return [(dataset_id, own, checked, dataset_id in decisions) for dataset_id, own, checked in rows]


@pytest.mark.skipif(not (NODE_WITH_JSDOM and HAS_REAL_DATA), reason="needs node, jsdom and the real data")
def test_the_page_and_the_csv_agree_on_every_real_dataset(tmp_path):
    """On every dataset the site shows (3,578 on 2026-11-17), with the real
    hand-decided list, the page's rule and this file give the same
    licences."""
    cases = real_cases()
    page = page_licences(tmp_path, [[own, checked, set_aside] for _, own, checked, set_aside in cases])
    csv = [shown_licences(own, checked, set_aside) for _, own, checked, set_aside in cases]
    differ = [case[0] for case, mine, theirs in zip(cases, csv, page) if mine != theirs]
    assert differ == []
    assert len(cases) >= 3578 and sum(case[3] for case in cases) == 9


@pytest.mark.skipif(not HAS_REAL_DATA, reason="needs the real data")
def test_the_real_list_fits_the_real_data():
    """Every dataset on the real list is shown, has the title the list
    gives, and has a checked licence, which is what the CSV now writes."""
    decisions = read_hand_decisions(REAL_LIST)
    con = duckdb.connect()
    con.execute(f"CREATE VIEW lunaris_datasets AS SELECT * FROM '{REAL_DATA / 'lunaris_datasets.parquet'}'")
    con.execute(f"CREATE VIEW dataset_source_checks AS SELECT * FROM "
                f"'{REAL_DATA / 'dataset_source_checks.parquet'}'")
    assert hand_decision_problems(con.cursor(), decisions) == []
    con.close()
    shown = {dataset_id: shown_licences(own, checked, set_aside)
             for dataset_id, own, checked, set_aside in real_cases() if set_aside}
    assert shown["oai:polardata.ca:10192"] == ["PDC Terms of Use (https://polardata.ca/policy/usage)"]
    assert shown["oai:abacus.library.ubc.ca-dataverse-abacus-open:44117"] == [
        "Open Government Licence - Surrey v2.0"]


# --- The hand-decided list ----------------------------------------------------------

# Who decided the 9 on the list, exactly as the list must say it.
DECIDED_BY = "Claude's reading, approved by Lucia, 2026-11-17"

# The test data's eagle-nests record: its own licence is the Open Government
# Licence - British Columbia with a link; the source check found the same
# licence with no link, so setting the record's aside shows the check's.
EAGLE = "oai:catalogue.data.gov.bc.ca:eagle-nests"
EAGLE_REASON = "The licence link in the record no longer works (a made-up reason for the tests)."


def test_the_real_list_holds_the_9_datasets_with_reasons():
    decisions = read_hand_decisions(REAL_LIST)
    polar = {f"oai:polardata.ca:{number}" for number in (10192, 10786, 11407, 11464, 11748, 11749, 12131)}
    surrey = {"oai:abacus.library.ubc.ca-dataverse-abacus-open:44117",
              "oai:abacus.library.ubc.ca-dataverse-abacus-open:44125"}
    assert set(decisions) == polar | surrey
    for dataset_id, row in decisions.items():
        assert row["decided_by"] == DECIDED_BY
        assert row["title"].strip() == row["title"] and row["title"]
        if dataset_id in polar:
            assert "https://www.polardata.ca/pdcinput/public/termsofuse" in row["reason"]
            # The day Lucia found the link dead, since the tip's own date is
            # the site-wide check date (3 November).
            assert row["reason"].startswith("On 17 November 2026, the licence link in the record")
            assert "no longer worked" in row["reason"]
        else:
            assert row["title"].startswith("[City of Surrey]")
            assert "not a licence" in row["reason"] and "Open Government Licence - Surrey" in row["reason"]
            assert "Abacus page, checked on 17 November 2026," in row["reason"]


@pytest.mark.parametrize("text, problem", [
    ("dataset_id,title,reason\n", "the columns must be"),
    ("dataset_id,title,reason,decided_by\na,A,,someone\n", "every column needs a value"),
    ("dataset_id,title,reason,decided_by\na,A,r,someone\na,A,r,someone\n", "listed twice"),
])
def test_a_badly_written_list_is_refused(tmp_path, text, problem):
    path = tmp_path / "list.csv"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match=problem):
        read_hand_decisions(path)
    assert HAND_DECISION_COLUMNS == ["dataset_id", "title", "reason", "decided_by"]


def eagle_list(tmp_path, monkeypatch, rows=((EAGLE, "Bald Eagle nest survey", EAGLE_REASON, DECIDED_BY),)):
    """Point the API at a list of the rows given (by default, eagle-nests)."""
    path = write_licence_decisions(tmp_path / "decisions.csv", rows)
    monkeypatch.setattr(api.dataset_licences, "HAND_DECISIONS_FILE", path)


def test_a_listed_dataset_shows_the_checked_licence_in_the_api_and_the_csv(data_dir, tmp_path, monkeypatch):
    eagle_list(tmp_path, monkeypatch)
    with TestClient(create_app(Settings(data_dir=data_dir))) as client:
        detail = client.get(f"/api/datasets/{EAGLE}").json()
        assert detail["record_licence_set_aside"] == EAGLE_REASON
        # The harvested licence is still there, unchanged.
        assert detail["licences"][0]["uri"] == (
            "https://www2.gov.bc.ca/gov/content/data/open-data/open-government-licence-bc")
        results = {result["id"]: result for result in client.get("/api/datasets").json()["results"]}
        assert results[EAGLE]["record_licence_set_aside"] == EAGLE_REASON
        assert all(result["record_licence_set_aside"] is None
                   for dataset_id, result in results.items() if dataset_id != EAGLE)
        text = client.get("/api/export/datasets").content.decode("utf-8-sig")
    rows = list(__import__("csv").DictReader(text.split("\r\n")[4:]))
    licences = {row["title"]: row["licence"] for row in rows}
    assert licences["Bald Eagle nest survey"] == "Open Government Licence - British Columbia"


def test_without_the_list_the_record_comes_first(client):
    detail = client.get(f"/api/datasets/{EAGLE}").json()
    assert detail["record_licence_set_aside"] is None


@pytest.mark.parametrize("row, problem", [
    (("oai:nowhere:1", "Nothing", "r", DECIDED_BY), "is not a dataset on the site"),
    ((EAGLE, "Another title", "r", DECIDED_BY), "is titled 'Bald Eagle nest survey' in the data"),
    (("oai:catalogue.data.gov.bc.ca:forest-plots", "Forest plots", "r", DECIDED_BY),
     "has no licence from the source check"),
])
def test_the_api_refuses_to_start_with_a_list_that_does_not_fit(data_dir, tmp_path, monkeypatch, row, problem):
    eagle_list(tmp_path, monkeypatch, rows=(row,))
    with pytest.raises(DataFilesError, match=problem):
        with TestClient(create_app(Settings(data_dir=data_dir))):
            pass
