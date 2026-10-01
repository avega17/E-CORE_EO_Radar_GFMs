"""Resume MRMS study years while a separate worker mirrors completed years to HF."""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ecore_weather.common import write_json
from ecore_weather import mrms

ROOT = Path(__file__).resolve().parent
FIRST_YEAR_MONTHS = 12
REMAINING_STUDY_MONTHS = 54


def checkpoint_count(output, start_year, end_year):
    count = 0
    for year in range(start_year, end_year + 1):
        for month in range(1, 13 if year < 2026 else 7):
            for product in mrms.DEFAULT_PRODUCTS:
                path = Path(output) / "months" / f"{year}-{month:02d}" / f"{product}.json"
                if not path.is_file():
                    continue
                row = json.loads(path.read_text())
                if row.get("status") == "unavailable":
                    count += 1
                elif row.get("status") == "complete" and row.get("archives") and all(
                    Path(item).is_file() and (Path(item).parent / "complete.json").is_file()
                    for item in row["archives"]):
                    count += 1
    return count


def start_mirror_watcher(args, output):
    """Start one detached, year-by-year HF mirror watcher and reuse it on resume."""
    record = output / "mirror-watcher.json"
    if record.is_file():
        try:
            prior = json.loads(record.read_text())
            pid = int(prior["pid"])
            os.kill(pid, 0)
            command = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode()
            if "mirror_mrms_study_background.py" in command:
                print(f"Reusing HF mirror watcher PID {pid}", flush=True)
                return pid
        except (OSError, ValueError, KeyError, ProcessLookupError):
            pass
    script = ROOT / "mirror_mrms_study_background.py"
    command = [sys.executable, str(script), "--source-root", args.destination,
        "--study-output", args.output, "--mirror-output", args.mirror_output,
        "--start-year", "2021", "--end-year", "2026", "--writers", str(args.mirror_writers)]
    log_path = output / "mirror-background.log"
    with log_path.open("a") as log:
        child = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, start_new_session=True,
            env=dict(os.environ, PYTHONUNBUFFERED="1"))
    write_json(record, {"pid": child.pid, "command": command, "log": str(log_path),
        "writers_per_completed_year": args.mirror_writers,
        "started_at": datetime.now(timezone.utc).isoformat()})
    print(f"Started background yearly HF mirror watcher PID {child.pid}; log: {log_path}",
          flush=True)
    return child.pid


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run and resume the complete MRMS study pipeline")
    parser.add_argument("--destination", default="/mnt/p/ecore_eo_datasets")
    parser.add_argument("--output", default="results/study-mrms")
    parser.add_argument("--mirror-output", default="results/mirror-mrms-yearly")
    parser.add_argument("--scratch", default="results/study-scratch",
                        help="Linux-local build directory; defaults to ignored results/study-scratch")
    parser.add_argument("--workers", type=int, default=max(1, __import__("multiprocessing").cpu_count()//2))
    parser.add_argument("--decode-workers", type=int, default=2)
    parser.add_argument("--monthly-writers", type=int, default=2)
    parser.add_argument("--mirror-writers", type=int, default=4,
                        help="Independent HF product-year bundles published concurrently")
    parser.add_argument("--max-hours", type=float, default=12.)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if min(args.workers, args.decode_workers, args.monthly_writers,
           args.mirror_writers) < 1:
        parser.error("Worker counts must be positive")
    fetch_base = [sys.executable, str(ROOT / "fetch_mrms_study.py"),
        "--destination", args.destination, "--output", args.output,
        "--workers", str(args.workers), "--decode-workers", str(args.decode_workers),
        "--monthly-writers", str(args.monthly_writers), "--max-hours", str(args.max_hours)]
    if args.scratch:
        fetch_base.extend(["--scratch", args.scratch])
    mirror = [sys.executable, str(ROOT / "mirror_mrms_study_background.py"),
        "--source-root", args.destination, "--study-output", args.output,
        "--mirror-output", args.mirror_output, "--start-year", "2021",
        "--end-year", "2026", "--writers", str(args.mirror_writers)]
    product_count = len(mrms.DEFAULT_PRODUCTS)
    first_year_target = FIRST_YEAR_MONTHS * product_count
    remaining_target = REMAINING_STUDY_MONTHS * product_count
    total_target = (FIRST_YEAR_MONTHS + REMAINING_STUDY_MONTHS) * product_count
    if args.dry_run:
        print(json.dumps({"first_year": fetch_base + ["--phase", "first-year"],
            "mirror": mirror, "remaining": fetch_base + ["--phase", "remaining"],
            "products": list(mrms.DEFAULT_PRODUCTS),
            "first_year_product_months": first_year_target,
            "remaining_product_months": remaining_target,
            "total_product_months": total_target}, indent=2))
        return 0
    Path(args.scratch).mkdir(parents=True, exist_ok=True)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    started = time.time()
    mirror_pid = None
    for phase, end_year, target in (("first-year", 2021, first_year_target),
                                    ("remaining", 2026, remaining_target)):
        first = 2021 if phase == "first-year" else 2022
        while True:
            before = checkpoint_count(output, first, end_year)
            if before == target:
                break
            print(f"Starting {phase}: {before}/{target} product-month checkpoints", flush=True)
            result = subprocess.run(fetch_base + ["--phase", phase], check=False)
            after = checkpoint_count(output, first, end_year)
            write_json(output / "orchestrator_status.json", {"phase": phase,
                "checkpoints": after, "target": target, "last_exit": result.returncode,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "elapsed_seconds": time.time()-started})
            if result.returncode not in (0, 75):
                return result.returncode
            if after == target:
                break
            if after <= before:
                raise RuntimeError(f"{phase} made no checkpoint progress; inspect the child job")
        if phase == "first-year":
            print("2021 local archives complete; starting detached year-by-year HF mirror",
                  flush=True)
            mirror_pid = start_mirror_watcher(args, output)
    write_json(output / "orchestrator_status.json", {"phase": "complete",
        "checkpoints": total_target, "target": total_target,
        "mirror_watcher_pid": mirror_pid,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": time.time()-started})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
