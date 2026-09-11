# Two notebooks to test and improve NOAA fetching

Implementation scope: September 2026. The first deliverable is a practical research
comparison, with evidence in [validation.md](validation.md).

## H2 2022 extension and HF write review

The three-month checks are complete. Review the mentor code and HF bucket access
patterns, coordinate publication independently of NOAA reads, test both HF and local
writes, and fetch July 1–January 1, 2022–2023 locally. Keep all available MRMS hours
and GOES-16 scans with eight selected infrared bands. Use fast scratch and the
mounted Windows P: DAS for long-term storage. Record actual elapsed time, transfer,
retained bytes and failures; no extrapolated speed claim. Keep one raw representation,
with readable product/date paths and optional Zarr ZIP containers. See
[review](mentor_code_review.md), [storage](storage.md), and [long sample](long_sample.md).

## Completed three-month test extension

The initial weekly comparisons below are complete. The expanded tests use
September 1–December 1 in **2022 and 2025**, excluding December 1. Test every MRMS
hourly slot (2,184 per period); discover the entire GOES period and compare six
representative scans. Use GOES-16 in 2022 and GOES-19 in 2025.

Hourly MRMS matching defaults to an observation at or before the slot, within five
minutes. Keep actual timestamps and offsets. Share request/pipeline functions
between interactive widgets and argparse scripts. Default downloads to half the
CPU count and offer inline images plus optional PNG export.

Keep documentation current in `docs/`. Selections use two JSON files; virtual
references use one bundle. Validation removes its own scratch and retains a compact
result. Remove successful manual test artifacts after recording their evidence.
Ordinary fetched research subsets remain durable. See [usage](notebooks.md) and
[developer commands](developer_guide.md).

## Original implementation order

1. Run the unchanged mentor hourly downloader for September 18–25, 2022 and
   September 15–22, 2024, using UTC and excluding the ending date. Report missing
   clock hours and inspect output geometry, units, values, and missingness. Keep
   the original scripts. Remove their temporary downloads and rasters.
2. Build the MRMS notebook: choose dates, product, Puerto Rico region, storage,
   and concurrency; inspect and save a STAC selection; fetch native pixels;
   save lossless Zarr; inspect Caribbean patches; compare session-only processing.
3. Compare identical source files and the mentor's processed output using
   sequential reads, four workers, and obstore. Report bytes, elapsed time,
   decoding/processing, writing, and memory. Measure raw Zarr and HF publishing
   separately. Repeat promising comparisons three times before claiming speed.
4. Build the GOES notebook with the same sequence. Begin with GOES-16 full-disk
   C08/C13. Compare full-file, range, and virtual reads on six scans per week:
   midnight/noon on the first, middle, and last included days. Preserve packed
   values and quality metadata. Check all selected variables and coordinates.
5. Check raw round trips, missing-value diagnostics, processing without mutation,
   failure cleanup, notebook synchronization, and execution. Run a small Colab
   check against the exact pushed revision when Google authorization is available.

## Keep the setup small

- `notebooks/01_mrms.py` and `02_goes.py` are the notebook sources. Jupytext
  generates their `.ipynb` partners. Explanations use plain language.
- `src/ecore_weather/` contains reusable readers, storage, STAC, diagnostics,
  processing, timing, and widgets. Notebook buttons call these same Python functions.
- `environment.yml` manages local Conda dependencies. The package's `notebooks`
  extra provides the matching pip dependencies for Colab. No GPU stack is needed.
- Colab setup detects the runtime, clones a configurable revision, and installs
  dependencies. Changes under `src/` must be pushed before clone-based testing.
  README notebook links are ordinary links; automated badges/actions are deferred.
- HF bucket storage is the default. `HF_BUCKET_NAME` names the bucket;
  `HF_DATASET_REPO` is a compatibility alias, not a dataset-repository assumption.
  An explicit local path is supported. Failed uploads never change destinations.

## Preserve the research data

Save one canonical raw subset per source object and selection. Preserve native
coordinates, decoded MRMS values and bitmap missingness, packed GOES values,
calibration, units, and quality flags. Do not interpolate, clean, clip, or replace
missing values during ingestion. Source containers are temporary scratch.

The mentor's interpolation and cleaning, and a version that masks documented
missing values first, are separate in-memory views. A centered 512 × 512 crop is
an optional view. Do not save processed rasters by default.

Report valid zero, documented missing values, no coverage, bitmap gaps, and
missing files separately. Statistics use valid observations only; empty patches
remain empty. Use product definitions rather than masking every negative value.

MRMS gzip still requires full-object reads. GOES references point to NOAA objects
and require continuing source access. Keep incompatible encodings in separate
groups. STAC and virtualization are experiments to measure, not automatic speedups.

## Later work

Managed Icechunk storage, external mirror comparisons, H3/DuckDB experiments,
broader architecture, production ingestion, automation, training, and model
inference follow the initial measurements. The cited Dynamical CONUS product is
not a replacement for CARIB data. Use [the supplied references](agent_dev_references)
when these later experiments begin.

## Final notebook usability and archive checks

- Short widget labels and hover help for Tasks (concurrent files) and Readers
  (independent decoding processes); maintain the existing product guides.
- Shared source/ROI paths across date requests, with verified adoption of older
  local folders and explicit relocation records.
- Radar coastline/land context and a third, small local/HF dataset viewer with
  optional PNG output. Tile servers remain outside this initial notebook scope.
- H1 2023 full available-hour MRMS matched-output comparison; January 2023
  CMIPF full discovery and representative reads across eight requested bands.
- Refresh all notebook pairs, test raw preservation/reuse and CLI paths, retain
  compact measurements, and remove owned successful test data. Colab validation
  of the exact commit follows publication and Google authentication.
