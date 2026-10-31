import argparse
import logging
import logging.handlers
import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import config, db, export, jobs

log = logging.getLogger("collector")


def daemon(cfg):
    """Runs forever: listings once a day at schedule.listings_at, downloads when due."""
    tz = ZoneInfo(os.environ.get("TZ", "Europe/Prague"))
    hh, mm = map(int, cfg["schedule"]["listings_at"].split(":"))
    db.connect(cfg["db_path"]).close()  # create the DB right away so Datasette can open it
    log.info("daemon started, listings daily at %02d:%02d %s", hh, mm, tz)
    if cfg["schedule"].get("run_on_start", False):
        jobs.run_listings(cfg)
        jobs.run_snapshot(cfg)
    while True:
        jobs.run_downloads(cfg)  # each item decides itself whether it is due
        now = datetime.now(tz)
        nxt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if nxt <= now:
            nxt += timedelta(days=1)
        # wake up at most hourly so downloads are checked and logs show we are alive
        sleep_s = min((nxt - now).total_seconds(), 3600)
        time.sleep(max(sleep_s, 1))
        if datetime.now(tz) >= nxt:
            jobs.run_listings(cfg)
            jobs.run_snapshot(cfg)


def status(cfg):
    con = db.connect(cfg["db_path"])
    print("Last runs:")
    for r in con.execute("SELECT job, started_at, finished_at, status, items, message FROM run "
                         "WHERE id IN (SELECT MAX(id) FROM run GROUP BY job) ORDER BY job"):
        print(f"  {r['job']:<32} {r['status']:<8} {r['items']:>7}  {r['started_at']}  {r['message'] or ''}")
    print("\nListings per source (distinct ids / snapshots):")
    for r in con.execute("SELECT source, COUNT(DISTINCT listing_id) d, COUNT(*) n FROM listing_snapshot GROUP BY source"):
        print(f"  {r['source']:<12} {r['d']:>8} {r['n']:>10}")


def setup_logging():
    """Log to stdout (docker logs) and to a rotating file that the web dashboard shows."""
    handlers = [logging.StreamHandler()]
    log_dir = os.environ.get("LOG_DIR", "/data/logs")
    try:
        os.makedirs(log_dir, exist_ok=True)
        handlers.append(logging.handlers.RotatingFileHandler(
            os.path.join(log_dir, "collector.log"), maxBytes=5_000_000, backupCount=5, encoding="utf-8"))
    except OSError:
        pass  # e.g. local run without /data – stdout is enough
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main():
    setup_logging()
    p = argparse.ArgumentParser(prog="collector")
    p.add_argument("--config")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("daemon", help="run forever on schedule")
    l = sub.add_parser("listings", help="scrape listings once")
    l.add_argument("--source", choices=["sreality", "bezrealitky", "idnes"])
    l.add_argument("--max-pages", type=int, help="limit pages per category (for testing)")
    d = sub.add_parser("downloads", help="download secondary datasets once")
    d.add_argument("--force", action="store_true", help="ignore every_days")
    d.add_argument("--only", help="name of one download item")
    e = sub.add_parser("export", help="export tables to CSV")
    e.add_argument("--out", default="/data/export")
    e.add_argument("--since", help="only snapshots from this ISO date, e.g. 2026-11-01")
    sub.add_parser("snapshot", help="create downloadable gzip snapshot of the database")
    sub.add_parser("status", help="show last runs and counts")
    a = p.parse_args()

    cfg = config.load(a.config)
    if a.cmd == "daemon":
        daemon(cfg)
    elif a.cmd == "listings":
        jobs.run_listings(cfg, a.source, a.max_pages)
    elif a.cmd == "downloads":
        jobs.run_downloads(cfg, a.force, a.only)
    elif a.cmd == "export":
        export.export(cfg, a.out, since=a.since)
    elif a.cmd == "snapshot":
        jobs.run_snapshot(cfg)
    elif a.cmd == "status":
        status(cfg)


if __name__ == "__main__":
    main()
