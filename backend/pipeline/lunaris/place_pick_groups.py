"""
place_pick_groups.py

Statistics only (2026-11-03): look for a group of the place lookup's drawn
names (stage 3's hand_check.csv, 349 names with Claude's draft readings)
that had no wrong and no unsure reading. The readings are drafts Lucia has
not checked, so a group found here still needs her hand check before
anything is drawn. Nothing here changes the map.

Each drawn name gets these features:
  final_class            our class of the record (bc_only and so on)
  catalogue              the catalogue it was harvested from
  feature_class          the GeoNames class of the place (P towns, S spots,
                         H water, T terrain)
  drawing_rule           point (a town or spot), sized (an area feature with a
                         size) or lake
  province_next_to_name  one order's quote has the name followed at once by
                         the place's province or state, or by BC ("Atlin,
                         British Columbia", "Boundary Bay; British Columbia",
                         "Valcartier, Quebec")
  same_number            both orders chose the same candidate number
  one_candidate          the name had one candidate only
  in_title               the name, as the record writes it, is in the title
  quote_from_list        a quote is from the record's place list
  large_area_word        the name has a word for a large area (archipelago,
                         strait, plateau, highland, coast, valley, range,
                         national park, islands)
Rule picks (stage 1) are not in the sample, so they cannot be counted here.

Writes, into --out (which must not exist yet):
  features.csv   the sample with the features
  groups.csv     every group of one, two or three features (and single
                 values of the class and catalogue), with its size and its
                 right, wrong and unsure counts
  report.txt     the groups worth reading, and every group of at least
                 MIN_GROUP names with no wrong and no unsure reading

Run, from backend/:
    python pipeline/lunaris/place_pick_groups.py \\
        --stage3 data/lunaris_place_lookup/stage3/full_run \\
        --run data/lunaris_place_lookup/fir_runs/places_p1_qwen3.6-35b-a3b \\
        --input data/lunaris_place_lookup/fir_inputs/places_p1 \\
        --out data/lunaris_place_lookup/stage3/groups
"""

import argparse
import itertools
import json
import os
import re
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import place_pick_checks as checks  # noqa: E402

# The smallest group the report calls "not tiny".
MIN_GROUP = 15

# Ways of writing BC right after a place name.
BC_WORDS = ["British Columbia", "BC", "B.C.", "B. C."]

LARGE_AREA_WORD = re.compile(r"archipelago|strait|plateau|highland|coast|valley|range|national park|islands\b",
                             re.IGNORECASE)


def province_next_to_name(as_written, admin1, quotes):
    """
    Return True if a quote has the name followed at once (after an optional
    closing bracket and one comma, semicolon or bar) by its province or
    state, or by BC.
    """
    words = BC_WORDS + ([admin1] if admin1 else [])
    after = r"\)?\s*[,;|]?\s*(?:" + "|".join(re.escape(w) for w in words) + r")(?![\w])"
    return any(re.search(re.escape(as_written) + after, quote or "") for quote in quotes)


def features(hand, names, answers, items, catalogues):
    """Return the hand-check sample with the features described at the top of the file."""
    frame = hand.merge(names[["dataset_id", "name", "feature_class", "drawing_rule", "candidates"]],
                       on=["dataset_id", "name"])
    frame["catalogue"] = frame["dataset_id"].map(catalogues)
    frame["final_class"] = frame["dataset_id"].map(lambda d: items[d]["final_class"])
    frame["province_next_to_name"] = [province_next_to_name(w, a, (q1, q2)) for w, a, q1, q2 in zip(
        frame["as_written"], frame["admin1"], frame["lookup_quote"], frame["shuffled_quote"])]
    frame["same_number"] = [answers.get((d, n, "lookup")) == answers.get((d, n, "shuffled"))
                            for d, n in zip(frame["dataset_id"], frame["name"])]
    frame["one_candidate"] = frame["candidates"] == 1
    frame["in_title"] = [w.lower() in items[d]["title"].lower() for d, w in zip(frame["dataset_id"], frame["as_written"])]
    frame["quote_from_list"] = [any("Places in the record" in (q or "") or " | " in (q or "") for q in (q1, q2))
                                for q1, q2 in zip(frame["lookup_quote"], frame["shuffled_quote"])]
    frame["large_area_word"] = frame["name"].str.contains(LARGE_AREA_WORD)
    return frame


def binary_features(frame):
    """Return the yes-or-no features to combine, by label."""
    return {
        "bc_only": frame["final_class"] == "bc_only",
        "not all_canada": frame["final_class"] != "all_canada",
        "not open.canada.ca": ~frame["catalogue"].str.contains("Open Government", na=False),
        "Borealis or Dryad": frame["catalogue"].str.contains("Borealis|Dryad", na=False),
        "CanWIN": frame["catalogue"].str.contains("CanWIN", na=False),
        "town or spot": frame["feature_class"].isin(["P", "S"]),
        "not a town or spot": ~frame["feature_class"].isin(["P", "S"]),
        "lake": frame["drawing_rule"] == "lake",
        "province next to name": frame["province_next_to_name"],
        "same candidate number": frame["same_number"],
        "one candidate": frame["one_candidate"],
        "several candidates": ~frame["one_candidate"],
        "in title": frame["in_title"],
        "quote not from the place list": ~frame["quote_from_list"],
        "no large-area word": ~frame["large_area_word"],
    }


def counts(frame, mask, label):
    """Return one row of groups.csv: the group's label, size and right, wrong and unsure counts."""
    readings = frame.loc[mask, "draft_reading"].value_counts()
    return {"group": label, "size": int(mask.sum()), "right": int(readings.get("right", 0)),
            "wrong": int(readings.get("wrong", 0)), "unsure": int(readings.get("unsure", 0))}


def group_rows(frame):
    """Return groups.csv's rows: single values of class and catalogue, and combinations of one to three features."""
    rows = [counts(frame, frame.index == frame.index, "all drawn names")]
    for column in ("final_class", "catalogue", "feature_class", "drawing_rule"):
        rows += [counts(frame, frame[column] == value, f"{column} = {value}") for value in frame[column].unique()]
    found = binary_features(frame)
    for size in (1, 2, 3):
        for combo in itertools.combinations(found, size):
            mask = pd.concat([found[c] for c in combo], axis=1).all(axis=1)
            rows.append(counts(frame, mask, " & ".join(combo)))
    return rows


def main():
    """Read stage 3's sample and the run's answers, and write the group statistics."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("--stage3", required=True, help="stage 3's folder (hand_check.csv and names.csv)")
    parser.add_argument("--run", required=True, help="the run folder (answers.jsonl), read only")
    parser.add_argument("--input", required=True, help="the input folder (items.jsonl.gz)")
    parser.add_argument("--datasets", default="data/api/lunaris_datasets.parquet")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    if os.path.exists(args.out):
        raise SystemExit(f"ERROR: {args.out} already exists; move it aside first")
    hand = pd.read_csv(os.path.join(args.stage3, "hand_check.csv"), keep_default_na=False)
    names = pd.read_csv(os.path.join(args.stage3, "names.csv"))
    answers = {}
    with open(os.path.join(args.run, "answers.jsonl"), encoding="utf-8") as handle:
        for line in map(json.loads, handle):
            answers[(line["dataset_id"], line["name"], line["order"])] = line["candidate_number"]
    items = checks.read_items(args.input)
    catalogues = pd.read_parquet(args.datasets, columns=["id", "source_name"]).set_index("id")["source_name"]
    frame = features(hand, names, answers, items, catalogues)
    groups = pd.DataFrame(group_rows(frame)).drop_duplicates("group")
    clean = groups[(groups["wrong"] == 0) & (groups["unsure"] == 0) & (groups["size"] >= MIN_GROUP)]
    os.makedirs(args.out)
    frame.to_csv(os.path.join(args.out, "features.csv"), index=False)
    groups.to_csv(os.path.join(args.out, "groups.csv"), index=False)
    shown = ["all drawn names", "bc_only", "not open.canada.ca", "town or spot", "lake", "province next to name",
             "same candidate number", "one candidate", "in title", "bc_only & town or spot",
             "town or spot & province next to name", "province next to name & no large-area word",
             "province next to name & quote not from the place list",
             "province next to name & quote not from the place list & no large-area word"]
    lines = ["Groups of the 349 drawn names, by Claude's draft readings (not checked by Lucia)", "",
             f"{'group':80} size right wrong unsure"]
    for label in shown:
        row = groups[groups["group"] == label].iloc[0]
        lines.append(f"{label:80} {row['size']:4} {row['right']:5} {row['wrong']:5} {row['unsure']:6}")
    lines += ["", f"Every group of at least {MIN_GROUP} names with no wrong and no unsure reading:"]
    lines += [f"  {row.group:78} {row.size:4}" for row in clean.sort_values("size", ascending=False).itertuples()]
    with open(os.path.join(args.out, "report.txt"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
