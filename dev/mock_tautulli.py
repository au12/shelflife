"""A fake Tautulli (and Plex) server with invented data, for development and README screenshots.

    pip install fastapi uvicorn pillow
    python dev/mock_tautulli.py            # listens on :8181

Point Shelflife at it with TAUTULLI_URL=http://localhost:8181 TAUTULLI_API_KEY=anything.
Add PLEX_URL=http://localhost:8181 PLEX_TOKEN=anything to exercise the Plex-direct size path.
"""
import hashlib
import io
import random
import time

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from PIL import Image, ImageDraw, ImageFont

rng = random.Random(7)
NOW = int(time.time())
DAY = 86400
GB = 1024 ** 3

USERS = [(101, "maple"), (102, "ridgeway"), (103, "nora.k"), (104, "den_tv"), (105, "grandpa_lou")]

MOVIES = [
    "The Long Meridian", "Saltwater Hymnal", "Glass Orchard", "Nine Lanterns Down", "The Cartographer's Son",
    "Low Tide Republic", "Paper Comets", "A Quiet Engine", "Harbor of Small Lights", "The Ninth Signal",
    "Velvet Freeway", "Copper Season", "Last Train to Wexley", "The Understudy", "Northbound Sparrow",
    "Ironwood", "Static Bloom", "The Fourth Witness", "Bright Rooms", "Deep Field", "The Lantern Keeper",
    "Undertow", "Kingdom of Moths", "A Brief Atlas", "Signal Fire", "Grey Hollow", "The Weight of Rain",
    "Monument Valley Run", "Sundown Protocol", "Ash & Ember", "The Patient Hours", "Night Market",
    "Halfway Station", "The Orchard Thief", "Blue Hour", "Under the Overpass", "Cold Open", "Red Shift",
    "The Long Goodbye Club", "Little Engines", "Moonlight Tariff", "Parallel Lines", "Sable Island",
    "Pocket Universe", "The Ballad of Joe Tin", "Far Field", "Common Ground", "Lighthouse Rules",
]
SHOWS = [
    ("Engine Company 41", 10), ("The Quiet Precinct", 6), ("Harbor Lights", 4), ("Backroads Kitchen", 8),
    ("Station Zero", 3), ("The Archivists", 5), ("Lowland", 2), ("Brick & Mortar", 7), ("Dispatch", 9),
    ("Small Town Saints", 4), ("The Long Haul", 3), ("Deep Space Relay", 5), ("Garden State of Mind", 2),
    ("Night Shift Diaries", 6), ("Cold Case Unit", 11), ("Pinecrest", 3), ("Workshop Wars", 4),
    ("The Lodge", 2),
]
EP_A = ["Cold", "Second", "Open", "Long", "Quiet", "Burning", "Lost", "Paper", "Iron", "Last", "Hidden", "Low",
        "Bright", "Broken", "Thin", "Early"]
EP_B = ["Water", "Shift", "Ground", "Signals", "Doors", "Ledger", "Harbor", "Alibi", "Lines", "Call", "Weather",
        "Bridge", "Hours", "Ashes", "Rounds", "Margins"]
PALETTE = ["#2f4858", "#33658a", "#86bbd8", "#758e4f", "#f6ae2d", "#f26419", "#5b3758", "#3d5a80",
           "#98c1d9", "#293241", "#6d597a", "#b56576", "#355070", "#e56b6f", "#4a5759", "#b0c4b1"]

libraries = [
    {"section_id": "1", "section_name": "Movies", "section_type": "movie", "count": 0, "is_active": 1},
    {"section_id": "2", "section_name": "TV Shows", "section_type": "show", "count": 0, "is_active": 1},
    {"section_id": "3", "section_name": "Music", "section_type": "artist", "count": 0, "is_active": 1},
]

items, seasons, episodes, history = {}, {}, {}, []
key = 1000


def nk():
    global key
    key += 1
    return key


for title in MOVIES:
    rk = nk()
    res = rng.choice(["1080", "1080", "1080", "4k", "720"])
    size = {"4k": rng.uniform(40, 85), "1080": rng.uniform(6, 32), "720": rng.uniform(2, 6)}[res] * GB
    items[rk] = {"rating_key": rk, "section_id": 1, "media_type": "movie", "title": title,
                 "year": rng.randint(1978, 2025), "added_at": NOW - rng.randint(10, 2200) * DAY,
                 "file_size": int(size), "video_resolution": res, "thumb": f"/library/metadata/{rk}/thumb/1"}

for title, nseasons in SHOWS:
    rk = nk()
    year = rng.randint(1995, 2022)
    items[rk] = {"rating_key": rk, "section_id": 2, "media_type": "show", "title": title, "year": year,
                 "added_at": NOW - rng.randint(60, 2400) * DAY, "thumb": f"/library/metadata/{rk}/thumb/1"}
    per_ep = rng.uniform(0.6, 4.5) * GB
    for s in range(1, nseasons + 1):
        sk = nk()
        seasons[sk] = {"rating_key": sk, "parent_rating_key": rk, "section_id": 2, "media_type": "season",
                       "media_index": s, "title": f"Season {s}", "added_at": items[rk]["added_at"] + s * 20 * DAY,
                       "thumb": f"/library/metadata/{rk}/thumb/1"}
        for e in range(1, rng.randint(8, 22) + 1):
            ek = nk()
            episodes[ek] = {"rating_key": ek, "parent_rating_key": sk, "grandparent_rating_key": rk,
                            "section_id": 2, "media_type": "episode", "media_index": e, "parent_media_index": s,
                            "title": f"{rng.choice(EP_A)} {rng.choice(EP_B)}", "show": title,
                            "file_size": int(per_ep * rng.uniform(0.8, 1.2)), "added_at": seasons[sk]["added_at"]}

# Watch history: some titles get regular viewing, some go stale, some are never touched.
row = 50000
for rk, it in items.items():
    fate = rng.random()
    if fate < 0.22:
        continue  # never watched
    age_days = (NOW - it["added_at"]) // DAY
    recency = min(age_days - 5, rng.choice([20, 90, 200, 400, 700, 1100]))
    if recency < 10:
        continue
    if it["media_type"] == "movie":
        for _ in range(rng.randint(1, 4)):
            when = NOW - rng.randint(1, recency) * DAY - rng.randint(0, 80000)
            uid, name = rng.choice(USERS)
            dur = rng.randint(70, 140) * 60
            row += 1
            history.append({"row_id": row, "id": row, "section_id": 1, "media_type": "movie", "rating_key": rk,
                            "parent_rating_key": "", "grandparent_rating_key": "", "user_id": uid, "user": name,
                            "friendly_name": name, "date": when, "started": when, "stopped": when + dur + 300,
                            "paused_counter": rng.choice([0, 0, 60, 240]), "play_duration": dur,
                            "full_title": it["title"], "title": it["title"], "parent_media_index": "",
                            "media_index": "", "percent_complete": 100, "watched_status": 1, "group_count": 1})
    else:
        eps = sorted((e for e in episodes.values() if e["grandparent_rating_key"] == rk),
                     key=lambda e: (e["parent_media_index"], e["media_index"]))
        for burst in range(rng.randint(1, 3)):
            uid, name = rng.choice(USERS)
            rec = min(age_days - 5, rng.choice([15, 45, 120, 300, 500, 800, 1100]))
            if rec < 10:
                continue
            first = rng.randrange(len(eps))
            watched = eps[first:first + rng.randint(4, 24)]
            when = NOW - rec * DAY
            for e in watched:
                when += rng.choice([0.03, 0.04, 1, 1, 2, 3]) * DAY
                if when > NOW:
                    break
                u2, n2 = (uid, name) if rng.random() < 0.85 else rng.choice(USERS)
                dur = rng.randint(38, 47) * 60
                row += 1
                st = int(when) + rng.randint(0, 20000)
                history.append({"row_id": row, "id": row, "section_id": 2, "media_type": "episode", "rating_key":
                                e["rating_key"], "parent_rating_key": e["parent_rating_key"],
                                "grandparent_rating_key": rk, "user_id": u2, "user": n2, "friendly_name": n2,
                                "date": st, "started": st, "stopped": st + dur + rng.choice([0, 120, 600]),
                                "paused_counter": rng.choice([0, 0, 0, 120, 540]), "play_duration": dur,
                                "full_title": f"{it['title']} - {e['title']}", "title": e["title"],
                                "parent_media_index": e["parent_media_index"], "media_index": e["media_index"],
                                "percent_complete": rng.choice([100, 100, 96, 42]),
                                "watched_status": 1, "group_count": rng.choice([1, 1, 1, 2])})
history.sort(key=lambda h: h["date"], reverse=True)

app = FastAPI()


def ok(data):
    return {"response": {"result": "success", "message": None, "data": data}}


def row_of(d):
    return {"rating_key": str(d["rating_key"]), "section_id": d["section_id"], "media_type": d["media_type"],
            "title": d["title"], "sort_title": d["title"], "year": str(d.get("year", "")),
            "added_at": str(d["added_at"]), "thumb": d.get("thumb", ""), "file_size": d.get("file_size", ""),
            "media_index": str(d.get("media_index", "")), "parent_media_index": "",
            "video_resolution": d.get("video_resolution", ""), "last_played": None, "play_count": None}


@app.get("/api/v2")
def api(request: Request):
    q = request.query_params
    cmd = q.get("cmd")
    if cmd == "get_server_info":
        return ok({"pms_identifier": "mockserver0123456789", "pms_name": "Mockbox", "pms_url": "http://localhost:8181"})
    if cmd == "get_libraries":
        return ok(libraries)
    if cmd == "get_library_media_info":
        sid, rk = int(q.get("section_id")), q.get("rating_key")
        if rk:
            rk = int(rk)
            if rk in items:
                rows = [s for s in seasons.values() if s["parent_rating_key"] == rk]
            else:
                rows = [e for e in episodes.values() if e["parent_rating_key"] == rk]
        else:
            rows = [i for i in items.values() if i["section_id"] == sid]
        # Mirror real Tautulli: show rows from the API carry no file size
        data = [row_of(r) | ({"file_size": ""} if r["media_type"] in ("show", "season") else {}) for r in rows]
        return ok({"data": data, "recordsTotal": len(data), "recordsFiltered": len(data)})
    if cmd == "get_history":
        start, length = int(q.get("start", 0)), int(q.get("length", 25))
        return ok({"data": history[start:start + length], "recordsTotal": len(history),
                   "recordsFiltered": len(history)})
    if cmd == "get_metadata":
        rk = int(q.get("rating_key"))
        d = items.get(rk) or seasons.get(rk) or {}
        return ok({"rating_key": str(rk), "title": d.get("title", ""), "guid": f"plex://item/{rk}",
                   "guids": guids_for(rk)})
    if cmd == "pms_image_proxy":
        return Response(poster(q.get("img", ""), int(q.get("width", 120)), int(q.get("height", 180))),
                        media_type="image/jpeg")
    return JSONResponse({"response": {"result": "error", "message": f"unknown cmd {cmd}", "data": {}}})


def poster(img: str, w: int, h: int) -> bytes:
    rk = int(img.split("/")[3]) if img.count("/") >= 3 else 0
    title = items.get(rk, {}).get("title", "")
    seed = int(hashlib.md5(img.encode()).hexdigest(), 16)
    c1, c2 = PALETTE[seed % len(PALETTE)], PALETTE[(seed // 7) % len(PALETTE)]
    im = Image.new("RGB", (w, h), c1)
    d = ImageDraw.Draw(im)
    for y in range(h):
        t = y / h
        a = tuple(int(int(c1[i:i + 2], 16) * (1 - t) + int(c2[i:i + 2], 16) * t) for i in (1, 3, 5))
        d.line([(0, y), (w, y)], fill=a)
    d.ellipse([w * 0.15, h * 0.12, w * 0.85, h * 0.12 + w * 0.7], outline=(255, 255, 255), width=max(1, w // 60))
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", max(9, w // 9))
    except OSError:
        font = ImageFont.load_default()
    words, lines, cur = title.split(), [], ""
    for wd in words:
        if len(cur + " " + wd) > 12 and cur:
            lines.append(cur)
            cur = wd
        else:
            cur = (cur + " " + wd).strip()
    lines.append(cur)
    y = h * 0.66
    for ln in lines[:3]:
        d.text((w * 0.1, y), ln, fill=(255, 255, 255), font=font)
        y += w // 8
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return buf.getvalue()


# ---- Plex-direct endpoints -------------------------------------------------

def plex_meta(d):
    m = {"ratingKey": str(d["rating_key"]), "title": d["title"], "titleSort": d["title"], "addedAt": d["added_at"],
         "thumb": d.get("thumb")}
    if "year" in d:
        m["year"] = d["year"]
    if d["media_type"] in ("movie", "episode"):
        m["Media"] = [{"videoResolution": d.get("video_resolution", "1080"),
                       "Part": [{"size": d["file_size"]}]}]
    if d["media_type"] == "season":
        m["index"], m["parentRatingKey"] = d["media_index"], str(d["parent_rating_key"])
    if d["media_type"] == "episode":
        m["parentRatingKey"], m["grandparentRatingKey"] = str(d["parent_rating_key"]), str(d["grandparent_rating_key"])
    return m


@app.get("/library/sections/{sid}/all")
def plex_all(sid: int, request: Request):
    q = request.query_params
    t = int(q.get("type"))
    pool = {1: items, 2: items, 3: seasons, 4: episodes}[t]
    want = {1: "movie", 2: "show", 3: "season", 4: "episode"}[t]
    rows = [plex_meta(r) for r in pool.values() if r["section_id"] == sid and r["media_type"] == want]
    start, size = int(q.get("X-Plex-Container-Start", 0)), int(q.get("X-Plex-Container-Size", 50))
    return {"MediaContainer": {"totalSize": len(rows), "size": len(rows[start:start + size]),
                               "Metadata": rows[start:start + size]}}


# ---- Plex single-item metadata ---------------------------------------------

def guids_for(rk):
    it = items.get(rk)
    if not it:
        return []
    if it["media_type"] == "movie":
        return [f"imdb://tt{rk:07d}", f"tmdb://{rk + 100000}"]
    return [f"tvdb://{rk + 200000}", f"tmdb://{rk + 300000}"]


@app.get("/library/metadata/{rk}")
def plex_metadata(rk: int):
    return {"MediaContainer": {"Metadata": [{"ratingKey": str(rk), "guid": f"plex://item/{rk}",
                                             "Guid": [{"id": g} for g in guids_for(rk)]}]}}


# ---- Radarr / Sonarr (mounted at /radarr and /sonarr) ----------------------

from datetime import datetime, timezone  # noqa: E402
from fastapi import HTTPException  # noqa: E402


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slug(t):
    return "".join(c if c.isalnum() else "-" for c in t.lower()).strip("-")


radarr_movies = {}
for rk, it in items.items():
    if it["media_type"] == "movie" and rk % 11:  # a few movies aren't in Radarr
        mid = len(radarr_movies) + 1
        radarr_movies[mid] = {"id": mid, "title": it["title"], "year": it["year"], "tmdbId": rk + 100000,
                              "imdbId": f"tt{rk:07d}", "titleSlug": f"{slug(it['title'])}-{rk + 100000}",
                              "monitored": True, "hasFile": True, "sizeOnDisk": it["file_size"]}

sonarr_series, sonarr_eps, sonarr_files = {}, {}, {}
for rk, it in items.items():
    if it["media_type"] != "show":
        continue
    sid = len(sonarr_series) + 1
    my_seasons = sorted((sv for sv in seasons.values() if sv["parent_rating_key"] == rk), key=lambda x: x["media_index"])
    sonarr_series[sid] = {"id": sid, "title": it["title"], "year": it["year"], "tvdbId": rk + 200000,
                          "titleSlug": slug(it["title"]), "monitored": True,
                          "status": "continuing" if sid % 3 == 0 else "ended",
                          "seasons": [{"seasonNumber": sv["media_index"], "monitored": True} for sv in my_seasons]}
    for sv in my_seasons:
        for e in sorted((e for e in episodes.values() if e["parent_rating_key"] == sv["rating_key"]),
                        key=lambda e: e["media_index"]):
            fid = len(sonarr_files) + 1
            sonarr_files[fid] = {"id": fid, "seriesId": sid, "seasonNumber": sv["media_index"], "size": e["file_size"]}
            eid = len(sonarr_eps) + 1
            sonarr_eps[eid] = {"id": eid, "seriesId": sid, "seasonNumber": sv["media_index"],
                               "episodeNumber": e["media_index"], "airDateUtc": iso(sv["added_at"] - 86400),
                               "monitored": True, "hasFile": True, "episodeFileId": fid}
    if sonarr_series[sid]["status"] == "continuing" and my_seasons:
        last = my_seasons[-1]["media_index"]
        for n in range(2):  # upcoming episodes, not on disk yet
            eid = len(sonarr_eps) + 1
            sonarr_eps[eid] = {"id": eid, "seriesId": sid, "seasonNumber": last, "episodeNumber": 90 + n,
                               "airDateUtc": iso(NOW + (n + 1) * 7 * DAY), "monitored": True, "hasFile": False,
                               "episodeFileId": 0}


def series_out(s):
    out = dict(s)
    out["seasons"] = []
    for sn in s["seasons"]:
        files = [f for f in sonarr_files.values() if f["seriesId"] == s["id"] and f["seasonNumber"] == sn["seasonNumber"]]
        eps = [e for e in sonarr_eps.values() if e["seriesId"] == s["id"] and e["seasonNumber"] == sn["seasonNumber"]]
        out["seasons"].append({**sn, "statistics": {"episodeFileCount": len(files), "totalEpisodeCount": len(eps),
                                                   "sizeOnDisk": sum(f["size"] for f in files)}})
    return out


def need_key(request: Request):
    if not request.headers.get("x-api-key"):
        raise HTTPException(401)


@app.get("/{svc}/api/v3/system/status")
def arr_status(svc: str, request: Request):
    need_key(request)
    return {"appName": svc.title(), "version": "4.0.mock"}


@app.get("/radarr/api/v3/movie")
def radarr_list(request: Request, tmdbId: int | None = None):
    need_key(request)
    return [m for m in radarr_movies.values() if tmdbId is None or m["tmdbId"] == tmdbId]


@app.get("/radarr/api/v3/movie/{mid}")
def radarr_get(mid: int, request: Request):
    need_key(request)
    if mid not in radarr_movies:
        raise HTTPException(404)
    return radarr_movies[mid]


@app.delete("/radarr/api/v3/movie/{mid}")
def radarr_delete(mid: int, request: Request):
    need_key(request)
    m = radarr_movies.pop(mid, None)
    if not m:
        raise HTTPException(404)
    rk = m["tmdbId"] - 100000
    items.pop(rk, None)
    print("RADARR delete", m["title"], dict(request.query_params), flush=True)
    return {}


@app.get("/sonarr/api/v3/series")
def sonarr_list(request: Request, tvdbId: int | None = None):
    need_key(request)
    return [series_out(s) for s in sonarr_series.values() if tvdbId is None or s["tvdbId"] == tvdbId]


@app.get("/sonarr/api/v3/series/{sid}")
def sonarr_get(sid: int, request: Request):
    need_key(request)
    if sid not in sonarr_series:
        raise HTTPException(404)
    return series_out(sonarr_series[sid])


@app.put("/sonarr/api/v3/series/{sid}")
async def sonarr_put(sid: int, request: Request):
    need_key(request)
    body = await request.json()
    s = sonarr_series[sid]
    s["monitored"] = body["monitored"]
    for sn in body["seasons"]:
        for mine in s["seasons"]:
            if mine["seasonNumber"] == sn["seasonNumber"] and mine["monitored"] != sn["monitored"]:
                mine["monitored"] = sn["monitored"]
                for e in sonarr_eps.values():
                    if e["seriesId"] == sid and e["seasonNumber"] == sn["seasonNumber"]:
                        e["monitored"] = sn["monitored"]
    print("SONARR put series", s["title"], s["monitored"], [(x["seasonNumber"], x["monitored"]) for x in s["seasons"]], flush=True)
    return series_out(s)


@app.delete("/sonarr/api/v3/series/{sid}")
def sonarr_delete(sid: int, request: Request):
    need_key(request)
    s = sonarr_series.pop(sid)
    items.pop(s["tvdbId"] - 200000, None)
    print("SONARR delete series", s["title"], dict(request.query_params), flush=True)
    return {}


@app.get("/sonarr/api/v3/episodefile")
def sonarr_files_list(seriesId: int, request: Request):
    need_key(request)
    return [f for f in sonarr_files.values() if f["seriesId"] == seriesId]


@app.delete("/sonarr/api/v3/episodefile/bulk")
async def sonarr_files_bulk(request: Request):
    need_key(request)
    ids = (await request.json())["episodeFileIds"]
    for fid in ids:
        sonarr_files.pop(fid, None)
        for e in sonarr_eps.values():
            if e["episodeFileId"] == fid:
                e["hasFile"], e["episodeFileId"] = False, 0
    print("SONARR delete files", len(ids), flush=True)
    return {}


@app.get("/sonarr/api/v3/episode")
def sonarr_eps_list(seriesId: int, request: Request):
    need_key(request)
    return [e for e in sonarr_eps.values() if e["seriesId"] == seriesId]


@app.put("/sonarr/api/v3/episode/monitor")
async def sonarr_eps_monitor(request: Request):
    need_key(request)
    body = await request.json()
    for eid in body["episodeIds"]:
        sonarr_eps[eid]["monitored"] = body["monitored"]
    print("SONARR episode monitor", len(body["episodeIds"]), body["monitored"], flush=True)
    return {}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8181, log_level="warning")
