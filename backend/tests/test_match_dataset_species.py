"""Tests for pipeline/lunaris/match_dataset_species.py, on made-up names and
a tiny species table: splitting mixed entries, writing out an abbreviated
genus only when the dataset names the genus, exact matches by scientific
and common name through the site's own name index and search_norm macro,
ambiguous names, group words, genera and families, the typo list, and the
rows of one dataset."""

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
