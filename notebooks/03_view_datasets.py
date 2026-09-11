# %% [markdown]
# # Explore radar and satellite imagery over time
# Choose **MRMS** or **GOES**, a local archive or the configured Hugging Face
# bucket, and UTC start/end dates and times. The ending instant is excluded.
# **Find observations** filters the archive first; choose one product/region
# from the short dataset list. Observation times use a slider rather than a
# long dropdown. The initial July 2022 day is in our existing archive; change
# the dates to explore the new 2023 fetches as they complete.
#
# Three tabs show a single image, every available observation within one day,
# or 4–8 selected images per day across the period. Maps support dragging and
# scrolling to zoom. Animations prepare images once, then use Play or the frame
# slider. Missing observations are not filled or interpolated in time.
#
# For display only, packed values are decoded, quality masks can be applied,
# and pixels are reprojected onto a small Web Mercator grid using nearest-neighbor
# sampling. Saved raw Zarr values and coordinates remain unchanged. Each animation
# is limited to 300 frames; shorten the period if necessary. Colors stay fixed
# throughout a sequence. Basemap tiles require internet access.

# %%
if __name__ != "__mp_main__":
    from IPython import get_ipython
    if __name__ == "__main__" and get_ipython() is None:
        from ecore_weather.viewer import main
        raise SystemExit(main())

# %% [markdown]
# ## Set up
# Locally, select the Conda kernel from `environment.yml`. Colab detects its
# runtime, clones the repository, and installs dependencies. Set `REVISION` to
# the pushed code you want to test. Never put a storage token in a saved cell.

# %%
if __name__ != "__mp_main__":  # Spawned readers must not construct notebook widgets.
    import os
    import subprocess
    import sys
    from pathlib import Path
    REPOSITORY = "https://github.com/avega17/E-CORE_EO_Radar_GFMs.git"
    REVISION = os.getenv("ECORE_REVISION", "main")
    IN_COLAB = "google.colab" in sys.modules or bool(os.getenv("COLAB_RELEASE_TAG"))
    if IN_COLAB:
        from google.colab import output
        output.enable_custom_widget_manager()
        root = Path("/content/E-CORE_EO_Radar_GFMs")
        if not root.exists():
            subprocess.run(["git", "clone", REPOSITORY, str(root)], check=True)
        subprocess.run(["git", "fetch", "origin", REVISION], cwd=root, check=True)
        subprocess.run(["git", "checkout", "--detach", "FETCH_HEAD"], cwd=root, check=True)
        os.chdir(root)
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", ".[notebooks]"], check=True)
    else:
        root = next((p for p in (Path.cwd(), *Path.cwd().parents)
                     if (p / "src/ecore_weather").exists()), Path.cwd())
        os.chdir(root)
    print("Repository:", root)
    print("Commit:", subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip())

# %% [markdown]
# ## Choose data, then a view
# Local example: `/mnt/p/ecore_eo_datasets`. Selecting **Hugging Face** fills in
# the configured `hf://buckets/.../noaa-subsets` location. You can also enter a
# narrower product/region folder or an individual raw Zarr path. An existing
# hf-mount directory works through **Local**, but mounting is not required.
# Credentials stay in your environment. HF ZIP subsets use temporary local
# scratch while opening; preparation closes those files after each frame.
#
# Change a search parameter and click **Find observations** again. Choose one
# dataset and satellite band. **Hide zero values** makes valid zero rain
# transparent in the display. **Save a PNG too** exports a static map from the
# single-image tab. Preparing an animation shows its own progress bar.

# %%
if __name__ != "__mp_main__":
    from ecore_weather import viewer
    panel = viewer.controls()

# %% [markdown]
# ## Run from a terminal
# `python notebooks/03_view_datasets.py /path/to/raw.zarr.zip --hide-zero --output figures/rain.png`
#
# The [storage guide](../docs/storage.md) explains paths and remote access.
# The [Leafmap Zarr example](https://leafmap.org/notebooks/111_zarr/),
# [Cloud Native Geospatial guide](https://guide.cloudnativegeo.org/zarr/zarr-in-practice.html),
# and [Copernicus xarray example](https://help.marine.copernicus.eu/en/articles/8077952-how-to-open-and-visualize-zarr-format-data)
# describe larger-scale visualization options. A tile service is later work if
# our small subset viewer becomes insufficient.
