"""File and metadata details for the detail panel: art, summary, genres, versions, file paths, HDR."""
import json
import time

from . import db, stats
from .clients import ApiError, Tautulli, _int, plex_client

TTL = 12 * 3600


def _hdr_from_text(text: str) -> str | None:
    t = text or ""
    found = []
    if "Dolby Vision" in t or "DoVi" in t:
        found.append("Dolby Vision")
    if "HDR10+" in t:
        found.append("HDR10+")
    elif "HDR10" in t:
        found.append("HDR10")
    elif "HLG" in t:
        found.append("HLG")
    elif "HDR" in t:
        found.append("HDR")
    return " · ".join(found) or None


def _from_plex(meta: dict) -> dict:
    versions = []
    for m in meta.get("Media") or []:
        files, hdr = [], None
        for p in m.get("Part") or []:
            files.append({"path": p.get("file"), "size": _int(p.get("size"))})
            for s in p.get("Stream") or []:
                if str(s.get("streamType")) == "1":
                    hdr = hdr or _hdr_from_text(f"{s.get('extendedDisplayTitle', '')} {s.get('displayTitle', '')}") \
                        or ("Dolby Vision" if s.get("DOVIPresent") else None) \
                        or ("HDR" if s.get("colorTrc") in ("smpte2084", "arib-std-b67") else None)
        versions.append(_version(m.get("videoResolution"), m.get("videoCodec"), m.get("bitrate"), m.get("container"),
                                 m.get("audioCodec"), m.get("audioChannels"), hdr, files))
    return {
        "art": meta.get("art"), "summary": meta.get("summary"), "tagline": meta.get("tagline"),
        "genres": [g.get("tag") for g in meta.get("Genre") or [] if g.get("tag")][:4],
        "content_rating": meta.get("contentRating"), "rating": meta.get("audienceRating") or meta.get("rating"),
        "duration": _int(meta.get("duration"), None), "studio": meta.get("studio"),
        "paths": [loc.get("path") for loc in meta.get("Location") or [] if loc.get("path")],
        "versions": versions,
    }


def _from_tautulli(meta: dict) -> dict:
    versions = []
    for m in meta.get("media_info") or []:
        files, hdr = [], None
        for p in m.get("parts") or []:
            files.append({"path": p.get("file"), "size": _int(p.get("file_size"))})
            for s in p.get("streams") or []:
                dr = s.get("video_dynamic_range")
                if dr and dr != "SDR":
                    hdr = hdr or dr
        versions.append(_version(m.get("video_resolution"), m.get("video_codec"), m.get("bitrate"),
                                 m.get("container"), m.get("audio_codec"), m.get("audio_channels"), hdr, files))
    return {
        "art": meta.get("art"), "summary": meta.get("summary"), "tagline": meta.get("tagline"),
        "genres": list(meta.get("genres") or [])[:4], "content_rating": meta.get("content_rating"),
        "rating": meta.get("audience_rating") or meta.get("rating"),
        "duration": _int(meta.get("duration"), None), "studio": meta.get("studio"),
        "paths": [p for p in meta.get("locations") or [] if p],
        "versions": versions,
    }


def _version(res, codec, bitrate, container, audio, channels, hdr, files) -> dict:
    names = " ".join((f.get("path") or "") for f in files).lower()
    return {"resolution": stats.res_label(res), "codec": (codec or "").upper() or None,
            "bitrate": _int(bitrate, None), "container": (container or "").upper() or None,
            "audio": (audio or "").upper() or None, "channels": _int(channels, None), "hdr": hdr,
            "remux": "remux" in names, "size": sum(f.get("size") or 0 for f in files), "files": files}


async def details(rating_key: int) -> dict:
    cached = db.query("SELECT data, fetched_at FROM meta_cache WHERE rating_key=?", (rating_key,))
    if cached and time.time() - cached[0]["fetched_at"] < TTL:
        return json.loads(cached[0]["data"])

    data: dict | None = None
    plex = plex_client()
    if plex:
        try:
            meta = await plex.metadata(rating_key)
            if meta:
                data = _from_plex(meta)
        except ApiError:
            data = None
        finally:
            await plex.close()
    if data is None:
        t = Tautulli()
        try:
            data = _from_tautulli(await t.metadata(rating_key))
        except ApiError:
            data = {"art": None, "versions": [], "paths": [], "genres": [], "error": "Couldn't read file details"}
        finally:
            await t.close()

    # Seasons inherit the show's backdrop when they don't have their own.
    if not data.get("art"):
        row = db.query("SELECT i.art FROM seasons s JOIN items i ON i.rating_key=s.show_key WHERE s.rating_key=? "
                       "UNION ALL SELECT art FROM items WHERE rating_key=?", (rating_key, rating_key))
        if row and row[0]["art"]:
            data["art"] = row[0]["art"]
    if not data.get("error"):
        with db.tx() as c:
            c.execute("INSERT OR REPLACE INTO meta_cache(rating_key, data, fetched_at) VALUES(?,?,?)",
                      (rating_key, json.dumps(data), int(time.time())))
    return data
