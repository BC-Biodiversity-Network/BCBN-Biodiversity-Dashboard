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
  and `_3g.40gb`. `fir/submit.sh` picks the slice from `NUM_CTX` (the table is
  at the top of `submit.sh`), or takes `GPU=...`. Plain `sbatch` gets the
  1g.10gb default.
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
  on the Mac. `vllm` 0.25.0 is there too. Its "cp38" tag is vLLM's usual
  build that works on Python 3.8 and newer (cp38-abi3), and 0.25.0 (July
  2026) is newer than 0.17, the first version that knows qwen3.5. So plan B
  (vLLM) may install straight from the Alliance wheels, without Apptainer.
  That still needs a test install.
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
    scp -r data/lunaris_llm_runs/test900_x4 songyanf@fir.alliancecan.ca:scratch/lunaris_llm/runs/

To send a newer copy of the code later, copy the folder's contents instead:
`scp -r pipeline/lunaris/llm/* songyanf@fir.alliancecan.ca:lunaris_llm/code/`.

On Fir, once after each code copy:

    chmod +x ~/lunaris_llm/code/fir/*.sh

## 4. Test runs: the 900 records, twice

**Ollama runs qwen3.5 one request at a time.** The architecture `qwen35` is
on a fixed list in Ollama that ignores `OLLAMA_NUM_PARALLEL`; the server log
says "model architecture does not currently support parallel requests". So
there is no WORKERS=4 run. Speed comes from running many batches at once,
each on its own 1g.10gb slice, and `submit.sh` refuses WORKERS above 1.

- **Run 1, `test900`:** all 900 records in one batch, on one 1g.10gb slice.
- **Run 2, `test900_x4`:** the same 900 records in 4 batches of 225, all 4 at
  once, each on its own 1g.10gb slice. It repeats run 1 on other nodes, so the
  two can be compared (Fir against Fir). It also tries out how the full run
  will work: several servers starting at once, slices that share a GPU, and
  the array, status and resubmit steps.

Both can be submitted at once:

    cd ~/scratch/lunaris_llm/runs/test900
    ~/lunaris_llm/code/fir/submit.sh 0

    cd ~/scratch/lunaris_llm/runs/test900_x4
    ~/lunaris_llm/code/fir/submit.sh 0-3

Watch them:

    squeue --me
    ~/lunaris_llm/code/fir/status.sh ~/scratch/lunaris_llm/runs/test900
    ~/lunaris_llm/code/fir/status.sh ~/scratch/lunaris_llm/runs/test900_x4
    tail -5 ~/scratch/lunaris_llm/runs/test900_x4/logs/lunaris-llm_*_*.out

The job log shows the GPU slice, the Ollama version, the model digest, whether
the model is on the GPU (`model on the GPU: 100%`), progress every 25 records,
and a summary at the end. The Ollama server's own log is in `logs/ollama_*.log`.

**If the model is not on the GPU.** On a busy node Ollama's GPU search can
time out ("GPU discovery watchdog timed out"), and then Ollama runs on the
CPU without stopping. Two things guard against that:

- The job script tells Ollama to try only its CUDA 13 libraries
  (`OLLAMA_LLM_LIBRARY=cuda_v13`), so there is one try, not two.
- After the first answer, `run_batch.py` checks that the whole model is on
  the GPU. If not, it writes nothing, stops with exit code 5, and records the
  node.

`status.sh` then lists the node and prints the resubmit line with
`EXCLUDE=<node>`, which keeps that batch away from the node.

**Where the time goes.** Each answer now records Ollama's own split: seconds
reading the prompt and seconds writing the answer. `status.sh` shows the
medians, and the merged `.about.json` keeps them. For run 1, which started
before this was added, look in the server log for llama.cpp's own timing
lines, and for whether it can reuse the shared instructions between records:

    grep -h -m 6 "prompt eval time\|eval time" ~/scratch/lunaris_llm/runs/test900/logs/ollama_*.log
    grep -h -c "full prompt re-processing" ~/scratch/lunaris_llm/runs/test900/logs/ollama_*.log

The second command counts how often the whole prompt was read again from the
start, because this model type cannot keep the instructions from the record
before.

If status shows records still failing, resubmit the line it prints. Only the
missing records are asked again. Records too long for the default context
(`context_full`) need a bigger one, which `submit.sh` puts on a 2g.20gb slice:

    NUM_CTX=65536 ~/lunaris_llm/code/fir/submit.sh 0

When status says all done, merge each run into its CSV:

    module load python/3.12 && source ~/lunaris_llm/venv/bin/activate
    python ~/lunaris_llm/code/merge_results.py ~/scratch/lunaris_llm/runs/test900
    python ~/lunaris_llm/code/merge_results.py ~/scratch/lunaris_llm/runs/test900_x4

This writes `trial_qwen3.5-4b-fir_v6_test900.csv` (and `..._test900_x4.csv`)
in each run folder, with a `.about.json` beside it: digest, Ollama version,
seconds per call, and the prompt and answer timings.

## 5. Compare with the Mac (Mac)

Copy both runs back:

    scp -r songyanf@fir.alliancecan.ca:scratch/lunaris_llm/runs/test900 data/lunaris_llm_runs/fir_test900
    scp -r songyanf@fir.alliancecan.ca:scratch/lunaris_llm/runs/test900_x4 data/lunaris_llm_runs/fir_test900_x4

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
        --b data/lunaris_llm_runs/fir_test900_x4/trial_qwen3.5-4b-fir_v6_test900_x4.csv \
        --out data/lunaris_llm_runs/fir_test900/compare_fir_run1_vs_x4.csv

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

This is the Ollama plan, sized for run 1's speed: 12.8 s per record on a
1g.10gb slice. If the vLLM test is chosen instead, this step changes.

- **Total:** 161,278 records x 12.8 s is about 573 slice-hours.
- **Batch size 600:** about 2.1 hours of work per batch, inside the 3-hour
  limit with room for slow records and model loading. That makes 269 batches
  (0 to 268).
- **16 at once:** about 36 hours of wall time. 32 at once would halve that,
  if slices keep starting within seconds.

**(Mac)** Make the batches and copy them:

    python pipeline/lunaris/llm/make_batches.py \
        --harvest exploration/lunaris/harvest_2026_10/lunaris_full_harvest.parquet \
        --run-dir data/lunaris_llm_runs/harvest_2026_10 --batch-size 600
    scp -r data/lunaris_llm_runs/harvest_2026_10 songyanf@fir.alliancecan.ca:scratch/lunaris_llm/runs/

**(Fir)** Submit, then watch:

    cd ~/scratch/lunaris_llm/runs/harvest_2026_10
    ~/lunaris_llm/code/fir/submit.sh 0-268%16
    ~/lunaris_llm/code/fir/status.sh ~/scratch/lunaris_llm/runs/harvest_2026_10

`status.sh` prints the exact `submit.sh` line that resubmits only unfinished
batches, with `EXCLUDE=` for any node where the model was off the GPU. Then
do the `NUM_CTX=65536` pass for the few long records (4 prompts are over
55,000 characters), merge, and copy back as in steps 4 and 5.

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
- **Exit code 5, "the model is not entirely on the GPU".** Ollama's GPU
  search timed out on that node. Resubmit with the line `status.sh` prints;
  it includes `EXCLUDE=<node>`.
- **The server does not start.** Read `logs/ollama_*.log`. If the program will
  not run on Fir at all, the fallback is the Apptainer image
  `docker://ollama/ollama:0.40.0` (ask Claude to adapt the job script), and
  plan B is vLLM.
