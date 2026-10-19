#!/bin/bash
# Run the Lunaris LLM filter on Fir: one batch per array task, one GPU slice each.
#
# Each task starts its own private Ollama server on the GPU slice it was given,
# runs run_batch.py over its batch, reports how much of the model sat on the
# GPU, and stops the server at the end. Rerunning a batch only asks about the
# records that do not have a valid answer yet.
#
# Submit it with fir/submit.sh from inside the run folder. submit.sh picks the
# GPU slice from WORKERS and NUM_CTX and passes it to sbatch:
#
#   cd ~/scratch/lunaris_llm/runs/test900
#   ~/lunaris_llm/code/fir/submit.sh 0
#
# Submitting this file with plain sbatch also works. It then gets the defaults
# below: the 1g.10gb slice, which is only meant for WORKERS=1 at the default
# context. The job prints a warning if the slice looks too small.
#
# Settings that can be given in front of submit.sh or sbatch (Slurm passes them on):
#   WORKERS=4      questions in flight at once (default 1, one at a time)
#   NUM_CTX=65536  context size, for the few records too long for 16,384
#
# No passwords, keys or tokens are used or needed anywhere in this script.

#SBATCH --account=def-srivast
#SBATCH --job-name=lunaris-llm
# Fir needs a GPU model in every request. Allowed: h100 (a whole GPU) or the
# MIG slices nvidia_h100_80gb_hbm3_1g.10gb, _2g.20gb and _3g.40gb.
#SBATCH --gpus=nvidia_h100_80gb_hbm3_1g.10gb:1
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
# 3 hours or less lands in gpubase_bygpu_b1, the partition that starts soonest.
#SBATCH --time=03:00:00
#SBATCH --output=logs/%x_%A_%a.out

# Modules first: some module scripts trip over "set -u". python/3.12 is the
# same module the virtual environment was built with (fir/README.md step 2).
module load python/3.12

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

# The GPU slice this job was given, as Slurm records it.
GPU_GIVEN=$(scontrol show job "${SLURM_JOB_ID:-0}" 2>/dev/null | grep -o 'gres/gpu:[^ ,]*' | head -1 || true)

echo "start    $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "host     $(hostname)  job ${SLURM_JOB_ID:-?}  array ${SLURM_ARRAY_JOB_ID:-?}_${BATCH}"
echo "run      $RUN_DIR  batch $BATCH  workers $WORKERS  context $NUM_CTX"
echo "slice    ${GPU_GIVEN:-unknown}"
if command -v nvidia-smi >/dev/null; then
    echo "gpu      $(nvidia-smi --query-gpu=name,driver_version --format=csv,noheader | head -1)"
fi

# The 1g.10gb slice is sized for one slot of 16,384 tokens. More than that
# might still fit, but if Ollama thinks it does not, it quietly runs part of
# the model on the CPU, which is far slower. submit.sh avoids this.
if [[ "$GPU_GIVEN" == *1g.10gb* && $((WORKERS * NUM_CTX)) -gt 16384 ]]; then
    echo "WARNING  WORKERS=$WORKERS x NUM_CTX=$NUM_CTX is more than the 1g.10gb slice is sized for."
    echo "         Use fir/submit.sh, which picks a bigger slice. Watch the 'gpu use' line at the end."
fi

# Pick a free port for this job's server. Several tasks can share one GPU
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

# Report how much of the loaded model is on the GPU, from Ollama's /api/ps.
# Anything under 100% means part of it ran on the CPU and the slice is too small.
report_gpu_use() {
    python - "$OLLAMA_HOST" <<'EOF' || true
import json, sys, urllib.request
try:
    with urllib.request.urlopen(f"http://{sys.argv[1]}/api/ps", timeout=5) as reply:
        models = json.load(reply).get("models", [])
except Exception as error:
    print(f"gpu use  could not ask the server ({error})")
    sys.exit()
if not models:
    print("gpu use  no model loaded")
for model in models:
    size, vram = model.get("size", 0), model.get("size_vram", 0)
    share = vram / size * 100 if size else 0
    print(f"gpu use  {model.get('name')}: {size / 2**30:.1f} GiB in all, {vram / 2**30:.1f} GiB "
          f"on the GPU ({share:.0f}%), context {model.get('context_length', '?')}")
    if share < 100:
        print("WARNING  part of the model ran on the CPU, which is much slower. "
              "Submit with a bigger slice (GPU=... fir/submit.sh).")
EOF
}

# However the job ends: report GPU use while the model is still loaded, then
# stop the server.
finish() {
    report_gpu_use
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
}
trap finish EXIT
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
