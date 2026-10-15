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
        f"SELECT gbif_species_key, statuses, [e.status FOR e IN entries] "
        f"FROM '{tmp_path / bcsee.OUTPUT_NAME}.parquet' ORDER BY 1").fetchall()
    assert written == [("K1", ["Yellow"], ["Yellow", "Not Reviewed"]),
                       ("K2", [], ["Unknown"])]

    csv = pd.read_csv(tmp_path / f"{bcsee.OUTPUT_NAME}.csv", dtype=str, keep_default_na=False)
    assert dict(zip(csv["gbif_species_key"], csv["statuses"])) == {"K1": "Yellow", "K2": ""}
