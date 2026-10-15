"""Tests for pipeline/gbif/build_species_image.py: only images that pass the
credit check are picked, the ranking among them is unchanged, the checks
and the comparison with the current table count correctly, and the table is
only replaced when every check passes.

The test species (all made up):
    S1  a CC0 image with no creator (rank 1, fails) and a CC BY image that
        passes (rank 2): the CC BY one is picked
    S2  two passing CC0 images, from 2020 and 2023: the 2023 one is picked
    S3  a CC0 image with no link to the original: no image
    S4  a Beaty image with no licence (rank 8), otherwise fully credited:
        no image, because the licence is required
    S5  no media at all: no image
    S6  a fully credited sound recording, not a StillImage: no image
"""

import sys
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd
import pytest

# The pipeline scripts are not a package; add their folder to the path the
# way the scripts themselves do.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline" / "gbif"))
import build_species_image as species_image  # noqa: E402

BEATY_KEY = species_image.BEATY_DATASET_KEYS[0]


def write_parquet(frame, path):
    """Write a pandas table to a parquet file with DuckDB. Text columns are
    turned into plain Python text first, because DuckDB 0.10.3 (the
    server's) cannot read pandas 3's newer text format."""
    frame = frame.copy()
    for column in frame.columns:
        if pd.api.types.is_string_dtype(frame[column].dtype):
            values = frame[column].astype(object)
            frame[column] = values.where(values.notna(), None)
    con = duckdb.connect()
    con.register("frame", frame)
    con.execute(f"COPY frame TO '{path}' (FORMAT PARQUET)")
    con.close()


def media(gbifid, order, licence_code, link="https://example.org/page", creator="A. Person",
          kind="StillImage", licence_text="some licence"):
    """One bc_media row. licence_text is the raw licence value; None means
    the record gave no licence at all."""
    return {"gbifid": gbifid, "media_order": order, "type": kind,
            "identifier": f"https://example.org/{gbifid}_{order}.jpg",
            "references": link, "creator": creator, "rightsholder": creator,
            "license": licence_text, "license_code": licence_code}


def write_tables(folder):
    """Write bc_occurrence, bc_media and bc_datasets for the test species
    into folder."""
    occurrence = pd.DataFrame([
        {"gbifid": "1", "specieskey": "S1", "species": "Species one", "datasetkey": "d",
         "eventdate": datetime(2021, 1, 1), "basisofrecord": "HUMAN_OBSERVATION"},
        {"gbifid": "2", "specieskey": "S1", "species": "Species one", "datasetkey": "d",
         "eventdate": datetime(2019, 1, 1), "basisofrecord": "HUMAN_OBSERVATION"},
        {"gbifid": "3", "specieskey": "S2", "species": "Species two", "datasetkey": "d",
         "eventdate": datetime(2020, 1, 1), "basisofrecord": "HUMAN_OBSERVATION"},
        {"gbifid": "4", "specieskey": "S2", "species": "Species two", "datasetkey": "d",
         "eventdate": datetime(2023, 1, 1), "basisofrecord": "HUMAN_OBSERVATION"},
        {"gbifid": "5", "specieskey": "S3", "species": "Species three", "datasetkey": "d",
         "eventdate": datetime(2022, 1, 1), "basisofrecord": "HUMAN_OBSERVATION"},
        {"gbifid": "6", "specieskey": "S4", "species": "Species four", "datasetkey": BEATY_KEY,
         "eventdate": datetime(2022, 1, 1), "basisofrecord": "PRESERVED_SPECIMEN"},
        {"gbifid": "7", "specieskey": "S5", "species": "Species five", "datasetkey": "d",
         "eventdate": datetime(2022, 1, 1), "basisofrecord": "HUMAN_OBSERVATION"},
        {"gbifid": "8", "specieskey": "S6", "species": "Species six", "datasetkey": "d",
         "eventdate": datetime(2022, 1, 1), "basisofrecord": "HUMAN_OBSERVATION"},
    ])
    images = pd.DataFrame([
        media("1", 1, "CC0_1_0", creator=None),          # S1, best licence, no creator
        media("2", 1, "CC_BY_4_0"),                      # S1, passes
        media("3", 1, "CC0_1_0"),                        # S2, passes, 2020
        media("4", 1, "CC0_1_0"),                        # S2, passes, 2023
        media("5", 1, "CC0_1_0", link="  "),             # S3, no link
        media("6", 1, None, licence_text=None),          # S4, Beaty, no licence
        media("8", 1, "CC0_1_0", kind="Sound"),          # S6, not a StillImage
    ])
    datasets = pd.DataFrame([
        {"datasetkey": "d", "title": "Test", "publisher": "Test", "bc_record_count": 7},
        {"datasetkey": BEATY_KEY, "title": "Beaty", "publisher": "UBC", "bc_record_count": 1},
    ])
    write_parquet(occurrence, folder / "bc_occurrence.parquet")
    write_parquet(images, folder / "bc_media.parquet")
    write_parquet(datasets, folder / "bc_datasets.parquet")


def build(folder, out_name="new.parquet"):
    """Run the selection on the test tables and return (connection, path
    of the new table)."""
    con = duckdb.connect()
    species_image.find_beaty_datasets(con, folder / "bc_datasets.parquet")
    out = folder / out_name
    species_image.build_species_image(con, folder / "bc_occurrence.parquet",
                                      folder / "bc_media.parquet", out)
    return con, out


def picks(path):
    """Return the picked image of each species as {specieskey: gbifid}."""
    return dict(duckdb.execute(
        f"SELECT specieskey, gbifid FROM read_parquet('{path}')").fetchall())


def write_previous(folder, chosen):
    """Write a 'current' bc_species_image.parquet holding, for each species,
    the given media row (specieskey: gbifid), copied from bc_media."""
    con = duckdb.connect()
    values = ", ".join(f"('{key}', '{gbifid}')" for key, gbifid in chosen.items())
    con.execute(f"""
        COPY (
            SELECT c.specieskey, m.gbifid, m.media_order, m.identifier, m."references",
                   m.creator, m.rightsholder, m.license_code, 1 AS license_rank
            FROM (VALUES {values}) AS c(specieskey, gbifid)
            JOIN read_parquet('{folder / "bc_media.parquet"}') AS m USING (gbifid)
        ) TO '{folder / "bc_species_image.parquet"}' (FORMAT PARQUET)""")
    con.close()


@pytest.fixture
def tables(tmp_path):
    """A folder with the test tables."""
    write_tables(tmp_path)
    return tmp_path


def test_picks_only_passing_images_with_the_current_ranking(tables):
    """S1 gets its passing CC BY image over the failing CC0 one, S2 its most
    recent passing image, and the species with no passing StillImage get
    none."""
    _, out = build(tables)
    assert picks(out) == {"S1": "2", "S2": "4"}


def test_checks_pass_on_a_correct_table(tables):
    """Every check passes on what build_species_image writes."""
    con, out = build(tables)
    checks = species_image.check_species_image(
        con, tables / "bc_occurrence.parquet", tables / "bc_media.parquet", out)
    assert all(ok for _, ok in checks), checks


def test_check_catches_a_failing_image(tables):
    """A table holding an image that fails the credit check is caught."""
    write_previous(tables, {"S1": "1"})   # gbifid 1 has no creator
    con = duckdb.connect()
    checks = dict(species_image.check_species_image(
        con, tables / "bc_occurrence.parquet", tables / "bc_media.parquet",
        tables / "bc_species_image.parquet"))
    failed = [description for description, ok in checks.items() if not ok]
    assert any("every picked image passes" in d for d in failed)


def test_species_without_image_reasons(tables):
    """Each species without an image is put under the right reason."""
    con, out = build(tables)
    reasons = dict(species_image.species_without_image(
        con, tables / "bc_occurrence.parquet", tables / "bc_media.parquet", out))
    assert reasons == {
        "images with a readable licence, but none also with an image URL, a link to "
        "the original and a creator": 1,     # S3
        "only images with no license": 1,    # S4
        "no still images at all": 2,         # S5, S6
    }


def test_compare_counts_gained_lost_changed(tables):
    """Compared with a current table where S1 had its failing image and S3
    had one: S1 changed, S2 gained, S3 lost, and no passing image was lost
    or changed."""
    write_previous(tables, {"S1": "1", "S3": "5"})
    con, out = build(tables)
    change = species_image.compare_with_previous(con, out, tables / "bc_species_image.parquet")
    assert change == {"gained": 1, "lost": 1, "changed": 1, "unchanged": 0,
                      "lost_passing": 0, "changed_passing": 0}


def test_compare_flags_a_passing_image_that_changes(tables):
    """If the current image passes but the new pick differs (S2 had its
    older passing image), it is counted, so the run would stop."""
    write_previous(tables, {"S2": "3"})
    con, out = build(tables)
    change = species_image.compare_with_previous(con, out, tables / "bc_species_image.parquet")
    assert change["changed_passing"] == 1


def test_species_after_merge(tables):
    """With S2 merged into S1, the two picked images make one card with an
    image."""
    (tables / "merge.csv").write_text("old_key,kept_key,species\nS2,S1,Species one\n")
    con, out = build(tables)
    assert species_image.species_after_merge(con, out, tables / "merge.csv") == 1


def run_main(monkeypatch, tables):
    """Run the script's main() on the test tables and return its exit code."""
    monkeypatch.setattr(sys, "argv", ["build_species_image.py", "--tables", str(tables),
                                      "--memory-limit", "1GB", "--threads", "1"])
    return species_image.main()


def test_main_replaces_the_table_when_checks_pass(monkeypatch, tables):
    """A clean run replaces bc_species_image.parquet and leaves no
    temporary file."""
    write_previous(tables, {"S1": "1", "S2": "4"})
    assert run_main(monkeypatch, tables) == 0
    assert picks(tables / "bc_species_image.parquet") == {"S1": "2", "S2": "4"}
    assert not (tables / "bc_species_image.tmp.parquet").exists()


def test_main_keeps_the_table_when_a_check_fails(monkeypatch, tables):
    """When a passing image would change, the current table is left as it
    was and the new one stays under its temporary name."""
    write_previous(tables, {"S2": "3"})
    assert run_main(monkeypatch, tables) == 1
    assert picks(tables / "bc_species_image.parquet") == {"S2": "3"}
    assert (tables / "bc_species_image.tmp.parquet").exists()
