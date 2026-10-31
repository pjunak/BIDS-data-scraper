"""Nightly downloadable snapshots of the whole database.

Uses SQLite's online backup API, so it is safe while the collector is writing.
Result: <exports_dir>/collector-YYYY-MM-DD.sqlite.gz plus collector-latest.sqlite.gz (symlink),
keeping the newest `keep` dated files.
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
    stamp = datetime.now().strftime("%Y-%m-%d")
    tmp_db = out_dir / f".{PREFIX}{stamp}.sqlite.tmp"
    target = out_dir / f"{PREFIX}{stamp}.sqlite.gz"
    tmp_gz = out_dir / f".{target.name}.tmp"

    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(tmp_db)
    try:
        src.backup(dst)
        dst.execute("PRAGMA journal_mode=DELETE")  # single self-contained file for whoever downloads it
    finally:
        dst.close()
        src.close()

    with open(tmp_db, "rb") as f_in, gzip.open(tmp_gz, "wb", compresslevel=6) as f_out:
        shutil.copyfileobj(f_in, f_out, length=1024 * 1024)
    tmp_db.unlink()
    os.replace(tmp_gz, target)  # atomic: a download never sees a half-written file

    latest = out_dir / LATEST
    tmp_link = out_dir / f".{LATEST}.tmp"
    if tmp_link.is_symlink() or tmp_link.exists():
        tmp_link.unlink()
    tmp_link.symlink_to(target.name)
    os.replace(tmp_link, latest)

    dated = sorted(p for p in out_dir.glob(f"{PREFIX}????-??-??.sqlite.gz"))
    for old in dated[:-keep]:
        old.unlink()
    log.info("snapshot %s (%.1f MB)", target, target.stat().st_size / 1e6)
    return target
