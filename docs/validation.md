# Validation and measurements

The weekly pilot and local notebook/script checks are complete. Expanded
September–November 2022 and 2025 validation passed; the six-month run is tracked in
[status](status.md). Colab remains an external check.

## Data and comparison rules

The original pilot periods are seven days in UTC, with the ending date excluded. MRMS uses
CARIB `MultiSensor_QPE_01H_Pass2_00.00` and the mentor's Puerto Rico region.
GOES uses GOES-16 `ABI-L2-MCMIPF`, C08/C13, and that same region.

The 2022 MRMS inventory contains 168 objects, but only 167 are at expected clock
hours. There is an extra September 24 **16:58 UTC** object and no **17:00 UTC**
object. Calling the mentor once for every listed object would round 16:58 down
to 16:00 and process that hour twice. The validation therefore calls all 168
expected hours, reports the missing 17:00 hour, and excludes the off-hour object
only from the matched speed comparison. Raw ingestion retains its actual time.

MRMS read bytes refer to full gzip objects; cropping does not reduce those bytes.
The processed-output comparison writes temporary GeoTIFFs with the mentor's
calculations. The raw-Zarr experiment does different work and is reported
separately. Stage seconds are summed per-file work time, not concurrent wall time.
Discovery and catalog-writing time are outside these fetch timings; the same
selection is already available to every method. The unchanged mentor still checks
each expected object before downloading. Peak memory samples the running process
and its children. Provider caches,
network conditions, and operating-system caches are not controlled.

## MRMS results

| Week | Expected mentor hours completed | Matched files | Mentor median | Four obstore workers, median | Ratio |
| --- | ---: | ---: | ---: | ---: | ---: |
| September 18–25, 2022 | 167 / 168 | 167 | 59.24 s | 9.55 s | 6.20× |
| September 15–22, 2024 | 168 / 168 | 168 | 59.60 s | 9.71 s | 6.14× |

Each median uses three fresh-process runs. **Every matched processed array and
grid transform was identical to the mentor output**, in both the initial
comparison and the repetitions. Mentor ranges were 59.16–60.91 s (2022) and
58.73–61.06 s (2024); obstore ranges were 9.54–9.55 s and 9.56–9.75 s.

The initial sequential revised reader took 30.61 s and 31.39 s. Four S3FS workers
took 9.82 s and 9.69 s; four obstore workers took 9.73 s and 9.60 s. These close
four-worker results **do not establish that obstore is better than S3FS**. The
measured benefit combines an already-selected file inventory, fewer source
existence checks, cached unchanged grid coordinates, and overlapping downloads.
It does not come from downloading smaller MRMS gzip objects.

Peak sampled memory (parent plus worker process) was about 0.83 GB for the mentor
and 0.91–0.96 GB for the four-worker revised run. The extra concurrency improved
time while using somewhat more memory; CPU decoding is bounded separately.

Evidence is consolidated in [weekly-summary.json](evidence/weekly-summary.json).
It retains timing rows, raw-storage totals, patch summaries, equality outcomes,
and the HF publish/resume result. Detailed successful-test artifacts were removed.

## GOES read throughput and block size

An H2 2022 GOES run at 8 bands and 6 full-disk scans per hour was tracking
roughly 1.75 s per file (about 12.6 h projected for ~26,000 scans). We measured
whether the fetch, not NOAA, was the limit by re-fetching one UTC day of GOES-16
MCMIPF 8-band scans (48 files) into temporary local Zarr stores, varying the
S3 backend, the range-read block size, and the worker count. Every variant
produced identical subset fingerprints, so the differences are speed, not
content. Two repeats each; wall time in seconds:

| Backend | Block | Workers | Wall (s) | vs baseline |
| --- | --- | --- | --- | --- |
| s3fs | 256 KB | 8 | 110.9 | — |
| obstore | 256 KB | 8 | 109.9 | −1% |
| obstore | 1 MB | 8 | 93.3 | −16% |
| obstore | 1 MB | 16 | **72.8** | **−34%** |
| obstore | 4 MB | 16 | 83.7 | −25% |

Summed per-file seconds show the dominant stage is the source read
(~620 s across 8 workers at 256 KB), not local writes or publishing
(~13 s publish). Larger 1 MB blocks cut read time about a third; 4 MB blocks
regressed, consistent with HDF5 reads over-fetching at very large block sizes.
The default GOES range-read block is now 1 MB, overridable in code.

Anonymous NOAA S3 documents no per-client rate cap at these levels. The observed
10–20 Mb/s on a much faster link is consistent with many small (256 KB)
latency-bound GET requests across a bounded worker pool, not throttling;
integrity `IfMatch` checks are kept on every read. These are single-day,
cache-sensitive measurements, not a sustained-load guarantee.

## Expanded checks and interfaces

The current periods are September 1–December 1 in 2022 and 2025, end excluded.

| MRMS period | Available / expected hours | Raw round trips | Mentor pixel and transform matches |
| --- | ---: | ---: | ---: |
| 2022 | 2,183 / 2,184 | 2,183 | 2,183 |
| 2025 | 2,184 / 2,184 | 2,184 | 2,184 |

Both full-period MRMS checks passed. The unavailable 2022 slot is September 14
05:00 UTC. September 24 16:58 and September 25 22:58 match the following nominal
hours; 2025 needs no shifted matches. A passing validation means available data
passed the checks, not that the archive has no missing hours.

The mean valid-pixel fraction for Puerto Rico in 2022 is 98.3914%. The longest
run of observed frames with no valid pixels is 21 across the selected patches.
For 2025 the mean valid fraction is 98.9415%, and the longest such run is 12
observed frames. These counts describe observed frames, not inferred durations
across missing files. Raw measurements and sentinel values remain unchanged.

Detailed compact results: [MRMS 2022](evidence/mrms-2022/validation.json) and
[MRMS 2025](evidence/mrms-2025/validation.json). Full-period validation runs legacy
comparisons in parallel batches to check correctness. The speed measurements in
the earlier table remain the repeated weekly pilot; the expanded checks do not
establish a new speed ratio.

MRMS matches one observation at or before each hour within five minutes and records
actual timestamps and offsets. The 16:58 example now matches 17:00 and reproduces
the mentor's output exactly, with only a source-selection adapter. The strict
weekly baseline above deliberately remains unchanged as historical evidence.

Twenty focused tests pass. Both notebooks executed with real source samples,
rendered figures, and temporary PNG export. Both argparse scripts fetched real
samples, reopened compact STAC selections, and saved figures; see
[interface checks](evidence/interface-checks.json). Explicit network client ownership
removed a duplicate-close warning exposed by GOES virtual reads.

Successful weekly scratch trees and the 36 HF smoke-test objects were deleted after
recording their results. Unrelated bucket data were left untouched. The expanded
validator retains one report per period and removes its own data and references.

## Expanded GOES results

Full inventories contain 12,972 GOES-16 objects for 2022 and 13,095 GOES-19 objects
for 2025. All six representative scans per period passed full/range/virtual equality
and raw storage checks. Reference creation took 135.30 and 119.16 seconds,
respectively, separately from subsequent reads. The inventory has 19 empty hours
in 2022 and one in 2025; mixed scan modes and partially filled hours are recorded
without assuming a missing-file count where cadence is uncertain.

See [GOES 2022](evidence/goes-2022/validation.json) and
[GOES 2025](evidence/goes-2025/validation.json). Only the representative scans were
read and compared; full inventory discovery is not validation of every GOES image.

## Scientific checks

- The raw writer checks every array and coordinate after a Zarr round trip, plus
  all dataset and variable attributes. It preserves numeric MRMS sentinels and a
  separate bitmap, and GOES packed values, calibration, flags, and coordinates.
- The mentor's interpolation is sensitive to tiny coordinate differences near
  zero rain and coverage gaps. The reader uses ecCodes' source-coordinate accessors,
  caches unchanged grid definitions, and restores the full source grid in memory
  for the exact comparison recipe. A one-pixel margin supports that comparison;
  a normally persisted crop cannot supply neighbours outside its stored edges.
- The mentor's written GeoTIFF has no CRS tag and no units tag in this tested
  environment. Its grid transform places the image over the intended region. The revised
  comparison preserves its pixel calculations and records the correct QPE unit,
  **mm accumulated over an hour**, separately. These are limitations of the
  reference output, not reasons to strip metadata from raw data.
- GOES virtual equality covers all selected image and quality variables,
  coordinates, scalar metadata, and time bounds. VirtualiZarr 2.7.3 requires a
  correction from `name/` to `name/0` for inline scalar chunk keys. Small metadata
  variables are read without CF decoding and inlined; image chunks remain NOAA
  references. No temporary NetCDF is referenced.
- GOES masks use decoded coordinates consistently. Applying a packed-coordinate
  mask to a decoded-coordinate array caused an empty result during development;
  a regression test now covers this case.
- Incompatible grids, calibration, compression, and quality-flag schemas form
  separate virtual groups. Per-scan quality percentages stay in each reference
  and do not imply a schema change.

## Local and storage checks

Both notebook pairs executed locally with one real source file each, using the
same functions as the widgets and temporary local stores. Normal execution only
constructs controls; buttons launch the larger runs. Committed notebook outputs
are cleared and `.py`/`.ipynb` cell contents are checked for synchronization.

A real MRMS subset was published to the configured HF bucket, verified by reading
it back, and reused on a second fetch. Publishing time includes upload, API calls,
and remote read-back validation; it is separate from source fetching and Zarr
writing. New raw-schema markers prevent earlier development outputs from being
mistaken for validated current output.

Unit tests cover missing values, valid zero, valid negative reflectivity, empty
patches, packed coordinates and quality masking, lossless round trips, session
processing without mutation, STAC round trips, half-open dates, scan selection,
explicit destinations, resume, and failure cleanup.

## Remaining external check

Colab CLI is installed, but `colab sessions` requires interactive Google sign-in.
No Colab session was allocated and no Colab execution is claimed. Its test must
use the exact pushed implementation revision. The notebook bootstrap is limited
to runtime detection, cloning/checking out that revision, and pip installation.

## Reproduce

Use [the notebooks](../README.md) or the same package functions from Python.
The requested selections are saved before fetching. Run reports contain source
identities, per-file outcomes, timings, and output locations. Validation intermediates are temporary and removed automatically; compact evidence
accompanies this report. Ordinary fetch outputs are durable research data.
Tested package versions are recorded in [versions.json](evidence/versions.json).

Source references: [NOAA MRMS](https://registry.opendata.aws/noaa-mrms-pds/),
[MRMS product definitions](https://www.nssl.noaa.gov/projects/mrms/operational/tables.php),
[NOAA GOES](https://registry.opendata.aws/noaa-goes/), and
[VirtualiZarr usage](https://virtualizarr.readthedocs.io/en/stable/how_to/usage.html).
The broader [reference list](agent_dev_references) remains available for later
mirror, storage, and model experiments.

## Earth2Studio monthly writer and study-job smoke checks (September 29, 2026)

The newer writer is separate from the earlier benchmark results above. In the
updated `ecore-weather` Conda environment, source-to-monthly-Zarr read-back
matched a live 2021 MRMS native crop and bitmap and a live GOES-16 C13 packed
crop and DQF. Both fetch notebooks ran their disposable smoke cells; the shared
viewer rendered monthly MRMS and GOES samples and the local DuckDB index rebuilt
from their completion manifests. A 20-minute, eight-product MRMS interval took
26.3 s with two product-month writer processes and 29.1 s with one process.
These are functional checks, not statistically reliable speed estimates.
Successful temporary artifacts were deleted. The HF S3 gateway passed a
27-byte write/read/delete check. A synthetic Earth2Studio monthly ZIP passed HF S3 upload,
read-back, completion-marker, and reuse checks; its remote test objects were
removed. The full study-period estimate and MRMS archive fetch have not run; see
[study jobs](study_jobs.md) and [current status](status.md).
