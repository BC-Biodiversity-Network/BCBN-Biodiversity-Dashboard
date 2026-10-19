#!/bin/bash
# Submit batches of a run to Fir, choosing the GPU slice to fit the work.
#
# Run it from inside the run folder, so the job logs land in its logs/ folder.
# The one argument is the array: which batches, and optionally how many at once.
#
#   cd ~/scratch/lunaris_llm/runs/test900_x4
#   ~/lunaris_llm/code/fir/submit.sh 0-3                  # 4 batches at once, 1g.10gb each
#   ~/lunaris_llm/code/fir/submit.sh 0-268%16             # 269 batches, 16 at once
#   NUM_CTX=65536 ~/lunaris_llm/code/fir/submit.sh 0      # long records, 2g.20gb
#   EXCLUDE=fc10203 ~/lunaris_llm/code/fir/submit.sh 0-3  # leave out a node (comma list)
#   GPU=h100 ~/lunaris_llm/code/fir/submit.sh 0           # choose the GPU yourself
#   CPUS=8 NUM_THREAD=8 ~/lunaris_llm/code/fir/submit.sh 0  # more CPUs, and tell Ollama
#   BACKEND=vllm ~/lunaris_llm/code/fir/submit.sh 0       # plan B: vLLM, 3g.40gb, 32 at once
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
# CPUs and memory grow with the slice. Ollama needs little of either.

BACKEND="${BACKEND:-ollama}"
NUM_CTX="${NUM_CTX:-16384}"
NUM_THREAD="${NUM_THREAD:-}"
ARRAY="${1:?give the batches to run, for example 0 or 0-268%16}"

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
        && ( -z "$NUM_THREAD" || "$NUM_THREAD" =~ ^[1-9][0-9]*$ ) ]]; then
    echo "ERROR: NUM_CTX ('$NUM_CTX'), WORKERS ('$WORKERS') and NUM_THREAD ('$NUM_THREAD') must be whole numbers."
    exit 1
fi
if [[ "$BACKEND" == ollama && "$WORKERS" != "1" ]]; then
    echo "ERROR: WORKERS=$WORKERS. Ollama runs qwen3.5 one request at a time, so only WORKERS=1"
    echo "       makes sense. To go faster, run more batches at once, for example 0-268%16."
    exit 1
fi

# The slice, unless GPU was given. vLLM holds the full bf16 weights (about
# 9 GB) plus room for many records at once, so it starts at 3g.40gb.
if [[ -z "${GPU:-}" ]]; then
    if [[ "$BACKEND" == vllm ]]; then
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
    *1g.10gb) DEFAULT_CPUS=2; MEM=8G ;;
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

echo "submitting batches $ARRAY: $BACKEND, WORKERS=$WORKERS NUM_CTX=$NUM_CTX${NUM_THREAD:+ NUM_THREAD=$NUM_THREAD} on $GPU ($CPUS CPUs, $MEM)${EXCLUDE:+, leaving out $EXCLUDE}"
export BACKEND WORKERS NUM_CTX NUM_THREAD
exec sbatch --array="$ARRAY" --gpus="$GPU:1" --cpus-per-task="$CPUS" --mem="$MEM" "${EXTRA[@]}" \
    "$(dirname "$0")/llm_array.sbatch"
