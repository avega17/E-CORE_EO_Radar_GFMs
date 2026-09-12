# Implementation status and handoff

Updated September 10, 2026.

## Completed

- Both three-month MRMS checks: 2,183/2,184 available files in 2022/2025, all raw
  round trips and mentor calculations match. One unavailable 2022 hour remains explicit.
- Both three-month GOES checks: full inventories (12,972 and 13,095 objects), six
  representative scans each, all full/range/virtual arrays match and raw writes pass.
- Notebook and script samples, plots, compact STAC validation, virtual concatenation,
  and weekly benchmark evidence. The 429 investigation supersedes the first HF writer.
- HF publisher now batches writes and read-back, uses one workstation coordinator,
  and writes completion markers last. Directory and ZIP Zarr preserve raw content.
- Readable product/date paths, optional independent HDF5 readers, and byte-only
  copying with verification for Windows NTFS/DrvFS. Mentor scripts remain unchanged.
- Detailed mentor review, HF storage/access guide, and six-month experiment guide.
- Selection controls now include a collapsed product and variable guide (CARIB
  products with units and missing-value codes; full-disk GOES products and all 16
  ABI bands). GOES widgets offer full disk only; CONUS stays command-line only.
- ecCodes "Truncating time" stderr notices are suppressed during GRIB metadata
  reads; a smoke run over the 16:58 off-hour file printed zero notices. Both
  notebooks were re-executed in smoke mode after these changes; temporary outputs
  were cleaned. Stored metadata is unchanged, so archives remain reusable.
- Earth2-Studio review and raw-data rationale documents; the review adopted no
  code changes (memoized listings and corrupt-file fallback are unneeded here).
- New fetches write Zarr format 3 stores; the earlier format 2 archive is
  preserved read-only at `/mnt/p/ecore_eo_datasets_zarrV2` and still viewable.
  The earlier long runs were stopped and will be restarted from scratch as v3.
- GOES discovery gains a scans-per-hour control (default 1, nearest the top of
  the hour); validation, benchmarks, and the long-run script pin every available
  scan. The dataset viewer decouples band from the search (band buttons switch
  without re-searching), uses short icon buttons, and exports HTML/GIF/MP4.
- `fetch_long_sample.py`/`long_sample_run.md` renamed to `dataset_fetch_run.*`;
  the doc records the script-vs-bash decision and the two configured v3 runs.

## Current checks and archive state

H2 2022 MRMS completed: 4,412 available files fetched and archived as verified ZIP
Zarr on Windows P:, with local cache copies removed. See the compact H2 evidence.
The old H2 GOES script is stopped; its last progress record reports 9,750/26,343
files. It is not a completed six-month GOES archive. User notebook kernels were
subsequently found active with reader processes, so inspect kernels as well as
script names before assuming that the archive is idle.

The final H1 2023 MRMS comparison and January 2023 CMIPF representative comparison
are running via `scripts/compare_2023.py`; logs are `/tmp/ecore-mrms-2023.log` and
`/tmp/ecore-goes-2023.log`, durable reports under `results/comparison-2023`.
Other notebook workloads were present; record this limitation with the timings.

Shared date-independent storage, widget hover help, radar map context and notebook
03 are implemented. All three notebooks executed locally; the first pass of the
expanded unit checks passed (33 tests). New HF shared-date resume and remote
radar viewing passed; owned remote test objects were deleted. Final checks follow
any fixes from the remaining comparisons.

The local GOES migration moved 370 verified stores before being paused upon finding
active older notebook writers. `results/h2-2022/goes-relocations.json` records those
moves. Raw arrays were not changed. Resume `scripts/merge_local_archive.py` only
after the user's fetching kernels are idle; old loaded code lacks shared-path
locks. Restart those kernels before using the revised storage implementation.

## Remaining

Finish the current comparisons, capture compact evidence and clean owned successful
test outputs. Complete archive migration once existing writers are idle. Launch
the two configured Zarr format 3 runs (MRMS H2 2022 and GOES September 2022 at
three scans/hour; see [dataset_fetch_run](dataset_fetch_run.md)) and record
actual measurements, then adopt the throughput experiment's measured defaults.
Keep ordinary durable research data. Colab still requires Google authorization
and the exact pushed revision; no Colab execution, commit, or push is claimed.
Production services, scheduler integration, training/inference and managed
mirrors remain explicit later work.
