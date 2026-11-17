"use strict";
/* The detail panel: backdrop, quality badges, actions, keep / leaving / request notes, files, seasons, history. */

const CODECS = { HEVC: "HEVC", H265: "HEVC", H264: "H.264", AVC: "H.264", AV1: "AV1", MPEG4: "MPEG-4", MPEG2VIDEO: "MPEG-2", VC1: "VC-1", VP9: "VP9" };
const codecName = (c) => (c ? CODECS[String(c).toUpperCase()] || String(c).toUpperCase() : "");

function openItem(rk) {
  if (S.openKey === rk) return;
  pushUrl({ item: rk });
  openDetail(rk);
}

function closeItem() {
  const d = (history.state && history.state.d) || 0;
  if (d > 0) history.go(-d);
  else { closeDetail(); writeUrl(); }
}

async function openDetail(rk) {
  const drawer = $("#drawer");
  const wasOpen = drawer.classList.contains("is-open");
  if (!wasOpen) S.returnFocus = document.activeElement;
  S.openKey = rk;
  S.arr = null;
  S.media = null;
  S.detail = null;
  drawer.classList.add("is-open");
  drawer.setAttribute("aria-hidden", "false");
  syncScrollLock();
  setInert(true);
  $("#dBody").innerHTML = detailSkeleton();
  $("#dBody").scrollTop = 0;
  if (!wasOpen) $(".drawer-panel").focus({ preventScroll: true });
  updateNav();
  try {
    const d = await api(`/api/item/${rk}`);
    if (S.openKey !== rk) return;
    S.detail = d;
    renderDetail();
    if (canDelete(d.item.kind)) loadArr(rk);
    loadMedia(rk);
  } catch (e) {
    if (S.openKey !== rk) return;
    $("#dBody").innerHTML = `<div class="empty"><h2>Couldn't open this title</h2><p>${esc(e.message)}</p></div>`;
  }
}

function closeDetail() {
  const drawer = $("#drawer");
  if (!drawer.classList.contains("is-open")) return;
  drawer.classList.remove("is-open");
  drawer.setAttribute("aria-hidden", "true");
  syncScrollLock();
  setInert(false);
  const last = S.openKey;
  S.openKey = null;
  S.detail = null;
  const row = last && $(`#list .row[data-rk="${last}"] .t-link`);
  const back = row || (S.returnFocus && document.contains(S.returnFocus) ? S.returnFocus : null);
  if (back) back.focus({ preventScroll: !!row });
  if (row) row.closest(".row").scrollIntoView({ block: "nearest" });
}

// Everything behind the open panel stops taking focus and clicks.
function setInert(on) {
  for (const el of [$("#top"), ...$$(".view"), $("#selbar")]) el.inert = on;
}

function updateNav() {
  const idx = S.filtered.findIndex((r) => r.rating_key === S.openKey);
  const inList = idx > -1 && S.tab === "cull";
  $("#dPrev").disabled = !inList || idx === 0;
  $("#dNext").disabled = !inList || idx === S.filtered.length - 1;
  $("#dBack").hidden = ((history.state && history.state.d) || 0) < 2;
}

function stepDetail(delta) {
  if (S.tab !== "cull") return;
  const idx = S.filtered.findIndex((r) => r.rating_key === S.openKey);
  const next = idx > -1 ? S.filtered[idx + delta] : null;
  if (!next) return;
  replaceUrl({ item: next.rating_key });
  openDetail(next.rating_key);
}

async function loadArr(rk) {
  S.arr = { rk, loading: true };
  let info;
  try { info = await api(`/api/arr/${rk}`); } catch (e) { info = { error: e.message }; }
  if (S.openKey !== rk) return;
  S.arr = { rk, ...info };
  rerender();
}

async function loadMedia(rk) {
  let m;
  try { m = await api(`/api/item/${rk}/media`); } catch (e) { m = { error: e.message, versions: [], paths: [] }; }
  if (S.openKey !== rk) return;
  S.media = { rk, ...m };
  rerender();
}

async function reloadDetail() {
  const rk = S.openKey;
  if (!rk) return;
  try {
    const d = await api(`/api/item/${rk}`);
    if (S.openKey !== rk) return;
    S.detail = d;
    rerender();
  } catch (e) { fail(e); }
}

// Re-render without losing the scroll position or the focused control.
function rerender() {
  if (!S.detail) return;
  const body = $("#dBody");
  const top = body.scrollTop;
  const active = document.activeElement;
  const focusId = active && body.contains(active) ? active.id || (active.dataset.season && `season-${active.dataset.season}`) : null;
  renderDetail();
  body.scrollTop = top;
  if (focusId) {
    const el = document.getElementById(focusId) || $(`[data-season="${focusId.replace("season-", "")}"]`, body);
    if (el) el.focus({ preventScroll: true });
  }
}

function detailSkeleton() {
  return `<div class="d-hero"><div class="d-hero-inner"><span class="poster d-poster"></span>
    <div class="d-head"><span class="sk" style="width:62%;height:26px"></span><span class="sk" style="width:38%"></span><span class="sk" style="width:46%"></span></div></div></div>
    <div class="d-pad"><span class="sk" style="width:30%;height:34px;margin-top:18px"></span><span class="sk" style="width:100%;height:58px;margin-top:22px"></span></div>`;
}

const badge = (t, cls = "") => `<span class="badge${cls ? " " + cls : ""}">${esc(t)}</span>`;

function qualityBadges(it, M) {
  const out = [];
  const v = M && M.versions && M.versions.length ? M.versions : null;
  if (v) {
    const uniq = (f) => [...new Set(v.map(f).filter(Boolean))];
    uniq((x) => x.resolution).forEach((r) => out.push(badge(r, r === "4K" ? "hi" : "")));
    uniq((x) => x.hdr).forEach((h) => h.split(" · ").forEach((p) => out.push(badge(p, "hi"))));
    uniq((x) => codecName(x.codec)).forEach((c) => out.push(badge(c)));
    if (v.some((x) => x.remux)) out.push(badge("Remux"));
    if (v.length > 1) out.push(badge(`${v.length} versions`));
  } else if (it.quality && it.quality.label) {
    const q = it.quality;
    out.push(badge(q.label, q.is4k ? "hi" : ""));
    if (q.codec) out.push(badge(codecName(q.codec)));
    if (q.versions > 1) out.push(badge(`${q.versions} versions`));
  }
  return out.join("");
}

function filesHtml(it, M, A) {
  const parts = [];
  const versions = (M && M.versions) || [];
  if (versions.length) {
    versions.forEach((v, i) => {
      const spec = [v.resolution, ...(v.hdr ? v.hdr.split(" · ") : []), codecName(v.codec), bitrate(v.bitrate),
        [v.audio, channels(v.channels)].filter(Boolean).join(" "), v.container].filter(Boolean);
      parts.push(`<div class="vbox">
        <div class="v-head"><span class="v-name">${versions.length > 1 ? `Version ${i + 1}` : "File"}</span><span class="v-spec">${spec.map(esc).join('<span class="sep" aria-hidden="true"></span>')}</span><span class="v-size">${v.size ? bytes(v.size) : ""}</span></div>
        ${v.files.map((f) => `<div class="path"><code>${esc(f.path || "Unknown path")}</code>${v.files.length > 1 && f.size ? `<span>${bytes(f.size)}</span>` : ""}</div>`).join("")}
      </div>`);
    });
  } else {
    const paths = [...new Set([...((M && M.paths) || []), ...(A && A.path ? [A.path] : [])])];
    if (A && A.file) paths.push(A.file);
    if (paths.length) parts.push(`<div class="vbox"><div class="v-head"><span class="v-name">${it.kind === "movie" ? "Files" : "Folder"}</span></div>${paths.map((p) => `<div class="path"><code>${esc(p)}</code></div>`).join("")}</div>`);
  }
  if (!parts.length) {
    if (!M) return `<p class="hint">Reading file details…</p>`;
    return `<p class="hint">${M.error ? esc(M.error) : "No file details for this one."}</p>`;
  }
  return parts.join("");
}

function histRows(history, isShow) {
  const min = S.settings ? S.settings.min_play_minutes : 0;
  return history.map((h) => {
    const why = h.counted ? "" : `<span class="why">${(h.duration || 0) < min * 60 ? "too short" : "ignored person"}</span>`;
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

function renderDetail() {
  const d = S.detail;
  const it = d.item;
  const isSeason = it.kind === "season";
  const kept = it.exclusion;
  const viaShow = isSeason && it.show_exclusion;
  const name = isSeason ? `${it.show_title} – ${it.title}` : it.title;
  const M = S.media && S.media.rk === it.rating_key ? S.media : null;
  const A = S.arr && S.arr.rk === it.rating_key ? S.arr : null;
  const svc = it.kind === "movie" ? "radarr" : "sonarr";
  const arrOn = canDelete(it.kind);
  const linked = A && A.matched && !A.loading && !A.error;
  const leaving = it.leaving;
  $("#dCrumb").textContent = isSeason ? it.show_title : it.library || "";
  updateNav();

  const meta = [];
  if (it.year) meta.push(it.year);
  if (M && M.content_rating) meta.push(M.content_rating);
  if (it.kind === "movie" && M && M.duration) meta.push(runtime(M.duration));
  if (it.kind === "show") meta.push(`${plural(it.season_count, "season")}, ${plural(it.episode_count, "episode")}`);
  if (isSeason) meta.push(plural(it.episode_count, "episode"));
  if (it.library) meta.push(it.library);
  const art = (M && M.art) || it.art;
  const badges = qualityBadges(it, M);

  // ---- actions
  const sonarrSeasons = linked && svc === "sonarr" ? A.seasons : null;
  const arrSeason = sonarrSeasons && isSeason ? sonarrSeasons.find((x) => x.number === it.season_index) : null;
  const blocked = kept || viaShow;
  const acts = [];
  if (!blocked) acts.push(`<button class="btn" id="dKeep" type="button">${icon("keep")}Keep</button>`);
  if (arrOn && !blocked && !leaving) acts.push(`<button class="btn ghost" id="dLeave" type="button">${icon("clock")}Leaving soon…</button>`);
  if (isSeason) acts.push(`<button class="btn ghost" id="dShow" type="button">Open show</button>`);
  if (it.plex_url) acts.push(`<a class="btn ghost" href="${esc(it.plex_url)}" target="_blank" rel="noopener">${icon("plex")}Open in Plex</a>`);
  if (linked) acts.push(`<a class="btn ghost" href="${esc(A.url)}" target="_blank" rel="noopener">${icon("external")}Show in ${SERVICE[svc]}</a>`);
  let delBtn = "";
  if (arrOn) {
    const why = blocked ? "On your keep list. Stop keeping it to delete." : A && A.blocked ? A.blocked : A && A.loading ? `Checking ${SERVICE[svc]}…`
      : A && A.error ? A.error : A && !A.matched ? `Not found in ${SERVICE[svc]}` : "";
    delBtn = `<button class="btn warn" id="dDelete" type="button"${why ? ` disabled title="${esc(why)}"` : ""}>${icon("trash")}${isSeason ? "Delete season…" : "Delete…"}</button>`;
  }
  let arrLine = "";
  if (A) {
    if (A.loading) arrLine = `<p class="arr-note">Checking ${SERVICE[svc]}…</p>`;
    else if (A.error) arrLine = `<p class="arr-note is-err">${icon("alert")}${SERVICE[svc]}: ${esc(A.error)}</p>`;
    else if (!A.matched) {
      arrLine = `<p class="arr-note">${icon("info")}Not found in ${SERVICE[svc]}, so it can't be deleted from here.${A.add_url
        ? ` <a href="${esc(A.add_url)}" target="_blank" rel="noopener">Add it to ${SERVICE[svc]}</a>` : ""}</p>`;
    }
    else if (svc === "sonarr" && !isSeason) arrLine = `<label class="switch"><input type="checkbox" id="dMonSeries" ${A.monitored ? "checked" : ""}><span class="knob"></span>Monitored in Sonarr</label>`;
    else if (arrSeason) arrLine = `<label class="switch"><input type="checkbox" id="dMonSeason" ${arrSeason.monitored ? "checked" : ""}><span class="knob"></span>Season ${arrSeason.number} monitored in Sonarr</label>`;
    if (A.matched && (A.blocked || A.warning)) arrLine += `<p class="caution">${icon("alert")}${esc(A.blocked ? `${A.blocked}.` : A.warning)}</p>`;
  }

  // ---- notes
  const notes = [];
  if (kept) {
    notes.push(`<div class="callout keep">${icon("keep")}<div><p><b>On your keep list.</b> It won't show up in the cull list or be deleted.</p>
      <div class="note-row"><input type="text" id="dNote" maxlength="500" placeholder="Why keep it? (optional)" value="${esc(kept.note || "")}" aria-label="Why keep it">
      <button class="btn ghost" id="dUnkeep" type="button">Stop keeping</button></div></div></div>`);
  } else if (viaShow) {
    notes.push(`<div class="callout keep">${icon("keep")}<div><p>The whole show is on your keep list, so this season stays too.</p></div></div>`);
  }
  if (leaving) {
    const whole = isSeason && leaving.rating_key !== it.rating_key;
    notes.push(`<div class="callout leaving">${icon("clock")}<div>
      <p><b>${whole ? "The whole show leaves" : "Leaves"} ${esc(until(leaving.delete_at))}</b>, on ${esc(dayDate(leaving.delete_at))}, unless someone watches it first.</p>
      ${leaving.note ? `<p class="muted">${esc(leaving.note)}</p>` : ""}
      ${whole ? "" : `<div class="note-row"><button class="btn ghost small" id="dExtend" type="button">${icon("plus")}7 more days</button><button class="btn ghost small" id="dCancelLeave" type="button">Don't delete</button></div>`}
    </div></div>`);
  }
  const reqs = it.requests || [];
  if (reqs.length) {
    notes.push(`<div class="callout request">${icon("user")}<div>${reqs.map((q) => `<p>Requested by <b>${esc(q.requested_by)}</b>${q.requested_at ? ` on ${esc(fullDate(q.requested_at))}` : ""}.
      ${q.watched === false ? `<span class="unwatched">${icon("eye-off")}They haven't watched it.</span>` : q.watched ? `<span class="watched">${icon("eye")}They've watched it.</span>` : ""}</p>`).join("")}</div></div>`);
  }

  // ---- seasons
  let seasonsHtml = "";
  if (d.seasons.length) {
    const max = Math.max(1, ...d.seasons.map((s) => s.size));
    seasonsHtml = `<section class="d-section"><h3>Seasons <span>${plural(d.seasons.length, "season")}</span></h3>
      <div class="tbl-wrap"><table>
      <thead><tr><th>Season</th><th class="r eps">Episodes</th><th class="season-bar"><span class="sr">Size</span></th><th class="r">Size on disk</th><th>Last watched</th><th class="r pl">Plays</th>${sonarrSeasons ? '<th class="mon">Monitored</th>' : ""}</tr></thead>
      <tbody>${d.seasons.map((s) => {
        const as = sonarrSeasons && sonarrSeasons.find((x) => x.number === s.season_index);
        return `<tr class="click" data-rk="${s.rating_key}" tabindex="0">
          <td>${esc(s.title)}${s.excluded ? ` <span class="tag kept">${icon("keep")}Kept</span>` : ""}${s.leaving_at ? ` <span class="tag leaving">${icon("clock")}${esc(shortDate(s.leaving_at))}</span>` : ""}</td>
          <td class="r eps">${s.episode_count}</td>
          <td class="season-bar" style="--tint:${tint(s.last_played)}"><div class="bar"><div class="fill" style="width:${Math.max(1, (s.size / max) * 100).toFixed(1)}%"></div></div></td>
          <td class="r">${bytes(s.size)}</td>
          <td>${s.last_played ? `${ymd(s.last_played)} <span class="ep ago">${ago(s.last_played)}</span>` : '<span class="never-t">Never</span>'}</td>
          <td class="r pl">${s.plays}</td>
          ${sonarrSeasons ? `<td class="mon">${as ? `<label class="switch" title="Monitor season ${as.number} in Sonarr"><input type="checkbox" data-season="${as.number}" ${as.monitored ? "checked" : ""} aria-label="Monitor season ${as.number} in Sonarr"><span class="knob"></span></label>` : ""}</td>` : ""}
        </tr>`;
      }).join("")}</tbody></table></div></section>`;
  }

  // ---- history
  const counted = d.history.filter((h) => h.counted).length;
  const histLimit = 100;
  const historyHtml = d.history.length
    ? `<div class="tbl-wrap"><table>
        <thead><tr><th>Date</th><th>Person</th><th>Title</th><th>Started</th><th class="r">Paused</th><th>Stopped</th><th class="r">Duration</th></tr></thead>
        <tbody id="hBody">${histRows(d.history.slice(0, histLimit), it.kind !== "movie")}</tbody></table></div>
        ${d.history.length > histLimit ? `<button class="btn ghost small more" id="hMore" type="button">Show all ${d.history.length.toLocaleString()} plays</button>` : ""}`
    : `<p class="hint">Tautulli has no record of anyone playing this.</p>`;

  const summary = M && M.summary ? `<div class="d-summary"><p id="dSummary">${esc(M.summary)}</p>${M.genres && M.genres.length ? `<p class="genres">${M.genres.map(esc).join(", ")}</p>` : ""}</div>` : "";

  $("#dBody").innerHTML = `
    <div class="d-hero${art ? " has-art" : ""}">
      ${art ? `<img class="d-art" src="${artUrl(art)}" alt="" onerror="this.parentNode.classList.remove('has-art');this.remove()">` : ""}
      <div class="d-hero-inner">
        ${posterImg(it.thumb, 240, "poster d-poster")}
        <div class="d-head">
          <h2 class="d-title" id="dTitle">${esc(name)}</h2>
          <p class="d-meta">${meta.map((m) => `<span>${esc(m)}</span>`).join("")}</p>
          ${badges ? `<div class="badges">${badges}</div>` : ""}
        </div>
      </div>
    </div>
    <div class="d-pad">
      <div class="d-size"><span class="num">${bytes(it.size)}</span><span class="lbl">on disk</span></div>
      <div class="d-actions">${acts.join("")}${delBtn ? `<span class="grow"></span>${delBtn}` : ""}</div>
      ${arrLine ? `<div class="d-arr">${arrLine}</div>` : ""}
      ${notes.join("")}
      <dl class="facts">
        <div><dt>Last watched</dt><dd class="${it.last_played ? "" : "never-t"}">${it.last_played ? `${ago(it.last_played)}<small>${ymd(it.last_played)}</small>` : "Never"}</dd></div>
        <div><dt>Last watched by</dt><dd>${esc(it.last_user || "—")}</dd></div>
        <div><dt>Plays</dt><dd>${it.plays.toLocaleString()}${it.users ? `<small>from ${plural(it.users, "person", "people")}</small>` : ""}</dd></div>
        <div><dt>Added</dt><dd>${it.added_at ? `${ago(it.added_at)}<small>${ymd(it.added_at)}</small>` : "—"}</dd></div>
      </dl>
      ${summary}
      <section class="d-section"><h3>${it.kind === "movie" ? "Files" : "On disk"}</h3>${filesHtml(it, M, A)}</section>
      ${seasonsHtml}
      <section class="d-section"><h3>Watch history <span>${plural(d.history.length, "play")}${counted !== d.history.length ? `, ${counted} counted` : ""}</span></h3>${historyHtml}</section>
    </div>`;
}

function bindDetail() {
  const drawer = $("#drawer");
  drawer.addEventListener("click", (e) => { if (e.target.closest("[data-close]")) closeItem(); });
  $("#dBack").addEventListener("click", () => history.back());
  $("#dPrev").addEventListener("click", () => stepDetail(-1));
  $("#dNext").addEventListener("click", () => stepDetail(1));

  const body = $("#dBody");
  body.addEventListener("click", async (e) => {
    const t = e.target.closest("button, tr.click, a");
    if (!t || !S.detail) return;
    const it = S.detail.item;
    const rk = it.rating_key;
    const name = it.kind === "season" ? `${it.show_title} – ${it.title}` : it.title;
    try {
      switch (t.id) {
        case "dKeep": {
          const res = await keepKeys([rk]);
          toast(`Kept ${name}${res && res.leaving_cancelled ? ", and it's off Leaving soon" : ""}`, async () => { await unkeepKeys([rk]); reloadDetail(); });
          reloadDetail();
          return;
        }
        case "dUnkeep":
          await unkeepKeys([rk]);
          toast(`${name} is back on the cull list`);
          reloadDetail();
          return;
        case "dShow": openItem(it.show_key); return;
        case "dLeave": openPlan([rk], "leaving"); return;
        case "dDelete": openPlan([rk], "delete", { source: "manual" }); return;
        case "dExtend": {
          const res = await post(`/api/leaving/${rk}/extend`, { days: 7 });
          toast(`${name} now leaves ${until(res.delete_at)}`);
          reloadDetail();
          refreshData();
          return;
        }
        case "dCancelLeave":
          await del(`/api/leaving/${rk}`);
          toast(`${name} won't be deleted`);
          reloadDetail();
          refreshData();
          return;
        case "hMore":
          $("#hBody").innerHTML = histRows(S.detail.history, it.kind !== "movie");
          t.remove();
          return;
      }
      if (t.matches("tr.click") && !e.target.closest("td.mon")) openItem(+t.dataset.rk);
    } catch (err) { fail(err); }
  });
  body.addEventListener("keydown", (e) => {
    const tr = e.target.closest("tr.click");
    if (tr && e.key === "Enter" && e.target === tr) openItem(+tr.dataset.rk);
  });
  body.addEventListener("change", async (e) => {
    const t = e.target;
    if (!S.detail) return;
    const it = S.detail.item;
    if (t.id === "dNote") {
      try { await put(`/api/exclusions/${it.rating_key}`, { note: t.value }); toast("Note saved"); } catch (err) { fail(err); }
      return;
    }
    let season = null;
    if (t.id === "dMonSeason") season = it.season_index;
    else if (t.dataset.season) season = +t.dataset.season;
    else if (t.id !== "dMonSeries") return;
    t.disabled = true;
    try {
      const res = await put(`/api/arr/${it.rating_key}/monitor`, season == null ? { monitored: t.checked } : { monitored: t.checked, season });
      S.arr = { ...S.arr, monitored: res.monitored, seasons: res.seasons };
      toast(`${season == null ? "Show" : `Season ${season}`} ${t.checked ? "monitored" : "unmonitored"} in Sonarr`);
    } catch (err) {
      t.checked = !t.checked;
      fail(err);
    }
    rerender();
  });
}
