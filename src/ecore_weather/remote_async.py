"""Direct, versioned monthly Zarr writing through Earth2Studio and HF S3."""

from __future__ import annotations

from collections import OrderedDict
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
from itertools import islice
import json
from pathlib import Path
import time
from uuid import uuid4

import numpy as np

from .common import Transport, iso, jsonable, utc
from .earth2_io import hf_store
from .hf_storage import BucketWriter, bucket_writer
from .monthly_stream import _archive_rows, _read_new, _science, _source_metadata
from .storage import open_raw, valid_raw_name


def _objects(writer, prefix):
    """List only objects in a writer-owned Zarr prefix."""
    token = None
    key_prefix = writer.key(prefix.rstrip("/")) + ("/" if prefix.endswith("/") else "")
    while True:
        args = {"Bucket": writer.config["bucket"], "Prefix": key_prefix}
        if token:
            args["ContinuationToken"] = token
        page = writer.client.list_objects_v2(**args)
        yield from page.get("Contents", [])
        token = page.get("NextContinuationToken")
        if not token:
            break


def _archive_exists(writer, relative):
    key = relative.rstrip("/") + ("/zarr.json" if relative.endswith(".zarr") else "")
    try:
        writer.client.head_object(Bucket=writer.config["bucket"], Key=writer.key(key))
        return True
    except Exception as error:
        code = getattr(error, "response", {}).get("Error", {}).get("Code")
        if code in {"NoSuchKey", "404", "NotFound"}:
            return False
        raise


def _delete_owned(writer, relative):
    """Remove an explicitly named archive, never a product or month prefix."""
    name = Path(relative).name
    if name == "raw.zarr.zip":
        writer.client.delete_object(Bucket=writer.config["bucket"], Key=writer.key(relative))
        return
    if not valid_raw_name(name):
        raise ValueError(f"Refusing to delete an unrecognized archive: {name}")
    prefix = relative.rstrip("/") + "/"
    while True:
        batch = [{"Key": item["Key"]} for item in islice(_objects(writer, prefix), 1000)]
        if not batch:
            break
        writer.client.delete_objects(Bucket=writer.config["bucket"],
            Delete={"Objects": batch, "Quiet": True})


def _cleanup_old_versions(writer, relative, active):
    """Prune only writer-owned inactive archive versions in one completed month."""
    prefix = writer.key(relative.rstrip("/")) + "/"
    token = None
    candidates = set()
    while True:
        args = {"Bucket": writer.config["bucket"], "Prefix": prefix,
                "Delimiter": "/"}
        if token:
            args["ContinuationToken"] = token
        page = writer.client.list_objects_v2(**args)
        for group in page.get("CommonPrefixes", []):
            name = group["Prefix"].removeprefix(prefix).rstrip("/")
            if valid_raw_name(name) and name.endswith(".zarr") and name != active:
                candidates.add(name)
        for item in page.get("Contents", []):
            name = item["Key"].removeprefix(prefix)
            if name == "raw.zarr.zip" and name != active:
                candidates.add(name)
        token = page.get("NextContinuationToken")
        if not token:
            break
    for name in sorted(candidates):
        _delete_owned(writer, relative.rstrip("/") + "/" + name)
    return len(candidates)


def _publish_rows(writer, relative, rows, read_row, source, product, satellite,
                  region, band, prior=None, pool_size=1, shard_time=72,
                  progress=None):
    """Build a new remote version, verify every array, then point completion at it."""
    from earth2studio.io import AsyncZarrBackend
    import torch
    import zarr
    from zarr.codecs import BloscCodec

    if not rows:
        raise ValueError("A monthly archive needs at least one source observation")
    name = f"raw-{uuid4().hex}.zarr"
    archive_relative = f"{relative.rstrip('/')}/{name}"
    remote_url = f"hf://buckets/{writer.bucket_id}/{writer.key(archive_relative)}"
    store = hf_store(writer.bucket_id, writer.key(archive_relative))
    times = np.asarray([np.datetime64(utc(row["time"]).replace(tzinfo=None), "ns")
                        for row in rows])
    codec = BloscCodec(cname="zstd", clevel=3, shuffle="shuffle")
    io = None
    expected = []
    metadata_values = []
    provenance = []
    pending = {}
    batch_start = 0
    batch_size = min(max(1, int(shard_time)), len(rows))
    variables = dimensions = dimension_attrs = None
    dataset_attrs = None
    write_start = time.perf_counter()
    with bucket_writer(writer.bucket_id, scope=relative):
        try:
            for index, row in enumerate(rows):
                source_ds, raw_metadata = read_row(row)
                if dimensions is None:
                    dimensions = OrderedDict((dim, np.asarray(source_ds.coords[dim].values))
                        for dim in source_ds.dims)
                    dimension_attrs = {dim: jsonable(source_ds.coords[dim].attrs)
                        for dim in dimensions}
                    variables = {key: {"dims": tuple(value.dims), "dtype": value.dtype,
                        "attrs": jsonable(value.attrs)} for key, value in source_ds.data_vars.items()}
                    dataset_attrs = {"source": source, "product": product,
                        "satellite": satellite, "requested_bbox": list(region),
                        "preservation": "Native measurements; per-observation metadata in source_metadata_json"}
                    coords_by_array = {
                        key: OrderedDict([("time", times),
                            *((dim, dimensions[dim]) for dim in spec["dims"])])
                        for key, spec in variables.items()}
                    io = AsyncZarrBackend(None,
                        parallel_coords=OrderedDict(time=times), store=store,
                        blocking=False, pool_size=max(1, min(2, int(pool_size))),
                        shard_coords={"time": min(max(1, int(shard_time)), len(rows))},
                        max_inflight_shards=2, zarr_codecs=codec)
                    for key, spec in variables.items():
                        io.add_array(coords_by_array[key], key, dtype=spec["dtype"])
                for dim, values in dimensions.items():
                    if not np.array_equal(source_ds.coords[dim].values, values):
                        raise ValueError(f"Native {dim} grid changed within the remote month")
                for key, spec in variables.items():
                    if key not in source_ds or source_ds[key].dims != spec["dims"] or source_ds[key].dtype != spec["dtype"]:
                        raise ValueError(f"Native {key} layout changed within the remote month")
                    values = np.ascontiguousarray(source_ds[key].values)
                    pending.setdefault(key, []).append(values)
                    expected.append((index, key, hashlib.sha256(values.tobytes()).digest()))
                metadata_values.append(raw_metadata)
                provenance.append({key: row.get(key) for key in
                    ("asset_id", "time", "source_url", "etag", "source_bytes",
                     "slot_time", "offset_seconds")})
                if len(pending[next(iter(variables))]) == batch_size or index + 1 == len(rows):
                    groups = {}
                    for key, spec in variables.items():
                        groups.setdefault(spec["dims"], []).append(key)
                    for dims, names in groups.items():
                        coords = OrderedDict([("time", times[batch_start:index+1]),
                            *((dim, dimensions[dim]) for dim in dims)])
                        tensors = [torch.from_numpy(np.stack(pending[key])) for key in names]
                        io.write(tensors, coords, names)
                    pending.clear()
                    batch_start = index + 1
                if progress and ((index + 1) % max(1, len(rows) // 20) == 0 or index + 1 == len(rows)):
                    progress(index + 1, len(rows))
            io.close()
            io = None
            root = zarr.open_group(store=zarr.storage.ObjectStore(store), mode="a",
                                   use_consolidated=False)
            root.attrs.update(dataset_attrs)
            for key, spec in variables.items():
                root[key].attrs.update(spec["attrs"])
            for dim, attrs in dimension_attrs.items():
                root[dim].attrs.update(attrs)
            auxiliary = {
                "source_asset_id": [row["asset_id"] for row in provenance],
                "source_url": [row["source_url"] for row in provenance],
                "source_etag": [row["etag"] for row in provenance],
                "request_slot_time": [row["slot_time"] or "" for row in provenance],
                "request_offset_seconds": [row["offset_seconds"] if row["offset_seconds"] is not None
                                           else float("nan") for row in provenance],
                "source_metadata_json": metadata_values,
            }
            sidecar = {"dataset_attrs": dataset_attrs,
                "variable_attrs": {key: spec["attrs"] for key, spec in variables.items()},
                "coordinate_attrs": dimension_attrs, "auxiliary_variables": {},
                "auxiliary_coords": {key: {"dims": ["time"], "values": values, "attrs": {}}
                    for key, values in auxiliary.items()}}
            writer.client.put_object(Bucket=writer.config["bucket"],
                Key=writer.key(archive_relative + "/ecore_metadata.json"),
                Body=json.dumps(sidecar, sort_keys=True).encode(), ContentType="application/json")
            zarr.consolidate_metadata(zarr.storage.ObjectStore(store))
            write_seconds = time.perf_counter() - write_start

            expected_by_array = {}
            for index, key, digest in expected:
                expected_by_array.setdefault(key, {})[index] = digest
            with open_raw(remote_url) as verified:
                if verified.sizes.get("time") != len(rows):
                    raise IOError("Remote Earth2Studio month has the wrong observation count")
                block_size = min(max(1, int(shard_time)), len(rows))
                for key, digests in expected_by_array.items():
                    for start in range(0, len(rows), block_size):
                        end = min(start + block_size, len(rows))
                        block = np.asarray(verified[key].isel(time=slice(start, end)).values)
                        for offset, actual in enumerate(block):
                            index = start + offset
                            if hashlib.sha256(np.ascontiguousarray(actual).tobytes()).digest() != digests[index]:
                                raise IOError(f"Remote Earth2Studio read-back differs at {index} {key}")
                if verified.source_metadata_json.values.tolist() != metadata_values:
                    raise IOError("Remote source metadata changed on read-back")
                for dim, values in dimensions.items():
                    np.testing.assert_array_equal(verified.coords[dim].values, values)
            items = list(_objects(writer, archive_relative + "/"))
            size = sum(item["Size"] for item in items)
            marker = {"raw_path": name, "source": source, "product": product,
                "satellite": satellite, "band": band, "region": list(region),
                "asset_ids": [row["asset_id"] for row in provenance],
                "assets": provenance, "stored_bytes": size,
                "observations": len(rows), "writer_backend": "earth2studio-async-zarr-backend",
                "hf_object_count": len(items), "hf_upload_seconds": write_seconds,
                "updated_at": iso(datetime.now(timezone.utc))}
            writer.put_json(relative + "/complete.json", marker)
        except Exception:
            if io is not None:
                io.close()
            try:
                _delete_owned(writer, archive_relative)
            except Exception:
                # An unmarked version is safe to audit or remove later.
                pass
            raise
        cleanup_pending = False
        try:
            _cleanup_old_versions(writer, relative, name)
        except Exception:
            cleanup_pending = True
    return {"status": "saved", "path": remote_url, "stored_bytes": size,
        "observations": len(rows), "read_bytes": 0, "hf_upload_seconds": write_seconds,
        "hf_upload_bytes": size, "hf_object_count": len(items),
        "old_version_cleanup_pending": cleanup_pending}


def write_selection_month(selection, assets, root, relative, band, workers=4,
                          backend="obstore", decode_workers=1, block_size=1024*1024):
    """Fetch a NOAA product-month directly into one verified remote Zarr."""
    writer = BucketWriter(root)
    marker = writer.marker(relative)
    if marker and not marker.get("assets"):
        raise ValueError(f"Existing HF month lacks merge provenance: {relative}")
    existing = set(marker.get("asset_ids", [])) if marker else set()
    if marker and all(asset.id in existing for asset in assets):
        raw_path = marker["raw_path"]
        if not _archive_exists(writer, relative + "/" + raw_path):
            raise IOError(f"HF completion marker has no archive: {relative}")
        with bucket_writer(writer.bucket_id, scope=relative):
            cleanup_count = _cleanup_old_versions(writer, relative, raw_path)
        return {"status": "reused", "path": root + "/" + relative + "/" + raw_path,
            "stored_bytes": marker["stored_bytes"], "observations": marker["observations"],
            "read_bytes": 0, "hf_upload_seconds": 0, "hf_upload_bytes": 0,
            "hf_object_count": marker.get("hf_object_count", 0),
            "old_versions_removed": cleanup_count}
    rows = _archive_rows(selection, assets, marker or {})
    old_url = root + "/" + relative + "/" + marker["raw_path"] if marker else None
    with ExitStack() as stack:
        old = stack.enter_context(open_raw(old_url)) if old_url else None
        transport = stack.enter_context(Transport(backend, decode_workers))

        def read_row(row):
            if row["kind"] == "old":
                index = row["old_index"]
                dataset = old.isel(time=index, drop=True).load()
                return _science(dataset, band), str(old.source_metadata_json.values[index])
            dataset, _ = _read_new(row["asset"], selection, band, transport, block_size)
            return _science(dataset, band), _source_metadata(dataset)

        outcome = _publish_rows(writer, relative, rows, read_row, selection.source,
            selection.product, selection.satellite, selection.bbox, band, prior=marker)
        outcome["read_bytes"] = transport.bytes
        return outcome


def mirror_local_month(local_archive, local_marker, root, relative, progress=None):
    """Mirror an already verified local month without refetching NOAA objects."""
    writer = BucketWriter(root)
    with open_raw(local_archive) as local:
        marker = writer.marker(relative)
        source_ids = set(local_marker.get("asset_ids", []))
        if marker and source_ids == set(marker.get("asset_ids", [])):
            raw_path = marker["raw_path"]
            if _archive_exists(writer, relative + "/" + raw_path):
                with bucket_writer(writer.bucket_id, scope=relative):
                    cleanup_count = _cleanup_old_versions(writer, relative, raw_path)
                return {"status": "reused", "path": root + "/" + relative + "/" + raw_path,
                    "stored_bytes": marker["stored_bytes"], "observations": marker["observations"],
                    "old_versions_removed": cleanup_count}
        rows = [{**row, "local_index": index} for index, row in enumerate(local_marker["assets"])]
        rows.sort(key=lambda row: (utc(row["time"]), row["asset_id"]))
        band = local_marker.get("band")

        def read_row(row):
            index = row["local_index"]
            dataset = local.isel(time=index, drop=True).load()
            return _science(dataset, band), str(local.source_metadata_json.values[index])

        return _publish_rows(writer, relative, rows, read_row,
            local_marker["source"], local_marker["product"],
            local_marker.get("satellite"), local_marker["region"], band,
            prior=marker, progress=progress)
