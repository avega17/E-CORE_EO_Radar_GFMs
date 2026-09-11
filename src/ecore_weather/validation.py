"""Full-period checks with temporary data and a single compact result file."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, replace
from pathlib import Path
import tempfile
import time

import pandas as pd

from . import benchmark, diagnostics, goes, storage
from .common import default_workers, write_json


def validate(selection, workers=None, report_path=None, compare_mentor=True, save_figures=None):
    """Validate every selected MRMS file, or representative GOES scans.

    Mentor comparisons use independent, isolated batches in parallel. Their
    timings are correctness-test timings, not sequential fetch speed benchmarks.
    Only the concise result and explicitly requested figures survive success.
    """
    workers = default_workers() if workers is None else workers
    start = time.perf_counter()
    result = {"selection_id": selection.id, "selection": selection.summary(), "workers": workers,
              "test_kind": "full MRMS period" if selection.source == "mrms" else "representative GOES scans"}
    with tempfile.TemporaryDirectory(prefix="ecore-validation-") as temp:
        root = Path(temp)
        if selection.source == "mrms":
            raw = storage.fetch(selection, workers=workers, backend="obstore", report_dir=None,
                                validate_only=True, scratch=root,
                                inspect=lambda ds: diagnostics.describe(ds).to_dict("records"),
                                progress=lambda done, total, row: print(f"Raw validation {done}/{total}", flush=True)
                                if done % 100 == 0 or done == total else None)
            result["raw"] = {k: raw[k] for k in ("wall_s", "read_bytes", "data_read_calls", "peak_rss_bytes")}
            result["raw"].update(validated=sum(r["status"] == "validated" for r in raw["records"]),
                stored_bytes=sum(r.get("stored_bytes", 0) for r in raw["records"]),
                logical_array_bytes=sum(r.get("logical_array_bytes", 0) for r in raw["records"]),
                failures=[r for r in raw["records"] if r["status"] == "failed"])
            table = pd.DataFrame([d for r in raw["records"] for d in r.get("diagnostics", [])])
            if not table.empty:
                result["patches"] = table.groupby("patch")[["valid_pct", "valid_zero_pct", "missing_fill_pct",
                    "no_coverage_pct", "bitmap_missing_pct", "nonfinite_pct"]].mean().to_dict("index")
                result["coverage_outages"] = diagnostics.outage_lengths(table).to_dict("records")
                if save_figures:
                    import matplotlib.pyplot as plt
                    path = Path(save_figures); path.mkdir(parents=True, exist_ok=True)
                    fig = diagnostics.plot_coverage(table, selection.expected_times)
                    fig.savefig(path / "coverage.png", dpi=150)
                    plt.close(fig)
            if compare_mentor and selection.assets:
                # Bound both the number of child processes and the data per batch.
                batch_size = max(1, (len(selection.assets)+workers-1)//workers)
                batches = [selection.assets[i:i+batch_size] for i in range(0, len(selection.assets), batch_size)]
                def check(i, assets):
                    ids = {a.id for a in assets}
                    matches = tuple(m for m in selection.hourly_matches if m["asset_id"] in ids)
                    expected = tuple(m["slot_time"] for m in matches) or tuple(a.time for a in assets)
                    batch = replace(selection, assets=assets, hourly_matches=matches, expected_times=expected)
                    _, rows = benchmark.run_mrms(batch, report_dir=root/f"batch-{i}",
                                                   variants=["legacy", "obstore-1"])
                    revised = rows[rows.variant != "legacy"]
                    return {"files": len(assets), "matched": int(revised.matches_legacy.sum()),
                            "failures": revised[~revised.matches_legacy].to_dict("records")}
                comparisons = []
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = [pool.submit(check, i, batch) for i, batch in enumerate(batches)]
                    for future in as_completed(futures):
                        comparisons.append(future.result())
                        print(f"Mentor comparison batches {len(comparisons)}/{len(batches)}", flush=True)
                result["mentor_comparison"] = {"files": sum(r["files"] for r in comparisons),
                    "matched": sum(r["matched"] for r in comparisons),
                    "failures": [f for r in comparisons for f in r["failures"]],
                    "note": "Parallel correctness verification; not a sequential-download speed benchmark."}
        else:
            coverage = goes.acquisition_coverage(selection)
            unusual = coverage[(coverage.observed_files != coverage.expected_files) | coverage.expected_files.isna()]
            result["acquisition_coverage"] = {
                "hours": len(coverage), "observed_files": int(coverage.observed_files.sum()),
                "empty_hours": coverage.loc[coverage.observed_files == 0, "hour"].tolist(),
                "unusual_hours": unusual.to_dict("records"),
                "note": "Counts describe discovered objects; equality is checked only on representative scans."}
            assets = goes.benchmark_assets(selection)
            table = benchmark.run_goes(selection, report_dir=root, assets=assets)
            result["tested_objects"] = [asdict(a) for a in assets]
            result["comparisons"] = table.to_dict("records")
            result["all_variables_match"] = bool(table.matches_full_file.all())
            import json
            references = json.loads(next(root.glob("*-references/index.json")).read_text())
            result["reference_build_s"] = references["build_s"]
            result["reference_bytes"] = references["reference_bytes"]
            result["encoding_groups"] = len(references["groups"])
            sample = replace(selection, assets=assets)
            raw = storage.fetch(sample, workers=workers, backend="s3fs", report_dir=None,
                                validate_only=True, scratch=root)
            result["raw"] = {"validated": sum(r["status"] == "validated" for r in raw["records"]),
                             "failures": [r for r in raw["records"] if r["status"] == "failed"]}
            if save_figures and assets:
                from .visualization import show_or_save
                ds, _ = goes.read(assets[0], selection.bbox, selection.bands)
                with ds:
                    show_or_save(ds, directory=save_figures, display=False)
        result["wall_s"] = time.perf_counter()-start
        result["passed"] = (bool(selection.assets) and not result["raw"]["failures"] and
            (result.get("all_variables_match", True)) and not result.get("mentor_comparison", {}).get("failures"))
    if report_path:
        write_json(report_path, result)
    return result
