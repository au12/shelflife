"""Optional Sonarr / Radarr integration: find the matching series or movie, delete it, change monitoring."""
import re
import time
from datetime import datetime, timezone

import httpx

from . import config, db
from .clients import ApiError, Tautulli


class NotInLibrary(LookupError):
    pass


class Arr:
    def __init__(self, name: str, url: str, key: str, public_url: str):
        self.name, self.url, self.public_url = name, url, public_url
        self.client = httpx.AsyncClient(
            base_url=f"{url}/api/v3", timeout=httpx.Timeout(120, connect=10), verify=config.VERIFY_SSL,
            headers={"X-Api-Key": key, "Accept": "application/json"})

    async def close(self):
        await self.client.aclose()

    async def req(self, method: str, path: str, *, ok404=False, **kw):
        try:
            r = await self.client.request(method, path, **kw)
        except httpx.HTTPError as e:
            raise ApiError(f"Couldn't reach {self.name} at {self.url} ({e.__class__.__name__})") from e
        if r.status_code == 401:
            raise ApiError(f"{self.name} rejected the API key")
        if r.status_code == 404 and ok404:
            return None
        if r.status_code >= 400:
            detail = ""
            try:
                body = r.json()
                detail = body.get("message") if isinstance(body, dict) else (body[0].get("errorMessage") if body else "")
            except (ValueError, AttributeError, IndexError):
                pass
            raise ApiError(f"{self.name} returned HTTP {r.status_code} for {method} {path}{': ' + detail if detail else ''}")
        if not r.content:
            return None
        try:
            return r.json()
        except ValueError:
            return None


def sonarr() -> Arr | None:
    if config.SONARR_URL and config.SONARR_API_KEY:
        return Arr("Sonarr", config.SONARR_URL, config.SONARR_API_KEY, config.SONARR_PUBLIC_URL)
    return None


def radarr() -> Arr | None:
    if config.RADARR_URL and config.RADARR_API_KEY:
        return Arr("Radarr", config.RADARR_URL, config.RADARR_API_KEY, config.RADARR_PUBLIC_URL)
    return None


def enabled() -> dict:
    return {"sonarr": bool(config.SONARR_URL and config.SONARR_API_KEY),
            "radarr": bool(config.RADARR_URL and config.RADARR_API_KEY)}


# ---------------------------------------------------------------- matching

GUID_RE = re.compile(r"^(?:com\.plexapp\.agents\.)?(?P<src>imdb|tmdb|tvdb|themoviedb|thetvdb)://(?P<id>[^?/]+)")


def parse_guids(guids: list[str]) -> dict:
    out = {}
    for g in guids:
        m = GUID_RE.match(g or "")
        if m:
            src = {"themoviedb": "tmdb", "thetvdb": "tvdb"}.get(m["src"], m["src"])
            out.setdefault(src, m["id"])
    return out


async def plex_guids(rating_key: int) -> dict:
    """External ids (tvdb/tmdb/imdb) for a Plex item, via Plex if we have a token, else via Tautulli."""
    guids: list[str] = []
    server = db.kv_get("server") or {}
    plex_url = config.PLEX_URL or server.get("url")
    if config.PLEX_TOKEN and plex_url:
        try:
            async with httpx.AsyncClient(timeout=30, verify=config.VERIFY_SSL) as c:
                r = await c.get(f"{plex_url}/library/metadata/{rating_key}",
                                headers={"X-Plex-Token": config.PLEX_TOKEN, "Accept": "application/json"})
            meta = (r.json().get("MediaContainer", {}).get("Metadata") or [{}])[0]
            guids = [g.get("id") for g in meta.get("Guid") or []] + [meta.get("guid") or ""]
        except (httpx.HTTPError, ValueError):
            guids = []
    if not guids:
        t = Tautulli()
        try:
            data = await t.call("get_metadata", rating_key=rating_key) or {}
            guids = list(data.get("guids") or []) + [data.get("guid") or ""]
        except ApiError:
            guids = []
        finally:
            await t.close()
    return parse_guids(guids)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _pick(candidates: list[dict], ids: dict, title: str, year) -> dict | None:
    for key, field in (("tvdb", "tvdbId"), ("tmdb", "tmdbId"), ("imdb", "imdbId")):
        if ids.get(key):
            for c in candidates:
                if str(c.get(field) or "") == str(ids[key]):
                    return c
    t = _norm(title)
    by_title = [c for c in candidates if _norm(c.get("title")) == t or _norm(c.get("sortTitle")) == t]
    if year:
        exact = [c for c in by_title if int(c.get("year") or 0) == int(year)]
        if len(exact) == 1:
            return exact[0]
    return by_title[0] if len(by_title) == 1 else None


def _plex_item(rating_key: int):
    """(target dict, show_key, season_number) for a movie, show or season in our DB."""
    it = db.query("SELECT rating_key, kind, title, year FROM items WHERE rating_key=?", (rating_key,))
    if it:
        return it[0], it[0]["rating_key"], None
    s = db.query("SELECT s.show_key, s.season_index, i.title, i.year FROM seasons s JOIN items i "
                 "ON i.rating_key=s.show_key WHERE s.rating_key=?", (rating_key,))
    if not s:
        return None, None, None
    return {"rating_key": s[0]["show_key"], "kind": "show", "title": s[0]["title"], "year": s[0]["year"]}, \
        s[0]["show_key"], s[0]["season_index"]


async def resolve(rating_key: int):
    """Returns (client, kind, arr_object, season_number). client is None when the integration is off."""
    target, key, season = _plex_item(rating_key)
    if not target:
        raise NotInLibrary("This title isn't in the library any more. Try a sync.")
    kind = "movie" if target["kind"] == "movie" else "show"
    client = radarr() if kind == "movie" else sonarr()
    if not client:
        return None, kind, None, season
    path = "/movie" if kind == "movie" else "/series"

    link = db.query("SELECT arr_id FROM arr_links WHERE rating_key=? AND service=?", (key, client.name))
    if link and link[0]["arr_id"]:
        obj = await client.req("GET", f"{path}/{link[0]['arr_id']}", ok404=True)
        if obj:
            return client, kind, obj, season

    ids = await plex_guids(key)
    candidates = []
    if kind == "movie" and ids.get("tmdb"):
        candidates = await client.req("GET", path, params={"tmdbId": ids["tmdb"]}) or []
    elif kind == "show" and ids.get("tvdb"):
        candidates = await client.req("GET", path, params={"tvdbId": ids["tvdb"]}) or []
    obj = _pick(candidates if isinstance(candidates, list) else [candidates], ids, target["title"], target["year"])
    if not obj:
        obj = _pick(await client.req("GET", path) or [], ids, target["title"], target["year"])

    with db.tx() as c:
        c.execute("INSERT OR REPLACE INTO arr_links(rating_key, service, arr_id, checked_at) VALUES(?,?,?,?)",
                  (key, client.name, obj["id"] if obj else None, int(time.time())))
    return client, kind, obj, season


def describe(client: Arr, kind: str, obj: dict, season) -> dict:
    url = f"{client.public_url}/{'movie' if kind == 'movie' else 'series'}/{obj.get('titleSlug') or obj.get('id')}"
    if kind == "movie":
        return {"service": "radarr", "id": obj["id"], "title": obj.get("title"), "year": obj.get("year"),
                "monitored": obj.get("monitored"), "has_file": obj.get("hasFile"),
                "size": obj.get("sizeOnDisk") or (obj.get("movieFile") or {}).get("size") or 0, "url": url}
    seasons = []
    for s in sorted(obj.get("seasons") or [], key=lambda s: s.get("seasonNumber", 0)):
        st = s.get("statistics") or {}
        seasons.append({"number": s.get("seasonNumber"), "monitored": s.get("monitored"),
                        "files": st.get("episodeFileCount", 0), "episodes": st.get("totalEpisodeCount", 0),
                        "size": st.get("sizeOnDisk", 0)})
    return {"service": "sonarr", "id": obj["id"], "title": obj.get("title"), "year": obj.get("year"),
            "monitored": obj.get("monitored"), "status": obj.get("status"), "seasons": seasons,
            "season": season, "url": url}


# ---------------------------------------------------------------- actions

def _aired(ep: dict, now: datetime) -> bool:
    when = ep.get("airDateUtc")
    if not when:
        return bool(ep.get("hasFile"))
    try:
        return datetime.fromisoformat(when.replace("Z", "+00:00")) <= now
    except ValueError:
        return True


async def delete_episode_files(client: Arr, series: dict, season=None, unmonitor=True) -> dict:
    sid = series["id"]
    files = await client.req("GET", "/episodefile", params={"seriesId": sid}) or []
    if season is not None:
        files = [f for f in files if f.get("seasonNumber") == season]
    ids = [f["id"] for f in files]
    freed = sum(int(f.get("size") or 0) for f in files)
    if ids:
        try:
            await client.req("DELETE", "/episodefile/bulk", json={"episodeFileIds": ids})
        except ApiError as e:
            if "HTTP 404" not in str(e) and "HTTP 405" not in str(e):
                raise
            for fid in ids:  # older Sonarr without the bulk endpoint
                await client.req("DELETE", f"/episodefile/{fid}", ok404=True)

    unmonitored = 0
    if unmonitor:
        now = datetime.now(timezone.utc)
        eps = await client.req("GET", "/episode", params={"seriesId": sid}) or []
        seasons = {season} if season is not None else {f.get("seasonNumber") for f in files}
        fresh = await client.req("GET", f"/series/{sid}")
        changed = False
        aired_ids = []
        for sn in seasons:
            s_eps = [e for e in eps if e.get("seasonNumber") == sn]
            aired = [e for e in s_eps if _aired(e, now)]
            aired_ids += [e["id"] for e in aired if e.get("monitored")]
            if s_eps and len(aired) == len(s_eps):
                # Whole season has aired: unmonitor the season itself so nothing comes back.
                for s in fresh.get("seasons") or []:
                    if s.get("seasonNumber") == sn and s.get("monitored"):
                        s["monitored"] = False
                        changed = True
        if changed:
            await client.req("PUT", f"/series/{sid}", json=fresh)
        if aired_ids:
            await client.req("PUT", "/episode/monitor", json={"episodeIds": aired_ids, "monitored": False})
        unmonitored = len(aired_ids)
    return {"files": len(ids), "freed": freed, "unmonitored_episodes": unmonitored}


async def set_monitor(client: Arr, series_id: int, monitored: bool, season=None) -> dict:
    series = await client.req("GET", f"/series/{series_id}")
    if season is None:
        series["monitored"] = monitored
    else:
        hit = [s for s in series.get("seasons") or [] if s.get("seasonNumber") == season]
        if not hit:
            raise ApiError(f"Sonarr has no season {season} for this show")
        hit[0]["monitored"] = monitored
    return await client.req("PUT", f"/series/{series_id}", json=series)


# ---------------------------------------------------------------- local bookkeeping

def forget_locally(rating_keys: list[int], kind: str, title: str, size: int):
    """Hide deleted titles right away; the next sync rebuilds sizes once Plex catches up."""
    now = int(time.time())
    with db.tx() as c:
        for rk in rating_keys:
            c.execute("INSERT OR REPLACE INTO deleted(rating_key, kind, title, size, deleted_at) VALUES(?,?,?,?,?)",
                      (rk, kind, title, size, now))
            c.execute("DELETE FROM items WHERE rating_key=?", (rk,))
            row = c.execute("SELECT show_key, size, episode_count FROM seasons WHERE rating_key=?", (rk,)).fetchone()
            if row:
                c.execute("DELETE FROM seasons WHERE rating_key=?", (rk,))
                c.execute("UPDATE items SET size=MAX(0, size-?), season_count=MAX(0, season_count-1), "
                          "episode_count=MAX(0, episode_count-?) WHERE rating_key=?",
                          (row["size"], row["episode_count"], row["show_key"]))
        c.execute("DELETE FROM arr_links WHERE rating_key IN (%s)" % ",".join("?" * len(rating_keys)), rating_keys)


def hidden_keys() -> set[int]:
    cutoff = int(time.time() - config.DELETED_HIDE_DAYS * 86400)
    with db.tx() as c:
        c.execute("DELETE FROM deleted WHERE deleted_at < ?", (cutoff,))
        return {r["rating_key"] for r in c.execute("SELECT rating_key FROM deleted").fetchall()}
