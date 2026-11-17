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

from . import arr, auth, config, db, issues, jobs, leaving, media, notify, seerr, stats, storage, sync, tasks
from .clients import ApiError, Tautulli

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
STATIC = os.path.join(os.path.dirname(__file__), "static")
_background: set[asyncio.Task] = set()


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    os.makedirs(config.IMAGE_CACHE_DIR, exist_ok=True)
    loop_task = asyncio.create_task(tasks.loop())
    yield
    loop_task.cancel()


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


# ---------------------------------------------------------------- status

@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/api/status")
def status():
    last = db.kv_get("last_sync") or {}
    server = db.kv_get("server") or {}
    sched = db.query("SELECT COUNT(*) AS n, MIN(delete_at) AS next, COALESCE(SUM(size),0) AS size "
                     "FROM leaving WHERE status='scheduled'")[0]
    return {
        "configured": sync.configured(),
        "version": config.VERSION,
        "sync": {**sync.state, "last_success": sync.state["last_success"] or last.get("at"),
                 "last_full": sync.state["last_full"] or db.kv_get("last_full_sync")},
        "size_source": sync.state["size_source"] or last.get("size_source"),
        "plex_direct": bool(config.PLEX_TOKEN),
        "auth": auth.enabled(),
        "integrations": {"plex": bool(config.PLEX_TOKEN), **arr.enabled(), "seerr": seerr.enabled(),
                         "notify": notify.enabled()},
        "interval_hours": config.SYNC_INTERVAL_HOURS,
        "full_sync_hours": config.FULL_SYNC_HOURS,
        "server": {"identifier": server.get("identifier"), "name": server.get("name")},
        "counts": last,
        "leaving": {"scheduled": sched["n"], "next": sched["next"], "size": sched["size"]},
        "keep_count": db.query("SELECT COUNT(*) AS n FROM exclusions")[0]["n"],
        "digest": notify.schedule_text(),
        "issues": issues.summary(),
    }


@app.post("/api/sync")
async def trigger_sync(full: bool = False):
    if not sync.configured():
        raise HTTPException(400, "Set TAUTULLI_URL and TAUTULLI_API_KEY first")
    if not sync.state["running"]:
        _spawn(sync.run_sync(full=True if full else None))
        await asyncio.sleep(0.05)
    return sync.state


@app.get("/api/libraries")
def libraries():
    return db.query("SELECT section_id, name, type, item_count FROM libraries ORDER BY type, name")


# ---------------------------------------------------------------- library rows

def _finish(row: dict, leaving_at: dict) -> dict:
    q = stats.quality(row.pop("quality", None), row.get("resolution"))
    row.pop("guids", None)
    row.update(quality_label=q["label"], is4k=q["is4k"], versions=q["versions"])
    row["leaving_at"] = leaving_at.get(row["rating_key"]) or (leaving_at.get(row["show_key"]) if row.get("show_key")
                                                               else None)
    return row


@app.get("/api/items")
def items(view: str = "titles"):
    settings = db.get_settings()
    excl = {r["rating_key"] for r in db.query("SELECT rating_key FROM exclusions")}
    leaving_at = leaving.active_map()

    if view == "seasons":
        st = stats.stats("season", settings)
        rows = db.query(
            "SELECT s.rating_key, s.show_key, s.section_id, s.season_index, s.title, s.size, s.episode_count, "
            "s.added_at, s.quality, COALESCE(s.thumb, i.thumb) AS thumb, i.art, i.title AS show_title, i.year "
            "FROM seasons s LEFT JOIN items i ON i.rating_key = s.show_key")
        seerr.annotate(rows, "season", settings)
        for r in rows:
            r["kind"] = "season"
            stats.with_stats(r, st)
            r["excluded"] = r["rating_key"] in excl or r["show_key"] in excl
            r["excluded_via_show"] = r["show_key"] in excl and r["rating_key"] not in excl
            _finish(r, leaving_at)
        return rows

    st = {"movie": stats.stats("movie", settings), "show": stats.stats("show", settings)}
    rows = db.query(
        "SELECT rating_key, section_id, kind, title, sort_title, year, thumb, art, added_at, size, season_count, "
        "episode_count, resolution, quality, guids FROM items")
    seerr.annotate(rows, None, settings)
    for r in rows:
        stats.with_stats(r, st[r["kind"]])
        r["excluded"] = r["rating_key"] in excl
        _finish(r, leaving_at)
    return rows


@app.get("/api/item/{rating_key}")
def item(rating_key: int):
    settings = db.get_settings()
    ignored = set(settings["ignored_users"])
    excl = {r["rating_key"]: r for r in db.query("SELECT * FROM exclusions")}
    server = db.kv_get("server") or {}
    leaving_rows = {r["rating_key"]: r for r in db.query("SELECT * FROM leaving WHERE status='scheduled'")}

    found = db.query("SELECT * FROM items WHERE rating_key=?", (rating_key,))
    seasons = []
    if found:
        it = found[0]
        level = it["kind"]
        stats.with_stats(it, stats.stats(level, settings, keys=[rating_key]))
        if level == "show":
            st = stats.stats("season", settings)
            seasons = [stats.with_stats(s, st) for s in db.query(
                "SELECT rating_key, season_index, title, size, episode_count, added_at FROM seasons "
                "WHERE show_key=? ORDER BY season_index", (rating_key,))]
            for s in seasons:
                s["excluded"] = s["rating_key"] in excl
                s["leaving_at"] = (leaving_rows.get(s["rating_key"]) or {}).get("delete_at")
        it["exclusion"] = excl.get(rating_key)
    else:
        found = db.query(
            "SELECT s.*, i.title AS show_title, i.year, i.art, COALESCE(s.thumb, i.thumb) AS thumb FROM seasons s "
            "LEFT JOIN items i ON i.rating_key = s.show_key WHERE s.rating_key=?", (rating_key,))
        if not found:
            raise HTTPException(404, "Not in the library (try a sync)")
        it = found[0]
        it["kind"] = level = "season"
        stats.with_stats(it, stats.stats("season", settings, keys=[rating_key]))
        it["exclusion"] = excl.get(rating_key)
        it["show_exclusion"] = excl.get(it["show_key"])

    hist_col = stats.LEVELS[level][0]
    q = stats.quality(it.pop("quality", None), it.get("resolution"))
    it.pop("guids", None)
    it["quality"] = q
    lib = db.query("SELECT name FROM libraries WHERE section_id=?", (it["section_id"],))
    it["library"] = lib[0]["name"] if lib else None
    it["leaving"] = leaving_rows.get(rating_key) or (leaving_rows.get(it.get("show_key")) if level == "season" else None)
    it["requests"] = seerr.requests_for(it, level, settings) if seerr.enabled() else []
    history = db.query(
        f"SELECT row_id, date, user_id, friendly_name, full_title, title, season, episode, started, stopped, paused, "
        f"duration, percent_complete, watched_status, group_count FROM history WHERE {hist_col}=? "
        f"ORDER BY started DESC", (rating_key,))
    min_s = settings["min_play_minutes"] * 60
    for h in history:
        h["counted"] = h["user_id"] not in ignored and (h["duration"] or 0) >= min_s
    plex_key = it.get("show_key") if level == "season" else rating_key
    it["plex_url"] = (
        f"https://app.plex.tv/desktop/#!/server/{server['identifier']}/details?key=%2Flibrary%2Fmetadata%2F{plex_key}"
        if server.get("identifier") and config.PLEX_TOKEN else None)
    return {"item": it, "seasons": seasons, "history": history}


@app.get("/api/item/{rating_key}/media")
async def item_media(rating_key: int):
    return await media.details(rating_key)


@app.get("/api/users")
def users():
    return db.query(
        "SELECT user_id, friendly_name, COUNT(*) AS plays, MAX(started) AS last_played FROM history "
        "WHERE user_id IS NOT NULL GROUP BY user_id ORDER BY plays DESC")


@app.get("/api/settings")
def get_settings():
    return db.get_settings()


@app.put("/api/settings")
async def put_settings(body: dict = Body(...)):
    before = db.get_settings()
    after = db.save_settings(body)
    if any(before[k] != after[k] for k in ("leaving_collection", "leaving_collection_name")):
        _spawn(leaving.sync_collections())
    return after


@app.get("/api/storage")
def get_storage():
    return storage.get()


# ---------------------------------------------------------------- keep list

@app.get("/api/exclusions")
def exclusions():
    return db.query(
        "SELECT e.*, COALESCE(i.size, s.size) AS size, COALESCE(i.thumb, s.thumb, si.thumb) AS thumb, "
        "si.title AS show_title, (i.rating_key IS NOT NULL OR s.rating_key IS NOT NULL) AS in_library "
        "FROM exclusions e LEFT JOIN items i ON i.rating_key=e.rating_key "
        "LEFT JOIN seasons s ON s.rating_key=e.rating_key LEFT JOIN items si ON si.rating_key=s.show_key "
        "ORDER BY e.created_at DESC")


def _exclude(c, rk: int, note: str | None) -> int:
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
    # Keeping something takes it (and a kept show's seasons) off the Leaving soon list.
    return c.execute("UPDATE leaving SET status='cancelled', status_at=?, note='Added to the keep list' "
                     "WHERE status='scheduled' AND (rating_key=? OR rating_key IN "
                     "(SELECT rating_key FROM seasons WHERE show_key=?))", (int(time.time()), rk, rk)).rowcount


@app.put("/api/exclusions/{rating_key}")
async def add_exclusion(rating_key: int, body: dict = Body(default={})):
    note = (body or {}).get("note")
    note = note.strip()[:500] if isinstance(note, str) and note.strip() else None
    with db.tx() as c:
        cancelled = _exclude(c, rating_key, note)
        if note is None and "note" in (body or {}):
            c.execute("UPDATE exclusions SET note=NULL WHERE rating_key=?", (rating_key,))
    if cancelled:
        _spawn(leaving.sync_collections())
    return {"ok": True, "leaving_cancelled": cancelled}


@app.post("/api/exclusions/bulk")
async def add_exclusions(body: dict = Body(...)):
    keys = [int(k) for k in body.get("rating_keys", [])][:5000]
    cancelled = 0
    with db.tx() as c:
        for k in keys:
            try:
                cancelled += _exclude(c, k, None)
            except HTTPException:
                pass
    if cancelled:
        _spawn(leaving.sync_collections())
    return {"ok": True, "count": len(keys), "leaving_cancelled": cancelled}


@app.delete("/api/exclusions/{rating_key}")
def remove_exclusion(rating_key: int):
    with db.tx() as c:
        c.execute("DELETE FROM exclusions WHERE rating_key=?", (rating_key,))
    return {"ok": True}


# ---------------------------------------------------------------- Sonarr / Radarr

@app.post("/api/arr/plan")
async def arr_plan(body: dict = Body(...)):
    keys = [int(k) for k in body.get("rating_keys", [])][:2000]
    try:
        items_ = await arr.plan(keys)
    except ApiError as e:
        raise HTTPException(502, str(e))
    return {"items": items_, "enabled": arr.enabled()}


@app.get("/api/arr/{rating_key}")
async def arr_info(rating_key: int):
    try:
        client, kind, obj, season = await arr.resolve(rating_key)
    except arr.NotInLibrary as e:
        raise HTTPException(404, str(e))
    except ApiError as e:
        raise HTTPException(502, str(e))
    service = "radarr" if kind == "movie" else "sonarr"
    if not client:
        return {"service": service, "enabled": False}
    try:
        if not obj:
            target, key, _ = arr._plex_item(rating_key)
            ids = await arr.plex_guids(key) if key else {}
            return {"service": service, "enabled": True, "matched": False,
                    "add_url": arr.add_url(kind, ids, target["title"], target["year"]) if target else None}
        blocked, warning = arr.shared_check(client.name, kind, obj, arr._plex_item(rating_key)[1])
        return {"enabled": True, "matched": True, "kept": bool(arr.kept_title(rating_key)), "blocked": blocked,
                "warning": warning, **arr.describe(client, kind, obj, season)}
    finally:
        await client.close()


# ---------------------------------------------------------------- issues

@app.get("/api/issues")
def issues_list():
    return issues.result()


@app.post("/api/issues/check")
async def issues_check():
    if not any(arr.enabled().values()):
        raise HTTPException(400, "Connect Sonarr or Radarr first")
    issues.start()
    await asyncio.sleep(0.05)
    return issues.result()


@app.post("/api/issues/ignore")
def issues_ignore(body: dict = Body(...)):
    keys = [str(k)[:200] for k in ([body.get("key")] + list(body.get("more") or []))[:5000] if k]
    keys = [k for k in keys if re.fullmatch(r"[a-z_]+:[\w:]+", k)]
    if not keys:
        raise HTTPException(400, "Unknown issue")
    for k in keys:
        issues.set_ignored(k, bool(body.get("ignored", True)))
    return issues.summary()


@app.post("/api/arr/{rating_key}/delete")
async def arr_delete(rating_key: int, body: dict = Body(default={})):
    try:
        res = await arr.perform_delete(rating_key, body.get("mode", "remove"), bool(body.get("exclusion", True)),
                                       bool(body.get("unmonitor", True)), source="manual")
    except arr.DeleteError as e:
        code = 409 if "keep list" in str(e) else 400 if "isn't set up" in str(e) or "mode" in str(e) else 502
        raise HTTPException(code, str(e))
    _spawn(arr.plex_refresh([res.get("section_id")]))
    _spawn(leaving.sync_collections())
    return res


@app.put("/api/arr/{rating_key}/monitor")
async def arr_monitor(rating_key: int, body: dict = Body(...)):
    """Sonarr monitoring. season omitted = the series itself; season=N = that season."""
    try:
        client, kind, obj, _ = await arr.resolve(rating_key)
    except (arr.NotInLibrary, ApiError) as e:
        raise HTTPException(404 if isinstance(e, arr.NotInLibrary) else 502, str(e))
    if not client or kind != "show":
        if client:
            await client.close()
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


# ---------------------------------------------------------------- bulk delete jobs

@app.post("/api/jobs/delete")
async def start_delete_job(body: dict = Body(...)):
    items_ = [{"rating_key": int(i["rating_key"]), "mode": i.get("mode") or "remove"}
              for i in body.get("items", []) if str(i.get("rating_key", "")).isdigit()][:2000]
    if not items_:
        raise HTTPException(400, "Nothing to delete")
    source = body.get("source") if body.get("source") in ("bulk", "review", "manual") else "bulk"
    job = jobs.start_delete(items_, bool(body.get("exclusion", True)), bool(body.get("unmonitor", True)), source)
    return job


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    job = jobs.JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "No such job")
    return job


# ---------------------------------------------------------------- history

@app.get("/api/history")
def deletion_history(limit: int = 500, offset: int = 0):
    totals = db.query("SELECT COUNT(*) AS n, COALESCE(SUM(size),0) AS size, MIN(at) AS since FROM deletions")[0]
    rows = db.query("SELECT * FROM deletions ORDER BY at DESC, id DESC LIMIT ? OFFSET ?",
                    (max(1, min(limit, 5000)), max(0, offset)))
    return {"items": rows, "count": totals["n"], "size": totals["size"], "since": totals["since"]}


# ---------------------------------------------------------------- leaving soon

@app.get("/api/leaving")
def leaving_list():
    return {"items": leaving.listing(), "collection": leaving.collection_status(),
            "default_days": db.get_settings()["leaving_days"]}


@app.post("/api/leaving")
async def leaving_schedule(body: dict = Body(...)):
    keys = [int(k) for k in body.get("rating_keys", [])][:2000]
    if not keys:
        raise HTTPException(400, "Nothing to schedule")
    days = max(1, min(365, int(float(body.get("days") or db.get_settings()["leaving_days"]))))
    return await leaving.schedule(keys, days, body.get("mode") or "remove", bool(body.get("exclusion", True)),
                                  bool(body.get("unmonitor", True)))


@app.delete("/api/leaving/{rating_key}")
async def leaving_cancel(rating_key: int):
    return {"ok": await leaving.cancel(rating_key)}


@app.post("/api/leaving/{rating_key}/extend")
def leaving_extend(rating_key: int, body: dict = Body(default={})):
    when = leaving.extend(rating_key, max(1, min(365, int(float(body.get("days") or 7)))))
    if when is None:
        raise HTTPException(404, "Not scheduled to leave")
    return {"ok": True, "delete_at": when}


@app.post("/api/leaving/{rating_key}/now")
async def leaving_now(rating_key: int):
    try:
        return await leaving.delete_now(rating_key)
    except arr.DeleteError as e:
        raise HTTPException(409 if "keep list" in str(e) else 400, str(e))


# ---------------------------------------------------------------- integrations & notifications

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
    if seerr.enabled():
        try:
            out["seerr"] = {"enabled": True, "ok": True, **await seerr.check()}
        except ApiError as e:
            out["seerr"] = {"enabled": True, "ok": False, "error": str(e)}
    else:
        out["seerr"] = {"enabled": False}
    out["plex"] = {"enabled": bool(config.PLEX_TOKEN)}
    out["notify"] = {"enabled": notify.enabled(), "schedule": notify.schedule_text()}
    out["storage"] = {"enabled": bool(storage.get()), "dir": config.STORAGE_DIR}
    return out


@app.post("/api/notify/test")
async def notify_test():
    try:
        sent = await notify.send_digest()
    except ApiError as e:
        raise HTTPException(502, str(e))
    if not sent:
        raise HTTPException(400, "Nothing to report yet. Run a sync first.")
    return {"ok": True}


# ---------------------------------------------------------------- images

IMG_RE = re.compile(r"^/library/metadata/\d+/(thumb|art)(/\d+)?$")


async def _image(img: str, w: int, h: int):
    if not IMG_RE.match(img) or not sync.configured():
        raise HTTPException(404)
    key = hashlib.sha1(f"{img}|{w}x{h}".encode()).hexdigest()
    path = os.path.join(config.IMAGE_CACHE_DIR, key[:2], key + ".jpg")
    if not os.path.exists(path):
        t = Tautulli()
        try:
            data, _ = await t.image(img, w, h)
        except Exception:  # noqa: BLE001 - any failure is just a missing image
            raise HTTPException(404)
        finally:
            await t.close()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "wb") as f:
            f.write(data)
        os.replace(path + ".tmp", path)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=604800"})


@app.get("/api/poster")
async def poster(img: str, w: int = 120):
    w = 240 if w > 120 else 120
    return await _image(img, w, w * 3 // 2)


@app.get("/api/art")
async def art(img: str):
    return await _image(img, 1280, 720)


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
