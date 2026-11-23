"""
build_bc_label_set.py

Draw 50 more DataONE records for labelling where the data were collected
(bc_class), to test filter 3. The first label set
(~/Desktop/BCBN/Results/dataone_label_set_2026-11-19/) has only 10 BC
records among its 60 biodiversity ones, too few to measure how often
filter 3 misses BC data. This set leans towards records likely to be in
or near BC.

Who can be drawn: records filter 2 kept (keep_union true in
data/dataone/filter2_results/filter2_union.parquet), not reused from
Lunaris (reuse_lunaris false; those take their Lunaris filter 3 class),
and not in the first label set.

The strata, drawn in this order with one fixed seed (SEED); a stratum with
fewer records than asked is taken whole, and the report says so:
  no_box_bc_word   20  no box, and the title, keywords or abstract mention
                       BC: the Lunaris BC filter's own word check
                       (bc_filter_rules.bc_mentions: "British Columbia",
                       "Colombie-Britannique", "B.C.", "C.-B.", "BC", and
                       the places in bc_places.csv)
  partial_overlap  15  one box partly overlapping BC (touches_bc_partly,
                       not multi-box)
  w_gt_e_or_sign    5  west greater than east, or sign_check
  multi_box         5  several boxes: touches_bc_partly or send_to_llm
  no_box_no_word    5  no box and no BC word, as a check
Rows are then shuffled (same seed), so the stratum order does not show in
the row numbers.

--strict draws a second set of 50 (Lucia, 2026-11-23), after the first set
turned out weak (many "BC" hits meant breast cancer or a year, many
partial-overlap boxes were worldwide, the multi-box rows touched no BC).
It uses another seed (STRICT_SEED), writes to another folder, leaves out
every record of the first label set and of the first set of 50, and draws:
  no_box_bc_word   20  as above, but a record whose only BC words are a bare
                       "BC" or "Vancouver" does not count
  partial_overlap  15  one box partly overlapping BC that is not huge (under
                       5 times BC's area, the Lunaris BC filter's limit) and
                       has at least 10% of its area in BC, both measured by
                       bc_filter_rules.classify_box / measure_box
  w_gt_e_or_sign    5  as above
  multi_box         5  several boxes, at least one touching BC
                       (touches_bc_partly only, not send_to_llm)
  no_box_no_word    5  as above
Without --strict the first set is drawn exactly as before (same pool, seed
and folder), so it can be checked to come out the same.

Each row has the columns of the first label set, filled the same way by
build_label_set.py's own functions (label_rows: title, full abstract,
keywords, box_or_point with every box of a multi-box record read from its
original document, our note on the box, link), without its biodiversity
column (every record here was kept by filter 2), plus:
  abstract_cut_by_dataone  "yes" when the abstract ends with "..." (DataONE
                           cut it), otherwise "no"
  namedLocation, site, placeKey   DataONE's place fields, joined with "; "
  stratum
  bc_class, notes          empty, for Lucia

Outputs, in ~/Desktop/BCBN/Results/dataone_bc_label_set_2026-11-23/, or
dataone_bc_label_set_2026-11-23_strict/ with --strict (the script stops if
the folder holds files, unless --replace; --out writes elsewhere, to check
a draw without touching either folder):
  label_set.csv, label_set.xlsx  the same rows; the xlsx has a bc_class dropdown
  README.txt                     what the set is, with the bc_class
                                 definitions copied word for word from the
                                 first label set's README (checked on writing)

Run, from backend/:
    ../.venv/bin/python pipeline/dataone/build_bc_label_set.py            # the first 50
    ../.venv/bin/python pipeline/dataone/build_bc_label_set.py --strict   # the strict 50
"""

import argparse
import os
import sys

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(BACKEND, "pipeline", "lunaris"))
from bc_filter_rules import bc_mentions, classify_box, load_bc_shape, measure_box, read_bc_places  # noqa: E402
from build_label_set import (BC_CLASSES, BOXES, EDGES, GROUPS, NOTE_HEADER, VERSIONS, excel_safe,  # noqa: E402
                             label_rows, read_fields)
from classify_multibox_records import BC_BOUNDARY, INDEX_EDGES  # noqa: E402

UNION = os.path.join(BACKEND, "data", "dataone", "filter2_results", "filter2_union.parquet")
FIRST_SET = os.path.expanduser("~/Desktop/BCBN/Results/dataone_label_set_2026-11-19")
OUT_DIR = os.path.expanduser("~/Desktop/BCBN/Results/dataone_bc_label_set_2026-11-23")
SEED = 20261123
STRICT_OUT_DIR = os.path.expanduser("~/Desktop/BCBN/Results/dataone_bc_label_set_2026-11-23_strict")
STRICT_SEED = 20261124
# BC words that alone do not make a record count as mentioning BC in the
# strict draw: "BC" was often breast cancer, Boston College or a year, and
# "Vancouver" Fort Vancouver in Washington State. Compared in lower case.
WEAK_BC_WORDS = {"bc", "vancouver"}
# The strict partial-overlap stratum: at least this share of the box in BC.
MIN_SHARE_IN_BC = 0.10

# (stratum, how many), in the order they are drawn.
STRATA = [("no_box_bc_word", 20), ("partial_overlap", 15), ("w_gt_e_or_sign", 5), ("multi_box", 5),
          ("no_box_no_word", 5)]
PLACE_FIELDS = ["namedLocation", "site", "placeKey"]
CUT_MARK = "..."
COLUMNS = ["row", "id", "datasource", "title", "abstract", "abstract_cut_by_dataone", "keywords",
           *PLACE_FIELDS, "box_or_point", NOTE_HEADER, "link", "stratum", "bc_class", "notes"]

README_HEAD = """DataONE BC label set, 2026-11-23{title_extra}
================================{rule_extra}

50 more DataONE records, in label_set.xlsx (the same rows as label_set.csv),
to test filter 3 (where the data were collected). The first label set
(dataone_label_set_2026-11-19) had only 10 BC records among its 60
biodiversity ones, so this set leans towards records likely to be in or
near BC. Every record here was kept by filter 2 (as biodiversity data), is
not reused from Lunaris, and is not in the first label set.{set_extra}

Fill in two columns for each row:

bc_class   where the data were collected, one of the classes below.
notes      anything worth saying, for example why you chose unsure.

The rows were drawn from five strata (column "stratum"), with seed {seed}:
{strata}
{strata_extra}Knowing the stratum can lean a reading; label from what the record says.

The columns are those of the first label set, without "biodiversity", plus:
  abstract_cut_by_dataone  "yes" when the abstract ends with "...": DataONE's
                           copy of the abstract was cut, so the full text is
                           longer (the link shows the dataset page).
  namedLocation, site, placeKey   DataONE's place fields, as harvested.
The column "{note}" is a short note we added about the box or point (for
example "west is greater than east"). It is not part of the record. The
box_or_point column gives the numbers as DataONE has them; for a record
with several boxes it lists every box read from the original document.

Copied word for word from the first label set's README
(dataone_label_set_2026-11-19/README.txt):

"""

# The parts of the first README copied here: from each heading to the next.
COPIED_SECTIONS = [("bc_class values", "How to label \"unsure\""),
                   ("How to label \"unsure\"", "What filter 2 counts as biodiversity data")]


def check_out_dir(out_dir, replace):
    """Stop unless out_dir is new or empty, or --replace was given."""
    if os.path.isdir(out_dir) and os.listdir(out_dir) and not replace:
        sys.exit(f"STOPPED: {out_dir} already holds files; pass --replace to write over them")
    os.makedirs(out_dir, exist_ok=True)


def joined(value):
    """Return a list field as text joined with "; ", a plain text as it is, and a missing value as ""."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return ""
    if isinstance(value, str):
        return value
    return "; ".join(str(v) for v in value)


def ids_in(folder):
    """Return the record ids in a label set folder's label_set.csv."""
    return set(pd.read_csv(os.path.join(folder, "label_set.csv"), dtype=str, usecols=["id"], encoding="utf-8-sig")["id"])


def read_pool(leave_out=()):
    """
    Return the records that may be drawn: kept by filter 2, not reused from
    Lunaris, not in the first label set nor in the folders leave_out; with
    datasource, group, reason (from dataone_groups.parquet, which label_rows
    reads), multibox and the BC words found in title, keywords and abstract.
    """
    union = pd.read_parquet(UNION, columns=["id", "datasource", "group", "multibox", "keep_union", "reuse_lunaris"])
    pool = union[union["keep_union"].fillna(False).astype(bool) & ~union["reuse_lunaris"]]
    taken = ids_in(FIRST_SET).union(*(ids_in(folder) for folder in leave_out))
    pool = pool[~pool["id"].isin(taken)].copy()
    reasons = pd.read_parquet(GROUPS, columns=["id", "reason"]).set_index("id")["reason"]
    pool["reason"] = pool["id"].map(reasons)
    if pool["reason"].isna().any():
        sys.exit("STOPPED: some records are missing from dataone_groups.parquet")
    texts = pq.read_table(VERSIONS, columns=["id", "title", "abstract", "keywords", "version_status"],
                          filters=[("id", "in", list(pool["id"]))]).to_pandas()
    texts = texts[texts["version_status"] == "latest"].drop_duplicates("id").set_index("id")
    places = read_bc_places()
    words = {}
    for record_id, row in texts.iterrows():
        found = []
        for part in (row["title"], joined(row["keywords"]), row["abstract"]):
            found += bc_mentions(part if isinstance(part, str) else "", places)
        words[record_id] = ", ".join(dict.fromkeys(found))
    pool["bc_words"] = pool["id"].map(words).fillna("")
    return pool


def only_weak_words(words):
    """Return True if a record's BC words (joined with ", ") are only bare "BC" or "Vancouver"."""
    found = {w.strip().lower() for w in words.split(",") if w.strip()}
    return bool(found) and found <= WEAK_BC_WORDS


def box_measures(pool, shape):
    """
    Return, for the single-box records of pool, the Lunaris BC filter's
    class of their index box (classify_box: huge, inside, partly_bc and so
    on) and its share in BC (measure_box), as a table by id. A box with no
    area gets class None and share None.
    """
    edges = ["id"] + list(INDEX_EDGES.values())
    ids = list(pool.loc[~pool["multibox"] & (pool["group"] == "touches_bc_partly"), "id"])
    boxes = pq.read_table(VERSIONS, columns=edges + ["version_status"], filters=[("id", "in", ids)]).to_pandas()
    boxes = boxes[boxes["version_status"] == "latest"].drop_duplicates("id")
    rows = []
    for row in boxes.itertuples(index=False):
        box = {"w": row.westBoundCoord, "e": row.eastBoundCoord, "s": row.southBoundCoord, "n": row.northBoundCoord}
        kind, _ = classify_box(box, shape)
        measured = measure_box(box, shape)
        rows.append({"id": row.id, "box_class": kind, "share_in_bc": None if measured is None else measured[0]})
    return pd.DataFrame(rows, columns=["id", "box_class", "share_in_bc"]).set_index("id")


def strict_pools(pool, shape):
    """Return {stratum: its records} for the strict draw, as the module docstring defines them."""
    no_box = pool["group"] == "no_box"
    single = ~pool["multibox"]
    measures = box_measures(pool, shape)
    box_class = pool["id"].map(measures["box_class"])
    share = pool["id"].map(measures["share_in_bc"]).astype(float)
    near = (box_class != "huge") & box_class.notna() & (share >= MIN_SHARE_IN_BC)
    strong = (pool["bc_words"] != "") & ~pool["bc_words"].map(only_weak_words)
    return {
        "no_box_bc_word": pool[no_box & strong],
        "partial_overlap": pool[single & (pool["group"] == "touches_bc_partly") & near],
        "w_gt_e_or_sign": pool[single & pool["group"].isin(["west_gt_east", "sign_check"])],
        "multi_box": pool[pool["multibox"] & (pool["group"] == "touches_bc_partly")],
        "no_box_no_word": pool[no_box & (pool["bc_words"] == "")],
    }


def strata_pools(pool):
    """Return {stratum: its records}, as the module docstring defines the strata (the first set of 50)."""
    no_box = pool["group"] == "no_box"
    single = ~pool["multibox"]
    return {
        "no_box_bc_word": pool[no_box & (pool["bc_words"] != "")],
        "partial_overlap": pool[single & (pool["group"] == "touches_bc_partly")],
        "w_gt_e_or_sign": pool[single & pool["group"].isin(["west_gt_east", "sign_check"])],
        "multi_box": pool[pool["multibox"] & pool["group"].isin(["touches_bc_partly", "send_to_llm"])],
        "no_box_no_word": pool[no_box & (pool["bc_words"] == "")],
    }


def draw(pools, rng):
    """
    Draw every stratum from pools ({stratum: records}; each sorted by id
    first, so the draw does not depend on file order), shuffle the rows and
    number them. Returns the chosen rows and, per stratum, (asked,
    available, drawn).
    """
    parts, counts = [], {}
    for stratum, wanted in STRATA:
        candidates = pools[stratum].sort_values("id")
        taken = min(wanted, len(candidates))
        parts.append(candidates.sample(taken, random_state=rng).assign(stratum=stratum))
        counts[stratum] = (wanted, len(candidates), taken)
    chosen = pd.concat(parts, ignore_index=True)
    if chosen["id"].duplicated().any():
        sys.exit("STOPPED: a record was drawn twice")
    chosen = chosen.sample(frac=1, random_state=rng).reset_index(drop=True)
    chosen.insert(0, "row", np.arange(1, len(chosen) + 1))
    return chosen, counts


def label_table(chosen):
    """
    Return the rows to label: build_label_set.label_rows (the first label
    set's columns, filled the same way) without biodiversity, plus the cut
    mark, the place fields, the stratum and the empty bc_class and notes.
    """
    fields = read_fields(chosen["id"])
    boxes = pd.read_parquet(BOXES)
    boxes = boxes[boxes["id"].isin(set(chosen["id"]))].drop_duplicates(["id"] + EDGES + [f"{e}_text" for e in EDGES])
    rows = label_rows(chosen, fields, boxes).set_index("id")
    places = pq.read_table(VERSIONS, columns=["id", *PLACE_FIELDS, "version_status"],
                           filters=[("id", "in", list(chosen["id"]))]).to_pandas()
    places = places[places["version_status"] == "latest"].drop_duplicates("id").set_index("id")
    table = chosen[["row", "id", "stratum"]].set_index("id").join(rows.drop(columns=["row"]))
    abstract = table["abstract"].fillna("")
    table["abstract_cut_by_dataone"] = abstract.str.rstrip().str.endswith(CUT_MARK).map({True: "yes", False: "no"})
    for field in PLACE_FIELDS:
        table[field] = places[field].reindex(table.index).map(joined)
    table["bc_class"], table["notes"] = "", ""
    return table.reset_index()[COLUMNS].sort_values("row")


def write_xlsx(labels, path):
    """Write the label table as xlsx: bold header, wrapped text, set widths, a dropdown for bc_class."""
    book = Workbook()
    sheet = book.active
    sheet.title = "label_set"
    sheet.append(COLUMNS)
    for values in labels.itertuples(index=False):
        sheet.append([excel_safe(v) for v in values])
    widths = {"row": 6, "id": 28, "datasource": 12, "title": 40, "abstract": 90, "abstract_cut_by_dataone": 12,
              "keywords": 30, "namedLocation": 20, "site": 20, "placeKey": 20, "box_or_point": 32, NOTE_HEADER: 30,
              "link": 30, "stratum": 16, "bc_class": 22, "notes": 40}
    fill = PatternFill("solid", start_color="FFF2CC")
    for index, name in enumerate(COLUMNS, start=1):
        letter = sheet.cell(row=1, column=index).column_letter
        sheet.column_dimensions[letter].width = widths[name]
        sheet.cell(row=1, column=index).font = Font(bold=True)
        if name in ("bc_class", "notes"):
            sheet.cell(row=1, column=index).fill = fill
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    letter = sheet.cell(row=1, column=COLUMNS.index("bc_class") + 1).column_letter
    rule = DataValidation(type="list", formula1='"' + ",".join(BC_CLASSES) + '"', allow_blank=True)
    rule.add(f"{letter}2:{letter}{len(labels) + 1}")
    sheet.add_data_validation(rule)
    sheet.freeze_panes = "D2"
    book.save(path)


def copied_sections():
    """Return the first README's bc_class definitions and its "unsure" guidance, word for word."""
    with open(os.path.join(FIRST_SET, "README.txt"), encoding="utf-8") as handle:
        first = handle.read()
    parts = []
    for start, end in COPIED_SECTIONS:
        if first.count(start + "\n") != 1 or end + "\n" not in first:
            sys.exit(f"STOPPED: the first README no longer has the section {start!r}")
        begin = first.index(start + "\n")
        parts.append(first[begin:first.index(end + "\n", begin)].rstrip() + "\n")
    return "\n".join(parts)


# What the strict set's README adds (the first set's README has none of it).
STRICT_README = {
    "title_extra": " (strict)",
    "rule_extra": "=========",
    "set_extra": ("\nIt also leaves out the 50 records of dataone_bc_label_set_2026-11-23 (the\n"
                  "first set of 50, kept as a test of false BC hits)."),
    "strata_extra": ("The strata are stricter than in the first set of 50:\n"
                     "  no_box_bc_word   a record whose only BC words are a bare \"BC\" or\n"
                     "                   \"Vancouver\" does not count\n"
                     "  partial_overlap  the box is under 5 times BC's area and has at least\n"
                     "                   10% of its area in BC\n"
                     "  multi_box        at least one box touches BC\n"),
}
FIRST_README = {key: "" for key in STRICT_README}


def write_readme(out_dir, seed, strict):
    """Write README.txt in out_dir: what the set is, then the copied sections; check the copy is word for word."""
    strata = "\n".join(f"  {name:16} {number:2}" for name, number in STRATA)
    copied = copied_sections()
    extra = STRICT_README if strict else FIRST_README
    text = README_HEAD.format(seed=seed, strata=strata, note=NOTE_HEADER, **extra) + copied
    with open(os.path.join(FIRST_SET, "README.txt"), encoding="utf-8") as handle:
        if copied.strip() not in handle.read():
            sys.exit("STOPPED: the copied sections are not word for word the first README's")
    with open(os.path.join(out_dir, "README.txt"), "w", encoding="utf-8") as handle:
        handle.write(text)


def report(chosen, counts, labels):
    """Print the counts per stratum and per datasource, and anything Excel could not hold exactly."""
    pd.set_option("display.width", 200)
    print("BY STRATUM (asked, available, drawn)")
    for stratum, (asked, available, drawn) in counts.items():
        short = "  <-- fewer than asked, all taken" if drawn < asked else ""
        print(f"   {stratum:16} asked {asked:2}, available {available:6,}, drawn {drawn:2}{short}")
    print(f"   total drawn: {len(chosen)}")
    print("\nBY DATASOURCE AND STRATUM")
    print(pd.crosstab(chosen["datasource"], chosen["stratum"], margins=True, margins_name="total").to_string())
    print("\nBY GROUP")
    print(chosen["group"].value_counts().to_string())
    print(f"\nabstracts cut by DataONE: {int((labels['abstract_cut_by_dataone'] == 'yes').sum())}; "
          f"empty abstracts: {int((labels['abstract'].fillna('') == '').sum())}")
    changed = sum(isinstance(v, str) and excel_safe(v) != v for v in labels.to_numpy().ravel())
    print(f"cells changed for Excel (control characters or over 32,767 characters): {changed}")


def main():
    """Draw the set (the first 50, or with --strict the strict 50) and write label_set.csv, .xlsx and README.txt."""
    parser = argparse.ArgumentParser(description="Build the DataONE BC label set.")
    parser.add_argument("--strict", action="store_true", help="draw the strict set of 50 (see the module docstring)")
    parser.add_argument("--out", default=None, help="write to this folder instead (to check a draw)")
    parser.add_argument("--replace", action="store_true", help="write over the files in the output folder")
    args = parser.parse_args()
    out_dir = args.out or (STRICT_OUT_DIR if args.strict else OUT_DIR)
    seed = STRICT_SEED if args.strict else SEED
    check_out_dir(out_dir, args.replace)
    if args.strict:
        pool = read_pool(leave_out=[OUT_DIR])
        pools = strict_pools(pool, load_bc_shape(BC_BOUNDARY))
    else:
        pools = strata_pools(read_pool())
    chosen, counts = draw(pools, np.random.RandomState(seed))
    labels = label_table(chosen)
    labels.to_csv(os.path.join(out_dir, "label_set.csv"), index=False, encoding="utf-8-sig")
    write_xlsx(labels, os.path.join(out_dir, "label_set.xlsx"))
    write_readme(out_dir, seed, args.strict)
    report(chosen, counts, labels)
    print(f"\nseed {seed}; wrote label_set.csv, label_set.xlsx, README.txt -> {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
