# Developer guide

Read [AGENTS.md](../AGENTS.md), [the plan](development_plan.md), and
[status](status.md) before continuing work. Update these docs when interfaces,
outputs, or evidence change. Keep research claims tied to [validation](validation.md).

## Environment and entry points

Create the Conda environment from `environment.yml` and activate `ecore-weather`.
This checkout's tested interpreter is `.conda/bin/python`. Compiled dependencies
come from conda-forge; the package's `notebooks` extra supplies matching pip
versions for Colab. Keep both definitions aligned. No GPU is required.

```bash
python notebooks/01_mrms.py --help
python notebooks/02_goes.py --help
python notebooks/01_mrms.py --operation inspect --period 2022 --output results/mrms
python notebooks/01_mrms.py --operation fetch \
  --start 2022-09-24T17:00:00Z --end 2022-09-24T18:00:00Z \
  --destination data/mrms --workers 4 --save-figures figures/mrms --recipe compare
python notebooks/02_goes.py --operation fetch \
  --start 2025-09-01 --end 2025-09-01T01:00:00Z --max-files 1 \
  --satellite auto --bands 8 13 --destination data/goes --save-figures figures/goes
python notebooks/01_mrms.py --operation validate --period 2022 --output results/mrms-2022
python notebooks/02_goes.py --operation validate --period 2025 --output results/goes-2025
```

Repeat validation with the other year. MRMS tests all available matched hourly
files, reports missing slots, and checks the mentor's calculations. GOES discovers
the full period and tests six representative scans. `--skip-mentor` omits the MRMS
comparison. `--selection results/mrms/collection.json` reuses an existing selection.
`--max-files` explicitly limits a sample; its remaining requested hours should not
be interpreted as an archive outage. Nonzero exit codes indicate failed validation
or fetching. Argonne scheduler directives are deliberately deferred; these scripts
already accept batch-job arguments. The notebook widgets list only full-disk GOES
products; the command line also accepts the CONUS variants (`ABI-L2-MCMIPC`,
`ABI-L2-CMIPC`) for advanced use.

## Code layout

| Module | Responsibility |
| --- | --- |
| `mrms`, `goes` | Discovery, native reads, source-specific processing; GOES virtual references |
| `common`, `catalog` | Requests, source identity, network counters, compact STAC persistence |
| `hf_storage` | Serialized batch publication and read-back for HF buckets |
| `storage` | Lossless writes, read-back checks, resume, HF/local publication, scratch cleanup |
| `diagnostics`, `visualization` | Sentinel-aware statistics, maps, session-only views and PNG export |
| `benchmark`, `_benchmark_worker` | Isolated mentor/revised comparisons and timing |
| `validation` | Full-period checks with temporary data and compact results |
| `ui`, `cli` | Widgets and argparse calling the same request and pipeline functions |

Do not edit the original mentor scripts. The comparison adapter redirects only
source selection when a nominal hour maps to an off-hour file; calculation steps
remain unchanged. Retain exact source identity and timing offsets in metadata.

## Focused checks

```bash
jupytext --sync notebooks/01_mrms.py notebooks/02_goes.py
python -m compileall -q src notebooks
pytest -q
python -m pip check
```

Use nbclient or nbconvert with `ECORE_NOTEBOOK_SMOKE=1` to execute the notebook
pairs against small real NOAA samples. This mode exercises selection, fetching,
plotting, and PNG export in temporary directories. Write executed notebooks outside
the source pair, then delete them after checking results. Clear committed outputs.
Network checks need access to NOAA. Some restricted sandboxes also block Zarr's
asynchronous I/O; run in the normal environment rather than treating a hang as
scientific validation evidence.

A raw round trip checks arrays, coordinates, and all attributes. Session processing
must leave raw fingerprints unchanged. Virtual equality includes scalar metadata
and quality flags, not just image pixels. Missing source hours and invalid pixels
are distinct outcomes. Correctness tests run in parallel batches; use repeated
matched benchmarks to support speed claims.

## Cleanup and handoff

Validation owns its temporary directory and removes it even after failure. Keep
only compact results under `docs/evidence/`; do not commit per-file STAC trees,
raw-week directories, full reference copies, executed notebooks, or test rasters.
For manual tests, delete only outputs belonging to that test after preserving its
summary. Never sweep unrelated local data or bucket contents. Normal fetch output
is durable research data and is not test scratch.

Default storage uses `HF_BUCKET_NAME` and `HF_TOKEN` from the untracked `.env`;
legacy aliases are documented in the README. HF tokens are not S3 gateway keys.
Colab setup detects Colab, clones a configurable revision, and installs dependencies.
Push the exact changed revision before testing cloned modules. Google authorization
is required for the Colab CLI; local success is not evidence of Colab execution.


For long local runs, see [the six-month guide](long_sample.md). `--scratch` selects
fast staging, `--container zip` reduces small-file overhead, and `--read-processes`
uses independent HDF5 readers. Spawned readers do not construct notebook widgets.
`--layout legacy` can address older hash-only paths. See [storage](storage.md) for
publication semantics and the Windows DAS mount check.

## Current interfaces and storage reuse

Notebook 03 calls `viewer.stores`, `viewer.draw`, and `viewer.controls`. Geography
is shared through `maps.py`; plots never modify raw arrays. Cartopy is declared
in both Conda and the Colab dependency extra. Pre-cache Natural Earth 50m land
for offline plotting. Widget help uses ipywidgets 8 `tooltip`; the older
`description_tooltip` is deprecated.

Storage identity is `subset_identity(selection)` plus the immutable source ID.
Do not add request start/end times, worker counts, or hourly slot assignments to
that identity. Request-specific slot metadata belongs in reports and temporary
inspection views. CMIP objects each contain one band; MCMIP band sets remain part
of the requested subset identity. `merge_local_archive.py` validates earlier
local stores before adopting shared paths. Test overlapping dates and container
changes whenever changing resume behavior.

`scripts/compare_2023.py --source mrms` compares all available H1 2023 hourly
files with the mentor's unchanged calculations, using sequential legacy and
4-/8-process revised variants. Each writes temporary matching GeoTIFFs. GOES uses
January 2023 CMIPF discovery and representative first/middle/final-day midnight
and noon reads for bands 1,2,3,7,8,9,10,13, preserving each band's native grid.
Benchmark stage times are summed task times; only wall time measures throughput.
No Argonne scheduler integration or model compatibility is implied.
