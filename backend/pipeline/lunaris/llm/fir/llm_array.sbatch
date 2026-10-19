#!/bin/bash
# Run the Lunaris LLM filter on Fir: one batch per array task, one GPU each.
#
# Each task starts its own private Ollama server on the GPU it was given,
# runs run_batch.py over its batch, and stops the server at the end. Rerunning
# a batch only asks about the records that do not have a valid answer yet.
#
# Submit it from inside the run folder, so the logs land in its logs/ folder:
#
#   cd ~/scratch/lunaris_llm/runs/test900
#   sbatch --array=0 ~/lunaris_llm/code/fir/llm_array.sbatch
#
# Settings that can be given in front of sbatch (Slurm passes them on):
#   WORKERS=4      questions in flight at once (default 1, one at a time)
#   NUM_CTX=65536  context size, for the few records too long for 16,384
#
# Lines marked CONFIRM ON FIR are guesses until the checks in fir/README.md
# have been run.
#
# No passwords, keys or tokens are used or needed anywhere in this script.

#SBATCH --account=def-srivast
#SBATCH --job-name=lunaris-llm
#SBATCH --gpus=1                  # CONFIRM ON FIR: GPU type, e.g. --gpus=h100:1, or a MIG slice if listed
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G                 # the model is about 4 GB; this leaves plenty of room
#SBATCH --time=03:00:00           # CONFIRM ON FIR: enough for one 4,000-record batch? adjust after the test run
#SBATCH --output=logs/%x_%A_%a.out

# Modules first: some module scripts trip over "set -u".
module load StdEnv/2023 python/3.12   # CONFIRM ON FIR: module names from "module avail python"

set -euo pipefail

# Where things live on Fir. Each can be changed by setting it before sbatch.
RUN_DIR="${RUN_DIR:-$SLURM_SUBMIT_DIR}"
CODE_DIR="${LUNARIS_LLM_CODE:-$HOME/lunaris_llm/code}"
VENV="${LUNARIS_LLM_VENV:-$HOME/lunaris_llm/venv}"
OLLAMA_BIN="${LUNARIS_OLLAMA_BIN:-$HOME/lunaris_llm/ollama/bin/ollama}"
export OLLAMA_MODELS="${OLLAMA_MODELS:-$HOME/scratch/lunaris_llm/models}"
WORKERS="${WORKERS:-1}"
NUM_CTX="${NUM_CTX:-16384}"
BATCH="${SLURM_ARRAY_TASK_ID:?submit this as an array job, for example sbatch --array=0}"

source "$VENV/bin/activate"

echo "start    $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "host     $(hostname)  job ${SLURM_JOB_ID:-?}  array ${SLURM_ARRAY_JOB_ID:-?}_${BATCH}"
echo "run      $RUN_DIR  batch $BATCH  workers $WORKERS  context $NUM_CTX"
if command -v nvidia-smi >/dev/null; then
    echo "gpu      $(nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader | head -1)"
fi

# Pick a free port for this job's server. Up to four tasks can share one GPU
# node, and each needs its own server, so the default port 11434 cannot be
# used by all of them.
PORT=$(python -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')
export OLLAMA_HOST="127.0.0.1:$PORT"
# As many parallel slots as workers, and keep the model loaded for the whole job.
export OLLAMA_NUM_PARALLEL="$WORKERS"
export OLLAMA_KEEP_ALIVE=-1

SERVER_LOG="$RUN_DIR/logs/ollama_${SLURM_ARRAY_JOB_ID:-local}_${BATCH}.log"
"$OLLAMA_BIN" serve >"$SERVER_LOG" 2>&1 &
SERVER_PID=$!
# Stop the server however the job ends.
trap 'kill "$SERVER_PID" 2>/dev/null || true; wait "$SERVER_PID" 2>/dev/null || true' EXIT
echo "ollama   $("$OLLAMA_BIN" --version 2>/dev/null | tail -1) on $OLLAMA_HOST, log $SERVER_LOG"

# run_batch.py waits for the server to come up, checks the model is there
# (it never downloads one), and does the batch. Its exit code is the job's.
set +e
python "$CODE_DIR/run_batch.py" "$RUN_DIR" --batch "$BATCH" \
    --ollama-url "http://$OLLAMA_HOST" --workers "$WORKERS" --num-ctx "$NUM_CTX"
STATUS=$?
set -e

echo "end      $(date -u +%Y-%m-%dT%H:%M:%SZ)  exit $STATUS"
exit $STATUS
