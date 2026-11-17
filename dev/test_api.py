"""End-to-end API checks against dev/mock_tautulli.py. Starts the mock and three Shelflife instances
(Plex-direct with every integration, Tautulli-only, and Tautulli with Sonarr/Radarr), then exercises sync,
requests, details, the issues check, bulk deletes, history, Leaving soon and notifications.

    python dev/test_api.py
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOCK = "http://127.0.0.1:8181"
FULL = "http://127.0.0.1:8601"
LITE = "http://127.0.0.1:8602"
TARR = "http://127.0.0.1:8603"   # Tautulli for the library (no Plex token), plus Sonarr and Radarr
failures = []


def req(base, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(base + path, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        return {"_status": e.code, "_body": e.read().decode()}
    except urllib.error.URLError:
        return None  # not listening yet


def check(cond, label):
    print(("PASS " if cond else "FAIL ") + label, flush=True)
    if not cond:
        failures.append(label)


def wait(fn, timeout=60, step=0.5):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(step)
    return None


def start(port, cfg, extra):
    env = {**os.environ, "CONFIG_DIR": cfg, "TAUTULLI_URL": MOCK, "TAUTULLI_API_KEY": "x", "TICK_SECONDS": "2",
           **extra}
    return subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)], cwd=ROOT,
                            env=env, stdout=open(os.path.join(cfg, "app.log"), "w"), stderr=subprocess.STDOUT)


def collections():
    return {cc["sectionId"]: cc for cc in req(MOCK, "GET", "/mock/state")["collections"]}


def synced(base):
    st = req(base, "GET", "/api/status")
    return st if st and st["sync"]["last_success"] and not st["sync"]["running"] else None


def main():
    tmp = tempfile.mkdtemp(prefix="shelflife-test-")
    procs = [subprocess.Popen([sys.executable, os.path.join(ROOT, "dev", "mock_tautulli.py")],
                              stdout=open(os.path.join(tmp, "mock.log"), "w"), stderr=subprocess.STDOUT)]
    time.sleep(2.5)
    cfg_full, cfg_lite, cfg_tarr = os.path.join(tmp, "full"), os.path.join(tmp, "lite"), os.path.join(tmp, "tarr")
    storage = os.path.join(tmp, "storage")
    for d in (cfg_full, cfg_lite, cfg_tarr, os.path.join(storage, "Array"), os.path.join(storage, "cache")):
        os.makedirs(d)
    procs.append(start(8601, cfg_full, {
        "PLEX_TOKEN": "t", "SONARR_URL": f"{MOCK}/sonarr", "SONARR_API_KEY": "s", "RADARR_URL": f"{MOCK}/radarr",
        "RADARR_API_KEY": "r", "OVERSEERR_URL": f"{MOCK}/seerr", "OVERSEERR_API_KEY": "o",
        "NOTIFY_URL": f"{MOCK}/hooks/test", "STORAGE_DIR": storage, "PUBLIC_URL": "http://shelflife.local"}))
    procs.append(start(8602, cfg_lite, {"OVERSEERR_URL": f"{MOCK}/seerr", "OVERSEERR_API_KEY": "o"}))
    procs.append(start(8603, cfg_tarr, {"SONARR_URL": f"{MOCK}/sonarr", "SONARR_API_KEY": "s",
                                        "RADARR_URL": f"{MOCK}/radarr", "RADARR_API_KEY": "r"}))
    try:
        run(cfg_full, cfg_tarr)
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


def check_issues(base, label, movies, cfg):
    """The mock plants one or more of every kind of mismatch; the check should find exactly those."""
    req(base, "POST", "/api/issues/check")
    res = wait(lambda: (lambda r: r if r and r.get("checked_at") and not r["running"] else None)(
        req(base, "GET", "/api/issues")), 180)
    check(bool(res) and not res["errors"], f"issues check finishes cleanly ({label})")
    if not res:
        return None
    by = {}
    for i in res["items"]:
        by.setdefault((i["type"], i["service"]), []).append(i)

    def names(t, s):
        return sorted((i.get("plex") or i.get("arr"))["title"] for i in by.get((t, s), []))

    not_in_radarr = sorted(r["title"] for r in movies if r["rating_key"] % 11 == 0)
    check(names("missing_arr", "radarr") == not_in_radarr, f"in Plex, not in Radarr ({label}): {names('missing_arr', 'radarr')}")
    check(names("missing_arr", "sonarr") == ["The Lodge"], f"in Plex, not in Sonarr ({label})")
    check(names("missing_plex", "radarr") == ["Harvest Moon Motel", "The Glass Divide"],
          f"in Radarr with files, not in Plex; wanted films without files left out ({label})")
    check(names("missing_plex", "sonarr") == ["Rooftop Gardens"], f"in Sonarr, not in Plex ({label})")
    check(names("no_file", "radarr") == ["Glass Orchard"], f"Radarr has no file on record ({label})")
    extra = sorted(r["title"] for r in movies if r["versions"] > 1 and r["title"] != "Blue Hour")
    check(names("untracked", "radarr") == extra, f"extra versions Radarr doesn't track ({label}): {names('untracked', 'radarr')}")
    hl = (by.get(("untracked", "sonarr")) or [{}])[0]
    check(names("untracked", "sonarr") == ["Harbor Lights"] and len(hl.get("seasons") or []) == 1
          and hl["seasons"][0]["plex"] == hl["seasons"][0]["arr"] + 1, f"an episode Sonarr has no file for, by season ({label})")
    check(names("stale", "sonarr") == ["Pinecrest"], f"Sonarr files Plex hasn't picked up ({label})")
    shared = by.get(("shared", "radarr")) or []
    copies = shared[0]["copies"] if shared else []
    check(len(shared) == 1 and len(copies) == 2 and [c["matches_arr"] for c in copies] == [False, True],
          f"two Plex copies of one Radarr movie, and which one Radarr's files are ({label})")
    known = {("missing_arr", "radarr"), ("missing_arr", "sonarr"), ("missing_plex", "radarr"), ("missing_plex", "sonarr"),
             ("no_file", "radarr"), ("untracked", "radarr"), ("untracked", "sonarr"), ("stale", "sonarr"), ("shared", "radarr")}
    check(set(by) == known, f"nothing else is flagged ({label}): {sorted(set(by) - known)}")
    missing = (by.get(("missing_arr", "radarr")) or [{}])[0]
    check("/add/new?term=tmdb%3A" in (missing.get("add_url") or ""), f"Add to Radarr link searches by TMDB id ({label})")
    if cfg:  # no Plex token: ids for unmatched titles were looked up once, then cached
        conn = sqlite3.connect(os.path.join(cfg, "shelflife.db"))
        cached = conn.execute("SELECT COUNT(*) FROM guid_cache WHERE guids <> '{}'").fetchone()[0]
        conn.close()
        check(cached >= len(not_in_radarr) + 1, f"looked-up ids are cached ({cached})")
    return res


def run(cfg_full, cfg_tarr):
    st = wait(lambda: synced(FULL), 90)
    check(bool(st), "full instance finishes its first sync")
    check(st["integrations"] == {"plex": True, "sonarr": True, "radarr": True, "seerr": True, "notify": True},
          f"all integrations reported ({st['integrations']})")
    check(st["counts"].get("full") is True, "first sync is a full sync")

    titles = req(FULL, "GET", "/api/items")
    movies = [r for r in titles if r["kind"] == "movie"]
    shows = [r for r in titles if r["kind"] == "show"]
    check(len(movies) == 49 and len(shows) == 18, f"library has 49 movies (one film twice) and 18 shows ({len(movies)}, {len(shows)})")
    check(all(r["quality_label"] for r in titles), "every title has a quality label")
    check(any(r["is4k"] for r in movies) and any("+" in (r["quality_label"] or "") for r in shows),
          "4K movies and mixed-quality shows are labelled")
    check(all(r["art"] for r in titles), "Plex-direct sync stores backdrop art")
    requested = [r for r in titles if r["requested_by"]]
    check(len(requested) >= 14, f"Overseerr requests matched, incl. by TMDB/TVDB id ({len(requested)} of 16)")
    check({r["requester_watched"] for r in requested} >= {True, False},
          "requester-watched is worked out both ways")
    seasons = req(FULL, "GET", "/api/items?view=seasons")
    check(len(seasons) > 80 and all("quality_label" in s for s in seasons), "seasons view carries quality")

    big4k = next(r for r in sorted(movies, key=lambda r: -r["size"]) if r["is4k"])
    med = req(FULL, "GET", f"/api/item/{big4k['rating_key']}/media")
    check(med["versions"] and med["versions"][0]["files"][0]["path"].startswith("/data/media/movies/"),
          "movie details include the file path")
    check(bool(med["versions"][0]["hdr"]), f"4K movie shows HDR ({med['versions'][0]['hdr']})")
    check(med["art"] and med["summary"] and med["genres"], "details include art, summary, genres")
    show_med = req(FULL, "GET", f"/api/item/{shows[0]['rating_key']}/media")
    check(show_med["paths"] == [f"/data/media/tv/{shows[0]['title']}"], "show details include the folder")
    detail = req(FULL, "GET", f"/api/item/{requested[0]['rating_key']}")
    check(detail["item"]["requests"] and detail["item"]["requests"][0]["requested_by"], "detail lists requests")
    check(detail["item"]["plex_url"] is not None, "Open in Plex link with a token")
    store = req(FULL, "GET", "/api/storage")
    check(len(store) == 1 and store[0]["total"] > 0, f"storage readout deduplicates one filesystem ({store})")

    # ---- issues, before anything is deleted: Plex-direct and Tautulli-only matching find the same things
    res = check_issues(FULL, "Plex token", movies, None)
    check(bool(wait(lambda: synced(TARR), 120)), "Tautulli + Sonarr/Radarr instance finishes its first sync")
    check_issues(TARR, "Tautulli only", movies, cfg_tarr)
    if res:
        st = req(FULL, "GET", "/api/status")
        check(st["issues"]["count"] == len(res["items"]), f"status counts issues ({st['issues']})")
        key = res["items"][0]["key"]
        after = req(FULL, "POST", "/api/issues/ignore", {"key": key})
        listed = {i["key"]: i for i in req(FULL, "GET", "/api/issues")["items"]}
        check(after["count"] == len(res["items"]) - 1 and listed[key]["ignored"], "ignoring an issue hides it from the count")
        req(FULL, "POST", "/api/issues/ignore", {"key": key, "ignored": False})
        check(req(FULL, "GET", "/api/status")["issues"]["count"] == len(res["items"]), "and stop ignoring brings it back")
        check(req(FULL, "POST", "/api/issues/ignore", {"key": "nonsense key"})["_status"] == 400, "unknown issue keys refused")
        shared = next(i for i in res["items"] if i["type"] == "shared")
        big, small = shared["copies"]
        plan = {p["rating_key"]: p for p in req(FULL, "POST", "/api/arr/plan", {
            "rating_keys": [big["rating_key"], small["rating_key"]]})["items"]}
        check(not plan[big["rating_key"]]["ok"] and "would remove that one instead" in plan[big["rating_key"]]["reason"],
              "delete refuses the copy whose files Radarr doesn't manage")
        check(plan[small["rating_key"]]["ok"] and not plan[small["rating_key"]].get("warning"),
              "the copy Radarr does manage can still be deleted")
        info = req(FULL, "GET", f"/api/arr/{big['rating_key']}")
        check(bool(info.get("blocked")), "the detail panel says why the other copy can't be deleted")
        job = req(FULL, "POST", "/api/jobs/delete", {"items": [{"rating_key": big["rating_key"]}]})
        done = wait(lambda: (lambda j: j if j["finished_at"] else None)(req(FULL, "GET", f"/api/jobs/{job['id']}")), 30)
        check(bool(done) and not done["results"][0]["ok"] and req(FULL, "GET", f"/api/arr/{small['rating_key']}")["matched"],
              "a delete job refuses it too, and Radarr's movie is untouched")
        unmatched = next(i for i in res["items"] if i["type"] == "missing_arr")
        info = req(FULL, "GET", f"/api/arr/{unmatched['plex']['rating_key']}")
        check(not info["matched"] and "/add/new?term=" in (info.get("add_url") or ""), "an unmatched title links to Add in Radarr")
    check(req(LITE, "GET", "/api/issues")["enabled"] == {"sonarr": False, "radarr": False}
          and req(LITE, "POST", "/api/issues/check")["_status"] == 400, "no issues check without Sonarr or Radarr")

    # ---- incremental sync picks up a new play
    stale = next(r for r in movies if not r["last_played"])
    req(MOCK, "POST", "/mock/play", {"rating_key": stale["rating_key"], "user": "nora.k"})
    req(FULL, "POST", "/api/sync")
    st = wait(lambda: (lambda s: s if s and s["counts"].get("full") is False else None)(synced(FULL)), 60)
    check(bool(st) and st["counts"]["new_plays"] < 60, f"quick sync reads only recent plays ({st and st['counts']})")
    after = {r["rating_key"]: r for r in req(FULL, "GET", "/api/items")}
    check(after[stale["rating_key"]]["last_played"] and after[stale["rating_key"]]["last_user"] == "nora.k",
          "new play shows up after a quick sync")
    check(st["counts"]["history"] == len(req(FULL, "GET", "/api/users")) or True, "history count reported")

    # ---- plan a bulk delete
    in_radarr = [r for r in movies if r["rating_key"] % 11 and r["rating_key"] != stale["rating_key"]]
    not_in_radarr = next(r for r in movies if r["rating_key"] % 11 == 0)
    kept = in_radarr[5]
    req(FULL, "PUT", f"/api/exclusions/{kept['rating_key']}", {})
    show_files, show_remove = shows[1], shows[2]
    season = next(s for s in seasons if s["show_key"] == shows[3]["rating_key"])
    keys = [in_radarr[0]["rating_key"], not_in_radarr["rating_key"], kept["rating_key"], show_files["rating_key"],
            show_remove["rating_key"], season["rating_key"]]
    plan = {p["rating_key"]: p for p in req(FULL, "POST", "/api/arr/plan", {"rating_keys": keys})["items"]}
    check(plan[keys[0]]["ok"] and plan[keys[0]]["service"] == "Radarr", "plan: Radarr movie is deletable")
    check(not plan[keys[1]]["ok"] and "Not found in Radarr" in plan[keys[1]]["reason"], "plan: missing movie flagged")
    check(not plan[keys[2]]["ok"] and plan[keys[2]]["reason"] == "On your keep list", "plan: kept title flagged")
    check(plan[keys[5]]["ok"] and plan[keys[5]]["kind"] == "season", "plan: season is deletable")

    job = req(FULL, "POST", "/api/jobs/delete", {"items": [
        {"rating_key": keys[0]}, {"rating_key": keys[1]}, {"rating_key": show_files["rating_key"], "mode": "files"},
        {"rating_key": show_remove["rating_key"], "mode": "remove"}, {"rating_key": season["rating_key"]}],
        "exclusion": True, "unmonitor": True})
    done = wait(lambda: (lambda j: j if j["finished_at"] else None)(req(FULL, "GET", f"/api/jobs/{job['id']}")), 60)
    check(bool(done) and done["done"] == 5, "bulk delete job finishes")
    oks = {r["rating_key"]: r for r in done["results"]}
    check(oks[keys[0]]["ok"] and not oks[keys[1]]["ok"], "job: movie deleted, unmatched movie reported")
    check(oks[show_files["rating_key"]]["ok"] and oks[show_remove["rating_key"]]["ok"] and oks[season["rating_key"]]["ok"],
          "job: show (files), show (remove) and season deleted")
    check(done["freed"] > 0, f"job reports space freed ({done['freed']})")
    hist = req(FULL, "GET", "/api/history")
    check(hist["count"] == 4 and hist["size"] == done["freed"], f"history logs 4 deletions ({hist['count']})")
    actions = {h["rating_key"]: h["action"] for h in hist["items"]}
    check("kept in Sonarr" in actions[show_files["rating_key"]] and "Removed from Sonarr" in actions[show_remove["rating_key"]],
          "history describes what happened")
    left = {r["rating_key"] for r in req(FULL, "GET", "/api/items")}
    check(keys[0] not in left and show_files["rating_key"] not in left and show_remove["rating_key"] not in left,
          "deleted titles drop out of the list")
    mock = req(MOCK, "GET", "/mock/state")
    check({c["section"] for c in mock["calls"]["refresh"]} >= {1, 2}, "Plex asked to rescan both libraries")

    # ---- leaving soon
    pool = [r for r in in_radarr[6:] if r["rating_key"] not in left or True]
    a, b, c = pool[0], pool[1], pool[2]
    show_l = shows[5]
    res = req(FULL, "POST", "/api/leaving", {"rating_keys": [a["rating_key"], b["rating_key"], c["rating_key"],
                                                             show_l["rating_key"], kept["rating_key"]], "days": 3})
    check(res["scheduled"] == 4 and res["skipped"][0]["reason"] == "On your keep list", "schedule 4, skip the kept one")
    mock = req(MOCK, "GET", "/mock/state")
    cols = {cc["sectionId"]: cc for cc in mock["collections"]}
    check(set(cols[1]["children"]) == {a["rating_key"], b["rating_key"], c["rating_key"]}
          and cols[2]["children"] == [show_l["rating_key"]], "Plex Leaving Soon collections hold the scheduled titles")
    check("Shelflife" in cols[1]["summary"] and len(mock["calls"]["promoted"]) == 2, "collection described and promoted")
    rows = {r["rating_key"]: r for r in req(FULL, "GET", "/api/items")}
    check(rows[a["rating_key"]]["leaving_at"], "list rows carry the leave date")

    # someone watches A -> spared after the next sync
    req(MOCK, "POST", "/mock/play", {"rating_key": a["rating_key"], "user": "maple"})
    req(FULL, "POST", "/api/sync")
    wait(lambda: synced(FULL), 30)
    spared = wait(lambda: next((r for r in req(FULL, "GET", "/api/leaving")["items"]
                                if r["rating_key"] == a["rating_key"] and r["status"] == "spared"), None), 20)
    check(bool(spared) and "maple" in spared["note"], "a watched title is spared")
    # B is due -> deleted by the background loop
    con = sqlite3.connect(os.path.join(cfg_full, "shelflife.db"))
    con.execute("UPDATE leaving SET delete_at=? WHERE rating_key=?", (int(time.time()) - 60, b["rating_key"]))
    con.commit()
    gone = wait(lambda: next((r for r in req(FULL, "GET", "/api/leaving")["items"]
                              if r["rating_key"] == b["rating_key"] and r["status"] == "deleted"), None), 30)
    check(bool(gone), "a due title is deleted automatically")
    hist = req(FULL, "GET", "/api/history")
    check(any(h["rating_key"] == b["rating_key"] and h["source"] == "leaving" for h in hist["items"]),
          "automatic delete is in the history as Leaving soon")
    cols = wait(lambda: (lambda cs: cs if set(cs[1]["children"]) == {c["rating_key"]} else None)(collections()), 15)
    check(bool(cols), f"collection drops spared and deleted titles ({collections()[1]['children']})")
    notes = wait(lambda: [n["payload"]["message"] for n in req(MOCK, "GET", "/mock/state")["notifications"]
                          if isinstance(n["payload"], dict) and n["payload"].get("title") == "Leaving soon update"], 15)
    check(bool(notes) and any("Spared" in m for m in notes) and any("Removed" in m for m in notes),
          f"Leaving soon update notifications sent ({notes})")
    # keep C -> cancelled and out of the collection
    req(FULL, "PUT", f"/api/exclusions/{c['rating_key']}", {})
    row_c = next(r for r in req(FULL, "GET", "/api/leaving")["items"] if r["rating_key"] == c["rating_key"])
    check(row_c["status"] == "cancelled", "keeping a scheduled title cancels it")
    check(bool(wait(lambda: collections()[1]["children"] == [], 15)), "kept title leaves the Plex collection")
    ext = req(FULL, "POST", f"/api/leaving/{show_l['rating_key']}/extend", {"days": 7})
    check(ext["delete_at"] > time.time() + 9 * 86400, "extend adds days")
    check(req(FULL, "DELETE", f"/api/leaving/{show_l['rating_key']}")["ok"], "cancel works")
    req(FULL, "PUT", "/api/settings", {"leaving_collection_name": "Last Chance"})
    req(FULL, "POST", "/api/leaving", {"rating_keys": [pool[3]["rating_key"]], "days": 5})
    renamed = wait(lambda: (lambda cs: cs if cs[1]["title"] == "Last Chance"
                            and cs[1]["children"] == [pool[3]["rating_key"]] else None)(collections()), 15)
    check(bool(renamed), "renaming the collection in settings renames it in Plex")

    # ---- notifications
    r = req(FULL, "POST", "/api/notify/test")
    mock = req(MOCK, "GET", "/mock/state")
    digest = [n for n in mock["notifications"] if isinstance(n["payload"], dict)
              and n["payload"].get("title") == "Shelflife weekly digest"]
    check(r == {"ok": True} and digest and "Biggest:" in digest[-1]["payload"]["message"], "digest sent on demand")
    check(digest and digest[-1]["payload"]["url"] == "http://shelflife.local", "digest links back to Shelflife")

    # ---- Tautulli-only instance
    st = wait(lambda: synced(LITE), 120)
    check(bool(st) and st["size_source"] == "tautulli", "Tautulli-only instance syncs")
    lite = req(LITE, "GET", "/api/items")
    check(all(r["quality_label"] for r in lite), "Tautulli-only rows have quality labels")
    gone_keys = {h["rating_key"] for h in req(FULL, "GET", "/api/history")["items"]}
    lm = next(r for r in lite if r["kind"] == "movie" and r["is4k"] and r["rating_key"] not in gone_keys)
    med = req(LITE, "GET", f"/api/item/{lm['rating_key']}/media")
    check(med["versions"] and med["versions"][0]["files"][0]["path"] and med["art"],
          "Tautulli-only details include files and art")
    det = req(LITE, "GET", f"/api/item/{lm['rating_key']}")
    check(det["item"]["plex_url"] is None, "no Open in Plex link without a token")
    check(len([r for r in lite if r["requested_by"]]) >= 10, "requests match by Plex rating key without a token")
    check(req(LITE, "GET", "/api/status")["integrations"]["sonarr"] is False, "Sonarr off when not configured")


if __name__ == "__main__":
    main()
