"""Disposable one-scan GOES-to-HF async Zarr preservation check."""

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ecore_weather import goes
from ecore_weather.hf_storage import BucketWriter
from ecore_weather.monthly import fetch_remote_streaming
from ecore_weather.storage import configured_bucket, open_raw


def main():
    selection = goes.discover(datetime(2021, 1, 1, tzinfo=timezone.utc),
        datetime(2021, 1, 1, 0, 10, tzinfo=timezone.utc), bands=(13,),
        satellite=16, product="ABI-L2-CMIPF")
    if not selection.assets:
        raise FileNotFoundError("No historical GOES-16 C13 source for the probe")
    root = f"hf://buckets/{configured_bucket()}/noaa-subsets/_smoke_goes/{uuid4().hex}"
    writer = BucketWriter(root)
    try:
        report = fetch_remote_streaming(selection, backend="obstore", remote_root=root,
            report_dir=None, index_results=False)
        archive = report["monthly_archives"][0]
        with open_raw(archive["path"]) as saved:
            if saved.sizes["time"] != 1 or not {"CMI_C13", "DQF_C13"} <= set(saved):
                raise AssertionError("HF month lost packed GOES imagery or quality flags")
            if "scale_factor" not in saved.CMI_C13.attrs or "_FillValue" not in saved.CMI_C13.attrs:
                raise AssertionError("HF month lost packed GOES calibration")
            sample = saved.isel(time=0, drop=False)
            goes.projection(sample)
        print(json.dumps({"result": "passed", "observations": archive["observations"],
            "stored_bytes": archive["stored_bytes"], "hf_upload_seconds": archive["hf_upload_seconds"]}))
    finally:
        prefix = writer.prefix + "/"
        while True:
            page = writer.client.list_objects_v2(Bucket=writer.config["bucket"], Prefix=prefix,
                MaxKeys=1000)
            objects = [{"Key": item["Key"]} for item in page.get("Contents", [])]
            if not objects:
                break
            writer.client.delete_objects(Bucket=writer.config["bucket"],
                Delete={"Objects": objects, "Quiet": True})


if __name__ == "__main__":
    main()
