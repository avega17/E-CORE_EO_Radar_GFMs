# Earth2-Studio data source review

Updated September 10, 2026.

We reviewed NVIDIA Earth2-Studio's downloaders for our two sources —
[earth2studio/data/mrms.py](https://github.com/NVIDIA/earth2studio/blob/main/earth2studio/data/mrms.py)
and
[earth2studio/data/goes.py](https://github.com/NVIDIA/earth2studio/blob/main/earth2studio/data/goes.py),
plus its
[GOES lexicon](https://github.com/NVIDIA/earth2studio/blob/main/earth2studio/lexicon/goes.py)
and [Zarr output backends](https://github.com/NVIDIA/earth2studio/tree/main/earth2studio/io) —
to check our approach against a production library and to look for simple
improvements worth adopting. Conclusion: the two pipelines solve different
problems, our approach already matches their main practices, and **we adopt no
code changes from this review**. The reasons are recorded below so the decision
is auditable. The broader motivation for our raw-Zarr design lives in
[the raw data rationale](raw_data_rationale.md).

## What the two pipelines are for

Earth2-Studio's data sources answer "give me variable `v` at time `t` on a
model-ready grid" for one forecast input at a time: they decode to physical
values, regrid where needed, and return an in-memory array for immediate model
use. Our pipeline answers "keep every available observation of this product for
this period, exactly as NOAA published it, so later steps can trust and re-derive
anything": we persist one lossless Zarr subset per source object with native
values, coordinates, units, and quality flags (see [AGENTS](../AGENTS.md)).
Both fetch from the same NOAA AWS buckets anonymously.

## Practice comparison

| Practice | Earth2-Studio | This pipeline | Verdict |
| --- | --- | --- | --- |
| S3 access | Anonymous, `obstore` store | Anonymous, `obstore` client | Same library; different calling shape |
| Concurrency | Async tasks, one per requested timestamp, ~24 workers | Bounded thread pool over a persisted STAC selection; I/O workers and CPU decoding bounded separately | Equivalent goals, different shape |
| Repeat LIST requests | Memoized hour-directory listings per prefix | Not needed: one LIST per day (MRMS) or hour (GOES) prefix per discovery, and the saved selection is reused afterwards | Not adopted; nothing repeated to memoize |
| Corrupt source objects | Falls back to the next-nearest candidate file within the time tolerance | One chosen asset per hourly slot; a corrupt object fails that slot explicitly | Not adopted; see below |
| MRMS coverage | CONUS products only | CARIB products for Puerto Rico | Different regions; ours required by scope |
| GRIB decode | `pygrib`; grid axes from header keys when regular, `latlons()` fallback | Direct `eccodes`; native coordinates cached by grid identity; bitmap kept as a separate mask | Equivalent speed idea, raw-preserving variant |
| GOES pixels | Decodes to physical values on an analytic ideal grid; NaN for fill | Packed integers, native fixed-grid coordinates, and per-band quality flags preserved | Deliberately different: raw first |
| Output | In-memory array; separate IO backends write model outputs to Zarr v3 (`ZarrBackend`, `AsyncZarrBackend`, `IceChunkBackend`) | Lossless raw Zarr per source object, directory or ZIP container | Both store results in Zarr |

## Considered and not adopted

**Memoized S3 listings.** Earth2-Studio answers many ad-hoc timestamps and
caches each hour's directory listing so repeated requests skip the LIST call.
Our discovery issues exactly one LIST per day prefix for MRMS
(`mrms.discover`) and one per hour prefix for GOES (`goes.discover`), then
persists the whole selection as STAC items that later steps reuse. There is no
second listing of the same prefix, so a listing cache would save nothing here.

**Next-candidate fallback for corrupt GRIB.** Their fetch walks further
candidates within the time tolerance when a file is truncated. Our hourly
matcher deliberately assigns exactly one source file per slot and keeps its
actual timestamp and offset in the manifest; silently substituting a different
observation would blur that record. Across the two three-month checks
(2,183/2,184 and 2,184/2,184 files) and the H2 2022 sample (4,412 files) we
observed zero corrupt objects, so this guards a failure mode we have not seen.
If a corrupt or truncated object ever appears in the archive, this is the first
idea to revisit.

**Analytic GOES grids and decoded values.** Their GOES source builds the fixed
grid from projection constants and returns physical values. Our raw subsets keep
NOAA's packed integers, scale/offset, and DQF flags; decoding and regridding
happen only in memory, per the raw-first rules. Their measured 3-pixel limb-band
NaN diagnostic (instrument-edge fill values that are not data problems) is a
nice in-memory quality idea for later visualization work; it is deferred, not
planned.

**Lexicon.** Their `GOESLexicon` maps `abi01c`–`abi16c` to `CMI_C01`–`CMI_C16`
with identity modifiers, matching how our packed preservation already behaves.
We used it as a cross-check for the band guide in the notebook controls.

## Where the libraries agree

Both projects treat the archived files as the source of truth, fetch without
credentials, bound their concurrency, and store results in Zarr. Earth2-Studio's
own model outputs go to Zarr (with sharding to keep file counts low, and an
optional Icechunk backend for versioned commits), which supports the same
storage choice we made for raw subsets; see
[the raw data rationale](raw_data_rationale.md) for that argument and for what
we deliberately leave out.

## Sources reviewed

Accessed September 10, 2026.

- [Earth2-Studio MRMS data source](https://github.com/NVIDIA/earth2studio/blob/main/earth2studio/data/mrms.py)
- [Earth2-Studio GOES data source](https://github.com/NVIDIA/earth2studio/blob/main/earth2studio/data/goes.py)
- [Earth2-Studio GOES lexicon](https://github.com/NVIDIA/earth2studio/blob/main/earth2studio/lexicon/goes.py)
- [Earth2-Studio IO backends (Zarr, async Zarr, Icechunk)](https://github.com/NVIDIA/earth2studio/tree/main/earth2studio/io)
