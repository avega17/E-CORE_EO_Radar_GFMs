"""The same research functions through argparse, suitable for later batch jobs."""

import argparse
from dataclasses import replace
from pathlib import Path

from .common import BENCHMARK_PERIODS, PR_BBOX, default_workers, write_json


def parser(source):
    p = argparse.ArgumentParser(description=f"NOAA {source.upper()} research notebook as a script")
    p.add_argument("--operation", choices=["inspect", "fetch", "benchmark", "validate"], default="inspect")
    p.add_argument("--period", choices=["2022", "2025"], default="2022")
    p.add_argument("--start", help="UTC start; overrides the selected example period")
    p.add_argument("--end", help="UTC ending date/time, excluded")
    p.add_argument("--bbox", nargs=4, type=float, default=PR_BBOX, metavar=("WEST", "SOUTH", "EAST", "NORTH"))
    p.add_argument("--product")
    p.add_argument("--selection", help="Reuse a saved STAC collection or item manifest")
    p.add_argument("--workers", type=int, default=default_workers(), help="Maximum concurrent file tasks; HF publication is coordinated separately")
    p.add_argument("--decode-workers", type=int, default=1, help="Concurrent GRIB decodes with threaded readers; each separate reader process decodes one file at a time")
    p.add_argument("--read-processes", type=int, default=0, help="Independent file readers, useful for HDF5; zero uses threads")
    p.add_argument("--scratch", help="Fast local staging directory; cleaned after each subset")
    p.add_argument("--container", choices=["directory", "zip"], default="directory", help="ZIP packs compressed Zarr chunks into one file")
    p.add_argument("--layout", choices=["readable", "legacy"], default="readable")
    p.add_argument("--backend", choices=["s3fs", "obstore"], default="s3fs")
    p.add_argument("--destination", default="hf", help="hf (default) or an explicit local directory")
    p.add_argument("--output", default=f"results/{source}", help="Small selection and report output folder")
    p.add_argument("--max-files", type=int, help="Explicitly limit a sample; the report records the smaller selection")
    p.add_argument("--save-figures", metavar="DIRECTORY", help="Save PNG figures; off by default")
    p.add_argument("--plot-index", type=int, nargs="+", default=[0], help="Fetched image indices to export")
    p.add_argument("--recipe", choices=["compare", "legacy_exact", "quality_aware"] if source == "mrms" else ["decode", "quality", "reproject"], help="Session-only processing for the exported figure")
    p.add_argument("--center-crop", action="store_true", help="Show a centered 512-pixel MRMS view")
    p.add_argument("--repeats", type=int, default=1, help="Speed benchmark repetitions")
    p.add_argument("--skip-mentor", action="store_true", help="Skip the optional legacy calculation check during validation")
    if source == "mrms":
        p.add_argument("--tolerance-minutes", type=float, default=5)
        p.add_argument("--time-match", choices=["previous", "nearest", "exact"], default="previous")
    else:
        p.add_argument("--satellite", choices=["auto", "16", "17", "18", "19"], default="auto")
        p.add_argument("--bands", nargs="+", type=int, default=[8, 13])
        p.add_argument("--scans-per-hour", type=int, default=1, metavar="N", help="Scans to keep per UTC hour (1-6); 0 keeps every available scan")
    return p


def main(source, argv=None):
    p = parser(source)
    args = p.parse_args(argv)
    if args.workers < 1 or args.decode_workers < 1 or args.repeats < 1 or (args.max_files is not None and args.max_files < 1):
        p.error("Workers, repeats, and max-files must be positive.")
    from . import benchmark, catalog, goes, mrms, storage, validation
    out = Path(args.output)
    try:
        if args.selection:
            selection = catalog.load_selection(args.selection)
            if selection.source != source:
                raise ValueError("The saved selection belongs to the other notebook.")
        else:
            periods = list(BENCHMARK_PERIODS.values())
            start, end = periods[0 if args.period == "2022" else 1]
            request = dict(start=args.start or start, end=args.end or end, bbox=args.bbox)
            if args.product:
                request["product"] = args.product
            if source == "mrms":
                selection = mrms.discover(**request, tolerance_minutes=args.tolerance_minutes, time_match=args.time_match)
            else:
                # Validation and benchmark runs keep every available scan explicitly.
                scans = args.scans_per_hour or None
                if args.operation in ("validate", "benchmark"):
                    scans = None
                selection = goes.discover(**request, satellite=args.satellite, bands=args.bands, scans_per_hour=scans)
        if args.max_files:
            assets = selection.assets[:args.max_files]
            ids = {a.id for a in assets}
            selection = replace(selection, assets=assets, hourly_matches=tuple(m for m in selection.hourly_matches if m["asset_id"] in ids))
        print(selection.summary(), flush=True)
        if args.operation == "validate":
            # Validation data, STACs, references, and detailed benchmarks are temporary.
            result = validation.validate(selection, args.workers, out/"validation.json",
                                         compare_mentor=not args.skip_mentor, save_figures=args.save_figures)
            print("Validation passed:", result["passed"], flush=True)
            return 0 if result["passed"] else 1
        catalog.save_selection(selection, out)
        if args.operation == "inspect":
            if source == "goes":
                print(goes.acquisition_coverage(selection).to_string(index=False))
            return 0
        if args.operation == "benchmark":
            if source == "mrms":
                _, records = benchmark.run_mrms(selection, report_dir=out, repeats=args.repeats, workers=args.workers, read_processes=args.read_processes)
                passed = not records.empty and bool(records.matches_legacy.all())
                print("Benchmark comparisons passed:", passed, flush=True)
                return 0 if passed else 1
            else:
                benchmark.run_goes(selection, report_dir=out, repeats=args.repeats)
            return 0
        report = storage.fetch(selection, destination=args.destination, workers=args.workers,
                               decode_workers=args.decode_workers, backend=args.backend, report_dir=out, scratch=args.scratch, layout=args.layout, read_processes=args.read_processes, container=args.container)
        if args.save_figures:
            from .visualization import show_or_save
            rows = [r for r in report["records"] if r["status"] in ("saved", "reused")]
            for index in args.plot_index:
                if not 0 <= index < len(rows):
                    raise ValueError(f"Image index {index} is outside the {len(rows)} successful images.")
                with storage.open_raw(rows[index]["url"]) as ds:
                    print(show_or_save(ds, args.save_figures, display=False, recipe=args.recipe,
                                       center_crop=args.center_crop))
        return 1 if any(r["status"] == "failed" for r in report["records"]) else 0
    except Exception as exc:
        write_json(out/"failure.json", {"source": source, "operation": args.operation,
                                       "error": f"{type(exc).__name__}: {exc}"})
        print(f"{type(exc).__name__}: {exc}", flush=True)
        return 1
