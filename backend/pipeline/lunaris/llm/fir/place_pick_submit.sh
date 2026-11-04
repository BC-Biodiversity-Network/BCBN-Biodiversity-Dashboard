#!/bin/bash
# Submit the place lookup's model step (fir/place_pick.sbatch) for one model
# and one input folder, choosing the GPU slice to fit the model.
#
# In the examples, B=/scratch/$USER/lunaris_llm. Arguments: the model, then
# the input folder's name under $B/places/inputs.
#
#   DRY_RUN=1 $B/places/code/llm/fir/place_pick_submit.sh qwen3.6:35b-a3b places_p1
#   LIMIT=2 $B/places/code/llm/fir/place_pick_submit.sh qwen3.6:35b-a3b places_p1
#   ONLY_IDS=$B/places/inputs/test_lists/hard_cases.txt $B/places/code/llm/fir/place_pick_submit.sh qwen3.6:35b-a3b places_p1
#   PARTS=4 $B/places/code/llm/fir/place_pick_submit.sh qwen3.6:35b-a3b places_p1
#
# The run folder is $B/places/runs/<input>_<model>, with ":" in the model
# name written as "-" (places_p1_qwen3.6-35b-a3b). Submitting again into the
# same folder only asks the prompts that have no answer yet. Use the same
# PARTS when resubmitting; ONLY=3,5 resubmits only those parts. A test with
# LIMIT=2 goes to its own folder (..._limit2), so it never mixes with the run.
# A test on chosen records, ONLY_IDS=<file of dataset ids, one per line>,
# goes to its own folder too, named after the file (..._only-hard_cases).
# Give LIMIT or ONLY_IDS, not both.
#
# The model store is the BC filter's, $B/bcf/models/<model> (MODELS_DIR=...
# uses another one). Nothing is downloaded here or in the job: a missing
# store stops this script, and a missing model stops the job before any
# prompt.
#
# The slices, as in geo_filter_submit.sh:
#   qwen3.5:9b        2g.20gb    4 CPUs  16G
#   qwen3.6:35b-a3b   3g.40gb    6 CPUs  32G
# Any other model needs GPU=... A dry run asks for no GPU.
#
# Flash attention (fir/flash_attention.sh): off by default for qwen3.6:*
# models (OLLAMA_FLASH_ATTENTION=0), because Ollama 0.40.0 crashes them on
# prompts of 1,024 tokens or more with it on (ollama issue #17434). The
# first job of a run records the setting in the run folder; a later submit
# with another setting into the same folder is refused.

MODEL="${1:?give the model, for example qwen3.6:35b-a3b}"
INPUT="${2:?give the input folder name under \$B/places/inputs, for example places_p1}"
B="${LUNARIS_BASE:-/scratch/$USER/lunaris_llm}"
SAFE_MODEL="${MODEL//:/-}"
INPUT_DIR="$B/places/inputs/$INPUT"
ONLY_NAME=""
if [[ -n "${ONLY_IDS:-}" ]]; then
    if [[ -n "${LIMIT:-}" ]]; then
        echo "ERROR: give LIMIT or ONLY_IDS, not both."
        exit 1
    fi
    if [[ ! -f "$ONLY_IDS" ]]; then
        echo "ERROR: no test list at $ONLY_IDS (copy it from the Mac first)."
        exit 1
    fi
    # The job reads the list by its full path.
    ONLY_IDS="$(cd "$(dirname "$ONLY_IDS")" && pwd -P)/$(basename "$ONLY_IDS")"
    ONLY_NAME="$(basename "$ONLY_IDS" .txt)"
    if ! [[ "$ONLY_NAME" =~ ^[A-Za-z0-9._-]+$ ]]; then
        echo "ERROR: the test list's name ('$ONLY_NAME') should be letters, digits, '.', '_' or '-'."
        exit 1
    fi
fi
RUN_DIR="${RUN_DIR:-$B/places/runs/${INPUT}_${SAFE_MODEL}${LIMIT:+_limit$LIMIT}${ONLY_NAME:+_only-$ONLY_NAME}}"
PARTS="${PARTS:-1}"
DRY_RUN="${DRY_RUN:-0}"
NUM_CTX="${NUM_CTX:-16384}"

if ! [[ "$PARTS" =~ ^[1-9][0-9]*$ && "$NUM_CTX" =~ ^[1-9][0-9]*$ ]]; then
    echo "ERROR: PARTS ('$PARTS') and NUM_CTX ('$NUM_CTX') must be whole numbers."
    exit 1
fi
if [[ -n "${LIMIT:-}" && ! "$LIMIT" =~ ^[1-9][0-9]*$ ]]; then
    echo "ERROR: LIMIT ('$LIMIT') must be a whole number."
    exit 1
fi
if [[ ! -f "$INPUT_DIR/items.jsonl.gz" ]]; then
    echo "ERROR: no input at $INPUT_DIR (copy it from the Mac first)."
    exit 1
fi

# The slice and its CPUs and memory, by model, unless GPU is given.
if [[ -z "${GPU:-}" ]]; then
    case "$MODEL" in
        qwen3.5:9b)      GPU=nvidia_h100_80gb_hbm3_2g.20gb ;;
        qwen3.6:35b-a3b) GPU=nvidia_h100_80gb_hbm3_3g.40gb ;;
        *) echo "ERROR: no slice known for $MODEL; give GPU=..."; exit 1 ;;
    esac
fi
case "$GPU" in
    *2g.20gb) CPUS=4; MEM=16G ;;
    *3g.40gb) CPUS=6; MEM=32G ;;
    h100)     CPUS=8; MEM=64G ;;
    *) echo "ERROR: unknown GPU '$GPU'. Use h100 or nvidia_h100_80gb_hbm3_{2g.20gb,3g.40gb}."; exit 1 ;;
esac
NUM_THREAD="${NUM_THREAD:-$CPUS}"

# A big model takes longer to copy to the node and to load.
if [[ "$GPU" == *3g.40gb || "$GPU" == h100 ]]; then
    STAGE_TIMEOUT="${STAGE_TIMEOUT:-1800}"
    LOAD_TIMEOUT="${LOAD_TIMEOUT:-900}"
fi

MODELS_DIR="${MODELS_DIR:-$B/bcf/models/$SAFE_MODEL}"
if [[ "$DRY_RUN" != 1 && ! -d "$MODELS_DIR/manifests" ]]; then
    echo "ERROR: no Ollama model store at $MODELS_DIR. This script never downloads a model."
    exit 1
fi

# Flash attention: off by default for qwen3.6:* (see the top of the file).
source "$(dirname "$0")/flash_attention.sh"
if [[ "$MODEL" == qwen3.6:* ]]; then
    if [[ -z "${OLLAMA_FLASH_ATTENTION+set}" ]]; then
        export OLLAMA_FLASH_ATTENTION=0
        echo "note     $MODEL: OLLAMA_FLASH_ATTENTION=0 set by default, so flash attention is off (ollama issue #17434)"
    else
        echo "WARNING  $MODEL: OLLAMA_FLASH_ATTENTION=$OLLAMA_FLASH_ATTENTION was set on purpose and is kept."
    fi
fi
if ! FLASH_ATTENTION=$(flash_attention_setting); then
    echo "ERROR: OLLAMA_FLASH_ATTENTION ('$OLLAMA_FLASH_ATTENTION') must be 0, 1, true, false or unset."
    exit 1
fi
if [[ "$DRY_RUN" != 1 ]]; then
    flash_attention_check_record "$RUN_DIR" "$FLASH_ATTENTION" check || exit 1
fi

EXTRA=()
if [[ -n "${EXCLUDE:-}" ]]; then
    if ! [[ "$EXCLUDE" =~ ^[A-Za-z0-9,._-]+$ ]]; then
        echo "ERROR: EXCLUDE ('$EXCLUDE') should be node names separated by commas."
        exit 1
    fi
    EXTRA+=(--exclude="$EXCLUDE")
fi
if [[ -n "${ONLY:-}" ]]; then
    if ! [[ "$ONLY" =~ ^[0-9]+([,-][0-9]+)*$ ]]; then
        echo "ERROR: ONLY ('$ONLY') should be part numbers, such as 3 or 3,5 or 0-2."
        exit 1
    fi
    for part in $(echo "$ONLY" | tr ',-' '  '); do
        if (( part >= PARTS )); then
            echo "ERROR: part $part is not one of the $PARTS parts (0 to $((PARTS - 1)))."
            exit 1
        fi
    done
fi
ARRAY="${ONLY:-0-$((PARTS - 1))}${MAX_AT_ONCE:+%$MAX_AT_ONCE}"
if [[ "$DRY_RUN" == 1 ]]; then
    # No GPU, no Ollama: two CPUs are enough to rebuild the prompts.
    EXTRA+=(--cpus-per-task=2 --mem=8G --time=00:30:00)
else
    EXTRA+=(--gpus="$GPU:1" --cpus-per-task="$CPUS" --mem="$MEM")
fi

mkdir -p "$RUN_DIR/logs"
# The job copies its code from the folder this script lives in (pipeline/lunaris
# on Fir), written as a physical path so it does not go through ~/scratch on /home.
LUNARIS_LLM_CODE="$(cd "$(dirname "$0")/../.." && pwd -P)"
LUNARIS_MODELS_DIR="$MODELS_DIR"
export MODEL INPUT_DIR RUN_DIR PARTS DRY_RUN NUM_CTX NUM_THREAD LUNARIS_LLM_CODE LUNARIS_MODELS_DIR \
    LUNARIS_BASE="$B"
if [[ -n "${STAGE_TIMEOUT:-}" ]]; then
    export STAGE_TIMEOUT
fi
if [[ -n "${LOAD_TIMEOUT:-}" ]]; then
    export LOAD_TIMEOUT
fi
if [[ -n "${LIMIT:-}" ]]; then
    export LIMIT
fi
if [[ -n "${ONLY_IDS:-}" ]]; then
    export ONLY_IDS
fi

echo "submitting $MODEL on $INPUT ($PARTS part(s), array $ARRAY) into $RUN_DIR"
if [[ -n "${ONLY_IDS:-}" ]]; then
    echo "         only the records listed in $ONLY_IDS"
fi
if [[ "$DRY_RUN" == 1 ]]; then
    echo "         DRY RUN: no GPU, no Ollama, nothing asked"
else
    echo "         $GPU ($CPUS CPUs, $MEM), NUM_THREAD=$NUM_THREAD, flash attention $FLASH_ATTENTION, model store $MODELS_DIR${EXCLUDE:+, leaving out $EXCLUDE}"
fi
exec sbatch --array="$ARRAY" --output="$RUN_DIR/logs/%x_%A_%a.out" "${EXTRA[@]}" \
    "$(dirname "$0")/place_pick.sbatch"
