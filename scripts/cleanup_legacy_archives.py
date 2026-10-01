"""Remove legacy MRMS archives only after the full local MRMS audit passes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ecore_weather.mrms import PRODUCTS
from ecore_weather.common import write_json
from audit_study_completion import audit


def legacy_mrms_trees(root):
    """Find prior one-observation MRMS trees from their subset descriptor."""
    root = Path(root).resolve()
    candidates, current, unclassified = [], [], []
    if not root.is_dir():
        return candidates, current, unclassified
    for product_dir in root.iterdir():
      if not product_dir.is_dir() or product_dir.name not in PRODUCTS:
        continue
      for roi_dir in product_dir.glob("roi-*"):
        if not roi_dir.is_dir():
            continue
        descriptor = roi_dir / "subset.json"
        if not descriptor.is_file():
            monthly_markers = list(roi_dir.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/complete.json"))
            if monthly_markers:
                try:
                    marker = json.loads(monthly_markers[0].read_text())
                except (OSError, json.JSONDecodeError):
                    unclassified.append(str(roi_dir))
                    continue
                if marker.get("writer_backend") == "earth2studio-zarr-backend" and marker.get("assets"):
                    current.append(str(roi_dir))
                else:
                    unclassified.append(str(roi_dir))
            else:
                unclassified.append(str(roi_dir))
            continue
        try:
            subset = json.loads(descriptor.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if (subset.get("source") != "mrms" or subset.get("product") != product_dir.name
            or subset.get("subset_id") != roi_dir.name.removeprefix("roi-")):
            unclassified.append(str(roi_dir))
            continue
        monthly = list(roi_dir.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/raw.zarr.zip"))
        if monthly:
            current.append(str(roi_dir))
            continue
        years = sorted(path for path in roi_dir.iterdir() if path.is_dir() and path.name.isdigit())
        sample_marker = None
        for year in years[:1]:
            months = sorted(path for path in year.iterdir() if path.is_dir() and path.name.isdigit())
            for month in months[:1]:
                days = sorted(path for path in month.iterdir() if path.is_dir() and path.name.isdigit())
                for day in days[:1]:
                    times = sorted(path for path in day.iterdir() if path.is_dir())
                    for stamp in times[:1]:
                        marker_path = stamp / "complete.json"
                        if marker_path.is_file():
                            sample_marker = json.loads(marker_path.read_text())
                            break
        # The earlier single-observation schema did not store ``product`` in
        # each marker. Its product is instead established by both the parent
        # directory and the NOAA source URL. Require the old schema identity
        # and matching subset descriptor before classifying it as removable.
        source_url = (sample_marker or {}).get("source_url", "")
        if (sample_marker
            and sample_marker.get("asset_id")
            and sample_marker.get("subset_id") == subset.get("subset_id")
            and sample_marker.get("selection_id")
            and "assets" not in sample_marker
            and f"/{product_dir.name}/" in source_url
            and sample_marker.get("writer_backend") != "earth2studio-zarr-backend"):
            candidates.append({"directory": str(roi_dir), "product": product_dir.name,
                "subset_id": subset["subset_id"], "layout": "one Zarr archive per source observation"})
        else:
            unclassified.append(str(roi_dir))
    return candidates, current, unclassified


def mrms_deletion_plan(dataset_root, legacy_v2_root):
    """Return only classified legacy MRMS ROI directories from both roots."""
    primary, current, unclassified = legacy_mrms_trees(Path(dataset_root) / "mrms")
    v2_root = Path(legacy_v2_root)
    v2_scan_root = v2_root / "mrms" if (v2_root / "mrms").is_dir() else v2_root
    v2_legacy, v2_current, v2_unclassified = legacy_mrms_trees(v2_scan_root)
    all_legacy = {row["directory"]: row for row in (*primary, *v2_legacy)}
    return (list(all_legacy.values()), current + v2_current,
            unclassified + v2_unclassified, v2_legacy, v2_current, v2_unclassified)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", default="/mnt/p/ecore_eo_datasets")
    parser.add_argument("--legacy-v2", default="/mnt/p/ecore_eo_datasets_zarrV2")
    parser.add_argument("--mrms-output", default="results/study-mrms")
    parser.add_argument("--goes-output", default="results/study-goes-estimate")
    parser.add_argument("--apply", action="store_true",
                        help="Delete only after the full local MRMS audit passes")
    parser.add_argument("--report", default="results/legacy-cleanup-report.json")
    args = parser.parse_args(argv)
    dataset_root = Path(args.dataset_root).resolve()
    v2_root = Path(args.legacy_v2).resolve()
    if v2_root.name != "ecore_eo_datasets_zarrV2" or v2_root.parent != Path("/mnt/p").resolve():
        parser.error("Legacy V2 target must be exactly /mnt/p/ecore_eo_datasets_zarrV2")
    if dataset_root != Path("/mnt/p/ecore_eo_datasets").resolve():
        parser.error("Dataset root must be exactly /mnt/p/ecore_eo_datasets")
    # Legacy MRMS may be removed once all local study archives validate. The
    # independent yearly HF audit remains available, but backup publication is
    # not a prerequisite for replacing already verified local source data.
    evidence = audit(args.goes_output, args.mrms_output, dataset_root, check_hf=False)
    if not evidence["complete"]:
        print(json.dumps({"status": "not-ready", "audit": evidence}, indent=2))
        return 75
    (legacy, current, unclassified, v2_legacy, v2_current,
     v2_unclassified) = mrms_deletion_plan(dataset_root, v2_root)
    report = {"status": "planned", "legacy_v2": str(v2_root),
        "legacy_v2_exists": v2_root.is_dir(), "legacy_v2_mrms_trees": v2_legacy,
        "legacy_v2_current_mrms_trees": v2_current,
        "legacy_v2_unclassified": v2_unclassified,
        "legacy_mrms_trees": legacy,
        "legacy_mrms_count": len(legacy), "current_mrms_trees": current,
        "unclassified_mrms_trees": unclassified,
        "audit": evidence}
    if not args.apply:
        write_json(args.report, report)
        print(json.dumps(report, indent=2))
        return 0
    # Persist the intended deletion set before touching durable data so a
    # partial filesystem failure still leaves a useful audit trail.
    report["status"] = "deleting"
    write_json(args.report, report)
    for row in legacy:
        shutil.rmtree(row["directory"])
    report["legacy_mrms_deleted"] = len(legacy)
    report["status"] = "deleted"
    write_json(args.report, report)
    print(json.dumps({"status": report["status"],
        "legacy_mrms_deleted": len(legacy),
        "legacy_mrms_directories": [row["directory"] for row in legacy]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
