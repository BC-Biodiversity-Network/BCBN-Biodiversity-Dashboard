"""
geo_env_check.py

Check, in a few seconds, that the Python packages the BC filter's job needs
are there and work as on the Mac, before the job does anything else
(fir/geo_filter.sbatch runs it first). It never changes anything.

Two uses:

  python geo_env_check.py --proj-data
      Print the PROJ data folder that belongs to the PROJ library pyproj
      has loaded, or nothing if it cannot be found. On Fir, pyproj comes as
      an Alliance wheel built against a PROJ installed on CVMFS, and its
      data (proj.db) is not found by itself ("Valid PROJ data directory not
      found"). The data sits next to that library, in ../share/proj; the job
      sets PROJ_DATA to it, so the data always matches the library. On the
      Mac, pyproj brings its own data, and this prints nothing.

  python geo_env_check.py --bc-boundary bc_boundary_gbif.wkt
      Import shapely, pyproj, duckdb, pandas and numpy and print their
      versions (and pyarrow's, which the job does not need); check pyproj
      found its data folder without a warning and that the folder's proj.db
      comes from the same PROJ version as the library; project one point
      to the equal-area projection the box notes use (EPSG:6933); and
      measure three fixed boxes against BC's outline. The point and the
      shares must agree with the Mac's (MAC_POINT, MAC_SHARES). Exit 0 if
      all is well, 1 with a line saying what is wrong.
"""

import argparse
import os
import sys
import warnings

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

# On the Mac (pyproj 3.8.0, PROJ 9.8.1): longitude -123, latitude 49 in EPSG:6933.
MAC_POINT = (-11867812.47086027, 5530553.823420606)

# On the Mac: three boxes and (share of the box in BC, share of BC covered,
# size against BC), from bc_filter_rules.measure_box with bc_boundary_gbif.wkt.
MAC_SHARES = [
    ({"w": -125.0, "s": 50.0, "e": -122.0, "n": 52.0}, (1.0, 0.04453798001944964, 0.04453798001944964)),
    ({"w": -121.0, "s": 49.0, "e": -112.0, "n": 56.0}, (0.38712167907211936, 0.17507859729652003, 0.45225727920007164)),
    ({"w": -141.0, "s": 41.0, "e": -100.0, "n": 70.0}, (0.1271670815296432, 1.0000000000000007, 7.863670282996124)),
]

# How far a number may differ from the Mac's: far below anything that could
# change a whole percent in a box note, only enough for the last digits of
# floating point arithmetic.
TOLERANCE = 1e-9


def loaded_libraries(name, maps="/proc/self/maps"):
    """Return the paths of the shared libraries this process has loaded whose file name contains name (Linux only)."""
    if not os.path.exists(maps):
        return []
    paths = []
    with open(maps, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            path = line.split()[-1] if len(line.split()) >= 6 else ""
            if name in os.path.basename(path) and path not in paths:
                paths.append(path)
    return paths


def proj_data_next_to(library):
    """Return the PROJ data folder installed with a PROJ library (../share/proj holding proj.db), or None."""
    folder = os.path.dirname(os.path.realpath(library))
    for candidate in (os.path.join(folder, "..", "share", "proj"), os.path.join(folder, "..", "..", "share", "proj")):
        if os.path.exists(os.path.join(candidate, "proj.db")):
            return os.path.normpath(candidate)
    return None


def find_proj_data():
    """Return the data folder of the PROJ library pyproj has loaded, or None (see the top of the file)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        import pyproj  # noqa: F401  (loads the PROJ library)
        from pyproj import Transformer  # noqa: F401
    for library in loaded_libraries("libproj"):
        found = proj_data_next_to(library)
        if found:
            return found
    return None


def close(a, b):
    """Return True if two numbers agree within TOLERANCE (relative to their size, at least 1)."""
    return abs(a - b) <= TOLERANCE * max(1.0, abs(a), abs(b))


def check(bc_boundary):
    """Run the checks described at the top of the file, printing one line each. Return the problems found."""
    problems = []
    for name in ("pandas", "numpy", "duckdb", "shapely"):
        try:
            module = __import__(name)
            print(f"check    {name} {module.__version__}")
        except ImportError as error:
            problems.append(f"{name} is missing ({error})")
    try:
        import pyarrow
        print(f"check    pyarrow {pyarrow.__version__} (not needed by this job)")
    except ImportError:
        print("check    pyarrow not installed (not needed by this job)")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            import pyproj
            import pyproj.database
            import pyproj.datadir
            from pyproj import Transformer
        except ImportError as error:
            problems.append(f"pyproj is missing ({error})")
            return problems
    for warning in caught:
        problems.append(f"pyproj warned: {str(warning.message).splitlines()[0]}")
    data_dir = pyproj.datadir.get_data_dir()
    libraries = loaded_libraries("libproj")
    print(f"check    pyproj {pyproj.__version__}, PROJ library {pyproj.proj_version_str}"
          + (f" ({libraries[0]})" if libraries else ""))
    print(f"check    PROJ data {data_dir} (PROJ_DATA={os.environ.get('PROJ_DATA', '')})")
    if not data_dir or not os.path.exists(os.path.join(data_dir, "proj.db")):
        problems.append(f"no proj.db in the PROJ data folder {data_dir!r}")
        return problems
    data_version = pyproj.database.get_database_metadata("PROJ.VERSION") or "unknown"
    print(f"check    proj.db from PROJ {data_version}")
    if data_version.split(".")[:2] != pyproj.proj_version_str.split(".")[:2]:
        problems.append(f"proj.db comes from PROJ {data_version}, but the library is PROJ "
                        f"{pyproj.proj_version_str}; set PROJ_DATA to that version's share/proj")
    try:
        point = Transformer.from_crs("EPSG:4326", "EPSG:6933", always_xy=True).transform(-123.0, 49.0)
    except Exception as error:  # any PROJ failure is reported, not raised
        problems.append(f"projecting to EPSG:6933 failed ({error})")
        return problems
    same = all(close(a, b) for a, b in zip(point, MAC_POINT))
    print(f"check    EPSG:6933 of (-123, 49): {point}, {'the same as' if same else 'NOT the same as'} the Mac's")
    if not same:
        problems.append(f"EPSG:6933 point {point} differs from the Mac's {MAC_POINT}")

    from bc_filter_rules import load_bc_shape, measure_box
    shape = load_bc_shape(bc_boundary)
    for box, expected in MAC_SHARES:
        measured = measure_box(box, shape)
        same = all(close(a, b) for a, b in zip(measured, expected))
        print(f"check    box {box}: in BC {measured[0]:.12f}, covers {measured[1]:.12f}, size {measured[2]:.12f}; "
              f"{'the same as' if same else 'NOT the same as'} the Mac's")
        if not same:
            problems.append(f"box {box} measures {measured}, the Mac {expected}")
    return problems


def main():
    """Print the PROJ data folder (--proj-data), or run the checks and return 0 if all is well, else 1."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("--proj-data", action="store_true", help="only print the PROJ data folder to use")
    parser.add_argument("--bc-boundary", help="bc_boundary_gbif.wkt, for the box check")
    args = parser.parse_args()
    if args.proj_data:
        print(find_proj_data() or "")
        return 0
    if not args.bc_boundary:
        parser.error("give --proj-data or --bc-boundary")
    problems = check(args.bc_boundary)
    for problem in problems:
        print(f"PROBLEM  {problem}")
    print("check    all well" if not problems else f"check    {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
