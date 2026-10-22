"""Tests for the GeoNames lookup (pipeline/lunaris/geonames_lookup.py): the
normalising rules (and that original names are never changed), the order of
the matching steps (exact, as_written, plural removed, approximate, direction
word removed), how places are ordered (hints, feature words, population),
the two separate parts of a result (GeoNames' candidates with code names,
and our summary), each summary category, and the hand-added entries.

Most tests use a tiny made-up table written to a temporary folder, with the
same columns as the real one, so they run anywhere. The last group uses the
real table (data/geonames/geonames_lookup.parquet, built by
build_geonames_lookup.py) when it is on disk and is skipped otherwise."""

import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
# The pipeline scripts are not a package; add their folder to the path the
# way the scripts themselves do.
sys.path.insert(0, str(BACKEND / "pipeline" / "lunaris"))
from geonames_lookup import (  # noqa: E402
    COLUMNS,
    DEFAULT_TABLE,
    GeoNamesLookup,
    feature_codes_of,
    is_generic_phrase,
    is_too_generic,
    normalise,
    read_hand_entries,
    split_comma,
    without_plural,
)

HAND_CSV = BACKEND / "pipeline" / "lunaris" / "geonames_hand_entries.csv"


# --- Normalising ---------------------------------------------------------

@pytest.mark.parametrize("name, expected", [
    ("Kalamalka L.", "kalamalka lake"),
    ("Kalamalka Lk", "kalamalka lake"),
    ("Fraser R.", "fraser river"),
    ("Mt Robson", "mount robson"),
    ("Mt. Robson", "mount robson"),
    ("Mission Cr.", "mission creek"),
    ("Mission Ck", "mission creek"),
    ("Saturna I.", "saturna island"),
    ("Gulf Is.", "gulf islands"),
    ("Roberts Pt", "roberts point"),
    ("Strathcona Pk", "strathcona park"),
    ("Golden Ears PP", "golden ears provincial park"),
    ("Golden Ears Prov. Park", "golden ears provincial park"),
    ("St. Elias Mountains", "saint elias mountains"),
    ("Île de Vancouver", "ile de vancouver"),
    ("Colombie-Britannique", "colombie britannique"),
    ("B.C.", "bc"),
    ("The Kootenays", "kootenays"),       # plurals are kept; see without_plural
    ("  Haida   Gwaii  ", "haida gwaii"),
    ("Nlaka'pamux", "nlakapamux"),
    ("København", "kobenhavn"),
    ("The", "the"),                       # a lone "the" is kept
    ("", ""),
])
def test_normalise(name, expected):
    assert normalise(name) == expected


def test_normalise_never_changes_the_name():
    name = "  Île de Vancouver, B.C. "
    copy = str(name)
    normalise(name)
    assert name == copy


def test_without_plural():
    assert without_plural("kootenays") == "kootenay"
    assert without_plural("rocky mountains") == "rocky mountain"
    assert without_plural("paris") == "paris"          # "is" is not taken for a plural
    assert without_plural("glass") == "glass"          # nor is "ss"


def test_split_comma():
    assert split_comma("Vancouver, British Columbia") == ("Vancouver", ["British Columbia"])
    assert split_comma("Montgomery, New York, USA") == ("Montgomery", ["New York", "USA"])
    assert split_comma("Guelph") == ("Guelph", [])


def test_generic_words_and_phrases():
    assert is_too_generic(normalise("Lake"))
    assert is_too_generic(normalise("rivers"))
    assert is_too_generic(normalise("Oceans"))
    assert is_too_generic("")
    assert not is_too_generic(normalise("Lake Louise"))
    assert is_generic_phrase(normalise("Western provinces"))
    assert is_generic_phrase(normalise("North Coast"))
    assert not is_generic_phrase(normalise("Southern Vancouver Island"))
    assert not is_generic_phrase(normalise("Okanagan"))


def test_feature_words():
    assert "H.STM" in feature_codes_of(normalise("Fraser River"))
    assert "H.STRT" in feature_codes_of(normalise("Strait of Georgia"))       # first word
    assert "T.MTS" in feature_codes_of(normalise("Rocky Mountains"))
    assert "T.MT" in feature_codes_of(normalise("Mt. Robson"))
    assert feature_codes_of(normalise("Victoria")) == set()


# --- A tiny made-up table ------------------------------------------------

def place(geonameid, name, region="outside_canada", name_type="main", language="", historic=False,
          feature="P.PPL", country="US", admin1="WA", population=0, main=None, cc2="", lat=0.0, lon=0.0):
    """One row of the made-up table, in the real table's columns; main is the place's main name."""
    feature_class, feature_code = feature.split(".")
    return {"norm": normalise(name), "name": name, "main_name": main or name, "name_type": name_type,
            "language": language, "is_historic": historic, "geonameid": geonameid,
            "feature_class": feature_class, "feature_code": feature_code, "country_code": country,
            "cc2": cc2, "admin1_code": admin1, "population": population, "latitude": lat, "longitude": lon,
            "region": region, "source": "geonames"}


ROWS = [
    place(1, "Victoria", "bc", feature="P.PPLA", country="CA", admin1="02", population=289625),
    place(2, "Victoria", feature="A.ADM1", country="AU", admin1="07", population=6500000),
    place(3, "Victoria", feature="P.PPL", country="US", admin1="TX", population=65000),
    place(4, "Lake Victoria", feature="H.LK", country="UG", admin1=""),
    place(5, "Kalamalka Lake", "bc", feature="H.LK", country="CA", admin1="02"),
    place(6, "Haida Gwaii", "bc", feature="T.ISLS", country="CA", admin1="02"),
    place(6, "Queen Charlotte Islands", "bc", name_type="alternate", language="en", historic=True,
          feature="T.ISLS", country="CA", admin1="02", main="Haida Gwaii"),
    place(7, "Vancouver Island", "bc", feature="T.ISL", country="CA", admin1="02"),
    place(7, "Île de Vancouver", "bc", name_type="alternate", language="fr", feature="T.ISL",
          country="CA", admin1="02", main="Vancouver Island"),
    place(8, "Vancouver", "bc", feature="P.PPLA2", country="CA", admin1="02", population=600000),
    place(9, "Vancouver", feature="P.PPL", country="US", admin1="WA", population=190000),
    place(10, "British Columbia", "bc", feature="A.ADM1", country="CA", admin1="02", population=5000000),
    place(10, "BC", "bc", name_type="alternate", language="abbr", feature="A.ADM1", country="CA", admin1="02",
          main="British Columbia"),
    place(11, "Washington", feature="A.ADM1", country="US", admin1="WA", population=7700000),
    place(12, "Guelph", "canada_other", feature="P.PPL", country="CA", admin1="08", population=130000),
    place(13, "Lake", feature="P.PPL", country="US", admin1="MS"),
    place(14, "Okanagan Lake", "bc", feature="H.LK", country="CA", admin1="02"),
    place(16, "Cascade Creek", feature="H.STM", country="AU", admin1="02"),
    # Plurals and feature words.
    place(20, "Rocky Mountains", feature="T.MTS", country="US", admin1="00", cc2="CA,US", lat=43.4, lon=-110.9),
    place(21, "Rocky Mountain", feature="P.PPL", country="US", admin1="OK", population=420),
    place(22, "Fraser River", "bc", feature="H.STM", country="CA", admin1="02", lat=49.1, lon=-123.2),
    place(23, "Fraser River", "canada_other", feature="P.PPL", country="CA", admin1="05", population=5000),
    place(24, "Western Province", feature="A.ADM1", country="LK", admin1="36", population=6000000),
    # Spanning the BC border, and its namesake elsewhere.
    place(30, "Strait of Georgia", feature="H.CHN", country="US", admin1="WA", cc2="CA,US", lat=48.8, lon=-122.9),
    place(31, "Rosario Strait", feature="H.CHN", country="US", admin1="WA", lat=48.6, lon=-122.8),
    place(31, "Strait of Georgia", name_type="alternate", feature="H.CHN", country="US", admin1="WA",
          main="Rosario Strait", lat=48.6, lon=-122.8),
    place(32, "Juan de Fuca Strait", "bc", feature="H.STRT", country="CA", admin1="02", cc2="CA,US"),
    place(33, "Grassy Lake", feature="H.LK", country="US", admin1="MT", cc2="CA", lat=49.0, lon=-110.2),
    # The places the real hand-added entries point to (their real GeoNames ids).
    place(5961613, "Glacier National Park of Canada", "bc", feature="L.RES", country="CA", admin1="02"),
    place(40, "Glacier National Park", feature="L.PRK", country="US", admin1="MT"),
    place(5720196, "Columbia River", feature="H.STM", country="US", admin1="OR", lat=46.2, lon=-124.1),
    place(41, "Columbia River", feature="P.PPL", country="US", admin1="WA", lat=47.3, lon=-120.1),
    # Containing BC, and the ocean.
    place(6251999, "Canada", "canada_other", feature="A.PCLI", country="CA", admin1="00", population=41000000),
    place(50, "Canada", feature="P.PPL", country="US", admin1="KY", population=100),
    place(51, "Pacific Ocean", feature="H.OCN", country="", admin1="00"),
    # Ties on everything but country: Canada, then the United States, then the rest.
    place(60, "Example Range", feature="T.MTS", country="AU", admin1="04"),
    place(61, "Example Range", feature="T.MTS", country="US", admin1="MT"),
    place(62, "Example Range", "canada_other", feature="T.MTS", country="CA", admin1="01"),
]

CODES = {"countries": {"CA": "Canada", "US": "United States", "AU": "Australia"},
         "admin1": {"CA.02": "British Columbia", "US.WA": "Washington"},
         "feature_classes": {"H": "stream, lake, ...", "P": "city, village,..."},
         "feature_codes": {"H.STM": "stream", "P.PPLA": "seat of a first-order administrative division"}}


@pytest.fixture
def lookup(tmp_path):
    """A lookup on the made-up table, with the real hand-added entries and a few code names."""
    import duckdb

    table, codes = tmp_path / "lookup.parquet", tmp_path / "codes.json"
    codes.write_text(json.dumps(CODES), encoding="utf-8")
    con = duckdb.connect()
    types = {"is_historic": "BOOLEAN", "geonameid": "INTEGER", "population": "BIGINT",
             "latitude": "DOUBLE", "longitude": "DOUBLE"}
    con.execute("CREATE TABLE t (" + ", ".join(f"{c} {types.get(c, 'VARCHAR')}" for c in COLUMNS) + ")")
    con.executemany(f"INSERT INTO t VALUES ({', '.join('?' for _ in COLUMNS)})",
                    [[row[c] for c in COLUMNS] for row in ROWS])
    con.execute(f"COPY (SELECT * FROM t ORDER BY norm) TO '{table}' (FORMAT parquet, ROW_GROUP_SIZE 4)")
    return GeoNamesLookup(str(table), str(HAND_CSV), str(codes))


def ids(result):
    """The GeoNames ids of a result's candidates, in order."""
    return [c["geonameid"] for c in result["candidates"]]


def category(result):
    """A result's summary category."""
    return result["summary"]["category"]


def test_result_has_three_separate_parts(lookup):
    result = lookup.lookup("Victoria", as_written="Victoria")
    assert set(result) == {"query", "candidates", "summary"}
    # The candidates hold only GeoNames' facts (and how each matched); our reading is in the summary.
    assert not {"region", "kind", "agrees_with_hint"} & set(result["candidates"][0])
    assert result["summary"]["candidate_kinds"] == ["outside_bc", "in_bc", "outside_bc"]


def test_candidates_carry_code_names(lookup):
    top = lookup.lookup("Kalamalka Lake")["candidates"][0]
    assert (top["country_code"], top["country_name"]) == ("CA", "Canada")
    assert (top["admin1_code"], top["admin1_name"]) == ("02", "British Columbia")
    river = lookup.lookup("Fraser River")["candidates"][0]
    assert (river["feature_class"], river["feature_class_name"]) == ("H", "stream, lake, ...")
    assert (river["feature_code"], river["feature_code_name"]) == ("STM", "stream")
    strait = lookup.lookup("Juan de Fuca Strait")["candidates"][0]
    assert (strait["cc2"], strait["cc2_names"]) == ("CA,US", ["Canada", "United States"])


def test_exact_match_on_main_and_alternate_names(lookup):
    result = lookup.lookup("Haida Gwaii")
    assert (result["summary"]["match"], category(result), result["summary"]["places_found"]) == \
        ("exact", "in_bc_only", 1)
    assert not result["summary"]["historic"]


def test_historic_name(lookup):
    result = lookup.lookup("Queen Charlotte Islands")
    assert 6 in ids(result)                     # Haida Gwaii, through its historic name
    assert result["summary"]["historic"]


def test_french_name(lookup):
    top = lookup.lookup("Ile de Vancouver")["candidates"][0]
    assert (top["geonameid"], top["main_name"], top["matched_name"]) == (7, "Vancouver Island", "Île de Vancouver")


def test_abbreviation(lookup):
    assert lookup.lookup("Kalamalka L.")["candidates"][0]["main_name"] == "Kalamalka Lake"


def test_names_in_results_are_unchanged(lookup):
    result = lookup.lookup("ile de VANCOUVER", as_written="Île de VANCOUVER")
    assert result["query"] == {"name": "ile de VANCOUVER", "as_written": "Île de VANCOUVER"}
    assert result["candidates"][0]["main_name"] == "Vancouver Island"


def test_namesakes_are_both_and_ordered_by_population(lookup):
    result = lookup.lookup("Victoria")
    assert category(result) == "both"
    assert ids(result) == [2, 1, 3]                       # Lake Victoria is another name
    assert result["summary"]["kinds"]["in_bc"] == 1 and result["summary"]["kinds"]["outside_bc"] == 2


def test_comma_hint_ranks_but_does_not_filter(lookup):
    result = lookup.lookup("Vancouver, British Columbia")
    assert result["summary"]["looked_up"] == "Vancouver"
    assert result["summary"]["hint_check"] == "agrees"
    assert category(result) == "both"                     # the Washington Vancouver is still there
    assert ids(result)[0] == 8
    assert ids(lookup.lookup("Vancouver, Washington"))[0] == 9


def test_comma_hint_from_as_written(lookup):
    result = lookup.lookup("Vancouver", as_written="Vancouver, BC")
    assert result["summary"]["hints"] == ["BC"] and result["summary"]["hint_check"] == "agrees"
    assert ids(result)[0] == 8


def test_comma_hint_that_disagrees_or_is_unknown(lookup):
    assert lookup.lookup("Guelph, British Columbia")["summary"]["hint_check"] == "disagrees"
    assert lookup.lookup("Guelph, Atlantis")["summary"]["hint_check"] == "not recognised"
    assert lookup.lookup("Guelph")["summary"]["hint_check"] == "no hint"


def test_generic_word_is_not_looked_up(lookup):
    result = lookup.lookup("Lake", as_written="Lake")
    assert (result["summary"]["match"], category(result), result["candidates"]) == \
        ("too generic", "too_generic", [])
    assert category(lookup.lookup("rivers", as_written="rivers")) == "too_generic"


def test_plural_is_removed_only_when_the_name_finds_nothing(lookup):
    # "Rocky Mountains" is found as written: the range, not the town of Rocky Mountain.
    result = lookup.lookup("Rocky Mountains")
    assert ids(result) == [20] and not result["summary"]["plural_removed"]
    result = lookup.lookup("Kalamalka Lakes")
    assert ids(result) == [5] and result["summary"]["plural_removed"]


def test_generic_phrase_never_reaches_the_plural_step(lookup):
    # Without the check, "Western provinces" would find Sri Lanka's Western Province.
    assert category(lookup.lookup("Western provinces")) == "too_generic"


def test_feature_word_ranks_that_kind_of_feature_first(lookup):
    # The town of Fraser River has a population; the river has none, but the name says "River".
    assert ids(lookup.lookup("Fraser River")) == [22, 23]


def test_direction_word_is_removed_only_when_nothing_is_found(lookup):
    result = lookup.lookup("Southern Vancouver Island")
    assert ids(result) == [7] and result["summary"]["direction_word_removed"] == "Southern"
    assert not lookup.lookup("Vancouver Island")["summary"]["direction_word_removed"]


def test_approximate_match(lookup):
    result = lookup.lookup("Kalamalka Lakke")
    assert result["summary"]["match"] == "approximate"
    assert ids(result) == [5] and result["candidates"][0]["similarity"] >= 0.8


def test_approximate_match_word_by_word(lookup):
    # 0.85 similar as a whole, but "crest" is not "creek": no match.
    assert category(lookup.lookup("Cascade Crest")) == "not_found"


def test_no_approximate_match_for_short_names(lookup):
    assert category(lookup.lookup("Gwlph")) == "not_found"   # too short to be safe


def test_matches_on_names_only(lookup):
    # Australia is a country code in the table, but no place is called that.
    assert category(lookup.lookup("Australia")) == "not_found"


def test_as_written_is_tried_when_the_name_finds_nothing(lookup):
    result = lookup.lookup("Vancouver Isle", as_written="Île de Vancouver")
    assert (result["summary"]["matched_on"], result["summary"]["match"]) == ("as_written", "exact")


def test_in_bc_only_and_outside_bc_only(lookup):
    assert category(lookup.lookup("Kalamalka Lake")) == "in_bc_only"
    assert category(lookup.lookup("Guelph")) == "outside_bc_only"


def test_spans_the_bc_border_from_second_countries(lookup):
    # Coded US, Canada as a second country, near BC: one place across BC's border.
    # Its namesake through an alternate name (Rosario Strait) does not make it "both".
    result = lookup.lookup("Strait of Georgia")
    assert category(result) == "spans_bc_border"
    assert result["summary"]["kinds"]["spans_bc_border"] == 1 and result["summary"]["kinds"]["outside_bc"] == 1
    # Coded BC, the US as a second country.
    assert category(lookup.lookup("Juan de Fuca Strait")) == "spans_bc_border"
    # Canada as a second country, but on the Montana-Alberta border, far from BC.
    assert category(lookup.lookup("Grassy Lake")) == "outside_bc_only"


def test_spans_the_bc_border_from_a_hand_entry(lookup):
    result = lookup.lookup("Columbia River")
    assert category(result) == "spans_bc_border"
    assert ids(result)[0] == 5720196                      # the river first: the name says "River"


def test_both_from_a_hand_added_name(lookup):
    # GeoNames has BC's park only as "Glacier National Park of Canada"; the hand entry points there.
    result = lookup.lookup("Glacier National Park")
    assert category(result) == "both"
    assert set(ids(result)) == {5961613, 40}
    bc_park = next(c for c in result["candidates"] if c["geonameid"] == 5961613)
    assert (bc_park["matched_from"], bc_park["main_name"]) == ("hand", "Glacier National Park of Canada")
    assert result["summary"]["hand_places"] == 1


def test_contains_bc(lookup):
    result = lookup.lookup("Canada")
    assert category(result) == "contains_bc"
    assert ids(result)[0] == 6251999


def test_ocean_with_no_country(lookup):
    assert category(lookup.lookup("Pacific Ocean")) == "ocean_no_country"


def test_ties_prefer_canada_then_the_united_states(lookup):
    assert ids(lookup.lookup("Example Range")) == [62, 61, 60]


def test_hand_added_entries(lookup):
    entries = read_hand_entries(HAND_CSV)
    assert len(entries) == 25
    assert sum(e["kind"] == "region" for e in entries) == 21
    assert {e["name"] for e in entries if e["kind"] == "alias"} == {"Glacier National Park"}
    assert {e["name"] for e in entries if e["kind"] == "spans_bc_border"} == {"Columbia River", "Rocky Mountains",
                                                                                   "Cascade Range"}
    result = lookup.lookup("Kootenays")
    assert result["summary"]["only_hand"] and category(result) == "in_bc_only"
    top = result["candidates"][0]
    assert (top["matched_from"], top["geonameid"]) == ("hand", None)
    assert lookup.lookup("Okanagan")["summary"]["only_hand"]
    # A hand-added place found next to GeoNames ones is not "only hand".
    assert not lookup.lookup("Queen Charlotte Islands")["summary"]["only_hand"]


# --- The real table, when it is on disk ----------------------------------

real = pytest.mark.skipif(not Path(DEFAULT_TABLE).exists(), reason="the real lookup table is not built")


@pytest.fixture(scope="module")
def real_lookup():
    """A lookup on the real table with the hand-added entries, as the pipeline runs it."""
    return GeoNamesLookup(DEFAULT_TABLE, str(HAND_CSV))


@real
def test_real_historic_name(real_lookup):
    result = real_lookup.lookup("Queen Charlotte Islands")
    assert any(c["main_name"] == "Haida Gwaii" and c["admin1_code"] == "02" for c in result["candidates"])


@real
def test_real_french_name(real_lookup):
    top = real_lookup.lookup("Ile de Vancouver")["candidates"][0]
    assert (top["main_name"], top["admin1_name"]) == ("Vancouver Island", "British Columbia")


@real
def test_real_abbreviation(real_lookup):
    result = real_lookup.lookup("Kalamalka L.")
    assert category(result) == "in_bc_only"
    assert result["candidates"][0]["main_name"] == "Kalamalka Lake"


@real
def test_real_comma_hint(real_lookup):
    result = real_lookup.lookup("Vancouver, British Columbia")
    assert result["summary"]["hint_check"] == "agrees" and category(result) == "both"
    top = result["candidates"][0]
    assert (top["main_name"], top["admin1_code"], top["feature_class"]) == ("Vancouver", "02", "P")


@real
def test_real_generic_word(real_lookup):
    assert category(real_lookup.lookup("Lake")) == "too_generic"


@real
@pytest.mark.parametrize("name", ["Victoria", "Richmond", "Glacier National Park"])
def test_real_namesakes_are_both(real_lookup, name):
    assert category(real_lookup.lookup(name)) == "both"


@real
@pytest.mark.parametrize("name", ["Strait of Georgia", "Salish Sea", "Columbia River"])
def test_real_spans_the_bc_border(real_lookup, name):
    assert category(real_lookup.lookup(name)) == "spans_bc_border"


@real
def test_real_contains_bc_and_ocean(real_lookup):
    assert category(real_lookup.lookup("Canada")) == "contains_bc"
    assert category(real_lookup.lookup("North America")) == "contains_bc"
    assert category(real_lookup.lookup("Pacific Ocean")) == "ocean_no_country"


def test_hand_entry_file_is_checked(tmp_path):
    bad = tmp_path / "hand.csv"
    bad.write_text("name,geonames_id,kind,note\nSomewhere,,alias,an alias needs an id\n", encoding="utf-8")
    with pytest.raises(ValueError):
        read_hand_entries(bad)


@real
def test_real_rocky_mountains(real_lookup):
    result = real_lookup.lookup("Rocky Mountains")
    top = result["candidates"][0]
    assert (top["feature_code"], top["country_code"]) == ("MTS", "CA")      # not Australia's
    assert result["summary"]["kinds"]["spans_bc_border"] == 1               # the hand entry


@real
def test_real_direction_word(real_lookup):
    result = real_lookup.lookup("Southern Vancouver Island")
    assert result["summary"]["direction_word_removed"] == "Southern"
    assert result["candidates"][0]["main_name"] == "Vancouver Island"


@real
def test_real_victoria_no_foreign_namesake_first(real_lookup):
    # Hong Kong's Danish alternate name is "Victoria"; it must not come first.
    top = real_lookup.lookup("Victoria")["candidates"][0]
    assert top["language"] in ("", "en", "fr", "abbr") and not top["is_historic"]


@real
def test_real_bc_hint_means_british_columbia(real_lookup):
    # "BC" is also Baja California in GeoNames; as a hint it means British Columbia.
    result = real_lookup.lookup("Victoria", as_written="Victoria, BC")
    top = result["candidates"][0]
    assert (top["main_name"], top["admin1_code"]) == ("Victoria", "02")


@real
@pytest.mark.parametrize("name", ["Kouchibouguac National Park", "Guelph"])
def test_real_outside_bc_only(real_lookup, name):
    assert category(real_lookup.lookup(name)) == "outside_bc_only"
