"""Export tables to CSV (UTF-8 with BOM and ';', so Excel and SSIS read Czech characters correctly)."""
import csv
import logging
from pathlib import Path

from . import db

log = logging.getLogger(__name__)

TABLES = ["run", "listing_snapshot", "listing_lifecycle", "listing_attributes",
          "listing_payload", "listing_detail", "download"]


def export(cfg: dict, out_dir: str, tables: list[str] | None = None, since: str | None = None):
    """Write one <table>.csv per table; `since` limits listing_snapshot to rows scraped from that date."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    with db.open_db(cfg["db_path"]) as con:
        for table in tables or TABLES:
            if table not in TABLES:
                raise ValueError(f"unknown table {table!r}")
            sql, args = f"SELECT * FROM {table}", ()
            if since and table == "listing_snapshot":
                sql, args = sql + " WHERE scraped_at >= ?", (since,)
            cursor = con.execute(sql, args)
            path = out / f"{table}.csv"
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f, delimiter=";")
                writer.writerow([column[0] for column in cursor.description])
                rows = 0
                for row in cursor:
                    writer.writerow(row)
                    rows += 1
            log.info("exported %s rows -> %s", rows, path)
