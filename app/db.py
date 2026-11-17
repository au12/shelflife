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
    art           TEXT,
    added_at      INTEGER,
    size          INTEGER DEFAULT 0,
    season_count  INTEGER DEFAULT 0,
    episode_count INTEGER DEFAULT 0,
    resolution    TEXT,
    quality       TEXT,                  -- JSON: resolution / codec counts, versions
    guids         TEXT                   -- JSON: {"tmdb": "...", "tvdb": "...", "imdb": "..."}
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
    episode_count INTEGER DEFAULT 0,
    quality       TEXT
);
CREATE INDEX IF NOT EXISTS seasons_show ON seasons(show_key);

-- Tautulli history, grouped the way Tautulli's own history table is. row_id holds Tautulli's
-- reference_id, which stays the same when a paused play is resumed later.
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

-- Which Sonarr series / Radarr movie a Plex item maps to
CREATE TABLE IF NOT EXISTS arr_links (
    rating_key  INTEGER PRIMARY KEY,
    service     TEXT,
    arr_id      INTEGER,
    checked_at  INTEGER
);

-- Titles deleted through Shelflife, hidden until Plex notices the files are gone
CREATE TABLE IF NOT EXISTS deleted (
    rating_key  INTEGER PRIMARY KEY,
    kind        TEXT,
    title       TEXT,
    size        INTEGER,
    deleted_at  INTEGER
);

-- Permanent record of everything deleted through Shelflife
CREATE TABLE IF NOT EXISTS deletions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          INTEGER NOT NULL,
    rating_key  INTEGER,
    kind        TEXT,                    -- movie | show | season
    title       TEXT,
    year        INTEGER,
    thumb       TEXT,
    size        INTEGER DEFAULT 0,
    service     TEXT,                    -- Radarr | Sonarr
    action      TEXT,                    -- what happened, in words
    source      TEXT                     -- manual | bulk | review | leaving
);
CREATE INDEX IF NOT EXISTS deletions_at ON deletions(at);

-- Leaving soon: deleted on delete_at unless someone watches them first
CREATE TABLE IF NOT EXISTS leaving (
    rating_key    INTEGER PRIMARY KEY,
    kind          TEXT,                  -- movie | show | season
    title         TEXT,
    year          INTEGER,
    section_id    INTEGER,
    thumb         TEXT,
    size          INTEGER DEFAULT 0,
    scheduled_at  INTEGER NOT NULL,
    delete_at     INTEGER NOT NULL,
    mode          TEXT DEFAULT 'remove', -- shows: remove | files
    exclusion     INTEGER DEFAULT 1,
    unmonitor     INTEGER DEFAULT 1,
    status        TEXT DEFAULT 'scheduled', -- scheduled | spared | deleted | cancelled | gone
    status_at     INTEGER,
    note          TEXT,
    attempts      INTEGER DEFAULT 0,
    last_attempt  INTEGER
);

-- Overseerr / Jellyseerr requests
CREATE TABLE IF NOT EXISTS requests (
    request_id        INTEGER PRIMARY KEY,
    media_type        TEXT,              -- movie | tv
    tmdb_id           INTEGER,
    tvdb_id           INTEGER,
    rating_key        INTEGER,
    rating_key_4k     INTEGER,
    requested_by      TEXT,
    requested_by_plex TEXT,
    requested_at      INTEGER,
    seasons           TEXT               -- JSON list of season numbers
);
CREATE INDEX IF NOT EXISTS requests_rk ON requests(rating_key);

-- File and metadata details fetched for the detail panel
CREATE TABLE IF NOT EXISTS meta_cache (
    rating_key  INTEGER PRIMARY KEY,
    data        TEXT,
    fetched_at  INTEGER
);

-- External ids looked up for Plex titles when the sync doesn't store them (no Plex token)
CREATE TABLE IF NOT EXISTS guid_cache (
    rating_key  INTEGER PRIMARY KEY,
    guids       TEXT,
    fetched_at  INTEGER
);

-- Issues the user has dismissed, by issue key
CREATE TABLE IF NOT EXISTS issue_ignores (
    key         TEXT PRIMARY KEY,
    created_at  INTEGER
);

CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

# Columns added after the first release; existing databases get them on start.
MIGRATIONS = {
    "items": {"art": "TEXT", "quality": "TEXT", "guids": "TEXT"},
    "seasons": {"quality": "TEXT"},
}

DEFAULT_SETTINGS = {
    "ignored_users": [],                  # user ids whose plays don't count as "watched"
    "min_play_minutes": 0,                # plays shorter than this don't count
    "leaving_days": 14,                   # default countdown for Leaving soon
    "leaving_collection": True,           # keep a "Leaving Soon" collection in Plex (needs a Plex token)
    "leaving_collection_name": "Leaving Soon",
    "leaving_promote": True,              # show that collection on Plex Home
}

_lock = threading.RLock()


def connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


_conn: sqlite3.Connection | None = None


def _migrate(conn: sqlite3.Connection) -> None:
    for table, cols in MIGRATIONS.items():
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, kind in cols.items():
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")


def init() -> None:
    global _conn
    _conn = connect()
    _conn.executescript(SCHEMA)
    _migrate(_conn)
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


def _bool(v) -> bool:
    return v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")


def save_settings(new: dict) -> dict:
    s = get_settings()
    if "ignored_users" in new:
        s["ignored_users"] = [int(u) for u in new["ignored_users"] if str(u).lstrip("-").isdigit()]
    if "min_play_minutes" in new:
        s["min_play_minutes"] = max(0, min(600, int(float(new["min_play_minutes"] or 0))))
    if "leaving_days" in new:
        s["leaving_days"] = max(1, min(365, int(float(new["leaving_days"] or 14))))
    for key in ("leaving_collection", "leaving_promote"):
        if key in new:
            s[key] = _bool(new[key])
    if "leaving_collection_name" in new:
        name = " ".join(str(new["leaving_collection_name"] or "").split())[:60]
        s["leaving_collection_name"] = name or DEFAULT_SETTINGS["leaving_collection_name"]
    kv_set("settings", s)
    return s
