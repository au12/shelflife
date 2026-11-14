"""Leaving soon: titles scheduled for deletion, spared if anyone watches them before the date.
Optionally mirrored into a "Leaving Soon" collection in Plex so the household can see what's going."""
import asyncio
import logging
import time

from . import arr, config, db, notify, stats, sync
from .clients import ApiError, plex_client

log = logging.getLogger("shelflife.leaving")
DAY = 86400
RECENT_DAYS = 30
MAX_ATTEMPTS = 24          # hourly retries before giving up on a failed delete
SUMMARY = ("These are leaving the server soon. Watch one to keep it. "
           "(Managed by Shelflife; changes made here are overwritten.)")

_lock = asyncio.Lock()
_last_collection_sync = 0.0


def scheduled() -> list[dict]:
    return db.query("SELECT * FROM leaving WHERE status='scheduled' ORDER BY delete_at")


def active_map() -> dict[int, int]:
    """rating_key -> delete_at for everything scheduled."""
    return {r["rating_key"]: r["delete_at"] for r in
            db.query("SELECT rating_key, delete_at FROM leaving WHERE status='scheduled'")}


def listing() -> list[dict]:
    rows = db.query(
        "SELECT * FROM leaving WHERE status='scheduled' OR status='failed' OR status_at >= ? "
        "ORDER BY CASE status WHEN 'scheduled' THEN 0 WHEN 'failed' THEN 1 ELSE 2 END, delete_at, status_at DESC",
        (int(time.time()) - RECENT_DAYS * DAY,))
    for r in rows:
        lib = arr._row(r["rating_key"])
        r["in_library"] = bool(lib)
        if lib:
            r["size"] = lib.get("size") or r["size"]
    return rows


async def schedule(keys: list[int], days: int, mode: str, exclusion: bool, unmonitor: bool) -> dict:
    now = int(time.time())
    delete_at = now + int(days) * DAY
    done, skipped = [], []
    for rk in keys:
        row = arr._row(rk)
        if not row:
            skipped.append({"rating_key": rk, "title": "Unknown title", "reason": "No longer in the library"})
            continue
        if arr.kept_title(rk):
            skipped.append({"rating_key": rk, "title": row["title"], "reason": "On your keep list"})
            continue
        with db.tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO leaving(rating_key, kind, title, year, section_id, thumb, size, scheduled_at, "
                "delete_at, mode, exclusion, unmonitor, status, status_at, note, attempts, last_attempt) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'scheduled',?,NULL,0,NULL)",
                (rk, row["kind"], row["title"], row.get("year"), row.get("section_id"), row.get("thumb"),
                 row.get("size") or 0, now, delete_at, mode if mode in ("remove", "files") else "remove",
                 int(bool(exclusion)), int(bool(unmonitor)), now))
        done.append(rk)
    if done:
        await sync_collections()
    return {"scheduled": len(done), "skipped": skipped, "delete_at": delete_at}


async def cancel(rating_key: int) -> bool:
    with db.tx() as c:
        n = c.execute("DELETE FROM leaving WHERE rating_key=? AND status IN ('scheduled','failed')",
                      (rating_key,)).rowcount
    if n:
        await sync_collections()
    return bool(n)


def extend(rating_key: int, days: int) -> int | None:
    now = int(time.time())
    with db.tx() as c:
        c.execute("UPDATE leaving SET delete_at=MAX(delete_at, ?) + ?, note=NULL, attempts=0 "
                  "WHERE rating_key=? AND status='scheduled'", (now, int(days) * DAY, rating_key))
        row = c.execute("SELECT delete_at FROM leaving WHERE rating_key=?", (rating_key,)).fetchone()
    return row["delete_at"] if row else None


async def delete_now(rating_key: int) -> dict:
    rows = db.query("SELECT * FROM leaving WHERE rating_key=? AND status IN ('scheduled','failed')", (rating_key,))
    if not rows:
        raise arr.DeleteError("Not scheduled to leave")
    r = rows[0]
    with db.tx() as c:  # failed rows go back to scheduled so the delete can mark them done
        c.execute("UPDATE leaving SET status='scheduled' WHERE rating_key=?", (rating_key,))
    try:
        res = await arr.perform_delete(rating_key, r["mode"] or "remove", bool(r["exclusion"]), bool(r["unmonitor"]),
                                       source="leaving")
    except arr.DeleteError:
        if r["status"] == "failed":
            with db.tx() as c:
                c.execute("UPDATE leaving SET status='failed' WHERE rating_key=?", (rating_key,))
        raise
    await arr.plex_refresh([res.get("section_id")])
    await sync_collections()
    return res


def _resolve(rating_key: int, status: str, note: str) -> None:
    with db.tx() as c:
        c.execute("UPDATE leaving SET status=?, status_at=?, note=? WHERE rating_key=? AND status='scheduled'",
                  (status, int(time.time()), note, rating_key))


def _watched_since(row: dict, settings: dict):
    """(who, when) for the latest counted play since the title was scheduled, else None."""
    st = stats.stats(row["kind"], settings, since=row["scheduled_at"], keys=[row["rating_key"]])
    s = st.get(row["rating_key"])
    return (s["last_user"] or "someone", s["last_played"]) if s and s["plays"] else None


async def tick() -> None:
    """Spare anything watched since it was scheduled, then delete whatever is due."""
    if _lock.locked():
        return
    async with _lock:
        rows = scheduled()
        if not rows:
            if time.time() - _last_collection_sync > 6 * 3600:
                await sync_collections()
            return
        now = int(time.time())
        due = [r for r in rows if r["delete_at"] <= now
               and (not r["last_attempt"] or now - r["last_attempt"] >= 3600)]
        if due:
            await sync.ensure_fresh(15 * 60)   # catch last-minute watches before deleting anything

        settings = db.get_settings()
        changed = False
        spared, deleted, failed, sections = [], [], [], set()
        for r in rows:
            rk = r["rating_key"]
            if arr.kept_title(rk):
                _resolve(rk, "cancelled", "Added to the keep list")
                changed = True
            elif not arr._row(rk):
                _resolve(rk, "gone", "No longer in the library")
                changed = True
            else:
                hit = _watched_since(r, settings)
                if hit:
                    _resolve(rk, "spared", f"Watched by {hit[0]}")
                    spared.append((r, hit[0]))
                    changed = True

        for r in due:
            rk = r["rating_key"]
            current = db.query("SELECT status, attempts FROM leaving WHERE rating_key=?", (rk,))
            if not current or current[0]["status"] != "scheduled":
                continue
            with db.tx() as c:
                c.execute("UPDATE leaving SET attempts=attempts+1, last_attempt=? WHERE rating_key=?", (now, rk))
            try:
                res = await arr.perform_delete(rk, r["mode"] or "remove", bool(r["exclusion"]), bool(r["unmonitor"]),
                                               source="leaving")
                deleted.append((r, res))
                sections.add(res.get("section_id"))
                changed = True
            except arr.DeleteError as e:
                attempts = (current[0]["attempts"] or 0) + 1
                if attempts >= MAX_ATTEMPTS:
                    with db.tx() as c:
                        c.execute("UPDATE leaving SET status='failed', status_at=?, note=? WHERE rating_key=?",
                                  (now, f"Couldn't delete: {e}", rk))
                    changed = True
                else:
                    with db.tx() as c:
                        c.execute("UPDATE leaving SET note=? WHERE rating_key=?",
                                  (f"Couldn't delete: {e}. Trying again in an hour.", rk))
                failed.append((r, str(e)))

        if sections:
            await arr.plex_refresh(sections)
        if changed or time.time() - _last_collection_sync > 6 * 3600:
            await sync_collections()
        await notify.leaving_report(deleted, spared, failed)
        await _heads_up()


async def _heads_up() -> None:
    """One notification per title, a day before it leaves."""
    now = int(time.time())
    warned = {int(k): v for k, v in (db.kv_get("leaving_warned") or {}).items()}
    soon = [r for r in scheduled() if r["delete_at"] - now <= DAY and warned.get(r["rating_key"]) != r["delete_at"]]
    if not soon:
        return
    for r in soon:
        warned[r["rating_key"]] = r["delete_at"]
    live = {r["rating_key"] for r in scheduled()}
    db.kv_set("leaving_warned", {str(k): v for k, v in warned.items() if k in live})
    await notify.heads_up(soon)


# ---------------------------------------------------------------- Plex collection

def _uri(machine: str, keys: list[int]) -> str:
    return f"server://{machine}/com.plexapp.plugins.library/library/metadata/{','.join(str(k) for k in keys)}"


async def _children(plex, key) -> set[int] | None:
    body = await plex.req("GET", f"/library/collections/{key}/children", ok404=True)
    if body is None:
        return None
    return {int(m["ratingKey"]) for m in (body.get("MediaContainer") or {}).get("Metadata") or [] if m.get("ratingKey")}


async def _adopt(plex, section_id: int, name: str):
    """A collection we made earlier (recognised by its summary) whose key we lost track of."""
    body = await plex.req("GET", f"/library/sections/{section_id}/collections", ok404=True) or {}
    for m in (body.get("MediaContainer") or {}).get("Metadata") or []:
        if m.get("title") == name and "Shelflife" in (m.get("summary") or ""):
            return m.get("ratingKey")
    return None


async def _create(plex, section_id: int, lib_type: str, name: str, first: int, machine: str, promote: bool):
    body = await plex.req("POST", "/library/collections", params={
        "type": 1 if lib_type == "movie" else 2, "title": name, "smart": 0, "sectionId": section_id,
        "uri": _uri(machine, [first])}) or {}
    meta = ((body.get("MediaContainer") or {}).get("Metadata") or [{}])[0]
    key = meta.get("ratingKey")
    if not key:
        return None
    try:
        await plex.req("PUT", f"/library/sections/{section_id}/all", params={
            "type": 18, "id": key, "summary.value": SUMMARY, "summary.locked": 1})
    except ApiError as e:
        log.info("Couldn't set the collection summary: %s", e)
    if promote:
        try:
            await plex.req("POST", f"/hubs/sections/{section_id}/manage", params={
                "metadataItemId": key, "promotedToRecommended": 1, "promotedToOwnHome": 1,
                "promotedToSharedHome": 1})
        except ApiError as e:
            log.info("Couldn't put the collection on Plex Home: %s", e)
    return key


async def sync_collections() -> None:
    """Make each library's "Leaving Soon" collection hold exactly the scheduled movies and shows."""
    global _last_collection_sync
    _last_collection_sync = time.time()
    plex = plex_client()
    if not plex:
        return
    settings = db.get_settings()
    machine = (db.kv_get("server") or {}).get("identifier")
    managed = db.kv_get("leaving_collections") or {}
    want: dict[int, list[int]] = {}
    if settings["leaving_collection"] and machine:
        for r in scheduled():
            if r["kind"] in ("movie", "show") and r["section_id"]:
                want.setdefault(int(r["section_id"]), []).append(r["rating_key"])
    lib_types = {lib["section_id"]: lib["type"] for lib in db.query("SELECT section_id, type FROM libraries")}
    name = settings["leaving_collection_name"]
    try:
        for sid in sorted({int(s) for s in managed} | set(want)):
            keys = want.get(sid, [])
            entry = managed.get(str(sid)) or {}
            try:
                key, current = entry.get("key"), None
                if key:
                    current = await _children(plex, key)
                    if current is None:
                        key = None
                if not key:
                    key = await _adopt(plex, sid, entry.get("name") or name)
                    current = await _children(plex, key) if key else None
                if not key:
                    if not keys:
                        managed.pop(str(sid), None)
                        continue
                    key = await _create(plex, sid, lib_types.get(sid, "movie"), name, keys[0], machine,
                                        settings["leaving_promote"])
                    if not key:
                        continue
                    current = await _children(plex, key) or set()
                elif entry.get("name") and entry["name"] != name:
                    try:
                        await plex.req("PUT", f"/library/sections/{sid}/all", params={
                            "type": 18, "id": key, "title.value": name, "title.locked": 1})
                    except ApiError as e:
                        log.info("Couldn't rename the collection: %s", e)
                current = current or set()
                add = [k for k in keys if k not in current]
                if add:
                    await plex.req("PUT", f"/library/collections/{key}/items", params={"uri": _uri(machine, add)})
                for k in sorted(current - set(keys)):
                    await plex.req("DELETE", f"/library/collections/{key}/items/{k}", ok404=True)
                managed[str(sid)] = {"key": key, "name": name}
            except ApiError as e:
                log.warning("Couldn't update the Plex collection for library %s: %s", sid, e)
    finally:
        await plex.close()
        db.kv_set("leaving_collections", managed)


def collection_status() -> dict:
    settings = db.get_settings()
    managed = db.kv_get("leaving_collections") or {}
    names = {lib["section_id"]: lib["name"] for lib in db.query("SELECT section_id, name FROM libraries")}
    return {"available": bool(config.PLEX_TOKEN),
            "enabled": settings["leaving_collection"], "name": settings["leaving_collection_name"],
            "libraries": [names.get(int(s), f"Library {s}") for s in managed]}
