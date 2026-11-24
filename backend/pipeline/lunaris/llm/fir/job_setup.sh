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
#   EXTRA_WHEELS        more packages to install on the node, besides pandas
#                       and duckdb (default none; the BC filter job sets it)
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

# How many times stage_copy tries one copy before the job stops.
STAGE_COPY_TRIES=3

# Copy one folder to the node's disk within the time limit, and say how
# big it was and how long it took. Arguments: what it is, from, to.
#
# The copy is "cp -RP --preserve=timestamps":
#   -R   the whole folder, with everything in it;
#   -P   a symbolic link is copied as a link, as cp -a did (Ollama's
#        libraries use links);
#   --preserve=timestamps   keep the files' times (Python's .pyc caches
#        then stay valid), and nothing else.
# It no longer uses cp -a, which also tries to keep ownership, permission
# lists (ACLs) and extended attributes. Keeping the permissions is the step
# that failed on some nodes ("cp: preserving permissions for ...: Numerical
# result out of range", 2026-11-24, jobs 5552195 and 5553367, and twice in
# the filter 2 run): cp reads and writes the files' ACLs then. Without it,
# each new file gets the source's permission bits less the job's umask, so
# an executable file (the Ollama program) stays executable.
#
# Each try copies into a new, empty folder (to.try1, to.try2, ...); the
# first one that works is renamed to "to", so the callers' paths do not
# change. A failed try is left where it is: it is on the node's temporary
# disk, which Slurm empties after the job. Each failed try prints one line
# with cp's message; after STAGE_COPY_TRIES failures the job stops (exit 6).
# The time limit applies to each try. Last, the copy must hold as many files
# (and links) and as many executable files as the source.
stage_copy() {
    local started=$SECONDS try target errors code note=""
    [[ -d "$2" ]] || fail_setup "$1 not found at $2"
    [[ ! -e "$3" ]] || fail_setup "cannot copy $1 to $3: it is already there"
    for ((try = 1; try <= STAGE_COPY_TRIES; try++)); do
        target="$3.try$try"
        errors="$3.try$try.cp_errors.txt"
        mkdir "$target" || fail_setup "cannot create $target"
        # "$2/." copies what is in the folder into the new, empty one.
        timeout "$STAGE_TIMEOUT" cp -RP --preserve=timestamps "$2/." "$target" 2>"$errors" && code=0 || code=$?
        if [[ $code -eq 0 ]]; then
            mv "$target" "$3" || fail_setup "cannot rename $target to $3"
            check_copy "$1" "$2" "$3"
            ((try == 1)) || note=" (try $try of $STAGE_COPY_TRIES)"
            echo "staged   $1: $(du -sh "$3" 2>/dev/null | cut -f1) in $((SECONDS - started)) s$note"
            return 0
        fi
        echo "copy     $1, try $try of $STAGE_COPY_TRIES failed: $(copy_error "$code" "$errors")"
    done
    fail_setup "copying $1 from $2 failed $STAGE_COPY_TRIES times or took over ${STAGE_TIMEOUT} s (shared filesystem trouble?)"
}

# Print one line saying why a copy try failed: the time limit, or cp's first
# message and how many more lines it wrote. Arguments: cp's exit code, the
# file holding its messages.
copy_error() {
    local first lines
    if [[ $1 -eq 124 ]]; then
        echo "took over ${STAGE_TIMEOUT} s"
        return 0
    fi
    first=$(head -n 1 "$2" 2>/dev/null || true)
    lines=$(wc -l < "$2" 2>/dev/null || echo 0)
    lines=$((lines))
    if ((lines > 1)); then
        echo "exit $1: ${first:-cp gave no message} (and $((lines - 1)) more lines in $2)"
    else
        echo "exit $1: ${first:-cp gave no message}"
    fi
}

# Print how many files and links a folder holds (everything but folders),
# or, with a second argument "exec", only the regular files its owner may
# run. Arguments: the folder, optionally "exec".
count_files() {
    if [[ "${2:-}" == exec ]]; then
        timeout "$STAGE_TIMEOUT" find "$1/" -type f -perm -100 | wc -l
    else
        timeout "$STAGE_TIMEOUT" find "$1/" ! -type d | wc -l
    fi
}

# Stop the job (exit 6) unless a copy holds as many files and links, and as
# many executable files, as its source. Arguments: what it is, from, to.
check_copy() {
    local source_files copy_files source_exec copy_exec
    source_files=$(count_files "$2") || fail_setup "could not count the files of $1 in $2"
    copy_files=$(count_files "$3") || fail_setup "could not count the files of $1 in $3"
    source_exec=$(count_files "$2" exec) || fail_setup "could not count the executable files of $1 in $2"
    copy_exec=$(count_files "$3" exec) || fail_setup "could not count the executable files of $1 in $3"
    if ((source_files != copy_files)); then
        fail_setup "the copy of $1 in $3 has $((copy_files)) files, but $2 has $((source_files))"
    fi
    if ((source_exec != copy_exec)); then
        fail_setup "the copy of $1 in $3 has $((copy_exec)) executable files, but $2 has $((source_exec))"
    fi
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
    # EXTRA_WHEELS: more packages a job needs, as pip names them, separated
    # by spaces (the BC filter job asks for shapely and pyproj). Empty by default.
    local extra=()
    read -r -a extra <<< "${EXTRA_WHEELS:-}"
    timeout "$STAGE_TIMEOUT" "$STAGE/venv/bin/pip" install --no-index -q "pandas==$PANDAS_VERSION" "duckdb==$DUCKDB_VERSION" "${extra[@]}" \
        || fail_setup "could not install pandas $PANDAS_VERSION, duckdb $DUCKDB_VERSION${EXTRA_WHEELS:+ and $EXTRA_WHEELS} from the Alliance wheels"
    source "$STAGE/venv/bin/activate"
    echo "python   $(python -c 'import sys, pandas; print(sys.version.split()[0], "with pandas", pandas.__version__)') on the node, in $((SECONDS - started)) s"
    if [[ -n "${EXTRA_WHEELS:-}" ]]; then
        echo "packages $("$STAGE/venv/bin/pip" freeze 2>/dev/null | grep -iE '^(duckdb|shapely|pyproj|numpy)==' | tr '\n' ' ')"
    fi
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
