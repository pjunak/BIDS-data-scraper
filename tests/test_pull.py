"""`pull` against a local stand-in for bids.junak.eu: the real login (web/auth.py) and the real
nightly snapshot, served the way nginx serves them (JSON listing of /exports/, files under /files/,
the script itself publicly at /pull.py)."""
import email.utils
import hashlib
import http.server
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime
from http.cookies import SimpleCookie
from pathlib import Path
from unittest import mock
from urllib.parse import unquote

from helpers import ROOT
from collector import db, pull, snapshots

_spec = importlib.util.spec_from_file_location("auth", Path(ROOT) / "web" / "auth.py")
auth = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(auth)

PASSWORD = "tymove-heslo"
ROLES = {"user": auth.hash_password(PASSWORD)}


class FakeSite:
    """nginx in front of auth.py: /auth/login goes to the real handler, everything else needs the cookie."""

    def __init__(self, root: Path):
        self.root = root
        self.hits: list[str] = []
        site = self

        class Handler(auth.make_handler(ROLES)):
            def do_GET(self):
                site.hits.append(self.path)
                if self.path == "/pull.py":  # public, like nginx's `location = /pull.py`
                    return self._send((Path(ROOT) / "collector" / "pull.py").read_bytes(), "text/plain")
                morsel = SimpleCookie(self.headers.get("Cookie", "")).get(auth.COOKIE)
                if not (morsel and auth.check_session(morsel.value, ROLES, time.time())):
                    return self._redirect(f"/login?next={self.path}")
                path = unquote(self.path)
                if path == "/api/exports/":
                    return self._send(json.dumps(site.listing()).encode(), "application/json")
                for prefix in ("/exports/", "/files/"):
                    if path.startswith(prefix):
                        file = site.root / prefix.strip("/") / path[len(prefix):]
                        if file.is_file():
                            return self._send(file.read_bytes(), "application/octet-stream")
                self.send_error(404)

            def _send(self, body: bytes, content_type: str):
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def listing(self):
        """Like nginx `autoindex_format json`: no dot files, symlinks followed."""
        out = []
        for p in sorted((self.root / "exports").iterdir()):
            if not p.name.startswith("."):
                st = p.stat()
                out.append({"name": p.name, "type": "file", "size": st.st_size,
                            "mtime": email.utils.formatdate(st.st_mtime, usegmt=True)})
        return out

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class PullTest(unittest.TestCase):
    def setUp(self):
        self.server_dir = Path(tempfile.mkdtemp())
        (self.server_dir / "exports").mkdir()
        self.db_path = self.server_dir / "collector.sqlite"
        db.connect(str(self.db_path)).close()
        self.site = FakeSite(self.server_dir)
        self.addCleanup(self.site.close)
        self.local = Path(tempfile.mkdtemp()) / "data"
        self.asked = 0

    # --- the server side ---------------------------------------------------------------------

    def add_file(self, name: str, url: str, filename: str, content: bytes):
        """A secondary file the collector stored: on disk under files/ and as a row in `download`."""
        path = self.server_dir / "files" / name / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        with db.open_db(str(self.db_path)) as con:
            con.execute("INSERT INTO download(name, url, fetched_at, path, sha256, bytes) VALUES (?, ?, ?, ?, ?, ?)",
                        (name, url, db.now(), f"/data/files/{name}/{filename}",
                         hashlib.sha256(content).hexdigest(), len(content)))
            con.commit()

    def snapshot(self, day: str):
        with mock.patch.object(snapshots, "datetime") as dt:
            dt.now.return_value = datetime.fromisoformat(day)
            snapshots.make_snapshot(str(self.db_path), str(self.server_dir / "exports"))

    def standard_server(self):
        self.add_file("csu", "https://csu/x.csv", "2026-10-01_x.csv", b"version 1")
        self.add_file("csu", "https://csu/x.csv", "2026-11-01_x.csv", b"version 2")
        self.add_file("policie", "https://p/202601.zip", "2026-11-01_202601.zip", b"jan")
        self.add_file("policie", "https://p/202602.zip", "2026-11-01_202602.zip", b"feb")
        self.snapshot("2026-11-02")

    # --- the client side ---------------------------------------------------------------------

    def password(self, base):
        self.asked += 1
        return PASSWORD

    def pull(self, **kw):
        kw.setdefault("password", self.password)
        return pull.pull(self.site.url, str(self.local), **kw)

    def local_files(self):
        root = self.local / "files"
        return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())

    # --- tests -------------------------------------------------------------------------------

    def test_first_pull_then_nothing_to_do(self):
        self.standard_server()
        self.assertEqual(self.pull(), [])
        self.assertEqual(self.asked, 1)
        con = sqlite3.connect(self.local / "collector.sqlite")
        self.addCleanup(con.close)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM download").fetchone()[0], 4)
        self.assertEqual(self.local_files(), ["csu/x.csv", "policie/202601.zip", "policie/202602.zip"],
                         "newest version of every URL, under its original name")
        self.assertEqual((self.local / "files/csu/x.csv").read_bytes(), b"version 2")
        if os.name == "posix":
            self.assertEqual((self.local / ".pull-session").stat().st_mode & 0o777, 0o600)

        self.site.hits.clear()
        self.assertEqual(self.pull(), [])
        self.assertEqual(self.asked, 1, "the saved session is reused")
        self.assertEqual(self.site.hits, ["/api/exports/"], "nothing is downloaded again")

    def test_new_snapshot_brings_new_versions_and_drops_replaced_ones(self):
        self.standard_server()
        self.pull()
        (self.local / "files/csu/moje_poznamky.txt").write_text("own file")
        self.add_file("csu", "https://csu/x.csv", "2026-12-01_x.csv", b"version 3")
        self.snapshot("2026-12-02")
        self.site.hits.clear()
        self.assertEqual(self.pull(), [])
        self.assertEqual(sorted(h for h in self.site.hits if h != "/api/exports/"),
                         ["/exports/collector-2026-12-02.sqlite.gz", "/files/csu/2026-12-01_x.csv"])
        self.assertEqual(self.local_files(), ["csu/moje_poznamky.txt", "csu/x.csv",
                                              "policie/202601.zip", "policie/202602.zip"], "same paths as before")
        self.assertEqual((self.local / "files/csu/x.csv").read_bytes(), b"version 3", "replaced in place")

    def test_only_and_all_versions(self):
        self.standard_server()
        self.pull(only=["csu"], all_versions=True)
        self.assertEqual(self.local_files(), ["csu/2026-10-01_x.csv", "csu/x.csv"],
                         "older versions keep the server's dated name, the newest has the plain one")
        self.assertEqual((self.local / "files/csu/2026-10-01_x.csv").read_bytes(), b"version 1")
        self.pull()
        self.assertIn("csu/2026-10-01_x.csv", self.local_files(), "explicitly fetched versions stay")
        with self.assertRaisesRegex(pull.PullError, "unknown file name.*available: csu, policie"):
            self.pull(only=["nonsense"])

    def test_a_copy_under_another_name_is_moved_not_downloaded(self):
        """E.g. files from the first version of pull, which kept the server's dated names."""
        self.standard_server()
        self.pull()
        state_path = self.local / ".pull.json"
        state = json.loads(state_path.read_text())
        for plain, dated in [("csu/x.csv", "csu/2026-11-01_x.csv"), ("policie/202601.zip", "policie/2026-11-01_202601.zip")]:
            os.replace(self.local / "files" / plain, self.local / "files" / dated)
            state["files"][dated] = state["files"].pop(plain)
        state_path.write_text(json.dumps(state))
        self.site.hits.clear()
        self.assertEqual(self.pull(), [])
        self.assertEqual(self.site.hits, ["/api/exports/"], "nothing downloaded")
        self.assertEqual(self.local_files(), ["csu/x.csv", "policie/202601.zip", "policie/202602.zip"])

    def test_same_file_name_from_two_urls_keeps_the_server_names(self):
        self.add_file("dup", "https://a/data.csv", "2026-11-01_data.csv", b"from a")
        self.add_file("dup", "https://b/data.csv", "2026-11-01-2_data.csv", b"from b")
        self.snapshot("2026-11-02")
        self.assertEqual(self.pull(), [])
        self.assertEqual(self.local_files(), ["dup/2026-11-01-2_data.csv", "dup/2026-11-01_data.csv"])

    def test_database_only(self):
        self.standard_server()
        self.pull(files=False)
        self.assertTrue((self.local / "collector.sqlite").is_file())
        self.assertFalse((self.local / "files").exists())

    def test_wrong_password(self):
        self.standard_server()
        with self.assertRaisesRegex(pull.PullError, "wrong password"):
            self.pull(password=lambda base: "spatne")
        self.assertFalse((self.local / "collector.sqlite").exists())
        self.assertFalse((self.local / ".pull-session").exists())

    def test_expired_session_asks_for_the_password_again(self):
        self.standard_server()
        self.local.mkdir(parents=True)
        (self.local / ".pull-session").write_text(json.dumps({"url": self.site.url, "cookie": "user.1.x"}))
        self.assertEqual(self.pull(), [])
        self.assertEqual(self.asked, 1)

    def test_a_damaged_file_is_reported_and_not_kept(self):
        self.standard_server()
        (self.server_dir / "files/policie/2026-11-01_202601.zip").write_bytes(b"JAN")  # same size, other content
        self.assertEqual(self.pull(), ["policie/202601.zip"])
        self.assertEqual(self.local_files(), ["csu/x.csv", "policie/202602.zip"])

    def test_a_truncated_snapshot_keeps_the_old_database(self):
        self.standard_server()
        self.pull()
        self.snapshot("2026-12-02")
        gz = self.server_dir / "exports/collector-2026-12-02.sqlite.gz"
        gz.write_bytes(gz.read_bytes()[:-100])
        with self.assertRaisesRegex(pull.PullError, "incomplete|damaged"):
            self.pull()
        con = sqlite3.connect(self.local / "collector.sqlite")
        self.addCleanup(con.close)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM download").fetchone()[0], 4)
        self.assertEqual([p.name for p in self.local.iterdir() if p.name.endswith(".part")], [])

    def test_no_snapshot_yet(self):
        with self.assertRaisesRegex(pull.PullError, "no database snapshot yet"):
            self.pull()

    def test_one_command_without_the_repository(self):
        """What a teammate types: the script comes from the site, nothing is installed or cloned."""
        self.standard_server()
        work = Path(tempfile.mkdtemp())
        one_liner = (f"import sys; sys.modules['requests'] = None; "  # prove it needs no third-party package
                     f"import urllib.request as u; exec(u.urlopen('{self.site.url}/pull.py').read())")
        cmd = [sys.executable, "-I", "-c", one_liner, "--url", self.site.url]  # -I: the repository is not importable
        env = dict(os.environ, BIDS_PASSWORD=PASSWORD)
        first = subprocess.run(cmd, cwd=work, env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertTrue((work / "bids-data" / "collector.sqlite").is_file(), "default folder ./bids-data")
        self.assertTrue((work / "bids-data" / "files" / "policie" / "202602.zip").is_file())
        again = subprocess.run(cmd, cwd=work, env=dict(env, BIDS_PASSWORD=""), capture_output=True, text=True,
                               timeout=60, stdin=subprocess.DEVNULL)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("already here", again.stderr)

    def test_password_in_the_command(self):
        """Unattended, e.g. for the teacher: nothing is asked, a wrong password fails cleanly."""
        self.standard_server()
        with mock.patch.dict(os.environ, {"BIDS_PASSWORD": ""}), \
                mock.patch.object(pull.getpass, "getpass", side_effect=AssertionError("must not ask")):
            self.assertEqual(pull.main(["--url", self.site.url, "--data", str(self.local), "--password", PASSWORD]), 0)
            self.assertTrue((self.local / "collector.sqlite").is_file())
            other = str(self.local.parent / "other")
            self.assertEqual(pull.main(["--url", self.site.url, "--data", other, "--password", "spatne"]), 1)

    def test_command_line_needs_no_server_config(self):
        self.standard_server()
        env = dict(os.environ, BIDS_PASSWORD=PASSWORD, PYTHONPATH=ROOT)
        env.pop("COLLECTOR_CONFIG", None)
        cmd = [sys.executable, "-m", "collector", "pull", "--url", self.site.url, "--data", str(self.local)]
        ok = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertTrue((self.local / "collector.sqlite").is_file())
        bad = subprocess.run(cmd + ["--data", str(self.local.parent / "other")], timeout=60,
                             env=dict(env, BIDS_PASSWORD="spatne"), capture_output=True, text=True)
        self.assertEqual(bad.returncode, 1)
        self.assertIn("wrong password", bad.stderr)


if __name__ == "__main__":
    unittest.main()
