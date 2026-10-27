"""Tests for the BC filter's LLM run (pipeline/lunaris/llm/geo_filter_run.py)
and its input folder (geo_filter_prepare.py), with a made-up model and a
made-up lookup, so neither Ollama nor the GeoNames table is needed: a full
run writes the merged files in the Mac trial layout; a rerun asks nothing
again; a failed answer is asked once more, then logged, and asked again on
the next run; a record whose pass 1 failed is not asked pass 2; the parts of
an array split the records and the merged files wait for every part; a
pass 1 prompt that differs from the Mac's stops the run before anything is
asked; repeated server failures stop it; and a record survives the JSON
copy of the input folder unchanged.

They reuse the made-up BC and record of test_geo_pass2_places.py."""

import gzip
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

BACKEND = Path(__file__).resolve().parents[1]
# The pipeline scripts are not a package; add their folders to the path the
# way the scripts themselves do.
sys.path.insert(0, str(BACKEND / "pipeline" / "lunaris"))
sys.path.insert(0, str(BACKEND / "pipeline" / "lunaris" / "llm"))
import geo_filter_prepare as prep  # noqa: E402
import geo_filter_run as gfr  # noqa: E402
import geo_pass2_prompt_c4 as c4  # noqa: E402
import geo_pass2_trial  # noqa: E402
import geo_prompt_v3  # noqa: E402
from prompt_v6 import prompt_sha1  # noqa: E402
from test_geo_pass2_places import RECORD, TOY_BC  # noqa: E402

PASS1_ANSWER = {"places": [{"as_written": "Lake Fakeville", "name": "Lake Fakeville", "role": "study_location"}]}
PASS2_ANSWER = {"reason": "r", "evidence": ["Lake Fakeville"], "in_bc": "yes", "other_canada": "no",
                "outside_canada": "no", "canada_wide": "no", "global": "no"}


class FakeLookup:
    """A lookup that finds nothing, with the result fields the prompt and the report read."""

    def __init__(self):
        """Count the lookups made."""
        self.calls = 0

    def lookup(self, name, as_written=None):
        """Return a "not found" result for one place."""
        self.calls += 1
        summary = {"category": "not_found", "category_text": "not found", "places_found": 0, "match": "none",
                   "matched_on": "", "plural_removed": False, "direction_word_removed": "", "historic": False,
                   "hints": [], "hint_check": "", "only_hand": False, "kinds": {}, "candidate_kinds": []}
        return {"query": {"name": name, "as_written": as_written}, "candidates": [], "summary": summary}


class FakeModel:
    """
    A model that answers both passes. fail maps (pass, number) to how many
    times in a row that prompt fails before it is answered (None: always).
    """

    def __init__(self, fail=None, error="reply was not valid JSON: x"):
        """Keep the failures to give and count the calls."""
        self.fail, self.error, self.calls = dict(fail or {}), error, []

    def __call__(self, text, schema):
        """Answer one prompt, or fail as set up."""
        which = "pass1" if schema is geo_prompt_v3.RESPONSE_SCHEMA else "pass2"
        number = int(text.split("Record identifier: oai:example.org:")[1].split("\n")[0])
        self.calls.append((which, number))
        left = self.fail.get((which, number), 0)
        if left is None or left > 0:
            if left:
                self.fail[(which, number)] = left - 1
            return {"error": self.error}
        answer = PASS1_ANSWER if which == "pass1" else PASS2_ANSWER
        return {"answer": json.loads(json.dumps(answer)), "input_tokens": 100}


def make_input(folder, count):
    """Write an input folder of count made-up records, as geo_filter_prepare.py writes one."""
    folder.mkdir(parents=True, exist_ok=True)
    rows, lines = [], []
    for number in range(count):
        record = dict(RECORD, id=f"oai:example.org:{number}")
        rows.append({"number": number, "record_id": record["id"], "source": "example", "title": record["title"],
                     "reason": "test", "text_mentions_bc": True, "evidence": ""})
        lines.append({"number": number, "record_id": record["id"], "record": record,
                      "pass1_sha1": prompt_sha1(geo_prompt_v3.build_prompt(record, TOY_BC))})
    pd.DataFrame(rows).to_csv(folder / "sample.csv", index=False)
    with gzip.open(folder / "records.jsonl.gz", "wt", encoding="utf-8") as handle:
        for line in lines:
            handle.write(json.dumps(line) + "\n")
    return folder


def make_run(tmp_path, count=2, part=0, parts=1, lookup=None):
    """Return a Run on a fresh input of count records, for one part, and its pass 1 prompts."""
    sample, records = gfr.read_input(make_input(tmp_path / "input", count))
    numbers = gfr.part_numbers(list(sample.index), part, parts)
    run = gfr.Run(str(tmp_path / "out"), sample, records, numbers, TOY_BC, lookup or FakeLookup(), part)
    return run, run.pass1_prompts()


def never_mind():
    """A GPU check that always passes."""


def jsonl(path):
    """Read a JSON lines file."""
    return [json.loads(line) for line in Path(path).read_text().splitlines()]


# --- A run and a rerun ---------------------------------------------------

def test_a_full_run_writes_the_mac_layout(tmp_path):
    """A full run asks both passes and writes the merged files in the Mac trial layout, with the Mac's prompts."""
    run, prompts = make_run(tmp_path)
    model = FakeModel()
    counts = run.run(model, never_mind, prompts, {"model": "m", "model_digest": "d"})
    assert counts == {"pass1_asked": 2, "pass1_failed": 0, "lookups": 2, "pass2_asked": 2, "pass2_failed": 0}
    assert model.calls == [("pass1", 0), ("pass1", 1), ("pass2", 0), ("pass2", 1)]
    assert run.finish()
    out = tmp_path / "out"
    pass1 = jsonl(out / "pass1" / "answers.jsonl")
    assert [line["number"] for line in pass1] == [0, 1] and pass1[0]["places"] == PASS1_ANSWER["places"]
    assert [line["record"] for line in jsonl(out / "pass1" / "lookup.jsonl")] == [0, 1]
    pass2 = jsonl(out / "pass2" / "answers.jsonl")
    assert [line["decision"]["class"] for line in pass2] == ["bc_only", "bc_only"]
    assert pass2[0]["evidence_check"]["unmatched"] == []
    for name in ("pass1/report.txt", "pass1/lookup.txt", "pass2/report.txt", "timing.csv", "pass1/sample.csv"):
        assert (out / name).exists(), name
    # The prompts are the ones the Mac builds.
    assert (out / "pass1" / "prompts" / "00.txt").read_text() == geo_prompt_v3.build_prompt(
        dict(RECORD, id="oai:example.org:0"), TOY_BC)
    places, lookups = [PASS1_ANSWER["places"][0]], [FakeLookup().lookup("Lake Fakeville", "Lake Fakeville")]
    assert (out / "pass2" / "prompts" / "01.txt").read_text() == c4.build_prompt(
        dict(RECORD, id="oai:example.org:1"), TOY_BC, places, lookups)
    # The Mac's pass 2 trial reads the merged pass 1 folder.
    sample, kept, looked_up = geo_pass2_trial.read_pass1(str(out / "pass1"))
    assert len(sample) == 2 and kept[1] == PASS1_ANSWER["places"] and len(looked_up[1]) == 1
    timing = pd.read_csv(out / "timing.csv")
    assert list(timing.columns) == ["number", "record_id", "pass1_s", "pass1_attempts", "lookup_s", "pass2_s",
                                    "pass2_attempts", "total_s"]


def test_a_rerun_asks_nothing_again(tmp_path):
    """A second run on a finished folder asks the model nothing and looks nothing up."""
    run, prompts = make_run(tmp_path)
    run.run(FakeModel(), never_mind, prompts, {})
    lookup = FakeLookup()
    again, prompts = make_run(tmp_path, lookup=lookup)
    model = FakeModel()
    counts = again.run(model, never_mind, prompts, {})
    assert model.calls == [] and lookup.calls == 0
    assert counts["pass1_asked"] == counts["pass2_asked"] == counts["lookups"] == 0
    assert again.todo(prompts) == ([], [], [])


# --- Failures ------------------------------------------------------------

def test_a_failed_answer_is_asked_once_more(tmp_path):
    """An answer that fails once is asked again at once, and the first error is kept."""
    run, prompts = make_run(tmp_path)
    run.run(FakeModel(fail={("pass1", 1): 1}), never_mind, prompts, {})
    line = gfr.latest_results(run.pass1_dir, "progress")[1]
    assert "answer" in line and line["attempts"] == 2 and line["first_error"].startswith("reply was not valid JSON")
    assert gfr.latest_results(run.pass1_dir, "progress")[0]["attempts"] == 1


def test_a_record_that_fails_twice_is_logged_and_asked_on_the_next_run(tmp_path):
    """A record failing twice is logged, gets no pass 2, and is asked again on the next run."""
    run, prompts = make_run(tmp_path)
    model = FakeModel(fail={("pass1", 1): None})
    counts = run.run(model, never_mind, prompts, {})
    assert model.calls.count(("pass1", 1)) == 2 and ("pass2", 1) not in model.calls
    assert counts["pass1_failed"] == 1
    pass1, _, pass2 = run.state()
    assert pass1[1]["attempts"] == 2 and "error" in pass1[1]
    assert pass2[1]["error"] == gfr.NO_PASS1
    log = Path(run.failures).read_text().splitlines()
    assert len(log) == 2 and "pass1" in log[0] and "attempts 2" in log[0] and "pass2" in log[1]
    assert run.todo(prompts) == ([1], [], [1])
    assert run.finish()                  # every record has a result, so the merged files are written
    assert "ERROR" in (Path(run.pass2_dir) / "report.txt").read_text()

    later = FakeModel()
    run.run(later, never_mind, prompts, {})
    assert later.calls == [("pass1", 1), ("pass2", 1)]
    assert run.todo(prompts) == ([], [], [])


def test_repeated_server_failures_stop_the_run(tmp_path):
    """Server failures on several records in a row stop the run with exit code 2."""
    run, prompts = make_run(tmp_path, count=7)
    model = FakeModel(fail={("pass1", n): None for n in range(7)}, error="gave up after 2 attempts: timed out")
    with pytest.raises(gfr.Stop) as stop:
        run.run(model, never_mind, prompts, {})
    assert stop.value.code == 2
    assert len(gfr.latest_results(run.pass1_dir, "progress")) == gfr.CONSECUTIVE_SERVER_FAILURES


def test_the_gpu_check_can_stop_the_run(tmp_path):
    """The GPU check after the first answer can stop the run with exit code 5."""
    run, prompts = make_run(tmp_path)

    def off_the_gpu():
        """A GPU check that finds the model on the CPU."""
        raise gfr.Stop(5, "not on the GPU")

    with pytest.raises(gfr.Stop) as stop:
        run.run(FakeModel(), off_the_gpu, prompts, {})
    assert stop.value.code == 5


def test_a_pass1_prompt_unlike_the_macs_stops_before_asking(tmp_path):
    """A pass 1 prompt whose fingerprint differs from the Mac's stops the run with exit code 1."""
    sample, records = gfr.read_input(make_input(tmp_path / "input", 2))
    records[1]["pass1_sha1"] = "0" * 12
    run = gfr.Run(str(tmp_path / "out"), sample, records, [0, 1], TOY_BC, FakeLookup(), 0)
    with pytest.raises(gfr.Stop) as stop:
        run.pass1_prompts()
    assert stop.value.code == 1 and "[1]" in stop.value.reason


def test_input_files_must_agree(tmp_path):
    """An input folder whose two files list different records is refused."""
    folder = make_input(tmp_path / "input", 2)
    sample = pd.read_csv(folder / "sample.csv")
    sample.loc[1, "record_id"] = "something else"
    sample.to_csv(folder / "sample.csv", index=False)
    with pytest.raises(gfr.Stop):
        gfr.read_input(folder)


# --- Parts ---------------------------------------------------------------

def test_parts_split_the_records():
    """The parts of an array take every record exactly once."""
    numbers = list(range(7))
    parts = [gfr.part_numbers(numbers, k, 3) for k in range(3)]
    assert parts == [[0, 3, 6], [1, 4], [2, 5]]
    assert sorted(sum(parts, [])) == numbers


def test_merged_files_wait_for_every_part(tmp_path):
    """The merged files are written only once every part has a result for every record."""
    run0, prompts0 = make_run(tmp_path, count=3, part=0, parts=2)
    run0.run(FakeModel(), never_mind, prompts0, {})
    assert not run0.finish()
    assert not (tmp_path / "out" / "pass2" / "answers.jsonl").exists()
    run1, prompts1 = make_run(tmp_path, count=3, part=1, parts=2)
    model = FakeModel()
    run1.run(model, never_mind, prompts1, {})
    assert model.calls == [("pass1", 1), ("pass2", 1)]
    assert run1.finish()
    assert [line["number"] for line in jsonl(tmp_path / "out" / "pass2" / "answers.jsonl")] == [0, 1, 2]


def test_limit_takes_the_first_records(tmp_path):
    """--limit keeps only the first records."""
    sample, records = gfr.read_input(make_input(tmp_path / "input", 5), limit=2)
    assert list(sample.index) == [0, 1] and sorted(records) == [0, 1]


# --- The input folder ----------------------------------------------------

def test_a_record_survives_the_json_copy():
    """A harvest row with arrays and NaN reads back from JSON unchanged, with the same pass 1 prompt."""
    row = dict(RECORD, abstract=float("nan"), subjects=np.array(["Fakeville; B.C.", "trout"], dtype=object),
               places=np.array([], dtype=object),
               boxes=np.array([{"w": -128.0, "s": 55.0, "e": -125.25, "n": 58.0}], dtype=object))
    copy = prep.json_copy(row)
    assert isinstance(copy["subjects"], list) and copy["places"] == [] and math.isnan(copy["abstract"])
    assert prep.same_value(prep.plain(row), copy)
    assert geo_prompt_v3.build_prompt(copy, TOY_BC) == geo_prompt_v3.build_prompt(row, TOY_BC)
    assert not prep.same_value(prep.plain(row), dict(copy, title="other"))


# --- Flash attention -----------------------------------------------------

RUNNER_LINE = ('time=2026-10-26T20:31:00.000-07:00 level=INFO source=llama_server.go:436 msg="starting llama-server" '
               'cmd="/node/ollama/lib/ollama/llama-server --model /node/blob -c 16384 -np 1 --spec-type draft-mtp '
               '--flash-attn {} -b 1024 -ub 1024 -t 6 --context-shift --keep 4"\n')


def test_the_runner_flash_attention_is_read_from_the_log(tmp_path):
    """The setting comes from the last runner command line; a line without one is "none"; no line is None."""
    log = tmp_path / "ollama.log"
    log.write_text("other line\n" + RUNNER_LINE.format("auto") + "more\n" + RUNNER_LINE.format("off"))
    assert gfr.runner_flash_attn(str(log)) == "off"
    log.write_text(RUNNER_LINE.replace("--flash-attn {} ", ""))
    assert gfr.runner_flash_attn(str(log)) == "none"
    log.write_text("no runner here\n")
    assert gfr.runner_flash_attn(str(log)) is None
    assert gfr.runner_flash_attn(str(tmp_path / "missing.log")) is None


def test_the_run_stops_if_flash_attention_is_not_off(tmp_path):
    """Asked for off, a runner with auto (or no runner line) stops the run; off passes; auto asked is only reported."""
    log = tmp_path / "ollama.log"
    log.write_text(RUNNER_LINE.format("auto"))
    with pytest.raises(gfr.Stop) as stop:
        gfr.check_runner_flash_attn(str(log), "off")
    assert stop.value.code == 1 and "--flash-attn auto" in stop.value.reason
    gfr.check_runner_flash_attn(str(log), "auto")
    log.write_text(RUNNER_LINE.format("off"))
    gfr.check_runner_flash_attn(str(log), "off")
    log.write_text("no runner here\n")
    with pytest.raises(gfr.Stop):
        gfr.check_runner_flash_attn(str(log), "off")


# --- Status --------------------------------------------------------------

def test_status_shows_each_part_and_what_is_left(tmp_path):
    """The status lists each part's answers and last job, and the parts to resubmit."""
    import geo_filter_status

    run0, prompts0 = make_run(tmp_path, count=3, part=0, parts=2)
    run0.run(FakeModel(fail={("pass1", 2): None}), never_mind, prompts0, {})
    append = gfr.append_line
    append(str(tmp_path / "out" / "logs" / "jobs.jsonl"),
           {"job_id": "11", "host": "fc1", "part": 0, "dry_run": False, "exit_code": 4})
    lines, unfinished = geo_filter_status.status(str(tmp_path / "out"), 2)
    text = "\n".join(lines)
    assert unfinished == [0, 1]
    assert "   0        2        1        1        1        1  job 11 on fc1, exit 4" in text
    assert "   1        1        0        0        0        0  no job finished yet" in text
    assert "ONLY=0,1" in text and "merged files: not yet" in text
