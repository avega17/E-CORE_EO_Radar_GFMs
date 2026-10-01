"""Wait for the full MRMS audit, remove verified legacy MRMS trees, then fetch GOES."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from audit_study_completion import audit
from ecore_weather.common import write_json


def _commands(args):
    py = sys.executable
    cleanup = [py, str(ROOT / "scripts/cleanup_legacy_archives.py"),
        "--dataset-root", args.destination, "--mrms-output", args.mrms_output,
        "--goes-output", args.goes_estimate, "--apply",
        "--report", str(Path(args.output) / "legacy-cleanup-report.json")]
    goes = [py, str(ROOT / "scripts/fetch_goes_staged.py"), "--phase", "all",
        "--destination", args.destination, "--output", args.output,
        "--scratch", args.scratch, "--workers", str(args.workers),
        "--monthly-writers", str(args.monthly_writers),
        "--decode-workers", str(args.decode_workers)]
    return cleanup, goes


def run(args):
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    status_path = output / "mrms-gate-status.json"
    log_path = output / "mrms-gate.log"
    cleanup, goes = _commands(args)
    if args.dry_run:
        evidence = audit(args.goes_estimate, args.mrms_output, args.destination)
        print(json.dumps({"mrms_complete": evidence["mrms_complete"],
            "full_audit_complete": evidence["complete"], "audit": evidence,
            "after_gate": [cleanup, goes], "poll_seconds": args.poll_seconds}, indent=2))
        return 0 if evidence["complete"] else 75

    while True:
        evidence = audit(args.goes_estimate, args.mrms_output, args.destination)
        if evidence["complete"]:
            break
        write_json(status_path, {"state": "waiting_for_mrms_audit",
            "checked_at": evidence["checked_at"], "complete_product_months":
                evidence["mrms_states"].get("complete", 0),
            "expected_product_months": evidence["mrms_expected_product_months"],
            "unfinished_examples": evidence["unfinished_examples"][:10]})
        time.sleep(args.poll_seconds)

    write_json(status_path, {"state": "legacy_cleanup", "audit": evidence,
        "started_at": time.time()})
    with log_path.open("a") as log:
        cleanup_result = subprocess.run(cleanup, cwd=ROOT, stdout=log,
                                        stderr=subprocess.STDOUT, check=False)
        if cleanup_result.returncode:
            write_json(status_path, {"state": "cleanup_failed",
                "returncode": cleanup_result.returncode, "audit": evidence})
            return cleanup_result.returncode
        cleanup_report = json.loads((output / "legacy-cleanup-report.json").read_text())
        if cleanup_report.get("status") != "deleted":
            raise RuntimeError("Legacy cleanup returned success without a deleted report")
        write_json(status_path, {"state": "goes_fetch", "audit": evidence,
            "legacy_mrms_deleted": cleanup_report.get("legacy_mrms_deleted", 0),
            "goes_command": goes, "started_at": time.time()})
        goes_result = subprocess.run(goes, cwd=ROOT, stdout=log,
                                     stderr=subprocess.STDOUT, check=False)
    if goes_result.returncode:
        write_json(status_path, {"state": "goes_fetch_failed",
            "returncode": goes_result.returncode, "command": goes,
            "legacy_mrms_deleted": cleanup_report.get("legacy_mrms_deleted", 0)})
        return goes_result.returncode
    summary = json.loads((output / "summary.json").read_text())
    if summary.get("status") != "complete" or summary.get("completed_months") != 66:
        raise RuntimeError("GOES staged fetch exited successfully without all 66 month checkpoints")
    write_json(status_path, {"state": "complete", "legacy_mrms_deleted":
        cleanup_report.get("legacy_mrms_deleted", 0), "goes_months": 66,
        "finished_at": time.time()})
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", default="/mnt/p/ecore_eo_datasets")
    parser.add_argument("--mrms-output", default="results/study-mrms")
    parser.add_argument("--goes-estimate", default="results/study-goes-estimate")
    parser.add_argument("--output", default="results/study-goes-fetch")
    parser.add_argument("--scratch", default="results/study-scratch")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--monthly-writers", type=int, default=4)
    parser.add_argument("--decode-workers", type=int, default=1)
    parser.add_argument("--poll-seconds", type=int, default=300)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if min(args.workers, args.monthly_writers, args.decode_workers,
           args.poll_seconds) < 1:
        parser.error("Worker and poll counts must be positive")
    if not Path(args.destination).is_dir():
        parser.error(f"Dataset destination is not mounted: {args.destination}")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
