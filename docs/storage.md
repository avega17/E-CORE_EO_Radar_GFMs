# Storage layout and Hugging Face access

## Why the HF writer changed

Previously each of up to 16 download threads ran two directory syncs, opened a
remote Zarr store for per-file read-back, and checked remote completion metadata.
The overlap multiplied listing, metadata and transfer requests. This is a credible
cause of the reported HTTP 429 responses; we have not captured the original
response headers and cannot identify its exact exhausted quota retrospectively.

HF recommends its direct API where possible because filesystem compatibility adds
overhead. The new publisher uses `batch_bucket_files` and `download_bucket_files`.
It uploads staged files together, downloads them together for local verification,
and publishes the completion marker last. Batches are not transactional; a partial
upload never means completion. Resume also uses batched downloads rather than
opening every Zarr chunk through the remote filesystem.

A thread lock and per-bucket POSIX file lock allow one publication operation at a
time on this workstation, independently of NOAA worker count. On a cluster use one
publishing coordinator; the local lock is not a distributed lock. SDK retries honor
rate-limit handling. Keep credentials available to the SDK and inspect HF's rate
limit headers/dashboard if throttling persists. This reduces request amplification;
it does not guarantee that any account quota can sustain an arbitrary workload.

A two-file directory test and a 16-file ZIP test with 16 configured download
workers passed upload, complete read-back and resume. The ZIP test retained
884,996 bytes across 16 subsets, took 56.20 seconds including validation, and
used 64 application-level batch calls (not a count of underlying HTTP requests).
Its 32 test objects were removed. See [directory evidence](evidence/hf-batch-check.json)
and [ZIP evidence](evidence/hf-batch-zip-check.json). These small checks are not
sustained-load claims. We keep HF upload/read-back timing separate from NOAA reads.

## Browsable paths

The configured public bucket is `asvnpr/ecore-eo-weather-GFMs`. A normal HF root is
`hf://buckets/asvnpr/ecore-eo-weather-GFMs/noaa-subsets`. Local roots use the same
relative layout:

```text
<root>/
  mrms/MultiSensor_QPE_01H_Pass2_00.00/
    roi-<pixel-selection-id>/
      2022/09/24/165800-<source-id>/
        raw.zarr/  OR raw.zarr.zip
        complete.json
  goes/ABI-L2-MCMIPF/goes16/
    roi-<pixel-selection-id>/
      2022/09/24/000020-<source-id>/...
```

Names expose source, product, satellite and actual observation date/time. The
`roi-` ID describes the requested geographic bounds and bands, independently of
start/end dates. Each root has a readable `subset.json` listing that region, product, satellite and band scope. Expanding a date range reuses the same source subsets after
read-back verification. Source IDs include the object identity; changed source
objects do not silently replace an earlier version. MRMS slot matching belongs to
the selection/run record, while raw metadata retains the actual observation time.

For single-band CMIP files the NOAA object already identifies its band and grid,
so adding bands to a request also reuses the previously selected files. MCMIP
subsets with different band sets and requests with different geographic bounds
remain distinct subsets. This change does not combine different native grids or
silently expand the scientific content of an existing store.

`collection.json` and `items.json` describe each request. Run reports map its source
identities to shared stored paths; keep these small records with the experiment.
The original hash-only layout remains available with `--layout legacy`.

Existing local date/hash folders are indexed once per fetch. Matching completed
subsets are verified and moved into shared paths without downloading NOAA again.
To merge an entire existing selection, including verified duplicates, run:

```bash
python scripts/merge_local_archive.py results/h2-2022/goes/collection.json \
  --destination /mnt/p/ecore_eo_datasets --output results/h2-2022/goes-relocations.json
```

The command downloads nothing, checks arrays and metadata before moving, and
removes only verified duplicate raw stores and their completion markers. It keeps
unrelated files. Earlier run URLs are historical after migration; the relocation
report maps old folders to new ones. Browse the shared root with notebook 03.
A rerun resumes a partially completed migration. Let older notebook fetches finish or stop them before migration, then restart their kernels to load the shared-path writer. The command checkpoints its relocation report and records an incomplete state when interrupted. Different data at a candidate
path is reported and retained for review. Old HF date/hash prefixes are not
moved by this local command; new HF runs use the same shared layout and resume
checks as local runs. Existing remote prefixes remain readable explicitly.

Per-object workstation locks cover resume, decoding and publication so overlapping
runs cannot overwrite each other's partial stores. HF publication also uses its
separate bucket coordinator. These locks are not a cluster-wide job scheduler.

Zarr retains native arrays, coordinates, quality flags, calibration and missing
values. `raw.zarr.zip` is the same Zarr store packaged as one ZIP file with its
existing compressed chunks, not another raster or a legacy source copy. Directory
Zarr supports ordinary filesystem browsing; ZIP Zarr reduces small-file overhead
on Windows DAS and object stores. Both are validated against the source content.
The ZIP option is `--container zip`; directory storage remains the notebook default.
Only one representation is retained per completed source subset. A repeat request
reuses the existing container rather than creating a second one.

## Reading data

```python
from ecore_weather.storage import open_raw

with open_raw('/mnt/p/ecore_eo_datasets/.../raw.zarr.zip') as raw:
    print(raw)                         # native packed/decoded-source values
    image = raw['CMI_C13'].load()       # GOES example; MRMS uses 'measurement'

with open_raw('hf://buckets/asvnpr/ecore-eo-weather-GFMs/noaa-subsets/.../raw.zarr') as raw:
    image = raw['measurement'].load()
```

Replace `...` with a recorded run location. `open_raw` supports directory and ZIP
stores locally and on HF. Remote directory access uses HfFileSystem for xarray
compatibility; a remote ZIP subset is downloaded through the batch API into owned
temporary storage and removed when the dataset closes. A ZIP contains one cropped
observation, not the full NOAA source. For repeated training, stage the selected
subsets locally rather than invoking remote reads from every training worker.

```bash
hf buckets list asvnpr/ecore-eo-weather-GFMs --recursive
hf sync hf://buckets/asvnpr/ecore-eo-weather-GFMs/noaa-subsets/<chosen-prefix> ./local-data
```

HF tokens and S3 gateway keys are different credentials. No S3 gateway is needed
for this writer. Bucket files are mutable and do not support git revisions; retain
source identities and validated selection metadata for reproducibility. Do not use
`--delete` when copying research subsets without reviewing the intended removals.

## Local cache and Windows DAS

The H2 2022 run uses fast owned scratch, then writes validated raw subsets to the
DAS at `/mnt/p/ecore_eo_datasets`. Verify `findmnt -T` shows Windows `P:`; an
unmounted `/mnt/p` directory can otherwise write to the Linux root disk. DrvFS may
reject POSIX permission/time copying, so publication copies data bytes and verifies
scientific metadata instead of imposing Linux filesystem attributes. Zarr ZIP is
preferred for this experiment to avoid millions of tiny NTFS files.

## References reviewed

All five supplied pages were accessed on September 10, 2026. The current Python
guide links to 1.31.0.rc0; the installed, tested SDK remains 1.30.0, whose batch API
and retry implementation were also inspected.

- [Buckets Python guide](https://huggingface.co/docs/huggingface_hub/guides/buckets)
- [Storage buckets](https://huggingface.co/docs/hub/storage-buckets)
- [Access patterns](https://huggingface.co/docs/hub/storage-buckets-access)
- [Integrations](https://huggingface.co/docs/hub/storage-buckets-integrations)
- [Filesystem API](https://huggingface.co/docs/huggingface_hub/guides/hf_file_system)
- [Rate limits](https://huggingface.co/docs/hub/rate-limits)
