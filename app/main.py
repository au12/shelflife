import asyncio
import hashlib
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from . import arr, auth, config, db, sync
from .clients import ApiError, Tautulli

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
STATIC = os.path.join(os.path.dirname(__file__), "static")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    os.makedirs(config.IMAGE_CACHE_DIR, exist_ok=True)
    task = asyncio.create_task(sync.scheduler())
    yield
    task.cancel()


app = FastAPI(title="Shelflife", lifespan=lifespan, docs_url=None, redoc_url=None)


PUBLIC_PATHS = {"/healthz", "/login", "/api/login", "/manifest.webmanifest"}


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if not auth.enabled() or path in PUBLIC_PATHS or path.startswith("/static/"):
        return await call_next(request)
    if auth.valid(request.cookies.get(auth.COOKIE)) or auth.check_basic(request.headers.get("authorization", "")):
        return await call_next(request)
    if path.startswith("/api/"):
        return JSONResponse({"detail": "Sign in required"}, status_code=401)
    target = path + (f"?{request.url.query}" if request.url.query else "")
    return RedirectResponse(f"/login?next={quote(target)}", status_code=303)


@app.get("/login")
def login_page(request: Request):
    if not auth.enabled() or auth.valid(request.cookies.get(auth.COOKIE)):
        return RedirectResponse("/", status_code=303)
    with open(os.path.join(STATIC, "login.html"), encoding="utf-8") as f:
        html = f.read().replace("{{v}}", config.VERSION)
    return Response(html, media_type="text/html", headers={"Cache-Control": "no-store"})


@app.post("/api/login")
async def login(request: Request, body: dict = Body(default={})):
    if not auth.enabled():
        return {"ok": True}
    ip = request.client.host if request.client else "?"
    wait = auth.locked_for(ip)
    if wait:
        raise HTTPException(429, f"Too many attempts. Try again in {max(1, round(wait / 60))} min.")
    if not auth.check_password(str(body.get("username", "")), str(body.get("password", ""))):
        auth.record_failure(ip)
        await asyncio.sleep(0.6)
        left = auth.MAX_FAILURES - len(auth._failures.get(ip, []))
        msg = "That username and password don't match."
        if 0 < left <= 2:
            msg += f" {left} {'try' if left == 1 else 'tries'} left before a 5 minute lockout."
        raise HTTPException(401, msg)
    auth.clear_failures(ip)
    remember = bool(body.get("remember"))
    token, ttl = auth.issue(remember)
    resp = JSONResponse({"ok": True})
    resp.set_cookie(auth.COOKIE, token, max_age=ttl if remember else None, httponly=True, samesite="lax",
                    secure=request.url.scheme == "https", path="/")
    return resp


@app.post("/api/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE, path="/")
    return resp


# ---------------------------------------------------------------- stats helpers

LEVELS = {
    "movie": ("rating_key", "movie"),
    "show": ("grandparent_rating_key", "episode"),
    "season": ("parent_rating_key", "episode"),
}


def _filters(settings):
    ignored = [int(u) for u in settings["ignored_users"]]
    where = ["media_type = ?", "COALESCE(duration,0) >= ?"]
    args = [settings["min_play_minutes"] * 60]
    if ignored:
        where.append(f"COALESCE(user_id,-1) NOT IN ({','.join('?' * len(ignored))})")
        args += ignored
    return where, args


def stats(level: str, settings: dict) -> dict[int, dict]:
    col, media_type = LEVELS[level]
    where, args = _filters(settings)
    # SQLite returns the bare friendly_name from the row holding MAX(started).
    rows = db.query(
        f"SELECT {col} AS k, MAX(started) AS last_played, friendly_name AS last_user, COUNT(*) AS plays, "
        f"COUNT(DISTINCT user_id) AS users FROM history WHERE {' AND '.join(where)} GROUP BY {col}",
        [media_type] + args,
    )
    return {r["k"]: r for r in rows if r["k"] is not None}


EMPTY = {"last_played": None, "last_user": None, "plays": 0, "users": 0}


def _with_stats(row: dict, st: dict[int, dict]) -> dict:
    s = st.get(row["rating_key"], EMPTY)
    row.update(last_played=s["last_played"], last_user=s["last_user"], plays=s["plays"], users=s["users"])
    return row


# ---------------------------------------------------------------- API

@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/api/status")
def status():
    last = db.kv_get("last_sync") or {}
    server = db.kv_get("server") or {}
    return {
        "configured": sync.configured(),
        "version": config.VERSION,
        "sync": {**sync.state, "last_success": sync.state["last_success"] or last.get("at")},
        "size_source": sync.state["size_source"] or last.get("size_source"),
        "plex_direct": bool(config.PLEX_TOKEN),
        "auth": auth.enabled(),
        "integrations": {"plex": bool(config.PLEX_TOKEN), **arr.enabled()},
        "interval_hours": config.SYNC_INTERVAL_HOURS,
        "server": {"identifier": server.get("identifier"), "name": server.get("name")},
        "counts": last,
    }


@app.post("/api/sync")
async def trigger_sync():
    if not sync.configured():
        raise HTTPException(400, "Set TAUTULLI_URL and TAUTULLI_API_KEY first")
    if not sync.state["running"]:
        asyncio.create_task(sync.run_sync())
        await asyncio.sleep(0.05)
    return sync.state


@app.get("/api/libraries")
def libraries():
    return db.query("SELECT section_id, name, type, item_count FROM libraries ORDER BY type, name")


@app.get("/api/items")
def items(view: str = "titles"):
    settings = db.get_settings()
    excl = {r["rating_key"]: r for r in db.query("SELECT rating_key, note FROM exclusions")}

    if view == "seasons":
        st = stats("season", settings)
        rows = db.query(
            "SELECT s.rating_key, s.show_key, s.section_id, s.season_index, s.title, s.size, s.episode_count, "
            "s.added_at, COALESCE(s.thumb, i.thumb) AS thumb, i.title AS show_title, i.year "
            "FROM seasons s LEFT JOIN items i ON i.rating_key = s.show_key")
        for r in rows:
            r["kind"] = "season"
            _with_stats(r, st)
            hit = excl.get(r["rating_key"]) or excl.get(r["show_key"])
            r["excluded"] = bool(hit)
            r["excluded_via_show"] = bool(hit) and r["rating_key"] not in excl
        return rows

    st_movie, st_show = stats("movie", settings), stats("show", settings)
    rows = db.query(
        "SELECT rating_key, section_id, kind, title, sort_title, year, thumb, added_at, size, season_count, "
        "episode_count, resolution FROM items")
    for r in rows:
        _with_stats(r, st_movie if r["kind"] == "movie" else st_show)
        r["excluded"] = r["rating_key"] in excl
    return rows


@app.get("/api/item/{rating_key}")
def item(rating_key: int):
    settings = db.get_settings()
    ignored = set(settings["ignored_users"])
    excl = {r["rating_key"]: r for r in db.query("SELECT * FROM exclusions")}
    server = db.kv_get("server") or {}

    found = db.query("SELECT * FROM items WHERE rating_key=?", (rating_key,))
    seasons = []
    if found:
        it = found[0]
        level = it["kind"]
        hist_col = LEVELS[level][0]
        _with_stats(it, stats(level, settings))
        if level == "show":
            st = stats("season", settings)
            seasons = [_with_stats(s, st) for s in db.query(
                "SELECT rating_key, season_index, title, size, episode_count, added_at FROM seasons "
                "WHERE show_key=? ORDER BY season_index", (rating_key,))]
            for s in seasons:
                s["excluded"] = s["rating_key"] in excl
        it["exclusion"] = excl.get(rating_key)
    else:
        found = db.query(
            "SELECT s.*, i.title AS show_title, i.year, COALESCE(s.thumb, i.thumb) AS thumb FROM seasons s "
            "LEFT JOIN items i ON i.rating_key = s.show_key WHERE s.rating_key=?", (rating_key,))
        if not found:
            raise HTTPException(404, "Not in the library (try a sync)")
        it = found[0]
        it["kind"] = "season"
        hist_col = "parent_rating_key"
        _with_stats(it, stats("season", settings))
        it["exclusion"] = excl.get(rating_key)
        it["show_exclusion"] = excl.get(it["show_key"])

    lib = db.query("SELECT name FROM libraries WHERE section_id=?", (it["section_id"],))
    it["library"] = lib[0]["name"] if lib else None
    history = db.query(
        f"SELECT row_id, date, user_id, friendly_name, full_title, title, season, episode, started, stopped, paused, "
        f"duration, percent_complete, watched_status, group_count FROM history WHERE {hist_col}=? "
        f"ORDER BY started DESC", (rating_key,))
    min_s = settings["min_play_minutes"] * 60
    for h in history:
        h["counted"] = h["user_id"] not in ignored and (h["duration"] or 0) >= min_s
    plex_key = it.get("show_key") if it["kind"] == "season" else rating_key
    it["plex_url"] = (
        f"https://app.plex.tv/desktop/#!/server/{server['identifier']}/details?key=%2Flibrary%2Fmetadata%2F{plex_key}"
        if server.get("identifier") and config.PLEX_TOKEN else None)
    return {"item": it, "seasons": seasons, "history": history}


@app.get("/api/users")
def users():
    return db.query(
        "SELECT user_id, friendly_name, COUNT(*) AS plays, MAX(started) AS last_played FROM history "
        "WHERE user_id IS NOT NULL GROUP BY user_id ORDER BY plays DESC")


@app.get("/api/settings")
def get_settings():
    return db.get_settings()


@app.put("/api/settings")
def put_settings(body: dict = Body(...)):
    return db.save_settings(body)


# ---------------------------------------------------------------- exclusions

@app.get("/api/exclusions")
def exclusions():
    rows = db.query(
        "SELECT e.*, COALESCE(i.size, s.size) AS size, COALESCE(i.thumb, s.thumb, si.thumb) AS thumb, "
        "si.title AS show_title, (i.rating_key IS NOT NULL OR s.rating_key IS NOT NULL) AS in_library "
        "FROM exclusions e LEFT JOIN items i ON i.rating_key=e.rating_key "
        "LEFT JOIN seasons s ON s.rating_key=e.rating_key LEFT JOIN items si ON si.rating_key=s.show_key "
        "ORDER BY e.created_at DESC")
    return rows


def _exclude(c, rk: int, note: str | None):
    it = c.execute("SELECT kind, title, year FROM items WHERE rating_key=?", (rk,)).fetchone()
    if it:
        kind, title, year = it["kind"], it["title"], it["year"]
    else:
        s = c.execute("SELECT s.title, i.title AS show_title, i.year FROM seasons s LEFT JOIN items i "
                      "ON i.rating_key=s.show_key WHERE s.rating_key=?", (rk,)).fetchone()
        if not s:
            raise HTTPException(404, "Not in the library")
        kind, title, year = "season", f"{s['show_title']} – {s['title']}", s["year"]
    c.execute(
        "INSERT INTO exclusions(rating_key,kind,title,year,note,created_at) VALUES(?,?,?,?,?,?) "
        "ON CONFLICT(rating_key) DO UPDATE SET note=COALESCE(excluded.note, exclusions.note)",
        (rk, kind, title, year, note, int(time.time())))


@app.put("/api/exclusions/{rating_key}")
def add_exclusion(rating_key: int, body: dict = Body(default={})):
    note = (body or {}).get("note")
    note = note.strip()[:500] if isinstance(note, str) and note.strip() else None
    with db.tx() as c:
        _exclude(c, rating_key, note)
        if note is None and "note" in (body or {}):
            c.execute("UPDATE exclusions SET note=NULL WHERE rating_key=?", (rating_key,))
    return {"ok": True}


@app.post("/api/exclusions/bulk")
def add_exclusions(body: dict = Body(...)):
    keys = [int(k) for k in body.get("rating_keys", [])][:5000]
    with db.tx() as c:
        for k in keys:
            try:
                _exclude(c, k, None)
            except HTTPException:
                pass
    return {"ok": True, "count": len(keys)}


@app.delete("/api/exclusions/{rating_key}")
def remove_exclusion(rating_key: int):
    with db.tx() as c:
        c.execute("DELETE FROM exclusions WHERE rating_key=?", (rating_key,))
    return {"ok": True}


# ---------------------------------------------------------------- Sonarr / Radarr

def _kept(rating_key: int) -> str | None:
    """Title of the keep-list entry protecting this item (itself, or its show for a season)."""
    rows = db.query(
        "SELECT e.title FROM exclusions e WHERE e.rating_key=? OR e.rating_key=(SELECT show_key FROM seasons "
        "WHERE rating_key=?)", (rating_key, rating_key))
    return rows[0]["title"] if rows else None


async def _resolve(rating_key: int):
    try:
        client, kind, obj, season = await arr.resolve(rating_key)
    except arr.NotInLibrary as e:
        raise HTTPException(404, str(e))
    except ApiError as e:
        raise HTTPException(502, str(e))
    return client, kind, obj, season


@app.get("/api/arr/{rating_key}")
async def arr_info(rating_key: int):
    client, kind, obj, season = await _resolve(rating_key)
    service = "radarr" if kind == "movie" else "sonarr"
    if not client:
        return {"service": service, "enabled": False}
    try:
        if not obj:
            return {"service": service, "enabled": True, "matched": False}
        return {"enabled": True, "matched": True, "kept": bool(_kept(rating_key)),
                **arr.describe(client, kind, obj, season)}
    finally:
        await client.close()


@app.post("/api/arr/{rating_key}/delete")
async def arr_delete(rating_key: int, body: dict = Body(default={})):
    """mode: 'remove' (take the movie/series out of Radarr/Sonarr and delete its files)
    or 'files' (Sonarr only: delete episode files, keep the series for future episodes)."""
    mode = body.get("mode", "remove")
    exclusion = bool(body.get("exclusion", True))
    unmonitor = bool(body.get("unmonitor", True))
    if _kept(rating_key):
        raise HTTPException(409, "This title is on your keep list. Stop keeping it first.")
    client, kind, obj, season = await _resolve(rating_key)
    if not client:
        raise HTTPException(400, f"{'Radarr' if kind == 'movie' else 'Sonarr'} isn't set up")
    try:
        if not obj:
            raise HTTPException(404, f"Couldn't find this title in {client.name}")
        target = db.query("SELECT title, size FROM items WHERE rating_key=? UNION ALL "
                          "SELECT title, size FROM seasons WHERE rating_key=?", (rating_key, rating_key))
        title, size = (target[0]["title"], target[0]["size"]) if target else (obj.get("title"), 0)
        try:
            if kind == "movie":
                await client.req("DELETE", f"/movie/{obj['id']}",
                                 params={"deleteFiles": "true", "addImportExclusion": str(exclusion).lower()})
                arr.forget_locally([rating_key], "movie", title, size)
                return {"ok": True, "message": f"Deleted {obj.get('title')} from Radarr", "freed": size}

            show_key = rating_key if season is None else \
                db.query("SELECT show_key FROM seasons WHERE rating_key=?", (rating_key,))[0]["show_key"]
            season_keys = [r["rating_key"] for r in db.query(
                "SELECT rating_key FROM seasons WHERE show_key=?", (show_key,))]
            show_size = (db.query("SELECT size FROM items WHERE rating_key=?", (show_key,)) or [{"size": size}])[0]["size"]

            if season is not None:  # one season: always "delete files, keep the series"
                res = await arr.delete_episode_files(client, obj, season=season, unmonitor=unmonitor)
                arr.forget_locally([rating_key], "season", title, size)
                return {"ok": True, "message": f"Deleted {res['files']} episode files from {obj.get('title')} "
                                               f"season {season}", **res}
            if mode == "remove":
                await client.req("DELETE", f"/series/{obj['id']}",
                                 params={"deleteFiles": "true", "addImportListExclusion": str(exclusion).lower()})
                arr.forget_locally([show_key] + season_keys, "show", title, show_size)
                return {"ok": True, "message": f"Removed {obj.get('title')} from Sonarr", "freed": show_size}
            if mode == "files":
                res = await arr.delete_episode_files(client, obj, season=None, unmonitor=unmonitor)
                # Only the seasons are hidden, so the show comes back as soon as Plex has new episodes.
                arr.forget_locally(season_keys, "season", title, show_size)
                with db.tx() as c:
                    c.execute("DELETE FROM items WHERE rating_key=?", (show_key,))
                return {"ok": True, "message": f"Deleted {res['files']} episode files from {obj.get('title')}; "
                                               f"it stays in Sonarr for new episodes", **res}
            raise HTTPException(400, "Unknown delete mode")
        except ApiError as e:
            raise HTTPException(502, str(e))
    finally:
        await client.close()


@app.put("/api/arr/{rating_key}/monitor")
async def arr_monitor(rating_key: int, body: dict = Body(...)):
    """Sonarr monitoring. season omitted = the series itself; season=N = that season."""
    client, kind, obj, _ = await _resolve(rating_key)
    if not client or kind != "show":
        raise HTTPException(400, "Monitoring changes need Sonarr and a TV show")
    try:
        if not obj:
            raise HTTPException(404, "Couldn't find this show in Sonarr")
        season = body.get("season")
        try:
            updated = await arr.set_monitor(client, obj["id"], bool(body.get("monitored")),
                                            None if season is None else int(season))
        except ApiError as e:
            raise HTTPException(502, str(e))
        return arr.describe(client, kind, updated or obj, None)
    finally:
        await client.close()


@app.get("/api/integrations")
async def integrations():
    out = {}
    for name, factory in (("sonarr", arr.sonarr), ("radarr", arr.radarr)):
        client = factory()
        if not client:
            out[name] = {"enabled": False}
            continue
        try:
            st = await client.req("GET", "/system/status")
            out[name] = {"enabled": True, "ok": True, "version": (st or {}).get("version")}
        except ApiError as e:
            out[name] = {"enabled": True, "ok": False, "error": str(e)}
        finally:
            await client.close()
    out["plex"] = {"enabled": bool(config.PLEX_TOKEN)}
    return out


# ---------------------------------------------------------------- posters

IMG_RE = re.compile(r"^/library/metadata/\d+/(thumb|art)(/\d+)?$")


@app.get("/api/poster")
async def poster(img: str, w: int = 120):
    if not IMG_RE.match(img) or not sync.configured():
        raise HTTPException(404)
    w = 240 if w > 120 else 120
    h = w * 3 // 2
    key = hashlib.sha1(f"{img}|{w}".encode()).hexdigest()
    path = os.path.join(config.IMAGE_CACHE_DIR, key[:2], key + ".jpg")
    if not os.path.exists(path):
        t = Tautulli()
        try:
            data, _ = await t.image(img, w, h)
        except (ApiError, Exception):
            raise HTTPException(404)
        finally:
            await t.close()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "wb") as f:
            f.write(data)
        os.replace(path + ".tmp", path)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=604800"})


# ---------------------------------------------------------------- UI

app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    with open(os.path.join(STATIC, "index.html"), encoding="utf-8") as f:
        html = f.read().replace("{{v}}", config.VERSION)
    return Response(html, media_type="text/html", headers={"Cache-Control": "no-cache"})


@app.get("/manifest.webmanifest")
def manifest():
    return JSONResponse({
        "name": "Shelflife", "short_name": "Shelflife", "start_url": "/", "display": "standalone",
        "background_color": "#1b2129", "theme_color": "#1b2129",
        "icons": [{"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"},
                  {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"}],
    })
