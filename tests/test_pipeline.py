import os
import sqlite3
import tempfile
import unittest

from helpers import FakeHttp, listings_cfg
from collector import jobs


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
        return sqlite3.connect(self.cfg["db_path"])

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


if __name__ == "__main__":
    unittest.main()
