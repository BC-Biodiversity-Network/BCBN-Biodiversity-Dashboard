"""
image_credit.py

The one definition of when an image can be shown on a species card: it
needs an image URL, a link to the original, a creator and a licence that
can be read and linked (a CC licence must name its version). The pipeline uses it to pick the species image
(pipeline/gbif/build_species_image.py, as SQL) and the API uses it to decide
whether to show the image (api/images.py, in Python), so the two can never
disagree. Both forms are built from the same column lists below, and a test
(tests/test_image_credit.py) checks they give the same answer.

Column names are those of bc_media and bc_species_image: identifier (the
image URL), references (the link to the original), creator, rightsholder
and license_code. The species card stores the same values with an image_
prefix; the API passes them in under these names.

Adding a fallback later: each part of the credit is the first column in
its list that has text. A rights holder fallback is then one change here,
adding "rightsholder" to CREATOR_COLUMNS, and every user of this module
follows. A link fallback that is not a column, such as the GBIF record
page, would go in link_of() and link_sql(), also only here. No fallback is
in use now.
"""

import re

# The columns that can supply each part of the credit, in order of
# preference. Only the first column of each is used today.
URL_COLUMNS = ("identifier",)
LINK_COLUMNS = ("references",)
CREATOR_COLUMNS = ("creator",)
LICENCE_COLUMN = "license_code"

# The licence codes that can be read and shown with a label and a link: CC0,
# the Public Domain Mark, and CC BY with any of NC, SA, ND and a version
# (CC_BY_NC_4_0). A CC code with no version (CC_BY_SA, which
# build_dwca_tables.py writes when the record names none) fails: CC BY asks
# for a link to the licence, and without a version that link would be a
# guess. Written so both Python's re and DuckDB's regexp_full_match read it
# the same way.
READABLE_LICENCE_PATTERN = r"CC0_1_0|PDM_1_0|CC_BY(?:_NC)?(?:_SA)?(?:_ND)?_[0-9]+_[0-9]+"


# ---------------------------------------------------------------- Python

def has_text(value):
    """True if value is a string with something other than spaces in it."""
    return isinstance(value, str) and value.strip() != ""


def first_text(image, columns):
    """Return the first of these columns of image (a dict) that has text,
    without the spaces around it, or None if none has."""
    for column in columns:
        value = image.get(column)
        if has_text(value):
            return value.strip()
    return None


def link_of(image):
    """Return the link to the original for an image dict, or None."""
    return first_text(image, LINK_COLUMNS)


def licence_is_readable(code):
    """True if code is a licence code the card can show."""
    return isinstance(code, str) and re.fullmatch(READABLE_LICENCE_PATTERN, code) is not None


def credit(image):
    """Return the credit to show for an image dict, as a dict with url,
    link, creator and license_code, or None if the image fails any of the
    four checks and must not be shown."""
    url = first_text(image, URL_COLUMNS)
    link = link_of(image)
    creator = first_text(image, CREATOR_COLUMNS)
    code = image.get(LICENCE_COLUMN)
    if url is None or link is None or creator is None or not licence_is_readable(code):
        return None
    return {"url": url, "link": link, "creator": creator, "license_code": code}


def passes(image):
    """True if an image dict passes all four checks."""
    return credit(image) is not None


# ------------------------------------------------------------------- SQL

def first_text_sql(alias, columns):
    """SQL for the first of these columns of table alias that has text,
    or NULL. Column names are quoted, because "references" is a SQL word."""
    parts = [f"nullif(trim({alias}.\"{column}\"), '')" for column in columns]
    return parts[0] if len(parts) == 1 else f"coalesce({', '.join(parts)})"


def link_sql(alias):
    """SQL for the link to the original of table alias, or NULL."""
    return first_text_sql(alias, LINK_COLUMNS)


def passes_sql(alias):
    """SQL that is true when the image in table alias passes all four
    checks, and false otherwise (never NULL). Plain SQL that DuckDB 0.10.3
    can run."""
    return (
        f"({first_text_sql(alias, URL_COLUMNS)} IS NOT NULL"
        f" AND {link_sql(alias)} IS NOT NULL"
        f" AND {first_text_sql(alias, CREATOR_COLUMNS)} IS NOT NULL"
        f" AND regexp_full_match(coalesce({alias}.\"{LICENCE_COLUMN}\", ''),"
        f" '{READABLE_LICENCE_PATTERN}'))"
    )
