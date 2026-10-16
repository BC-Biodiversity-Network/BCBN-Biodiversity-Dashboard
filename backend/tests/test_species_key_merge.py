"""Tests for the hand-checked species key merge: the rules a manual row of
species_key_merge_manual.csv must pass (make_species_key_merge.py), the same
synonym rule in build_species_tables.py, the merged card, and the BCSEE
status that follows the merge. Cooper's Hawk (VP2T6 Accipiter cooperii
merged into CVW5J Astur cooperii) is the worked example. Everything runs on
tiny files in the test's own folder, except one test that checks the real
manual file against the real Catalogue of Life file when it is there."""

import csv
import re
import sys
from pathlib import Path

import duckdb
import pandas as pd
import pytest

BACKEND = Path(__file__).resolve().parents[1]
# The pipeline scripts are not a package; add their folders to the path the
# way the scripts themselves do.
sys.path.insert(0, str(BACKEND / "pipeline" / "gbif"))
sys.path.insert(0, str(BACKEND / "pipeline" / "bcsee"))
import build_bcsee_status_display as bcsee  # noqa: E402
import build_species_tables as tables  # noqa: E402
import make_species_key_merge as merge  # noqa: E402

REAL_COL = BACKEND / "data" / "col" / "315557" / "col_names_315557.parquet"

# A tiny Catalogue of Life file: (id, parent_id, status, scientific_name,
# authorship). Cooper's Hawk as in release 315557, a plant genus move whose
# combining author changed, a name listed under another species with
# another author, an ambiguous synonym, and an accepted key with no synonym.
COL_ROWS = [
    ("VP2T6", "627QR", "accepted", "Accipiter cooperii", "(Bonaparte, 1828)"),
    ("CVW5J", "CTQ6H", "accepted", "Astur cooperii", "(Bonaparte, 1828)"),
    ("64FV9", "CVW5J", "synonym", "Accipiter cooperii", "(Bonaparte, 1828)"),
    ("VL7BH", "G1", "accepted", "Rhytidiadelphus triquetrus", "(Hedw.) Warnst."),
    ("9LM7J", "G2", "accepted", "Hylocomiadelphus triquetrus", "(Hedw.) Ochyra & Stebel"),
    ("SYN2", "9LM7J", "synonym", "Rhytidiadelphus triquetrus", "(Hedw.) Warnst."),
    ("LN", "G3", "accepted", "Lasius niger", "(Linnaeus, 1758)"),
    ("TC", "G4", "accepted", "Tetramorium caespitum", "(Linnaeus, 1758)"),
    ("SYN3", "TC", "synonym", "Lasius niger", "auct."),
    ("AMB", "G5", "accepted", "Bombus alter", "Smith, 1900"),
    ("BK", "G6", "accepted", "Bombus kept", "Smith, 1900"),
    ("SYN4", "BK", "ambiguous synonym", "Bombus alter", "Smith, 1900"),
    ("PROV", "G7", "provisionally accepted", "Bombus provisional", "Smith, 1900"),
    ("SYN5", "PROV", "synonym", "Bombus alter", "Smith, 1900"),
]


def write_parquet(frame, path):
    """Write a small table as Parquet, with text in the plain format any
    DuckDB version reads."""
    con = duckdb.connect()
    con.register("frame", bcsee.plain_text_columns(frame))
    con.execute(f"COPY frame TO '{path}' (FORMAT PARQUET)")
    con.close()


@pytest.fixture
def col_names(tmp_path):
    """The tiny Catalogue of Life file, in the columns fetch_col_names.py
    writes that the merge reads."""
    frame = pd.DataFrame(COL_ROWS, columns=["id", "parent_id", "status", "scientific_name",
                                            "authorship"])
    frame["rank"] = "species"
    path = tmp_path / "col_names.parquet"
    write_parquet(frame, path)
    return path


def manual_row(old_key="VP2T6", kept_key="CVW5J", synonym_id="64FV9", **changes):
    """One manual row for old_key -> kept_key, filled in from COL_ROWS, with
    any column replaced by changes."""
    names = {row[0]: row for row in COL_ROWS}
    row = {"old_key": old_key, "kept_key": kept_key,
           "old_name": names[old_key][3], "old_authorship": names[old_key][4],
           "kept_name": names[kept_key][3], "kept_authorship": names[kept_key][4],
           "col_synonym_id": synonym_id, "reason": "test", "checked_by": "test",
           "checked_date": "2026-10-16"}
    row.update(changes)
    return row


def test_coopers_hawk_row_passes(col_names):
    """VP2T6 Accipiter cooperii into CVW5J Astur cooperii passes every rule."""
    by_id = merge.check_manual([manual_row()], str(col_names), "manual.csv")
    assert set(by_id) == {"VP2T6", "CVW5J"}


def test_plant_genus_move_with_a_new_combining_author_passes(col_names):
    """The two keys' own authorships are not compared: a plant moved to
    another genus gets a new combining author, and still passes."""
    merge.check_manual([manual_row("VL7BH", "9LM7J", "SYN2")], str(col_names), "manual.csv")


@pytest.mark.parametrize("row, words", [
    (manual_row("VP2T6", "TC", "64FV9"),
     "does not list Accipiter cooperii (Bonaparte, 1828) as a synonym of TC"),
    (manual_row("LN", "TC", "SYN3"), "only with another authorship"),
    (manual_row("AMB", "BK", "SYN4"), "only as an ambiguous synonym"),
    (manual_row(synonym_id="WRONG"), "not col_synonym_id WRONG"),
    (manual_row("AMB", "PROV", "SYN5"), "PROV is provisionally accepted, not accepted"),
    (manual_row(old_name="Accipiter cooperi"), "old_name is 'Accipiter cooperi'"),
    (manual_row(kept_authorship="(Bonaparte, 1829)"), "kept_authorship is '(Bonaparte, 1829)'"),
])
def test_rows_that_break_a_rule_are_refused(col_names, row, words):
    """Not a synonym of the kept key; a synonym with the same name but
    another author than the old key (Lasius niger under Tetramorium
    caespitum); only an ambiguous synonym; the wrong synonym id; a kept key
    that is not accepted; and names or authorships that are not COL's."""
    with pytest.raises(SystemExit, match=re.escape(words)):
        merge.check_manual([row], str(col_names), "manual.csv")


@pytest.mark.parametrize("column", ["reason", "checked_by", "checked_date"])
def test_rows_without_reason_or_checker_or_date_are_refused(col_names, column):
    """A row must say why, who checked it and when."""
    with pytest.raises(SystemExit, match=f"{column} is blank"):
        merge.check_manual([manual_row(**{column: ""})], str(col_names), "manual.csv")


def test_manual_rows_need_the_col_file():
    """Rows to check and no Catalogue of Life file stops the run."""
    with pytest.raises(SystemExit, match="--col-names"):
        merge.check_manual([manual_row()], None, "manual.csv")


@pytest.mark.skipif(not REAL_COL.exists(), reason="the real Catalogue of Life file is not here")
def test_the_tracked_manual_file_passes_against_the_real_col_file():
    """Every row of the real species_key_merge_manual.csv passes the rules
    against the real Catalogue of Life release 315557."""
    rows = merge.read_manual(merge.DEFAULT_MANUAL)
    assert any(r["old_key"] == "VP2T6" and r["kept_key"] == "CVW5J" for r in rows)
    merge.check_manual(rows, str(REAL_COL), merge.DEFAULT_MANUAL)


def write_csv(path, rows):
    """Write a list of dicts as a CSV with the first row's columns."""
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_merge_list_holds_the_manual_row(tmp_path, col_names, monkeypatch):
    """A full run writes the automatic pair and the manual row; the manual
    row has no record counts and says which synonym it rests on."""
    lookup = tmp_path / "duplicate_species_lookup.csv"
    levels = {"kingdom": "Animalia", "phylum": "Arthropoda", "class": "Insecta",
              "order": "Hymenoptera", "family": "Apidae", "genus": "Bombus",
              "species": "Bombus same"}
    write_csv(lookup, [{**levels, "specieskey": key, "record_count": count,
                        "status": status, "authorship": "Smith, 1900", "error": ""}
                       for key, count, status in (("A1", 10, "accepted"),
                                                  ("A2", 2, "provisionally accepted"))])
    manual = tmp_path / "manual.csv"
    write_csv(manual, [manual_row()])
    monkeypatch.setattr(sys, "argv", ["make_species_key_merge.py", "--lookup", str(lookup),
                                      "--manual", str(manual), "--col-names", str(col_names)])
    assert merge.main() == 0
    with open(tmp_path / merge.MERGE_NAME, newline="", encoding="utf-8") as handle:
        rows = {r["old_key"]: r for r in csv.DictReader(handle)}
    assert rows["A2"]["kept_key"] == "A1"
    assert rows["VP2T6"]["kept_key"] == "CVW5J"
    assert rows["VP2T6"]["species"] == "Astur cooperii"
    assert rows["VP2T6"]["old_record_count"] == ""
    assert rows["VP2T6"]["merge_reason"].startswith("manual: COL synonym 64FV9")


def write_occurrence(path, records):
    """Write a tiny bc_occurrence.parquet from (specieskey, species,
    datasetkey, year) records, all in one bird family and one hexagon."""
    frame = pd.DataFrame([
        {"gbifid": str(i), "specieskey": key, "species": species, "datasetkey": dataset,
         "year": year, "kingdom": "Animalia", "phylum": "Chordata", "class": "Aves",
         "order": "Accipitriformes", "family": "Accipitridae", "genus": species.split()[0],
         "decimallatitude": 49.0 + i / 10, "decimallongitude": -123.0 - i / 10,
         "h3_r4": "84a", "h3_r5": "85a", "h3_r6": "86a", "h3_r7": "87a"}
        for i, (key, species, dataset, year) in enumerate(records)])
    write_parquet(frame, path)


COOPERS_RECORDS = [("CVW5J", "Astur cooperii", "D1", 2020), ("CVW5J", "Astur cooperii", "D1", 2021),
                   ("CVW5J", "Astur cooperii", "D2", 2022), ("VP2T6", "Accipiter cooperii", "D3", 1887)]


def test_species_tables_accept_a_synonym_merge_and_refuse_other_name_changes(tmp_path, col_names):
    """Two keys with different species names are merged only when the old
    name is a Catalogue of Life synonym of the kept key."""
    occurrence = tmp_path / "bc_occurrence.parquet"
    write_occurrence(occurrence, COOPERS_RECORDS + [("TC", "Tetramorium caespitum", "D1", 2020),
                                                   ("LN", "Lasius niger", "D1", 2020)])
    con = duckdb.connect()
    tables.check_merge_species(con, str(occurrence), [("VP2T6", "CVW5J", "manual")],
                               "merge.csv", str(col_names))
    with pytest.raises(SystemExit, match="LN -> TC: species names differ"):
        tables.check_merge_species(con, str(occurrence), [("LN", "TC", "manual")],
                                   "merge.csv", str(col_names))


def test_merged_card_sums_both_keys_and_names_the_old_one(tmp_path, col_names):
    """After the merge VP2T6 has no card; CVW5J has the records of both
    keys, VP2T6 in merged_keys, and VP2T6's own name and authorship in
    merged_names."""
    occurrence = tmp_path / "bc_occurrence.parquet"
    write_occurrence(occurrence, COOPERS_RECORDS)
    images = tmp_path / "bc_species_image.parquet"
    write_parquet(pd.DataFrame([{
        "gbifid": "1", "specieskey": "CVW5J", "identifier": "https://example.org/a.jpg",
        "references": "https://example.org/a", "creator": "A. Person", "rightsholder": "",
        "license_code": "CC0_1_0", "license_rank": 1.0, "license_source": "media"}]), images)
    datasets = tmp_path / "bc_datasets.parquet"
    write_parquet(pd.DataFrame({"datasetkey": ["D1", "D2", "D3"],
                                "title": ["One", "Two", "Three"]}), datasets)
    card_path = tmp_path / "species_card_gbif.parquet"

    con = duckdb.connect()
    tables.register_records(con, str(occurrence), [("VP2T6", "CVW5J", "manual")])
    tables.build_species_card(con, str(images), str(datasets), str(col_names), "test release",
                              str(card_path))
    cards = duckdb.execute(
        f"SELECT specieskey, col_name, genus, record_count, first_year, n_datasets, "
        f"merged_keys, merged_record_count, merged_names FROM '{card_path}'").fetchall()
    assert len(cards) == 1
    key, name, genus, records, first_year, n_datasets, keys, moved, names = cards[0]
    assert (key, name, genus) == ("CVW5J", "Astur cooperii", "Astur")
    assert (records, first_year, n_datasets, moved) == (4, 1887, 3, 1)
    assert keys == ["VP2T6"]
    assert names == [{"key": "VP2T6", "name": "Accipiter cooperii",
                      "authorship": "(Bonaparte, 1828)"}]


# BCSEE after the merge: the status follows the merge.

COOPERS_NAMES = {
    "VP2T6": {"canonicalName": "Accipiter cooperii", "name": "Accipiter cooperii (Bonaparte, 1828)",
              "rank": "SPECIES", "status": "ACCEPTED", "kingdom": "Animalia"},
    "CVW5J": {"canonicalName": "Astur cooperii", "name": "Astur cooperii (Bonaparte, 1828)",
              "rank": "SPECIES", "status": "ACCEPTED", "kingdom": "Animalia"},
}


def write_coopers_bcsee(folder):
    """Write a tiny bcsee_gbif_match.parquet and bcsee_status.parquet with
    BCSEE's Cooper's Hawk entry matched to VP2T6, as it is."""
    match = pd.DataFrame([{
        "element_code": "ABNKC12040", "scientific_name": "Accipiter cooperii",
        "gbif_species_key": "VP2T6", "gbif_usage_key": "VP2T6", "gbif_match_type": "EXACT",
        "gbif_rank": "SPECIES", "gbif_status": "ACCEPTED", "join_ok": True}])
    status = pd.DataFrame([{
        "element_code": "ABNKC12040", "scientific_name": "Accipiter cooperii",
        "bc_list": "Yellow", "english_name": "Cooper's Hawk", "classification_level": "Species"}])
    write_parquet(match, folder / "bcsee_gbif_match.parquet")
    write_parquet(status, folder / "bcsee_status.parquet")


def test_after_the_merge_cvw5j_has_yellow_once_and_vp2t6_no_row(tmp_path):
    """With VP2T6 merged into CVW5J and no extra keys, CVW5J has the Yellow
    status exactly once, as the whole species, and VP2T6 has no row."""
    write_coopers_bcsee(tmp_path)
    kept = bcsee.read_entries(tmp_path, [("VP2T6", "CVW5J", "manual")])
    no_extra = pd.DataFrame(columns=bcsee.EXTRA_KEY_COLUMNS)
    bcsee.check_extra_keys(no_extra, kept, {"ABNKC12040": "Accipiter cooperii"}, {"VP2T6"})
    kept = bcsee.apply_extra_keys(kept, no_extra, COOPERS_NAMES)
    table, _ = bcsee.group_by_species(kept, COOPERS_NAMES)

    assert list(table["gbif_species_key"]) == ["CVW5J"]
    row = table.iloc[0]
    assert row["statuses"] == ["Yellow"]
    assert [e["status"] for e in row["entries"]] == ["Yellow"]
    assert not row["has_separate_bcsee_species"]
    assert row["entries"][0]["is_species_level"]
    assert row["entries"][0]["common_label"] == "Cooper's Hawk"
    assert row["matched_species_keys"] == ["VP2T6"]


def test_the_old_extra_key_row_cannot_come_back_after_the_merge(tmp_path):
    """The extra-key row ABNKC12040 -> CVW5J is refused once the merge has
    put the entry on CVW5J, so the status can never be shown twice."""
    write_coopers_bcsee(tmp_path)
    kept = bcsee.read_entries(tmp_path, [("VP2T6", "CVW5J", "manual")])
    extra = pd.DataFrame([{"element_code": "ABNKC12040", "extra_gbif_species_key": "CVW5J",
                           "bcsee_scientific_name": "Accipiter cooperii",
                           "extra_gbif_scientific_name": "Astur cooperii", "reason": "r",
                           "checked_by": "c", "checked_date": "d"}])
    with pytest.raises(ValueError, match="ABNKC12040 is already on CVW5J"):
        bcsee.check_extra_keys(extra, kept, {"ABNKC12040": "Accipiter cooperii"}, {"VP2T6"})


REAL_API = BACKEND / "data" / "api"


@pytest.mark.skipif(not (REAL_API / "species_card_gbif.parquet").exists(),
                    reason="the real data is not in backend/data/api")
def test_real_data_has_one_coopers_hawk_with_yellow_once():
    """In the real data, VP2T6 has no card and no BCSEE row of its own; CVW5J
    lists it as merged, under its old name, and has Yellow exactly once."""
    con = duckdb.connect()
    card = f"read_parquet('{REAL_API / 'species_card_gbif.parquet'}')"
    status = f"read_parquet('{REAL_API / 'bcsee_status_by_gbif_species.parquet'}')"
    assert con.execute(f"SELECT count(*) FROM {card} WHERE specieskey = 'VP2T6'").fetchone()[0] == 0
    keys, names = con.execute(
        f"SELECT merged_keys, [m.name FOR m IN merged_names] FROM {card} "
        f"WHERE specieskey = 'CVW5J'").fetchone()
    assert keys == ["VP2T6"] and names == ["Accipiter cooperii"]
    assert con.execute(f"SELECT count(*) FROM {status} "
                       f"WHERE gbif_species_key = 'VP2T6'").fetchone()[0] == 0
    statuses, entries = con.execute(
        f"SELECT statuses, [e.status FOR e IN entries] FROM {status} "
        f"WHERE gbif_species_key = 'CVW5J'").fetchone()
    assert statuses == ["Yellow"] and entries == ["Yellow"]
