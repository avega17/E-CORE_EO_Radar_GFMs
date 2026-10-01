# GOES-East study-period storage and time estimate

Completed processing and estimates for GOES dataset covering January 1, 2021 through June 30, 2026
(inclusive). The Puerto Rico region is
`[-70.24, 14.36, -62.56, 22.04]`. The job inventoried all 66 months from
NOAA S3 metadata and read **257 small native-pixel crop samples**, including
one initial read check. It did **not** fetch the full selected imagery.
GOES-East uses GOES-16 before April 7, 2025, 15:00 UTC and GOES-19 afterward.

| Requested scenario | Available band files | Nominal missing band files | Listed whole-file S3 | Estimated ROI read transfer | Cropped NetCDF | Compressed Zarr |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 8 bands, 6 scans/hour | 2,303,762 | 8,302 | 146.52 TB | 30.28 TB | 1.00 TB | 0.75 TB |
| 8 bands, 3 scans/hour | 1,152,882 | 3,150 | 73.32 TB | 15.16 TB | 0.50 TB | 0.37 TB |
| 8 bands, 1 scan/hour | 384,420 | 924 | 24.44 TB | 5.05 TB | 0.17 TB | 0.12 TB |
| 16 bands, 6 scans/hour | 4,607,533 | 16,595 | 202.28 TB | 49.22 TB | 1.43 TB | 1.04 TB |

The eight bands are C01, C02, C03, C07, C08, C09, C10, and C13. TB is decimal
(10¹² bytes). “Missing” compares the nominal six, three, or one scans per UTC
hour per band with listed files; it is a scenario count, not proof that every
missing file was expected in NOAA's operating mode. The whole-file S3 total is
shown separately because range reads can still transfer much more than the
retained Puerto Rico crop. Cropped NetCDF and Zarr sizes are empirical
extrapolations of packed source-pixel samples, not actual study archives.

The Zarr low–high ranges are **0.42–1.26 TB** for eight bands at six scans/hour,
**0.21–0.63 TB** at three, **0.07–0.21 TB** at one, and **0.63–1.68 TB** for
all 16 bands at six. The 16-band case is the requested upper scenario for
storage planning.

For time, the sum of sampled per-file read and local write task durations gives
central values of **77.1, 38.6, 12.9, and 134.5 serial task-days** in the same
scenario order. A separate illustrative workstation wall-time range assumes
eight effective concurrent tasks for its low end, four for its center, and one
for its high end: **8.4–118.6, 4.2–59.4, 1.4–19.8, and 14.7–207.2 days**.
The corresponding centers are 19.3, 9.6, 3.2, and 33.6 days. These are rough
scheduling examples, **not measured multi-worker job durations**. NOAA request
latency, network saturation, compressed-chunk layout, and monthly writer behavior
could change them. The high serial case includes a 30% margin; inventory time
is reported separately in the machine-readable result.

Samples cover January, April, July, and October in 2022 and 2025, near midnight
and noon, across all 16 bands and both operational satellites. Each sample
measures listed source bytes, actual range-read transfer, an uncompressed
cropped NetCDF file, compressed local Zarr chunks, and local read/write task
time. Temporary sample files were removed. The extrapolation assumes these
samples represent the other study years. It adds 2 KiB of per-observation
provenance allowance to Zarr chunks; static month metadata and the current
remote async shard layout may change the final byte count.

The durable evidence is in ignored local files
`results/study-goes-estimate/summary.json`, `scenarios.csv`, `samples.json`,
`inventory/YYYY-MM.json`, and `run_config.json`. Repeating
`python scripts/estimate_goes_study.py --output results/study-goes-estimate`
reuses matching inventory and sample checkpoints without downloading the
selected full-study imagery.
