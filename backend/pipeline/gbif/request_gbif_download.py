"""
request_gbif_download.py

First step of the GBIF pipeline: ask GBIF for a Darwin Core Archive download
of every PRESENT record inside BC's boundary, wait for GBIF to build it, and
download the zip. It replaces download_bc_raw.py, which copied a bounding box
out of the S3 snapshot; a download comes with a citable DOI, the media items
and every dataset's metadata. build_dwca_tables.py turns the zip into the
dashboard's tables.

Input:  a WKT polygon file (default bc_boundary_gbif.wkt, written by
        make_gbif_boundary.py)
Output, in --outdir:
    <key>.zip            the Darwin Core Archive
    <key>.download.json  the download key, DOI, status, record count and the
                         request that was sent (without the username or email)

The request (checked against GBIF's API documentation, and the same shape as
the request behind test download 0009659-260928105237408):
  - format DWCA (Darwin Core Archive)
  - predicate: OCCURRENCE_STATUS equals PRESENT, and within the polygon
  - checklistKey: the Catalogue of Life, the same checklist
    match_bcsee_gbif.py matches names against. Without it GBIF uses its old
    backbone taxonomy, whose species keys don't join to anything else here.
  - verbatimExtensions: Multimedia. GBIF then also adds the interpreted
    multimedia.txt that build_dwca_tables.py reads.

How a run goes:
  1. Submit the request. GBIF answers with a download key straight away, and
     <key>.download.json is written at once, so the key is never lost.
  2. Check the status every --poll-minutes until GBIF reports SUCCEEDED. A
     BC-wide download can take hours. Network errors while waiting are
     printed and retried.
  3. Download the zip (to <key>.zip.part, renamed when complete), check its
     size against what GBIF reports, and update <key>.download.json with the
     DOI and record count.

A stopped run continues with --key <key>: it skips step 1. If the zip is
already there at the right size, it is not downloaded again.

Credentials come from three environment variables, the names rgbif and
pygbif also use. They are never printed or saved:
    GBIF_USER   your GBIF.org username (not your email)
    GBIF_PWD    your GBIF.org password
    GBIF_EMAIL  where GBIF sends the "download ready" email
Only submitting needs them. Checking the status and downloading the zip are
public, so --key and --dry-run work without them.

GBIF lets one user run only a few downloads at once. If it answers "too many
downloads" (HTTP 429), wait for one to finish.

Run:
    python request_gbif_download.py --dry-run
    python request_gbif_download.py --wkt /Users/lucia/Desktop/BCBN/Results/bc_boundary_gbif.wkt --outdir ~/bcbn/data
    python request_gbif_download.py --key 0009659-260928105237408 --outdir ~/bcbn/data
"""

import argparse
import json
import os
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import requests
import shapely

# match_bcsee_gbif.py lives in pipeline/bcsee/. Importing the checklist key
# from it keeps both scripts on the same Catalogue of Life checklist.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bcsee"))

from match_bcsee_gbif import CHECKLIST_KEY, HEADERS


API = "https://api.gbif.org/v1/occurrence/download"
REQUEST_URL = f"{API}/request"

# The interpreted multimedia.txt comes with this verbatim extension.
MULTIMEDIA_EXTENSION = "http://rs.gbif.org/terms/1.0/Multimedia"

# Environment variables holding the credentials.
ENV_USER = "GBIF_USER"
ENV_PASSWORD = "GBIF_PWD"
ENV_EMAIL = "GBIF_EMAIL"

# GBIF's download statuses. Still being worked on, finished, or stopped for
# good. SUSPENDED is a pause on GBIF's side, so it counts as still working.
WAITING_STATUSES = {"PREPARING", "RUNNING", "SUSPENDED"}
FAILED_STATUSES = {"CANCELLED", "KILLED", "FAILED", "FILE_ERASED"}

# How long one HTTP call may take before it counts as failed.
TIMEOUT_SECONDS = 60


def read_polygon(wkt_path):
    """Read a WKT polygon from a file, check GBIF can use it, and return it
    as one line of text.

    Stops if the file is not a single valid polygon, or if its points run
    clockwise: GBIF reads a clockwise polygon as the whole world except the
    polygon, so it is never what was meant.
    """
    with open(wkt_path, encoding="utf-8") as f:
        wkt = " ".join(f.read().split())

    geometry = shapely.from_wkt(wkt)
    if geometry.geom_type != "Polygon":
        raise ValueError(f"{wkt_path} holds a {geometry.geom_type}, not a POLYGON")
    if not geometry.is_valid:
        raise ValueError(f"{wkt_path} is not a valid polygon: "
                         f"{shapely.is_valid_reason(geometry)}")
    if not geometry.exterior.is_ccw:
        raise ValueError(
            f"{wkt_path} runs clockwise. GBIF needs the points anti-clockwise "
            f"(make_gbif_boundary.py writes them that way)."
        )

    min_lon, min_lat, max_lon, max_lat = geometry.bounds
    print(f"Polygon: {wkt_path}")
    print(f"  {len(geometry.exterior.coords):,} points, {len(wkt):,} characters, "
          f"lon {min_lon:.3f} to {max_lon:.3f}, lat {min_lat:.3f} to {max_lat:.3f}")
    return wkt


def build_request(wkt, user, email):
    """Return the download request GBIF expects, as a dict."""
    return {
        "creator": user,
        "notificationAddresses": [email],
        "sendNotification": True,
        "format": "DWCA",
        "checklistKey": CHECKLIST_KEY,
        "verbatimExtensions": [MULTIMEDIA_EXTENSION],
        "predicate": {
            "type": "and",
            "predicates": [
                {"type": "equals", "key": "OCCURRENCE_STATUS", "value": "PRESENT"},
                {"type": "within", "geometry": wkt},
            ],
        },
    }


def public_request(request):
    """Return a copy of the request with the username and email taken out,
    safe to print or save."""
    return {k: v for k, v in request.items()
            if k not in ("creator", "notificationAddresses")}


def read_credentials():
    """Return (user, password, email) from the environment, or stop with a
    message naming the variables that are missing (never their values)."""
    values = {name: os.environ.get(name, "").strip()
              for name in (ENV_USER, ENV_PASSWORD, ENV_EMAIL)}
    missing = [name for name, value in values.items() if not value]
    if missing:
        sys.exit(f"Set these environment variables first: {', '.join(missing)}")
    return values[ENV_USER], values[ENV_PASSWORD], values[ENV_EMAIL]


def redact(text, secrets):
    """Return text with every secret value replaced by "***", so GBIF's
    error messages can be printed safely."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


def submit(request, user, password, email):
    """Send the download request to GBIF and return the download key."""
    print(f"\nSubmitting the download request to {REQUEST_URL}...")
    resp = requests.post(
        REQUEST_URL,
        json=request,
        auth=(user, password),
        headers=HEADERS,
        timeout=TIMEOUT_SECONDS,
    )
    if resp.status_code == 201:
        return resp.text.strip()

    message = redact(resp.text.strip(), [user, password, email])
    if resp.status_code == 401:
        sys.exit("GBIF refused the username or password (HTTP 401). Check "
                 f"{ENV_USER} is your GBIF username, not your email.")
    if resp.status_code == 429:
        sys.exit("GBIF says you have too many downloads running (HTTP 429). "
                 "Wait for one to finish, then run this again.")
    sys.exit(f"GBIF rejected the request (HTTP {resp.status_code}): {message}")


def get_status(key):
    """Return GBIF's record for one download (status, DOI, size, ...)."""
    resp = requests.get(f"{API}/{key}", headers=HEADERS, timeout=TIMEOUT_SECONDS)
    resp.raise_for_status()
    return resp.json()


def info_path(outdir, key):
    """Return the path of the file that records one download."""
    return os.path.join(outdir, f"{key}.download.json")


def save_info(outdir, key, request=None, status=None):
    """Write <key>.download.json: the key, the request sent and, once known,
    GBIF's status, DOI and record count. Keeps whatever an earlier run
    already wrote there, so the request survives a resume with --key."""
    path = info_path(outdir, key)
    info = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            info = json.load(f)

    info["download_key"] = key
    if request is not None:
        info["request"] = request
        info.setdefault("submitted", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    if status is not None:
        # GBIF's public record never holds the username or email, but the
        # request is still taken from it through public_request to be sure.
        info["request"] = info.get("request") or public_request(status.get("request", {}))
        info["status"] = status.get("status")
        info["doi"] = status.get("doi")
        info["doi_url"] = f"https://doi.org/{status['doi']}" if status.get("doi") else None
        info["created"] = status.get("created")
        info["modified"] = status.get("modified")
        info["total_records"] = status.get("totalRecords")
        info["number_datasets"] = status.get("numberDatasets")
        info["size_bytes"] = status.get("size")
        info["download_link"] = status.get("downloadLink")
        info["erase_after"] = status.get("eraseAfter")

    with open(path, "w", encoding="utf-8") as f:
        json.dump(info, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return path


def wait_until_ready(key, outdir, poll_minutes):
    """Check the download's status every poll_minutes until GBIF reports
    SUCCEEDED, and return its final record. Stops if the download failed."""
    started = time.time()
    while True:
        try:
            status = get_status(key)
        except requests.RequestException as err:
            # A dropped connection shouldn't end a wait that can last hours.
            print(f"  could not check the status ({err}); trying again in "
                  f"{poll_minutes:g} min")
            time.sleep(poll_minutes * 60)
            continue

        state = status.get("status")
        elapsed = (time.time() - started) / 60
        now = datetime.now().strftime("%H:%M")
        print(f"  {now}  {state}  ({elapsed:.0f} min waited)")
        save_info(outdir, key, status=status)

        if state == "SUCCEEDED":
            return status
        if state in FAILED_STATUSES:
            sys.exit(f"GBIF stopped download {key} with status {state}.")
        if state not in WAITING_STATUSES:
            print(f"  (status {state!r} is not one this script knows; still waiting)")
        time.sleep(poll_minutes * 60)


def download_zip(status, outdir):
    """Download the finished zip to <key>.zip, unless a file of the right
    size is already there, and return its path.

    Writes to <key>.zip.part first and renames it only when the size matches
    what GBIF reported, so a half-downloaded file never looks finished.
    """
    key = status["key"]
    zip_path = os.path.join(outdir, f"{key}.zip")
    expected = status.get("size")

    if os.path.exists(zip_path) and os.path.getsize(zip_path) == expected:
        print(f"\n{zip_path} is already downloaded ({expected:,} bytes).")
        return zip_path

    url = status.get("downloadLink") or f"{REQUEST_URL}/{key}.zip"
    part_path = zip_path + ".part"
    print(f"\nDownloading {url}")
    print(f"  {expected / 1e6:,.1f} MB to {zip_path}")

    with requests.get(url, headers=HEADERS, stream=True,
                      timeout=TIMEOUT_SECONDS, allow_redirects=True) as resp:
        resp.raise_for_status()
        written = 0
        next_report = 0
        with open(part_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8 << 20):
                f.write(chunk)
                written += len(chunk)
                # A line roughly every 10% so a long download shows progress.
                if expected and written >= next_report:
                    print(f"  {written / 1e6:,.0f} MB ({100 * written / expected:.0f}%)")
                    next_report += expected / 10

    if expected is not None and written != expected:
        raise RuntimeError(
            f"Downloaded {written:,} bytes but GBIF says the zip is {expected:,}. "
            f"The partial file is {part_path}; run again with --key {key}."
        )
    os.replace(part_path, zip_path)

    # A cheap check that the file is a readable zip, without unpacking it.
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
    if "occurrence.txt" not in names:
        raise RuntimeError(f"{zip_path} has no occurrence.txt")
    print(f"  done, {len(names)} files in the archive")
    return zip_path


def main():
    """Submit (or pick up) a GBIF download, wait for it and download it."""
    parser = argparse.ArgumentParser(
        description="Request a GBIF Darwin Core Archive download inside a "
        "polygon, wait for it, and download the zip."
    )
    parser.add_argument(
        "--wkt",
        default="/Users/lucia/Desktop/BCBN/Results/bc_boundary_gbif.wkt",
        help="File holding the WKT POLYGON to filter on (from make_gbif_boundary.py).",
    )
    parser.add_argument(
        "--outdir",
        default="/Users/lucia/Desktop/BCBN/Results",
        help="Directory to save the zip and <key>.download.json in.",
    )
    parser.add_argument(
        "--key",
        default=None,
        help="Pick up an existing download by its key: skip the request, "
        "just wait for it and download it.",
    )
    parser.add_argument(
        "--poll-minutes",
        type=float,
        default=5,
        help="Minutes between status checks (default 5).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only print the request that would be sent.",
    )
    args = parser.parse_args()

    if args.key:
        key = args.key.strip()
        print(f"Picking up download {key}")
    else:
        wkt = read_polygon(args.wkt)

        if args.dry_run:
            # Placeholders, not the real values, even if they are set.
            request = build_request(wkt, f"<{ENV_USER}>", f"<{ENV_EMAIL}>")
            print(f"\nDry run. This request would be sent to POST {REQUEST_URL}")
            print(f"with HTTP Basic auth from ${ENV_USER} and ${ENV_PASSWORD}:\n")
            print(json.dumps(request, indent=2))
            return

        user, password, email = read_credentials()
        request = build_request(wkt, user, email)
        key = submit(request, user, password, email)
        os.makedirs(args.outdir, exist_ok=True)
        path = save_info(args.outdir, key, request=public_request(request))
        print(f"GBIF accepted the request. Download key: {key}")
        print(f"Saved to {path}")
        print(f"If this run stops, continue with:\n"
              f"  python request_gbif_download.py --key {key} --outdir {args.outdir}")

    if args.dry_run:
        print("Dry run: not waiting for or downloading anything.")
        return

    os.makedirs(args.outdir, exist_ok=True)
    print(f"\nWaiting for GBIF to build the download "
          f"(checking every {args.poll_minutes:g} min)...")
    status = wait_until_ready(key, args.outdir, args.poll_minutes)
    print(f"Ready: {status.get('totalRecords'):,} records from "
          f"{status.get('numberDatasets'):,} datasets, DOI {status.get('doi')}")

    zip_path = download_zip(status, args.outdir)
    path = save_info(args.outdir, key, status=status)
    print(f"\nZip:  {zip_path}")
    print(f"Info: {path}")
    print(f"DOI:  https://doi.org/{status.get('doi')}")


if __name__ == "__main__":
    main()
