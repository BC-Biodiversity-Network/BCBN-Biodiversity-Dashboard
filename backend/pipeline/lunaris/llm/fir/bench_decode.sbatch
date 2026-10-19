#!/bin/bash
# Measure Ollama's answer-writing speed on one GPU slice: is it the CPUs?
#
# Starts a private Ollama server the same way llm_array.sbatch does, then runs
# bench_decode.py, which sends the same records under a few settings (as run,
# num_thread set, no presence penalty, no JSON schema). Nothing is written
# into the run folder; the results go to a CSV in the folder you submit from.
#
# Submit it twice from a bench folder, once with 2 CPUs and once with 8, and
# compare the two tables at the end of the logs:
#
#   mkdir -p ~/scratch/lunaris_llm/bench/logs && cd ~/scratch/lunaris_llm/bench
#   sbatch --cpus-per-task=2 ~/lunaris_llm/code/fir/bench_decode.sbatch ~/scratch/lunaris_llm/runs/test900
#   sbatch --cpus-per-task=8 --mem=16G ~/lunaris_llm/code/fir/bench_decode.sbatch ~/scratch/lunaris_llm/runs/test900
#
# BENCH_RECORDS=24 sets the records per setting (default 24). Each job takes
# 10 to 25 minutes on a 1g.10gb slice.
#
# No passwords, keys or tokens are used or needed anywhere in this script.

#SBATCH --account=def-srivast
#SBATCH --job-name=lunaris-bench
#SBATCH --gpus=nvidia_h100_80gb_hbm3_1g.10gb:1
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --time=00:45:00
#SBATCH --output=logs/%x_%j.out

module load python/3.12

set -euo pipefail

RUN_DIR="${1:?give a run folder to take the records from, for example ~/scratch/lunaris_llm/runs/test900}"
CODE_DIR="${LUNARIS_LLM_CODE:-$HOME/lunaris_llm/code}"
VENV="${LUNARIS_LLM_VENV:-$HOME/lunaris_llm/venv}"
OLLAMA_BIN="${LUNARIS_OLLAMA_BIN:-$HOME/lunaris_llm/ollama/bin/ollama}"
export OLLAMA_MODELS="${OLLAMA_MODELS:-$HOME/scratch/lunaris_llm/models}"
CPUS="${SLURM_CPUS_PER_TASK:-2}"

source "$VENV/bin/activate"

echo "start    $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "host     $(hostname)  job ${SLURM_JOB_ID:-?}"
echo "cpus     job $CPUS, usable $(nproc), node $(nproc --all)"
if command -v nvidia-smi >/dev/null; then
    echo "gpu      $(nvidia-smi --query-gpu=name,driver_version --format=csv,noheader | head -1)"
fi

PORT=$(python -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')
export OLLAMA_HOST="127.0.0.1:$PORT"
export OLLAMA_NUM_PARALLEL=1
export OLLAMA_KEEP_ALIVE=-1
export OLLAMA_LLM_LIBRARY="${OLLAMA_LLM_LIBRARY:-cuda_v13}"

SERVER_LOG="logs/ollama_bench_${SLURM_JOB_ID:-local}.log"
"$OLLAMA_BIN" serve >"$SERVER_LOG" 2>&1 &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null || true; wait "$SERVER_PID" 2>/dev/null || true' EXIT

python "$CODE_DIR/bench_decode.py" "$RUN_DIR" --ollama-url "http://$OLLAMA_HOST" \
    --records "${BENCH_RECORDS:-24}" --threads "$CPUS" \
    --out "bench_${CPUS}cpu_${SLURM_JOB_ID:-local}.csv"

# What the runner was started with, from the server log.
echo "runner   $(grep -o -- '--threads [0-9]*' "$SERVER_LOG" | sort | uniq -c | tr '\n' ' ')"
echo "end      $(date -u +%Y-%m-%dT%H:%M:%SZ)"
