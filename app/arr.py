"""Optional Sonarr / Radarr integration: find the matching series or movie, delete it, change monitoring."""
import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone
from urllib.parse import quote

import httpx

from . import config, db
from .clients import ApiError, Tautulli, plex_client

log = logging.getLogger("shelflife.arr")


class NotInLibrary(LookupError):
    pass


class DeleteError(RuntimeError):
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


GUID_CACHE_DAYS = 30


def stored_ids(rating_key: int, guids_json: str | None = None) -> dict | None:
    """External ids we already know for a Plex title: from the last sync (Plex token), else from an earlier lookup.
    None means nobody has looked yet; {} means we looked and Plex has none."""
    if guids_json is None:
        rows = db.query("SELECT guids FROM items WHERE rating_key=?", (rating_key,))
        guids_json = rows[0]["guids"] if rows else None
    if guids_json:
        try:
            return json.loads(guids_json)
        except ValueError:
            pass
    cached = db.query("SELECT guids, fetched_at FROM guid_cache WHERE rating_key=?", (rating_key,))
    if cached and cached[0]["fetched_at"] > time.time() - GUID_CACHE_DAYS * 86400:
        try:
            return json.loads(cached[0]["guids"] or "{}")
        except ValueError:
            return None
    return None


async def plex_guids(rating_key: int) -> dict:
    """External ids (tvdb/tmdb/imdb) for a Plex item: from the last sync, else Plex, else Tautulli.
    Lookups are cached, since without a Plex token the sync doesn't store them."""
    ids = stored_ids(rating_key)
    if ids is not None:
        return ids
    guids: list[str] = []
    answered = False
    plex = plex_client()
    if plex:
        try:
            meta = await plex.metadata(rating_key) or {}
            guids = [g.get("id") for g in meta.get("Guid") or []] + [meta.get("guid") or ""]
            answered = bool(meta)
        except ApiError:
            guids = []
        finally:
            await plex.close()
    if not parse_guids(guids):
        t = Tautulli()
        try:
            data = await t.metadata(rating_key)
            guids = list(data.get("guids") or []) + [data.get("guid") or ""]
            answered = answered or bool(data)
        except ApiError:
            pass
        finally:
            await t.close()
    ids = parse_guids(guids)
    if answered:  # a failed lookup isn't cached, so it's tried again next time
        with db.tx() as c:
            c.execute("INSERT OR REPLACE INTO guid_cache(rating_key, guids, fetched_at) VALUES(?,?,?)",
                      (rating_key, json.dumps(ids), int(time.time())))
    return ids


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


class ArrIndex:
    """A Sonarr/Radarr catalog indexed for matching Plex titles: external ids first, then title and year."""
    FIELDS = (("tvdb", "tvdbId"), ("tmdb", "tmdbId"), ("imdb", "imdbId"))

    def __init__(self, catalog: list[dict]):
        self.by: dict[str, dict[str, dict]] = {key: {} for key, _ in self.FIELDS}
        self.titles: dict[str, list[dict]] = {}
        for c in catalog:
            for key, field in self.FIELDS:
                v = str(c.get(field) or "")
                if v and v != "0":
                    self.by[key].setdefault(v, c)
            for t in {_norm(c.get("title")), _norm(c.get("sortTitle"))} - {""}:
                self.titles.setdefault(t, []).append(c)

    def pick(self, ids: dict, title: str, year) -> dict | None:
        for key, _ in self.FIELDS:
            if ids.get(key) and str(ids[key]) in self.by[key]:
                return self.by[key][str(ids[key])]
        by_title = self.titles.get(_norm(title), [])
        if not year:
            return by_title[0] if len(by_title) == 1 else None
        # A title match has to agree on the year (give or take one, since release dates differ): a remake
        # with the same name is a different film, and Shelflife deletes through whatever this returns.
        for spread in (0, 1):
            near = [c for c in by_title if c.get("year") and abs(int(c["year"]) - int(year)) <= spread]
            if len(near) == 1:
                return near[0]
            if near:
                return None
        return by_title[0] if len(by_title) == 1 and not by_title[0].get("year") else None


def _pick(candidates: list[dict], ids: dict, title: str, year) -> dict | None:
    return ArrIndex(candidates).pick(ids, title, year)


def arr_url(public_url: str, kind: str, obj: dict) -> str:
    return f"{public_url}/{'movie' if kind == 'movie' else 'series'}/{obj.get('titleSlug') or obj.get('id')}"


def add_url(kind: str, ids: dict, title: str, year=None) -> str | None:
    """Radarr's / Sonarr's add-new page, already searching for this title."""
    base = config.RADARR_PUBLIC_URL if kind == "movie" else config.SONARR_PUBLIC_URL
    if not base:
        return None
    if kind == "movie":
        term = f"tmdb:{ids['tmdb']}" if ids.get("tmdb") else f"imdb:{ids['imdb']}" if ids.get("imdb") else \
            f"{title} {year or ''}".strip()
    else:
        term = f"tvdb:{ids['tvdb']}" if ids.get("tvdb") else title
    return f"{base}/add/new?term={quote(term)}"


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


def _remember(key: int, service: str, arr_id) -> None:
    remember_many(service, {key: arr_id})


def remember_many(service: str, links: dict[int, int | None]) -> None:
    now = int(time.time())
    with db.tx() as c:
        c.executemany("INSERT OR REPLACE INTO arr_links(rating_key, service, arr_id, checked_at) VALUES(?,?,?,?)",
                      [(rk, service, arr_id, now) for rk, arr_id in links.items()])


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
    try:
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
    except Exception:
        await client.close()
        raise
    _remember(key, client.name, obj["id"] if obj else None)
    return client, kind, obj, season


async def resolve_many(rating_keys: list[int]) -> dict[int, dict]:
    """Match many Plex items at once, reading each Sonarr/Radarr catalog a single time."""
    out: dict[int, dict] = {}
    meta, groups = {}, {"movie": [], "show": []}
    for rk in rating_keys:
        target, key, season = _plex_item(rk)
        if not target:
            out[rk] = {"error": "No longer in the library"}
            continue
        kind = "movie" if target["kind"] == "movie" else "show"
        meta[rk] = (target, key, season)
        groups[kind].append(rk)

    for kind, rks in groups.items():
        if not rks:
            continue
        name = "Radarr" if kind == "movie" else "Sonarr"
        client = radarr() if kind == "movie" else sonarr()
        if not client:
            for rk in rks:
                out[rk] = {"kind": kind, "service": name, "error": f"{name} isn't set up"}
            continue
        try:
            catalog = await client.req("GET", "/movie" if kind == "movie" else "/series") or []
            index = ArrIndex(catalog)
            by_id = {c.get("id"): c for c in catalog}
            links = {r["rating_key"]: r["arr_id"] for r in db.query(
                "SELECT rating_key, arr_id FROM arr_links WHERE service=?", (name,))}
            sem = asyncio.Semaphore(4)

            async def one(rk):
                target, key, season = meta[rk]
                obj = by_id.get(links.get(key))
                if not obj:
                    async with sem:
                        ids = await plex_guids(key)
                    obj = index.pick(ids, target["title"], target["year"])
                    _remember(key, name, obj["id"] if obj else None)
                out[rk] = {"kind": kind, "service": name, "obj": obj, "season": season, "key": key,
                           "error": None if obj else f"Not found in {name}"}

            await asyncio.gather(*(one(rk) for rk in rks))
        except ApiError as e:
            for rk in rks:
                out.setdefault(rk, {"kind": kind, "service": name, "error": str(e)})
        finally:
            await client.close()
    return out


def describe(client: Arr, kind: str, obj: dict, season) -> dict:
    url = arr_url(client.public_url, kind, obj)
    if kind == "movie":
        return {"service": "radarr", "id": obj["id"], "title": obj.get("title"), "year": obj.get("year"),
                "monitored": obj.get("monitored"), "has_file": obj.get("hasFile"), "path": obj.get("path"),
                "file": (obj.get("movieFile") or {}).get("path"),
                "size": obj.get("sizeOnDisk") or (obj.get("movieFile") or {}).get("size") or 0, "url": url}
    seasons = []
    for s in sorted(obj.get("seasons") or [], key=lambda s: s.get("seasonNumber", 0)):
        st = s.get("statistics") or {}
        seasons.append({"number": s.get("seasonNumber"), "monitored": s.get("monitored"),
                        "files": st.get("episodeFileCount", 0), "episodes": st.get("totalEpisodeCount", 0),
                        "size": st.get("sizeOnDisk", 0)})
    return {"service": "sonarr", "id": obj["id"], "title": obj.get("title"), "year": obj.get("year"),
            "monitored": obj.get("monitored"), "status": obj.get("status"), "seasons": seasons,
            "season": season, "path": obj.get("path"), "url": url}


# ---------------------------------------------------------------- keep list

def kept_title(rating_key: int) -> str | None:
    """Title of the keep-list entry protecting this item (itself, or its show for a season)."""
    rows = db.query(
        "SELECT e.title FROM exclusions e WHERE e.rating_key=? OR e.rating_key=(SELECT show_key FROM seasons "
        "WHERE rating_key=?)", (rating_key, rating_key))
    return rows[0]["title"] if rows else None


# ---------------------------------------------------------------- planning a delete

def _row(rating_key: int) -> dict | None:
    """Library row for a movie, show or season, with what the UI and history log need."""
    it = db.query("SELECT rating_key, kind, title, year, thumb, size, section_id FROM items WHERE rating_key=?",
                  (rating_key,))
    if it:
        return it[0]
    s = db.query("SELECT s.rating_key, 'season' AS kind, s.title AS season_title, s.season_index, s.size, "
                 "s.section_id, s.show_key, COALESCE(s.thumb, i.thumb) AS thumb, i.title AS show_title, i.year "
                 "FROM seasons s LEFT JOIN items i ON i.rating_key=s.show_key WHERE s.rating_key=?", (rating_key,))
    if not s:
        return None
    r = s[0]
    r["title"] = f"{r['show_title']} – {r['season_title']}"
    return r


async def plan(rating_keys: list[int]) -> list[dict]:
    """What a bulk delete would do for each item, and which ones can't be deleted (and why)."""
    resolved = await resolve_many([rk for rk in rating_keys if not kept_title(rk) and _row(rk)])
    out = []
    for rk in rating_keys:
        row = _row(rk)
        item = {"rating_key": rk, "ok": False, "reason": None}
        if not row:
            item.update(title="Unknown title", kind=None, reason="No longer in the library")
            out.append(item)
            continue
        item.update(title=row["title"], kind=row["kind"], year=row.get("year"), size=row.get("size") or 0,
                    thumb=row.get("thumb"), season_index=row.get("season_index"), show_title=row.get("show_title"))
        if kept_title(rk):
            item["reason"] = "On your keep list"
        else:
            r = resolved.get(rk) or {}
            item["service"] = r.get("service")
            if r.get("error"):
                item["reason"] = r["error"]
            else:
                obj = r["obj"]
                item.update(ok=True, arr_title=obj.get("title"), arr_year=obj.get("year"), status=obj.get("status"))
                blocked, warning = shared_check(r["service"], r["kind"], obj, r["key"])
                if blocked:
                    item.update(ok=False, reason=blocked)
                elif warning:
                    item["warning"] = warning
        out.append(item)
    return out


def arr_size(kind: str, obj: dict) -> int:
    if kind == "movie":
        return obj.get("sizeOnDisk") or (obj.get("movieFile") or {}).get("size") or 0
    st = obj.get("statistics") or {}
    if "sizeOnDisk" in st:
        return st["sizeOnDisk"] or 0
    return sum((x.get("statistics") or {}).get("sizeOnDisk", 0) for x in obj.get("seasons") or [])


def close_in_size(a: int, b: int) -> bool:
    """Loose comparison for telling which of two Plex copies Sonarr/Radarr's files belong to."""
    return bool(a and b) and abs(a - b) <= max(1024 ** 3, 0.05 * max(a, b))


def sharing(service: str, arr_id, key: int) -> list[dict]:
    """Other Plex titles matched to the same Sonarr series / Radarr movie, as of their last match."""
    if not arr_id:
        return []
    rows = db.query(
        "SELECT i.rating_key, i.title, i.year, i.size, b.name AS library FROM arr_links l "
        "JOIN items i ON i.rating_key=l.rating_key LEFT JOIN libraries b ON b.section_id=i.section_id "
        "WHERE l.service=? AND l.arr_id=? AND l.rating_key<>?", (service, arr_id, key))
    hidden = hidden_keys()
    return [r for r in rows if r["rating_key"] not in hidden]


def shared_check(service: str, kind: str, obj: dict, key: int) -> tuple[str | None, str | None]:
    """(reason to refuse, warning) when another Plex title matches the same Sonarr/Radarr entry as this one.
    Deleting goes through that entry, so it removes whichever copy Sonarr/Radarr manages: refuse when its files
    are clearly the other copy, warn when it can't be told."""
    others = sharing(service, obj.get("id"), key)
    if not others:
        return None, None
    o = others[0]
    where = f"“{o['title']}”" + (f" in {o['library']}" if o.get("library") else "")
    size = arr_size(kind, obj)
    mine = (db.query("SELECT size FROM items WHERE rating_key=?", (key,)) or [{"size": 0}])[0]["size"] or 0
    noun = "movie" if kind == "movie" else "show"
    mine_close, theirs_close = close_in_size(size, mine), close_in_size(size, o["size"] or 0)
    if mine_close and not theirs_close:
        return None, None  # the copy Sonarr/Radarr manages is this one
    if theirs_close and not mine_close:
        return (f"{service}'s files for this {noun} are the copy {where}, so deleting through {service} would "
                f"remove that one instead"), None
    return None, (f"{service} has one entry for this {noun} and for {where}. Deleting removes {service}'s copy, "
                  f"which may be that one.")


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


async def perform_delete(rating_key: int, mode: str = "remove", exclusion: bool = True, unmonitor: bool = True,
                         source: str = "manual") -> dict:
    """Delete one movie, show or season through Radarr/Sonarr, then hide it and log it.
    Shows: mode "remove" takes the series out of Sonarr; "files" deletes episode files and keeps the series.
    Seasons always keep the series. Raises DeleteError with a readable reason."""
    if kept_title(rating_key):
        raise DeleteError("On your keep list")
    row = _row(rating_key)
    if not row:
        raise DeleteError("No longer in the library")
    try:
        client, kind, obj, season = await resolve(rating_key)
    except NotInLibrary as e:
        raise DeleteError(str(e)) from e
    except ApiError as e:
        raise DeleteError(str(e)) from e
    if not client:
        raise DeleteError(f"{'Radarr' if kind == 'movie' else 'Sonarr'} isn't set up")
    try:
        if not obj:
            raise DeleteError(f"Not found in {client.name}")
        blocked, _ = shared_check(client.name, kind, obj, _plex_item(rating_key)[1])
        if blocked:
            raise DeleteError(blocked)
        title, size = row["title"], row.get("size") or 0
        if kind == "movie":
            await client.req("DELETE", f"/movie/{obj['id']}",
                             params={"deleteFiles": "true", "addImportExclusion": str(bool(exclusion)).lower()})
            freed = size or obj.get("sizeOnDisk") or 0
            forget_locally([rating_key], "movie", title, freed)
            action = "Removed from Radarr" + (", import exclusion added" if exclusion else "")
            message = f"Removed {title} from Radarr"
        elif season is not None:
            res = await delete_episode_files(client, obj, season=season, unmonitor=unmonitor)
            freed = res["freed"] or size
            forget_locally([rating_key], "season", title, freed)
            action = f"Deleted {res['files']} episode files, kept in Sonarr" + \
                     (", episodes unmonitored" if unmonitor and res["unmonitored_episodes"] else "")
            message = f"Deleted {res['files']} episode files from {title}"
        else:
            show_key = rating_key
            season_keys = [r["rating_key"] for r in db.query("SELECT rating_key FROM seasons WHERE show_key=?",
                                                             (show_key,))]
            if mode == "remove":
                await client.req("DELETE", f"/series/{obj['id']}",
                                 params={"deleteFiles": "true", "addImportListExclusion": str(bool(exclusion)).lower()})
                freed = size or (obj.get("statistics") or {}).get("sizeOnDisk") or 0
                forget_locally([show_key] + season_keys, "show", title, freed)
                action = "Removed from Sonarr" + (", import list exclusion added" if exclusion else "")
                message = f"Removed {title} from Sonarr"
            elif mode == "files":
                res = await delete_episode_files(client, obj, season=None, unmonitor=unmonitor)
                freed = res["freed"] or size
                # Only the seasons are hidden, so the show comes back as soon as Plex has new episodes.
                forget_locally(season_keys, "season", title, freed)
                with db.tx() as c:
                    c.execute("DELETE FROM items WHERE rating_key=?", (show_key,))
                action = f"Deleted {res['files']} episode files, kept in Sonarr" + \
                         (", aired episodes unmonitored" if unmonitor and res["unmonitored_episodes"] else "")
                message = f"Deleted {res['files']} episode files from {title}; it stays in Sonarr for new episodes"
            else:
                raise DeleteError("Unknown delete mode")
    except ApiError as e:
        raise DeleteError(str(e)) from e
    finally:
        await client.close()

    now = int(time.time())
    with db.tx() as c:
        c.execute("INSERT INTO deletions(at, rating_key, kind, title, year, thumb, size, service, action, source) "
                  "VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (now, rating_key, row["kind"], title, row.get("year"), row.get("thumb"), int(freed or 0),
                   client.name, action, source))
        c.execute("UPDATE leaving SET status='deleted', status_at=?, note=? WHERE rating_key=? AND status='scheduled'",
                  (now, "Deleted" if source == "leaving" else "Deleted by hand", rating_key))
    return {"ok": True, "message": message, "freed": int(freed or 0), "action": action,
            "section_id": row.get("section_id"), "title": title}


async def plex_refresh(section_ids) -> None:
    """Ask Plex to rescan the libraries we deleted from, so the titles disappear there quickly."""
    ids = sorted({s for s in section_ids if s})
    if not ids:
        return
    plex = plex_client()
    if not plex:
        return
    try:
        for sid in ids:
            try:
                await plex.refresh_section(sid)
            except ApiError as e:
                log.warning("Couldn't ask Plex to rescan library %s: %s", sid, e)
    finally:
        await plex.close()


# ---------------------------------------------------------------- local bookkeeping

def forget_locally(rating_keys: list[int], kind: str, title: str, size: int):
    """Hide deleted titles right away; the next sync rebuilds sizes once Plex catches up."""
    if not rating_keys:
        return
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
