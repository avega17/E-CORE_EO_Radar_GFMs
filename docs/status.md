# Implementation status and handoff

Updated October 1, 2026, 13:38 UTC. A fresh audit at 13:01 verified all 264
MRMS product-month checkpoints and found the completed study collections
consistent. The yearly HF mirror is complete for 2021–2026, with all 24
product-year bundles verified. The GOES job is working through its staged
H1 2026 period. January 2026 is complete and checkpointed after all eight
GOES-19 band-month archives passed read-back; February 2026 is now fetching.
The current commands and live progress are recorded at the end of this file.
The GOES estimate and inventory scenarios remain complete; earlier weekly and
three-month timings in [validation.md](validation.md) are historical evidence.

## Verified on the current code

- The full local suite passed 87 tests after the selection-hash fix. It used an
  isolated temporary DuckDB so validation did not contend with the active GOES
  writer. A targeted 35,700-source regression test confirms `Selection.id` is
  evaluated once per indexed collection.

- The `ecore-weather` Conda interpreter imports Earth2Studio 0.18.0, DuckDB,
  and Zarr. The bare Zarr timeout occurred only in this agent's restricted
  runtime: an unsandboxed temporary write/reopen returned `[1, 2, 3]`.
- An unsandboxed unique write/read/delete probe succeeded in
  `/mnt/p/ecore_eo_datasets`. The MRMS job repeats it and a tiny Earth2Studio
  Zarr round-trip before data fetching.
- A live 2021 CARIB precipitation-rate crop and azimuthal-shear crop decoded
  with their GRIB bitmaps. The S3 azimuthal-shear keys are
  `MergedAzShear_0-2kmAGL_00.50` and `MergedAzShear_3-6kmAGL_00.50`; their
  descriptive NOAA product names differ. The inspected low-level file was only
  215 bytes and decoded to an all-zero 1536 × 1536 ROI, so zero must not be
  counted as confirmed valid or confirmed missing without more context.
- A live GOES-16 C13 2021 source-to-monthly-Zarr read-back matched packed CMI
  and DQF values. A live MRMS source-to-monthly-Zarr read-back matched native
  values and bitmap. The one-file GOES source was about 27 MB, its range read
  transferred about 9.2 MB, and its uncompressed ROI NetCDF crop was about
  227 KB. These are distinct measures, not a study-period estimate.
- A temporary 20-minute eight-product MRMS run succeeded with two independent
  monthly writer processes in 26.3 s. The one-writer comparison took 29.1 s.
  This short, cache-sensitive result is only a functional check. Successful
  test archives were removed.
- A one-day GOES metadata inventory returned 2,304 available CMIPF band files:
  1,152 / 576 / 192 for the eight-band 6 / 3 / 1 per-hour scenarios, and 2,304
  for 16 bands at six per hour. Its successful test outputs were removed.
- A 27-byte HF S3 gateway object passed write/read-back and was deleted. A
  synthetic Earth2Studio monthly ZIP also passed full HF publish/read-back,
  completion-marker, and reuse checks under a unique smoke prefix; its two
  remote objects were removed. This does not establish large-upload throughput.
- Earth2Studio `AsyncZarrBackend` also completed a direct HF S3 write–close–
  reopen check through obstore: two small native-dtype arrays and timestamps
  matched. The probe took 38.85 s for 13 objects totaling 4,057 bytes, then
  removed its test prefix. It establishes compatibility, not a practical
  high-frequency write rate. Direct HF fetch mode still supports the async
  monthly writer; local monthly archives use ZIP, and the study mirror now
  backs them up as one yearly ZIP per MRMS product.
- A synthetic two-observation month and a live two-observation 2021 MRMS month
  passed direct HF async write, remote array/provenance read-back, month merge,
  repeat reuse, and test-prefix cleanup. The live merge exposed a shard/chunk
  divisibility issue, now corrected by using one full-ROI spatial chunk. These
  small checks do not measure representative-month rate limits or throughput.
- A second isolated remote month reopened through the project
  `MonthlyZarrSource` and appeared with both observations in the HF viewer's
  time-filtered search. Its test objects were removed afterward.
- A live one-scan GOES-16 C13 CMIPF fetch wrote directly to HF and reopened
  with packed `CMI_C13`, `DQF_C13`, calibration attributes, and projection
  metadata intact. The check passed again with the 72-slice grouped writer;
  its 186,676-byte test store was removed. The repeat's HF write/metadata
  stage took 142.9 s; a single scan is too small to extrapolate full-month
  GOES throughput.
- Both fetch notebooks executed their built-in disposable smoke paths in the
  updated Conda kernel. The shared viewer produced 64 × 64 Leaflet frames from
  live one-observation MRMS and GOES monthly archives, and a fresh DuckDB index
  rebuilt from each completion manifest and found its observation.
- Viewer 03 now selects observations after **Find** for both MRMS and GOES,
  including separate per-band GOES monthly stores. A live MRMS monthly ZIP and
  an existing GOES multiband ZIP each produced an interactive-map frame. The
  MRMS fetch currently holds DuckDB's writer lock; direct ZIP paths bypass the
  index and broader searches fall back to completion manifests. The live HF
  yearly-backup browser listed 16 verified 2021–2024 MRMS packages, inspected
  one annual manifest by byte range, restored its 36,721-byte 2020-12 monthly
  member to disposable scratch, and rendered its radar sample. Scratch was
  removed. A live widget check also listed the package, inspected its months,
  prepared that member in disposable scratch, and selected its observation.
  The new storage explorer reads completion/STAC metadata without
  opening image chunks; its percentages compare retained ZIP bytes against
  listed NOAA source-object bytes, which may already be compressed.
- The synchronized viewer notebook executed its three code cells in the
  `ecore-weather` Jupyter kernel without errors, and its saved copy has no
  execution output. MRMS and GOES single-image CLI runs each wrote a PNG from
  a real local archive; their disposable output directory was removed. The
  focused viewer tests pass, and the combined data/viewer suite passed 80
  tests. A live HF storage-widget check listed the annual products, inspected
  one product/year, and displayed monthly size comparisons.

## Current architecture

Local monthly archives now stream a bounded prefetch of up to 16 source reads
per archive into Earth2Studio's `ZarrBackend`, then verify the packed ZIP before writing
`complete.json`. Repeated requests merge only new observations and reuse a
fully complete month. Each source's non-spatial calibration and projection
values are retained in the per-observation metadata sidecar. One coordinator
writes DuckDB; product-month workers do not. The study script defaults to two
independent product-month writer processes and a local P: destination. Its
source-read thread count is independently configurable per writer and capped
at 16; decode slots are independently configurable too. The live continuation
uses four writers × eight source-read threads and one decode slot per writer,
for at most 32 in-flight reads and four concurrent decodes. An Argonne node can
use four writers × 16 reads to test up to 64 I/O tasks without changing the
writer or decode counts. Direct HF fetch mode writes
versioned monthly Zarr stores with Earth2Studio's async backend and updates
`complete.json` after full remote read-back. The study backup instead packages
verified local monthly ZIPs into one HF ZIP per MRMS product/year, avoiding
per-observation remote writes during backup.

`scripts/estimate_goes_study.py` inventoried GOES-East CMIPF metadata over
2021-01-01 through 2026-06-30, with 257 bounded crop samples. All 66 month
checkpoints and four scenarios passed summary checks. The eight-band six-scan
scenario estimates 0.75 TB central compressed Zarr; all 16 bands at six scans
estimate 1.04 TB central. These are extrapolations, not downloaded datasets;
see [the full estimate](goes_study_estimate.md).
`scripts/fetch_mrms_study.py` starts with 2021 across four default products,
records
coverage, and makes a measured continuation choice. See
[study jobs](study_jobs.md) for commands, outputs, and resume behavior.
The latest machine-readable evidence is in
`results/study-mrms/resume-audit-20260930.json`; it verifies all local MRMS
product-months and all 24 remote yearly bundles. The MRMS mirror watcher has
finished. Check `results/study-goes-fetch/mrms-gate.log`,
`results/study-goes-fetch/checkpoints/`, and the stage-specific output folders
for GOES progress.

The four 2021 yearly packages total 3,611,749,226 bytes. Each remote object was streamed back and matched its local SHA-256; there were no 429 responses. Seven old or interrupted month-level prefixes were removed only after all four packages passed. Local monthly DAS archives remain unchanged. The cleanup gate removed the one classified old per-observation MRMS tree. Its follow-up dry run found eight current Earth2Studio MRMS product trees, no remaining legacy or unclassified MRMS trees, and no `/mnt/p/ecore_eo_datasets_zarrV2` root. Legacy GOES ROI trees stay untouched until their corresponding new archives are complete.
The scratch Zarr builds are temporary and not restartable: an unplanned process
or host failure can require refetching active product-months, while already
marked monthly archives remain reusable. Each completed product-month is
published and verified independently; the pipeline does not wait for all
variables before copying its archive to DAS. The configured 12-hour MRMS pause
is at a calendar-month boundary and lets current product-month writers finish
before exiting.
The GOES gate deletes only classified legacy MRMS ROI trees after the full local
MRMS audit passes. It leaves GOES and unclassified trees in place. The HF yearly
mirror is a separate backup check; it is not a prerequisite for deleting
verified legacy MRMS.

The eight readable MRMS `--product` aliases now parse to their exact NOAA keys;
all eight focused alias tests passed on 2026-09-29. The full
`tests/test_data.py` suite now passes 73 tests with dependency warnings,
including a test that yearly MRMS packages preserve monthly ZIP bytes and
checksums, plus checks for scoped HF writer locks and bounded month-mirror batches,
the four defaults, run-config history, and month-boundary stop gate. The CLI help lists the readable names. Jupytext confirms that
`notebooks/01_mrms.py` and its notebook partner are synchronized. The guarded cleanup inventory identifies one old per-observation
Pass2 tree and eight current Earth2Studio monthly trees, with none left
unclassified. The cleanup script recognizes the old marker schema and saved a
deletion plan before removing the single classified legacy MRMS tree. Its HF
audit checks all 24 product-year bundles, and its deletion plan targets
classified MRMS ROI trees only so legacy GOES remains.

### 2023-05 composite reflectivity recovery

The May 2023 composite-reflectivity run stopped because two NOAA CARIB catalog
entries are zero-byte objects. A scan of all 4,464 listed source objects found
that the other 4,462 pass gzip and GRIB2 framing checks. The fetcher now keeps
the complete source selection in STAC, excludes only zero-byte objects from
decoding, and records their IDs, keys, times, sizes, ETags, and reason in the
month checkpoint. The completion audit verifies that each such record exactly
matches a zero-byte STAC item and that no invalid item appears in the archive.
This keeps absent source files distinct from valid zero rain and pixel-level
missingness.

A bounded retry completed the canonical May composite-reflectivity archive:
4,462 observations, 163,296,228 listed bytes, 163,275,265 returned source
bytes, and 199,155,621 bytes stored. Two requested slots remain explicitly
unavailable. The saved archive passed the monthly read-back checks. May 2023
low-level shear completed with 4,463 observations in 789.6 seconds, and Pass2
QPE completed with 741 observations in 688.0 seconds. The original failure
record is retained under `failure-history/`, and the May product selections
remain available. A later `Expected one complete GRIB2 message` read failure
was followed by a successful retry. A bounded reread of the 4,462 nonempty
objects found no gzip or GRIB framing errors, consistent with a transient read.
The reader retries size, simple ETag-MD5, gzip, and GRIB-length integrity
failures up to three reads before it reports the NOAA key. May 2025 low-level
shear completed with 4,464 observations in 1,429.4 seconds; Pass2 QPE completed
with 744 observations in 1,295.6 seconds. The shear archive passed stored-size
and SHA-256 checks, and both product-months pass the latest audit.

## Remaining gates and limits

- The GOES inventory and estimate are complete, but the 66-month image fetch
  remains in progress. The active first-month build has not yet produced a
  completed monthly checkpoint. The audit reports are
  `results/study-mrms/resume-audit-20260930.json` and
  `results/study-goes-fetch/mrms-gate-status.json`. All 24 HF yearly backups
  are verified.
- Verify a longer interrupted/resumed month, peak memory, and source provenance
  on the exact study outputs. Legacy archive migration is not a gate.
- A Colab check on the pushed revision remains a follow-up and does not block
  the local DAS study job.
- A January 2021 precipitation-rate HF pilot completed. The first
  observation-at-a-time attempt was interrupted after showing high remote
  per-call latency, and its unmarked objects were removed. Twelve-observation
  batching reached 223 of 4,464 observations in 191 seconds and 446 before
  interruption; its unmarked objects were also removed. The final 72-slice
  grouped writer wrote and fully read back 4,464 observations: 48,608,757
  remote bytes in 134 objects, 400.8 s upload/metadata stage, 467.1 s end to
  end, and about 2.5 GB observed peak resident memory. No 429 occurred in this
  one-month pilot. A repeated call reused the archive in 2.1 s.
- Legacy GOES archives remain until all corresponding Earth2Studio study
  periods are fetched and validated. The full local MRMS audit passed, its one
  classified legacy tree was deleted, and all yearly HF bundles passed their
  remote checks. The staged GOES process is active on January 2026 and proceeds
  in descending half-year batches with monthly checkpoints.

## Dataset explorer update (September 30, 2026)

The viewer notebook is now named `03_explore_datasets.py` with a synchronized
`03_explore_datasets.ipynb` partner. A sequence prepares frames by opening each
monthly ZIP store once, rather than reopening the same store for each timestamp.
On the local January 2021 precipitation-rate archive, 12 spaced frames at a
192-pixel display size took 0.889 s through grouped reads, compared with 7.270 s
when each frame reopened the archive (8.2x in this one sequential check; no
cache reset, so treat it as a focused regression measurement, not a general
throughput claim). The viewer tests passed 11/11. A stale progress update after
the grouped-read loop referenced an undefined `i`, breaking single-frame and
both animation modes after frame preparation; it is removed, and the regression
test checks progress updates across multiple monthly stores.
An additional MRMS regression check verifies grouped reads select each
timestamp from the full monthly store rather than repeating its first frame.
The local January 2021 precipitation-rate archive also produced four distinct
display-frame hashes for timestamps spanning the month.

The map animation now links Play and the frame slider through the notebook
kernel, where each index change updates the Leaflet image overlay. This ensures
the raster refresh is delivered alongside the changing frame label. A widget
regression test advances Play and checks that both the overlay image and
timestamp change; the interactive browser display should be checked after
restarting the notebook kernel so it imports the updated viewer module.

The writer remains on one-time-step by 256-pixel spatial chunks. This favors
single-frame maps and bounded spatial queries; alternative chunk sizes should
wait for representative map, patch, and time-series benchmarks. Storage ratios
are sums of verified archive bytes against complete listed NOAA object sizes.
They are weighted byte comparisons, not compression ratios; partially listed
source sizes now show coverage and do not masquerade as complete totals.

## Study job check (October 1, 2026, 02:05 UTC)

The latest MRMS audit still reports all 264 product-month checkpoints complete.
The annual mirror watcher reached `complete` for 2021 through 2026, with four
verified product bundles for each year. The most recent legacy-cleanup inventory
found no remaining legacy or unclassified MRMS trees; legacy GOES remains
untouched. The staged GOES process is active on January 2026 with four monthly
writers. Its selected month contains 35,700 CMIPF band files; the four active
scratch archives are still building and no month completion checkpoint exists
yet. Leave those scratch stores in place until the fetcher verifies and publishes
the month. The running command is the resumable `fetch_goes_staged.py --phase
all` flow, which continues through descending half-year periods after each
checkpoint. A live scratch check found C01/C02/C03/C07 time-chunk indices at
2996/971/2611/3753 respectively, confirming that the January stores are
advancing rather than stalled.

### GOES run recovery and current stage (October 1, 2026, 13:38 UTC)

January's selection contains 35,700 GOES-19 CMIPF band files. Its existing
monthly report records a 26,219-second (7 h 17 min) wall run, 437,479,514,610
bytes read through NOAA range requests, and 12,410,480,150 bytes across the
eight retained Zarr ZIP archives. The archived observations per band are C01
4,463; C02 4,463; C03 4,463; C07 4,463; C08 4,463; C09 4,461; C10 4,461; and
C13 4,463. The two lower counts are the available source scans, not padded
times. Every report entry was `saved`; all eight archive files and completion
markers exist, and marker source, product, satellite, band, observation count,
stored size, and archive-hash fields matched the report.

The first staged-run attempt finished writing January but stopped before
checkpointing because `_verify_month` searched only at the month directory
level, while the CLI writes its report under `ABI-L2-CMIPF/`. The verifier now
searches product subdirectories, and a regression test covers that layout. I
validated the existing report and markers, then wrote the missing January
checkpoint without refetching imagery. The earlier interrupted attempt record
is preserved under `failure-history/`. The staged job has resumed and logged
`Reused checkpoint 2026-01-01`; it is now fetching February 2026, whose
selection contains 32,215 band files. At the latest check, PIDs 37350 (gate),
37694 (staged runner), and 37726 (February fetch) were active with four month
writers, with eight source-read threads per writer (up to 32 concurrent reads).
The latest 30-second check saw active band-store time-chunk counts advance from
C01/C02/C03/C07 1,123/338/943/1,340 to 1,135/344/954/1,362; each writer's OS
write counter also increased. Zarr metadata show 4,028 time
positions for these arrays, but the chunk counts are only progress indicators
while archives are incomplete. These are in-month counts, not completion
markers. February has no month checkpoint yet. Keep its scratch stores intact
until the fetcher verifies and publishes the month. The runner continues in
descending half-year order with monthly verification checkpoints.

January's measured runtime confirms that the complete study is not an
overnight job. The existing empirical estimate remains 8.4–118.6 workstation
days, with a 19.3-day center; it is an estimate, not a measured full-study run.
The current process will continue the requested full period. Reassess batching
and concurrency using completed monthly reports rather than extrapolating from
the first four bands or from listed full-file sizes.
