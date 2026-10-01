"""Bounded local monthly writer using Earth2Studio's ZarrBackend."""

from __future__ import annotations

from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import zipfile

import numpy as np

from .common import Transport, iso, jsonable, utc, write_json
from .storage import open_raw, object_writer


# Keep the queue finite even if callers request a very large download pool.
# The study job's default of 16 therefore allows 16 NOAA read/decode tasks per
# monthly archive; writer-process and GRIB-decode limits remain independent.
MAX_SOURCE_READ_WORKERS = 16


def source_read_workers(workers):
    """Return the bounded per-archive NOAA read concurrency."""
    return max(1, min(int(workers), MAX_SOURCE_READ_WORKERS))


def _science(ds, band):
    if band is None:
        return ds[["measurement", "bitmap_valid"]]
    names = [name for name in ds.data_vars if name == "CMI" or name == "DQF"
             or name in (f"CMI_C{band:02d}", f"DQF_C{band:02d}")]
    renamed = ds[names].rename({name: f"{name}_C{band:02d}" for name in names
                                if name in ("CMI", "DQF")})
    if not any(name.startswith("CMI") for name in renamed):
        raise ValueError(f"GOES source has no C{band:02d} image")
    return renamed


def _source_metadata(ds):
    return json.dumps({"dataset": jsonable(ds.attrs),
        "variables": {name: {"attrs": jsonable(ds[name].attrs),
            "dims": list(ds[name].dims), "dtype": ds[name].dtype.str,
            "values": jsonable(np.asarray(ds[name].values).tolist())
                      if ds[name].ndim <= 1 and name not in ("x", "y", "latitude", "longitude")
                      else None}
            for name in ds.variables}}, sort_keys=True, default=str)


def _read_new(asset, selection, band, transport, block_size):
    from .earth2_sources import read_selected_asset
    dataset, timing = read_selected_asset(asset, selection.source, selection.bbox,
        selection.product, (band,) if band else selection.bands, transport, block_size)
    return dataset, timing


def _archive_rows(selection, assets, prior):
    matches = {row["asset_id"]: row for row in selection.hourly_matches}
    rows = [{**row, "kind": "old", "old_index": index}
            for index, row in enumerate(prior.get("assets", []))]
    done = {row["asset_id"] for row in rows}
    for asset in assets:
        if asset.id in done:
            continue
        match = matches.get(asset.id, {})
        rows.append({"kind": "new", "asset": asset, "asset_id": asset.id,
            "time": asset.time, "source_url": asset.url, "etag": asset.etag,
            "source_bytes": asset.size, "slot_time": match.get("slot_time") or "",
            "offset_seconds": match.get("offset_seconds")})
    rows.sort(key=lambda row: (utc(row["time"]), row["asset_id"]))
    return rows


def _write_one(selection, assets, target, band, workers=16, backend="obstore",
               decode_workers=1, block_size=1024*1024, scratch=None):
    """Build one archive; no source observation is held past its Zarr write."""
    from earth2studio.io import ZarrBackend
    from zarr.codecs import BloscCodec, BloscShuffle
    import torch
    import zarr

    target = Path(target)
    with object_writer(target):
        marker_path = target / "complete.json"
        backup = target / ".raw.zarr.zip.backup"
        if backup.is_file():
            if not marker_path.is_file():
                raise IOError(f"Unmarked backup needs inspection: {backup}")
            # A crash between replacing the ZIP and writing the new marker
            # leaves the prior verified archive here. Restore that version.
            os.replace(backup, target / "raw.zarr.zip")
        prior = json.loads(marker_path.read_text()) if marker_path.is_file() else {}
        rows = _archive_rows(selection, assets, prior)
        if rows and all(row["kind"] == "old" for row in rows):
            return {"path": str(target / "raw.zarr.zip"), "status": "reused",
                    "observations": len(rows), "stored_bytes": prior.get("stored_bytes", 0),
                    "read_bytes": 0, "write_seconds": 0, "assets": prior.get("assets", [])}
        if not rows:
            raise ValueError("No available source observations for this month")
        if prior and not (target / "raw.zarr.zip").is_file():
            raise IOError(f"Completion marker has no archive: {target}")

        started = time.perf_counter()
        parent = Path(tempfile.mkdtemp(prefix="ecore-month-", dir=scratch))
        directory, packed = parent / "raw.zarr", parent / "raw.zarr.zip"
        checksums = []
        observed_metadata = []
        actual_rows = []
        old = None
        io = None
        codec = BloscCodec(cname="zstd", clevel=3, shuffle=BloscShuffle.shuffle)
        read_bytes = 0
        try:
            with ExitStack() as stack:
                if prior:
                    old = stack.enter_context(open_raw(target / "raw.zarr.zip"))
                transport = stack.enter_context(Transport(backend, decode_workers))
                read_workers = source_read_workers(workers)
                pool = stack.enter_context(ThreadPoolExecutor(max_workers=read_workers))
                # NOAA reads overlap the serial archive writer. Downloads and
                # completed datasets in this queue are bounded independently
                # from the per-process decode semaphore in Transport.
                queue = deque()
                iterator = iter(rows)

                def enqueue():
                    try:
                        row = next(iterator)
                    except StopIteration:
                        return False
                    future = (pool.submit(_read_new, row["asset"], selection, band,
                        transport, block_size) if row["kind"] == "new" else None)
                    queue.append((row, future))
                    return True

                for _ in range(min(len(rows), read_workers)):
                    enqueue()
                dimensions = None
                arrays = None
                for index in range(len(rows)):
                    row, future = queue.popleft()
                    enqueue()
                    if future is None:
                        ds = old.isel(time=row["old_index"], drop=True).load()
                        raw_meta = str(old.source_metadata_json.values[row["old_index"]])
                        source = _science(ds, band)
                    else:
                        try:
                            ds, _ = future.result()
                        except Exception as exc:
                            raise OSError(f"Could not read {selection.source.upper()} source {row['asset'].key} "
                                f"at {row['asset'].time}: {type(exc).__name__}: {exc}") from exc
                        raw_meta = _source_metadata(ds)
                        source = _science(ds, band)
                    if dimensions is None:
                        dimensions = OrderedDict((name, np.asarray(source.coords[name].values))
                            for name in source.dims)
                        arrays = {name: source[name] for name in source.data_vars}
                        times = np.asarray([np.datetime64(utc(r["time"]).replace(tzinfo=None), "ns")
                                            for r in rows])
                        chunks = {"time": 1, **{name: min(256, len(values))
                            for name, values in dimensions.items()}}
                        io = ZarrBackend(str(directory), chunks=chunks,
                            backend_kwargs={"overwrite": True}, zarr_codecs=codec)
                        for name, array in arrays.items():
                            coords = OrderedDict([("time", times),
                                *((dim, dimensions[dim]) for dim in array.dims)])
                            io.add_array(coords, name)
                            if io.root[name].dtype != array.dtype:
                                del io.root[name]
                                io.root.create_array(name, shape=(len(times), *array.shape),
                                    chunks=(1, *(chunks[dim] for dim in array.dims)),
                                    dtype=array.dtype, dimension_names=["time", *array.dims],
                                    compressors=codec, fill_value=None)
                        io.root.attrs.update({"source": selection.source,
                            "product": selection.product, "satellite": selection.satellite,
                            "requested_bbox": list(selection.bbox),
                            "preservation": "Native measurements; per-observation metadata in source_metadata_json"})
                    for name, values in dimensions.items():
                        if not np.array_equal(source.coords[name].values, values):
                            raise ValueError(f"Native {name} grid changed within monthly archive")
                    for name, template in arrays.items():
                        if name not in source or source[name].dims != template.dims or source[name].dtype != template.dtype:
                            raise ValueError(f"Native {name} layout changed within monthly archive")
                        value = np.ascontiguousarray(source[name].values)
                        coords = OrderedDict([("time", times[index:index+1]),
                            *((dim, dimensions[dim]) for dim in template.dims)])
                        io.write(torch.from_numpy(value[np.newaxis]), coords, name)
                        checksums.append((index, name, hashlib.sha256(value.tobytes()).hexdigest()))
                    observed_metadata.append(raw_meta)
                    actual_rows.append({key: row.get(key) for key in
                        ("asset_id", "time", "source_url", "etag", "source_bytes",
                         "slot_time", "offset_seconds")})
                close = getattr(io, "close", None)
                if close:
                    close()
                io = None
                root = zarr.open_group(str(directory), mode="a")
                for name, array in arrays.items():
                    root[name].attrs.update(jsonable(array.attrs))
                for name, values in dimensions.items():
                    root[name].attrs.update(jsonable(source.coords[name].attrs))
                auxiliary = {
                    "source_asset_id": [r["asset_id"] for r in actual_rows],
                    "source_url": [r["source_url"] for r in actual_rows],
                    "source_etag": [r["etag"] for r in actual_rows],
                    "request_slot_time": [r["slot_time"] or "" for r in actual_rows],
                    "request_offset_seconds": [r["offset_seconds"] if r["offset_seconds"] is not None
                                               else float("nan") for r in actual_rows],
                    "source_metadata_json": observed_metadata,
                }
                metadata = {"dataset_attrs": dict(root.attrs),
                    "variable_attrs": {name: jsonable(array.attrs) for name, array in arrays.items()},
                    "coordinate_attrs": {name: jsonable(source.coords[name].attrs)
                        for name in dimensions},
                    "auxiliary_variables": {},
                    "auxiliary_coords": {name: {"dims": ["time"], "values": values, "attrs": {}}
                        for name, values in auxiliary.items()}}
                (directory / "ecore_metadata.json").write_text(json.dumps(metadata, sort_keys=True))
                zarr.consolidate_metadata(str(directory))
                with zipfile.ZipFile(packed, "w", compression=zipfile.ZIP_STORED,
                                     allowZip64=True) as archive:
                    for file in sorted(directory.rglob("*")):
                        if file.is_file():
                            archive.write(file, file.relative_to(directory).as_posix())
                with open_raw(packed) as verified:
                    if len(verified.time) != len(rows):
                        raise IOError("Monthly archive has the wrong observation count")
                    for index, name, expected in checksums:
                        value = np.ascontiguousarray(verified[name].isel(time=index).values)
                        if hashlib.sha256(value.tobytes()).hexdigest() != expected:
                            raise IOError(f"Monthly read-back differs at {index} {name}")
                    if verified.source_metadata_json.values.tolist() != observed_metadata:
                        raise IOError("Monthly source metadata changed in read-back")
                read_bytes = transport.bytes
            target.mkdir(parents=True, exist_ok=True)
            staged = target / ".raw.zarr.zip.tmp"
            shutil.copyfile(packed, staged)
            marker = {"raw_path": "raw.zarr.zip", "source": selection.source,
                "product": selection.product, "satellite": selection.satellite,
                "band": band, "region": list(selection.bbox),
                "asset_ids": [r["asset_id"] for r in actual_rows],
                "assets": actual_rows, "stored_bytes": packed.stat().st_size,
                "observations": len(actual_rows), "writer_backend": "earth2studio-zarr-backend",
                "updated_at": iso(datetime.now(timezone.utc))}
            digest = hashlib.sha256()
            with packed.open("rb") as stream:
                for block in iter(lambda: stream.read(8*1024*1024), b""):
                    digest.update(block)
            marker["archive_sha256"] = digest.hexdigest()
            old_archive = target / "raw.zarr.zip"
            if old_archive.is_file() and prior:
                os.replace(old_archive, backup)
            try:
                os.replace(staged, old_archive)
                write_json(marker_path, marker)
            except Exception:
                if backup.is_file():
                    os.replace(backup, old_archive)
                raise
            if backup.is_file():
                backup.unlink()
            return {"path": str(target / "raw.zarr.zip"), "status": "saved",
                "observations": len(rows), "stored_bytes": marker["stored_bytes"],
                "read_bytes": read_bytes, "write_seconds": time.perf_counter()-started,
                "assets": actual_rows}
        finally:
            if io is not None and hasattr(io, "close"):
                io.close()
            shutil.rmtree(parent, ignore_errors=True)
