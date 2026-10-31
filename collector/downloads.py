"""Secondary datasets: plain file downloads, kept with history and de-duplicated by content hash.

type: direct    download `url` (optionally saved as `filename`)
type: discover  fetch `page_url` and download every link matching `link_regex`, e.g. all quarterly
                XLSX files of the MF rent price map. `link_template` builds the URL from each match
                ("https://x/{}.zip"); `recheck_last: N` re-fetches the N newest links on every run.
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

CHUNK = 1024 * 1024


def run_all(con, http, files_dir: Path, items: list[dict], force: bool = False, only: str | None = None):
    """Run every enabled item that is due (or all with force); each is its own run in the run table."""
    for item in items:
        if not item.get("enabled", True) or only not in (None, item["name"]):
            continue
        if not force and not _is_due(con, item):
            continue
        run_id = db.start_run(con, f"download:{item['name']}")
        try:
            db.finish_run(con, run_id, "ok", run_item(con, http, files_dir, item))
        except Exception as e:  # one broken source must not stop the others
            log.exception("download %s failed", item["name"])
            db.finish_run(con, run_id, "failed", 0, repr(e))


def run_item(con, http, files_dir: Path, item: dict) -> int:
    """Download one item; returns the number of new file versions stored."""
    if item["type"] == "direct":
        return int(_save(con, http, files_dir, item["name"], item["url"], item.get("filename")))
    if item["type"] == "discover":
        links = _discover_links(http, item)
        known = {r["url"] for r in con.execute("SELECT url FROM download WHERE name = ?", (item["name"],))}
        # Dated archive files never change, so only new URLs are fetched, plus the newest
        # `recheck_last` ones for sources that keep regenerating recent files.
        recheck = set(links[-item["recheck_last"]:]) if item.get("recheck_last") else set()
        todo = [url for url in links if url not in known or url in recheck]
        return sum(_save(con, http, files_dir, item["name"], url, None) for url in todo)
    raise ValueError(f"unknown download type {item['type']!r}")


def _is_due(con, item: dict) -> bool:
    row = con.execute("SELECT MAX(started_at) AS last FROM run WHERE job = ? AND status = 'ok'",
                      (f"download:{item['name']}",)).fetchone()
    if not row["last"]:
        return True
    age = datetime.now(timezone.utc) - datetime.fromisoformat(row["last"])
    return age >= timedelta(days=item.get("every_days", 7))


def _discover_links(http, item: dict) -> list[str]:
    """Sorted absolute URLs of the files linked from page_url; fails loudly when nothing matches."""
    page = http.get(item["page_url"]).text
    template = item.get("link_template", "{}")
    links = sorted({urljoin(item["page_url"], template.format(match))
                    for match in re.findall(item["link_regex"], page)})
    if not links:
        raise RuntimeError(f"no links matching {item['link_regex']!r} on {item['page_url']}")
    return links


def _save(con, http, files_dir: Path, name: str, url: str, filename: str | None) -> bool:
    """Download url and keep it only if its content differs from every stored version of the item.

    Streams to disk, so large files (the ČSÚ population CSV is ~200 MB) never sit in memory.
    """
    filename = filename or unquote(Path(urlparse(url).path).name) or "data.bin"
    folder = files_dir / name
    folder.mkdir(parents=True, exist_ok=True)
    part = folder / f".{filename}.part"
    sha, size = hashlib.sha256(), 0
    try:
        with http.get(url, stream=True) as response, open(part, "wb") as f:
            for chunk in response.iter_content(CHUNK):
                sha.update(chunk)
                f.write(chunk)
                size += len(chunk)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    digest = sha.hexdigest()
    if con.execute("SELECT 1 FROM download WHERE name = ? AND sha256 = ?", (name, digest)).fetchone():
        part.unlink()
        log.info("download %s: %s unchanged", name, filename)
        return False

    stamp = datetime.now().strftime("%Y-%m-%d")
    target, n = folder / f"{stamp}_{filename}", 1
    while target.exists():  # a second new version on the same day must not overwrite the first
        n += 1
        target = folder / f"{stamp}-{n}_{filename}"
    os.replace(part, target)
    con.execute("INSERT INTO download(name, url, fetched_at, path, sha256, bytes) VALUES (?, ?, ?, ?, ?, ?)",
                (name, url, db.now(), str(target), digest, size))
    con.commit()
    log.info("download %s: saved %s (%.1f kB)", name, target, size / 1024)
    return True
