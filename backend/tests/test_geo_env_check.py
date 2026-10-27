"""Tests for the BC filter job's first check (pipeline/lunaris/llm/geo_env_check.py):
finding the PROJ library in a process's map of loaded files, finding the
PROJ data installed next to it, the full check on this machine, and that the
BC filter's code imports without pyarrow (which the Fir job does not have)."""

import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
# The pipeline scripts are not a package; add their folders to the path the
# way the scripts themselves do.
sys.path.insert(0, str(BACKEND / "pipeline" / "lunaris"))
sys.path.insert(0, str(BACKEND / "pipeline" / "lunaris" / "llm"))
import geo_env_check as env  # noqa: E402

BC_BOUNDARY = Path("/Users/lucia/Desktop/BCBN/Results/bc_boundary_gbif.wkt")


def test_loaded_libraries_reads_the_map(tmp_path):
    """The PROJ library is picked out of a /proc/self/maps listing, once, and other files are left out."""
    lib = "/cvmfs/soft/proj/9.4.1/lib64/libproj.so.25.9.4.1"
    maps = tmp_path / "maps"
    maps.write_text(f"7f00-7f10 r--p 00000000 00:2a 123 {lib}\n"
                    f"7f10-7f20 r-xp 00010000 00:2a 123 {lib}\n"
                    "7f20-7f30 r--p 00000000 00:2a 456 /usr/lib64/libc.so.6\n"
                    "7f30-7f40 rw-p 00000000 00:00 0\n")
    assert env.loaded_libraries("libproj", str(maps)) == [lib]
    assert env.loaded_libraries("libproj", str(tmp_path / "missing")) == []


def test_proj_data_is_the_share_folder_next_to_the_library(tmp_path):
    """The data folder is ../share/proj beside the library, and only if it holds proj.db."""
    lib = tmp_path / "proj" / "9.4.1" / "lib64" / "libproj.so.25"
    lib.parent.mkdir(parents=True)
    lib.write_text("")
    assert env.proj_data_next_to(str(lib)) is None
    data = tmp_path / "proj" / "9.4.1" / "share" / "proj"
    data.mkdir(parents=True)
    (data / "proj.db").write_text("")
    assert env.proj_data_next_to(str(lib)) == str(data)


def test_the_check_passes_here():
    """On this machine every package is there and the projection and box shares are the Mac's."""
    if not BC_BOUNDARY.exists():
        return
    assert env.check(str(BC_BOUNDARY)) == []


def test_the_bc_filter_imports_without_pyarrow():
    """bc_filter_rules and the run script import with pyarrow blocked, as on the Fir job's node."""
    code = ("import sys; sys.modules['pyarrow'] = None; sys.modules['pyarrow.parquet'] = None; "
            f"sys.path[:0] = [{str(BACKEND / 'pipeline' / 'lunaris' / 'llm')!r}, "
            f"{str(BACKEND / 'pipeline' / 'lunaris')!r}]; "
            "import bc_filter_rules, geo_filter_run; print('ok')")
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
