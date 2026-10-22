"""
Talk to an Ollama server: ask one question, and check the server is ready.

Shared by exploration/llm/run_trial.py (the Mac run) and run_batch.py (the
cluster run on Fir), so both send exactly the same request. ask_ollama was
moved here from run_trial.py word for word.

Only the Python standard library is used for the HTTP calls, so nothing extra
has to be installed on the cluster.
"""

import json
import time
import urllib.error
import urllib.request

from prompt_v6 import RESPONSE_SCHEMA


# How much text Ollama is allowed to hold for one request, in tokens: the
# prompt plus the answer. Ollama's own default is small and when a prompt is
# longer it cuts it without any error, so the model would be answering about
# half a record. The v6 instructions are about 3,500 tokens, and the longest
# records in the test set add about 7,000 more, so 8,192 is not enough for
# them. 16,384 leaves room for those and for the answer.
OLLAMA_NUM_CTX = 16384

# How long to wait for one answer before giving up on that call, in seconds.
# A small model on a laptop answers in a few seconds. A large model on a busy
# node can take much longer, so this is generous, and it can be raised with
# --ollama-timeout.
OLLAMA_TIMEOUT = 300


class OllamaUnreachable(Exception):
    """
    Raised when nothing answers at the Ollama address.

    That is not something a retry fixes. The server is not started, or the
    address is wrong, so the run stops with a message saying so instead of
    trying every record and failing each one.
    """


def plain_json_schema(schema):
    """
    Return a copy of the answer schema in plain JSON Schema, for Ollama.

    RESPONSE_SCHEMA is written for Gemini. It can carry keys only Gemini
    understands, such as propertyOrdering, and Gemini also accepts type names
    in capitals such as "STRING". Ollama wants standard JSON Schema, so those
    keys are dropped and type names are put in lower case. Everything else is
    kept as it is, including the order of the properties, so "reason" stays
    first. Ollama fills the fields in the order they appear here.
    """
    if isinstance(schema, dict):
        plain = {}
        for key, value in schema.items():
            if key == "propertyOrdering":
                continue
            if key == "type" and isinstance(value, str):
                plain[key] = value.lower()
            else:
                plain[key] = plain_json_schema(value)
        return plain
    if isinstance(schema, list):
        return [plain_json_schema(item) for item in schema]
    return schema


def chat_request_body(model, prompt, num_ctx=OLLAMA_NUM_CTX, num_thread=None, schema=None):
    """
    Return the body of one /api/chat request, as ask_ollama sends it.

    Kept apart so bench_decode.py can send exactly the same request, then
    change one setting at a time. num_thread is how many CPU threads Ollama's
    runner may use. When it is not given, Ollama picks a number itself and the
    request is exactly the one the Mac run sends. schema is the answer format
    to force; by default the prompt v6 one, so the biodiversity filter's
    requests are unchanged. The geography prompt passes its own.
    """
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "think": False,
        "format": plain_json_schema(schema if schema is not None else RESPONSE_SCHEMA),
        "options": {"temperature": 0, "num_ctx": num_ctx},
    }
    if num_thread:
        body["options"]["num_thread"] = num_thread
    return body


def ask_ollama(url, model, prompt, num_ctx=OLLAMA_NUM_CTX, timeout=OLLAMA_TIMEOUT, num_thread=None,
               schema=None):
    """
    Send one prompt to an Ollama server and return the answer and token counts.

    This plays the same part as ask_model does for Gemini and returns the same
    shape, so the rest of the script does not care which one answered. Another
    local server, such as vLLM, would be added as one more small function like
    this one.

    It uses Ollama's own chat endpoint, /api/chat, and asks for:
      the same prompt text Gemini gets, as a single user message
      format: the answer schema, so Ollama forces the reply into that shape
      think: false, so a model with a thinking mode answers straight away
          instead of first writing out long reasoning that is not wanted
      temperature 0, the same as for Gemini
      num_ctx: room for the whole prompt, see OLLAMA_NUM_CTX
      num_thread: only if given, see chat_request_body
      schema: the answer format; prompt v6's unless another is given

    A call that times out or fails is tried once more. If the server cannot be
    reached at all, OllamaUnreachable is raised to stop the run. If the prompt
    filled the whole context, Ollama may have cut it, so the answer is
    returned as an error rather than trusted.
    """
    body = chat_request_body(model, prompt, num_ctx, num_thread, schema)
    request = urllib.request.Request(
        url.rstrip("/") + "/api/chat",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )

    last_error = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                reply = json.loads(response.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as error:
            # The server is there but refused the request. A missing model is
            # the common case, and retrying will not pull it.
            detail = error.read().decode("utf-8", errors="replace")[:200]
            return {"error": f"Ollama said {error.code}: {detail}"}
        except urllib.error.URLError as error:
            if isinstance(error.reason, (ConnectionRefusedError, FileNotFoundError)):
                raise OllamaUnreachable(str(error.reason))
            last_error = error
        except (TimeoutError, OSError) as error:
            last_error = error
        if attempt == 0:
            print(f"    call failed ({last_error}), trying once more")
    else:
        return {"error": f"gave up after 2 attempts: {last_error}"}

    input_tokens = reply.get("prompt_eval_count")
    if input_tokens and input_tokens >= num_ctx - 16:
        return {"error": f"the prompt filled the whole context ({input_tokens} of "
                         f"{num_ctx} tokens) and may have been cut, raise --num-ctx"}
    content = reply.get("message", {}).get("content", "")
    try:
        answer = json.loads(content)
    except json.JSONDecodeError as error:
        return {"error": f"reply was not valid JSON: {error}"}
    return {
        "answer": answer,
        "input_tokens": input_tokens,
        "output_tokens": reply.get("eval_count"),
        # Ollama does not count reasoning separately. With think set to false
        # there should be none, so this is recorded as 0.
        "thinking_tokens": 0,
        # Where the time went, as Ollama measured it, in seconds: loading the
        # model, reading the prompt, and writing the answer.
        "timings": reply_timings(reply),
    }


def reply_timings(reply):
    """
    Return Ollama's own timings for one answer, in seconds.

    Ollama reports them in nanoseconds: load_duration (loading the model, only
    on the first request), prompt_eval_duration (reading the prompt) and
    eval_duration (writing the answer, one token at a time). Splitting the
    time this way shows whether a slow answer is slow to read or slow to
    write. A value Ollama did not send is left out.
    """
    timings = {}
    for key, name in [("load_duration", "load_s"), ("prompt_eval_duration", "prompt_s"),
                      ("eval_duration", "answer_s"), ("total_duration", "total_s")]:
        if isinstance(reply.get(key), (int, float)):
            timings[name] = round(reply[key] / 1e9, 3)
    return timings


def get_json(url, path, timeout=10):
    """Fetch one of Ollama's small JSON pages, such as /api/version, and return it."""
    with urllib.request.urlopen(url.rstrip("/") + path, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_for_server(url, seconds):
    """
    Wait until an Ollama server answers, and return its version.

    On the cluster the server is started by the job script a moment before
    the Python run, and it takes a few seconds to come up. This asks every two
    seconds. If nothing answers in time, OllamaUnreachable is raised.
    """
    deadline = time.monotonic() + seconds
    while True:
        try:
            return get_json(url, "/api/version").get("version")
        except (urllib.error.URLError, OSError) as error:
            if time.monotonic() >= deadline:
                raise OllamaUnreachable(f"nothing answered at {url} after {seconds} s ({error})")
        time.sleep(2)


def model_digest(url, model):
    """
    Return the digest of the model the server holds under this name, or None.

    The digest identifies the exact weights. On the Mac, qwen3.5:4b is the MLX
    build; on an NVIDIA machine the same name is the GGUF build, with another
    digest. Recording it with every answer shows which weights produced it.

    Nothing is pulled here. If the model is missing this returns None, and the
    caller stops with a message, because on the cluster the compute nodes may
    have no internet and the model must be pulled on the login node first.
    """
    for entry in get_json(url, "/api/tags").get("models", []):
        if entry.get("name") == model or entry.get("model") == model:
            return entry.get("digest")
    return None


def gpu_share(url, model):
    """
    Return how much of the loaded model sits on the GPU, from 0.0 to 1.0.

    Ollama's /api/ps lists each loaded model with its size and the part of it
    in GPU memory (size_vram). Anything under 1.0 means part of the model runs
    on the CPU, which is far slower. That happens on Fir when Ollama's GPU
    discovery times out on a busy node. Returns None if the model is not
    loaded or the server cannot say.
    """
    try:
        models = get_json(url, "/api/ps").get("models", [])
    except (urllib.error.URLError, OSError, ValueError):
        return None
    for entry in models:
        if entry.get("name") == model or entry.get("model") == model:
            size = entry.get("size") or 0
            return (entry.get("size_vram") or 0) / size if size else None
    return None


# What Ollama writes in its log once it has looked for GPUs: one "inference
# compute" line per device it will use. A line with library=cpu means it found
# no usable GPU and will run on the CPU.
DISCOVERY_LINE = "inference compute"


def discovery_result(server_log, seconds):
    """
    Read Ollama's log until it says which device it will use, and return that.

    Returns ("gpu", line), ("cpu", line) or ("unknown", note). Ollama looks
    for GPUs when it starts. On Fir this search has timed out ("GPU discovery
    watchdog timed out") when the shared filesystem was slow, and Ollama then
    carried on with the CPU only, which makes every answer time out. Finding
    that in the log within a minute or two lets the job stop at once instead.
    "unknown" means no such line appeared in time; the caller then relies on
    loading the model and checking it is on the GPU.
    """
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            lines = [line for line in open(server_log, encoding="utf-8", errors="replace")
                     if DISCOVERY_LINE in line]
        except OSError:
            lines = []
        if lines:
            # Give Ollama a moment to write the line for every device.
            time.sleep(1)
            lines = [line for line in open(server_log, encoding="utf-8", errors="replace")
                     if DISCOVERY_LINE in line]
            gpu = [line for line in lines if "library=cpu" not in line.lower() and "id=cpu" not in line]
            return ("gpu", gpu[0].strip()) if gpu else ("cpu", lines[0].strip())
        time.sleep(2)
    return "unknown", f"no '{DISCOVERY_LINE}' line in {server_log} after {seconds} s"


def load_model(url, model, num_ctx=OLLAMA_NUM_CTX, num_thread=None, timeout=360):
    """
    Ask Ollama to load the model now, with the settings the answers will use.

    A /api/generate request with no prompt only loads the model and keeps it
    loaded. Doing this before the first record means a model that cannot
    start is found once, within the time limit, instead of as one timeout per
    record. The options match chat_request_body, so the first real request
    reuses this load. Returns (seconds it took, None) or (None, the problem).
    """
    options = {"num_ctx": num_ctx}
    if num_thread:
        options["num_thread"] = num_thread
    body = {"model": model, "keep_alive": -1, "stream": False, "options": options}
    request = urllib.request.Request(url.rstrip("/") + "/api/generate",
                                     data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:200]
        return None, f"Ollama said {error.code} while loading the model: {detail}"
    except (urllib.error.URLError, OSError) as error:
        return None, f"the model did not load within {timeout} s ({error})"
    return time.monotonic() - started, None


def prepare_ollama(url, model, num_ctx=OLLAMA_NUM_CTX, num_thread=None, server_log=None,
                   discovery_seconds=150, load_timeout=360):
    """
    Check an Ollama server is ready to answer on the GPU, before any record is asked.

    Three checks, each failing fast:
      1. if the server's log is given, it must report a GPU, not the CPU
      2. the model must load within load_timeout seconds
      3. once loaded, all of it must be in GPU memory

    Returns (exit_code, message): 0 if all is well, 5 if the model would run
    on the CPU (wholly or partly), 6 if the model did not load. run_batch.py
    and bench_decode.py use these as their own exit codes.
    """
    if server_log:
        found, line = discovery_result(server_log, discovery_seconds)
        if found == "cpu":
            return 5, f"Ollama found no usable GPU and fell back to the CPU: {line[:200]}"
        print(f"GPU search: {found} ({line[:160]})")
    seconds, problem = load_model(url, model, num_ctx, num_thread, load_timeout)
    if problem:
        return 6, problem
    share = gpu_share(url, model)
    if share is None or share < 0.999:
        return 5, f"the model loaded, but {'an unknown share' if share is None else f'only {share:.0%}'} of it is on the GPU"
    return 0, f"model loaded in {seconds:.1f} s, 100% on the GPU"
