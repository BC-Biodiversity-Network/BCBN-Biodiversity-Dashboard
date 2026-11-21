"""
dataone_common.py

Small helpers shared by the DataONE pipeline scripts, for matching records
by DOI, UUID and title. They were first written in the third DataONE probe
(exploration/dataone/probe_dataone_3.py, not committed) and are copied here
unchanged, so the pipeline does not depend on exploration code. The probe
keeps its own copies.

norm_for_similarity and title_similarity compare the titles of a DataONE
record and the Lunaris record it matched by DOI or UUID (match_lunaris.py
and exploration/dataone/check_lunaris_match.py). They were first written
in check_lunaris_match.py on 2026-11-20 and moved here unchanged.
"""

import difflib
import re

# A DOI anywhere in a text: "10.", a registrant code of 4 to 9 digits, "/",
# then everything up to a space, a quote, an angle bracket, "?", "&" or "#".
DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"'<>?&#]+", re.IGNORECASE)

# A Research Workspace id, which hides a DOI: 10.24431_rw1k59m_20210915T201228Z
# is the DOI 10.24431/rw1k59m, with a timestamp after it.
RW_STYLE = re.compile(r"^(10\.\d{4,9})_([A-Za-z0-9.]+)_\d{8}T\d{6}Z?$")

# A UUID written with hyphens (8-4-4-4-12 hexadecimal digits), any case.
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def norm_doi(value):
    """
    Normalise one DOI for matching: lower case, no 'doi:' or resolver
    prefix, no trailing punctuation. Returns None if there is no DOI in it.
    """
    if value is None or (isinstance(value, float) and value != value):
        return None
    m = DOI_RE.search(str(value))
    if not m:
        return None
    return m.group(0).lower().rstrip(".,;)]}")


def norm_title(value):
    """Normalise a title for a rough match: lower case, letters and digits only."""
    if not isinstance(value, str):
        return ""
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def strip_lang(title):
    """Drop a trailing language tag such as '@en' that some sources add to titles."""
    return re.sub(r"@[a-z]{2}$", "", str(title or ""))


def norm_for_similarity(text):
    """
    Normalise a text before comparing it: lower case, drop a trailing "@xx"
    (or "@xx-yy") language tag, turn punctuation into spaces, collapse
    whitespace. Returns "" for a missing value.
    """
    if not isinstance(text, str):
        return ""
    text = " ".join(text.lower().split())
    text = re.sub(r"@[a-z]{2}(-[a-z]{2})?$", "", text)
    text = re.sub(r"[^\w\s]|_", " ", text)
    return " ".join(text.split())


def title_similarity(a, b):
    """
    Return difflib's similarity ratio (0 to 1) of two texts that have
    already been through norm_for_similarity; NaN if either is empty.
    """
    if not a or not b:
        return float("nan")
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()
