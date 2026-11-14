"""Browser checks for the web UI against dev/mock_tautulli.py, at desktop and phone sizes.

Starts the mock, a Shelflife with every integration (:8611) and a Tautulli-only one (:8612), then drives
Chromium with Playwright: filters, URL state, keyboard shortcuts, the detail panel, bulk delete, Leaving soon,
Review mode, the other tabs and settings. Fails on page errors, console errors, broken flows, text below
4.5:1 contrast, or rows that overlap on a phone.

    python dev/test_ui.py            # CHROME=/path/to/chrome to use a specific browser
"""
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

from playwright.async_api import async_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOCK = "http://127.0.0.1:8181"
FULL = "http://127.0.0.1:8611"
LITE = "http://127.0.0.1:8612"
failures = []
problems = []   # console errors and page errors, collected per page


def check(cond, label):
    print(("PASS " if cond else "FAIL ") + label, flush=True)
    if not cond:
        failures.append(label)


def get(url):
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return json.loads(r.read())
    except (urllib.error.URLError, ConnectionError):
        return None


def synced(base):
    st = get(base + "/api/status")
    return st if st and st["sync"]["last_success"] and not st["sync"]["running"] else None


def start(port, cfg, extra):
    env = {**os.environ, "CONFIG_DIR": cfg, "TAUTULLI_URL": MOCK, "TAUTULLI_API_KEY": "x", "TICK_SECONDS": "5", **extra}
    return subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)], cwd=ROOT,
                            env=env, stdout=open(os.path.join(cfg, "app.log"), "w"), stderr=subprocess.STDOUT)


# Every visible piece of text, measured against the background it actually sits on.
CONTRAST_JS = r"""() => {
  const parse = (c) => {
    let m = c.match(/rgba?\(([^)]+)\)/);
    if (m) { const p = m[1].split(/[\s,\/]+/).filter(Boolean).map(Number); return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1]; }
    m = c.match(/color\(srgb ([^)]+)\)/);
    if (m) { const p = m[1].split(/[\s\/]+/).filter(Boolean).map(Number); return [p[0] * 255, p[1] * 255, p[2] * 255, p.length > 3 ? p[3] : 1]; }
    return null;
  };
  const over = (top, under) => { const a = top[3]; return [0, 1, 2].map((i) => top[i] * a + under[i] * (1 - a)).concat(1); };
  const lum = (c) => { const v = c.slice(0, 3).map((x) => { x /= 255; return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4; }); return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]; };
  const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };
  const bgOf = (el) => {
    const stack = [];
    for (let e = el; e; e = e.parentElement) {
      const cs = getComputedStyle(e);
      if (cs.backgroundImage !== "none" && !e.matches("select")) return null;
      const c = parse(cs.backgroundColor);
      if (c && c[3] > 0) { stack.push(c); if (c[3] >= 1) break; }
      if (e.matches("dialog[open]")) break;
    }
    let bg = [27, 33, 41, 1];
    for (const c of stack.reverse()) bg = over(c, bg);
    return bg;
  };
  const bad = [];
  for (const el of document.querySelectorAll("body *")) {
    if (el.closest("svg, script, style, [inert], .sr")) continue;
    if (el.closest(":disabled, [aria-disabled=true], .is-off, .d-hero.has-art, .rv-art")) continue;
    const own = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
    if (!own || !el.getClientRects().length) continue;
    const r = el.getBoundingClientRect();
    if (r.bottom < 0 || r.top > innerHeight || r.right < 0 || r.left > innerWidth || !r.width) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === "hidden" || +cs.opacity === 0) continue;
    let alpha = 1;
    for (let e = el; e; e = e.parentElement) alpha *= +getComputedStyle(e).opacity;
    if (alpha < 0.05) continue;
    const bg = bgOf(el);
    const fg = parse(cs.color);
    if (!bg || !fg) continue;
    const shown = over([fg[0], fg[1], fg[2], fg[3] * alpha], bg);
    const size = parseFloat(cs.fontSize), weight = +cs.fontWeight;
    const need = size >= 24 || (size >= 18.66 && weight >= 600) ? 3 : 4.5;
    const got = ratio(shown, bg);
    if (got < need) bad.push(`${el.tagName.toLowerCase()}.${[...el.classList].join(".")} "${el.textContent.trim().slice(0, 30)}" ${got.toFixed(2)}`);
  }
  return bad;
}"""

OVERLAP_JS = """() => [...document.querySelectorAll('#list .row')].slice(0, 40).filter((li) => {
  const a = li.querySelector('.when').getBoundingClientRect(), b = li.querySelector('.plays').getBoundingClientRect();
  return a.width && b.width && a.right > b.left + 1 && a.bottom > b.top && b.bottom > a.top;
}).map((li) => li.querySelector('.t-link').textContent)"""


def watch(page, tag):
    page.on("console", lambda m: problems.append(f"{tag} console {m.type}: {m.text}") if m.type == "error" else None)
    page.on("pageerror", lambda e: problems.append(f"{tag} page error: {e}"))


async def contrast(page, label):
    bad = await page.evaluate(CONTRAST_JS)
    check(not bad, f"text contrast holds: {label}" + (f" ({'; '.join(bad[:6])})" if bad else ""))


async def ready(page, url):
    await page.goto(url)
    await page.wait_for_selector("#list .row:not(.skel), #empty:not([hidden])")
    await page.wait_for_timeout(300)


async def sel_count(page):
    return await page.evaluate("S.selected.size")


async def desktop(b):
    page = await b.new_page(viewport={"width": 1440, "height": 900})
    watch(page, "desktop")
    await ready(page, FULL)
    rows = await page.locator("#list .row").count()
    check(rows > 10, f"cull list renders rows ({rows})")
    head = await page.text_content("#headline")
    check(bool(re.search(r"\d+(\.\d+)? (TB|GB) in \d+ titles not watched in the last year", head)), f"headline reads naturally ({head.strip()})")
    check(await page.locator(".leg").count() >= 3, "last-watched legend shows")
    check(await page.locator("#storage .disk").count() == 1, "free space readout shows")
    await contrast(page, "cull list")

    # ---- keyboard: move, select, clear
    await page.keyboard.press("j")
    first = await page.evaluate("document.activeElement.closest('.row') && document.activeElement.closest('.row').dataset.rk")
    check(first == await page.get_attribute("#list .row >> nth=0", "data-rk"), "J focuses the first row")
    await page.keyboard.press("x")
    await page.keyboard.press("j")
    await page.keyboard.press("x")
    check(await sel_count(page) == 2 and await page.is_visible("#selbar"), "X selects rows and shows the selection bar")
    await page.keyboard.press("Escape")
    check(await sel_count(page) == 0 and not await page.is_visible("#selbar"), "Esc clears the selection")

    # ---- search and empty state
    await page.keyboard.press("/")
    check(await page.evaluate("document.activeElement.id") == "fSearch", "/ focuses search")
    await page.keyboard.type("zzqqx")
    await page.wait_for_timeout(300)
    check("Nothing called" in (await page.text_content("#empty")), "a search with no hits explains itself")
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(200)
    check(await page.locator("#list .row").count() == rows, "Esc in search clears it")

    # ---- filters in the URL survive a reload
    await ready(page, FULL + "/?age=730&never=exclude&sort=last")
    chip = await page.text_content("[data-chip=age]")
    head = await page.text_content("#headline")
    check("2 years" in chip and "watched before, but not in the last 2 years" in head, "filters load from the URL")
    check("is-on" in (await page.get_attribute("[data-sort=last]", "class")), "sort loads from the URL")
    await page.reload()
    await page.wait_for_selector("#list .row:not(.skel), #empty:not([hidden])")
    check("age=730" in page.url and "2 years" in (await page.text_content("[data-chip=age]")), "a reload keeps the filters")
    await page.click("[data-chip=age]")
    await page.click("#pop .opt:has-text('1 year')")
    await page.wait_for_timeout(250)
    check("age=" not in page.url and await page.is_hidden("#pop"), "choosing a filter closes the popover and updates the URL")
    await page.click("[data-chip=never]")
    await page.click("#pop .opt:has-text('Included')")
    await page.click("[data-sort=title]")
    titles = await page.locator("#list .row .t-link").all_text_contents()
    check(titles[:5] == sorted(titles[:5], key=str.lower) or len(titles) < 2, "title column sorts A to Z")
    await page.click("[data-sort=size]")
    check("sort=" not in page.url, "default sort leaves the URL clean")

    # ---- the spectrum filters by bucket
    await page.click(".leg[data-bucket=never]")
    await page.wait_for_timeout(200)
    check("nobody has ever watched" in (await page.text_content("#headline")) and "bucket=never" in page.url,
          "clicking Never watched in the legend filters to it")
    whens = await page.locator("#list .row .when").all_text_contents()
    check(all("Never" in w for w in whens), "only never-watched rows remain")
    check(await page.locator("[data-chip=age]").count() == 0, "the bar's choice replaces the age chip")
    await page.click("[data-clear-bucket]")
    check("bucket=" not in page.url, "clearing the bar filter restores the list")

    # ---- detail panel, prev/next, URL and back button
    rk0 = await page.get_attribute("#list .row >> nth=0", "data-rk")
    await page.click("#list .row >> nth=0 >> .t-link")
    await page.wait_for_selector("#dTitle")
    check(f"item={rk0}" in page.url, "opening a title puts it in the URL")
    await page.wait_for_selector(".vbox, .d-section .hint", timeout=10000)
    await page.wait_for_timeout(800)
    check(await page.locator(".badge").count() > 0, "quality badges show")
    check(await page.locator(".d-art").count() == 1, "backdrop art shows")
    await contrast(page, "detail panel")
    await page.keyboard.press("j")
    await page.wait_for_timeout(600)
    rk1 = await page.get_attribute("#list .row >> nth=1", "data-rk")
    check(f"item={rk1}" in page.url, "J moves to the next title in the panel")
    await page.go_back()
    await page.wait_for_timeout(400)
    check(not await page.evaluate("document.querySelector('#drawer').classList.contains('is-open')"), "Back closes the panel")

    # ---- seasons: open, delete dialog wording, back to the show
    await page.click("#fView [data-view=seasons]")
    await page.wait_for_selector("#list .row:not(.skel)")
    check("view=seasons" in page.url and "episode" in (await page.text_content("#list .row >> nth=0")), "seasons view lists seasons")
    await page.click("#list .row >> nth=0 >> .t-link")
    await page.wait_for_selector("#dDelete:not([disabled])", timeout=10000)
    check("Delete season" in (await page.text_content("#dDelete")), "a season offers Delete season")
    await page.click("#dDelete")
    await page.wait_for_selector("#flow[open] #pGo")
    check("files?" in (await page.text_content("#flow h2")) and "Delete season files" in (await page.text_content("#pGo")),
          "the season delete dialog says what it deletes")
    await page.click("#flow [data-flow=cancel]")
    await page.click("#dShow")
    await page.wait_for_timeout(700)
    check(await page.is_visible("#dBack"), "Open show keeps a way back to the season")
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(300)
    await page.click("#fView [data-view=titles]")
    await page.wait_for_selector("#list .row:not(.skel)")

    # ---- bulk delete asks you to type the count
    await page.check("#selAll")
    n = await sel_count(page)
    await page.click("#selDelete")
    await page.wait_for_selector("#flow[open] #pGo")
    ok = await page.evaluate("FLOW.items.filter((i) => i.ok).length")
    if ok >= 10:
        check(await page.is_disabled("#pGo"), f"deleting {ok} titles waits for the typed count")
        await page.fill("#pConfirm", str(ok))
        check(await page.is_enabled("#pGo"), "typing the count enables Delete")
    check(n > 10 and "Delete" in (await page.text_content("#pGo")), f"bulk delete plans {n} titles")
    await page.click("#flow [data-flow=cancel]")
    await page.keyboard.press("Escape")

    # ---- Free up
    await page.keyboard.press("f")
    await page.wait_for_selector("#freeForm")
    await page.fill("#freeAmt", "100")
    await page.press("#freeAmt", "Enter")
    await page.wait_for_timeout(200)
    size = await page.evaluate("selectedRows().reduce((a, r) => a + r.size, 0)")
    check(size >= 100 * 1024 ** 3 and await sel_count(page) >= 1, "Free up selects at least the goal")

    # ---- Keep with Shift+K, then Undo
    keep0 = await page.text_content("#keepCount")
    await page.keyboard.press("Shift+K")
    await page.wait_for_selector("#toast.is-on button")
    await page.wait_for_timeout(300)
    keep1 = await page.text_content("#keepCount")
    check(int(keep1 or 0) > int(keep0 or 0), "Shift+K keeps the selection")
    await page.click("#toast button")
    await page.wait_for_timeout(800)
    check((await page.text_content("#keepCount")) == keep0, "Undo takes them back off the keep list")

    # ---- Leaving soon: schedule two, extend, cancel, delete now
    await page.keyboard.press("Escape")
    boxes = page.locator("#list .row .cb input")
    await boxes.nth(2).check()
    await boxes.nth(3).check()
    await page.keyboard.press("Shift+L")
    await page.wait_for_selector("#flow[open] #pGo")
    check("2 titles" in (await page.text_content("#flow h2")) and "They go on" in (await page.text_content("#pWhen")),
          "Leaving soon dialog shows the date")
    await page.click("[data-days='7']")
    check("7" == await page.input_value("#pDays"), "quick day buttons set the countdown")
    await page.click("#pGo")
    await page.wait_for_timeout(1200)
    check((await page.text_content("#leavingCount")) == "2", "two titles scheduled")
    tags = await page.locator("#list .tag.leaving").count()
    check(tags == 2, f"scheduled rows carry a leaving tag ({tags})")
    await page.click(".tab[data-tab=leaving]")
    await page.wait_for_selector("#view-leaving .prow:not(.skel)")
    check("tab=leaving" in page.url and await page.locator("#view-leaving .lv-scheduled").count() == 2, "Leaving soon tab lists them")
    await contrast(page, "Leaving soon tab")
    first = page.locator("#view-leaving .lv-scheduled").first
    await first.locator("[data-act=extend]").click()
    await page.wait_for_selector("#toast.is-on:has-text('now leaves')")
    await page.locator("#view-leaving .lv-scheduled").nth(1).locator("[data-act=cancel]").click()
    await page.wait_for_timeout(700)
    check(await page.locator("#view-leaving .lv-scheduled").count() == 1, "Don't delete takes a title off")
    await page.locator("#view-leaving .lv-scheduled").first.locator("[data-act=now]").click()
    await page.wait_for_selector("#flow[open] button[value=ok]")
    await page.click("#flow button[value=ok]")
    await page.wait_for_selector("#toast.is-on:has-text('freed')", timeout=15000)
    await page.wait_for_timeout(500)
    check(await page.locator("#view-leaving .lv-deleted").count() == 1, "Delete now deletes and shows it as deleted")
    await page.go_back()
    await page.wait_for_timeout(500)
    check("tab=" not in page.url and await page.is_visible("#view-cull"), "Back returns to the cull list")

    # ---- Review: decide, pause, close without deleting
    before = await page.locator("#list .row").count()
    await page.keyboard.press("r")
    await page.wait_for_selector("#rvCard")
    check("review=1" in page.url, "Review is in the URL")
    await contrast(page, "review card")
    await page.keyboard.press("ArrowLeft")
    await page.wait_for_timeout(350)
    await page.keyboard.press("ArrowDown")
    await page.wait_for_timeout(350)
    check("2 of" in (await page.text_content(".rv-count")) or "3 of" in (await page.text_content(".rv-count")), "arrow keys move through titles")
    await page.keyboard.press("z")
    await page.wait_for_timeout(300)
    await page.keyboard.press("Escape")
    await page.wait_for_selector(".rv-summary")
    check("1" in (await page.text_content(".rv-sum .q dd")), "the summary counts the delete queue")
    await contrast(page, "review summary")
    await page.click("[data-rv=close]")
    await page.wait_for_timeout(500)
    check("review=" not in page.url and await page.locator("#list .row").count() == before, "closing Review deletes nothing")

    # ---- History, Keep list, Settings, Help
    await page.click(".tab[data-tab=history]")
    await page.wait_for_selector("#view-history table")
    check(await page.locator("#view-history tbody tr").count() >= 1 and "freed" in (await page.text_content("#view-history .lead")),
          "History lists the deletion with space freed")
    await contrast(page, "History tab")
    await page.click(".tab[data-tab=keep]")
    await page.wait_for_selector("#view-keep .page-head")
    await contrast(page, "Keep list tab")
    await page.click(".tab[data-tab=cull]")
    await page.click("#settingsBtn")
    await page.wait_for_selector("#settings[open] #sInteg .integ")
    await contrast(page, "settings")
    await page.fill("#sDays", "21")
    await page.click("#sSave")
    await page.wait_for_timeout(600)
    check(get(FULL + "/api/settings")["leaving_days"] == 21, "settings save")
    await page.keyboard.press("?")
    check(await page.is_visible("#help"), "? opens the shortcut list")
    await page.keyboard.press("Escape")
    await page.close()


async def phone(b):
    page = await b.new_page(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, device_scale_factor=2)
    watch(page, "phone")
    await ready(page, FULL)
    overlap = await page.evaluate(OVERLAP_JS)
    check(not overlap, f"last watched and plays never overlap on a phone ({overlap[:3]})")
    wide = await page.evaluate("document.documentElement.scrollWidth - innerWidth")
    check(wide <= 0, f"no sideways scrolling on a phone ({wide}px)")
    await contrast(page, "phone list")
    await page.click("#filtersBtn")
    await page.wait_for_selector("#sheet[open]")
    await page.click("#sheet [data-group=age] .opt:has-text('2 years')")
    await page.wait_for_timeout(200)
    check("2 years" in (await page.text_content("#headline")), "the filter sheet changes the list")
    await contrast(page, "filter sheet")
    await page.click("#sheetReset")
    await page.click("#sheetDone")
    await page.locator("#list .row .cb input").nth(0).check()
    box = await page.locator("#selbar").bounding_box()
    check(box and box["x"] >= 0 and box["x"] + box["width"] <= 390, "selection bar fits the screen")
    await page.click("#selClear")
    await page.click("#list .row >> nth=0 >> .t-link")
    await page.wait_for_selector("#dTitle")
    await page.wait_for_timeout(1200)
    wide = await page.evaluate("document.querySelector('#dBody').scrollWidth - document.querySelector('#dBody').clientWidth")
    check(wide <= 0, f"the detail panel fits a phone ({wide}px)")
    await page.click("#drawer .drawer-bar [data-close]")
    await page.click("#reviewBtn")
    await page.wait_for_selector("#rvCard")
    card = await page.locator("#rvCard").bounding_box()
    await page.mouse.move(card["x"] + card["width"] / 2, card["y"] + 120)
    await page.mouse.down()
    await page.mouse.move(card["x"] + card["width"] / 2 + 200, card["y"] + 130, steps=8)
    await page.mouse.up()
    await page.wait_for_timeout(500)
    check("1" in (await page.text_content(".rv-tally .k")), "swiping right keeps a title")
    await page.click("[data-rv=undo]")
    await page.wait_for_timeout(400)
    await page.click("[data-rv=finish]")
    await page.wait_for_timeout(300)
    await page.close()


async def lite(b):
    page = await b.new_page(viewport={"width": 1280, "height": 860})
    watch(page, "tautulli-only")
    await ready(page, LITE)
    await page.locator("#list .row .cb input").nth(0).check()
    check(await page.is_hidden("#selDelete") and await page.is_hidden("#selLeaving"),
          "without Sonarr/Radarr there is no Delete or Leaving soon")
    await page.click("#selClear")
    await page.click("#list .row >> nth=0 >> .t-link")
    await page.wait_for_selector("#dTitle")
    await page.wait_for_timeout(800)
    check(await page.locator("#dDelete, #dLeave").count() == 0 and await page.locator("text=Open in Plex").count() == 0,
          "the panel hides Delete, Leaving soon and Open in Plex")
    await page.close()


def main():
    tmp = tempfile.mkdtemp(prefix="shelflife-ui-")
    procs = [subprocess.Popen([sys.executable, os.path.join(ROOT, "dev", "mock_tautulli.py")],
                              stdout=open(os.path.join(tmp, "mock.log"), "w"), stderr=subprocess.STDOUT)]
    time.sleep(2.5)
    full, lite_cfg, storage = (os.path.join(tmp, d) for d in ("full", "lite", "storage"))
    for d in (full, lite_cfg, os.path.join(storage, "Array")):
        os.makedirs(d)
    procs.append(start(8611, full, {
        "PLEX_TOKEN": "t", "SONARR_URL": f"{MOCK}/sonarr", "SONARR_API_KEY": "s", "RADARR_URL": f"{MOCK}/radarr",
        "RADARR_API_KEY": "r", "OVERSEERR_URL": f"{MOCK}/seerr", "OVERSEERR_API_KEY": "o",
        "NOTIFY_URL": f"{MOCK}/hooks/ui", "STORAGE_DIR": storage}))
    procs.append(start(8612, lite_cfg, {}))
    try:
        end = time.time() + 90
        while time.time() < end and not (synced(FULL) and synced(LITE)):
            time.sleep(0.5)
        check(bool(synced(FULL) and synced(LITE)), "both instances finish their first sync")

        async def run():
            async with async_playwright() as p:
                b = await p.chromium.launch(executable_path=os.environ.get("CHROME") or None)
                try:
                    await desktop(b)
                    await phone(b)
                    await lite(b)
                finally:
                    await b.close()
        asyncio.run(run())
        check(not problems, "no console or page errors" + (f": {problems[:5]}" if problems else ""))
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            p.wait(timeout=10)
        if not failures:
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            print("logs kept in", tmp)
    print(f"\n{len(failures)} failures" if failures else "\nAll checks passed")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
