# NOAA radar and satellite subsets for Puerto Rico

Research notebooks for checking the mentor's MRMS workflow, preserving raw NOAA
subsets, and measuring whether range reads and virtual datasets help.

| Notebook | What it does |
| --- | --- |
| [MRMS radar](notebooks/01_mrms.ipynb) · [Python source](notebooks/01_mrms.py) | Compare the original downloader, save raw radar subsets, and inspect Caribbean missing values. |
| [GOES imagery](notebooks/02_goes.ipynb) · [Python source](notebooks/02_goes.py) | Select bands and a region, inspect quality, and compare full-file, range, and virtual reads. |
| [Dataset viewer](notebooks/03_view_datasets.ipynb) · [Python source](notebooks/03_view_datasets.py) | Browse local or HF Zarr subsets, choose a variable, and display or export a geographic map. |

## Local setup

```bash
conda env create -f environment.yml
conda activate ecore-weather
python -m ipykernel install --user --name ecore-weather --display-name 'E-CORE weather'
```

Open a notebook in Jupyter or VS Code and select the E-CORE weather kernel.
Buttons make downloads and uploads explicit. The default examples use September
1–December 1 in both 2022 and 2025 (UTC, ending date excluded).

Set `HF_BUCKET_NAME=ecore-eo-weather-GFMs` and `HF_TOKEN` in an untracked `.env` for the
default Hugging Face destination. No AWS credentials
are required for NOAA datasets. To save locally, enter a local directory in the notebook's
**Save to** control. Authentication failures do not silently switch storage.

Raw data retain source pixels, coordinates, sentinel codes, and quality metadata.
Interpolation and cleaning stay in notebook memory. Source files and comparison
GeoTIFFs are temporary. STAC selections and small run reports go under `results/`;
large datasets and generated artifacts are ignored by Git.

## Colab

Open [MRMS in Colab](https://colab.research.google.com/github/avega17/E-CORE_EO_Radar_GFMs/blob/main/notebooks/01_mrms.ipynb),
[GOES in Colab](https://colab.research.google.com/github/avega17/E-CORE_EO_Radar_GFMs/blob/main/notebooks/02_goes.ipynb), or [the viewer in Colab](https://colab.research.google.com/github/avega17/E-CORE_EO_Radar_GFMs/blob/main/notebooks/03_view_datasets.ipynb)
after the notebook files have been pushed. The setup cell detects Colab, clones
this repository if needed, and installs dependencies. Set `REVISION` to the
pushed branch or commit being tested. The cell prints the resolved commit.
Use session environment variables for storage credentials; never save tokens in
notebook cells. Automated links and GitHub Actions are later work.

## Development and evidence

- [Notebook usage and expected outputs](docs/notebooks.md)
- [HF and local storage layout](docs/storage.md)
- [Mentor code review](docs/mentor_code_review.md)
- [Six-month sample and progress](docs/long_sample.md)
- [Developer guide and script commands](docs/developer_guide.md)
- [Current handoff status](docs/status.md)
- [Development plan](docs/development_plan.md)
- [Validation and measurements](docs/validation.md)
- [Why raw data lives in Zarr](docs/raw_data_rationale.md)
- [Earth2-Studio data source review](docs/earth2studio_review.md)
- [Shared agent guidelines](AGENTS.md)
- [Supplied reference list](docs/agent_dev_references)
- [Original mentor scripts](PR_rain_512_crop/)

Reusable Python functions live under `src/ecore_weather/`. They can also be called
directly from scripts, without widgets. For example:

```python
from ecore_weather import mrms, catalog, storage
selection = mrms.discover('2025-09-01', '2025-09-01T01:00:00')
catalog.save_selection(selection, 'results/mrms-example')
report = storage.fetch(selection)  # configured HF bucket; destination='data' for local
```

After editing notebook sources:

```bash
jupytext --sync notebooks/*.py
python -m compileall -q src notebooks
pytest -q
```

Keep notebook outputs cleared before committing.
