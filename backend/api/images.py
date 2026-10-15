"""
images.py

Turns the image columns of a species card into the image the page shows,
or into nothing when the image cannot be credited properly.

The rule: an image is only shown with its creator, its licence and a link
to the original. So an image is returned only when the card has all four
of: the image URL, the link to the original, a creator, and a licence
code that can be read. Anything less and the card has no image. The rule
itself lives in shared/image_credit.py, which the pipeline also uses to
pick the species image, so the pipeline only picks images this module
will show.
"""

import re

from shared.image_credit import credit, has_text

# iNaturalist serves every photo in several sizes, and the "original" one
# the records point to can be several megabytes. The card uses "medium"
# (up to 500 pixels on the long side) instead. Only the file name changes,
# so the picture and its credit are the same.
INATURALIST_ORIGINAL = re.compile(
    r"^(https://(?:inaturalist-open-data\.s3\.amazonaws\.com|static\.inaturalist\.org)"
    r"/photos/\d+/)original(\.[A-Za-z]+)$")

# The CC BY codes build_dwca_tables.py writes: CC_BY with any of NC, SA,
# ND, with or without a version (CC_BY_SA_4_0, CC_BY_SA). This splits a
# code into its parts for the label; which codes count as readable is
# decided by shared/image_credit.py, and a test checks the two agree.
CC_LICENCE_CODE = re.compile(r"^CC_BY((?:_NC)?(?:_SA)?(?:_ND)?)(?:_([0-9]+)_([0-9]+))?$")


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
    # The card stores the image columns with an image_ prefix; the shared
    # rule uses the bc_media names.
    image = {column: card.get(f"image_{column}") for column in
             ("identifier", "references", "creator", "rightsholder", "license_code")}
    shown = credit(image)
    if shown is None:
        return None

    licence_label, licence_url = describe_licence(shown["license_code"])
    rightsholder = image["rightsholder"]

    return {
        "url": display_url(shown["url"]),
        "page_url": shown["link"],
        "creator": shown["creator"],
        "rightsholder": rightsholder.strip() if has_text(rightsholder) else None,
        "license_code": shown["license_code"],
        "license_label": licence_label,
        "license_url": licence_url,
        "no_derivatives": "_ND" in shown["license_code"],
    }
