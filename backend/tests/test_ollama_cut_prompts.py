"""Tests for run_batch.py catching prompts Ollama cut silently.

Ollama 0.40 cuts a prompt longer than the context to num_ctx // 2 + 2
tokens (8,194 at 16,384) instead of refusing it. run_batch.py now turns such
an answer into a context_full error, so the NUM_CTX=65536 second pass asks
it again. Nothing else changes: an answer of any other length is kept as it
was, and answers already in a results file are read as before (old run
folders keep theirs).

The replies here are fakes: urllib's urlopen is replaced, so no server is
asked."""

import inspect
import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
LLM = BACKEND / "pipeline" / "lunaris" / "llm"
sys.path.insert(0, str(LLM))
import ollama_client  # noqa: E402
import run_batch  # noqa: E402
from prompt_v6 import PROMPT_VERSION  # noqa: E402
from run_files import error_kind, problem_with_result  # noqa: E402

# An answer that fits prompt v6's schema.
ANSWER = {"reason": "Counts of nesting eagles.", "concerns_living_things": True, "organisms": ["eagle"],
          "organism_level": "species", "topic": "organisms", "about_organisms_themselves": "not_applicable",
          "ecological_purpose_stated": "not_applicable", "experimental_animals": "no", "extinct_only": "no",
          "form": "dataset", "language": "english"}


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


def fake_server(monkeypatch, input_tokens):
    """Make every Ollama call answer ANSWER with Ollama's prompt_eval_count set to input_tokens."""
    reply = {"message": {"content": json.dumps(ANSWER)}, "prompt_eval_count": input_tokens, "eval_count": 120}
    monkeypatch.setattr(ollama_client.urllib.request, "urlopen", lambda request, timeout=None: FakeResponse(reply))


def ask(num_ctx=16384):
    """Ask the fake server one prompt the way run_one_batch does."""
    return run_batch.ask_ollama_checked("http://127.0.0.1:11434", "qwen3.5:4b", "a prompt", num_ctx, 30, None)


def test_the_cut_size_is_half_the_context_plus_two():
    assert run_batch.cut_by_ollama(8194, 16384)
    assert run_batch.cut_by_ollama(32770, 65536)
    for tokens in (8193, 8195, 4515, 16266, None):
        assert not run_batch.cut_by_ollama(tokens, 16384)
    # At 65536 the old cut size is an ordinary length.
    assert not run_batch.cut_by_ollama(8194, 65536)


def test_a_reply_of_8194_tokens_at_16384_is_context_full(monkeypatch):
    fake_server(monkeypatch, 8194)
    result = ask()
    assert "answer" not in result
    assert error_kind(result["error"]) == "context_full"
    assert "8194" in result["error"] and "16384" in result["error"]


@pytest.mark.parametrize("tokens", [8193, 8195])
def test_replies_one_token_either_side_are_kept(monkeypatch, tokens):
    fake_server(monkeypatch, tokens)
    result = ask()
    assert result["answer"] == ANSWER and result["input_tokens"] == tokens
    # Exactly what ask_ollama itself returns.
    assert result == ollama_client.ask_ollama("http://127.0.0.1:11434", "qwen3.5:4b", "a prompt", num_ctx=16384,
                                              timeout=30)


def test_the_old_check_still_works(monkeypatch):
    fake_server(monkeypatch, 16380)
    assert error_kind(ask()["error"]) == "context_full"


def test_the_second_pass_context_is_checked_at_its_own_size(monkeypatch):
    fake_server(monkeypatch, 32770)
    assert error_kind(ask(65536)["error"]) == "context_full"
    fake_server(monkeypatch, 8194)
    assert ask(65536)["answer"] == ANSWER


def test_run_one_batch_asks_ollama_through_the_check():
    source = inspect.getsource(run_batch.run_one_batch)
    assert "ask_ollama_checked(" in source
    assert "return ask_ollama(" not in source


def test_answers_already_saved_are_read_as_before():
    # A results line holding a cut answer still counts as valid when read:
    # the check is made when asking, not when reading, so old run folders
    # are unchanged (cut answers there need a new run folder to be asked again).
    line = {"prompt_version": PROMPT_VERSION, "model": "qwen3.5:4b", "prompt_sha1": "abc",
            "answer": ANSWER, "input_tokens": 8194, "num_ctx": 16384}
    assert problem_with_result(line, {"model": "qwen3.5:4b"}, "abc") is None
