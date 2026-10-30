"""Tests for pipeline/lunaris/check_dataset_species_names.py, with made-up
species facts and GBIF answers (no calls to GBIF): which kind each row is,
the plural and common-name rules, the GBIF rules for scientific spellings
(confirmed, rejected, deferred), the review list, and the answer cache."""

import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "pipeline" / "lunaris"))
import check_dataset_species_names as ck  # noqa: E402

FACTS = {
    "WOLF": {"col_name": "Canis lupus", "kingdom": "Animalia", "english": ["Grey Wolf"], "entry_english": [],
             "scientific": ["canis lupus"]},
    "SEAL": {"col_name": "Phoca vitulina", "kingdom": "Animalia", "english": ["Harbour Seal"], "entry_english": [],
             "scientific": ["phoca vitulina"]},
    "WOODPECKER": {"col_name": "Melanerpes lewis", "kingdom": "Animalia", "english": ["Lewis's Woodpecker"],
                   "entry_english": [], "scientific": ["melanerpes lewis"]},
    "GRASS": {"col_name": "Phalaris arundinacea", "kingdom": "Plantae", "english": [],
              "entry_english": ["reed canarygrass"], "scientific": ["phalaris arundinacea"]},
    "FROG": {"col_name": "Lithobates catesbeianus", "kingdom": "Animalia", "english": ["American Bullfrog"],
             "entry_english": [], "scientific": ["lithobates catesbeianus"]},
}


def row(name, key, reason="Spelling: test"):
    """Return a made-up row of the hand-checked list."""
    return {"name_as_written": name, "specieskey": key, "species_name": FACTS[key]["col_name"], "reason": reason,
            "datasets_affected": "1", "status": "", "evidence": "", "checked_by": "", "checked_date": ""}


def test_row_kinds():
    """The review list first, then plural rows, then common or scientific by the nearest name."""
    assert ck.row_kind(row("kokanee", "WOLF"), FACTS) == "review"
    assert ck.row_kind(row("grey wolves", "WOLF", "Plural: test"), FACTS) == "plural"
    assert ck.row_kind(row("gray wolf", "WOLF"), FACTS) == "common"
    assert ck.row_kind(row("canis lupis", "WOLF"), FACTS) == "scientific"


@pytest.mark.parametrize("name, key, status, evidence", [
    ("gray wolf", "WOLF", "confirmed", "BCSEE English name 'Grey Wolf'; differs only by US spelling (gray for grey)"),
    ("harbor seals", "SEAL", "confirmed", "US spelling (harbor for harbour), a final s"),
    ("lewis woodpecker", "WOODPECKER", "deferred", "by more than spaces"),
    ("reed canary grass", "GRASS", "deferred", "on an entry below the species"),
    ("grey wolve", "WOLF", "deferred", "by more than spaces"),
])
def test_common_names(name, key, status, evidence):
    """Only spaces, hyphens, apostrophes, case, a final s/es and US spelling are accepted."""
    found = ck.check_common(name, FACTS[key])
    assert found[0] == status and evidence in found[1]


def test_spaces_and_hyphens_are_named():
    """A name written as one word or with a hyphen dropped is accepted and says so."""
    assert ck.common_difference("long bodied cellar spider", "Longbodied Cellar Spider") == ["spaces", "letter case"]
    assert ck.common_difference("steller sealions", "Steller Sea Lion") == ["a final s", "spaces", "letter case"]
    assert ck.common_difference("grisly bear", "Grizzly Bear") is None


def test_plurals():
    """Confirmed only when the singular is exactly a species-level BCSEE English name."""
    assert ck.check_plural("grey wolves", FACTS["WOLF"])[0] == "deferred"
    assert ck.check_plural("harbour seals", FACTS["SEAL"]) == (
        "confirmed", "singular 'harbour seal' is BCSEE's English name 'Harbour Seal'")
    assert ck.check_plural("reed canarygrasses", FACTS["GRASS"])[0] == "deferred"


def gbif_record(name, status="ACCEPTED", species=None, key=1, rank="SPECIES"):
    """Return a made-up GBIF Backbone name record."""
    return {"key": key, "canonicalName": name, "scientificName": f"{name} Author", "rank": rank,
            "taxonomicStatus": status, "species": species or name}


def test_no_exact_name_and_a_fuzzy_match_to_ours_is_confirmed():
    """The misspelling is no GBIF name, and GBIF's fuzzy match goes to our species (also through a synonym)."""
    match = {"matchType": "FUZZY", "usageKey": 7, "scientificName": "Lithobates catesbiana (Shaw, 1802)",
             "canonicalName": "Lithobates catesbiana", "species": "Lithobates catesbeianus"}
    status, evidence = ck.check_scientific("lithobates catesbianus", "Lithobates catesbianus",
                                           "Lithobates catesbeianus", match, [])
    assert status == "confirmed" and "fuzzy match to Lithobates catesbiana" in evidence and "usageKey 7" in evidence


def test_an_exact_name_of_another_species_is_rejected():
    """GBIF has the name as written as its own accepted species, or as a synonym of another species."""
    match = {"matchType": "EXACT", "canonicalName": "Bathyphantes brevis", "species": "Bathyphantes brevis"}
    status, evidence = ck.check_scientific("bathyphantes brevis", "Bathyphantes brevis", "Bathyphantes brevipes",
                                           match, [gbif_record("Bathyphantes brevis", key=2134063)])
    assert status == "rejected" and "its own accepted taxon (key 2134063" in evidence
    synonym = gbif_record("Dolomedes scriptus", status="SYNONYM", species="Dolomedes vittatus")
    status, evidence = ck.check_scientific("dolomedes scriptus", "Dolomedes scriptus", "Dolomedes striatus",
                                           {"matchType": "EXACT"}, [synonym])
    assert status == "rejected" and "a synonym of Dolomedes vittatus" in evidence


def test_an_exact_synonym_of_ours_or_our_name_with_an_authority_is_confirmed():
    """A misspelling GBIF keeps as a synonym of our species, or our name with its authority, is ours."""
    synonym = gbif_record("Oncorhynchus tschawytscha", status="SYNONYM", species="Oncorhynchus tshawytscha")
    assert ck.check_scientific("oncorhynchus tschawytscha", "Oncorhynchus tschawytscha", "Oncorhynchus tshawytscha",
                               {"matchType": "EXACT"}, [synonym])[0] == "confirmed"
    match = {"matchType": "EXACT", "canonicalName": "Zostera marina", "scientificName": "Zostera marina L.",
             "species": "Zostera marina", "usageKey": 3}
    status, evidence = ck.check_scientific("zostera marina l", "Zostera marina L.", "Zostera marina", match, [])
    assert status == "confirmed" and "with its authority" in evidence


def test_a_match_to_another_species_is_deferred():
    """No exact name, but GBIF's fuzzy match goes elsewhere: unclear."""
    match = {"matchType": "FUZZY", "canonicalName": "Pandora glacialis", "species": "Pandora glacialis",
             "scientificName": "Pandora glacialis X"}
    assert ck.check_scientific("pardosa glacialis", "Pardosa glacialis", "Pardosa glacialis var",
                               match, [])[0] == "deferred"


def test_a_match_above_species_or_no_match_is_deferred():
    """No exact name and no species match: the species comes from the spelling alone, so a person checks it."""
    match = {"matchType": "HIGHERRANK", "rank": "KINGDOM", "canonicalName": "Animalia"}
    status, evidence = ck.check_scientific("eryopis argentea", "Eryopis argentea", "Euryopis argentea", match, [])
    assert (status, evidence) == ("deferred", "GBIF matched only at kingdom level (Animalia), so the species was "
                                              "chosen from spelling alone; needs human review")
    assert ck.check_scientific("canis lupis", "Canis lupis", "Canis lupus", {"matchType": "NONE"}, [])[0] == "deferred"


def test_the_review_list_is_always_deferred(tmp_path):
    """The names to look at by hand stay deferred, with no checker and no GBIF call."""
    cache = ck.GbifCache(str(tmp_path / "cache.json"), offline=True)
    checked, kinds = ck.check_rows([row("kokanee", "WOLF")], FACTS, {}, cache)
    assert kinds == ["review"]
    assert (checked[0]["status"], checked[0]["checked_by"]) == ("deferred", "")
    assert checked[0]["evidence"].startswith("needs human review")


def test_the_cache_saves_answers_and_calls_once(tmp_path, monkeypatch):
    """A new answer is fetched once and kept in the file; offline, a missing answer stops the run."""
    monkeypatch.setattr(ck, "CALL_DELAY", 0)
    calls = []
    path = str(tmp_path / "cache.json")
    cache = ck.GbifCache(path, opener=lambda url: calls.append(url) or {"answer": url})
    assert cache.get("https://x/1") == {"answer": "https://x/1"}
    assert cache.get("https://x/1") == {"answer": "https://x/1"}
    assert calls == ["https://x/1"]
    assert json.load(open(path)) == {"https://x/1": {"answer": "https://x/1"}}
    assert ck.GbifCache(path, offline=True).get("https://x/1") == {"answer": "https://x/1"}
    with pytest.raises(SystemExit, match="no cached GBIF answer"):
        ck.GbifCache(path, offline=True).get("https://x/2")


def test_scientific_rows_use_the_name_as_written_and_the_kingdom(tmp_path):
    """The GBIF requests carry the name as written in the datasets and the species' kingdom."""
    asked = []
    cache = ck.GbifCache(str(tmp_path / "cache.json"),
                         opener=lambda url: asked.append(url) or ({"results": []} if "datasetKey" in url else
                                                                  {"matchType": "FUZZY",
                                                                   "species": "Lithobates catesbeianus"}))
    checked, _ = ck.check_rows([row("lithobates catesbianus", "FROG")], FACTS,
                               {"lithobates catesbianus": "Lithobates catesbianus"}, cache)
    assert "name=Lithobates+catesbianus" in asked[0] and "kingdom=Animalia" in asked[0]
    assert (checked[0]["status"], checked[0]["checked_by"], checked[0]["checked_date"]) == (
        "confirmed", "Lucia", "2026-10-29")
