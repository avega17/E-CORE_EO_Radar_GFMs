# Notebook usage and expected results

The `.py` percent-format files in `notebooks/` are the source of truth. Their
`.ipynb` partners are synchronized with Jupytext. Reusable operations live in
`src/ecore_weather/`, so notebook buttons and argparse commands use the same
selection, fetch, archive, and visualization functions.

| Notebook | What it does | Expected result |
| --- | --- | --- |
| [01 MRMS](../notebooks/01_mrms.ipynb) | Finds CARIB products, records STAC selections, saves raw ROI samples into monthly archives, and examines coverage and sentinel values. | A product/month archive report, per-slot fetch outcomes, patch statistics, and raw/session-processing figures. |
| [02 GOES](../notebooks/02_goes.ipynb) | Finds full-disk scans and selected bands, records STAC, stores native-resolution subsets, and compares reads or builds sample references. | Per-band monthly archive reports, scan coverage, quality diagnostics, and image previews. |
| [Explore datasets](../notebooks/03_03_explore_datasets.ipynb) | Searches monthly archives, restores one month from an HF yearly backup when requested, and previews single samples or sequences. | A time-filtered list, interactive map, optional animations/PNG export, and a storage-size explorer. |

## Shared defaults

Date ranges are UTC and exclude the end. MRMS defaults to precipitation rate,
composite reflectivity, and low-level azimuthal shear at ten-minute slots, plus
native hourly multisensor Pass2 QPE. Other products remain selectable. For each
slot it selects the newest observation at or before the slot within five
minutes, never reusing a file. GOES defaults to all available scans and
StormScope example channels C01, C02, C03, C07, C08, C09, C10, and C13. Actual
source timestamps and gaps are shown; no image is synthesized for a missing scan.

Set the product, dates, region, destination, and task/reader concurrency in the
widgets. The **Find** button only discovers objects and saves compact STAC
metadata. The **Fetch** button reads and writes data. `hf` uses the configured HF
bucket; enter a local path to select local storage. Keep NOAA scratch on a disk
with adequate free space. Local runs may use two writers for separate monthly
archives; remote publishing uses one coordinator.

Hover over each worker control for its purpose. Download tasks control concurrent
source files; reader processes separate HDF5 reads from the kernel when needed;
monthly writers build independent local monthly stores. Bounds prevent multiple
workers from writing the same month.

## Data and displays

Each local month is stored as a compressed Zarr v3 ZIP. HF months are compressed
Zarr v3 object stores written through Earth2Studio's async backend. Their
product/region/month grouping is stable across requested date windows, and a
completion marker points to the verified remote version. Repeated fetches merge
only missing source records.
The local path streams source observations into a temporary monthly build and
removes that build after read-back validation, without retaining source-file
duplicates. The new monthly store follows
Earth2Studio's named-array and coordinate conventions and can be opened through
the project's `MonthlyZarrSource`. Older single-observation folders are outside
the acceptance checks; migrating them is optional.

The archived measurements remain raw. The radar and GOES display operations may
apply quality masks, decode calibration, interpolate, reproject for a map, or
hide zero rain to improve the view; none of these changes is saved in the raw
archive. GOES packed counts should not be interpreted as temperatures until
scale and offset are applied. Radar accumulation products remain accumulations.

Both fetch notebooks show preview plots inline. Check **Save displayed figures**
to write PNGs. In notebook 03, choose MRMS or GOES first, enter a local archive
path or HF bucket path, filter by date/time, then select an observation. The
single-sample tab uses a pan/zoom map; other tabs prepare a sequence for one day
or a multi-day period. Sequences are preloaded for the selected, bounded region
rather than streaming tiles.

The HF yearly MRMS ZIPs are backup containers. In notebook 03, open **HF yearly
backup**, list verified packages, inspect a product/year, and choose **Prepare
month**. Byte-range reads restore only that monthly Zarr ZIP to the local cache;
the viewer checks its SHA-256 and completion marker before searching it. Annual
ZIPs are not directly openable Zarr stores. Directly readable remote monthly
Zarr stores can still be searched. The **Storage explorer** nests a summary,
archive details, and size comparison under product, year, and month selectors.
It compares stored regional Zarr bytes with listed NOAA source-object bytes and
reports the percentage only for rows with known original sizes. NOAA objects may
already be compressed, and their full-file sizes do not represent the ROI crop.
The size percentage is the ratio of total stored archive bytes to total complete
listed source-object bytes, not a compression ratio or a mean of month-by-month
percentages. An archive with any unlisted source size is excluded from that
comparison and shown in the coverage figure. The explorer opens each monthly
archive once when preparing a sequence, then reads the requested frames from
that store.
A live fetch may hold the local DuckDB write lock; direct archive paths bypass
it and broader searches fall back to completion manifests. Restart the notebook
kernel after changing imported viewer modules. The setup cell puts this checkout's
`src/` directory first on `sys.path`, and prints the loaded viewer module path.
If the search reports matches but a visualization still requests a new search,
restart the kernel and rerun the setup and widget cells so callbacks no longer
use an older imported viewer module.

## Script use

Run the source as a script with the same request functions and CLI options:

```bash
python notebooks/01_mrms.py --operation inspect --start 2024-09-15 --end 2024-09-22
python notebooks/01_mrms.py --operation fetch --start 2024-09-15 --end 2024-09-22 \
  --destination /mnt/p/ecore_eo_datasets --save-figures figures/mrms
python notebooks/02_goes.py --operation inspect --start 2025-09-01 --end 2025-10-01
```

For MRMS, `--product` accepts `precipitation-rate`, `composite-reflectivity`,
`base-reflectivity`, `radar-only-qpe-1h`, `low-level-azimuthal-shear`,
`mid-level-azimuthal-shear`, `multisensor-qpe-pass1`, and
`multisensor-qpe-pass2`. These readable names resolve to NOAA's exact product
keys in selections and archive metadata.

Colab setup only detects Colab, clones the repository at `ECORE_REVISION` (or
`main`), and installs `.[notebooks]`. The selected commit is printed. Package
changes must be pushed at that revision before clone-based Colab testing.
