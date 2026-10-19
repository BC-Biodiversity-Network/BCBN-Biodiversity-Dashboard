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
internet. Fir needs a GPU model in every request; plain `--gpus=1` is refused.

    salloc --account=def-srivast --gpus=nvidia_h100_80gb_hbm3_1g.10gb:1 --cpus-per-task=2 --mem=4G --time=0:15:00
    nvidia-smi
    curl -sI --max-time 10 https://registry.ollama.ai/v2/ | head -1 || echo "no internet on compute nodes"
    exit

What the checks found on 2026-10-19, and what was done about it:

- **A GPU model is required in every request.** Allowed: `h100` (a whole
  H100 80 GB) and the MIG slices `nvidia_h100_80gb_hbm3_1g.10gb`, `_2g.20gb`
  and `_3g.40gb`. `fir/submit.sh` picks the slice from `WORKERS` and `NUM_CTX`
  (the table is at the top of `submit.sh`), or takes `GPU=...`. Plain
  `sbatch` gets the 1g.10gb default.
- **The 1g.10gb slice was granted within seconds.** It has 9,984 MiB and 16
  SMs, driver 580.178.04, CUDA 13.0. The Ollama archive brings its own CUDA
  libraries and only needs the driver.
- **`--account=def-srivast` works.** Slurm picks def-srivast_gpu by itself.
- **Partitions.** `gpubase_bygpu_b1` takes jobs up to 3 hours and starts
  soonest, b2 up to 12 hours, b3 up to a day. The job script asks for 3 hours,
  so it lands in b1. The interactive partition only has MIG slices.
- **Modules.** `python/3.12` (3.12.4) exists; 3.11 is the default, and 3.13
  and 3.14 are there too. The scripts load `python/3.12` only. Fir loads a
  standard environment (StdEnv) by itself at login and in jobs, and
  `python/3.12` was found under it, so no StdEnv line is needed. Step 2 shows
  how to note which one it is. The `apptainer` module exists.
- **Python packages.** `pandas` 3.0.5 has wheels for 3.12, the same version as
  on the Mac. `vllm` is only 0.25.0, which is far too old for qwen3.5, so plan
  B (vLLM) would have to run in an Apptainer container.
- **Internet.** The login node reaches GitHub and the Ollama registry. So
  does a compute node, but the jobs still never download: the model is pulled
  once on the login node, so every job uses the same copy.
- **Disk.** /home has 48 GiB, /scratch 19 TiB, and there is no `~/projects`
  yet, so runs and the model live in scratch. Scratch is cleaned of old
  files, so copy results back to the Mac when a run finishes.

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

The Python environment. The first line shows which standard environment Fir
loaded for you; write it down. If Fir changes its default one day and the jobs
start failing to import pandas, rebuild the environment with these same
commands.

    module list 2>&1 | grep -o 'StdEnv/[0-9]*'
    module load python/3.12
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

Run 1 asks one record at a time, the same as the Mac, on a 1g.10gb slice.
Run 2 asks 4 at a time on a 2g.20gb slice. `submit.sh` picks the slices. They
use separate GPU slices, so both can be submitted at once.

    cd ~/scratch/lunaris_llm/runs/test900
    ~/lunaris_llm/code/fir/submit.sh 0

    cd ~/scratch/lunaris_llm/runs/test900_par4
    WORKERS=4 ~/lunaris_llm/code/fir/submit.sh 0

Watch them:

    squeue --me
    ~/lunaris_llm/code/fir/status.sh ~/scratch/lunaris_llm/runs/test900
    ~/lunaris_llm/code/fir/status.sh ~/scratch/lunaris_llm/runs/test900_par4
    tail -5 ~/scratch/lunaris_llm/runs/test900/logs/lunaris-llm_*_0.out

The job log shows the GPU slice, the Ollama version, the model digest,
progress every 25 records and a summary at the end. The Ollama server's own
log is in `logs/ollama_*.log`.

The last line of each job log, `gpu use`, says how much of the model was on
the GPU. It should say 100%. Less means part of the model ran on the CPU
because the slice was too small. Resubmit that run with the next slice up, for
example `GPU=nvidia_h100_80gb_hbm3_2g.20gb ~/lunaris_llm/code/fir/submit.sh 0`.
If it says 0%, Ollama did not use the MIG slice at all; try `GPU=h100`.

    grep -h "gpu use\|WARNING" ~/scratch/lunaris_llm/runs/test900*/logs/lunaris-llm_*.out

If status shows records still failing, resubmit the same command. Only the
missing records are asked again. Records too long for the default context
(`context_full`) need a bigger one:

    NUM_CTX=65536 ~/lunaris_llm/code/fir/submit.sh 0

When status says all done, merge each run into its CSV:

    module load python/3.12 && source ~/lunaris_llm/venv/bin/activate
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

**(Fir)** Submit all 41 batches, 4 at a time. Use the `WORKERS` that the test
runs showed works best. A 4,000-record batch must also finish within the
3-hour limit, so check the test runs' speed first: at more than about 2.5
seconds per record, use smaller batches (`--batch-size 2000` in make_batches).

    cd ~/scratch/lunaris_llm/runs/harvest_2026_10
    WORKERS=4 ~/lunaris_llm/code/fir/submit.sh 0-40%4
    ~/lunaris_llm/code/fir/status.sh ~/scratch/lunaris_llm/runs/harvest_2026_10

`status.sh` prints the exact `submit.sh` line that resubmits only unfinished
batches. Then do the `NUM_CTX=65536` pass for the few long records
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
