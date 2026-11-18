"""
dataset_licences.py

A dataset's licences as the dataset's detail page shows them, for the
licence column of the Datasets CSV (export.py). The page's own code is
readLicence and readLicences in frontend/src/datasetText.js and the Licence
row of DatasetDetail.jsx; this file follows it step by step, and
tests/test_dataset_licences.py runs both on the same entries (the page's
code with node and jsdom) to check they agree.

The rule (the page's):

    - A rights entry with no name and a URI starting
      "info:eu-repo/semantics/" is an access right (open, restricted...),
      not a licence: the page shows it under Access, so it is left out.
    - The name "None" with no URI is a catalogue's way of saying there is
      no licence (2 records in October 2026), so it is left out too.
    - The record's own licences (harvested from Lunaris) come first. Only
      when the record has none does the page show the licences BCBN found
      when checking the original catalogue in November 2026
      (dataset_source_checks.parquet), marked "checked by BCBN".
    - The one exception is a short hand-decided list, licence_hand_decisions.csv
      next to this file: for a dataset on it, the record's own licence is
      set aside (a dead link, or something that is not a licence) and the
      checked one is shown, as if the record had none. The harvested text
      stays in the data unchanged. The API reads the list once when it
      starts (datasets.prepare_datasets) and tells the page which datasets
      it covers (record_licence_set_aside, with the reason the page shows),
      so the page and the CSV follow one list.

What is written for each licence is the text the page shows, in full:
plain text, with HTML tags taken out and entities such as &amp; read (193
records hold HTML in a licence), runs of spaces made one, never shortened
(the page shortens a long one and shows the whole text behind a button).
Some catalogues split one HTML link across the name and the URI ("<a
href='" and the rest); like the page, the two are joined again and the
link's text and address are used. A licence given only as a URI is named
the way the page names it: the SPDX id for an spdx.org address ("CC0-1.0"),
else the URI. Then, when the page links the licence (an http or https
address), the address in brackets: "Open Government Licence (https://...)".
"""

import csv
import re
from html.parser import HTMLParser
from pathlib import Path

# The hand-decided list of datasets whose own licence is set aside (see the
# top of this file). It is tracked by git, so it sits with the code, not in
# data/. The tests point this at their own list.
HAND_DECISIONS_FILE = Path(__file__).resolve().parent / "licence_hand_decisions.csv"

# Its columns: the shown dataset's id, its title (as a check that the id is
# the dataset meant), why its own licence is set aside (in plain English;
# the page shows it), and who decided and when.
HAND_DECISION_COLUMNS = ["dataset_id", "title", "reason", "decided_by"]

# The DataCite rights URIs that give an access right, not a licence.
ACCESS_RIGHTS_PREFIX = "info:eu-repo/semantics/"

# The text a few catalogues put in place of a licence when there is none.
NO_LICENCE_PLACEHOLDER = "None"

# What the page calls a licence with neither a name nor a URI (no record
# had one in November 2026).
UNNAMED_LICENCE = "Unnamed licence"

# True for text that looks like it holds HTML tags (looksLikeHtml).
HTML_TAG = re.compile(r"</?[a-z][^>]*>?", re.IGNORECASE)

# The block tags plainText puts a space in front of, so words on either side
# of one are not run together.
BLOCK_TAG = re.compile(r"<(/?)(p|h[1-6]|li|ul|ol|br|hr|div|tr|td|blockquote)\b", re.IGNORECASE)

# A quote mark or angle bracket in a URI: the sign of an HTML link split
# across the name and the URI (repairSplitLink).
SPLIT_LINK_SIGN = re.compile(r"[<>'\"]")

# A web address the page links (isWebLink): http or https only.
WEB_LINK = re.compile(r"^https?://[^\s'\"<>]+$", re.IGNORECASE)

# A licence URI on spdx.org, whose id the page uses as its name (nameFromUri).
SPDX_URI = re.compile(r"^https?://spdx\.org/licenses/([^/]+?)(\.html)?$", re.IGNORECASE)

# Runs of white space, the way JavaScript's \s sees them (Python's \s
# leaves out the byte order mark, which JavaScript counts).
SPACES = re.compile(r"[\s﻿]+")


class _TextAndLinks(HTMLParser):
    """Reads HTML the way the page uses it: all its text (textContent) and
    the href of every <a> that has one, in order."""

    def __init__(self):
        """Start with no text and no links."""
        super().__init__(convert_charrefs=True)
        self.text = []
        self.links = []

    def handle_starttag(self, tag, attrs):
        """Keep the href of an <a> tag that has one."""
        if tag == "a":
            href = dict(attrs).get("href")
            if href is not None:
                self.links.append(href)

    def handle_data(self, data):
        """Keep the text between tags."""
        self.text.append(data)


def read_html(html):
    """Return (text, links) of HTML: its text with the tags taken out and
    entities read, and the href of each link in it."""
    parser = _TextAndLinks()
    parser.feed(html)
    parser.close()
    return "".join(parser.text), parser.links


def js_trim(text):
    """Return text with white space taken off both ends, as JavaScript's
    trim() does (it also takes off the byte order mark)."""
    return text.strip().strip("﻿").strip()


def looks_like_html(text):
    """True if text looks like it holds HTML tags."""
    return bool(HTML_TAG.search(text or ""))


def plain_text(text):
    """Return text that may hold HTML as plain text, as the page's
    plainText does: tags taken out (with a space before each block tag),
    entities read, runs of spaces and line breaks made single spaces."""
    if not text:
        return ""
    result = text
    if looks_like_html(text):
        result = read_html(BLOCK_TAG.sub(r" <\1\2", text))[0]
    return js_trim(SPACES.sub(" ", result))


def is_web_link(uri):
    """True for an address the page makes a link (http or https only)."""
    return bool(WEB_LINK.match(uri or ""))


def name_from_uri(uri):
    """Return the name the page gives a licence that has only a URI: the
    SPDX id for an spdx.org address, else the URI itself."""
    match = SPDX_URI.match(uri)
    return match.group(1) if match else uri


def repair_split_link(name, uri):
    """Return (name, uri) for an HTML link a catalogue split across the
    name and the URI, joined again: the first link's text and address, or
    with no link the text (as the URI too when it is a web address). Return
    None when the entry is not split that way."""
    if not looks_like_html(name) or not SPLIT_LINK_SIGN.search(uri):
        return None
    joined = name + uri
    links = read_html(joined)[1]
    if links:
        # The page reads the link's text from the first <a href>; only its
        # own text, so read the joined HTML up to the end of that link.
        return plain_text(first_link_text(joined)), js_trim(links[0])
    text = plain_text(joined)
    return text, text if is_web_link(text) else ""


class _FirstLinkText(HTMLParser):
    """Reads the text inside the first <a> that has an href."""

    def __init__(self):
        """Start outside any link."""
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.done = False
        self.text = []

    def handle_starttag(self, tag, attrs):
        """Note the start of the first link, and tags nested in it."""
        if self.done:
            return
        if self.depth:
            if tag == "a":
                # A new <a> closes the open one in HTML.
                self.depth, self.done = 0, True
            return
        if tag == "a" and dict(attrs).get("href") is not None:
            self.depth = 1

    def handle_endtag(self, tag):
        """Stop at the end of the first link."""
        if self.depth and tag == "a":
            self.depth, self.done = 0, True

    def handle_data(self, data):
        """Keep text while inside the first link."""
        if self.depth and not self.done:
            self.text.append(data)


def first_link_text(html):
    """Return the text inside the first <a href> of html."""
    parser = _FirstLinkText()
    parser.feed(html)
    parser.close()
    return "".join(parser.text)


def licence_text(entry):
    """Return how one rights entry ({"name", "uri"}, as stored) is written
    in the CSV, or None when the page does not show it as a licence (an
    access right, or the "None" placeholder): the page's full text for it,
    then its link in brackets when the page links it and the text is not
    the link itself."""
    name = entry.get("name") or ""
    uri = js_trim(entry.get("uri") or "")
    if not js_trim(name) and uri.startswith(ACCESS_RIGHTS_PREFIX):
        return None
    if name == NO_LICENCE_PLACEHOLDER and not uri:
        return None
    repaired = repair_split_link(name, uri)
    if repaired:
        name, uri = repaired
    text = plain_text(name) or (name_from_uri(uri) if uri else UNNAMED_LICENCE)
    link = uri if is_web_link(uri) else None
    return f"{text} ({link})" if link and link != text else text


def licence_texts(entries):
    """Return the licences among a list of rights entries, each written by
    licence_text, leaving out what is not a licence."""
    texts = (licence_text(entry) for entry in entries or [])
    return [text for text in texts if text is not None]


def shown_licences(record_licences, checked_licences, record_set_aside=False):
    """Return the licences the detail page shows for a dataset, written for
    the CSV: the record's own, or when it has none, the ones the November
    2026 source check found (None when not checked). record_set_aside is
    true for a dataset on the hand-decided list: its own licences are then
    left out, so the checked ones are shown."""
    own = [] if record_set_aside else licence_texts(record_licences)
    return own or licence_texts(checked_licences)


def read_hand_decisions(path=None):
    """Return the hand-decided list as {dataset id: its row}, each row a
    dict of HAND_DECISION_COLUMNS, read from path (HAND_DECISIONS_FILE when
    None). Raises ValueError when the columns are not exactly those, a
    value is empty or a dataset is listed twice."""
    path = Path(path or HAND_DECISIONS_FILE)
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != HAND_DECISION_COLUMNS:
            raise ValueError(f"{path.name}: the columns must be {', '.join(HAND_DECISION_COLUMNS)}; "
                             f"got {', '.join(reader.fieldnames or [])}")
        decisions = {}
        for line, row in enumerate(reader, start=2):
            if any(not (row[column] or "").strip() for column in HAND_DECISION_COLUMNS):
                raise ValueError(f"{path.name}, line {line}: every column needs a value")
            if row["dataset_id"] in decisions:
                raise ValueError(f"{path.name}, line {line}: {row['dataset_id']} is listed twice")
            decisions[row["dataset_id"]] = {column: row[column] for column in HAND_DECISION_COLUMNS}
    return decisions


def hand_decision_problems(cursor, decisions):
    """Return a list of problems with the hand-decided list against the
    data: each dataset must be one the site shows (not another copy's id),
    with the title the list gives, and the source check must have a licence
    for it (else setting its own aside would leave it with none)."""
    problems = []
    for dataset_id, row in decisions.items():
        found = cursor.execute(
            """SELECT d.title, s.licences FROM lunaris_datasets AS d
               LEFT JOIN dataset_source_checks AS s ON s.dataset_id = d.id
               WHERE d.id = ?""", [dataset_id]).fetchone()
        if found is None:
            problems.append(f"{dataset_id} is not a dataset on the site")
        elif found[0] != row["title"]:
            problems.append(f"{dataset_id} is titled {found[0]!r} in the data, not {row['title']!r}")
        elif not licence_texts([dict(entry) for entry in found[1] or []]):
            problems.append(f"{dataset_id} has no licence from the source check to show instead")
    return problems
