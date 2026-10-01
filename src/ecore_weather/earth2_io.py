"""Earth2Studio-compatible Zarr writing for native monthly xarray datasets."""

from __future__ import annotations

from collections import OrderedDict
import json
from pathlib import Path

import numpy as np

from .common import jsonable


def _auxiliary_metadata(dataset):
    """Return non-dimension coordinates that the Earth2 backend cannot index."""
    coordinates = {}
    for name, coordinate in dataset.coords.items():
        if name in dataset.dims:
            continue
        coordinates[name] = {
            "dims": list(coordinate.dims),
            "values": jsonable(np.asarray(coordinate.values).tolist()),
            "attrs": jsonable(coordinate.attrs),
        }
    return coordinates


def write_dataset(dataset, path, pool_size=1, shard_time=1):
    """Write a raw monthly dataset using Earth2Studio's standard ``ZarrBackend``.

    Each xarray data variable becomes a named Zarr array, matching the
    Earth2Studio IO convention. Native dimension coordinates and numeric pixel
    values are written through the backend. Small non-dimension provenance
    coordinates are kept in ``ecore_metadata.json`` beside the arrays and are
    restored by :func:`ecore_weather.storage.open_raw`.

    ``pool_size`` and ``shard_time`` remain accepted for caller compatibility;
    standard ``ZarrBackend`` is intentionally synchronous and does not shard.
    """
    del pool_size, shard_time
    from earth2studio.io import ZarrBackend
    from zarr.codecs import BloscCodec, BloscShuffle
    import torch
    import zarr

    if "time" not in dataset.coords or "time" not in dataset.dims:
        raise ValueError("Monthly Earth2Studio Zarr datasets require a time dimension.")
    times = np.asarray(dataset.time.values)
    arrays = {name: dataset[name] for name in dataset.data_vars
              if dataset[name].dtype.kind in "biufc" and "time" in dataset[name].dims}
    if not arrays:
        raise ValueError("No numeric time-dependent arrays were found.")

    dimension_coords = OrderedDict()
    for name in dataset.dims:
        coordinate = dataset.coords.get(name)
        if coordinate is None or coordinate.ndim != 1 or coordinate.dims != (name,):
            raise ValueError(f"Earth2Studio Zarr needs a one-dimensional coordinate for {name!r}.")
        dimension_coords[name] = np.asarray(coordinate.values)

    chunks = {name: 1 if name == "time" else min(256, len(values))
              for name, values in dimension_coords.items()}
    codec = BloscCodec(cname="zstd", clevel=3, shuffle=BloscShuffle.shuffle)
    io = ZarrBackend(str(path), chunks=chunks, backend_kwargs={"overwrite": True},
                     zarr_codecs=codec)
    try:
        for name, array in arrays.items():
            coords = OrderedDict((dim, dimension_coords[dim]) for dim in array.dims)
            # Earth2Studio 0.18 derives dtype from supplied data and forwards
            # extra kwargs to Zarr. Passing dtype here duplicates that argument.
            # Register coordinates through its backend, then replace the empty
            # default float32 array with the source dtype before writing chunks.
            io.add_array(coords, name)
            if io.root[name].dtype != array.dtype:
                del io.root[name]
                io.root.create_array(name, shape=tuple(len(dimension_coords[d]) for d in array.dims),
                    chunks=tuple(chunks[d] for d in array.dims), dtype=array.dtype,
                    dimension_names=list(array.dims), compressors=codec, fill_value=None)
        for index in range(len(times)):
            for name, array in arrays.items():
                ordered = array.transpose("time", *[dim for dim in array.dims if dim != "time"])
                coords = OrderedDict((dim, values[index:index + 1] if dim == "time" else values)
                                     for dim, values in
                                     ((dim, dimension_coords[dim]) for dim in ordered.dims))
                values = np.ascontiguousarray(ordered.isel(time=index).values)
                tensor = torch.from_numpy(np.expand_dims(values, axis=0))
                io.write(tensor, coords, name)
    finally:
        close = getattr(io, "close", None)
        if close:
            close()

    root = zarr.open_group(str(path), mode="a")
    root.attrs.update(jsonable(dataset.attrs))
    for name, array in arrays.items():
        root[name].attrs.update(jsonable(array.attrs))
    for name, coordinate in dataset.coords.items():
        if name in root:
            root[name].attrs.update(jsonable(coordinate.attrs))
    # ZarrBackend stores the dimension and variable arrays. This sidecar keeps
    # per-observation IDs, source URLs, time offsets, and source metadata without
    # coercing strings into scientific pixel arrays.
    metadata = {
        "dataset_attrs": jsonable(dataset.attrs),
        "variable_attrs": {name: jsonable(array.attrs) for name, array in dataset.data_vars.items()},
        "auxiliary_variables": {
            name: {"dims": list(array.dims), "dtype": array.dtype.str,
                   "values": jsonable(np.asarray(array.values).tolist())}
            for name, array in dataset.data_vars.items() if name not in arrays
        },
        "coordinate_attrs": {name: jsonable(coord.attrs) for name, coord in dataset.coords.items()},
        "auxiliary_coords": _auxiliary_metadata(dataset),
    }
    Path(path, "ecore_metadata.json").write_text(json.dumps(metadata, sort_keys=True))
    zarr.consolidate_metadata(str(path))
    return path


def hf_store(bucket_id, prefix=""):
    """Create an obstore S3 store for an HF bucket without touching global AWS auth."""
    from .hf_storage import s3_configuration
    from obstore.store import S3Store

    config = s3_configuration(bucket_id)
    return S3Store(config["bucket"], prefix=prefix,
        endpoint=config["endpoint_url"], region=config["region_name"],
        access_key_id=config["aws_access_key_id"],
        secret_access_key=config["aws_secret_access_key"],
        virtual_hosted_style_request=False)
