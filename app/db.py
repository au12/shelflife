import json
import os
import sqlite3
import threading
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS libraries (
    section_id   INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    type         TEXT NOT NULL,          -- movie | show
    item_count   INTEGER DEFAULT 0
);

-- Movies and whole TV shows: the rows of the main culling list
CREATE TABLE IF NOT EXISTS items (
    rating_key    INTEGER PRIMARY KEY,
    section_id    INTEGER NOT NULL,
    kind          TEXT NOT NULL,         -- movie | show
    title         TEXT NOT NULL,
    sort_title    TEXT,
    year          INTEGER,
    thumb         TEXT,
    added_at      INTEGER,
    size          INTEGER DEFAULT 0,
    season_count  INTEGER DEFAULT 0,
    episode_count INTEGER DEFAULT 0,
    resolution    TEXT
);
CREATE INDEX IF NOT EXISTS items_section ON items(section_id);

CREATE TABLE IF NOT EXISTS seasons (
    rating_key    INTEGER PRIMARY KEY,
    show_key      INTEGER NOT NULL,
    section_id    INTEGER NOT NULL,
    season_index  INTEGER,
    title         TEXT,
    thumb         TEXT,
    added_at      INTEGER,
    size          INTEGER DEFAULT 0,
    episode_count INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS seasons_show ON seasons(show_key);

-- Tautulli history (grouped the same way Tautulli's own history table is)
CREATE TABLE IF NOT EXISTS history (
    row_id                 INTEGER PRIMARY KEY,
    section_id             INTEGER,
    media_type             TEXT,
    rating_key             INTEGER,
    parent_rating_key      INTEGER,
    grandparent_rating_key INTEGER,
    user_id                INTEGER,
    user                   TEXT,
    friendly_name          TEXT,
    date                   INTEGER,
    started                INTEGER,
    stopped                INTEGER,
    paused                 INTEGER,
    duration               INTEGER,
    full_title             TEXT,
    title                  TEXT,
    season                 INTEGER,
    episode                INTEGER,
    percent_complete       INTEGER,
    watched_status         REAL,
    group_count            INTEGER
);
CREATE INDEX IF NOT EXISTS history_rk ON history(rating_key);
CREATE INDEX IF NOT EXISTS history_prk ON history(parent_rating_key);
CREATE INDEX IF NOT EXISTS history_gprk ON history(grandparent_rating_key);

CREATE TABLE IF NOT EXISTS exclusions (
    rating_key  INTEGER PRIMARY KEY,
    kind        TEXT,
    title       TEXT,
    year        INTEGER,
    note        TEXT,
    created_at  INTEGER
);

CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

DEFAULT_SETTINGS = {
    "ignored_users": [],        # user ids whose plays don't count as "watched"
    "min_play_minutes": 0,      # plays shorter than this don't count
}

_lock = threading.Lock()


def connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


_conn: sqlite3.Connection | None = None


def init() -> None:
    global _conn
    _conn = connect()
    _conn.executescript(SCHEMA)
    _conn.commit()


@contextmanager
def tx():
    """Serialised access to the shared connection."""
    assert _conn is not None
    with _lock:
        try:
            yield _conn
            _conn.commit()
        except Exception:
            _conn.rollback()
            raise


def query(sql: str, args=()) -> list[dict]:
    with tx() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def kv_get(key: str, default=None):
    rows = query("SELECT value FROM kv WHERE key=?", (key,))
    if not rows:
        return default
    try:
        return json.loads(rows[0]["value"])
    except ValueError:
        return default


def kv_set(key: str, value) -> None:
    with tx() as c:
        c.execute(
            "INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )


def get_settings() -> dict:
    s = dict(DEFAULT_SETTINGS)
    s.update(kv_get("settings", {}) or {})
    return s


def save_settings(new: dict) -> dict:
    s = get_settings()
    if "ignored_users" in new:
        s["ignored_users"] = [int(u) for u in new["ignored_users"] if str(u).lstrip("-").isdigit()]
    if "min_play_minutes" in new:
        s["min_play_minutes"] = max(0, int(float(new["min_play_minutes"] or 0)))
    kv_set("settings", s)
    return s
