"use strict";
/* Issues tab: where Plex and Sonarr/Radarr disagree. */

// [type, service, heading, what it means]
const ISSUE_SECTIONS = [
  ["shared", "radarr", "Same Radarr movie",
    "Each of these has two Plex copies that match one Radarr movie, often because a 4K library is run by a second Radarr. Deleting goes through that one entry, so Shelflife won't delete a copy whose files Radarr doesn't manage."],
  ["shared", "sonarr", "Same Sonarr show",
    "Each of these has two Plex copies that match one Sonarr show. Deleting goes through that one entry, so Shelflife won't delete a copy whose files Sonarr doesn't manage."],
  ["missing_arr", "radarr", "Not in Radarr",
    "Plex has these movies and Radarr doesn't, so Shelflife can't delete them and Radarr won't upgrade or replace them."],
  ["missing_arr", "sonarr", "Not in Sonarr",
    "Plex has these shows and Sonarr doesn't, so Shelflife can't delete them and Sonarr won't download new episodes."],
  ["missing_plex", "radarr", "In Radarr, not in Plex",
    "Radarr has files for these, but they aren't in any Plex library Shelflife reads. Check that the folder is part of a Plex library, then scan it."],
  ["missing_plex", "sonarr", "In Sonarr, not in Plex",
    "Sonarr has episode files for these, but they aren't in any Plex library Shelflife reads. Check that the folder is part of a Plex library, then scan it."],
  ["no_file", "radarr", "No file in Radarr",
    "Plex plays these, but Radarr has no file on record, so it may download them again. A rescan of the movie in Radarr usually picks the file up."],
  ["no_file", "sonarr", "No files in Sonarr",
    "Plex plays these, but Sonarr has no episode files on record. A rescan of the show in Sonarr usually picks them up."],
  ["untracked", "radarr", "More in Plex than Radarr tracks",
    "Usually a second version. Deleting through Radarr removes Radarr's folder; a copy kept anywhere else stays on disk."],
  ["untracked", "sonarr", "Episodes Sonarr doesn't track",
    "Plex has episodes Sonarr has no file for. Deleting with “Delete files, keep in Sonarr” only removes the files Sonarr tracks."],
  ["stale", "radarr", "Plex is behind Radarr",
    "Radarr's file differs from what Plex shows, usually an upgrade Plex hasn't scanned yet. Scanning the library in Plex fixes it."],
  ["stale", "sonarr", "Plex is behind Sonarr",
    "Sonarr has files for episodes Plex doesn't show yet. Scanning the library in Plex fixes it."],
];
const ISSUE_PAGE = 30;
const issueLabel = (i) => (ISSUE_SECTIONS.find(([t, s]) => t === i.type && s === i.service) || [0, 0, i.type])[2];
let issuesData = null;
let issuesShowIgnored = false;
let issuesTimer = null;
const issuesExpanded = new Set();

async function renderIssues() {
  const el = $("#view-issues");
  if (!el.dataset.loaded) el.innerHTML = pageSkeleton();
  try { issuesData = await api("/api/issues"); } catch (e) { el.innerHTML = pageError(e); return; }
  if (S.tab !== "issues") return;
  el.dataset.loaded = "1";
  drawIssues();
  if (issuesData.running) pollIssues();
}

function pollIssues() {
  clearTimeout(issuesTimer);
  issuesTimer = setTimeout(async () => {
    try { issuesData = await api("/api/issues"); } catch (_) { return; }
    if (S.tab === "issues") drawIssues();
    if (issuesData.running) pollIssues();
    else loadStatus().catch(() => {});
  }, 1200);
}

function servicesText(d) {
  const on = ["radarr", "sonarr"].filter((s) => d.enabled[s]).map((s) => SERVICE[s]);
  return on;
}

function issueDetail(i) {
  const p = i.plex || {}, a = i.arr || {};
  const svc = SERVICE[i.service];
  if (i.type === "no_file") return `${a.monitored ? "Monitored" : "Not monitored"} in ${svc}.`;
  if (i.type === "untracked" || i.type === "stale") {
    if (i.kind === "movie") {
      return i.type === "untracked"
        ? `Plex has ${bytes(p.size)}${p.versions > 1 ? ` in ${p.versions} versions` : ""}. Radarr tracks ${bytes(a.size)}.`
        : `Radarr has ${bytes(a.size)}. Plex shows ${bytes(p.size)}.`;
    }
    const seasons = (i.seasons || []).slice(0, 3).map((s) => `${s.season === 0 ? "Specials" : `Season ${s.season}`}: ${s.plex} in Plex, ${s.arr} in Sonarr`);
    const more = (i.seasons || []).length > 3 ? `, and ${i.seasons.length - 3} more seasons` : "";
    return `Plex has ${plural(p.episodes, "episode")}; Sonarr has files for ${a.files.toLocaleString()}.${seasons.length ? ` ${seasons.join(". ")}${more}.` : ""}`;
  }
  return "";
}

function issueRow(i, ignoredView = false) {
  const p = i.plex, a = i.arr || {};
  const svc = SERVICE[i.service];
  const acts = [];
  if (i.type === "missing_arr" && i.add_url) acts.push(`<a class="btn ghost small" href="${esc(i.add_url)}" target="_blank" rel="noopener">${icon("plus")}Add to ${svc}</a>`);
  if (a.url) acts.push(`<a class="btn ghost small" href="${esc(a.url)}" target="_blank" rel="noopener">${icon("external")}Show in ${svc}</a>`);
  acts.push(ignoredView
    ? `<button class="btn ghost small" type="button" data-unignore>Stop ignoring</button>`
    : `<button class="btn ghost small" type="button" data-ignore title="Hide this one; it stays hidden after later checks">Ignore</button>`);
  const label = ignoredView ? `<span class="itype">${esc(issueLabel(i))}</span>` : "";
  const yr = (t) => (t.year ? ` <span class="yr">${t.year}</span>` : "");
  let poster, main;
  if (i.type === "shared") {
    poster = posterImg((i.copies[0] || {}).thumb, 120);
    main = `<span class="t-link">${esc(a.title || i.copies[0].title)}${yr(a.year ? a : i.copies[0])}</span>${label}
      <ul class="copies">${i.copies.map((c) => `<li><a class="t-link" href="?item=${c.rating_key}" data-open="${c.rating_key}">${esc(c.title)}</a>
        <span class="psub">${esc([c.library, bytes(c.size), c.quality].filter(Boolean).join(" · "))}</span>
        ${c.matches_arr ? `<span class="tag mine" title="${svc}'s files on disk match this copy's size">${svc}'s files</span>` : ""}</li>`).join("")}</ul>`;
  } else if (i.type === "missing_plex") {
    poster = `<span class="poster ghost-poster" aria-hidden="true">${icon("disk")}</span>`;
    main = `<span class="t-link">${esc(a.title)}${yr(a)}</span>${label}
      <span class="psub">${esc(`${svc} · ${i.kind === "movie" ? bytes(a.size) : `${plural(a.files, "episode file")}, ${bytes(a.size)}`}`)}</span>
      ${a.path ? `<code class="ipath">${esc(a.path)}</code>` : ""}`;
  } else {
    const sub = i.kind === "movie" ? [p.library, bytes(p.size), p.quality] : [p.library, plural(p.episodes, "episode"), bytes(p.size)];
    const detail = issueDetail(i);
    poster = posterImg(p.thumb, 120);
    main = `<a class="t-link" href="?item=${p.rating_key}" data-open="${p.rating_key}">${esc(p.title)}${yr(p)}</a>${label}
      <span class="psub">${esc(sub.filter(Boolean).join(" · "))}</span>${detail ? `<span class="idetail">${esc(detail)}</span>` : ""}`;
  }
  return `<li class="prow irow" data-key="${esc(i.key)}">${poster}<div class="pmain">${main}</div><div class="pacts">${acts.join("")}</div></li>`;
}

function drawIssues() {
  const d = issuesData;
  const el = $("#view-issues");
  const active = d.items.filter((i) => !i.ignored);
  const ignoredItems = d.items.filter((i) => i.ignored);
  const svcs = servicesText(d);
  const either = svcs.join(" or ");
  let lead;
  if (!d.checked_at) lead = d.running ? "Checking for the first time…" : "Not checked yet.";
  else if (active.length) lead = `<span class="big">${active.length.toLocaleString()}</span> ${active.length === 1 ? "place" : "places"} where Plex and ${esc(either)} disagree.`;
  else lead = `Plex and ${esc(svcs.join(" and "))} agree.`;
  const compared = [];
  if (d.compared.radarr) compared.push(`${plural(d.compared.radarr.plex, "movie")} against Radarr`);
  if (d.compared.sonarr) compared.push(`${plural(d.compared.sonarr.plex, "show")} against Sonarr`);
  const status = d.running
    ? `<span class="checking">${icon("sync")}${esc(d.phase || "Checking")}${d.detail ? ` (${esc(d.detail)})` : ""}…</span>`
    : `${d.checked_at ? `Checked ${esc(since(d.checked_at))}${compared.length ? `: ${compared.join(" and ")}` : ""}. ` : ""}Shelflife checks again after every sync.
       <button class="linkish" type="button" data-check>Check now</button>`;
  const errors = Object.entries(d.errors || {}).map(([s, m]) => `<p class="err">${icon("alert")}${esc(SERVICE[s] || s)}: ${esc(m)}</p>`).join("");

  const sections = ISSUE_SECTIONS.map(([type, service, title, why]) => {
    const rows = active.filter((i) => i.type === type && i.service === service);
    if (!rows.length) return "";
    const id = `${type}:${service}`;
    const shown = issuesExpanded.has(id) ? rows : rows.slice(0, ISSUE_PAGE);
    const extra = type === "missing_plex" && d.ignored_libraries.length ? " Libraries Shelflife is set to skip (IGNORE_LIBRARIES) show up here too." : "";
    return `<section class="psec isec" data-sec="${id}">
      <div class="isec-head"><h2>${esc(title)} <span>${rows.length.toLocaleString()}</span></h2>
        ${rows.length > 1 ? `<button class="btn ghost small" type="button" data-ignore-all>Ignore all ${rows.length.toLocaleString()}</button>` : ""}</div>
      <p class="explain">${esc(why + extra)}</p>
      <ul class="plist">${shown.map((i) => issueRow(i)).join("")}</ul>
      ${shown.length < rows.length ? `<button class="btn ghost small more" type="button" data-more>Show all ${rows.length.toLocaleString()}</button>` : ""}
    </section>`;
  }).join("");

  const ignoredHtml = ignoredItems.length
    ? `<section class="psec ignored-sec"><button class="linkish" type="button" data-toggle-ignored>${issuesShowIgnored ? "Hide" : "Show"} ${plural(ignoredItems.length, "ignored issue")}</button>
       ${issuesShowIgnored ? `<ul class="plist">${ignoredItems.map((i) => issueRow(i, true)).join("")}</ul>` : ""}</section>`
    : "";

  el.innerHTML = `<header class="page-head"><h1>Issues</h1><p class="lead">${lead}</p>
      <p class="explain">${status}</p>${errors}
      ${!d.checked_at || active.length ? "" : `<p class="explain">Every movie and show in Plex is matched to ${esc(either)} the same way Shelflife matches before deleting, so what shows here is what a delete would do.</p>`}</header>
    ${sections}${ignoredHtml}`;
}

async function setIgnored(keys, ignored) {
  const res = await post("/api/issues/ignore", { key: keys[0], more: keys.slice(1), ignored });
  const set = new Set(keys);
  for (const i of issuesData.items) if (set.has(i.key)) i.ignored = ignored;
  $("#issuesCount").textContent = res.count || "";
  if (S.status && S.status.issues) S.status.issues.count = res.count;
  drawIssues();
}

function bindIssues() {
  const el = $("#view-issues");
  el.addEventListener("click", async (e) => {
    const open = e.target.closest("[data-open]");
    if (open) {
      if (e.metaKey || e.ctrlKey || e.shiftKey) return;
      e.preventDefault();
      openItem(+open.dataset.open);
      return;
    }
    const b = e.target.closest("button");
    if (!b || !issuesData) return;
    try {
      if (b.matches("[data-check]")) {
        b.disabled = true;
        issuesData = await post("/api/issues/check");
        drawIssues();
        pollIssues();
      } else if (b.matches("[data-ignore], [data-unignore]")) {
        const key = b.closest("[data-key]").dataset.key;
        const on = b.matches("[data-ignore]");
        await setIgnored([key], on);
        if (on) toast("Ignored. It stays hidden after later checks.", () => setIgnored([key], false).catch(fail));
      } else if (b.matches("[data-ignore-all]")) {
        const [type, service] = b.closest("[data-sec]").dataset.sec.split(":");
        const keys = issuesData.items.filter((i) => !i.ignored && i.type === type && i.service === service).map((i) => i.key);
        await setIgnored(keys, true);
        toast(`Ignored ${plural(keys.length, "issue")}`, () => setIgnored(keys, false).catch(fail));
      } else if (b.matches("[data-more]")) {
        issuesExpanded.add(b.closest("[data-sec]").dataset.sec);
        drawIssues();
      } else if (b.matches("[data-toggle-ignored]")) {
        issuesShowIgnored = !issuesShowIgnored;
        drawIssues();
      }
    } catch (err) { fail(err); }
  });
}
