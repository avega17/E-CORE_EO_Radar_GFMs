"""Small, isolated Earth2Studio async Zarr round-trip on the HF S3 gateway.

Run with ``PYTHONPATH=src python scripts/probe_hf_async_zarr.py``. The script
creates a unique temporary prefix, verifies two arrays, and deletes only that
prefix. It never prints credentials or the bucket identity.
"""

from collections import OrderedDict
import json
import time
from uuid import uuid4

import numpy as np
import torch
import zarr
from zarr.codecs import BloscCodec
from earth2studio.io import AsyncZarrBackend
from obstore.store import from_url

from ecore_weather.hf_storage import s3_client, s3_configuration
from ecore_weather.storage import configured_bucket


def main():
    bucket_id = configured_bucket()
    config = s3_configuration(bucket_id)
    client = s3_client(bucket_id)
    prefix = f"noaa-subsets/_smoke_async/{uuid4().hex}/raw.zarr"
    url = f"s3://{config['bucket']}/{prefix}"
    kwargs = {
        "endpoint": config["endpoint_url"],
        "region": config["region_name"],
        "access_key_id": config["aws_access_key_id"],
        "secret_access_key": config["aws_secret_access_key"],
        "virtual_hosted_style_request": False,
    }
    coords = OrderedDict(
        time=np.array(["2025-01-01T00:00:00", "2025-01-01T00:10:00"], dtype="datetime64[s]"),
        y=np.array([0, 1], dtype=np.int32),
        x=np.array([0, 1, 2], dtype=np.int32),
    )
    values = np.arange(12, dtype=np.int16).reshape(2, 2, 3)
    flags = np.array([[[0, 1, 0], [0, 0, 2]], [[1, 0, 0], [0, 2, 0]]], dtype=np.uint8)
    started = time.perf_counter()
    try:
        io = AsyncZarrBackend(
            None, parallel_coords=OrderedDict(time=coords["time"]),
            store=url, store_kwargs=kwargs, blocking=False, pool_size=1,
            zarr_codecs=BloscCodec(cname="zstd", clevel=3, shuffle="shuffle"),
        )
        try:
            io.add_array(coords, "measurement", dtype=np.int16)
            io.add_array(coords, "quality", dtype=np.uint8)
            for index in range(2):
                selection = OrderedDict((name, value[index:index + 1] if name == "time" else value)
                                        for name, value in coords.items())
                io.write(torch.from_numpy(values[index:index + 1].copy()), selection, "measurement")
                io.write(torch.from_numpy(flags[index:index + 1].copy()), selection, "quality")
        finally:
            io.close()
        group = zarr.open_group(
            store=zarr.storage.ObjectStore(from_url(url, **kwargs)), mode="r", use_consolidated=False
        )
        np.testing.assert_array_equal(group["measurement"][:], values)
        np.testing.assert_array_equal(group["quality"][:], flags)
        np.testing.assert_array_equal(group["time"][:], coords["time"])
        if group["measurement"].dtype != values.dtype or group["quality"].dtype != flags.dtype:
            raise AssertionError("HF Zarr array dtype changed")
        response = client.list_objects_v2(Bucket=config["bucket"], Prefix=prefix)
        objects = response.get("Contents", [])
        print(json.dumps({"result": "passed", "objects": len(objects),
                          "bytes": sum(item["Size"] for item in objects),
                          "seconds": round(time.perf_counter() - started, 2)}))
    finally:
        continuation = None
        while True:
            args = {"Bucket": config["bucket"], "Prefix": prefix}
            if continuation:
                args["ContinuationToken"] = continuation
            page = client.list_objects_v2(**args)
            keys = [{"Key": item["Key"]} for item in page.get("Contents", [])]
            if keys:
                client.delete_objects(Bucket=config["bucket"], Delete={"Objects": keys, "Quiet": True})
            continuation = page.get("NextContinuationToken")
            if not continuation:
                break


if __name__ == "__main__":
    main()
