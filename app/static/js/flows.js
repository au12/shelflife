"use strict";
/* Bulk actions: delete (plan, confirm, job progress), Leaving soon scheduling, and Review mode. */

const CONFIRM_AT = 10;   // deleting this many titles or more asks you to type the count
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let FLOW = null;

const MODES = {
  remove: ["Remove from Sonarr", "Deletes every episode file and removes the show from Sonarr, so nothing new downloads."],
  files: ["Delete files, keep in Sonarr", "Deletes every episode file. The show stays in Sonarr, so new episodes still download."],
};

/* ------------------------------------------------------------ plan: delete or schedule */

async function openPlan(keys, action, opts = {}) {
  keys = [...new Set(keys.map(Number))].filter(Boolean);
  if (!keys.length) return;
  closePop();
  const dlg = $("#flow");
  if (dlg.open) dlg.close();
  const P = { keys, action, opts, items: null, mode: "remove", days: (S.settings && S.settings.leaving_days) || 14 };
  FLOW = P;
  dlg.className = `dlg flow ${action}`;
  dlg.innerHTML = `<div class="dlg-head"><h2>Checking Sonarr and Radarr…</h2></div>
    <div class="dlg-body"><div class="prog indet"><span></span></div><p class="hint">Matching ${plural(keys.length, "title")}.</p></div>
    <div class="dlg-foot"><span class="grow"></span><button class="btn ghost" type="button" data-flow="cancel">Cancel</button></div>`;
  dlg.showModal();
  try {
    P.items = (await post("/api/arr/plan", { rating_keys: keys })).items;
  } catch (e) {
    if (FLOW !== P) return;
    dlg.innerHTML = `<div class="dlg-head"><h2>Couldn't check Sonarr and Radarr</h2></div><div class="dlg-body"><p class="err">${icon("alert")}${esc(e.message)}</p></div>
      <div class="dlg-foot"><span class="grow"></span><button class="btn" type="button" data-flow="cancel">Close</button></div>`;
    return;
  }
  if (FLOW !== P || !dlg.open) return;
  renderPlan(P);
}

function planItemHtml(i) {
  const name = i.kind === "season" ? `${i.show_title || ""} – Season ${i.season_index}` : i.title;
  const sub = i.ok ? `${KIND[i.kind] || ""}${i.service ? ` · ${SERVICE[i.service]}` : ""}${i.kind === "show" && i.status === "continuing" ? " · still airing" : ""}` : i.reason;
  return `<li class="pl-item${i.ok ? "" : " is-bad"}">${posterImg(i.thumb, 120, "poster tiny")}
    <span class="pl-main"><span class="pl-title">${esc(name)}${i.year && i.kind !== "season" ? ` <span class="yr">${i.year}</span>` : ""}</span>
    <span class="pl-sub">${i.ok ? "" : icon("alert")}${esc(sub || "")}</span></span>
    <span class="pl-size">${i.size ? bytes(i.size) : ""}</span></li>`;
}

function renderPlan(P) {
  const dlg = $("#flow");
  const isDel = P.action === "delete";
  const ok = P.items.filter((i) => i.ok);
  const bad = P.items.filter((i) => !i.ok);
  const has = (k) => ok.some((i) => i.kind === k);
  const size = ok.reduce((a, i) => a + (i.size || 0), 0);
  const single = P.items.length === 1;
  const first = P.items[0];

  let title;
  if (single) {
    const nm = first.kind === "season" ? `${first.show_title} season ${first.season_index}` : first.title;
    title = isDel ? (first.kind === "season" ? `Delete ${nm} files?` : `Delete ${nm}?`) : `Schedule ${nm} to leave?`;
  } else {
    title = isDel ? `Delete ${plural(P.items.length, "title")}?` : `Schedule ${plural(P.items.length, "title")} to leave?`;
  }

  const parts = [];
  if (!ok.length) {
    parts.push(`<p class="err">${icon("alert")}${single ? esc(first.reason) : "None of these can be deleted from here."}</p>`);
  } else if (isDel && single) {
    if (first.kind === "movie") parts.push(`<p>Radarr removes the movie and deletes its files (${bytes(first.size)}), so it won't download again. This can't be undone.</p>`);
    if (first.kind === "season") parts.push(`<p>Sonarr deletes the episode files in this season (${bytes(first.size)}). The show stays in Sonarr. This can't be undone.</p>`);
    if (first.kind === "show") parts.push(`<p>Frees ${bytes(first.size)}. This can't be undone.</p>`);
  } else if (isDel) {
    parts.push(`<p>${ok.length === P.items.length ? `All ${plural(ok.length, "title")} can be deleted` : `${ok.length} of ${P.items.length} can be deleted`}, freeing <b>${bytes(size)}</b>. This can't be undone.</p>`);
  } else {
    parts.push(`<div class="days"><label for="pDays">Delete in</label><input id="pDays" type="number" min="1" max="365" step="1" inputmode="numeric" value="${P.days}"><span>days</span>
      <span class="days-quick">${[7, 14, 30].map((d) => `<button type="button" class="chip${d === P.days ? " is-set" : ""}" data-days="${d}">${d} days</button>`).join("")}</span></div>
      <p class="days-when" id="pWhen"></p>
      <p class="hint">If anyone watches one before then, it stays and comes off the list.${collectionHint()}</p>`);
  }

  if (has("show")) {
    parts.push(`<div class="choices" role="radiogroup" aria-label="What to do with ${single ? "the show" : "shows"}">${!single ? '<p class="choices-l">For shows</p>' : ""}
      ${Object.entries(MODES).map(([m, [label, desc]]) => `<label class="choice"><input type="radio" name="pMode" value="${m}" ${P.mode === m ? "checked" : ""}>
        <span><b>${label}</b>${desc}</span></label>`).join("")}</div>`);
  }
  const exclLabel = has("movie") && has("show") ? "Add to Radarr and Sonarr import exclusions so lists don't add them back"
    : has("movie") ? `Add to Radarr's import exclusions so lists don't add ${single ? "it" : "them"} back` : `Add to Sonarr's import list exclusions so lists don't add ${single ? "it" : "them"} back`;
  if (has("movie") || has("show")) parts.push(`<label class="check" id="pExclWrap"><input type="checkbox" id="pExcl" checked> ${exclLabel}</label>`);
  if (has("season") || has("show")) parts.push(`<label class="check" id="pUnmonWrap"><input type="checkbox" id="pUnmon" checked> <span id="pUnmonLabel"></span></label>`);

  if (!single || bad.length) {
    const list = [...ok, ...bad];
    parts.push(`<details class="plan"${single || bad.length ? " open" : ""}><summary>${single ? "Details" : `${plural(ok.length, "title")} to ${isDel ? "delete" : "schedule"}${bad.length ? `, ${bad.length} can't be` : ""}`}</summary>
      <ul class="plan-list">${list.map(planItemHtml).join("")}</ul></details>`);
  }
  if (isDel && ok.length >= CONFIRM_AT) {
    parts.push(`<label class="confirm" for="pConfirm">Type <b>${ok.length}</b> to confirm <input id="pConfirm" type="text" inputmode="numeric" autocomplete="off" spellcheck="false"></label>`);
  }

  dlg.innerHTML = `<form method="dialog" id="planForm">
    <div class="dlg-head"><h2>${esc(title)}</h2></div>
    <div class="dlg-body">${parts.join("")}<p class="err" id="pErr" role="alert"></p></div>
    <div class="dlg-foot"><span class="grow"></span><button class="btn ghost" type="button" data-flow="cancel">Cancel</button>
      ${ok.length ? `<button class="btn ${isDel ? "danger" : ""}" id="pGo" type="submit"></button>` : ""}</div></form>`;
  syncPlan(P);
  const go = $("#pConfirm") || $("#pDays") || $("input[name=pMode]:checked") || $("#pGo") || $("[data-flow=cancel]", dlg);
  if (go) go.focus();
}

function collectionHint() {
  const ig = (S.status && S.status.integrations) || {};
  if (!ig.plex || !S.settings || !S.settings.leaving_collection) return "";
  return ` Everyone can see what's going in the “${esc(S.settings.leaving_collection_name)}” collection in Plex.`;
}

function syncPlan(P) {
  const dlg = $("#flow");
  const isDel = P.action === "delete";
  const ok = P.items.filter((i) => i.ok);
  const has = (k) => ok.some((i) => i.kind === k);
  const mode = ($("input[name=pMode]:checked", dlg) || {}).value || "remove";
  P.mode = mode;
  const excl = $("#pExclWrap");
  if (excl) excl.hidden = !has("movie") && mode !== "remove";
  const unmon = $("#pUnmonWrap");
  if (unmon) {
    unmon.hidden = !has("season") && mode !== "files";
    $("#pUnmonLabel").textContent = has("season") && !has("show")
      ? "Unmonitor the deleted episodes so Sonarr doesn't download them again"
      : "Unmonitor episodes that already aired so Sonarr doesn't download them again";
  }
  if (!isDel) {
    const n = clamp(parseInt($("#pDays").value, 10) || 0, 0, 365);
    P.days = n;
    $("#pWhen").textContent = n ? `${ok.length === 1 ? "It goes" : "They go"} on ${dayDate(now() + n * DAY)}.` : "";
    $$("[data-days]", dlg).forEach((b) => b.classList.toggle("is-set", +b.dataset.days === n));
  }
  const go = $("#pGo");
  if (!go) return;
  const size = ok.reduce((a, i) => a + (i.size || 0), 0);
  let label;
  if (!isDel) label = `Schedule ${plural(ok.length, "title")}`;
  else if (P.items.length === 1) {
    const k = ok[0].kind;
    label = k === "movie" ? "Delete from Radarr" : k === "season" ? "Delete season files" : mode === "files" ? "Delete episode files" : "Remove from Sonarr";
  } else label = `Delete ${plural(ok.length, "title")}, ${bytes(size)}`;
  go.innerHTML = `${icon(isDel ? "trash" : "clock")}${esc(label)}`;
  const confirm = $("#pConfirm");
  go.disabled = (confirm && confirm.value.trim() !== String(ok.length)) || (!isDel && !P.days);
}

async function submitPlan(P) {
  const dlg = $("#flow");
  const ok = P.items.filter((i) => i.ok);
  const exclusion = $("#pExcl") ? $("#pExcl").checked : true;
  const unmonitor = $("#pUnmon") ? $("#pUnmon").checked : true;
  const go = $("#pGo");
  go.disabled = true;
  $("#pErr").textContent = "";
  if (P.action === "leaving") {
    go.textContent = "Scheduling…";
    try {
      const res = await post("/api/leaving", { rating_keys: ok.map((i) => i.rating_key), days: P.days, mode: P.mode, exclusion, unmonitor });
      dlg.close();
      for (const i of ok) S.selected.delete(i.rating_key);
      const skipped = res.skipped && res.skipped.length ? ` ${plural(res.skipped.length, "title")} couldn't be scheduled.` : "";
      toast(`${res.scheduled === 1 ? "1 title leaves" : `${res.scheduled} titles leave`} ${until(res.delete_at)}.${skipped}`);
      await refreshData();
      if (S.openKey) reloadDetail();
      if (S.tab === "leaving") renderLeaving();
    } catch (e) {
      $("#pErr").textContent = e.message;
      syncPlan(P);
    }
    return;
  }
  go.textContent = "Starting…";
  try {
    const items = ok.map((i) => ({ rating_key: i.rating_key, mode: i.kind === "show" ? P.mode : "remove" }));
    P.job = await post("/api/jobs/delete", { items, exclusion, unmonitor, source: P.opts.source || "bulk" });
  } catch (e) {
    $("#pErr").textContent = e.message;
    syncPlan(P);
    return;
  }
  renderJob(P);
  pollJob(P);
}

/* ------------------------------------------------------------ job progress */

function renderJob(P) {
  const dlg = $("#flow");
  dlg.className = "dlg flow job";
  dlg.innerHTML = `<div class="dlg-head"><h2 id="jTitle"></h2></div>
    <div class="dlg-body"><div class="prog"><span id="jBar"></span></div><p class="prog-text" id="jText"></p><ol class="results" id="jResults"></ol></div>
    <div class="dlg-foot" id="jFoot"><span class="grow"></span><button class="btn ghost" type="button" data-flow="hide">Hide and keep going</button></div>`;
  updateJob(P);
}

function updateJob(P) {
  const j = P.job;
  if (FLOW !== P || !$("#flow").open || !$("#jBar")) return;
  const done = !!j.finished_at;
  const okN = j.results.filter((r) => r.ok).length;
  const badN = j.results.length - okN;
  $("#jTitle").textContent = done ? (okN ? `Deleted ${plural(okN, "title")}` : "Nothing was deleted") : `Deleting ${plural(j.total, "title")}…`;
  $("#jBar").style.width = `${j.total ? (j.done / j.total) * 100 : 0}%`;
  $("#jText").innerHTML = done
    ? `<b>${bytes(j.freed)}</b> freed.${badN ? ` ${plural(badN, "title")} couldn't be deleted.` : ""}${S.status && S.status.integrations.plex ? " Plex is rescanning." : ""}`
    : `${j.done} of ${j.total}${j.current ? ` · ${esc(j.current)}` : ""}`;
  const list = $("#jResults");
  for (const r of j.results.slice(list.children.length)) {
    list.insertAdjacentHTML("beforeend", `<li class="${r.ok ? "ok" : "bad"}">${icon(r.ok ? "check" : "alert")}<span class="r-t">${esc(r.title)}</span>
      <span class="r-m">${esc(r.ok ? (r.freed ? bytes(r.freed) : "Done") : r.message)}</span></li>`);
  }
  list.scrollTop = list.scrollHeight;
  if (done && !$("#jDone")) {
    $("#jFoot").innerHTML = `<span class="grow"></span><button class="btn" type="button" id="jDone" data-flow="close">Done</button>`;
    $("#jDone").focus();
  }
}

async function pollJob(P) {
  let errors = 0;
  while (!P.job.finished_at) {
    await sleep(700);
    try {
      P.job = await api(`/api/jobs/${P.job.id}`);
      errors = 0;
    } catch (e) {
      if (++errors > 8) { toast(`Lost track of the delete: ${e.message}`, null, true); return; }
      continue;
    }
    updateJob(P);
  }
  jobFinished(P);
}

async function jobFinished(P) {
  const j = P.job;
  const gone = new Set(j.results.filter((r) => r.ok).map((r) => r.rating_key));
  for (const k of gone) S.selected.delete(k);
  if (P.background || FLOW !== P || !$("#flow").open) {
    const okN = gone.size, badN = j.results.length - okN;
    toast(`Deleted ${plural(okN, "title")}, ${bytes(j.freed)} freed${badN ? `. ${badN} couldn't be deleted` : ""}`, null, !!badN && !okN);
  }
  if (S.openKey && gone.has(S.openKey)) {
    const it = S.detail && S.detail.item;
    if (it && it.kind === "season" && it.show_key) { replaceUrl({ item: it.show_key }); openDetail(it.show_key); }
    else closeItem();
  }
  await refreshData().catch(() => {});
  loadStorage();
  if (S.tab === "history") renderHistory();
  if (S.tab === "leaving") renderLeaving();
}

function bindFlows() {
  const dlg = $("#flow");
  dlg.addEventListener("click", (e) => {
    const b = e.target.closest("[data-flow]");
    const P = FLOW;
    if (e.target.closest("[data-days]") && P) { $("#pDays").value = e.target.closest("[data-days]").dataset.days; syncPlan(P); return; }
    if (!b) return;
    if (b.dataset.flow === "hide" && P) P.background = true;
    dlg.close();
  });
  dlg.addEventListener("input", () => { if (FLOW && FLOW.items && $("#planForm")) syncPlan(FLOW); });
  dlg.addEventListener("change", () => { if (FLOW && FLOW.items && $("#planForm")) syncPlan(FLOW); });
  dlg.addEventListener("submit", (e) => {
    if (e.target.id !== "planForm") return;
    e.preventDefault();
    if (FLOW && !$("#pGo").disabled) submitPlan(FLOW);
  });
  dlg.addEventListener("close", () => {
    const P = FLOW;
    if (P && P.job && !P.job.finished_at) P.background = true;
  });
}

/* ------------------------------------------------------------ Review mode */

const R = { items: [], i: 0, queue: [], kept: [], skipped: 0, log: [], summary: false, busy: false };

function startReview(keys) {
  if (S.tab !== "cull" || S.reviewOpen) return;
  const base = keys && keys.size ? S.filtered.filter((r) => keys.has(r.rating_key)) : S.filtered;
  const pool = base.filter((r) => !r.excluded);
  if (!pool.length) { toast("Nothing to review in this list"); return; }
  closePop();
  if (S.openKey) closeDetail();
  Object.assign(R, { items: pool, i: 0, queue: [], kept: [], skipped: 0, log: [], summary: false, busy: false });
  S.reviewOpen = true;
  syncScrollLock();
  pushUrl({ review: true });
  $("#review").showModal();
  renderReview();
}

function closeReview(fromPop = false) {
  if (!S.reviewOpen) return;
  S.reviewOpen = false;
  syncScrollLock();
  const dlg = $("#review");
  if (dlg.open) dlg.close();
  dlg.innerHTML = "";
  if (!fromPop && history.state && history.state.review) history.back();
  else writeUrl();
  if (R.kept.length) refreshData().catch(fail);
}

function reviewSub(r) {
  const bits = [KIND[r.kind]];
  if (r.kind === "show") bits.push(plural(r.season_count, "season"));
  if (r.kind === "season") bits.push(plural(r.episode_count, "episode"));
  if (r.quality_label) bits.push(r.quality_label);
  const lib = libName(r.section_id);
  if (lib) bits.push(lib);
  return bits.filter(Boolean).map(esc).join(" · ");
}

function renderReview() {
  const dlg = $("#review");
  if (R.summary || R.i >= R.items.length) { renderReviewSummary(); return; }
  const focusAct = document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.rv : null;
  const r = R.items[R.i];
  const qSize = R.queue.reduce((a, x) => a + x.size, 0);
  const pct = (R.i / R.items.length) * 100;
  dlg.innerHTML = `
    <div class="rv-bg">${r.art ? `<img src="${artUrl(r.art)}" alt="" onerror="this.remove()">` : ""}</div>
    <div class="rv-top">
      <span class="rv-count">${(R.i + 1).toLocaleString()} of ${R.items.length.toLocaleString()}</span>
      <span class="rv-tally"><span class="q" title="In the delete queue">${icon("trash")}${R.queue.length}<small>${bytes(qSize)}</small></span><span class="k" title="Kept">${icon("keep")}${R.kept.length}</span></span>
      <button class="btn ghost small" type="button" data-rv="undo" ${R.log.length ? "" : "disabled"} title="Undo (Z)">${icon("undo")}<span class="hide-sm">Undo</span></button>
      <button class="btn ghost icon" type="button" data-rv="finish" aria-label="Finish reviewing" title="Finish (Esc)">${icon("close")}</button>
      <div class="rv-progress" aria-hidden="true"><span style="width:${pct}%"></span></div>
    </div>
    <div class="rv-stage">
      <article class="rv-card" id="rvCard" aria-labelledby="rvTitle">
        <div class="rv-art">${r.art ? `<img src="${artUrl(r.art)}" alt="" onerror="this.remove()">` : ""}
          <span class="stamp del">${icon("trash")}Delete</span><span class="stamp keep">${icon("keep")}Keep</span></div>
        <div class="rv-body">
          ${posterImg(r.thumb, 240, "poster rv-poster")}
          <div class="rv-head">
            <h2 class="rv-title" id="rvTitle">${esc(nameOf(r))}${r.year && r.kind !== "season" ? ` <span class="yr">${r.year}</span>` : ""}</h2>
            <p class="rv-meta">${reviewSub(r)}</p>
          </div>
          <p class="rv-size" style="--tint:${tint(r.last_played)}"><span class="num">${bytes(r.size)}</span><span class="bar"><span style="width:${Math.max(2, (r.size / R.items.reduce((m, x) => Math.max(m, x.size), 1)) * 100).toFixed(1)}%"></span></span></p>
          <dl class="rv-facts">
            <div><dt>Last watched</dt><dd class="${r.last_played ? "" : "never-t"}">${r.last_played ? `${ago(r.last_played)}${r.last_user ? `<small>by ${esc(r.last_user)}</small>` : ""}` : "Never"}</dd></div>
            <div><dt>Plays</dt><dd>${r.plays.toLocaleString()}${r.users > 1 ? `<small>from ${r.users} people</small>` : ""}</dd></div>
            <div><dt>Added</dt><dd>${monthYear(r.added_at)}</dd></div>
            ${r.requested_by ? `<div><dt>Requested by</dt><dd>${esc(r.requested_by)}${r.requester_watched === false ? "<small>hasn't watched it</small>" : r.requester_watched ? "<small>has watched it</small>" : ""}</dd></div>` : ""}
            ${r.leaving_at ? `<div><dt>Leaving</dt><dd>${esc(dayDate(r.leaving_at))}</dd></div>` : ""}
          </dl>
        </div>
      </article>
    </div>
    <div class="rv-actions">
      <button class="rv-btn del" type="button" data-rv="delete" aria-keyshortcuts="ArrowLeft">${icon("trash")}<span>Delete</span><kbd class="hide-touch">←</kbd></button>
      <button class="rv-btn skip" type="button" data-rv="skip" aria-keyshortcuts="ArrowDown">${icon("skip")}<span>Skip</span><kbd class="hide-touch">↓</kbd></button>
      <button class="rv-btn keep" type="button" data-rv="keep" aria-keyshortcuts="ArrowRight">${icon("keep")}<span>Keep</span><kbd class="hide-touch">→</kbd></button>
    </div>`;
  bindSwipe($("#rvCard"));
  const f = focusAct && $(`[data-rv="${focusAct}"]`, dlg);
  (f && !f.disabled ? f : $(".rv-btn.skip", dlg)).focus({ preventScroll: true });
  const next = R.items[R.i + 1];
  if (next) { if (next.art) new Image().src = artUrl(next.art); if (next.thumb) new Image().src = posterUrl(next.thumb, 240); }
}

function renderReviewSummary() {
  R.summary = true;
  const dlg = $("#review");
  const finished = R.i >= R.items.length;
  const qSize = R.queue.reduce((a, x) => a + x.size, 0);
  const left = R.items.length - R.i;
  let next = "";
  if (R.queue.length) {
    next = canDelete()
      ? `<div class="rv-sum-actions"><button class="btn danger" type="button" data-rv="go-delete">${icon("trash")}Delete ${plural(R.queue.length, "title")}…</button>
          <button class="btn ghost" type="button" data-rv="go-leaving">${icon("clock")}Schedule them to leave instead…</button></div>`
      : `<p class="hint">Sonarr and Radarr aren't set up, so Shelflife can't delete these. Export the list to delete them yourself.</p>
         <div class="rv-sum-actions"><button class="btn" type="button" data-rv="go-export">${icon("download")}Export ${plural(R.queue.length, "title")}</button></div>`;
  }
  dlg.innerHTML = `<div class="rv-bg"></div>
    <div class="rv-summary">
      <h2>${finished ? "That's the whole list" : "Review paused"}</h2>
      <p class="muted">${finished ? `You went through ${plural(R.items.length, "title")}.` : `${plural(left, "title")} still to look at.`}${R.kept.length ? " Kept titles are already on your keep list." : ""}</p>
      <dl class="rv-sum">
        <div class="q"><dt>${icon("trash")}To delete</dt><dd>${R.queue.length.toLocaleString()}<small>${bytes(qSize)}</small></dd></div>
        <div class="k"><dt>${icon("keep")}Kept</dt><dd>${R.kept.length.toLocaleString()}</dd></div>
        <div><dt>${icon("skip")}Skipped</dt><dd>${R.skipped.toLocaleString()}</dd></div>
      </dl>
      ${next}
      <div class="rv-sum-foot">
        ${!finished ? `<button class="btn ghost" type="button" data-rv="resume">Keep reviewing</button>` : ""}
        ${R.log.length ? `<button class="btn ghost" type="button" data-rv="undo">${icon("undo")}Undo last</button>` : ""}
        <button class="btn ghost" type="button" data-rv="close">${R.queue.length ? "Close without deleting" : "Close"}</button>
      </div>
    </div>`;
  ($(".rv-sum-actions .btn", dlg) || $("[data-rv=resume]", dlg) || $("[data-rv=close]", dlg)).focus();
}

function flyOut(card, type) {
  if (!card || matchMedia("(prefers-reduced-motion: reduce)").matches) return Promise.resolve();
  card.classList.remove("dragging");
  card.classList.add(`fly-${type}`);
  return sleep(190);
}

async function decide(type) {
  if (R.busy || R.summary || R.i >= R.items.length) return;
  const r = R.items[R.i];
  R.busy = true;
  if (type === "keep") {
    try { await put(`/api/exclusions/${r.rating_key}`, {}); } catch (e) { R.busy = false; fail(e); resetCard(); return; }
    R.kept.push(r);
  } else if (type === "delete") R.queue.push(r);
  else R.skipped++;
  R.log.push({ type, r });
  await flyOut($("#rvCard"), type);
  R.i++;
  R.busy = false;
  renderReview();
}

async function undoReview() {
  if (R.busy || !R.log.length) return;
  R.busy = true;
  const last = R.log.pop();
  if (last.type === "keep") {
    try { await del(`/api/exclusions/${last.r.rating_key}`); } catch (e) { fail(e); }
    R.kept.pop();
  } else if (last.type === "delete") R.queue.pop();
  else R.skipped--;
  R.i--;
  R.summary = false;
  R.busy = false;
  renderReview();
}

function resetCard() {
  const card = $("#rvCard");
  if (!card) return;
  card.style.transform = "";
  card.style.setProperty("--del", 0);
  card.style.setProperty("--keep", 0);
}

function bindSwipe(card) {
  if (!card) return;
  let sx = 0, dx = 0, id = null;
  card.addEventListener("pointerdown", (e) => {
    if ((e.pointerType === "mouse" && e.button !== 0) || R.busy) return;
    id = e.pointerId;
    sx = e.clientX;
    dx = 0;
    card.setPointerCapture(id);
    card.classList.add("dragging");
  });
  card.addEventListener("pointermove", (e) => {
    if (e.pointerId !== id) return;
    dx = e.clientX - sx;
    card.style.transform = `translateX(${dx}px) rotate(${dx / 24}deg)`;
    card.style.setProperty("--del", clamp(-dx / 110, 0, 1));
    card.style.setProperty("--keep", clamp(dx / 110, 0, 1));
  });
  const end = (e) => {
    if (e.pointerId !== id) return;
    id = null;
    card.classList.remove("dragging");
    if (dx < -110) decide("delete");
    else if (dx > 110) decide("keep");
    else resetCard();
  };
  card.addEventListener("pointerup", end);
  card.addEventListener("pointercancel", end);
}

function reviewKey(e) {
  const onButton = e.target.closest && e.target.closest("button");
  if (R.summary) return;
  switch (e.key) {
    case "ArrowLeft": e.preventDefault(); decide("delete"); break;
    case "ArrowRight": e.preventDefault(); decide("keep"); break;
    case "ArrowDown": e.preventDefault(); decide("skip"); break;
    case " ": if (!onButton) { e.preventDefault(); decide("skip"); } break;
    case "z": case "Z": case "Backspace": e.preventDefault(); undoReview(); break;
    default:
  }
}

function bindReview() {
  const dlg = $("#review");
  dlg.addEventListener("click", (e) => {
    const b = e.target.closest("[data-rv]");
    if (!b || b.disabled) return;
    const keys = () => R.queue.map((r) => r.rating_key);
    switch (b.dataset.rv) {
      case "delete": case "keep": case "skip": decide(b.dataset.rv); break;
      case "undo": undoReview(); break;
      case "finish": R.log.length ? renderReviewSummary() : closeReview(); break;
      case "resume": R.summary = false; renderReview(); break;
      case "close": closeReview(); break;
      case "go-delete": { const k = keys(); closeReview(); openPlan(k, "delete", { source: "review" }); break; }
      case "go-leaving": { const k = keys(); closeReview(); openPlan(k, "leaving"); break; }
      case "go-export": exportCsv(R.queue, "review"); break;
      default:
    }
  });
  dlg.addEventListener("cancel", (e) => {
    e.preventDefault();
    if (!R.summary && R.log.length) renderReviewSummary();
    else closeReview();
  });
}
