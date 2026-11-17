"""Notifications: a weekly digest and Leaving soon updates, sent to a Discord or Slack webhook,
an ntfy topic, or any URL that accepts JSON."""
import logging
import time
from datetime import datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx

from . import config, db, stats
from .clients import ApiError

log = logging.getLogger("shelflife.notify")
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def enabled() -> bool:
    return bool(config.NOTIFY_URL)


def schedule_text() -> str | None:
    if not enabled() or config.DIGEST_DAY not in DAYS:
        return None
    day = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][DAYS.index(config.DIGEST_DAY)]
    return f"{day}s at {config.DIGEST_HOUR:02d}:00 ({config.TZ})"


def _tz():
    try:
        return ZoneInfo(config.TZ)
    except Exception:  # noqa: BLE001 - unknown zone names fall back to UTC
        return ZoneInfo("UTC")


def human(n: int) -> str:
    n = n or 0
    if n >= 1024 ** 4:
        return f"{n / 1024 ** 4:.2f} TB"
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.1f} GB"
    return f"{max(1, round(n / 1024 ** 2))} MB"


def ago(ts: int | None) -> str:
    if not ts:
        return "never watched"
    days = (time.time() - ts) / 86400
    if days < 60:
        return f"last watched {int(days)} days ago"
    if days < 365:
        return f"last watched {int(days / 30.4)} months ago"
    return f"last watched {days / 365.25:.1f} years ago"


def _kind(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if "discord" in host:
        return "discord"
    if host.endswith("hooks.slack.com"):
        return "slack"
    if "ntfy" in host:
        return "ntfy"
    return "json"


async def send(title: str, lines: list[str], link: str | None = None) -> None:
    if not enabled():
        raise ApiError("Set NOTIFY_URL to send notifications")
    url, text = config.NOTIFY_URL, "\n".join(lines).strip()
    kind = _kind(url)
    try:
        async with httpx.AsyncClient(timeout=20, verify=config.VERIFY_SSL) as c:
            if kind == "discord":
                embed = {"title": title, "description": text[:4000], "color": 0x7DB79A}
                if link:
                    embed["url"] = link
                r = await c.post(url, json={"username": "Shelflife", "embeds": [embed]})
            elif kind == "slack":
                r = await c.post(url, json={"text": f"*{title}*\n{text}" + (f"\n<{link}|Open Shelflife>" if link else "")})
            elif kind == "ntfy":
                headers = {"Title": title.encode("latin-1", "replace").decode("latin-1")}
                if link:
                    headers["Click"] = link
                r = await c.post(url, content=text.encode(), headers=headers)
            else:
                r = await c.post(url, json={"title": title, "message": text, "url": link})
    except httpx.HTTPError as e:
        raise ApiError(f"Couldn't reach the notification URL ({e.__class__.__name__})") from e
    if r.status_code >= 400:
        raise ApiError(f"The notification URL returned HTTP {r.status_code}")


def digest() -> tuple[str, list[str]] | None:
    """The default cull view: titles nobody has watched in a year, added more than 30 days ago, not kept."""
    rows = db.query("SELECT rating_key, kind, title, year, size, added_at FROM items")
    if not rows:
        return None
    settings = db.get_settings()
    now = time.time()
    kept = {r["rating_key"] for r in db.query("SELECT rating_key FROM exclusions")}
    st = {"movie": stats.stats("movie", settings), "show": stats.stats("show", settings)}
    cands = []
    for r in rows:
        if r["rating_key"] in kept or (r["added_at"] and r["added_at"] > now - 30 * 86400):
            continue
        s = st[r["kind"]].get(r["rating_key"])
        last = s["last_played"] if s else None
        if last and last > now - 365 * 86400:
            continue
        cands.append({**r, "last_played": last})
    cands.sort(key=lambda r: -(r["size"] or 0))
    total = sum(r["size"] or 0 for r in cands)
    lines = [f"{human(total)} in {len(cands)} titles nobody has watched in the last year."]
    if cands:
        lines += ["", "Biggest:"]
        for r in cands[:10]:
            year = f" ({r['year']})" if r["year"] else ""
            lines.append(f"• {r['title']}{year}: {human(r['size'])}, {ago(r['last_played'])}")

    soon = db.query("SELECT title, size, delete_at FROM leaving WHERE status='scheduled' AND delete_at <= ? "
                    "ORDER BY delete_at", (now + 7 * 86400,))
    if soon:
        lines += ["", f"Leaving this week ({human(sum(r['size'] or 0 for r in soon))}):"]
        tz = _tz()
        lines += [f"• {r['title']}, {datetime.fromtimestamp(r['delete_at'], tz).strftime('%a %b %-d')}" for r in soon[:8]]

    freed = db.query("SELECT COUNT(*) AS n, COALESCE(SUM(size),0) AS s FROM deletions WHERE at >= ?",
                     (now - 7 * 86400,))[0]
    if freed["n"]:
        lines += ["", f"Freed this week: {human(freed['s'])} from {freed['n']} deletions."]

    from . import issues  # late import: issues pulls in sync, which isn't needed for anything else here
    n = issues.summary()["count"]
    if n:
        lines += ["", f"{n} {'place' if n == 1 else 'places'} where Plex and Sonarr/Radarr disagree. "
                      "See the Issues tab."]
    return "Shelflife weekly digest", lines


async def send_digest() -> bool:
    d = digest()
    if not d:
        return False
    await send(d[0], d[1], config.PUBLIC_URL or None)
    return True


async def tick() -> None:
    if not enabled() or config.DIGEST_DAY not in DAYS:
        return
    local = datetime.now(_tz())
    if local.weekday() != DAYS.index(config.DIGEST_DAY) or local.hour < config.DIGEST_HOUR:
        return
    today = local.date().isoformat()
    if db.kv_get("digest_last") == today or not db.query("SELECT 1 FROM items LIMIT 1"):
        return
    db.kv_set("digest_last", today)  # mark first, so a broken webhook doesn't retry every minute
    try:
        await send_digest()
    except ApiError as e:
        log.warning("Weekly digest failed: %s", e)


async def leaving_report(deleted: list, spared: list, failed: list) -> None:
    if not enabled() or not (deleted or spared or failed):
        return
    lines = []
    if deleted:
        lines.append(f"Removed {len(deleted)} {'title' if len(deleted) == 1 else 'titles'}, "
                     f"{human(sum(res.get('freed') or 0 for _, res in deleted))} freed:")
        lines += [f"• {row['title']}" for row, _ in deleted[:15]]
    if spared:
        lines += ([""] if lines else []) + ["Spared because someone watched:"]
        lines += [f"• {row['title']} (watched by {who})" for row, who in spared[:15]]
    if failed:
        lines += ([""] if lines else []) + ["Couldn't delete, trying again in an hour:"]
        lines += [f"• {row['title']}: {why}" for row, why in failed[:15]]
    try:
        await send("Leaving soon update", lines, config.PUBLIC_URL or None)
    except ApiError as e:
        log.warning("Leaving soon notification failed: %s", e)


async def heads_up(rows: list[dict]) -> None:
    if not enabled() or not rows:
        return
    lines = ["These leave tomorrow unless someone watches them:"]
    lines += [f"• {r['title']} ({human(r['size'])})" for r in rows[:15]]
    try:
        await send("Leaving tomorrow", lines, config.PUBLIC_URL or None)
    except ApiError as e:
        log.warning("Leaving soon heads-up failed: %s", e)
