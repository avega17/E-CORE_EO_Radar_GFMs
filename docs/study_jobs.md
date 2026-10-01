# Study-period jobs

The scripts share the notebook readers and Earth2Studio monthly writer. Run them
from the repository root with the updated `ecore-weather` Conda interpreter.
Dates are UTC and the end is excluded. The study period is January 1, 2021
through June 30, 2026 (`--end 2026-07-01`). The Puerto Rico bbox is
`[-70.24, 14.36, -62.56, 22.04]`. The mentor's 768 × 768 number describes
MRMS pixels, not an area of 768 km²; native GOES bands have different shapes.

## GOES: estimate only

```bash
/home/asvnpr/miniforge3/envs/ecore-weather/bin/python scripts/estimate_goes_study.py --dry-run
/home/asvnpr/miniforge3/envs/ecore-weather/bin/python scripts/estimate_goes_study.py --output results/study-goes-estimate
```

The first run inventories CMIPF objects month by month, checkpointing compact
monthly JSON summaries under `inventory/`. It handles the April 7, 2025 15:00
UTC GOES-16/19 operational handoff. It counts eight bands at six, three, and
one scan per hour, plus 16 bands at six per hour. Counts and listed full-file
bytes come from NOAA object metadata. No full-study raw imagery is fetched.

For size and time estimates, the script first checks one representative C13
crop, then reads at most 16 band files for each selected day/night seasonal
sample. Samples are native packed-pixel ROI reads. It measures transferred
range-read bytes, an uncompressed cropped NetCDF file, and compressed Zarr
chunks. Successful temporary sample files are removed, while compact
`samples.json` measurements persist for resume. `summary.json` has low,
central, and high empirical extrapolations; `scenarios.csv` is a compact table.
The reported local time extrapolates sampled read and write task seconds. It is
not a measured multi-process job wall time or an HF upload forecast. Listed
full-file S3 bytes are never treated as retained ROI bytes. If a selected
satellite/band lacks samples, its size/time estimate is `null` and the script
returns exit code 2. `--inventory-only` deliberately leaves estimates null.

The GOES notebook script uses the same estimator:

```bash
/home/asvnpr/miniforge3/envs/ecore-weather/bin/python notebooks/02_goes.py \
  --operation estimate --output results/study-goes-estimate
```

Use a new output directory when changing the ROI. Monthly inventory checkpoints
are reused only for matching date bounds; sample checkpoints reject a different
ROI. Repeat an interrupted command to continue from the first missing month or
sample.

## MRMS: fetch and verify monthly data

```bash
/home/asvnpr/miniforge3/envs/ecore-weather/bin/python scripts/fetch_mrms_study.py --dry-run
/home/asvnpr/miniforge3/envs/ecore-weather/bin/python scripts/fetch_mrms_study.py \
  --phase auto --destination /mnt/p/ecore_eo_datasets --output results/study-mrms
```

Before NOAA reads, the script writes and removes a unique P: probe and verifies
a tiny Earth2Studio Zarr archive on that mount. It checks source availability
per product-month and reads a representative source object before writing.
The four default study products are precipitation rate, composite reflectivity,
low-level azimuthal shear, and multisensor Pass2 one-hour QPE. The first three
use ten-minute request slots; Pass2 uses its hourly cadence. Other products,
including radar-only QPE, mid-level shear, and Pass1 QPE, remain available by
explicit selection. Azimuthal shear is a radar rotation proxy; its zero code is
ambiguous and is recorded separately from valid rainfall zero. See the exact
four S3 product IDs in the dry-run output and the [NOAA MRMS
product table](https://www.nssl.noaa.gov/projects/mrms/operational/tables.php).

The job first covers 2021 (48 default product-month checkpoints). The remaining
54 months require 216 checkpoints, for 264 across the study period. Once all
2021 default product-months have verified checkpoints, it extrapolates the
remaining nominal work from observed 2021
seconds and output bytes, with a 30% margin. If the continuation fits 12 hours
and 125% of projected archive bytes fit on P: while a separate scratch
allowance fits on the configured scratch disk, it can continue month by
month. Otherwise it stops after 2021 with exit code 75 and writes
`continuation_plan.json`. Run the saved `--phase remaining` command repeatedly:
it chooses the first incomplete year (2022–2025, then January–June 2026) when
annual partitioning is indicated. Exit code 75 also means the 12-hour limit
was reached at a month boundary; rerun the same command. Exit code 1 means an
error that needs inspection. Product-months without matching historical CARIB
objects get an explicit `unavailable` checkpoint, never a zero-filled archive.
If a catalog-listed NOAA object is itself zero bytes, it remains in the saved
STAC selection but is excluded from pixel decoding and listed in the checkpoint
as an unavailable source file. The archive stores the other valid observations;
the audit permits only these explicitly recorded zero-byte omissions.

Each product-month has a small JSON checkpoint under `months/YYYY-MM/` and a
saved STAC selection. Archive data live under stable product/ROI/year/month
paths on P:. The parent alone updates the local DuckDB index. Each worker
publishes and verifies its single product-month archive independently, so the
next variable does not need to finish first. A completed archive has
`raw.zarr.zip` and `complete.json` after read-back validation.
`--monthly-writers` controls concurrent, independent product-month archives;
`--workers` controls source-read threads inside each writer and is capped at 16
per archive. `--decode-workers` separately controls GRIB decode slots per
writer. For the 32-thread workstation, the current live setting is four writers
× eight source-read threads and one decode slot per writer. This allows up to
32 concurrent I/O reads without multiplying writer processes beyond four. For
a 64-thread Argonne node, these settings can be raised independently after
measuring the node; for example, four writers × 16 source reads allows up to 64
in-flight reads, while one decode slot per writer keeps the decode pool
bounded. These are limits, not a guarantee that more threads will increase
throughput.

Repeat the same command after interruption; verified product-months are reused.
Existing archives for products outside the current defaults remain untouched.
When request defaults change, the prior run configuration is kept in
`run-config-history/`. `summary.json` records newly completed work, and
`failure.json` records the last error.

For a short functional run, set both `--start` and `--end` and use a disposable
local destination and output directory. Successful test archives should be
removed after retaining compact timings. A short run cannot establish a
full-year throughput or memory bound. Do not run the GOES estimate and MRMS
fetch together if their timings will be compared.

## Remaining validation

The current code passed one-observation MRMS and GOES source-to-Zarr read-back,
one-day GOES inventory, a short one/two-writer MRMS comparison, and synthetic
plus live two-observation direct HF async monthly read-back/merge/reuse checks.
The January 2021 precipitation-rate HF month also passed full remote read-back.
Before relying on the full study data, inspect a
representative full month for source counts, coverage, calibration, bitmap,
peak memory, resume behavior, and DuckDB rebuild/viewer access. A small
Colab run on the pushed revision is a separate check and is not a prerequisite
for this local P: job. See [status](status.md) for current
evidence and limits.

The initial background supervisor used a frozen copy of the older remote
writer. `scripts/handoff_mrms_supervisor.py` can pause only that supervisor,
allow its active local child to finish the current run, then launch a newer
verified snapshot. Its `--dry-run` checks process identities and the new
snapshot without sending signals. The real watcher writes
`results/study-mrms/handoff_status.json` and `handoff.log`; if it fails before
replacing the supervisor, it resumes the old one rather than leaving the local
fetch paused. Check those files and live PIDs before starting another job.

The staged MRMS supervisor starts a detached mirror watcher after the local
2021 checkpoints are complete. It waits for each later year to have all its
product-months checkpointed, then publishes one HF backup ZIP per product/year
while the fetch proceeds. Only one year-level bundle job runs at a time; each
job uploads the independent product bundles with four workers. The watcher
records `waiting_for_year`, `mirroring`, `failed`, or `complete` and its
completed years in `results/mirror-mrms-yearly/background-status.json`. A year
is complete only after the bundler reports all four uploads and read-back
checks. The final 2026 bundle covers January through June. To run a single year manually:

```bash
/home/asvnpr/miniforge3/envs/ecore-weather/bin/python scripts/mirror_mrms_year_bundle.py --year 2021 --dry-run
/home/asvnpr/miniforge3/envs/ecore-weather/bin/python scripts/mirror_mrms_year_bundle.py \
  --year 2021 --source-root /mnt/p/ecore_eo_datasets --study-output results/study-mrms \
  --writers 4
```

The bundler requires every in-scope product-month checkpoint to be complete or
explicitly unavailable (48 checkpoints for a full year, 24 for H1 2026). A
boundary match may reference archives stored in two calendar months. It checks
each local ZIP against the saved completion SHA, packages monthly ZIPs, STAC
selections, and their coverage checkpoints, and uploads four independent
product-year files. Including checkpoints preserves reasons for any unavailable
source objects alongside the full listings. It then reads each
complete HF object back and compares its SHA-256 before publishing its marker.
Only after all four packages pass does it remove matching old month-level
prefixes; unfamiliar or mismatched prefixes are preserved. The P: monthly
archives remain unchanged. Reports and package files are kept under
`results/mirror-mrms-yearly/<year>/`.

The first run published four packages totaling 3,611,749,226 bytes (3.36 GiB).
All four streamed read-backs matched their local SHA-256 values, with no 429
responses. The run removed seven old or interrupted month-level prefixes;
there are no remaining preserved prefixes. `--cleanup-only` rechecks the
yearly markers, report, and object sizes before retrying exact-prefix cleanup.

Use `scripts/audit_study_completion.py` for the final check. It validates the
GOES inventory, all 264 MRMS product-month checkpoints against their saved STAC
item IDs and local archive manifests, and—when `--check-hf` is provided—all 24
yearly HF product markers (four products for 2021–2025 plus H1 2026) against
their saved bundle reports and remote object sizes. HF verification is an
optional backup check. The cleanup script requires the full local MRMS audit
to pass, then removes only classified MRMS ROI directories; a complete HF
backup is not a prerequisite to remove verified legacy duplicates. The full
The local audit passed again at 2026-10-01 00:17 UTC: all 264 MRMS
product-month checkpoints, STAC selections, and local archive manifests
matched; the four GOES inventory scenarios also passed. The gate removed one
classified legacy MRMS tree. A follow-up cleanup dry run found no remaining
legacy or unclassified MRMS trees and no legacy V2 root. Legacy GOES remains in
place until the corresponding new archives are fetched and checked.

The legacy cleanup removes only classified MRMS ROI directories. If the older
V2 root exists, it scans for MRMS product trees inside it and leaves the root,
unclassified content, and all GOES trees in place.

After the full MRMS audit passes and legacy MRMS storage is cleaned, fetch GOES
to the same local destination in this order: H1 2026, then H2 and H1 of each
year from 2025 down through 2021. `scripts/fetch_goes_staged.py` runs the same
`02_goes.py` CLI once per month, using CMIPF, all available scans, automatic
GOES-East satellite selection, and bands C01/C02/C03/C07/C08/C09/C10/C13. Its
monthly checkpoints verify each archive and marker, and reruns reuse completed
months. If an earlier run saved a matching monthly STAC item selection but
stopped before completing that month, the runner checks its dates, region,
product, and bands, then reuses the selection instead of repeating the NOAA
inventory. DuckDB selection and fetch records use a vectorized batch insert so
large GOES months do not spend a long serial pass inserting each source file.
This changes only the local search index; the STAC selection remains the durable
record.

```bash
/home/asvnpr/miniforge3/envs/ecore-weather/bin/python scripts/fetch_goes_staged.py \
  --phase h1-2026 --destination /mnt/p/ecore_eo_datasets
```

After that stage, run with `--phase h2-2025`, then each half-year phase in
descending order through `h1-2021`. `--phase all` resumes every remaining
stage in order.

`scripts/run_goes_after_mrms.py` can wait for the full local MRMS audit, run the
guarded removal of classified legacy MRMS archives, then launch `fetch_goes_staged.py`
through all 11 half-year stages. It leaves legacy GOES files in place. The
current background gate uses four independent monthly writers with eight
source-read threads per writer and one decode slot per writer. Its state and log
are `results/study-goes-fetch/mrms-gate-status.json` and `mrms-gate.log`;
rerunning it is safe because cleanup is audit-gated and the GOES fetcher reuses
matching STAC selections and verified monthly checkpoints. The January 2026
selection-index pass was restarted after changing the indexer to compute the
full-selection hash once instead of once per file. The active PIDs and checkpoint
state are recorded in [status](status.md).

For a workstation run that should keep resuming month-boundary pauses, use the
supervisor after the GOES estimate has finished. Freeze the current `src/` and
`scripts/` first so later edits in the active checkout cannot change what a
spawned worker imports partway through the long run:

```bash
/home/asvnpr/miniforge3/envs/ecore-weather/bin/python scripts/create_study_snapshot.py
# Use the printed path in place of <snapshot>:
/home/asvnpr/miniforge3/envs/ecore-weather/bin/python \
  results/study-code-snapshots/<snapshot>/scripts/run_mrms_staged.py --dry-run
/home/asvnpr/miniforge3/envs/ecore-weather/bin/python \
  results/study-code-snapshots/<snapshot>/scripts/run_mrms_staged.py \
  --destination /mnt/p/ecore_eo_datasets --output results/study-mrms
```

For the actual long run, execute the **snapshot** `scripts/run_mrms_staged.py`
path. The snapshot
manifest records SHA-256 hashes of every Python source file, and each child
run config records that manifest's code hash alongside the Git revision.
To keep the workstation job running after the terminal closes, use
`scripts/launch_mrms_background.py --snapshot results/study-code-snapshots/<snapshot>`
after the 66-month GOES `summary.json` reports `sample_complete: true`. It
checks the snapshot hashes, writes `results/study-mrms/launcher.json`, and sends
progress to `results/study-mrms/staged.log`. Pass `--monthly-writers`,
`--workers`, and `--decode-workers` to select writer, source-read, and decode
concurrency independently. The current job resumed from verified checkpoints with:

```bash
/home/asvnpr/miniforge3/envs/ecore-weather/bin/python scripts/launch_mrms_background.py \
  --snapshot results/study-code-snapshots/84882249397dac9c \
  --destination /mnt/p/ecore_eo_datasets --output results/study-mrms \
  --scratch results/study-scratch --monthly-writers 4 --workers 8 \
  --decode-workers 1 --max-hours 12 --mirror-writers 4
```

The current supervisor reuses completed 2021, starts a detached year-by-year
HF mirror watcher, then resumes 2022–June 2026 from the first incomplete
product-month. It stops on a real child failure or a run that
makes no checkpoint progress; `orchestrator_status.json` records the last exit.
Rerun the same command after fixing a failure or reboot. The supervisor does
not start the GOES job or run the two network-intensive jobs together. Its
default scratch path is `results/study-scratch` on the Linux root disk, with
much more free space than the 48 GB `/tmp` tmpfs; temporary month builds are
removed after verification or failure.

The MRMS reader checks each object's listed byte count, a 32-character
single-part S3 ETag when present, gzip integrity, and the GRIB2 message length
before decoding. It retries a failed integrity check up to three times, then
reports the source key and observed sizes and fails that product-month. It does
not skip a nonempty but invalid object or publish an incomplete month.
