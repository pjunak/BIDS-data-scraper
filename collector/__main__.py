"""Command line and scheduler: `python -m collector <command>`.

`daemon` is what the server runs; the other commands run one job and exit.
"""
import argparse
import logging
import logging.handlers
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import config, db, export, jobs, trigger
from .sources import SOURCES

log = logging.getLogger("collector")

WAKE_UP_S = 3600  # the daemon checks due downloads at least this often


def next_run_at(now: datetime, hour: int, minute: int) -> datetime:
    """The next time the clock shows hour:minute, strictly after now."""
    run_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return run_at if run_at > now else run_at + timedelta(days=1)


def daemon(cfg: dict):
    """Run forever: listings daily at schedule.listings_at, downloads when due, manual runs on request."""
    schedule = cfg["schedule"]
    tz = ZoneInfo(os.environ.get("TZ", "Europe/Prague"))
    hour, minute = map(int, schedule["listings_at"].split(":"))

    with db.open_db(cfg["db_path"]) as con:  # creates the database right away, so Datasette can open it
        interrupted = db.fail_interrupted_runs(con)
    if interrupted:
        log.warning("marked %s runs interrupted by the last restart as failed", interrupted)
    log.info("daemon started, listings daily at %02d:%02d %s", hour, minute, tz)

    def scrape():
        jobs.run_listings(cfg)
        jobs.run_snapshot(cfg)

    manual = trigger.Trigger(cooldown_s=schedule.get("manual_cooldown_min", 60) * 60)
    trigger.serve(manual, int(os.environ.get("TRIGGER_PORT", "8082")))
    if schedule.get("run_on_start", False):
        manual.run(scrape)

    while True:
        jobs.run_downloads(cfg)  # each item decides itself whether it is due
        now = datetime.now(tz)
        run_at = next_run_at(now, hour, minute)
        timeout = max(min((run_at - now).total_seconds(), WAKE_UP_S), 1)
        if manual.wait(timeout) or datetime.now(tz) >= run_at:
            manual.run(scrape)


def print_status(cfg: dict):
    """Last run of every job and the number of listings collected per portal."""
    with db.open_db(cfg["db_path"]) as con:
        print("Last runs:")
        for r in con.execute("SELECT job, started_at, status, items, message FROM run "
                             "WHERE id IN (SELECT MAX(id) FROM run GROUP BY job) ORDER BY job"):
            print(f"  {r['job']:<32} {r['status']:<8} {r['items']:>7}  {r['started_at']}  {r['message'] or ''}")
        print("\nListings per source (distinct listings / snapshots):")
        for r in con.execute("SELECT source, COUNT(DISTINCT listing_id) AS listings, COUNT(*) AS snapshots "
                             "FROM listing_snapshot GROUP BY source"):
            print(f"  {r['source']:<12} {r['listings']:>8} {r['snapshots']:>10}")


def setup_logging():
    """Log to stdout (docker logs) and to the rotating file that the dashboard shows."""
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    log_dir = os.environ.get("LOG_DIR", "/data/logs")
    file_error = None
    try:
        os.makedirs(log_dir, exist_ok=True)
        handlers.append(logging.handlers.RotatingFileHandler(
            os.path.join(log_dir, "collector.log"), maxBytes=5_000_000, backupCount=5, encoding="utf-8"))
    except OSError as e:  # e.g. a local run without /data
        file_error = e
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if file_error:
        log.warning("logging to stdout only, cannot write %s: %s", log_dir, file_error)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="collector", description="BIDS real-estate data collector")
    parser.add_argument("--config", help="path to config.yaml (default: $COLLECTOR_CONFIG)")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("daemon", help="run forever on the schedule")

    listings = commands.add_parser("listings", help="scrape listings once")
    listings.add_argument("--source", choices=sorted(SOURCES))
    listings.add_argument("--max-pages", type=int, help="limit pages per category (for testing)")

    downloads = commands.add_parser("downloads", help="download secondary datasets once")
    downloads.add_argument("--force", action="store_true", help="ignore every_days")
    downloads.add_argument("--only", help="name of one download item")

    csv_export = commands.add_parser("export", help="export tables to CSV")
    csv_export.add_argument("--out", default="/data/export")
    csv_export.add_argument("--since", help="only snapshots from this ISO date, e.g. 2026-11-01")

    commands.add_parser("snapshot", help="create the downloadable gzip snapshot of the database")
    commands.add_parser("status", help="show the last runs and counts")
    return parser


def main():
    setup_logging()
    args = build_parser().parse_args()
    cfg = config.load(args.config)
    if args.command == "daemon":
        daemon(cfg)
    elif args.command == "listings":
        jobs.run_listings(cfg, only=args.source, max_pages=args.max_pages)
    elif args.command == "downloads":
        jobs.run_downloads(cfg, force=args.force, only=args.only)
    elif args.command == "export":
        export.export(cfg, args.out, since=args.since)
    elif args.command == "snapshot":
        jobs.run_snapshot(cfg)
    elif args.command == "status":
        print_status(cfg)


if __name__ == "__main__":
    main()
