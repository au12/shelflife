"use strict";
/* The cull list: filters, the last-watched spectrum, sorting, rows, selection, Free up, CSV export. */

const PAGE = 120;
const FILTER_KEYS = ["lib", "age", "never", "grace", "req", "kept", "bucket"];

const AGE_OPTS = [[0, "Any time"], [30, "1 month"], [91, "3 months"], [182, "6 months"], [365, "1 year"], [730, "2 years"], [1095, "3 years"], [1825, "5 years"]];
const AGE_TEXT = { 30: "the last month", 91: "the last 3 months", 182: "the last 6 months", 365: "the last year", 730: "the last 2 years", 1095: "the last 3 years", 1825: "the last 5 years" };
const NEVER_OPTS = [["include", "Included"], ["exclude", "Left out"], ["only", "Only these"]];
const GRACE_OPTS = [[0, "Any time"], [30, "Over 30 days ago"], [91, "Over 3 months ago"], [182, "Over 6 months ago"], [365, "Over a year ago"]];
const REQ_OPTS = [["", "Any"], ["req", "Requested"], ["unwatched", "Requester hasn't watched"], ["none", "Not requested"]];
const VIEW_OPTS = [["titles", "Titles"], ["seasons", "Seasons"]];
const SORT_OPTS = [["size", "Size on disk"], ["last", "Last watched"], ["added", "Date added"], ["plays", "Plays"], ["title", "Title"]];

const SORTS = {
  size: { key: (r) => r.size, dir: "desc", asc: "smallest first", desc: "largest first" },
  last: { key: (r) => r.last_played || 0, dir: "asc", asc: "longest ago first", desc: "most recent first" },
  added: { key: (r) => r.added_at || 0, dir: "asc", asc: "oldest first", desc: "newest first" },
  plays: { key: (r) => r.plays, dir: "asc", asc: "fewest first", desc: "most first" },
  title: {
    key: (r) => (r.kind === "season" ? `${r.show_title} ${String(r.season_index).padStart(3, "0")}` : r.sort_title || r.title || "").toLowerCase(),
    dir: "asc", asc: "A to Z", desc: "Z to A",
  },
};
const sortLabel = (k) => (SORT_OPTS.find((o) => o[0] === k) || [k, k])[1];
const sortPhrase = () => `${sortLabel(S.f.sort).toLowerCase()}, ${SORTS[S.f.sort][S.f.dir]}`;
const optText = (opts, v) => ((opts.find((o) => String(o[0]) === String(v)) || [v, String(v)])[1]);

function noun(plural_ = false) {
  let n = "title";
  if (S.f.view === "seasons") n = "season";
  else if (S.f.lib) {
    const lib = S.libraries.find((l) => String(l.section_id) === S.f.lib);
    if (lib) n = lib.type === "movie" ? "movie" : "show";
  }
  return plural_ ? n + "s" : n;
}

/* ------------------------------------------------------------ filter controls */

function filterGroups() {
  const groups = [
    { key: "lib", label: "Library", opts: [["", "All libraries"], ...S.libraries.map((l) => [String(l.section_id), l.name])] },
    { key: "age", label: "Not watched in", opts: AGE_OPTS },
    { key: "never", label: "Never watched", opts: NEVER_OPTS },
    { key: "grace", label: "Added", opts: GRACE_OPTS },
  ];
  if (S.status && S.status.integrations && S.status.integrations.seerr) groups.push({ key: "req", label: "Requests", opts: REQ_OPTS });
  return groups;
}

function chipParts(key) {
  const f = S.f;
  if (key === "lib") return [null, f.lib ? libName(f.lib) || "Library" : "All libraries"];
  if (key === "age") return f.age ? ["Not watched in", optText(AGE_OPTS, f.age)] : ["Last watched", "any time"];
  if (key === "never") return ["Never watched", optText(NEVER_OPTS, f.never).toLowerCase()];
  if (key === "grace") return ["Added", optText(GRACE_OPTS, f.grace).toLowerCase()];
  return ["Requests", optText(REQ_OPTS, f.req).toLowerCase()];
}

const isDirty = () => FILTER_KEYS.some((k) => S.f[k] !== DEFAULTS[k]);
const activeCount = () => FILTER_KEYS.filter((k) => S.f[k] !== DEFAULTS[k]).length + (S.f.view !== DEFAULTS.view ? 1 : 0);

function renderChips() {
  const f = S.f;
  const html = [];
  if (f.bucket) {
    const b = BUCKETS.find((x) => x.id === f.bucket);
    const [k, v] = b.chip;
    html.push(`<button type="button" class="chip is-set" data-clear-bucket title="Remove this filter" aria-label="${esc([k, v].filter(Boolean).join(" "))}. Remove this filter">
      <span class="dot" style="--c:${b.color}"></span>${k ? `<span class="k">${esc(k)}</span>` : ""}<b>${esc(v)}</b>${icon("close", "x")}</button>`);
  }
  for (const g of filterGroups()) {
    if (f.bucket && (g.key === "age" || g.key === "never")) continue;   // the bar's choice stands in for these
    const [k, v] = chipParts(g.key);
    const set = f[g.key] !== DEFAULTS[g.key];
    html.push(`<button type="button" class="chip${set ? " is-set" : ""}" data-chip="${g.key}" aria-haspopup="true" aria-expanded="false">
      ${k ? `<span class="k">${esc(k)}</span>` : ""}<b>${esc(v)}</b>${icon("down", "chev")}</button>`);
  }
  html.push(`<button type="button" class="chip toggle${f.kept ? " is-set" : ""}" data-toggle-kept aria-pressed="${f.kept}">${icon(f.kept ? "check" : "keep")}<b>Show kept</b></button>`);
  if (isDirty()) html.push(`<button type="button" class="chip-reset" data-reset>Reset</button>`);
  $("#chips").innerHTML = html.join("");
  const n = activeCount();
  $("#filtersN").hidden = !n;
  $("#filtersN").textContent = n;
  renderViewSeg();
}

function seasonsAllowed() {
  const lib = S.libraries.find((l) => String(l.section_id) === S.f.lib);
  return lib ? lib.type === "show" : S.libraries.some((l) => l.type === "show");
}

function renderViewSeg() {
  const allowed = seasonsAllowed();
  $$("#fView button").forEach((b) => {
    b.setAttribute("aria-checked", String(b.dataset.view === S.f.view));
    if (b.dataset.view === "seasons") {
      b.disabled = !allowed;
      b.title = allowed ? "Every season as its own row" : "This library has no TV shows";
    }
  });
}

function groupHtml(g, layout, prefix) {
  return `<fieldset class="opts ${layout}" data-group="${g.key}"><legend>${esc(g.label)}</legend><div class="opt-wrap">
    ${g.opts.map(([v, t]) => `<label class="opt${g.disabled && g.disabled(v) ? " is-off" : ""}"><input type="radio" name="${prefix}-${g.key}" value="${esc(v)}"
      ${String(S.f[g.key]) === String(v) ? "checked" : ""}${g.disabled && g.disabled(v) ? " disabled" : ""}><span>${esc(t)}</span></label>`).join("")}</div></fieldset>`;
}

function setFilter(key, raw) {
  const f = S.f;
  if (key === "age" || key === "grace") f[key] = +raw;
  else if (key === "kept") f.kept = !!raw;
  else if (key === "sort") { f.sort = raw; f.dir = SORTS[raw].dir; }
  else f[key] = raw;
  if (key === "age" || key === "never") f.bucket = "";
  if (key === "lib" && !seasonsAllowed()) f.view = "titles";
  ensureView().then(() => apply());
}

function resetFilters() {
  for (const k of FILTER_KEYS) S.f[k] = DEFAULTS[k];
  ensureView().then(() => apply());
}

/* --- popovers (desktop chips, Free up) */

let popAnchor = null;

function openPop(anchor, html, cls = "") {
  closePop();
  const pop = $("#pop");
  pop.className = `pop ${cls}`;
  pop.innerHTML = html;
  pop.hidden = false;
  popAnchor = anchor;
  anchor.setAttribute("aria-expanded", "true");
  const r = anchor.getBoundingClientRect();
  pop.style.left = `${clamp(r.left + scrollX, scrollX + 8, scrollX + innerWidth - pop.offsetWidth - 8)}px`;
  pop.style.top = `${r.bottom + scrollY + 6}px`;
  const first = $("input:checked", pop) || $("input:not([type=hidden]), button, select", pop);
  if (first) first.focus();
}

function closePop(focusBack = false) {
  const pop = $("#pop");
  if (pop.hidden) return;
  pop.hidden = true;
  pop.innerHTML = "";
  const a = popAnchor;
  popAnchor = null;
  if (a) {
    a.setAttribute("aria-expanded", "false");
    if (focusBack && document.contains(a)) a.focus();
    else if (focusBack) { const same = a.dataset.chip && $(`[data-chip="${a.dataset.chip}"]`); if (same) same.focus(); }
  }
}

function openChip(btn) {
  if (popAnchor === btn) { closePop(); return; }
  const g = filterGroups().find((x) => x.key === btn.dataset.chip);
  if (g) openPop(btn, groupHtml(g, "list", "p"), "pop-opts");
}

/* --- mobile filter sheet */

function sheetHtml() {
  const f = S.f;
  const allowed = seasonsAllowed();
  const parts = [];
  if (f.bucket) {
    const b = BUCKETS.find((x) => x.id === f.bucket);
    parts.push(`<div class="sheet-note"><span class="dot" style="--c:${b.color}"></span>Showing only titles ${esc(b.phrase)}.
      <button type="button" class="btn ghost small" data-clear-bucket>Clear</button></div>`);
  }
  parts.push(groupHtml({ key: "view", label: "Show", opts: VIEW_OPTS, disabled: (v) => v === "seasons" && !allowed }, "pills", "s"));
  parts.push(`<div class="sort-group">${groupHtml({ key: "sort", label: "Sort by", opts: SORT_OPTS }, "pills", "s")}
    <button type="button" class="btn ghost small" data-flip-dir>${icon(f.dir === "asc" ? "up" : "down")}${esc(SORTS[f.sort][f.dir])}</button></div>`);
  for (const g of filterGroups()) {
    if (f.bucket && (g.key === "age" || g.key === "never")) continue;
    parts.push(groupHtml(g, "pills", "s"));
  }
  parts.push(`<label class="switch"><input type="checkbox" data-kept-switch ${f.kept ? "checked" : ""}><span class="knob"></span>Show kept titles</label>`);
  return parts.join("");
}

function openSheet() {
  $("#sheetBody").innerHTML = sheetHtml();
  $("#sheet").showModal();
}

function refreshSheet() {
  if (!$("#sheet").open) return;
  const body = $("#sheetBody");
  const top = body.scrollTop;
  const focusName = document.activeElement && body.contains(document.activeElement) ? document.activeElement.name || document.activeElement.dataset.flipDir : null;
  body.innerHTML = sheetHtml();
  body.scrollTop = top;
  if (focusName) {
    const el = $(`input[name="${focusName}"]:checked`, body) || $("[data-flip-dir]", body);
    if (el) el.focus();
  }
}

/* ------------------------------------------------------------ filtering */

async function ensureView() {
  if (S.f.view === "seasons" && !S.seasons) {
    $("#headline").innerHTML = `<span class="big skel-text">&nbsp;</span>`;
    renderSkeleton();
    S.seasons = await api("/api/items?view=seasons");
  }
}

function apply() {
  const f = S.f;
  const pool = f.view === "seasons" ? S.seasons || [] : S.titles;
  const cutoff = f.age > 0 ? now() - f.age * DAY : Infinity;
  const graceCut = f.grace > 0 ? now() - f.grace * DAY : Infinity;
  const q = f.q.trim().toLowerCase();
  const per = Object.fromEntries(BUCKETS.map((b) => [b.id, { size: 0, count: 0, inSize: 0, inCount: 0 }]));
  let total = 0, hiddenKept = 0;
  const out = [];

  for (const r of pool) {
    if (f.lib && String(r.section_id) !== f.lib) continue;
    if (r.added_at && r.added_at >= graceCut) continue;
    if (q && !`${r.title} ${r.show_title || ""}`.toLowerCase().includes(q)) continue;
    if (f.req === "req" && !r.requested_by) continue;
    if (f.req === "none" && r.requested_by) continue;
    if (f.req === "unwatched" && !(r.requested_by && r.requester_watched === false)) continue;
    const b = bucketOf(r.last_played);
    let pass;
    if (f.bucket) pass = b === f.bucket;
    else {
      const never = !r.last_played;
      pass = !(f.never === "exclude" && never) && !(f.never === "only" && !never) && (never || r.last_played < cutoff);
    }
    if (r.excluded && !f.kept) { if (pass) hiddenKept++; continue; }
    const p = per[b];
    p.size += r.size; p.count++; total += r.size;
    if (pass) { p.inSize += r.size; p.inCount++; out.push(r); }
  }

  const s = SORTS[f.sort] || SORTS.size;
  const dir = f.dir === "asc" ? 1 : -1;
  out.sort((a, b) => {
    const x = s.key(a), y = s.key(b);
    return (x < y ? -1 : x > y ? 1 : 0) * dir || b.size - a.size;
  });

  S.filtered = out;
  S.hiddenKept = hiddenKept;
  S.maxSize = Math.max(1, ...out.map((r) => r.size));
  const visible = new Set(out.map((r) => r.rating_key));
  for (const k of [...S.selected]) if (!visible.has(k)) S.selected.delete(k);

  store.set("filters", { ...f, q: "", bucket: "" });
  renderHeadline();
  renderSpectrum(total, per);
  renderChips();
  renderSortHead();
  const keep = S.rendered;
  renderList(true);
  while (S.rendered < Math.min(keep, S.filtered.length)) renderList(false);
  renderSelbar();
  refreshSheet();
  if (typeof writeUrl === "function") writeUrl();
}

/* ------------------------------------------------------------ headline + spectrum */

function renderHeadline() {
  if (!S.loaded) return;
  const f = S.f;
  const total = S.filtered.reduce((a, r) => a + r.size, 0);
  let tail = "";
  if (f.bucket) tail = ` ${BUCKETS.find((b) => b.id === f.bucket).phrase}`;
  else if (f.never === "only") tail = " nobody has ever watched";
  else if (AGE_TEXT[f.age]) tail = f.never === "exclude" ? ` watched before, but not in ${AGE_TEXT[f.age]}` : ` not watched in ${AGE_TEXT[f.age]}`;
  else if (f.never === "exclude") tail = " that have been watched";
  const n = S.filtered.length;
  $("#headline").innerHTML = `<span class="big">${bytes(total)}</span> <span class="hl-text">in <b>${n.toLocaleString()} ${noun(n !== 1)}</b>${esc(tail)}.</span>`;
  const sub = [];
  if (S.hiddenKept) sub.push(`${plural(S.hiddenKept, `kept ${noun()}`)} hidden. <button type="button" class="linkish" data-toggle-kept>Show ${S.hiddenKept === 1 ? "it" : "them"}</button>`);
  if (f.grace) sub.push(`Leaving out anything added in the last ${AGE_TEXT[f.grace] ? AGE_TEXT[f.grace].replace("the last ", "") : `${f.grace} days`}.`);
  $("#subline").innerHTML = sub.map((x) => `<span>${x}</span>`).join(" ");
  $("#reviewBtn").disabled = !S.filtered.some((r) => !r.excluded);
  $("#freeBtn").disabled = $("#reviewBtn").disabled;
}

function renderSpectrum(total, per) {
  const el = $("#spectrum");
  S.spectrum = { total, per };
  if (!S.loaded || !total) { el.hidden = true; return; }
  el.hidden = false;
  // Wide screens: each slice is one button, its label sitting under its own segment.
  // If any label won't fit under its segment (narrow window, tiny slice), fall back to bar + legend row.
  if (!matchMedia("(max-width: 760px)").matches && !(S.f.bucket && !per[S.f.bucket].size)) {
    el.innerHTML = spectrumColumns(total, per);
    const fits = $$(".sp-col", el).every((c) => $(".sp-lab", c).scrollWidth <= c.clientWidth + 1);
    if (fits) { el.dataset.w = el.clientWidth; return; }
  }
  el.innerHTML = spectrumRow(total, per);
  el.dataset.w = el.clientWidth;
}

function bucketLabel(b, p) {
  return `${esc(b.label)}: ${bytes(p.size)} in ${plural(p.count, noun())}`;
}

function spectrumColumns(total, per) {
  const f = S.f;
  const cols = BUCKETS.filter((b) => per[b.id].size).map((b) => {
    const p = per[b.id];
    const on = f.bucket === b.id;
    const inPct = (p.inSize / p.size) * 100;
    const note = p.inSize !== p.size && !f.bucket ? `, ${bytes(p.inSize)} of it in this list` : "";
    return `<button type="button" class="sp-col${on ? " is-on" : ""}${f.bucket && !on ? " is-dim" : ""}" data-bucket="${b.id}" style="flex-grow:${(p.size / total) * 1000};--c:${b.color}"
      aria-pressed="${on}" aria-label="${bucketLabel(b, p)}${note}. ${on ? "Showing only these" : "Show only these"}" title="${bucketLabel(b, p)}${note}">
      <span class="sp-seg"><span class="sp-in" style="width:${inPct.toFixed(1)}%"></span></span>
      <span class="sp-lab"><span class="dot"></span><span class="leg-l">${esc(b.label)}</span><span class="leg-v">${bytes(p.size)}</span></span></button>`;
  }).join("");
  return `<div class="sp-cols">${cols}</div>`;
}

function spectrumRow(total, per) {
  const f = S.f;
  const segs = BUCKETS.map((b) => {
    const p = per[b.id];
    if (!p.size) return "";
    const on = f.bucket === b.id;
    const inPct = (p.inSize / p.size) * 100;
    return `<span class="sp-seg${on ? " is-on" : ""}${f.bucket && !on ? " is-dim" : ""}" data-bucket="${b.id}" style="flex-grow:${(p.size / total) * 1000};--c:${b.color}"
      title="${bucketLabel(b, p)}${p.inSize !== p.size && !f.bucket ? `, ${bytes(p.inSize)} of it in this list` : ""}"><span class="sp-in" style="width:${inPct.toFixed(1)}%"></span></span>`;
  }).join("");
  const legend = BUCKETS.filter((b) => per[b.id].count || f.bucket === b.id).map((b) => {
    const p = per[b.id];
    const on = f.bucket === b.id;
    return `<button type="button" class="leg${on ? " is-on" : ""}${f.bucket && !on ? " is-dim" : ""}" data-bucket="${b.id}" aria-pressed="${on}"
      aria-label="${bucketLabel(b, p)}. ${on ? "Showing only these" : "Show only these"}">
      <span class="dot" style="--c:${b.color}"></span><span class="leg-l"><span class="l-long">${esc(b.label)}</span><span class="l-short">${esc(b.short)}</span></span><span class="leg-v">${bytes(p.size)}</span></button>`;
  }).join("");
  return `<div class="sp-bar" aria-hidden="true">${segs}</div><div class="legend">${legend}</div>`;
}

new ResizeObserver(() => {
  const el = $("#spectrum");
  if (S.spectrum && !el.hidden && String(el.clientWidth) !== el.dataset.w) renderSpectrum(S.spectrum.total, S.spectrum.per);
}).observe($("#spectrum"));

function toggleBucket(id) {
  S.f.bucket = S.f.bucket === id ? "" : id;
  apply();
}

/* ------------------------------------------------------------ rows */

function renderSortHead() {
  $$("#listHead [data-sort]").forEach((b) => {
    const k = b.dataset.sort;
    const on = k === S.f.sort;
    b.classList.toggle("is-on", on);
    b.classList.toggle("is-asc", on && S.f.dir === "asc");
    const next = on ? (S.f.dir === "asc" ? "desc" : "asc") : SORTS[k].dir;
    b.setAttribute("aria-label", `${sortLabel(k)}${on ? `, sorted ${SORTS[k][S.f.dir]}` : ""}. Sort ${SORTS[k][next]}`);
  });
}

function setSort(k) {
  if (S.f.sort === k) S.f.dir = S.f.dir === "asc" ? "desc" : "asc";
  else { S.f.sort = k; S.f.dir = SORTS[k].dir; }
  apply();
}

function rowHtml(r) {
  const sel = S.selected.has(r.rating_key);
  const isSeason = r.kind === "season";
  let title;
  const sub = [];
  if (isSeason) {
    title = `${esc(r.show_title || "Unknown show")}<span class="yr">${esc(r.title)}</span>`;
    sub.push(plural(r.episode_count, "episode"));
    if (r.excluded_via_show) sub.push("show is kept");
  } else {
    title = `${esc(r.title)}${r.year ? `<span class="yr">${r.year}</span>` : ""}`;
    if (r.kind === "show") sub.push(`${plural(r.season_count, "season")}, ${plural(r.episode_count, "episode")}`);
    else if (!S.f.lib && S.libraries.filter((l) => l.type === "movie").length > 1) sub.push(libName(r.section_id));
  }
  if (r.quality_label) sub.push(r.quality_label);
  if (r.versions > 1 && r.kind === "movie") sub.push(`${r.versions} versions`);
  const tags = [];
  if (r.excluded) tags.push(`<span class="tag kept">${icon("keep")}Kept</span>`);
  if (r.leaving_at) tags.push(`<span class="tag leaving" title="Leaves ${esc(dayDate(r.leaving_at))} unless someone watches it">${icon("clock")}${esc(shortDate(r.leaving_at))}</span>`);
  let req = "";
  if (r.requested_by) {
    const unwatched = r.requester_watched === false;
    req = `<span class="req${unwatched ? " unwatched" : ""}" title="Requested by ${esc(r.requested_by)}${unwatched ? ", who hasn't watched it" : r.requester_watched ? ", who has watched it" : ""}">${icon(unwatched ? "eye-off" : "user")}${esc(r.requested_by)}</span>`;
  }
  const t = tint(r.last_played);
  const w = Math.max(0.6, (r.size / S.maxSize) * 100);
  return `<li class="row${sel ? " is-sel" : ""}${r.excluded ? " is-kept" : ""}" data-rk="${r.rating_key}">
    <label class="cb"><input type="checkbox" ${sel ? "checked" : ""} aria-label="Select ${esc(nameOf(r))}"></label>
    ${posterImg(r.thumb, 120)}
    <div class="t-main">
      <div class="t-title"><a class="t-link" href="?item=${r.rating_key}">${title}</a>${tags.join("")}</div>
      <div class="t-sub"><span class="t-sub-text">${esc(sub.join(" · "))}</span>${req}</div>
    </div>
    <div class="shelf" style="--tint:${t}"><div class="bar"><div class="fill" style="width:${w.toFixed(2)}%"></div></div><span class="num">${bytes(r.size)}</span></div>
    <div class="when${r.last_played ? "" : " never"}" style="--tint:${t}"><span class="ago"><span class="dot"></span>${ago(r.last_played)}</span>${r.last_user ? `<span class="who">${esc(r.last_user)}</span>` : ""}</div>
    <div class="plays">${r.plays ? `<b>${r.plays.toLocaleString()}</b> ${r.plays === 1 ? "play" : "plays"}${r.users > 1 ? `<span class="who">${r.users} people</span>` : ""}` : `<span class="none">No plays</span>`}</div>
    <div class="added">${monthYear(r.added_at)}</div>
  </li>`;
}

function renderList(reset) {
  const list = $("#list");
  if (reset) { list.innerHTML = ""; S.rendered = 0; }
  if (!S.loaded) return;
  const next = S.filtered.slice(S.rendered, S.rendered + PAGE);
  list.insertAdjacentHTML("beforeend", next.map(rowHtml).join(""));
  S.rendered += next.length;
  renderEmpty();
}

function renderSkeleton() {
  const line = (w) => `<span class="sk" style="width:${w}%"></span>`;
  $("#list").innerHTML = Array.from({ length: 9 }, (_, i) => `<li class="row skel" aria-hidden="true">
    <span class="cb"></span><span class="poster"></span>
    <div class="t-main">${line(40 + ((i * 17) % 35))}${line(25 + ((i * 11) % 20))}</div>
    <div class="shelf">${line(100 - i * 9)}</div><div class="when">${line(70)}</div><div class="plays">${line(50)}</div><div class="added">${line(60)}</div></li>`).join("");
  $("#empty").hidden = true;
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
        ? `<h2>Sync failed</h2><p>${esc(st.sync.last_error)}</p><p><button class="btn" type="button" data-sync>Try again</button></p>`
        : `<h2>No library data yet</h2><p>Run a sync to pull your movies, shows and watch history from Tautulli.</p><p><button class="btn" type="button" data-sync>Sync now</button></p>`;
  } else if (!S.filtered.length) {
    html = S.f.q
      ? `<h2>Nothing called “${esc(S.f.q)}” here</h2><p>Check the spelling, or look in a different library or with fewer filters.</p>`
      : `<h2>Nothing matches</h2><p>Everything in this view was watched recently, or the filters are narrow. Try a shorter “Not watched in” window.</p>${isDirty() ? `<p><button class="btn ghost" type="button" data-reset>Reset filters</button></p>` : ""}`;
  }
  e.innerHTML = html;
  e.hidden = !html;
  $("#listHead").hidden = !S.filtered.length;
}

new IntersectionObserver((ents) => {
  if (ents.some((e) => e.isIntersecting) && S.rendered < S.filtered.length) renderList(false);
}, { rootMargin: "900px" }).observe($("#sentinel"));

function focusRow(idx) {
  if (!S.filtered.length) return;
  idx = clamp(idx, 0, S.filtered.length - 1);
  while (idx >= S.rendered && S.rendered < S.filtered.length) renderList(false);
  const li = $(`#list .row[data-rk="${S.filtered[idx].rating_key}"]`);
  if (!li) return;
  $(".t-link", li).focus({ preventScroll: true });
  li.scrollIntoView({ block: "nearest" });
}

function focusedRow() {
  const li = document.activeElement && document.activeElement.closest && document.activeElement.closest("#list .row");
  return li ? S.filtered.find((r) => r.rating_key === +li.dataset.rk) : null;
}

function moveFocus(delta) {
  const cur = focusedRow();
  const idx = cur ? S.filtered.indexOf(cur) + delta : delta > 0 ? 0 : S.filtered.length - 1;
  focusRow(idx);
}

/* ------------------------------------------------------------ selection */

const selectedRows = () => S.filtered.filter((r) => S.selected.has(r.rating_key));

function renderSelbar() {
  const n = S.selected.size;
  $("#selbar").hidden = !n || S.tab !== "cull";
  document.body.classList.toggle("has-sel", !!n && S.tab === "cull");
  const all = $("#selAll");
  const selectable = S.filtered.filter((r) => !r.excluded).length;
  all.checked = n > 0 && n >= selectable;
  all.indeterminate = n > 0 && n < selectable;
  if (!n) return;
  const rows = selectedRows();
  const size = rows.reduce((a, r) => a + r.size, 0);
  $("#selText").innerHTML = `<b>${n.toLocaleString()}</b> selected <span class="sel-size">${bytes(size)}</span>`;
  const deletable = rows.some((r) => !r.excluded && canDelete(r.kind));
  $("#selDelete").hidden = $("#selLeaving").hidden = !canDelete();
  $("#selDelete").disabled = $("#selLeaving").disabled = !deletable;
  $("#selKeep").disabled = rows.every((r) => r.excluded);
}

function paintSelection() {
  $$("#list .row").forEach((li) => {
    const s = S.selected.has(+li.dataset.rk);
    li.classList.toggle("is-sel", s);
    const box = $("input", li);
    if (box) box.checked = s;
  });
  renderSelbar();
}

function toggleSelect(rk, on, range) {
  if (range && S.lastClicked != null) {
    const ids = S.filtered.map((r) => r.rating_key);
    const a = ids.indexOf(S.lastClicked), b = ids.indexOf(rk);
    if (a > -1 && b > -1) for (const id of ids.slice(Math.min(a, b), Math.max(a, b) + 1)) on ? S.selected.add(id) : S.selected.delete(id);
  } else {
    on ? S.selected.add(rk) : S.selected.delete(rk);
  }
  S.lastClicked = rk;
  paintSelection();
}

function selectAll(on) {
  S.selected.clear();
  if (on) for (const r of S.filtered) if (!r.excluded) S.selected.add(r.rating_key);
  paintSelection();
}

function clearSelection() {
  S.selected.clear();
  S.lastClicked = null;
  paintSelection();
}

/* ------------------------------------------------------------ Free up */

function openFreeUp() {
  const btn = $("#freeBtn");
  if (popAnchor === btn) { closePop(); return; }
  const pool = S.filtered.filter((r) => !r.excluded);
  const total = pool.reduce((a, r) => a + r.size, 0);
  const goal = store.get("freeGoal", { amount: 500, unit: "GB" });
  const quick = [[100, "GB"], [250, "GB"], [500, "GB"], [1, "TB"], [2, "TB"]];
  openPop(btn, `<form class="freeup" id="freeForm">
    <label class="freeup-l" for="freeAmt">Free up at least</label>
    <div class="free-row"><input id="freeAmt" type="number" min="0.1" step="any" inputmode="decimal" value="${goal.amount}" required>
      <select id="freeUnit" aria-label="Unit"><option${goal.unit === "GB" ? " selected" : ""}>GB</option><option${goal.unit === "TB" ? " selected" : ""}>TB</option></select></div>
    <div class="free-quick">${quick.map(([a, u]) => `<button type="button" class="chip" data-amt="${a}" data-unit="${u}">${a} ${u}</button>`).join("")}</div>
    <p class="hint">Selects from the top of this list (${esc(sortPhrase())}) until the goal is met. The list holds ${bytes(total)}.</p>
    <button class="btn wide" type="submit">${icon("target")}Select titles</button></form>`, "pop-free");
  $("#freeAmt").select();
}

function freeUp(amount, unit) {
  const target = amount * 1024 ** (unit === "TB" ? 4 : 3);
  if (!(target > 0)) return;
  store.set("freeGoal", { amount, unit });
  S.selected.clear();
  let sum = 0, n = 0;
  for (const r of S.filtered) {
    if (r.excluded) continue;
    if (sum >= target) break;
    S.selected.add(r.rating_key);
    sum += r.size;
    n++;
  }
  closePop();
  paintSelection();
  if (!n) toast("Nothing in this list to select");
  else if (sum < target) toast(`This list only holds ${bytes(sum)}, so all ${plural(n, noun())} are selected`);
  else toast(`Selected ${plural(n, noun())}, ${bytes(sum)}`);
}

/* ------------------------------------------------------------ CSV */

function exportCsv(rows, name) {
  const head = ["Title", "Season", "Year", "Type", "Library", "Size (GB)", "Size (bytes)", "Quality", "Last watched", "Last watched by", "Plays", "People", "Added", "Requested by", "Leaving on", "Kept"];
  const lines = rows.map((r) => [
    r.kind === "season" ? r.show_title : r.title, r.kind === "season" ? r.title : "", r.year || "", r.kind, libName(r.section_id),
    (r.size / 1024 ** 3).toFixed(2), r.size, r.quality_label || "", r.last_played ? ymd(r.last_played) : "never", r.last_user || "",
    r.plays, r.users, ymd(r.added_at), r.requested_by || "", r.leaving_at ? ymd(r.leaving_at) : "", r.excluded ? "yes" : "",
  ]);
  const csv = [head, ...lines].map((l) => l.map((v) => `"${String(v ?? "").replace(/"/g, '""')}"`).join(",")).join("\r\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8" }));
  a.download = `shelflife-${name || S.f.view}-${ymd(now())}.csv`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

/* ------------------------------------------------------------ keep list helpers + data */

async function keepKeys(keys, note) {
  const res = keys.length === 1
    ? await put(`/api/exclusions/${keys[0]}`, note === undefined ? {} : { note })
    : await post("/api/exclusions/bulk", { rating_keys: keys });
  for (const k of keys) S.selected.delete(k);
  await refreshData();
  return res;
}

async function unkeepKeys(keys) {
  await Promise.all(keys.map((k) => del(`/api/exclusions/${k}`)));
  await refreshData();
}

async function refreshData() {
  const [titles, seasons] = await Promise.all([api("/api/items"), S.seasons ? api("/api/items?view=seasons") : Promise.resolve(null)]);
  S.titles = titles;
  S.seasons = seasons;
  loadStatus().catch(() => {});
  apply();
}

/* ------------------------------------------------------------ events */

function bindList() {
  let qt;
  $("#fSearch").addEventListener("input", (e) => { S.f.q = e.target.value; clearTimeout(qt); qt = setTimeout(apply, 120); });
  $("#fSearch").addEventListener("keydown", (e) => {
    if (e.key === "Escape") { if (e.target.value) { e.target.value = ""; S.f.q = ""; apply(); } else e.target.blur(); e.stopPropagation(); }
    if (e.key === "ArrowDown" || (e.key === "Enter" && S.filtered.length)) { e.preventDefault(); focusRow(0); }
  });
  $$("#fView button").forEach((b) => b.addEventListener("click", () => {
    if (b.disabled || S.f.view === b.dataset.view) return;
    S.f.view = b.dataset.view;
    S.selected.clear();
    S.rendered = 0;
    ensureView().then(apply);
  }));

  const toolbar = $(".toolbar");
  toolbar.addEventListener("click", (e) => {
    const chip = e.target.closest("[data-chip]");
    if (chip) { openChip(chip); return; }
    if (e.target.closest("[data-clear-bucket]")) { S.f.bucket = ""; apply(); return; }
    if (e.target.closest("[data-toggle-kept]")) { S.f.kept = !S.f.kept; apply(); return; }
    if (e.target.closest("[data-reset]")) resetFilters();
  });
  $("#filtersBtn").addEventListener("click", openSheet);
  $("#exportAll").addEventListener("click", () => exportCsv(S.filtered));

  const pop = $("#pop");
  pop.addEventListener("change", (e) => {
    const g = e.target.closest("[data-group]");
    if (g && e.target.matches("input[type=radio]")) setFilter(g.dataset.group, e.target.value);
  });
  pop.addEventListener("pointerup", (e) => { if (e.target.closest(".opt")) setTimeout(() => closePop(true), 80); });
  pop.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closePop(true); }
    else if (e.key === "Enter" && e.target.matches("input[type=radio]")) { e.preventDefault(); closePop(true); }
  });
  pop.addEventListener("focusout", (e) => { if (e.relatedTarget && !pop.contains(e.relatedTarget) && e.relatedTarget !== popAnchor) closePop(); });
  pop.addEventListener("click", (e) => {
    const q = e.target.closest("[data-amt]");
    if (q) freeUp(+q.dataset.amt, q.dataset.unit);
  });
  pop.addEventListener("submit", (e) => {
    if (e.target.id !== "freeForm") return;
    e.preventDefault();
    freeUp(parseFloat($("#freeAmt").value), $("#freeUnit").value);
  });
  document.addEventListener("pointerdown", (e) => {
    if (!pop.hidden && !pop.contains(e.target) && !(popAnchor && popAnchor.contains(e.target))) closePop();
  });
  addEventListener("resize", () => closePop());

  const sheet = $("#sheet");
  sheet.addEventListener("change", (e) => {
    if (e.target.matches("[data-kept-switch]")) { S.f.kept = e.target.checked; apply(); return; }
    const g = e.target.closest("[data-group]");
    if (!g || !e.target.matches("input[type=radio]")) return;
    if (g.dataset.group === "view") { S.f.view = e.target.value; S.selected.clear(); ensureView().then(apply); }
    else setFilter(g.dataset.group, e.target.value);
  });
  sheet.addEventListener("click", (e) => {
    if (e.target.closest("[data-flip-dir]")) { S.f.dir = S.f.dir === "asc" ? "desc" : "asc"; apply(); }
    if (e.target.closest("[data-clear-bucket]")) { S.f.bucket = ""; apply(); }
    if (e.target === sheet) sheet.close();
  });
  $("#sheetDone").addEventListener("click", () => sheet.close());
  $("#sheetReset").addEventListener("click", resetFilters);

  $("#spectrum").addEventListener("click", (e) => {
    const b = e.target.closest("[data-bucket]");
    if (b && !b.disabled) toggleBucket(b.dataset.bucket);
  });
  $("#subline").addEventListener("click", (e) => { if (e.target.closest("[data-toggle-kept]")) { S.f.kept = true; apply(); } });
  $("#empty").addEventListener("click", (e) => {
    if (e.target.closest("[data-sync]")) startSync();
    if (e.target.closest("[data-reset]")) resetFilters();
  });

  $$("#listHead [data-sort]").forEach((b) => b.addEventListener("click", () => setSort(b.dataset.sort)));
  $("#selAll").addEventListener("change", (e) => selectAll(e.target.checked));

  const list = $("#list");
  list.addEventListener("click", (e) => {
    const li = e.target.closest(".row");
    if (!li || li.classList.contains("skel")) return;
    const rk = +li.dataset.rk;
    if (e.target.closest(".cb")) {
      if (e.target.matches("input")) toggleSelect(rk, e.target.checked, e.shiftKey);
      return;
    }
    if (e.target.closest("a") && (e.metaKey || e.ctrlKey || e.shiftKey)) return; // new tab / window
    e.preventDefault();
    openItem(rk);
  });
  list.addEventListener("auxclick", (e) => { if (e.button === 1 && !e.target.closest("a")) { const li = e.target.closest(".row"); if (li) open(`?item=${li.dataset.rk}`, "_blank"); } });

  $("#freeBtn").addEventListener("click", openFreeUp);
  $("#reviewBtn").addEventListener("click", () => startReview());
  $("#selKeep").addEventListener("click", keepSelection);
  $("#selLeaving").addEventListener("click", () => openPlan([...S.selected], "leaving"));
  $("#selDelete").addEventListener("click", () => openPlan([...S.selected], "delete", { source: "bulk" }));
  $("#selReview").addEventListener("click", () => startReview(new Set(S.selected)));
  $("#selExport").addEventListener("click", () => exportCsv(selectedRows(), "selection"));
  $("#selClear").addEventListener("click", clearSelection);
}

async function keepSelection() {
  const keys = selectedRows().filter((r) => !r.excluded).map((r) => r.rating_key);
  if (!keys.length) return;
  try {
    const res = await keepKeys(keys);
    const extra = res && res.leaving_cancelled ? ` and took ${plural(res.leaving_cancelled, "title")} off Leaving soon` : "";
    toast(`Kept ${plural(keys.length, noun())}${extra}`, () => unkeepKeys(keys).catch(fail));
  } catch (e) { fail(e); }
}
