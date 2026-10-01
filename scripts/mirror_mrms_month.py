"""Mirror one verified P: MRMS month through the direct Earth2Studio HF writer."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ecore_weather.common import write_json
from ecore_weather.remote_async import mirror_local_month
from ecore_weather.storage import configured_bucket


def main(argv=None):
    parser = argparse.ArgumentParser(description="Mirror one local MRMS month to the HF bucket")
    parser.add_argument("archive", help="Verified local raw.zarr.zip path")
    parser.add_argument("--source-root", default="/mnt/p/ecore_eo_datasets")
    parser.add_argument("--output", default="results/mirror-mrms-month.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    archive = Path(args.archive).resolve()
    root = Path(args.source_root).resolve()
    marker_path = archive.parent / "complete.json"
    if not archive.is_file() or not marker_path.is_file():
        parser.error("The local archive and its completion marker must exist")
    marker = json.loads(marker_path.read_text())
    if marker.get("source") != "mrms" or marker.get("raw_path") != archive.name:
        parser.error("The completion marker does not describe this MRMS archive")
    if archive.stat().st_size != marker.get("stored_bytes"):
        parser.error("Local archive size differs from its completion marker")
    checksum = hashlib.sha256()
    with archive.open("rb") as stream:
        for block in iter(lambda: stream.read(8*1024*1024), b""):
            checksum.update(block)
    if checksum.hexdigest() != marker.get("archive_sha256"):
        parser.error("Local archive SHA-256 differs from its completion marker")
    relative = archive.parent.relative_to(root).as_posix()
    remote_root = f"hf://buckets/{configured_bucket()}/noaa-subsets"
    if args.dry_run:
        print(json.dumps({"archive": str(archive), "relative": relative,
            "observations": marker.get("observations"), "remote_root": remote_root}, indent=2))
        return 0
    from loguru import logger
    logger.remove()
    logger.add(sys.stderr, level="WARNING")
    start = time.perf_counter()
    result = mirror_local_month(archive, marker, remote_root, relative,
        progress=lambda done, total: print(f"Remote month {relative}: {done}/{total}", flush=True))
    result["wall_seconds"] = time.perf_counter() - start
    write_json(args.output, result)
    print(json.dumps({"status": result["status"], "observations": result["observations"],
        "stored_bytes": result["stored_bytes"], "wall_seconds": result["wall_seconds"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
