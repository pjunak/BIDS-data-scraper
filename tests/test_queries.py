"""Every SQL query used by the dashboard and Datasette must run against the current schema.

Catches the classic mistake of renaming a column in db.py and silently breaking the website.
"""
import re
import tempfile
import unittest
from pathlib import Path

import yaml

from helpers import ROOT
from collector import db


class QueriesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.con = db.connect(str(Path(tempfile.mkdtemp()) / "q.sqlite"))

    @classmethod
    def tearDownClass(cls):
        cls.con.close()

    def test_dashboard_queries(self):
        html = (Path(ROOT) / "web" / "index.html").read_text(encoding="utf-8")
        queries = re.findall(r'\bq\("((?:[^"\\]|\\.)*)"\)', html)
        self.assertGreaterEqual(len(queries), 5)
        for sql in queries:
            with self.subTest(sql=sql[:60]):
                self.con.execute(sql).fetchall()

    def test_datasette_canned_queries(self):
        meta = yaml.safe_load((Path(ROOT) / "datasette" / "metadata.yml").read_text(encoding="utf-8"))
        queries = meta["databases"]["collector"]["queries"]
        self.assertTrue(queries)
        for name, q in queries.items():
            with self.subTest(query=name):
                self.con.execute(q["sql"]).fetchall()

    def test_datasette_described_tables_exist(self):
        meta = yaml.safe_load((Path(ROOT) / "datasette" / "metadata.yml").read_text(encoding="utf-8"))
        existing = {r[0] for r in self.con.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
        for t in meta["databases"]["collector"]["tables"]:
            self.assertIn(t, existing)


if __name__ == "__main__":
    unittest.main()
