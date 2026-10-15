"""
images.py

Turns the image columns of a species card into the image the page shows,
or into nothing when the image cannot be credited properly.

The rule: an image is only shown with its creator, its licence and a link
to the original. So an image is returned only when the card has all four
of: the image URL, the link to the original, a creator, and a licence
code that can be read. Anything less and the card has no image. How
bc_species_image picks the image is not changed here; picking an image
that has full credit is a later pipeline step.
"""

import re

# iNaturalist serves every photo in several sizes, and the "original" one
# the records point to can be several megabytes. The card uses "medium"
# (up to 500 pixels on the long side) instead. Only the file name changes,
# so the picture and its credit are the same.
INATURALIST_ORIGINAL = re.compile(
    r"^(https://(?:inaturalist-open-data\.s3\.amazonaws\.com|static\.inaturalist\.org)"
    r"/photos/\d+/)original(\.[A-Za-z]+)$")

# The licence codes build_dwca_tables.py writes: CC0_1_0, PDM_1_0, and
# CC_BY with any of NC, SA, ND, with or without a version (CC_BY_SA_4_0,
# CC_BY_SA).
CC_LICENCE_CODE = re.compile(r"^CC_BY((?:_NC)?(?:_SA)?(?:_ND)?)(?:_(\d+)_(\d+))?$")


def has_text(value):
    """True if value is a string with something other than spaces in it."""
    return isinstance(value, str) and value.strip() != ""


def display_url(identifier):
    """Return the URL the card should load for an image: the medium size
    for an iNaturalist photo, and the image's own URL for anything else."""
    match = INATURALIST_ORIGINAL.match(identifier)
    if match:
        return f"{match.group(1)}medium{match.group(2)}"
    return identifier


def describe_licence(code):
    """Return (label, url) for a licence code, for example
    ("CC BY-NC 4.0", "https://creativecommons.org/licenses/by-nc/4.0/"),
    or None if the code is empty or not one we know.

    A code with no version (CC_BY_SA) gets a label but no link, because
    the record never said which version it is and the link would be a
    guess."""
    if code == "CC0_1_0":
        return "CC0 1.0", "https://creativecommons.org/publicdomain/zero/1.0/"
    if code == "PDM_1_0":
        return "Public Domain Mark 1.0", "https://creativecommons.org/publicdomain/mark/1.0/"

    match = CC_LICENCE_CODE.match(code or "")
    if not match:
        return None

    # "_NC_SA" becomes ["by", "nc", "sa"].
    parts = ["by"] + [part.lower() for part in match.group(1).split("_") if part]
    label = "CC " + "-".join(part.upper() for part in parts)

    if match.group(2) is None:
        return label, None

    version = f"{match.group(2)}.{match.group(3)}"
    return (f"{label} {version}",
            f"https://creativecommons.org/licenses/{'-'.join(parts)}/{version}/")


def card_image(card):
    """Return the image block for a card (a dict of card columns), or None
    if the image is missing or cannot be fully credited.

    The block holds what the page needs to show the image and its credit:
    url (the medium size for iNaturalist), page_url (the link to the
    original), creator, rightsholder, the licence code, label and link, and
    no_derivatives, which is true for ND licences so the page knows never
    to crop that image."""
    identifier = card.get("image_identifier")
    page_url = card.get("image_references")
    creator = card.get("image_creator")
    licence = describe_licence(card.get("image_license_code"))

    if not (has_text(identifier) and has_text(page_url) and has_text(creator)
            and licence is not None):
        return None

    licence_label, licence_url = licence
    rightsholder = card.get("image_rightsholder")

    return {
        "url": display_url(identifier.strip()),
        "page_url": page_url.strip(),
        "creator": creator.strip(),
        "rightsholder": rightsholder.strip() if has_text(rightsholder) else None,
        "license_code": card["image_license_code"],
        "license_label": licence_label,
        "license_url": licence_url,
        "no_derivatives": "_ND" in card["image_license_code"],
    }
