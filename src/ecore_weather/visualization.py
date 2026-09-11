"""Shared notebook/script figure display and optional PNG export."""

from pathlib import Path
import matplotlib.pyplot as plt

from . import diagnostics, goes, mrms


def show_or_save(ds, directory=None, display=True, variable=None, recipe=None, center_crop=False):
    figures = [("raw-and-quality", diagnostics.plot_maps(ds, variable))]
    if "measurement" in ds:
        from .maps import plot_geographic
        figures.append(("rainfall-map", plot_geographic(ds, hide_zero=True)))
    if recipe:
        if recipe == "compare" and "measurement" in ds:
            fig, axes = plt.subplots(1, 2, figsize=(12, 4), constrained_layout=True)
            for choice, label, ax in zip(("legacy_exact", "quality_aware"),
                                         ("Mentor's processing", "Mask missing values first"), axes):
                view = mrms.process(ds, choice, bbox=tuple(ds.attrs.get("requested_bbox", mrms.PR_BBOX)))
                if center_crop:
                    view = mrms.centered_crop(view)
                view.plot(ax=ax)
                ax.set_title(label)
            figures.append(("processing-comparison", fig))
            recipe = None
    if recipe:
        if "measurement" in ds:
            view = mrms.process(ds, recipe=recipe, bbox=tuple(ds.attrs.get("requested_bbox", mrms.PR_BBOX)))
            if center_crop:
                view = mrms.centered_crop(view)
        else:
            variable = variable or goes.science_variables(ds)[0]
            view = goes.process(ds, variable, reproject=recipe == "reproject", mask_quality=recipe != "decode",
                                bbox=tuple(ds.attrs.get("requested_bbox", goes.PR_BBOX)))
        fig, ax = plt.subplots(figsize=(7, 5), constrained_layout=True)
        view.plot(ax=ax)
        ax.set_title(recipe.replace("_", " "))
        figures.append((recipe, fig))
    saved = []
    stamp = str(ds.attrs.get("observation_time", "sample")).replace(":", "-")
    try:
        for label, fig in figures:
            if directory:
                path = Path(directory); path.mkdir(parents=True, exist_ok=True)
                destination = path / f"{stamp}-{label}.png"
                fig.savefig(destination, dpi=150)
                saved.append(str(destination))
            if display:
                from IPython.display import display as show
                show(fig)
    finally:
        for _, fig in figures:
            plt.close(fig)
    return saved
