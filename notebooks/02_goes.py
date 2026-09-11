# %% [markdown]
# # GOES: raw subsets, quality flags, and virtual references
# Start with full-disk C08/C13 imagery. The example periods are September 1–
# December 1 in 2022 and 2025. Automatic GOES-East selection uses GOES-16 for
# the 2022 period and GOES-19 for the 2025 period. You can select a satellite
# explicitly. Raw storage keeps packed integers, coordinates, calibration, and
# quality flags. Decoding and interpolation happen only in memory.

# %%
if __name__ != "__mp_main__":  # Spawned readers must not construct notebook widgets.
    SOURCE = "goes"
# %%
if __name__ != "__mp_main__":  # Spawned readers must not construct notebook widgets.
    # The notebook and command-line entry use the same package functions.
    from IPython import get_ipython
    if __name__ == "__main__" and get_ipython() is None:
        import os
        from pathlib import Path
        os.environ.setdefault("ECORE_REPO_ROOT", str(Path(__file__).resolve().parents[1]))
        from ecore_weather.cli import main
        raise SystemExit(main(SOURCE))

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

# %%
if __name__ != "__mp_main__":  # Spawned readers must not construct notebook widgets.
    from dataclasses import replace
    from collections import Counter
    import pandas as pd
    import matplotlib.pyplot as plt
    import ipywidgets as widgets
    from IPython.display import display
    if get_ipython() is not None:
        get_ipython().run_line_magic("matplotlib", "inline")
    from ecore_weather import benchmark, catalog, diagnostics, storage, ui, validation, visualization
    from ecore_weather.common import PATCHES
    SMOKE = os.getenv("ECORE_NOTEBOOK_SMOKE") == "1"

# %%
if __name__ != "__mp_main__":  # Spawned readers must not construct notebook widgets.
    from ecore_weather import goes
    READER = goes
# %% [markdown]
# ## Choose files and save the selection
# Dates, region, products, storage, and worker count come from these controls.
# End dates are excluded. Workers default to half the detected CPUs; decoding
# is bounded separately. Creating controls does not start network work.
#
# The default durable destination is the HF bucket configured in `.env` or session
# environment variables. Enter a local path to choose local storage explicitly.
# STAC describes the selection using two JSON files in the run folder, rather
# than creating one directory per observation. The current selection replaces
# those two files; choose another run folder when you want to keep another selection.
#
# The product list offers full-disk imagery only. The CONUS sector (roughly
# 20°N–50°N, 125°W–65°W) does not cover Puerto Rico, and this project works in
# the Caribbean, so the CONUS products would fetch the wrong region here; they
# remain available from the command line for advanced use. A collapsed "Product
# and variable guide" below the controls describes both full-disk products and
# all sixteen ABI bands.

# %%
if __name__ != "__mp_main__":  # Spawned readers must not construct notebook widgets.
    controls = ui.selection_controls(SOURCE)
    display(controls["panel"])
    def choose_data():
        selection = READER.discover(**ui.read_controls(controls))
        display(selection.summary())
        if selection.source == "goes":
            display(READER.acquisition_coverage(selection))
        print("Saved selection:", catalog.save_selection(selection, controls["output"].value))
        return selection
    chosen = ui.action("Find and save selection", choose_data)

# %% [markdown]
# ## Fetch raw subsets
# Source values, coordinates, and missing-value information stay unchanged.
# Lossless Zarr storage replaces temporary source containers. Completed subsets
# are verified and reused; failures are reported without changing destinations.
# The run report keeps per-file outcomes and timings. Publishing includes the
# remote read-back check and is measured separately from source reads and writing.
#
# Each scan is fetched once and kept as one canonical Zarr copy. The ordinary
# reader transfers only the requested bands and region, so there is no need to
# download whole full-disk files to study Puerto Rico. The reference bundle in
# the last section points back to NOAA's files instead of copying them; the same
# pointer idea is what large projects use to make whole archives browsable.
# See docs/raw_data_rationale.md.

# %%
if __name__ != "__mp_main__":  # Spawned readers must not construct notebook widgets.
    def fetch_data():
        with ui.FetchProgress(len(chosen["value"].assets)) as progress:
            report = storage.fetch(chosen["value"], destination=controls["destination"].value,
                                   workers=controls["workers"].value, report_dir=controls["output"].value,
                                   layout=controls["layout"].value, container=controls["container"].value, read_processes=controls["read_processes"].value,
                                   scratch=controls["scratch"].value or None, progress=progress,
                                   inspect=lambda ds: diagnostics.describe(ds).to_dict("records"))
        successful = [r for r in report["records"] if r["status"] in ("saved", "reused")]
        controls["image_index"].max = max(0, len(successful)-1)
        display(dict(Counter(r["status"] for r in report["records"])))
        display(pd.DataFrame([r for r in report["records"] if r["status"] == "failed"]))
        return report
    fetched = ui.action("Fetch raw subsets", fetch_data)

# %% [markdown]
# ## View a fetched image
# Range reads select bands and a native rectangular window before loading.
# The satellite grid is curved relative to longitude/latitude, so that window
# encloses the requested region. Compressed chunks may contain additional pixels.
# Packed pixel values are not temperatures until scale and offset are applied.
# The quality view keeps DQF=0 pixels. Raw quality flags remain available.
# Use the checkbox to save the displayed figures as PNGs; it is off by default.

# %%
if __name__ != "__mp_main__":  # Spawned readers must not construct notebook widgets.
    display(controls["view_panel"])
    recipe = widgets.Dropdown(options=[("Decoded values", "decode"), ("Quality mask", "quality"),
        ("Puerto Rico grid", "reproject"), ("Raw only", None)], value="quality", description="View")
    display(recipe)
    def show_image():
        rows = [r for r in fetched["value"]["records"] if r["status"] in ("saved", "reused")]
        row = rows[controls["image_index"].value]
        with storage.open_raw(row["url"]) as raw:
            before = storage.fingerprint(raw)
            print("Source observation:", row["time"])
            display(diagnostics.describe(raw))
            files = visualization.show_or_save(raw, controls["figure_dir"].value if controls["save_figures"].value else None,
                                               recipe=recipe.value)
            assert storage.fingerprint(raw) == before
            if files:
                print("Saved figures:", files)
        return files
    image_view = ui.action("Display selected image", show_image)

# %% [markdown]
# ## Try a virtual dataset
# VirtualiZarr records where array pieces live in NOAA files. Kerchunk references
# are bundled in one local JSON file; they are pointers rather than another image
# copy and still require NOAA access. Different grids, calibration, or compression
# stay in separate groups. Start with six scans across the chosen period:
# midnight and noon on the first, middle, and final included days.

# %%
if __name__ != "__mp_main__":  # Spawned readers must not construct notebook widgets.
    def make_references():
        selection = chosen["value"]
        path = goes.build_virtual(selection, Path(controls["output"].value)/"references",
                                   assets=goes.benchmark_assets(selection))
        print("Reference bundle:", path)
        with goes.open_virtual(path) as virtual:
            display(virtual)
        return path
    references = ui.action("Build sample references", make_references)

# %% [markdown]
# ## Validate representative scans or measure reading methods
# Discovery covers the full period. Validation uses the six actual scans above
# and compares full-file, range, and virtual reads, plus raw-Zarr round trips.
# It keeps one compact result and removes temporary successful-test artifacts.
# A normal speed experiment retains its small reports and reference bundle.
# Reference-building time is separate from reopening and reading. The full-file
# baseline temporarily downloads several gigabytes. Timing is sensitive to caches
# and network conditions; value equality and returned bytes are checked separately.

# %%
if __name__ != "__mp_main__":  # Spawned readers must not construct notebook widgets.
    def validate_data():
        return validation.validate(chosen["value"], workers=controls["workers"].value,
                                   report_path=Path(controls["output"].value)/"validation.json")
    checks = ui.action("Validate representative scans", validate_data)
    def compare_data(repeats=1):
        table = benchmark.run_goes(chosen["value"], report_dir=controls["output"].value, repeats=repeats)
        display(table)
        return table
    comparison = ui.action("Compare reading methods", compare_data)

# %%
if __name__ != "__mp_main__":  # Spawned readers must not construct notebook widgets.
    if SMOKE:
        import tempfile
        selection = goes.discover("2025-09-01", "2025-09-01T01:00:00")
        chosen["value"] = replace(selection, assets=selection.assets[:1])
        with tempfile.TemporaryDirectory(prefix="ecore-notebook-") as local:
            controls["destination"].value = local+"/data"
            controls["output"].value = local+"/run"
            controls["figure_dir"].value = local+"/figures"
            controls["save_figures"].value = True
            controls["workers"].value = 1
            catalog.save_selection(chosen["value"], controls["output"].value)
            fetched["value"] = fetch_data()
            assert all(r["status"] == "saved" for r in fetched["value"]["records"])
            assert show_image()

# %% [markdown]
# [Notebook guide](../docs/notebooks.md) · [Developer guide](../docs/developer_guide.md)
# · [Validation results](../docs/validation.md)
