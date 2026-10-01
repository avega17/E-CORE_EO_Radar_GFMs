# Monthly NOAA archives

## Archive paths

Local fetches store one compressed Zarr v3 ZIP for each source, product, satellite
if applicable, native region/grid/band selection, and UTC month. Request dates,
worker counts, and MRMS slot assignments do not change the archive identity.
Repeated requests merge only source objects missing from a previously
completed monthly archive. An interrupted build that has not been published
must be rebuilt; its temporary Zarr is not resumed. The month's `complete.json`
records each source URL, object identity, ETag, actual observation time,
request slot/offset where relevant, and the verified observation count. The
writer checks every numeric observation chunk against its source before
publishing the completion marker.

```text
<root>/
  mrms/<product>/roi-<grid-and-region-id>/2023/01/raw.zarr.zip
  goes/ABI-L2-CMIPF/goes19/C13/roi-<native-grid-and-region-id>/2025/09/raw.zarr.zip
  .../complete.json
```

The path is independent of requested start/end dates, so overlapping fetches
reuse the month. A request near a UTC month boundary can list two actual-data
month archives: the selected prior observation stays in its true month. GOES bands keep separate archives because they have different
native resolutions. In a multiband product each output still preserves its
band-specific native grid. Existing per-observation folders remain readable in
the viewer; migrating them is optional and does not block new fetches.

Each month contains one canonical completed raw archive. The local writer reads
source objects through a bounded prefetch queue and writes one observation at
a time to a temporary product-month Zarr on fast Linux scratch. It does not
hold the entire month's data in RAM or retain duplicate GRIB/NetCDF source
files. After the month is written, the writer closes it, packages a ZIP,
reopens it and checks the values, then copies it to the DAS and writes
`complete.json`. This avoids publishing a partial archive and avoids writing
every Zarr chunk directly to DrvFS. Scratch is removed after success or a
handled error. A forced kill or machine failure can leave temporary directories
behind; the next run does not resume them, so it can repeat the current
product-month's NOAA reads and work. Previously verified product-months remain
on the DAS and are reused. MRMS selections are saved before fetching, so a
restart can reuse the source list. The fetcher publishes and checkpoints each
single-product monthly archive independently; it does not wait for other MRMS
products. At most the configured number of product-month writers is in flight.
Remove orphan `ecore-month-*` scratch only after confirming no fetch workers
still use it. The saved values are lossless:
MRMS keeps its
decoded source numbers and bitmap mask, and GOES keeps packed integers, scale and
offset, native coordinates, DQF, fill codes, and associated metadata. Small
per-observation calibration, projection, and time variables are browsable in
the `source_metadata_json` sidecar coordinate. No cleaned,
interpolated, reprojected, or normalized values enter these archives.

## Local and HF destinations

The default `hf` destination writes directly to the configured HF bucket through
the S3 gateway. `HF_BUCKET_NAME=namespace/bucket` is preferred;
`HF_DATASET_REPO` remains a compatibility alias. `HF_S3_ENDPOINT` should identify
the namespace endpoint, normally `https://s3.hf.co/<namespace>`. S3 access and
secret keys are separate from the Hub `HF_TOKEN`/`HF_API_KEY`. NOAA reads use a
different unsigned S3 client.

For the S3 path, one coordinator uses Earth2Studio's `AsyncZarrBackend` with
obstore to write a compressed Zarr v3 store directly to HF. The store gets a
unique `raw-<id>.zarr` name under the stable product/region/month path. The
writer closes pending writes, checks every remote array and source metadata
entry, and then updates `complete.json` to point to that verified version. On a
month merge, the old completed version remains available until the new marker
is written; only then is the older writer-owned version removed. Interrupted
unmarked versions do not count as completed research data. A 429 or other
gateway failure stops the operation clearly. Remote bytes and write time are
measured after a run; no precise HF forecast is shown.

The direct backend passed a tiny isolated compatibility check, a live
two-observation NOAA MRMS merge/reuse check, and a complete January 2021
precipitation-rate month: 4,464 observations and 48.6 MB over 134 HF objects.
The full month passed remote source-value and metadata read-back in 467 s,
then reused without rewriting. This is one product-month measurement, not a
general HF throughput guarantee. The writer batches up to 72 time slices into
each shard. The 2021 mirror now supports two independent monthly archive
writers. Each writer is locked to its own product/region/month prefix; older
bucket-wide publishers are excluded while these scoped writers are active.
Start with two and reduce to one with `--monthly-writers 1` if the gateway
throttles. This parallel mode is being validated against the live mirror before
it is used for larger HF transfers. Run
`PYTHONPATH=src python scripts/probe_hf_monthly_async.py` or
`scripts/probe_hf_fetch_async.py` for isolated write/reopen/cleanup tests.
See the [Earth2Studio review](earth2studio_review.md) for the performance limit.

Use an explicit local directory to avoid remote writes:

```bash
python notebooks/01_mrms.py --operation fetch --start 2023-01-01 --end 2023-02-01 \
  --destination /mnt/p/ecore_eo_datasets --workers 8 --monthly-writers 2
python notebooks/02_goes.py --operation fetch --start 2023-01-01 --end 2023-02-01 \
  --destination /mnt/p/ecore_eo_datasets --workers 8 --monthly-writers 2
```

The local writers can process separate product/month archives concurrently.
They never write to the same month. HF mirror writers follow the same rule and
default to two independent month prefixes; they never share a store or shard.
Source reads and HDF5/GRIB decoding use independently bounded
worker pools. `--workers` permits up to 16 concurrent NOAA source tasks per
monthly archive; the MRMS decode pool remains two slots per writer by default.
With two local writers, that can mean up to 32 in-flight source tasks while at
most four GRIB files decode. More queued work can use more memory, so measure
memory and wall time on a representative month. For fast monthly builds, pass `--scratch` on a
local Linux disk with enough free space. Scratch is cleaned after each verified
month or failed build.

## S3 access and safe completion

The S3 client uses the namespace endpoint, bare bucket name, `us-east-1`,
path-style addressing, checksums only when required, bounded connection pools,
and retries for transient failures. The HF token is never passed as an AWS key.
Do not log credentials or put them in a manifest, URL, notebook, or report.

Before a larger upload, verify the configured S3 gateway with a tiny temporary
object and test upload, reopen, read-back, and deletion. Then test a small Zarr
month. If this compatibility check or a repeated upload fails, use an explicit
local destination and one coordinated publisher later; do not silently route
data somewhere else. A partially uploaded month has no completion marker and is
not searchable as complete.

## Reading archives

```python
from ecore_weather.storage import open_raw

with open_raw("/mnt/p/ecore_eo_datasets/mrms/.../2023/01/raw.zarr.zip") as month:
    print(month)
    sample = month.sel(time="2023-01-01T12:00:00")
    print(sample.measurement.values)
```

For GOES select the band variable (for example `CMI_C13`) and its corresponding
`DQF_C13`. Xarray opens only the requested chunks when chunking is enabled; use
time and spatial slices before loading larger arrays. A saved month can also be
opened as an Earth2Studio source:

```python
from ecore_weather.earth2_sources import MonthlyZarrSource

source = MonthlyZarrSource(
    "/mnt/p/ecore_eo_datasets/mrms/PrecipRate_00.00/roi-.../2023/01/raw.zarr.zip",
    source="mrms",
    product="PrecipRate_00.00",
)
values = source(["2023-01-01T12:00:00"], ["precip_rate"])
```

GOES uses `source="goes"`, `band=13`, and Earth2Studio variable `abi13c`.
This adapter returns the native data and grid through the Earth2Studio
`DataSource(time, variable)` interface. It does not decode calibration, mask
quality flags, interpolate, or make a regional archive compatible with a model
checkpoint. Read locally for repeated training access instead of having each
worker issue remote reads.

The same source/product/region/month grouping is used below
`hf://buckets/<namespace>/<bucket>/noaa-subsets/`, but the remote payload is the
versioned `.zarr` directory named by `complete.json` rather than a ZIP.
For listing, synchronization, or authentication, use the `hf` CLI and Hub token.
For direct S3 reads or writes, use the gateway credentials and exact namespace
endpoint. Access instructions may change with Hub releases; consult the current
[HF bucket S3 guide](https://huggingface.co/docs/hub/storage-buckets-s3) before
changing the client settings.

## Year-sized HF backup packages

HF is a backup destination, while monthly local Zarr archives remain the
canonical stores used for browsing and model-data access. [Hugging Face describes
buckets as mutable, non-versioned object storage suited to backups](https://huggingface.co/docs/hub/storage-buckets).
The remote backup now groups verified monthly ZIPs without merging their
scientific arrays: one file per MRMS product/year under
`noaa-subsets/yearly-v1/mrms/<product>/<year>/`, with a `complete.json` marker.
Each ZIP contains the original monthly ZIP members, their completion markers,
the selected STAC items, and `year_manifest.json` with checksums and coverage.
GOES yearly packages are a later task; they must keep the separate band/month
archives and satellite/calibration epochs distinct.

Notebook 03 reads the Earth2Studio monthly archive layout directly from its
completion markers, or uses `results/archive_index.duckdb` when it has matching
local records. The index is only a speed-up: it can be rebuilt from archive
markers. Search results are grouped by product, region, and satellite across
months; GOES bands remain selectable within a result. Restart the notebook
kernel after changing imported viewer code so its callbacks use the new source.

The notebook treats a yearly HF ZIP as a backup container, not an analysis-ready
Zarr store. Choose **Hugging Face**, open **HF yearly backup**, list packages,
inspect a product and year, then click **Prepare month**. Its range reader
transfers the selected monthly ZIP member to `results/view-cache` by default,
checks its SHA-256 against the year manifest and monthly completion marker, and
opens that local Zarr ZIP. Change the cache path if the month needs more space.
The canonical DAS month and annual HF backup are not changed. Directly searching
an annual ZIP as though it were Zarr is unsupported. Direct remote monthly Zarr
stores, if present, can still be searched, but the yearly packages remain the
backup path.

The nested **Storage explorer** uses product, year, and month selectors to move
from totals to individual monthly archives. For local storage it reads shallow
completion markers; for HF it lists verified annual markers, then reads only
the selected ZIP's manifest and monthly coverage metadata with range requests.
It reports stored monthly Zarr bytes, listed NOAA source-object bytes, and the
stored/source percentage where source sizes are known. This percentage compares
the compressed regional archive with the complete NOAA objects used to build it;
it is not a compression ratio for the raw cropped pixels. NOAA source files may
already be compressed, and full-object sizes are not the size of the ROI crop.
When some source sizes are absent, the panel reports the coverage and calculates
the ratio using only rows with known source sizes. The annual package's total
size is also shown separately because it includes the monthly members and small
manifest files.

## Chunk layout and viewer reads

The monthly writer currently chunks arrays as `(time=1, y<=256, x<=256)`
(or the equivalent native dimensions for GOES). This matches the common map
operation: select one observation, then read its spatial field. It also keeps
each observation independently addressable and limits a small spatial query to
the chunks it intersects. A full Puerto Rico frame spans several spatial
chunks, so increasing spatial chunk size could reduce file opens for map views
but would make small-area reads fetch more pixels. Point time series would favor
larger time chunks, which conflicts with maps and independent observation
resumes. We retain the current compromise until representative training and
viewer benchmarks show a better shared choice.

The dataset explorer opens each monthly store once for the selected sequence,
then reads all requested timestamps from that open store. Daily and multi-day
views often revisit the same month, so this avoids reopening ZIP metadata for
every frame. The sequence still reads one timestamp at a time and converts only
the selected ROI to display images; it does not load a full monthly archive into
memory. A missing date or scan is omitted and remains visible in the time list.

When evaluating a different writer layout, benchmark at least these actual
operations: one timestamp/full ROI for maps, a multi-day sequence of timestamps,
a timestamp over a small spatial patch, and a point time series. Compare cold
and warm local-cache reads, chunk count/bytes touched, file count, write time,
and peak memory. In Zarr a request loads every intersecting chunk, so chunk
shapes should follow the operations the project performs rather than a generic
"optimal" shape. Zarr v3 sharding may reduce small-file counts, but the writer
must hold a shard's contents while assembling it; test memory and random-read
cost before enabling it.

The storage explorer compares two different measurements: the stored monthly
ROI archive and the listed sizes of the complete NOAA source objects. Each
source file contributes its listed object size once within its archive. The
displayed ratio is `sum(stored archive bytes) / sum(complete listed source
object bytes)` for rows where every source size is known. It is a weighted
comparison of totals, not the mean of monthly percentages and not a raw-data
compression ratio. NOAA's source objects may already be compressed, while the
archive includes decoded ROI arrays, coordinates, quality/bitmap arrays, and
per-observation provenance. A value above 100% therefore does not by itself
indicate a faulty Zarr compressor. The source objects are full files, so their
bytes also cannot be treated as the size of an equivalent cropped source file.
If any source size is absent, that archive is excluded from the complete
comparison and the interface reports file/row coverage instead of presenting a
partial sum as complete.

These choices follow Zarr's rule that reads touch complete intersecting chunks
and that chunk layouts should match expected operations. See the [xarray Zarr
tutorial](https://tutorial.xarray.dev/intermediate/intro-to-zarr.html), the
[access-pattern analysis guide](https://github.com/uw-ssec/rse-plugins/blob/main/plugins/zarr-chunk-optimization/skills/access-pattern-analysis/SKILL.md),
[Zarr v3 performance guide](https://zarr.readthedocs.io/en/v3.1.4/user-guide/performance/),
[STAC Zarr best practices](https://github.com/radiantearth/stac-best-practices/blob/main/best-practices-zarr.md),
and [Earthmover's Zarr overview](https://www.earthmover.io/blog/what-is-zarr/).
STAC metadata should describe each stable monthly store and its multidimensional
coverage; it does not determine the store's chunk layout.

The four 2021 bundles occupy 3,611,749,226 bytes (3.36 GiB). They were
published with four independent product-year writers. All four completed
upload and full streaming SHA-256 read-back without HTTP 429 responses. The
saved reports keep upload and read-back times separate; the read-back took
longer than upload for the three larger bundles. The source
monthly ZIPs are already compressed, so the outer package uses ZIP_STORED for
those members: it reduces object count and simplifies backup transfer without
spending CPU trying to compress Zarr chunks again. Small metadata and the year
manifest are compressed.

The resumed mirror run measured the following for the same four objects:

| MRMS product | Package size | Upload | Full read-back |
| --- | ---: | ---: | ---: |
| Pass2 hourly QPE | 258 MB | 8.8 s | 14.1 s |
| Composite reflectivity | 1.16 GB | 25.1 s | 50.4 s |
| Low-level azimuthal shear | 1.03 GB | 21.8 s | 36.9 s |
| Precipitation rate | 1.15 GB | 29.0 s | 59.1 s |

This is a measured yearly-package transfer, not a controlled speed comparison
against a complete month-by-month mirror of the same year.

The yearly publisher verifies each local ZIP against its completion marker,
uploads to a separate yearly prefix, streams the complete remote object back to
compute SHA-256, and writes the yearly completion marker only after that check.
Only then does it remove matching month-level prefixes. The initial migration
removed seven complete or interrupted MRMS month prefixes after confirming the
yearly packages; the local monthly DAS archives were not changed. If cleanup
finds an unfamiliar key layout or a marker mismatch, it preserves that remote
prefix and reports it. HF has no object versioning, so do not delete a yearly
bundle until another verified backup is available.

To rebuild a 2021 MRMS backup from the local canonical archives, run:

```bash
python scripts/mirror_mrms_year_bundle.py --year 2021 --dry-run
python scripts/mirror_mrms_year_bundle.py --year 2021 --writers 4
```

Use `mirror_mrms_year_bundle.py` for study backups. The older
`mirror_mrms_year.py` publishes individual remote month stores and is not the
backup path used by `run_mrms_staged.py`.

Use `--cleanup-only` only after the saved summary records all four verified
bundles. It rechecks the remote markers and object sizes before removing exact
replaced month prefixes. Never run multiple publishers for the same product/year.

## Local DuckDB index

`results/archive_index.duckdb` speeds local month/observation searches and stores
selection and fetch-run measurements. It is an index, not a data copy. Keep it on
a Linux local disk rather than `/mnt/`, a bucket, or a shared cluster filesystem.
One process owns writes; workers return events to that coordinator. Rebuild the
index from local archive manifests if it is removed:

```python
from ecore_weather.index import rebuild
rebuild(["/mnt/p/ecore_eo_datasets"])
```

STAC selections and monthly completion manifests remain portable records of the
request and source objects.

## Earlier stores

Legacy single-observation stores are outside the monthly archive acceptance
checks. Existing files are left in place; this migration does not convert or
delete them. Prioritize opening new monthly archives through the Earth2Studio
source adapter.
