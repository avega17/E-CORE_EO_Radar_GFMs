"""Start the frozen MRMS supervisor after the GOES estimate has completed."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone


def main(argv=None):
    parser = argparse.ArgumentParser(description="Launch the staged MRMS study from a source snapshot")
    parser.add_argument("--snapshot", required=True, help="Directory printed by create_study_snapshot.py")
    parser.add_argument("--goes-summary", default="results/study-goes-estimate/summary.json")
    parser.add_argument("--output", default="results/study-mrms")
    parser.add_argument("--destination", default="/mnt/p/ecore_eo_datasets")
    parser.add_argument("--scratch", default="results/study-scratch")
    parser.add_argument("--workers", type=int, default=16,
                        help="NOAA source-read threads per product-month writer (bounded at 16)")
    parser.add_argument("--decode-workers", type=int, default=2,
                        help="MRMS decode slots per product-month writer")
    parser.add_argument("--monthly-writers", type=int, default=2,
                        help="Independent product-month archive writers")
    parser.add_argument("--max-hours", type=float, default=12.0,
                        help="Maximum time per child fetch before pausing at a month boundary")
    parser.add_argument("--mirror-writers", type=int, default=4,
                        help="Independent 2021 HF product-year bundle writers")
    args = parser.parse_args(argv)
    if min(args.workers, args.decode_workers, args.monthly_writers,
           args.mirror_writers) < 1 or args.max_hours <= 0:
        parser.error("Worker counts and max-hours must be positive")
    summary_path = Path(args.goes_summary)
    if not summary_path.is_file():
        parser.error("The GOES estimate has not completed; run the jobs separately")
    goes = json.loads(summary_path.read_text())
    if not goes.get("sample_complete") or goes.get("months_complete") != 66:
        parser.error("GOES study inventory or crop sampling is incomplete")
    snapshot = Path(args.snapshot).resolve()
    manifest = json.loads((snapshot / "snapshot.json").read_text())
    for entry in manifest["files"]:
        import hashlib
        path = snapshot / entry["path"]
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            parser.error(f"Code snapshot differs: {path}")
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    pid_file = output / "launcher.json"
    if pid_file.is_file():
        old = json.loads(pid_file.read_text())
        old_pid = int(old["pid"])
        try:
            os.kill(old_pid, 0)
        except ProcessLookupError:
            pass
        except PermissionError:
            parser.error(f"Cannot inspect earlier staged MRMS PID {old_pid}")
        else:
            stat = Path(f"/proc/{old_pid}/stat")
            try:
                state = stat.read_text().rsplit(") ", 1)[1].split()[0]
            except FileNotFoundError:
                state = None
            if state not in (None, "Z"):
                parser.error(f"An earlier staged MRMS supervisor still has PID {old_pid}")
    log_path = output / "staged.log"
    command = [sys.executable, str(snapshot / "scripts" / "run_mrms_staged.py"),
        "--destination", args.destination, "--output", str(output),
        "--scratch", args.scratch, "--workers", str(args.workers),
        "--decode-workers", str(args.decode_workers),
        "--monthly-writers", str(args.monthly_writers),
        "--max-hours", str(args.max_hours),
        "--mirror-writers", str(args.mirror_writers)]
    environment = dict(os.environ, PYTHONUNBUFFERED="1")
    with log_path.open("a") as log:
        child = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, start_new_session=True, env=environment)
    pid_file.write_text(json.dumps({"pid": child.pid, "command": command,
        "code_snapshot_sha256": manifest["sha256"], "log": str(log_path),
        "workers": args.workers, "decode_workers": args.decode_workers,
        "monthly_writers": args.monthly_writers, "max_hours": args.max_hours,
        "mirror_writers": args.mirror_writers,
        "started_at": datetime.now(timezone.utc).isoformat()}, indent=2) + "\n")
    print(f"Started staged MRMS supervisor PID {child.pid}; log: {log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
