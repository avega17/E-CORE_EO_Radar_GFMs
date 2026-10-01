"""Mirror one verified local MRMS study year to HF through Earth2Studio async IO."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import subprocess
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ecore_weather.common import write_json
from ecore_weather import mrms
from ecore_weather.remote_async import mirror_local_month
from ecore_weather.storage import configured_bucket

_PRINT_LOCK = threading.Lock()


def _mirror_one(relative, entry, remote_root):
    archive, marker = entry["archive"], entry["marker"]
    started = time.perf_counter()

    def report_progress(done, total):
        if done == total or done % max(1, total // 20) == 0:
            with _PRINT_LOCK:
                print(f"Mirroring {relative}: {done}/{total}", flush=True)

    result = mirror_local_month(archive, marker, remote_root, relative,
                                progress=report_progress)
    return {"archive": relative, "status": result["status"],
        "path": result["path"], "observations": result["observations"],
        "stored_bytes": result["stored_bytes"],
        "selected_by": entry["selected_by"],
        "wall_seconds": time.perf_counter()-started}


def _publish_batches(archives, remote_root, output, monthly_writers):
    """Publish independent archive prefixes in bounded batches.

    The main thread alone writes completion reports; each worker owns one
    local ZIP and one stable remote product-month prefix.
    """
    summary, errors = [], []
    items = sorted(archives.items())
    with ThreadPoolExecutor(max_workers=monthly_writers,
                            thread_name_prefix="hf-month") as pool:
        for start in range(0, len(items), monthly_writers):
            batch = items[start:start + monthly_writers]
            futures = {pool.submit(_mirror_one, relative, entry, remote_root): relative
                       for relative, entry in batch}
            for future in as_completed(futures):
                relative = futures[future]
                try:
                    record = future.result()
                except Exception as error:
                    errors.append({"product_month": relative,
                        "error": f"{type(error).__name__}: {error}"})
                    continue
                summary.append(record)
                write_json(output / "archives" / f"{relative}.json", record)
                print(f"Mirrored {relative}: {record['observations']} observations, "
                      f"{record['wall_seconds']:.1f}s", flush=True)
            if errors:
                pending = [key for key, _ in items[start + len(batch):]]
                return summary, errors, pending
    return summary, errors, []


def main(argv=None):
    parser = argparse.ArgumentParser(description="Mirror a complete local MRMS study year to HF")
    parser.add_argument("--year", type=int, default=2021)
    parser.add_argument("--source-root", default="/mnt/p/ecore_eo_datasets")
    parser.add_argument("--study-output", default="results/study-mrms")
    parser.add_argument("--output", default="results/mirror-mrms-2021")
    parser.add_argument("--remote-root", help="HF bucket prefix; defaults to configured noaa-subsets")
    parser.add_argument("--monthly-writers", type=int, default=2,
        help="Independent monthly HF archives to publish concurrently (start with 2)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.monthly_writers < 1:
        parser.error("--monthly-writers must be positive")
    local_root = Path(args.source_root).resolve()
    output = Path(args.output)
    remote_root = args.remote_root or f"hf://buckets/{configured_bucket()}/noaa-subsets"
    if not remote_root.startswith("hf://buckets/"):
        parser.error("--remote-root must be an HF bucket prefix")
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    snapshot_manifest = Path(__file__).resolve().parents[1] / "snapshot.json"
    snapshot_sha256 = (json.loads(snapshot_manifest.read_text())["sha256"]
                       if snapshot_manifest.is_file() else None)
    checkpoints = []
    archives = {}
    for month in range(1, 13):
        for product in mrms.DEFAULT_PRODUCTS:
            checkpoint = Path(args.study_output) / "months" / f"{args.year}-{month:02d}" / f"{product}.json"
            if not checkpoint.is_file():
                raise FileNotFoundError(f"The local study year is unfinished: {checkpoint}")
            row = json.loads(checkpoint.read_text())
            if row.get("status") == "unavailable":
                checkpoints.append({"checkpoint": checkpoint, "status": "unavailable"})
                continue
            if row.get("status") != "complete" or not row.get("archives"):
                raise ValueError(f"Local product-month is not verified: {checkpoint}")
            selection_path = checkpoint.parent / f"{product}-selection" / "items.json"
            selected_ids = {item["id"] for item in
                json.loads(selection_path.read_text()).get("features", [])}
            if len(selected_ids) != row.get("matched_slots"):
                raise ValueError(f"Saved selection count differs from checkpoint: {selection_path}")
            local_markers = []
            for archive_name in row["archives"]:
                archive = Path(archive_name).resolve()
                if not archive.is_relative_to(local_root):
                    raise ValueError(f"Archive is outside source root: {archive}")
                marker_path = archive.parent / "complete.json"
                if not archive.is_file() or not marker_path.is_file():
                    raise FileNotFoundError(f"Local archive or marker is missing: {archive}")
                marker = json.loads(marker_path.read_text())
                if (marker.get("raw_path") != archive.name or marker.get("source") != "mrms" or
                    marker.get("product") != product or marker.get("asset_ids") is None or
                    marker.get("observations") != len(marker["asset_ids"]) or
                    archive.stat().st_size != marker.get("stored_bytes") or
                    not marker.get("archive_sha256")):
                    raise ValueError(f"Local marker does not verify this archive: {marker_path}")
                local_markers.append(marker)
                relative = archive.parent.relative_to(local_root).as_posix()
                entry = archives.setdefault(relative, {"archive": archive, "marker": marker,
                    "selected_by": []})
                if entry["marker"].get("asset_ids") != marker["asset_ids"]:
                    raise ValueError(f"Archive marker changed while referenced by the year: {relative}")
                entry["selected_by"].append(checkpoint.parent.name + "/" + product)
            archived_ids = {asset_id for marker in local_markers for asset_id in marker["asset_ids"]}
            if not selected_ids.issubset(archived_ids):
                raise ValueError(f"A selected source file is absent from archives: {checkpoint}")
            checkpoints.append({"checkpoint": checkpoint, "status": "complete",
                "selected_observations": len(selected_ids), "archives": row["archives"]})
    if args.dry_run:
        print(json.dumps({"year": args.year,
            "available_product_months": sum(row["status"] == "complete" for row in checkpoints),
            "unavailable_product_months": sum(row["status"] == "unavailable" for row in checkpoints),
            "unique_local_archives": len(archives),
            "monthly_writers": args.monthly_writers,
            "archive_months": sorted({Path(key).parent.name for key in archives}),
            "source_root": str(local_root), "remote_root": remote_root,
            "revision": revision, "code_snapshot_sha256": snapshot_sha256}, indent=2))
        return 0
    # Earth2Studio emits a debug line for every asynchronous chunk write.
    # A full year would otherwise produce a very large operational log.
    from loguru import logger
    logger.remove()
    logger.add(sys.stderr, level="WARNING")
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "run_config.json", {"year": args.year,
        "source_root": str(local_root), "remote_root": remote_root,
        "git_revision": revision, "code_snapshot_sha256": snapshot_sha256,
        "python": sys.executable})
    summary, errors, pending = _publish_batches(
        archives, remote_root, output, args.monthly_writers)
    if errors:
        write_json(output / "failure.json", {"errors": errors,
            "completed": summary, "pending": pending})
        return 1
    checkpoint_rows = [{"product_month": row["checkpoint"].parent.name + "/" + row["checkpoint"].stem,
        "status": row["status"], "selected_observations": row.get("selected_observations", 0),
        "archives": row.get("archives", [])} for row in checkpoints]
    write_json(output / "summary.json", {"year": args.year, "status": "complete",
        "monthly_writers": args.monthly_writers,
        "product_months": checkpoint_rows, "archives": summary,
        "available_product_months": sum(row["status"] == "complete" for row in checkpoints),
        "unavailable_product_months": sum(row["status"] == "unavailable" for row in checkpoints),
        "unique_archives": len(summary),
        "remote_bytes": sum(row.get("stored_bytes", 0) for row in summary)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
