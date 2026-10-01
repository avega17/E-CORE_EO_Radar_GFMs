"""Let an active local MRMS child finish, then resume with a newer snapshot."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def _process(pid):
    stat = Path(f"/proc/{pid}/stat")
    cmdline = Path(f"/proc/{pid}/cmdline")
    try:
        state = stat.read_text().rsplit(") ", 1)[1].split()[0]
        command = cmdline.read_bytes().replace(b"\0", b" ").decode(errors="replace")
        return state, command
    except FileNotFoundError:
        return None


def _status(path, **values):
    values["updated_at"] = datetime.now(timezone.utc).isoformat()
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(values, indent=2) + "\n")
    temporary.replace(target)


def _month_is_complete(output, month, products):
    """Require every requested field's month checkpoint and local marker."""
    folder = Path(output) / "months" / month
    for product in products:
        checkpoint = folder / f"{product}.json"
        try:
            row = json.loads(checkpoint.read_text())
        except (OSError, ValueError):
            return False
        if row.get("month") != month or row.get("product") != product:
            return False
        if row.get("status") == "unavailable":
            continue
        if row.get("status") != "complete" or not row.get("archives"):
            return False
        for archive_name in row["archives"]:
            archive = Path(archive_name)
            marker_path = archive.parent / "complete.json"
            try:
                marker = json.loads(marker_path.read_text())
            except (OSError, ValueError):
                return False
            if (not archive.is_file() or marker.get("product") != product or
                    marker.get("raw_path") != archive.name or
                    archive.stat().st_size != marker.get("stored_bytes")):
                return False
    return True


def _run(args):
    old_parent = _process(args.old_parent)
    old_child = _process(args.old_child)
    if not old_parent or "run_mrms_staged.py" not in old_parent[1] or args.old_snapshot not in old_parent[1]:
        raise RuntimeError("Old supervisor PID does not match the expected frozen script")
    if not old_child or "fetch_mrms_study.py" not in old_child[1] or args.old_snapshot not in old_child[1]:
        raise RuntimeError("Active local fetch PID does not match the expected frozen script")
    manifest = json.loads((Path(args.new_snapshot) / "snapshot.json").read_text())
    stop_products = args.stop_products
    if args.stop_after_month and not stop_products:
        try:
            stop_products = json.loads((Path(args.output) / "run_config.json").read_text())["products"]
        except (OSError, ValueError, KeyError) as error:
            raise RuntimeError("Cannot identify the active product set for the requested stop month") from error
    if args.stop_after_month and not stop_products:
        raise RuntimeError("--stop-after-month requires at least one product checkpoint")
    if args.dry_run:
        print(json.dumps({"old_parent": args.old_parent, "old_child": args.old_child,
            "new_snapshot": args.new_snapshot, "new_sha256": manifest["sha256"],
            "stop_after_month": args.stop_after_month,
            "stop_products": stop_products}))
        return 0
    stopped = False
    try:
        os.kill(args.old_parent, signal.SIGSTOP)
        stopped = True
        _status(args.status, state="waiting_for_local_child", old_parent=args.old_parent,
            old_child=args.old_child, new_snapshot=args.new_snapshot,
            new_sha256=manifest["sha256"])
        print(f"Stopped old supervisor {args.old_parent}; local fetch {args.old_child} continues", flush=True)
        stop_requested = False
        while True:
            child = _process(args.old_child)
            if child is None or child[0] == "Z":
                break
            if "fetch_mrms_study.py" not in child[1] or args.old_snapshot not in child[1]:
                raise RuntimeError("Local fetch PID changed identity while awaiting handoff")
            if (args.stop_after_month and not stop_requested and
                    _month_is_complete(args.output, args.stop_after_month, stop_products)):
                os.kill(args.old_child, signal.SIGINT)
                stop_requested = True
                _status(args.status, state="stopping_after_complete_month",
                    old_parent=args.old_parent, old_child=args.old_child,
                    stop_after_month=args.stop_after_month, stop_products=stop_products,
                    new_snapshot=args.new_snapshot, new_sha256=manifest["sha256"])
                print(f"All requested {args.stop_after_month} product-months are verified; "
                      "sent interrupt so the worker pool can drain", flush=True)
            time.sleep(2 if args.stop_after_month else 30)
        parent = _process(args.old_parent)
        if parent and "run_mrms_staged.py" in parent[1] and args.old_snapshot in parent[1]:
            os.kill(args.old_parent, signal.SIGKILL)
        stopped = False
        command = [sys.executable, str(Path(__file__).resolve().parent / "launch_mrms_background.py"),
            "--snapshot", args.new_snapshot, "--destination", args.destination,
            "--output", args.output, "--scratch", args.scratch]
        for attempt in range(20):
            result = subprocess.run(command, capture_output=True, text=True)
            if result.returncode == 0:
                _status(args.status, state="launched_new_supervisor", old_parent=args.old_parent,
                    old_child=args.old_child, new_snapshot=args.new_snapshot,
                    new_sha256=manifest["sha256"], launcher_output=result.stdout.strip())
                print(result.stdout.strip(), flush=True)
                return 0
            if "earlier staged MRMS supervisor still has PID" not in result.stderr:
                raise RuntimeError(result.stderr.strip())
            time.sleep(1)
        raise RuntimeError("Old supervisor PID did not clear after termination")
    except Exception as error:
        _status(args.status, state="failed", old_parent=args.old_parent,
            old_child=args.old_child, new_snapshot=args.new_snapshot, error=str(error))
        if stopped:
            parent = _process(args.old_parent)
            if parent and "run_mrms_staged.py" in parent[1] and args.old_snapshot in parent[1]:
                os.kill(args.old_parent, signal.SIGCONT)
        raise


def _watch_command(args):
    command = [sys.executable, str(Path(__file__).resolve()),
        "--old-parent", str(args.old_parent), "--old-child", str(args.old_child),
        "--old-snapshot", args.old_snapshot, "--new-snapshot", args.new_snapshot,
        "--destination", args.destination, "--output", args.output,
        "--scratch", args.scratch, "--status", args.status, "--log", args.log]
    if args.stop_after_month:
        command.extend(["--stop-after-month", args.stop_after_month])
    if args.stop_products:
        command.extend(["--stop-products", *args.stop_products])
    command.append("--watch")
    return command


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-parent", type=int, required=True)
    parser.add_argument("--old-child", type=int, required=True)
    parser.add_argument("--old-snapshot", required=True)
    parser.add_argument("--new-snapshot", required=True)
    parser.add_argument("--destination", default="/mnt/p/ecore_eo_datasets")
    parser.add_argument("--output", default="results/study-mrms")
    parser.add_argument("--scratch", default="results/study-scratch")
    parser.add_argument("--status", default="results/study-mrms/handoff_status.json")
    parser.add_argument("--log", default="results/study-mrms/handoff.log")
    parser.add_argument("--stop-after-month",
                        help="Interrupt the active fetch after every configured product in YYYY-MM is verified")
    parser.add_argument("--stop-products", nargs="*",
                        help="Product keys required at the stop month; defaults to products in run_config.json")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--watch", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.dry_run or args.watch:
        return _run(args)
    command = _watch_command(args)
    log = Path(args.log)
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a") as stream:
        watcher = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, start_new_session=True)
    print(f"Handoff watcher PID {watcher.pid}; log: {log}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
