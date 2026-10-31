"""Export tables to CSV (UTF-8 with BOM, so Excel and SSIS read Czech characters correctly)."""
import csv
import logging
from pathlib import Path

from . import db

log = logging.getLogger(__name__)
TABLES = ["run", "listing_snapshot", "listing_lifecycle", "listing_payload", "listing_detail", "download"]


def export(cfg, out_dir: str, tables=None, since: str | None = None):
    con = db.connect(cfg["db_path"])
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for t in tables or TABLES:
        sql, args = f"SELECT * FROM {t}", ()
        if since and t == "listing_snapshot":
            sql, args = sql + " WHERE scraped_at >= ?", (since,)
        cur = con.execute(sql, args)
        path = out / f"{t}.csv"
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow([d[0] for d in cur.description])
            n = 0
            for row in cur:
                w.writerow(row)
                n += 1
        log.info("exported %s rows -> %s", n, path)
    con.close()
