# NOAA radar and satellite subsets for Puerto Rico

This project prepares reproducible raw subsets from NOAA MRMS radar and GOES
satellite archives for weather-forecasting research. The notebooks use custom
Earth2Studio-compatible sources to retain the Puerto Rico region, requested
products and bands, and native source metadata. They save validated, compressed
monthly Zarr archives and keep analysis steps separate from the raw data.

| Notebook | Description |
| --- | --- |
| [MRMS radar](notebooks/01_mrms.ipynb) · [Python source](notebooks/01_mrms.py) | Select CARIB radar products, archive raw monthly subsets, and inspect coverage and missing-value codes. |
| [GOES imagery](notebooks/02_goes.ipynb) · [Python source](notebooks/02_goes.py) | Select full-disk scans and ABI bands, archive native-grid subsets, and inspect data quality. |
| [Dataset explorer](notebooks/03_03_explore_datasets.ipynb) · [Python source](notebooks/03_03_explore_datasets.py) | Browse monthly archives, prepare one month from an HF yearly backup, compare storage sizes, and preview samples or sequences on a map. |

## Set up

```bash
conda env create -f environment.yml
conda activate ecore-weather
python -m ipykernel install --user --name ecore-weather --display-name 'E-CORE weather'
```

Set `HF_BUCKET_NAME=namespace/bucket`, Hub token credentials, and the separate HF
S3 gateway keys in an untracked `.env` to use the default remote destination.
`HF_DATASET_REPO` remains a compatibility alias for the bucket name. NOAA source
reads are anonymous. Use a local path in the **Save to** control or
`--destination PATH` for local archives. Read [storage and bucket access](docs/storage.md)
before starting a large upload.

MRMS defaults to four fields: precipitation rate, composite reflectivity, and
low-level azimuthal shear sampled at ten-minute slots, plus multisensor Pass2
QPE at hourly cadence. Other MRMS products remain selectable. GOES defaults to
every available scan and eight StormScope example channels: C01, C02, C03, C07,
C08, C09, C10, and C13. Both keep actual observation times and report gaps.

Each source, product, native ROI/grid or band, and UTC month has one compressed
Zarr archive. A repeated fetch merges additional dates into that month. Local
runs can build separate monthly archives with two writers; HF writing uses one
Earth2Studio async coordinator and verifies remote arrays before completion.
Raw values and product metadata are preserved. Cleaning,
interpolation, calibration decoding, and display reprojection remain session-only
operations. Local archives use Earth2Studio's `ZarrBackend`; HF archives use
`AsyncZarrBackend` through obstore. Both can be reopened with the project's
Earth2Studio-compatible monthly source adapter.

## Use notebooks or scripts

Open the paired notebooks in Jupyter or VS Code and run **Find** before **Fetch**.
The Python sources also accept argparse options for batch use:

```bash
python notebooks/01_mrms.py --operation inspect --start 2023-01-01 --end 2023-02-01
python notebooks/01_mrms.py --operation fetch --start 2023-01-01 --end 2023-02-01 \
  --product precipitation-rate composite-reflectivity \
  --destination /mnt/p/ecore_eo_datasets --workers 8 --monthly-writers 2
python notebooks/02_goes.py --operation fetch --start 2025-09-01 --end 2025-10-01 \
  --bands 1 2 3 7 8 9 10 13 --destination /mnt/p/ecore_eo_datasets
python notebooks/02_goes.py --operation estimate --output results/study-goes-estimate
python scripts/run_mrms_staged.py --destination /mnt/p/ecore_eo_datasets
```

The default task pool is half the detected CPU count. Decoding can be bounded
separately. See [notebook usage and outputs](docs/notebooks.md) and the
[developer guide](docs/developer_guide.md) for more examples.

## Colab

[Open MRMS in Colab](https://colab.research.google.com/github/avega17/E-CORE_EO_Radar_GFMs/blob/main/notebooks/01_mrms.ipynb),
[GOES in Colab](https://colab.research.google.com/github/avega17/E-CORE_EO_Radar_GFMs/blob/main/notebooks/02_goes.ipynb), or
[the dataset explorer in Colab](https://colab.research.google.com/github/avega17/E-CORE_EO_Radar_GFMs/blob/main/notebooks/03_03_explore_datasets.ipynb)
after those files are pushed. Setup detects Colab, clones the chosen revision,
and installs the notebook dependencies. Never store credentials in a notebook.

## Project notes

- [Development plan](docs/development_plan.md)
- [Storage layout and HF access](docs/storage.md)
- [Earth2Studio source and IO review](docs/earth2studio_review.md)
- [Notebook functions and outputs](docs/notebooks.md)
- [Developer guide](docs/developer_guide.md)
- [Validation evidence](docs/validation.md)
- [Current implementation status](docs/status.md)
- [Study-period estimate and fetch jobs](docs/study_jobs.md)
- [Completed GOES study-period estimate](docs/goes_study_estimate.md)
- [Shared agent instructions](AGENTS.md)

Reusable modules are in `src/ecore_weather/`. STAC selections and monthly
completion manifests are the portable records. `results/archive_index.duckdb`
is a local, rebuildable search and run index; it is not required to open an archive.
