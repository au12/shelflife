"use strict";
/* Start-up, URL state and the back button, sync status, storage, settings, keyboard shortcuts. */

const TABS = ["cull", "leaving", "keep", "history"];

/* ------------------------------------------------------------ URL state */

function parseParam(k, v) {
  v = String(v ?? "");
  switch (k) {
    case "age": case "grace": { const n = parseInt(v, 10); return Number.isFinite(n) && n >= 0 && n <= 3650 ? n : DEFAULTS[k]; }
    case "kept": return v === "1" || v === "true";
    case "view": return v === "seasons" ? "seasons" : "titles";
    case "never": return ["include", "exclude", "only"].includes(v) ? v : DEFAULTS.never;
    case "sort": return SORTS[v] ? v : DEFAULTS.sort;
    case "dir": return v === "asc" ? "asc" : "desc";
    case "bucket": return BUCKETS.some((b) => b.id === v) ? v : "";
    case "req": return ["req", "unwatched", "none"].includes(v) ? v : "";
    case "lib": return /^\d+$/.test(v) ? v : "";
    default: return v.slice(0, 200);
  }
}

function readUrl() {
  const p = new URLSearchParams(location.search);
  const f = { ...DEFAULTS };
  let any = false;
  for (const k of Object.keys(DEFAULTS)) if (p.has(k)) { any = true; f[k] = parseParam(k, p.get(k)); }
  if (!p.has("dir")) f.dir = SORTS[f.sort].dir;
  const tab = TABS.includes(p.get("tab")) ? p.get("tab") : "cull";
  return { tab, f, any, item: parseInt(p.get("item"), 10) || null, review: p.get("review") === "1" };
}

function urlFor(extra = {}) {
  const p = new URLSearchParams();
  const tab = extra.tab || S.tab;
  if (tab !== "cull") p.set("tab", tab);
  else {
    for (const k of Object.keys(DEFAULTS)) {
      if (k === "dir") { if (S.f.dir !== SORTS[S.f.sort].dir) p.set("dir", S.f.dir); continue; }
      if (S.f[k] === DEFAULTS[k]) continue;
      p.set(k, k === "kept" ? "1" : S.f[k]);
    }
  }
  const item = "item" in extra ? extra.item : S.openKey;
  if (item) p.set("item", item);
  if ("review" in extra ? extra.review : S.reviewOpen) p.set("review", "1");
  const q = p.toString();
  return location.pathname + (q ? `?${q}` : "");
}

let routing = false;   // while following the back button, the URL is already right
function writeUrl() { if (!routing) history.replaceState(history.state, "", urlFor()); }
function replaceUrl(extra) { history.replaceState(history.state, "", urlFor(extra)); }
function pushUrl(extra) {
  const st = history.state || {};
  history.pushState({ d: (st.d || 0) + 1, review: !!extra.review }, "", urlFor(extra));
}

async function applyRoute() {
  const r = readUrl();
  let filtersChanged = false;
  routing = true;
  try {
    if (r.tab !== S.tab) showTab(r.tab);
    if (!r.review && S.reviewOpen) closeReview(true);
    if (r.item) { if (S.openKey !== r.item) openDetail(r.item); else updateNav(); }
    else if (S.openKey) closeDetail();
    filtersChanged = r.tab === "cull" && Object.keys(DEFAULTS).some((k) => S.f[k] !== r.f[k]);
    if (filtersChanged) { S.f = r.f; $("#fSearch").value = S.f.q; }
  } finally { routing = false; }
  if (filtersChanged) { await ensureView(); apply(); }
}

/* ------------------------------------------------------------ status + sync */

function since(ts) {
  const s = now() - ts;
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return ago(ts).toLowerCase();
}

async function loadStatus() {
  S.status = await api("/api/status");
  renderSync();
  $("#keepCount").textContent = S.status.keep_count || "";
  $("#leavingCount").textContent = S.status.leaving.scheduled || "";
  return S.status;
}

function renderSync() {
  const st = S.status;
  if (!st) return;
  const el = $("#syncText");
  const running = st.sync.running;
  $("#syncBtn").classList.toggle("spin", running);
  $("#syncBtn").disabled = running || !st.configured;
  $("#syncBar").hidden = !running;
  el.classList.toggle("is-err", !!st.sync.last_error && !running);
  if (!st.configured) el.textContent = "Not connected";
  else if (running) el.textContent = `${st.sync.phase || "Syncing"}${st.sync.detail ? `: ${st.sync.detail}` : ""}`;
  else if (st.sync.last_error) el.textContent = "Last sync failed";
  else el.textContent = st.sync.last_success ? `Synced ${since(st.sync.last_success)}` : "Never synced";
  el.title = st.sync.last_error || (st.sync.last_success ? `Last synced ${ymd(st.sync.last_success)} ${hm(st.sync.last_success)}` : "");
}

let polling = false;
async function pollSync() {
  if (polling) return;
  polling = true;
  try {
    for (;;) {
      await sleep(1500);
      const st = await loadStatus();
      if (!st.sync.running) break;
    }
    if (S.status.sync.last_error) toast(`Sync failed: ${S.status.sync.last_error}`, null, true);
    else {
      const c = S.status.counts || {};
      toast(c.new_plays ? `Synced. ${plural(c.new_plays, "new play")}.` : "Synced");
      await loadAll();
    }
  } catch (e) {
    fail(e);
  } finally { polling = false; }
}

async function startSync(full = false) {
  try {
    await post(`/api/sync${full ? "?full=true" : ""}`);
    await loadStatus();
    renderEmpty();
    pollSync();
  } catch (e) { fail(e); }
}

/* ------------------------------------------------------------ storage */

async function loadStorage() {
  try { S.storage = await api("/api/storage"); } catch (_) { S.storage = []; }
  const el = $("#storage");
  if (!S.storage.length) { el.hidden = true; return; }
  el.hidden = false;
  el.innerHTML = S.storage.slice(0, 3).map((s) => {
    const pct = (s.used / s.total) * 100;
    const level = pct >= 92 ? " crit" : pct >= 80 ? " warn" : "";
    return `<div class="disk${level}" title="${esc(s.name)}: ${bytes(s.used)} used of ${bytes(s.total)}">
      <div class="disk-top"><span class="disk-name">${icon("disk")}${esc(s.name)}</span><span class="disk-free"><b>${bytes(s.free)}</b> free</span></div>
      <div class="disk-bar" role="meter" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(pct)}" aria-label="${esc(s.name)} used"><span style="width:${pct.toFixed(1)}%"></span></div></div>`;
  }).join("");
}

/* ------------------------------------------------------------ settings */

async function openSettings() {
  const dlg = $("#settings");
  let settings, users, st;
  try {
    [settings, users, st] = await Promise.all([api("/api/settings"), api("/api/users"), loadStatus()]);
  } catch (e) { fail(e); return; }
  S.settings = settings;
  const ignored = new Set(settings.ignored_users);
  $("#sUsers").innerHTML = users.length
    ? users.map((u) => `<label><input type="checkbox" value="${u.user_id}" ${ignored.has(u.user_id) ? "checked" : ""}>
        <span>${esc(u.friendly_name)}</span><span class="n">${plural(u.plays, "play")}, last ${ago(u.last_played).toLowerCase()}</span></label>`).join("")
    : `<p class="hint">No one yet. People appear after the first sync.</p>`;
  $("#sMin").value = settings.min_play_minutes;
  $("#sDays").value = settings.leaving_days;
  const plex = st.integrations.plex;
  $("#sCollection").innerHTML = `
    <label class="switch"><input type="checkbox" id="sCol" ${settings.leaving_collection ? "checked" : ""} ${plex ? "" : "disabled"}><span class="knob"></span>List them in a Plex collection</label>
    <div class="sub-opts" id="sColOpts">
      <label class="inline">Collection name <input type="text" id="sColName" maxlength="60" value="${esc(settings.leaving_collection_name)}" ${plex ? "" : "disabled"}></label>
      <label class="switch"><input type="checkbox" id="sPromote" ${settings.leaving_promote ? "checked" : ""} ${plex ? "" : "disabled"}><span class="knob"></span>Show it on Plex Home for everyone</label>
    </div>
    ${plex ? "" : `<p class="hint">Needs a Plex token (<code>PLEX_TOKEN</code>).</p>`}`;
  const syncCol = () => { $("#sColOpts").hidden = !$("#sCol").checked; };
  $("#sCol").addEventListener("change", syncCol);
  syncCol();

  const src = st.size_source === "plex"
    ? "File sizes come straight from Plex and count every version of a movie."
    : "File sizes come through Tautulli. Adding <code>PLEX_TOKEN</code> makes syncs much faster for big TV libraries and counts every version of a movie.";
  $("#sSync").innerHTML = `<p class="hint">${st.sync.last_success ? `Last synced ${esc(fullDate(st.sync.last_success))} at ${hm(st.sync.last_success)}.` : "Not synced yet."}
      A quick sync reads new plays every ${st.interval_hours} hours; a full sync rereads everything every ${st.full_sync_hours} hours. ${src}</p>
    ${st.sync.last_error ? `<p class="err">${icon("alert")}${esc(st.sync.last_error)}</p>` : ""}
    <div class="row-btns"><button class="btn ghost small" type="button" id="sSyncQuick" ${st.sync.running ? "disabled" : ""}>${icon("sync")}Sync now</button>
      <button class="btn ghost small" type="button" id="sSyncFull" ${st.sync.running ? "disabled" : ""}>Full sync</button></div>`;
  $("#sNotify").innerHTML = st.integrations.notify
    ? `<p class="hint">${st.digest ? `Sent ${esc(st.digest)}: ` : "Turned off with DIGEST_DAY. "}the biggest titles nobody has watched in a year, what's leaving this week, and the space freed. Leaving soon deletions are reported as they happen.</p>
       <div class="row-btns"><button class="btn ghost small" type="button" id="sDigest">${icon("send")}Send one now</button></div>`
    : `<p class="hint">Set <code>NOTIFY_URL</code> to a Discord or Slack webhook, an ntfy topic, or any URL that accepts JSON to get a weekly summary and Leaving soon updates.</p>`;
  $("#sVersion").textContent = `Shelflife ${st.version}`;
  $("#sLogout").hidden = !st.auth;
  $("#sInteg").innerHTML = `<p class="hint">Checking…</p>`;
  api("/api/integrations").then((ig) => {
    const line = (name, v, off) => `<li><span class="iname">${name}</span>${!v.enabled ? `<span class="muted">${off}</span>`
      : v.ok === false ? `<span class="bad">${icon("alert")}${esc(v.error)}</span>`
        : `<span class="good">${icon("check")}Connected${v.version ? ` <small>v${esc(v.version)}</small>` : ""}</span>`}</li>`;
    $("#sInteg").innerHTML = `<ul class="integ">
      <li><span class="iname">Tautulli</span>${st.configured ? `<span class="good">${icon("check")}Connected</span>` : `<span class="bad">${icon("alert")}Set TAUTULLI_URL and TAUTULLI_API_KEY</span>`}</li>
      ${line("Plex", { ...ig.plex, ok: ig.plex.enabled ? true : undefined }, "Optional. PLEX_TOKEN gives faster syncs, Open in Plex, and the Leaving Soon collection.")}
      ${line("Sonarr", ig.sonarr, "Optional. SONARR_URL and SONARR_API_KEY let you delete shows and change monitoring.")}
      ${line("Radarr", ig.radarr, "Optional. RADARR_URL and RADARR_API_KEY let you delete movies.")}
      ${line("Overseerr", ig.seerr, "Optional. OVERSEERR_URL and OVERSEERR_API_KEY (Jellyseerr works too) show who asked for what.")}
      <li><span class="iname">Free space</span>${ig.storage.enabled ? `<span class="good">${icon("check")}Reading ${esc(ig.storage.dir)}</span>` : `<span class="muted">Optional. Mount a share read-only under ${esc(ig.storage.dir)} (for example /mnt/user to ${esc(ig.storage.dir)}/Array) to see free space.</span>`}</li>
    </ul>`;
  }).catch((e) => { $("#sInteg").innerHTML = `<p class="err">${esc(e.message)}</p>`; });
  dlg.showModal();
}

async function saveSettings() {
  const body = {
    ignored_users: $$("#sUsers input:checked").map((i) => +i.value),
    min_play_minutes: +$("#sMin").value || 0,
    leaving_days: +$("#sDays").value || 14,
  };
  if (!$("#sCol").disabled) {
    body.leaving_collection = $("#sCol").checked;
    body.leaving_collection_name = $("#sColName").value;
    body.leaving_promote = $("#sPromote").checked;
  }
  try {
    S.settings = await put("/api/settings", body);
    toast("Settings saved");
    await loadAll();
  } catch (e) { fail(e); }
}

function bindSettings() {
  $("#settingsBtn").addEventListener("click", openSettings);
  $("#settingsForm").addEventListener("submit", (e) => {
    if (e.submitter && e.submitter.value === "save") saveSettings();
  });
  $("#settings").addEventListener("click", async (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    if (b.id === "sSyncQuick" || b.id === "sSyncFull") {
      $("#settings").close();
      startSync(b.id === "sSyncFull");
    } else if (b.id === "sDigest") {
      b.disabled = true;
      try { await post("/api/notify/test"); toast("Digest sent"); } catch (err) { fail(err); }
      b.disabled = false;
    } else if (b.id === "sLogout") {
      await fetch("/api/logout", { method: "POST" });
      location.href = "/login";
    }
  });
  $("#syncBtn").addEventListener("click", () => startSync());
  $("#helpBtn").addEventListener("click", () => $("#help").showModal());
  $("#help").addEventListener("click", (e) => { if (e.target.closest("[data-close]") || e.target === $("#help")) $("#help").close(); });
}

/* ------------------------------------------------------------ keyboard */

function onKey(e) {
  if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey) return;
  if (S.reviewOpen) { reviewKey(e); return; }
  if ($("dialog[open]")) return;
  const t = e.target;
  const typing = t.closest && t.closest("input, textarea, select, [contenteditable]") && !["checkbox", "radio"].includes(t.type);
  if (typing) return;
  const drawerOpen = !!S.openKey;
  const selected = S.selected.size ? [...S.selected] : null;
  switch (e.key) {
    case "/":
      e.preventDefault();
      if (drawerOpen) closeItem();
      if (S.tab !== "cull") showTab("cull", { push: true });
      $("#fSearch").focus();
      $("#fSearch").select();
      break;
    case "?": $("#help").showModal(); break;
    case "j": case "k":
      e.preventDefault();
      if (drawerOpen) stepDetail(e.key === "j" ? 1 : -1);
      else if (S.tab === "cull") moveFocus(e.key === "j" ? 1 : -1);
      break;
    case "x": {
      const r = !drawerOpen && focusedRow();
      if (r) toggleSelect(r.rating_key, !S.selected.has(r.rating_key));
      break;
    }
    case "o": {
      const r = !drawerOpen && focusedRow();
      if (r) openItem(r.rating_key);
      break;
    }
    case "Escape":
      if (!$("#pop").hidden) closePop(true);
      else if (drawerOpen) closeItem();
      else if (S.selected.size && S.tab === "cull") clearSelection();
      break;
    case "r": if (S.tab === "cull" && !drawerOpen) startReview(selected ? new Set(selected) : null); break;
    case "f": if (S.tab === "cull" && !drawerOpen && !$("#freeBtn").disabled) { e.preventDefault(); openFreeUp(); } break;
    case "K":
      if (drawerOpen) { const b = $("#dKeep"); if (b) b.click(); }
      else if (selected && S.tab === "cull") keepSelection();
      break;
    case "L":
      if (drawerOpen) { const b = $("#dLeave"); if (b) b.click(); }
      else if (selected && S.tab === "cull" && canDelete()) openPlan(selected, "leaving");
      break;
    case "Delete": case "#": {
      if (drawerOpen) { const b = $("#dDelete"); if (b && !b.disabled) b.click(); break; }
      if (S.tab !== "cull" || !canDelete()) break;
      const keys = selected || (focusedRow() ? [focusedRow().rating_key] : null);
      if (keys) openPlan(keys, "delete", { source: keys.length > 1 ? "bulk" : "manual" });
      break;
    }
    default:
  }
}

/* ------------------------------------------------------------ start */

async function loadAll() {
  const [libs, titles] = await Promise.all([api("/api/libraries"), api("/api/items"), loadStatus()]);
  S.libraries = libs;
  S.titles = titles;
  if (S.f.lib && !libs.some((l) => String(l.section_id) === S.f.lib)) S.f.lib = "";
  if (!seasonsAllowed()) S.f.view = "titles";
  S.seasons = S.f.view === "seasons" ? await api("/api/items?view=seasons") : null;
  S.loaded = true;
  apply();
}

function measureTop() {
  const h = $("#top").getBoundingClientRect().height;
  document.documentElement.style.setProperty("--top-h", `${Math.round(h)}px`);
}

(async function init() {
  const r = readUrl();
  const saved = store.get("filters", null);
  if (r.any) S.f = r.f;
  else if (saved) for (const k of Object.keys(DEFAULTS)) if (k in saved) S.f[k] = parseParam(k, saved[k] === true ? "1" : saved[k] === false ? "" : saved[k]);
  S.f.q = r.f.q || "";
  $("#fSearch").value = S.f.q;
  S.tab = r.tab;
  S.openKey = r.item;   // opened once the data is in
  history.replaceState({ d: 0 }, "", urlFor({ item: r.item, review: false }));

  bindList();
  bindDetail();
  bindFlows();
  bindReview();
  bindTabs();
  bindSettings();
  document.addEventListener("keydown", onKey);
  addEventListener("popstate", () => { applyRoute().catch(fail); });
  measureTop();
  new ResizeObserver(measureTop).observe($("#top"));

  renderSkeleton();
  renderChips();
  renderSortHead();
  if (S.tab !== "cull") showTab(S.tab);
  try {
    const [, settings] = await Promise.all([loadStatus(), api("/api/settings")]);
    S.settings = settings;
    await loadAll();
    loadStorage();
    if (r.item) openDetail(r.item);
    if (S.status.sync.running) pollSync();
  } catch (e) {
    S.loaded = true;
    S.openKey = null;
    $("#list").innerHTML = "";
    $("#empty").hidden = false;
    $("#empty").innerHTML = `<h2>Can't reach Shelflife</h2><p>${esc(e.message)}</p>`;
  }
  setInterval(() => { if (!polling && !document.hidden) loadStatus().catch(() => {}); }, 60000);
})();
