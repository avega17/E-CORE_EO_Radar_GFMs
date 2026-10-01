"""Disposable month-level Earth2Studio HF S3 round-trip for the current writer."""

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
from uuid import uuid4

import numpy as np
import xarray as xr

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ecore_weather.common import iso
from ecore_weather.earth2_io import write_dataset
from ecore_weather.hf_storage import BucketWriter
from ecore_weather.remote_async import mirror_local_month
from ecore_weather.storage import configured_bucket, open_raw, pack_raw
from ecore_weather.earth2_sources import MonthlyZarrSource
from ecore_weather.view_index import inventory


def main():
    bucket = configured_bucket()
    nonce = uuid4().hex
    root = f"hf://buckets/{bucket}/noaa-subsets/_smoke_monthly/{nonce}"
    relative = "mrms/PrecipRate_00.00/roi-probe/2021/01"
    writer = BucketWriter(root)
    try:
        with tempfile.TemporaryDirectory(prefix="ecore-monthly-async-probe-") as temp:
            folder = Path(temp)
            times = np.array(["2021-01-01T00:00:00", "2021-01-01T00:10:00"], dtype="datetime64[ns]")
            values = np.array([[[0., -1.], [2., 3.]], [[4., 5.], [6., 7.]]], dtype="float32")
            bitmap = np.array([[[1, 0], [1, 1]], [[1, 1], [0, 1]]], dtype="uint8")
            ids = ["probe-a", "probe-b"]
            metadata = [json.dumps({"source": identifier}) for identifier in ids]
            dataset = xr.Dataset({
                "measurement": (("time", "latitude", "longitude"), values),
                "bitmap_valid": (("time", "latitude", "longitude"), bitmap),
            }, coords={"time": times, "latitude": np.array([18., 17.]),
                "longitude": np.array([-67., -66.]),
                "source_metadata_json": ("time", metadata)})
            archive_dir = folder / "raw.zarr"
            write_dataset(dataset, archive_dir)
            archive = pack_raw(folder)
            rows = [{"asset_id": identifier, "time": iso(time.astype("datetime64[us]").astype(object)),
                     "source_url": "s3://noaa-mrms-pds/probe", "etag": identifier,
                     "source_bytes": 100, "slot_time": "", "offset_seconds": 0.0}
                    for identifier, time in zip(ids, times)]
            marker = {"raw_path": "raw.zarr.zip", "source": "mrms",
                "product": "PrecipRate_00.00", "satellite": None,
                "region": [-70.24, 14.36, -62.56, 22.04],
                "band": None, "assets": rows, "asset_ids": ids}
            first = mirror_local_month(archive, marker, root, relative)
            again = mirror_local_month(archive, marker, root, relative)
            if again["status"] != "reused":
                raise AssertionError("Remote month was duplicated on repeat")
            with open_raw(first["path"]) as saved:
                np.testing.assert_array_equal(saved.measurement.values, values)
                np.testing.assert_array_equal(saved.bitmap_valid.values, bitmap)
                np.testing.assert_array_equal(saved.source_metadata_json.values, metadata)
            source = MonthlyZarrSource(first["path"], source="mrms", product="PrecipRate_00.00")
            np.testing.assert_array_equal(source(["2021-01-01T00:10:00Z"],
                ["precip_rate"]).isel(time=0, variable=0).values, values[1])
            records = inventory(root, source="mrms", start="2021-01-01",
                end="2021-01-02")
            if len(records) != 2 or any(row["path"] != first["path"] for row in records):
                raise AssertionError("HF viewer did not discover the versioned month")
            print(json.dumps({"result": "passed", "objects": first["hf_object_count"],
                              "bytes": first["stored_bytes"],
                              "write_seconds": round(first["hf_upload_seconds"], 2)}))
    finally:
        prefix = writer.prefix + "/"
        token = None
        while True:
            args = {"Bucket": writer.config["bucket"], "Prefix": prefix}
            if token:
                args["ContinuationToken"] = token
            page = writer.client.list_objects_v2(**args)
            keys = [{"Key": item["Key"]} for item in page.get("Contents", [])]
            if keys:
                writer.client.delete_objects(Bucket=writer.config["bucket"],
                    Delete={"Objects": keys, "Quiet": True})
            token = page.get("NextContinuationToken")
            if not token:
                break


if __name__ == "__main__":
    main()
