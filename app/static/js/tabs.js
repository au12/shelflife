"use strict";
/* Tabs: Leaving soon, Keep list, History. */

const SOURCE = { manual: "Title panel", bulk: "Selection", review: "Review", leaving: "Leaving soon" };
const pageSkeleton = () => `<div class="page-head"><span class="sk" style="width:220px;height:30px"></span><span class="sk" style="width:min(520px,90%);margin-top:14px"></span></div>
  <ul class="plist">${Array.from({ length: 5 }, () => `<li class="prow skel"><span class="poster"></span><div><span class="sk" style="width:45%"></span><span class="sk" style="width:25%"></span></div></li>`).join("")}</ul>`;
const pageError = (e) => `<div class="empty"><h2>Couldn't load this page</h2><p>${esc(e.message)}</p></div>`;

async function showTab(tab, { push = false } = {}) {
  if (!TABS.includes(tab)) tab = "cull";
  const changed = S.tab !== tab;
  S.tab = tab;
  $$(".tab").forEach((a) => { if (a.dataset.tab === tab) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current"); });
  const current = $(".tab[aria-current]");
  if (current) current.scrollIntoView({ block: "nearest", inline: "nearest" });  // the tab row scrolls on phones
  for (const t of TABS) $(`#view-${t}`).hidden = t !== tab;
  closePop();
  renderSelbar();
  if (push && changed) history.pushState({ d: 0 }, "", urlFor({ item: null }));
  else writeUrl();
  if (changed) scrollTo(0, 0);
  document.title = tab === "cull" ? "Shelflife" : `${{ leaving: "Leaving soon", keep: "Keep list", history: "History", issues: "Issues" }[tab]} · Shelflife`;
  if (tab === "leaving") await renderLeaving();
  if (tab === "keep") await renderKeep();
  if (tab === "history") await renderHistory();
  if (tab === "issues") await renderIssues();
}

/* ------------------------------------------------------------ Leaving soon */

function leavingRow(r) {
  const title = `${esc(r.title)}${r.year && r.kind !== "season" ? ` <span class="yr">${r.year}</span>` : ""}`;
  const link = r.in_library ? `<a class="t-link" href="?item=${r.rating_key}" data-open="${r.rating_key}">${title}</a>` : `<span class="t-link">${title}</span>`;
  const sub = [KIND[r.kind], r.size ? bytes(r.size) : null, r.kind === "show" && r.mode === "files" ? "files only, stays in Sonarr" : null].filter(Boolean).map(esc).join(" · ");
  let side = "", acts = "", note = r.note ? `<span class="pnote">${esc(r.note)}</span>` : "";
  if (r.status === "scheduled") {
    const span = Math.max(1, r.delete_at - r.scheduled_at);
    const pct = clamp(((now() - r.scheduled_at) / span) * 100, 0, 100);
    side = `<div class="lv-when"><b>${esc(until(r.delete_at))}</b><span>${esc(dayDate(r.delete_at))}</span><span class="lv-track" aria-hidden="true"><span style="width:${pct.toFixed(1)}%"></span></span></div>`;
    acts = `<button class="btn ghost small" type="button" data-act="extend" title="Push the date back a week">${icon("plus")}7 days</button>
      <button class="btn ghost small" type="button" data-act="cancel">Don't delete</button>
      <button class="btn warn small" type="button" data-act="now">Delete now</button>`;
    if (r.note && r.note.startsWith("Couldn't")) note = `<span class="pnote is-err">${icon("alert")}${esc(r.note)}</span>`;
  } else if (r.status === "failed") {
    side = `<div class="lv-when is-err"><b>Couldn't delete</b><span>${esc(shortDate(r.status_at))}</span></div>`;
    note = `<span class="pnote is-err">${icon("alert")}${esc(r.note || "Delete failed")}</span>`;
    acts = `<button class="btn ghost small" type="button" data-act="now">Try again</button><button class="btn ghost small" type="button" data-act="cancel">Take it off the list</button>`;
  } else {
    const what = { spared: "Stayed", deleted: "Deleted", cancelled: "Taken off the list", gone: "Gone from Plex" }[r.status] || r.status;
    side = `<div class="lv-when done ${r.status}"><b>${esc(what)}</b><span>${esc(shortDate(r.status_at))}</span></div>`;
  }
  return `<li class="prow lv-${r.status}" data-rk="${r.rating_key}" data-title="${esc(r.title)}">
    ${posterImg(r.thumb, 120)}
    <div class="pmain">${link}<span class="psub">${sub}</span>${note}</div>
    ${side}
    ${acts ? `<div class="pacts">${acts}</div>` : ""}
  </li>`;
}

async function renderLeaving() {
  const el = $("#view-leaving");
  if (!el.dataset.loaded) el.innerHTML = pageSkeleton();
  let data;
  try { data = await api("/api/leaving"); } catch (e) { el.innerHTML = pageError(e); return; }
  if (S.tab !== "leaving") return;
  el.dataset.loaded = "1";
  const items = data.items;
  const sched = items.filter((r) => r.status === "scheduled");
  const failed = items.filter((r) => r.status === "failed");
  const recent = items.filter((r) => r.status !== "scheduled" && r.status !== "failed");
  const size = sched.reduce((a, r) => a + (r.size || 0), 0);
  const col = data.collection;
  let colNote = "";
  if (!col.available) colNote = "Add a Plex token and Shelflife will also list these in a Plex collection, so everyone can see what's going.";
  else if (!col.enabled) colNote = "The Plex collection is turned off in Settings.";
  else if (sched.some((r) => r.kind !== "season")) colNote = `In Plex, they're in the “${esc(col.name)}” collection${col.libraries.length ? ` in ${col.libraries.map(esc).join(" and ")}` : ""}.`;

  let lead;
  if (sched.length) {
    lead = `<span class="big">${bytes(size)}</span> in ${plural(sched.length, "title")}. The next one goes ${esc(until(sched[0].delete_at))}.`;
  } else {
    lead = "Nothing is scheduled to leave.";
  }
  const explain = canDelete()
    ? `Each title is deleted on its date through Sonarr or Radarr, unless someone watches it first. Then it stays. ${colNote}`
    : "Sonarr and Radarr aren't set up, so nothing can be scheduled to leave yet.";

  el.innerHTML = `<header class="page-head"><h1>Leaving soon</h1><p class="lead">${lead}</p><p class="explain">${explain}</p>
      ${!sched.length && canDelete() ? `<p class="explain">To schedule titles, select them in the cull list and choose <b>Leaving soon</b>, or open a title and use <b>Leaving soon…</b> there.</p>` : ""}</header>
    ${failed.length ? `<section class="psec"><h2>Needs attention <span>${failed.length}</span></h2><ul class="plist">${failed.map(leavingRow).join("")}</ul></section>` : ""}
    ${sched.length ? `<section class="psec"><h2>Scheduled <span>${sched.length}</span></h2><ul class="plist">${sched.map(leavingRow).join("")}</ul></section>` : ""}
    ${recent.length ? `<section class="psec"><h2>Last 30 days <span>${recent.length}</span></h2><ul class="plist">${recent.map(leavingRow).join("")}</ul></section>` : ""}`;
}

async function leavingAction(li, act) {
  const rk = +li.dataset.rk;
  const title = li.dataset.title;
  const btn = $(`[data-act="${act}"]`, li);
  try {
    if (act === "extend") {
      const res = await post(`/api/leaving/${rk}/extend`, { days: 7 });
      toast(`${title} now leaves ${until(res.delete_at)}`);
    } else if (act === "cancel") {
      await del(`/api/leaving/${rk}`);
      toast(`${title} won't be deleted`);
    } else if (act === "now") {
      const ok = await confirmDlg({ title: `Delete ${title} now?`, body: `<p>Shelflife deletes it through Sonarr or Radarr right away instead of waiting. This can't be undone.</p>`, ok: "Delete now", danger: true });
      if (!ok) return;
      if (btn) { btn.disabled = true; btn.textContent = "Deleting…"; }
      const res = await post(`/api/leaving/${rk}/now`);
      toast(`${res.message}${res.freed ? `, ${bytes(res.freed)} freed` : ""}`);
      loadStorage();
    }
  } catch (e) {
    fail(e);
  }
  renderLeaving();
  refreshData().catch(() => {});
}

/* ------------------------------------------------------------ Keep list */

async function renderKeep() {
  const el = $("#view-keep");
  if (!el.dataset.loaded) el.innerHTML = pageSkeleton();
  let rows;
  try { rows = await api("/api/exclusions"); } catch (e) { el.innerHTML = pageError(e); return; }
  if (S.tab !== "keep") return;
  el.dataset.loaded = "1";
  S.keepRows = rows;
  const size = rows.reduce((a, r) => a + (r.size || 0), 0);
  el.innerHTML = `<header class="page-head"><h1>Keep list</h1>
      <p class="lead">${rows.length ? `<span class="big">${bytes(size)}</span> in ${plural(rows.length, "title")} you're keeping.` : "Nothing kept yet."}</p>
      <p class="explain">Kept titles never show up in the cull list and can't be deleted from Shelflife. Keeping a show keeps all of its seasons.
      ${rows.length ? "" : "Open any title and choose <b>Keep</b>, or select a few rows and use <b>Keep</b> in the bar at the bottom."}</p>
      ${rows.length > 8 ? `<label class="search page-search">${icon("search")}<input type="search" id="keepSearch" placeholder="Search the keep list" aria-label="Search the keep list"></label>` : ""}</header>
    <ul class="plist keeplist" id="keepList">${rows.map(keepRow).join("")}</ul>`;
}

function keepRow(r) {
  const title = `${esc(r.title)}${r.year && r.kind !== "season" ? ` <span class="yr">${r.year}</span>` : ""}`;
  return `<li class="prow" data-rk="${r.rating_key}" data-q="${esc(`${r.title} ${r.note || ""}`.toLowerCase())}">
    ${posterImg(r.thumb, 120)}
    <div class="pmain">${r.in_library ? `<a class="t-link" href="?item=${r.rating_key}" data-open="${r.rating_key}">${title}</a>` : `<span class="t-link">${title}</span>`}
      <span class="psub">${esc(r.kind === "show" ? "Whole show" : KIND[r.kind] || "")} · kept ${esc(fullDate(r.created_at))}${r.in_library ? "" : " · no longer in Plex"}</span></div>
    <input class="note" type="text" maxlength="500" placeholder="Why keep it? (optional)" value="${esc(r.note || "")}" aria-label="Note for ${esc(r.title)}">
    <span class="psize">${r.size ? bytes(r.size) : ""}</span>
    <div class="pacts"><button class="btn ghost small" type="button" data-act="unkeep">Stop keeping</button></div>
  </li>`;
}

/* ------------------------------------------------------------ History */

async function renderHistory() {
  const el = $("#view-history");
  if (!el.dataset.loaded) el.innerHTML = pageSkeleton();
  let h;
  try { h = await api("/api/history"); } catch (e) { el.innerHTML = pageError(e); return; }
  if (S.tab !== "history") return;
  el.dataset.loaded = "1";
  const lead = h.count
    ? `<span class="big">${bytes(h.size)}</span> freed by ${plural(h.count, "deletion")} since ${esc(fullDate(h.since))}.`
    : "Nothing deleted yet.";
  el.innerHTML = `<header class="page-head"><h1>History</h1><p class="lead">${lead}</p>
      <p class="explain">Everything Shelflife deletes is listed here, whether you deleted it yourself or it left on its Leaving soon date.</p></header>
    ${h.items.length ? `<div class="tbl-wrap page-tbl"><table class="hist">
      <thead><tr><th>When</th><th>Title</th><th class="r">Freed</th><th>What happened</th><th class="src">From</th></tr></thead>
      <tbody>${h.items.map((r) => `<tr>
        <td class="nowrap">${esc(fullDate(r.at))}<span class="ep">${hm(r.at)}</span></td>
        <td class="title-cell"><span class="ht">${posterImg(r.thumb, 120, "poster tiny")}<span>${esc(r.title)}${r.year && r.kind !== "season" ? ` <span class="yr">${r.year}</span>` : ""}<span class="ep">${esc(KIND[r.kind] || "")}</span></span></span></td>
        <td class="r nowrap">${bytes(r.size)}</td>
        <td>${esc(r.action || "")}</td>
        <td class="src">${esc(SOURCE[r.source] || r.source || "")}</td></tr>`).join("")}</tbody></table></div>
      ${h.count > h.items.length ? `<p class="hint">Showing the latest ${h.items.length.toLocaleString()}.</p>` : ""}` : ""}`;
}

function bindTabs() {
  $$(".tab").forEach((a) => a.addEventListener("click", (e) => {
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
    e.preventDefault();
    if (S.openKey) closeDetail();
    showTab(a.dataset.tab, { push: true });
  }));
  $(".brand").addEventListener("click", (e) => {
    if (e.metaKey || e.ctrlKey || e.shiftKey) return;
    e.preventDefault();
    if (S.openKey) closeDetail();
    showTab("cull", { push: true });
  });

  for (const id of ["view-leaving", "view-keep"]) {
    $(`#${id}`).addEventListener("click", async (e) => {
      const open = e.target.closest("[data-open]");
      if (open) {
        if (e.metaKey || e.ctrlKey || e.shiftKey) return;
        e.preventDefault();
        openItem(+open.dataset.open);
        return;
      }
      const b = e.target.closest("[data-act]");
      const li = e.target.closest(".prow");
      if (!b || !li) return;
      if (id === "view-leaving") { leavingAction(li, b.dataset.act); return; }
      const rk = +li.dataset.rk;
      try {
        await unkeepKeys([rk]);
        li.remove();
        toast("Back on the cull list", async () => { await keepKeys([rk]); renderKeep(); });
        renderKeep();
      } catch (err) { fail(err); }
    });
  }
  $("#view-keep").addEventListener("change", async (e) => {
    if (!e.target.matches(".note")) return;
    const rk = +e.target.closest(".prow").dataset.rk;
    try { await put(`/api/exclusions/${rk}`, { note: e.target.value }); toast("Note saved"); } catch (err) { fail(err); }
  });
  $("#view-keep").addEventListener("input", (e) => {
    if (e.target.id !== "keepSearch") return;
    const q = e.target.value.trim().toLowerCase();
    $$("#keepList .prow").forEach((li) => { li.hidden = q && !li.dataset.q.includes(q); });
  });
}
