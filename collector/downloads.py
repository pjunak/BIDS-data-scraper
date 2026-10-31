"""Secondary datasets: plain file downloads, kept with history and de-duplicated by content hash.

type: direct    -> download `url`
type: discover  -> fetch `page_url`, download every link matching `link_regex`
                   (e.g. all quarterly XLSX files of the MF rent price map). Optional
                   `link_template` builds the URL from each match ("https://x/{}.zip"),
                   `recheck_last: N` re-fetches the N newest links on every run.
"""
import hashlib
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

from . import db

log = logging.getLogger(__name__)


def _is_due(con, item: dict) -> bool:
    row = con.execute("SELECT MAX(started_at) AS t FROM run WHERE job=? AND status='ok'",
                      (f"download:{item['name']}",)).fetchone()
    if not row["t"]:
        return True
    last = datetime.fromisoformat(row["t"])
    return datetime.now(timezone.utc) - last >= timedelta(days=item.get("every_days", 7))


def _save(con, http, files_dir: Path, name: str, url: str, filename: str | None) -> bool:
    """Download url; keep it only if the content differs from every stored version of that item.

    Streams to disk, so large files (the ČSÚ population CSV is ~200 MB) never sit in memory.
    """
    filename = filename or unquote(Path(urlparse(url).path).name) or "data.bin"
    folder = files_dir / name
    folder.mkdir(parents=True, exist_ok=True)
    part = folder / f".{filename}.part"
    sha, size = hashlib.sha256(), 0
    with http.get(url, stream=True) as r, open(part, "wb") as f:
        for chunk in r.iter_content(1024 * 1024):
            sha.update(chunk)
            f.write(chunk)
            size += len(chunk)
    digest = sha.hexdigest()
    if con.execute("SELECT 1 FROM download WHERE name=? AND sha256=?", (name, digest)).fetchone():
        part.unlink()
        log.info("download %s: %s unchanged", name, filename)
        return False
    stamp = datetime.now().strftime("%Y-%m-%d")
    target, n = folder / f"{stamp}_{filename}", 1
    while target.exists():  # a second new version on the same day must not overwrite the first
        n += 1
        target = folder / f"{stamp}-{n}_{filename}"
    os.replace(part, target)
    con.execute("INSERT INTO download(name, url, fetched_at, path, sha256, bytes) VALUES (?,?,?,?,?,?)",
                (name, url, db.now(), str(target), digest, size))
    con.commit()
    log.info("download %s: saved %s (%.1f kB)", name, target, size / 1024)
    return True


def run_item(con, http, files_dir: Path, item: dict) -> int:
    if item["type"] == "direct":
        return int(_save(con, http, files_dir, item["name"], item["url"], item.get("filename")))
    if item["type"] == "discover":
        html = http.get(item["page_url"]).text
        template = item.get("link_template", "{}")
        links = sorted({urljoin(item["page_url"], template.format(m)) for m in re.findall(item["link_regex"], html)})
        if not links:
            raise RuntimeError(f"no links matching {item['link_regex']!r} on {item['page_url']}")
        known = {r["url"] for r in con.execute("SELECT url FROM download WHERE name=?", (item["name"],))}
        # Dated archive files never change, so only URLs we have not seen yet are fetched, plus the
        # newest `recheck_last` ones for sources that keep regenerating recent files.
        recheck = set(links[-item["recheck_last"]:]) if item.get("recheck_last") else set()
        saved = 0
        for url in links:
            if url in known and url not in recheck:
                continue
            saved += _save(con, http, files_dir, item["name"], url, None)
        return saved
    raise ValueError(f"unknown download type {item['type']!r}")


def run_all(con, http, files_dir: Path, items: list[dict], force: bool = False, only: str | None = None):
    for item in items:
        if not item.get("enabled", True) or (only and item["name"] != only):
            continue
        if not force and not _is_due(con, item):
            continue
        run_id = db.start_run(con, f"download:{item['name']}")
        try:
            n = run_item(con, http, files_dir, item)
            db.finish_run(con, run_id, "ok", n)
        except Exception as e:  # one broken source must not stop the others
            log.exception("download %s failed", item["name"])
            db.finish_run(con, run_id, "failed", 0, repr(e))
