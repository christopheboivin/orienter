import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Raw daily step counts. Steps (not distance) are the source of truth so the
-- game survives a change of watch/vendor or of stride length.
CREATE TABLE IF NOT EXISTS daily_steps (
    day        TEXT PRIMARY KEY,   -- ISO date
    steps      INTEGER NOT NULL,
    source     TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);

-- The journey, as an ordered list of legs. Legs are planned once and never
-- re-routed, so the position never jumps.
CREATE TABLE IF NOT EXISTS legs (
    seq          INTEGER PRIMARY KEY,
    mode         TEXT NOT NULL,     -- walk | ferry | flight
    geometry     TEXT NOT NULL,     -- JSON [[lat, lon], ...]
    length_m     REAL NOT NULL,     -- geometric length
    walk_start_m REAL NOT NULL,     -- walked distance at which this leg starts
    walk_m       REAL NOT NULL,     -- walking distance it consumes (0 for ferry/flight)
    label        TEXT,
    places_done  INTEGER NOT NULL DEFAULT 0,  -- crossed places detected?
    created_at   TEXT NOT NULL
);

-- Cities / regions / countries entered along the planned route.
CREATE TABLE IF NOT EXISTS places (
    id        INTEGER PRIMARY KEY,
    kind      TEXT NOT NULL,        -- country | region | city
    osm_id    TEXT NOT NULL,
    name      TEXT NOT NULL,
    at_walk_m REAL NOT NULL,
    lat       REAL NOT NULL,
    lon       REAL NOT NULL,
    wikipedia TEXT,
    wikidata  TEXT,
    summary   TEXT,
    thumbnail TEXT,
    url       TEXT,
    UNIQUE (kind, osm_id)
);

-- Raised when the walker can't continue east (coast, ...); the player chooses.
CREATE TABLE IF NOT EXISTS decisions (
    id          INTEGER PRIMARY KEY,
    created_at  TEXT NOT NULL,
    lat         REAL NOT NULL,
    lon         REAL NOT NULL,
    walk_m      REAL NOT NULL,
    reason      TEXT NOT NULL,
    choice      TEXT,
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS http_cache (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def connect(path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.execute("PRAGMA journal_mode = WAL")  # web requests read while a sync writes
    conn.executescript(SCHEMA)
    return conn


def get_setting(conn: sqlite3.Connection, key: str, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def set_setting(conn: sqlite3.Connection, key: str, value) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value)),
    )


def cache_get(conn: sqlite3.Connection, key: str):
    row = conn.execute("SELECT value FROM http_cache WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else None


def cache_put(conn: sqlite3.Connection, key: str, value) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO http_cache (key, value, fetched_at) VALUES (?, ?, ?)",
        (key, json.dumps(value), now_iso()),
    )
    conn.commit()
