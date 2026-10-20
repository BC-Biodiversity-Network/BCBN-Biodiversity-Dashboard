#!/bin/bash
# Show a run's progress and errors in one command, on the Fir login node.
#
#   /scratch/$USER/lunaris_llm/code/fir/status.sh /scratch/$USER/lunaris_llm/runs/test900
#
# status.py needs pandas, which lives in the login virtual environment on
# /scratch (not /home, see fir/job_setup.sh), so this
# loads the Python module and the environment first and then runs it. It only
# reads files and asks squeue, so it is safe to run at any time.

module load python/3.12 >/dev/null 2>&1   # the same module as llm_array.sbatch
source "${LUNARIS_LLM_VENV:-${LUNARIS_BASE:-${SCRATCH:-/scratch/$USER}/lunaris_llm}/venv}/bin/activate"
python "$(dirname "$0")/../status.py" "${1:-.}" "${@:2}"
