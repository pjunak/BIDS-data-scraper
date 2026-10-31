import gzip
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import FakeHttp, listings_cfg
from collector import jobs, snapshots


class SnapshotTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = listings_cfg(str(self.tmp / "c.sqlite"))
        self.cfg["exports_dir"] = str(self.tmp / "exports")
        orig = jobs.make_http
        jobs.make_http = lambda c: FakeHttp()
        jobs.run_listings(self.cfg)
        jobs.make_http = orig

    def test_snapshot_is_a_valid_standalone_database(self):
        jobs.run_snapshot(self.cfg)
        latest = Path(self.cfg["exports_dir"]) / snapshots.LATEST
        self.assertTrue(latest.is_symlink())
        raw = self.tmp / "restored.sqlite"
        raw.write_bytes(gzip.decompress(latest.read_bytes()))
        con = sqlite3.connect(raw)
        self.addCleanup(con.close)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM listing_snapshot").fetchone()[0], 5)
        self.assertEqual(con.execute("PRAGMA journal_mode").fetchone()[0], "delete")
        self.assertEqual([p.name for p in Path(self.cfg["exports_dir"]).iterdir() if p.name.startswith(".")], [],
                         "no temp files left behind")

    def test_rotation_keeps_newest(self):
        out = Path(self.cfg["exports_dir"])
        out.mkdir()
        for d in ["2026-01-01", "2026-01-02", "2026-01-03"]:
            (out / f"collector-{d}.sqlite.gz").write_bytes(b"old")
        with mock.patch.object(snapshots, "datetime") as dt:
            dt.now.return_value.strftime.return_value = "2026-01-04"
            snapshots.make_snapshot(self.cfg["db_path"], str(out), keep=2)
        dated = sorted(p.name for p in out.glob("collector-????-??-??.sqlite.gz"))
        self.assertEqual(dated, ["collector-2026-01-03.sqlite.gz", "collector-2026-01-04.sqlite.gz"])
        self.assertEqual(os.readlink(out / snapshots.LATEST), "collector-2026-01-04.sqlite.gz")


if __name__ == "__main__":
    unittest.main()
