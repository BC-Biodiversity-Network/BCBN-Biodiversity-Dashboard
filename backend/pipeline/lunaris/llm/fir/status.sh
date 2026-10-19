#!/bin/bash
# Show a run's progress and errors in one command, on the Fir login node.
#
#   ~/lunaris_llm/code/fir/status.sh ~/scratch/lunaris_llm/runs/test900
#
# status.py needs pandas, which lives in the run's virtual environment, so this
# loads the Python module and the environment first and then runs it. It only
# reads files and asks squeue, so it is safe to run at any time.

module load StdEnv/2023 python/3.12 >/dev/null 2>&1   # CONFIRM ON FIR: same modules as llm_array.sbatch
source "${LUNARIS_LLM_VENV:-$HOME/lunaris_llm/venv}/bin/activate"
python "$(dirname "$0")/../status.py" "${1:-.}" "${@:2}"
