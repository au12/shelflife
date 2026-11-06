import os


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


TAUTULLI_URL = _env("TAUTULLI_URL").rstrip("/")
TAUTULLI_API_KEY = _env("TAUTULLI_API_KEY")

# Optional: talk to Plex directly for file sizes. Much faster for big TV libraries,
# and counts every version of a movie (Tautulli only counts one).
PLEX_URL = _env("PLEX_URL").rstrip("/")
PLEX_TOKEN = _env("PLEX_TOKEN")

SYNC_INTERVAL_HOURS = float(_env("SYNC_INTERVAL_HOURS", "12") or 12)
SYNC_CONCURRENCY = int(_env("SYNC_CONCURRENCY", "4") or 4)
VERIFY_SSL = _env("VERIFY_SSL", "true").lower() not in ("0", "false", "no")

# Comma-separated library names or section ids to skip (e.g. "Home Videos,Kids Movies")
IGNORE_LIBRARIES = [s.strip().lower() for s in _env("IGNORE_LIBRARIES").split(",") if s.strip()]

AUTH_USER = _env("AUTH_USER")
AUTH_PASS = _env("AUTH_PASS")

CONFIG_DIR = _env("CONFIG_DIR", "/config")
DB_PATH = os.path.join(CONFIG_DIR, "shelflife.db")
IMAGE_CACHE_DIR = os.path.join(CONFIG_DIR, "image-cache")

VERSION = _env("SHELFLIFE_VERSION", "dev")
