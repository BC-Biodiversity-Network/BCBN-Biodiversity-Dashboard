"""Tests that a prompt Ollama cut is recorded as context_full even when the
model's reply is broken JSON, and is not asked again at once.

What happened (DataONE filter 3 label test, record [127], 2026-11-23): a
36,078-token prompt was cut by Ollama 0.40 to 8,194 tokens; the model then
wrote until the context was full and its JSON was unfinished. ask_ollama
returned "reply was not valid JSON" without the token count, so
run_batch.ask_ollama_checked could not see the cut, the record was saved
as bad_json, and geo_filter_run.ask_with_retry asked it a second time
(about 40 seconds each).

Now ask_ollama keeps the token counts on a JSON error, ask_ollama_checked
checks for a cut on errors as on answers, and ask_with_retry does not ask a
context_full prompt again. The four cases:
  - cut prompt, broken JSON: context_full, asked once;
  - cut prompt, good JSON: context_full, as before (now also asked once);
  - prompt not cut, broken JSON: bad_json, asked twice, as before;
  - prompt not cut, good JSON: unchanged.

The replies are fakes: urllib's urlopen is replaced, so no server is asked."""

import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
LLM = BACKEND / "pipeline" / "lunaris" / "llm"
sys.path.insert(0, str(LLM))
sys.path.insert(0, str(LLM.parent))
import geo_filter_run  # noqa: E402
import geo_pass1_trial  # noqa: E402
import geo_prompt_v3  # noqa: E402
import ollama_client  # noqa: E402
import run_batch  # noqa: E402
from run_files import error_kind  # noqa: E402
from test_ollama_cut_prompts import ANSWER as V6_ANSWER, FakeResponse  # noqa: E402

# A pass 1 answer naming a place that is in the prompt text below.
PASS1_ANSWER = {"places": [{"as_written": "Examplia", "name": "Examplia", "role": "study_location"}]}
PROMPT = "Record to read: Fish of Examplia."
# What a model writes when it runs out of room: JSON that stops part way.
BROKEN = '{"places": [{"as_written": "Exam'
CUT = 8194          # what Ollama 0.40 cuts a too-long prompt to at num_ctx 16,384
NOT_CUT = 4000


def fake_server(monkeypatch, content, input_tokens, output_tokens=16383):
    """
    Make every Ollama call reply with the given message content and token
    counts (None leaves a count out), and return the list of calls made,
    which grows by one for each request.
    """
    calls = []
    reply = {"message": {"content": content}, "prompt_eval_count": input_tokens}
    if output_tokens is not None:
        reply["eval_count"] = output_tokens

    def urlopen(request, timeout=None):
        """Count the request and return the fake reply."""
        calls.append(json.loads(request.data))
        return FakeResponse(reply)

    monkeypatch.setattr(ollama_client.urllib.request, "urlopen", urlopen)
    return calls


def ask_checked(num_ctx=16384, schema=geo_prompt_v3.RESPONSE_SCHEMA):
    """Ask the fake server once through the cut check, as geo_filter_run's ask does."""
    return run_batch.ask_ollama_checked("http://127.0.0.1:11434", "qwen3.6:35b-a3b", PROMPT, num_ctx, 30, 6,
                                        schema=schema)


def ask_pass1_with_retry(num_ctx=16384):
    """Ask one pass 1 prompt the way geo_filter_run.py does: through the check, the retry and pass 1's checks."""
    return geo_filter_run.ask_with_retry(lambda text: ask_checked(num_ctx), PROMPT,
                                         lambda r: geo_pass1_trial.finish_answer(r, PROMPT))


def cut_error(tokens=CUT, num_ctx=16384):
    """Return the context_full error a cut prompt gives, as ask_ollama_checked writes it."""
    return {"error": f"the prompt filled the whole context: Ollama cut it to {tokens} tokens (half of "
                     f"{num_ctx}) and dropped its start, raise --num-ctx", "input_tokens": tokens}


# --- A cut prompt -----------------------------------------------------------------

def test_a_cut_prompt_with_broken_json_is_context_full(monkeypatch):
    fake_server(monkeypatch, BROKEN, CUT)
    assert ask_checked() == cut_error()


def test_a_cut_prompt_with_broken_json_is_asked_only_once(monkeypatch):
    calls = fake_server(monkeypatch, BROKEN, CUT)
    result = ask_pass1_with_retry()
    assert len(calls) == 1
    assert result["attempts"] == 1 and "first_error" not in result
    assert error_kind(result["error"]) == "context_full" and result["input_tokens"] == CUT


def test_a_cut_prompt_with_good_json_is_still_context_full(monkeypatch):
    fake_server(monkeypatch, json.dumps(PASS1_ANSWER), CUT)
    assert ask_checked() == cut_error()
    # Whatever the JSON looked like, a cut prompt gives the same error.
    fake_server(monkeypatch, BROKEN, CUT)
    assert ask_checked() == cut_error()
    # Through the retry it is now asked once too.
    calls = fake_server(monkeypatch, json.dumps(PASS1_ANSWER), CUT)
    result = ask_pass1_with_retry()
    assert len(calls) == 1 and result["attempts"] == 1 and error_kind(result["error"]) == "context_full"


def test_ollamas_own_full_context_check_is_not_asked_again_either(monkeypatch):
    # ask_ollama's older check (a prompt within 16 tokens of num_ctx) is also context_full.
    calls = fake_server(monkeypatch, json.dumps(PASS1_ANSWER), 16380)
    result = ask_pass1_with_retry()
    assert len(calls) == 1 and result["attempts"] == 1 and error_kind(result["error"]) == "context_full"


def test_the_cut_size_is_judged_at_the_runs_own_context(monkeypatch):
    # At 65536, 8,194 tokens is an ordinary length: broken JSON there is bad JSON.
    fake_server(monkeypatch, BROKEN, CUT)
    assert error_kind(ask_checked(65536)["error"]) == "bad_json"
    fake_server(monkeypatch, BROKEN, 32770)
    assert ask_checked(65536) == cut_error(32770, 65536)


# --- A prompt that was not cut ---------------------------------------------------

def test_broken_json_from_a_whole_prompt_is_bad_json_and_keeps_its_counts(monkeypatch):
    fake_server(monkeypatch, BROKEN, NOT_CUT, output_tokens=120)
    result = ask_checked()
    assert error_kind(result["error"]) == "bad_json"
    assert set(result) == {"error", "input_tokens", "output_tokens"}
    assert result["input_tokens"] == NOT_CUT and result["output_tokens"] == 120
    # A count Ollama does not give is left out, not written as None.
    fake_server(monkeypatch, BROKEN, NOT_CUT, output_tokens=None)
    assert set(ask_checked()) == {"error", "input_tokens"}


def test_broken_json_from_a_whole_prompt_is_asked_again_as_before(monkeypatch):
    calls = fake_server(monkeypatch, BROKEN, NOT_CUT)
    result = ask_pass1_with_retry()
    assert len(calls) == 2 and result["attempts"] == 2
    assert error_kind(result["error"]) == "bad_json" and error_kind(result["first_error"]) == "bad_json"


def test_a_good_answer_to_a_whole_prompt_is_unchanged(monkeypatch):
    calls = fake_server(monkeypatch, json.dumps(PASS1_ANSWER), NOT_CUT, output_tokens=40)
    result = ask_checked()
    # Exactly what ask_ollama itself returns, with the same fields as before.
    assert result == ollama_client.ask_ollama("http://127.0.0.1:11434", "qwen3.6:35b-a3b", PROMPT, num_ctx=16384,
                                              timeout=30, num_thread=6, schema=geo_prompt_v3.RESPONSE_SCHEMA)
    assert set(result) == {"answer", "input_tokens", "output_tokens", "thinking_tokens", "timings"}
    assert result["answer"] == PASS1_ANSWER and result["input_tokens"] == NOT_CUT
    calls.clear()
    kept = ask_pass1_with_retry()
    assert len(calls) == 1 and kept["attempts"] == 1 and kept["answer"] == PASS1_ANSWER


@pytest.mark.parametrize("tokens", [8193, 8195])
def test_filter_2_v6_answers_that_were_not_cut_are_unchanged(monkeypatch, tokens):
    # run_batch.py's default ask (prompt v6's schema): same result as ask_ollama.
    fake_server(monkeypatch, json.dumps(V6_ANSWER), tokens, output_tokens=120)
    result = run_batch.ask_ollama_checked("http://127.0.0.1:11434", "qwen3.5:4b", "a prompt", 16384, 30, None)
    assert result == ollama_client.ask_ollama("http://127.0.0.1:11434", "qwen3.5:4b", "a prompt", num_ctx=16384,
                                              timeout=30)
    assert result["answer"] == V6_ANSWER


def test_filter_2_v6_cut_broken_json_is_filed_as_context_full(monkeypatch):
    # run_batch.py files an error by its kind; a cut prompt is now context_full there too.
    fake_server(monkeypatch, BROKEN, CUT)
    result = run_batch.ask_ollama_checked("http://127.0.0.1:11434", "qwen3.5:4b", "a prompt", 16384, 30, None)
    assert error_kind(result["error"]) == "context_full"
