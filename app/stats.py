"""Watch statistics and quality labels, computed from the cached library and Tautulli history."""
import json

from . import db

# Which history column a level's plays roll up to, and which history rows count.
LEVELS = {
    "movie": ("rating_key", "movie"),
    "show": ("grandparent_rating_key", "episode"),
    "season": ("parent_rating_key", "episode"),
}
EMPTY = {"last_played": None, "last_user": None, "plays": 0, "users": 0}


def play_filters(settings: dict, ignore_users: bool = True) -> tuple[list[str], list]:
    where = ["COALESCE(duration,0) >= ?"]
    args: list = [settings["min_play_minutes"] * 60]
    ignored = [int(u) for u in settings.get("ignored_users") or []]
    if ignore_users and ignored:
        where.append(f"COALESCE(user_id,-1) NOT IN ({','.join('?' * len(ignored))})")
        args += ignored
    return where, args


def stats(level: str, settings: dict, since: int | None = None, keys: list[int] | None = None) -> dict[int, dict]:
    """Counted plays per item at a level: last played, who, how many plays, how many people."""
    col, media_type = LEVELS[level]
    where, args = play_filters(settings)
    where = ["media_type = ?"] + where
    args = [media_type] + args
    if since:  # plays that finished after `since` count, so a watch in progress at that moment counts too
        where.append("COALESCE(stopped, started) >= ?")
        args.append(since)
    if keys:
        where.append(f"{col} IN ({','.join('?' * len(keys))})")
        args += list(keys)
    # SQLite returns the bare friendly_name from the row holding MAX(started).
    rows = db.query(
        f"SELECT {col} AS k, MAX(started) AS last_played, friendly_name AS last_user, COUNT(*) AS plays, "
        f"COUNT(DISTINCT user_id) AS users FROM history WHERE {' AND '.join(where)} GROUP BY {col}",
        args,
    )
    return {r["k"]: r for r in rows if r["k"] is not None}


def with_stats(row: dict, st: dict[int, dict]) -> dict:
    s = st.get(row["rating_key"], EMPTY)
    row.update(last_played=s["last_played"], last_user=s["last_user"], plays=s["plays"], users=s["users"])
    return row


# ---------------------------------------------------------------- quality

RES_LABEL = {"4k": "4K", "1080": "1080p", "720": "720p", "sd": "SD"}
RES_ORDER = ["4k", "1080", "720", "sd"]


def norm_res(v) -> str | None:
    v = str(v or "").lower().strip().rstrip("ip")
    if not v:
        return None
    return {"2160": "4k", "uhd": "4k", "576": "sd", "480": "sd", "360": "sd", "240": "sd"}.get(v, v)


def res_label(v) -> str | None:
    v = norm_res(v)
    if not v:
        return None
    return RES_LABEL.get(v) or (f"{v}p" if v.isdigit() else v.upper())


def load_json(v, default=None):
    if not v:
        return default if default is not None else {}
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return default if default is not None else {}


def quality(q_json, resolution=None) -> dict:
    """Readable quality summary: label like "1080p" or "1080p + 4K", plus the raw counts."""
    q = load_json(q_json)
    res = {k: v for k, v in (q.get("res") or {}).items() if k}
    if not res and resolution and norm_res(resolution):
        res = {norm_res(resolution): 1}
    label = None
    if res:
        ordered = sorted(res, key=lambda k: (-res[k], RES_ORDER.index(k) if k in RES_ORDER else 9))
        label = res_label(ordered[0])
        rest = [res_label(k) for k in ordered[1:]]
        if len(rest) == 1:
            label += f" + {rest[0]}"
        elif rest:
            label += f" + {len(rest)} more"
    codec = q.get("codec") or {}
    top_codec = max(codec, key=codec.get) if codec else None
    return {"label": label, "res": res, "codec": top_codec, "versions": int(q.get("versions") or 1),
            "is4k": "4k" in res}
