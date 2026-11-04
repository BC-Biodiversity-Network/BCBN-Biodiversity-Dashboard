"""Tests for the statistics on groups of drawn names (pipeline/lunaris/place_pick_groups.py):
when a quote names the province right after the place, and the right,
wrong and unsure counts of a group."""

import sys
from pathlib import Path

import pandas as pd

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "pipeline" / "lunaris"))
import place_pick_groups as groups  # noqa: E402


def test_province_right_after_the_name():
    assert groups.province_next_to_name("Atlin", "British Columbia", ["wetlands in Atlin, British Columbia, Canada"])
    assert groups.province_next_to_name("Boundary Bay", "British Columbia", ["Boundary Bay; British Columbia; Canada"])
    assert groups.province_next_to_name("Valcartier", "Quebec", ["", "Station, Valcartier, Quebec."])
    assert groups.province_next_to_name("Comox", "British Columbia", ["seals from Comox (BC)"]) is False
    assert groups.province_next_to_name("Broughton Archipelago", "British Columbia",
                                        ["salmon farms in the Broughton Archipelago, BC"])
    # BC must be a word of its own, and must come right after the name.
    assert not groups.province_next_to_name("Victoria", "British Columbia", ["Victoria, BCE"])
    assert not groups.province_next_to_name("Kamloops", "British Columbia", ["Kamloops and Prince George, BC"])


def test_group_counts():
    frame = pd.DataFrame({"draft_reading": ["right", "right", "wrong", "unsure"]})
    row = groups.counts(frame, pd.Series([True, True, True, False]), "test")
    assert row == {"group": "test", "size": 3, "right": 2, "wrong": 1, "unsure": 0}
