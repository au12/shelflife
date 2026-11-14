"use strict";
/* Shared helpers and app state. Loaded first; the other scripts use these globals. */

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const DAY = 86400;
const now = () => Date.now() / 1000;
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

const DEFAULTS = { lib: "", view: "titles", age: 365, never: "include", grace: 30, q: "", sort: "size", dir: "desc", kept: false, bucket: "", req: "" };

const S = {
  status: null,
  settings: null,
  libraries: [],
  titles: [],
  seasons: null,
  loaded: false,
  filtered: [],
  rendered: 0,
  maxSize: 1,
  hiddenKept: 0,
  selected: new Set(),
  lastClicked: null,
  tab: "cull",
  f: { ...DEFAULTS },
  openKey: null,
  detail: null,
  arr: null,
  media: null,
  storage: [],
  reviewOpen: false,
};

/* ------------------------------------------------------------ formatting */

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

function calendarDays(ts) {
  const a = new Date(); a.setHours(0, 0, 0, 0);
  const b = new Date(ts * 1000); b.setHours(0, 0, 0, 0);
  return Math.round((b - a) / 864e5);
}

// "today", "tomorrow", "in 5 days"
function until(ts) {
  const d = calendarDays(ts);
  if (ts <= now()) return "any minute now";
  if (d <= 0) return "today";
  if (d === 1) return "tomorrow";
  if (d < 21) return `in ${d} days`;
  return `in ${Math.round(d / 7)} weeks`;
}

const pad = (n) => String(n).padStart(2, "0");
const ymd = (ts) => { if (!ts) return "—"; const d = new Date(ts * 1000); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; };
const hm = (ts) => { if (!ts) return "—"; const d = new Date(ts * 1000); return `${pad(d.getHours())}:${pad(d.getMinutes())}`; };
const fmtDate = (ts, o) => (ts ? new Date(ts * 1000).toLocaleDateString(undefined, o) : "—");
const monthYear = (ts) => fmtDate(ts, { month: "short", year: "numeric" });
const shortDate = (ts) => fmtDate(ts, { month: "short", day: "numeric" });
const dayDate = (ts) => fmtDate(ts, { weekday: "short", month: "short", day: "numeric" });
const fullDate = (ts) => fmtDate(ts, { month: "short", day: "numeric", year: "numeric" });
const mins = (s) => `${Math.round((s || 0) / 60)} min`;
const plural = (n, w, p) => `${Number(n).toLocaleString()} ${n === 1 ? w : p || w + "s"}`;

function runtime(ms) {
  if (!ms) return "";
  const m = Math.round(ms / 60000);
  return m >= 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m} min`;
}

function bitrate(kbps) {
  if (!kbps) return "";
  return kbps >= 1000 ? `${(kbps / 1000).toFixed(kbps >= 10000 ? 0 : 1)} Mbps` : `${kbps} kbps`;
}

function channels(n) {
  return { 1: "Mono", 2: "Stereo", 6: "5.1", 8: "7.1" }[n] || (n ? `${n} ch` : "");
}

/* ------------------------------------------------------------ staleness colours */

// Sage when recently watched, drying to straw as it ages (log scale, 1 month to 3 years); violet if never.
const FRESH = [125, 183, 154], STALE = [212, 180, 103];
function tintDays(days) {
  const t = clamp(Math.log(Math.max(1, days) / 30) / Math.log(1095 / 30), 0, 1);
  return `rgb(${FRESH.map((v, i) => Math.round(v + (STALE[i] - v) * t)).join(",")})`;
}
const tint = (lp) => (lp ? tintDays((now() - lp) / DAY) : "var(--never)");

const BUCKETS = [
  { id: "m3", chip: ["Watched in the", "last 3 months"], label: "Last 3 months", short: "Under 3 months", phrase: "watched in the last 3 months", color: tintDays(30) },
  { id: "y1", chip: ["Last watched", "3–12 months ago"], label: "3–12 months ago", short: "3–12 months", phrase: "last watched 3 to 12 months ago", color: tintDays(190) },
  { id: "y3", chip: ["Last watched", "1–3 years ago"], label: "1–3 years ago", short: "1–3 years", phrase: "last watched 1 to 3 years ago", color: tintDays(600) },
  { id: "old", chip: ["Last watched", "over 3 years ago"], label: "Over 3 years ago", short: "Over 3 years", phrase: "last watched over 3 years ago", color: tintDays(1095) },
  { id: "never", chip: [null, "Never watched"], label: "Never watched", short: "Never", phrase: "nobody has ever watched", color: "var(--never)" },
];
function bucketOf(lp) {
  if (!lp) return "never";
  const d = (now() - lp) / DAY;
  return d < 91 ? "m3" : d < 365 ? "y1" : d < 1095 ? "y3" : "old";
}

/* ------------------------------------------------------------ markup bits */

const icon = (name, cls = "") => `<svg class="ic${cls ? " " + cls : ""}" aria-hidden="true" focusable="false"><use href="#i-${name}"></use></svg>`;
const posterUrl = (thumb, w = 120) => (thumb ? `/api/poster?w=${w}&img=${encodeURIComponent(thumb)}` : "");
const artUrl = (art) => (art ? `/api/art?img=${encodeURIComponent(art)}` : "");

function posterImg(thumb, w = 120, cls = "poster") {
  return thumb
    ? `<img class="${cls}" src="${posterUrl(thumb, w)}" alt="" loading="lazy" decoding="async" onerror="this.removeAttribute('src')">`
    : `<span class="${cls}"></span>`;
}

const libName = (id) => (S.libraries.find((l) => String(l.section_id) === String(id)) || {}).name || "";
const KIND = { movie: "Movie", show: "Show", season: "Season" };
const SERVICE = { radarr: "Radarr", sonarr: "Sonarr" };
const canDelete = (kind) => {
  const ig = (S.status && S.status.integrations) || {};
  return kind ? !!(kind === "movie" ? ig.radarr : ig.sonarr) : !!(ig.radarr || ig.sonarr);
};
const nameOf = (r) => (r.kind === "season" ? `${r.show_title || "Unknown show"} – ${r.title}` : r.title);

/* ------------------------------------------------------------ server */

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (r.status === 401 && path !== "/api/login") {
    location.href = `/login?next=${encodeURIComponent(location.pathname + location.search)}`;
    throw new Error("Signed out");
  }
  if (!r.ok) {
    let msg = `Request failed (${r.status})`;
    try { msg = (await r.json()).detail || msg; } catch (_) { /* not json */ }
    throw new Error(msg);
  }
  return r.status === 204 ? null : r.json();
}
const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body || {}) });
const put = (path, body) => api(path, { method: "PUT", body: JSON.stringify(body || {}) });
const del = (path) => api(path, { method: "DELETE" });

/* ------------------------------------------------------------ feedback */

let toastTimer, toastHide;
function toast(msg, undo, error = false) {
  const t = $("#toast");
  clearTimeout(toastTimer);
  clearTimeout(toastHide);
  t.classList.toggle("is-err", !!error);
  t.innerHTML = `${error ? icon("alert") : ""}<span>${esc(msg)}</span>${undo ? '<button type="button">Undo</button>' : ""}`;
  if (undo) $("button", t).onclick = () => { hideToast(); undo(); };
  // Re-showing the popover puts it above any dialog that opened since.
  if (t.showPopover) { try { if (t.matches(":popover-open")) t.hidePopover(); t.showPopover(); } catch (_) { /* unsupported */ } }
  t.classList.add("is-on");
  toastTimer = setTimeout(hideToast, undo ? 7000 : error ? 6500 : 3500);
}
function hideToast() {
  const t = $("#toast");
  t.classList.remove("is-on");
  clearTimeout(toastHide);
  toastHide = setTimeout(() => { if (t.hidePopover && !t.classList.contains("is-on")) { try { t.hidePopover(); } catch (_) { /* already hidden */ } } }, 250);
}
const fail = (e) => toast(e && e.message ? e.message : String(e), null, true);

const store = {
  get(k, d) { try { const v = localStorage.getItem(`shelflife.${k}`); return v == null ? d : JSON.parse(v); } catch (_) { return d; } },
  set(k, v) { try { localStorage.setItem(`shelflife.${k}`, JSON.stringify(v)); } catch (_) { /* storage off */ } },
};

// A small promise-based confirm built on the shared flow dialog.
function confirmDlg({ title, body = "", ok = "OK", danger = false }) {
  const dlg = $("#flow");
  dlg.className = "dlg flow small";
  dlg.innerHTML = `<form method="dialog">
    <div class="dlg-head"><h2>${esc(title)}</h2></div>
    <div class="dlg-body">${body}</div>
    <div class="dlg-foot"><span class="grow"></span><button class="btn ghost" value="cancel">Cancel</button>
    <button class="btn ${danger ? "danger" : ""}" value="ok">${esc(ok)}</button></div></form>`;
  return new Promise((resolve) => {
    dlg.addEventListener("close", () => resolve(dlg.returnValue === "ok"), { once: true });
    dlg.returnValue = "";
    dlg.showModal();
  });
}


// The page behind the detail panel and Review mode doesn't scroll; padding stands in for the scrollbar so nothing shifts.
function syncScrollLock() {
  const lock = $("#drawer").classList.contains("is-open") || S.reviewOpen;
  const b = document.body;
  if (lock && !b.classList.contains("locked")) {
    b.style.setProperty("--sbw", `${Math.max(0, innerWidth - document.documentElement.clientWidth)}px`);
    b.classList.add("locked");
  } else if (!lock) b.classList.remove("locked");
}
