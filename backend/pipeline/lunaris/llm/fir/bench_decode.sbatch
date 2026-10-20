#!/bin/bash
# Measure Ollama's answer-writing speed on one GPU slice: is it the CPUs?
#
# Copies what it runs to the node's own disk and starts a private Ollama
# server the same way llm_array.sbatch does (fir/job_setup.sh), then runs
# bench_decode.py, which checks the model loads and sits on the GPU and then
# sends the same records under a few settings (as run, num_thread set, no
# presence penalty, no JSON schema). Nothing is written into the run folder;
# the results go to a CSV in the folder you submit from.
#
# Submit it twice from the bench folder, once with 2 CPUs and once with 8,
# and compare the two tables at the end of the logs:
#
#   B=/scratch/$USER/lunaris_llm
#   mkdir -p $B/bench/logs && cd $B/bench
#   sbatch --cpus-per-task=2 $B/code/fir/bench_decode.sbatch $B/runs/test900
#   sbatch --cpus-per-task=8 --mem=16G $B/code/fir/bench_decode.sbatch $B/runs/test900
#
# BENCH_RECORDS=24 sets the records per setting (default 24). Each job takes
# 10 to 25 minutes on a 1g.10gb slice. Exit codes: 0 done, 2 the server broke
# part way, 5 the model is not on the GPU, 6 it did not load or the copying
# to the node's disk failed.
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

source "${LUNARIS_LLM_CODE:-${LUNARIS_BASE:-${SCRATCH:-/scratch/$USER}/lunaris_llm}/code}/fir/job_setup.sh"

RUN_DIR="${1:?give a run folder to take the records from, for example /scratch/\$USER/lunaris_llm/runs/test900}"
CPUS="${SLURM_CPUS_PER_TASK:-2}"
SERVER_PID=""

echo "start    $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "host     $(hostname)  job ${SLURM_JOB_ID:-?}"
echo "cpus     job $CPUS, usable $(nproc), node $(nproc --all)"
if command -v nvidia-smi >/dev/null; then
    echo "gpu      $(timeout 60 nvidia-smi --query-gpu=name,driver_version --format=csv,noheader | head -1)"
fi

stage_code_and_python
stage_ollama
pick_port
export OLLAMA_NUM_PARALLEL=1
SERVER_LOG="$PWD/logs/ollama_bench_${SLURM_JOB_ID:-local}.log"
start_ollama "$SERVER_LOG"
trap 'if [[ -n "$SERVER_PID" ]]; then kill "$SERVER_PID" 2>/dev/null || true; wait "$SERVER_PID" 2>/dev/null || true; fi' EXIT

set +e
python "$CODE_DIR/bench_decode.py" "$RUN_DIR" --ollama-url "http://$OLLAMA_HOST" \
    --records "${BENCH_RECORDS:-24}" --threads "$CPUS" --server-log "$SERVER_LOG" \
    --out "bench_${CPUS}cpu_${SLURM_JOB_ID:-local}.csv"
STATUS=$?
set -e

# Any --threads flags in the server log. Ollama 0.40 sets the thread count
# when it loads the model rather than on the command line, so this is often
# empty even when num_thread took effect; the table's speeds are what count.
echo "runner   --threads flags in the server log: $(grep -o -- '--threads [0-9]*' "$SERVER_LOG" | sort | uniq -c | tr '\n' ' ')"
echo "end      $(date -u +%Y-%m-%dT%H:%M:%SZ)  exit $STATUS"
exit $STATUS
