"""Local copy of the data from bids.junak.eu, for ETL and analysis. One command, nothing to install
but Python 3.8+ (on Windows type `python` instead of `python3`):

    python3 -c "import urllib.request as u; exec(u.urlopen('https://bids.junak.eu/pull.py').read())"

In a clone of the repository the same thing is `python -m collector pull`.

It logs in with the team password (the same one as on the website) and downloads
  * the newest nightly database snapshot (/exports/collector-YYYY-MM-DD.sqlite.gz)
    into <data>/collector.sqlite,
  * the secondary files listed in that snapshot's `download` table into <data>/files/<name>/,
    each checked against its SHA-256. By default only the newest version of every file, under
    its original name (without the server's download date), so local paths never change.
Only what changed since the last pull is downloaded. The password is asked when the site wants a
login, or given up front with --password (or $BIDS_PASSWORD) so the command runs unattended. The
session cookie is kept in <data>/.pull-session, so it is needed again only after 30 days.

The website serves this file as it is (nginx: /pull.py), so it uses the standard library only and
imports nothing from the package.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import http.client
import json
import logging
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
import zlib
from http.cookies import CookieError, SimpleCookie
from pathlib import Path

log = logging.getLogger("collector.pull")

DEFAULT_URL = "https://bids.junak.eu"
COOKIE = "bids_session"  # web/auth.py
SNAPSHOT_RE = re.compile(r"^collector-\d{4}-\d{2}-\d{2}\.sqlite\.gz$")
STAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:-\d+)?_(.+)$")  # collector/downloads.py: <date>[-n]_<original name>
DB_NAME = "collector.sqlite"
STATE_NAME = ".pull.json"
SESSION_NAME = ".pull-session"
CHUNK = 1024 * 1024
TRANSFER_ERRORS = (OSError, http.client.HTTPException)  # connection reset, timeout, incomplete read


class PullError(Exception):
    """Something the user has to act on; the message says what."""


class NotLoggedIn(PullError):
    pass


class Unreachable(PullError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # redirects come back as HTTPError: the login answers with one, and so does a lost session


class Site:
    """bids.junak.eu behind its password login (nginx auth_request + web/auth.py)."""

    def __init__(self, base_url: str, timeout_s: float = 60):
        self.base = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.cookie: str | None = None
        self.opener = urllib.request.build_opener(_NoRedirect)

    def _open(self, path: str, data: bytes | None = None):
        # The cookie is `Secure`, which a cookie jar would not send back over plain http (a local
        # test stack), so it is sent by hand.
        headers = {"User-Agent": "BIDS collector pull"}
        if self.cookie:
            headers["Cookie"] = f"{COOKIE}={self.cookie}"
        request = urllib.request.Request(self.base + path, data=data, headers=headers)
        try:
            return self.opener.open(request, timeout=self.timeout_s)
        except urllib.error.HTTPError as e:
            return e  # also a response: status, headers, body
        except TRANSFER_ERRORS as e:
            raise Unreachable(f"cannot reach {self.base}: {getattr(e, 'reason', e)}") from e

    def login(self, password: str):
        r = self._open("/auth/login", urllib.parse.urlencode({"password": password, "next": "/"}).encode())
        code, set_cookie = r.getcode(), r.headers.get("Set-Cookie", "")
        r.close()
        if code == 429:
            raise PullError("too many login attempts, wait a minute and try again")
        try:
            morsel = SimpleCookie(set_cookie).get(COOKIE)
        except CookieError:
            morsel = None
        if code != 303 or not morsel or not morsel.value:
            raise PullError(f"wrong password for {self.base}")
        self.cookie = morsel.value

    def get(self, path: str):
        """GET a path of the site; read it in chunks and close it. Raises NotLoggedIn without a valid session."""
        r = self._open(path)
        code = r.getcode()
        if code == 401 or (code in (301, 302, 303, 307, 308) and r.headers.get("Location", "").startswith("/login")):
            r.close()
            raise NotLoggedIn(f"not logged in to {self.base}")
        if code != 200:
            r.close()
            raise PullError(f"{self.base}{path}: HTTP {code}")
        return r

    def newest_snapshot(self) -> dict | None:
        """{'name', 'size', 'mtime'} of the newest dated snapshot, from nginx's JSON directory listing."""
        r = self.get("/api/exports/")
        try:
            entries = json.loads(r.read().decode("utf-8"))
        except ValueError as e:
            raise PullError(f"{self.base}/api/exports/ did not return JSON; is the URL right?") from e
        except TRANSFER_ERRORS as e:
            raise Unreachable(f"cannot reach {self.base}: {e}") from e
        finally:
            r.close()
        snapshots = [e for e in entries if e.get("type") == "file" and SNAPSHOT_RE.match(e.get("name", ""))]
        return max(snapshots, key=lambda e: e["name"]) if snapshots else None


def _chunks(response):
    return iter(lambda: response.read(CHUNK), b"")


# ---------------------------------------------------------------------------------- local state

def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_json(path: Path, data: dict, private: bool = False):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    if private:
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _replace(src: Path, dest: Path):
    try:
        os.replace(src, dest)
    except PermissionError as e:  # Windows: the file is open in another program
        src.unlink(missing_ok=True)
        raise PullError(f"cannot replace {dest}; close the programs that have it open and run pull again") from e


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ------------------------------------------------------------------------------------ database

def pull_database(site: Site, data: Path, snapshot: dict) -> None:
    """Stream the gzip snapshot, unpack it on the fly and swap it in as <data>/collector.sqlite."""
    dest = data / DB_NAME
    part = data / f".{DB_NAME}.part"
    unzip = zlib.decompressobj(16 + zlib.MAX_WBITS)  # gzip; checks the CRC at the end
    try:
        r = site.get(f"/exports/{urllib.parse.quote(snapshot['name'])}")
        try:
            with open(part, "wb") as f:
                for chunk in _chunks(r):
                    f.write(unzip.decompress(chunk))
                f.write(unzip.flush())
        finally:
            r.close()
        if not unzip.eof:
            raise PullError(f"{snapshot['name']}: download incomplete, run pull again")
        try:
            con = sqlite3.connect(part)
            try:
                ok = con.execute("PRAGMA quick_check").fetchone()[0] == "ok"
            finally:
                con.close()
        except sqlite3.DatabaseError:
            ok = False
        if not ok:
            raise PullError(f"{snapshot['name']} is not a valid database")
    except zlib.error as e:
        part.unlink(missing_ok=True)
        raise PullError(f"{snapshot['name']} is damaged ({e}), run pull again") from e
    except TRANSFER_ERRORS as e:
        part.unlink(missing_ok=True)
        raise PullError(f"{snapshot['name']}: transfer failed ({e}), run pull again") from e
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    for side in ("-wal", "-shm", "-journal"):  # leftovers of the old database would corrupt the new one
        (data / f"{DB_NAME}{side}").unlink(missing_ok=True)
    _replace(part, dest)


# --------------------------------------------------------------------------------------- files

def _safe_part(text: str) -> bool:
    return bool(text) and not text.startswith(".") and "/" not in text and "\\" not in text


def _original_name(server_name: str) -> str:
    match = STAMP_RE.match(server_name)
    return match.group(1) if match else server_name


def wanted_files(db_path: Path, only: list[str] | None, all_versions: bool) -> list[dict]:
    """Files to have locally, from the `download` table: {'name', 'server', 'rel', 'sha256', 'bytes'}.

    `server` is the path under /files/, `rel` the local path under <data>/files/. The newest
    version of every downloaded URL is saved under its original name, so a newer version replaces
    it in place and ETL paths stay fixed. With all_versions the older versions are kept too, under
    the server's dated names."""
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute("SELECT id, name, url, path, sha256, bytes FROM download ORDER BY id").fetchall()
    finally:
        con.close()
    names = {r[1] for r in rows}
    unknown = sorted(set(only or []) - names)
    if unknown:
        raise PullError(f"unknown file name(s): {', '.join(unknown)}; available: {', '.join(sorted(names))}")
    newest: dict[tuple, tuple] = {}
    for row in rows:
        newest[(row[1], row[2])] = row  # rows are ordered by id, so the last one wins
    newest_ids = {row[0] for row in newest.values()}
    chosen = rows if all_versions else sorted(newest.values())
    files = []
    for row_id, name, _, path, sha, size in chosen:
        if only and name not in only:
            continue
        server_name = path.replace("\\", "/").rsplit("/", 1)[-1]  # stored as the server's path
        local_name = _original_name(server_name) if row_id in newest_ids else server_name
        if not (_safe_part(name) and _safe_part(server_name) and _safe_part(local_name)):
            raise PullError(f"unexpected file path in the database: {name}/{server_name}")
        files.append({"name": name, "server": f"{name}/{server_name}", "rel": f"{name}/{local_name}",
                      "sha256": sha, "bytes": size})
    # Two URLs of one item with the same file name: keep the server's (unique) names for those.
    taken: dict[str, int] = {}
    for f in files:
        taken[f["rel"]] = taken.get(f["rel"], 0) + 1
    for f in files:
        if taken[f["rel"]] > 1:
            f["rel"] = f["server"]
    return files


def _is_current(path: Path, want: dict, known: dict | None) -> bool:
    if not path.is_file():
        return False
    st = path.stat()
    if st.st_size != want["bytes"]:
        return False
    if known and known.get("sha256") == want["sha256"] and known.get("mtime_ns") == st.st_mtime_ns:
        return True  # unchanged since we checked it last time, no need to hash it again
    return _sha256(path) == want["sha256"]


def _download_file(site: Site, want: dict, dest: Path):
    part = dest.with_name(f".{dest.name}.part")
    digest, size = hashlib.sha256(), 0
    try:
        r = site.get("/files/" + "/".join(urllib.parse.quote(p) for p in want["server"].split("/")))
        try:
            with open(part, "wb") as f:
                for chunk in _chunks(r):
                    digest.update(chunk)
                    f.write(chunk)
                    size += len(chunk)
        finally:
            r.close()
        if size != want["bytes"] or digest.hexdigest() != want["sha256"]:
            raise PullError(f"{want['rel']}: does not match its SHA-256 from the database")
    except TRANSFER_ERRORS as e:
        part.unlink(missing_ok=True)
        raise PullError(f"{want['rel']}: transfer failed ({e})") from e
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    _replace(part, dest)


def pull_files(site: Site, data: Path, files: list[dict], state: dict, save, keep: bool = False) -> list[str]:
    """Download the missing or changed files; returns the ones that failed.

    keep marks the files as wanted for good (--all-versions), so a later pull never removes them."""
    known = state.setdefault("files", {})

    def remember(w: dict, path: Path):
        keep_it = keep or known.get(w["rel"], {}).get("keep", False)
        known[w["rel"]] = {"sha256": w["sha256"], "mtime_ns": path.stat().st_mtime_ns, "keep": keep_it}

    todo = []
    for w in files:
        path = data / "files" / w["rel"]
        if _is_current(path, w, known.get(w["rel"])):
            remember(w, path)
        else:
            todo.append(w)

    # A file that is already here under another name (an earlier layout, or a renamed version)
    # is moved instead of downloaded again. Only files pull itself saved and nobody changed.
    wanted = {w["rel"] for w in files}
    here: dict[str, str] = {}
    for rel, entry in known.items():
        path = data / "files" / rel
        if rel not in wanted and not entry.get("keep") and path.is_file() \
                and path.stat().st_mtime_ns == entry.get("mtime_ns"):
            here.setdefault(entry["sha256"], rel)
    for w in list(todo):
        old = here.pop(w["sha256"], None)
        if old:
            dest = data / "files" / w["rel"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            _replace(data / "files" / old, dest)
            log.info("renamed files/%s -> files/%s", old, w["rel"])
            remember(w, dest)
            todo.remove(w)
    if todo:
        log.info("%d files to download (%.0f MB)", len(todo), sum(w["bytes"] for w in todo) / 1e6)
    failed = []
    for i, w in enumerate(todo, 1):
        dest = data / "files" / w["rel"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        log.info("[%d/%d] files/%s (%.1f MB)", i, len(todo), w["rel"], w["bytes"] / 1e6)
        try:
            _download_file(site, w, dest)
        except (NotLoggedIn, Unreachable):
            raise
        except PullError as e:
            log.error("%s", e)
            failed.append(w["rel"])
            continue
        remember(w, dest)
        save()

    # Older versions that an earlier pull fetched and a newer one has replaced. Files changed
    # locally are kept, and so is everything pull did not download itself.
    pulled_names = {w["name"] for w in files}
    for rel in sorted(set(known) - wanted):
        path = data / "files" / rel
        if rel.split("/", 1)[0] not in pulled_names or known[rel].get("keep"):
            continue
        if path.is_file() and path.stat().st_mtime_ns == known[rel].get("mtime_ns"):
            path.unlink()
            log.info("removed files/%s (replaced by a newer version)", rel)
        del known[rel]
    save()
    return failed


# ---------------------------------------------------------------------------------------- main

def ask_password(base: str) -> str:
    password = os.environ.get("BIDS_PASSWORD", "")
    if not password:
        try:
            password = getpass.getpass(f"Password for {base}: ")
        except (EOFError, KeyboardInterrupt):
            password = ""
    if not password:
        raise PullError("no password: type it when asked, or set BIDS_PASSWORD")
    return password


def pull(base_url: str, data_dir: str, *, files: bool = True, only: list[str] | None = None,
         all_versions: bool = False, password=ask_password) -> list[str]:
    """Bring data_dir up to date; returns the files that failed (empty = all good)."""
    data = Path(data_dir)
    data.mkdir(parents=True, exist_ok=True)
    site = Site(base_url)
    session_path, state_path = data / SESSION_NAME, data / STATE_NAME
    session = _load_json(session_path)
    if session.get("url") == site.base and session.get("cookie"):
        site.cookie = session["cookie"]
    state = _load_json(state_path)

    def save():
        _write_json(state_path, state)

    def logged_in(call):
        try:
            return call()
        except NotLoggedIn:
            site.login(password(site.base))
            _write_json(session_path, {"url": site.base, "cookie": site.cookie}, private=True)
            return call()

    snapshot = logged_in(site.newest_snapshot)
    db_path = data / DB_NAME
    if snapshot is None:
        raise PullError(f"{site.base} has no database snapshot yet; the first one is made after the nightly scrape")
    if db_path.is_file() and state.get("snapshot") == snapshot:
        log.info("database: %s already here", snapshot["name"])
    else:
        log.info("database: downloading %s (%.1f MB)", snapshot["name"], snapshot["size"] / 1e6)
        logged_in(lambda: pull_database(site, data, snapshot))
        state["snapshot"] = snapshot
        save()
    if not files:
        return []
    return logged_in(lambda: pull_files(site, data, wanted_files(db_path, only, all_versions), state, save,
                                        keep=all_versions))


def add_arguments(parser: argparse.ArgumentParser, default_data: str):
    parser.add_argument("--url", default=os.environ.get("BIDS_URL", DEFAULT_URL),
                        help=f"the site (default: {DEFAULT_URL})")
    parser.add_argument("--data", default=default_data, help=f"local folder for the data (default: ./{default_data})")
    parser.add_argument("--no-files", action="store_true", help="only the database, no secondary files")
    parser.add_argument("--only", action="append", metavar="NAME",
                        help="only this secondary file item (repeatable), e.g. --only csu_mzdy_kraje")
    parser.add_argument("--all-versions", action="store_true",
                        help="every stored version of the files, not just the newest")
    parser.add_argument("--password", help="the site password, so nothing is asked "
                                           "(else $BIDS_PASSWORD, else it is asked when the site wants a login)")


def run(args) -> int:
    """The `pull` command; prints what to do on errors instead of a traceback."""
    password = (lambda base: args.password) if args.password else ask_password
    try:
        failed = pull(args.url, args.data, files=not args.no_files, only=args.only,
                      all_versions=args.all_versions, password=password)
    except PullError as e:
        log.error("pull failed: %s", e)
        return 1
    if failed:
        log.error("%d files failed, run pull again: %s", len(failed), ", ".join(failed))
        return 1
    log.info("done: %s", Path(args.data).resolve())
    return 0


def main(argv: list[str] | None = None) -> int:
    """Standalone entry point: the one-command download, or `python collector/pull.py`."""
    parser = argparse.ArgumentParser(prog="pull", description="Local copy of the BIDS data (database + files); "
                                                             "downloads only what changed.")
    add_arguments(parser, "bids-data")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
