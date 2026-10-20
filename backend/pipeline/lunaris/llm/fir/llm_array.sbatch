#!/bin/bash
# Run the Lunaris LLM filter on Fir: one batch per array task, one GPU slice each.
#
# Each task copies what it runs to the node's own disk (fir/job_setup.sh),
# starts its own private server on the GPU slice it was given (Ollama, or
# vLLM for the plan B test), checks the model is loaded and on the GPU before
# asking about any record, runs run_batch.py over its batch, and stops the
# server at the end. Rerunning a batch only asks about the records that do
# not have a valid answer yet.
#
# Submit it with fir/submit.sh from inside the run folder. submit.sh picks the
# GPU slice and passes it to sbatch:
#
#   cd /scratch/$USER/lunaris_llm/runs/test900
#   /scratch/$USER/lunaris_llm/code/fir/submit.sh 0
#
# Submitting this file with plain sbatch also works. It then gets the defaults
# below: the 1g.10gb slice, meant for Ollama at the default context.
#
# Settings that can be given in front of submit.sh or sbatch (Slurm passes them on):
#   NUM_CTX=65536  context size, for the few records too long for 16,384
#   NUM_THREAD=2   Ollama's CPU threads for its runner. Default: the job's CPU
#                  count. Left to itself Ollama sizes its threads from the
#                  whole node (48 cores on Fir), and on a 2-CPU job that made
#                  answers 5.7 times slower (bench_decode, 10 vs 60 tokens/s,
#                  same answers). NUM_THREAD=0 lets Ollama pick, as in run 1.
#   OLLAMA_LLM_LIBRARY=cuda_v12   which of Ollama's CUDA libraries to use
#                  (default cuda_v13)
#   BACKEND=vllm   plan B: serve the model with vLLM instead (fir/README.md)
#   LOAD_TIMEOUT=360  seconds Ollama may take to load the model (default 360)
#   LUNARIS_BASE   master folder on /scratch (see fir/job_setup.sh)
# With Ollama, WORKERS stays 1: it runs qwen3.5 one request at a time anyway.
#
# Exit codes are run_batch.py's (0 done, 2 server broke, 3 batch locked,
# 4 some records failed, 5 not on the GPU, 6 model did not load), plus
# 6 when copying to the node's disk or building Python there failed.
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

# Modules first: some module scripts trip over "set -u". VLLM_MODULES can
# name extra modules the vLLM test needs, if any.
module load python/3.12 ${VLLM_MODULES:-}

set -euo pipefail

# The shared setup: where things live, copying to the node's disk, starting
# Ollama. It is read from the master code folder on /scratch.
source "${LUNARIS_LLM_CODE:-${LUNARIS_BASE:-${SCRATCH:-/scratch/$USER}/lunaris_llm}/code}/fir/job_setup.sh"

RUN_DIR="${RUN_DIR:-$SLURM_SUBMIT_DIR}"
BACKEND="${BACKEND:-ollama}"
WORKERS="${WORKERS:-1}"
NUM_CTX="${NUM_CTX:-16384}"
NUM_THREAD="${NUM_THREAD:-${SLURM_CPUS_PER_TASK:-0}}"
BATCH="${SLURM_ARRAY_TASK_ID:?submit this as an array job, for example sbatch --array=0}"
SERVER_PID=""

# The GPU slice this job was given, as Slurm records it.
GPU_GIVEN=$(scontrol show job "${SLURM_JOB_ID:-0}" 2>/dev/null | grep -o 'gres/gpu:[^ ,]*' | head -1 || true)

echo "start    $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "host     $(hostname)  job ${SLURM_JOB_ID:-?}  array ${SLURM_ARRAY_JOB_ID:-?}_${BATCH}"
echo "run      $RUN_DIR  batch $BATCH  backend $BACKEND  workers $WORKERS  context $NUM_CTX  threads $([[ "$NUM_THREAD" == 0 ]] && echo "Ollama picks" || echo "$NUM_THREAD")"
echo "slice    ${GPU_GIVEN:-unknown}  cpus ${SLURM_CPUS_PER_TASK:-?}"
if command -v nvidia-smi >/dev/null; then
    echo "gpu      $(timeout 60 nvidia-smi --query-gpu=name,driver_version --format=csv,noheader | head -1)"
fi

# The 1g.10gb slice is sized for a context of 16,384 tokens. More than that
# might still fit, but if Ollama thinks it does not, it quietly runs part of
# the model on the CPU, which is far slower. submit.sh avoids this.
if [[ "$BACKEND" == ollama && "$GPU_GIVEN" == *1g.10gb* && $((WORKERS * NUM_CTX)) -gt 16384 ]]; then
    echo "WARNING  WORKERS=$WORKERS x NUM_CTX=$NUM_CTX is more than the 1g.10gb slice is sized for."
    echo "         Use fir/submit.sh, which picks a bigger slice."
fi

# Copy the code to the node's disk and set up Python there.
stage_code_and_python
pick_port
SERVER_LOG="$RUN_DIR/logs/${BACKEND}_${SLURM_ARRAY_JOB_ID:-local}_${BATCH}.log"

if [[ "$BACKEND" == vllm ]]; then
    # Plan B: vLLM serving Qwen3.5-4B from the Hugging Face download, offline.
    # Its own Python environment and the weights stay on /scratch: the
    # environment cannot be moved, and this is a one-off test.
    # The served name is the run's model name, so run_batch.py can find it.
    VLLM_BIN="${LUNARIS_VLLM_BIN:-$LUNARIS_BASE/venv_vllm/bin/vllm}"
    VLLM_MODEL_DIR="${VLLM_MODEL_DIR:-$LUNARIS_BASE/hf/Qwen3.5-4B}"
    MODEL=$(python -c 'import json, sys; print(json.load(open(sys.argv[1]))["model"])' "$RUN_DIR/run.json")
    export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
    # Prefix caching keeps the shared instructions, text only (no image or
    # video inputs), and thinking off by default as well as per request.
    # VLLM_FLAGS replaces this list if a flag is not accepted (see README).
    DEFAULT_VLLM_FLAGS='--enable-prefix-caching --limit-mm-per-prompt {"image":0,"video":0} --default-chat-template-kwargs {"enable_thinking":false}'
    read -r -a FLAGS <<< "${VLLM_FLAGS:-$DEFAULT_VLLM_FLAGS}"
    "$VLLM_BIN" serve "$VLLM_MODEL_DIR" --served-model-name "$MODEL" --host 127.0.0.1 --port "$PORT" \
        --max-model-len "$NUM_CTX" --max-num-seqs "$WORKERS" --gpu-memory-utilization 0.90 \
        "${FLAGS[@]}" >"$SERVER_LOG" 2>&1 &
    SERVER_PID=$!
    echo "vllm     $("$VLLM_BIN" --version 2>/dev/null | tail -1) serving $VLLM_MODEL_DIR as $MODEL on $OLLAMA_HOST, log $SERVER_LOG"
else
    # Copy the Ollama program and the model to the node's disk, and start it.
    stage_ollama
    export OLLAMA_NUM_PARALLEL="$WORKERS"
    start_ollama "$SERVER_LOG"
fi

# Report how much of the loaded model is on the GPU, from Ollama's /api/ps.
# Anything under 100% means part of it ran on the CPU.
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
        print("WARNING  part of the model ran on the CPU, which is much slower.")
EOF
}

# However the job ends: report GPU use (Ollama only) while the model is still
# loaded, then stop the server.
finish() {
    if [[ "$BACKEND" == ollama && -n "$SERVER_PID" ]]; then
        report_gpu_use
    fi
    if [[ -n "$SERVER_PID" ]]; then
        kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
}
trap finish EXIT

# run_batch.py waits for the server, checks it found the GPU and that the
# model loads and sits on the GPU, and only then does the batch. It never
# downloads anything. Its exit code is the job's.
ARGS=(--batch "$BATCH" --backend "$BACKEND" --ollama-url "http://$OLLAMA_HOST"
      --workers "$WORKERS" --num-ctx "$NUM_CTX")
if [[ "$BACKEND" == ollama ]]; then
    ARGS+=(--server-log "$SERVER_LOG" --load-timeout "${LOAD_TIMEOUT:-360}")
fi
if [[ "$BACKEND" == ollama && "$NUM_THREAD" != 0 ]]; then
    ARGS+=(--num-thread "$NUM_THREAD")
fi
set +e
python "$CODE_DIR/run_batch.py" "$RUN_DIR" "${ARGS[@]}"
STATUS=$?
set -e

echo "end      $(date -u +%Y-%m-%dT%H:%M:%SZ)  exit $STATUS"
if [[ $STATUS -eq 5 || $STATUS -eq 6 ]]; then
    echo "NOTE     the model did not get going on $(hostname -s); see the reason above and the server log."
    echo "         status.sh prints the line to resubmit, with EXCLUDE= if one node keeps failing."
fi
exit $STATUS
