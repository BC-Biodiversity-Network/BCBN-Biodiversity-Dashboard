# Flash attention for the BC filter's jobs: what OLLAMA_FLASH_ATTENTION asks
# for, and a record of it in the run folder so one run never mixes settings.
# Sourced by fir/geo_filter_submit.sh and fir/geo_filter.sbatch, not run on
# its own.
#
# Why. Ollama 0.40.0 runs qwen3.6 (MoE) models with flash attention on by
# default ("--flash-attn auto"), and then crashes on every prompt of 1,024
# tokens or more (ollama issue #17434). With OLLAMA_FLASH_ATTENTION=0 the
# runner starts with "--flash-attn off" and the crash is gone. Flash
# attention on or off can change answers slightly, so every record of a run
# must use the same setting.
#
# The record is the file flash_attention.txt in the run folder: its first
# line is the setting (off, on or auto), the second says when and how it was
# recorded. The first job of a run writes it; later jobs and submits must match.

# Print the setting OLLAMA_FLASH_ATTENTION asks for: "auto" when it is not
# set or empty (Ollama's own default), "off" for 0 or false, "on" for 1 or
# true. Return 1 for any other value.
flash_attention_setting() {
    case "${OLLAMA_FLASH_ATTENTION-}" in
        "") echo auto ;;
        0|false|FALSE|False) echo off ;;
        1|true|TRUE|True) echo on ;;
        *) return 1 ;;
    esac
}

# Print the setting recorded in a run folder, or nothing if none is recorded.
# Argument: the run folder.
flash_attention_recorded() {
    if [[ -f "$1/flash_attention.txt" ]]; then
        head -1 "$1/flash_attention.txt"
    fi
}

# Check a setting against a run folder's record, and write the record if
# there is none yet and the third argument is "write". Print a line saying
# what happened. Return 1, with an ERROR line, if the folder already records
# a different setting. Arguments: run folder, setting, "write" or "check".
flash_attention_check_record() {
    local recorded
    recorded=$(flash_attention_recorded "$1")
    if [[ -n "$recorded" && "$recorded" != "$2" ]]; then
        echo "ERROR: $1 was run with flash attention $recorded, but this job would use $2"
        echo "       (OLLAMA_FLASH_ATTENTION=${OLLAMA_FLASH_ATTENTION-<not set>}). One run must not mix settings:"
        echo "       submit with the run's own setting, or use a new run folder (RUN_DIR=...)."
        return 1
    fi
    if [[ -n "$recorded" ]]; then
        echo "flash    attention $2, as recorded for this run"
    elif [[ "$3" == write ]]; then
        mkdir -p "$1"
        printf '%s\nrecorded by job %s on %s: OLLAMA_FLASH_ATTENTION=%s, model %s\n' "$2" \
            "${SLURM_JOB_ID:-local}" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${OLLAMA_FLASH_ATTENTION-<not set>}" \
            "${MODEL:-?}" > "$1/flash_attention.txt.tmp"
        mv "$1/flash_attention.txt.tmp" "$1/flash_attention.txt"
        echo "flash    attention $2, recorded for this run in $1/flash_attention.txt"
    else
        echo "flash    attention $2 (not recorded yet; the first job records it)"
    fi
}
