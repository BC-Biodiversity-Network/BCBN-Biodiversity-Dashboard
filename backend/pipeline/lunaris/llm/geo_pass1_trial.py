"""
geo_pass1_trial.py

Try pass 1 of the geography step (geo_prompt_v3.py) on a small sample of
the records the BC filter's rules left undecided (needs_llm), on the local
Ollama, and write what came back. Optionally, compare the answers with
earlier runs on the same sample, including the trials of the earlier prompt
versions 1 and 2 (their answers are read from their output folders; the old
prompts themselves are no longer needed).

The sample is drawn with a fixed random seed and spread across the reasons
the rules could not decide (STRATA below): no evidence at all, a huge box, a
partly-BC box, a mostly-BC box, plus the rare sign-typo and disagreement
cases. Within each reason, half the records have BC mentioned in the text
(by the rules' cross-check columns) and half do not, where there are enough.
The same seed and rules file give the same sample, so runs can be compared
record by record. With --sample, the records listed in that CSV (record_id
column) are run instead, in its order, such as the labelled records of
data/lunaris_bc_filter/label_set_2026_10.

Settings are those of the Mac runs of prompt v6: the shared ask_ollama,
temperature 0, thinking off, a 16,384-token context, Ollama choosing its own
threads.

After each answer, checks run in code (geo_prompt_v3.clean_places and
geo_answer_checks.py): a place whose as_written is not in the record's own
text is dropped (the leak check), duplicates (one place listed twice for the
same role) are counted and removed, and every name that is neither in the
record's text nor an expansion of its as_written is listed for a person to
look at. For an earlier run given to --compare-with, the same checks are
counted on its answers as they were saved.

Output (--out), all ignored by git:
  sample.csv        the records drawn, with their reason and text flag
  prompts/NN.txt    the exact text sent for each record
  answers.jsonl     one line per record: the answer or the error, tokens,
                    seconds, and the places our code kept and dropped
  report.txt        the extracted places per record, every distinct name with
                    counts, the leak check, names to look at, duplicates,
                    failures, speed and prompt length
  compare_X.txt     for each --compare-with folder X: the records whose places
                    changed from that run, and the two runs' figures side by side

Run, from backend/:
    python pipeline/lunaris/llm/geo_pass1_trial.py \
        --bc-boundary /Users/lucia/Desktop/BCBN/Results/bc_boundary_gbif.wkt \
        --compare-with data/lunaris_bc_filter/pass1_trial \
        --compare-with data/lunaris_bc_filter/pass1_trial_v2
"""

import argparse
import json
import os
import statistics
import sys
import time
from collections import Counter

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
from bc_filter_rules import load_bc_shape  # noqa: E402
import geo_prompt_v3 as prompt  # noqa: E402
from geo_answer_checks import name_needs_a_look, squash, written_in_record  # noqa: E402
from ollama_client import ask_ollama, wait_for_server  # noqa: E402

# Where the trial is written unless --out says otherwise.
DEFAULT_OUT = "data/lunaris_bc_filter/pass1_trial_v3"

# Why the rules could not decide (from their evidence column), and how many
# records to draw for each.
STRATA = [
    ("no evidence", lambda e: e == "nothing that decides", 16),
    ("huge box", lambda e: e.startswith("huge box"), 10),
    ("partly-BC box", lambda e: e.startswith("box partly in BC"), 6),
    ("mostly-BC box", lambda e: e.startswith("box mostly in BC"), 6),
    ("sign typo", lambda e: "longitude sign flipped" in e, 1),
    ("disagree", lambda e: e.startswith("disagree"), 1),
]


def draw_sample(rules, seed):
    """
    Return the sample: for each reason in STRATA, its number of records,
    half with BC mentioned in the text and half without where possible.
    """
    llm = rules[rules["bc_class"] == "needs_llm"].copy()
    text_cols = ["text_title", "text_keywords", "text_abstract"]
    llm["text_mentions_bc"] = (llm[text_cols].fillna("") != "").any(axis=1)
    picked = []
    for name, matches, wanted in STRATA:
        pool = llm[llm["evidence"].map(matches)]
        with_bc, without = pool[pool["text_mentions_bc"]], pool[~pool["text_mentions_bc"]]
        take_bc = min(len(with_bc), (wanted + 1) // 2)
        take_without = min(len(without), wanted - take_bc)
        take_bc = min(len(with_bc), wanted - take_without)  # top up if one side is short
        part = pd.concat([with_bc.sample(take_bc, random_state=seed),
                          without.sample(take_without, random_state=seed)])
        part["reason"] = name
        picked.append(part)
    return pd.concat(picked).reset_index(drop=True)


def sample_from_file(rules, path):
    """
    Return the records listed in a CSV with a record_id column, in the file's
    order, as rows of the rules file (instead of draw_sample). Stop if a
    record id is not in the rules file.
    """
    wanted = pd.read_csv(path, keep_default_na=False)["record_id"]
    rows = rules.set_index("record_id")
    missing = [record_id for record_id in wanted if record_id not in rows.index]
    if missing:
        sys.exit(f"{len(missing)} record ids from {path} are not in the rules file, such as {missing[0]}")
    sample = rows.loc[list(wanted)].reset_index()
    text_cols = ["text_title", "text_keywords", "text_abstract"]
    sample["text_mentions_bc"] = (sample[text_cols].fillna("") != "").any(axis=1)
    sample["reason"] = "listed in " + os.path.basename(path)
    return sample


def read_harvest(path, ids):
    """Return the harvest rows for the given ids (latest datestamp per id), as dicts by id."""
    columns = ["id", "datestamp", "title", "subjects", "abstract", "publisher", "places", "points", "boxes"]
    harvest = pd.read_parquet(path, columns=columns)
    harvest = harvest[harvest["id"].isin(set(ids))]
    harvest = harvest.sort_values("datestamp", ascending=False, kind="stable").drop_duplicates("id")
    return {row["id"]: row for row in harvest.to_dict("records")}


def not_in_record(places, prompt):
    """
    Return the as_written values that do not appear in the record's own text
    (ignoring case and spacing): names taken from somewhere else, such as the
    instructions or our notes, or reworded by the model.
    """
    return [p["as_written"] for p in places if not written_in_record(p["as_written"], prompt)]


def place_name(place):
    """Return a place's name: standard_name in version 1, name from version 2."""
    return place["standard_name"] if "standard_name" in place else place["name"]


def place_key(place):
    """Return what makes two places the same place for the same role: the name (case and spacing ignored) and role."""
    return (squash(place_name(place)), place["role"])


def duplicates(places):
    """Return the places that repeat an earlier one's name and role, as listed by the model."""
    seen, repeats = set(), []
    for place in places:
        if place_key(place) in seen:
            repeats.append(place)
        seen.add(place_key(place))
    return repeats


def place_text(place):
    """Write one place on one line, for the reports."""
    text = f"{place['as_written']!r} -> {place_name(place)!r}"
    within = [place.get("within_region", ""), place.get("within_country", "")]
    if any(within):
        text += " (within " + ", ".join(w for w in within if w) + ")"
    return text + f" [{place['role']}]"


def final_places(result):
    """Return the places a result ends with: after duplicates were removed if they were, else as answered."""
    if "places" in result:
        return result["places"]
    return result["answer"]["places"]


def read_run(folder):
    """Read an earlier run's answers (by record id) and prompts (by number) from its folder."""
    results = {}
    with open(os.path.join(folder, "answers.jsonl"), encoding="utf-8") as handle:
        for line in handle:
            result = json.loads(line)
            results[result["record_id"]] = result
    prompts = {}
    for name in os.listdir(os.path.join(folder, "prompts")):
        with open(os.path.join(folder, "prompts", name), encoding="utf-8") as handle:
            prompts[int(name.split(".")[0])] = handle.read()
    return results, prompts


def run_figures(sample, results, prompts):
    """
    Return the summary lines for one run: distinct names with counts, how
    many come with a region or country (version 2), roles, the leak check,
    the names to look at, duplicates, failures, speed and prompt length.
    """
    lines = []
    names, by_role = Counter(), Counter()
    with_region = with_country = mentions = 0
    leaks, repeats, odd_names = [], [], []
    has_within = False
    for number, row in sample.iterrows():
        result = results[row["record_id"]]
        if "error" in result:
            continue
        repeats += [(number, p) for p in duplicates(result["answer"]["places"])]
        leaks += [(number, name) for name in not_in_record(result["answer"]["places"], prompts[number])]
        for place in final_places(result):
            if name_needs_a_look(place, place_name(place), prompts[number]):
                odd_names.append((number, place))
            has_within = has_within or "within_region" in place
            mentions += 1
            names[place_name(place)] += 1
            by_role[place["role"]] += 1
            with_region += bool(place.get("within_region"))
            with_country += bool(place.get("within_country"))
    lines.append(f"DISTINCT NAMES ({len(names)}) over {mentions} mentions, with counts")
    for name, count in sorted(names.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"  {count:3d}  {name}")
    lines.append("")
    if has_within:
        lines.append(f"mentions with a region: {with_region}; with a country: {with_country} (of {mentions})")
    lines.append("ROLES: " + ", ".join(f"{role} {n}" for role, n in by_role.most_common()))
    lines.append(f"LEAK CHECK: {len(leaks)} as_written not found in the record's own text")
    lines += [f"  [{number:02d}] {name!r}" for number, name in leaks]
    if any("dropped" in r for r in results.values()):
        lines.append("  (from version 3 these places are dropped by our code, so they are not in the places above)")
    lines.append(f"NAMES TO LOOK AT (not in the record's text, not an expansion of as_written): {len(odd_names)}")
    lines += [f"  [{number:02d}] {place_text(place)}" for number, place in odd_names]
    lines.append(f"DUPLICATES in the model's answers (same name and role): {len(repeats)}")
    lines += [f"  [{number:02d}] {place_text(place)}" for number, place in repeats]
    failed = [r for r in results.values() if "error" in r]
    seconds = [r["seconds"] for r in results.values() if "seconds" in r]
    tokens = [r["input_tokens"] for r in results.values() if r.get("input_tokens")]
    lines.append(f"JSON or schema failures: {len(failed)} of {len(results)}")
    if seconds:
        lines.append(f"seconds per record: mean {statistics.mean(seconds):.1f}, median "
                     f"{statistics.median(seconds):.1f}, range {min(seconds):.1f} to {max(seconds):.1f}")
    if tokens:
        lines.append(f"prompt tokens: mean {statistics.mean(tokens):,.0f}, median "
                     f"{statistics.median(tokens):,.0f}, range {min(tokens):,} to {max(tokens):,}")
    return lines


def write_report(path, sample, results, prompts):
    """Write the per-record places, then the run's figures (run_figures)."""
    lines = []
    for number, row in sample.iterrows():
        result = results[row["record_id"]]
        lines.append(f"[{number:02d}] {row['title']}")
        lines.append(f"     reason: {row['reason']}; BC in text (rules): {'yes' if row['text_mentions_bc'] else 'no'}; "
                     f"source: {row['source']}")
        if "error" in result:
            lines.append(f"     ERROR: {result['error']}")
            continue
        places = final_places(result)
        if not places:
            lines.append("     (no places)")
        lines += [f"     - {place_text(place)}" for place in places]
    lines.append("")
    lines += run_figures(sample, results, prompts)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def comparable(place):
    """
    Return a place as (as_written, name, region, country, role), so that a
    version 1 place (which has no region or country) and a version 2 place
    can be compared.
    """
    return (place["as_written"], place_name(place), place.get("within_region", ""),
            place.get("within_country", ""), place["role"])


def write_comparison(path, sample, before, before_prompts, after, after_prompts):
    """
    Write the records whose places changed between an earlier run (before)
    and this one (after), with both lists in full, then both runs' figures.
    A record counts as changed if any place's as_written, name, region,
    country or role differs, or a place was added or dropped.
    """
    lines, changed = [], 0
    for number, row in sample.iterrows():
        old, new = before.get(row["record_id"]), after[row["record_id"]]
        if old is None:
            lines.append(f"[{number:02d}] not in the earlier run")
            continue
        old_places = [] if "error" in old else final_places(old)
        new_places = [] if "error" in new else final_places(new)
        if "error" not in old and "error" not in new and \
                sorted(map(comparable, old_places)) == sorted(map(comparable, new_places)):
            continue
        changed += 1
        lines.append(f"[{number:02d}] {row['title']}")
        lines.append("   before:" + (" ERROR" if "error" in old else "" if old_places else " (no places)"))
        lines += [f"     - {place_text(p)}" for p in old_places]
        lines.append("   after:" + (" ERROR" if "error" in new else "" if new_places else " (no places)"))
        lines += [f"     - {place_text(p)}" for p in new_places]
    lines.insert(0, f"RECORDS THAT CHANGED: {changed} of {len(sample)}")
    lines.insert(1, "")
    lines += ["", "=== BEFORE ===", *run_figures(sample, before, before_prompts),
              "", "=== AFTER ===", *run_figures(sample, after, after_prompts)]
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def main():
    """Draw the sample, run pass 1 on it, write the outputs, and compare with an earlier run if asked."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("--rules", default="data/lunaris_bc_filter/rules_2026_10.csv")
    parser.add_argument("--harvest", default="exploration/lunaris/harvest_2026_10_20/lunaris_full_harvest.parquet")
    parser.add_argument("--bc-boundary", required=True, help="bc_boundary_gbif.wkt")
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--compare-with", action="append", default=[],
                        help="an earlier run's output folder, to compare with; may be given more than once")
    parser.add_argument("--model", default="qwen3.5:4b")
    parser.add_argument("--ollama-url", default="http://localhost:11434")
    parser.add_argument("--seed", type=int, default=20261022)
    parser.add_argument("--sample", help="a CSV with a record_id column: run these records, in this order, "
                                         "instead of drawing the trial sample")
    args = parser.parse_args()
    out = args.out

    os.makedirs(os.path.join(out, "prompts"), exist_ok=True)
    rules = pd.read_csv(args.rules, keep_default_na=False)
    sample = sample_from_file(rules, args.sample) if args.sample else draw_sample(rules, args.seed)
    sample[["record_id", "source", "title", "reason", "text_mentions_bc", "evidence"]].to_csv(
        os.path.join(out, "sample.csv"), index_label="number")
    print(sample.groupby(["reason", "text_mentions_bc"]).size().to_string())

    shape = load_bc_shape(args.bc_boundary)
    harvest = read_harvest(args.harvest, sample["record_id"])
    print(f"Ollama {wait_for_server(args.ollama_url, 60)} at {args.ollama_url}, model {args.model}, "
          f"geography prompt v{prompt.GEO_PROMPT_VERSION}")

    results, prompts = {}, {}
    with open(os.path.join(out, "answers.jsonl"), "w", encoding="utf-8") as answers:
        for number, row in sample.iterrows():
            prompts[number] = prompt.build_prompt(harvest[row["record_id"]], shape)
            with open(os.path.join(out, "prompts", f"{number:02d}.txt"), "w", encoding="utf-8") as handle:
                handle.write(prompts[number])
            started = time.monotonic()
            result = ask_ollama(args.ollama_url, args.model, prompts[number], schema=prompt.RESPONSE_SCHEMA)
            result["seconds"] = round(time.monotonic() - started, 2)
            if "answer" in result:
                problem = prompt.check_answer(result["answer"])
                if problem:
                    result = {"error": f"answer does not fit the schema: {problem}",
                              "seconds": result["seconds"], "input_tokens": result.get("input_tokens")}
                else:
                    result["places"], result["dropped"] = prompt.clean_places(result["answer"]["places"],
                                                                              prompts[number])
                    for place in result["dropped"]:
                        print(f"  [{number:02d}] dropped, not in the record's text: {place_text(place)}", flush=True)
            result["record_id"] = row["record_id"]
            results[row["record_id"]] = result
            answers.write(json.dumps(result, ensure_ascii=False) + "\n")
            answers.flush()
            places = len(final_places(result)) if "answer" in result else "ERROR"
            print(f"  [{number:02d}] {result['seconds']:5.1f} s  {places} places  {row['title'][:70]}", flush=True)

    write_report(os.path.join(out, "report.txt"), sample, results, prompts)
    for folder in args.compare_with:
        before, before_prompts = read_run(folder)
        name = "compare_" + os.path.basename(os.path.normpath(folder)) + ".txt"
        write_comparison(os.path.join(out, name), sample, before, before_prompts, results, prompts)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
