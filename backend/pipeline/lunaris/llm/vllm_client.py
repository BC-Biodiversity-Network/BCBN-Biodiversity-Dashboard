"""
Talk to a vLLM server: ask one question, and check the server is ready.

This is plan B for the cluster run: the same model (Qwen3.5-4B, from Hugging
Face, in full bf16 precision) served by vLLM instead of Ollama. vLLM answers
many records at once and keeps the shared instructions in its cache, so it
can be much faster per GPU. ask_vllm returns the same shape as ask_ollama, so
run_batch.py, merge_results.py and status.py work unchanged.

The request asks for the same things as the Ollama one:
  the same prompt text, as a single user message
  the answer schema, enforced by vLLM's structured output (json_schema)
  thinking off (chat_template_kwargs enable_thinking false)
  temperature 0
  presence_penalty 1.5, the value Ollama's qwen3.5:4b uses by default, so
      the two runs differ in the weights, not in the settings. (Ollama
      applies it to the last 64 tokens, vLLM to the whole answer; for
      answers of about 113 tokens this hardly differs.)

Only the Python standard library is used for the HTTP calls.
"""

import hashlib
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from ollama_client import plain_json_schema
from prompt_v6 import RESPONSE_SCHEMA

# Answers are about 113 tokens. The longest seen on the Mac was 2,341 (a
# runaway that still closed its JSON), so the limit is set well above that.
VLLM_MAX_TOKENS = 4096

# Matches Ollama's default for qwen3.5:4b (see the module docstring).
PRESENCE_PENALTY = 1.5


class ServerUnreachable(Exception):
    """Raised when nothing answers at the vLLM address, so the run should stop."""


def get_json(url, path, timeout=10):
    """Fetch one of vLLM's small JSON pages, such as /v1/models, and return it."""
    with urllib.request.urlopen(url.rstrip("/") + path, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_for_server(url, seconds):
    """
    Wait until the vLLM server is ready, and return its version.

    vLLM takes several minutes to start: loading about 9 GB of weights,
    compiling and capturing CUDA graphs. Its /health page answers only once it
    can serve. This asks every five seconds and raises ServerUnreachable if it
    is not ready in time.
    """
    deadline = time.monotonic() + seconds
    while True:
        try:
            with urllib.request.urlopen(url.rstrip("/") + "/health", timeout=10) as response:
                if response.status == 200:
                    break
        except (urllib.error.URLError, OSError):
            pass
        if time.monotonic() >= deadline:
            raise ServerUnreachable(f"vLLM at {url} was not ready after {seconds} s")
        time.sleep(5)
    try:
        return get_json(url, "/version").get("version")
    except (urllib.error.URLError, OSError, ValueError):
        return "unknown"


def weights_fingerprint(url, model):
    """
    Return a fingerprint of the weights the server loaded under this name, or None.

    vLLM has no model digest like Ollama's. /v1/models gives the folder the
    weights were loaded from (its "root"), which is on this same node, so the
    fingerprint is the sha1 of the folder's config.json and weight index, plus
    the names and sizes of the weight files. Two runs with the same
    fingerprint used the same download.
    """
    try:
        entries = get_json(url, "/v1/models").get("data", [])
    except (urllib.error.URLError, OSError, ValueError):
        return None
    for entry in entries:
        if entry.get("id") != model:
            continue
        folder = Path(entry.get("root") or "")
        digest = hashlib.sha1()
        for name in ["config.json", "model.safetensors.index.json"]:
            if (folder / name).exists():
                digest.update((folder / name).read_bytes())
        for weights in sorted(folder.glob("*.safetensors")):
            digest.update(f"{weights.name}:{weights.stat().st_size}".encode())
        return "vllm-" + digest.hexdigest()
    return None


def chat_request_body(model, prompt):
    """Return the body of one /v1/chat/completions request, as ask_vllm sends it."""
    return {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "presence_penalty": PRESENCE_PENALTY,
        "max_tokens": VLLM_MAX_TOKENS,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "record_facts", "schema": plain_json_schema(RESPONSE_SCHEMA)},
        },
        "chat_template_kwargs": {"enable_thinking": False},
    }


def ask_vllm(url, model, prompt, timeout=600):
    """
    Send one prompt to a vLLM server and return the answer and token counts.

    Returns the same shape as ask_ollama: {"answer", "input_tokens",
    "output_tokens", "thinking_tokens", "timings"} or {"error": ...}. A prompt
    too long for the server's context comes back as the same "the prompt
    filled the whole context" error Ollama's does, so it is grouped as
    context_full and retried with a larger context. If the server cannot be
    reached at all, ServerUnreachable is raised to stop the run.
    """
    request = urllib.request.Request(
        url.rstrip("/") + "/v1/chat/completions",
        data=json.dumps(chat_request_body(model, prompt)).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    started = time.monotonic()
    last_error = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                reply = json.loads(response.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:300]
            if "maximum context length" in detail or "max_model_len" in detail or "too long" in detail:
                return {"error": f"the prompt filled the whole context and was refused, raise "
                                 f"--num-ctx (vLLM said {error.code}: {detail[:120]})"}
            return {"error": f"vLLM said {error.code}: {detail[:200]}"}
        except urllib.error.URLError as error:
            if isinstance(error.reason, (ConnectionRefusedError, FileNotFoundError)):
                raise ServerUnreachable(str(error.reason))
            last_error = error
        except (TimeoutError, OSError) as error:
            last_error = error
    else:
        return {"error": f"gave up after 2 attempts: {last_error}"}

    choice = (reply.get("choices") or [{}])[0]
    usage = reply.get("usage") or {}
    if choice.get("finish_reason") == "length":
        return {"error": f"answer was cut off at {VLLM_MAX_TOKENS} tokens"}
    content = (choice.get("message") or {}).get("content") or ""
    try:
        answer = json.loads(content)
    except json.JSONDecodeError as error:
        return {"error": f"reply was not valid JSON: {error}"}
    return {
        "answer": answer,
        "input_tokens": usage.get("prompt_tokens"),
        "output_tokens": usage.get("completion_tokens"),
        "thinking_tokens": 0,
        # vLLM does not split one request's time into prompt and answer, so
        # only the total, as seen from here, is recorded.
        "timings": {"total_s": round(time.monotonic() - started, 3)},
    }
