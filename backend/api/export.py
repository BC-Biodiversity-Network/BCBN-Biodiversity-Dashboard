"""
export.py

The CSV downloads, for reading search results into R (asked for by Diane
and Evan at the November 4, 2026 meeting):

    GET /api/export/datasets      the Datasets search results
    GET /api/export/ranks/{rank}  the species list of a rank card
    GET /api/export/hexes/{cell}  the species list of a hexagon's panel
    GET /api/export/area          the species list of the hexagons under a box
                                  (area.py)

Each takes the same options as its list (minus paging) and gives every row
that passes them, in the order the list shows. The rows come from the very
code the lists use (datasets.dataset_request and ordered_datasets,
ranks.rank_card and cells.cell_species with no page limit), so a download
always holds exactly what the list holds, the species sensitive.py hides
left out the same way, and a bad request gets the same answer.

Only what comes from the sources, as it is: no record counts, years or
anything else BCBN worked out.

    Datasets (6 columns): title, creators, published, catalogue, doi, link
        title and creators as harvested (creators joined with "; "), the
        issued date as stored, the catalogue name the page shows, the DOI
        when the record's identifier is one (never another kind of id), and
        the link to the original catalogue the page uses.
    Species lists (4 columns): specieskey, scientific_name, english_name,
        bcsee_status: the card's Catalogue of Life name (no authorship),
        the English name the card shows, and the BC statuses the list chips
        show (Red, Blue, Yellow, Exotic) joined with "; ".
    Hexagon species list (5 columns): the same 4 and gbif_link, the GBIF
        search the hexagon panel links to for that species, hexagon and
        years (gbif_links.py).
    Area species list (5 columns): the same 5, gbif_link being the GBIF
        search for that species inside the box (area.py).

The file: UTF-8 text with a byte order mark (BOM, three bytes) at the very
start, so Excel on Windows reads it as UTF-8 and shows accented letters
correctly; standard CSV quoting (a value holding a comma, a quote mark or a
line break is put in quotes, a quote mark inside written twice); lines
ending in CR LF. After the BOM it starts with exactly 4 lines, each
starting with "# ": what the file is and the export date (Vancouver time),
how the list was made (only the options used), the sources, and how to read
it in R. Then the column names and the rows. Anything a visitor typed that
goes into those 4 lines has its line breaks turned into spaces, so it can
never add a line. The BOM sits at the start of the first of those lines,
which read.csv(file, skip = 4) skips, so R (and Python, skipping 4 lines
the same way) reads the column names and rows without it. The file name
holds only letters, digits, "-" and "_".
"""

import csv
import io
import re
import unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo

from api.area import area_species
from api.cells import cards_gbif_keys, cell_species
from api.datasets import dataset_request, ordered_datasets
import h3

from api.gbif_links import gbif_cell_search_url, to_fixed
from api.ranks import rank_card

# The export date is the date in Vancouver.
VANCOUVER = ZoneInfo("America/Vancouver")

# The byte order mark that starts every file, so Excel knows it is UTF-8.
BOM = "\ufeff"

# How many "# " lines come before the column names, and the last of them.
HEADER_LINE_COUNT = 4
R_LINE = "To read in R: read.csv(file, skip = 4)"

# The columns of each file.
DATASET_COLUMNS = ["title", "creators", "published", "catalogue", "doi", "link"]
SPECIES_COLUMNS = ["specieskey", "scientific_name", "english_name", "bcsee_status"]
HEXAGON_COLUMNS = SPECIES_COLUMNS + ["gbif_link"]

# What each dataset row is read from (d is lunaris_datasets).
DATASET_SQL_COLUMNS = "d.title, d.creators, d.issued, d.source_name, d.link, d.link_type"

# A link the Datasets page follows (isWebLink in datasetText.js), and the
# start of a DOI written as a link (the export's link for a DOI record).
WEB_LINK = re.compile(r"^https?://[^\s'\"<>]+$", re.IGNORECASE)
DOI_LINK = "https://doi.org/"

# Every character Python or R could take as the end of a line.
LINE_BREAKS = re.compile(r"[\r\n\v\f\x1c-\x1e\x85  ]+")

# How the header names each option, as the pages word them.
DATASET_GROUP_LABELS = {"bc_data": "BC data", "pending": "May include BC"}
DATASET_SORT_LABELS = {"relevance": "relevance", "newest": "newest first", "oldest": "oldest first",
                       "title": "title A to Z"}
ACCESS_LABELS = {"open": "open access", "restricted": "restricted access"}
STATUS_LABELS = {"red": "Red", "blue": "Blue", "yellow": "Yellow", "exotic": "Exotic", "none": "no status"}
SPECIES_SORT_LABELS = {"most_records": "most records first", "fewest_records": "fewest records first",
                       "scientific": "scientific name A to Z", "english": "English name A to Z"}

# How long a part of a file name made from a search can be.
FILE_PART_LENGTH = 40


def export_date(now=None):
    """Return the export date, YYYY-MM-DD, in Vancouver time (now is a
    datetime with a time zone, for the tests; the clock otherwise)."""
    return (now or datetime.now(VANCOUVER)).astimezone(VANCOUVER).date().isoformat()


def one_line(text):
    """Return text for a header line: every line break turned into a space,
    runs of spaces made one, and the ends trimmed."""
    return " ".join(LINE_BREAKS.sub(" ", str(text)).split())


def quoted(text):
    """Return a visitor's words for a header line, in quote marks."""
    return f'"{one_line(text)}"'


def file_part(text):
    """Return text as a safe part of a file name: accents dropped, every run
    of other characters made one "-", at most FILE_PART_LENGTH long."""
    plain = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^A-Za-z0-9]+", "-", plain).strip("-")[:FILE_PART_LENGTH].strip("-")


def file_name(kind, parts, date):
    """Return a file name such as bcbn_species_Chordata_2000-2020_2026-11-09.csv
    from the kind of list, the parts that say which one (empty ones are left
    out), and the export date."""
    named = [part for part in (file_part(part) for part in parts if part) if part]
    return "_".join(["bcbn", kind, *named, date]) + ".csv"


def csv_text(header_lines, columns, rows):
    """Return the whole file: the 4 header lines, each starting with "# ",
    the column names, and the rows, with standard CSV quoting. None is
    written as an empty value."""
    lines = [one_line(line) for line in header_lines]
    if len(lines) != HEADER_LINE_COUNT:
        raise ValueError(f"a download has exactly {HEADER_LINE_COUNT} header lines; got {len(lines)}")
    buffer = io.StringIO(newline="")
    for line in lines:
        buffer.write(f"# {line}\r\n")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow(["" if value is None else value for value in row])
    return buffer.getvalue()


def csv_file(text):
    """Return the bytes of a download: the byte order mark, then the file's
    text (csv_text), all in UTF-8. The BOM goes in front of the first "# "
    line, so the file still has exactly 4 header lines."""
    return (BOM + text).encode("utf-8")


def or_list(values):
    """Return values joined for a header line: "a", "a or b", "a, b or c"."""
    values = [one_line(value) for value in values]
    return values[0] if len(values) == 1 else ", ".join(values[:-1]) + " or " + values[-1]


def years_text(word, year_from, year_to):
    """Return how a header line names a year range, either end open, or
    None when there is none: "published 2010 to 2020"."""
    if year_from is not None and year_to is not None:
        return f"{word} {year_from} to {year_to}"
    if year_from is not None:
        return f"{word} {year_from} or later"
    if year_to is not None:
        return f"{word} {year_to} or earlier"
    return None


# --- Datasets ------------------------------------------------------------------


def dataset_doi(link, link_type):
    """Return the DOI of a dataset whose identifier is a DOI (the export
    wrote it as https://doi.org/<DOI>), else None."""
    if link_type == "doi" and link and link.startswith(DOI_LINK):
        return link[len(DOI_LINK):]
    return None


def dataset_row(title, creators, issued, source_name, link, link_type):
    """Return one dataset's row: the 6 columns (see the top of this file)."""
    return [title, "; ".join(creators or []), issued, source_name, dataset_doi(link, link_type),
            link if WEB_LINK.match(link or "") else None]


def datasets_search_line(data, request):
    """Return the header line saying how the datasets list was made: the
    search words, each filter used, and the order."""
    filters = request["filters"]
    parts = [f"words {quoted(request['query'])}" if request["words"] else "no search words"]
    groups = [DATASET_GROUP_LABELS.get(group, group) for group in filters["frontend_group"]]
    parts.append(groups[0] + " only" if len(groups) == 1 else " and ".join(groups))
    if filters["catalogue"]:
        parts.append("catalogue " + or_list(data.dataset_catalogues.get(value, value)
                                            for value in filters["catalogue"]))
    if filters["final_class"]:
        parts.append("location class " + or_list(filters["final_class"]))
    published = years_text("published", filters["year_from"], filters["year_to"])
    if published:
        parts.append(published)
    if request["organism"]:
        parts.append(f"organism {quoted(request['organism'])}")
    if filters["access"]:
        parts.append(or_list(ACCESS_LABELS[value] for value in filters["access"]) + " only")
    if filters["mappable"]:
        parts.append("only datasets that can be shown on the map")
    species = request["species_asked"]
    if species:
        english = f", {species['english_name']}" if species["english_name"] else ""
        parts.append(f"linked to species {species['col_name']} (key {species['specieskey']}{english})")
    parts.append("sorted by " + DATASET_SORT_LABELS[request["sort"]])
    return "Search: " + "; ".join(one_line(part) for part in parts)


def export_datasets(data, date, query=None, catalogue=None, final_class=None, frontend_group=None,
                    sort=None, year_from=None, year_to=None, organism=None, access=None, mappable=False,
                    species=None):
    """Return (file name, CSV text) of every dataset the Datasets list keeps
    for these options, in its order. Raises what list_datasets raises for a
    bad request, since the request is checked by the same code."""
    cursor = data.cursor()
    request = dataset_request(data, cursor, query, catalogue, final_class, frontend_group, sort,
                              year_from, year_to, organism, access, mappable, species)
    rows = [dataset_row(*row) for row in ordered_datasets(cursor, request, DATASET_SQL_COLUMNS)]
    header = [f"BCBN Datasets search results, exported {date}",
              datasets_search_line(data, request),
              "Source: Lunaris dataset records, as harvested; each row links to the original catalogue",
              R_LINE]
    name = file_name("datasets", [" ".join(request["words"])], date)
    return name, csv_text(header, DATASET_COLUMNS, rows)


# --- Species lists ---------------------------------------------------------------


def species_row(result):
    """Return one species' 4 columns from a list result (species_list.py)."""
    return [result["specieskey"], result["col_name"], result["english_name"],
            "; ".join(result["statuses"]) or None]


def species_source_line(meta):
    """Return the header line naming the sources of a species list: the GBIF
    download, the Catalogue of Life release and the BCSEE export."""
    download, statuses = meta["gbif_download"], meta["statuses"]
    return ("Source: GBIF occurrence download " + (download.get("doi_url") or download.get("doi") or "")
            + f"; names: Catalogue of Life {meta['names']['col_release']}"
            + f"; English names and BC status: {statuses['status_source']}, provincial export of "
            + f"{statuses['status_source_date']}, accessed {statuses['status_pulled_on']}")


def path_text(path):
    """Return a rank's path for a header line: "kingdom Animalia > phylum
    Chordata", a missing rank written as "(missing)"."""
    return " > ".join(f"{step['rank']} {step['name'] or '(missing)'}" for step in path)


def species_options_text(answer):
    """Return the parts of a header line for a species list's own options
    (years, name filter, BC status, order) from the list's answer."""
    parts = []
    if answer["years_counted"]:
        parts.append(years_text("records from", *answer["years_counted"]))
    if answer["q"] is not None and answer["q"].strip():
        parts.append(f"names matching {quoted(answer['q'])}")
    if answer["status"]:
        parts.append("BC status " + or_list(STATUS_LABELS[status] for status in answer["status"]))
    parts.append("sorted by " + SPECIES_SORT_LABELS[answer["sort"]])
    return parts


def years_part(answer):
    """Return the years of a list for its file name ("2000-2020"), or None."""
    return "-".join(str(year) for year in answer["years_counted"]) if answer["years_counted"] else None


def export_rank(data, date, rank, given, q=None, statuses=None, sort=None, year_from=None, year_to=None):
    """Return (file name, CSV text) of every species on a rank card's list
    for these options, in its order. The list is the card's own (rank_card
    with no page limit), so it raises what the card raises."""
    card = rank_card(data, rank, given, 0, None, q, statuses, sort, year_from, year_to)
    rows = [species_row(member) for member in card["members"]]
    parts = [f"species of {card['rank']} {card['name']} ({path_text(card['path'])})",
             *species_options_text(card)]
    header = [f"BCBN species list for {card['rank']} {card['name']}, exported {date}",
              "List: " + "; ".join(parts), species_source_line(data.meta), R_LINE]
    name = file_name("species", [card["name"], years_part(card)], date)
    return name, csv_text(header, SPECIES_COLUMNS, rows)


def export_hexagon(data, date, cell, q=None, statuses=None, sort=None, year_from=None, year_to=None,
                   rank=None, rank_given=None):
    """Return (file name, CSV text) of every species on a hexagon's list for
    these options, in its order, each with the GBIF search the hexagon panel
    links to for it (its card key and every key merged into it, the
    hexagon, the years counted). The list is the panel's own (cell_species
    with no page limit), so it raises what the panel's list raises."""
    answer = cell_species(data, cell, year_from, year_to, q, None, 0, None, rank, rank_given, statuses, sort)
    years = answer["years_counted"]
    keys = cards_gbif_keys(data.cursor(), [result["specieskey"] for result in answer["results"]])
    rows = [species_row(result) + [gbif_cell_search_url(keys[result["specieskey"]], answer["cell"], years)]
            for result in answer["results"]]
    parts = [f"species in H3 hexagon {answer['cell']}, a hexagon of about {km2_text(h3.cell_area(answer['cell'], 'km^2'))}"]
    if answer["rank"]:
        label = answer["rank"]
        parts.append(f"only {label['rank']} {label['name']} ({path_text(label['path'])})")
    parts += species_options_text(answer)
    header = [f"BCBN species list for hexagon {answer['cell']}, exported {date}",
              "List: " + "; ".join(parts), species_source_line(data.meta), R_LINE]
    name = file_name("species_hexagon",
                     [answer["cell"], answer["rank"]["name"] if answer["rank"] else None, years_part(answer)],
                     date)
    return name, csv_text(header, HEXAGON_COLUMNS, rows)


def km2_text(value):
    """Write an area for a header line the way the site writes it (km2Text
    in frontend/src/HexagonPanel.jsx): one decimal below 10 km², else the
    nearest km² with commas, halves rounded up ("5.2 km²", "1,504 km²").
    Files name the hexagons' size, never H3's resolution number."""
    if value < 10:
        return f"{to_fixed(value, 1)} km²"
    return f"{int(to_fixed(value, 0)):,} km²"


def degrees(value):
    """Write a box edge for a header line: up to six decimals, no trailing
    zeros ("-120.6", "50")."""
    return format(value, ".6f").rstrip("0").rstrip(".")


def export_area(data, date, west, south, east, north, res, q=None, statuses=None, sort=None,
                year_from=None, year_to=None, rank=None, rank_given=None):
    """Return (file name, CSV text) of every species in the hexagons under a
    box for these options, in the list's order, each with its GBIF search
    inside the box for the years counted. The list is the area search's own
    (area_species with no page limit), so it raises what that raises."""
    answer = area_species(data, west, south, east, north, res, year_from, year_to, q, 0, None, rank,
                          rank_given, statuses, sort)
    rows = [species_row(result) + [result["gbif_link"]] for result in answer["results"]]
    box = answer["box"]
    count = answer["hexagon_count"]
    size = km2_text(h3.average_hexagon_area(answer["res"], "km^2"))
    parts = [f"species in the {count:,} {'hexagon' if count == 1 else 'hexagons'} of about {size} "
             f"{'' if count == 1 else 'each '}that touch the box west {degrees(box['west'])}, "
             f"south {degrees(box['south'])}, east {degrees(box['east'])}, north {degrees(box['north'])}"]
    if answer["rank"]:
        label = answer["rank"]
        parts.append(f"only {label['rank']} {label['name']} ({path_text(label['path'])})")
    parts += species_options_text(answer)
    header = [f"BCBN species list for an area, exported {date}",
              "List: " + "; ".join(parts), species_source_line(data.meta), R_LINE]
    name = file_name("species_area", [answer["rank"]["name"] if answer["rank"] else None, years_part(answer)], date)
    return name, csv_text(header, HEXAGON_COLUMNS, rows)
