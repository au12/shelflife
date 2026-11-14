import os


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _num(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)) or default)
    except ValueError:
        return default


TAUTULLI_URL = _env("TAUTULLI_URL").rstrip("/")
TAUTULLI_API_KEY = _env("TAUTULLI_API_KEY")

# Optional: talk to Plex directly. Much faster for big TV libraries, counts every version of a movie,
# adds Open in Plex links and the Plex "Leaving Soon" collection.
PLEX_URL = _env("PLEX_URL").rstrip("/")
PLEX_TOKEN = _env("PLEX_TOKEN")

# Optional: Sonarr / Radarr for deleting and (un)monitoring. *_PUBLIC_URL is the address your
# browser uses for "Show in Sonarr/Radarr" links, if it differs from what the container uses.
SONARR_URL = _env("SONARR_URL").rstrip("/")
SONARR_API_KEY = _env("SONARR_API_KEY")
SONARR_PUBLIC_URL = (_env("SONARR_PUBLIC_URL") or SONARR_URL).rstrip("/")
RADARR_URL = _env("RADARR_URL").rstrip("/")
RADARR_API_KEY = _env("RADARR_API_KEY")
RADARR_PUBLIC_URL = (_env("RADARR_PUBLIC_URL") or RADARR_URL).rstrip("/")

# Optional: Overseerr or Jellyseerr (same API), for "requested by".
OVERSEERR_URL = (_env("OVERSEERR_URL") or _env("JELLYSEERR_URL")).rstrip("/")
OVERSEERR_API_KEY = _env("OVERSEERR_API_KEY") or _env("JELLYSEERR_API_KEY")

# Optional: notifications. A Discord or Slack webhook, an ntfy topic URL, or any URL that accepts JSON.
NOTIFY_URL = _env("NOTIFY_URL")
DIGEST_DAY = (_env("DIGEST_DAY", "sun") or "sun").lower()[:3]   # mon..sun, or "off"
DIGEST_HOUR = int(_num("DIGEST_HOUR", 9)) % 24
PUBLIC_URL = _env("PUBLIC_URL").rstrip("/")                     # link back to Shelflife in notifications
TZ = _env("TZ", "UTC") or "UTC"

# Optional: every folder mounted under here shows its free space, e.g. /mnt/user -> /storage/Array (read-only).
STORAGE_DIR = _env("STORAGE_DIR", "/storage")

# Deleted titles stay hidden this long while Plex catches up with the removed files.
DELETED_HIDE_DAYS = _num("DELETED_HIDE_DAYS", 7)

# Quick syncs read only new watch history; a full sync re-reads everything.
SYNC_INTERVAL_HOURS = _num("SYNC_INTERVAL_HOURS", 6)
FULL_SYNC_HOURS = _num("FULL_SYNC_HOURS", 24)
SYNC_CONCURRENCY = max(1, int(_num("SYNC_CONCURRENCY", 4)))
TICK_SECONDS = max(1, _num("TICK_SECONDS", 60))      # how often background work (Leaving soon, digest) runs
VERIFY_SSL = _env("VERIFY_SSL", "true").lower() not in ("0", "false", "no")

# Comma-separated library names or section ids to skip (e.g. "Home Videos,Kids Movies")
IGNORE_LIBRARIES = [s.strip().lower() for s in _env("IGNORE_LIBRARIES").split(",") if s.strip()]

AUTH_USER = _env("AUTH_USER")
AUTH_PASS = _env("AUTH_PASS")

CONFIG_DIR = _env("CONFIG_DIR", "/config")
DB_PATH = os.path.join(CONFIG_DIR, "shelflife.db")
IMAGE_CACHE_DIR = os.path.join(CONFIG_DIR, "image-cache")

VERSION = _env("SHELFLIFE_VERSION", "dev")
