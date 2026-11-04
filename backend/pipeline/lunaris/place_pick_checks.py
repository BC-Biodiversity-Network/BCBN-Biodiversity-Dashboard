"""
place_pick_checks.py

Stage 3 of the new place lookup, on the Mac: check the model step's answers
(prompt P1, a run folder from llm/place_pick_run.py) and decide, for every
place name the model was asked about, whether one place is drawn. Nothing
here changes the map; the site is only changed once the hand-check sample
passes and Lucia says so.

A name's place is drawn only when all of these hold:
  agree       both orders (the lookup's and the shuffled one) chose the same
              GeoNames place
  quotes      both answers' quotes are in the record (see quote_check)
  drawing     the place passes place_drawing_rules.drawing_decision (large
              areas, streams, the lake rules and hand list, parks and
              other GeoNames class L areas never drawn, and the size rule
              for other area features)

The quote check: the quote must be in the record as the prompt showed it
(title, description, keywords and the record's own place list, with their
labels; not our notes or the catalogue line), with spelling, punctuation
and capitals unchanged; runs of spaces and line breaks count as one space.
A quote that joins pieces of the record with "..." (or "…") passes only
when every piece is in the record, in the order given.

A prompt that failed on Fir (its answer did not fit after two attempts, in
the run's failed.csv) is not asked again: the names in it lack that order's
answer, so they are not drawn, and the report lists every failed prompt with
its names.

Reads (read only): the input folder (items.jsonl.gz), the run folder
(answers.jsonl and failed.csv), the GeoNames lookup table (for each picked place's feature
class), the Canadian names file and the hand list of lakes never drawn, and
optionally the Water Hub page points (only to measure distances; they never
decide anything) and a file of Claude's draft readings for the hand check.

Writes, into --out (which must not exist yet):
  names.csv              one row per record and name asked: both answers,
                         the quote checks, the drawing rule, drawn or not,
                         and why
  hand_check.csv         the names to check by hand (--sample all: every
                         name, for a test run; --sample drawn: every name
                         that would be drawn), with the record's title, both
                         answers and
                         quotes, flags, Claude's draft reading (from
                         --drafts) and empty columns for Lucia
  check_points.csv       for the records in the Water Hub check set, every
                         place either order chose, with its distance in km
                         from the page's point, and whether it is drawn
  lakes_at_one_million.csv  every lake in the input's candidate lists that
                         the Canadian names file shows at 1:1,000,000, with
                         its rule (lake, or lake_hand_list) and the records
                         where both orders chose it
  report.txt             the counts

Run, from backend/:
    python pipeline/lunaris/place_pick_checks.py \\
        --input data/lunaris_place_lookup/fir_inputs/places_p1 \\
        --run data/lunaris_place_lookup/fir_runs/places_p1_qwen3.6-35b-a3b_only-hard_cases \\
        --out data/lunaris_place_lookup/stage3/only-hard_cases \\
        --check-points <Results>/lunaris_place_lookup_2026-11/cbwh_page_coordinates_for_checking.csv
"""

import argparse
import gzip
import json
import math
import os
import re
import sys
from collections import Counter

import duckdb
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "llm"))
import place_drawing_rules as drawing  # noqa: E402
import place_pick_prompt as prompt  # noqa: E402

DEFAULT_GEONAMES = "data/geonames/geonames_lookup.parquet"

# What joins pieces of the record in a quote.
ELLIPSIS = re.compile(r"\s*(?:\.\.\.|…)\s*")

# Words in a model's reason that suggest it chose a place because of the
# catalogue or our earlier reading, which the prompt forbids. Only a flag
# for the hand check; it decides nothing.
CATALOGUE_WORDS = re.compile(r"catalogue|catalog|earlier reading|collected only in BC|from a BC", re.IGNORECASE)

EARTH_RADIUS_KM = 6371.0


def squeeze(text):
    """Return text with every run of spaces and line breaks made one space, and no space at either end."""
    return re.sub(r"\s+", " ", text or "").strip()


def record_section(item):
    """Return the record as the prompt showed it: title, description, keywords and place list, without our notes."""
    text = prompt.record_text(item)
    return text[:text.rfind("\n\nCatalogue: ")]


def quote_check(quote, record):
    """
    Return (ok, pieces, problem) for one quote against the record's text:
    ok when every piece (the quote split at "...") is in the record, in
    order; pieces is how many pieces there were; problem says what failed.
    """
    pieces = [piece for piece in (squeeze(part) for part in ELLIPSIS.split(quote or "")) if piece]
    if not pieces:
        return False, 0, "empty quote"
    text, start = squeeze(record), 0
    for number, piece in enumerate(pieces, start=1):
        found = text.find(piece, start)
        if found < 0:
            where = "not in the record" if piece not in text else "out of order"
            return False, len(pieces), f"piece {number} of {len(pieces)} {where}: {piece[:80]!r}"
        start = found + len(piece)
    return True, len(pieces), ""


def distance_km(latitude1, longitude1, latitude2, longitude2):
    """Return the great-circle distance between two points, in km."""
    p1, p2 = math.radians(latitude1), math.radians(latitude2)
    dp, dl = p2 - p1, math.radians(longitude2 - longitude1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def read_items(folder):
    """Return the input folder's items by dataset id."""
    with gzip.open(os.path.join(folder, "items.jsonl.gz"), "rt", encoding="utf-8") as handle:
        return {line["dataset_id"]: line["item"] for line in map(json.loads, handle)}


def read_failed(run, folder):
    """
    Return the run's failed prompts (failed.csv), each with its unit name,
    dataset id, order, error and the names it asked, from the input folder.
    """
    path = os.path.join(run, "failed.csv")
    failed = pd.read_csv(path, keep_default_na=False) if os.path.exists(path) else pd.DataFrame()
    if failed.empty:
        return []
    units = {}
    with gzip.open(os.path.join(folder, "items.jsonl.gz"), "rt", encoding="utf-8") as handle:
        for line in map(json.loads, handle):
            for unit in line["units"]:
                units[unit["unit"]] = (unit["order"], [line["item"]["names"][i]["name"] for i in unit["indexes"]])
    return [{"unit": row.unit, "dataset_id": row.dataset_id, "order": units[row.unit][0], "error": row.error,
             "names": units[row.unit][1]} for row in failed.itertuples()]


def read_answers(run):
    """Return the run's answers by (dataset id, name), then by order."""
    answers = {}
    with open(os.path.join(run, "answers.jsonl"), encoding="utf-8") as handle:
        for line in map(json.loads, handle):
            answers.setdefault((line["dataset_id"], line["name"]), {})[line["order"]] = line
    return answers


def feature_classes(geonameids, path=DEFAULT_GEONAMES):
    """Return {GeoNames id: feature class} for the given ids (hand-added places have none), from the lookup table."""
    geonameids = {i for i in geonameids if i is not None}
    if not geonameids:
        return {}
    ids = ", ".join(str(int(i)) for i in sorted(geonameids))
    rows = duckdb.sql(f"select distinct geonameid, feature_class from read_parquet('{path}') "
                      f"where geonameid in ({ids})").fetchall()
    return {geonameid: feature_class for geonameid, feature_class in rows}


def candidate_of(item, name, geonameid):
    """Return the listed candidate with this GeoNames id for this name, or None."""
    for entry in item["names"]:
        if entry["name"] == name:
            for candidate in entry.get("candidates") or []:
                if candidate["geonameid"] == geonameid:
                    return candidate
    return None


def answer_text(item, name, answer):
    """Return one order's answer in words: the place chosen (name, kind, where) or none and why."""
    if answer is None:
        return "no answer"
    if answer["geonameid"] is None:
        return f"none: {answer['none_because']}"
    candidate = candidate_of(item, name, answer["geonameid"]) or {}
    where = ", ".join(part for part in (candidate.get("admin1"), candidate.get("country")) if part)
    return f"{candidate.get('name')} ({candidate.get('feature')}, {where}; {answer['geonameid']})"


def name_row(item, name, by_order, classes, cgn, never_drawn, failed_orders=()):
    """
    Return one row of names.csv: both answers, their quote checks, whether
    they agree, the drawing rule for an agreed place, drawn or not, and why.
    failed_orders are the orders whose prompt for this name failed on Fir.
    """
    first, second = prompt.ORDERS
    record = record_section(item)
    entry = next((e for e in item["names"] if e["name"] == name), {})
    row = {"dataset_id": item["dataset_id"], "title": item["title"], "name": name,
           "as_written": entry.get("as_written"), "match": entry.get("match"),
           "candidates": len(entry.get("candidates") or [])}
    for order in prompt.ORDERS:
        answer = by_order.get(order)
        ok, pieces, problem = quote_check(answer["quote"], record) if answer else (False, 0, "no answer")
        row.update({f"{order}_answer": answer_text(item, name, answer),
                    f"{order}_geonameid": answer["geonameid"] if answer else None,
                    f"{order}_none_because": answer["none_because"] if answer else None,
                    f"{order}_quote": answer["quote"] if answer else None,
                    f"{order}_reason": answer["reason"] if answer else None,
                    f"{order}_quote_ok": ok, f"{order}_quote_pieces": pieces, f"{order}_quote_problem": problem,
                    f"{order}_reason_names_catalogue": bool(answer and CATALOGUE_WORDS.search(answer["reason"] or ""))})
    ids = [row[f"{order}_geonameid"] for order in prompt.ORDERS]
    if failed_orders:
        outcome = "prompt_failed"
    elif any(order not in by_order for order in prompt.ORDERS):
        outcome = "an_order_has_no_answer"
    elif ids[0] is None and ids[1] is None:
        outcome = "both_none"
    elif ids[0] == ids[1]:
        outcome = "same_place"
    else:
        outcome = "orders_disagree"
    row.update(outcome=outcome, drawing_rule=None, scale=None, drawn=False)
    for field in ("geonameid", "place", "feature", "feature_class", "feature_code", "admin1", "country",
                  "latitude", "longitude"):
        row[field] = None
    if outcome == "same_place":
        candidate = dict(candidate_of(item, name, ids[0]), feature_class=classes.get(ids[0]))
        ok, rule, scale = drawing.drawing_decision(candidate, cgn, never_drawn)
        row.update(geonameid=ids[0], place=candidate["name"], feature=candidate["feature"],
                   feature_class=candidate["feature_class"], feature_code=candidate["feature_code"],
                   admin1=candidate["admin1"], country=candidate["country"], latitude=candidate["latitude"],
                   longitude=candidate["longitude"], drawing_rule=rule, scale=scale)
        quotes_ok = row[f"{first}_quote_ok"] and row[f"{second}_quote_ok"]
        row["drawn"] = bool(ok and quotes_ok)
        row["why"] = ("drawn" if row["drawn"] else "quote_not_in_record" if ok else f"not_drawn: {rule}")
    elif outcome == "prompt_failed":
        row["why"] = "prompt_failed: " + " and ".join(failed_orders)
    elif outcome == "both_none":
        row["why"] = "both_none: " + " / ".join(str(by_order[o]["none_because"]) for o in prompt.ORDERS)
    else:
        row["why"] = outcome
    return row


def check_point_rows(names, items, points):
    """
    Return check_points.csv's rows: for each record in the Water Hub check
    set (usable rows only), every place either order chose, with its
    distance in km from the page's point and whether it is drawn.
    """
    usable = points[points["usable_as_check"] == "yes"]
    page = {f"oai:canwin-datahub.ad.umanitoba.ca:{r.record_id}": r for r in usable.itertuples()}
    rows = []
    for row in names:
        point = page.get(row["dataset_id"])
        if point is None:
            continue
        chosen = {}
        for order in prompt.ORDERS:
            if row[f"{order}_geonameid"] is not None:
                chosen.setdefault(row[f"{order}_geonameid"], []).append(order)
        for geonameid, orders in chosen.items():
            candidate = candidate_of(items[row["dataset_id"]], row["name"], geonameid)
            rows.append({"dataset_id": row["dataset_id"], "title": row["title"], "name": row["name"],
                         "chosen_in": " and ".join(orders), "geonameid": geonameid, "place": candidate["name"],
                         "admin1": candidate["admin1"], "latitude": candidate["latitude"],
                         "longitude": candidate["longitude"], "page_latitude": point.page_latitude,
                         "page_longitude": point.page_longitude,
                         "km_from_page_point": round(distance_km(candidate["latitude"], candidate["longitude"],
                                                                 point.page_latitude, point.page_longitude), 1),
                         "drawn": bool(row["drawn"] and row["geonameid"] == geonameid)})
    return rows


def lake_rows(items, names, cgn, never_drawn):
    """
    Return lakes_at_one_million.csv's rows: every lake in the input's
    candidate lists that the Canadian names file shows at 1:1,000,000 (on
    the hand list or not), with how many records list it and the records
    where both orders chose it.
    """
    chosen = Counter()
    for row in names:
        if row["outcome"] == "same_place":
            chosen[row["geonameid"]] += 1
    lakes = {}
    for item in items.values():
        for entry in item["names"]:
            for candidate in entry.get("candidates") or []:
                if drawing.feature_type(candidate) != "lake" or candidate.get("kind") not in drawing.IN_BC_KINDS:
                    continue
                scale = drawing.cgn_scale(candidate, cgn, lambda category, concise: concise == "LAKE")
                if scale != drawing.LARGEST_SCALE:
                    continue
                found = lakes.setdefault(candidate["geonameid"], {
                    "geonameid": candidate["geonameid"], "lake": candidate["name"], "feature_code":
                    candidate["feature_code"], "latitude": candidate["latitude"],
                    "longitude": candidate["longitude"], "scale": scale,
                    "rule": "lake_hand_list" if drawing.on_never_drawn_list(candidate, never_drawn) else "lake",
                    "records_listing_it": set()})
                found["records_listing_it"].add(item["dataset_id"])
    rows = []
    for found in sorted(lakes.values(), key=lambda lake: lake["lake"]):
        rows.append(dict(found, records_listing_it=len(found["records_listing_it"]),
                         records_choosing_it_in_both_orders=chosen.get(found["geonameid"], 0)))
    return rows


def report_lines(names, check_rows, lake_list, run, failed=()):
    """
    Return report.txt's lines: outcomes, the failed prompts, quote checks,
    drawing rules, what the size and park rules remove, and distances.
    """
    frame = pd.DataFrame(names)
    lines = [f"Place lookup, stage 3 checks: {run}", "",
             f"{len(frame):,} names in {frame['dataset_id'].nunique():,} records; drawn: {int(frame['drawn'].sum()):,}",
             "", "Outcome of the two orders:"]
    lines += [f"  {key:24} {value:,}" for key, value in frame["outcome"].value_counts().items()]
    lines += ["", f"Prompts that failed on Fir (not asked again; their names are not drawn): {len(failed)}"]
    for found in failed:
        lines.append(f"  {found['unit']} {found['dataset_id']}: {found['error']}; names: {'; '.join(found['names'])}")
    lines += ["", "Why each name is or is not drawn:"]
    lines += [f"  {key:40} {value:,}" for key, value in frame["why"].value_counts().items()]
    quotes = pd.concat([pd.DataFrame({"ok": frame[f"{o}_quote_ok"], "pieces": frame[f"{o}_quote_pieces"],
                                      "problem": frame[f"{o}_quote_problem"], "quote": frame[f"{o}_quote"],
                                      "name": frame["name"], "order": o})
                        for o in prompt.ORDERS])
    quotes = quotes[quotes["quote"].notna()]
    joined = quotes[quotes["pieces"] > 1]
    lines += ["", f"Quotes: {len(quotes):,}; in the record: {int(quotes['ok'].sum()):,}; not: "
                  f"{int((~quotes['ok']).sum()):,}",
              f"  joined with \"...\": {len(joined):,}; every piece in the record, in order: "
              f"{int(joined['ok'].sum()):,}; not: {int((~joined['ok']).sum()):,}"]
    for bad in quotes[~quotes["ok"]].itertuples():
        lines.append(f"  not in the record ({bad.order}, {bad.name}): {bad.problem}")
    flagged = frame[frame[[f"{o}_reason_names_catalogue" for o in prompt.ORDERS]].any(axis=1)]
    lines += ["", f"Reasons naming the catalogue or our earlier reading (check by hand): {len(flagged):,}"]
    for row in flagged.itertuples():
        lines.append(f"  {row.name} ({row.title[:60]})")
    agreed = frame[frame["outcome"] == "same_place"]
    lines += ["", f"Places both orders chose: {len(agreed):,}; by drawing rule:"]
    lines += [f"  {key:16} {value:,}" for key, value in agreed["drawing_rule"].value_counts().items()]
    removed = agreed[agreed["drawing_rule"].isin(["area_no_size", "too_big"])]
    lines += ["", f"Removed by the size rule for area features (would have been drawn before): {len(removed):,}"]
    for row in removed.itertuples():
        scale = f"1:{int(row.scale):,}" if pd.notna(row.scale) else "no size"
        lines.append(f"  {row.place} ({row.feature}, {row.admin1 or row.country}; {scale}), from \"{row.name}\"")
    parks = agreed[agreed["drawing_rule"] == "park_or_area"]
    lines += ["", f"Removed by the park rule (GeoNames class L: parks, reserves and other areas, never drawn): "
                  f"{len(parks):,}"]
    for row in parks.itertuples():
        lines.append(f"  {row.place} ({row.feature}, {row.admin1 or row.country}), from \"{row.name}\" in "
                     f"{row.title[:60]}")
    lines += ["", f"Water Hub check set: {len(check_rows):,} places chosen in {len({r['dataset_id'] for r in check_rows}):,}"
                  f" records (see check_points.csv)"]
    for row in check_rows:
        lines.append(f"  {row['name']}: {row['place']} ({row['chosen_in']}) {row['km_from_page_point']} km "
                     f"from the page point; {'drawn' if row['drawn'] else 'not drawn'}")
    allowed = [lake for lake in lake_list if lake["rule"] == "lake"]
    lines += ["", f"Lakes at 1:1,000,000 in the candidate lists: {len(lake_list)} ({len(allowed)} the rule allows, "
                  f"{len(lake_list) - len(allowed)} on the hand list; see lakes_at_one_million.csv)"]
    return lines


def main():
    """Read the run's answers, check them, and write the stage 3 files."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("--input", required=True, help="the input folder from place_pick_prepare.py")
    parser.add_argument("--run", required=True, help="the run folder, copied back from Fir (read only)")
    parser.add_argument("--out", required=True, help="the folder to write")
    parser.add_argument("--geonames", default=DEFAULT_GEONAMES)
    parser.add_argument("--cgn", default=drawing.DEFAULT_CGN)
    parser.add_argument("--check-points", help="the Water Hub page points (for distances only)")
    parser.add_argument("--sample", choices=["all", "drawn"], default="all",
                        help="which names go in hand_check.csv: all, or only those that would be drawn")
    parser.add_argument("--drafts", help="Claude's draft readings: dataset_id, name, draft_reading, draft_quote, "
                                         "draft_note")
    args = parser.parse_args()
    if os.path.exists(args.out):
        raise SystemExit(f"ERROR: {args.out} already exists; move it aside first")

    items = read_items(args.input)
    answers = read_answers(args.run)
    failed = read_failed(args.run, args.input)
    run_records = sorted({dataset_id for dataset_id, _ in answers} | {found["dataset_id"] for found in failed})
    asked = [(d, items[d]["names"][i]["name"]) for d in run_records for i in prompt.model_names(items[d])]
    classes = feature_classes({line["geonameid"] for found in answers.values() for line in found.values()
                               if line["geonameid"] is not None}, args.geonames)
    cgn, never_drawn = drawing.read_cgn(args.cgn), drawing.read_never_drawn()
    failed_orders = {}
    for found in failed:
        for name in found["names"]:
            failed_orders.setdefault((found["dataset_id"], name), []).append(found["order"])
    names = [name_row(items[d], name, answers.get((d, name), {}), classes, cgn, never_drawn,
                      tuple(failed_orders.get((d, name), ()))) for d, name in asked]
    points = pd.read_csv(args.check_points) if args.check_points else pd.DataFrame(columns=["usable_as_check"])
    check_rows = check_point_rows(names, items, points)
    lake_list = lake_rows(items, names, cgn, never_drawn)

    frame = pd.DataFrame(names)
    hand = (frame[frame["drawn"]] if args.sample == "drawn" else frame)[["dataset_id", "title", "name", "as_written", "outcome", "drawn", "why", "place",
                                "feature", "admin1", "country", "latitude", "longitude"]
                               + [f"{o}_{field}" for o in prompt.ORDERS
                                  for field in ("answer", "quote", "quote_ok", "reason", "reason_names_catalogue")]]
    if args.drafts:
        drafts = pd.read_csv(args.drafts, keep_default_na=False)
        hand = hand.merge(drafts, on=["dataset_id", "name"], how="left")
    else:
        hand = hand.assign(draft_reading="", draft_quote="", draft_note="")
    hand = hand.assign(lucia_reading="", lucia_note="")

    os.makedirs(args.out)
    pd.DataFrame(names).to_csv(os.path.join(args.out, "names.csv"), index=False)
    hand.to_csv(os.path.join(args.out, "hand_check.csv"), index=False)
    pd.DataFrame(check_rows, columns=["dataset_id", "title", "name", "chosen_in", "geonameid", "place", "admin1",
                                      "latitude", "longitude", "page_latitude", "page_longitude",
                                      "km_from_page_point", "drawn"]).to_csv(
        os.path.join(args.out, "check_points.csv"), index=False)
    pd.DataFrame(lake_list).to_csv(os.path.join(args.out, "lakes_at_one_million.csv"), index=False)
    report = report_lines(names, check_rows, lake_list, args.run, failed)
    with open(os.path.join(args.out, "report.txt"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(report) + "\n")
    print("\n".join(report))
    print(f"\nwrote {args.out}: names.csv, hand_check.csv, check_points.csv, lakes_at_one_million.csv, report.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
