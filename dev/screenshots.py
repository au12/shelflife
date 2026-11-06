"""Capture README screenshots against a running Shelflife (default http://localhost:8585)."""
import asyncio, sys
from playwright.async_api import async_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8585"
OUT = sys.argv[2] if len(sys.argv) > 2 else "docs/screenshots"

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path="/usr/bin/google-chrome")
        d = await b.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
        await d.goto(BASE); await d.wait_for_selector(".row"); await d.wait_for_timeout(800)
        await d.screenshot(path=f"{OUT}/desktop-list.png")
        await d.click(".row:has(.plays b) .t-main"); await d.wait_for_selector(".d-title"); await d.wait_for_timeout(700)
        await d.screenshot(path=f"{OUT}/desktop-detail.png")
        await d.eval_on_selector(".d-section:last-child", "el => el.scrollIntoView({block: 'start'})"); await d.wait_for_timeout(300)
        await d.screenshot(path=f"{OUT}/desktop-history.png")
        await d.keyboard.press("Escape"); await d.wait_for_timeout(300)
        await d.click("[data-view=seasons]"); await d.wait_for_timeout(700)
        await d.screenshot(path=f"{OUT}/desktop-seasons.png")
        await d.click("[data-view=titles]")
        m = await b.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=3, is_mobile=True, has_touch=True)
        await m.goto(BASE); await m.wait_for_selector(".row"); await m.wait_for_timeout(800)
        await m.screenshot(path=f"{OUT}/mobile-list.png")
        await m.click(".row:has(.plays b) .t-main"); await m.wait_for_selector(".d-title"); await m.wait_for_timeout(700)
        await m.screenshot(path=f"{OUT}/mobile-detail.png")
        await b.close()

asyncio.run(main())
