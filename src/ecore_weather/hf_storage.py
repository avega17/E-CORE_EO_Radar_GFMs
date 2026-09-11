"""Coordinated HF batch transfers; keep filesystem compatibility out of writes."""

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading

_LOCK = threading.RLock()


@contextmanager
def bucket_writer(bucket):
    """One publisher per workstation bucket, including across Python processes.

    Cluster jobs must use one publishing coordinator, or put ECORE_HF_LOCK_DIR on
    a shared filesystem with working POSIX locks. This is not a distributed lock.
    """
    import fcntl
    folder = Path(os.getenv("ECORE_HF_LOCK_DIR", tempfile.gettempdir())) / "ecore-hf-locks"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (hashlib.sha256(bucket.encode()).hexdigest()[:20] + ".lock")
    with _LOCK, path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


class Publisher:
    def __init__(self, root, api=None):
        from dotenv import load_dotenv
        load_dotenv()
        from huggingface_hub import HfApi
        from huggingface_hub.utils import disable_progress_bars
        disable_progress_bars("huggingface_hub")
        parts = root.removeprefix("hf://buckets/").split("/")
        self.bucket = "/".join(parts[:2])
        self.api = api or HfApi(token=os.getenv("HF_TOKEN") or os.getenv("HF_API_KEY"))
        self.batch_calls = self.readback_bytes = 0

    def prefix(self, root):
        return root.removeprefix(f"hf://buckets/{self.bucket}/").rstrip("/")

    def check(self):
        with bucket_writer(self.bucket):
            self.api.bucket_info(self.bucket)

    def _download(self, paths, directory):
        files = []
        for remote, relative in paths:
            local = Path(directory) / relative
            local.parent.mkdir(parents=True, exist_ok=True)
            files.append((remote, local))
        self.api.download_bucket_files(self.bucket, files, raise_on_missing_files=True)
        self.batch_calls += 1
        self.readback_bytes += sum(p.stat().st_size for _, p in files)

    def resume(self, root, selection_id, asset_id, schema, subset_id=None):
        from .storage import fingerprint, metadata_fingerprint, open_raw
        prefix = self.prefix(root)
        with bucket_writer(self.bucket), tempfile.TemporaryDirectory(prefix="ecore-hf-resume-") as temp:
            infos = list(self.api.get_bucket_paths_info(self.bucket, [prefix + "/complete.json"]))
            self.batch_calls += 1
            if not infos:
                return False
            self._download([(infos[0], "complete.json")], temp)
            marker = json.loads((Path(temp)/"complete.json").read_text())
            identity_matches = marker.get("subset_id") == subset_id if subset_id else marker.get("selection_id") == selection_id
            if not identity_matches or (marker.get("asset_id"), marker.get("raw_schema_version")) != (asset_id, schema):
                return False
            raw_path = marker.get("raw_path", "raw.zarr")
            if raw_path not in {"raw.zarr", "raw.zarr.zip"}:
                raise ValueError("Invalid raw container in completion marker.")
            paths = marker.get("files")
            if paths is None:
                # Earlier stores have no file inventory. Discover just this owned store.
                paths = [x.path[len(prefix)+1:] for x in self.api.list_bucket_tree(
                    self.bucket, prefix=prefix+"/raw.zarr", recursive=True) if hasattr(x, "size")]
            if not paths or any(not (p == raw_path or p.startswith(raw_path+"/")) or ".." in Path(p).parts for p in paths):
                raise ValueError("Invalid raw-store inventory in completion marker.")
            self._download([(prefix+"/"+p, p) for p in paths], temp)
            with open_raw(Path(temp)/marker.get("raw_path", "raw.zarr")) as ds:
                return raw_path if fingerprint(ds) == marker["array_sha256"] and metadata_fingerprint(ds) == marker["metadata_sha256"] else False

    def publish(self, staging, root, marker):
        from .storage import fingerprint, metadata_fingerprint, open_raw
        prefix = self.prefix(root)
        files = sorted(p for p in Path(staging).rglob("*") if p.is_file() and p.relative_to(staging).as_posix() != "complete.json")
        paths = [p.relative_to(staging).as_posix() for p in files]
        with bucket_writer(self.bucket):
            self.api.batch_bucket_files(self.bucket, add=[(p, prefix+"/"+rel) for p, rel in zip(files, paths)])
            self.batch_calls += 1
            with tempfile.TemporaryDirectory(prefix="ecore-hf-verify-", dir=staging) as temp:
                self._download([(prefix+"/"+p, p) for p in paths], temp)
                with open_raw(Path(temp)/marker.get("raw_path", "raw.zarr")) as ds:
                    if fingerprint(ds) != marker["array_sha256"] or metadata_fingerprint(ds) != marker["metadata_sha256"]:
                        raise IOError("Published arrays or metadata failed the batch read-back check.")
            # HF batches are not transactional: publish completion only after verification.
            payload = json.dumps({**marker, "files": paths}).encode()
            self.api.batch_bucket_files(self.bucket, add=[(payload, prefix+"/complete.json")])
            self.batch_calls += 1
