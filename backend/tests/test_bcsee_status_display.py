"""Tests for pipeline/bcsee/build_bcsee_status_display.py: which BCSEE
entries are kept, how they are ordered and grouped, and the Catalogue of
Life release check before any GBIF name is looked up. Nothing here talks
to GBIF or writes outside the test's own folder."""

import sys
from pathlib import Path

import duckdb
import pandas as pd
import pytest

# The pipeline scripts are not a package; add their folder to the path the
# way the scripts themselves do.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline" / "bcsee"))
import build_bcsee_status_display as bcsee  # noqa: E402


def entry(status, label, species_level=False):
    """A minimal status entry, enough for order_key()."""
    return {"status": status, "scientific_label": label, "common_label": "",
            "is_species_level": species_level}


def test_order_key_handles_every_status():
    """Species-level first, then shown statuses, then those without a
    status, and a value it does not know sorts last instead of failing."""
    entries = [
        entry("Strange new value", "A a"),
        entry("Not Reviewed", "A a ssp. b"),
        entry("Yellow", "A a ssp. c"),
        entry("Unknown", "A a", species_level=True),
    ]
    entries.sort(key=bcsee.order_key)
    assert [e["status"] for e in entries] == [
        "Unknown", "Yellow", "Not Reviewed", "Strange new value"]


# Two GBIF names the grouping needs, in the shape saved_names() returns.
NAMES = {
    "K1": {"canonicalName": "Abies grandis", "name": "Abies grandis", "rank": "SPECIES",
           "status": "ACCEPTED", "kingdom": "Plantae"},
    "K2": {"canonicalName": "Bombus vosnesenskii", "name": "Bombus vosnesenskii",
           "rank": "SPECIES", "status": "ACCEPTED", "kingdom": "Animalia"},
}


def kept_row(code, key, name, bc_list, level="Species", english=""):
    """One row of the table read_entries() returns, for an entry GBIF
    matched at species level to key."""
    return {"element_code": code, "scientific_name": name, "gbif_species_key": key,
            "gbif_usage_key": key, "gbif_match_type": "EXACT",
            "gbif_rank": "SPECIES" if level == "Species" else "SUBSPECIES",
            "gbif_status": "ACCEPTED", "join_ok": True, "key_changed": False,
            "matched_species_key": key, "bc_list": bc_list, "english_name": english,
            "classification_level": level}


def test_grouping_keeps_no_status_entries_apart():
    """A species with only Not Reviewed gets a row with no statuses; a
    Yellow species keeps its Not Reviewed subspecies as its own entry, and
    its statuses list holds only Yellow."""
    kept = pd.DataFrame([
        kept_row("E1", "K1", "Abies grandis", "Yellow", english="grand fir"),
        kept_row("E2", "K1", "Abies grandis ssp. test", "Not Reviewed", level="Subspecies"),
        kept_row("E3", "K2", "Bombus vosnesenskii", "Not Reviewed",
                 english="Yellow-faced Bumble Bee"),
    ])
    table, _ = bcsee.group_by_species(kept, NAMES)
    rows = table.set_index("gbif_species_key")

    assert rows.loc["K1", "statuses"] == ["Yellow"]
    assert [e["status"] for e in rows.loc["K1", "entries"]] == ["Yellow", "Not Reviewed"]

    assert rows.loc["K2", "statuses"] == []
    assert [e["status"] for e in rows.loc["K2", "entries"]] == ["Not Reviewed"]
    assert rows.loc["K2", "entries"][0]["common_label"] == "Yellow-faced Bumble Bee"


def write_inputs(folder):
    """Write a tiny bcsee_gbif_match.parquet and bcsee_status.parquet: one
    entry for each BCSEE value, all matched to key K1."""
    values = ["Red", "Yellow", "Not Reviewed", "Unknown", "Accidental", "No Status",
              "Extinct", "Other"]
    match = pd.DataFrame({
        "element_code": [f"E{i}" for i in range(len(values))],
        "scientific_name": [f"Name {i}" for i in range(len(values))],
        "gbif_species_key": "K1", "gbif_usage_key": "K1", "gbif_match_type": "EXACT",
        "gbif_rank": "SPECIES", "gbif_status": "ACCEPTED", "join_ok": True,
    })
    status = pd.DataFrame({
        "element_code": match["element_code"], "bc_list": values,
        "english_name": "", "classification_level": "Species",
    })
    con = duckdb.connect()
    for name, frame in (("bcsee_gbif_match", match), ("bcsee_status", status)):
        # Plain Python text, which DuckDB 0.10.3 (the server's) can read.
        con.register("frame", bcsee.plain_text_columns(frame))
        con.execute(f"COPY frame TO '{folder / name}.parquet' (FORMAT PARQUET)")
        con.unregister("frame")
    con.close()


def test_read_entries_keeps_no_status_and_leaves_out_extinct_and_other(tmp_path):
    """Shown statuses and the four values without a status are kept;
    Extinct and Other are left out."""
    write_inputs(tmp_path)
    kept = bcsee.read_entries(tmp_path)
    assert sorted(kept["bc_list"]) == sorted(
        ["Red", "Yellow", "Not Reviewed", "Unknown", "Accidental", "No Status"])


RELEASE_315557 = {"checklistbank_key": 315557, "alias": "COL26.6 XR"}


def test_release_check_passes_on_the_same_release():
    """No problem when GBIF still uses the release asked for."""
    assert bcsee.release_problem(315557, RELEASE_315557) is None


@pytest.mark.parametrize("expected, current, words", [
    (None, RELEASE_315557, "--col-release was not given"),
    (315557, None, "could not find out"),
    (315557, {"checklistbank_key": 320000, "alias": "COL26.9 XR"}, "not 315557"),
])
def test_release_check_refuses(expected, current, words):
    """No release given, an unknown release, or a different one all stop
    the lookups."""
    assert words in bcsee.release_problem(expected, current)


def test_no_lookup_and_no_check_when_every_name_is_saved(tmp_path):
    """With every name already saved, nothing is checked or asked, so no
    --col-release is needed."""
    names = dict(NAMES)
    bcsee.add_missing_names({"K1", "K2"}, names, tmp_path / "answers.jsonl", None)
    assert not (tmp_path / "answers.jsonl").exists()


def test_missing_names_without_release_stop_before_asking(tmp_path, monkeypatch):
    """A name to look up with no --col-release stops before GBIF is asked
    for any name, and nothing is written."""
    import request_gbif_download
    monkeypatch.setattr(request_gbif_download, "lookup_col_release",
                        lambda when: RELEASE_315557)
    monkeypatch.setattr(bcsee, "ask_key", lambda key: pytest.fail("GBIF was asked"))
    with pytest.raises(ValueError, match="--col-release was not given"):
        bcsee.add_missing_names({"K1", "K9"}, dict(NAMES), tmp_path / "answers.jsonl", None)
    assert not (tmp_path / "answers.jsonl").exists()


def test_written_files_keep_only_shown_statuses(tmp_path):
    """The parquet and CSV written to disk hold only shown statuses in
    statuses (empty for a species without one), while the entries keep
    their own BCSEE values. Checked on the files themselves, since the
    parquet is assembled separately from the table in memory."""
    kept = pd.DataFrame([
        kept_row("E1", "K1", "Abies grandis", "Yellow", english="grand fir"),
        kept_row("E2", "K1", "Abies grandis ssp. test", "Not Reviewed", level="Subspecies"),
        kept_row("E3", "K2", "Bombus vosnesenskii", "Unknown"),
    ])
    table, _ = bcsee.group_by_species(kept, NAMES)
    for column, value in (("status_source", "BCSEE"), ("status_source_date", "2026-05-05"),
                          ("status_pulled_on", "2026-09-26")):
        table[column] = value
    bcsee.write_outputs(table, tmp_path)

    written = duckdb.execute(
        f"SELECT gbif_species_key, statuses, [e.status FOR e IN entries], "
        f"[e.classification_level FOR e IN entries] "
        f"FROM '{tmp_path / bcsee.OUTPUT_NAME}.parquet' ORDER BY 1").fetchall()
    assert written == [("K1", ["Yellow"], ["Yellow", "Not Reviewed"], ["Species", "Subspecies"]),
                       ("K2", [], ["Unknown"], ["Species"])]

    csv = pd.read_csv(tmp_path / f"{bcsee.OUTPUT_NAME}.csv", dtype=str, keep_default_na=False)
    assert dict(zip(csv["gbif_species_key"], csv["statuses"])) == {"K1": "Yellow", "K2": ""}


# bcsee_extra_keys.csv: one BCSEE entry also shown on another key.

NAMES_WITH_EXTRA = {**NAMES, "K3": {"canonicalName": "Abies altera", "name": "Abies altera",
                                     "rank": "SPECIES", "status": "ACCEPTED",
                                     "kingdom": "Plantae"}}
BCSEE_NAMES = {"E1": "Abies grandis", "E3": "Bombus vosnesenskii"}


def extra_rows(*rows):
    """A bcsee_extra_keys.csv table with the given (element_code, extra key,
    BCSEE name, GBIF name of the extra key) rows."""
    return pd.DataFrame([{"element_code": code, "extra_gbif_species_key": key,
                          "bcsee_scientific_name": bcsee_name,
                          "extra_gbif_scientific_name": gbif_name, "reason": "test",
                          "checked_by": "test", "checked_date": "2026-10-16"}
                         for code, key, bcsee_name, gbif_name in rows])


def two_species():
    """The read_entries() table for a Yellow species on K1 and a Not
    Reviewed species on K2."""
    return pd.DataFrame([
        kept_row("E1", "K1", "Abies grandis", "Yellow", english="grand fir"),
        kept_row("E3", "K2", "Bombus vosnesenskii", "Not Reviewed"),
    ])


def test_extra_key_is_applied_and_the_entry_stays_on_its_own_key(tmp_path):
    """The listed entry is copied to the extra key, marked with
    also_applied_from_key, and still sits on its own key with its status,
    also in the parquet written to disk."""
    kept = two_species()
    extra = extra_rows(("E1", "K3", "Abies grandis", "Abies altera"))
    bcsee.check_extra_keys(extra, kept, BCSEE_NAMES, set())
    bcsee.check_extra_key_names(extra, NAMES_WITH_EXTRA)
    kept = bcsee.apply_extra_keys(kept, extra, NAMES_WITH_EXTRA)
    table, _ = bcsee.group_by_species(kept, NAMES_WITH_EXTRA)
    rows = table.set_index("gbif_species_key")

    assert rows.loc["K1", "statuses"] == ["Yellow"]
    assert rows.loc["K1", "entries"][0]["also_applied_from_key"] == ""
    assert rows.loc["K3", "statuses"] == ["Yellow"]
    copy = rows.loc["K3", "entries"][0]
    assert copy["scientific_label"] == "Abies grandis"
    assert copy["common_label"] == "grand fir"
    assert copy["element_codes"] == ["E1"]
    assert copy["matched_species_keys"] == ["K1"]
    assert copy["also_applied_from_key"] == "K1"
    assert rows.loc["K2", "entries"][0]["also_applied_from_key"] == ""

    for column, value in (("status_source", "BCSEE"), ("status_source_date", "2026-05-05"),
                          ("status_pulled_on", "2026-09-26")):
        table[column] = value
    bcsee.write_outputs(table, tmp_path)
    written = duckdb.execute(
        f"SELECT gbif_species_key, statuses, [e.also_applied_from_key FOR e IN entries] "
        f"FROM '{tmp_path / bcsee.OUTPUT_NAME}.parquet' ORDER BY 1").fetchall()
    assert written == [("K1", ["Yellow"], [""]), ("K2", [], [""]), ("K3", ["Yellow"], ["K1"])]


@pytest.mark.parametrize("row, words", [
    (("E9", "K3", "Abies grandis", "Abies altera"), "E9 is not in bcsee_status.parquet"),
    (("E1", "K3", "Abies wrong", "Abies altera"), "E1 is Abies grandis in BCSEE"),
    (("E1", "K1", "Abies grandis", "Abies grandis"), "E1 is already on K1"),
    (("E1", "OLD", "Abies grandis", "Abies altera"), "OLD is an old key"),
])
def test_extra_key_rows_that_do_not_fit_the_entries_stop(row, words):
    """An unknown element_code, a wrong BCSEE name, the entry's own key or
    an old key of the merge stops the run before anything is applied."""
    with pytest.raises(ValueError, match=words):
        bcsee.check_extra_keys(extra_rows(row), two_species(), BCSEE_NAMES, {"OLD"})


@pytest.mark.parametrize("row, words", [
    (("E1", "K9", "Abies grandis", "Abies nowhere"), "GBIF has no name for K9"),
    (("E1", "K3", "Abies grandis", "Abies typo"), "K3 is Abies altera in GBIF"),
])
def test_extra_key_that_is_missing_or_misnamed_stops(row, words):
    """A key GBIF has no name for, or whose name is not the one written in
    the row, stops the run."""
    with pytest.raises(ValueError, match=words):
        bcsee.check_extra_key_names(extra_rows(row), NAMES_WITH_EXTRA)


def test_extra_key_that_already_has_an_entry_stops():
    """A key with a BCSEE entry of its own (here Not Reviewed) is not given
    a second one; the run stops instead of guessing which applies."""
    extra = extra_rows(("E1", "K2", "Abies grandis", "Bombus vosnesenskii"))
    with pytest.raises(ValueError, match="K2 already has a BCSEE entry"):
        bcsee.check_extra_keys(extra, two_species(), BCSEE_NAMES, set())


def test_two_rows_onto_one_key_stop():
    """Two entries listed for the same extra key stop the run, since the
    card would then show two species-level entries."""
    extra = extra_rows(("E1", "K3", "Abies grandis", "Abies altera"),
                       ("E3", "K3", "Bombus vosnesenskii", "Abies altera"))
    with pytest.raises(ValueError, match="K3 is the extra key of more than one row"):
        bcsee.check_extra_keys(extra, two_species(), BCSEE_NAMES, set())


def test_extra_keys_file_must_exist_and_be_complete(tmp_path):
    """A missing file, or a row with a blank value, stops the run; a file
    with only the header gives no rows."""
    path = tmp_path / "bcsee_extra_keys.csv"
    with pytest.raises(FileNotFoundError):
        bcsee.read_extra_keys(path)
    path.write_text(",".join(bcsee.EXTRA_KEY_COLUMNS) + "\n")
    assert bcsee.read_extra_keys(path).empty
    path.write_text(",".join(bcsee.EXTRA_KEY_COLUMNS) + "\nE1,K3,Abies grandis,,r,c,d\n")
    with pytest.raises(ValueError, match="has no extra_gbif_scientific_name"):
        bcsee.read_extra_keys(path)


def test_the_tracked_extra_keys_file_reads():
    """The real bcsee_extra_keys.csv next to the script is well formed."""
    extra = bcsee.read_extra_keys(bcsee.EXTRA_KEYS_FILE)
    assert len(extra) >= 1
