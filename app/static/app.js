"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const DAY = 86400;
const PAGE = 120;

const S = {
  status: null,
  libraries: [],
  titles: [],
  seasons: null,
  filtered: [],
  rendered: 0,
  maxSize: 1,
  selected: new Set(),
  lastClicked: null,
  stack: [],
  f: { lib: "", view: "titles", age: 365, never: "include", grace: 30, q: "", sort: "size", rev: false, showKept: false },
};

/* ------------------------------------------------------------ utils */

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const now = () => Date.now() / 1000;

function bytes(n) {
  if (!n) return "0 GB";
  const tb = n / 1024 ** 4;
  if (tb >= 1) return `${tb.toFixed(tb >= 10 ? 1 : 2)} TB`;
  const gb = n / 1024 ** 3;
  if (gb >= 1) return `${gb.toFixed(gb >= 100 ? 0 : 1)} GB`;
  return `${Math.max(1, Math.round(n / 1024 ** 2))} MB`;
}

function ago(ts) {
  if (!ts) return "Never";
  const d = (now() - ts) / DAY;
  if (d < 1) return "Today";
  if (d < 2) return "Yesterday";
  if (d < 14) return `${Math.floor(d)} days ago`;
  if (d < 60) return `${Math.floor(d / 7)} weeks ago`;
  if (d < 365) return `${Math.floor(d / 30.4)} months ago`;
  const y = d / 365.25;
  return `${y < 10 ? y.toFixed(1) : Math.round(y)} years ago`;
}

const pad = (n) => String(n).padStart(2, "0");
function ymd(ts) { if (!ts) return "—"; const d = new Date(ts * 1000); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; }
function hm(ts) { if (!ts) return "—"; const d = new Date(ts * 1000); return `${pad(d.getHours())}:${pad(d.getMinutes())}`; }
function monthYear(ts) { return ts ? new Date(ts * 1000).toLocaleDateString(undefined, { month: "short", year: "numeric" }) : "—"; }
const mins = (s) => `${Math.round((s || 0) / 60)} mins`;
const plural = (n, w, p) => `${n.toLocaleString()} ${n === 1 ? w : p || w + "s"}`;

// Size-bar tint: sage when recently watched, drying to straw as it ages, violet if never watched.
const FRESH = [125, 183, 154], STALE = [212, 180, 103];
function tint(lp) {
  if (!lp) return "var(--never)";
  const days = Math.max(1, (now() - lp) / DAY);
  const t = Math.min(1, Math.max(0, Math.log(days / 30) / Math.log(1095 / 30)));
  const c = FRESH.map((v, i) => Math.round(v + (STALE[i] - v) * t));
  return `rgb(${c.join(",")})`;
}

function posterUrl(thumb, w = 120) { return thumb ? `/api/poster?w=${w}&img=${encodeURIComponent(thumb)}` : ""; }
function posterImg(thumb, w, cls = "poster") {
  return thumb
    ? `<img class="${cls}" src="${posterUrl(thumb, w)}" alt="" loading="lazy" onerror="this.removeAttribute('src')">`
    : `<span class="${cls}"></span>`;
}

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (r.status === 401 && path !== "/api/login") {
    location.href = `/login?next=${encodeURIComponent(location.pathname + location.search)}`;
    throw new Error("Signed out");
  }
  if (!r.ok) {
    let msg = `${r.status}`;
    try { msg = (await r.json()).detail || msg; } catch (_) { /* not json */ }
    throw new Error(msg);
  }
  return r.json();
}

let toastTimer;
function toast(msg, undo) {
  const t = $("#toast");
  t.innerHTML = `<span>${esc(msg)}</span>${undo ? '<button type="button">Undo</button>' : ""}`;
  if (undo) $("button", t).onclick = () => { t.classList.remove("is-on"); undo(); };
  t.classList.add("is-on");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("is-on"), undo ? 6000 : 3000);
}

function saveFilters() { try { localStorage.setItem("shelflife.filters", JSON.stringify(S.f)); } catch (_) { /* storage off */ } }
function loadFilters() {
  try { Object.assign(S.f, JSON.parse(localStorage.getItem("shelflife.filters") || "{}")); } catch (_) { /* storage off */ }
}

/* ------------------------------------------------------------ data */

async function loadStatus() {
  S.status = await api("/api/status");
  renderSync();
  return S.status;
}

async function loadData() {
  const [libs, titles] = await Promise.all([api("/api/libraries"), api("/api/items")]);
  S.libraries = libs;
  S.titles = titles;
  S.seasons = null;
  if (S.f.view === "seasons") S.seasons = await api("/api/items?view=seasons");
  renderLibraries();
  refreshKeepCount();
  apply();
}

async function refreshKeepCount() {
  try {
    const k = await api("/api/exclusions");
    $("#keepCount").textContent = k.length ? k.length : "";
    S.keepCache = k;
  } catch (_) { /* ignore */ }
}

/* ------------------------------------------------------------ filters */

function renderLibraries() {
  const sel = $("#fLib");
  const opts = [`<option value="">All libraries</option>`].concat(
    S.libraries.map((l) => `<option value="${l.section_id}">${esc(l.name)}</option>`));
  sel.innerHTML = opts.join("");
  if (!S.libraries.some((l) => String(l.section_id) === String(S.f.lib))) S.f.lib = "";
  sel.value = S.f.lib;
  syncControls();
}

function syncControls() {
  const lib = S.libraries.find((l) => String(l.section_id) === String(S.f.lib));
  const movieOnly = lib ? lib.type === "movie" : !S.libraries.some((l) => l.type === "show");
  if (movieOnly && S.f.view === "seasons") S.f.view = "titles";
  $$(".seg button").forEach((b) => {
    b.setAttribute("aria-checked", String(b.dataset.view === S.f.view));
    if (b.dataset.view === "seasons") b.disabled = movieOnly;
  });
  $("#fAge").value = String(S.f.age);
  $("#fNever").value = S.f.never;
  $("#fGrace").value = String(S.f.grace);
  $("#fSearch").value = S.f.q;
  $("#fSort").value = S.f.sort;
  $("#fDir").classList.toggle("is-asc", S.f.rev);
  $("#fShowKept").checked = S.f.showKept;
}

const SORTS = {
  size: { key: (r) => r.size, desc: true },
  last: { key: (r) => r.last_played || 0, desc: false },
  added: { key: (r) => r.added_at || 0, desc: false },
  plays: { key: (r) => r.plays, desc: false },
  title: { key: (r) => (r.kind === "season" ? `${r.show_title} ${String(r.season_index).padStart(3, "0")}` : r.sort_title || r.title).toLowerCase(), desc: false },
};

function apply() {
  const f = S.f;
  const pool = f.view === "seasons" ? S.seasons || [] : S.titles;
  const cutoff = f.age > 0 ? now() - f.age * DAY : Infinity;
  const graceCut = f.grace > 0 ? now() - f.grace * DAY : Infinity;
  const q = f.q.trim().toLowerCase();
  let hiddenKept = 0;

  const out = pool.filter((r) => {
    if (f.lib && String(r.section_id) !== String(f.lib)) return false;
    const never = !r.last_played;
    if (f.never === "exclude" && never) return false;
    if (f.never === "only" && !never) return false;
    if (!never && r.last_played >= cutoff) return false;
    if (r.added_at && r.added_at >= graceCut) return false;
    if (q && !`${r.title} ${r.show_title || ""}`.toLowerCase().includes(q)) return false;
    if (r.excluded && !f.showKept) { hiddenKept++; return false; }
    return true;
  });

  const s = SORTS[f.sort] || SORTS.size;
  const dir = (s.desc ? -1 : 1) * (f.rev ? -1 : 1);
  out.sort((a, b) => {
    const x = s.key(a), y = s.key(b);
    return (x < y ? -1 : x > y ? 1 : 0) * dir || b.size - a.size;
  });

  S.filtered = out;
  S.hiddenKept = hiddenKept;
  S.maxSize = Math.max(1, ...out.map((r) => r.size));
  for (const k of [...S.selected]) if (!out.some((r) => r.rating_key === k)) S.selected.delete(k);
  saveFilters();
  renderHeadline();
  const keepRendered = S.rendered;
  renderList(true);
  while (S.rendered < Math.min(keepRendered, S.filtered.length)) renderList(false);
  renderSelbar();
}

function renderHeadline() {
  const f = S.f;
  const total = S.filtered.reduce((a, r) => a + r.size, 0);
  const noun = f.view === "seasons" ? "season" : "title";
  const ageTxt = { 30: "the last month", 91: "the last 3 months", 182: "the last 6 months", 365: "the last year", 730: "the last 2 years", 1095: "the last 3 years", 1825: "the last 5 years" }[f.age];
  let tail = "";
  if (f.never === "only") tail = " nobody has ever watched";
  else if (ageTxt) tail = f.never === "exclude" ? ` watched before, but not in ${ageTxt}` : ` not watched in ${ageTxt}`;
  else if (f.never === "exclude") tail = " that have been watched";
  const kept = S.hiddenKept ? ` ${plural(S.hiddenKept, "kept " + noun)} hidden.` : "";
  $("#headline").innerHTML =
    `<span class="big">${bytes(total)}</span>in <b>${plural(S.filtered.length, noun)}</b>${esc(tail)}.${esc(kept)}`;
}

function rowHtml(r) {
  const sel = S.selected.has(r.rating_key);
  const isSeason = r.kind === "season";
  const lib = S.libraries.find((l) => l.section_id === r.section_id);
  let title, sub;
  if (isSeason) {
    title = `${esc(r.show_title || "Unknown show")}<span class="yr">${esc(r.title)}</span>`;
    sub = `${plural(r.episode_count, "episode")}${r.excluded_via_show ? " · show is kept" : ""}`;
  } else {
    title = `${esc(r.title)}${r.year ? `<span class="yr">${r.year}</span>` : ""}`;
    sub = r.kind === "show"
      ? `${plural(r.season_count, "season")}, ${plural(r.episode_count, "episode")}`
      : [lib && lib.name, r.resolution && (r.resolution === "4k" ? "4K" : /^\d+$/.test(r.resolution) ? r.resolution + "p" : r.resolution.toUpperCase())].filter(Boolean).join(" · ");
  }
  const kept = r.excluded ? `<span class="tag">Kept</span>` : "";
  const t = tint(r.last_played);
  const w = Math.max(0.6, (r.size / S.maxSize) * 100);
  return `<li class="row${sel ? " is-sel" : ""}${r.excluded ? " is-kept" : ""}" data-rk="${r.rating_key}" tabindex="0">
    <input type="checkbox" ${sel ? "checked" : ""} aria-label="Select ${esc(isSeason ? `${r.show_title} ${r.title}` : r.title)}">
    ${posterImg(r.thumb, 120)}
    <div class="t-main"><div class="t-title">${title}${kept}</div><div class="t-sub">${esc(sub)}</div></div>
    <div class="shelf" style="--tint:${t}"><div class="bar"><div class="fill" style="width:${w.toFixed(2)}%"></div></div><span class="num">${bytes(r.size)}</span></div>
    <div class="when${r.last_played ? "" : " never"}" style="--tint:${t}"><span><span class="dot"></span>${ago(r.last_played)}</span>${r.last_user ? `<span class="who">${esc(r.last_user)}</span>` : ""}</div>
    <div class="plays">${r.plays ? `<b>${r.plays}</b> ${r.plays === 1 ? "play" : "plays"}${r.users > 1 ? `<span class="who">${r.users} people</span>` : ""}` : "No plays"}</div>
    <div class="added">${monthYear(r.added_at)}</div>
  </li>`;
}

function renderList(reset) {
  const list = $("#list");
  if (reset) { list.innerHTML = ""; S.rendered = 0; }
  const next = S.filtered.slice(S.rendered, S.rendered + PAGE);
  list.insertAdjacentHTML("beforeend", next.map(rowHtml).join(""));
  S.rendered += next.length;
  renderEmpty();
}

function renderEmpty() {
  const e = $("#empty");
  const st = S.status;
  let html = "";
  if (st && !st.configured) {
    html = `<h2>Connect Tautulli to get started</h2><p>Set <code>TAUTULLI_URL</code> and <code>TAUTULLI_API_KEY</code> on the container, then restart it. The API key is under Tautulli → Settings → Web Interface.</p>`;
  } else if (!S.titles.length) {
    html = st && st.sync.running
      ? `<h2>Reading your libraries</h2><p>The first sync is running. Large TV libraries can take a few minutes when file sizes come through Tautulli.</p>`
      : st && st.sync.last_error
        ? `<h2>Sync failed</h2><p>${esc(st.sync.last_error)}</p>`
        : `<h2>No library data yet</h2><p>Run a sync to pull your movies, shows and watch history from Tautulli.</p><p><button class="btn" onclick="startSync()">Sync now</button></p>`;
  } else if (!S.filtered.length) {
    html = `<h2>Nothing matches</h2><p>Everything in this view has been watched recently, or the filters are too narrow. Try a shorter "Not watched in" window.</p>`;
  }
  e.innerHTML = html;
  e.hidden = !html;
  $(".list-head").hidden = !S.filtered.length;
}

new IntersectionObserver((ents) => {
  if (ents.some((e) => e.isIntersecting) && S.rendered < S.filtered.length) renderList(false);
}, { rootMargin: "800px" }).observe($("#sentinel"));

/* ------------------------------------------------------------ selection */

function renderSelbar() {
  const n = S.selected.size;
  $("#selbar").hidden = !n;
  if (!n) return;
  const size = S.filtered.filter((r) => S.selected.has(r.rating_key)).reduce((a, r) => a + r.size, 0);
  $("#selText").innerHTML = `<b>${n}</b> selected, ${bytes(size)}`;
}

function toggleSelect(rk, on, range) {
  if (range && S.lastClicked != null) {
    const ids = S.filtered.map((r) => r.rating_key);
    const a = ids.indexOf(S.lastClicked), b = ids.indexOf(rk);
    if (a > -1 && b > -1) {
      for (const id of ids.slice(Math.min(a, b), Math.max(a, b) + 1)) on ? S.selected.add(id) : S.selected.delete(id);
    }
  } else {
    on ? S.selected.add(rk) : S.selected.delete(rk);
  }
  S.lastClicked = rk;
  $$("#list .row").forEach((li) => {
    const s = S.selected.has(+li.dataset.rk);
    li.classList.toggle("is-sel", s);
    $("input", li).checked = s;
  });
  renderSelbar();
}

function exportCsv(rows) {
  const head = ["Title", "Season", "Year", "Type", "Library", "Size (GB)", "Size (bytes)", "Last watched", "Last watched by", "Plays", "People", "Added", "Kept"];
  const lib = (id) => (S.libraries.find((l) => l.section_id === id) || {}).name || "";
  const lines = rows.map((r) => [
    r.kind === "season" ? r.show_title : r.title, r.kind === "season" ? r.title : "", r.year || "",
    r.kind, lib(r.section_id), (r.size / 1024 ** 3).toFixed(2), r.size, r.last_played ? ymd(r.last_played) : "never",
    r.last_user || "", r.plays, r.users, ymd(r.added_at), r.excluded ? "yes" : "",
  ]);
  const csv = [head, ...lines].map((l) => l.map((v) => `"${String(v).replace(/"/g, '""')}"`).join(",")).join("\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  a.download = `shelflife-${S.f.view}-${ymd(now())}.csv`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

/* ------------------------------------------------------------ keep list */

async function keep(keys, note) {
  if (keys.length === 1) await api(`/api/exclusions/${keys[0]}`, { method: "PUT", body: JSON.stringify(note === undefined ? {} : { note }) });
  else await api("/api/exclusions/bulk", { method: "POST", body: JSON.stringify({ rating_keys: keys }) });
  await markKept();
}

async function unkeep(keys) {
  await Promise.all(keys.map((k) => api(`/api/exclusions/${k}`, { method: "DELETE" })));
  await markKept();
}

async function markKept() {
  const [titles, seasons] = await Promise.all([
    api("/api/items"), S.seasons ? api("/api/items?view=seasons") : Promise.resolve(null)]);
  S.titles = titles;
  S.seasons = seasons;
  refreshKeepCount();
  apply();
}

function clearSelection() {
  S.selected.clear();
  $$("#list .row.is-sel").forEach((li) => { li.classList.remove("is-sel"); $("input", li).checked = false; });
  renderSelbar();
}

async function renderKeep() {
  const rows = await api("/api/exclusions");
  S.keepCache = rows;
  $("#keepCount").textContent = rows.length || "";
  $("#keepEmpty").hidden = rows.length > 0;
  $("#keepList").innerHTML = rows.map((r) => `
    <li data-rk="${r.rating_key}">
      ${posterImg(r.thumb, 120)}
      <div class="t-main">
        <div class="t-title">${esc(r.title)}${r.year ? `<span class="yr">${r.year}</span>` : ""}${r.in_library ? "" : `<span class="tag">No longer in Plex</span>`}</div>
        <div class="t-sub">${r.kind === "show" ? "Whole show" : r.kind === "season" ? "Season" : "Movie"}, kept ${ymd(r.created_at)}</div>
      </div>
      <input class="note" type="text" maxlength="500" placeholder="Why keep it? (optional)" value="${esc(r.note || "")}" aria-label="Note for ${esc(r.title)}">
      <span class="size">${r.size ? bytes(r.size) : ""}</span>
      <button class="btn ghost small rm">Stop keeping</button>
    </li>`).join("");
}

/* ------------------------------------------------------------ detail drawer */

async function openDetail(rk, push = false) {
  const drawer = $("#drawer");
  if (!push) S.stack = [];
  S.stack.push(rk);
  $("#dBody").innerHTML = `<p class="hint">Loading…</p>`;
  drawer.classList.add("is-open");
  drawer.setAttribute("aria-hidden", "false");
  $(".drawer-panel").focus();
  S.arr = null;
  try {
    const d = await api(`/api/item/${rk}`);
    const svc = d.item.kind === "movie" ? "radarr" : "sonarr";
    if (S.status && S.status.integrations && S.status.integrations[svc]) S.arr = { rk, loading: true };
    renderDetail(d);
    if (S.arr) loadArr(rk);
  } catch (e) {
    $("#dBody").innerHTML = `<p class="hint">Couldn't load this title: ${esc(e.message)}</p>`;
  }
  $("#dBack").hidden = S.stack.length < 2;
}

async function loadArr(rk) {
  let info;
  try { info = await api(`/api/arr/${rk}`); } catch (e) { info = { error: e.message }; }
  if (!S.detail || S.detail.item.rating_key !== rk) return;
  S.arr = { rk, ...info };
  renderDetail(S.detail);
}

const arrFor = (it) => (S.arr && S.arr.rk === it.rating_key ? S.arr : null);
const SVC = { radarr: "Radarr", sonarr: "Sonarr" };

function closeDetail() {
  const drawer = $("#drawer");
  drawer.classList.remove("is-open");
  drawer.setAttribute("aria-hidden", "true");
  S.stack = [];
  const row = S.lastOpened && $(`#list .row[data-rk="${S.lastOpened}"]`);
  if (row) row.focus();
}

function histRows(history, isShow, settingsMin) {
  return history.map((h) => {
    const why = h.counted ? "" : `<span class="why">(${(h.duration || 0) < settingsMin * 60 ? "short" : "ignored user"})</span>`;
    const ep = h.season != null && h.episode != null ? `<span class="ep">S${h.season} · E${h.episode}</span>` : "";
    return `<tr class="${h.counted ? "" : "uncounted"}">
      <td>${ymd(h.date)}</td>
      <td>${esc(h.friendly_name)}${why}</td>
      <td class="title-cell">${esc(isShow ? h.title : h.full_title || h.title)}${ep}</td>
      <td>${hm(h.started)}</td>
      <td class="r">${mins(h.paused)}</td>
      <td>${hm(h.stopped)}</td>
      <td class="r">${mins(h.duration)}</td>
    </tr>`;
  }).join("");
}

function renderDetail(d) {
  const it = d.item;
  S.detail = d;
  const isSeason = it.kind === "season";
  const kept = it.exclusion;
  const viaShow = isSeason && it.show_exclusion;
  const name = isSeason ? `${it.show_title} – ${it.title}` : it.title;
  $("#dCrumb").textContent = isSeason ? it.show_title : it.library || "";

  let sub = [];
  if (it.year) sub.push(it.year);
  if (it.library) sub.push(it.library);
  if (it.kind === "show") sub.push(`${plural(it.season_count, "season")}, ${plural(it.episode_count, "episode")}`);
  if (isSeason) sub.push(plural(it.episode_count, "episode"));
  if (it.resolution) sub.push(it.resolution === "4k" ? "4K" : /^\d+$/.test(it.resolution) ? `${it.resolution}p` : it.resolution.toUpperCase());

  const keepBlock = kept
    ? `<div class="keepnote">
        <p><b>On your keep list.</b> It won't show up in the cull list.</p>
        <div class="row2"><input type="text" id="dNote" maxlength="500" placeholder="Why keep it? (optional)" value="${esc(kept.note || "")}">
        <button class="btn ghost" id="dUnkeep">Stop keeping</button></div>
      </div>`
    : viaShow
      ? `<div class="keepnote"><p>The whole show is on your keep list, so this season is hidden from the cull list too.</p></div>`
      : "";

  const svc = it.kind === "movie" ? "radarr" : "sonarr";
  const A = arrFor(it);
  const linked = A && A.matched && !A.loading && !A.error;
  const sonarrSeasons = linked && svc === "sonarr" ? A.seasons : null;
  const arrSeason = sonarrSeasons && isSeason ? sonarrSeasons.find((x) => x.number === it.season_index) : null;
  let arrActions = "", arrRow = "";
  if (A) {
    if (A.loading) arrRow = `<p class="arr-note">Checking ${SVC[svc]}…</p>`;
    else if (A.error) arrRow = `<p class="arr-note is-err">${SVC[svc]}: ${esc(A.error)}</p>`;
    else if (!A.matched) arrRow = `<p class="arr-note">Not found in ${SVC[svc]}, so it can't be deleted from here.</p>`;
    else {
      const blocked = kept || viaShow;
      const label = isSeason ? "Delete season files" : "Delete";
      arrActions = `<a class="btn ghost" href="${esc(A.url)}" target="_blank" rel="noopener">Show in ${SVC[svc]}</a>
        <button class="btn warn" id="dDelete" ${blocked ? 'disabled title="On your keep list. Stop keeping it to delete."' : ""}>${label}</button>`;
      if (svc === "sonarr" && !isSeason) {
        arrRow = `<label class="switch"><input type="checkbox" id="dMonSeries" ${A.monitored ? "checked" : ""}><span class="knob"></span>Monitored in Sonarr</label>`;
      } else if (arrSeason) {
        arrRow = `<label class="switch"><input type="checkbox" id="dMonSeason" ${arrSeason.monitored ? "checked" : ""}><span class="knob"></span>Season ${arrSeason.number} monitored in Sonarr</label>`;
      }
    }
  }

  const settingsMin = S.settings ? S.settings.min_play_minutes : 0;
  const counted = d.history.filter((h) => h.counted).length;
  const histLimit = 100;

  let seasonsHtml = "";
  if (d.seasons.length) {
    const max = Math.max(1, ...d.seasons.map((s) => s.size));
    seasonsHtml = `<section class="d-section"><h3>Seasons <span>${plural(d.seasons.length, "season")}</span></h3>
      <div class="tbl-wrap"><table>
      <thead><tr><th>Season</th><th class="r eps">Episodes</th><th class="season-bar"></th><th class="r">Size on disk</th><th>Last watched</th><th class="r pl">Plays</th>${sonarrSeasons ? '<th class="mon">Monitored</th>' : ""}</tr></thead>
      <tbody>${d.seasons.map((s) => { const as = sonarrSeasons && sonarrSeasons.find((x) => x.number === s.season_index); return `<tr class="click" data-rk="${s.rating_key}" tabindex="0">
        <td>${esc(s.title)}${s.excluded ? ' <span class="tag">Kept</span>' : ""}</td>
        <td class="r eps">${s.episode_count}</td>
        <td class="season-bar" style="--tint:${tint(s.last_played)}"><div class="bar"><div class="fill" style="width:${Math.max(1, s.size / max * 100).toFixed(1)}%"></div></div></td>
        <td class="r">${bytes(s.size)}</td>
        <td>${s.last_played ? `${ymd(s.last_played)} <span class="ep ago">${ago(s.last_played)}</span>` : '<span style="color:var(--never)">Never</span>'}</td>
        <td class="r pl">${s.plays}</td>${sonarrSeasons ? `<td class="mon">${as ? `<label class="switch" title="Monitor season ${as.number} in Sonarr"><input type="checkbox" data-season="${as.number}" ${as.monitored ? "checked" : ""} aria-label="Monitor season ${as.number}"><span class="knob"></span></label>` : ""}</td>` : ""}</tr>`; }).join("")}</tbody></table></div></section>`;
  }

  const historyHtml = d.history.length
    ? `<div class="tbl-wrap"><table>
        <thead><tr><th>Date</th><th>User</th><th>Title</th><th>Started</th><th class="r">Paused</th><th>Stopped</th><th class="r">Duration</th></tr></thead>
        <tbody id="hBody">${histRows(d.history.slice(0, histLimit), it.kind !== "movie", settingsMin)}</tbody></table></div>
        ${d.history.length > histLimit ? `<button class="btn ghost small more-hist" id="hMore">Show all ${d.history.length.toLocaleString()} plays</button>` : ""}`
    : `<p class="hint">Tautulli has no record of anyone playing this.</p>`;

  $("#dBody").innerHTML = `
    <div class="d-head">
      ${posterImg(it.thumb, 240)}
      <div>
        <h2 class="d-title" id="dTitle">${esc(name)}</h2>
        <p class="d-sub">${esc(sub.join(" · "))}</p>
        <p class="d-size">${bytes(it.size)}<small>on disk</small></p>
        <div class="d-actions">
          ${kept || viaShow ? "" : `<button class="btn" id="dKeep">Keep</button>`}
          ${isSeason ? `<button class="btn ghost" id="dShow">Open show</button>` : ""}
          ${it.plex_url ? `<a class="btn ghost" href="${esc(it.plex_url)}" target="_blank" rel="noopener">Open in Plex</a>` : ""}
          ${arrActions}
        </div>
        ${arrRow ? `<div class="d-arr">${arrRow}</div>` : ""}
      </div>
    </div>
    ${keepBlock}
    <dl class="facts">
      <div><dt>Last watched</dt><dd style="${it.last_played ? "" : "color:var(--never)"}">${it.last_played ? ago(it.last_played) : "Never"}</dd></div>
      <div><dt>Last watched by</dt><dd>${esc(it.last_user || "—")}</dd></div>
      <div><dt>Plays</dt><dd>${it.plays}${it.users ? ` <span class="ep">from ${plural(it.users, "person", "people")}</span>` : ""}</dd></div>
      <div><dt>Added</dt><dd>${ymd(it.added_at)}</dd></div>
    </dl>
    ${seasonsHtml}
    <section class="d-section"><h3>Watch history <span>${plural(d.history.length, "play")}${counted !== d.history.length ? `, ${counted} counted` : ""}</span></h3>${historyHtml}</section>`;

  const body = $("#dBody");
  const bump = async () => renderDetail(await api(`/api/item/${it.rating_key}`));
  $("#dKeep", body)?.addEventListener("click", async () => {
    await keep([it.rating_key]);
    toast(`Kept ${name}`, async () => { await unkeep([it.rating_key]); bump(); });
    bump();
  });
  $("#dUnkeep", body)?.addEventListener("click", async () => {
    await unkeep([it.rating_key]);
    toast(`${name} is back on the cull list`);
    bump();
  });
  $("#dNote", body)?.addEventListener("change", async (e) => {
    await api(`/api/exclusions/${it.rating_key}`, { method: "PUT", body: JSON.stringify({ note: e.target.value }) });
    toast("Note saved");
  });
  $("#dShow", body)?.addEventListener("click", () => openDetail(it.show_key, true));
  $("#hMore", body)?.addEventListener("click", (e) => {
    $("#hBody").innerHTML = histRows(d.history, it.kind !== "movie", settingsMin);
    e.target.remove();
  });
  $("#dDelete", body)?.addEventListener("click", () => openDelete(it, A, arrSeason));
  const monitor = async (input, season) => {
    input.disabled = true;
    try {
      const res = await api(`/api/arr/${it.rating_key}/monitor`, {
        method: "PUT", body: JSON.stringify(season == null ? { monitored: input.checked } : { monitored: input.checked, season }) });
      S.arr = { ...S.arr, monitored: res.monitored, seasons: res.seasons };
      toast(`${season == null ? "Show" : `Season ${season}`} ${input.checked ? "monitored" : "unmonitored"} in Sonarr`);
    } catch (e) {
      input.checked = !input.checked;
      toast(e.message);
    }
    renderDetail(S.detail);
  };
  $("#dMonSeries", body)?.addEventListener("change", (e) => monitor(e.target, null));
  $("#dMonSeason", body)?.addEventListener("change", (e) => monitor(e.target, arrSeason.number));
  $$("td.mon", body).forEach((td) => td.addEventListener("click", (e) => e.stopPropagation()));
  $$("td.mon input", body).forEach((inp) => inp.addEventListener("change", () => monitor(inp, +inp.dataset.season)));
  $$("tr.click", body).forEach((tr) => {
    const go = () => openDetail(+tr.dataset.rk, true);
    tr.addEventListener("click", go);
    tr.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  });
}

/* ------------------------------------------------------------ delete via Sonarr / Radarr */

function openDelete(it, A, arrSeason) {
  const dlg = $("#delDlg");
  const isSeason = it.kind === "season";
  const size = bytes(isSeason && arrSeason && arrSeason.size ? arrSeason.size : it.size);
  let html;
  if (it.kind === "movie") {
    html = `<h2>Delete ${esc(it.title)}?</h2>
      <p>Radarr removes the movie and deletes its files (${size}), so it won't download again. This can't be undone.</p>
      <label class="check"><input type="checkbox" name="exclusion" checked> Add to Radarr's import exclusions so lists don't add it back</label>`;
  } else if (isSeason) {
    const files = arrSeason ? arrSeason.files : it.episode_count;
    html = `<h2>Delete ${esc(it.show_title)} season ${it.season_index} files?</h2>
      <p>Sonarr deletes the ${plural(files, "episode file")} in this season (${size}). The show stays in Sonarr. This can't be undone.</p>
      <label class="check"><input type="checkbox" name="unmonitor" checked> Unmonitor the deleted episodes so Sonarr doesn't download them again</label>`;
  } else {
    html = `<h2>Delete ${esc(it.title)}?</h2>
      <div class="choices">
        <label class="choice"><input type="radio" name="mode" value="remove" checked>
          <span><b>Remove from Sonarr</b>Deletes every episode file (${size}) and removes the show from Sonarr. Nothing new will download.</span></label>
        <label class="choice"><input type="radio" name="mode" value="files">
          <span><b>Delete files, keep in Sonarr</b>Deletes every episode file (${size}). The show stays in Sonarr so new episodes still download.</span></label>
      </div>
      <label class="check" data-for="remove"><input type="checkbox" name="exclusion" checked> Add to Sonarr's import list exclusions so lists don't add it back</label>
      <label class="check" data-for="files" hidden><input type="checkbox" name="unmonitor" checked> Unmonitor episodes that have already aired so they don't download again</label>
      <p class="hint">This can't be undone.</p>`;
  }
  $("#delBody").innerHTML = html;
  const go = $("#delGo");
  const sync = () => {
    const mode = ($("input[name=mode]:checked", dlg) || {}).value;
    $$("[data-for]", dlg).forEach((l) => { l.hidden = l.dataset.for !== mode; });
    go.textContent = it.kind === "movie" ? "Delete from Radarr" : isSeason ? "Delete season files" : mode === "files" ? "Delete episode files" : "Remove from Sonarr";
  };
  $$("input[name=mode]", dlg).forEach((r) => r.addEventListener("change", sync));
  sync();
  go.disabled = false;
  go.onclick = async (e) => {
    e.preventDefault();
    const mode = ($("input[name=mode]:checked", dlg) || {}).value || "remove";
    const opt = (n) => { const el = $(`[data-for="${mode}"] input[name=${n}], label:not([data-for]) > input[name=${n}]`, dlg); return el ? el.checked : true; };
    go.disabled = true;
    go.textContent = "Deleting…";
    try {
      const res = await api(`/api/arr/${it.rating_key}/delete`, {
        method: "POST", body: JSON.stringify({ mode, exclusion: opt("exclusion"), unmonitor: opt("unmonitor") }) });
      dlg.close();
      toast(`${res.message}${res.freed ? `, ${bytes(res.freed)} freed` : ""}`);
      await markKept();
      if (isSeason) openDetail(it.show_key); else closeDetail();
    } catch (err) {
      go.disabled = false;
      sync();
      $("#delErr").textContent = err.message;
    }
  };
  $("#delErr").textContent = "";
  dlg.showModal();
}

/* ------------------------------------------------------------ sync + settings */

function renderSync() {
  const st = S.status;
  const el = $("#syncText");
  const btn = $("#syncBtn");
  if (!st) return;
  btn.classList.toggle("spin", st.sync.running);
  el.classList.toggle("is-err", !!st.sync.last_error && !st.sync.running);
  if (!st.configured) el.textContent = "Not connected";
  else if (st.sync.running) el.textContent = `${st.sync.phase}${st.sync.detail ? ": " + st.sync.detail : ""}`;
  else if (st.sync.last_error) el.textContent = "Last sync failed";
  else el.textContent = st.sync.last_success ? `Synced ${ago(st.sync.last_success).toLowerCase()}` : "Never synced";
  el.title = st.sync.last_error || "";
}

let polling = false;
async function pollSync() {
  if (polling) return;
  polling = true;
  try {
    for (;;) {
      await new Promise((r) => setTimeout(r, 1500));
      const st = await loadStatus();
      if (!st.sync.running) break;
    }
    if (S.status.sync.last_error) toast(`Sync failed: ${S.status.sync.last_error}`);
    else { toast("Sync finished"); await loadData(); }
  } finally { polling = false; }
}

async function startSync() {
  try {
    await api("/api/sync", { method: "POST" });
    await loadStatus();
    renderEmpty();
    pollSync();
  } catch (e) { toast(e.message); }
}
window.startSync = startSync;

async function openSettings() {
  const [settings, users, st] = await Promise.all([api("/api/settings"), api("/api/users"), loadStatus()]);
  S.settings = settings;
  const ignored = new Set(settings.ignored_users);
  $("#sUsers").innerHTML = users.length
    ? users.map((u) => `<label><input type="checkbox" value="${u.user_id}" ${ignored.has(u.user_id) ? "checked" : ""}>
        ${esc(u.friendly_name)}<span class="n">${plural(u.plays, "play")}, last ${ago(u.last_played).toLowerCase()}</span></label>`).join("")
    : `<p class="hint">No one yet. Users appear after the first sync.</p>`;
  $("#sMin").value = settings.min_play_minutes;
  const src = st.size_source === "plex"
    ? "File sizes come straight from Plex and include every version of a movie."
    : "File sizes come through Tautulli. For big TV libraries, adding <code>PLEX_TOKEN</code> makes syncs much faster and counts every version of a movie.";
  $("#sSync").innerHTML = `<p>${st.sync.last_success ? `Last synced ${ymd(st.sync.last_success)} at ${hm(st.sync.last_success)}.` : "Not synced yet."} Syncs every ${st.interval_hours} hours.</p><p>${src}</p>
    ${st.sync.last_error ? `<p style="color:var(--danger)">Last error: ${esc(st.sync.last_error)}</p>` : ""}`;
  $("#sVersion").textContent = `Shelflife ${st.version}`;
  $("#sLogout").hidden = !st.auth;
  $("#sInteg").innerHTML = `<p class="hint">Checking…</p>`;
  api("/api/integrations").then((ig) => {
    const line = (name, v, off) => `<li><b>${name}</b> ${!v.enabled ? `<span class="muted">${off}</span>`
      : v.ok === false ? `<span class="bad">${esc(v.error)}</span>` : `<span class="good">Connected${v.version ? ` (v${esc(v.version)})` : ""}</span>`}</li>`;
    $("#sInteg").innerHTML = `<ul class="integ">
      ${line("Plex", ig.plex, "Not set up. Add PLEX_TOKEN for faster syncs and Open in Plex links.")}
      ${line("Sonarr", ig.sonarr, "Not set up. Add SONARR_URL and SONARR_API_KEY to delete shows and change monitoring.")}
      ${line("Radarr", ig.radarr, "Not set up. Add RADARR_URL and RADARR_API_KEY to delete movies.")}</ul>`;
  }).catch((e) => { $("#sInteg").innerHTML = `<p class="hint">${esc(e.message)}</p>`; });
  $("#settings").showModal();
}

async function saveSettings() {
  const ignored = $$("#sUsers input:checked").map((i) => +i.value);
  S.settings = await api("/api/settings", { method: "PUT", body: JSON.stringify({ ignored_users: ignored, min_play_minutes: +$("#sMin").value || 0 }) });
  toast("Settings saved");
  await loadData();
}

/* ------------------------------------------------------------ events */

function bind() {
  const set = (k, v) => { S.f[k] = v; apply(); };
  $("#fLib").addEventListener("change", (e) => { S.f.lib = e.target.value; syncControls(); ensureView(); });
  $$(".seg button").forEach((b) => b.addEventListener("click", () => { S.f.view = b.dataset.view; S.selected.clear(); syncControls(); ensureView(); }));
  $("#fAge").addEventListener("change", (e) => set("age", +e.target.value));
  $("#fNever").addEventListener("change", (e) => set("never", e.target.value));
  $("#fGrace").addEventListener("change", (e) => set("grace", +e.target.value));
  let qt;
  $("#fSearch").addEventListener("input", (e) => { S.f.q = e.target.value; clearTimeout(qt); qt = setTimeout(apply, 120); });
  $("#fSort").addEventListener("change", (e) => { S.f.rev = false; syncControls(); set("sort", e.target.value); });
  $("#fDir").addEventListener("click", () => { S.f.rev = !S.f.rev; syncControls(); apply(); });
  $("#fShowKept").addEventListener("change", (e) => set("showKept", e.target.checked));

  const list = $("#list");
  list.addEventListener("click", (e) => {
    const li = e.target.closest(".row");
    if (!li) return;
    const rk = +li.dataset.rk;
    if (e.target.matches("input[type=checkbox]")) { toggleSelect(rk, e.target.checked, e.shiftKey); return; }
    S.lastOpened = rk;
    openDetail(rk);
  });
  list.addEventListener("keydown", (e) => {
    const li = e.target.closest(".row");
    if (!li || e.target.matches("input")) return;
    if (e.key === "Enter") { S.lastOpened = +li.dataset.rk; openDetail(+li.dataset.rk); }
    if (e.key === " ") { e.preventDefault(); toggleSelect(+li.dataset.rk, !S.selected.has(+li.dataset.rk)); }
  });

  $("#selKeep").addEventListener("click", async () => {
    const keys = [...S.selected];
    S.selected.clear();
    await keep(keys);
    toast(`Kept ${plural(keys.length, S.f.view === "seasons" ? "season" : "title")}`, () => unkeep(keys));
  });
  $("#selClear").addEventListener("click", clearSelection);
  $("#selExport").addEventListener("click", () => exportCsv(S.filtered.filter((r) => S.selected.has(r.rating_key))));
  $("#exportAll").addEventListener("click", () => exportCsv(S.filtered));

  $$(".tab").forEach((t) => t.addEventListener("click", () => {
    $$(".tab").forEach((x) => x.classList.toggle("is-on", x === t));
    const keepTab = t.dataset.tab === "keep";
    $("#cullView").hidden = keepTab;
    $("#keepView").hidden = !keepTab;
    if (keepTab) renderKeep();
  }));
  $("#keepList").addEventListener("click", async (e) => {
    const li = e.target.closest("li");
    if (!li) return;
    const rk = +li.dataset.rk;
    if (e.target.closest(".rm")) {
      await unkeep([rk]);
      li.remove();
      toast("Back on the cull list", async () => { await keep([rk]); renderKeep(); });
      $("#keepEmpty").hidden = $("#keepList").children.length > 0;
      return;
    }
    if (e.target.closest(".t-main, .poster")) openDetail(rk);
  });
  $("#keepList").addEventListener("change", async (e) => {
    if (!e.target.matches(".note")) return;
    const rk = +e.target.closest("li").dataset.rk;
    await api(`/api/exclusions/${rk}`, { method: "PUT", body: JSON.stringify({ note: e.target.value }) });
    toast("Note saved");
  });

  $("#drawer").addEventListener("click", (e) => { if (e.target.closest("[data-close]")) closeDetail(); });
  $("#dBack").addEventListener("click", () => { S.stack.pop(); const rk = S.stack.pop(); openDetail(rk, true); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && $("#drawer").classList.contains("is-open") && !$("#settings").open) closeDetail();
  });

  $("#syncBtn").addEventListener("click", startSync);
  $("#settingsBtn").addEventListener("click", openSettings);
  $("#sLogout").addEventListener("click", async (e) => {
    e.preventDefault();
    await fetch("/api/logout", { method: "POST" });
    location.href = "/login";
  });
  $("#settingsForm").addEventListener("submit", (e) => {
    if (e.submitter && e.submitter.value === "save") saveSettings();
  });
}

async function ensureView() {
  if (S.f.view === "seasons" && !S.seasons) {
    $("#headline").textContent = "Loading seasons…";
    S.seasons = await api("/api/items?view=seasons");
  }
  apply();
}

(async function init() {
  loadFilters();
  bind();
  syncControls();
  try {
    const st = await loadStatus();
    S.settings = await api("/api/settings");
    await loadData();
    if (st.sync.running) pollSync();
  } catch (e) {
    $("#empty").hidden = false;
    $("#empty").innerHTML = `<h2>Can't reach Shelflife</h2><p>${esc(e.message)}</p>`;
  }
  setInterval(() => { if (!polling) loadStatus().catch(() => {}); }, 60000);
})();
