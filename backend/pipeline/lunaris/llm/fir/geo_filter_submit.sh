#!/bin/bash
# Submit the BC filter's LLM run (fir/geo_filter.sbatch) for one model and one
# input folder, choosing the GPU slice to fit the model.
#
# In the examples, B=/scratch/$USER/lunaris_llm. Arguments: the model, then
# the input folder's name under $B/bcf/inputs.
#
#   $B/bcf/code/llm/fir/geo_filter_submit.sh qwen3.5:4b label150
#   DRY_RUN=1 $B/bcf/code/llm/fir/geo_filter_submit.sh qwen3.5:4b label150
#   PARTS=10 $B/bcf/code/llm/fir/geo_filter_submit.sh qwen3.5:4b needs_llm   # 10 parts at once
#   EXCLUDE=fc10203 $B/bcf/code/llm/fir/geo_filter_submit.sh gemma4:e4b label150
#   GPU=nvidia_h100_80gb_hbm3_3g.40gb $B/bcf/code/llm/fir/geo_filter_submit.sh qwen3.5:9b label150
#
# The run folder is $B/bcf/runs/<input>_<model>, with ":" in the model name
# written as "-" (label150_qwen3.5-4b). Submitting again into the same folder
# only does the records that have no answer yet.
#
# The model store. Each model has its own Ollama model folder, so a job
# copies only its own model to the node: $B/bcf/models/<model>, except
# qwen3.5:4b, which uses the v6 run's store, $B/models. MODELS_DIR=... uses
# another one. Nothing is downloaded here or in the job: a missing store
# stops this script, and a missing model stops the job before any record.
#
# How the slice is chosen (download sizes of the GGUF builds Fir pulls; the
# memory adds about 1 to 2 GB for a 16,384-token context and working buffers):
#
#   model             download   slice      CPUs  memory
#   qwen3.5:4b        3.3 GB     1g.10gb    2     8G      (as in the v6 run)
#   qwen3.5:9b        6.6 GB     2g.20gb    4     16G     (too tight for 10 GB)
#   gemma4:e4b        6.6 GB     2g.20gb    4     16G     (too tight for 10 GB)
#   qwen3.6:35b-a3b   24 GB      3g.40gb    6     32G
#
# Any other model needs GPU=... A dry run (DRY_RUN=1) asks for no GPU.
# NUM_THREAD defaults to the job's CPU count, as in the v6 run.
#
# PARTS=N splits the records into N parts, one array task each, MAX_AT_ONCE
# at a time (default all). Use the same PARTS when resubmitting a run.

MODEL="${1:?give the model, for example qwen3.5:4b}"
INPUT="${2:?give the input folder name under \$B/bcf/inputs, for example label150}"
B="${LUNARIS_BASE:-/scratch/$USER/lunaris_llm}"
SAFE_MODEL="${MODEL//:/-}"
INPUT_DIR="$B/bcf/inputs/$INPUT"
RUN_DIR="${RUN_DIR:-$B/bcf/runs/${INPUT}_${SAFE_MODEL}}"
PARTS="${PARTS:-1}"
DRY_RUN="${DRY_RUN:-0}"
NUM_CTX="${NUM_CTX:-16384}"

if ! [[ "$PARTS" =~ ^[1-9][0-9]*$ && "$NUM_CTX" =~ ^[1-9][0-9]*$ ]]; then
    echo "ERROR: PARTS ('$PARTS') and NUM_CTX ('$NUM_CTX') must be whole numbers."
    exit 1
fi
if [[ ! -f "$INPUT_DIR/records.jsonl.gz" ]]; then
    echo "ERROR: no input at $INPUT_DIR (copy it from the Mac first)."
    exit 1
fi

# The slice and its CPUs and memory, by model, unless GPU is given.
if [[ -z "${GPU:-}" ]]; then
    case "$MODEL" in
        qwen3.5:4b)      GPU=nvidia_h100_80gb_hbm3_1g.10gb ;;
        qwen3.5:9b)      GPU=nvidia_h100_80gb_hbm3_2g.20gb ;;
        gemma4:e4b)      GPU=nvidia_h100_80gb_hbm3_2g.20gb ;;
        qwen3.6:35b-a3b) GPU=nvidia_h100_80gb_hbm3_3g.40gb ;;
        *) echo "ERROR: no slice known for $MODEL; give GPU=..."; exit 1 ;;
    esac
fi
case "$GPU" in
    *1g.10gb) CPUS=2; MEM=8G ;;
    *2g.20gb) CPUS=4; MEM=16G ;;
    *3g.40gb) CPUS=6; MEM=32G ;;
    h100)     CPUS=8; MEM=64G ;;
    *) echo "ERROR: unknown GPU '$GPU'. Use h100 or nvidia_h100_80gb_hbm3_{1g.10gb,2g.20gb,3g.40gb}."; exit 1 ;;
esac
NUM_THREAD="${NUM_THREAD:-$CPUS}"

# A big model takes longer to copy to the node and to load.
if [[ "$GPU" == *3g.40gb || "$GPU" == h100 ]]; then
    STAGE_TIMEOUT="${STAGE_TIMEOUT:-1800}"
    LOAD_TIMEOUT="${LOAD_TIMEOUT:-900}"
fi

# This model's store.
if [[ -z "${MODELS_DIR:-}" ]]; then
    if [[ "$MODEL" == qwen3.5:4b ]]; then
        MODELS_DIR="$B/models"
    else
        MODELS_DIR="$B/bcf/models/$SAFE_MODEL"
    fi
fi
if [[ "$DRY_RUN" != 1 && ! -d "$MODELS_DIR/manifests" ]]; then
    echo "ERROR: no Ollama model store at $MODELS_DIR. Pull $MODEL into it first, only after Lucia"
    echo "       approves (fir/README.md, section 8, step B)."
    exit 1
fi

EXTRA=()
if [[ -n "${EXCLUDE:-}" ]]; then
    if ! [[ "$EXCLUDE" =~ ^[A-Za-z0-9,._-]+$ ]]; then
        echo "ERROR: EXCLUDE ('$EXCLUDE') should be node names separated by commas."
        exit 1
    fi
    EXTRA+=(--exclude="$EXCLUDE")
fi
ARRAY="0-$((PARTS - 1))${MAX_AT_ONCE:+%$MAX_AT_ONCE}"
if [[ "$DRY_RUN" == 1 ]]; then
    # No GPU, no Ollama: two CPUs are enough to rebuild the prompts and open the lookup.
    EXTRA+=(--cpus-per-task=2 --mem=8G --time=00:30:00)
else
    EXTRA+=(--gpus="$GPU:1" --cpus-per-task="$CPUS" --mem="$MEM")
fi

mkdir -p "$RUN_DIR/logs"
# The job copies its code from the folder this script lives in (pipeline/lunaris
# on Fir), written as a physical path so it does not go through ~/scratch on /home.
LUNARIS_LLM_CODE="$(cd "$(dirname "$0")/../.." && pwd -P)"
LUNARIS_MODELS_DIR="$MODELS_DIR"
GEONAMES_DIR="${GEONAMES_DIR:-$B/bcf/geonames}"
BC_BOUNDARY="${BC_BOUNDARY:-$B/bcf/bc_boundary_gbif.wkt}"
export MODEL INPUT_DIR RUN_DIR PARTS DRY_RUN NUM_CTX NUM_THREAD LUNARIS_LLM_CODE LUNARIS_MODELS_DIR \
    GEONAMES_DIR BC_BOUNDARY LUNARIS_BASE="$B"
if [[ -n "${STAGE_TIMEOUT:-}" ]]; then
    export STAGE_TIMEOUT
fi
if [[ -n "${LOAD_TIMEOUT:-}" ]]; then
    export LOAD_TIMEOUT
fi
if [[ -n "${LIMIT:-}" ]]; then
    export LIMIT
fi

echo "submitting $MODEL on $INPUT ($PARTS part(s), array $ARRAY) into $RUN_DIR"
if [[ "$DRY_RUN" == 1 ]]; then
    echo "         DRY RUN: no GPU, no Ollama, nothing asked"
else
    echo "         $GPU ($CPUS CPUs, $MEM), NUM_THREAD=$NUM_THREAD, model store $MODELS_DIR${EXCLUDE:+, leaving out $EXCLUDE}"
fi
exec sbatch --array="$ARRAY" --output="$RUN_DIR/logs/%x_%A_%a.out" "${EXTRA[@]}" \
    "$(dirname "$0")/geo_filter.sbatch"
