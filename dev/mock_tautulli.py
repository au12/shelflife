"""Fake Tautulli, Plex, Radarr, Sonarr, Overseerr and a webhook catcher, all with invented data,
for development, tests and README screenshots.

    pip install fastapi uvicorn pillow
    python dev/mock_tautulli.py            # listens on :8181

Point Shelflife at it:
    TAUTULLI_URL=http://localhost:8181 TAUTULLI_API_KEY=x
    PLEX_TOKEN=x                                         (Plex lives at the same address)
    SONARR_URL=http://localhost:8181/sonarr SONARR_API_KEY=x
    RADARR_URL=http://localhost:8181/radarr RADARR_API_KEY=x
    OVERSEERR_URL=http://localhost:8181/seerr OVERSEERR_API_KEY=x
    NOTIFY_URL=http://localhost:8181/hooks/test

Test helpers: POST /mock/play {"rating_key": 123, "user": "maple"} adds a play right now;
GET /mock/state shows Plex collections, rescans and received notifications.
"""
import hashlib
import io
import random
import time
from datetime import datetime, timezone
from urllib.parse import unquote

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from PIL import Image, ImageDraw, ImageFont

rng = random.Random(7)
NOW = int(time.time())
DAY = 86400
GB = 1024 ** 3
MACHINE = "mockserver0123456789"

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
GENRES = ["Drama", "Thriller", "Comedy", "Mystery", "Sci-Fi", "Documentary", "Crime", "Adventure", "Romance"]
SUMMARY = ("A quiet story about people who keep showing up for each other, told across one long, strange year. "
           "Invented for Shelflife's test data.")
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


def slug(t):
    return "".join(c if c.isalnum() else "-" for c in t.lower()).strip("-")


for title in MOVIES:
    rk = nk()
    res = rng.choice(["1080", "1080", "1080", "4k", "720"])
    size = {"4k": rng.uniform(40, 85), "1080": rng.uniform(6, 32), "720": rng.uniform(2, 6)}[res] * GB
    year = rng.randint(1978, 2025)
    remux = res == "4k" and rng.random() < 0.5
    tag = "Remux-2160p" if remux else {"4k": "WEBDL-2160p", "1080": "Bluray-1080p", "720": "HDTV-720p"}[res]
    items[rk] = {"rating_key": rk, "section_id": 1, "media_type": "movie", "title": title, "year": year,
                 "added_at": NOW - rng.randint(10, 2200) * DAY, "file_size": int(size), "video_resolution": res,
                 "video_codec": "hevc" if res == "4k" or rng.random() < 0.4 else "h264",
                 "bitrate": int(size * 8 / 1000 / (rng.randint(95, 150) * 60)),
                 "hdr": rng.choice(["HDR10", "Dolby Vision · HDR10"]) if res == "4k" else None,
                 "thumb": f"/library/metadata/{rk}/thumb/1", "art": f"/library/metadata/{rk}/art/1",
                 "genres": rng.sample(GENRES, 2), "duration": rng.randint(88, 150) * 60000,
                 "file": f"/data/media/movies/{title} ({year})/{title} ({year}) [{tag}].mkv",
                 "folder": f"/data/media/movies/{title} ({year})"}
    if rng.random() < 0.06:  # a second, smaller version
        items[rk]["extra_version"] = {"video_resolution": "1080", "file_size": int(size * 0.3), "video_codec": "h264",
                                      "file": items[rk]["file"].replace(tag, "Bluray-1080p")}
        items[rk]["file_size"] += items[rk]["extra_version"]["file_size"]

for title, nseasons in SHOWS:
    rk = nk()
    year = rng.randint(1995, 2022)
    res = rng.choice(["1080", "1080", "1080", "720", "4k"])
    items[rk] = {"rating_key": rk, "section_id": 2, "media_type": "show", "title": title, "year": year,
                 "added_at": NOW - rng.randint(60, 2400) * DAY, "thumb": f"/library/metadata/{rk}/thumb/1",
                 "art": f"/library/metadata/{rk}/art/1", "genres": rng.sample(GENRES, 2),
                 "folder": f"/data/media/tv/{title}"}
    per_ep = rng.uniform(0.6, 4.5) * GB
    for s in range(1, nseasons + 1):
        sk = nk()
        seasons[sk] = {"rating_key": sk, "parent_rating_key": rk, "section_id": 2, "media_type": "season",
                       "media_index": s, "title": f"Season {s}", "added_at": items[rk]["added_at"] + s * 20 * DAY,
                       "thumb": f"/library/metadata/{rk}/thumb/1"}
        ep_res = "4k" if res == "4k" and s == nseasons else ("1080" if res == "4k" else res)
        for e in range(1, rng.randint(8, 22) + 1):
            ek = nk()
            ep_title = f"{rng.choice(EP_A)} {rng.choice(EP_B)}"
            episodes[ek] = {"rating_key": ek, "parent_rating_key": sk, "grandparent_rating_key": rk,
                            "section_id": 2, "media_type": "episode", "media_index": e, "parent_media_index": s,
                            "title": ep_title, "show": title, "video_resolution": ep_res,
                            "video_codec": "hevc" if ep_res == "4k" else "h264",
                            "file_size": int(per_ep * rng.uniform(0.8, 1.2) * (2.5 if ep_res == "4k" else 1)),
                            "added_at": seasons[sk]["added_at"],
                            "file": f"/data/media/tv/{title}/Season {s:02d}/{title} - S{s:02d}E{e:02d} - {ep_title}.mkv"}

# Watch history: some titles get regular viewing, some go stale, some are never touched.
row = 50000


def add_history(it, ep, uid, name, when, dur, pct=100):
    global row
    row += 1
    base = {"row_id": row, "id": row, "reference_id": row, "user_id": uid, "user": name, "friendly_name": name,
            "date": when, "started": when, "stopped": when + dur + 120, "paused_counter": rng.choice([0, 0, 60, 240]),
            "play_duration": dur, "percent_complete": pct, "watched_status": 1, "group_count": 1}
    if ep is None:
        base.update(section_id=1, media_type="movie", rating_key=it["rating_key"], parent_rating_key="",
                    grandparent_rating_key="", full_title=it["title"], title=it["title"], parent_media_index="",
                    media_index="")
    else:
        base.update(section_id=2, media_type="episode", rating_key=ep["rating_key"],
                    parent_rating_key=ep["parent_rating_key"], grandparent_rating_key=it["rating_key"],
                    full_title=f"{it['title']} - {ep['title']}", title=ep["title"],
                    parent_media_index=ep["parent_media_index"], media_index=ep["media_index"])
    history.append(base)


for rk, it in items.items():
    if rng.random() < 0.22:
        continue  # never watched
    age_days = (NOW - it["added_at"]) // DAY
    recency = min(age_days - 5, rng.choice([20, 90, 200, 400, 700, 1100]))
    if recency < 10:
        continue
    if it["media_type"] == "movie":
        for _ in range(rng.randint(1, 4)):
            when = NOW - rng.randint(1, recency) * DAY - rng.randint(0, 80000)
            uid, name = rng.choice(USERS)
            add_history(it, None, uid, name, when, rng.randint(70, 140) * 60)
    else:
        eps = sorted((e for e in episodes.values() if e["grandparent_rating_key"] == rk),
                     key=lambda e: (e["parent_media_index"], e["media_index"]))
        for _burst in range(rng.randint(1, 3)):
            uid, name = rng.choice(USERS)
            rec = min(age_days - 5, rng.choice([15, 45, 120, 300, 500, 800, 1100]))
            if rec < 10:
                continue
            first = rng.randrange(len(eps))
            when = NOW - rec * DAY
            for e in eps[first:first + rng.randint(4, 24)]:
                when += rng.choice([0.03, 0.04, 1, 1, 2, 3]) * DAY
                if when > NOW:
                    break
                u2, n2 = (uid, name) if rng.random() < 0.85 else rng.choice(USERS)
                add_history(it, e, u2, n2, int(when) + rng.randint(0, 20000), rng.randint(38, 47) * 60,
                            rng.choice([100, 100, 96, 42]))
history.sort(key=lambda h: h["date"], reverse=True)

# Overseerr requests: a handful of titles, some with the Plex rating key, some only with TMDB/TVDB ids.
requests = []
for i, rk in enumerate(rng.sample(sorted(items), 16)):
    it = items[rk]
    who = rng.choice(["maple", "nora.k", "den_tv", "grandpa_lou"])
    is_movie = it["media_type"] == "movie"
    requests.append({
        "id": i + 1, "status": 2, "type": "movie" if is_movie else "tv", "is4k": False,
        "createdAt": datetime.fromtimestamp(it["added_at"] - 2 * DAY, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "media": {"id": i + 1, "mediaType": "movie" if is_movie else "tv",
                  "tmdbId": rk + 100000 if is_movie else rk + 300000, "tvdbId": None if is_movie else rk + 200000,
                  "ratingKey": None if i % 5 == 4 else str(rk), "ratingKey4k": None, "status": 5},
        "requestedBy": {"id": 10 + i % 4, "displayName": who, "plexUsername": who, "email": f"{slug(who)}@example.com"},
        "seasons": [] if is_movie else [{"seasonNumber": n} for n in range(1, rng.randint(1, 3) + 1)],
    })

# A second Plex copy of one film, with the same TMDB/IMDb ids: the setup where a 4K library is run by a
# second Radarr. Both copies match the one Radarr entry Shelflife knows, which the Issues tab flags.
_orig = next(k for k, v in items.items() if v["media_type"] == "movie" and v["title"] == "Blue Hour")
_dup = nk()
items[_dup] = {**items[_orig], "rating_key": _dup, "title": "Blue Hour", "dup_of": _orig, "video_resolution": "4k",
               "file_size": int(61.5 * GB), "video_codec": "hevc", "hdr": "Dolby Vision · HDR10",
               "thumb": f"/library/metadata/{_dup}/thumb/1", "art": f"/library/metadata/{_dup}/art/1",
               "file": f"/data/media/movies-4k/Blue Hour ({items[_orig]['year']})/Blue Hour [Remux-2160p].mkv",
               "folder": f"/data/media/movies-4k/Blue Hour ({items[_orig]['year']})"}
items[_dup].pop("extra_version", None)
add_history(items[_dup], None, 101, "maple", NOW - 3 * DAY - 4000, 118 * 60)
history.sort(key=lambda h: h["date"], reverse=True)

app = FastAPI()
calls = {"refresh": [], "promoted": [], "edits": []}
notifications = []
collections: dict[str, dict] = {}


def ok(data):
    return {"response": {"result": "success", "message": None, "data": data}}


def row_of(d):
    return {"rating_key": str(d["rating_key"]), "section_id": d["section_id"], "media_type": d["media_type"],
            "title": d["title"], "sort_title": d["title"], "year": str(d.get("year", "")),
            "added_at": str(d["added_at"]), "thumb": d.get("thumb", ""), "file_size": d.get("file_size", ""),
            "media_index": str(d.get("media_index", "")), "parent_media_index": "",
            "video_resolution": d.get("video_resolution", ""), "video_codec": d.get("video_codec", ""),
            "bitrate": str(d.get("bitrate", "")), "last_played": None, "play_count": None}


def guids_for(rk):
    it = items.get(rk)
    if not it:
        return []
    rk = it.get("dup_of") or rk  # a second copy of a film carries the same ids
    if it["media_type"] == "movie":
        return [f"imdb://tt{rk:07d}", f"tmdb://{rk + 100000}"]
    return [f"tvdb://{rk + 200000}", f"tmdb://{rk + 300000}"]


def tautulli_media_info(d):
    versions = [d] + ([d["extra_version"]] if d.get("extra_version") else [])
    out = []
    for v in versions:
        size = v["file_size"] - (d["extra_version"]["file_size"] if v is d and d.get("extra_version") else 0)
        out.append({"container": "mkv", "bitrate": str(d.get("bitrate", "")), "video_codec": v.get("video_codec"),
                    "video_resolution": v.get("video_resolution"), "audio_codec": "eac3", "audio_channels": "6",
                    "parts": [{"file": v["file"], "file_size": str(size),
                               "streams": [{"type": "1", "video_dynamic_range": d.get("hdr") or "SDR"}]}]})
    return out


@app.get("/api/v2")
def api(request: Request):
    q = request.query_params
    cmd = q.get("cmd")
    if cmd == "get_server_info":
        return ok({"pms_identifier": MACHINE, "pms_name": "Mockbox", "pms_url": "http://localhost:8181"})
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
        out = {"rating_key": str(rk), "title": d.get("title", ""), "guid": f"plex://item/{rk}",
               "guids": guids_for(rk), "art": d.get("art", ""), "summary": SUMMARY if d else "",
               "genres": d.get("genres", []), "content_rating": "TV-14" if d.get("media_type") == "show" else "PG-13",
               "audience_rating": "7.4", "duration": str(d.get("duration", "")), "studio": "Mock Pictures"}
        if d.get("media_type") == "movie":
            out["media_info"] = tautulli_media_info(d)
        return ok(out)
    if cmd == "pms_image_proxy":
        return Response(image(q.get("img", ""), int(q.get("width", 120)), int(q.get("height", 180))),
                        media_type="image/jpeg")
    return JSONResponse({"response": {"result": "error", "message": f"unknown cmd {cmd}", "data": {}}})


def image(img: str, w: int, h: int) -> bytes:
    parts = img.split("/")
    rk = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
    title = items.get(rk, {}).get("title", "")
    seed = int(hashlib.md5(f"/library/metadata/{rk}".encode()).hexdigest(), 16)
    c1, c2 = PALETTE[seed % len(PALETTE)], PALETTE[(seed // 7) % len(PALETTE)]
    im = Image.new("RGB", (w, h), c1)
    d = ImageDraw.Draw(im)
    wide = "/art" in img
    for y in range(h):
        t = y / h
        a = tuple(int(int(c1[i:i + 2], 16) * (1 - t) + int(c2[i:i + 2], 16) * t) for i in (1, 3, 5))
        d.line([(0, y), (w, y)], fill=a)
    if wide:  # a soft landscape for backdrops
        for i in range(6):
            r = random.Random(seed + i)
            cx, cy, rad = r.randint(0, w), r.randint(h // 3, h), r.randint(h // 4, h)
            d.ellipse([cx - rad, cy - rad // 2, cx + rad, cy + rad // 2],
                      fill=tuple(min(255, int(c2[j:j + 2], 16) + 18 * i) for j in (1, 3, 5)))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=82)
        return buf.getvalue()
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


# ---- Plex -------------------------------------------------------------------

def need_token(request: Request):
    if not request.headers.get("x-plex-token"):
        raise HTTPException(401)


def plex_media(d):
    versions = [d] + ([d["extra_version"]] if d.get("extra_version") else [])
    out = []
    for v in versions:
        size = v["file_size"] - (d["extra_version"]["file_size"] if v is d and d.get("extra_version") else 0)
        stream = {"streamType": 1, "displayTitle": f"{'4K' if v.get('video_resolution') == '4k' else v.get('video_resolution', '') + 'p'}"
                  f"{' ' + d['hdr'].replace(' · ', '/') if d.get('hdr') and v is d else ''} (HEVC)"}
        if d.get("hdr") and v is d:
            stream["extendedDisplayTitle"] = f"4K {d['hdr'].replace(' · ', '/')} (HEVC Main 10)"
        out.append({"videoResolution": v.get("video_resolution", "1080"), "videoCodec": v.get("video_codec", "h264"),
                    "bitrate": d.get("bitrate", 8000), "container": "mkv", "audioCodec": "eac3", "audioChannels": 6,
                    "Part": [{"size": size, "file": v.get("file"), "Stream": [stream]}]})
    return out


def plex_meta(d, guids=False):
    m = {"ratingKey": str(d["rating_key"]), "title": d["title"], "titleSort": d["title"], "addedAt": d["added_at"],
         "thumb": d.get("thumb")}
    if "year" in d:
        m["year"] = d["year"]
    if d.get("art"):
        m["art"] = d["art"]
    if d["media_type"] in ("movie", "episode"):
        m["Media"] = plex_media(d)
    if d["media_type"] == "season":
        m["index"], m["parentRatingKey"] = d["media_index"], str(d["parent_rating_key"])
    if d["media_type"] == "episode":
        m["parentRatingKey"], m["grandparentRatingKey"] = str(d["parent_rating_key"]), str(d["grandparent_rating_key"])
    if guids and d["media_type"] in ("movie", "show"):
        m["guid"] = f"plex://{d['media_type']}/{d['rating_key']}"
        m["Guid"] = [{"id": g} for g in guids_for(d["rating_key"])]
    return m


@app.get("/library/sections/{sid}/all")
def plex_all(sid: int, request: Request):
    need_token(request)
    q = request.query_params
    t = int(q.get("type"))
    pool = {1: items, 2: items, 3: seasons, 4: episodes}[t]
    want = {1: "movie", 2: "show", 3: "season", 4: "episode"}[t]
    rows = [plex_meta(r, guids=q.get("includeGuids") == "1") for r in pool.values()
            if r["section_id"] == sid and r["media_type"] == want]
    start, size = int(q.get("X-Plex-Container-Start", 0)), int(q.get("X-Plex-Container-Size", 50))
    return {"MediaContainer": {"totalSize": len(rows), "size": len(rows[start:start + size]),
                               "Metadata": rows[start:start + size]}}


@app.get("/library/metadata/{rk}")
def plex_metadata(rk: int, request: Request):
    need_token(request)
    d = items.get(rk) or seasons.get(rk) or episodes.get(rk)
    if not d:
        raise HTTPException(404)
    m = plex_meta(d, guids=True)
    m.update(summary=SUMMARY, Genre=[{"tag": g} for g in d.get("genres", [])], audienceRating=7.4,
             contentRating="TV-14" if d["media_type"] == "show" else "PG-13", duration=d.get("duration"),
             studio="Mock Pictures")
    if d["media_type"] == "show":
        m["Location"] = [{"path": d["folder"]}]
    return {"MediaContainer": {"Metadata": [m]}}


@app.get("/library/sections/{sid}/refresh")
def plex_refresh(sid: int, request: Request):
    need_token(request)
    calls["refresh"].append({"section": sid, "at": time.time()})
    return Response(status_code=200)


def uri_keys(uri: str) -> list[int]:
    return [int(k) for k in unquote(uri).rsplit("/", 1)[-1].split(",") if k.strip().isdigit()]


@app.get("/library/sections/{sid}/collections")
def plex_collections(sid: int, request: Request):
    need_token(request)
    return {"MediaContainer": {"Metadata": [
        {"ratingKey": c["ratingKey"], "title": c["title"], "summary": c["summary"]}
        for c in collections.values() if c["sectionId"] == sid]}}


@app.post("/library/collections")
def plex_create_collection(request: Request):
    need_token(request)
    q = request.query_params
    ck = str(nk())
    collections[ck] = {"ratingKey": ck, "title": q.get("title"), "summary": "", "sectionId": int(q.get("sectionId")),
                       "type": q.get("type"), "children": uri_keys(q.get("uri", ""))}
    return {"MediaContainer": {"Metadata": [{"ratingKey": ck, "title": q.get("title")}]}}


@app.get("/library/collections/{ck}/children")
def plex_collection_children(ck: str, request: Request):
    need_token(request)
    if ck not in collections:
        raise HTTPException(404)
    return {"MediaContainer": {"Metadata": [{"ratingKey": str(k)} for k in collections[ck]["children"]]}}


@app.put("/library/collections/{ck}/items")
def plex_collection_add(ck: str, request: Request):
    need_token(request)
    for k in uri_keys(request.query_params.get("uri", "")):
        if k not in collections[ck]["children"]:
            collections[ck]["children"].append(k)
    return {}


@app.delete("/library/collections/{ck}/items/{rk}")
def plex_collection_remove(ck: str, rk: int, request: Request):
    need_token(request)
    if ck in collections and rk in collections[ck]["children"]:
        collections[ck]["children"].remove(rk)
    return {}


@app.put("/library/sections/{sid}/all")
def plex_edit(sid: int, request: Request):
    need_token(request)
    q = request.query_params
    calls["edits"].append(dict(q))
    c = collections.get(q.get("id"))
    if c:
        if "summary.value" in q:
            c["summary"] = q["summary.value"]
        if "title.value" in q:
            c["title"] = q["title.value"]
    return {}


@app.post("/hubs/sections/{sid}/manage")
def plex_promote(sid: int, request: Request):
    need_token(request)
    calls["promoted"].append(dict(request.query_params))
    return {}


# ---- Radarr / Sonarr (mounted at /radarr and /sonarr) ----------------------

def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


radarr_movies = {}
for rk, it in items.items():
    if it["media_type"] == "movie" and rk % 11 and not it.get("dup_of"):  # a few movies aren't in Radarr
        mid = len(radarr_movies) + 1
        primary = it["file_size"] - (it["extra_version"]["file_size"] if it.get("extra_version") else 0)
        radarr_movies[mid] = {"id": mid, "title": it["title"], "year": it["year"], "tmdbId": rk + 100000,
                              "imdbId": f"tt{rk:07d}", "titleSlug": f"{slug(it['title'])}-{rk + 100000}",
                              "monitored": True, "hasFile": True, "sizeOnDisk": primary,
                              "path": it["folder"], "movieFile": {"path": it["file"], "size": primary}}

sonarr_series, sonarr_eps, sonarr_files = {}, {}, {}
for rk, it in items.items():
    if it["media_type"] != "show" or it["title"] == "The Lodge":  # The Lodge isn't in Sonarr
        continue
    sid = len(sonarr_series) + 1
    my_seasons = sorted((sv for sv in seasons.values() if sv["parent_rating_key"] == rk), key=lambda x: x["media_index"])
    sonarr_series[sid] = {"id": sid, "title": it["title"], "year": it["year"], "tvdbId": rk + 200000,
                          "titleSlug": slug(it["title"]), "monitored": True, "path": it["folder"],
                          "status": "continuing" if sid % 3 == 0 else "ended",
                          "seasons": [{"seasonNumber": sv["media_index"], "monitored": True} for sv in my_seasons]}
    for sv in my_seasons:
        for e in sorted((e for e in episodes.values() if e["parent_rating_key"] == sv["rating_key"]),
                        key=lambda e: e["media_index"]):
            fid = len(sonarr_files) + 1
            sonarr_files[fid] = {"id": fid, "seriesId": sid, "seasonNumber": sv["media_index"], "size": e["file_size"],
                                 "path": e["file"], "plex_rk": e["rating_key"]}
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


# ---- deliberate mismatches for the Issues tab
# Radarr has this film but no file on record
_nofile = next(m for m in radarr_movies.values() if m["title"] == "Glass Orchard")
_nofile.update(hasFile=False, sizeOnDisk=0)
_nofile.pop("movieFile")
# In Radarr with a file, but not in Plex; plus one wanted film with no file (not an issue)
for _title, _year, _size in (("Harvest Moon Motel", 2019, 18.4), ("The Glass Divide", 2021, 27.9),
                             ("Coming Attractions", 2026, 0)):
    _mid = len(radarr_movies) + 1
    radarr_movies[_mid] = {"id": _mid, "title": _title, "year": _year, "tmdbId": 990000 + _mid,
                           "imdbId": f"tt99{_mid:05d}", "titleSlug": f"{slug(_title)}-{990000 + _mid}",
                           "monitored": True, "hasFile": bool(_size), "sizeOnDisk": int(_size * GB),
                           "path": f"/data/media/movies/{_title} ({_year})"}
    if _size:
        radarr_movies[_mid]["movieFile"] = {"path": f"/data/media/movies/{_title} ({_year})/{_title} ({_year}).mkv",
                                            "size": int(_size * GB)}
# In Sonarr with files, but not in Plex
_sid = len(sonarr_series) + 1
sonarr_series[_sid] = {"id": _sid, "title": "Rooftop Gardens", "year": 2020, "tvdbId": 880001,
                       "titleSlug": "rooftop-gardens", "monitored": True, "path": "/data/media/tv/Rooftop Gardens",
                       "status": "ended", "seasons": [{"seasonNumber": 1, "monitored": True}]}
for _e in range(1, 7):
    _fid = len(sonarr_files) + 1
    sonarr_files[_fid] = {"id": _fid, "seriesId": _sid, "seasonNumber": 1, "size": int(1.3 * GB),
                          "path": f"/data/media/tv/Rooftop Gardens/Season 01/Rooftop Gardens - S01E{_e:02d}.mkv",
                          "plex_rk": None}
    _eid = len(sonarr_eps) + 1
    sonarr_eps[_eid] = {"id": _eid, "seriesId": _sid, "seasonNumber": 1, "episodeNumber": _e,
                        "airDateUtc": iso(NOW - 900 * DAY), "monitored": True, "hasFile": True, "episodeFileId": _fid}


def _series_named(title):
    return next(v for v in sonarr_series.values() if v["title"] == title)


# Plex has an episode Sonarr has no file for
_hl = _series_named("Harbor Lights")
_f = next(f for f in sonarr_files.values() if f["seriesId"] == _hl["id"] and f["seasonNumber"] == 1)
sonarr_files.pop(_f["id"])
for _ep in sonarr_eps.values():
    if _ep["episodeFileId"] == _f["id"]:
        _ep["hasFile"], _ep["episodeFileId"] = False, 0
# Sonarr has files for two episodes Plex hasn't picked up yet
_pc = _series_named("Pinecrest")
_last = max(sn["seasonNumber"] for sn in _pc["seasons"])
for _e in (50, 51):
    _fid = max(sonarr_files) + 1
    sonarr_files[_fid] = {"id": _fid, "seriesId": _pc["id"], "seasonNumber": _last, "size": int(1.1 * GB),
                          "path": f"/data/media/tv/Pinecrest/Season {_last:02d}/Pinecrest - S{_last:02d}E{_e}.mkv",
                          "plex_rk": None}
    _eid = max(sonarr_eps) + 1
    sonarr_eps[_eid] = {"id": _eid, "seriesId": _pc["id"], "seasonNumber": _last, "episodeNumber": _e,
                        "airDateUtc": iso(NOW - 2 * DAY), "monitored": True, "hasFile": True, "episodeFileId": _fid}


def series_out(s):
    out = dict(s)
    out["seasons"] = []
    total = 0
    for sn in s["seasons"]:
        files = [f for f in sonarr_files.values() if f["seriesId"] == s["id"] and f["seasonNumber"] == sn["seasonNumber"]]
        eps = [e for e in sonarr_eps.values() if e["seriesId"] == s["id"] and e["seasonNumber"] == sn["seasonNumber"]]
        size = sum(f["size"] for f in files)
        total += size
        out["seasons"].append({**sn, "statistics": {"episodeFileCount": len(files), "totalEpisodeCount": len(eps),
                                                   "sizeOnDisk": size}})
    out["statistics"] = {"sizeOnDisk": total,
                         "episodeFileCount": sum(x["statistics"]["episodeFileCount"] for x in out["seasons"]),
                         "totalEpisodeCount": sum(x["statistics"]["totalEpisodeCount"] for x in out["seasons"])}
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
    items.pop(m["tmdbId"] - 100000, None)
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
    print("SONARR put series", s["title"], s["monitored"], [(x["seasonNumber"], x["monitored"]) for x in s["seasons"]],
          flush=True)
    return series_out(s)


def _drop_plex_episode(plex_rk):
    ep = episodes.pop(plex_rk, None)
    if ep and not any(e["parent_rating_key"] == ep["parent_rating_key"] for e in episodes.values()):
        seasons.pop(ep["parent_rating_key"], None)  # Plex drops a season once its last episode is gone


@app.delete("/sonarr/api/v3/series/{sid}")
def sonarr_delete(sid: int, request: Request):
    need_key(request)
    s = sonarr_series.pop(sid)
    show_rk = s["tvdbId"] - 200000
    items.pop(show_rk, None)
    for sk in [k for k, v in seasons.items() if v["parent_rating_key"] == show_rk]:
        seasons.pop(sk, None)
    for ek in [k for k, v in episodes.items() if v["grandparent_rating_key"] == show_rk]:
        episodes.pop(ek, None)
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
        f = sonarr_files.pop(fid, None)
        if f:
            _drop_plex_episode(f["plex_rk"])
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


# ---- Overseerr --------------------------------------------------------------

@app.get("/seerr/api/v1/status")
def seerr_status():
    return {"version": "1.33.2", "commitTag": "mock"}


@app.get("/seerr/api/v1/request")
def seerr_requests(request: Request, take: int = 20, skip: int = 0):
    if not request.headers.get("x-api-key"):
        raise HTTPException(403)
    page = requests[skip:skip + take]
    return {"pageInfo": {"pages": (len(requests) + take - 1) // take, "pageSize": take, "results": len(requests),
                         "page": skip // take + 1}, "results": page}


# ---- Webhook catcher and test helpers ---------------------------------------

@app.post("/hooks/{name}")
async def hook(name: str, request: Request):
    body = await request.body()
    try:
        payload = await request.json()
    except ValueError:
        payload = body.decode(errors="replace")
    notifications.append({"hook": name, "at": time.time(), "payload": payload,
                          "title": request.headers.get("title")})
    return {}


@app.post("/mock/play")
async def mock_play(request: Request):
    """Add a play happening now: a movie, a show's first episode, or a season's first episode."""
    body = await request.json()
    rk = int(body["rating_key"])
    user = body.get("user", "maple")
    uid = dict((n, u) for u, n in USERS).get(user, 101)
    if rk in items and items[rk]["media_type"] == "movie":
        add_history(items[rk], None, uid, user, int(time.time()) - 30, 95 * 60)
    else:
        show_rk = rk if rk in items else seasons[rk]["parent_rating_key"]
        eps = sorted((e for e in episodes.values() if (e["grandparent_rating_key"] == rk or e["parent_rating_key"] == rk)),
                     key=lambda e: (e["parent_media_index"], e["media_index"]))
        add_history(items[show_rk], eps[0], uid, user, int(time.time()) - 30, 42 * 60)
    history.sort(key=lambda h: h["date"], reverse=True)
    return {"ok": True, "plays": len(history)}


@app.get("/mock/state")
def mock_state():
    return {"collections": list(collections.values()), "calls": calls, "notifications": notifications,
            "items": len(items), "seasons": len(seasons), "episodes": len(episodes)}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8181, log_level="warning")
