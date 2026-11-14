"""The background loop: scheduled syncs, Leaving soon, and the weekly digest."""
import asyncio
import logging

from . import config, leaving, notify, sync

log = logging.getLogger("shelflife.tasks")


async def loop() -> None:
    sync.load_state()
    if not sync.configured():
        log.warning("TAUTULLI_URL / TAUTULLI_API_KEY not set; sync disabled")
    while True:
        try:
            if sync.configured() and sync.due():
                await sync.run_sync()
            await leaving.tick()
            await notify.tick()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - keep the loop alive; problems show up in the log and the UI
            log.exception("Background task failed")
        await asyncio.sleep(config.TICK_SECONDS)
