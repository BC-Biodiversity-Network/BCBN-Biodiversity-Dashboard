#!/bin/bash
# Run the place lookup's model step on Fir (llm/place_pick_run.py, prompt P1)
# for one model and one input folder. One job per part of the records; an
# array splits the run into parts.
#
# Submit it with fir/place_pick_submit.sh, which picks the GPU slice for the
# model, the run folder and the model store, and passes them on. A dry run
# (DRY_RUN=1) asks no model, starts no Ollama and needs no GPU.
#
# It reuses the BC filter's setup (fir/job_setup.sh): the code, Python, the
# Ollama program and the model are copied to the node's own disk, Python is
# built there from the Alliance wheels (pandas only is needed; no GeoNames
# table, no shapely), and every step has a time limit (exit 6 if one
# fails). The input is read from /scratch, and results and logs are written
# to the run folder on /scratch as they come.
#
# Settings, exported by place_pick_submit.sh (or set before sbatch):
#   MODEL          the Ollama model name, such as qwen3.6:35b-a3b
#   INPUT_DIR      the input folder from place_pick_prepare.py
#   RUN_DIR        the run folder for this model
#   PARTS          how many parts the records are split into (array size)
#   NUM_CTX, NUM_THREAD, LOAD_TIMEOUT   as in geo_filter.sbatch
#   DRY_RUN=1      check and rebuild the prompts only
#   OLLAMA_FLASH_ATTENTION   0 turns flash attention off (the default for
#                  qwen3.6:* models, set by the submit script); recorded in
#                  the run folder, as in geo_filter.sbatch
#   LIMIT=2        only the first records (a test)
#   ONLY_IDS       a file of dataset ids, one per line: only those records
#                  (a test; not with LIMIT)
#   LUNARIS_LLM_CODE  the code folder (pipeline/lunaris), LUNARIS_MODELS_DIR
#                  the model store, as in fir/job_setup.sh
#
# Exit codes are place_pick_run.py's (0 done, 1 setup, 2 server broke,
# 3 part locked, 4 some prompts failed, 5 not on the GPU, 6 model did not
# load), plus 6 when copying to the node's disk or building Python failed.
#
# No passwords, keys or tokens are used or needed anywhere in this script.
# Nothing is downloaded and nothing is deleted.

#SBATCH --account=def-srivast
#SBATCH --job-name=place-pick
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --time=03:00:00

# Modules first: some module scripts trip over "set -u".
module load python/3.12

set -euo pipefail

source "${LUNARIS_LLM_CODE:?set by place_pick_submit.sh}/llm/fir/job_setup.sh"

: "${MODEL:?}" "${INPUT_DIR:?}" "${RUN_DIR:?}"
PARTS="${PARTS:-1}"
PART="${SLURM_ARRAY_TASK_ID:-0}"
NUM_CTX="${NUM_CTX:-16384}"
NUM_THREAD="${NUM_THREAD:-${SLURM_CPUS_PER_TASK:-0}}"
DRY_RUN="${DRY_RUN:-0}"
SERVER_PID=""

GPU_GIVEN=$(scontrol show job "${SLURM_JOB_ID:-0}" 2>/dev/null | grep -o 'gres/gpu:[^ ,]*' | head -1 || true)
echo "start    $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "host     $(hostname)  job ${SLURM_JOB_ID:-?}  part $PART of $PARTS"
echo "run      $RUN_DIR  model $MODEL  input $INPUT_DIR  context $NUM_CTX  threads $NUM_THREAD$([[ "$DRY_RUN" == 1 ]] && echo "  DRY RUN")"
echo "slice    ${GPU_GIVEN:-none}  cpus ${SLURM_CPUS_PER_TASK:-?}"

# Copy the code to the node's disk, and build Python there.
stage_code_and_python

ARGS=(--input "$INPUT_DIR" --out "$RUN_DIR" --model "$MODEL" --part "$PART" --parts "$PARTS" --num-ctx "$NUM_CTX")
if [[ -n "${LIMIT:-}" ]]; then
    ARGS+=(--limit "$LIMIT")
fi
if [[ -n "${ONLY_IDS:-}" ]]; then
    ARGS+=(--only-ids "$ONLY_IDS")
fi

if [[ "$DRY_RUN" == 1 ]]; then
    ARGS+=(--dry-run)
else
    if command -v nvidia-smi >/dev/null; then
        echo "gpu      $(timeout 60 nvidia-smi --query-gpu=name,driver_version --format=csv,noheader | head -1)"
    fi
    # Flash attention: the setting asked for, checked against (or recorded in)
    # the run folder, so one run never mixes settings (fir/flash_attention.sh).
    source "$CODE_DIR/llm/fir/flash_attention.sh"
    FLASH_ATTENTION=$(flash_attention_setting) \
        || fail_setup "OLLAMA_FLASH_ATTENTION ('$OLLAMA_FLASH_ATTENTION') must be 0, 1, true, false or unset"
    if ! flash_attention_check_record "$RUN_DIR" "$FLASH_ATTENTION" write; then
        echo "end      $(date -u +%Y-%m-%dT%H:%M:%SZ)  exit 1"
        exit 1
    fi
    # Copy the Ollama program and this model's store to the node's disk, and start Ollama.
    pick_port
    SERVER_LOG="$RUN_DIR/logs/ollama_${SLURM_JOB_ID:-local}_${PART}.log"
    stage_ollama
    export OLLAMA_NUM_PARALLEL=1
    start_ollama "$SERVER_LOG"
    # However the job ends, stop the server.
    finish() {
        if [[ -n "$SERVER_PID" ]]; then
            kill "$SERVER_PID" 2>/dev/null || true
            wait "$SERVER_PID" 2>/dev/null || true
        fi
    }
    trap finish EXIT
    ARGS+=(--ollama-url "http://$OLLAMA_HOST" --server-log "$SERVER_LOG" --load-timeout "${LOAD_TIMEOUT:-360}"
           --expect-flash-attn "$FLASH_ATTENTION")
    if [[ "$NUM_THREAD" != 0 ]]; then
        ARGS+=(--num-thread "$NUM_THREAD")
    fi
fi

set +e
python "$CODE_DIR/llm/place_pick_run.py" "${ARGS[@]}"
STATUS=$?
set -e
echo "end      $(date -u +%Y-%m-%dT%H:%M:%SZ)  exit $STATUS"
exit $STATUS
