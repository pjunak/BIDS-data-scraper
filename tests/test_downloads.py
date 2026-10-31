import functools
import http.server
import os
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

import helpers  # noqa: F401
from collector import jobs


class DownloadsTest(unittest.TestCase):
    """Runs a real local HTTP server so the download code path is exercised end to end."""

    @classmethod
    def setUpClass(cls):
        cls.www = Path(tempfile.mkdtemp())
        (cls.www / "assets").mkdir()
        (cls.www / "assets" / "2026-08-15_Cenova-mapa.xlsx").write_bytes(b"xlsx-new")
        (cls.www / "assets" / "2026-05-15_Cenova-mapa.xlsx").write_bytes(b"xlsx-old")
        (cls.www / "gtfs.zip").write_bytes(b"zip-v1")
        (cls.www / "api").mkdir()
        (cls.www / "api" / "202601.zip").write_bytes(b"jan")
        (cls.www / "api" / "202602.zip").write_bytes(b"feb")
        (cls.www / "api" / "listing.json").write_text('{"data":[{"name":"202602"},{"name":"202601"}]}')
        (cls.www / "page.html").write_text(
            '<a href="/assets/2026-08-15_Cenova-mapa.xlsx">a</a> <a href=\'/assets/2026-05-15_Cenova-mapa.xlsx\'>b</a>'
            ' <a href="/other.pdf">c</a>')
        class Quiet(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *args):
                pass
        handler = functools.partial(Quiet, directory=str(cls.www))
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.cfg = {"db_path": os.path.join(tmp, "d.sqlite"), "files_dir": os.path.join(tmp, "files"),
                    "user_agent": "test", "request_delay_s": 0, "downloads": [
                        {"name": "mf", "type": "discover", "page_url": f"{self.base}/page.html",
                         "link_regex": r'/assets/[^"\'\s]*Cenova-mapa[^"\'\s]*\.xlsx', "every_days": 7},
                        {"name": "gtfs", "type": "direct", "url": f"{self.base}/gtfs.zip",
                         "filename": "gtfs.zip", "every_days": 7},
                        {"name": "todo", "enabled": False, "type": "direct", "url": "DOPLNIT"},
                    ]}

    def files(self):
        return sorted(p.name for p in Path(self.cfg["files_dir"]).rglob("*") if p.is_file())

    def test_discover_direct_and_dedupe(self):
        jobs.run_downloads(self.cfg)
        self.assertEqual(len(self.files()), 3)
        jobs.run_downloads(self.cfg)               # not due yet
        jobs.run_downloads(self.cfg, force=True)   # due, but nothing changed
        self.assertEqual(len(self.files()), 3)
        con = sqlite3.connect(self.cfg["db_path"])
        jobs_run = [r[0] for r in con.execute("SELECT job FROM run")]
        self.assertNotIn("download:todo", jobs_run, "disabled items never run")
        self.assertEqual(jobs_run.count("download:gtfs"), 2)

    def test_changed_file_is_archived_as_new_version(self):
        jobs.run_downloads(self.cfg, only="gtfs")
        (self.www / "gtfs.zip").write_bytes(b"zip-v2")
        try:
            jobs.run_downloads(self.cfg, force=True, only="gtfs")
            con = sqlite3.connect(self.cfg["db_path"])
            self.assertEqual(con.execute("SELECT COUNT(*) FROM download WHERE name='gtfs'").fetchone()[0], 2)
            stored = sorted(Path(r[0]).read_bytes() for r in con.execute("SELECT path FROM download WHERE name='gtfs'"))
            self.assertEqual(stored, [b"zip-v1", b"zip-v2"], "a same-day version must not overwrite the first")
        finally:
            (self.www / "gtfs.zip").write_bytes(b"zip-v1")

    def test_link_template_and_recheck_last(self):
        item = {"name": "police", "type": "discover", "page_url": f"{self.base}/api/listing.json",
                "link_regex": r'"name":"(\d{6})"', "link_template": f"{self.base}/api/{{}}.zip", "recheck_last": 1}
        self.cfg["downloads"] = [item]
        jobs.run_downloads(self.cfg)
        self.assertEqual([f.split("_", 1)[1] for f in self.files()], ["202601.zip", "202602.zip"])
        (self.www / "api" / "202602.zip").write_bytes(b"feb-updated")
        (self.www / "api" / "202601.zip").write_bytes(b"jan-updated")
        try:
            jobs.run_downloads(self.cfg, force=True)
            con = sqlite3.connect(self.cfg["db_path"])
            urls = [r[0].rsplit("/", 1)[1] for r in con.execute("SELECT url FROM download ORDER BY id")]
            self.assertEqual(urls, ["202601.zip", "202602.zip", "202602.zip"], "only the newest link is re-checked")
        finally:
            (self.www / "api" / "202601.zip").write_bytes(b"jan")
            (self.www / "api" / "202602.zip").write_bytes(b"feb")

    def test_missing_links_mark_run_failed(self):
        self.cfg["downloads"][0]["link_regex"] = r"nothing-matches"
        jobs.run_downloads(self.cfg, only="mf")
        con = sqlite3.connect(self.cfg["db_path"])
        self.assertEqual(con.execute("SELECT status FROM run").fetchone()[0], "failed")


if __name__ == "__main__":
    unittest.main()
