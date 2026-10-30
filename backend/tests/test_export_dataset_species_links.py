"""Tests for pipeline/lunaris/export_dataset_species_links.py, on a made-up
match table: only exact matches and confirmed hand-checked rows carry a
species, every name keeps its place in the record, and a match table
that is older than the hand-checked list or the datasets is refused."""

import sys
from pathlib import Path

import pandas as pd

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "pipeline" / "lunaris"))
import export_dataset_species_links as ex  # noqa: E402


def match(dataset, name, norm, status, key=None, expanded=None):
    """Return one made-up row of the match table."""
    return {"dataset_id": dataset, "name": name, "name_norm": norm, "status": status, "specieskey": key,
            "matched_name": "Matched" if key else None, "matched_field": "col_name" if key else None,
            "expanded_name": expanded}


TABLE = pd.DataFrame([
    match("d1", "fish", "fish", "group_word"),
    match("d1", "Oncorhynchus nerka", "oncorhynchus nerka", "scientific", "K1"),
    match("d1", "O. keta", "o keta", "expanded_abbreviation", "K2", "oncorhynchus keta"),
    match("d2", "sockeyes", "sockeyes", "manual", "K1"),
    match("d2", "O. tschawytscha", "o tschawytscha", "manual", "K3", "oncorhynchus tschawytscha"),
    match("d2", "kokanee", "kokanee", "unmatched"),
])


def manual_row(name, key, status="confirmed"):
    """Return one made-up row of the hand-checked list."""
    checked = status != "deferred"
    return {"name_as_written": name, "specieskey": key, "status": status,
            "checked_by": "Lucia" if checked else "", "checked_date": "2026-10-29" if checked else ""}


MANUAL = [manual_row("sockeyes", "K1"), manual_row("oncorhynchus tschawytscha", "K3"),
          manual_row("kokanee", "K1", "deferred"), manual_row("daphnia schodleri", "K9", "rejected")]


def test_only_linked_names_carry_a_species():
    """Group words and unmatched names keep their name and place but no species."""
    links = ex.build_links(TABLE)
    assert list(links.columns) == ex.COLUMNS
    assert links["position"].tolist() == [0, 1, 2, 0, 1, 2]
    assert [key if pd.notna(key) else None for key in links["specieskey"]] == [None, "K1", "K2", "K1", "K3", None]
    assert links.loc[0, ["name_as_written", "match_status"]].tolist() == ["fish", "group_word"]
    assert links.loc[0, ["matched_name", "matched_field"]].isna().all()


def test_an_up_to_date_table_passes():
    """Manual links are confirmed rows (also with the genus written out); deferred rows stay unlinked."""
    assert ex.check_manual_links(TABLE, MANUAL) == []


def test_a_manual_link_that_is_no_longer_confirmed_is_refused():
    """The list deferred the row after the matcher ran."""
    rows = [manual_row("sockeyes", "K1", "deferred"), manual_row("oncorhynchus tschawytscha", "K3")]
    assert ex.check_manual_links(TABLE, rows) == [
        "'sockeyes' is linked from the hand-checked list, but is not a confirmed row there now"]


def test_a_confirmed_row_without_who_or_when_does_not_count():
    """status confirmed alone is not enough."""
    rows = [dict(manual_row("sockeyes", "K1"), checked_date=""), manual_row("oncorhynchus tschawytscha", "K3")]
    assert len(ex.check_manual_links(TABLE, rows)) == 1


def test_a_manual_link_to_another_species_or_a_newly_confirmed_name_is_refused():
    """The list changed the species, or confirmed a name the table has not linked."""
    rows = [manual_row("sockeyes", "K2"), manual_row("oncorhynchus tschawytscha", "K3"),
            manual_row("kokanee", "K1")]
    assert ex.check_manual_links(TABLE, rows) == [
        "'kokanee' is a confirmed row of the hand-checked list, but the table does not link it",
        "'sockeyes' is linked to K1, but the confirmed row says K2"]


def test_the_names_must_be_those_of_the_datasets():
    """A repeated name written the same way is left out, as the matcher does; any other difference is refused."""
    table = pd.DataFrame([match("d1", "O. kisutch", "o kisutch", "unmatched"),
                          match("d1", "coho", "coho", "unmatched"),
                          match("d9", "fish", "fish", "group_word")])
    written = {"O. kisutch": "o kisutch", "O.kisutch": "o kisutch", "coho": "coho", "chum": "chum", "": ""}
    datasets = pd.DataFrame({"id": ["d1"], "organisms": [["O. kisutch", "O.kisutch", "coho"]]})
    assert ex.check_dataset_names(table, datasets, written) == ["d9: in the match table but not in the datasets"]
    datasets = pd.DataFrame({"id": ["d1"], "organisms": [["O. kisutch", "chum"]]})
    assert ex.check_dataset_names(table.iloc[:2], datasets, written) == [
        "d1: its names are not those of the match table"]


def test_names_are_written_the_search_way():
    """The check uses the site's own search_norm macro."""
    assert ex.search_way(["O.kisutch", "Douglas-fir"]) == {"O.kisutch": "o kisutch", "Douglas-fir": "douglas fir"}
