"""Tests for the CSV downloads (api/export.py and api/gbif_links.py):
GET /api/export/datasets, /api/export/ranks/{rank} and
/api/export/hexes/{cell}. The client and the test data are in conftest.py:
the Datasets list shows salmon (2022), eagle-nests (2020) and forest-plots
(no date) by default; kingdom Animalia holds AAAA (the robin, 5 records)
and CCCC (1 record), Plantae BBBB (3, old key OLD1 merged into it) and DDDD
(1); hexagon A (872800b30ffffff) holds AAAA and CCCC, hexagon B
(8728c9b40ffffff) BBBB and DDDD."""

import csv
import io
import json
import shutil
import subprocess
from datetime import datetime, timezone

import h3
import pandas
import pytest
from fastapi.testclient import TestClient

import api.cells
import api.ranks
from api.export import BOM, export_date, file_name, km2_text, one_line
from api.gbif_links import gbif_cell_search_url, to_fixed
from api.main import create_app
from api.settings import Settings
from conftest import DATASETS_SQL, write_test_data
from frontend_node import NODE_READY, front_end_module, run_node

CELL_A = "872800b30ffffff"
CELL_B = "8728c9b40ffffff"
ANIMALIA = {"kingdom": "Animalia"}

# The front end's own example (frontend/src/gbifLinks.test.js): the robin,
# key 59PMB, in hexagon 8728d1aacffffff, for 2000 to 2020. This is the link
# gbifCellSearchUrl in frontend/src/gbifLinks.js gives for it, made by
# running that file with node on 2026-11-09.
ROBIN_CELL = "8728d1aacffffff"
ROBIN_LINK = (
    "https://www.gbif.org/occurrence/search?checklistKey=7ddf754f-d193-4cc9-b351-99906754a03b"
    "&taxonKey=59PMB&geometry=POLYGON%28%28-123.346457+48.419051%2C-123.338215+48.429769%2C"
    "-123.348150+48.438713%2C-123.366327+48.436938%2C-123.374565+48.426220%2C-123.364631+48.417276%2C"
    "-123.346457+48.419051%29%29&occurrenceStatus=PRESENT&year=2000%2C2020")

# The byte order mark as it starts every file: three bytes in UTF-8.
BOM_BYTES = b"\xef\xbb\xbf"

# A dataset title with every awkward thing a value can hold: commas, quote
# marks, a line break and accented letters (what the BOM is for). The test
# data's eagle-nests title is replaced by it, as SQL.
TRICKY_TITLE = 'Bald Eagle, "nest" survey à l\'île\nsecond line, with a comma (été)'
TRICKY_SQL = ("'Bald Eagle, \"nest\" survey à l''île' || chr(10) || "
              "'second line, with a comma (été)'")


def download(client, path, **params):
    """Ask for a download and return (response, header lines, column names,
    rows as dicts), checking it worked, is a CSV attachment and starts with
    one byte order mark. The file is read as plain UTF-8, as a reader that
    knows nothing of the BOM would, so the BOM stays at the start of the
    first header line; it is taken off that line before it is returned."""
    response = client.get(path, params=params)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert response.headers["content-disposition"].startswith('attachment; filename="bcbn_')
    assert response.content.startswith(BOM_BYTES + b"# BCBN ")
    header, columns, rows = read_back(response.content.decode("utf-8"))
    assert header[0].startswith(BOM + "# ")
    return response, [header[0][len(BOM):], *header[1:]], columns, rows


def read_back(text):
    """Read a downloaded file the way R's read.csv(file, skip = 4) does:
    the first 4 lines skipped (the BOM with the first of them), then CSV
    with a row of column names. Returns (the 4 skipped lines, the column
    names, the rows as dicts)."""
    stream = io.StringIO(text, newline="")
    header = [stream.readline() for _ in range(4)]
    reader = csv.reader(stream)
    columns = next(reader)
    return header, columns, [dict(zip(columns, row)) for row in reader]


def keys(rows):
    """The specieskey of each species row, in order."""
    return [row["specieskey"] for row in rows]


# --- The file ---------------------------------------------------------------------


def test_exactly_four_header_lines_then_the_columns(client):
    for path, params in (("/api/export/datasets", {}), ("/api/export/ranks/kingdom", ANIMALIA),
                         (f"/api/export/hexes/{CELL_A}", {})):
        response, header, columns, _ = download(client, path, **params)
        assert all(line.startswith("# ") and line.endswith("\r\n") for line in header)
        assert header[3] == "# To read in R: read.csv(file, skip = 4)\r\n"
        # The fifth line is the column names, not a fifth "#" line.
        assert not response.content.decode("utf-8").split("\r\n")[4].startswith("#")
        assert columns[0] in ("title", "specieskey")


def test_the_datasets_file(client):
    response, header, columns, rows = download(client, "/api/export/datasets", q="eagle")
    assert header[0] == f"# BCBN Datasets search results, exported {export_date()}\r\n"
    assert header[1] == '# Search: words "eagle"; BC data only; sorted by relevance\r\n'
    assert header[2] == ("# Source: Lunaris dataset records, as harvested; each row links to the "
                         "original catalogue\r\n")
    assert columns == ["title", "creators", "published", "catalogue", "doi", "link"]
    assert rows[0] == {"title": "Bald Eagle nest survey",
                       "creators": "SBOT Requests;  Blackburn, Ian ; SBOT Requests",
                       "published": "2020-05-01", "catalogue": "BC Data Catalogue", "doi": "",
                       "link": "https://catalogue.data.gov.bc.ca/dataset/eagle-nests"}
    salmon = next(row for row in rows if row["title"].startswith("Salmon"))
    assert (salmon["doi"], salmon["link"]) == ("10.1/salmon", "https://doi.org/10.1/salmon")
    plots = next(row for row in rows if row["title"] == "Forest plots")
    assert (plots["published"], plots["creators"], plots["doi"]) == ("", "", "")
    assert response.headers["content-disposition"] == (
        f'attachment; filename="bcbn_datasets_eagle_{export_date()}.csv"')


def test_the_rank_file(client):
    response, header, columns, rows = download(client, "/api/export/ranks/phylum", kingdom="Animalia",
                                               phylum="Chordata", year_from=2019, year_to=2020)
    assert header[0] == f"# BCBN species list for phylum Chordata, exported {export_date()}\r\n"
    assert header[1] == ("# List: species of phylum Chordata (kingdom Animalia > phylum Chordata); "
                         "records from 2019 to 2020; sorted by most records first\r\n")
    assert header[2] == ("# Source: GBIF occurrence download https://doi.org/10.15468/dl.test; names: "
                         "Catalogue of Life COL26.6 XR (ChecklistBank 315557); English names and BC "
                         "status: BC Species and Ecosystems Explorer (BCSEE), provincial export of "
                         "2026-05-05, accessed 2026-09-26\r\n")
    assert columns == ["specieskey", "scientific_name", "english_name", "bcsee_status"]
    assert rows == [{"specieskey": "AAAA", "scientific_name": "Turdus migratorius",
                     "english_name": "American Robin", "bcsee_status": "Yellow"}]
    assert response.headers["content-disposition"] == (
        f'attachment; filename="bcbn_species_Chordata_2019-2020_{export_date()}.csv"')


def test_the_hexagon_file(client):
    response, header, columns, rows = download(client, f"/api/export/hexes/{CELL_A}")
    assert header[0] == f"# BCBN species list for hexagon {CELL_A}, exported {export_date()}\r\n"
    # The hexagon's own size, never its resolution number.
    size = km2_text(h3.cell_area(CELL_A, "km^2"))
    assert header[1] == (f"# List: species in H3 hexagon {CELL_A}, a hexagon of about {size}; "
                         "sorted by most records first\r\n")
    assert size == "5.1 km²"
    assert "resolution" not in "".join(header)
    assert header[2].startswith("# Source: GBIF occurrence download https://doi.org/10.15468/dl.test;")
    assert columns == ["specieskey", "scientific_name", "english_name", "bcsee_status", "gbif_link"]
    assert keys(rows) == ["AAAA", "CCCC"]
    # The card's name without its authorship, and empty values where the
    # species has no English name or shown status.
    assert rows[1]["scientific_name"] == "Bombus (Pyrobombus) vosnesenskii"
    assert (rows[1]["english_name"], rows[1]["bcsee_status"]) == ("", "")
    assert response.headers["content-disposition"] == (
        f'attachment; filename="bcbn_species_hexagon_{CELL_A}_{export_date()}.csv"')


def test_a_hexagon_file_for_a_rank_names_it(client):
    response, header, _, rows = download(client, f"/api/export/hexes/{CELL_A}", rank="genus", kingdom="Animalia",
                                         phylum="Arthropoda", **{"class": "Insecta", "order": "Hymenoptera"},
                                         family="Apidae", genus="Bombus", year_from=2021)
    assert keys(rows) == ["CCCC"]
    assert "; only genus Bombus (kingdom Animalia > phylum Arthropoda > class Insecta > order Hymenoptera " \
           "> family Apidae > genus Bombus); records from 2021 to 2021;" in header[1]
    assert response.headers["content-disposition"] == (
        f'attachment; filename="bcbn_species_hexagon_{CELL_A}_Bombus_2021-2021_{export_date()}.csv"')


def test_the_doi_column_only_ever_holds_a_doi(client):
    _, _, _, rows = download(client, "/api/export/datasets", frontend_group=["bc_data", "pending"])
    by_title = {row["title"]: row for row in rows}
    assert by_title["Golden eagle tracking worldwide"]["doi"] == "10.5061/dryad.golden"
    # A record whose identifier is a web address has no DOI, even if it had
    # some other identifier.
    for title in ("Bald Eagle nest survey", "Forest plots", "Beagle Channel water chemistry"):
        assert by_title[title]["doi"] == ""


def tricky_download(tmp_path):
    """Return the bytes of the Datasets download (sorted by title, so the
    tricky title comes first) from test data whose eagle-nests title is
    TRICKY_TITLE."""
    sql = DATASETS_SQL.replace("'Bald Eagle nest survey'", TRICKY_SQL)
    settings = Settings(data_dir=write_test_data(tmp_path, datasets_sql=sql), sensitive_max_res=None)
    with TestClient(create_app(settings)) as client:
        return download(client, "/api/export/datasets", sort="title")[0].content


def test_a_value_with_commas_quotes_a_line_break_and_accents_round_trips(tmp_path):
    content = tricky_download(tmp_path)
    _, columns, rows = read_back(content.decode("utf-8"))
    assert columns == ["title", "creators", "published", "catalogue", "doi", "link"]
    assert rows[0]["title"] == TRICKY_TITLE
    assert rows[0]["creators"] == "SBOT Requests;  Blackburn, Ian ; SBOT Requests"
    # Quoted, the quote marks inside written twice.
    assert ('"Bald Eagle, ""nest"" survey à l\'île\nsecond line, with a comma (été)"'
            in content.decode("utf-8"))


def test_every_file_starts_with_one_bom_before_the_four_header_lines(client):
    for path, params in (("/api/export/datasets", {}), ("/api/export/ranks/kingdom", ANIMALIA),
                         (f"/api/export/hexes/{CELL_A}", {})):
        content = client.get(path, params=params).content
        assert content.startswith(BOM_BYTES) and not content.startswith(BOM_BYTES * 2)
        # Read as UTF-8 with a BOM (as Excel does), the text starts with the
        # first "# " line, and the BOM is nowhere else.
        text = content.decode("utf-8-sig")
        assert BOM not in text
        lines = text.split("\r\n")
        assert [line[:2] for line in lines[:5]] == ["# ", "# ", "# ", "# ", lines[4][:2]]
        assert not lines[4].startswith("#")


def test_pandas_reads_the_file_skipping_four_lines(tmp_path):
    content = tricky_download(tmp_path)
    frame = pandas.read_csv(io.BytesIO(content), skiprows=4, keep_default_na=False)
    assert list(frame.columns) == ["title", "creators", "published", "catalogue", "doi", "link"]
    assert len(frame) == 3
    assert frame["title"][0] == TRICKY_TITLE


@pytest.mark.skipif(shutil.which("Rscript") is None, reason="needs R (Rscript)")
def test_r_reads_the_file_with_skip_4(tmp_path):
    """Runs read.csv(file, skip = 4), as the file's last header line says,
    and checks the column names, the row count, and the tricky title letter
    by letter (R writes the code of each character, so nothing is lost on
    the way back)."""
    path = tmp_path / "datasets.csv"
    path.write_bytes(tricky_download(tmp_path))
    script = (f"d <- read.csv({json.dumps(str(path))}, skip = 4)\n"
              "cat(names(d), sep = ','); cat('\\n'); cat(nrow(d)); cat('\\n')\n"
              "cat(utf8ToInt(enc2utf8(d$title[1])), sep = ',')\n")
    output = subprocess.run(["Rscript", "-e", script], capture_output=True, text=True, check=True).stdout
    names, count, codes = output.splitlines()
    assert names == "title,creators,published,catalogue,doi,link"
    assert count == "3"
    assert [int(code) for code in codes.split(",")] == [ord(letter) for letter in TRICKY_TITLE]


@pytest.mark.parametrize("words", ["eagle\nnests", "eagle\r\nnests", "eagle\rnests", "eagle nests"])
def test_a_query_with_a_line_break_stays_on_one_header_line(client, words):
    response, header, columns, _ = download(client, "/api/export/datasets", q=words)
    assert header[1].startswith('# Search: words "eagle nests";')
    assert columns[0] == "title"
    _, header, columns, _ = download(client, f"/api/export/hexes/{CELL_A}", q=words.replace("eagle", "robin"))
    assert 'names matching "robin nests"' in header[1]
    assert columns[0] == "specieskey"


def test_header_text_and_file_names_are_cleaned():
    assert one_line("a\n\nb\r\nc d  e ") == "a b c d e"
    assert file_name("datasets", ["Truite/saumon: été 2020!", None], "2026-11-09") == \
        "bcbn_datasets_Truite-saumon-ete-2020_2026-11-09.csv"
    assert file_name("datasets", ["海"], "2026-11-09") == "bcbn_datasets_2026-11-09.csv"


def test_the_export_date_is_the_date_in_vancouver():
    # 3 a.m. UTC on November 10 is still November 9 in Vancouver.
    assert export_date(datetime(2026, 11, 10, 3, 0, tzinfo=timezone.utc)) == "2026-11-09"
    assert export_date(datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)) == "2026-11-10"


# --- The same rows as the lists -----------------------------------------------------


@pytest.mark.parametrize("params", [{}, {"q": "eagle"}, {"frontend_group": ["bc_data", "pending"]},
                                    {"frontend_group": "pending", "sort": "title"}, {"sort": "oldest"},
                                    {"year_from": 2020}, {"access": "restricted"}, {"mappable": True},
                                    {"species": "AAAA", "frontend_group": ["bc_data", "pending"]},
                                    {"organism": "salmon"}, {"catalogue": "catalogue.data.gov.bc.ca"}])
def test_the_datasets_file_holds_the_list_in_its_order(client, params):
    listed = client.get("/api/datasets", params={**params, "page_size": 100}).json()
    _, _, _, rows = download(client, "/api/export/datasets", **params)
    assert len(rows) == listed["total"]
    assert [row["title"] for row in rows] == [result["title"] for result in listed["results"]]


@pytest.mark.parametrize("params", [{}, {"q": "robin"}, {"status": "yellow"}, {"status": "none"},
                                    {"sort": "fewest_records"}, {"sort": "scientific"}, {"sort": "english"},
                                    {"year_from": 2021}])
def test_the_species_files_hold_the_lists_in_their_order(client, params):
    card = client.get("/api/ranks/kingdom", params={**ANIMALIA, **params}).json()
    _, _, _, rows = download(client, "/api/export/ranks/kingdom", **ANIMALIA, **params)
    assert len(rows) == card["matching"]
    assert keys(rows) == [member["specieskey"] for member in card["members"]]
    panel = client.get(f"/api/hexes/{CELL_A}", params=params).json()
    _, _, _, rows = download(client, f"/api/export/hexes/{CELL_A}", **params)
    assert len(rows) == panel["matching"]
    assert keys(rows) == [result["specieskey"] for result in panel["results"]]


def test_filters_and_sort_change_the_rows(client):
    assert keys(download(client, "/api/export/ranks/kingdom", **ANIMALIA, sort="fewest_records")[3]) == \
        ["CCCC", "AAAA"]
    assert keys(download(client, "/api/export/ranks/kingdom", **ANIMALIA, status="none")[3]) == ["CCCC"]
    assert keys(download(client, f"/api/export/hexes/{CELL_A}", q="robin")[3]) == ["AAAA"]
    assert keys(download(client, f"/api/export/hexes/{CELL_A}", year_from=2021)[3]) == ["CCCC"]
    titles = [row["title"] for row in download(client, "/api/export/datasets", sort="title")[3]]
    assert titles == ["Bald Eagle nest survey", "Forest plots", "Salmon counts in Hakai Pass"]


def test_paging_parameters_do_not_cut_the_download(client):
    assert len(download(client, "/api/export/datasets", page=2, page_size=1)[3]) == 3
    assert len(download(client, "/api/export/ranks/kingdom", **ANIMALIA, offset=1, limit=1)[3]) == 2
    assert len(download(client, f"/api/export/hexes/{CELL_A}", offset=1, limit=1)[3]) == 2


def test_hidden_species_never_appear(client, monkeypatch):
    """A stand-in rule hides the robin everywhere (sensitive.py hides
    nothing for now): it is left out of both species files, as of the
    lists."""
    def stand_in_rule(settings, res):
        """Hide AAAA in every list."""
        return frozenset({"AAAA"})

    monkeypatch.setattr(api.cells, "hidden_species", stand_in_rule)
    monkeypatch.setattr(api.ranks, "hidden_species", stand_in_rule)
    for path, params in (("/api/export/ranks/kingdom", ANIMALIA), (f"/api/export/hexes/{CELL_A}", {})):
        response, _, _, rows = download(client, path, **params)
        assert keys(rows) == ["CCCC"]
        assert "AAAA" not in response.content.decode("utf-8")
        assert "Turdus" not in response.content.decode("utf-8")
    # A rank whose only species is hidden is not found, as for its card.
    assert client.get("/api/export/ranks/phylum", params={**ANIMALIA, "phylum": "Chordata"}).status_code == 404


# --- Bad requests ---------------------------------------------------------------------


@pytest.mark.parametrize("list_path, export_path, params", [
    ("/api/datasets", "/api/export/datasets", {"q": "a"}),
    ("/api/datasets", "/api/export/datasets", {"sort": "relevance"}),
    ("/api/datasets", "/api/export/datasets", {"catalogue": "nowhere.example.org"}),
    ("/api/datasets", "/api/export/datasets", {"frontend_group": "elsewhere"}),
    ("/api/datasets", "/api/export/datasets", {"species": "NOPE"}),
    ("/api/datasets", "/api/export/datasets", {"year_from": 2021, "year_to": 2019}),
    ("/api/ranks/phylum", "/api/export/ranks/phylum", {"kingdom": "Animalia"}),
    ("/api/ranks/tribe", "/api/export/ranks/tribe", {"tribe": "Bombini"}),
    ("/api/ranks/kingdom", "/api/export/ranks/kingdom", {**ANIMALIA, "status": "purple"}),
    ("/api/ranks/kingdom", "/api/export/ranks/kingdom", {**ANIMALIA, "sort": "size"}),
    ("/api/ranks/kingdom", "/api/export/ranks/kingdom", {**ANIMALIA, "q": "a"}),
    ("/api/ranks/kingdom", "/api/export/ranks/kingdom", {**ANIMALIA, "year_from": 1700}),
    ("/api/ranks/phylum", "/api/export/ranks/phylum", {**ANIMALIA, "phylum": "Nope"}),
    ("/api/hexes/abc", "/api/export/hexes/abc", {}),
    ("/api/hexes/832800fffffffff", "/api/export/hexes/832800fffffffff", {}),
    (f"/api/hexes/{CELL_A}", f"/api/export/hexes/{CELL_A}", {"status": "purple"}),
    (f"/api/hexes/{CELL_A}", f"/api/export/hexes/{CELL_A}", {"sort": "size"}),
    (f"/api/hexes/{CELL_A}", f"/api/export/hexes/{CELL_A}", {"year_from": 2021, "year_to": 2019}),
    (f"/api/hexes/{CELL_A}", f"/api/export/hexes/{CELL_A}", {"rank": "kingdom"}),
    (f"/api/hexes/{CELL_A}", f"/api/export/hexes/{CELL_A}", {"rank": "kingdom", "kingdom": "Fungi"}),
])
def test_bad_requests_get_the_lists_answer(client, list_path, export_path, params):
    listed = client.get(list_path, params=params)
    exported = client.get(export_path, params=params)
    assert listed.status_code in (400, 404)
    assert (exported.status_code, exported.json()) == (listed.status_code, listed.json())


def test_the_hexagon_download_refuses_a_species(client):
    for value in ("AAAA", ""):
        response = client.get(f"/api/export/hexes/{CELL_A}", params={"species": value})
        assert response.status_code == 400
        assert "species=" in response.json()["detail"]


def test_the_export_paths_are_not_taken_for_a_dataset_id(client):
    assert client.get("/api/export/datasets").status_code == 200
    # /api/datasets/export is still read as a dataset id, which does not exist.
    assert client.get("/api/datasets/export").status_code == 404


# --- The GBIF link -------------------------------------------------------------------


def test_the_robin_link_is_the_front_ends():
    assert gbif_cell_search_url(["59PMB"], ROBIN_CELL, (2000, 2020)) == ROBIN_LINK


def test_numbers_are_rounded_as_javascript_rounds_them():
    # 0.0078125 is exact in binary: a true half, which toFixed rounds up and
    # Python's own format would round to the even digit.
    assert to_fixed(-123.0078125) == "-123.007813"
    assert to_fixed(48.0) == "48.000000"


@pytest.mark.skipif(not NODE_READY, reason="needs node and the front end's packages (npm install in frontend/)")
def test_the_links_match_gbif_links_js_run_by_node(tmp_path):
    """Runs frontend/src/gbifLinks.js itself on a few cases (every
    resolution, several keys, with and without years) and compares the
    text of every link."""
    cases = [(["59PMB"], ROBIN_CELL, [2000, 2020]), (["CVW5J", "VP2T6"], ROBIN_CELL, None),
             (["AAAA"], CELL_A, None), (["BBBB", "OLD1"], CELL_B, [2019, 2019]),
             (["X"], "842800bffffffff", [1900, 2026]), (["X"], "8528c9b7fffffff", None),
             (["X"], "862800b37ffffff", None)]
    script = (f"import {{ gbifCellSearchUrl }} from {front_end_module('gbifLinks.js')!r}\n"
              + "".join(f"console.log(gbifCellSearchUrl({json.dumps({'keys': keys, 'cell': cell, 'years': years})}))\n"
                        for keys, cell, years in cases))
    # gbifLinks.js reads BC's boundary with a Vite "?raw" import, which
    # run_node makes work in plain node.
    output = run_node(tmp_path, script)
    assert output.splitlines() == [gbif_cell_search_url(keys, cell, years) for keys, cell, years in cases]


def test_the_hexagon_file_gives_the_panels_link(client):
    """Each row's link is the one the hexagon panel gives for that species:
    its card key and every key merged into it, the hexagon, and the years
    counted (a missing end filled in, as the panel does)."""
    for params, years in (({}, None), ({"year_from": 2019}, (2019, 2021))):
        _, _, _, rows = download(client, f"/api/export/hexes/{CELL_B}", **params)
        for row in rows:
            panel = client.get(f"/api/hexes/{CELL_B}", params={**params, "species": row["specieskey"]}).json()
            assert panel["years_counted"] == (list(years) if years else None)
            assert row["gbif_link"] == gbif_cell_search_url(panel["species_asked_for"]["gbif_keys"], CELL_B, years)
    willow = next(row for row in rows if row["specieskey"] == "BBBB")
    assert "taxonKey=BBBB&taxonKey=OLD1&" in willow["gbif_link"]
    assert willow["gbif_link"].endswith("&year=2019%2C2021")
