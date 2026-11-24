"""Tests for the Fir jobs' copy to the node's disk (stage_copy in
pipeline/lunaris/llm/fir/job_setup.sh), run on the Mac with bash.

What happened (DataONE filter 3 dry run, jobs 5552195 and 5553367, and twice
in the filter 2 run): on some nodes "cp -a" failed with
  cp: preserving permissions for '...': Numerical result out of range
and the job stopped with exit 6. stage_copy now copies without keeping
ownership, permission lists or extended attributes (cp -RP
--preserve=timestamps), tries up to 3 times, each into a new empty folder,
and checks the copy holds as many files and executable files as the source.

A stand-in cp, first on PATH, fails with that exact message as many times
as asked and then copies with the Mac's own cp; a stand-in timeout runs the
command (the Mac has none) or, if asked, reports the time limit. The job
script is sourced with "set -euo pipefail", as the .sbatch files do."""

import os
import stat
import subprocess
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
JOB_SETUP = BACKEND / "pipeline" / "lunaris" / "llm" / "fir" / "job_setup.sh"
MESSAGE = ("cp: preserving permissions for '{dst}/__pycache__/filter3_union_sample.cpython-314.pyc': "
           "Numerical result out of range")

# The stand-in cp. It counts its calls in FAKE_CP_STATE and writes its
# arguments to FAKE_CP_LOG. The first FAKE_CP_FAILS calls copy everything
# and then fail with the Fir message, as GNU cp does (it goes on copying and
# reports the error at the end). FAKE_CP_AFTER can spoil a copy that works:
# "drop" removes one file, "noexec" takes the execute bit off bin/ollama.
FAKE_CP = r"""#!/bin/bash
calls=$(( $(cat "$FAKE_CP_STATE" 2>/dev/null || echo 0) + 1 ))
echo "$calls" > "$FAKE_CP_STATE"
echo "$*" >> "$FAKE_CP_LOG"
src="${@: -2:1}"
dst="${@: -1}"
/bin/cp -RP "$src" "$dst" || exit 1
if (( calls <= ${FAKE_CP_FAILS:-0} )); then
    echo "cp: preserving permissions for '$dst/__pycache__/filter3_union_sample.cpython-314.pyc': Numerical result out of range" >&2
    exit 1
fi
case "${FAKE_CP_AFTER:-}" in
    drop) rm "$dst/lib/libggml.so.13" ;;
    noexec) chmod a-x "$dst/bin/ollama" ;;
esac
exit 0
"""

# The stand-in timeout: drop the seconds and run the command, or, with
# FAKE_TIMEOUT_HIT set, end as timeout does when the limit is reached.
FAKE_TIMEOUT = r"""#!/bin/bash
shift
if [[ -n "${FAKE_TIMEOUT_HIT:-}" && "$1" == cp ]]; then
    exit 124
fi
exec "$@"
"""


def write_script(path, text):
    """Write a small executable script."""
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def make_source(folder):
    """
    Make a folder like the Ollama program on /scratch: an executable program,
    two libraries, a link to one of them, and a cached .pyc and a .csv like
    the code folder's. Return the folder.
    """
    (folder / "bin").mkdir(parents=True)
    (folder / "lib").mkdir()
    (folder / "__pycache__").mkdir()
    write_script(folder / "bin" / "ollama", "#!/bin/bash\necho 'ollama version is 0.0-test'\n")
    (folder / "lib" / "libggml.so.13").write_bytes(b"library")
    (folder / "lib" / "libcudart.so.13.0").write_bytes(b"cuda")
    (folder / "lib" / "libcudart.so.13").symlink_to("libcudart.so.13.0")
    (folder / "__pycache__" / "filter3_union_sample.cpython-314.pyc").write_bytes(b"pyc")
    (folder / "bc_places.csv").write_text("name\nKamloops\n", encoding="utf-8")
    for path in folder.rglob("*"):
        if path.is_file() and not path.is_symlink() and path.name != "ollama":
            path.chmod(0o640)       # -rw-r-----, as on /scratch
    return folder


def run_copy(tmp_path, fails=0, after="", timeout_hit=False):
    """
    Source job_setup.sh in bash, as the job scripts do, and stage the made-up
    Ollama program to the node's disk with the stand-in cp and timeout.
    Return bash's exit code, its output, the source folder, the node's stage
    folder and the cp calls made.
    """
    fake_bin = tmp_path / "fake_bin"
    fake_bin.mkdir()
    write_script(fake_bin / "cp", FAKE_CP)
    write_script(fake_bin / "timeout", FAKE_TIMEOUT)
    source = make_source(tmp_path / "scratch" / "ollama")
    node = tmp_path / "node"
    node.mkdir()
    env = dict(os.environ, PATH=f"{fake_bin}:{os.environ['PATH']}", SLURM_TMPDIR=str(node),
               STAGE_TIMEOUT="1800", FAKE_CP_STATE=str(tmp_path / "cp_calls"),
               FAKE_CP_LOG=str(tmp_path / "cp_args"), FAKE_CP_FAILS=str(fails), FAKE_CP_AFTER=after,
               FAKE_TIMEOUT_HIT="1" if timeout_hit else "")
    script = f"""set -euo pipefail
source "{JOB_SETUP}"
mkdir -p "$STAGE"
stage_copy "Ollama program" "{source}" "$STAGE/ollama"
echo "THE JOB GOES ON"
"""
    done = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=60)
    log = tmp_path / "cp_args"
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return done.returncode, done.stdout + done.stderr, source, node / "lunaris", calls


def listing(folder):
    """Return every path under a folder, relative to it, with links shown as links."""
    return sorted((str(p.relative_to(folder)), p.is_symlink()) for p in folder.rglob("*"))


# --- The copy goes on after a failed try ------------------------------------------

def test_one_failed_try_is_reported_and_the_second_try_is_used(tmp_path):
    code, output, source, stage, calls = run_copy(tmp_path, fails=1)
    assert code == 0 and "THE JOB GOES ON" in output
    assert len(calls) == 2
    # One line for the failed try, with cp's own message.
    failed = [line for line in output.splitlines() if "failed:" in line]
    assert failed == ["copy     Ollama program, try 1 of 3 failed: exit 1: "
                      + MESSAGE.format(dst=f"{stage}/ollama.try1")]
    assert "staged   Ollama program:" in output and "(try 2 of 3)" in output
    # The copy is complete, at the path the job uses, and the program still runs.
    copy = stage / "ollama"
    assert listing(copy) == listing(source)
    assert os.access(copy / "bin" / "ollama", os.X_OK)
    assert subprocess.run([str(copy / "bin" / "ollama")], capture_output=True, text=True).stdout.startswith(
        "ollama version")
    assert (copy / "lib" / "libcudart.so.13").is_symlink()
    # The failed try is left where it was; the second try was a new, empty folder.
    assert (stage / "ollama.try1").is_dir() and not (stage / "ollama.try2").exists()


def test_a_copy_that_works_first_time_is_as_before(tmp_path):
    code, output, source, stage, calls = run_copy(tmp_path)
    assert code == 0 and "THE JOB GOES ON" in output and "failed" not in output
    assert len(calls) == 1 and "(try" not in output
    assert listing(stage / "ollama") == listing(source)


def test_cp_is_not_asked_to_keep_permissions_owners_or_attributes(tmp_path):
    _, _, source, stage, calls = run_copy(tmp_path)
    args = calls[0].split()
    assert args[:2] == ["-RP", "--preserve=timestamps"]
    assert args[2:] == [f"{source}/.", f"{stage}/ollama.try1"]
    for kept in ("-a", "-p", "--archive", "mode", "ownership", "xattr", "all", "context"):
        assert not any(kept == a or kept in a.split("=")[-1].split(",") for a in args), kept


def test_file_times_are_kept(tmp_path):
    # The Mac's cp is called without -p by the stand-in, so only check that the
    # option is asked for; on Fir, GNU cp keeps the times with it.
    _, _, _, _, calls = run_copy(tmp_path)
    assert "--preserve=timestamps" in calls[0].split()


# --- Three failed tries stop the job ---------------------------------------------

def test_three_failed_tries_stop_the_job_with_exit_6(tmp_path):
    code, output, _, stage, calls = run_copy(tmp_path, fails=3)
    assert code == 6 and "THE JOB GOES ON" not in output
    assert len(calls) == 3
    failed = [line for line in output.splitlines() if "failed:" in line]
    assert [line.split(" failed:")[0] for line in failed] == [
        f"copy     Ollama program, try {n} of 3" for n in (1, 2, 3)]
    assert all("Numerical result out of range" in line for line in failed)
    assert "SETUP FAILED  copying Ollama program from" in output and "failed 3 times" in output
    assert "exit 6" in output.splitlines()[-1]
    # Each try had its own folder.
    assert all((stage / f"ollama.try{n}").is_dir() for n in (1, 2, 3))


def test_two_failed_tries_still_go_on(tmp_path):
    code, output, source, stage, calls = run_copy(tmp_path, fails=2)
    assert code == 0 and "THE JOB GOES ON" in output and len(calls) == 3
    assert "(try 3 of 3)" in output
    assert listing(stage / "ollama") == listing(source)


def test_the_time_limit_is_reported_per_try(tmp_path):
    code, output, _, _, _ = run_copy(tmp_path, timeout_hit=True)
    assert code == 6
    assert sum("failed: took over 1800 s" in line for line in output.splitlines()) == 3


# --- The copy must be complete ----------------------------------------------------

def test_a_copy_with_a_file_missing_stops_the_job(tmp_path):
    code, output, source, stage, _ = run_copy(tmp_path, after="drop")
    assert code == 6 and "THE JOB GOES ON" not in output
    # The source has 6 files and links: the program, 3 libraries (one a link), a .pyc and a .csv.
    assert f"SETUP FAILED  the copy of Ollama program in {stage}/ollama has 5 files, but {source} has 6" in output


def test_a_program_that_lost_its_execute_bit_stops_the_job(tmp_path):
    code, output, source, stage, _ = run_copy(tmp_path, after="noexec")
    assert code == 6
    assert f"the copy of Ollama program in {stage}/ollama has 0 executable files, but {source} has 1" in output


def test_a_folder_already_there_is_not_copied_over(tmp_path):
    # The stage is new for every job; a folder already there means something is wrong.
    (tmp_path / "node" / "lunaris" / "ollama").mkdir(parents=True)
    fake_bin = tmp_path / "fake_bin"
    fake_bin.mkdir()
    write_script(fake_bin / "timeout", FAKE_TIMEOUT)
    source = make_source(tmp_path / "scratch" / "ollama")
    env = dict(os.environ, PATH=f"{fake_bin}:{os.environ['PATH']}", SLURM_TMPDIR=str(tmp_path / "node"))
    script = f'set -euo pipefail\nsource "{JOB_SETUP}"\nstage_copy "Ollama program" "{source}" "$STAGE/ollama"\n'
    done = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=60)
    assert done.returncode == 6 and "it is already there" in done.stdout


def test_the_copy_keeps_the_source_permission_bits_less_the_umask(tmp_path):
    _, _, source, stage, _ = run_copy(tmp_path)
    copy = stage / "ollama"
    umask = os.umask(0)
    os.umask(umask)
    for path in source.rglob("*"):
        if path.is_file() and not path.is_symlink():
            mode = stat.S_IMODE(path.stat().st_mode)
            assert stat.S_IMODE((copy / path.relative_to(source)).stat().st_mode) == mode & ~umask


@pytest.mark.parametrize("caller", ["stage_code_and_python", "stage_ollama"])
def test_the_callers_still_use_the_same_paths(caller):
    # The copy ends at the path asked for, so CODE_DIR, OLLAMA_BIN, OLLAMA_MODELS
    # and the GeoNames folder of geo_filter.sbatch are unchanged.
    text = JOB_SETUP.read_text(encoding="utf-8")
    body = text.split(f"{caller}() {{")[1].split("\n}\n")[0]
    expected = {"stage_code_and_python": ['stage_copy code "$SOURCE_CODE" "$STAGE/code"', 'CODE_DIR="$STAGE/code"'],
                "stage_ollama": ['"$STAGE/ollama"', 'OLLAMA_BIN="$STAGE/ollama/bin/ollama"',
                                 'export OLLAMA_MODELS="$STAGE/models"']}[caller]
    for line in expected:
        assert line in body
