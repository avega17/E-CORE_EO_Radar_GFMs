"""Audit GOES estimates, MRMS monthly checkpoints, and optional HF year bundles."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ecore_weather import mrms
from ecore_weather.common import write_json
from ecore_weather.hf_storage import BucketWriter
from ecore_weather.storage import configured_bucket


SCENARIOS = {"eight_6ph", "eight_3ph", "eight_1ph", "sixteen_6ph"}


def _months():
    return [f"{year}-{month:02d}" for year in range(2021, 2027)
            for month in range(1, 13 if year < 2026 else 7)]


def audit(goes_root, mrms_root, local_root, check_hf=False, remote_root=None,
          yearly_output="results/mirror-mrms-yearly"):
    errors = []
    goes_root, mrms_root = map(Path, (goes_root, mrms_root))
    local_root = Path(local_root).resolve()
    months = _months()
    summary_path = goes_root / "summary.json"
    goes_ok = False
    if summary_path.is_file():
        summary = json.loads(summary_path.read_text())
        goes_ok = (summary.get("study_start") == "2021-01-01T00:00:00Z"
                   and summary.get("study_end_excluded") == "2026-07-01T00:00:00Z"
                   and summary.get("months_complete") == 66
                   and summary.get("sample_complete") is True
                   and summary.get("inventory_only") is False
                   and set(summary.get("scenarios", {})) == SCENARIOS
                   and all((goes_root / "inventory" / f"{month}.json").is_file()
                           for month in months))
    if not goes_ok:
        errors.append("GOES 66-month inventory, bounded samples, or four scenarios are incomplete")

    writer = BucketWriter(remote_root or
        f"hf://buckets/{configured_bucket()}/noaa-subsets/yearly-v1") if check_hf else None
    states = Counter()
    hf_states = Counter()
    hf_year_states = {}
    missing = []
    hf_expected = {}
    for month in months:
        for product in mrms.DEFAULT_PRODUCTS:
            checkpoint = mrms_root / "months" / month / f"{product}.json"
            label = f"{month}/{product}"
            if not checkpoint.is_file():
                states["missing_checkpoint"] += 1
                missing.append(label)
                continue
            try:
                row = json.loads(checkpoint.read_text())
                if row.get("month") != month or row.get("product") != product:
                    raise ValueError("checkpoint identity differs")
                if row.get("status") == "unavailable":
                    states["unavailable"] += 1
                    continue
                if row.get("status") != "complete" or not row.get("archives"):
                    raise ValueError("checkpoint is not complete or lists no archives")
                selection_path = checkpoint.parent / f"{product}-selection" / "items.json"
                features = json.loads(selection_path.read_text()).get("features", [])
                feature_map = {item["id"]: item for item in features}
                selection_ids = set(feature_map)
                invalid_records = row.get("invalid_source_files", [])
                invalid_ids = {item["asset_id"] for item in invalid_records}
                if len(selection_ids) != row.get("listed_slots", row.get("matched_slots")):
                    raise ValueError("saved STAC selection count differs from listed slots")
                if len(invalid_ids) != len(invalid_records) or not invalid_ids.issubset(selection_ids):
                    raise ValueError("invalid-source records are duplicate or absent from the STAC selection")
                for invalid in invalid_records:
                    feature = feature_map[invalid["asset_id"]]
                    source = feature.get("properties", {}).get("ecore:source", {})
                    if (invalid.get("reason") != "zero_byte_noaa_object" or
                        invalid.get("size") != 0 or source.get("size") != 0 or
                        source.get("key") != invalid.get("key") or
                        source.get("time") != invalid.get("observation_time") or
                        source.get("etag") != invalid.get("etag")):
                        raise ValueError("invalid-source record does not match a zero-byte STAC item")
                if len(selection_ids) - len(invalid_ids) != row.get("matched_slots"):
                    raise ValueError("valid source count differs from matched slots")
                archive_markers = []
                for archive_name in row["archives"]:
                    archive = Path(archive_name).resolve()
                    if not archive.is_file() or not archive.is_relative_to(local_root):
                        raise ValueError(f"archive missing or outside local root: {archive}")
                    marker = json.loads((archive.parent / "complete.json").read_text())
                    if (marker.get("raw_path") != archive.name or marker.get("source") != "mrms" or
                        marker.get("product") != product or
                        marker.get("observations") != len(marker.get("asset_ids", [])) or
                        archive.stat().st_size != marker.get("stored_bytes") or
                        not marker.get("archive_sha256")):
                        raise ValueError(f"local archive manifest differs: {archive}")
                    archive_markers.append((archive, marker))
                available_ids = {asset_id for _, marker in archive_markers
                                 for asset_id in marker["asset_ids"]}
                if not (selection_ids - invalid_ids).issubset(available_ids):
                    raise ValueError("one or more valid selected sources are absent from archives")
                if available_ids.intersection(invalid_ids):
                    raise ValueError("an unavailable zero-byte NOAA object was archived")
                states["complete"] += 1
            except Exception as error:
                states["invalid"] += 1
                missing.append(label)
                errors.append(f"{label}: {type(error).__name__}: {error}")
    total = len(months) * len(mrms.DEFAULT_PRODUCTS)
    mrms_complete = states["complete"] + states["unavailable"] == total
    if not mrms_complete:
        errors.append(f"MRMS has {total - states['complete'] - states['unavailable']} unfinished product-months")
    hf_complete = None
    if check_hf:
        yearly_output = Path(yearly_output)
        years = range(2021, 2027)
        hf_states["expected_product_bundles"] = len(mrms.DEFAULT_PRODUCTS) * len(years)
        for year in years:
            year_output = yearly_output / str(year)
            summary_path = year_output / "summary.json"
            try:
                summary = json.loads(summary_path.read_text())
            except (OSError, ValueError):
                summary = {}
            saved_bundles = {row.get("product"): row for row in summary.get("bundles", [])}
            year_verified = 0
            year_errors = []
            for product in mrms.DEFAULT_PRODUCTS:
                label = f"HF-yearly:{year}/{product}"
                try:
                    local_bundle_path = year_output / f"{product}-local-bundle.json"
                    remote_report = saved_bundles.get(product)
                    if not local_bundle_path.is_file() or not remote_report:
                        raise FileNotFoundError("local bundle or remote upload report is missing")
                    local_bundle = json.loads(local_bundle_path.read_text())
                    remote = writer.marker(f"mrms/{product}/{year}")
                    expected_hash = local_bundle.get("sha256")
                    expected_size = local_bundle.get("size")
                    if (summary.get("status") != "complete" or summary.get("year") != year or
                        remote_report.get("status") not in {"saved", "reused"} or
                        remote_report.get("archive_sha256") != expected_hash or
                        remote_report.get("stored_bytes") != expected_size or
                        not remote or remote.get("source") != "mrms" or
                        remote.get("product") != product or remote.get("year") != year or
                        remote.get("archive_sha256") != expected_hash or
                        remote.get("stored_bytes") != expected_size or
                        remote.get("manifest_sha256") != local_bundle.get("manifest_sha256") or
                        not remote.get("raw_path")):
                        raise ValueError("yearly completion marker or saved read-back report differs")
                    key = writer.key(f"mrms/{product}/{year}/{remote['raw_path']}")
                    head = writer.client.head_object(Bucket=writer.config["bucket"], Key=key)
                    if head.get("ContentLength") != expected_size:
                        raise ValueError("yearly object size differs from verified local bundle")
                    year_verified += 1
                    hf_states["verified_product_bundles"] += 1
                except Exception as error:
                    year_errors.append(label)
                    errors.append(f"{label}: {type(error).__name__}: {error}")
                    hf_states["missing_or_mismatch"] += 1
                    missing.append(label)
            hf_year_states[str(year)] = {"verified": year_verified,
                "expected": len(mrms.DEFAULT_PRODUCTS), "errors": year_errors}
        hf_complete = hf_states["verified_product_bundles"] == hf_states["expected_product_bundles"]
        if not hf_complete:
            errors.append("One or more MRMS HF yearly backups are incomplete")
    return {"checked_at": datetime.now(timezone.utc).isoformat(),
        "goes_complete": goes_ok, "mrms_complete": mrms_complete,
        "hf_yearly_complete": hf_complete,
        "hf_2021_complete": hf_year_states.get("2021", {}).get("verified") == len(mrms.DEFAULT_PRODUCTS) if check_hf else None,
        "mrms_expected_product_months": total,
        "hf_expected_product_bundles": hf_states.get("expected_product_bundles"),
        "mrms_states": dict(states), "hf_states": dict(hf_states),
        "hf_year_states": hf_year_states,
        "unfinished_examples": missing[:25], "errors": errors[:25],
        # HF verification is optional; when requested it becomes part of the
        # completion gate, otherwise report completion of the local study data.
        "complete": goes_ok and mrms_complete and hf_complete is not False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--goes-root", default="results/study-goes-estimate")
    parser.add_argument("--mrms-root", default="results/study-mrms")
    parser.add_argument("--local-root", default="/mnt/p/ecore_eo_datasets")
    parser.add_argument("--check-hf", action="store_true")
    parser.add_argument("--remote-root")
    parser.add_argument("--yearly-output", default="results/mirror-mrms-yearly")
    parser.add_argument("--output", default="results/study-completion-audit.json")
    args = parser.parse_args(argv)
    report = audit(args.goes_root, args.mrms_root, args.local_root,
                   args.check_hf, args.remote_root, args.yearly_output)
    write_json(args.output, report)
    print(json.dumps(report, indent=2))
    return 0 if report["complete"] else 75


if __name__ == "__main__":
    raise SystemExit(main())
