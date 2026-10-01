"""Local DuckDB index for selections and completed archive observations.

STAC and each archive's manifest remain the portable source of truth. This file
is a rebuildable search index and run log; it must stay on a local Linux disk,
not DrvFS, an HF bucket, or a shared cluster filesystem.
"""

from __future__ import annotations

import json
import os
import contextlib
import fcntl
from datetime import datetime, timezone
from pathlib import Path

from .common import utc


@contextlib.contextmanager
def _writer_lock(path):
    """Serialize local DuckDB writers, including overlapping notebook runs."""
    import hashlib
    filename = database_path(path)
    key = hashlib.sha256(str(filename).encode()).hexdigest()[:20]
    lock_path = Path("/tmp") / f"ecore-duckdb-{key}.lock"
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def database_path(path=None) -> Path:
    value = path or os.getenv("ECORE_INDEX_PATH", "results/archive_index.duckdb")
    if str(value).startswith(("hf://", "/mnt/")):
        raise ValueError("The DuckDB index must be stored on a local Linux filesystem.")
    return Path(value).expanduser().resolve()


def connect(path=None):
    """Open or create the index. Call writes from one coordinator process."""
    import duckdb

    filename = database_path(path)
    filename.parent.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(str(filename))
    connection.execute("""
        CREATE TABLE IF NOT EXISTS selections (
            selection_id VARCHAR PRIMARY KEY, source VARCHAR, product VARCHAR,
            start_utc TIMESTAMP, end_utc TIMESTAMP, request_json JSON,
            catalog_path VARCHAR, indexed_at TIMESTAMP DEFAULT current_timestamp
        )
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS observations (
            source VARCHAR, asset_id VARCHAR, subset_id VARCHAR, product VARCHAR,
            actual_time TIMESTAMP, slot_time TIMESTAMP, band INTEGER,
            source_url VARCHAR, etag VARCHAR, source_bytes UBIGINT,
            archive_url VARCHAR, status VARCHAR, selection_id VARCHAR,
            offset_seconds DOUBLE,
            indexed_at TIMESTAMP DEFAULT current_timestamp,
            PRIMARY KEY (source, asset_id, subset_id)
        )
    """)
    connection.execute("ALTER TABLE observations ADD COLUMN IF NOT EXISTS offset_seconds DOUBLE")
    connection.execute("""
        CREATE TABLE IF NOT EXISTS fetch_runs (
            run_id VARCHAR PRIMARY KEY, source VARCHAR, product VARCHAR,
            selection_id VARCHAR, destination VARCHAR, started_at TIMESTAMP,
            wall_seconds DOUBLE, source_bytes UBIGINT, stored_bytes UBIGINT,
            peak_rss_bytes UBIGINT, status VARCHAR, report_json JSON
        )
    """)
    return connection


def _write_observation_rows(db, rows, columns):
    """Upsert observation records as a vectorized DuckDB relation.

    ``executemany`` sends one statement per source file. Large GOES monthly
    selections contain tens of thousands of rows, so registering one typed
    pandas table avoids turning catalog preparation into a long serial loop.
    """
    if not rows:
        return
    import pandas as pd

    frame = pd.DataFrame.from_records(rows, columns=columns)
    for column in ("actual_time", "slot_time"):
        if column in frame:
            frame[column] = pd.to_datetime(frame[column], utc=True).dt.tz_localize(None)
    for column, dtype in (("band", "Int32"), ("source_bytes", "UInt64"),
                          ("offset_seconds", "Float64")):
        if column in frame:
            frame[column] = pd.array(frame[column], dtype=dtype)
    for column in ("source", "asset_id", "subset_id", "product", "source_url",
                   "etag", "archive_url", "status", "selection_id"):
        if column in frame:
            frame[column] = pd.array(frame[column], dtype="string")

    relation = "incoming_observation_rows"
    db.register(relation, frame)
    try:
        target_columns = ", ".join(columns)
        db.execute(f"INSERT OR REPLACE INTO observations ({target_columns}) "
                   f"SELECT {target_columns} FROM {relation}")
    finally:
        db.unregister(relation)


def record_selection(selection, catalog_path, path=None):
    """Index a STAC selection and its NOAA source objects."""
    # Selection.id hashes the complete dataclass, including every source asset.
    # Compute it once: evaluating the property inside the asset loop makes
    # catalog indexing quadratic for large GOES selections.
    selection_id = selection.id
    selection_summary = json.dumps(selection.summary())
    with _writer_lock(path), connect(path) as db:
        db.execute("DELETE FROM selections WHERE selection_id = ?", [selection_id])
        db.execute("""INSERT INTO selections VALUES (?, ?, ?, ?, ?, ?, ?, current_timestamp)""",
                   [selection_id, selection.source, selection.product,
                    utc(selection.start).replace(tzinfo=None), utc(selection.end).replace(tzinfo=None),
                    selection_summary, str(catalog_path)])
        slots = {m.get("asset_id"): m.get("slot_time") for m in selection.hourly_matches}
        rows = []
        for asset in selection.assets:
            slot = slots.get(asset.id)
            band = None
            if selection.source == "goes":
                import re
                match = re.search(r"-M\dC(\d{2})_", asset.key)
                band = int(match[1]) if match else None
            rows.append([selection.source, asset.id, "", selection.product,
                         utc(asset.time).replace(tzinfo=None),
                         utc(slot).replace(tzinfo=None) if slot else None, band,
                         asset.url, asset.etag, asset.size, None, "selected", selection_id])
        _write_observation_rows(db, rows, ["source", "asset_id", "subset_id", "product",
            "actual_time", "slot_time", "band", "source_url", "etag", "source_bytes",
            "archive_url", "status", "selection_id"])


def record_fetch(report, path=None):
    """Record completed, reused, and failed item outcomes plus measured run data."""
    if not report:
        return
    selection = report.get("selection_summary", {})
    source = report.get("source", selection.get("source", "unknown"))
    product = selection.get("product", "unknown")
    destination = report.get("root", "")
    subset_id = report.get("subset_id", "")
    run_id = report.get("run_id") or report.get("selection_id", "") + ":" + str(report.get("started_at", ""))
    started = report.get("started_at") or datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    with _writer_lock(path), connect(path) as db:
        rows = []
        for row in report.get("records", []):
            actual = row.get("time")
            if not actual:
                continue
            rows.append([source, row.get("asset_id", ""), row.get("subset_id", subset_id), product,
                 utc(actual).replace(tzinfo=None),
                 utc(row["slot_time"]).replace(tzinfo=None) if row.get("slot_time") else None,
                 row.get("band"), row.get("source_url", ""), row.get("etag", ""),
                 row.get("source_bytes", 0), row.get("url"), row.get("status", "unknown"),
                 report.get("selection_id", ""), row.get("offset_seconds")])
        _write_observation_rows(db, rows, ["source", "asset_id", "subset_id", "product",
            "actual_time", "slot_time", "band", "source_url", "etag", "source_bytes",
            "archive_url", "status", "selection_id", "offset_seconds"])
        db.execute("""INSERT OR REPLACE INTO fetch_runs
            (run_id, source, product, selection_id, destination, started_at,
             wall_seconds, source_bytes, stored_bytes, peak_rss_bytes, status, report_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [run_id, source, product, report.get("selection_id", ""), destination,
             utc(started).replace(tzinfo=None), report.get("wall_s", 0), report.get("read_bytes", 0),
             report.get("stored_bytes", 0), report.get("peak_rss_bytes", 0),
             "interrupted" if report.get("interrupted") else "complete",
             json.dumps(report, default=str)])


def search(location, source=None, start=None, end=None, band=None, path=None):
    """Find locally indexed completed archives under a location and time range."""
    db_path = database_path(path)
    if not db_path.exists():
        return []
    root = str(Path(location).expanduser().resolve()).rstrip("/")
    clauses = ["archive_url IS NOT NULL", "status IN ('saved', 'reused', 'archived')", "archive_url LIKE ?"]
    params = [root + "%"]
    if source:
        clauses.append("source = ?"); params.append(source)
    if start:
        clauses.append("actual_time >= ?"); params.append(utc(start).replace(tzinfo=None))
    if end:
        clauses.append("actual_time < ?"); params.append(utc(end).replace(tzinfo=None))
    if band is not None:
        clauses.append("(band = ? OR band IS NULL)"); params.append(int(band))
    with connect(db_path) as db:
        try:
            rows = db.execute("""SELECT archive_url, actual_time, source, band, product, asset_id,
                                      source_url, slot_time, offset_seconds, subset_id
                              FROM observations WHERE """ +
                              " AND ".join(clauses) + " ORDER BY actual_time", params).fetchall()
        except Exception:  # A damaged or older index is rebuildable from manifests.
            return []
    return [{"path": row[0], "time": row[1].replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z"),
             "source": row[2], "band": row[3], "product": row[4], "asset_id": row[5],
             "source_url": row[6], "slot_time": row[7].replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z") if row[7] else None,
             "offset_seconds": row[8], "subset_id": row[9]} for row in rows]


def rebuild(locations, path=None):
    """Rebuild observations from local completion manifests without opening chunks."""
    from . import view_index

    filename = database_path(path)
    statement = """INSERT OR REPLACE INTO observations
        (source, asset_id, subset_id, product, actual_time, slot_time, band,
         source_url, etag, source_bytes, archive_url, status, offset_seconds)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'reused', ?)"""
    with _writer_lock(filename):
        if filename.exists():
            filename.unlink()
        with connect(filename) as db:
          batch = []
          for location in locations:
            for record in view_index.inventory(location):
                archive_url = record["path"]
                source_url = record.get("source_url", "")
                asset_id = record.get("asset_id") or Path(archive_url).parent.name
                batch.append([record["source"], asset_id, record.get("subset_id", ""),
                     record.get("product", record["dataset"].split("/")[-1]),
                     utc(record["time"]).replace(tzinfo=None),
                     utc(record["slot_time"]).replace(tzinfo=None) if record.get("slot_time") else None,
                     record.get("band"), source_url, record.get("etag", ""),
                     record.get("source_bytes", 0), archive_url, record.get("offset_seconds")])
                if len(batch) >= 5000:
                    db.executemany(statement, batch)
                    batch.clear()
          if batch:
            db.executemany(statement, batch)
    return filename
