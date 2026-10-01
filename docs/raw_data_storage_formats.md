# Why we rebuilt the data pipeline: raw data in Zarr, fetched once

Updated September 10, 2026.

This document explains, in plain language, why this project moved away from the
mentor's original download scripts to a raw-lossless pipeline built around Zarr,
and why that choice also serves us well on shared drives and HPC systems. It is
the narrative companion to the measured evidence in
[validation](validation.md), [storage](storage.md), and
[dataset_fetch_run](dataset_fetch_run.md), and to the library comparison in
[the Earth2-Studio review](earth2studio_review.md).

## Why we changed the fetching approach

The mentor's original scripts were written to prepare training images quickly:
for each hour, download a radar file, unpack it, interpolate to a fixed grid,
clean negative values, and save a picture-like GeoTIFF. Reviewing them closely
(see [mentor code review](mentor_code_review.md)) showed why that design could
not carry a reproducible research archive:

- **Sequential downloads.** One hour at a time, waiting on the network between
  files. Overlapping downloads measured roughly a sixfold speedup at four
  workers (medians of three repeated runs; see [validation](validation.md)).
- **Clock-hour timestamps.** Files were requested at exactly 17:00, so a real
  16:58 observation was missed. We now match each slot to the latest file at or
  before the hour within five minutes and keep the true timestamp.
- **Cleaning before masking.** Interpolation ran before missing values were
  masked, so NOAA's missing-data codes could bleed into neighboring pixels and
  the raw distinctions disappeared. Missing radar coverage is not zero rain.
- **GeoTIFF as the only durable output.** Units, calibration, quality flags, and
  missing-value codes were lost; the tested files even lacked CRS and units
  tags. Every later analysis would have to re-download and re-decode the
  originals to check anything.

The mentor scripts remain in the repository unchanged as reference behavior.
The refactor keeps their calculations measurable while making the raw data
trustworthy and reusable.

## Why raw data lives in Zarr

Weather fields are arrays: values on a grid with coordinates like latitude,
longitude, and time. Array storage keeps that structure. As Earthmover's
[Tensors vs. Tables](https://www.earthmover.io/blog/tensors-vs-tables/) explains
with benchmarks, indexing a coordinate-aware array answers "give me this region
at this time" directly, while flattening the same data into rows forces scanning
and duplicating coordinates. In their example, a point timeseries took under
200 ms from Zarr against 2.5 s from a state-of-the-art table engine.

Zarr is the array store we use because it is simple (arrays plus metadata as
ordinary files), open, and readable by the standard scientific Python tools we
already depend on. Each source object becomes one lossless Zarr subset: native
values, coordinates, units, timestamps, and quality flags, verified by read-back
before it counts as saved. Nothing is interpolated, cleaned, or reprojected at
ingestion; processing happens in memory afterwards.

Two honest qualifications, both measured and written down:

- **Lossless is not always smaller.** For the H2 2022 radar sample, the decoded
  Zarr subsets were larger on disk than the original gzip transfer, because GRIB
  packs the mostly rain-free field very efficiently. We keep the Zarr for access
  and preservation, not for size (see [long_sample](long_sample.md)).
- **More files is its own cost.** A Zarr directory contains many small chunk
  files. The ZIP container option packs the identical store into one file per
  source object, which is measurably friendlier to Windows network drives and
  parallel filesystems (see [storage](storage.md)).

## Reading without copying everything

For satellite imagery we go further: the ordinary reader requests only the
selected bands and the region around Puerto Rico from NOAA's cloud objects, so
we never download whole full-disk scans. The optional reference bundle applies
the same idea at archive scale: a small local file records *where* each piece of
each scan lives inside NOAA's files, letting us reopen many scans as one dataset
without holding a second copy.

This "virtual references" pattern is proven at much larger scales. Earthmover
cloud-optimized the entire GOES-16 archive — 380,000 files, ~115 TB — without
copying it: the reference manifests cost about $100 once and roughly 80 GB of
storage (~$1.84 per month) instead of ~$2,600 per month to duplicate the archive
([Virtual Zarr](https://www.earthmover.io/blog/virtual-zarr)). The same team
showed the trick works for GRIB files too
([Virtual GRIB/NBM](https://www.earthmover.io/blog/virtual-grib-nbm)), with one
catch that matters to us: **it cannot see through gzip compression**, and MRMS
files are gzipped. That is exactly why radar gets a materialized cropped Zarr
while satellite scans additionally support range reads and references. Our
references point at NOAA's objects, so they stay valid only while NOAA keeps
serving those files; the fetched raw Zarr subsets remain our durable copy.

## Working well with HPC systems

The project will train models at Argonne's ALCF. Its documentation shapes our
storage choices:

- **Project data lives on big parallel filesystems** — Eagle (`/eagle`) for
  Polaris and Flare (`/flare`) for Aurora. They are fast for large files, have
  directory-level quotas, and are **not backed up**; keeping our own verified
  copies elsewhere (the DAS archive and the HF bucket) is required, not optional.
- **Millions of tiny files are the enemy** of parallel filesystems and of
  transfers. ZIP Zarr keeps each hourly subset to one file, which copies faster
  and scans faster than directory trees of chunks.
- **Transfers go through Globus**, a managed transfer service with dedicated
  endpoints (`alcf#dtn_eagle`, `alcf#dtn_grand`, `alcf#dtn_flare` for the
  respective filesystems; `alcf#dtn_home` for home directories). Fewer, larger,
  verified files are precisely what Globus moves best.
- **Stage before compute.** ALCF guidance is to place training data on the
  project filesystem before the job, rather than having every GPU rank fetch
  NOAA files or publish chunks mid-run. Our flow matches that: fetch and verify
  once, archive, transfer, then read locally during training. Polaris compute
  nodes also offer fast local scratch (`/local/scratch`, wiped after each job)
  for temporary staging inside a job.

Because our subsets are self-describing arrays with preserved metadata, what
lands on Eagle or Flare through Globus is exactly what training reads — no
re-decoding step at the far end, and no ambiguity about units or missing values.

## Fits the modeling tools

This direction is where the modeling ecosystem already is. NVIDIA Earth2-Studio,
whose data downloaders we reviewed in detail
([review](earth2studio_review.md)), stores model outputs in Zarr through its
`ZarrBackend`, an async variant for high-throughput inference, and an optional
Icechunk backend that adds versioned commits. Keeping our raw inputs in the same
family of formats means the path from raw archive to model input to model output
stays in one toolchain, with regridding or interpolation done deliberately at
model preparation time — never silently at ingestion.

## Deliberately out of scope

Live ingestion services, training and fine-tuning, model execution, and fancier
storage engines (including Icechunk) are deferred per [AGENTS](../AGENTS.md).
Virtual references remain an experiment to measure, not a promised speedup:
[the development plan](development_plan.md) requires claims to stay tied to
measured bytes, requests, and wall time.

## Sources reviewed

Accessed September 10, 2026.

- [Earthmover: Tensors vs. Tables](https://www.earthmover.io/blog/tensors-vs-tables/)
- [Earthmover: Cloud-optimizing the GOES-16 archive as Virtual Zarr](https://www.earthmover.io/blog/virtual-zarr)
- [Earthmover: Virtual references for GRIB archives (NBM)](https://www.earthmover.io/blog/virtual-grib-nbm)
- [ALCF: File systems and storage](https://docs.alcf.anl.gov/data-management/filesystem-and-storage/#project-directories)
- [ALCF: Using Globus](https://docs.alcf.anl.gov/data-management/data-transfer/using-globus/)
- [Earth2-Studio IO backends (Zarr, async Zarr, Icechunk)](https://github.com/NVIDIA/earth2studio/tree/main/earth2studio/io)
