import httpx

from . import config


class ApiError(RuntimeError):
    pass


def _int(v, default=0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


class Tautulli:
    def __init__(self, url: str = None, api_key: str = None):
        self.url = (url or config.TAUTULLI_URL).rstrip("/")
        self.api_key = api_key or config.TAUTULLI_API_KEY
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(300, connect=10), verify=config.VERIFY_SSL)

    async def close(self):
        await self.client.aclose()

    async def call(self, cmd: str, **params):
        params = {k: v for k, v in params.items() if v is not None}
        try:
            r = await self.client.get(f"{self.url}/api/v2", params={"apikey": self.api_key, "cmd": cmd, **params})
        except httpx.HTTPError as e:
            raise ApiError(f"Couldn't reach Tautulli at {self.url} ({e.__class__.__name__})") from e
        if r.status_code == 401:
            raise ApiError("Tautulli rejected the API key")
        if r.status_code >= 400:
            raise ApiError(f"Tautulli returned HTTP {r.status_code} for {cmd}")
        try:
            body = r.json()["response"]
        except (ValueError, KeyError) as e:
            raise ApiError(f"Unexpected response from Tautulli for {cmd}") from e
        if body.get("result") != "success":
            raise ApiError(f"Tautulli {cmd} failed: {body.get('message') or 'unknown error'}")
        return body.get("data")

    async def image(self, img: str, width: int, height: int) -> tuple[bytes, str]:
        r = await self.client.get(
            f"{self.url}/api/v2",
            params={"apikey": self.api_key, "cmd": "pms_image_proxy", "img": img,
                    "width": width, "height": height, "img_format": "jpg", "fallback": "poster"},
        )
        if r.status_code >= 400:
            raise ApiError(f"Image fetch failed ({r.status_code})")
        return r.content, r.headers.get("content-type", "image/jpeg")

    async def media_info(self, section_id, rating_key=None, refresh=True) -> list[dict]:
        data = await self.call(
            "get_library_media_info", section_id=section_id, rating_key=rating_key,
            refresh="true" if refresh else None, length=1_000_000, order_column="sort_title", order_dir="asc",
        )
        return (data or {}).get("data") or []


class Plex:
    PAGE = 1000

    def __init__(self, url: str, token: str):
        self.url = url.rstrip("/")
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(300, connect=10), verify=config.VERIFY_SSL,
            headers={"X-Plex-Token": token, "Accept": "application/json",
                     "X-Plex-Product": "Shelflife", "X-Plex-Client-Identifier": "shelflife"},
        )

    async def close(self):
        await self.client.aclose()

    async def all_of_type(self, section_id, plex_type: int) -> list[dict]:
        """Every item of a type in a library section (1=movie 2=show 3=season 4=episode)."""
        out, start = [], 0
        while True:
            try:
                r = await self.client.get(
                    f"{self.url}/library/sections/{section_id}/all",
                    params={"type": plex_type, "X-Plex-Container-Start": start, "X-Plex-Container-Size": self.PAGE},
                )
            except httpx.HTTPError as e:
                raise ApiError(f"Couldn't reach Plex at {self.url} ({e.__class__.__name__})") from e
            if r.status_code == 401:
                raise ApiError("Plex rejected the token")
            if r.status_code >= 400:
                raise ApiError(f"Plex returned HTTP {r.status_code}")
            mc = r.json().get("MediaContainer", {})
            batch = mc.get("Metadata") or []
            out.extend(batch)
            total = _int(mc.get("totalSize"), len(out))
            start += len(batch)
            if not batch or start >= total:
                return out


def plex_media_size(meta: dict) -> int:
    """Total bytes on disk for a Plex item, counting every version and part."""
    total = 0
    for media in meta.get("Media") or []:
        for part in media.get("Part") or []:
            total += _int(part.get("size"))
    return total
