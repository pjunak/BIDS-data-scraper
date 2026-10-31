"""Nightly downloadable snapshots of the whole database.

Uses SQLite's online backup API, so it is safe while the collector is writing. The result is
<exports_dir>/collector-YYYY-MM-DD.sqlite.gz plus collector-latest.sqlite.gz (a symlink to it);
only the newest `keep` dated files are kept.
"""
import gzip
import logging
import os
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

PREFIX = "collector-"
LATEST = "collector-latest.sqlite.gz"


def make_snapshot(db_path: str, exports_dir: str, keep: int = 7) -> Path:
    out_dir = Path(exports_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{PREFIX}{datetime.now():%Y-%m-%d}.sqlite.gz"
    tmp_db = out_dir / f".{target.stem}.tmp"
    tmp_gz = out_dir / f".{target.name}.tmp"
    try:
        _backup(db_path, tmp_db)
        with open(tmp_db, "rb") as f_in, gzip.open(tmp_gz, "wb", compresslevel=6) as f_out:
            shutil.copyfileobj(f_in, f_out, length=1024 * 1024)
        os.replace(tmp_gz, target)  # atomic: a download never sees a half-written file
    finally:
        tmp_db.unlink(missing_ok=True)
        tmp_gz.unlink(missing_ok=True)

    _point_latest_at(out_dir, target)
    for old in sorted(out_dir.glob(f"{PREFIX}????-??-??.sqlite.gz"))[:-keep]:
        old.unlink()
    log.info("snapshot %s (%.1f MB)", target, target.stat().st_size / 1e6)
    return target


def _backup(db_path: str, out_path: Path):
    """Consistent copy of a live database as one self-contained file (no -wal/-shm)."""
    src, dst = sqlite3.connect(db_path), sqlite3.connect(out_path)
    try:
        src.backup(dst)
        dst.execute("PRAGMA journal_mode=DELETE")
    finally:
        dst.close()
        src.close()


def _point_latest_at(out_dir: Path, target: Path):
    """Replace the collector-latest symlink atomically."""
    tmp_link = out_dir / f".{LATEST}.tmp"
    tmp_link.unlink(missing_ok=True)
    tmp_link.symlink_to(target.name)
    os.replace(tmp_link, out_dir / LATEST)
