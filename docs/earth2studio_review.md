# Earth2Studio source and IO review

Updated September 29, 2026.

## Why the project uses custom sources

Earth2Studio data sources provide a common call pattern for weather values at a
time and variable. Its stock NOAA sources do not expose the Puerto Rico CARIB
region and every MRMS field needed here with the raw metadata this archive must
retain. The project therefore wraps its existing MRMS CARIB and GOES ABI readers
in `MRMSCaribbeanSource` and `GOESCaribbeanSource` in
`src/ecore_weather/earth2_sources.py`.

The wrappers expose the Earth2Studio DataSource-style call contract for one
variable or band and also expose raw xarray datasets for archiving. The latter
path preserves source observation times, coordinates, packed GOES values,
calibration, DQF, MRMS decoded numbers, and GRIB bitmap missingness. This is a
fetch/storage adapter; it does not imply that a Puerto Rico dataset is directly
compatible with a released StormScope checkpoint.

## MRMS temporal meaning

Precipitation rate and composite reflectivity are discovered around ten-minute
request slots, using the most recent observation at or before each slot within
five minutes. Actual file timestamps and time offsets stay in STAC and archive
provenance. Pass2 one-hour QPE remains hourly. Radar-only rolling one-hour QPE
can be sampled every ten minutes, but its temporal meaning remains an accumulated
one-hour total. Accumulation windows must not be treated as instantaneous rates.

The NOAA [MRMS tables](https://www.nssl.noaa.gov/projects/mrms/operational/tables.php)
remain the authority for product units, issue cadence, and product-specific
missing codes. Do not mark all negative radar values as missing.

## GOES selection

GOES defaults to per-band CMIPF files and the eight StormScope example channels
C01, C02, C03, C07, C08, C09, C10, and C13. The widget names the bands in plain
language with wavelength. A native-grid archive is kept for each band because
ABI channels have different spatial resolutions. MCMIPF remains available where
one scan-wide file is useful, but source encoding and coordinate compatibility
must be checked before combining files.

## Storage choice and open validation

NVIDIA's local-data-source example uses Earth2Studio's `ZarrBackend` to create
named arrays over fixed coordinates, writes one time slice at a time, and then
reopens the result through an Earth2Studio source. The project follows that
pattern with native NOAA variable names and grids. `MonthlyZarrSource` exposes a
saved MRMS product or GOES band through the standard `(time, variable)` source
call. The packed NOAA source values remain raw; this source API does not claim
model readiness or checkpoint compatibility.

The default local writer is the standard `ZarrBackend`, using lossless Zstd/Blosc
compression and small time chunks. Non-dimension coordinates such as source URL,
ETag, requested slot, and source metadata are kept in a compact
`ecore_metadata.json` sidecar in the same monthly archive. Numeric grids, values,
and variable attributes remain named Zarr arrays. A complete content and
metadata read-back is required before publishing. A failed backend check stops
the run; it never switches to a different writer. The remote path uses
`AsyncZarrBackend` with one writer, bounded time sharding, and a versioned
completion marker. Its buffered writes add memory and restart considerations,
so close and read-back are mandatory.
Source staging files are removed only after the final local archive or HF upload
passes validation.

The updated `ecore-weather` environment passed local Earth2Studio
write–close–reopen checks, live MRMS and GOES source-to-monthly-archive checks,
and a synthetic monthly HF S3 publish/read-back check. These establish
correctness for small samples, not full-month throughput; see [status](status.md).

## Direct remote async Zarr on the HF S3 gateway

Earth2Studio 0.18.0's `AsyncZarrBackend` accepts an `s3://` URL with obstore
settings, or an obstore `S3Store` instance, and writes Zarr v3 objects through
obstore. The official [remote IO example](https://nvidia.github.io/earth2studio/main/examples/07_misc/03_io_performance/#remote-non-blocking-async-zarr-io)
uses non-blocking writes with compression and calls `close()` to wait for them.
The [backend API](https://nvidia.github.io/earth2studio/main/modules/generated/io/AsyncZarrBackend/)
also allows arrays to be declared with native dtypes before writing. On HF, use
the namespace endpoint, bare bucket, path-style requests, region `us-east-1`,
and the separate gateway credentials. The `hf_store()` helper builds the
equivalent configured obstore instance without changing NOAA's anonymous client.

`scripts/probe_hf_async_zarr.py` made a unique remote Zarr store, wrote two
time slices of `int16` measurements and `uint8` quality flags, closed the
backend, and reopened it independently through an obstore-backed Zarr reader.
Values, timestamps, and dtypes matched exactly. The successful probe took
38.85 seconds to create/read 13 objects totaling 4,057 bytes, then removed
those test objects. This proves basic gateway compatibility, not useful monthly
throughput. The tiny transaction's latency and object count make unsharded
per-time-chunk writes costly for the current high-frequency archive.
With tens of thousands of GOES band observations per month, even one chunk per
observation would create many requests; earlier parallel HF publication was
also rate-limited. That request-count concern is an inference from the layout,
not a measured month-long comparison.

The requested default remote writer now uses this backend with one full-ROI
spatial chunk and up to 72 time slices per shard. A unique versioned store is
written, closed, and checked before a month marker points to it. A synthetic
two-observation archive and a live two-observation MRMS fetch both passed
write–reopen–merge–reuse checks. These are functional checks, not a
representative-month performance result. The live test initially exposed a
shard/chunk divisibility error with 512-pixel chunks on a 768-pixel ROI; keeping
the requested ROI as one spatial chunk resolved it. The resulting full-ROI
chunks suit whole-image reads but may be less efficient for tiny patch queries.
Before mirroring a full study year, measure a complete product-month for
request count, 429 responses, total time, read-back cost, shard memory, and
interruption recovery. `close()` alone is not an atomic monthly commit; the
versioned marker provides the completion boundary.

The first full-month HF pilot exposed a performance cost that the two-sample
checks could not show: Earth2Studio validates coordinates against the remote
store on every `write()` call. Writing every observation separately made about
72 radar observations in six minutes. The remote writer now batches up to 72 time
slices per call and groups arrays with the same dimensions, while retaining
per-observation source hashes and a separate read-back check. The first
12-slice-batched run reached 223 observations in 191 seconds and 446 before
interruption; that is a partial rate, not a completed-month measurement. The
final 72-slice grouped-call variant completed January 2021 CARIB precipitation
rate: 4,464 observations, 48,608,757 remote bytes in 134 objects, 400.8 seconds
through the upload/metadata stage and 467.1 seconds end to end including full
remote value, coordinate, and source-metadata read-back. Peak observed resident
memory was about 2.5 GB. No 429 occurred in this one-month test. A repeat call
reused the verified archive in 2.1 seconds. Both interrupted, unmarked pilot
versions were removed. This validates one radar product-month on the current
gateway; it does not establish throughput for all products or GOES bands.

The stock source APIs, custom adapter interfaces, backend contracts, and version
compatibility should be rechecked against the official
[DataSource guide](https://nvidia.github.io/earth2studio/main/userguide/components/datasources/),
[custom source example](https://nvidia.github.io/earth2studio/main/examples/08_extend/03_custom_datasource/),
[ZarrBackend API](https://nvidia.github.io/earth2studio/main/modules/generated/io/ZarrBackend/),
[local data source example](https://nvidia.github.io/earth2studio/main/examples/07_misc/04_local_datasource/),
and [IO guide](https://nvidia.github.io/earth2studio/main/userguide/components/io/)
when revising source or storage contracts.
