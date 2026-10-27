#!/bin/bash
# Run the BC filter's two LLM passes on Fir (llm/geo_filter_run.py): pass 1 v3,
# the GeoNames lookup and pass 2 C4, for one model and one record list. One job
# per part of the records; an array splits a long run into parts.
#
# Submit it with fir/geo_filter_submit.sh, which picks the GPU slice for the
# model, the run folder and the model store, and passes them on. A dry run
# (DRY_RUN=1) asks no model, starts no Ollama and needs no GPU.
#
# It reuses the v6 run's setup (fir/job_setup.sh): the code, Python, the
# Ollama program and the model are copied to the node's own disk, Python is
# built there from the Alliance wheels (here with shapely and pyproj as
# well, for the box notes), and every step has a time limit (exit 6 if one
# fails). The GeoNames table is copied to the node's disk too. The record
# list is read from /scratch, and results and logs are written to the run
# folder on /scratch as they come.
#
# Settings, exported by geo_filter_submit.sh (or set before sbatch):
#   MODEL          the Ollama model name, such as qwen3.5:4b
#   INPUT_DIR      the input folder from geo_filter_prepare.py
#   RUN_DIR        the run folder for this model
#   PARTS          how many parts the records are split into (array size)
#   GEONAMES_DIR   folder with geonames_lookup.parquet and geonames_codes.json
#   BC_BOUNDARY    bc_boundary_gbif.wkt
#   NUM_CTX, NUM_THREAD, LOAD_TIMEOUT   as in llm_array.sbatch
#   DRY_RUN=1      check and rebuild the prompts only
#   REFERENCE_PASS1, REFERENCE_PASS2   dry run: the Mac's run folders, to
#                  check the prompts byte for byte
#   OLLAMA_FLASH_ATTENTION   0 turns flash attention off (the default for
#                  qwen3.6:* models, set by the submit script). The job records
#                  the setting in the run folder, refuses one that differs from
#                  the record, and the run script stops before any record if
#                  the runner did not start with the setting asked for.
#   GEO_MODULES    extra modules to load (not needed: PROJ_DATA is found by
#                  llm/geo_env_check.py; a proj module given here wins)
#   LIMIT=2        only the first records (a test)
#   LUNARIS_LLM_CODE  the code folder (pipeline/lunaris), LUNARIS_MODELS_DIR
#                  the model store, as in fir/job_setup.sh
#
# Exit codes are geo_filter_run.py's (0 done, 1 setup, 2 server broke,
# 3 part locked, 4 some records failed, 5 not on the GPU, 6 model did not
# load, 7 dry run found prompts that differ from the Mac's), plus 6 when
# copying to the node's disk or building Python there failed.
#
# No passwords, keys or tokens are used or needed anywhere in this script.
# Nothing is downloaded and nothing is deleted.

#SBATCH --account=def-srivast
#SBATCH --job-name=bc-filter
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --time=03:00:00

# Modules first: some module scripts trip over "set -u".
module load python/3.12 ${GEO_MODULES:-}

set -euo pipefail

source "${LUNARIS_LLM_CODE:?set by geo_filter_submit.sh}/llm/fir/job_setup.sh"

: "${MODEL:?}" "${INPUT_DIR:?}" "${RUN_DIR:?}" "${GEONAMES_DIR:?}" "${BC_BOUNDARY:?}"
PARTS="${PARTS:-1}"
PART="${SLURM_ARRAY_TASK_ID:-0}"
NUM_CTX="${NUM_CTX:-16384}"
NUM_THREAD="${NUM_THREAD:-${SLURM_CPUS_PER_TASK:-0}}"
DRY_RUN="${DRY_RUN:-0}"
export EXTRA_WHEELS="${EXTRA_WHEELS:-shapely pyproj}"
SERVER_PID=""

GPU_GIVEN=$(scontrol show job "${SLURM_JOB_ID:-0}" 2>/dev/null | grep -o 'gres/gpu:[^ ,]*' | head -1 || true)
echo "start    $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "host     $(hostname)  job ${SLURM_JOB_ID:-?}  part $PART of $PARTS"
echo "run      $RUN_DIR  model $MODEL  input $INPUT_DIR  context $NUM_CTX  threads $NUM_THREAD$([[ "$DRY_RUN" == 1 ]] && echo "  DRY RUN")"
echo "slice    ${GPU_GIVEN:-none}  cpus ${SLURM_CPUS_PER_TASK:-?}"

# Copy the code and the GeoNames table to the node's disk, and build Python there.
stage_code_and_python
stage_copy "GeoNames table" "$GEONAMES_DIR" "$STAGE/geonames"
cp "$BC_BOUNDARY" "$STAGE/bc_boundary.wkt" || fail_setup "BC outline not found at $BC_BOUNDARY"

# pyproj's data. The Alliance pyproj wheel finds its PROJ library on CVMFS but
# not that library's data folder (proj.db). The data sits next to the library,
# in ../share/proj, which is also where the matching proj module points, so
# PROJ_DATA is set to it and always matches the library. A PROJ_DATA already
# set (for example by a proj module in GEO_MODULES) is kept.
if [[ -z "${PROJ_DATA:-}" ]]; then
    PROJ_FOUND=$(timeout 120 python "$CODE_DIR/llm/geo_env_check.py" --proj-data 2>/dev/null || true)
    if [[ -n "$PROJ_FOUND" ]]; then
        export PROJ_DATA="$PROJ_FOUND"
    fi
fi
# Check the packages before anything else: versions, pyproj's data, and that a
# projected point and three box shares are the Mac's. A missing piece stops
# the job here, in seconds, with exit code 6.
timeout 300 python "$CODE_DIR/llm/geo_env_check.py" --bc-boundary "$STAGE/bc_boundary.wkt" \
    || fail_setup "the Python packages are not ready for the BC filter (see the check lines above)"

ARGS=(--input "$INPUT_DIR" --out "$RUN_DIR" --bc-boundary "$STAGE/bc_boundary.wkt"
      --table "$STAGE/geonames/geonames_lookup.parquet" --codes "$STAGE/geonames/geonames_codes.json"
      --model "$MODEL" --part "$PART" --parts "$PARTS" --num-ctx "$NUM_CTX")
if [[ -n "${LIMIT:-}" ]]; then
    ARGS+=(--limit "$LIMIT")
fi

if [[ "$DRY_RUN" == 1 ]]; then
    ARGS+=(--dry-run)
    if [[ -n "${REFERENCE_PASS1:-}" ]]; then
        ARGS+=(--reference-pass1 "$REFERENCE_PASS1")
    fi
    if [[ -n "${REFERENCE_PASS2:-}" ]]; then
        ARGS+=(--reference-pass2 "$REFERENCE_PASS2")
    fi
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
python "$CODE_DIR/llm/geo_filter_run.py" "${ARGS[@]}"
STATUS=$?
set -e
echo "end      $(date -u +%Y-%m-%dT%H:%M:%SZ)  exit $STATUS"
exit $STATUS
