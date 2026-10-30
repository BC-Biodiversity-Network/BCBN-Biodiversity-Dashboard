"""Tests for pipeline/lunaris/match_dataset_species.py, on made-up names and
a tiny species table: splitting mixed entries, writing out an abbreviated
genus only when the dataset names the genus, exact matches by scientific
and common name through the site's own name index and search_norm macro,
ambiguous names, group words, genera and families, the typo list, the
rows of one dataset, and the hand-checked name list (only confirmed rows
are used, and a wrong one stops the run)."""

import sys
from pathlib import Path

import pandas as pd
import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "pipeline" / "lunaris"))
import match_dataset_species as ms  # noqa: E402

# A tiny species table and BCSEE file: two salmon, a bear with a merged
# synonym, and two cards with the same name (as in the real table).
SPECIES_SQL = """
    SELECT * FROM (VALUES
        ('K1', 'Oncorhynchus nerka', 'Oncorhynchus', 'Salmonidae', []::STRUCT(key VARCHAR, name VARCHAR, authorship VARCHAR)[]),
        ('K2', 'Oncorhynchus keta', 'Oncorhynchus', 'Salmonidae', []::STRUCT(key VARCHAR, name VARCHAR, authorship VARCHAR)[]),
        ('K3', 'Ursus americanus', 'Ursus', 'Ursidae',
         [{'key': 'OLD', 'name': 'Euarctos americanus', 'authorship': ''}]::STRUCT(key VARCHAR, name VARCHAR, authorship VARCHAR)[]),
        ('K4', 'Mesodinium rubrum', 'Mesodinium', 'Mesodiniidae', []::STRUCT(key VARCHAR, name VARCHAR, authorship VARCHAR)[]),
        ('K5', 'Mesodinium rubrum', 'Mesodinium', 'Mesodiniidae', []::STRUCT(key VARCHAR, name VARCHAR, authorship VARCHAR)[])
    ) AS t(specieskey, col_name, genus, family, merged_names)
"""
ENTRY = "STRUCT(is_species_level BOOLEAN, common_label VARCHAR, scientific_label VARCHAR)"
BCSEE_SQL = f"""
    SELECT * FROM (VALUES
        ('K1', [{{'is_species_level': true, 'common_label': 'Sockeye Salmon',
                 'scientific_label': 'Oncorhynchus nerka'}}]::{ENTRY}[]),
        ('K3', [{{'is_species_level': true, 'common_label': 'American Black Bear',
                 'scientific_label': 'Ursus americanus'}}]::{ENTRY}[])
    ) AS t(gbif_species_key, entries)
"""


@pytest.fixture(scope="module")
def species(tmp_path_factory):
    """The tiny tables as parquet files, opened the way the script opens the real ones."""
    folder = tmp_path_factory.mktemp("species")
    con = ms.duckdb.connect()
    con.execute(f"COPY ({SPECIES_SQL}) TO '{folder / 'species.parquet'}' (FORMAT PARQUET)")
    con.execute(f"COPY ({BCSEE_SQL}) TO '{folder / 'bcsee.parquet'}' (FORMAT PARQUET)")
    opened = ms.open_species(str(folder / "species.parquet"), str(folder / "bcsee.parquet"))
    genera, families = ms.higher_taxa(opened)
    return {"con": opened, "lookup": ms.name_lookup(opened), "genera": genera, "families": families}


@pytest.mark.parametrize("entry, names", [
    ("Gasterosteus aculeatus", ["Gasterosteus aculeatus"]),
    ("picea glauca (white spruce)", ["picea glauca", "white spruce"]),
    ("sockeye (O. nerka) salmon", ["sockeye salmon", "O. nerka"]),
    ("acer negundo (manitoba maple, box-elder)", ["acer negundo", "manitoba maple", "box-elder"]),
    ("black bear / grisly bear", ["black bear", "grisly bear"]),
    ("Cherries, sweet", ["Cherries, sweet"]),
    ("Red-necked and Red Phalaropes", ["Red-necked and Red Phalaropes"]),
])
def test_split_entry(entry, names):
    """Brackets and slashes separate names; commas outside brackets and "and" do not."""
    assert ms.split_entry(entry) == names


def test_names_are_written_with_the_site_search_macro(species):
    """Case, accents, hyphens and apostrophes are written the way the site's search writes them."""
    written = ms.normalise_all(species["con"], ["Sockeye  SALMON", "Douglas-fir", "Lewis's Woodpecker"])
    assert written == {"Sockeye  SALMON": "sockeye salmon", "Douglas-fir": "douglas fir",
                       "Lewis's Woodpecker": "lewiss woodpecker"}


def test_an_abbreviated_genus_is_written_out_only_from_the_same_dataset():
    """O. keta becomes Oncorhynchus keta only when exactly one genus with O starts a name of the dataset."""
    genera_here = ms.dataset_genera(["oncorhynchus nerka", "fish"], {"oncorhynchus": "Oncorhynchus"})
    assert genera_here == {"oncorhynchus"}
    assert ms.expand_abbreviation("O. keta", "o keta", genera_here) == "oncorhynchus keta"
    assert ms.expand_abbreviation("U. americanus", "u americanus", genera_here) is None
    assert ms.expand_abbreviation("O. keta", "o keta", {"oncorhynchus", "odocoileus"}) is None
    assert ms.expand_abbreviation("Oncorhynchus keta", "oncorhynchus keta", genera_here) is None


def classify(species, norm, expanded=None):
    """Classify one written name against the tiny tables."""
    return ms.classify(norm, species["lookup"], species["genera"], species["families"], expanded)


def test_exact_scientific_and_common_matches(species):
    """A card name, a merged synonym and a BCSEE English name each link one species; how is recorded."""
    sci = classify(species, "oncorhynchus nerka")
    assert (sci["status"], sci["specieskey"], sci["matched_field"]) == ("scientific", "K1", "col_name")
    merged = classify(species, "euarctos americanus")
    assert (merged["status"], merged["specieskey"], merged["matched_field"]) == ("scientific", "K3", "merged_name")
    common = classify(species, "american black bear")
    assert (common["status"], common["specieskey"], common["matched_field"]) == ("common", "K3", "english_name")
    expanded = classify(species, "o keta", "oncorhynchus keta")
    assert (expanded["status"], expanded["specieskey"]) == ("expanded_abbreviation", "K2")


def test_no_close_spellings(species):
    """A name one letter off, or a plural, is not linked."""
    for norm in ["oncorhynchus nerk", "american black bears", "sockeye"]:
        assert classify(species, norm)["specieskey"] is None


def test_a_name_of_two_species_is_ambiguous(species):
    """Two cards with one name: not linked, both listed."""
    result = classify(species, "mesodinium rubrum")
    assert (result["status"], result["specieskey"], result["candidates"]) == ("ambiguous", None, ["K4", "K5"])
    assert result["candidate_names"] == ["Mesodinium rubrum", "Mesodinium rubrum"]


def test_group_words_genera_and_families(species):
    """One-word names are group words; a genus or family of the table is marked; longer names are unmatched."""
    fish = classify(species, "fish")
    assert (fish["status"], fish["higher_taxon"]) == ("group_word", None)
    genus = classify(species, "oncorhynchus spp")
    assert (genus["status"], genus["higher_taxon"], genus["higher_taxon_rank"]) == ("group_word", "Oncorhynchus", "genus")
    family = classify(species, "salmonidae")
    assert (family["higher_taxon"], family["higher_taxon_rank"]) == ("Salmonidae", "family")
    assert classify(species, "pacific salmon")["status"] == "unmatched"


def test_the_rows_of_one_dataset(species):
    """Every name after splitting, once, with the entry it came from and the runs that gave it."""
    record = {"id": "oai:x:1", "frontend_group": "bc_data",
              "organisms": ["Oncorhynchus nerka", "chum (O. keta)", "fish", "FISH"],
              "organisms_4b": ["Oncorhynchus nerka", "fish"], "organisms_35b": ["oncorhynchus nerka", "chum (O. keta)"]}
    names = [n for entry in record["organisms"] for n in ms.split_entry(entry)]
    norms = ms.normalise_all(species["con"], names)
    rows = ms.match_rows(record, norms, species["lookup"], species["genera"], species["families"])
    table = pd.DataFrame(rows, columns=ms.COLUMNS)
    assert list(table["name"]) == ["Oncorhynchus nerka", "chum", "O. keta", "fish"]
    assert list(table["status"]) == ["scientific", "group_word", "expanded_abbreviation", "group_word"]
    assert list(table["runs"]) == ["both", "35b", "35b", "4b"]
    assert table.loc[2, ["entry", "expanded_name", "specieskey"]].tolist() == [
        "chum (O. keta)", "oncorhynchus keta", "K2"]


def test_possible_typos_are_listed_not_linked(species):
    """Names one or two letters from a site name are listed with the nearest name; short names are skipped."""
    typos = ms.possible_typos(species["con"], ["oncorhynchus nerk", "american black bears", "fishes", "bear"])
    assert set(typos["name_norm"]) == {"oncorhynchus nerk", "american black bears"}
    nearest = typos[typos["name_norm"] == "american black bears"].iloc[0]
    assert (nearest["distance"], nearest["near_name"], nearest["specieskey"]) == (1, "American Black Bear", "K3")


# --- The hand-checked name list --------------------------------------------

def write_manual(folder, rows):
    """Write a hand-checked list with the given rows (dicts with any of MANUAL_COLUMNS) and return its path."""
    path = folder / "names_manual.csv"
    pd.DataFrame([{column: row.get(column, "") for column in ms.MANUAL_COLUMNS} for row in rows],
                 columns=ms.MANUAL_COLUMNS).to_csv(path, index=False)
    return str(path)


SOCKEYE_ROW = {"name_as_written": "kokanee", "specieskey": "K1", "species_name": "Oncorhynchus nerka",
               "reason": "lake form of sockeye", "datasets_affected": "3"}
CONFIRMED = {"status": "confirmed", "evidence": "test", "checked_by": "Lucia", "checked_date": "2026-10-29"}


def manual_from(species, tmp_path, rows):
    """Read and check a hand-checked list with these rows against the tiny tables."""
    path = write_manual(tmp_path, rows)
    return ms.check_manual_names(species["con"], ms.read_manual_names(path), path)


def test_an_unconfirmed_row_is_ignored(species, tmp_path):
    """A row not confirmed (no status, deferred or rejected, even when checked) is neither checked further nor used."""
    for unconfirmed in [{}, {"status": "deferred"}, {**CONFIRMED, "status": "rejected"},
                        {**CONFIRMED, "status": "deferred"}, {"checked_by": "Lucia", "checked_date": "2026-10-29"}]:
        manual = manual_from(species, tmp_path, [{**SOCKEYE_ROW, **unconfirmed, "specieskey": "NOPE"}])
        assert manual == {}
        assert classify_with(species, "kokanee", manual)["status"] == "group_word"


def classify_with(species, norm, manual):
    """Classify one written name against the tiny tables with a hand-checked list."""
    return ms.classify(norm, species["lookup"], species["genera"], species["families"], manual=manual)


def test_a_confirmed_row_links_with_status_manual(species, tmp_path):
    """A confirmed row links its name to its species when the exact match does not."""
    manual = manual_from(species, tmp_path, [{**SOCKEYE_ROW, **CONFIRMED}])
    result = classify_with(species, "kokanee", manual)
    assert (result["status"], result["specieskey"], result["matched_name"], result["matched_field"]) == (
        "manual", "K1", "Oncorhynchus nerka", "manual")


def test_an_exact_match_comes_before_the_list(species, tmp_path):
    """A confirmed row never changes a name the exact match already links, but can settle an ambiguous one."""
    rows = [{**SOCKEYE_ROW, **CONFIRMED, "name_as_written": "oncorhynchus keta"},
            {**SOCKEYE_ROW, **CONFIRMED, "name_as_written": "mesodinium rubrum", "specieskey": "K4",
             "species_name": "Mesodinium rubrum"}]
    manual = manual_from(species, tmp_path, rows)
    assert classify_with(species, "oncorhynchus keta", manual)["specieskey"] == "K2"
    settled = classify_with(species, "mesodinium rubrum", manual)
    assert (settled["status"], settled["specieskey"]) == ("manual", "K4")


@pytest.mark.parametrize("change, message", [
    ({"specieskey": "NOPE"}, "specieskey 'NOPE' has no card on the site"),
    ({"species_name": "Oncorhynchus keta"}, "is not the card's name 'Oncorhynchus nerka' for K1"),
    ({"name_as_written": "Kokanee"}, "is not written the way the matcher writes names"),
    ({"checked_date": "29 Oct 2026"}, "is not YYYY-MM-DD"),
    ({"status": "maybe"}, "status 'maybe' is not one of"),
    ({"checked_by": ""}, "status confirmed needs checked_by and checked_date"),
    ({"checked_date": ""}, "status confirmed needs checked_by and checked_date"),
])
def test_a_wrong_confirmed_row_stops_the_run(species, tmp_path, change, message):
    """A confirmed row with an unknown key, the wrong card name, an unwritten name, a bad date, an unknown
    status, or status confirmed without who and when stops the run."""
    with pytest.raises(SystemExit, match=message):
        manual_from(species, tmp_path, [{**SOCKEYE_ROW, **CONFIRMED, **change}])


def test_a_name_given_twice_or_a_missing_column_stops_the_run(species, tmp_path):
    """Two confirmed rows for one name, or a file without a column, stop the run."""
    with pytest.raises(SystemExit, match="given more than once"):
        manual_from(species, tmp_path, [{**SOCKEYE_ROW, **CONFIRMED}, {**SOCKEYE_ROW, **CONFIRMED}])
    path = tmp_path / "broken.csv"
    path.write_text("name_as_written,specieskey\nkokanee,K1\n")
    with pytest.raises(SystemExit, match="has no column"):
        ms.read_manual_names(str(path))


def test_a_missing_list_changes_nothing(tmp_path):
    """No file at all reads as an empty list."""
    assert ms.read_manual_names(str(tmp_path / "none.csv")) == []


def test_the_draft_list_has_the_columns_and_is_written_the_matcher_way():
    """The real list next to the script reads, and every name is lower case without punctuation."""
    rows = ms.read_manual_names(ms.DEFAULT_MANUAL)
    assert rows and all(row["name_as_written"] == " ".join(row["name_as_written"].split()) for row in rows)
    assert all(re_ok(row["name_as_written"]) for row in rows)
    assert len({row["name_as_written"] for row in rows}) == len(rows)


def re_ok(name):
    """True if a name only holds lower-case letters, digits and single spaces, as search_norm writes it."""
    return ms.re.fullmatch(r"[a-z0-9]+( [a-z0-9]+)*", name) is not None
