"""Tests for pipeline/lunaris/export_dataset_source_checks.py, on made-up
rows: the check's access words become the plain values (the BC Data
Catalogue's download_audience among them), dates go with the dated values
only, licences are read from the check's text (Zenodo's id replaced by the
licence of its own DataCite record when that names the same one), removed
and the agreement with the record are worked out, Lucia's hand checks are
added (an FRDR one replacing the DataCite-only row; the Water Hub and
PANGAEA ones matched by record id; these three dated 3 November), a field
is left empty when it was not checked, and the file is refused when a dataset is
checked twice by hand, is not on the site or comes from a catalogue the
script does not know."""

import sys
from pathlib import Path

import pandas as pd
import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "pipeline" / "lunaris"))
import export_dataset_source_checks as sc  # noqa: E402

from api.db import SOURCE_ACCESS_DATED, SOURCE_ACCESS_OPEN  # noqa: E402

OPEN = [{"name": None, "uri": "info:eu-repo/semantics/openAccess"}]
RESTRICTED = [{"name": None, "uri": "info:eu-repo/semantics/restrictedAccess"}]
FRDR = "https://doi.org/10.20383/103.0818"


def table_row(rid, **fields):
    """Return one made-up row of the source check table, every value as text."""
    row = {"id": rid, "catalogue": "Zenodo", "platform": "Zenodo", "checked_with": "Zenodo API",
           "source_access": "open", "source_embargo": "none", "source_licence": "", "source_licence_family": "",
           "datacite_licence": "", "source_exists": "yes", "note": ""}
    row.update(fields)
    return row


def shown_table(rows):
    """Return made-up shown datasets (id, link, source_name, licences), indexed by id."""
    return pd.DataFrame(rows, columns=["id", "link", "source_name", "licences"]).set_index("id")


def hand_row(catalogue, link, access, licence="Statistics Canada Open Licence (termsOfUse in Dataverse JSON)",
             record_id=""):
    """Return one made-up row of Lucia's hand check file."""
    return {"catalogue": catalogue, "record_id": record_id, "link_checked": link, "source_access": access,
            "source_licence": licence, "still_exists": "yes (RELEASED)"}


def test_the_access_values_are_the_apis():
    """The export writes only the values the API knows, with the same open ones and dated ones."""
    assert sc.ACCESS_VALUES == SOURCE_ACCESS_OPEN
    assert sc.DATED == set(SOURCE_ACCESS_DATED)


def test_reading_the_checks_licence_text():
    """Each part is a name and its first web address; HTML entities are read; no text is no licence."""
    assert sc.read_licence_text("Creative Commons Attribution 4.0 International https://creativecommons.org/"
                                "licenses/by/4.0/ | cc-by-4.0") == [
        {"name": "Creative Commons Attribution 4.0 International",
         "uri": "https://creativecommons.org/licenses/by/4.0/"},
        {"name": "cc-by-4.0", "uri": None}]
    assert sc.read_licence_text("PDC Terms of Use (https://polardata.ca/policy/usage)")[0]["uri"] == \
        "https://polardata.ca/policy/usage"
    assert sc.read_licence_text("Terms &amp; conditions") == [{"name": "Terms & conditions", "uri": None}]
    assert sc.read_licence_text("") == []


@pytest.mark.parametrize("licence_id, entry, same", [
    ("cc-by-4.0", {"name": "Creative Commons Attribution 4.0 International",
                   "uri": "https://creativecommons.org/licenses/by/4.0/legalcode"}, True),
    ("cc-by-nc-sa-4.0", {"name": "x", "uri": "https://creativecommons.org/licenses/by/4.0/legalcode"}, False),
    ("cc-by-3.0", {"name": "x", "uri": "https://creativecommons.org/licenses/by/4.0/"}, False),
    ("mit-license", {"name": "MIT License", "uri": "https://opensource.org/licenses/MIT"}, True),
    ("cc-zero", {"name": "Creative Commons Zero", "uri": None}, False),
])
def test_a_zenodo_id_and_a_datacite_entry_are_the_same_licence_only_when_they_name_it(licence_id, entry, same):
    assert sc.same_licence(licence_id, entry) is same


def test_access_values_dates_licences_and_removed_from_the_table():
    """BC's restricted becomes its audience; dated values keep their date; Zenodo gets its DataCite licence."""
    rows = [
        table_row("z", source_licence="cc-by-4.0", source_licence_family="CC BY 4.0",
                  datacite_licence="Creative Commons Attribution 4.0 International "
                                   "https://creativecommons.org/licenses/by/4.0/legalcode"),
        table_row("bc", catalogue="BC Data Catalogue", platform="BC Data Catalogue", source_access="restricted",
                  note="state active; download_audience Government and Business BCeID, view_audience Public"),
        table_row("b", catalogue="UBC Dataverse (Borealis)", platform="Borealis", source_access="some files locked",
                  source_embargo="until 2027-02-01", source_licence="(not given)", note="2 of 4 files locked"),
        table_row("gone", source_access="withdrawn", source_exists="no"),
        table_row("z4", source_licence="cc-by-4.0", datacite_licence="MIT License https://opensource.org/licenses/MIT"),
        table_row("cw", catalogue="Canadian Watershed Information Network (CanWIN)", platform="CanWIN",
                  checked_with="open.canada.ca CKAN package_show (same id as the CanWIN copy)"),
    ]
    shown = shown_table([("z", "", "Zenodo", RESTRICTED), ("bc", "", "BC Data Catalogue", OPEN),
                         ("b", "", "UBC Dataverse (Borealis)", OPEN), ("gone", "", "Zenodo", RESTRICTED),
                         ("z4", "", "Zenodo", RESTRICTED),
                         ("cw", "", "CanWIN", OPEN)])
    by_id = {row["dataset_id"]: row for row in sc.api_rows(pd.DataFrame(rows), shown)}
    assert (by_id["z"]["access"], by_id["z"]["access_matches_record"]) == ("open", False)
    assert by_id["z"]["licences"] == [{"name": "Creative Commons Attribution 4.0 International",
                                       "uri": "https://creativecommons.org/licenses/by/4.0/legalcode"}]
    assert (by_id["bc"]["access"], by_id["bc"]["access_note"]) == (
        "government_bceid_only", "download_audience Government and Business BCeID")
    assert (by_id["b"]["access"], by_id["b"]["access_until"], by_id["b"]["licences"]) == (
        "some_files_locked", "2027-02-01", None)
    assert (by_id["gone"]["access"], by_id["gone"]["access_note"], by_id["gone"]["removed"]) == (None, None, True)
    # DataCite names another licence: Zenodo's id is kept, unlinked.
    assert by_id["z4"]["licences"] == [{"name": "cc-by-4.0", "uri": None}]
    assert by_id["z"]["removed"] is False and by_id["z"]["checked_on"] == "2026-11-02"
    assert by_id["cw"]["catalogue_checked"] == "Open Government Portal (Government of Canada)"


def test_the_hand_checks_polar_abacus_circle_and_frdr():
    """Polar Public and Limited, Abacus UBC only, cIRcle open, FRDR open (out of embargo, in Lucia's words)."""
    shown = shown_table([
        ("polar", "https://www.polardata.ca/pdcsearch/PDCSearch.jsp?doi_id=10192", "Polar Data Catalogue", OPEN),
        ("abacus", "https://hdl.handle.net/11272.1/AB2/XGDNN6", "Abacus", OPEN),
        ("circle", "https://hdl.handle.net/2429/68682", "cIRcle (UBC Library)", RESTRICTED),
        ("frdr", FRDR, "Federated Research Data Repository (FRDR)", RESTRICTED),
        ("frdr2", "https://doi.org/10.20383/103.01012", "Federated Research Data Repository (FRDR)", RESTRICTED),
    ])
    hand = pd.DataFrame([
        hand_row("Polar Data Catalogue", "https://www.polardata.ca/pdcsearch/PDCSearch.jsp?doi_id=10192",
                 "Limited (Access Constraints: Limited; no download link)"),
        hand_row("Abacus (Statistics Canada public files)", "https://hdl.handle.net/11272.1/AB2/XGDNN6", "UBC only"),
        hand_row("cIRcle (UBC Library)", "http://hdl.handle.net/2429/68682", "Open: opened without logging in",
                 "Attribution-NonCommercial-NoDerivatives 4.0 International (RightsURI "
                 "http://creativecommons.org/licenses/by-nc-nd/4.0/)"),
    ])
    by_id = {row["dataset_id"]: row for row in sc.hand_rows(hand, shown)}
    assert (by_id["polar"]["access"], by_id["polar"]["access_matches_record"]) == ("limited", False)
    assert by_id["abacus"]["access"] == "ubc_only" and by_id["abacus"]["checked_how"] == "by hand"
    assert by_id["abacus"]["licences"] == [{"name": "Statistics Canada Open Licence", "uri": None}]
    assert (by_id["circle"]["access"], by_id["circle"]["access_matches_record"]) == ("open", False)
    assert by_id["circle"]["licences"][0]["uri"] == "http://creativecommons.org/licenses/by-nc-nd/4.0/"
    assert by_id["circle"]["removed"] is False

    frdr = pd.DataFrame([
        hand_row("Federated Research Data Repository (FRDR)", FRDR, "Open: page opened without logging in; notes say "
                 "'Item exited embargo and became publicly available on 2024-03-10'",
                 "Creative Commons Attribution 4.0 International (CC BY 4.0)"),
        hand_row("SFU Research Data (FRDR)", "https://doi.org/10.20383/103.01012", "Open: page opened",
                 "Creative Commons Public Domain Dedication (CC0 1.0)"),
    ])
    rows = {row["dataset_id"]: row for row in sc.hand_rows(frdr, shown)}
    assert (rows["frdr"]["access"], rows["frdr"]["checked_how"], rows["frdr"]["access_matches_record"]) == (
        "open", "by hand", False)
    # FRDR was checked on 3 November, the Polar, Abacus and cIRcle pages on 2 November.
    assert rows["frdr"]["checked_on"] == rows["frdr2"]["checked_on"] == "2026-11-03"
    assert by_id["circle"]["checked_on"] == "2026-11-02"
    assert "publicly available on 2024-03-10" in rows["frdr"]["access_note"]
    # A licence's short name in brackets is part of its name.
    assert rows["frdr"]["licences"] == [
        {"name": "Creative Commons Attribution 4.0 International (CC BY 4.0)", "uri": None}]
    assert rows["frdr2"]["catalogue_checked"] == "Federated Research Data Repository (FRDR)"
    with pytest.raises(SystemExit, match="does not say Open"):
        sc.hand_rows(pd.DataFrame([hand_row("SFU Research Data (FRDR)", FRDR, "Restricted: login")]), shown)


def test_a_hand_check_replaces_the_tables_row_and_twice_by_hand_or_not_on_the_site_is_refused():
    """A hand check replaces the table's row; two hand checks of one dataset, or a dataset not shown, stop it."""
    shown = shown_table([("a", "https://hdl.handle.net/2429/1", "cIRcle (UBC Library)", RESTRICTED),
                         ("frdr", FRDR, "FRDR", RESTRICTED),
                         ("frdr2", "https://doi.org/10.20383/103.01012", "FRDR", RESTRICTED)])
    table = pd.DataFrame([table_row(r, platform="FRDR", checked_with="DataCite only") for r in ("a", "frdr", "frdr2")])
    hand = pd.DataFrame([hand_row("cIRcle (UBC Library)", "https://hdl.handle.net/2429/1", "Open")])
    none = pd.DataFrame({"id": []})
    rows = sc.build(table, none, hand, shown)
    assert [(row["dataset_id"], row["checked_how"]) for row in rows] == [
        ("frdr", "DataCite"), ("frdr2", "DataCite"), ("a", "by hand")]
    with pytest.raises(SystemExit, match="checked twice by hand"):
        sc.build(table, none, pd.concat([hand, hand]), shown)
    with pytest.raises(SystemExit, match="does not show"):
        sc.build(pd.concat([table, pd.DataFrame([table_row("x")])]), none, hand.iloc[0:0], shown)
    # Not checked rows are left out, so the hand check is then the only one.
    assert len(sc.build(table, pd.DataFrame({"id": ["a"]}), hand, shown)) == 3


def test_the_water_hub_and_pangaea_hand_checks_are_matched_by_record_id():
    """
    A Water Hub copy agrees with its CanWIN record, so nothing differs; a
    PANGAEA dataset is open with a CC-BY-4.0 licence where its record says
    restricted with none. Both were checked on 3 November.
    """
    hub = "oai:canwin-datahub.ad.umanitoba.ca:kinbasket-reservoir-at-bush-harbour"
    pangaea = "oai:pangaea.de:10.1594/pangaea.962186"
    shown = shown_table([
        (hub, "https://canwin-datahub.ad.umanitoba.ca/data/dataset/kinbasket-reservoir-at-bush-harbour",
         "Canadian Watershed Information Network (CanWIN)", OPEN + [{"name": "CC-BY-SA-4.0", "uri": None}]),
        (pangaea, "https://doi.org/10.1594/pangaea.962186", "PANGAEA", RESTRICTED),
    ])
    hand = pd.DataFrame([
        hand_row("CanWIN (own datasets)", "https://data.cbwaterhub.ca/dataset/kinbasket-reservoir-at-bush-harbour",
                 "Open: page opened without logging in", "CC-BY-SA-4.0 (shown on the page)",
                 record_id="kinbasket-reservoir-at-bush-harbour"),
        hand_row("PANGAEA", "https://doi.org/10.1594/pangaea.962186",
                 "Open: page opened without logging in; Download Data link visible; no embargo notice.",
                 "Creative Commons Attribution 4.0 International (CC-BY-4.0)",
                 record_id="doi:10.1594/pangaea.962186"),
    ])
    by_id = {row["dataset_id"]: row for row in sc.hand_rows(hand, shown)}
    assert by_id[hub]["catalogue_checked"] == "Columbia Basin Water Hub"
    assert (by_id[hub]["access"], by_id[hub]["access_matches_record"], by_id[hub]["checked_on"]) == (
        "open", True, "2026-11-03")
    assert by_id[hub]["licences"] == [{"name": "CC-BY-SA-4.0", "uri": None}]
    assert by_id[pangaea]["catalogue_checked"] == "PANGAEA"
    assert (by_id[pangaea]["access"], by_id[pangaea]["access_matches_record"], by_id[pangaea]["checked_on"]) == (
        "open", False, "2026-11-03")
    assert by_id[pangaea]["licences"] == [
        {"name": "Creative Commons Attribution 4.0 International (CC-BY-4.0)", "uri": None}]
    assert by_id[pangaea]["removed"] is False

    with pytest.raises(SystemExit, match="does not say Open"):
        sc.hand_rows(hand.assign(source_access="Restricted: login"), shown)
    with pytest.raises(SystemExit, match="no shown dataset has the id"):
        sc.hand_rows(hand.assign(record_id="not-shown"), shown)


def test_a_hand_check_from_an_unknown_catalogue_stops_the_export():
    """A catalogue label the script has no rule for is refused, not guessed."""
    shown = shown_table([("a", "https://example.org/a", "Somewhere", OPEN)])
    hand = pd.DataFrame([hand_row("Somewhere new", "https://example.org/a", "Open")])
    with pytest.raises(SystemExit, match="does not know: Somewhere new"):
        sc.build(pd.DataFrame([table_row("a")]), pd.DataFrame({"id": []}), hand, shown)
