import asyncio
import json
import logging
import time

from . import arr, config, db, seerr, stats
from .clients import ApiError, Plex, Tautulli, _int, plex_media_size

log = logging.getLogger("shelflife.sync")

state = {
    "running": False,
    "full": False,
    "phase": "",
    "detail": "",
    "started_at": None,
    "last_success": None,
    "last_full": None,
    "last_error": None,
    "last_attempt": None,
    "size_source": None,
    "requests_error": None,
}

_lock = asyncio.Lock()


def _set(phase, detail=""):
    state["phase"], state["detail"] = phase, detail
    log.info("%s %s", phase, detail)


def configured() -> bool:
    return bool(config.TAUTULLI_URL and config.TAUTULLI_API_KEY)


def load_state() -> None:
    last = db.kv_get("last_sync") or {}
    state["last_success"] = last.get("at")
    state["size_source"] = last.get("size_source")
    state["last_full"] = db.kv_get("last_full_sync")


def full_due() -> bool:
    last_full = db.kv_get("last_full_sync") or 0
    return not db.kv_get("history_cursor") or time.time() - last_full >= config.FULL_SYNC_HOURS * 3600


def due() -> bool:
    if state["running"]:
        return False
    if state["last_error"] and time.time() - (state["last_attempt"] or 0) < 900:
        return False  # back off after a failed sync
    return time.time() - (state["last_success"] or 0) >= config.SYNC_INTERVAL_HOURS * 3600


async def ensure_fresh(max_age: int) -> None:
    """Wait for a running sync, or run a quick one if the history is older than max_age seconds."""
    while state["running"]:
        await asyncio.sleep(1)
    if time.time() - (state["last_success"] or 0) > max_age:
        await run_sync(full=False)


async def run_sync(full: bool | None = None) -> None:
    if _lock.locked():
        return
    async with _lock:
        full = full_due() if full is None else bool(full)
        state.update(running=True, full=full, started_at=int(time.time()), last_attempt=int(time.time()),
                     last_error=None)
        t = Tautulli()
        plex = None
        try:
            _set("Connecting to Tautulli")
            server = await t.call("get_server_info") or {}
            db.kv_set("server", {
                "identifier": server.get("pms_identifier"),
                "name": server.get("pms_name"),
                "url": server.get("pms_url"),
            })

            plex_url = config.PLEX_URL or server.get("pms_url") or ""
            if config.PLEX_TOKEN and plex_url:
                plex = Plex(plex_url, config.PLEX_TOKEN)
                state["size_source"] = "plex"
            else:
                state["size_source"] = "tautulli"

            libs = []
            for lib in await t.call("get_libraries") or []:
                if lib.get("section_type") not in ("movie", "show"):
                    continue
                if str(lib.get("is_active", 1)) == "0":
                    continue
                name = (lib.get("section_name") or "").strip()
                if name.lower() in config.IGNORE_LIBRARIES or str(lib.get("section_id")) in config.IGNORE_LIBRARIES:
                    continue
                libs.append({"section_id": _int(lib["section_id"]), "name": name, "type": lib["section_type"]})

            items, seasons = [], []
            for lib in libs:
                _set("Reading library", lib["name"])
                if plex:
                    li, ls = await _inventory_plex(plex, lib)
                else:
                    li, ls = await _inventory_tautulli(t, lib, refresh=full)
                lib["item_count"] = len(li)
                items += li
                seasons += ls

            items, seasons = _drop_deleted(items, seasons)

            _set("Reading watch history", "everything" if full else "new plays")
            history, cursor = await _history(t, full)

            requests = None
            state["requests_error"] = None
            if seerr.enabled():
                _set("Reading requests")
                try:
                    requests = await seerr.fetch_all()
                except ApiError as e:
                    state["requests_error"] = str(e)
                    log.warning("Couldn't read requests: %s", e)

            _set("Saving")
            with db.tx() as c:
                c.execute("DELETE FROM libraries")
                c.execute("DELETE FROM items")
                c.execute("DELETE FROM seasons")
                c.executemany(
                    "INSERT INTO libraries(section_id,name,type,item_count) VALUES(:section_id,:name,:type,:item_count)",
                    libs)
                c.executemany(
                    "INSERT OR REPLACE INTO items(rating_key,section_id,kind,title,sort_title,year,thumb,art,added_at,"
                    "size,season_count,episode_count,resolution,quality,guids) VALUES(:rating_key,:section_id,:kind,"
                    ":title,:sort_title,:year,:thumb,:art,:added_at,:size,:season_count,:episode_count,:resolution,"
                    ":quality,:guids)", items)
                c.executemany(
                    "INSERT OR REPLACE INTO seasons(rating_key,show_key,section_id,season_index,title,thumb,added_at,"
                    "size,episode_count,quality) VALUES(:rating_key,:show_key,:section_id,:season_index,:title,:thumb,"
                    ":added_at,:size,:episode_count,:quality)", seasons)
                if full:
                    c.execute("DELETE FROM history")
                c.executemany(
                    "INSERT OR REPLACE INTO history VALUES(:row_id,:section_id,:media_type,:rating_key,"
                    ":parent_rating_key,:grandparent_rating_key,:user_id,:user,:friendly_name,:date,:started,"
                    ":stopped,:paused,:duration,:full_title,:title,:season,:episode,:percent_complete,"
                    ":watched_status,:group_count)", history)
                if requests is not None:
                    c.execute("DELETE FROM requests")
                    c.executemany(
                        "INSERT OR REPLACE INTO requests VALUES(:request_id,:media_type,:tmdb_id,:tvdb_id,:rating_key,"
                        ":rating_key_4k,:requested_by,:requested_by_plex,:requested_at,:seasons)", requests)
                plays = c.execute("SELECT COUNT(*) FROM history").fetchone()[0]

            now = int(time.time())
            state["last_success"] = now
            db.kv_set("history_cursor", cursor)
            if full:
                state["last_full"] = now
                db.kv_set("last_full_sync", now)
            db.kv_set("last_sync", {"at": now, "size_source": state["size_source"], "full": full,
                                    "items": len(items), "seasons": len(seasons), "history": plays,
                                    "new_plays": len(history)})
            _set("Done", f"{len(items)} titles, {len(seasons)} seasons, {plays} plays")
        except ApiError as e:
            state["last_error"] = str(e)
            log.error("Sync failed: %s", e)
        except Exception as e:  # noqa: BLE001 - keep the scheduler alive, surface the error in the UI
            state["last_error"] = f"{e.__class__.__name__}: {e}"
            log.exception("Sync failed")
        finally:
            state["running"] = False
            await t.close()
            if plex:
                await plex.close()


def _drop_deleted(items: list[dict], seasons: list[dict]):
    """Leave out anything deleted through Shelflife that Plex hasn't caught up with yet."""
    hidden = arr.hidden_keys()
    if not hidden:
        return items, seasons
    gone_seasons = {s["rating_key"] for s in seasons if s["rating_key"] in hidden}
    seasons = [s for s in seasons if s["rating_key"] not in hidden and s["show_key"] not in hidden]
    by_show: dict[int, list[dict]] = {}
    for s in seasons:
        by_show.setdefault(s["show_key"], []).append(s)
    kept = []
    for it in items:
        if it["rating_key"] in hidden:
            continue
        if it["kind"] == "show" and gone_seasons:
            mine = by_show.get(it["rating_key"], [])
            if not mine and it["season_count"]:
                continue
            it.update(size=sum(s["size"] for s in mine), season_count=len(mine),
                      episode_count=sum(s["episode_count"] for s in mine))
        kept.append(it)
    return kept, seasons


# ---------------------------------------------------------------- inventories

def _bump(q: dict, key: str, value, n: int = 1) -> None:
    if value:
        bucket = q.setdefault(key, {})
        bucket[value] = bucket.get(value, 0) + n


def _merge(into: dict, q: dict) -> None:
    for key in ("res", "codec"):
        for k, n in (q.get(key) or {}).items():
            _bump(into, key, k, n)


def _plex_guids(meta: dict) -> str | None:
    ids = arr.parse_guids([g.get("id") for g in meta.get("Guid") or []] + [meta.get("guid") or ""])
    return json.dumps(ids) if ids else None


async def _inventory_plex(plex: Plex, lib: dict):
    sid = lib["section_id"]
    if lib["type"] == "movie":
        items = []
        for m in await plex.all_of_type(sid, 1, guids=True):
            medias = m.get("Media") or [{}]
            q: dict = {"versions": len(m.get("Media") or []) or 1}
            for media in medias:
                _bump(q, "res", stats.norm_res(media.get("videoResolution")))
                _bump(q, "codec", (media.get("videoCodec") or "").lower() or None)
            items.append(_item(sid, "movie", m.get("ratingKey"), m.get("title"), m.get("titleSort"), m.get("year"),
                               m.get("thumb"), m.get("addedAt"), plex_media_size(m),
                               resolution=medias[0].get("videoResolution"), art=m.get("art"), quality=q,
                               guids=_plex_guids(m)))
        return items, []

    shows = await plex.all_of_type(sid, 2, guids=True)
    _set("Reading library", f"{lib['name']}: seasons")
    season_meta = await plex.all_of_type(sid, 3)
    _set("Reading library", f"{lib['name']}: episodes")
    episodes = await plex.all_of_type(sid, 4)

    season_size, season_eps, season_q = {}, {}, {}
    for ep in episodes:
        pk = _int(ep.get("parentRatingKey"))
        season_size[pk] = season_size.get(pk, 0) + plex_media_size(ep)
        season_eps[pk] = season_eps.get(pk, 0) + 1
        media = (ep.get("Media") or [{}])[0]
        q = season_q.setdefault(pk, {})
        _bump(q, "res", stats.norm_res(media.get("videoResolution")))
        _bump(q, "codec", (media.get("videoCodec") or "").lower() or None)

    seasons, show_size, show_seasons, show_eps, show_q = [], {}, {}, {}, {}
    for s in season_meta:
        rk, show = _int(s.get("ratingKey")), _int(s.get("parentRatingKey"))
        size, eps = season_size.get(rk, 0), season_eps.get(rk, 0)
        if eps == 0:
            continue
        seasons.append(_season(sid, show, rk, s.get("index"), s.get("title"), s.get("thumb"), s.get("addedAt"),
                               size, eps, season_q.get(rk)))
        show_size[show] = show_size.get(show, 0) + size
        show_seasons[show] = show_seasons.get(show, 0) + 1
        show_eps[show] = show_eps.get(show, 0) + eps
        _merge(show_q.setdefault(show, {}), season_q.get(rk) or {})

    items = []
    for sh in shows:
        rk = _int(sh.get("ratingKey"))
        items.append(_item(sid, "show", rk, sh.get("title"), sh.get("titleSort"), sh.get("year"), sh.get("thumb"),
                           sh.get("addedAt"), show_size.get(rk, 0), show_seasons.get(rk, 0), show_eps.get(rk, 0),
                           art=sh.get("art"), quality=show_q.get(rk), guids=_plex_guids(sh)))
    return items, seasons


async def _inventory_tautulli(t: Tautulli, lib: dict, refresh: bool = True):
    sid = lib["section_id"]
    rows = await t.media_info(sid, refresh=refresh)
    if lib["type"] == "movie":
        items = []
        for r in rows:
            q: dict = {}
            _bump(q, "res", stats.norm_res(r.get("video_resolution")))
            _bump(q, "codec", (r.get("video_codec") or "").lower() or None)
            items.append(_item(sid, "movie", r.get("rating_key"), r.get("title"), r.get("sort_title"), r.get("year"),
                               r.get("thumb"), r.get("added_at"), _int(r.get("file_size")),
                               resolution=r.get("video_resolution"), quality=q))
        return items, []

    # Tautulli's API doesn't expose show-level file sizes, so walk show -> seasons -> episodes.
    sem = asyncio.Semaphore(max(1, config.SYNC_CONCURRENCY))
    done = 0

    async def walk(show: dict):
        nonlocal done
        show_key = _int(show.get("rating_key"))
        async with sem:
            season_rows = await t.media_info(sid, rating_key=show_key, refresh=refresh)
        out = []
        for s in season_rows:
            if s.get("media_type") not in ("season", None, ""):
                continue
            async with sem:
                eps = await t.media_info(sid, rating_key=s.get("rating_key"), refresh=refresh)
            if not eps:
                continue
            q: dict = {}
            for e in eps:
                _bump(q, "res", stats.norm_res(e.get("video_resolution")))
                _bump(q, "codec", (e.get("video_codec") or "").lower() or None)
            out.append(_season(sid, show_key, s.get("rating_key"), s.get("media_index"), s.get("title"),
                               s.get("thumb"), s.get("added_at"), sum(_int(e.get("file_size")) for e in eps),
                               len(eps), q))
        done += 1
        if done % 10 == 0 or done == len(rows):
            _set("Reading library", f"{lib['name']}: {done}/{len(rows)} shows")
        return show, out

    results = await asyncio.gather(*(walk(r) for r in rows))
    items, seasons = [], []
    for show, show_seasons in results:
        seasons += show_seasons
        q: dict = {}
        for s in show_seasons:
            _merge(q, stats.load_json(s["quality"]))
        items.append(_item(sid, "show", show.get("rating_key"), show.get("title"), show.get("sort_title"),
                           show.get("year"), show.get("thumb"), show.get("added_at"),
                           sum(s["size"] for s in show_seasons), len(show_seasons),
                           sum(s["episode_count"] for s in show_seasons), quality=q))
    return items, seasons


def _item(sid, kind, rk, title, sort_title, year, thumb, added_at, size, season_count=0, episode_count=0,
          resolution=None, art=None, quality=None, guids=None):
    return {"rating_key": _int(rk), "section_id": sid, "kind": kind, "title": title or "Untitled",
            "sort_title": sort_title or title or "", "year": _int(year, None), "thumb": thumb or None,
            "art": art or None, "added_at": _int(added_at, None), "size": int(size or 0),
            "season_count": season_count, "episode_count": episode_count,
            "resolution": (resolution or "").lower() or None,
            "quality": json.dumps(quality) if quality else None, "guids": guids}


def _season(sid, show_key, rk, index, title, thumb, added_at, size, eps, quality=None):
    return {"rating_key": _int(rk), "show_key": _int(show_key), "section_id": sid, "season_index": _int(index, None),
            "title": title or (f"Season {index}" if index not in (None, "") else "Season"), "thumb": thumb or None,
            "added_at": _int(added_at, None), "size": int(size or 0), "episode_count": eps,
            "quality": json.dumps(quality) if quality else None}


# ---------------------------------------------------------------- history

def _history_row(h: dict) -> dict:
    return {
        # reference_id is stable when a paused play is resumed later; row_id isn't.
        "row_id": _int(h.get("reference_id") or h.get("row_id") or h.get("id")),
        "section_id": _int(h.get("section_id"), None),
        "media_type": h.get("media_type"),
        "rating_key": _int(h.get("rating_key"), None),
        "parent_rating_key": _int(h.get("parent_rating_key"), None),
        "grandparent_rating_key": _int(h.get("grandparent_rating_key"), None),
        "user_id": _int(h.get("user_id"), None),
        "user": h.get("user"),
        "friendly_name": h.get("friendly_name") or h.get("user"),
        "date": _int(h.get("date"), None),
        "started": _int(h.get("started"), None),
        "stopped": _int(h.get("stopped"), None),
        "paused": _int(h.get("paused_counter")),
        "duration": _int(h.get("play_duration", h.get("duration"))),
        "full_title": h.get("full_title"),
        "title": h.get("title"),
        "season": _int(h.get("parent_media_index"), None),
        "episode": _int(h.get("media_index"), None),
        "percent_complete": _int(h.get("percent_complete"), None),
        "watched_status": h.get("watched_status"),
        "group_count": _int(h.get("group_count"), 1),
    }


async def _history(t: Tautulli, full: bool) -> tuple[list[dict], dict]:
    """All history on a full sync; otherwise only plays since the last sync (with a 3 day overlap,
    so resumed plays that grew their group get refreshed)."""
    cursor = db.kv_get("history_cursor") or {}
    max_date = 0 if full else _int(cursor.get("max_date"))
    stop_before = None if full or not max_date else max_date - 3 * 86400
    out, start, page = [], 0, 1000 if stop_before else 5000
    while True:
        data = await t.call("get_history", grouping=1, start=start, length=page, order_column="date",
                            order_dir="desc", include_archived=1) or {}
        rows = data.get("data") or []
        reached = False
        for h in rows:
            when = _int(h.get("date"), 0)
            if stop_before and when and when < stop_before:
                reached = True
                break
            max_date = max(max_date, when)
            if h.get("media_type") in ("movie", "episode"):
                out.append(_history_row(h))
        start += len(rows)
        _set("Reading watch history", f"{start} sessions")
        if reached or not rows or start >= _int(data.get("recordsFiltered"), 0):
            return out, {"max_date": max_date}
