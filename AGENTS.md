# Project development guidelines

This NSF-funded project develops reproducible NOAA radar and satellite data pipelines for near-term rain and storm forecasting, initially for Puerto Rico. Follow the development plan in `docs/development_plan.md` and the supplied sources in `docs/agent_dev_references`.

## Scientific data preservation

- Persist requested raw raster subsets as-is in scientific content. Storage/container changes and lossless compression are allowed; ingestion must not clean, interpolate, reproject, normalize, clip, impute, quantize, or temporally aggregate the measurements.
- Preserve native coordinates, resolution, values, units, timestamps, quality flags, missing-value encodings, and relevant product metadata. For packed GOES variables, preserve packed values and their scale/offset and fill metadata. For GRIB, preserve decoded numeric values and explicit bitmap missingness; do not claim original GRIB bytes are retained.
- Keep one canonical persisted representation of a subset. Do not retain duplicate legacy GRIB, gzip, NetCDF, or GeoTIFF files. Source files needed for decoding are temporary scratch, cleaned after success or failure. Preserve URLs, object identity, checksums, and metadata for provenance instead.
- Virtual references may point directly to NOAA's original objects, never to temporary downloads that will be deleted. Reference artifacts remain dependent on remote source availability.
- Keep diagnostics and derived preprocessing separate from raw ingestion. Never overwrite raw data with masks or cleaned values. Persist derived rasters only when explicitly requested; small diagnostic tables, plots, and provenance are ordinary outputs.
- Interpret sentinels by product, metadata, and source version. Do not mask all negative values: negative radar reflectivity can be valid. Distinguish no coverage, missing pixels, missing observations/files, valid zero, and unknown anomalies.
- Preserve accumulation intervals and physical units. Hourly QPE accumulation is not instantaneous rain rate. Do not infer historical operational availability from S3 LastModified, which may reflect backfill.
- Do not fill missing observations with zeros or allow future observations into model input windows. Report coverage and eligibility explicitly.

## Repository and notebooks

- Put reusable code in an installable package under `src/ecore_weather/`, organized by responsibility. Keep notebooks focused on explanation, selection controls, figures, and small calls into the package.
- Author user-facing notebooks as valid Python percent-format scripts in `notebooks/`, using `# %%` and `# %% [markdown]`. Pair them with `.ipynb` using Jupytext; treat `.py` as the source of truth.
- Commit synchronized notebook pairs without credentials, large embedded data, or bulky execution outputs. Check script compilation and notebook synchronization for notebook changes.
- Maintain README links and Colab launch links for every user-facing notebook, updating descriptions when interfaces change.
- Notebook widgets and headless Python/CLI execution must use the same request and pipeline interfaces. Widget construction must not launch downloads or uploads.
- In Colab, clone the repository at a configurable revision, install the package, and report the resolved commit. Testing modified `src/` code through cloning requires that exact revision to be pushed first. Do not report Colab validation against a different revision as validation of local changes.
- Use `AGENTS.md` as the shared instruction source. Harness-specific instruction files should point to it rather than maintain competing copies.

## Environments

- Use Conda for local and future cluster development, with `environment.yml` as the environment specification. Prefer conda-forge for compiled geospatial/scientific dependencies and declare pip-only dependencies explicitly.
- Include Jupytext, notebook tooling, and the dependencies actually used. Keep dependency management reproducible; avoid an independently maintained `requirements.txt` as a competing environment definition.
- Colab may use a tested, version-matched pip bootstrap because it supplies its own notebook runtime. Keep this compatibility mapping aligned with the Conda environment.
- Keep optional GPU/model dependencies isolated from the CPU data pipeline. Do not assume Argonne resources use the same accelerator or CUDA stack as the local machine.

## Catalogs, storage, and performance

- Discover and persist selections before fetching data. Use STAC Collections and Items for interoperable metadata, with a linked executable selection manifest and run status records.
- Use anonymous access for NOAA AWS. Keep credentials out of URLs, logs, notebooks, STAC, and committed files.
- Default durable storage to the configured Hugging Face bucket. Support an explicit local destination and bounded local scratch with available-space checks. Never silently change durable destinations following an authentication/upload failure.
- The existing `HF_DATASET_REPO` setting currently names the project bucket. Migrate to `HF_BUCKET_NAME` with a documented compatibility alias; do not infer that it names a dataset repository.
- Use the HF token for Hub bucket APIs. Hugging Face S3 gateway credentials are distinct credentials; never treat an HF token as an AWS access key.
- Publish completed, validated partitions with provenance; support retries and resume without marking partial uploads complete. Do not delete unrelated existing local or remote assets.
- Optimize based on measured bytes, requests, wall time, decode cost, memory, and storage. Do not promise that STAC, Zarr, virtualization, or more workers automatically reduces source transfer.
- Bound I/O concurrency separately from CPU decoding. Use isolated temporary directories, manage file handles, avoid nested worker oversubscription, and coordinate shared writes.
- Preserve source encoding/calibration epochs when combining GOES files. Verify compatible grids, dtypes, codecs, scale/offset values, and quality-flag schemas.

## Current scope and evidence

- First deliver historical Puerto Rico research batch pipelines. Live ingestion services, training, fine-tuning, and NVIDIA model execution are outside the current implementation scope.
- Use original AWS MRMS CARIB data for Puerto Rico. Dynamical's cited MRMS CONUS collection does not cover Puerto Rico.
- Materialize lossless cropped MRMS Zarr where gzip prevents useful range virtualization. Support GOES range reads and optional virtual artifacts referencing AWS.
- Earthmover GOES-16 access is an optional authenticated comparison. It must not block the AWS-based workflow or be assumed to cover GOES-19.
- The released StormScope workflow has CONUS grid and substantial GPU requirements. Puerto Rico preparation must produce an honest compatibility report, not assert direct checkpoint readiness or forecast skill.
- Consult the supplied references, verify source metadata and current primary documentation, and distinguish verified findings from hypotheses. Record access limitations and relevant version changes.


## First notebook implementation priorities

- Retain the completed weekly pilot evidence (September 18–25, 2022 and September
  15–22, 2024). Current notebook examples and expanded tests use the periods below.
- Validate the unchanged mentor calculations before optimizing. Compare identical
  source files, report off-hour files separately, and repeat promising timing
  comparisons three times. Keep HF publishing separate from download timings.
- Write explanations in plain language. Keep Colab to runtime detection, cloning,
  and dependency installation; automated launch links and CI are later work.
- Use one small Zarr store per source object initially. More elaborate partitioning,
  production services, external mirrors, Icechunk, and model inference are deferred.
- Keep the mentor scripts unchanged. Their grid and missing-value behavior are
  reference behavior to measure, not rules for preserving scientific raw data.


## Expanded validation and documentation

- Current test periods are September 1–December 1 in 2022 and 2025 (UTC, end excluded): full hourly MRMS and full GOES discovery with representative scan reads.
- Default hourly MRMS matching is at or before the slot within five minutes. Preserve actual source timestamps and offsets; nearest either side is explicit opt-in.
- Maintain `docs/notebooks.md`, `docs/developer_guide.md`, `docs/validation.md`, and `docs/status.md` as functionality and evidence change.
- Both notebook sources must support argparse execution and dynamic widgets through shared functions. Default download workers to `max(1, multiprocessing.cpu_count() // 2)` and bound decoding separately.
- Provide inline imagery and optional PNG export in both interfaces. Keep processing in memory.
- Keep selections to a Collection plus ItemCollection and virtual references to one bundle. Validation uses temporary data and retains a compact result; delete successful test artifacts after recording evidence. Never delete unrelated or ordinary durable research data.


## H2 2022 archive and HF publication

- Current long sample is July 1, 2022–January 1, 2023: all available hourly MRMS and GOES-16 scans with C08–C11/C13–C16. This channel selection does not establish checkpoint compatibility.
- Stage locally and archive to `/mnt/p/ecore_eo_datasets`; verify Windows P: is actually mounted with `findmnt`. Do not mistake an ordinary WSL directory for the DAS.
- Keep HF publication separate from download concurrency. Use direct batch APIs and one publishing coordinator; completion follows raw read-back verification. Workstation POSIX locks are not distributed cluster locks.
- Support readable source/product/date paths and directory or ZIP Zarr, retaining one canonical raw container. Never copy Linux permission attributes onto DrvFS as a scientific metadata requirement.
- Maintain `docs/storage.md`, `docs/mentor_code_review.md`, and `docs/long_sample.md` alongside the existing guides and evidence.


## Shared archives and viewing

- Readable storage paths must not depend on the requested time window. Use source
  identity plus native pixel/band selection; keep request slots in run metadata.
- Verify earlier local stores before moving or removing duplicate raw containers.
  Keep relocation evidence and never delete unrelated archive files.
- Maintain the third local/HF dataset viewer notebook alongside the two fetchers.
  Geographic context and display masks must leave saved raw arrays unchanged.
- Final long comparison uses H1 2023 available hourly MRMS and January 2023 GOES-16
  CMIPF discovery with representative bands 1,2,3,7,8,9,10,13 scan comparisons.
