"""Tests for resumable study-run checkpoint verification."""

import json

from scripts.fetch_goes_staged import _verify_month


def test_verify_month_finds_product_nested_goes_report(tmp_path):
    report_dir = tmp_path / "2026-01"
    product_dir = report_dir / "ABI-L2-CMIPF"
    archive_dir = tmp_path / "dataset" / "C01" / "2026" / "01"
    product_dir.mkdir(parents=True)
    archive_dir.mkdir(parents=True)
    archive = archive_dir / "raw.zarr.zip"
    archive.write_bytes(b"verified test archive")
    (archive_dir / "complete.json").write_text(json.dumps({"observations": 2}))
    report = {
        "source": "goes",
        "interrupted": False,
        "selection_id": "selection-id",
        "records": [{"band": 1}, {"band": 1}],
        "monthly_archives": [{
            "path": str(archive), "band": 1, "status": "saved",
            "observations": 2, "stored_bytes": len(b"verified test archive"),
        }],
    }
    (product_dir / "selection-id-monthly.json").write_text(json.dumps(report))

    verified, archives, bands = _verify_month(report_dir)

    assert verified["selection_id"] == "selection-id"
    assert len(archives) == 1
    assert archives[0]["path"] == str(archive)
    assert bands == [1]
