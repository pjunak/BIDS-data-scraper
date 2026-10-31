"""The jobs the scheduler and the command line run: listings, downloads, snapshot.

Each job is recorded in the `run` table. A failing source is marked `failed` and never stops the others.
"""
import logging
from pathlib import Path

from . import db, downloads, snapshots
from .http import Http
from .sources import SOURCES

log = logging.getLogger(__name__)

COMMIT_EVERY = 200


def make_http(cfg: dict) -> Http:
    return Http(cfg["user_agent"], cfg.get("request_delay_s", 1.0))


def run_listings(cfg: dict, only: str | None = None, max_pages: int | None = None):
    """Scrape every enabled portal (or just `only`), one run per portal."""
    http = make_http(cfg)
    with db.open_db(cfg["db_path"]) as con:
        for name, source_cfg in cfg["listings"].items():
            if source_cfg.get("enabled", True) and only in (None, name):
                _scrape_source(con, http, name, source_cfg, max_pages)


def _scrape_source(con, http: Http, name: str, source_cfg: dict, max_pages: int | None):
    source = SOURCES[name]
    run_id = db.start_run(con, f"listings:{name}")
    count = new = 0
    try:
        for rec, payload in source.iter_listings(http, source_cfg, max_pages=max_pages):
            new += db.save_listing(con, run_id, rec, payload)
            count += 1
            if count % COMMIT_EVERY == 0:
                con.commit()
        con.commit()
        details = 0
        if source.fetch_detail and source_cfg.get("fetch_details_for_new", False):
            details = _fetch_new_details(con, http, name, source, run_id, source_cfg.get("max_details_per_run", 3000))
        db.finish_run(con, run_id, "ok", count, f"new={new} details={details}")
        log.info("listings %s: %s snapshots, %s new, %s details", name, count, new, details)
    except Exception as e:
        con.commit()  # keep what we got; the run is marked failed so ETL can ignore it
        log.exception("listings %s failed after %s items", name, count)
        db.finish_run(con, run_id, "failed", count, repr(e))


def _fetch_new_details(con, http: Http, name: str, source, run_id: int, limit: int) -> int:
    """Fetch the detail of listings seen for the first time; returns how many were stored."""
    ids = [r["listing_id"] for r in con.execute(
        "SELECT listing_id FROM listing_snapshot WHERE run_id = ? AND source = ?", (run_id, name))]
    todo = [listing_id for listing_id in ids if not db.has_detail(con, name, listing_id)][:limit]
    stored = 0
    for attempt, listing_id in enumerate(todo, start=1):
        try:
            db.save_detail(con, name, listing_id, source.fetch_detail(http, listing_id))
            stored += 1
        except Exception as e:  # the listing may have disappeared in the meantime
            log.warning("detail %s/%s failed: %r", name, listing_id, e)
        if attempt % COMMIT_EVERY == 0:
            con.commit()
    con.commit()
    return stored


def run_downloads(cfg: dict, force: bool = False, only: str | None = None):
    """Download the secondary datasets that are due (all of them with force)."""
    with db.open_db(cfg["db_path"]) as con:
        downloads.run_all(con, make_http(cfg), Path(cfg["files_dir"]), cfg.get("downloads", []), force, only)


def run_snapshot(cfg: dict):
    """Create the downloadable database snapshot; recorded as a run so it shows on the dashboard."""
    with db.open_db(cfg["db_path"]) as con:
        run_id = db.start_run(con, "export:snapshot")
        try:
            path = snapshots.make_snapshot(cfg["db_path"], cfg["exports_dir"], cfg.get("exports_keep", 7))
            db.finish_run(con, run_id, "ok", 1, path.name)
        except Exception as e:
            log.exception("snapshot failed")
            db.finish_run(con, run_id, "failed", 0, repr(e))
