"""Run comparable experiments and keep small timing reports, not legacy files."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import re
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
import pandas as pd

from .common import PeakMemory, write_json, default_workers


def validate_mentor_week(selection, report_dir="artifacts/benchmarks/mentor-validation"):
    """Call the unchanged hourly downloader for every expected clock hour."""
    from .common import Asset, utc
    by_time = {a.time: a for a in selection.assets}
    assets = []
    for stamp in selection.expected_times:
        when = utc(stamp)
        key = (f"CARIB/{selection.product}/{when:%Y%m%d}/"
               f"MRMS_{selection.product}_{when:%Y%m%d-%H%M%S}.grib2.gz")
        assets.append(by_time.get(stamp, Asset("noaa-mrms-pds", key, 0, "not-listed", stamp)))
    return run_mrms(replace(selection, assets=assets, hourly_matches=(), time_tolerance_minutes=0,
                            time_match="exact"), report_dir=report_dir, variants=["legacy"])


def comparable_hours(selection):
    """The unchanged mentor API accepts whole hours, not off-hour source files."""
    expected = set(selection.expected_times)
    selected = [a for a in selection.assets if a.time in expected]
    omitted = [a.time for a in selection.assets if a.time not in expected]
    if omitted:
        print("Off-hour files excluded only from the legacy comparison:", omitted)
    return replace(selection, assets=selected)


def raster_summary(path):
    import hashlib
    import rasterio
    with rasterio.open(path) as raster:
        values = raster.read(1)
        valid = np.isfinite(values)
        if raster.nodata is not None and np.isfinite(raster.nodata):
            valid &= values != raster.nodata
        canonical = values.astype("float32")
        canonical[~valid] = np.nan
        return {"shape": list(values.shape), "dtype": str(values.dtype), "crs": str(raster.crs),
                "transform": list(raster.transform), "units_in_file": raster.tags().get("units"),
                "valid_pixels": int(valid.sum()), "zero_pixels": int((values[valid] == 0).sum()),
                "min": float(values[valid].min()) if valid.any() else None,
                "max": float(values[valid].max()) if valid.any() else None,
                "array_sha256": hashlib.sha256(canonical.tobytes()).hexdigest()}


def goes_throughput(selection, report_dir, variants, repeats=2):
    """Measure GOES fetch throughput across backend, block size and workers.

    Each variant writes to an isolated temporary local destination that is
    deleted after timing. The first variant's per-asset fingerprints are the
    reference; every later variant must match them, so speed differences never
    come from changed content. Timing is sensitive to caches and network
    conditions; repeat promising variants before drawing conclusions.

    variants: iterable of dicts with backend, block_size and workers keys.
    Returns a DataFrame with one row per (variant, repeat).
    """
    from . import storage
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    reference = None
    rows = []
    for variant in variants:
        backend, block_size, workers = variant["backend"], int(variant["block_size"]), int(variant["workers"])
        for attempt in range(int(repeats)):
            with tempfile.TemporaryDirectory(prefix="ecore-throughput-") as temp:
                before = time.perf_counter()
                report = storage.fetch(selection, destination=temp, workers=workers, backend=backend,
                                       read_processes=workers, report_dir=None, container="zip",
                                       block_size=block_size)
                wall = time.perf_counter() - before
                saved = [r for r in report["records"] if r["status"] in ("saved", "reused")]
                failed = [r for r in report["records"] if r["status"] == "failed"]
                fps = {}
                for r in saved:
                    with storage.open_raw(r["url"]) as ds:
                        fps[r["asset_id"]] = storage.fingerprint(ds)
                if reference is None:
                    reference = fps
                equal = fps == reference
                row = {"backend": backend, "block_size": block_size, "workers": workers,
                       "repeat": attempt, "wall_s": wall, "files": len(saved), "failed": len(failed),
                       "read_s": sum(r.get("read_decode_crop_s", 0) for r in saved),
                       "write_s": sum(r.get("write_s", 0) for r in saved),
                       "publish_s": sum(r.get("publish_s", 0) for r in saved),
                       "bytes": report.get("read_bytes"), "requests": report.get("requests"),
                       "fingerprints_equal": equal}
                row["effective_MBps"] = (report.get("read_bytes", 0) / 1e6 / wall) if wall else None
                rows.append(row)
                print(row, flush=True)
    table = pd.DataFrame(rows)
    write_json(report_dir / "goes-throughput.json", {"variants": [dict(v) for v in variants],
                                                      "repeats": repeats, "rows": rows,
                                                      "files": len(selection.assets)})
    return table


def run_mrms(selection, report_dir="artifacts/benchmarks", repeats=1, variants=None, workers=None, read_processes=0):
    """Run the original and revised readers in separate fresh Python processes.

    Each variant writes the same temporary GeoTIFF output. The raw-Zarr/HF run
    is deliberately measured through storage.fetch, not called a fetch speedup.
    """
    from .catalog import save_selection
    from .mrms import DEFAULT_PRODUCT
    if selection.source != "mrms" or selection.product != DEFAULT_PRODUCT:
        raise ValueError("The unchanged mentor benchmark supports its original CARIB Pass2 QPE product.")
    if not selection.assets:
        raise ValueError("No source files were selected.")
    if any(a.time[14:19] != "00:00" for a in selection.assets) and not selection.hourly_matches:
        raise ValueError("Off-hour files require an explicit hourly matching manifest.")
    workers = default_workers() if workers is None else workers
    if read_processes < 0:
        raise ValueError("read_processes cannot be negative.")
    default_variants = ["legacy", "s3fs-1", f"s3fs-{workers}", f"obstore-{workers}"]
    if read_processes:
        default_variants.append(f"obstore-process-{min(read_processes, workers)}")
    variants = list(dict.fromkeys(variants or default_variants))
    if any(v != "legacy" and not re.fullmatch(r"(s3fs|obstore)(-process)?-[1-9]\d*", v) for v in variants):
        raise ValueError("Unknown benchmark variant.")
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    rows, all_records = [], []
    with tempfile.TemporaryDirectory(prefix="ecore-benchmark-") as temp:
        selection_file = save_selection(selection, Path(temp) / "selection").resolve()
        for repeat in range(repeats):
            order = variants if repeat % 2 == 0 else list(reversed(variants))
            for variant in order:
                result_path = Path(temp) / f"{variant}.json"
                before = time.perf_counter()
                with PeakMemory() as memory:
                    result = subprocess.run([sys.executable, "-m", "ecore_weather._benchmark_worker",
                        str(selection_file), variant, str(result_path)], text=True, capture_output=True)
                if result.returncode:
                    raise RuntimeError(f"{variant} failed: {result.stderr[-3000:]}")
                data = json.loads(result_path.read_text())
                summary = {k: v for k, v in data.items() if k != "records"}
                summary.update(variant=variant, repeat=repeat+1, selection_id=selection.id,
                               process_wall_s=time.perf_counter()-before, peak_rss_bytes=memory.peak,
                               start=selection.start, end=selection.end)
                rows.append(summary)
                all_records.extend([{**r, "variant": variant, "repeat": repeat+1} for r in data["records"]])
                print(f"{variant}, repeat {repeat+1}: {data['succeeded']}/{len(selection.assets)} files, {data['wall_s']:.2f} s", flush=True)
    baseline = {r["asset_id"]: r for r in all_records if r["variant"] == "legacy" and r["status"] == "ok"}
    for record in all_records:
        ref = baseline.get(record["asset_id"])
        record["matches_legacy"] = bool(ref and record["status"] == "ok" and
                                        record["raster"]["array_sha256"] == ref["raster"]["array_sha256"] and
                                        record["raster"]["transform"] == ref["raster"]["transform"])
    table = pd.DataFrame(rows)
    table.to_csv(report_dir / f"mrms-{selection.id}.csv", index=False)
    write_json(report_dir / f"mrms-{selection.id}-details.json", {
        "selection": asdict(selection), "summary": rows, "records": all_records,
        "notes": "Fresh processes and local scratch; provider-side caches are uncontrolled. Read bytes exclude HTTP/TLS headers. Stage seconds are summed task times for concurrent runs.",
    })
    return table, pd.DataFrame(all_records)


def run_goes(selection, report_dir="artifacts/benchmarks", assets=None, repeats=1):
    """Compare full files, ranged subsets, and VirtualiZarr/Kerchunk reads."""
    from . import goes
    from .common import Transport
    assets = list(assets) if assets is not None else goes.benchmark_assets(selection)
    if not assets:
        raise ValueError("No GOES scans selected.")
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    virtual_dir = report_dir / f"goes-{selection.id}-references"
    index = goes.build_virtual(selection, virtual_dir, assets=assets)
    index_data = json.loads(index.read_text())
    rows = []
    for repeat in range(repeats):
        for i, asset in enumerate(assets):
            baseline = None
            for mode in ("full_file", "range", "virtual"):
                transport = Transport("s3fs")
                before = time.perf_counter()
                with transport, PeakMemory() as memory:
                    if mode == "virtual":
                        with goes.open_virtual(index, record=i) as vds:
                            ds = vds.load()
                        bytes_read = calls = None  # fsspec owns reference reads; do not invent counts.
                    else:
                        ds, _ = goes.read(asset, selection.bbox, selection.bands, transport,
                                          full_file=mode == "full_file")
                        bytes_read, calls = transport.bytes, transport.requests
                    arrays = {name: ds[name].values.copy() for name in ds.variables}
                    if baseline is None:
                        baseline = arrays
                    matches = arrays.keys() == baseline.keys() and all(
                        np.array_equal(values, baseline[name], equal_nan=True) for name, values in arrays.items())
                    elapsed = time.perf_counter() - before
                rows.append({"selection_id": selection.id, "asset_id": asset.id, "time": asset.time,
                             "mode": mode, "repeat": repeat+1, "wall_s": elapsed, "read_bytes": bytes_read,
                             "data_read_calls": calls, "peak_rss_bytes": memory.peak, "matches_full_file": matches})
                ds.close()
                if not matches:
                    raise AssertionError(f"{mode} did not match the full source subset: {asset.key}")
            print(f"GOES scan {i+1}/{len(assets)}, repeat {repeat+1}: values match", flush=True)
    table = pd.DataFrame(rows)
    table.to_csv(report_dir / f"goes-{selection.id}.csv", index=False)
    write_json(report_dir / f"goes-{selection.id}-references-summary.json",
               {k: v for k, v in index_data.items() if k != "records"})
    return table


def plot_timings(table, label="variant"):
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(8, 4), constrained_layout=True)
    table.groupby(label).wall_s.median().plot.bar(ax=ax)
    ax.set(ylabel="Median elapsed seconds", xlabel="", title="Measured runtime for the same selection")
    ax.tick_params(axis="x", rotation=20)
    return fig
