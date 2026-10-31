import hashlib
import json
import sqlite3
from datetime import datetime, timezone

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS run (
    id          INTEGER PRIMARY KEY,
    job         TEXT NOT NULL,          -- e.g. listings:sreality, download:mf_cenova_mapa
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL,          -- running | ok | failed
    items       INTEGER DEFAULT 0,
    message     TEXT
);

-- One row per listing per run: the time series used for prices, listing duration, discounts.
CREATE TABLE IF NOT EXISTS listing_snapshot (
    run_id       INTEGER NOT NULL REFERENCES run(id),
    source       TEXT NOT NULL,
    listing_id   TEXT NOT NULL,
    scraped_at   TEXT NOT NULL,
    offer_type   TEXT,                  -- prodej | pronajem
    estate_type  TEXT,                  -- byt | dum
    title        TEXT,
    price        INTEGER,
    area_m2      REAL,
    disposition  TEXT,
    locality     TEXT,
    lat          REAL,
    lon          REAL,
    url          TEXT,
    payload_hash TEXT,
    PRIMARY KEY (run_id, source, listing_id)
);
CREATE INDEX IF NOT EXISTS ix_snapshot_listing ON listing_snapshot(source, listing_id);

-- Raw list payloads, stored only when they change (keeps the DB small).
CREATE TABLE IF NOT EXISTS listing_payload (
    source       TEXT NOT NULL,
    listing_id   TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    first_seen   TEXT NOT NULL,
    payload      TEXT NOT NULL,
    PRIMARY KEY (source, listing_id, payload_hash)
);

-- Raw detail payloads, fetched once for newly seen listings.
CREATE TABLE IF NOT EXISTS listing_detail (
    source     TEXT NOT NULL,
    listing_id TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    payload    TEXT NOT NULL,
    PRIMARY KEY (source, listing_id, fetched_at)
);

CREATE TABLE IF NOT EXISTS download (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    url        TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    path       TEXT NOT NULL,
    sha256     TEXT NOT NULL,
    bytes      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_download_name ON download(name, fetched_at);

-- Views are recreated on every connect, so a changed definition reaches existing databases.
-- Handy view for the "doba inzerce / zlevnění" KPI.
DROP VIEW IF EXISTS listing_lifecycle;
CREATE VIEW listing_lifecycle AS
SELECT source, listing_id,
       MIN(scraped_at) AS first_seen,
       MAX(scraped_at) AS last_seen,
       COUNT(*)        AS n_snapshots,
       MIN(price)      AS min_price,
       MAX(price)      AS max_price,
       COUNT(DISTINCT price) - 1 AS n_price_changes
FROM listing_snapshot GROUP BY source, listing_id;

-- Property attributes that only the raw payloads carry (KPI: new builds, energy class, condition).
-- Sreality: from the detail fetched once per listing; Bezrealitky: from the newest list payload.
DROP VIEW IF EXISTS listing_attributes;
CREATE VIEW listing_attributes AS
SELECT source, listing_id,
       json_extract(payload, '$.building_condition.name') AS condition,
       json_extract(payload, '$.ownership.name')          AS ownership,
       CASE WHEN json_extract(payload, '$.energy_efficiency_rating_cb.value') BETWEEN 1 AND 7
            THEN substr(json_extract(payload, '$.energy_efficiency_rating_cb.name'), 1, 1) END AS energy_class,
       json_extract(payload, '$.usable_area')             AS usable_area_m2,
       json_extract(payload, '$.estate_area')             AS land_area_m2,
       -- 4 ve výstavbě, 5 developerský projekt, 6 novostavba
       json_extract(payload, '$.building_condition.value') IN (4, 5, 6) AS is_new_build
FROM listing_detail WHERE source = 'sreality'
UNION ALL
SELECT source, listing_id,
       json_extract(payload, '$.condition'),
       NULLIF(json_extract(payload, '$.ownership'), 'UNDEFINED'),
       NULLIF(json_extract(payload, '$.penb'), ''),
       json_extract(payload, '$.surface'),
       json_extract(payload, '$.surfaceLand'),
       json_extract(payload, '$.condition') IN ('NEW', 'CONSTRUCTION', 'PROJECT')
FROM listing_payload p
WHERE source = 'bezrealitky'
  AND first_seen = (SELECT MAX(first_seen) FROM listing_payload q
                    WHERE q.source = p.source AND q.listing_id = p.listing_id);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path, timeout=60)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def start_run(con, job: str) -> int:
    cur = con.execute("INSERT INTO run(job, started_at, status) VALUES (?,?, 'running')", (job, now()))
    con.commit()
    return cur.lastrowid


def finish_run(con, run_id: int, status: str, items: int = 0, message: str | None = None):
    con.execute("UPDATE run SET finished_at=?, status=?, items=?, message=? WHERE id=?",
                (now(), status, items, (message or "")[:2000], run_id))
    con.commit()


def payload_hash(payload) -> str:
    return hashlib.sha1(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def save_listing(con, run_id: int, rec: dict, payload) -> bool:
    """Store one snapshot. Returns True if this listing was never seen before."""
    ts = now()
    h = payload_hash(payload)
    new = con.execute("SELECT 1 FROM listing_snapshot WHERE source=? AND listing_id=? LIMIT 1",
                      (rec["source"], rec["listing_id"])).fetchone() is None
    con.execute("INSERT OR IGNORE INTO listing_payload VALUES (?,?,?,?,?)",
                (rec["source"], rec["listing_id"], h, ts, json.dumps(payload, ensure_ascii=False)))
    con.execute(
        """INSERT OR REPLACE INTO listing_snapshot
           (run_id, source, listing_id, scraped_at, offer_type, estate_type, title, price, area_m2,
            disposition, locality, lat, lon, url, payload_hash)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (run_id, rec["source"], rec["listing_id"], ts, rec.get("offer_type"), rec.get("estate_type"),
         rec.get("title"), rec.get("price"), rec.get("area_m2"), rec.get("disposition"),
         rec.get("locality"), rec.get("lat"), rec.get("lon"), rec.get("url"), h))
    return new


def has_detail(con, source: str, listing_id: str) -> bool:
    return con.execute("SELECT 1 FROM listing_detail WHERE source=? AND listing_id=? LIMIT 1",
                       (source, listing_id)).fetchone() is not None


def save_detail(con, source: str, listing_id: str, payload):
    con.execute("INSERT OR IGNORE INTO listing_detail VALUES (?,?,?,?)",
                (source, listing_id, now(), json.dumps(payload, ensure_ascii=False)))
