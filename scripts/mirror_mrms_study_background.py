"""Mirror completed MRMS study years while the local fetch proceeds.

The watcher waits for all product-month checkpoints in each calendar year,
then delegates that immutable year to the existing verified HF bundler. Only
one year bundle job runs at a time; that job uses independent product uploads.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent
DEFAULT_PRODUCTS = (
    "PrecipRate_00.00",
    "MergedReflectivityQCComposite_00.50",
    "MergedAzShear_0-2kmAGL_00.50",
    "MultiSensor_QPE_01H_Pass2_00.00",
)


def _year_months(year):
    return range(1, 7 if year == 2026 else 13)


def _year_complete(study_output, year, products=DEFAULT_PRODUCTS):
    """Require every in-scope month/product to have a verified local result."""
    root = Path(study_output) / "months"
    for month in _year_months(year):
        key = f"{year}-{month:02d}"
        for product in products:
            path = root / key / f"{product}.json"
            try:
                row = json.loads(path.read_text())
            except (OSError, ValueError):
                return False
            if row.get("month") != key or row.get("product") != product:
                return False
            if row.get("status") == "unavailable":
                continue
            if row.get("status") != "complete" or not row.get("archives"):
                return False
            for archive_name in row["archives"]:
                archive = Path(archive_name)
                try:
                    marker = json.loads((archive.parent / "complete.json").read_text())
                except (OSError, ValueError):
                    return False
                if (not archive.is_file() or marker.get("source") != "mrms" or
                        marker.get("product") != product or marker.get("raw_path") != archive.name or
                        archive.stat().st_size != marker.get("stored_bytes")):
                    return False
    return True


def _bundle_is_complete(output, year, products=DEFAULT_PRODUCTS):
    path = Path(output) / str(year) / "summary.json"
    try:
        summary = json.loads(path.read_text())
    except (OSError, ValueError):
        return False
    return (summary.get("status") == "complete" and summary.get("year") == year and
            {row.get("product") for row in summary.get("bundles", [])} == set(products))


def _write_status(path, **values):
    """Atomically publish the watcher's current year and completed years."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"updated_at": datetime.now(timezone.utc).isoformat(), **values}
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, prefix=".mirror-status-",
                                     delete=False) as stream:
        json.dump(payload, stream, indent=2)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


def run(args):
    output = Path(args.mirror_output)
    output.mkdir(parents=True, exist_ok=True)
    status_path = output / "background-status.json"
    completed_years = []
    for year in range(args.start_year, args.end_year + 1):
        year_output = output / str(year)
        if _bundle_is_complete(output, year):
            print(f"Reused completed HF year mirror {year}", flush=True)
            completed_years.append(year)
            _write_status(status_path, state="waiting_for_year", completed_years=completed_years,
                waiting_for_year=year + 1 if year < args.end_year else None,
                last_completed_year=year, writers=args.writers)
            continue
        print(f"Waiting for all local {year} product-month archives", flush=True)
        _write_status(status_path, state="waiting_for_year", completed_years=completed_years,
            waiting_for_year=year, writers=args.writers)
        while not _year_complete(args.study_output, year):
            try:
                status = json.loads(status_path.read_text())
            except (OSError, ValueError):
                status = {}
            if status.get("stop_requested"):
                _write_status(status_path, state="stopped", completed_years=completed_years,
                    waiting_for_year=year, writers=args.writers)
                return 0
            time.sleep(args.poll_seconds)
        command = [sys.executable, str(ROOT / "mirror_mrms_year_bundle.py"),
            "--year", str(year), "--source-root", args.source_root,
            "--study-output", args.study_output, "--output", str(year_output),
            "--writers", str(args.writers)]
        _write_status(status_path, state="mirroring", year=year,
            completed_years=completed_years, command=command, writers=args.writers,
            started_at=datetime.now(timezone.utc).isoformat())
        result = subprocess.run(command, check=False)
        if result.returncode:
            _write_status(status_path, state="failed", year=year,
                completed_years=completed_years, returncode=result.returncode,
                command=command, writers=args.writers)
            return result.returncode
        if not _bundle_is_complete(output, year):
            _write_status(status_path, state="failed", year=year,
                completed_years=completed_years,
                error="Year bundle exited successfully without a complete summary",
                command=command, writers=args.writers)
            return 1
        completed_years.append(year)
        print(f"Completed verified HF year mirror {year}", flush=True)
        _write_status(status_path, state="waiting_for_year", completed_years=completed_years,
            last_completed_year=year,
            waiting_for_year=year + 1 if year < args.end_year else None,
            writers=args.writers)
    _write_status(status_path, state="complete", completed_years=completed_years,
        through_year=args.end_year, writers=args.writers)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", default="/mnt/p/ecore_eo_datasets")
    parser.add_argument("--study-output", default="results/study-mrms")
    parser.add_argument("--mirror-output", default="results/mirror-mrms-yearly")
    parser.add_argument("--start-year", type=int, default=2021)
    parser.add_argument("--end-year", type=int, default=2026)
    parser.add_argument("--writers", type=int, default=4)
    parser.add_argument("--poll-seconds", type=int, default=60)
    args = parser.parse_args(argv)
    if args.start_year < 2021 or args.end_year > 2026 or args.end_year < args.start_year:
        parser.error("Year range must be within 2021–2026")
    if args.writers < 1 or args.poll_seconds < 1:
        parser.error("writers and poll-seconds must be positive")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
