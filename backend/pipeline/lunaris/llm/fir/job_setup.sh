# Shared setup for the Fir job scripts (llm_array.sbatch and bench_decode.sbatch).
# It is sourced by them, not run on its own.
#
# Why it exists. On 2026-10-19 Fir's /home was unreachable from some nodes for
# a while ("Cannot send after transport endpoint shutdown"). Ollama's program
# and libraries and the Python environment lived on /home, and every job
# started then failed the same way: Ollama's GPU search timed out, it fell
# back to the CPU, and the model never loaded. So now:
#
#   - The master copies live on /scratch (LUNARIS_BASE, by default
#     /scratch/$USER/lunaris_llm), and every path is written out in full
#     rather than going through the ~/scratch link, which sits on /home.
#   - Each job first copies what it runs (the code, the Ollama program and
#     the model) to the node's own disk ($SLURM_TMPDIR) and builds a fresh
#     Python environment there from the Alliance wheels. After that the job
#     reads nothing from /home or /scratch except its batch, and writes only
#     its results and logs to /scratch.
#   - Every step has a time limit. If one fails or hangs, the job stops with
#     exit code 6 and says which step, instead of timing out record by record.
#
# Settings (all optional, set before sbatch or submit.sh):
#   LUNARIS_BASE        master folder (default /scratch/$USER/lunaris_llm)
#   LUNARIS_LLM_CODE    code to copy (default $LUNARIS_BASE/code)
#   LUNARIS_OLLAMA_DIR  Ollama program to copy (default $LUNARIS_BASE/ollama)
#   LUNARIS_MODELS_DIR  Ollama model store to copy (default $LUNARIS_BASE/models)
#   LUNARIS_LLM_VENV    use this existing Python environment instead of
#                       building one on the node (for tests)
#   STAGE_TIMEOUT       seconds allowed for each copy or install (default 600)

LUNARIS_BASE="${LUNARIS_BASE:-${SCRATCH:-/scratch/$USER}/lunaris_llm}"
SOURCE_CODE="${LUNARIS_LLM_CODE:-$LUNARIS_BASE/code}"
SOURCE_OLLAMA="${LUNARIS_OLLAMA_DIR:-$LUNARIS_BASE/ollama}"
SOURCE_MODELS="${LUNARIS_MODELS_DIR:-$LUNARIS_BASE/models}"
STAGE_TIMEOUT="${STAGE_TIMEOUT:-600}"
PANDAS_VERSION="${PANDAS_VERSION:-3.0.5}"
DUCKDB_VERSION="${DUCKDB_VERSION:-1.5.3}"   # the GeoNames lookup; 1.5.3 is the Fir wheel for Python 3.12
STAGE="${SLURM_TMPDIR:-$(mktemp -d)}/lunaris"

# Stop the job with exit code 6 and a line saying what failed.
fail_setup() {
    echo "SETUP FAILED  $1"
    echo "end      $(date -u +%Y-%m-%dT%H:%M:%SZ)  exit 6"
    exit 6
}

# Copy one folder to the node's disk within the time limit, and say how
# big it was and how long it took. Arguments: what it is, from, to.
stage_copy() {
    local started=$SECONDS
    [[ -d "$2" ]] || fail_setup "$1 not found at $2"
    timeout "$STAGE_TIMEOUT" cp -a "$2" "$3" \
        || fail_setup "copying $1 from $2 failed or took over ${STAGE_TIMEOUT} s (shared filesystem trouble?)"
    echo "staged   $1: $(du -sh "$3" 2>/dev/null | cut -f1) in $((SECONDS - started)) s"
}

# Copy the code and set up Python on the node's disk. Afterwards CODE_DIR
# is the local copy of the code and its Python environment is active.
stage_code_and_python() {
    mkdir -p "$STAGE" || fail_setup "cannot create $STAGE"
    echo "stage    $STAGE ($(df -h "$STAGE" 2>/dev/null | awk 'NR == 2 {print $4}') free)"
    stage_copy code "$SOURCE_CODE" "$STAGE/code"
    CODE_DIR="$STAGE/code"
    if [[ -n "${LUNARIS_LLM_VENV:-}" ]]; then
        source "$LUNARIS_LLM_VENV/bin/activate" || fail_setup "cannot use the Python environment $LUNARIS_LLM_VENV"
        echo "python   using $LUNARIS_LLM_VENV"
        return
    fi
    # A fresh environment from the Alliance wheels, as the Alliance advises
    # for jobs: nothing in it comes from /home.
    local started=$SECONDS
    timeout "$STAGE_TIMEOUT" virtualenv --no-download -q "$STAGE/venv" \
        || fail_setup "could not create a Python environment on the node"
    timeout "$STAGE_TIMEOUT" "$STAGE/venv/bin/pip" install --no-index -q "pandas==$PANDAS_VERSION" "duckdb==$DUCKDB_VERSION" \
        || fail_setup "could not install pandas $PANDAS_VERSION and duckdb $DUCKDB_VERSION from the Alliance wheels"
    source "$STAGE/venv/bin/activate"
    echo "python   $(python -c 'import sys, pandas; print(sys.version.split()[0], "with pandas", pandas.__version__)') on the node, in $((SECONDS - started)) s"
}

# Copy the Ollama program and the model to the node's disk. Afterwards
# OLLAMA_BIN is the local program and OLLAMA_MODELS the local model store.
stage_ollama() {
    stage_copy "Ollama program" "$SOURCE_OLLAMA" "$STAGE/ollama"
    stage_copy "model store" "$SOURCE_MODELS" "$STAGE/models"
    OLLAMA_BIN="$STAGE/ollama/bin/ollama"
    export OLLAMA_MODELS="$STAGE/models"
}

# Start a private Ollama server from the local copy, logging to the file
# given. HOME points at the node's disk, because Ollama keeps a small
# settings folder (~/.ollama) in HOME and /home must not be needed.
# Sets SERVER_PID.
start_ollama() {
    export OLLAMA_KEEP_ALIVE=-1
    # Ollama looks for the GPU with its CUDA 12 and CUDA 13 libraries in turn,
    # each with a 30 second limit. Fir's driver (580, CUDA 13.0) works with the
    # CUDA 13 ones, so only those are tried.
    export OLLAMA_LLM_LIBRARY="${OLLAMA_LLM_LIBRARY:-cuda_v13}"
    mkdir -p "$STAGE/home"
    HOME="$STAGE/home" "$OLLAMA_BIN" serve >"$1" 2>&1 &
    SERVER_PID=$!
    echo "ollama   $(HOME="$STAGE/home" "$OLLAMA_BIN" --version 2>/dev/null | tail -1) on $OLLAMA_HOST, log $1"
}

# Pick a free local port and point OLLAMA_HOST at it. Several jobs can share
# one GPU node, and each needs its own server.
pick_port() {
    PORT=$(python -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')
    export OLLAMA_HOST="127.0.0.1:$PORT"
}
