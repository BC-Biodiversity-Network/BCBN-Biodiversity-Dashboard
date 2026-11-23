"""Tests for two fixes made before the DataONE filter 3 work (2026-11-23).

1. pipeline/lunaris/bc_filter_rules.py: a box with west 180 and east -180
   used to crash measure_box (division by zero), because read as crossing
   the 180 degree line both halves had no width. Such a box is now read as
   going all the way round (-180 to 180). Other boxes are measured as
   before, and the saved Lunaris rule results rebuild unchanged (when the
   saved files are on this machine).
2. pipeline/lunaris/llm/geo_filter_run.py: both passes now ask through
   run_batch.ask_ollama_checked, so an answer whose prompt Ollama cut
   silently (input_tokens == num_ctx // 2 + 2) is a context_full error, not
   an answer. Replies here are fakes: urllib's urlopen is replaced, so no
   server is asked.
"""

import inspect
import io
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

BACKEND = Path(__file__).resolve().parents[1]
LUNARIS = BACKEND / "pipeline" / "lunaris"
sys.path.insert(0, str(LUNARIS))
sys.path.insert(0, str(LUNARIS / "llm"))
import bc_filter_rules as rules_module  # noqa: E402
import geo_filter_run  # noqa: E402
import geo_pass1_trial  # noqa: E402
import geo_pass2_prompt_c4  # noqa: E402
import geo_prompt_v3  # noqa: E402
import ollama_client  # noqa: E402
import run_batch  # noqa: E402
from run_files import error_kind  # noqa: E402

BOUNDARY = Path.home() / "Desktop" / "BCBN" / "Results" / "bc_boundary_gbif.wkt"
HARVEST = BACKEND / "exploration" / "lunaris" / "harvest_2026_10_20" / "lunaris_full_harvest.parquet"
SAVED_RULES = [BACKEND / "data" / "lunaris_bc_filter" / "rules_2026_10_v2.csv",
               BACKEND / "data" / "lunaris_llm_runs" / "filter2_35b_compare" / "rules_newly_kept_35b.csv"]

needs_boundary = pytest.mark.skipif(not BOUNDARY.exists(), reason="bc_boundary_gbif.wkt is not on this machine")


@pytest.fixture(scope="module")
def rules():
    """The BC filter's rules with BC's real outline."""
    return rules_module.load_rules(str(BOUNDARY))


def box(w, e, s, n):
    """Return a box as the harvest stores it."""
    return {"w": w, "e": e, "s": s, "n": n}


# --- 1. The box that goes all the way round -----------------------------------

def test_a_180_to_minus_180_box_is_read_as_going_all_the_way_round():
    pieces = rules_module.repair_box(180.0, 51.5, -180.0, 66.5)
    assert len(pieces) == 1 and pieces[0].bounds == (-180.0, 51.5, 180.0, 66.5)


def test_other_west_greater_than_east_boxes_are_repaired_as_before():
    crossing = rules_module.repair_box(170.0, 50.0, -170.0, 60.0)
    assert [p.bounds for p in crossing] == [(170.0, 50.0, 180.0, 60.0), (-180.0, 50.0, -170.0, 60.0)]
    swapped = rules_module.repair_box(-120.0, 50.0, -125.0, 55.0)
    assert [p.bounds for p in swapped] == [(-125.0, 50.0, -120.0, 55.0)]
    assert rules_module.repair_box(180.0, 50.0, 180.0, 60.0) == []


@needs_boundary
@pytest.mark.parametrize("s,n,kind,bc_class", [
    (-90.0, 90.0, "huge", "needs_llm"),    # worldwide
    (90.0, -90.0, "huge", "needs_llm"),    # worldwide, south and north swapped
    (90.0, 0.0, "huge", "needs_llm"),      # the northern half
    (51.5, 66.5, "huge", "needs_llm"),     # a band of latitude through BC
    (-90.0, 0.0, "outside", "not_bc"),     # the southern half
])
def test_the_nine_dataone_boxes_no_longer_crash(rules, s, n, kind, bc_class):
    b = box(180.0, -180.0, s, n)
    share, cover, size = rules_module.measure_box(b, rules.shape)
    # Area sums round: a box covering all of BC can measure a hair over 1.
    assert 0 <= share <= 1 + 1e-9 and 0 <= cover <= 1 + 1e-9 and size > 0
    assert rules_module.classify_box(b, rules.shape)[0] == kind
    assert rules_module.classify_record({"id": "x", "boxes": [b], "points": None}, rules)["bc_class"] == bc_class


@needs_boundary
def test_a_box_whose_projected_area_is_zero_counts_as_no_area(rules, monkeypatch):
    # A stand-in repair that returns a rectangle with no width: measure_box
    # must say "no area" (None) instead of dividing by zero.
    monkeypatch.setattr(rules_module, "repair_box", lambda w, s, e, n: [rules_module.make_box(10.0, 0.0, 10.0, 5.0)])
    assert rules_module.measure_box(box(10.0, 10.0, 0.0, 5.0), rules.shape) is None


@needs_boundary
@pytest.mark.parametrize("saved", SAVED_RULES, ids=lambda p: p.name)
def test_the_saved_lunaris_rule_results_rebuild_unchanged(rules, saved):
    if not saved.exists() or not HARVEST.exists():
        pytest.skip(f"{saved.name} or the Lunaris harvest is not on this machine")
    from build_bc_filter_rules import read_records
    old = pd.read_csv(saved, dtype=str, keep_default_na=False).sort_values("record_id").reset_index(drop=True)
    rows = []
    for record in read_records(str(HARVEST), set(old["record_id"])).to_dict("records"):
        row = rules_module.classify_record(record, rules)
        row["title"] = record["title"]
        rows.append(row)
    buffer = io.StringIO()
    pd.DataFrame(rows)[list(old.columns)].to_csv(buffer, index=False)
    buffer.seek(0)
    new = pd.read_csv(buffer, dtype=str, keep_default_na=False).sort_values("record_id").reset_index(drop=True)
    assert list(new["record_id"]) == list(old["record_id"])
    assert not (new != old).any(axis=1).any()


# --- 2. The cut check in the BC filter's two passes ----------------------------

class FakeResponse:
    """Stand-in for the HTTP response urlopen returns: a JSON body, usable in a with block."""

    def __init__(self, body):
        """Keep the reply body as bytes."""
        self.body = json.dumps(body).encode("utf-8")

    def read(self):
        """Return the reply body."""
        return self.body

    def __enter__(self):
        """Return itself, as a real response does."""
        return self

    def __exit__(self, *exc):
        """Nothing to close."""
        return False


def fake_server(monkeypatch, answer, input_tokens):
    """Make every Ollama call answer `answer` with prompt_eval_count input_tokens; return the request bodies sent."""
    sent = []

    def urlopen(request, timeout=None):
        """Keep the request body and reply with the fake answer."""
        sent.append(json.loads(request.data.decode("utf-8")))
        return FakeResponse({"message": {"content": json.dumps(answer)}, "prompt_eval_count": input_tokens,
                             "eval_count": 50})

    monkeypatch.setattr(ollama_client.urllib.request, "urlopen", urlopen)
    return sent


PASS1_ANSWER = {"places": [{"as_written": "Examplia", "name": "Examplia", "role": "study_location"}]}
PASS2_ANSWER = {"reason": "Collected in BC.", "evidence": [], "in_bc": "yes", "other_canada": "no",
                "outside_canada": "no", "canada_wide": "no", "global": "no"}


def ask(schema, num_ctx=16384):
    """Ask the fake server once, as geo_filter_run's ask does."""
    return run_batch.ask_ollama_checked("http://127.0.0.1:11434", "qwen3.6:35b-a3b", "a prompt", num_ctx, 30, 6,
                                        schema=schema)


@pytest.mark.parametrize("schema,answer", [(geo_prompt_v3.RESPONSE_SCHEMA, PASS1_ANSWER),
                                           (geo_pass2_prompt_c4.RESPONSE_SCHEMA, PASS2_ANSWER)],
                         ids=["pass 1", "pass 2"])
def test_a_cut_answer_in_either_pass_is_context_full(monkeypatch, schema, answer):
    sent = fake_server(monkeypatch, answer, 8194)
    result = ask(schema)
    assert "answer" not in result and error_kind(result["error"]) == "context_full"
    assert result["input_tokens"] == 8194
    # The pass's own schema was sent, not prompt v6's.
    assert sent[0]["format"] == ollama_client.plain_json_schema(schema)


@pytest.mark.parametrize("tokens", [8193, 8195])
def test_answers_one_token_either_side_are_kept(monkeypatch, tokens):
    fake_server(monkeypatch, PASS2_ANSWER, tokens)
    result = ask(geo_pass2_prompt_c4.RESPONSE_SCHEMA)
    assert result["answer"] == PASS2_ANSWER and result["input_tokens"] == tokens


def test_a_cut_pass_1_answer_is_tried_once_more_and_saved_as_an_error(monkeypatch):
    fake_server(monkeypatch, PASS1_ANSWER, 8194)
    result = geo_filter_run.ask_with_retry(lambda text: ask(geo_prompt_v3.RESPONSE_SCHEMA), "a prompt",
                                           lambda r: geo_pass1_trial.finish_answer(r, "Examplia"))
    assert result["attempts"] == 2 and error_kind(result["error"]) == "context_full"
    assert "places" not in result


def test_geo_filter_run_asks_through_the_check():
    source = inspect.getsource(geo_filter_run.main)
    assert "ask_ollama_checked(" in source and "ask_ollama(" not in source.replace("ask_ollama_checked(", "")


def test_run_batch_still_sends_prompt_v6s_schema_by_default(monkeypatch):
    sent = fake_server(monkeypatch, {"x": 1}, 4000)
    run_batch.ask_ollama_checked("http://127.0.0.1:11434", "qwen3.5:4b", "a prompt", 16384, 30, None)
    assert sent[0] == ollama_client.chat_request_body("qwen3.5:4b", "a prompt", 16384, None)
