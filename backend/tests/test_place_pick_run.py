"""Tests for the place lookup's model run (pipeline/lunaris/llm/place_pick_run.py)
and its input (place_pick_prepare.py), with a fake model: every prompt is
checked against the Mac's fingerprint before anything is asked, each record
is asked in both orders, an answer that does not fit is asked once more, a
rerun asks nothing again, answers are read back to GeoNames ids, the merged
files wait for every part, and repeated server errors stop the run. The
report works when no answer, or only some, choose a place (the first Fir
test stopped there), a test list of dataset ids picks the records, and a
failure while writing the merged files still leaves the job's line."""

import gzip
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "pipeline" / "lunaris" / "llm"))
import place_pick_prepare as prep  # noqa: E402
import place_pick_run as ppr  # noqa: E402
from test_place_pick_prompt import ITEM  # noqa: E402


def make_input(folder, count=2):
    """Write an input folder of count made-up records, as place_pick_prepare.py does."""
    folder.mkdir(parents=True)
    lines = [prep.prepared(n, dict(ITEM, dataset_id=f"oai:x:{n}")) for n in range(count)]
    with gzip.open(folder / "items.jsonl.gz", "wt", encoding="utf-8") as handle:
        for line in lines:
            handle.write(json.dumps(line) + "\n")
    pd.DataFrame([{"number": l["number"], "dataset_id": l["dataset_id"]} for l in lines]).to_csv(
        folder / "sample.csv", index=False)
    return str(folder)


class FakeModel:
    """Answers every prompt by choosing the place whose latitude is 51.5703, or fails as told."""

    def __init__(self, fail=0, error="reply was not valid JSON"):
        """fail: how many first calls fail with error."""
        self.calls, self.fail, self.error = 0, fail, error

    def __call__(self, text):
        """Answer one prompt."""
        self.calls += 1
        if self.calls <= self.fail:
            return {"error": self.error}
        lines = [l for l in text.splitlines() if l.startswith("  ") and ". Susan Lake" in l]
        number = next(int(l.split(".")[0]) for l in lines if "51.5703" in l)
        return {"answer": {"places": [{"place": "P1", "reason": "Columbia Shuswap", "quote": "CSISS",
                                       "candidate": number, "none_because": ""}]}}


def never_mind():
    """A GPU check that always passes."""


def test_a_full_run_asks_both_orders_and_reads_the_same_place_back(tmp_path):
    records = ppr.read_input(make_input(tmp_path / "in"))
    run = ppr.Run(str(tmp_path / "out"), records, 0, 1)
    built = run.prompts()
    assert sorted(built) == ["0000_lookup_0", "0000_shuffled_0", "0001_lookup_0", "0001_shuffled_0"]
    model = FakeModel()
    run.run(model, never_mind, built, {"model": "fake"})
    assert model.calls == 4 and run.finish()
    answers = [json.loads(l) for l in (tmp_path / "out" / "answers.jsonl").read_text().splitlines()]
    assert {(a["order"], a["geonameid"]) for a in answers} == {("lookup", 2), ("shuffled", 2)}
    report = (tmp_path / "out" / "report.txt").read_text()
    assert "the same place (or both none) for 2" in report
    # A rerun asks nothing again.
    again = FakeModel()
    run.run(again, never_mind, run.prompts(), {})
    assert again.calls == 0


def test_a_prompt_unlike_the_macs_stops_before_asking(tmp_path):
    records = ppr.read_input(make_input(tmp_path / "in"))
    records[1]["units"][0]["sha1"] = "0" * 12
    with pytest.raises(ppr.Stop, match="differ from the ones made on the Mac"):
        ppr.Run(str(tmp_path / "out"), records, 0, 1).prompts()


def test_a_bad_answer_is_asked_once_more_and_a_second_failure_is_logged(tmp_path):
    records = ppr.read_input(make_input(tmp_path / "in", count=1))
    run = ppr.Run(str(tmp_path / "out"), records, 0, 1)
    built = run.prompts()
    run.run(FakeModel(fail=1), never_mind, built, {})
    assert run.todo(built) == []
    run2 = ppr.Run(str(tmp_path / "out2"), records, 0, 1)
    run2.run(FakeModel(fail=2), never_mind, run2.prompts(), {})
    assert run2.todo(run2.prompts()) == ["0000_lookup_0"]
    assert "0000_lookup_0" in (tmp_path / "out2" / "logs" / "failures_part0.txt").read_text()


def test_repeated_server_errors_stop_the_run(tmp_path):
    records = ppr.read_input(make_input(tmp_path / "in", count=4))
    run = ppr.Run(str(tmp_path / "out"), records, 0, 1)
    with pytest.raises(ppr.Stop, match="in a row"):
        run.run(FakeModel(fail=99, error="gave up after 2 attempts: timed out"), never_mind, run.prompts(), {})


def test_parts_split_the_records_and_the_merged_files_wait_for_every_part(tmp_path):
    records = ppr.read_input(make_input(tmp_path / "in", count=3))
    first = ppr.Run(str(tmp_path / "out"), records, 0, 2)
    assert [r["number"] for r in first.mine] == [0, 2]
    first.run(FakeModel(), never_mind, first.prompts(), {})
    assert not first.finish()
    second = ppr.Run(str(tmp_path / "out"), records, 1, 2)
    second.run(FakeModel(), never_mind, second.prompts(), {})
    assert second.finish()


def test_input_files_must_agree(tmp_path):
    folder = make_input(tmp_path / "in")
    pd.DataFrame([{"number": 0, "dataset_id": "other"}]).to_csv(Path(folder) / "sample.csv", index=False)
    with pytest.raises(ppr.Stop, match="do not list the same records"):
        ppr.read_input(folder)


class ScriptedModel:
    """Answers the prompts in the order asked: True chooses the place at latitude 51.5703, False answers none."""

    def __init__(self, picks):
        """picks: one True or False per prompt, in the order they will be asked."""
        self.picks, self.calls = list(picks), 0

    def __call__(self, text):
        """Answer one prompt."""
        pick = self.picks[self.calls]
        self.calls += 1
        if not pick:
            return {"answer": {"places": [{"place": "P1", "reason": "A habitat word.", "quote": "Lake",
                                           "candidate": 0, "none_because": "not_a_place"}]}}
        return FakeModel()(text)


def run_scripted(tmp_path, picks, count):
    """Run count made-up records with a scripted model and return the answers and the report."""
    records = ppr.read_input(make_input(tmp_path / "in", count=count))
    run = ppr.Run(str(tmp_path / "out"), records, 0, 1)
    run.run(ScriptedModel(picks), never_mind, run.prompts(), {})
    assert run.finish()
    answers = [json.loads(l) for l in (tmp_path / "out" / "answers.jsonl").read_text().splitlines()]
    return answers, (tmp_path / "out" / "report.txt").read_text()


def test_the_report_works_when_no_answer_chooses_a_place(tmp_path):
    """Every answer none, in both orders (as in the first Fir test: Indigenous, Treeline)."""
    answers, report = run_scripted(tmp_path, [False] * 4, count=2)
    assert {a["geonameid"] for a in answers} == {None} and len(answers) == 4
    assert "lookup: 2 names, a place chosen for 0; none because: {'not_a_place': 2}" in report
    assert ("both orders answered for 2 names; the same place (or both none) for 2 (the same place 0, both none 2), "
            "different for 0") in report


def test_the_report_works_when_some_answers_choose_a_place(tmp_path):
    """Record 0: the same place in both orders; record 1: a place, then none; record 2: none in both."""
    answers, report = run_scripted(tmp_path, [True, True, True, False, False, False], count=3)
    assert [a["geonameid"] for a in answers] == [2, 2, 2, None, None, None]
    assert "lookup: 3 names, a place chosen for 2" in report
    assert "shuffled: 3 names, a place chosen for 1" in report
    assert "the same place (or both none) for 2 (the same place 1, both none 1), different for 1" in report


def test_the_report_counts_a_different_place_as_different(tmp_path):
    """Two answers that choose different places do not agree."""
    lines = ppr.report_lines([{"dataset_id": "a", "name": "X", "order": "lookup", "candidate_number": 1,
                               "geonameid": 10, "none_because": None},
                              {"dataset_id": "a", "name": "X", "order": "shuffled", "candidate_number": 1,
                               "geonameid": 11, "none_because": None}], {}, [])
    assert any("the same place 0, both none 0), different for 1" in line for line in lines)


def test_a_test_list_picks_the_records_by_dataset_id(tmp_path):
    """Only the listed records are read, in number order and with their own numbers; comments are skipped."""
    folder = make_input(tmp_path / "in", count=4)
    listed = tmp_path / "test.txt"
    listed.write_text("# hard cases\noai:x:3\n\noai:x:1\n", encoding="utf-8")
    records = ppr.read_input(folder, only_ids=ppr.read_ids(str(listed)))
    assert [(r["number"], r["dataset_id"]) for r in records] == [(1, "oai:x:1"), (3, "oai:x:3")]
    assert ppr.Run(str(tmp_path / "out"), records, 0, 1).prompts().keys() == {
        "0001_lookup_0", "0001_shuffled_0", "0003_lookup_0", "0003_shuffled_0"}
    with pytest.raises(ppr.Stop, match="not in"):
        ppr.read_input(folder, only_ids=["oai:x:1", "oai:x:9"])
    with pytest.raises(ppr.Stop, match="no dataset ids"):
        ppr.read_input(folder, only_ids=[])


def fake_server(monkeypatch, model):
    """Replace the Ollama calls main makes with a fake server that answers with model."""
    monkeypatch.setattr(ppr, "wait_for_server", lambda url, seconds: "0.0-test")
    monkeypatch.setattr(ppr, "model_digest", lambda url, name: "d" * 64)
    monkeypatch.setattr(ppr, "ask_ollama", lambda url, name, text, **options: model(text))


def test_main_runs_a_test_list_and_refuses_it_with_a_limit(tmp_path, monkeypatch):
    """main with --only-ids asks only the listed records and writes the merged files; with --limit too it refuses."""
    folder = make_input(tmp_path / "in", count=3)
    listed = tmp_path / "test.txt"
    listed.write_text("oai:x:2\n", encoding="utf-8")
    model = FakeModel()
    fake_server(monkeypatch, model)
    out = tmp_path / "out"
    base = ["place_pick_run.py", "--input", folder, "--out", str(out), "--model", "fake", "--allow-cpu"]
    monkeypatch.setattr(sys, "argv", base + ["--only-ids", str(listed)])
    assert ppr.main() == 0 and model.calls == 2
    assert {json.loads(l)["dataset_id"] for l in (out / "answers.jsonl").read_text().splitlines()} == {"oai:x:2"}
    assert json.loads((out / "run.json").read_text())["only_ids"] == str(listed)
    monkeypatch.setattr(sys, "argv", base + ["--only-ids", str(listed), "--limit", "2"])
    assert ppr.main() == 1


def test_a_failure_writing_the_merged_files_still_leaves_the_jobs_line(tmp_path, monkeypatch):
    """If the report cannot be written, the job ends with an error and logs/jobs.jsonl says exit 1 and why."""
    folder = make_input(tmp_path / "in", count=1)
    fake_server(monkeypatch, FakeModel())

    def broken(decisions, done, names):
        """A report that cannot be written."""
        raise KeyError("lookup")

    monkeypatch.setattr(ppr, "report_lines", broken)
    out = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", ["place_pick_run.py", "--input", folder, "--out", str(out), "--model", "fake",
                                      "--allow-cpu"])
    with pytest.raises(KeyError):
        ppr.main()
    job = json.loads((out / "logs" / "jobs.jsonl").read_text().splitlines()[-1])
    assert job["exit_code"] == 1 and job["reason"].startswith("writing the merged files: KeyError")
    assert (out / "answers.jsonl").exists()
