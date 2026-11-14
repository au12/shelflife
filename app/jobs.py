"""Background jobs with progress the UI can poll (bulk deletes)."""
import asyncio
import logging
import time
import uuid

from . import arr, leaving

log = logging.getLogger("shelflife.jobs")

JOBS: dict[str, dict] = {}
_tasks: set[asyncio.Task] = set()


def _trim():
    if len(JOBS) > 20:
        for jid in sorted(JOBS, key=lambda j: JOBS[j]["started_at"])[: len(JOBS) - 20]:
            if JOBS[jid]["finished_at"]:
                JOBS.pop(jid, None)


def start_delete(items: list[dict], exclusion: bool, unmonitor: bool, source: str = "bulk") -> dict:
    """items: [{"rating_key": int, "mode": "remove" | "files"}]"""
    job = {"id": uuid.uuid4().hex[:12], "type": "delete", "total": len(items), "done": 0, "current": None,
           "results": [], "freed": 0, "started_at": time.time(), "finished_at": None}
    JOBS[job["id"]] = job
    _trim()
    task = asyncio.create_task(_run_delete(job, items, exclusion, unmonitor, source))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return job


async def _run_delete(job: dict, items: list[dict], exclusion: bool, unmonitor: bool, source: str):
    sections = set()
    try:
        for it in items:
            rk = int(it["rating_key"])
            row = arr._row(rk) or {}
            title = row.get("title") or f"Item {rk}"
            job["current"] = title
            try:
                res = await arr.perform_delete(rk, it.get("mode") or "remove", exclusion, unmonitor, source)
                job["results"].append({"rating_key": rk, "ok": True, "title": title, "message": res["message"],
                                       "freed": res["freed"]})
                job["freed"] += res["freed"] or 0
                sections.add(res.get("section_id"))
            except arr.DeleteError as e:
                job["results"].append({"rating_key": rk, "ok": False, "title": title, "message": str(e)})
            except Exception as e:  # noqa: BLE001 - one bad item shouldn't stop the batch
                log.exception("Delete failed for %s", rk)
                job["results"].append({"rating_key": rk, "ok": False, "title": title,
                                       "message": f"{e.__class__.__name__}: {e}"})
            job["done"] += 1
        job["current"] = "Asking Plex to rescan" if sections else None
        await arr.plex_refresh(sections)
        try:  # deleted titles also leave the Plex "Leaving Soon" collection
            await leaving.sync_collections()
        except Exception:  # noqa: BLE001 - the deletes already happened; the collection catches up later
            log.exception("Couldn't update the Leaving Soon collection")
    finally:
        job["current"] = None
        job["finished_at"] = time.time()
