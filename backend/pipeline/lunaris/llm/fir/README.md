# Lunaris LLM filter on Fir

How to run the LLM filter (qwen3.5:4b, prompt v6) on the Alliance cluster Fir,
one batch of records per GPU job, with Ollama 0.40.0.

The model only reports facts about each record. The yes or no decision is made
afterwards, on the Mac, by `exploration/llm/score_trial.py`. The cluster's
merged CSV has exactly the same columns as the Mac's `run_trial.py` output, so
the Mac tools read it unchanged. Its model column says `qwen3.5:4b-fir`.

Every command below is run by hand. Commands marked **(Mac)** run on the Mac
from `backend/`; everything else runs on Fir. No password, key or token is
written into any file here.

## Before you start: the weights differ from the Mac

On the Mac, Ollama runs the MLX build of `qwen3.5:4b` (nvfp4, ID
`7f3251fa6878`). On Fir's NVIDIA GPUs the same name pulls the GGUF build
(expected ID `d8b0f5e9760c`). These are different 4-bit versions of the same
model, so some answers can change. That is what test runs 1 and 2 measure.

## What is where

| On Fir | What |
|---|---|
| `~/lunaris_llm/code/` | this folder (`pipeline/lunaris/llm/`), copied from the Mac |
| `~/lunaris_llm/venv/` | Python environment (pandas only) |
| `~/lunaris_llm/ollama/` | the Ollama 0.40.0 program |
| `~/scratch/lunaris_llm/models/` | the downloaded model (`OLLAMA_MODELS`) |
| `~/scratch/lunaris_llm/runs/<run>/` | one folder per run: `run.json`, `inputs/`, `results/`, `errors/`, `logs/` |

Scratch is cleaned of old files on Alliance clusters, so copy results back to
the Mac when a run finishes. Once `/project` is ready for def-srivast, the code,
the program and the model can move there.

## 0. Logging in

    ssh songyanf@fir.alliancecan.ca

Each `ssh` and `scp` asks for MFA. Optional, **(Mac)** `~/.ssh/config`, to
reuse one login for 4 hours (this is a direct connection, not a jump host, and
stores no secret):

    Host fir.alliancecan.ca
        ControlMaster auto
        ControlPath ~/.ssh/cm-%r@%h:%p
        ControlPersist 4h

## 1. First-login checks

    # GPU types, partitions and time limits
    sinfo -o "%P %G %l" | sort -u
    # modules and Python packages on offer
    module avail python 2>&1 | head -20
    module spider apptainer 2>&1 | head -5
    avail_wheels pandas vllm
    # can the login node download? (each should print an HTTP status line)
    curl -sI https://github.com | head -1
    curl -sI https://registry.ollama.ai/v2/ | head -1
    # disk space and file-count quotas, and whether /project is ready
    diskusage_report
    ls -ld ~/projects/* 2>/dev/null || echo "no project folder yet"
    # which accounts you can charge
    sacctmgr -n show assoc user=$USER format=account%30 | sort -u

Then a 15-minute GPU session to see the GPU and whether compute nodes have
internet:

    salloc --account=def-srivast --gpus=1 --cpus-per-task=2 --mem=4G --time=0:15:00
    nvidia-smi
    curl -sI --max-time 10 https://registry.ollama.ai/v2/ | head -1 || echo "no internet on compute nodes"
    exit

What each answer changes:

- **GPU types.** Put the type in the `--gpus` line of `llm_array.sbatch` (for
  example `--gpus=h100:1`). If MIG slices such as `2g.20gb` are listed, use the
  smallest one. The model needs about 4 GB, and a slice is quicker to get and
  uses less of the group's share.
- **Time limits.** Keep `--time` at or under 3 hours if possible, because
  short jobs start sooner.
- **Python module name.** If it is not `python/3.12`, change the `module load`
  line in `llm_array.sbatch`, `status.sh` and step 2.
- **Login node downloads.** If GitHub or the Ollama registry is blocked, stop
  and tell Claude. Nothing gets downloaded on the Mac without asking first.
- **Compute nodes have no internet.** That is expected and fine: the model is
  pulled on the login node, and the jobs never download anything.
- **Quotas.** The model is about 3 to 4 GB and the Ollama program about 1 to
  2 GB. If home is tight, put `ollama/` and `venv/` in scratch and set
  `LUNARIS_OLLAMA_BIN` and `LUNARIS_LLM_VENV` when submitting.
- **No Apptainer.** It only matters if the Ollama program will not run (step 2).

## 2. One-time setup on the login node

    mkdir -p ~/lunaris_llm ~/scratch/lunaris_llm/runs ~/scratch/lunaris_llm/models

The Ollama 0.40.0 program, no root needed:

    cd ~/lunaris_llm
    curl -fL -o ollama-linux-amd64.tgz \
        https://github.com/ollama/ollama/releases/download/v0.40.0/ollama-linux-amd64.tgz
    mkdir -p ollama && tar -C ollama -xzf ollama-linux-amd64.tgz
    ~/lunaris_llm/ollama/bin/ollama --version

If the download gives 404, open
https://github.com/ollama/ollama/releases/tag/v0.40.0 and use the
`linux-amd64` file listed there. If it ends in `.tar.zst`, unpack it with
`tar --zstd -C ollama -xf <file>`.

The Python environment:

    module load StdEnv/2023 python/3.12
    virtualenv --no-download ~/lunaris_llm/venv
    source ~/lunaris_llm/venv/bin/activate
    pip install --no-index --upgrade pip
    pip install --no-index pandas
    deactivate

The model. A server is started on the login node only to download, then
stopped. It uses no GPU. If port 11555 is busy, pick another number.

    export OLLAMA_MODELS=~/scratch/lunaris_llm/models
    export OLLAMA_HOST=127.0.0.1:11555
    ~/lunaris_llm/ollama/bin/ollama serve > ~/scratch/lunaris_llm/pull_server.log 2>&1 &
    sleep 5
    ~/lunaris_llm/ollama/bin/ollama pull qwen3.5:4b
    ~/lunaris_llm/ollama/bin/ollama list
    ~/lunaris_llm/ollama/bin/ollama show qwen3.5:4b | head -12
    kill %1
    unset OLLAMA_HOST

`ollama list` should show ID `d8b0f5e9760c` (the GGUF build). Write down the
ID and the quantization line from `ollama show`.

## 3. Copy the code and the test input (Mac)

    ssh songyanf@fir.alliancecan.ca 'mkdir -p ~/lunaris_llm ~/scratch/lunaris_llm/runs'
    scp -r pipeline/lunaris/llm songyanf@fir.alliancecan.ca:lunaris_llm/code
    scp -r data/lunaris_llm_runs/test900 songyanf@fir.alliancecan.ca:scratch/lunaris_llm/runs/

To send a newer copy of the code later, copy the folder's contents instead:
`scp -r pipeline/lunaris/llm/* songyanf@fir.alliancecan.ca:lunaris_llm/code/`.

On Fir, make the folder for the second test run now, before anything has run,
so it starts empty:

    cd ~/scratch/lunaris_llm/runs
    cp -r test900 test900_par4
    chmod +x ~/lunaris_llm/code/fir/*.sh

## 4. Test runs: the 900 records, twice

Run 1 asks one record at a time, the same as the Mac. Run 2 asks 4 at a time.
They use separate GPUs, so both can be submitted at once.

    cd ~/scratch/lunaris_llm/runs/test900
    sbatch --array=0 ~/lunaris_llm/code/fir/llm_array.sbatch

    cd ~/scratch/lunaris_llm/runs/test900_par4
    WORKERS=4 sbatch --array=0 ~/lunaris_llm/code/fir/llm_array.sbatch

Watch them:

    squeue --me
    ~/lunaris_llm/code/fir/status.sh ~/scratch/lunaris_llm/runs/test900
    ~/lunaris_llm/code/fir/status.sh ~/scratch/lunaris_llm/runs/test900_par4
    tail -5 ~/scratch/lunaris_llm/runs/test900/logs/lunaris-llm_*_0.out

The job log shows the GPU, the Ollama version, the model digest, progress
every 25 records and a summary at the end. The Ollama server's own log is in
`logs/ollama_*.log`.

If status shows records still failing, resubmit the same command. Only the
missing records are asked again. Records too long for the default context
(`context_full`) need a bigger one:

    NUM_CTX=65536 sbatch --array=0 ~/lunaris_llm/code/fir/llm_array.sbatch

When status says all done, merge each run into its CSV:

    module load StdEnv/2023 python/3.12 && source ~/lunaris_llm/venv/bin/activate
    python ~/lunaris_llm/code/merge_results.py ~/scratch/lunaris_llm/runs/test900
    python ~/lunaris_llm/code/merge_results.py ~/scratch/lunaris_llm/runs/test900_par4

This writes `trial_qwen3.5-4b-fir_v6_test900.csv` (and `..._test900_par4.csv`)
in each run folder, with a `.about.json` beside it: digest, Ollama version,
seconds per call.

## 5. Compare with the Mac (Mac)

Copy both runs back:

    scp -r songyanf@fir.alliancecan.ca:scratch/lunaris_llm/runs/test900 data/lunaris_llm_runs/fir_test900
    scp -r songyanf@fir.alliancecan.ca:scratch/lunaris_llm/runs/test900_par4 data/lunaris_llm_runs/fir_test900_par4

Score the Fir run on its own:

    python exploration/llm/score_trial.py \
        --labels exploration/llm/test_set_reviewed_harvest_2026_10.csv --reviewed-only \
        --trial data/lunaris_llm_runs/fir_test900/trial_qwen3.5-4b-fir_v6_test900.csv

Compare Mac and Fir record by record, then the two Fir runs with each other:

    python exploration/llm/compare_runs.py \
        --a exploration/llm/trial_qwen3.5-4b_v6_harvest_2026_10.csv \
        --b data/lunaris_llm_runs/fir_test900/trial_qwen3.5-4b-fir_v6_test900.csv \
        --out data/lunaris_llm_runs/fir_test900/compare_mac_vs_fir.csv

    python exploration/llm/compare_runs.py \
        --a data/lunaris_llm_runs/fir_test900/trial_qwen3.5-4b-fir_v6_test900.csv \
        --b data/lunaris_llm_runs/fir_test900_par4/trial_qwen3.5-4b-fir_v6_test900_par4.csv \
        --out data/lunaris_llm_runs/fir_test900/compare_fir_seq_vs_par4.csv

If you load more than one trial into `score_trial.py`, always pass
`--disagreements <a new file>`. Otherwise it overwrites
`exploration/llm/for_evan_disagreements.csv`.

**Close enough** means all of:

1. All 900 answered, none failed, and one model digest.
2. The final yes or no agrees with the Mac on at least 95% of the 900, and on
   at least 95% of the 259 reviewed records.
3. `concerns_living_things` agrees on at least 95%.
4. Recall within 2 points of 94.4%, and precision within 3 points of 84.2%.

One heavy reviewed record can move precision by up to about 10 points. So if
gate 4 fails, read the heaviest flipped records in the compare CSV before
judging. The Fir-against-Fir comparison shows how much the result moves on the
same weights, which helps tell weight differences from noise.

The full run starts only after Lucia approves this result.

## 6. The full October harvest (only after approval)

**(Mac)** Make the batches: 161,278 records, 41 batches of 4,000, about 57 MB.
Then copy them:

    python pipeline/lunaris/llm/make_batches.py \
        --harvest exploration/lunaris/harvest_2026_10/lunaris_full_harvest.parquet \
        --run-dir data/lunaris_llm_runs/harvest_2026_10
    scp -r data/lunaris_llm_runs/harvest_2026_10 songyanf@fir.alliancecan.ca:scratch/lunaris_llm/runs/

**(Fir)** Submit all 41 batches, 4 at a time. Use the `WORKERS` and `--time`
that the test runs showed work best:

    cd ~/scratch/lunaris_llm/runs/harvest_2026_10
    WORKERS=4 sbatch --array=0-40%4 ~/lunaris_llm/code/fir/llm_array.sbatch
    ~/lunaris_llm/code/fir/status.sh ~/scratch/lunaris_llm/runs/harvest_2026_10

`status.sh` prints the exact `sbatch --array=...` line that resubmits only
unfinished batches. Then do the `NUM_CTX=65536` pass for the few long records
(4 prompts are over 55,000 characters), merge, and copy back as in steps 4
and 5.

## When something goes wrong

- **"is being worked on by job N".** Another job holds the batch. If that job
  has ended (check `squeue --me`), the next run takes the lock over by itself.
- **The job hit its time limit.** Nothing written is lost. Resubmit, and only
  the rest is asked.
- **"does not have qwen3.5:4b".** `OLLAMA_MODELS` points somewhere without
  the model. Redo the pull in step 2.
- **"already has answers from digest(s)".** The model changed in the middle
  of a run. Start a new run folder rather than mixing weights.
- **"a prompt differs from the one made on the Mac".** The code on Fir is
  older or newer than the code that made the batches. Copy the code again
  (step 3).
- **The server does not start.** Read `logs/ollama_*.log`. If the program will
  not run on Fir at all, the fallback is the Apptainer image
  `docker://ollama/ollama:0.40.0` (ask Claude to adapt the job script), and
  plan B is vLLM.
