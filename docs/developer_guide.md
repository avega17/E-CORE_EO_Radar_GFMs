# Developer guide

Read [AGENTS.md](../AGENTS.md), the [current plan](development_plan.md), and
[status](status.md) and [study jobs](study_jobs.md) before changing the fetch or archive layout. Keep claims and
measurements in the docs tied to completed runs.

## Environment and commands

Create the Conda environment from `environment.yml`. It supplies the data stack,
Jupytext, DuckDB, notebook tools, and pinned Earth2Studio. The package's
`notebooks` extra is the Colab install path. GPU and inference dependencies stay
outside this data-preparation environment.

```bash
conda env create -f environment.yml
conda activate ecore-weather
python -m ipykernel install --user --name ecore-weather --display-name 'E-CORE weather'
python notebooks/01_mrms.py --help
python notebooks/02_goes.py --help
```

Examples use the same selection and fetching functions as the widgets:

```bash
python notebooks/01_mrms.py --operation inspect --start 2023-01-01 --end 2023-02-01
python notebooks/01_mrms.py --operation fetch --start 2023-01-01 --end 2023-02-01 \
  --product precipitation-rate composite-reflectivity \
  --destination /mnt/p/ecore_eo_datasets --workers 8 --monthly-writers 2
python notebooks/02_goes.py --operation fetch --start 2023-01-01 --end 2023-02-01 \
  --satellite 16 --bands 1 2 3 7 8 9 10 13 --destination /mnt/p/ecore_eo_datasets
```

MRMS defaults to precipitation rate, composite reflectivity, and low-level
azimuthal shear sampled every ten minutes, plus hourly multisensor Pass2 QPE.
Other products remain selectable. The default match is latest at/before a slot
within five minutes, without reusing a file. GOES defaults to every available scan and
eight StormScope example bands, using per-band CMIPF. `--scans-per-hour 0`
means keep all scans. `--monthly-writers` applies to separate local monthly
stores. The year-bundle HF backup uses four independent product/year files and
checks each complete remote object against its local SHA-256 before cleanup.
Direct monthly HF writes remain a separate option. The default download pool is
half the detected CPU count; decoding is bounded independently. A script's
`--save-figures DIRECTORY` writes preview PNGs. `--selection` reuses a saved
STAC collection or manifest.

On the local streaming path, `--workers` controls concurrent NOAA read tasks per
monthly archive, up to a bound of 16. With the MRMS study defaults of two
monthly writer processes and two decode slots per process, that permits up to
32 in-flight source tasks and four simultaneous GRIB decodes. Network reads
overlap the serial Zarr write; the decode semaphore is separate. A monthly
writer owns a different product-month archive, so increasing `--workers` does
not add writer processes. More pending reads can hold more compressed inputs
and decoded arrays in memory, so keep both limits explicit and compare wall
time, source wait, decode/write time, and peak memory. The existing short
one-versus-two-writer MRMS pilot (29.1 s versus 26.3 s) was cache-sensitive and
does not establish an optimal read-thread count.

Argonne scheduler directives are deferred; the CLIs already accept batch
arguments. Colab setup detects the runtime, clones the selected revision, and
installs dependencies. Push the exact changed revision before testing package
code through a Colab clone.

## Package map

| Module | Role |
| --- | --- |
| `mrms`, `goes` | Product discovery, cadence, source decoding, and native ROI reads |
| `earth2_sources` | Earth2Studio-compatible MRMS/GOES data-source adapters |
| `earth2_io` | Earth2Studio local `ZarrBackend` writer and HF obstore helper |
| `monthly` | Stable month paths, merge/resume, verification, cleanup, and writer concurrency |
| `remote_async` | Direct Earth2Studio async HF month writer and local-year mirror |
| `catalog`, `index` | STAC selection records and rebuildable local DuckDB search/run index |
| `hf_storage` | HF S3 gateway configuration, one publisher, and read-back verification |
| `storage` | Raw source staging, fingerprints, readers, and common helpers |
| `diagnostics`, `visualization`, `view_frames` | Missing-value statistics and session-only views |
| `viewer`, `view_index` | Notebook 03 controls and monthly observation lookup; direct archive reads work while DuckDB is locked by a fetch |
| `view_backup`, `view_storage` | Range-read HF annual backup metadata, verify a restored month, and summarize archive/source sizes |
| `ui`, `cli` | Shared notebook and argparse request/pipeline interfaces |

Do not edit the original mentor scripts. Their numerical steps remain a
reference comparison. Archive content must preserve native raw scientific
values; masks, interpolation, decoding, reprojection, and display scaling are
session-only operations.

## Focused checks

```bash
jupytext --sync notebooks/01_mrms.py notebooks/02_goes.py notebooks/03_03_explore_datasets.py
python -m compileall -q src notebooks
pytest -q
python -m pip check
```

For viewer changes, run `pytest -q tests/test_viewer.py`. Its annual-backup
fixture uses a fake range-reading S3 client; a separate live check can restore
one small monthly member into a temporary directory and remove that directory
after opening a frame. Do not treat an annual ZIP as a Zarr path. The viewer's
storage explorer reads completion/STAC metadata only; it does not inspect
every chunk in the archived arrays.

Use `ECORE_NOTEBOOK_SMOKE=1` with nbclient for short end-to-end notebook checks
in a runtime with NOAA access. Save executed notebooks outside the source pair
and remove them after review. A live HF test must use a unique temporary key or
archive prefix and remove only that test data after a successful read-back.

The raw validation compares values and metadata after source-to-Zarr round
trips. MRMS checks timestamps and accumulation labels; GOES checks packed values,
coordinates, calibration, and DQF. Monthly archives should reopen through
`MonthlyZarrSource`, which implements Earth2Studio's `DataSource` call pattern.
The standard writer is authoritative: a backend or content-check failure stops
the run rather than switching to another serialization path. Full-period
historical jobs and external network checks are distinct from unit tests.

## Archive index and cleanup

`results/archive_index.duckdb` is a local rebuildable index. Keep its writes in
one process and keep the file on local Linux storage. STAC and each monthly
`complete.json` remain the source of truth. Rebuild with:

```python
from ecore_weather.index import rebuild
rebuild(["/mnt/p/ecore_eo_datasets"])
```

Keep compact results in `docs/evidence/`. Do not commit per-file selection
folders, raw weeks, temporary monthly stores, test archives, secrets, or notebook
execution output. Delete only artifacts created for a successful test; never
remove ordinary research archives or unrelated bucket objects. Check `findmnt -T`
before writing to `/mnt/p`; an unmounted WSL path is on the Linux root disk.
