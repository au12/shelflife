"""Where Plex and Sonarr/Radarr disagree.

Every Plex movie and show is matched the same way Shelflife matches before a delete, against the whole Radarr and
Sonarr catalogs, then compared:

- missing_arr   in Plex, not in Radarr/Sonarr: Shelflife can't delete it, nothing upgrades it
- missing_plex  in Radarr/Sonarr with files, not in any Plex library Shelflife reads
- shared        two or more Plex titles matched to one Radarr movie / Sonarr series
- no_file       matched, but Radarr/Sonarr has no file on record
- untracked     Plex has more than Radarr/Sonarr tracks (an extra version, episodes without a file there)
- stale         Radarr/Sonarr has more or newer files than Plex shows (Plex hasn't scanned them)

The result is kept in kv["issues"], rebuilt after each sync and on request.
"""
import asyncio
import logging
import time

from . import arr, config, db, stats, sync
from .clients import ApiError

log = logging.getLogger("shelflife.issues")

# A movie's sizes only count as different past the larger of 1% and 200 MB.
MIN_GAP = 200 * 1024 ** 2
REL_GAP = 0.01
LOOKUP_CONCURRENCY = 4

state = {"running": False, "phase": None, "detail": None}
_task: asyncio.Task | None = None


def _differs(a: int, b: int) -> bool:
    return abs((a or 0) - (b or 0)) > max(MIN_GAP, REL_GAP * max(a or 0, b or 0))


def _signature() -> str:
    """Which services were compared; a change (say, Sonarr added) makes the stored result stale."""
    return f"radarr={config.RADARR_URL}|sonarr={config.SONARR_URL}"


# ---------------------------------------------------------------- results

def ignored() -> set[str]:
    return {r["key"] for r in db.query("SELECT key FROM issue_ignores")}


def set_ignored(key: str, on: bool) -> None:
    with db.tx() as c:
        if on:
            c.execute("INSERT OR REPLACE INTO issue_ignores(key, created_at) VALUES(?,?)", (key, int(time.time())))
        else:
            c.execute("DELETE FROM issue_ignores WHERE key=?", (key,))


def _plex_keys(item: dict) -> list[int]:
    if item.get("copies"):
        return [c["rating_key"] for c in item["copies"]]
    return [item["plex"]["rating_key"]] if item.get("plex") else []


def result() -> dict:
    data = db.kv_get("issues") or {}
    ign = ignored()
    hidden = arr.hidden_keys()
    items = []
    for it in data.get("items") or []:
        if any(k in hidden for k in _plex_keys(it)):
            continue  # deleted through Shelflife since the check
        items.append({**it, "ignored": it["key"] in ign})
    return {"checked_at": data.get("checked_at"), "items": items, "errors": data.get("errors") or {},
            "compared": data.get("compared") or {}, "enabled": arr.enabled(), "running": state["running"],
            "phase": state["phase"], "detail": state["detail"],
            "ignored_libraries": [s for s in config.IGNORE_LIBRARIES]}


def summary() -> dict:
    r = result()
    return {"count": sum(1 for i in r["items"] if not i["ignored"]), "checked_at": r["checked_at"],
            "running": r["running"]}


# ---------------------------------------------------------------- checking

def tick() -> None:
    """Called from the background loop: re-check once each sync has finished, in the background."""
    global _task
    if state["running"] or sync.state["running"] or not any(arr.enabled().values()):
        return
    last = (db.kv_get("last_sync") or {}).get("at") or 0
    data = db.kv_get("issues") or {}
    if not last or ((data.get("checked_at") or 0) >= last and data.get("signature") == _signature()):
        return
    _task = asyncio.create_task(check())


def start() -> None:
    """Check now (the Check again button); a check that's already running carries on."""
    global _task
    if not state["running"]:
        _task = asyncio.create_task(check())


async def check() -> None:
    if state["running"]:
        return
    state.update(running=True, phase="Reading your libraries", detail=None)
    try:
        hidden = arr.hidden_keys()
        libs = {str(r["section_id"]): r["name"] for r in db.query("SELECT section_id, name FROM libraries")}
        rows = [r for r in db.query(
            "SELECT rating_key, section_id, kind, title, year, size, episode_count, quality, guids, thumb "
            "FROM items WHERE kind IN ('movie','show')") if r["rating_key"] not in hidden]
        seasons: dict[int, dict[int, int]] = {}
        for s in db.query("SELECT show_key, season_index, episode_count FROM seasons"):
            seasons.setdefault(s["show_key"], {})[s["season_index"]] = s["episode_count"]
        out = {"checked_at": None, "items": [], "errors": {}, "compared": {}, "signature": _signature()}
        for kind, make in (("movie", arr.radarr), ("show", arr.sonarr)):
            client = make()
            if not client:
                continue
            service = client.name.lower()
            plex_rows = [r for r in rows if r["kind"] == kind]
            try:
                state.update(phase=f"Reading {client.name}", detail=None)
                catalog = await client.req("GET", "/movie" if kind == "movie" else "/series") or []
                matches, ids = await _match(client.name, plex_rows, catalog)
            except ApiError as e:
                out["errors"][service] = str(e)
                continue
            finally:
                await client.close()
            out["items"] += _compare(kind, service, client.public_url, plex_rows, catalog, matches, ids, seasons,
                                     libs)
            out["compared"][service] = {"plex": len(plex_rows), "arr": len(catalog)}
        out["checked_at"] = int(time.time())
        db.kv_set("issues", out)
        log.info("Issues check: %d found", len(out["items"]))
    except Exception:  # noqa: BLE001 - a failed check shouldn't take the background loop down
        log.exception("Issues check failed")
    finally:
        state.update(running=False, phase=None, detail=None)


async def _match(service: str, plex_rows: list[dict], catalog: list[dict]):
    """Plex rating key -> catalog entry (or None), the way a delete would resolve it. Titles that only match
    once their ids are known get those looked up (and cached) first."""
    index = arr.ArrIndex(catalog)
    matches: dict[int, dict | None] = {}
    ids: dict[int, dict] = {}
    lookup = []
    state.update(phase=f"Matching titles in {service}")
    for r in plex_rows:
        known = arr.stored_ids(r["rating_key"], r["guids"])
        ids[r["rating_key"]] = known or {}
        matches[r["rating_key"]] = index.pick(known or {}, r["title"], r["year"])
        if matches[r["rating_key"]] is None and known is None:
            lookup.append(r)
    if lookup:
        sem = asyncio.Semaphore(LOOKUP_CONCURRENCY)
        done = 0

        async def one(r):
            nonlocal done
            async with sem:
                found = await arr.plex_guids(r["rating_key"])
            done += 1
            state["detail"] = f"{done} of {len(lookup)}"
            ids[r["rating_key"]] = found
            matches[r["rating_key"]] = index.pick(found, r["title"], r["year"])

        state.update(phase="Looking up ids for unmatched titles", detail=f"0 of {len(lookup)}")
        await asyncio.gather(*(one(r) for r in lookup))
    arr.remember_many(service, {rk: (obj["id"] if obj else None) for rk, obj in matches.items()})
    return matches, ids


def _plex_side(r: dict, libs: dict) -> dict:
    q = stats.quality(r["quality"])
    return {"rating_key": r["rating_key"], "title": r["title"], "year": r["year"], "thumb": r["thumb"],
            "library": libs.get(str(r["section_id"])), "size": r["size"] or 0, "episodes": r["episode_count"] or 0,
            "versions": q["versions"], "quality": q["label"]}


def _arr_side(kind: str, public_url: str, obj: dict) -> dict:
    if kind == "movie":
        files = 1 if obj.get("hasFile") else 0
        season_files = None
    else:
        st = obj.get("statistics") or {}
        season_files = {s.get("seasonNumber"): (s.get("statistics") or {}).get("episodeFileCount", 0)
                        for s in obj.get("seasons") or []}
        files = st["episodeFileCount"] if "episodeFileCount" in st else sum(season_files.values())
    return {"id": obj.get("id"), "title": obj.get("title"), "year": obj.get("year"), "size": arr.arr_size(kind, obj),
            "files": files, "monitored": obj.get("monitored"), "path": obj.get("path"),
            "url": arr.arr_url(public_url, kind, obj), "season_files": season_files}


def _season_diffs(plex_seasons: dict[int, int], arr_seasons: dict) -> list[dict]:
    out = []
    for n in sorted(set(plex_seasons) | {k for k, v in arr_seasons.items() if v}, key=lambda x: (x is None, x)):
        p, a = plex_seasons.get(n, 0), arr_seasons.get(n, 0) or 0
        if p != a:
            out.append({"season": n, "plex": p, "arr": a})
    return out


def _compare(kind, service, public_url, plex_rows, catalog, matches, ids, seasons, libs) -> list[dict]:
    items: list[dict] = []
    by_arr: dict = {}
    for r in plex_rows:
        obj = matches.get(r["rating_key"])
        if obj:
            by_arr.setdefault(obj.get("id"), []).append(r)
    catalog_by_id = {c.get("id"): c for c in catalog}

    for r in plex_rows:
        rk = r["rating_key"]
        obj = matches.get(rk)
        plex = _plex_side(r, libs)
        base = {"service": service, "kind": kind, "plex": plex}
        if not obj:
            items.append({**base, "key": f"missing_arr:{rk}", "type": "missing_arr",
                          "add_url": arr.add_url(kind, ids.get(rk) or {}, r["title"], r["year"])})
            continue
        if len(by_arr[obj.get("id")]) > 1:
            continue  # reported once, as shared
        a = _arr_side(kind, public_url, obj)
        base["arr"] = a
        if not a["files"]:
            items.append({**base, "key": f"no_file:{rk}", "type": "no_file"})
        elif kind == "movie":
            if _differs(plex["size"], a["size"]):
                kind_ = "untracked" if plex["size"] > a["size"] else "stale"
                items.append({**base, "key": f"{kind_}:{rk}", "type": kind_})
        elif plex["episodes"] != a["files"]:
            kind_ = "untracked" if plex["episodes"] > a["files"] else "stale"
            items.append({**base, "key": f"{kind_}:{rk}", "type": kind_,
                          "seasons": _season_diffs(seasons.get(rk, {}), a.pop("season_files") or {})})
        a.pop("season_files", None)

    for arr_id, rows in by_arr.items():
        if len(rows) < 2:
            continue
        a = _arr_side(kind, public_url, catalog_by_id[arr_id])
        a.pop("season_files", None)
        copies = sorted((_plex_side(r, libs) for r in rows), key=lambda c: -c["size"])
        for c in copies:  # which copy Sonarr/Radarr's files look like, when that's clear
            c["matches_arr"] = arr.close_in_size(a["size"], c["size"])
        items.append({"service": service, "kind": kind, "key": f"shared:{service}:{arr_id}", "type": "shared",
                      "arr": a, "copies": copies})

    for c in catalog:
        if c.get("id") in by_arr:
            continue
        a = _arr_side(kind, public_url, c)
        a.pop("season_files", None)
        if a["files"]:
            items.append({"service": service, "kind": kind, "key": f"missing_plex:{service}:{c.get('id')}",
                          "type": "missing_plex", "arr": a})
    return items
