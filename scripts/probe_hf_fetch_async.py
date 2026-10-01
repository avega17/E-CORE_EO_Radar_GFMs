"""Disposable live NOAA-to-HF Earth2Studio monthly merge and reuse check."""

from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ecore_weather import mrms
from ecore_weather.hf_storage import BucketWriter
from ecore_weather.monthly import fetch_remote_streaming
from ecore_weather.storage import configured_bucket, open_raw


def main():
    selection = mrms.discover(datetime(2021, 1, 1, tzinfo=timezone.utc),
        datetime(2021, 1, 1, 0, 20, tzinfo=timezone.utc),
        product="PrecipRate_00.00", tolerance_minutes=5, time_match="previous")
    if len(selection.assets) < 2:
        raise FileNotFoundError("Two historical MRMS observations are needed for this probe")
    root = f"hf://buckets/{configured_bucket()}/noaa-subsets/_smoke_fetch/{uuid4().hex}"
    writer = BucketWriter(root)
    try:
        first = fetch_remote_streaming(replace(selection, assets=selection.assets[:1]),
            backend="obstore", remote_root=root, report_dir=None, index_results=False)
        merged = fetch_remote_streaming(selection, backend="obstore", remote_root=root,
            report_dir=None, index_results=False)
        reused = fetch_remote_streaming(selection, backend="obstore", remote_root=root,
            report_dir=None, index_results=False)
        first_path = first["monthly_archives"][0]["path"]
        merged_path = merged["monthly_archives"][0]["path"]
        if first_path == merged_path or reused["monthly_archives"][0]["status"] != "reused":
            raise AssertionError("Remote merge or reuse failed")
        with open_raw(merged_path) as saved:
            if saved.sizes["time"] != 2 or "bitmap_valid" not in saved:
                raise AssertionError("Remote month lost a source observation or bitmap")
        print(json.dumps({"result": "passed", "observations": 2,
            "first_bytes": first["stored_bytes"], "merged_bytes": merged["stored_bytes"],
            "merged_path_changed": True}))
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
