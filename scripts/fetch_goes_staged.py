"""Fetch the GOES ROI in resumable monthly batches, newest study year first."""

from __future__ import annotations

import argparse
from datetime import date
import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ecore_weather.common import default_workers, write_json
from ecore_weather.common import utc


STAGES = [
    ("h1-2026", date(2026, 1, 1), date(2026, 7, 1)),
    ("h2-2025", date(2025, 7, 1), date(2026, 1, 1)),
    ("h1-2025", date(2025, 1, 1), date(2025, 7, 1)),
    ("h2-2024", date(2024, 7, 1), date(2025, 1, 1)),
    ("h1-2024", date(2024, 1, 1), date(2024, 7, 1)),
    ("h2-2023", date(2023, 7, 1), date(2024, 1, 1)),
    ("h1-2023", date(2023, 1, 1), date(2023, 7, 1)),
    ("h2-2022", date(2022, 7, 1), date(2023, 1, 1)),
    ("h1-2022", date(2022, 1, 1), date(2022, 7, 1)),
    ("h2-2021", date(2021, 7, 1), date(2022, 1, 1)),
    ("h1-2021", date(2021, 1, 1), date(2021, 7, 1)),
]
BANDS = (1, 2, 3, 7, 8, 9, 10, 13)


def _months(start, end):
    current = start
    while current < end:
        nxt = date(current.year + (current.month == 12),
                   1 if current.month == 12 else current.month + 1, 1)
        yield current, min(nxt, end)
        current = nxt


def _verify_month(report_dir):
    # The CLI writes product reports under <month>/<product>/, so a direct
    # glob at the month level misses successfully fetched monthly collections.
    reports = sorted(Path(report_dir).rglob("*-monthly.json"),
                     key=lambda path: path.stat().st_mtime_ns)
    if not reports:
        raise FileNotFoundError(f"No monthly fetch report found under {report_dir}")
    report = json.loads(reports[-1].read_text())
    if report.get("source") != "goes" or report.get("interrupted"):
        raise IOError("GOES fetch report marks the month as failed or incomplete")
    archives = []
    for entry in report.get("monthly_archives", []):
        archive = Path(entry.get("path", ""))
        if entry.get("status") not in {"saved", "reused"}:
            raise IOError(f"GOES monthly archive did not complete: {entry}")
        if not archive.is_file() or not (archive.parent / "complete.json").is_file():
            raise IOError(f"GOES archive or completion marker missing: {archive}")
        marker = json.loads((archive.parent / "complete.json").read_text())
        if marker.get("observations") != entry.get("observations"):
            raise IOError(f"GOES observation count differs from marker: {archive}")
        archives.append({"path": str(archive), "band": entry.get("band"),
            "observations": entry.get("observations"), "stored_bytes": entry.get("stored_bytes")})
    if not archives:
        raise IOError("GOES fetch report contains no completed monthly archives")
    available_bands = sorted({int(row["band"]) for row in report.get("records", [])
                              if row.get("band") is not None})
    return report, archives, available_bands


def _reusable_selection(report_dir, start, end):
    """Reuse a saved STAC selection after interruption before its month fetch."""
    path = Path(report_dir) / "ABI-L2-CMIPF" / "items.json"
    if not path.is_file():
        return None
    from ecore_weather.catalog import load_selection
    selection = load_selection(path)
    if (selection.source != "goes" or selection.product != "ABI-L2-CMIPF"
            or utc(selection.start) != utc(start) or utc(selection.end) != utc(end)
            or tuple(selection.bbox) != (-70.24, 14.36, -62.56, 22.04)
            or tuple(selection.bands) != BANDS or selection.scans_per_hour is not None):
        return None
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=[row[0] for row in STAGES] + ["all"], default="all")
    parser.add_argument("--destination", default="/mnt/p/ecore_eo_datasets")
    parser.add_argument("--output", default="results/study-goes-fetch")
    parser.add_argument("--scratch", default="results/study-scratch")
    parser.add_argument("--workers", type=int, default=default_workers())
    parser.add_argument("--monthly-writers", type=int, default=2)
    parser.add_argument("--decode-workers", type=int, default=1)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.workers < 1 or args.monthly_writers < 1 or args.decode_workers < 1:
        parser.error("Worker counts must be positive")
    if not Path(args.destination).is_dir():
        parser.error(f"Local dataset destination does not exist: {args.destination}")
    stages = [row for row in STAGES if args.phase == "all" or row[0] == args.phase]
    command_base = [sys.executable, str(Path(__file__).resolve().parents[1] / "notebooks" / "02_goes.py"),
        "--operation", "fetch", "--destination", args.destination,
        "--bands", *map(str, BANDS), "--scans-per-hour", "0", "--satellite", "auto",
        "--workers", str(args.workers), "--decode-workers", str(args.decode_workers),
        "--monthly-writers", str(args.monthly_writers), "--scratch", args.scratch]
    plan = []
    for name, start, end in stages:
        for month_start, month_end in _months(start, end):
            report_dir = Path(args.output) / name / f"{month_start:%Y-%m}"
            command = command_base + ["--start", month_start.isoformat(), "--end", month_end.isoformat(),
                "--output", str(report_dir)]
            plan.append({"stage": name, "start": month_start.isoformat(),
                "end_excluded": month_end.isoformat(), "report_dir": str(report_dir),
                "command": command})
    if args.dry_run:
        print(json.dumps({"stages": [row[0] for row in stages], "bands": BANDS,
            "scans_per_hour": "all available", "monthly_runs": plan}, indent=2))
        return 0

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "run_config.json", {"stages": [row[0] for row in stages],
        "bands": BANDS, "scan_selection": "all available", "satellite": "auto",
        "destination": str(Path(args.destination).resolve()), "workers": args.workers,
        "monthly_writers": args.monthly_writers, "decode_workers": args.decode_workers,
        "start_order": "H1 2026, then six-month batches descending through H1 2021"})
    completed = []
    start_time = time.perf_counter()
    for month in plan:
        checkpoint = Path(args.output) / "checkpoints" / month["stage"] / f"{month['start']}.json"
        if checkpoint.is_file():
            saved = json.loads(checkpoint.read_text())
            valid = True
            for item in saved.get("archives", []):
                archive = Path(item["path"])
                valid &= archive.is_file() and (archive.parent / "complete.json").is_file()
            if valid and saved.get("status") == "complete":
                completed.append(saved)
                print(f"Reused checkpoint {month['start']}", flush=True)
                continue
        report_dir = Path(month["report_dir"])
        command = month["command"]
        saved_selection = _reusable_selection(report_dir, month["start"], month["end_excluded"])
        if saved_selection:
            command = [*command, "--selection", str(saved_selection)]
            print(f"Reusing saved STAC selection for {month['start']}", flush=True)
        print(f"Fetching GOES {month['start']} through {month['end_excluded']}", flush=True)
        result = subprocess.run(command, check=False)
        if result.returncode:
            failure = {"stage": month["stage"], "month": month["start"],
                "returncode": result.returncode, "command": command}
            write_json(output / "failure.json", failure)
            return result.returncode
        report, archives, available_bands = _verify_month(report_dir)
        row = {"status": "complete", "stage": month["stage"],
            "start": month["start"], "end_excluded": month["end_excluded"],
            "selection_id": report["selection_id"],
            "selected_assets": len(report.get("records", [])),
            "available_bands": available_bands,
            "missing_bands": sorted(set(BANDS) - set(available_bands)),
            "archives": archives, "read_bytes": report.get("read_bytes", 0),
            "stored_bytes": report.get("stored_bytes", 0),
            "wall_seconds": report.get("wall_s")}
        write_json(checkpoint, row)
        completed.append(row)
        print(f"Completed {month['start']}: {row['selected_assets']} source files, "
              f"{len(archives)} band-month archives", flush=True)
    write_json(output / "summary.json", {"status": "complete", "stages": [r[0] for r in stages],
        "completed_months": len(completed), "elapsed_seconds": time.perf_counter()-start_time,
        "months": completed})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
