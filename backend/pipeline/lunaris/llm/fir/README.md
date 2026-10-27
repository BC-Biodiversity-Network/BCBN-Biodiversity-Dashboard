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

Everything lives on /scratch, in one folder. On Fir, set this once per login
(or add it to `~/.bashrc`); every Fir command below uses it:

    export B=/scratch/$USER/lunaris_llm

The full `/scratch/...` path is used on purpose rather than `~/scratch`.
`~/scratch` is a link that sits on /home, and on 2026-10-19 /home was
unreachable from some nodes for hours. Jobs also copy what they run to the
node's own disk first (`fir/job_setup.sh`), so a slow shared disk stops a job
in its first minute with a clear message instead of hanging.

| On Fir | What |
|---|---|
| `$B/code/` | this folder (`pipeline/lunaris/llm/`), copied from the Mac |
| `$B/venv/` | Python environment for the login node (status, merge); jobs build their own |
| `$B/ollama/` | the Ollama 0.40.0 program |
| `$B/models/` | the downloaded model (`OLLAMA_MODELS` when pulling) |
| `$B/runs/<run>/` | one folder per run: `run.json`, `inputs/`, `results/`, `errors/`, `logs/` |
| `$B/venv_vllm/` | vLLM's own Python environment (plan B test only) |
| `$B/hf/Qwen3.5-4B/` | the Hugging Face weights for vLLM (plan B test only) |
| `$B/bench/` | the CPU benchmark's logs and CSVs (step 4b) |

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

    mkdir -p $B/runs $B/models

The Ollama 0.40.0 program, no root needed:

    cd $B
    curl -fL -o ollama-linux-amd64.tar.zst \
        https://github.com/ollama/ollama/releases/download/v0.40.0/ollama-linux-amd64.tar.zst
    mkdir -p ollama && tar -I zstd -xf ollama-linux-amd64.tar.zst -C ollama
    $B/ollama/bin/ollama --version

The v0.40.0 Linux file is a `.tar.zst` (there is no `.tgz` for this
version), and `zstd` is available on Fir to unpack it.

The Python environment. The first line shows which standard environment Fir
loaded for you; write it down. If Fir changes its default one day and the jobs
start failing to import pandas, rebuild the environment with these same
commands.

    module list 2>&1 | grep -o 'StdEnv/[0-9]*'
    module load python/3.12
    virtualenv --no-download $B/venv
    source $B/venv/bin/activate
    pip install --no-index --upgrade pip
    pip install --no-index pandas==3.0.5
    deactivate

The model. A server is started on the login node only to download, then
stopped. It uses no GPU. If port 11555 is busy, pick another number.

    export OLLAMA_MODELS=$B/models
    export OLLAMA_HOST=127.0.0.1:11555
    $B/ollama/bin/ollama serve > $B/pull_server.log 2>&1 &
    sleep 5
    $B/ollama/bin/ollama pull qwen3.5:4b
    $B/ollama/bin/ollama list
    $B/ollama/bin/ollama show qwen3.5:4b | head -12
    kill %1
    unset OLLAMA_HOST

`ollama list` should show ID `d8b0f5e9760c` (the GGUF build). Write down the
ID and the quantization line from `ollama show`.

## 2b. Moving an existing setup off /home (once)

Up to 2026-10-19 the Ollama program and the Python environment were in
`~/lunaris_llm` on /home. The model and the runs were already on /scratch
(`~/scratch/lunaris_llm` and `$B` are the same folder). To move the rest, on
a login node where /home works, with no lunaris jobs queued (`squeue --me`):

    export B=/scratch/$USER/lunaris_llm
    ls $B/models $B/runs
    cd $B
    curl -fL -o ollama-linux-amd64.tar.zst \
        https://github.com/ollama/ollama/releases/download/v0.40.0/ollama-linux-amd64.tar.zst
    mkdir -p ollama && tar -I zstd -xf ollama-linux-amd64.tar.zst -C ollama
    $B/ollama/bin/ollama --version
    module load python/3.12
    virtualenv --no-download $B/venv
    source $B/venv/bin/activate
    pip install --no-index --upgrade pip
    pip install --no-index pandas==3.0.5
    deactivate

The Ollama program is downloaded again rather than copied from /home, so no
half-read file from the bad spell comes along. Then copy the code to `$B/code`
(step 3). Leave the old `~/lunaris_llm` where it is for now; removing it is an
optional clean-up at the very end of this file.

## 3. Copy the code and the test input (Mac)

    ssh songyanf@fir.alliancecan.ca 'mkdir -p /scratch/songyanf/lunaris_llm/runs'
    scp -r pipeline/lunaris/llm songyanf@fir.alliancecan.ca:/scratch/songyanf/lunaris_llm/code
    scp -r data/lunaris_llm_runs/test900 songyanf@fir.alliancecan.ca:/scratch/songyanf/lunaris_llm/runs/
    scp -r data/lunaris_llm_runs/test900_x4 songyanf@fir.alliancecan.ca:/scratch/songyanf/lunaris_llm/runs/

To send a newer copy of the code later, copy the folder's contents instead:
`scp -r pipeline/lunaris/llm/* songyanf@fir.alliancecan.ca:/scratch/songyanf/lunaris_llm/code/`.

On Fir, once after each code copy:

    chmod +x $B/code/fir/*.sh

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

    cd $B/runs/test900
    $B/code/fir/submit.sh 0

    cd $B/runs/test900_x4
    $B/code/fir/submit.sh 0-3

Watch them:

    squeue --me
    $B/code/fir/status.sh $B/runs/test900
    $B/code/fir/status.sh $B/runs/test900_x4
    tail -5 $B/runs/test900_x4/logs/lunaris-llm_*_*.out

The job log shows the GPU slice, the Ollama version, the model digest, whether
the model is on the GPU (`model on the GPU: 100%`), progress every 25 records,
and a summary at the end. The Ollama server's own log is in `logs/ollama_*.log`.

**If the model does not get going.** Ollama's GPU search can time out
("GPU discovery watchdog timed out"), and then Ollama carries on with the CPU
only and the model never loads in time. On 2026-10-19 this hit every job
started while /home was unreachable, because Ollama and Python were on
/home. Each job now guards against it:

- It copies the code, the Ollama program and the model to the node's own
  disk and builds Python there (`fir/job_setup.sh`). The job log shows each
  step with its size and time. If a step fails or takes over 10 minutes, the
  job stops with exit code 6 (`SETUP FAILED ...`).
- Ollama tries only its CUDA 13 libraries (`OLLAMA_LLM_LIBRARY=cuda_v13`).
- Before asking about any record, `run_batch.py` makes three checks. First,
  Ollama's log must report a GPU: if it fell back to the CPU, the job stops
  at once with exit code 5. Second, the model must load within 6 minutes
  (`LOAD_TIMEOUT`), or the job stops with exit code 6. Third, all of the
  model must be on the GPU (exit code 5 if not).
- If 5 requests in a row then fail with a timeout or a server error, the job
  stops with exit code 2 rather than failing record after record.

`status.sh` lists jobs that stopped before their first record, with node and
reason. One node failing again and again: leave it out with `EXCLUDE=<node>`.
Many nodes failing at the same moment: suspect the shared filesystem, and
wait before resubmitting.

**Where the time goes.** Each answer now records Ollama's own split: seconds
reading the prompt and seconds writing the answer. `status.sh` shows the
medians, and the merged `.about.json` keeps them. For run 1, which started
before this was added, look in the server log for llama.cpp's own timing
lines, and for whether it can reuse the shared instructions between records:

    grep -h -m 6 "prompt eval time\|eval time" $B/runs/test900/logs/ollama_*.log
    grep -h -c "full prompt re-processing" $B/runs/test900/logs/ollama_*.log

The second command counts how often the whole prompt was read again from the
start, because this model type cannot keep the instructions from the record
before.

If status shows records still failing, resubmit the line it prints. Only the
missing records are asked again. Records too long for the default context
(`context_full`) need a bigger one, which `submit.sh` puts on a 2g.20gb slice:

    NUM_CTX=65536 $B/code/fir/submit.sh 0

When status says all done, merge each run into its CSV:

    module load python/3.12 && source $B/venv/bin/activate
    python $B/code/merge_results.py $B/runs/test900
    python $B/code/merge_results.py $B/runs/test900_x4

This writes `trial_qwen3.5-4b-fir_v6_test900.csv` (and `..._test900_x4.csv`)
in each run folder, with a `.about.json` beside it: digest, Ollama version,
seconds per call, and the prompt and answer timings.

## 4b. Is answer writing short of CPU?

Run 1's server log showed where the time goes. Reading the prompt takes about
1 second, because the shared instructions are reused from the record before.
Writing the answer (about 113 tokens) runs at only about 10 tokens a second,
about 10 of the 12.8 seconds. The GPU slice should manage far more, so the
suspect is the CPU side: picking each token from a 248,000-word vocabulary,
the presence penalty, and the JSON schema check all run on the CPU, and the
job had 2 CPUs. Ollama may also start many more threads than that.

**Free look while run 1 is still going.** Put run 1's job id in `JOB`. This
shows how many CPUs the job may use, how many the node has, how many threads
Ollama's runner started (and its `--threads` setting), and how busy each
process is:

    JOB=<run 1 job id>
    srun --jobid=$JOB --overlap bash -c 'echo "usable $(nproc) of $(nproc --all)"; for p in $(pgrep -u $USER -f "llama-server|ollama"); do echo "$p $(grep Threads /proc/$p/status) $(tr "\0" " " < /proc/$p/cmdline | grep -o -- "--threads [0-9]*")"; done; top -b -n 1 -u $USER | head -15'

A runner with dozens of threads, or `--threads` far above 2, while `top`
shows about 200% CPU, means the 2 CPUs are oversubscribed.

**The benchmark.** Two short jobs, each sending the same 24 records to
Ollama under four settings: as run, with `num_thread` set to the job's CPU
count, also without the presence penalty, and also without the JSON schema.
One job has 2 CPUs and one has 8. Each takes 10 to 25 minutes on a 1g.10gb
slice; together about 0.1 H100-hours.

    mkdir -p $B/bench/logs && cd $B/bench
    sbatch --cpus-per-task=2 $B/code/fir/bench_decode.sbatch $B/runs/test900
    sbatch --cpus-per-task=8 --mem=16G $B/code/fir/bench_decode.sbatch $B/runs/test900
    tail -12 $B/bench/logs/lunaris-bench_*.out

Each log ends with a table: answer tokens a second, prompt seconds, total
seconds, how busy the CPUs were, and whether `threads` gives the same answers
as `as_run`. Before deciding, also check what each job is billed:

    scontrol show job <job id> | grep -o 'billing=[0-9]*'

Asking for more CPUs than a 1g slice comes with may make each slice-hour
cost more.

**Result (job 66026636, 2 CPUs, and 66026637, 8 CPUs; 24 records each):**

| setting | 2 CPUs: tok/s | total s | 8 CPUs: tok/s | total s | same as as_run |
|---|---|---|---|---|---|
| as_run | 10.4 | 12.36 | 36.5 | 3.62 | |
| threads | 59.7 | 2.37 | 60.4 | 2.35 | 24/24 facts and text, both jobs |
| threads_no_penalty | 62.0 | 2.28 | 62.7 | 2.27 | |
| threads_no_schema | 59.7 | 2.37 | 60.4 | 2.35 | |

Setting `num_thread` to the job's CPU count makes answers 5.7 times faster on
2 CPUs, with identical answers. More CPUs add nothing once it is set, and the
schema and the penalty cost almost nothing. So `submit.sh` and
`llm_array.sbatch` now set `num_thread` to the job's CPU count by default
(2 on a 1g.10gb slice). `NUM_THREAD=0` brings back Ollama's own choice, which
is how run 1 and the first test900_x4 ran.

The logs said "no --threads flag" even for the fast setting. Ollama 0.40 hands
the thread count to its runner when it loads the model, not on the command
line, so that line cannot show it. The thread counts it printed include idle
helper threads, and a runner from the previous setting that had not yet
exited. The speeds and the "CPU busy" column are the evidence.

How to read it (kept for any later benchmark):

- **`threads` is much faster than `as_run` on 2 CPUs, with the same facts.**
  Ollama started too many threads, and setting `num_thread` is free. (This
  is what happened, and it is now the default.)
- **Only the 8-CPU job is fast.** The work needs more CPUs: submit with
  `CPUS=8 NUM_THREAD=8`, if the billing check allows it.
- **`threads_no_schema` or `threads_no_penalty` is much faster.** The schema
  or the penalty is the cost. Neither can simply be dropped, because both
  change the answers, so this points to vLLM.
- **Nothing helps, and the CPUs are not busy.** The slowness is on the GPU
  side, in llama.cpp's code for this kind of model. That also points to vLLM.

## 5. Compare with the Mac (Mac)

Copy both runs back:

    scp -r songyanf@fir.alliancecan.ca:/scratch/songyanf/lunaris_llm/runs/test900 data/lunaris_llm_runs/fir_test900
    scp -r songyanf@fir.alliancecan.ca:/scratch/songyanf/lunaris_llm/runs/test900_x4 data/lunaris_llm_runs/fir_test900_x4

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

**Close enough** means all of these, which `compare_runs.py` checks:

1. All 900 answered, none failed, and one model digest.
2. The final yes or no agrees with the Mac on at least 95% of the 900.
3. `concerns_living_things` agrees on at least 95%.
4. On the reviewed records where the two runs disagree, Fir is right about as
   often as the Mac: on at least 40% of them, or on no more than 2 fewer.
5. Recall within 2 points of 94.4% and precision within 3 points of 84.2%,
   with the single heaviest reviewed disagreement set aside.

Why 4 and 5 are worded this way. With 259 reviewed records and their
weights, one heavy record can move precision by about 10 points. In run 1,
row 455 (weight 1,744) alone took Fir's precision from 82.9% to 93.5%. And a
fixed cap on reviewed disagreements (13 records for 95%) is crossed by two
borderline records either way. So the scores are compared with the heaviest
disagreement set aside, and the disagreements are judged by who gets them
right, not by how many there are. `compare_runs.py` still prints the plain
reviewed agreement and the unadjusted scores, for reading.

Run 1 against the Mac passes all five: 95.3% agreement on the 900, 95.6% on
`concerns_living_things`, Fir right on 8 of the 15 reviewed disagreements and
the Mac on 7, and with row 455 set aside precision -1.2 and recall -1.9.
The Fir-against-Fir comparison (test900_x4) shows how much Fir varies on its
own, which tells weight differences from run-to-run noise.

The full run starts only after Lucia approves this result.

## 6. The full run, on the 20 October harvest (only after approval)

The full run uses a fresh harvest from 2026-10-20, not the October 9 one. The
October 9 harvest had only 67 Dryad records (5,851 in September), because of
a Lunaris feed problem that Tristan has since fixed. On 2026-10-20 the feed
reported 168,278 records, 5,958 of them in the Dryad set. The harvest uses
the same script and format as before (`lunaris_harvest_only.py`, Sickle,
oai_datacite), so the title, subjects and abstract text match what the test
set and prompt v6 were validated on.

**(Mac) 1. Harvest.** From `backend/`, in its own folder so the October one
stays as it is. It takes about 2 h 10 min to 2 h 20 min; `caffeinate` keeps
the Mac awake.

    mkdir -p exploration/lunaris/harvest_2026_10_20
    caffeinate -i ../.venv/bin/python pipeline/lunaris/lunaris_harvest_only.py \
        --outdir exploration/lunaris/harvest_2026_10_20 2>&1 | tee exploration/lunaris/harvest_2026_10_20/harvest.log

If it is interrupted, run the same command again soon, the same day. It
carries on from the saved batches by fetching and skipping what it already
has. That only lines up if the feed has not changed in between; after a long
gap, start again in a new folder.

**(Mac) 2. Check it** before building anything from it:

    python pipeline/lunaris/check_harvest.py exploration/lunaris/harvest_2026_10_20 \
        --compare exploration/lunaris/harvest_2026_10 --live

Look for:
- about 168,278 records fetched and 0 unreadable;
- about 5,958 Dryad records, against 67 on October 9;
- no warnings other than ones you can explain.

The by-source table lists every source that changed by at least 50 records
and 10% since October 9.

**(Mac) 3. Make the batches and copy them.**

    python pipeline/lunaris/llm/make_batches.py \
        --harvest exploration/lunaris/harvest_2026_10_20/lunaris_full_harvest.parquet \
        --run-dir data/lunaris_llm_runs/harvest_2026_10_20 --batch-size 3000
    scp -r data/lunaris_llm_runs/harvest_2026_10_20 songyanf@fir.alliancecan.ca:/scratch/songyanf/lunaris_llm/runs/

Expect about 168,050 unique records in about 57 batches (0 to 56), about
60 MB. The make step prints the exact count and how many prompts may be too
long for the default context. Use its last batch number in the submit line
below if it differs from 56.

**The plan and its size.** Ollama on 1g.10gb slices with 2 CPUs,
`num_thread` 2 (the default), one request at a time per slice.

- **Speed.** About 2.4 s per record: 2.37 s in the benchmark with
  `num_thread` set, plus a little overhead per request. Sizing allows up to
  2.6 s.
- **Total.** About 168,050 records x 2.4 to 2.6 s is about 112 to 121
  slice-hours. A 1g slice is about 1/7 of an H100, so about 16 to 17
  H100-hours.
- **Batch size 3,000.** 2.0 to 2.2 hours of work per batch, plus about 2
  minutes to copy to the node and load the model. That stays inside the
  3-hour limit, so batches land in the quickest partition.
- **19 at once.** About 57 batches in 3 waves of 19, so about 6.5 hours of
  wall time. 16 at once would need 4 waves (about 9 hours).
- **Afterwards.** Prompts too long for 16,384 tokens (4 in the October 9
  harvest) come back as `context_full`. One `NUM_CTX=65536` pass on just
  their batches, on a 2g.20gb slice, takes minutes.

**(Fir) 4. Submit, then watch.** Copy the code first as in step 3, so the
`num_thread` default is in place.

    cd $B/runs/harvest_2026_10_20
    $B/code/fir/submit.sh 0-56%19
    $B/code/fir/status.sh $B/runs/harvest_2026_10_20

`submit.sh` should print `NUM_THREAD=2` on its "submitting" line.
`status.sh` prints the exact `submit.sh` line that resubmits only unfinished
batches, with `EXCLUDE=` for a node that keeps failing. When only the
`context_full` records are left, resubmit just those batches with
`NUM_CTX=65536` in front, for example
`NUM_CTX=65536 $B/code/fir/submit.sh 12,40`. Then merge and copy back as in
steps 4 and 5.

**What can wait.** The keyword filter (`build_lunaris_candidates.py`) still
reads the September harvest by default. It is not part of this run. The
test set was validated on the October 9 text and does not need refreshing
for this run. If it is ever refreshed from the new harvest, give it a new
file and a new Mac cache folder: the cache key is the record id, model and
prompt version, not the text, so an old cache would return answers about the
old text.

## 7. Plan B test: vLLM on the same 900 records

vLLM serves the same model (Qwen3.5-4B) from Hugging Face's full-precision
(bf16) weights, about 9 GB. It writes answers for many records at once and
keeps the shared instructions cached. These are a third version of the
weights, after the Mac's MLX build and Fir's GGUF build, so the test has to
pass the same gates against the Mac on its own (step 5).

**One-time setup on the login node** (about 30 minutes, mostly waiting):

    module load python/3.12
    avail_wheels vllm torch transformers xgrammar huggingface_hub
    virtualenv --no-download $B/venv_vllm
    source $B/venv_vllm/bin/activate
    pip install --no-index --upgrade pip
    pip install --no-index vllm==0.25.0 huggingface_hub
    python -c "import vllm; print(vllm.__version__)"
    mkdir -p $B/hf
    hf download Qwen/Qwen3.5-4B --local-dir $B/hf/Qwen3.5-4B
    du -sh $B/hf/Qwen3.5-4B
    deactivate

If `import vllm` fails over a missing library, check the Alliance vLLM page for
the modules it needs. Name them in `VLLM_MODULES` when submitting, for example
`VLLM_MODULES="cuda/12.6"`. If the `hf` command is missing, use
`huggingface-cli download` with the same arguments.

**Copy the test folder (Mac):**

    scp -r data/lunaris_llm_runs/test900_vllm songyanf@fir.alliancecan.ca:/scratch/songyanf/lunaris_llm/runs/

It holds the same 900 records as one batch. Its model is `Qwen/Qwen3.5-4B` and
its label `qwen3.5-4b-vllm-bf16`.

**Run it (Fir):** one job on a 3g.40gb slice, 32 records in flight.

    cd $B/runs/test900_vllm
    BACKEND=vllm $B/code/fir/submit.sh 0
    $B/code/fir/status.sh $B/runs/test900_vllm
    tail -20 $B/runs/test900_vllm/logs/vllm_*_0.log

vLLM takes several minutes to start. If its log says one of the extra flags
is not accepted, resubmit with your own list, for example
`VLLM_FLAGS='--enable-prefix-caching'`. The default list is in
`llm_array.sbatch`. Then merge and compare as in steps 4 and 5:

    module load python/3.12 && source $B/venv/bin/activate
    python $B/code/merge_results.py $B/runs/test900_vllm

    # Mac, from backend/
    scp -r songyanf@fir.alliancecan.ca:/scratch/songyanf/lunaris_llm/runs/test900_vllm data/lunaris_llm_runs/fir_test900_vllm
    python exploration/llm/compare_runs.py \
        --a exploration/llm/trial_qwen3.5-4b_v6_harvest_2026_10.csv \
        --b data/lunaris_llm_runs/fir_test900_vllm/trial_qwen3.5-4b-vllm-bf16_v6_test900_vllm.csv \
        --out data/lunaris_llm_runs/fir_test900_vllm/compare_mac_vs_vllm.csv

The `.about.json` beside the merged CSV holds the run's start and end
times, so records a second can be worked out from them.

## 8. BC filter: comparing models on the 150 labelled records

The BC filter's two LLM passes (pass 1 v3, the GeoNames lookup, pass 2 C4)
run on Fir with `llm/geo_filter_run.py`, started by `fir/geo_filter.sbatch`
through `fir/geo_filter_submit.sh`. It reuses this folder's setup:
`job_setup.sh` (copy to the node's disk, Python from the Alliance wheels,
with shapely and pyproj added through `EXTRA_WHEELS`), the same Ollama
0.40.0 program in `$B/ollama`, the same GPU checks and lock files as
`run_batch.py`, and `qwen3.5:4b` from the v6 store `$B/models`. Each other
model gets its own store, `$B/bcf/models/<model>`, so a job copies only its
own model to the node. Nothing is downloaded by any job.

The job needs no harvest, rules file or pyarrow: `geo_filter_prepare.py`
(Mac) puts exactly the records it needs in a small input folder, with the
fingerprint of every pass 1 prompt, and the job checks each prompt against
it before asking anything. Results are saved record by record, so a job can
be resubmitted at any time and only the missing records are asked; a failed
answer is asked once more at once and logged in `logs/failures_part0.txt`.

On Fir everything lives under `$B/bcf`:

| On Fir | What |
|---|---|
| `$B/bcf/code/` | `pipeline/lunaris` (the BC filter code, with `llm/` and `llm/fir/`) |
| `$B/bcf/geonames/` | `geonames_lookup.parquet`, `geonames_codes.json` |
| `$B/bcf/bc_boundary_gbif.wkt` | BC's outline |
| `$B/bcf/inputs/label150/` | the 150 records (`geo_filter_prepare.py`) |
| `$B/bcf/reference/` | the Mac's `pass1_label150` and `pass2_label150_c4`, for the dry run's byte check |
| `$B/bcf/models/<model>/` | one Ollama store per model (not for `qwen3.5:4b`, which is in `$B/models`) |
| `$B/bcf/runs/label150_<model>/` | one run folder per model: `pass1/`, `pass2/`, `timing.csv`, `logs/` |

### A. Checks (Fir; no downloads)

    export B=/scratch/$USER/lunaris_llm
    $B/ollama/bin/ollama --version
    find $B/models/manifests -type f | sed "s|.*/library/||"
    du -sh $B/models
    ls $B/bcf 2>/dev/null || echo "no bcf folder yet"
    find $B -maxdepth 4 -name "*.parquet" 2>/dev/null
    module load python/3.12
    avail_wheels shapely pyproj duckdb pandas
    diskusage_report

Then, to see the models Ollama itself lists (a server on the login node,
no GPU, only to list; nothing is pulled):

    export OLLAMA_MODELS=$B/models OLLAMA_HOST=127.0.0.1:11555
    $B/ollama/bin/ollama serve > $B/list_server.log 2>&1 &
    sleep 5
    $B/ollama/bin/ollama list
    $B/ollama/bin/ollama show qwen3.5:4b | head -12
    kill %1
    unset OLLAMA_MODELS OLLAMA_HOST

### B. Model downloads (only after Lucia approves)

One store per model. Run the block once per model, with `M` and `D` set as
below; about 6.6 GB, 6.6 GB and 24 GB. If a pull stops part way, the same
`pull` line carries on.

    M=qwen3.5:9b;      D=$B/bcf/models/qwen3.5-9b
    M=gemma4:e4b;      D=$B/bcf/models/gemma4-e4b
    M=qwen3.6:35b-a3b; D=$B/bcf/models/qwen3.6-35b-a3b

    mkdir -p $D
    export OLLAMA_MODELS=$D OLLAMA_HOST=127.0.0.1:11555
    $B/ollama/bin/ollama serve > $B/bcf/pull_server.log 2>&1 &
    sleep 5
    $B/ollama/bin/ollama pull $M
    $B/ollama/bin/ollama list
    $B/ollama/bin/ollama show $M | head -15
    kill %1
    unset OLLAMA_MODELS OLLAMA_HOST

Write down each ID and quantization. If a pull says the model needs a newer
Ollama, stop and tell Claude.

### C. Copy data and code (Mac, from `backend/`)

    python pipeline/lunaris/llm/geo_filter_prepare.py \
        --records data/lunaris_bc_filter/label_set_2026_10/needs_llm_150.csv \
        --bc-boundary /Users/lucia/Desktop/BCBN/Results/bc_boundary_gbif.wkt \
        --out data/lunaris_bc_filter/fir_inputs/label150
    ssh songyanf@fir.alliancecan.ca 'mkdir -p /scratch/songyanf/lunaris_llm/bcf/inputs /scratch/songyanf/lunaris_llm/bcf/geonames /scratch/songyanf/lunaris_llm/bcf/reference /scratch/songyanf/lunaris_llm/bcf/runs'
    scp -r pipeline/lunaris 'songyanf@fir.alliancecan.ca:/scratch/songyanf/lunaris_llm/bcf/code'
    scp data/geonames/geonames_lookup.parquet data/geonames/geonames_codes.json 'songyanf@fir.alliancecan.ca:/scratch/songyanf/lunaris_llm/bcf/geonames/'
    scp /Users/lucia/Desktop/BCBN/Results/bc_boundary_gbif.wkt 'songyanf@fir.alliancecan.ca:/scratch/songyanf/lunaris_llm/bcf/'
    scp -r data/lunaris_bc_filter/fir_inputs/label150 'songyanf@fir.alliancecan.ca:/scratch/songyanf/lunaris_llm/bcf/inputs/'
    scp -r data/lunaris_bc_filter/pass1_label150 data/lunaris_bc_filter/pass2_label150_c4 'songyanf@fir.alliancecan.ca:/scratch/songyanf/lunaris_llm/bcf/reference/'

To send newer code later, copy the folder's contents instead:
`scp -r pipeline/lunaris/* 'songyanf@fir.alliancecan.ca:/scratch/songyanf/lunaris_llm/bcf/code/'`.
Then, on Fir, once after each code copy:

    chmod +x $B/bcf/code/llm/fir/*.sh

### D. Submit (Fir)

First a dry run: no GPU and no model. It builds Python with shapely and
pyproj on a node, opens the lookup, and checks that all 150 pass 1 prompts,
and the pass 2 prompts built from the Mac's pass 1 answers, are byte for
byte the Mac's. It should end with `150 identical` twice and `exit 0`.

    DRY_RUN=1 REFERENCE_PASS1=$B/bcf/reference/pass1_label150 \
        REFERENCE_PASS2=$B/bcf/reference/pass2_label150_c4 \
        $B/bcf/code/llm/fir/geo_filter_submit.sh qwen3.5:4b label150
    squeue --me
    cat $B/bcf/runs/label150_qwen3.5-4b/logs/bc-filter_*_0.out

Then 2 records with the baseline, to see the timing and the GPU check:

    LIMIT=2 $B/bcf/code/llm/fir/geo_filter_submit.sh qwen3.5:4b label150

Then the four runs (the first carries on from the 2 records):

    $B/bcf/code/llm/fir/geo_filter_submit.sh qwen3.5:4b label150
    $B/bcf/code/llm/fir/geo_filter_submit.sh qwen3.5:9b label150
    $B/bcf/code/llm/fir/geo_filter_submit.sh gemma4:e4b label150
    $B/bcf/code/llm/fir/geo_filter_submit.sh qwen3.6:35b-a3b label150

Watch them:

    squeue --me
    tail -5 $B/bcf/runs/label150_*/logs/bc-filter_*.out
    cat $B/bcf/runs/label150_*/logs/failures_part0.txt 2>/dev/null

A job that ends with exit 2, 4, 5 or 6 is resubmitted with the same line
(add `EXCLUDE=<node>` for exit 5 or 6); only the missing records are asked.

### E. Copy back and score (Mac, from `backend/`)

    mkdir -p data/lunaris_bc_filter/fir_runs
    scp -r 'songyanf@fir.alliancecan.ca:/scratch/songyanf/lunaris_llm/bcf/runs/label150_qwen3.5-4b' data/lunaris_bc_filter/fir_runs/
    scp -r 'songyanf@fir.alliancecan.ca:/scratch/songyanf/lunaris_llm/bcf/runs/label150_qwen3.5-9b' data/lunaris_bc_filter/fir_runs/
    scp -r 'songyanf@fir.alliancecan.ca:/scratch/songyanf/lunaris_llm/bcf/runs/label150_gemma4-e4b' data/lunaris_bc_filter/fir_runs/
    scp -r 'songyanf@fir.alliancecan.ca:/scratch/songyanf/lunaris_llm/bcf/runs/label150_qwen3.6-35b-a3b' data/lunaris_bc_filter/fir_runs/
    diff -r data/lunaris_bc_filter/fir_runs/label150_qwen3.5-4b/pass1/prompts data/lunaris_bc_filter/pass1_label150/prompts && echo "pass 1 prompts identical to the Mac"
    python pipeline/lunaris/llm/geo_pass2_score.py \
        --labels data/lunaris_bc_filter/label_set_2026_10/labels_final.csv \
        --pass1 data/lunaris_bc_filter/pass1_label150 --run mac-qwen3.5:4b=data/lunaris_bc_filter/pass2_label150_c4 \
        --model-run qwen3.5:4b=data/lunaris_bc_filter/fir_runs/label150_qwen3.5-4b \
        --model-run qwen3.5:9b=data/lunaris_bc_filter/fir_runs/label150_qwen3.5-9b \
        --model-run gemma4:e4b=data/lunaris_bc_filter/fir_runs/label150_gemma4-e4b \
        --model-run qwen3.6:35b-a3b=data/lunaris_bc_filter/fir_runs/label150_qwen3.6-35b-a3b \
        --out data/lunaris_bc_filter/score_label150_models.txt

The report ends with one table of all runs: 7-class accuracy, shown / not
shown accuracy, shown but should not be, not shown but should be, failures
and retries per pass, and seconds per record.

### Later: the full 12,431 needs_llm records

The same scripts, with an input made from the rules file and the run split
into parts that each fit in 3 hours (use the chosen model's seconds per
record from `timing.csv` to pick PARTS):

    # Mac
    python pipeline/lunaris/llm/geo_filter_prepare.py --rules-class needs_llm \
        --bc-boundary /Users/lucia/Desktop/BCBN/Results/bc_boundary_gbif.wkt \
        --out data/lunaris_bc_filter/fir_inputs/needs_llm
    scp -r data/lunaris_bc_filter/fir_inputs/needs_llm 'songyanf@fir.alliancecan.ca:/scratch/songyanf/lunaris_llm/bcf/inputs/'
    # Fir
    PARTS=16 $B/bcf/code/llm/fir/geo_filter_submit.sh <model> needs_llm

## 9. BC filter: the full run with qwen3.6:35b-a3b (flash attention off)

The chosen model is `qwen3.6:35b-a3b` on a 3g.40gb slice, with flash
attention off: Ollama 0.40.0 crashes this MoE model on every prompt of 1,024
tokens or more with flash attention on (ollama issue #17434). The fallback is
`qwen3.5:9b` on a 2g.20gb slice, which needs no workaround.

`geo_filter_submit.sh` sets `OLLAMA_FLASH_ATTENTION=0` by itself for
`qwen3.6:*` models and says so. The first job of a run records the setting in
`flash_attention.txt` in the run folder; a later submit or job with another
setting is refused, so one run never mixes settings. Each job checks the
runner's command line in its Ollama log for `--flash-attn off` once the model
has loaded, and stops before the first record (exit 1) if it is not there.
`llm/geo_filter_status.py` shows a run's progress part by part.

**Size.** 12,431 records at about 2.7 s each (the 150-record run; the full
set's prompts are as long on average) is about 9.3 hours of 3g.40gb slice
time, about 4.0 H100-hours. With `PARTS=8`, each part has about 1,550
records, about 70 minutes plus 1 to 2 minutes to copy and load the model: about
1.2 hours of wall time when all 8 run at once, longer if fewer 3g.40gb slices
are free. No prompt reaches the 16,384-token context in pass 1 (the longest is
8,413 tokens); a few pass 2 prompts might, and would end as `context_full`
(see C below).

### A. Copy the code and the two inputs (Mac, from `backend/`)

    scp -r pipeline/lunaris/* 'songyanf@fir.alliancecan.ca:/scratch/songyanf/lunaris_llm/bcf/code/'
    scp -r data/lunaris_bc_filter/fir_inputs/longest data/lunaris_bc_filter/fir_inputs/needs_llm 'songyanf@fir.alliancecan.ca:/scratch/songyanf/lunaris_llm/bcf/inputs/'

### B. The longest-prompt test (Fir)

    export B=/scratch/$USER/lunaris_llm
    chmod +x $B/bcf/code/llm/fir/*.sh
    $B/bcf/code/llm/fir/geo_filter_submit.sh qwen3.6:35b-a3b longest
    squeue --me
    R=$B/bcf/runs/longest_qwen3.6-35b-a3b
    cat $R/logs/bc-filter_*_0.out
    module load python/3.12
    python $B/bcf/code/llm/geo_filter_status.py $R

20 records: the 15 with the longest pass 1 prompts (3,719 to 8,413 tokens)
and 5 likely to have a long Place list in pass 2. Check:

- the submit line says `flash attention off`, and the job log has
  `flash    attention off, recorded for this run` and
  `runner   flash attention: off (asked for off)`;
- `model loaded ..., 100% on the GPU`, then 20 records in each pass, and
  `exit 0`; the status shows 20 and 20 with 0 failing;
- GPU memory: llama-server sets aside all its GPU memory when it loads the
  model (weights, the 16,384-token cache and its working buffers), so a
  longer prompt does not take more. The figures are in the Ollama log:

      grep -h "projected to use\|model buffer size\|KV buffer size\|compute buffer size" $R/logs/ollama_*.log
      grep -hc "CUDA error\|out of memory" $R/logs/ollama_*.log
      grep -h "task.n_tokens" $R/logs/ollama_*.log | grep -o "n_tokens = [0-9]*" | sort -t= -k2 -n | tail -3

  The second line should print 0; the third shows the longest prompts asked.
- if some pass 2 records failed with "the prompt filled the whole context",
  rerun only them with a bigger context (same folder, same setting):
  `NUM_CTX=65536 $B/bcf/code/llm/fir/geo_filter_submit.sh qwen3.6:35b-a3b longest`.
  Tell Claude if that happens, so the full run can do the same.

### C. The full run (Fir), only once B is clean

    RUN_DIR=$B/bcf/runs/needs_llm_qwen3.6-35b-a3b_nofa PARTS=8 \
        $B/bcf/code/llm/fir/geo_filter_submit.sh qwen3.6:35b-a3b needs_llm

Watch it:

    R=$B/bcf/runs/needs_llm_qwen3.6-35b-a3b_nofa
    squeue --me
    python $B/bcf/code/llm/geo_filter_status.py $R --parts 8
    tail -2 $R/logs/bc-filter_*_*.out
    cat $R/logs/failures_part*.txt 2>/dev/null | tail -20

After each part's job ends, by its exit code (the status shows it):

| exit | meaning | what to do |
|---|---|---|
| 0 | part done | nothing |
| 1 | setup problem (wrong flash attention, missing model, prompt check) | read the job log; do not just resubmit |
| 2 | the server stopped answering (5 failures in a row) | resubmit that part; if the same records fail again, tell Claude |
| 3 | another job holds the part | check `squeue --me`; wait |
| 4 | some records failed | resubmit that part; only they are asked again |
| 5 | the model not on the GPU | resubmit that part with `EXCLUDE=<node>` |
| 6 | setup failed or the model did not load | resubmit that part; add `EXCLUDE=<node>` if it repeats |

Resubmit only the parts that need it, with the same PARTS and RUN_DIR (the
status prints the ONLY list):

    RUN_DIR=$R PARTS=8 ONLY=3,5 $B/bcf/code/llm/fir/geo_filter_submit.sh qwen3.6:35b-a3b needs_llm
    RUN_DIR=$R PARTS=8 ONLY=3 EXCLUDE=fc11002 $B/bcf/code/llm/fir/geo_filter_submit.sh qwen3.6:35b-a3b needs_llm

Records that fail with "the prompt filled the whole context" need
`NUM_CTX=65536` in front of the same resubmit line. The merged files
(`pass1/answers.jsonl`, `pass2/answers.jsonl`, `timing.csv`) are written by
the last part to finish once every record has a result.

### D. Copy the run back (Mac, from `backend/`)

    scp -r 'songyanf@fir.alliancecan.ca:/scratch/songyanf/lunaris_llm/bcf/runs/needs_llm_qwen3.6-35b-a3b_nofa' data/lunaris_bc_filter/fir_runs/

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
- **Exit code 5, "fell back to the CPU" or "not entirely on the GPU".**
  Ollama's GPU search timed out on that node. Resubmit with the line
  `status.sh` prints (with `EXCLUDE=` for a node that fails repeatedly).
- **Exit code 6, "SETUP FAILED".** Copying to the node's disk, or building
  Python there, failed or hung; the line says which step. Usually the shared
  filesystem; resubmit later. "not found" means a folder is missing from `$B`
  (step 2b).
- **Exit code 6, "the model did not load".** Ollama started but could not
  load the model in time; see `logs/ollama_*.log`.
- **The server does not start.** Read `logs/ollama_*.log`. If the program will
  not run on Fir at all, the fallback is the Apptainer image
  `docker://ollama/ollama:0.40.0` (ask Claude to adapt the job script), and
  plan B is vLLM.

## Optional clean-up: the old copy on /home

Nothing needs this, and no script deletes anything. Up to 2026-10-19 the
Ollama program and the Python environment were in `~/lunaris_llm` on /home.
Once at least one job has run successfully from `/scratch/$USER/lunaris_llm`
(its log shows `staged ...` lines and `model loaded ..., 100% on the GPU`),
that old copy is no longer used, and it can be removed to free space on /home.

`rm` on Fir is permanent: there is no bin to restore from. Look at what is
there first, and make sure no run or result lives inside it:

    ls -la ~/lunaris_llm
    du -sh ~/lunaris_llm

Then, only if you want the space back:

    rm -r ~/lunaris_llm
