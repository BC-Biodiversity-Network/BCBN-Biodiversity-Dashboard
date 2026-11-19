"""
build_label_set.py

Build a small DataONE label set (200 records) for Lucia to label by hand,
to test the filter 2 and filter 3 prompts before they run on Fir. Reads
only local files; makes no requests.

Inputs (harvest folder): dataone_groups.parquet, dataone_lunaris_match.parquet,
dataone_full_harvest_versions.parquet (the record's fields) and
originals_boxes.parquet (every box of the multi-box records).

The sample, drawn with a fixed seed (SEED). Every bucket except the last
two takes only records that go to filter 2 and have no Lunaris match at all
(match_type none), so the buckets do not overlap and no record is one that
would skip filter 2:
  no_box                                   80: HD 20, DRYAD 20, BOREALIS 6,
                                               PANGAEA 7, 27 from all others
  touches_bc_partly                        35  (one box or several)
  inside_bc + touches_bc_all_inside        20
  west_gt_east                             20
  sign_check                               13
  send_to_llm (multi-box)                  12
  title_only Lunaris matches                6  (any group)
  DOI or UUID Lunaris matches              14: 7 Lunaris filter 2 yes, 7 no
The rows are then shuffled, so the buckets are not in blocks.

Outputs, in OUT_DIR (outside the repo):
  label_set.csv, label_set.xlsx
      row, id, datasource, title, abstract (exactly as harvested), keywords,
      box_or_point (the numbers), our_note_on_box (added by us, not from the
      record), link (the record's page on search.dataone.org), then the
      empty columns biodiversity, bc_class and notes. Nothing that hints at
      the answer: no group, no Lunaris answer. The xlsx has dropdowns for
      biodiversity and bc_class and wrapped text.
  answers_hidden.csv
      row, id, bucket, group, match_type, and for the Lunaris matches the
      Lunaris ids, filter 2 decision and final class.
  README.txt
      the bc_class values (copied from the Lunaris filter 3 class
      definitions in pipeline/lunaris/llm/geo_pass2_classes.py), how to
      label "unsure", what filter 2 counts as biodiversity data (copied from
      prompt v6 in pipeline/lunaris/llm/prompt_v6.py and from decide() in
      exploration/llm/score_trial.py; the copies are checked against those
      files before writing), and where the scope rules are.
--readme-only rewrites README.txt alone.
The script stops if OUT_DIR already holds files, unless --replace.

Run, from backend/:
    ../.venv/bin/python pipeline/dataone/build_label_set.py
"""

import argparse
import os
import re
import sys
from urllib.parse import quote

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from classify_multibox_records import BOXES, EDGES, HARVEST, INDEX_EDGES, VERSIONS  # noqa: E402

GROUPS = os.path.join(HARVEST, "dataone_groups.parquet")
MATCH = os.path.join(HARVEST, "dataone_lunaris_match.parquet")
OUT_DIR = os.path.expanduser("~/Desktop/BCBN/Results/dataone_label_set_2026-11-19")
SEED = 20261119
VIEW = "https://search.dataone.org/view/"

# (bucket name, groups it draws from, number), drawn in this order.
GROUP_BUCKETS = [
    ("touches_bc_partly", ["touches_bc_partly"], 35),
    ("inside_bc", ["inside_bc", "touches_bc_all_inside"], 20),
    ("west_gt_east", ["west_gt_east"], 20),
    ("sign_check", ["sign_check"], 13),
    ("multi_box_send_to_llm", ["send_to_llm"], 12),
]
NO_BOX_BY_SOURCE = {"HD": 20, "DRYAD": 20, "BOREALIS": 6, "PANGAEA": 7}
NO_BOX_OTHERS = 27
TITLE_ONLY = 6
LUNARIS_EACH = 7       # Lunaris filter 2 yes, and the same number of no

BIODIVERSITY = ["yes", "no", "unsure"]
BC_CLASSES = ["bc_only", "canada_incl_bc", "all_canada", "bc_and_outside_canada", "global", "elsewhere",
              "unknown", "unsure"]
NOTE_HEADER = "our_note_on_box (added by us, not from the record)"
LABEL_COLUMNS = ["row", "id", "datasource", "title", "abstract", "keywords", "box_or_point", NOTE_HEADER, "link",
                 "biodiversity", "bc_class", "notes"]
# Characters Excel cannot store (control characters other than tab, line feed and carriage return).
ILLEGAL_XLSX = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
EXCEL_CELL_LIMIT = 32_767

README = """DataONE label set, 2026-11-19
=============================

200 DataONE records, in label_set.xlsx (the same rows as label_set.csv).
Fill in three columns for each row:

biodiversity   yes, no or unsure: is this dataset biodiversity data (the
               question filter 2 asks)?
bc_class       where the data were collected, one of the classes below.
notes          anything worth saying, for example why you chose unsure.

The column "{note}" is a short note we added about the box
or point (for example "west is greater than east"). It is not part of the
record. The box_or_point column gives the numbers as DataONE has them; for a
record with several boxes it lists every box read from the original document.

bc_class values
---------------
Copied from the Lunaris filter 3 class definitions
(backend/pipeline/lunaris/llm/geo_pass2_classes.py):

  bc_only                data only in BC.
  canada_incl_bc         several regions inside Canada including BC, but not
                         Canada-wide (BC plus one neighbouring province counts).
  all_canada             a Canada-wide dataset.
  bc_and_outside_canada  BC, or Canadian regions including BC, plus places
                         outside Canada (North America-wide or Pacific
                         Northwest-wide data that includes BC counts here).
  global                 worldwide data; a record that only says "global".
  elsewhere              the data were not collected in BC.
  unknown                the record does not say enough.

How to label "unsure"
---------------------
- biodiversity: choose "unsure" when you cannot tell from the record whether
  it is biodiversity data. Say why in notes.
- bc_class: "unknown" is a class, not a doubt: it means the record itself does
  not say enough to place it, and it is the answer we want the model to give
  then. Choose "unsure" only when the record does say enough but you cannot
  decide between classes; write the classes you hesitate between in notes
  (for example "bc_only or canada_incl_bc").
- You may open the link to read the dataset page, but label from what the
  record says where you can: the model sees only the record.

What filter 2 counts as biodiversity data
-----------------------------------------
Filter 2 works in two steps, and both are copied below word for word.

1. The model reports facts about the record. It is told not to decide:
   "Do not decide whether it belongs in a biodiversity database, that
   decision is made separately." The prompt is version 6, the version both
   Lunaris full runs used (qwen3.5:4b and qwen3.6:35b-a3b; prompt_version 6
   in their run.json files), in backend/pipeline/lunaris/llm/prompt_v6.py
   (BASE_INSTRUCTIONS, plus worked examples in prompt_examples_v6.json in
   the same folder). The field notes for the facts the decision reads:

   concerns_living_things: true if the record is about living organisms or
   the ecosystems they live in. Physical and chemical measurements are
   false, such as water quality, weather, ocean currents, temperature,
   conductivity, irradiance, bathymetry, wave height, and carbon or
   greenhouse gas fluxes. They stay false even when the record mentions an
   ecosystem, a forest or another living setting, unless the record
   actually samples or counts organisms.

   topic: pick the single category that best describes what the record is
   MAINLY about. Do not choose a category just because living things are
   mentioned somewhere.
     organisms: the subject is particular organisms, their populations,
   behaviour, diet, genetics or distribution.
     habitat_or_ecosystem: the subject is a place, a water body or an
   ecosystem, and organisms are part of the picture rather than the point. A
   study of a lake's ecology is this, not organisms. Vegetation cover, forest
   cover, tree canopy and similar maps or layers that describe living plants
   also go here, not under land_and_boundaries.
     forestry_agriculture_fisheries: the subject is growing, harvesting or
   managing organisms as a resource, including crops, timber and fish stocks.
     land_and_boundaries: park and protected area boundaries, general land
   use, zoning, parcel and cadastral data, administrative boundaries,
   topographic maps.
     physical_environment: physical or chemical measurements with no living
   subject, such as water temperature, bathymetry, weather, geophysical
   surveys.
     administrative: government records such as budgets, schedules,
   addresses, election boundaries.
     human_health: human medical or health records.
     other: none of the above fits.

   about_organisms_themselves: answer this ONLY when topic is
   forestry_agriculture_fisheries. yes when the record is about the
   organisms, for example tree species composition, crop trials, fish
   populations. Catch or harvest statistics broken down by species or
   species group also count as yes, because they say which organisms were
   taken and how many. no when it is about tenure boundaries, harvest volumes
   or values with no breakdown by species, farm income, prices or licensing.
   For every other topic answer not_applicable.

   ecological_purpose_stated: answer this ONLY when topic is
   land_and_boundaries. yes when the record states an ecological or
   conservation purpose. no when it is a plain administrative or
   cartographic product. For every other topic answer not_applicable.

   experimental_animals: yes only in two cases. First, when the organisms
   are standard lab model organisms used as lab tools: lab mice and rats,
   fruit flies, C. elegans, zebrafish lab lines, and lab populations of
   E. coli or yeast. Second, when the record is an experiment run on farmed
   animals, such as a broiler chicken leg health trial. no when the
   experiment is on a wild or native species, even if it is done in a lab:
   cultures of wild algae, fish or invertebrates exposed to a drug or
   pollutant, and other ecotoxicology or behaviour experiments on such
   species are all no. Also no for wild organisms, field surveys, crop or
   plant variety trials, aquaculture stock and other farmed organisms that
   are not the subject of an animal experiment. Answer yes or no for every
   record.

   extinct_only: yes when the only organisms the record is about are
   extinct, such as fossils or ancient DNA of extinct species or
   populations. no when any living species is part of the subject, or when
   no organisms are named. Answer yes or no for every record.

2. Code turns those facts into yes or no: decide() in
   backend/exploration/llm/score_trial.py (with out_as_whole_category() in
   the same file). The rules, checked in this order; the first that applies
   decides. The quoted text is the code's own comments.

   a. experimental_animals is yes -> no. "Whole category rule: experimental
      animals are out. Lab mice, fruit fly behaviour studies, experimental
      E. coli populations, and experiments run on farmed animals such as the
      broiler chicken leg health trial."
   b. extinct_only is yes -> no. "Whole category rule: extinct species are
      out, such as the ancient DNA study of Ice Age deer."
   c. topic is physical_environment -> no. "Physical environment data is out:
      water quality, currents, temperature, conductivity, weather,
      irradiance, carbon and gas fluxes. This is criterion 1 and boundary
      question 4, "environmental data not ecological"."
   d. topic is human_health -> no. "Criterion 5. Human health and biomedical
      records are out, unless the subject is wild organisms, in which case
      the model would not have called the topic human_health in the first
      place."
   e. topic is administrative -> no. "Criterion 4. Government administrative
      records are out."
   f. concerns_living_things is false -> no. "Criterion 1. Nothing living in
      it, nothing to index. Purely physical measurements are mostly caught
      already by the physical environment rule above. This catches the rest."
   g. topic is forestry_agriculture_fisheries -> yes only if
      about_organisms_themselves is yes. "Criterion 2. Forestry, agriculture
      and fisheries count only when the record is about the organisms, not
      about tenure, volumes or prices."
   h. topic is land_and_boundaries -> yes only if ecological_purpose_stated
      is yes. "Criterion 3. Boundaries, land cover and maps count only when
      an ecological or conservation purpose is stated."
   i. Anything else -> yes. "Everything else that concerns living things is
      in."

   For Lunaris, a few hand decisions were applied on top
   (backend/pipeline/lunaris/filter2_hand_decisions.csv); they are not
   part of the rule.

The scope rules behind these steps
----------------------------------
- backend/exploration/llm/scope_criteria.md (in the repo; last changed
  2026-10-07 in commit d5ce371). The rules settled with Evan, with criteria
  1 and 3 reworded on 2026-10-07. decide() follows this version.
- ~/Desktop/BCBN/Results/Claude outputs/scope_criteria.md is an older copy
  (2026-09-23), from before that rewording. Use the repo version.
- backend/exploration/llm/test_set_to_label_guide.md (2026-09-21) is the
  guide for the first labelling round (is_biodiversity, subject, form).
"""

# Text the README copies, each checked against its source before the
# README is written: (source file, text). Spaces and line breaks are
# ignored in the comparison, and "#" too for the code comments.
PROMPT_FILE = os.path.join(HERE, "..", "lunaris", "llm", "prompt_v6.py")
DECIDE_FILE = os.path.join(HERE, "..", "..", "exploration", "llm", "score_trial.py")
COPIED = [
    (PROMPT_FILE, "Do not decide whether it belongs in a biodiversity database, that decision is made separately."),
    (PROMPT_FILE, "unless the record actually samples or counts organisms."),
    (PROMPT_FILE, "Catch or harvest statistics broken down by species or species group also count as yes"),
    (PROMPT_FILE, "are not the subject of an animal experiment. Answer yes or no for every record."),
    (PROMPT_FILE, "no when any living species is part of the subject, or when no organisms are named."),
    (DECIDE_FILE, "Whole category rule: extinct species are out, such as the ancient DNA study of Ice Age deer."),
    (DECIDE_FILE, "This is criterion 1 and boundary question 4, \"environmental data not ecological\"."),
    (DECIDE_FILE, "Criterion 4. Government administrative records are out."),
    (DECIDE_FILE, "Criterion 3. Boundaries, land cover and maps count only when an ecological or conservation "
                  "purpose is stated."),
    (DECIDE_FILE, "Everything else that concerns living things is in."),
]
# The field notes copied in full: each README paragraph must appear in the prompt.
PROMPT_FIELDS = ["concerns_living_things", "topic", "about_organisms_themselves", "ecological_purpose_stated",
                 "experimental_animals", "extinct_only"]


def squash(text, drop_hash=False):
    """Return text with line breaks, runs of spaces and Python line continuations made single spaces."""
    text = text.replace("\\\n", " ")
    if drop_hash:
        text = text.replace("#", " ")
    return " ".join(text.split())


def readme_text():
    """
    Return the README text, after checking that what it copies still matches
    its sources: every listed quote, and the full field notes of the prompt
    facts the decision reads. Stops if anything differs.
    """
    text = README.format(note=NOTE_HEADER)
    sources = {}
    for path, quote_text in COPIED:
        if path not in sources:
            with open(path, encoding="utf-8") as handle:
                sources[path] = squash(handle.read(), drop_hash=path == DECIDE_FILE)
        if squash(quote_text) not in sources[path]:
            sys.exit(f"STOPPED: the README quotes text that is no longer in {path}: {quote_text[:60]}...")
        if squash(quote_text) not in squash(text):
            sys.exit(f"STOPPED: the README does not hold the quote it is checked for: {quote_text[:60]}...")
    with open(PROMPT_FILE, encoding="utf-8") as handle:
        prompt = handle.read().replace("\\\n", "")
    for field in PROMPT_FIELDS:
        # The prompt's note on this field: from "field: " to the next blank line.
        start = prompt.index(f"\n{field}: ") + 1
        paragraph = prompt[start:prompt.index("\n\n", start)]
        if squash(paragraph) not in squash(text):
            sys.exit(f"STOPPED: the README's note on {field} is not word for word the prompt's")
    return text


def write_readme():
    """Write README.txt in OUT_DIR."""
    with open(os.path.join(OUT_DIR, "README.txt"), "w", encoding="utf-8") as handle:
        handle.write(readme_text())


def check_out_dir(replace):
    """Stop unless OUT_DIR is new or empty, or --replace was given."""
    if os.path.isdir(OUT_DIR) and os.listdir(OUT_DIR) and not replace:
        sys.exit(f"STOPPED: {OUT_DIR} already holds files; pass --replace to write over them")
    os.makedirs(OUT_DIR, exist_ok=True)


def read_candidates():
    """Return the records that go to filter 2, with their group, datasource and Lunaris match columns."""
    groups = pd.read_parquet(GROUPS)
    groups = groups[groups["to_filter2"]]
    match = pd.read_parquet(MATCH).drop(columns=["datasource", "group"])
    table = groups.merge(match, on="id", how="left", validate="one_to_one")
    if table["match_type"].isna().any():
        sys.exit("STOPPED: some filter 2 records are missing from the match file")
    return table


def draw(pool, number, rng, label):
    """Return `number` rows drawn at random from pool; stop if pool is too small."""
    if len(pool) < number:
        sys.exit(f"STOPPED: {label} has only {len(pool)} records, {number} wanted")
    return pool.sample(number, random_state=rng)


def sample(table, rng):
    """Draw every bucket of the sample (see the module docstring) and return the rows with a bucket column."""
    parts = []
    plain = table[table["match_type"] == "none"]
    no_box = plain[plain["group"] == "no_box"]
    for source, number in NO_BOX_BY_SOURCE.items():
        parts.append(draw(no_box[no_box["datasource"] == source], number, rng, f"no_box {source}")
                     .assign(bucket=f"no_box {source}"))
    others = no_box[~no_box["datasource"].isin(NO_BOX_BY_SOURCE)]
    parts.append(draw(others, NO_BOX_OTHERS, rng, "no_box others").assign(bucket="no_box other datasources"))
    for bucket, groups, number in GROUP_BUCKETS:
        parts.append(draw(plain[plain["group"].isin(groups)], number, rng, bucket).assign(bucket=bucket))
    parts.append(draw(table[table["match_type"] == "title_only"], TITLE_ONLY, rng, "title_only")
                 .assign(bucket="lunaris title_only"))
    strong = table[table["match_type"].isin(["doi", "uuid"])]
    for decision in ("yes", "no"):
        parts.append(draw(strong[strong["lunaris_filter2_summary"] == decision], LUNARIS_EACH, rng,
                          f"lunaris {decision}").assign(bucket=f"lunaris doi/uuid, filter 2 {decision}"))
    chosen = pd.concat(parts, ignore_index=True)
    if chosen["id"].duplicated().any():
        sys.exit("STOPPED: a record was drawn twice")
    chosen = chosen.sample(frac=1, random_state=rng).reset_index(drop=True)
    chosen.insert(0, "row", np.arange(1, len(chosen) + 1))
    return chosen


def read_fields(ids):
    """Return title, abstract, keywords and the four index box fields of the given records, by id."""
    columns = ["id", "title", "abstract", "keywords"] + list(INDEX_EDGES.values())
    fields = pq.read_table(VERSIONS, columns=columns, filters=[("id", "in", list(ids))]).to_pandas()
    fields = fields.drop_duplicates("id").rename(columns={v: k for k, v in INDEX_EDGES.items()})
    return fields.set_index("id")


def number(value):
    """Return a coordinate as short text ("49.25", "-123.1"), or "empty" when missing or NaN."""
    return "empty" if value is None or pd.isna(value) else f"{value:g}"


def box_text(north, south, east, west):
    """Return one box or point as text: "point: lat ..., lon ..." or "box: N .., S .., E .., W ..."."""
    if all(pd.notna(v) for v in (north, south, east, west)) and north == south and east == west:
        return f"point: lat {number(north)}, lon {number(west)}"
    return f"box: N {number(north)}, S {number(south)}, E {number(east)}, W {number(west)}"


def box_notes(north, south, east, west):
    """Return the facts worth noting about one box: west > east, south > north, a line, out-of-range values."""
    values = (north, south, east, west)
    if any(v is None or pd.isna(v) for v in values):
        missing = [e for e, v in zip(EDGES, values) if v is None or pd.isna(v)]
        return ["no box or point given"] if len(missing) == 4 else [f"box incomplete: {', '.join(missing)} empty"]
    notes = []
    if west > east:
        notes.append("west is greater than east")
    if south > north:
        notes.append("south is greater than north")
    if north == south and east == west:
        notes.append("a point (north = south, east = west)")
    elif north == south or east == west:
        notes.append("a line, not a box (" + ("north = south" if north == south else "east = west") + ")")
    if max(abs(north), abs(south)) > 90:
        notes.append("latitude outside -90 to 90")
    if max(abs(east), abs(west)) > 180:
        notes.append("longitude outside -180 to 180")
    return notes


def multi_box_cells(boxes):
    """Return (box_or_point, note) for a record with several boxes from its original document."""
    if boxes.empty:
        return "", "several boxes in DataONE's index, but the original document could not be read"
    lines, notes = [], []
    for number_, box in enumerate(boxes.itertuples(), start=1):
        lines.append(f"{number_}. " + box_text(box.north, box.south, box.east, box.west))
        for note in box_notes(box.north, box.south, box.east, box.west):
            if not note.startswith("a point"):
                notes.append(f"box {number_}: {note}")
    head = f"{len(boxes)} boxes in the original document (DataONE's search index shows only the first)"
    return "\n".join(lines), "; ".join([head] + notes)


def label_rows(chosen, fields, boxes):
    """Return the table Lucia labels: the record's fields, its box and our note, the link and empty columns."""
    multi = chosen["reason"].str.startswith("multi-box")
    rows = []
    for record, is_multi in zip(chosen.itertuples(), multi):
        f = fields.loc[record.id]
        if is_multi:
            shown, note = multi_box_cells(boxes[boxes["id"] == record.id])
        else:
            edges = [f[e] for e in EDGES]
            given = any(pd.notna(v) for v in edges)
            shown = box_text(*edges) if given else ""
            note = "; ".join(box_notes(*edges))
        keywords = f["keywords"]
        rows.append({"row": record.row, "id": record.id, "datasource": record.datasource,
                     "title": f["title"], "abstract": f["abstract"],
                     "keywords": "; ".join(keywords) if keywords is not None else "",
                     "box_or_point": shown, NOTE_HEADER: note, "link": VIEW + quote(record.id, safe=""),
                     "biodiversity": "", "bc_class": "", "notes": ""})
    return pd.DataFrame(rows, columns=LABEL_COLUMNS)


def hidden_rows(chosen):
    """Return the answer table: row, id, bucket, group, match type, and the Lunaris answers for the Lunaris buckets."""
    out = chosen[["row", "id", "bucket", "group", "match_type"]].copy()
    lunaris = chosen["bucket"].str.startswith("lunaris")
    join = lambda values: " | ".join(values) if values is not None else ""  # noqa: E731
    out["lunaris_ids"] = np.where(lunaris, chosen["lunaris_ids"].map(join), "")
    out["lunaris_filter2"] = np.where(lunaris, chosen["lunaris_filter2_summary"], "")
    out["lunaris_final_class"] = np.where(lunaris, chosen["lunaris_final_class"].map(join), "")
    return out.sort_values("row")


def excel_safe(value):
    """Return a cell value Excel can store: control characters removed, cut to Excel's cell limit."""
    if not isinstance(value, str):
        return value
    return ILLEGAL_XLSX.sub("", value)[:EXCEL_CELL_LIMIT]


def write_xlsx(labels, path):
    """Write the label table as xlsx: bold header, wrapped text, set widths, dropdowns for biodiversity and bc_class."""
    book = Workbook()
    sheet = book.active
    sheet.title = "label_set"
    sheet.append(LABEL_COLUMNS)
    for values in labels.itertuples(index=False):
        sheet.append([excel_safe(v) for v in values])
    widths = {"row": 6, "id": 28, "datasource": 12, "title": 40, "abstract": 90, "keywords": 30, "box_or_point": 32,
              NOTE_HEADER: 30, "link": 30, "biodiversity": 13, "bc_class": 22, "notes": 40}
    fill = PatternFill("solid", start_color="FFF2CC")
    for index, name in enumerate(LABEL_COLUMNS, start=1):
        letter = sheet.cell(row=1, column=index).column_letter
        sheet.column_dimensions[letter].width = widths[name]
        sheet.cell(row=1, column=index).font = Font(bold=True)
        if name in ("biodiversity", "bc_class", "notes"):
            sheet.cell(row=1, column=index).fill = fill
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    last = len(labels) + 1
    for name, values in (("biodiversity", BIODIVERSITY), ("bc_class", BC_CLASSES)):
        letter = sheet.cell(row=1, column=LABEL_COLUMNS.index(name) + 1).column_letter
        rule = DataValidation(type="list", formula1='"' + ",".join(values) + '"', allow_blank=True)
        rule.add(f"{letter}2:{letter}{last}")
        sheet.add_data_validation(rule)
    sheet.freeze_panes = "D2"
    book.save(path)


def report(chosen, labels):
    """Print the counts by bucket, group and datasource, and any text Excel could not hold exactly."""
    pd.set_option("display.width", 200)
    print("BY BUCKET")
    print(chosen["bucket"].value_counts().to_string())
    print(f"   total: {len(chosen)}")
    print("\nBY GROUP")
    print(chosen["group"].value_counts().to_string())
    print("\nBY DATASOURCE")
    print(chosen["datasource"].value_counts().to_string())
    changed = sum(isinstance(v, str) and excel_safe(v) != v for v in labels.to_numpy().ravel())
    print(f"\ncells changed for Excel (control characters or over {EXCEL_CELL_LIMIT:,} characters): {changed}")
    print(f"records with an empty abstract: {int(labels['abstract'].isna().sum() + (labels['abstract'] == '').sum())}")


def main():
    """Draw the sample and write the four files."""
    parser = argparse.ArgumentParser(description="Build the DataONE label set.")
    parser.add_argument("--replace", action="store_true", help="write over the files in the output folder")
    parser.add_argument("--readme-only", action="store_true",
                        help="rewrite only README.txt, leaving the label set and answers as they are")
    args = parser.parse_args()
    if args.readme_only:
        write_readme()
        print(f"wrote README.txt -> {OUT_DIR}")
        return 0
    check_out_dir(args.replace)
    rng = np.random.RandomState(SEED)
    chosen = sample(read_candidates(), rng)
    fields = read_fields(chosen["id"])
    boxes = pd.read_parquet(BOXES)
    boxes = boxes[boxes["id"].isin(set(chosen["id"]))].drop_duplicates(["id"] + EDGES + [f"{e}_text" for e in EDGES])
    labels = label_rows(chosen, fields, boxes)
    labels.to_csv(os.path.join(OUT_DIR, "label_set.csv"), index=False, encoding="utf-8-sig")
    write_xlsx(labels, os.path.join(OUT_DIR, "label_set.xlsx"))
    hidden_rows(chosen).to_csv(os.path.join(OUT_DIR, "answers_hidden.csv"), index=False, encoding="utf-8-sig")
    write_readme()
    report(chosen, labels)
    print(f"\nwrote label_set.csv, label_set.xlsx, answers_hidden.csv, README.txt -> {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
