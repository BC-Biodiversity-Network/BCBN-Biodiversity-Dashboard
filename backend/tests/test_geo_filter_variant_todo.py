"""Tests that geo_filter_run.py judges what is left in the run's own
geography prompt variant (pipeline/lunaris/llm/geo_filter_run.py).

The bug these guard against: Run.todo() rebuilt each pass 2 prompt in the
Lunaris variant to compare it with the fingerprint saved with the answer.
In a DataONE run the two never matched, so every record looked unasked:
a finished run was left with every pass 2 to do and ended with exit 4, and
every rerun asked the whole of pass 2 again.

For each variant (lunaris and dataone) a small run is made in a temporary
folder and driven through geo_filter_run.main() itself, with a made-up
model, a made-up lookup and no Ollama:
  - when every record has both answers, todo() is empty and the exit code
    is 0, and a second run asks nothing;
  - when one record has no pass 2 answer, todo() lists exactly that record
    and the exit code is 4; once its answer is gone from the progress file,
    the next run asks only that record.
The prompt builders have no default variant, and the instructions keep
their fingerprints (no prompt text changed)."""

import gzip
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

BACKEND = Path(__file__).resolve().parents[1]
# The pipeline scripts are not a package; add their folders to the path the
# way the scripts themselves do.
sys.path.insert(0, str(BACKEND / "pipeline" / "lunaris"))
sys.path.insert(0, str(BACKEND / "pipeline" / "lunaris" / "llm"))
import geo_filter_run as gfr  # noqa: E402
import geo_prompt_v3  # noqa: E402
import geo_prompt_variants as gv  # noqa: E402
from prompt_v6 import prompt_sha1  # noqa: E402
from test_geo_filter_run import PASS1_ANSWER, PASS2_ANSWER, FakeLookup  # noqa: E402
from test_geo_pass2_places import RECORD, TOY_BC  # noqa: E402

VARIANTS = ["lunaris", "dataone"]
COUNT = 3
# The fingerprints of each variant's instructions, as the runs recorded them.
FINGERPRINTS = {
    "lunaris": ("aeb422116caceae01bf4aa2632a07f40c6fdd666", "25baa4472cdd216f8ac641112c6aa620201c16fb"),
    "dataone": ("18d4d73e0186276f6e69eea1bcbe93b65e1623fb", "6ac23fd3dd28a8b76307ffa99d9cbe71a4c3efbc"),
}


# --- A made-up input folder and model -------------------------------------------

def make_record(variant, number):
    """
    Return made-up record number in the shape its variant reads: a Lunaris
    harvest row, or a DataONE record as filter3_records.read_geo_records
    gives it. Both name Lake Fakeville, the place the made-up model finds.
    """
    if variant == "lunaris":
        return dict(RECORD, id=f"oai:example.org:{number}")
    return {"id": f"doi:10.0000/example.{number}", "repository": "PANGAEA", "title": "Trout in Lake Fakeville",
            "subjects": ["trout"], "abstract": "Counts from 2001.", "named_location": None, "place_key": None,
            "site": None, "boxes": [], "unreadable_boxes": 0}


def make_input(folder, variant, count=COUNT):
    """
    Write an input folder of count made-up records in a variant, as
    geo_filter_prepare.py (lunaris: no input.json) and
    prepare_filter3_input.py (dataone: input.json naming the variant) write one.
    """
    folder.mkdir(parents=True)
    rows, lines = [], []
    for number in range(count):
        record = make_record(variant, number)
        rows.append({"number": number, "record_id": record["id"], "source": "example", "title": record["title"],
                     "reason": "test", "text_mentions_bc": True, "evidence": ""})
        lines.append({"number": number, "record_id": record["id"], "record": record,
                      "pass1_sha1": prompt_sha1(gv.build_pass1_prompt(record, TOY_BC, variant))})
    pd.DataFrame(rows).to_csv(folder / "sample.csv", index=False)
    with gzip.open(folder / "records.jsonl.gz", "wt", encoding="utf-8") as handle:
        for line in lines:
            handle.write(json.dumps(line) + "\n")
    if variant != "lunaris":
        (folder / "input.json").write_text(json.dumps(gv.fingerprints(variant)), encoding="utf-8")
    return folder


class FakeModel:
    """
    A model that answers both passes, telling the record by the identifier
    line in the prompt. fail lists the (pass, record id) prompts it never
    answers. calls keeps every (pass, record id) asked.
    """

    def __init__(self, fail=()):
        """Keep the prompts to fail and start an empty list of calls."""
        self.fail, self.calls = set(fail), []

    def __call__(self, text, schema):
        """Answer one prompt, or fail with bad JSON as set up."""
        which = "pass1" if schema is geo_prompt_v3.RESPONSE_SCHEMA else "pass2"
        record_id = text.split("Record identifier: ")[1].split("\n")[0]
        self.calls.append((which, record_id))
        if (which, record_id) in self.fail:
            return {"error": "reply was not valid JSON: x"}
        answer = PASS1_ANSWER if which == "pass1" else PASS2_ANSWER
        return {"answer": json.loads(json.dumps(answer)), "input_tokens": 100}


def run_main(monkeypatch, tmp_path, model):
    """
    Run geo_filter_run.main() on the input folder in tmp_path with the
    made-up model, the made-up lookup and the toy BC outline, and nothing
    that needs Ollama or the GeoNames table. Return its exit code.
    """
    def fake_lookup(*args, **kwargs):
        """Return a made-up lookup with no hand-added entries missing."""
        lookup = FakeLookup()
        lookup.missing_hand_ids = []
        return lookup

    def fake_ask(url, model_name, text, num_ctx, timeout, num_thread, schema=None):
        """Send one prompt to the made-up model instead of Ollama."""
        return model(text, schema)

    monkeypatch.setattr(gfr, "wait_for_server", lambda url, seconds: "0.0-test")
    monkeypatch.setattr(gfr, "model_digest", lambda url, name: "f" * 64)
    monkeypatch.setattr(gfr, "load_bc_shape", lambda path: TOY_BC)
    monkeypatch.setattr(gfr.geonames_lookup, "GeoNamesLookup", fake_lookup)
    monkeypatch.setattr(gfr, "ask_ollama_checked", fake_ask)
    monkeypatch.setattr(sys, "argv", ["geo_filter_run.py", "--input", str(tmp_path / "input"),
                                      "--out", str(tmp_path / "out"), "--bc-boundary", "unused.wkt",
                                      "--model", "fake:1b", "--allow-cpu"])
    return gfr.main()


def run_of(tmp_path):
    """Return a Run on the run folder in tmp_path, as main() makes it, and its pass 1 prompts."""
    folder = tmp_path / "input"
    sample, records = gfr.read_input(str(folder))
    run = gfr.Run(str(tmp_path / "out"), sample, records, list(sample.index), TOY_BC, FakeLookup(), 0,
                  gfr.read_variant(str(folder)))
    return run, run.pass1_prompts()


def last_job(tmp_path):
    """Return the last line of the run's logs/jobs.jsonl."""
    lines = (tmp_path / "out" / "logs" / "jobs.jsonl").read_text(encoding="utf-8").splitlines()
    return json.loads(lines[-1])


def pass2_progress(tmp_path):
    """Return the path of the run's pass 2 progress file (one part)."""
    return tmp_path / "out" / "pass2" / "progress_part0.jsonl"


# --- A finished run -------------------------------------------------------------

@pytest.mark.parametrize("variant", VARIANTS)
def test_a_finished_run_has_nothing_left_and_exits_0(monkeypatch, tmp_path, variant):
    make_input(tmp_path / "input", variant)
    assert run_main(monkeypatch, tmp_path, FakeModel()) == 0
    assert last_job(tmp_path)["exit_code"] == 0
    run, prompts = run_of(tmp_path)
    assert run.variant == variant
    assert run.todo(prompts) == ([], [], [])
    # A second run finds everything done and asks nothing.
    again = FakeModel()
    assert run_main(monkeypatch, tmp_path, again) == 0
    assert again.calls == []


def test_the_dataone_test_would_catch_the_old_check(tmp_path):
    # In a DataONE run the saved pass 2 fingerprints are those of the dataone
    # prompts, never of the Lunaris ones, so a check in the wrong variant
    # (the old bug) would find every record unasked.
    make_input(tmp_path / "input", "dataone")
    run, prompts = run_of(tmp_path)
    run.run(FakeModel(), lambda: None, prompts, {})
    pass1, lookups, pass2 = run.state()
    for number in run.numbers:
        record, places = run.record(number), gfr.pass1_places(pass1[number])
        results = lookups[number]["results"]
        assert pass2[number]["prompt_sha1"] == prompt_sha1(gfr.build_pass2(record, TOY_BC, places, results,
                                                                           "dataone"))
        assert pass2[number]["prompt_sha1"] != prompt_sha1(gfr.build_pass2(record, TOY_BC, places, results,
                                                                           "lunaris"))


# --- A record without its pass 2 answer -----------------------------------------

@pytest.mark.parametrize("variant", VARIANTS)
def test_a_record_whose_pass2_fails_is_the_only_one_left_and_exits_4(monkeypatch, tmp_path, variant):
    make_input(tmp_path / "input", variant)
    failing = make_record(variant, 1)["id"]
    assert run_main(monkeypatch, tmp_path, FakeModel(fail=[("pass2", failing)])) == 4
    assert last_job(tmp_path)["exit_code"] == 4
    run, prompts = run_of(tmp_path)
    assert run.todo(prompts) == ([], [], [1])


@pytest.mark.parametrize("variant", VARIANTS)
def test_a_missing_pass2_answer_is_the_only_one_asked_again(monkeypatch, tmp_path, variant):
    make_input(tmp_path / "input", variant)
    assert run_main(monkeypatch, tmp_path, FakeModel()) == 0
    # Take record 1's pass 2 answer out of the progress file.
    path = pass2_progress(tmp_path)
    kept = [line for line in path.read_text(encoding="utf-8").splitlines() if json.loads(line)["number"] != 1]
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    run, prompts = run_of(tmp_path)
    assert run.todo(prompts) == ([], [], [1])
    # While its pass 2 keeps failing, the run ends with exit 4 and asks only that record.
    failing = make_record(variant, 1)["id"]
    still = FakeModel(fail=[("pass2", failing)])
    assert run_main(monkeypatch, tmp_path, still) == 4
    assert set(still.calls) == {("pass2", failing)}
    # Once it is answered, nothing is left and the run ends with exit 0.
    later = FakeModel()
    assert run_main(monkeypatch, tmp_path, later) == 0
    assert later.calls == [("pass2", failing)]
    assert run.todo(prompts) == ([], [], [])


# --- The builders and the fingerprints --------------------------------------------

def test_the_prompt_builders_need_a_variant():
    places = PASS1_ANSWER["places"]
    lookups = [FakeLookup().lookup("Lake Fakeville", "Lake Fakeville")]
    with pytest.raises(TypeError):
        gfr.build_pass2(RECORD, TOY_BC, places, lookups)
    with pytest.raises(TypeError):
        gv.build_pass1_prompt(RECORD, TOY_BC)
    with pytest.raises(TypeError):
        gv.build_pass2_prompt(RECORD, TOY_BC, places, lookups)
    with pytest.raises(ValueError):
        gfr.build_pass2(RECORD, TOY_BC, places, lookups, "canadian")


@pytest.mark.parametrize("variant", VARIANTS)
def test_the_instructions_keep_their_fingerprints(variant):
    pass1, pass2 = FINGERPRINTS[variant]
    assert gv.fingerprints(variant) == {"geo_prompt_variant": variant, "pass1_instructions_sha1": pass1,
                                        "pass2_instructions_sha1": pass2}
    assert prompt_sha1(gv.pass1_instructions(variant)) == pass1
    assert prompt_sha1(gv.pass2_instructions(variant)) == pass2
