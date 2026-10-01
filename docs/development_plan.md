# NOAA monthly archives with Earth2Studio

This sprint refactors the research fetchers to use Earth2Studio-compatible
sources, preserve native NOAA observations, and create compressed monthly Zarr
archives. The wrappers reuse the project’s CARIB MRMS and GOES readers because
the stock Earth2Studio sources do not provide the needed Caribbean region and
variable selection. Training and StormScope inference remain outside this work.
The study-period commands, checkpoints, and current evidence are in
[study jobs](study_jobs.md) and [status](status.md).

## Data selection

- MRMS study defaults to four products: precipitation rate, composite
  reflectivity, low-level azimuthal shear at ten-minute request slots, and
  multisensor Pass2 QPE at hourly cadence. Other supported products remain
  selectable. The latest file at or before each slot may be used within five
  minutes, at most once. Rolling one-hour QPE is still an accumulation.
  The CARIB S3 shear keys have underscores and a `00.50` suffix; a shear zero
  is ambiguous without bitmap/coverage context. See the
  [NOAA product table](https://www.nssl.noaa.gov/projects/mrms/operational/tables.php).
- GOES defaults to every discovered full-disk scan and eight StormScope example
  bands (C01, C02, C03, C07, C08, C09, C10, C13). CMIPF selects separate files per
  band; MCMIPF is available when a scan-wide multiband file is useful. GOES
  samples retain native grids, packed pixels, quality flags, calibration, and
  actual scan times.
- STAC selections persist the files and requested region/times before fetching.
  NOAA source objects are anonymous. No dates or measurements are synthesized
  to hide missing observations.

## Storage and resume

Each archive covers one source, product, satellite where relevant, native ROI
and grid/band, and UTC month. Repeated date requests merge new source objects
into the existing month. Local runs may build two different monthly stores at a
time; source-read and decode pools have separate limits. HF month mirroring may
publish two distinct monthly prefixes concurrently, with same-prefix writes
serialized and a one-writer fallback if throttling occurs. The local writer permits up to 16 source-read tasks per month,
bounded separately from the decode slots, and writes observations directly to
Earth2Studio Zarr. It does not
accumulate per-object staging stores. Temporary monthly builds are removed
after raw-data read-back validation.

Use Earth2Studio's standard `ZarrBackend` for local monthly archives.
The source arrays retain their native names and dimensions, and the backend
stores each variable as a named array with explicit coordinates. The project
`MonthlyZarrSource` reopens these arrays through Earth2Studio's
`DataSource(time, variable)` interface. Compact auxiliary source provenance is
stored alongside the Zarr arrays. The default HF writer uses Earth2Studio's
`AsyncZarrBackend` to write compressed Zarr objects directly through obstore.
It shards across a bounded number of time slices, verifies the remote arrays,
then updates the month completion marker. A local monthly ZIP keeps compressed
chunks together on P:. Migration of older stores is out of scope for this path.

Use the S3 gateway credentials for direct HF bucket access, separately from the
Hub token used by metadata APIs. A remote month is complete only after the
backend closes, each array and provenance entry is read back, and the completion
marker points to the verified version. If the gateway is unavailable or
throttles requests, stop; an explicit local destination remains available.
Never silently change the destination or writer. Do not report an HF time
forecast; record measured remote bytes and elapsed time after each run.

## Index and estimates

`results/archive_index.duckdb` is a local, rebuildable discovery and run log.
One coordinator owns writes. STAC files and each archive's completion manifest
remain the portable source of truth. Rebuild the index with
`ecore_weather.index.rebuild(["/local/archive/root"])` after removing the local
database or moving to a new workstation.

The selector reports listed source bytes, sampled raw-subset and compressed-Zarr
size ranges, and approximate local fetch times for week/month/year durations.
Only observed sample timings support those rough local estimates. HF transfer
rates are reported only after a completed publish.

## Notebook and script use

`notebooks/01_mrms.py` and `notebooks/02_goes.py` share request and pipeline
functions with their argparse entry points. Widget controls do not fetch until
the user presses a button. Both notebooks show fetched imagery and can save PNGs.
Colab setup detects the runtime, clones the selected revision, and installs the
notebooks extra; test changed package code there only after pushing that revision.

## Checks required before the long sample

1. Match source pixels, coordinates, calibration, quality flags, sentinel values,
   and missingness before and after the Earth2Studio `ZarrBackend` write. Reopen
   the result through `MonthlyZarrSource` and compare requested Earth2Studio
   values and timestamps.
2. Test ten-minute MRMS assignment, missing slots, Pass2 hourly matching, and the
   separation of accumulation interval from request frequency.
3. Run a small direct S3 upload/reopen/read-back check. Then verify bounded HF
   publication and resume on a representative month; use one publisher.
4. Compare one and two local monthly writers. Confirm no worker shares a store,
   memory stays within practical limits, and interrupted staging is cleaned.
5. Verify DuckDB search and rebuild from manifests. Prioritize the new monthly
   archive schema; legacy archive migration is not a release gate.
6. Run focused tests, synchronize the Jupytext pairs, and try both CLI entry
   points. A live full-period job follows these checks.

See the [developer guide](developer_guide.md), [storage guide](storage.md),
[notebook guide](notebooks.md), [current status](status.md), and
[Earth2Studio review](earth2studio_review.md) for operational details.
