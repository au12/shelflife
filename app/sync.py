import asyncio
import logging
import time

from . import arr, config, db
from .clients import ApiError, Plex, Tautulli, _int, plex_media_size

log = logging.getLogger("shelflife.sync")

state = {
    "running": False,
    "phase": "",
    "detail": "",
    "started_at": None,
    "last_success": None,
    "last_error": None,
    "size_source": None,
}

_lock = asyncio.Lock()


def _set(phase, detail=""):
    state["phase"], state["detail"] = phase, detail
    log.info("%s %s", phase, detail)


def configured() -> bool:
    return bool(config.TAUTULLI_URL and config.TAUTULLI_API_KEY)


async def run_sync() -> None:
    if _lock.locked():
        return
    async with _lock:
        state.update(running=True, started_at=int(time.time()), last_error=None)
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
                    li, ls = await _inventory_tautulli(t, lib)
                lib["item_count"] = len(li)
                items += li
                seasons += ls

            items, seasons = _drop_deleted(items, seasons)

            _set("Reading watch history")
            history = await _history(t)

            _set("Saving")
            with db.tx() as c:
                c.execute("DELETE FROM libraries")
                c.execute("DELETE FROM items")
                c.execute("DELETE FROM seasons")
                c.execute("DELETE FROM history")
                c.executemany(
                    "INSERT INTO libraries(section_id,name,type,item_count) VALUES(:section_id,:name,:type,:item_count)",
                    libs)
                c.executemany(
                    "INSERT OR REPLACE INTO items(rating_key,section_id,kind,title,sort_title,year,thumb,added_at,size,"
                    "season_count,episode_count,resolution) VALUES(:rating_key,:section_id,:kind,:title,:sort_title,"
                    ":year,:thumb,:added_at,:size,:season_count,:episode_count,:resolution)", items)
                c.executemany(
                    "INSERT OR REPLACE INTO seasons(rating_key,show_key,section_id,season_index,title,thumb,added_at,"
                    "size,episode_count) VALUES(:rating_key,:show_key,:section_id,:season_index,:title,:thumb,"
                    ":added_at,:size,:episode_count)", seasons)
                c.executemany(
                    "INSERT OR REPLACE INTO history VALUES(:row_id,:section_id,:media_type,:rating_key,"
                    ":parent_rating_key,:grandparent_rating_key,:user_id,:user,:friendly_name,:date,:started,"
                    ":stopped,:paused,:duration,:full_title,:title,:season,:episode,:percent_complete,"
                    ":watched_status,:group_count)", history)

            state["last_success"] = int(time.time())
            db.kv_set("last_sync", {"at": state["last_success"], "size_source": state["size_source"],
                                    "items": len(items), "seasons": len(seasons), "history": len(history)})
            _set("Done", f"{len(items)} titles, {len(seasons)} seasons, {len(history)} plays")
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

async def _inventory_plex(plex: Plex, lib: dict):
    sid = lib["section_id"]
    if lib["type"] == "movie":
        items = []
        for m in await plex.all_of_type(sid, 1):
            media = (m.get("Media") or [{}])[0]
            items.append(_item(sid, "movie", m.get("ratingKey"), m.get("title"), m.get("titleSort"), m.get("year"),
                               m.get("thumb"), m.get("addedAt"), plex_media_size(m),
                               resolution=media.get("videoResolution")))
        return items, []

    shows = await plex.all_of_type(sid, 2)
    _set("Reading library", f"{lib['name']}: seasons")
    season_meta = await plex.all_of_type(sid, 3)
    _set("Reading library", f"{lib['name']}: episodes")
    episodes = await plex.all_of_type(sid, 4)

    season_size, season_eps = {}, {}
    for ep in episodes:
        pk = _int(ep.get("parentRatingKey"))
        season_size[pk] = season_size.get(pk, 0) + plex_media_size(ep)
        season_eps[pk] = season_eps.get(pk, 0) + 1

    seasons, show_size, show_seasons, show_eps = [], {}, {}, {}
    for s in season_meta:
        rk, show = _int(s.get("ratingKey")), _int(s.get("parentRatingKey"))
        size, eps = season_size.get(rk, 0), season_eps.get(rk, 0)
        if eps == 0:
            continue
        seasons.append(_season(sid, show, rk, s.get("index"), s.get("title"), s.get("thumb"), s.get("addedAt"),
                               size, eps))
        show_size[show] = show_size.get(show, 0) + size
        show_seasons[show] = show_seasons.get(show, 0) + 1
        show_eps[show] = show_eps.get(show, 0) + eps

    items = []
    for sh in shows:
        rk = _int(sh.get("ratingKey"))
        items.append(_item(sid, "show", rk, sh.get("title"), sh.get("titleSort"), sh.get("year"), sh.get("thumb"),
                           sh.get("addedAt"), show_size.get(rk, 0), show_seasons.get(rk, 0), show_eps.get(rk, 0)))
    return items, seasons


async def _inventory_tautulli(t: Tautulli, lib: dict):
    sid = lib["section_id"]
    rows = await t.media_info(sid)
    if lib["type"] == "movie":
        return [_item(sid, "movie", r.get("rating_key"), r.get("title"), r.get("sort_title"), r.get("year"),
                      r.get("thumb"), r.get("added_at"), _int(r.get("file_size")),
                      resolution=r.get("video_resolution")) for r in rows], []

    # Tautulli's API doesn't expose show-level file sizes, so walk show -> seasons -> episodes.
    sem = asyncio.Semaphore(max(1, config.SYNC_CONCURRENCY))
    done = 0

    async def walk(show: dict):
        nonlocal done
        show_key = _int(show.get("rating_key"))
        async with sem:
            season_rows = await t.media_info(sid, rating_key=show_key)
        out = []
        for s in season_rows:
            if s.get("media_type") not in ("season", None, ""):
                continue
            async with sem:
                eps = await t.media_info(sid, rating_key=s.get("rating_key"))
            if not eps:
                continue
            out.append(_season(sid, show_key, s.get("rating_key"), s.get("media_index"), s.get("title"),
                               s.get("thumb"), s.get("added_at"), sum(_int(e.get("file_size")) for e in eps),
                               len(eps)))
        done += 1
        if done % 10 == 0 or done == len(rows):
            _set("Reading library", f"{lib['name']}: {done}/{len(rows)} shows")
        return show, out

    results = await asyncio.gather(*(walk(r) for r in rows))
    items, seasons = [], []
    for show, show_seasons in results:
        seasons += show_seasons
        items.append(_item(sid, "show", show.get("rating_key"), show.get("title"), show.get("sort_title"),
                           show.get("year"), show.get("thumb"), show.get("added_at"),
                           sum(s["size"] for s in show_seasons), len(show_seasons),
                           sum(s["episode_count"] for s in show_seasons)))
    return items, seasons


def _item(sid, kind, rk, title, sort_title, year, thumb, added_at, size, season_count=0, episode_count=0,
          resolution=None):
    return {"rating_key": _int(rk), "section_id": sid, "kind": kind, "title": title or "Untitled",
            "sort_title": sort_title or title or "", "year": _int(year, None), "thumb": thumb or None,
            "added_at": _int(added_at, None), "size": int(size or 0), "season_count": season_count,
            "episode_count": episode_count, "resolution": (resolution or "").lower() or None}


def _season(sid, show_key, rk, index, title, thumb, added_at, size, eps):
    return {"rating_key": _int(rk), "show_key": _int(show_key), "section_id": sid, "season_index": _int(index, None),
            "title": title or (f"Season {index}" if index not in (None, "") else "Season"), "thumb": thumb or None,
            "added_at": _int(added_at, None), "size": int(size or 0), "episode_count": eps}


# ---------------------------------------------------------------- history

async def _history(t: Tautulli) -> list[dict]:
    out, start, page = [], 0, 5000
    while True:
        data = await t.call("get_history", grouping=1, start=start, length=page, order_column="date",
                            order_dir="desc", include_archived=1) or {}
        rows = data.get("data") or []
        for h in rows:
            if h.get("media_type") not in ("movie", "episode"):
                continue
            out.append({
                "row_id": _int(h.get("row_id") or h.get("id")),
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
            })
        start += len(rows)
        _set("Reading watch history", f"{start} sessions")
        if not rows or start >= _int(data.get("recordsFiltered"), 0):
            return out


# ---------------------------------------------------------------- scheduler

async def scheduler():
    if not configured():
        log.warning("TAUTULLI_URL / TAUTULLI_API_KEY not set; sync disabled")
        return
    last = (db.kv_get("last_sync") or {}).get("at")
    state["last_success"] = last
    state["size_source"] = (db.kv_get("last_sync") or {}).get("size_source")
    while True:
        last = state["last_success"] or 0
        if time.time() - last >= config.SYNC_INTERVAL_HOURS * 3600 and not state["running"]:
            await run_sync()
            if state["last_error"]:
                # back off a bit before retrying a failed sync
                await asyncio.sleep(900)
                continue
        await asyncio.sleep(60)
