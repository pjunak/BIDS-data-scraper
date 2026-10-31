import os
import sqlite3
import tempfile
import unittest

from datetime import datetime

from helpers import FakeHttp, listings_cfg
from collector import db, jobs
from collector.__main__ import next_run_at


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = listings_cfg(os.path.join(self.tmp, "t.sqlite"))
        self._orig = jobs.make_http

    def tearDown(self):
        jobs.make_http = self._orig

    def run_with(self, http):
        jobs.make_http = lambda c: http
        jobs.run_listings(self.cfg)
        con = sqlite3.connect(self.cfg["db_path"])
        self.addCleanup(con.close)
        return con

    def test_two_nights(self):
        self.run_with(FakeHttp())
        con = self.run_with(FakeHttp())
        q = lambda sql: con.execute(sql).fetchone()[0]  # noqa: E731
        self.assertEqual(q("SELECT COUNT(*) FROM run WHERE status='ok'"), 6)
        self.assertEqual(q("SELECT COUNT(*) FROM listing_snapshot"), 10)
        self.assertEqual(q("SELECT COUNT(*) FROM listing_payload"), 5, "unchanged payloads are stored once")
        self.assertEqual(q("SELECT COUNT(*) FROM listing_detail"), 3, "details only for new listings")
        self.assertEqual(q("SELECT MAX(n_snapshots) FROM listing_lifecycle"), 2)
        self.assertEqual(q("SELECT COUNT(*) FROM listing_attributes WHERE is_new_build AND condition = 'Novostavba'"), 3)
        self.assertEqual(q("SELECT area_m2 FROM listing_attributes a JOIN listing_snapshot s USING (source, listing_id) "
                           "WHERE source = 'bezrealitky' LIMIT 1"), 55)

    def test_broken_source_does_not_stop_others(self):
        con = self.run_with(FakeHttp(broken={"bezrealitky"}))
        status = dict(con.execute("SELECT job, status FROM run"))
        self.assertEqual(status["listings:bezrealitky"], "failed")
        self.assertEqual(status["listings:sreality"], "ok")
        self.assertEqual(status["listings:idnes"], "ok")
        msg = con.execute("SELECT message FROM run WHERE job='listings:bezrealitky'").fetchone()[0]
        self.assertIn("down", msg)

    def test_restart_marks_interrupted_runs_failed(self):
        self.run_with(FakeHttp())
        con = sqlite3.connect(self.cfg["db_path"])
        self.addCleanup(con.close)
        con.execute("INSERT INTO run(job, started_at, status) VALUES ('listings:sreality', '2026-11-01', 'running')")
        con.commit()
        self.assertEqual(db.fail_interrupted_runs(con), 1)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM run WHERE status = 'running'").fetchone()[0], 0)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM run WHERE status = 'ok'").fetchone()[0], 3,
                         "finished runs are left alone")


class ScheduleTest(unittest.TestCase):
    def test_next_run_is_today_or_tomorrow(self):
        self.assertEqual(next_run_at(datetime(2026, 11, 1, 1, 0), 3, 15), datetime(2026, 11, 1, 3, 15))
        self.assertEqual(next_run_at(datetime(2026, 11, 1, 3, 15), 3, 15), datetime(2026, 11, 2, 3, 15))
        self.assertEqual(next_run_at(datetime(2026, 11, 1, 22, 0), 3, 15), datetime(2026, 11, 2, 3, 15))


if __name__ == "__main__":
    unittest.main()
