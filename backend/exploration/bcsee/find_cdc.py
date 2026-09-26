"""
Find every Conservation Data Centre package, without guessing at words.

The earlier attempts searched for phrases like "Conservation Data Centre".
A phrase only appears in a record because a person chose to type it, so a
record where nobody typed it is invisible no matter how carefully the phrase
is chosen. One real package is titled "CDC  BIOTICS Occurence Attributes",
with a doubled space and a misspelling, which is the shape of the problem.

The province also marks these records in ways nobody types by hand:

  the contact address     cdcdata@gov.bc.ca is the address listed for
                          Conservation Data Centre data
  the database table      the CDC's own database is called BIOTICS, and its
                          tables in the provincial warehouse are all named
                          BIOT_something
  the warehouse schema    WHSE_TERRESTRIAL_ECOLOGY, which is wider than the
                          CDC but holds most of its spatial layers

This runs all four, the three structural marks and the old phrase, keeps them
apart, and prints which ones found each package. A package found by the
contact address and not by the phrase is exactly what the phrase was missing,
and now it has a name instead of being a worry.

This reads the cache that survey_bcdc.py already downloaded. It makes no
network calls at all.

Usage:

    python find_cdc.py
    python find_cdc.py --cache ../bcdc/cache/bcdc_all_packages.json
"""

import argparse
import csv
import json
import sys
from pathlib import Path


# Each entry is (short name, the text to look for, which thing it marks, why).
#
# Two different things are being looked for here, and keeping them apart is
# the point. The Conservation Data Centre is an organisation. BIOTICS is the
# database it keeps. The BC Species and Ecosystems Explorer is a website that
# shows part of that database. So the Explorer sits inside the CDC's holdings
# rather than being the same as them, and asking for one when you wanted the
# other gets you either too much or too little.
IDENTIFIERS = [
    ("bcsee", "bcsee", "BCSEE",
     "the Explorer's short name, which appears in its published file names"),
    ("eswp", "eswp", "BCSEE",
     "the Explorer application's internal code, from its web address"),
    ("explorer", "species and ecosystems explorer", "BCSEE",
     "the Explorer's full name spelled out, which a person has to type"),
    ("contact", "cdcdata@gov.bc.ca", "CDC",
     "the address the province lists for Conservation Data Centre data"),
    ("biotics", "biot_", "CDC",
     "BIOTICS is the CDC's database; its warehouse tables all start BIOT_"),
    ("schema", "whse_terrestrial_ecology", "CDC",
     "the warehouse schema CDC spatial layers sit in, wider than the CDC"),
    ("phrase", "conservation data centre", "CDC",
     "what the old keyword search matched, kept for comparison only"),
]


def load(path):
    """
    Read the catalogue that survey_bcdc.py downloaded earlier.

    Failing here with a clear sentence is better than failing later with a
    stack trace, because the usual cause is simply that the survey has not
    been run yet on this machine.
    """
    if not path.exists():
        print(f"  no cache at {path}")
        print("  run survey_bcdc.py first, it downloads the catalogue once")
        return None
    with open(path, encoding="utf-8") as fh:
        packages = json.load(fh)
    print(f"  {len(packages):,} packages loaded from {path.name}")
    return packages


def raw_text(pkg):
    """
    Turn the whole package record into one lowercase string.

    Every other version of this picked a few fields to look at and then
    missed whatever lived in the others. Turning the entire record into text
    means there is no field left out, because no field was chosen. It is
    crude, and being crude is the point: a mark can be anywhere in the record
    and this will still see it.
    """
    return json.dumps(pkg, ensure_ascii=False).lower()


def where(pkg, needle):
    """
    Name the parts of the record a mark was found in.

    Knowing that a package matched is not the same as knowing why. If the
    contact address turns up under "contacts" that is the province labelling
    the record, and if it turns up in the free text of the description it may
    just be somebody being helpful, which is weaker evidence.
    """
    hits = []
    for key, value in pkg.items():
        if needle in json.dumps(value, ensure_ascii=False).lower():
            hits.append(key)
    return hits


def find(packages):
    """
    Run every identifier over every package and keep the results apart.

    They are deliberately not merged yet. The interesting result is not the
    total, it is which identifier found what, because that is the only way to
    see what any single one of them would have missed on its own.
    """
    texts = {pkg["id"]: raw_text(pkg) for pkg in packages}
    found = {}
    print()
    print("=" * 78)
    print("EACH MARK ON ITS OWN")
    print("=" * 78)
    for scope in ["BCSEE", "CDC"]:
        print()
        print(f"  marks of the {scope}")
        print()
        for short, needle, sc, why in IDENTIFIERS:
            if sc != scope:
                continue
            hit = {pid for pid, t in texts.items() if needle in t}
            found[short] = hit
            print(f"    {short:10s} {len(hit):5,}   {needle}")
            print(f"    {'':10s}         {why}")
    return found


def nesting(found):
    """
    Say how the Explorer's packages sit inside the wider CDC set.

    This is the question that started the confusion, so it gets answered with
    counts rather than with an explanation. If every BCSEE package also
    carries a CDC mark then the Explorer really is a window onto the CDC's
    holdings and searching for one reaches the other. If some do not, then
    they are separate things that merely overlap, and which one you ask for
    changes the answer you get.
    """
    bcsee = set().union(*[found[s] for s, _, sc, _ in IDENTIFIERS
                          if sc == "BCSEE"])
    cdc = set().union(*[found[s] for s, _, sc, _ in IDENTIFIERS
                        if sc == "CDC"])
    print()
    print("=" * 78)
    print("HOW THE TWO SETS RELATE")
    print("=" * 78)
    print()
    print(f"  {len(bcsee):5,}  carry a mark of the Explorer")
    print(f"  {len(cdc):5,}  carry a mark of the Conservation Data Centre")
    print(f"  {len(bcsee & cdc):5,}  carry both")
    print(f"  {len(bcsee - cdc):5,}  Explorer only, no CDC mark at all")
    print(f"  {len(cdc - bcsee):5,}  CDC only, nothing to do with the Explorer")
    print()
    if not (bcsee - cdc):
        print("  Every Explorer package is also marked as CDC, so the Explorer")
        print("  is a view onto the CDC's holdings rather than a separate set.")
        print("  Asking for the CDC gets you the Explorer and a good deal more.")
    else:
        print("  Some Explorer packages carry no CDC mark, so the two are not")
        print("  nested and asking for one will not get you all of the other.")
    return bcsee, cdc


def compare(packages, found):
    """
    Put the four results side by side, one row per package.

    A row ticked under "contact" and blank under "phrase" is a package the
    old keyword search could never have reached. Those rows are the whole
    reason for doing this, so they are listed first.
    """
    by_id = {p["id"]: p for p in packages}
    union = set().union(*found.values()) if found else set()
    marks = [short for short, _, _, _ in IDENTIFIERS]

    rows = []
    for pid in union:
        got = [m for m in marks if pid in found[m]]
        rows.append((by_id[pid], got))

    # Fewest marks first, so the packages that only one method caught, which
    # are the ones most easily missed, are at the top rather than buried.
    rows.sort(key=lambda r: (len(r[1]), r[0].get("title") or ""))

    print("=" * 78)
    print(f"ALL {len(union)} PACKAGES ANY MARK FOUND")
    print("=" * 78)
    print()
    header = "  " + "".join(f"{m:9s}" for m in marks) + " title"
    print(header)
    print("  " + "-" * 74)
    for pkg, got in rows:
        ticks = "".join(f"{'yes' if m in got else '-':9s}" for m in marks)
        print(f"  {ticks} {(pkg.get('title') or '')[:38]}")

    missed = [(p, g) for p, g in rows if "phrase" not in g]
    print()
    print("=" * 78)
    print("WHAT THE OLD KEYWORD SEARCH WOULD HAVE MISSED")
    print("=" * 78)
    print()
    if not missed:
        print("  Nothing. Every package a structural mark found, the phrase")
        print("  found too. That is worth knowing, and it is not something")
        print("  the keyword search could ever have told you about itself.")
    else:
        for pkg, got in missed:
            org = (pkg.get("organization") or {}).get("title") or "?"
            print(f"  {pkg.get('title')}")
            print(f"    found by   {', '.join(got)}")
            print(f"    org        {org}")
            print(f"    licence    {pkg.get('license_title')}")
            for short, needle, _, _ in IDENTIFIERS:
                if short in got:
                    print(f"    '{needle}' appears in: "
                          f"{', '.join(where(pkg, needle)) or '?'}")
            print()
    return rows


def write_csv(rows, path):
    """Write the combined list, with a column per mark, for reading later."""
    marks = [short for short, _, _, _ in IDENTIFIERS]
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["title", "organisation", "licence", "formats", "url"]
                   + marks)
        for pkg, got in rows:
            formats = sorted({(r.get("format") or "?").upper()
                              for r in pkg.get("resources") or []})
            w.writerow([
                pkg.get("title"),
                (pkg.get("organization") or {}).get("title"),
                pkg.get("license_title"),
                " ".join(formats),
                f"https://catalogue.data.gov.bc.ca/dataset/{pkg.get('name')}",
            ] + ["yes" if m in got else "" for m in marks])
    print(f"  wrote {path.name}  ({len(rows)} rows)")


def main():
    """Load the cached catalogue and find the CDC packages structurally."""
    parser = argparse.ArgumentParser(description=__doc__)
    here = Path(__file__).parent
    parser.add_argument("--cache",
                        default=str(here.parent / "bcdc" / "cache"
                                    / "bcdc_all_packages.json"),
                        help="The catalogue file survey_bcdc.py wrote.")
    parser.add_argument("--out-dir", default=str(here),
                        help="Where to write the result table.")
    args = parser.parse_args()

    print("=" * 78)
    print("READING THE CACHED CATALOGUE")
    print("=" * 78)
    print()
    packages = load(Path(args.cache).expanduser())
    if not packages:
        return 1

    found = find(packages)
    nesting(found)
    rows = compare(packages, found)

    out = Path(args.out_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    write_csv(rows, out / "cdc_packages.csv")

    print()
    print("=" * 78)
    print("WHAT THIS STILL CANNOT SEE")
    print("=" * 78)
    print()
    print("  A CDC record with none of these four marks is still invisible.")
    print("  The three structural marks are much harder to omit than a phrase")
    print("  in a description, but harder is not impossible, and only the")
    print("  Conservation Data Centre itself can confirm the list is whole.")
    print("  That is a question for cdcdata@gov.bc.ca, not for a script.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
