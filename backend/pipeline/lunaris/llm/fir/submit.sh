#!/bin/bash
# Submit batches of a run to Fir, choosing the GPU slice to fit the work.
#
# Run it from inside the run folder, so the job logs land in its logs/ folder.
# The one argument is the array: which batches, and optionally how many at once.
#
#   cd ~/scratch/lunaris_llm/runs/test900
#   ~/lunaris_llm/code/fir/submit.sh 0                    # one record at a time, 1g.10gb
#   WORKERS=4 ~/lunaris_llm/code/fir/submit.sh 0          # four at a time, 2g.20gb
#   NUM_CTX=65536 ~/lunaris_llm/code/fir/submit.sh 0      # long records, 2g.20gb
#   ~/lunaris_llm/code/fir/submit.sh 0-40%4               # 41 batches, 4 at once
#   GPU=h100 ~/lunaris_llm/code/fir/submit.sh 0           # choose the GPU yourself
#
# How the slice is chosen. Ollama keeps a context of NUM_CTX tokens for each of
# the WORKERS slots, and qwen3.5:4b needs 32 KiB of KV cache per token (only 8
# of its 32 layers keep one). On top of that come about 3 to 4 GB of weights,
# about 1 GB of working buffers and about 0.5 GB for CUDA itself:
#
#   WORKERS x NUM_CTX    KV cache   about in all   slice
#   1 x 16,384           0.5 GiB    5 to 6 GB      1g.10gb   (10 GB)
#   4 x 16,384           2 GiB      7 to 8 GB      2g.20gb   (20 GB)
#   1 x 65,536           2 GiB      7 to 8 GB      2g.20gb   (20 GB)
#   more than 131,072    8+ GiB     14 GB or more  3g.40gb   (40 GB)
#
# The middle rows might squeeze into 10 GB, but with too little room to spare.
# If Ollama thinks the model does not fit, it quietly runs part of it on the
# CPU, which is far slower. Each job reports at the end ("gpu use") how much of
# the model was on the GPU, so the choice can be checked.
#
# CPUs and memory grow with the slice. Ollama needs little of either.

WORKERS="${WORKERS:-1}"
NUM_CTX="${NUM_CTX:-16384}"
ARRAY="${1:?give the batches to run, for example 0 or 0-40%4}"

# Both settings must be whole numbers, or the slice below would be chosen wrong.
if ! [[ "$WORKERS" =~ ^[1-9][0-9]*$ && "$NUM_CTX" =~ ^[1-9][0-9]*$ ]]; then
    echo "ERROR: WORKERS ('$WORKERS') and NUM_CTX ('$NUM_CTX') must be whole numbers."
    exit 1
fi
TOKENS=$((WORKERS * NUM_CTX))

# The slice, unless GPU was given.
if [[ -z "${GPU:-}" ]]; then
    if (( TOKENS <= 16384 )); then
        GPU=nvidia_h100_80gb_hbm3_1g.10gb
    elif (( TOKENS <= 131072 )); then
        GPU=nvidia_h100_80gb_hbm3_2g.20gb
    else
        GPU=nvidia_h100_80gb_hbm3_3g.40gb
    fi
fi

# CPUs and memory to go with it.
case "$GPU" in
    *1g.10gb) CPUS=2; MEM=8G ;;
    *2g.20gb) CPUS=4; MEM=16G ;;
    *3g.40gb) CPUS=6; MEM=32G ;;
    h100)     CPUS=8; MEM=64G ;;
    *) echo "ERROR: unknown GPU '$GPU'. Use h100 or nvidia_h100_80gb_hbm3_{1g.10gb,2g.20gb,3g.40gb}."; exit 1 ;;
esac

echo "submitting batches $ARRAY: WORKERS=$WORKERS NUM_CTX=$NUM_CTX on $GPU ($CPUS CPUs, $MEM)"
export WORKERS NUM_CTX
exec sbatch --array="$ARRAY" --gpus="$GPU:1" --cpus-per-task="$CPUS" --mem="$MEM" \
    "$(dirname "$0")/llm_array.sbatch"
