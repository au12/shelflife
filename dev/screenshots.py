"""Capture the README screenshots against a running Shelflife (default http://localhost:8585).

Run Shelflife against dev/mock_tautulli.py with every integration on (Plex token, Sonarr, Radarr, Overseerr), from a
fresh /config. The script schedules a few titles to leave, keeps one and deletes two first, so every page has
something on it. The free-space readout is faked here, since the dev machine's disk isn't an Unraid array.

    python dev/screenshots.py [base url] [output dir]
"""
import asyncio
import json
import sys
import time
import urllib.request

from playwright.async_api import async_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8585"
OUT = sys.argv[2] if len(sys.argv) > 2 else "docs/screenshots"
TB = 1024 ** 4
STORAGE = [{"name": "Array", "total": int(96 * TB), "free": int(11.8 * TB), "used": int(84.2 * TB)}]


def api(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=60) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else None


def stage():
    items = api("GET", "/api/items")
    by = {r["title"]: r for r in items}
    never = sorted((r for r in items if not r["last_played"] and not r["excluded"]), key=lambda r: -r["size"])
    # Two deletions for the History tab
    gone = [r for r in never if r["kind"] == "movie"][:2]
    job = api("POST", "/api/jobs/delete", {"items": [{"rating_key": r["rating_key"]} for r in gone], "source": "review"})
    while not api("GET", f"/api/jobs/{job['id']}")["finished_at"]:
        time.sleep(0.5)
    # A few titles leaving soon, one of them in a couple of days
    rest = [r for r in never if r not in gone]
    api("POST", "/api/leaving", {"rating_keys": [r["rating_key"] for r in rest[:3]], "days": 14})
    api("POST", "/api/leaving", {"rating_keys": [rest[3]["rating_key"]], "days": 3})
    # One kept title with a note
    keep = by.get("Saltwater Hymnal") or rest[5]
    api("PUT", f"/api/exclusions/{keep['rating_key']}", {"note": "Rewatch every winter"})
    return by


async def shot(page, name, jpeg=False):
    await page.wait_for_timeout(500)
    # Review mode's blurred backdrop makes a huge PNG, so those two are JPEGs.
    if jpeg:
        await page.screenshot(path=f"{OUT}/{name}.jpg", type="jpeg", quality=88)
    else:
        await page.screenshot(path=f"{OUT}/{name}.png")
    print("saved", name)


async def main():
    by = stage()
    detail = by.get("Northbound Sparrow") or next(iter(by.values()))
    async with async_playwright() as p:
        b = await p.chromium.launch()
        d = await b.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
        await d.route("**/api/storage", lambda r: r.fulfill(json=STORAGE))
        await d.goto(BASE)
        await d.wait_for_selector("#list .row:not(.skel)")
        await shot(d, "desktop-list")

        await d.goto(f"{BASE}/?item={detail['rating_key']}")
        await d.wait_for_selector(".vbox")
        await d.wait_for_timeout(1200)
        await shot(d, "desktop-detail")
        await d.keyboard.press("Escape")


        boxes = d.locator("#list .row .cb input")
        for i in range(4):
            await boxes.nth(i).check()
        await d.click("#selDelete")
        await d.wait_for_selector("#flow[open] #pGo")
        await d.click("#flow details.plan summary")
        await shot(d, "desktop-delete")
        await d.click("#flow [data-flow=cancel]")
        await d.click("#selClear")

        await d.keyboard.press("r")
        await d.wait_for_selector("#rvCard")
        await d.keyboard.press("ArrowLeft")
        await d.wait_for_timeout(400)
        await d.keyboard.press("ArrowRight")
        await d.wait_for_timeout(1000)
        await shot(d, "desktop-review", jpeg=True)
        await d.keyboard.press("z")
        await d.wait_for_timeout(500)
        await d.keyboard.press("z")
        await d.wait_for_timeout(500)
        await d.click("[data-rv=finish]")
        await d.wait_for_timeout(400)
        if await d.locator("[data-rv=close]").count():
            await d.click("[data-rv=close]")

        await d.click(".tab[data-tab=leaving]")
        await d.wait_for_selector("#view-leaving .prow:not(.skel)")
        await shot(d, "desktop-leaving")
        await d.click(".tab[data-tab=history]")
        await d.wait_for_selector("#view-history table")
        await shot(d, "desktop-history")
        api("POST", "/api/issues/check")
        while api("GET", "/api/issues")["running"]:
            time.sleep(0.5)
        await d.click(".tab[data-tab=issues]")
        await d.wait_for_selector("#view-issues .isec")
        await shot(d, "desktop-issues")

        m = await b.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=3, is_mobile=True, has_touch=True)
        await m.route("**/api/storage", lambda r: r.fulfill(json=STORAGE))
        await m.goto(BASE)
        await m.wait_for_selector("#list .row:not(.skel)")
        await shot(m, "mobile-list")
        await m.goto(f"{BASE}/?item={detail['rating_key']}")
        await m.wait_for_selector(".vbox")
        await m.wait_for_timeout(1200)
        await shot(m, "mobile-detail")
        await m.goto(BASE)
        await m.wait_for_selector("#list .row:not(.skel)")
        await m.click("#reviewBtn")
        await m.wait_for_selector("#rvCard")
        await m.wait_for_timeout(800)
        await shot(m, "mobile-review", jpeg=True)
        await b.close()


asyncio.run(main())
