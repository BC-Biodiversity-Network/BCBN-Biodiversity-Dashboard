"""Tests for images.py: licence labels and the iNaturalist image size."""

import pytest

from api.images import card_image, describe_licence, display_url


@pytest.mark.parametrize("code, expected", [
    ("CC0_1_0", ("CC0 1.0", "https://creativecommons.org/publicdomain/zero/1.0/")),
    ("PDM_1_0", ("Public Domain Mark 1.0", "https://creativecommons.org/publicdomain/mark/1.0/")),
    ("CC_BY_4_0", ("CC BY 4.0", "https://creativecommons.org/licenses/by/4.0/")),
    ("CC_BY_NC_SA_3_0", ("CC BY-NC-SA 3.0", "https://creativecommons.org/licenses/by-nc-sa/3.0/")),
    ("CC_BY_NC_ND_4_0", ("CC BY-NC-ND 4.0", "https://creativecommons.org/licenses/by-nc-nd/4.0/")),
    # No version in the record, so no link rather than a guessed one.
    ("CC_BY_SA", ("CC BY-SA", None)),
])
def test_known_licences(code, expected):
    """Every code build_dwca_tables.py can write gets a label and, when it
    names a version, the matching Creative Commons link."""
    assert describe_licence(code) == expected


@pytest.mark.parametrize("code", [None, "", "Copyright", "CC_BY_XX_4_0"])
def test_unknown_licences(code):
    """An empty or unknown code has no licence to show."""
    assert describe_licence(code) is None


def test_inaturalist_sizes():
    """iNaturalist originals become medium; other URLs are left alone."""
    assert display_url(
        "https://inaturalist-open-data.s3.amazonaws.com/photos/736813177/original.jpg"
    ) == "https://inaturalist-open-data.s3.amazonaws.com/photos/736813177/medium.jpg"
    assert display_url(
        "https://static.inaturalist.org/photos/5/original.JPEG"
    ) == "https://static.inaturalist.org/photos/5/medium.JPEG"
    other = "https://caos.boldsystems.org/api/objects/x.jpg?subunit=1024.jpg"
    assert display_url(other) == other


FULL_CREDIT = {
    "image_identifier": "https://example.org/a.jpg",
    "image_references": "https://example.org/a",
    "image_creator": "A. Person",
    "image_rightsholder": None,
    "image_license_code": "CC0_1_0",
}


@pytest.mark.parametrize("missing", [
    "image_identifier", "image_references", "image_creator", "image_license_code"])
def test_image_needs_full_credit(missing):
    """Without any one of URL, link, creator or a readable licence there is
    no image; a blank value counts as missing."""
    assert card_image(FULL_CREDIT) is not None
    assert card_image({**FULL_CREDIT, missing: None}) is None
    assert card_image({**FULL_CREDIT, missing: "  "}) is None
