"""Tests for the report on coordinates written in a record's own text
(pipeline/lunaris/record_text_coordinates.py): each form is read to the
right point, ranges and boxes are not points, measurements and
temperatures are not coordinates, a list of many values is not paired up,
signs and letters agree, and the checks and roles."""

import sys
from pathlib import Path

import pytest
from shapely.geometry import box

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "pipeline" / "lunaris"))
import record_text_coordinates as rtc  # noqa: E402


def points(text):
    """Return the points found in a text, rounded to 4 decimals."""
    return [(round(f["lat"], 4), round(f["lon"], 4)) for f in rtc.coordinates(text) if f["kind"] == "point"]


@pytest.mark.parametrize("text, expected", [
    ("Riske Creek (52.0025°N, 122.4116°W)", (52.0025, -122.4116)),
    ("Wizard's Rock (48°51.5′N; 125°9.4′W)", (48.8583, -125.1567)),
    ("Sidney Island (48°37'39'N, 123°19'30\"W) is", (48.6275, -123.325)),
    ("coordinates 48°50'19.00\"N, 125°08'02.00\"W", (48.8386, -125.1339)),
    ("near Satah Mountain (52 o 28'N, 124 o 43'W)", (52.4667, -124.7167)),
    ("Latitude 48.3149 N Longitude 126.0580 W Depth 896.1 m", (48.3149, -126.058)),
    ("lat: 50.0307, long: −125.0992; bottom", (50.0307, -125.0992)),
    ("Stanley Park (49.30275394688415, -123.12643462372476).", (49.3028, -123.1264)),
    ("54° North, 130° West", (54.0, -130.0)),
])
def test_each_form_gives_the_right_point(text, expected):
    assert points(text) == [expected]


def test_a_point_written_twice_in_one_text_counts_once():
    assert points("GPS 48°50'19.00\"N, 125°08'02.00\"W (48.83861111, -125.13388889)") == [(48.8386, -125.1339)]


def test_a_minus_sign_and_a_letter_both_mean_west():
    found = rtc.coordinates("-120.4603W, 50.7007N")
    assert points("-120.4603W, 50.7007N") == [(50.7007, -120.4603)]
    assert rtc.signed(-87.85, "E") == (-87.85, "minus sign with N or E, read as S or W")
    assert found[0]["note"] == ""


def test_place_list_pairs_only_when_one_of_each_is_left():
    assert points("Kamloops | -120.4603W | Lac Du Bois Provincial Park | 50.7007N") == [(50.7007, -120.4603)]
    many = rtc.coordinates("31.92N | -33.5N | 26E | -87.85E | -2.33N | 33.75N | -66.5E")
    assert all(f["kind"].endswith("_alone") for f in many) and len(many) == 6


def test_ranges_and_boxes_are_not_points():
    kinds = [f["kind"] for f in rtc.coordinates("the study area (45° N to 58° N and from the coast to 148° W)")]
    assert kinds == ["lat_range", "lon_alone"]
    assert [f["kind"] for f in rtc.coordinates("Northeast Pacific from 20 to 60°N")] == ["lat_range"]
    found = rtc.coordinates("(-117˚ W to -120˚ W, 53˚ N to 55˚ N)")
    assert [(f["kind"], f["s"], f["n"], f["w"], f["e"]) for f in found] == [("box", 53, 55, -120, -117)]
    edges = rtc.coordinates("Geographic extent West: −139.06°East: −110.43°South: 47.68°North: 60.61° Suggested")
    assert [(f["kind"], f["form"], f["s"], f["n"], f["w"], f["e"]) for f in edges] == [
        ("box", "bounds", 47.68, 60.61, -139.06, -110.43)]


def test_temperatures_measurements_and_units_are_not_coordinates():
    assert rtc.coordinates("below 20°C, 2 min at 94 °C, 15 N isotopes, a 100 W lamp, 2.5E-3, 10.7 ± 0.7, 1.234, 5.678") == []


def test_checks():
    bc = box(-139, 48.3, -114, 60)
    ok = {"lat": 52.0, "lon": -122.4, "lon_signed": True}
    assert rtc.checks(ok, "bc_only", [], bc) == ""
    assert rtc.checks({"lat": 38.8, "lon": 90.1, "lon_signed": False}, "global", [], bc) == "missing_west"
    assert rtc.checks({"lat": 7.6, "lon": 36.8, "lon_signed": False}, "global", [], bc) == ""
    assert rtc.checks({"lat": -110.4, "lon": 139.1, "lon_signed": True}, "bc_only", [], bc) == "out_of_range"
    assert rtc.checks({"lat": 45.0, "lon": -75.0, "lon_signed": True}, "bc_only", [], bc) == "outside_class"
    assert rtc.checks(ok, "bc_only", [{"s": 48, "n": 49, "w": -124, "e": -123}], bc) == "outside_own_boxes"


def test_roles_from_the_words_around():
    text = "Data were collected at the study site (52.0025°N, 122.4116°W) every spring."
    found = rtc.coordinates(text)[0]
    assert rtc.role_of(text, found["start"], found["end"]) == "study_site"
    text = "Weather data came from the Environment Canada weather station at 52.1800N, 122.0500W."
    found = rtc.coordinates(text)[0]
    assert rtc.role_of(text, found["start"], found["end"]) == "weather_or_climate_station"
    assert rtc.role_of("Bamfield | 48°51.5′N; 125°9.4′W", 11, 32) == "no_role_shown"
