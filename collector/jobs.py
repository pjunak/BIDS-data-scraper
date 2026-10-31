import logging
from pathlib import Path

from . import db, downloads, snapshots
from .http import Http
from .sources import SOURCES

log = logging.getLogger(__name__)


def make_http(cfg) -> Http:
    return Http(cfg["user_agent"], cfg.get("request_delay_s", 1.0))


def run_listings(cfg, only: str | None = None, max_pages: int | None = None):
    con = db.connect(cfg["db_path"])
    http = make_http(cfg)
    for name, scfg in cfg["listings"].items():
        if not scfg.get("enabled", True) or (only and name != only):
            continue
        mod = SOURCES[name]
        run_id = db.start_run(con, f"listings:{name}")
        count = new = 0
        try:
            for rec, payload in mod.iter_listings(http, scfg, max_pages=max_pages):
                is_new = db.save_listing(con, run_id, rec, payload)
                count += 1
                if is_new:
                    new += 1
                if count % 500 == 0:
                    con.commit()
            con.commit()
            details = 0
            if mod.fetch_detail and scfg.get("fetch_details_for_new", False):
                details = _fetch_new_details(con, http, name, mod, run_id, scfg.get("max_details_per_run", 3000))
            db.finish_run(con, run_id, "ok", count, f"new={new} details={details}")
            log.info("listings %s: %s snapshots, %s new, %s details", name, count, new, details)
        except Exception as e:
            con.commit()  # keep what we got; run is marked failed so ETL can ignore it
            log.exception("listings %s failed after %s items", name, count)
            db.finish_run(con, run_id, "failed", count, repr(e))
    con.close()


def _fetch_new_details(con, http, name, mod, run_id, limit) -> int:
    ids = [r["listing_id"] for r in con.execute(
        "SELECT listing_id FROM listing_snapshot WHERE run_id=? AND source=?", (run_id, name))]
    todo = [i for i in ids if not db.has_detail(con, name, i)][:limit]
    done = 0
    for lid in todo:
        try:
            db.save_detail(con, name, lid, mod.fetch_detail(http, lid))
            done += 1
        except Exception as e:  # listing may have disappeared in the meantime
            log.warning("detail %s/%s failed: %r", name, lid, e)
        if done % 200 == 0:
            con.commit()
    con.commit()
    return done


def run_downloads(cfg, force=False, only=None):
    con = db.connect(cfg["db_path"])
    downloads.run_all(con, make_http(cfg), Path(cfg["files_dir"]), cfg.get("downloads", []), force, only)
    con.close()


def run_snapshot(cfg):
    """Create the downloadable DB snapshot; recorded as a run so it shows on the dashboard."""
    con = db.connect(cfg["db_path"])
    run_id = db.start_run(con, "export:snapshot")
    try:
        path = snapshots.make_snapshot(cfg["db_path"], cfg["exports_dir"], cfg.get("exports_keep", 7))
        db.finish_run(con, run_id, "ok", 1, path.name)
    except Exception as e:
        log.exception("snapshot failed")
        db.finish_run(con, run_id, "failed", 0, repr(e))
    con.close()
