#!/bin/bash
# Submit batches of a run to Fir, choosing the GPU slice to fit the work.
#
# Run it from inside the run folder, so the job logs land in its logs/ folder.
# In the examples, B=/scratch/$USER/lunaris_llm.
# The one argument is the array: which batches, and optionally how many at once.
#
#   cd /scratch/$USER/lunaris_llm/runs/test900_x4
#   /scratch/$USER/lunaris_llm/code/fir/submit.sh 0-3                  # 4 batches at once, 1g.10gb each
#   $B/code/fir/submit.sh 0-53%18               # 54 batches, 18 at once
#   NUM_CTX=65536 $B/code/fir/submit.sh 0      # long records, 2g.20gb
#   EXCLUDE=fc10203 $B/code/fir/submit.sh 0-3  # leave out a node (comma list)
#   GPU=h100 $B/code/fir/submit.sh 0           # choose the GPU yourself
#   NUM_THREAD=0 $B/code/fir/submit.sh 0       # let Ollama pick its threads (as in run 1)
#   BACKEND=vllm $B/code/fir/submit.sh 0       # plan B: vLLM, 3g.40gb, 32 at once
#   TIME=06:00:00 $B/f2code/fir/submit.sh 0-56%8   # a longer time limit (default 3 hours)
#
# The model comes from the run folder's run.json. For qwen3.6:* models (the
# filter 2 rerun with qwen3.6:35b-a3b):
#   - the slice is 3g.40gb (6 CPUs, 32G), unless GPU is given;
#   - the model store is $B/bcf/models/<model> (":" written as "-"), unless
#     MODELS_DIR is given, and it must exist: nothing is downloaded;
#   - flash attention is turned off (OLLAMA_FLASH_ATTENTION=0), because Ollama
#     0.40.0 crashes these models on every prompt of 1,024 tokens or more with
#     it on (ollama issue #17434), and prompt v6 is longer than that. Setting
#     OLLAMA_FLASH_ATTENTION yourself overrides it, with a warning;
#   - copying the model to the node and loading it get more time.
# Every job records its flash attention setting in the run folder
# (fir/flash_attention.sh); a submit with another setting into the same run
# folder is refused, so one run never mixes settings.
#
# One request at a time only. Ollama always runs qwen3.5 (architecture qwen35)
# one request at a time, whatever OLLAMA_NUM_PARALLEL says, so WORKERS above 1
# only makes requests wait in line. Speed comes from running many batches at
# once, each on its own small slice. With Ollama, WORKERS above 1 is refused.
#
# How the slice is chosen. qwen3.5:4b needs 32 KiB of KV cache per token of
# context (only 8 of its 32 layers keep one). On top of that come about 3 to
# 4 GB of weights, about 1 GB of working buffers and about 0.5 GB for CUDA:
#
#   NUM_CTX              KV cache   about in all   slice
#   up to 16,384         0.5 GiB    5 to 6 GB      1g.10gb   (10 GB)
#   up to 131,072        2-4 GiB    7 to 10 GB     2g.20gb   (20 GB)
#   more                 4+ GiB     10 GB or more  3g.40gb   (40 GB)
#
# 65,536 might squeeze into 10 GB, but with too little room to spare: if
# Ollama thinks the model does not fit, it quietly runs part of it on the CPU.
# Each job checks this after its first answer and stops with exit code 5 if
# the model is not entirely on the GPU.
#
# CPUs and memory grow with the slice. Ollama itself needs little memory, but
# its runner keeps a prompt cache in memory of up to 8 GiB; with 8G on the
# 1g.10gb slice the job's memory limit killed the runner now and then (the
# BC filter's 4b run, 2026-10-27), so that slice gets 16G.

BACKEND="${BACKEND:-ollama}"
B="${LUNARIS_BASE:-${SCRATCH:-/scratch/$USER}/lunaris_llm}"
NUM_CTX="${NUM_CTX:-16384}"
NUM_THREAD="${NUM_THREAD:-}"   # empty: the job's CPU count (set below); 0: Ollama picks
ARRAY="${1:?give the batches to run, for example 0 or 0-53%18}"

# vLLM answers many records at once, so it gets 32 in flight by default.
if [[ "$BACKEND" == vllm ]]; then
    WORKERS="${WORKERS:-32}"
elif [[ "$BACKEND" == ollama ]]; then
    WORKERS="${WORKERS:-1}"
else
    echo "ERROR: BACKEND ('$BACKEND') must be ollama or vllm."
    exit 1
fi

# The settings must be whole numbers, or the slice below would be chosen wrong.
if ! [[ "$NUM_CTX" =~ ^[1-9][0-9]*$ && "$WORKERS" =~ ^[1-9][0-9]*$
        && ( -z "$NUM_THREAD" || "$NUM_THREAD" =~ ^[0-9]+$ ) ]]; then
    echo "ERROR: NUM_CTX ('$NUM_CTX'), WORKERS ('$WORKERS') and NUM_THREAD ('$NUM_THREAD') must be whole numbers."
    exit 1
fi
if [[ "$BACKEND" == ollama && "$WORKERS" != "1" ]]; then
    echo "ERROR: WORKERS=$WORKERS. Ollama runs qwen3.5 one request at a time, so only WORKERS=1"
    echo "       makes sense. To go faster, run more batches at once, for example 0-53%18."
    exit 1
fi

# The run's model, from run.json in this folder (the run folder).
if [[ ! -f run.json ]]; then
    echo "ERROR: no run.json here. Run this from inside the run folder."
    exit 1
fi
MODEL=$(grep -o '"model": *"[^"]*"' run.json | head -1 | sed 's/.*: *"\(.*\)"/\1/')
if [[ -z "$MODEL" ]]; then
    echo "ERROR: run.json names no model."
    exit 1
fi

# The slice, unless GPU was given. vLLM holds the full bf16 weights (about
# 9 GB) plus room for many records at once, so it starts at 3g.40gb. A
# qwen3.6 model (about 24 GB) needs 3g.40gb too.
if [[ -z "${GPU:-}" ]]; then
    if [[ "$MODEL" == qwen3.6:* ]]; then
        GPU=nvidia_h100_80gb_hbm3_3g.40gb
    elif [[ "$BACKEND" == vllm ]]; then
        GPU=nvidia_h100_80gb_hbm3_3g.40gb
    elif (( NUM_CTX <= 16384 )); then
        GPU=nvidia_h100_80gb_hbm3_1g.10gb
    elif (( NUM_CTX <= 131072 )); then
        GPU=nvidia_h100_80gb_hbm3_2g.20gb
    else
        GPU=nvidia_h100_80gb_hbm3_3g.40gb
    fi
fi

# CPUs and memory to go with it. CPUS=... overrides the CPU count, for
# example to test whether Ollama's answer writing is short of CPU.
case "$GPU" in
    *1g.10gb) DEFAULT_CPUS=2; MEM=16G ;;
    *2g.20gb) DEFAULT_CPUS=4; MEM=16G ;;
    *3g.40gb) DEFAULT_CPUS=6; MEM=32G ;;
    h100)     DEFAULT_CPUS=8; MEM=64G ;;
    *) echo "ERROR: unknown GPU '$GPU'. Use h100 or nvidia_h100_80gb_hbm3_{1g.10gb,2g.20gb,3g.40gb}."; exit 1 ;;
esac
CPUS="${CPUS:-$DEFAULT_CPUS}"
if ! [[ "$CPUS" =~ ^[1-9][0-9]*$ ]]; then
    echo "ERROR: CPUS ('$CPUS') must be a whole number."
    exit 1
fi

# Ollama's CPU threads: by default as many as the job has CPUs. Left to
# itself Ollama sizes its threads from the whole node (48 cores on Fir), and
# on 2 CPUs that made answers 5.7 times slower with the same answers
# (bench_decode: 10 vs 60 tokens/s). More CPUs did not help once this was set.
if [[ -z "$NUM_THREAD" && "$BACKEND" == ollama ]]; then
    NUM_THREAD="$CPUS"
fi

# A qwen3.6 model: its own store, flash attention off, more time to copy and
# load it (see the top of the file).
if [[ "$MODEL" == qwen3.6:* ]]; then
    MODELS_DIR="${MODELS_DIR:-$B/bcf/models/${MODEL//:/-}}"
    STAGE_TIMEOUT="${STAGE_TIMEOUT:-1800}"
    LOAD_TIMEOUT="${LOAD_TIMEOUT:-900}"
    if [[ -z "${OLLAMA_FLASH_ATTENTION+set}" ]]; then
        export OLLAMA_FLASH_ATTENTION=0
        echo "note     $MODEL: OLLAMA_FLASH_ATTENTION=0 set by default, so flash attention is off (ollama issue #17434)"
    else
        echo "WARNING  $MODEL: OLLAMA_FLASH_ATTENTION=$OLLAMA_FLASH_ATTENTION was set on purpose and is kept. With flash"
        echo "         attention on, this model crashes on prompts of 1,024 tokens or more (ollama issue #17434)."
    fi
fi
if [[ -n "${MODELS_DIR:-}" ]]; then
    if [[ ! -d "$MODELS_DIR/manifests" ]]; then
        echo "ERROR: no Ollama model store at $MODELS_DIR. This script never downloads a model."
        exit 1
    fi
    export LUNARIS_MODELS_DIR="$MODELS_DIR"
fi
if [[ -n "${STAGE_TIMEOUT:-}" ]]; then
    export STAGE_TIMEOUT
fi
if [[ -n "${LOAD_TIMEOUT:-}" ]]; then
    export LOAD_TIMEOUT
fi
if [[ -n "${LIMIT:-}" ]]; then
    export LIMIT
fi

# Flash attention: what OLLAMA_FLASH_ATTENTION asks for, checked against the
# run folder's record (written by the first job), so one run never mixes settings.
source "$(dirname "$0")/flash_attention.sh"
if ! FLASH_ATTENTION=$(flash_attention_setting); then
    echo "ERROR: OLLAMA_FLASH_ATTENTION ('$OLLAMA_FLASH_ATTENTION') must be 0, 1, true, false or unset."
    exit 1
fi
if [[ "$BACKEND" == ollama ]]; then
    flash_attention_check_record "$PWD" "$FLASH_ATTENTION" check || exit 1
fi

# A different time limit, for example TIME=06:00:00. Over 3 hours lands in a
# partition that may start later.
if [[ -n "${TIME:-}" ]]; then
    if ! [[ "$TIME" =~ ^[0-9]{1,2}:[0-9]{2}:[0-9]{2}$ ]]; then
        echo "ERROR: TIME ('$TIME') should look like 06:00:00."
        exit 1
    fi
fi

# Nodes to leave out, for example ones where Ollama's GPU discovery timed out
# (status.sh lists them). A comma-separated list of node names.
EXTRA=()
if [[ -n "${EXCLUDE:-}" ]]; then
    if ! [[ "$EXCLUDE" =~ ^[A-Za-z0-9,._-]+$ ]]; then
        echo "ERROR: EXCLUDE ('$EXCLUDE') should be node names separated by commas."
        exit 1
    fi
    EXTRA+=(--exclude="$EXCLUDE")
fi

if [[ -n "${TIME:-}" ]]; then
    EXTRA+=(--time="$TIME")
fi
echo "submitting batches $ARRAY: $MODEL, $BACKEND, WORKERS=$WORKERS NUM_CTX=$NUM_CTX${NUM_THREAD:+ NUM_THREAD=$NUM_THREAD} on $GPU ($CPUS CPUs, $MEM)${EXCLUDE:+, leaving out $EXCLUDE}${TIME:+, time limit $TIME}"
if [[ "$BACKEND" == ollama ]]; then
    echo "         flash attention $FLASH_ATTENTION${MODELS_DIR:+, model store $MODELS_DIR}"
fi
if [[ "$NUM_THREAD" == 0 ]]; then
    echo "         NUM_THREAD=0: Ollama picks its own thread count (about 5.7 times slower on 2 CPUs)"
fi
# The job copies its code from the folder this script lives in, written as
# a physical path so it does not go through the ~/scratch link on /home.
LUNARIS_LLM_CODE="$(cd "$(dirname "$0")/.." && pwd -P)"
export BACKEND WORKERS NUM_CTX NUM_THREAD LUNARIS_LLM_CODE
exec sbatch --array="$ARRAY" --gpus="$GPU:1" --cpus-per-task="$CPUS" --mem="$MEM" "${EXTRA[@]}" \
    "$(dirname "$0")/llm_array.sbatch"
