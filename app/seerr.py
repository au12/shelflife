"""Optional Overseerr / Jellyseerr integration: who requested what."""
import json
from datetime import datetime

import httpx

from . import config, db, stats
from .clients import ApiError, _int


def enabled() -> bool:
    return bool(config.OVERSEERR_URL and config.OVERSEERR_API_KEY)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=f"{config.OVERSEERR_URL}/api/v1", timeout=httpx.Timeout(60, connect=10),
                             verify=config.VERIFY_SSL,
                             headers={"X-Api-Key": config.OVERSEERR_API_KEY, "Accept": "application/json"})


async def _get(c: httpx.AsyncClient, path: str, **params):
    try:
        r = await c.get(path, params=params)
    except httpx.HTTPError as e:
        raise ApiError(f"Couldn't reach Overseerr at {config.OVERSEERR_URL} ({e.__class__.__name__})") from e
    if r.status_code in (401, 403):
        raise ApiError("Overseerr rejected the API key")
    if r.status_code >= 400:
        raise ApiError(f"Overseerr returned HTTP {r.status_code} for {path}")
    try:
        return r.json()
    except ValueError as e:
        raise ApiError(f"Unexpected response from Overseerr for {path}") from e


def _epoch(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


async def fetch_all() -> list[dict]:
    out, skip = [], 0
    async with _client() as c:
        while True:
            body = await _get(c, "/request", take=100, skip=skip, filter="all", sort="added")
            results = body.get("results") or []
            for q in results:
                media = q.get("media") or {}
                user = q.get("requestedBy") or {}
                out.append({
                    "request_id": _int(q.get("id")),
                    "media_type": media.get("mediaType") or q.get("type"),
                    "tmdb_id": _int(media.get("tmdbId"), None),
                    "tvdb_id": _int(media.get("tvdbId"), None),
                    "rating_key": _int(media.get("ratingKey"), None),
                    "rating_key_4k": _int(media.get("ratingKey4k"), None),
                    "requested_by": user.get("displayName") or user.get("plexUsername") or user.get("username")
                    or user.get("jellyfinUsername") or user.get("email"),
                    "requested_by_plex": user.get("plexUsername") or user.get("jellyfinUsername") or user.get("username"),
                    "requested_at": _epoch(q.get("createdAt")),
                    "seasons": json.dumps([s.get("seasonNumber") for s in q.get("seasons") or []
                                           if s.get("seasonNumber") is not None]),
                })
            skip += len(results)
            total = _int((body.get("pageInfo") or {}).get("results"), 0)
            if not results or skip >= total:
                return out


async def check() -> dict:
    async with _client() as c:
        about = await _get(c, "/status")
        await _get(c, "/request", take=1, skip=0, filter="all")
    return {"version": about.get("version")}


# ---------------------------------------------------------------- matching requests to library rows

class _Index:
    def __init__(self, settings: dict):
        self.settings = settings
        self.by_rk, self.by_tmdb, self.by_tvdb = {}, {}, {}
        for q in db.query("SELECT * FROM requests"):
            for k in (q["rating_key"], q["rating_key_4k"]):
                if k:
                    self.by_rk.setdefault(k, []).append(q)
            if q["media_type"] == "movie" and q["tmdb_id"]:
                self.by_tmdb.setdefault(q["tmdb_id"], []).append(q)
            if q["media_type"] == "tv" and q["tvdb_id"]:
                self.by_tvdb.setdefault(q["tvdb_id"], []).append(q)
        self.empty = not (self.by_rk or self.by_tmdb or self.by_tvdb)
        self.guids = {} if self.empty else {
            r["rating_key"]: stats.load_json(r["guids"])
            for r in db.query("SELECT rating_key, guids FROM items WHERE guids IS NOT NULL")}
        self._viewers = {}

    def viewers(self, level: str) -> dict[int, set]:
        """Lower-cased usernames and friendly names that played each item."""
        if level not in self._viewers:
            col, media_type = stats.LEVELS[level]
            seen: dict[int, set] = {}
            for v in db.query(
                    f"SELECT {col} AS k, LOWER(COALESCE(user,'')) AS u, LOWER(COALESCE(friendly_name,'')) AS f "
                    f"FROM history WHERE media_type=? AND COALESCE(duration,0) >= ? GROUP BY k, u, f",
                    (media_type, self.settings["min_play_minutes"] * 60)):
                seen.setdefault(v["k"], set()).update({v["u"], v["f"]} - {""})
            self._viewers[level] = seen
        return self._viewers[level]

    def find(self, row: dict, level: str) -> list[dict]:
        key = row["show_key"] if level == "season" else row["rating_key"]
        found = list(self.by_rk.get(key, []))
        if not found:
            ids = self.guids.get(key) or {}
            if level == "movie":
                found = list(self.by_tmdb.get(_int(ids.get("tmdb"), -1), []))
            else:
                found = list(self.by_tvdb.get(_int(ids.get("tvdb"), -1), []))
        if level == "season":
            found = [q for q in found if not stats.load_json(q["seasons"], [])
                     or row.get("season_index") in stats.load_json(q["seasons"], [])]
        return sorted(found, key=lambda q: q["requested_at"] or 0)

    def describe(self, row: dict, level: str) -> list[dict]:
        seen = self.viewers(level).get(row["rating_key"], set())
        out, names = [], set()
        for q in self.find(row, level):
            who = q["requested_by"] or q["requested_by_plex"] or "Someone"
            if who in names:
                continue
            names.add(who)
            handles = {h.lower() for h in (q["requested_by_plex"], q["requested_by"]) if h}
            out.append({"requested_by": who, "requested_at": q["requested_at"],
                        "watched": bool(handles & seen) if handles else None})
        return out


def annotate(rows: list[dict], level: str | None, settings: dict) -> None:
    """Adds requested_by / requested_at / requester_watched to library rows.
    level is "season" for season rows; None means each row's own kind (movie or show)."""
    idx = _Index(settings)
    for r in rows:
        r.update(requested_by=None, requested_at=None, requester_watched=None)
        if idx.empty:
            continue
        reqs = idx.describe(r, level or r["kind"])
        if not reqs:
            continue
        names = [q["requested_by"] for q in reqs]
        r["requested_by"] = ", ".join(names[:2]) + (f" +{len(names) - 2}" if len(names) > 2 else "")
        r["requested_at"] = reqs[0]["requested_at"]
        known = [q["watched"] for q in reqs if q["watched"] is not None]
        r["requester_watched"] = any(known) if known else None


def requests_for(row: dict, level: str, settings: dict) -> list[dict]:
    idx = _Index(settings)
    return [] if idx.empty else idx.describe(row, level)
