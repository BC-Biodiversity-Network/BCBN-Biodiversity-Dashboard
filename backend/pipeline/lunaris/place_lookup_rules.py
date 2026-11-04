"""
place_lookup_rules.py

Stage 1 of the new place lookup (planned 2026-11-03): for every place name
pass 1 found in a shown Lunaris dataset, decide by simple rules whether it
is drawn (one GeoNames place) or not drawn, or leave it to the model, and
write the GeoNames candidate lists the model will see. No model is run here.

Why: the old lookup picked each name's GeoNames match on its own, without
the record's context, and some picks were wrong (the Columbia Basin placed
in Illinois). The rules below only pick a place when the record itself says
which one it means; everything else they cannot settle goes to the model.

The rules, in this order (the first that applies decides):
  affiliation         every mention of the name is an author's institution
                      (pass 1's role): not drawn
  too_broad_list      the name is in place_rules_too_broad.csv (the whole
                      world, a climate zone, a realm and so on): not drawn
  too_generic         the lookup found the name too generic to look up
                      ("lake", "western provinces"): not drawn
  not_found           GeoNames has no place with this name: not drawn
  approximate_only    GeoNames has only places with a similar name, not
                      this one (Fraser Ridge found Kinsey Ridge): not drawn
  model_contains      instead of not_found or approximate_only, when some
                      GeoNames places have the name's words in their own
                      name ("Gwaii Haanas" in "Gwaii Haanas National Park
                      Reserve and Haida Heritage Site"): the model decides,
                      from those places (see contains_lookup)
  record_place_list   the record's own place list (as harvested) names the
                      province, state or country with the name ("Maple
                      Ridge; British Columbia; Canada") and exactly one
                      GeoNames place with the name lies there: that place,
                      or not drawn when it is a large area
  written_with_hint   the name as the record's text writes it with a comma
                      ("Alberta, Canada"), found word for word in the
                      record, when the one place with the name there is a
                      large area: not drawn. A small place is left to the
                      model, since the text may name it for another reason
                      than where the data were collected (in a first try,
                      "between Wardner, BC and Libby, Montana" and a
                      calibration value "St. Augustine, FL" were picked)
  large_area          the lookup's best place is a large area (see
                      is_large: a country, province, region, ocean, sea,
                      range, strait, island chain or regional district, or
                      an area containing BC) and no other place with the
                      name is a small place in BC: not drawn
  model               anything else: the model decides, from the record and
                      the candidate list
The two hint rules only use exact matches (not a plural removed, a direction
word dropped or a similar name).

Each name gets a quote copied unchanged from the record where it can be
found: the harvested place list entry, the keyword, the title, or the
sentence of the abstract that has it.

Reads (read only): lunaris_datasets.parquet, the GeoNames lookup table,
place_rules_too_broad.csv and BC's outline (for which boxes the map draws).
Writes, into --out (which must not exist yet):
  names.csv           one row per shown dataset and place name: the record,
                      the name, in_map_scope (the record has no box or point
                      the map draws, so a place would be its only location),
                      what the lookup found, and the decision: decision
                      (pick, do_not_draw or model), rule, reason, the picked
                      place's GeoNames fields, and the quote and its field
  model_items.jsonl   one line per dataset with names left to the model: the
                      record (title, abstract, keywords and place list as
                      harvested, catalogue, our class), every place name it
                      has with its rule decision, and, for each name left to
                      the model, its candidate list (see listed_candidates)
  counts.txt          the counts per rule, and 10 examples of each

Run, from backend/:
    python pipeline/lunaris/place_lookup_rules.py --bc-boundary <path to bc_boundary_gbif.wkt>
"""

import argparse
import csv
import json
import os
import re
import sys
from collections import Counter

import pandas as pd

from bc_filter_rules import load_bc_shape
from export_lunaris_datasets import COUNTRY_CODE_PREFIX, LARGE_AREA_CODES, NOT_DRAWN_FLAGS, box_flags
from geonames_lookup import (GeoNamesLookup, best_row_per_place, feature_codes_of, is_generic_phrase, is_too_generic,
                             normalise, rank_key, split_comma)

HERE = os.path.dirname(os.path.abspath(__file__))
TOO_BROAD_CSV = os.path.join(HERE, "place_rules_too_broad.csv")
DEFAULT_DATASETS = "data/api/lunaris_datasets.parquet"
DEFAULT_OUT = "data/lunaris_place_lookup/stage1"

# A name with more candidates than MAX_LISTED shows the model every
# candidate in BC or crossing its border, every one in Canada, and the
# lookup's best OTHERS_LISTED of the rest; the others are given as counts
# by country, so the prompt stays short.
MAX_LISTED = 40
OTHERS_LISTED = 20

# Large areas besides the export's LARGE_AREA_CODES (countries, provinces,
# regions, oceans, seas, gulfs, ranges): straits, island chains and second-
# level divisions (BC's regional districts, US counties). Each is too long or
# too wide to be drawn as one point.
EXTRA_LARGE_CODES = {"H": {"STRT"}, "T": {"ISLS"}, "A": {"ADM2"}}

# The contains search skips names shorter than this (letters and digits),
# which would find too many places.
MIN_CONTAINS_LENGTH = 5

# Boxes the map does not draw with a solid line: those with an issue, and
# those that match all of BC or all of Canada (drawn dashed). A record with
# only such boxes and no point gets its place names listed on the page.
NOT_SOLID_FLAGS = NOT_DRAWN_FLAGS | {"matches_bc_rectangle", "matches_canada_extent"}

# How many examples of each rule counts.txt shows.
EXAMPLES = 10


def read_too_broad(path=TOO_BROAD_CSV):
    """Return the normalised names in place_rules_too_broad.csv."""
    with open(path, encoding="utf-8") as handle:
        return {normalise(row["name"]) for row in csv.DictReader(handle)}


def is_large(candidate):
    """
    Return True for a candidate too large to draw as a point: a large-area
    feature (LARGE_AREA_CODES or EXTRA_LARGE_CODES), a country, or a place
    with no coordinates (a hand-added BC region).
    """
    feature_class, code = candidate.get("feature_class") or "", candidate.get("feature_code") or ""
    if candidate.get("latitude") is None or candidate.get("longitude") is None:
        return True
    return ((feature_class == "A" and code.startswith(COUNTRY_CODE_PREFIX))
            or code in LARGE_AREA_CODES.get(feature_class, set()) | EXTRA_LARGE_CODES.get(feature_class, set()))


def large_reason(candidate):
    """
    Return why a large candidate is not drawn: ocean_or_sea (water: an ocean,
    sea, gulf or strait), region (a region, regional district or hand-added
    BC region) or too_broad (anything else: a country, province, range or
    island chain).
    """
    feature_class, code = candidate.get("feature_class") or "", candidate.get("feature_code") or ""
    if feature_class == "H":
        return "ocean_or_sea"
    if code in ("RGN", "RGNE", "AREA", "HAND", "ADM2"):
        return "region"
    return "too_broad"


def contains_norms(name):
    """
    Return the normalised forms of a name (and of its spelling as written)
    that the contains search may use: not too short, not generic.
    """
    norms = {normalise(split_comma(name["name"])[0]), normalise(split_comma(name["as_written"])[0])}
    return sorted(norm for norm in norms if len(norm.replace(" ", "")) >= MIN_CONTAINS_LENGTH
                  and not is_too_generic(norm) and not is_generic_phrase(norm))


def contains_lookup(lookup, norms):
    """
    Return a lookup answer, in GeoNamesLookup.lookup's shape, of every
    GeoNames place whose name has one of norms as whole words ("gwaii
    haanas" in "gwaii haanas national park reserve and haida heritage
    site"), ranked as the lookup ranks places. Its summary's match is
    "contains". It reads the whole table once per form (about 1.5 s).
    """
    rows = []
    for norm in norms:
        rows += lookup.rows_where("contains(' ' || norm || ' ', ?)", [f" {norm} "])
    places = best_row_per_place(rows)
    for place in places:
        place["kind"] = lookup.place_kind(place)
    codes = feature_codes_of(norms[0]) if norms else set()
    places.sort(key=lambda place: rank_key(place, codes))
    kinds = [place["kind"] for place in places]
    return {"query": {"norms": norms}, "candidates": [lookup.candidate_record(place) for place in places],
            "summary": {"match": "contains", "looked_up": " | ".join(norms), "places_found": len(places),
                        "kinds": dict(Counter(kinds)), "candidate_kinds": kinds, "plural_removed": False,
                        "direction_word_removed": ""}}


def widened(result, wider):
    """
    Return the decision for a name the rules declined as not_found or
    approximate_only, now that the contains search found wider (a lookup
    answer): left to the model (rule model_contains) when wider has
    candidates, else the decision as it was.
    """
    if result["rule"] not in ("not_found", "approximate_only") or not wider["candidates"]:
        return result
    return dict(result, decision="model", rule="model_contains", reason=None)


def in_map_scope(boxes, points, shape):
    """Return True when the map draws nothing of the record's own: no point and no box with a solid line."""
    return not points and not any(not (box_flags(box, shape) & NOT_SOLID_FLAGS) for box in boxes)


def record_names(looked_up_places):
    """
    Return the place names of one record once each, in pass 1's order:
    {name, as_written, roles}. as_written is the first one given for a study
    place (else the first one), and roles every role the name had.
    """
    names = {}
    for place in looked_up_places:
        entry = names.setdefault(place["name"], {"name": place["name"], "as_written": None, "roles": []})
        if place["role"] not in entry["roles"]:
            entry["roles"].append(place["role"])
        if entry["as_written"] is None or (place["role"] != "affiliation" and entry["roles"][0] == "affiliation"):
            entry["as_written"] = place["as_written"] or place["name"]
    return list(names.values())


def pattern_of(text):
    """Return a case-insensitive pattern for text, any run of spaces matching any other."""
    return re.compile(r"\s+".join(re.escape(word) for word in text.split()), re.IGNORECASE)


def sentence_around(text, start, end, longest=400):
    """
    Return the sentence of text that holds text[start:end], copied unchanged:
    from after the previous full stop, question or exclamation mark followed
    by a space, or line break (or the start), to the next one. A sentence
    longer than longest is cut to a window around the match, at spaces.
    """
    first = max((m.end() for m in re.finditer(r"[.!?]\s+|\n+", text) if m.end() <= start), default=0)
    last = min((m.start() + 1 for m in re.finditer(r"[.!?](?=\s|$)|\n", text) if m.start() >= end),
               default=len(text))
    if last - first > longest:
        first = max(first, start - longest // 2)
        last = min(last, end + longest // 2)
        first = text.find(" ", first, start) + 1 if text.find(" ", first, start) != -1 else first
        last = text.rfind(" ", end, last) if text.rfind(" ", end, last) != -1 else last
    return text[first:last].strip()


def find_quote(record, text):
    """
    Return (field, quote) for where a record gives text, copied unchanged
    from it: the entry of its harvested place list, then the keyword, then
    the title, then the sentence of the abstract. (None, None) when text is
    not in the record.
    """
    if not text or not text.strip():
        return None, None
    pattern = pattern_of(text)
    for field in ("places", "subjects"):
        for entry in record.get(field) or []:
            if pattern.search(entry):
                return field, entry
    if pattern.search(record.get("title") or ""):
        return "title", record["title"]
    abstract = record.get("abstract") or ""
    found = pattern.search(abstract)
    if found:
        return "abstract", sentence_around(abstract, found.start(), found.end())
    return None, None


def hinted_candidates(parts, name_norms, candidates, hint_places):
    """
    Return the candidates that lie in every province, state or country named
    in parts (the pieces of one place list entry or one comma-written name),
    when one of the parts is the name itself and at least one other is a
    recognised province, state or country; else None. hint_places is
    GeoNamesLookup.hint_places.
    """
    norms = [normalise(part) for part in parts]
    if not any(norm in name_norms for norm in norms):
        return None
    recognised = [found for part, norm in zip(parts, norms) if norm not in name_norms
                  for found in [hint_places(part)] if found]
    if not recognised:
        return None
    return [c for c in candidates
            if all(any(c["country_code"] == country and (admin1 is None or c["admin1_code"] == admin1)
                       for country, admin1 in area) for area in recognised)]


def hint_pick(record, name, lookup, hint_places):
    """
    Return the two hint rules' decision for a name, or None when neither
    applies: (rule, candidate or None, field, quote). The candidate is the
    one place with the name in the hinted area; None means the name is
    hinted but that place is a large area. A hint in the text only decides
    a large area; a small place is left to the model.
    """
    how = lookup["summary"]
    if how.get("match") != "exact" or how.get("plural_removed") or how.get("direction_word_removed"):
        return None
    name_norms = {normalise(split_comma(name["name"])[0]), normalise(split_comma(name["as_written"])[0])}
    candidates = lookup["candidates"]
    for entry in record.get("places") or []:
        parts = [part.strip() for part in re.split(r"[;,]", entry) if part.strip()]
        inside = hinted_candidates(parts, name_norms, candidates, hint_places)
        if inside is not None and len(inside) == 1:
            return "record_place_list", (None if is_large(inside[0]) else inside[0]), "places", entry
    written = name["as_written"]
    if "," in written:
        field, quote = find_quote(record, written)
        inside = hinted_candidates([part.strip() for part in written.split(",")], name_norms, candidates,
                                   hint_places) if quote else None
        if inside is not None and len(inside) == 1 and is_large(inside[0]):
            return "written_with_hint", None, field, quote
    return None


def decide(record, name, lookup, too_broad, hint_places):
    """
    Return the rules' decision for one place name of one record (see the
    module docstring): {decision, rule, reason, candidate, field, quote}.
    lookup is GeoNamesLookup.lookup's answer for the name with every
    candidate; too_broad the names of place_rules_too_broad.csv.
    """
    field, quote = find_quote(record, name["as_written"])
    if field is None:
        field, quote = find_quote(record, name["name"])

    def answer(decision, rule, reason=None, candidate=None, at=None, words=None):
        """Return one decision, with the name's quote unless a rule gives its own."""
        return {"decision": decision, "rule": rule, "reason": reason, "candidate": candidate,
                "field": at or field, "quote": words or quote}

    summary = lookup["summary"]
    candidates = lookup["candidates"]
    if all(role == "affiliation" for role in name["roles"]):
        return answer("do_not_draw", "affiliation", "affiliation")
    if normalise(split_comma(name["name"])[0]) in too_broad:
        return answer("do_not_draw", "too_broad_list", "too_broad")
    if summary.get("match") == "too generic":
        return answer("do_not_draw", "too_generic", "too_broad")
    if not candidates:
        return answer("do_not_draw", "not_found", "not_in_geonames")
    if summary.get("match") == "approximate":
        return answer("do_not_draw", "approximate_only", "not_in_geonames")
    hinted = hint_pick(record, name, lookup, hint_places)
    if hinted:
        rule, candidate, at, words = hinted
        if candidate is None:
            return answer("do_not_draw", rule, "too_broad", at=at, words=words)
        return answer("pick", rule, candidate=candidate, at=at, words=words)
    kinds = summary.get("candidate_kinds") or []
    top_kind = kinds[0] if kinds else None
    small_in_bc = any(kind in ("in_bc", "spans_bc_border") and not is_large(c) for c, kind in zip(candidates, kinds))
    if (is_large(candidates[0]) or top_kind == "contains_bc") and not small_in_bc:
        reason = "too_broad" if top_kind == "contains_bc" else large_reason(candidates[0])
        return answer("do_not_draw", "large_area", reason)
    return answer("model", "model")


def listed_candidates(candidates, kinds):
    """
    Return (listed, omitted) for a name's candidates in the lookup's order:
    every candidate when there are at most MAX_LISTED; else every one in BC
    or crossing its border, every one in Canada and the best OTHERS_LISTED
    of the rest, in the lookup's order. omitted counts the others left out,
    by country code.
    """
    if len(candidates) <= MAX_LISTED:
        return list(candidates), {}
    listed, omitted, others = [], Counter(), 0
    for candidate, kind in zip(candidates, kinds):
        if kind in ("in_bc", "spans_bc_border") or candidate["country_code"] == "CA":
            listed.append(candidate)
        elif others < OTHERS_LISTED:
            listed.append(candidate)
            others += 1
        else:
            omitted[candidate["country_code"] or "none"] += 1
    return listed, dict(omitted.most_common())


def model_candidate(candidate, kind):
    """Return the fields of a candidate the model sees, with our reading of it (kind, large) kept for the checks."""
    return {"geonameid": candidate["geonameid"], "name": candidate["main_name"],
            "matched_name": candidate["matched_name"], "feature": candidate["feature_code_name"] or
            f"{candidate['feature_class']}.{candidate['feature_code']}", "feature_code": candidate["feature_code"],
            "admin1": candidate["admin1_name"], "country": candidate["country_name"] or candidate["country_code"],
            "country_code": candidate["country_code"], "latitude": candidate["latitude"],
            "longitude": candidate["longitude"], "population": candidate["population"], "kind": kind,
            "large": is_large(candidate)}


def name_row(record, name, lookup, result, scope):
    """Return one row of names.csv."""
    summary, candidate = lookup["summary"], result["candidate"] or {}
    kinds = summary.get("kinds") or {}
    return {
        "dataset_id": record["id"], "title": record["title"], "catalogue": record["source_name"],
        "frontend_group": record["frontend_group"], "final_class": record["final_class"], "in_map_scope": scope,
        "name": name["name"], "as_written": name["as_written"], "roles": " ".join(name["roles"]),
        "match": summary.get("match"), "places_found": summary.get("places_found", 0),
        "places_in_bc": kinds.get("in_bc", 0), "decision": result["decision"], "rule": result["rule"],
        "reason": result["reason"], "geonames_id": candidate.get("geonameid"),
        "geonames_name": candidate.get("main_name"), "feature_code": candidate.get("feature_code"),
        "admin1": candidate.get("admin1_name"), "country_code": candidate.get("country_code"),
        "latitude": candidate.get("latitude"), "longitude": candidate.get("longitude"),
        "quote_field": result["field"], "quote": result["quote"],
    }


def model_item(record, scope, decided):
    """
    Return one line of model_items.jsonl: the record as harvested (title,
    abstract, keywords, place list), its catalogue and our class, and every
    place name with the rules' decision; names left to the model also carry
    their candidate list and the counts of candidates left out.
    """
    names = []
    for name, lookup, result in decided:
        entry = {"name": name["name"], "as_written": name["as_written"], "roles": name["roles"],
                 "decision": result["decision"], "rule": result["rule"], "reason": result["reason"]}
        if result["decision"] == "model":
            kinds = lookup["summary"].get("candidate_kinds") or []
            listed, omitted = listed_candidates(lookup["candidates"], kinds)
            kind_of = {c["geonameid"]: k for c, k in zip(lookup["candidates"], kinds)}
            entry.update(candidates=[model_candidate(c, kind_of.get(c["geonameid"])) for c in listed],
                         candidates_found=len(lookup["candidates"]), omitted_by_country=omitted,
                         match=lookup["summary"].get("match"), looked_up=lookup["summary"].get("looked_up"),
                         direction_word_removed=lookup["summary"].get("direction_word_removed") or "",
                         plural_removed=bool(lookup["summary"].get("plural_removed")))
        names.append(entry)
    return {"dataset_id": record["id"], "in_map_scope": scope, "title": record["title"],
            "abstract": record["abstract"], "keywords": record["subjects"],
            "place_list": record["places"], "catalogue": record["source_name"],
            "final_class": record["final_class"], "frontend_group": record["frontend_group"], "names": names}


def count_lines(rows):
    """Return counts.txt's lines: decisions and rules for all names and for those in the map's scope, and examples."""
    frame = pd.DataFrame(rows)
    lines = ["Place lookup, stage 1 (rules only, no model)", "=" * 44, ""]
    for label, part in (("All shown datasets", frame), ("In the map's scope (no box or point drawn from the record)",
                                                         frame[frame["in_map_scope"]])):
        lines += [f"{label}: {len(part):,} names in {part['dataset_id'].nunique():,} datasets", ""]
        table = part.groupby(["decision", "rule"]).agg(names=("name", "size"), datasets=("dataset_id", "nunique"))
        lines += table.to_string().splitlines() + [""]
        left = part[part["decision"] == "model"]
        lines += [f"Left to the model: {len(left):,} names in {left['dataset_id'].nunique():,} datasets", ""]
    lines += ["Examples (in the map's scope first, then the rest; the first 10 of each rule)", ""]
    ordered = pd.concat([frame[frame["in_map_scope"]], frame[~frame["in_map_scope"]]])
    for rule, part in ordered.groupby("rule", sort=False):
        lines.append(f"{rule} ({len(part):,})")
        for _, row in part.drop_duplicates(["name", "rule"]).head(EXAMPLES).iterrows():
            where = (f" -> {row['geonames_name']}, {row['admin1']}, {row['country_code']} ({row['feature_code']})"
                     if row["decision"] == "pick" else "")
            quote = f' | {row["quote_field"]}: "{row["quote"][:160]}"' if row["quote"] else " | not found in the record"
            lines.append(f"  {row['name']!r} in \"{row['title'][:70]}\"{where}{quote}")
        lines.append("")
    return lines


def main():
    """Read the shown datasets, decide every place name by the rules, and write names.csv, model_items.jsonl and counts.txt."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("--datasets", default=DEFAULT_DATASETS)
    parser.add_argument("--bc-boundary", required=True, help="BC's outline as WKT (bc_boundary_gbif.wkt)")
    parser.add_argument("--out", default=DEFAULT_OUT)
    args = parser.parse_args()
    if os.path.exists(args.out):
        raise SystemExit(f"ERROR: {args.out} already exists; move it aside first")

    shape = load_bc_shape(args.bc_boundary)
    too_broad = read_too_broad()
    lookup = GeoNamesLookup()
    datasets = pd.read_parquet(args.datasets, columns=[
        "id", "title", "abstract", "subjects", "places", "source_name", "final_class", "frontend_group", "boxes",
        "points", "looked_up_places"])
    cache, wider_cache, rows, items = {}, {}, [], []
    for record in datasets.to_dict("records"):
        # The list columns come as arrays and a missing text as NaN; plain
        # lists and None are easier to test.
        for column in ("subjects", "places", "boxes", "points", "looked_up_places"):
            record[column] = list(record[column]) if record[column] is not None else []
        for column in ("title", "abstract"):
            record[column] = record[column] if isinstance(record[column], str) else None
        names = record_names(record["looked_up_places"])
        if not names:
            continue
        scope = in_map_scope(record["boxes"], record["points"], shape)
        decided = []
        for name in names:
            key = (name["name"], name["as_written"])
            if key not in cache:
                cache[key] = lookup.lookup(name["name"], name["as_written"], max_rows=10**6)
            found = cache[key]
            result = decide(record, name, found, too_broad, lookup.hint_places)
            if result["rule"] in ("not_found", "approximate_only"):
                norms = tuple(contains_norms(name))
                if norms not in wider_cache:
                    wider_cache[norms] = contains_lookup(lookup, list(norms)) if norms else {"candidates": []}
                if widened(result, wider_cache[norms]) is not result:
                    result, found = widened(result, wider_cache[norms]), wider_cache[norms]
            decided.append((name, found, result))
            rows.append(name_row(record, name, found, result, scope))
        if any(result["decision"] == "model" for _, _, result in decided):
            items.append(model_item(record, scope, decided))

    os.makedirs(args.out)
    pd.DataFrame(rows).to_csv(os.path.join(args.out, "names.csv"), index=False)
    with open(os.path.join(args.out, "model_items.jsonl"), "w", encoding="utf-8") as handle:
        for item in items:
            handle.write(json.dumps(item, ensure_ascii=False, default=str) + "\n")
    lines = count_lines(rows)
    with open(os.path.join(args.out, "counts.txt"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    print("\n".join(lines[:40]))
    print(f"\nwrote names.csv ({len(rows):,} rows), model_items.jsonl ({len(items):,} datasets) and counts.txt "
          f"in {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
