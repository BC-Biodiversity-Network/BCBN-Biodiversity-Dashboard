"""
safe_outputs.py

Helpers so a pipeline step never overwrites good outputs with bad ones.

A step writes each output to a temporary name in the same folder first
(species_hex.parquet becomes species_hex.tmp.parquet, bc_hex_r5.csv.gz
becomes bc_hex_r5.tmp.csv.gz), runs all its checks on the temporary files,
and only then replaces the real files. If a check fails, the real files are
left exactly as they were, and the temporary files stay where they are so
they can be looked at.

Used by build_species_tables.py and build_hex_aggregates.py.
"""

import os


def temporary_path(path):
    """Return the temporary name for an output: ".tmp" goes before the
    file's extensions, so "a/b.parquet" gives "a/b.tmp.parquet" and
    "a/b.csv.gz" gives "a/b.tmp.csv.gz"."""
    folder, name = os.path.split(path)
    stem, dot, extensions = name.partition(".")
    return os.path.join(folder, f"{stem}.tmp{dot}{extensions}")


def clear_temporary(paths):
    """Delete temporary files left over from an earlier run, so a new run
    never reads or reports an old one by mistake. paths are the real
    output paths."""
    for path in paths:
        temporary = temporary_path(path)
        if os.path.exists(temporary):
            os.remove(temporary)


def replace_outputs(paths):
    """Move each temporary file onto its real name. Each move is a single
    rename in the same folder, so a real file is never half written."""
    for path in paths:
        os.replace(temporary_path(path), path)


def report_not_replaced(paths):
    """Print which real files were NOT replaced and where the temporary
    files are, after a failed check."""
    print("\nA check FAILED, so these files were NOT replaced (the previous "
          "versions are untouched):")
    for path in paths:
        state = "kept as it was" if os.path.exists(path) else "does not exist yet"
        print(f"  {path}  ({state})")
    print("The new files from this run are left here for inspection:")
    for path in paths:
        print(f"  {temporary_path(path)}")
