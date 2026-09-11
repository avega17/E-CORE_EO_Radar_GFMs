# Six-month local sample: H2 2022

The requested period is July 1, 2022 through January 1, 2023, UTC, ending date
excluded: 184 days and 4,416 nominal radar hours. Use the mentor's Puerto Rico
region, west −70.24°, south 14.36°, east −62.56°, north 22.04°.

The radar sample keeps every available hourly CARIB Pass2 QPE subset. Discovery
found 4,412 files; the four unavailable nominal hours are August 8 18:00, August 11
13:00, September 14 05:00 and December 17 05:00. The September 24/25 off-hour files
match the following nominal hours with their actual timestamps preserved.

The GOES sample keeps every available GOES-16 full-disk MCMIPF scan in the period,
selecting eight bands: C08, C09, C10, C11, C13, C14, C15 and C16. This is a research
selection of infrared water-vapor, cloud-phase, window and CO₂ channels, usable by
day and night. It is not a declaration of a particular checkpoint's required input.
The exact trained model may require additional/different channels, radar variables,
normalization and grid preparation. See [NOAA's ABI table](https://www.goes-r.gov/spacesegment/ABI-tech-summary.html).

## How the run is organized

`scripts/fetch_long_sample.py` saves the full selection before fetching. Local
scratch holds only a bounded number of active subsets. The GOES run uses eight
independent file-reader processes and sixteen task workers. Separate processes
avoid h5py's shared thread lock; they are not HF upload processes. Limit numerical
library threads when launching a multi-process read.

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python scripts/fetch_long_sample.py \
  --source goes --destination /mnt/p/ecore_eo_datasets \
  --output results/h2-2022 --read-processes 8
```

The current MRMS run began in `data/h2-2022-cache` while Windows P: was being
mounted. Its validated data will be moved to the DAS after completion; this is
explicit local staging, not a silent replacement for the configured HF destination.
GOES writes to the mounted DAS directly after local staging and validation.

Zarr ZIP containers reduce directory and small-file overhead on NTFS. Existing
compressed Zarr chunks are packed without a second compression pass. Raw values,
metadata and missing codes are checked before removing the temporary directory
store. The DAS copy is checked again before publishing its completion marker.

## Completed radar fetch

All 4,412 available files were fetched locally without failures in 669.79 seconds
(fetch, decoding, storage and verification), reading 150,394,867 source bytes.
The effective end-to-end rate was 0.22454 MB/s. Native ROI arrays occupy
23,474,946,048 logical bytes; directory Zarr retained 218,900,163 bytes before ZIP
packaging and DAS archival. The lossless subset is larger than the original gzip
transfer total: GRIB packs the mostly sparse radar field efficiently, while our
store preserves decoded numeric values, bitmap and metadata. Cropping does not
promise that a different lossless container is smaller than the original encoding.

The measured run began before selection-ID calculation was moved out of the
per-file loop. This result documents that run, not a benchmark of later edits.
DAS packaging/copy time is recorded separately when archival finishes.

## Progress and measurements

Current machine-readable state is in `results/h2-2022/{source}-progress.json`.
A completed source writes `{source}-summary.json`; detailed per-file reports and
the two-file STAC selection remain in its source subdirectory. Repeating the same
command verifies completed subsets and retries unfinished work. Do not run a second
writer for the same local output while the first one is active.

Report the following separately:

- Discovery and total elapsed time.
- NOAA bytes actually read, source request count and effective MB/s over fetch wall
  time. This effective rate includes decoding, writing and verification; it is not
  the workstation's link speed.
- Total size of listed full source objects, which is not the amount downloaded
  for GOES range reads.
- Logical selected-array bytes, compressed retained bytes, and archive-copy time.
- Successful, reused and failed files, unavailable times and peak sampled memory.

An initial two-scan eight-band GOES check read 55,086,743 bytes from source files
whose combined size is 622,632,591 bytes. It retained about 2.68 MB as Zarr ZIP.
This small sample is not an extrapolated six-month throughput result. The full run
must finish before reporting its runtime or sustained speed.

Windows P: was initially available to Windows but not mounted inside this WSL
instance. After `sudo mount -t drvfs P: /mnt/p`, `findmnt` reported P: over 9p/DrvFS
and approximately 7.3 TiB free. Always check the mount before a long write.
