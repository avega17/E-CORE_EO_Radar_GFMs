# Project development guidelines

This NSF-funded project prepares reproducible NOAA radar and satellite datasets
for near-term rain and storm forecasting, initially for Puerto Rico. Read
[`docs/development_plan.md`](docs/development_plan.md) and the supplied references
in `docs/agent_dev_references.md` before extending the pipeline. Keep technical
explanations direct and readable; introduce terms when they first matter.

## Scientific data preservation

- Persist requested raw raster subsets unchanged in scientific content. Storage
  and lossless compression may change; ingestion must not clean, interpolate,
  reproject, normalize, impute, quantize, or aggregate measurements.
- Preserve native coordinates, resolution, values, units, timestamps, quality
  flags, missing-value encodings, and product metadata. GOES packed values must
  retain their scale, offset, and fill metadata. MRMS GRIB values are decoded
  numbers with explicit bitmap missingness; do not claim the original GRIB bytes
  are retained.
- Keep one canonical persisted archive for each source/product/region-grid/band
  and month. Temporary NOAA source objects and per-observation staging stores are
  removed after the monthly archive passes read-back verification..
- Keep diagnostics and derived processing separate from raw storage. Interpolate,
  mask, decode, and reproject in the session unless a separate output is explicitly
  requested. Never overwrite the raw archive.
- Interpret sentinels by product, metadata, and source version. Do not mask all
  negative values: negative reflectivity can be valid. Separate valid zero,
  no-coverage values, bitmap gaps, absent files, and unknown anomalies.
- Preserve accumulation intervals and physical units. Hourly QPE is an
  accumulation even if selected at ten-minute intervals. Do not use S3
  `LastModified` as historical operational availability.
- Do not fill missing observations with zeros or include future observations in
  model windows. Report coverage and eligibility explicitly.

## Sources and temporal selection

- Use original AWS CARIB MRMS products for Puerto Rico.
- Use custom Earth2Studio-compatible sources in `src/ecore_weather/earth2_sources.py`
  when the stock source does not provide the needed region, product, or raw
  metadata. Keep shared reads in the project MRMS and GOES modules.
- MRMS study defaults: precipitation rate, composite reflectivity, and
  low-level azimuthal shear sampled on ten-minute UTC slots, plus multisensor
  Pass2 QPE at hourly cadence. Other supported products remain selectable. Use the
  exact CARIB S3 azimuthal-shear keys with underscores and 00.50 suffixes.
  Match the newest source
  observation at/before each slot within five minutes, do not reuse an observation,
  and record actual times/offsets and missing slots.
- GOES defaults: full-disk scans as available, per-band CMIPF, and StormScope
  example bands C01/C02/C03/C07/C08/C09/C10/C13. Keep each native band grid and
  calibration epoch separate. MCMIPF is an option, not a reason to merge
  incompatible arrays.
- Earth2Studio compatibility does not establish compatibility with a released
  StormScope checkpoint. Training, fine-tuning, inference, and scheduler-specific
  workflows remain outside current scope.

## Storage and index

- Use anonymous NOAA reads. Keep credentials out of URLs, logs, notebooks, STAC,
  manifests, and committed files.
- Default durable storage to the configured HF bucket and permit explicit local
  storage. `HF_BUCKET_NAME=namespace/bucket` is preferred;
  `HF_DATASET_REPO` is a documented compatibility alias. `HF_TOKEN` is for Hub
  APIs, not an S3 access key. The S3 gateway's key and secret are distinct.
- Use the namespace endpoint, bare bucket name, path-style S3 addressing,
  `us-east-1`, and checksums only when required. Verify the gateway with a small
  write/reopen/read-back test before relying on it. One coordinator publishes
  remote monthly archives; after repeated errors, fail clearly and require an
  explicit local destination. Never silently redirect output.
- Use Earth2Studio's standard `ZarrBackend` for the default local monthly
  archive path. Keep arrays named and coordinate-aware so a project
  `DataSource` can reopen them through Earth2Studio's `(time, variable)` API.
  Preserve non-dimension source provenance in a compact sidecar. The direct-HF
  monthly writer uses Earth2Studio's `AsyncZarrBackend` through obstore, with
  bounded time sharding, remote read-back, and a completion marker only after
  verification. Study backups use one ZIP per MRMS product/year under
  `noaa-subsets/yearly-v1/`; each packages verified local monthly archives
  without merging their arrays. Publish four independent product/year bundles
  in parallel, read each object back and check its SHA-256, then remove only
  matching superseded monthly prefixes. Never let concurrent workers write the
  same store or shard, and never silently change writer implementations after a
  failure.
- Start with two local monthly writers for separate stores. Bound source download
  concurrency and decoding independently. Direct HF monthly writes use one
  writer; the year-bundle backup uses four independent product/year transfers.
  Report HF transfer measurements after a run; do not forecast HF throughput
  precisely.
- Keep `results/archive_index.duckdb` on a local Linux disk. One process owns
  writes. It is a rebuildable convenience index; STAC selections and monthly
  completion manifests remain the portable source of truth.
- Optimize from measured transfer bytes, requests, elapsed time, decoding cost,
  memory, and final size. Zarr, STAC, range reads, and concurrency are not automatic
  guaranteed speedups.

## Package, notebooks, and environment

- Put reusable functions under `src/ecore_weather/`; keep notebooks focused on
  explanation, dynamic selection controls, visuals, and small calls into modules.
- Author notebook sources as Python percent-format files under `notebooks/` with
  `# %%` and `# %% [markdown]`. Keep `.ipynb` partners synchronized using
  Jupytext. Commit clean notebooks without credentials, large embedded data, or
  execution outputs.
- Notebook widgets and CLI scripts must use the same request and pipeline
  functions. Widget construction must not fetch data. Both fetchers support
  argparse, progress output, inline visualizations, and optional PNG exports.
- Keep notebook 03 available for local and HF archives, with source selection,
  date/time filtering, one-sample map view, and bounded daily/multi-day sequences.
  HF annual ZIPs are backup containers: restore a selected, verified monthly
  member into a local cache before viewing it. The storage explorer compares
  compressed archive bytes with listed NOAA source-object bytes from metadata.
  Viewer masks must not alter archived arrays. Prioritize the new monthly
  Earth2Studio schema; legacy single-observation support is optional.
- In Colab, detect the runtime, clone a configurable revision, install notebook
  dependencies, and show the resolved commit. Push modified `src/` code before
  validating it through a clone. Do not report another revision as local-code
  validation.
- Use Conda and `environment.yml`; prefer conda-forge for compiled geospatial
  dependencies. Declare pip-only dependencies explicitly. Keep optional GPU/model
  dependencies out of the CPU data environment.
- Update README links, `docs/notebooks.md`, `docs/developer_guide.md`,
  `docs/storage.md`, `docs/status.md`, and this file when interfaces or evidence
  change. Use plain-language explanations.

## Validation and operations

- Cover the completed weekly pilots, the September–November 2022/2025 selection
  checks, and the larger requested representative periods only as described in
  current validation/status docs. Full MRMS slot checks and representative GOES
  scan reads have different cost and coverage.
- Check raw source-to-Zarr value, coordinate, calibration, quality, sentinel, and
  bitmap preservation; repeated and interrupted runs; monthly merge/reuse;
  the Earth2Studio monthly-source contract; index search/rebuild; and HF
  read-back. Legacy archive migration is outside the storage acceptance gate.
- Delete only successful test artifacts created for that test after retaining
  compact evidence. Do not delete durable research data or unrelated bucket files.
  Keep detailed file explosions and executed notebook outputs out of Git.
- Verify `/mnt/p` is the mounted Windows P: drive before a long local archive
  fetch. Keep scratch on a fast Linux disk when practical; ensure enough free
  space. Avoid imposing Linux permission metadata on DrvFS.
- Record which checks passed and which were blocked by the runtime or network.
  Do not claim live HF, Earth2Studio, Colab, or long-run validation without a
  completed test on the exact current code.
- The two study job commands and their checkpoints are in `docs/study_jobs.md`.
  The GOES job inventories metadata and reads only bounded crop samples; the
  MRMS job writes to P: after a mount and Earth2Studio preflight. Treat exit 75
  as a resumable month-boundary pause, not completion of the study period.
