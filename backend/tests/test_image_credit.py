"""Tests for shared/image_credit.py: the Python form (used by the API) and
the SQL form (used by the pipeline) of the credit check always agree, and
the API's licence labels cover exactly the licences the check accepts."""

import itertools

import duckdb
import pytest

from api.images import describe_licence
from shared import image_credit
from shared.image_credit import licence_is_readable, passes, passes_sql

# Text values to try in the URL, link and creator columns: missing, empty,
# only spaces, and real text with and without spaces around it.
TEXTS = [None, "", "   ", "x", "  x  "]

# Licence codes to try: every kind build_dwca_tables.py writes, and some it
# never should.
CODES = [None, "", "CC0_1_0", "PDM_1_0", "CC_BY_4_0", "CC_BY_SA", "CC_BY_ND_4_0",
         "CC_BY_NC_4_0", "CC_BY_NC_SA_3_0", "CC_BY_NC_ND_4_0", "CC_BY_SA_2_5",
         "CC0", "cc_by_4_0", "CC_BY_4_0 ", "CC_BY_XX_4_0", "CC_BY_4", "Copyright"]


def sql_answers(rows):
    """Run passes_sql over rows (dicts with identifier, references, creator,
    rightsholder and license_code) in DuckDB and return its answers in
    order."""
    con = duckdb.connect()
    con.execute('CREATE TABLE t (n INTEGER, identifier VARCHAR, "references" VARCHAR, '
                'creator VARCHAR, rightsholder VARCHAR, license_code VARCHAR)')
    con.executemany("INSERT INTO t VALUES (?, ?, ?, ?, ?, ?)",
                    [[i, r["identifier"], r["references"], r["creator"],
                      r["rightsholder"], r["license_code"]] for i, r in enumerate(rows)])
    answers = [row[0] for row in con.execute(
        f"SELECT {passes_sql('t')} FROM t ORDER BY n").fetchall()]
    con.close()
    return answers


def all_combinations():
    """Every combination of TEXTS in the three text columns and CODES as the
    licence, with a rights holder that the strict rule must ignore."""
    return [{"identifier": url, "references": link, "creator": creator,
             "rightsholder": "R", "license_code": code}
            for url, link, creator, code in itertools.product(TEXTS, TEXTS, TEXTS, CODES)]


def test_python_and_sql_agree_on_every_combination():
    """The API's check and the pipeline's check give the same answer for
    every combination, and the SQL is never NULL."""
    rows = all_combinations()
    assert sql_answers(rows) == [passes(row) for row in rows]


def test_strict_rule_examples():
    """A full credit passes; missing any one part fails; a rights holder
    does not stand in for the creator."""
    full = {"identifier": "u", "references": "l", "creator": "c", "license_code": "CC0_1_0"}
    assert passes(full)
    for part in ("identifier", "references", "creator", "license_code"):
        assert not passes({**full, part: None})
    assert not passes({**full, "creator": None, "rightsholder": "R"})


@pytest.mark.parametrize("code", CODES)
def test_api_labels_exactly_the_readable_licences(code):
    """describe_licence gives a label for a code exactly when the shared
    check calls it readable, so a passing image always has a label."""
    assert (describe_licence(code) is not None) == licence_is_readable(code)


def test_a_fallback_in_one_place_reaches_both_forms(monkeypatch):
    """Adding rightsholder to CREATOR_COLUMNS (a possible later fallback)
    changes the Python and the SQL check together."""
    monkeypatch.setattr(image_credit, "CREATOR_COLUMNS", ("creator", "rightsholder"))
    row = {"identifier": "u", "references": "l", "creator": None,
           "rightsholder": "R", "license_code": "CC0_1_0"}
    assert passes(row)
    assert sql_answers([row]) == [True]
    assert image_credit.credit(row)["creator"] == "R"
