"use strict";
/* Zap Drop web UI - vanilla JS, no build step. */

// ---------------------------------------------------------------- helpers
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];

function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "html") el.innerHTML = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

const ICONS = {
  folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  file: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>',
  drive: '<rect x="3" y="6" width="18" height="12" rx="2"/><path d="M7 14h.01M11 14h6"/>',
  pc: '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>',
  up: '<path d="M12 19V5M5 12l7-7 7 7"/>',
  refresh: '<path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/>',
  plusfolder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M12 11v5M9.5 13.5h5"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  moon: '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
  lock: '<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
  shield: '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/>',
  hand: '<path d="M8 13V5.5a1.5 1.5 0 0 1 3 0V12M11 11.5v-7a1.5 1.5 0 0 1 3 0V12M14 12V6.5a1.5 1.5 0 0 1 3 0V15a6 6 0 0 1-6 6h-1a6 6 0 0 1-5-2.7L3 15a1.5 1.5 0 0 1 2.5-1.6L8 16"/>',
  globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
  down: '<path d="M12 5v14M5 12l7 7 7-7"/>',
  send: '<path d="M12 19V5M5 12l7-7 7 7"/>',
  pause: '<path d="M8 5v14M16 5v14"/>',
  play: '<path d="M7 4l13 8-13 8z"/>',
  x: '<path d="M6 6l12 12M18 6L6 18"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
  open: '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
  logout: '<path d="M15 4h4a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-4M10 17l5-5-5-5M15 12H3"/>',
};
function icon(name, cls = "") {
  const t = document.createElement("template");
  t.innerHTML = `<svg viewBox="0 0 24 24" class="${cls}">${ICONS[name] || ""}</svg>`;
  return t.content.firstChild;
}

function fmtBytes(n) {
  n = Number(n) || 0;
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i === 0 ? 0 : n < 10 ? 2 : n < 100 ? 1 : 0)} ${u[i]}`;
}
const fmtSpeed = (n) => `${fmtBytes(n)}/s`;
function fmtDur(s) {
  if (s == null || !isFinite(s)) return "";
  s = Math.max(0, Math.round(s));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}
function fmtDate(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  return d.toLocaleDateString(undefined, { day: "2-digit", month: "short", year: "numeric" }) + " " +
    d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}
const MODE_INFO = {
  pin: { label: "PIN", icon: "lock", desc: "Others enter your PIN to connect." },
  pin_tls: { label: "PIN + encryption", icon: "shield", desc: "PIN, and all traffic is encrypted (TLS)." },
  approve: { label: "Approve requests", icon: "hand", desc: "You accept or decline each device." },
  open: { label: "Open", icon: "globe", desc: "Anyone on your network can connect." },
};
const lsGet = (k, d = null) => { try { return localStorage.getItem(k) ?? d; } catch { return d; } };
const lsSet = (k, v) => { try { localStorage.setItem(k, v); } catch { } };

function toast(msg, kind = "") {
  const t = h("div", { class: `toast ${kind}` }, msg);
  $("#toasts").append(t);
  setTimeout(() => t.remove(), kind === "err" ? 6000 : 3500);
}

// ---------------------------------------------------------------- API
async function api(method, url, body) {
  const opt = { method, headers: { "X-ZapDrop": "1" }, credentials: "same-origin" };
  if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  const r = await fetch(url, opt);
  let data = null;
  try { data = await r.json(); } catch { }
  if (r.status === 401 && data && data.login) { showLogin(); throw new Error("Login required"); }
  if (!r.ok) {
    const e = new Error((data && data.detail) || r.statusText || "Request failed");
    e.status = r.status; e.peer = data && data.peer_status;
    throw e;
  }
  return data;
}
const errToast = (e) => toast(e.message || String(e), "err");

// ---------------------------------------------------------------- state
const S = {
  data: null,
  tab: "files",
  peerId: lsGet("zapdrop-peer"),
  local: { path: lsGet("zapdrop-local", ""), parent: null, entries: [], quick: [], sel: new Set(), anchor: null },
  remote: { path: "", entries: [], sel: new Set(), anchor: null, loadedFor: null, error: "", loading: false },
  lastPeerStatus: undefined,
  shownApprovals: new Set(),
  settingsDirty: false,
};
let dragFrom = null;

const me = () => (S.data && S.data.me) || {};
const peer = () => (S.data && S.data.peers.find((p) => p.id === S.peerId)) || null;

// ---------------------------------------------------------------- boot / login
async function boot() {
  let sess;
  try { sess = await api("GET", "/api/session"); } catch (e) { document.body.textContent = "Cannot reach Zap Drop: " + e.message; return; }
  if (!sess.authed) return showLogin(sess);
  $("#login").classList.add("hidden");
  $("#app").classList.remove("hidden");
  $("#logout-btn").classList.toggle("hidden", sess.local);
  setupStatic();
  await poll();
  loadLocal(S.local.path);
  setInterval(poll, 1000);
}

function showLogin(sess) {
  $("#app").classList.add("hidden");
  $("#login").classList.remove("hidden");
  if (sess && !sess.admin_set) {
    $("#login-sub").textContent = "Remote control is off. On the PC running Zap Drop, set an admin password in Settings.";
  }
  $("#login-pw").focus();
}

$("#login-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  $("#login-err").textContent = "";
  try {
    await api("POST", "/api/login", { password: $("#login-pw").value });
    location.reload();
  } catch (e) { $("#login-err").textContent = e.message; }
});

// ---------------------------------------------------------------- polling
let polling = false;
async function poll() {
  if (polling) return;
  polling = true;
  try {
    const prev = new Map(((S.data && S.data.jobs) || []).map((j) => [j.id, j.state]));
    S.data = await api("GET", "/api/state");
    // refresh the left pane when a download into the open folder finishes
    const lp = (S.local.path || "").toLowerCase();
    if (lp && S.data.jobs.some((j) => j.kind !== "push_out" && j.state === "done" && prev.has(j.id) &&
      prev.get(j.id) !== "done" && (j.dest || "").toLowerCase().startsWith(lp))) {
      const keep = S.local.sel;
      loadLocal(S.local.path).then(() => { S.local.sel = keep; paintSel("local"); });
    }
    renderTop();
    renderPeers();
    renderBadge();
    checkApprovals();
    if (!peer() && S.data.peers.length === 1) { S.peerId = S.data.peers[0].id; lsSet("zapdrop-peer", S.peerId); renderPeers(); }
    const p = peer();
    const status = p ? `${p.id}:${p.status}:${p.online}` : null;
    if (status !== S.lastPeerStatus) {
      S.lastPeerStatus = status;
      renderRemote();
    }
    if (S.tab === "transfers") renderJobs();
    if (S.tab === "settings" && !S.settingsDirty && !document.activeElement?.closest("#settings")) renderSettings();
  } catch (e) {
    // server restarting or network blip: keep trying silently
  } finally { polling = false; }
}

// ---------------------------------------------------------------- top bar + sidebar
function renderTop() {
  const m = me();
  $("#me-alias").textContent = m.alias || "";
  const pill = $("#host-pill");
  pill.textContent = m.hosting ? `Hosting · ${MODE_INFO[m.mode]?.label}` : "Not hosting";
  pill.classList.toggle("on", !!m.hosting);
  const foot = $("#side-foot");
  foot.replaceChildren(
    h("div", {}, "This PC: ", h("b", {}, m.alias || "")),
    h("div", {}, (m.ips || []).map((ip) => `${ip}:${m.port}`).join(", ") || "No network address"),
    ...(m.discovery_error ? [h("div", { class: "error" }, m.discovery_error)] : []),
  );
}

function renderPeers() {
  const list = $("#peer-list");
  const peers = S.data.peers;
  if (!peers.length) {
    list.replaceChildren(h("div", { class: "side-empty" },
      "No other PCs found yet. Start Zap Drop on the other PC and turn on hosting there, or add it by IP."));
    return;
  }
  list.replaceChildren(...peers.map((p) => {
    const statusTxt = !p.online ? "Offline" : p.status === "connected" ? "Connected" :
      p.status === "pending" ? "Waiting for approval" : p.status === "denied" ? "Declined" : "Not connected";
    const el = h("div", {
      class: `peer ${p.online ? "online" : ""} ${p.id === S.peerId ? "active" : ""}`,
      title: `${p.host}:${p.port}`,
      onclick: () => selectPeer(p.id),
    },
      h("div", { class: "avatar" }, icon("pc")),
      h("div", { class: "pmeta" },
        h("div", { class: "pname" }, p.alias),
        h("div", { class: "psub" }, icon(MODE_INFO[p.mode]?.icon || "lock"), statusTxt)));
    el.addEventListener("dragover", (ev) => { if (dragFrom === "local") { ev.preventDefault(); el.classList.add("drop"); } });
    el.addEventListener("dragleave", () => el.classList.remove("drop"));
    el.addEventListener("drop", (ev) => { ev.preventDefault(); el.classList.remove("drop"); if (dragFrom === "local") sendTo(p.id); });
    return el;
  }));
}

function renderBadge() {
  const active = S.data.jobs.filter((j) => ["queued", "running"].includes(j.state)).length;
  const b = $("#tx-badge");
  b.textContent = active;
  b.classList.toggle("hidden", !active);
}

function selectPeer(id) {
  if (S.peerId !== id) {
    S.peerId = id;
    lsSet("zapdrop-peer", id);
    S.remote = { path: "", entries: [], sel: new Set(), anchor: null, loadedFor: null, error: "", loading: false };
  }
  S.lastPeerStatus = undefined;
  switchTab("files");
  renderPeers();
  renderRemote();
}

// ---------------------------------------------------------------- static wiring
function setupStatic() {
  $("#theme-btn").replaceChildren(icon(isDark() ? "sun" : "moon"));
  $("#theme-btn").onclick = () => {
    const next = isDark() ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    lsSet("zapdrop-theme", next);
    $("#theme-btn").replaceChildren(icon(isDark() ? "sun" : "moon"));
  };
  $("#logout-btn").replaceChildren(icon("logout"));
  $("#logout-btn").onclick = async () => { await api("POST", "/api/logout"); location.reload(); };
  $("#host-pill").onclick = toggleHosting;
  $("#add-peer-btn").onclick = addPeerDialog;
  $$("#tabs button").forEach((b) => (b.onclick = () => switchTab(b.dataset.tab)));
  for (const pane of ["local", "remote"]) {
    const root = $(`#${pane}-pane`);
    $('[data-act="up"]', root)?.replaceChildren(icon("up"));
    $('[data-act="refresh"]', root)?.replaceChildren(icon("refresh"));
    $('[data-act="mkdir"]', root)?.replaceChildren(icon("plusfolder"));
    $('[data-act="up"]', root).onclick = () => (pane === "local" ? localUp() : remoteUp());
    $('[data-act="refresh"]', root).onclick = () => (pane === "local" ? loadLocal(S.local.path) : loadRemote(S.remote.path));
    setupListKeys(pane);
    setupPaneDrop(pane);
  }
  $('#local-pane [data-act="mkdir"]').onclick = localMkdir;
  $("#send-btn").onclick = () => sendTo(S.peerId);
  $("#download-btn").onclick = () => downloadTo(S.local.path);
  $("#clear-done-btn").onclick = clearFinished;
  $("#clear-history-btn").onclick = async () => {
    if (await confirmDialog("Clear history?", "This removes the list of past transfers. Files are not touched.", "Clear")) {
      await api("DELETE", "/api/history"); renderHistory();
    }
  };
}
const isDark = () => {
  const t = document.documentElement.dataset.theme;
  return t ? t === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
};

function switchTab(tab) {
  S.tab = tab;
  $$("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  $$(".tab").forEach((t) => t.classList.toggle("active", t.id === `tab-${tab}`));
  if (tab === "transfers") renderJobs();
  if (tab === "history") renderHistory();
  if (tab === "settings") { S.settingsDirty = false; renderSettings(); }
}

async function toggleHosting() {
  const m = me();
  if (!m.hosting && ["pin", "pin_tls"].includes(m.mode) && !m.has_pin) {
    toast("Set a PIN first (Settings → Hosting).", "err");
    switchTab("settings");
    return;
  }
  try {
    await api("POST", "/api/settings", { hosting: !m.hosting });
    await poll();
    toast(!m.hosting ? "Hosting on. Other PCs can now see this one." : "Hosting off.", "ok");
  } catch (e) { errToast(e); }
}

// ---------------------------------------------------------------- file lists (shared)
function rowEl(pane, e, key, isRoot) {
  const st = S[pane];
  const sel = st.sel.has(key);
  const cb = h("input", { type: "checkbox" });
  cb.checked = sel;
  const ic = e.is_dir ? (isRoot && pane === "local" ? "drive" : "folder") : "file";
  const row = h("div", { class: `row ${sel ? "sel" : ""}`, draggable: "true", "data-key": key },
    cb,
    h("div", { class: "name" }, icon(ic, ic), h("span", { title: e.name }, e.name)),
    h("div", { class: "size" }, e.is_dir ? "" : fmtBytes(e.size)),
    h("div", { class: "date" }, e.mtime ? fmtDate(e.mtime) : ""));
  cb.addEventListener("click", (ev) => { ev.stopPropagation(); toggleSel(pane, key, true); });
  row.addEventListener("click", (ev) => clickRow(pane, key, ev));
  row.addEventListener("dblclick", () => { if (e.is_dir) openEntry(pane, e, key); });
  row.addEventListener("dragstart", (ev) => {
    if (!st.sel.has(key)) { st.sel = new Set([key]); st.anchor = key; paintSel(pane); }
    dragFrom = pane;
    ev.dataTransfer.effectAllowed = "copy";
    ev.dataTransfer.setData("text/plain", `${st.sel.size} item(s)`);
  });
  row.addEventListener("dragend", () => { dragFrom = null; $$(".drop").forEach((x) => x.classList.remove("drop")); });
  if (pane === "local" && e.is_dir) { // drop remote items into a specific local folder
    row.addEventListener("dragover", (ev) => { if (dragFrom === "remote") { ev.preventDefault(); ev.stopPropagation(); row.classList.add("drop"); } });
    row.addEventListener("dragleave", () => row.classList.remove("drop"));
    row.addEventListener("drop", (ev) => {
      if (dragFrom !== "remote") return;
      ev.preventDefault(); ev.stopPropagation(); row.classList.remove("drop");
      downloadTo(key);
    });
  }
  return row;
}

function keysOf(pane) { return $$(`#${pane}-list .row[data-key]`).map((r) => r.dataset.key); }

function clickRow(pane, key, ev) {
  const st = S[pane];
  if (ev.shiftKey && st.anchor) {
    const keys = keysOf(pane);
    const a = keys.indexOf(st.anchor), b = keys.indexOf(key);
    if (a >= 0 && b >= 0) {
      const [x, y] = a < b ? [a, b] : [b, a];
      if (!ev.ctrlKey) st.sel.clear();
      keys.slice(x, y + 1).forEach((k) => st.sel.add(k));
    }
  } else if (ev.ctrlKey || ev.metaKey) {
    toggleSel(pane, key, false); return;
  } else {
    st.sel = new Set([key]);
    st.anchor = key;
  }
  paintSel(pane);
}

function toggleSel(pane, key) {
  const st = S[pane];
  if (st.sel.has(key)) st.sel.delete(key); else st.sel.add(key);
  st.anchor = key;
  paintSel(pane);
}

function paintSel(pane) {
  const st = S[pane];
  $$(`#${pane}-list .row[data-key]`).forEach((r) => {
    const on = st.sel.has(r.dataset.key);
    r.classList.toggle("sel", on);
    const cb = $("input", r); if (cb) cb.checked = on;
  });
  updateFooters();
}

function setupListKeys(pane) {
  const list = $(`#${pane}-list`);
  list.addEventListener("keydown", (ev) => {
    const st = S[pane];
    if (ev.key === "a" && (ev.ctrlKey || ev.metaKey)) {
      ev.preventDefault(); st.sel = new Set(keysOf(pane)); paintSel(pane);
    } else if (ev.key === "Backspace") {
      ev.preventDefault(); pane === "local" ? localUp() : remoteUp();
    } else if (ev.key === "Escape") {
      st.sel.clear(); paintSel(pane);
    } else if (ev.key === "Enter" && st.sel.size === 1) {
      const key = [...st.sel][0];
      const e = st.entries.find((x) => entryKey(pane, x) === key);
      if (e && e.is_dir) openEntry(pane, e, key);
    }
  });
}

function setupPaneDrop(pane) {
  const el = $(`#${pane}-pane`);
  const accepts = () => (pane === "remote" ? dragFrom === "local" : dragFrom === "remote");
  el.addEventListener("dragover", (ev) => { if (accepts()) { ev.preventDefault(); el.classList.add("drop"); } });
  el.addEventListener("dragleave", (ev) => { if (!el.contains(ev.relatedTarget)) el.classList.remove("drop"); });
  el.addEventListener("drop", (ev) => {
    el.classList.remove("drop");
    if (!accepts()) return;
    ev.preventDefault();
    if (pane === "remote") sendTo(S.peerId); else downloadTo(S.local.path);
  });
}

function entryKey(pane, e) {
  if (pane === "local") return e.path;
  return S.remote.path ? `${S.remote.path}/${e.name}` : e.name;
}

function openEntry(pane, e, key) {
  if (pane === "local") loadLocal(e.path); else loadRemote(key);
}

function crumbs(el, parts, onPick) {
  const kids = [];
  parts.forEach((p, i) => {
    if (i) kids.push(h("span", { class: "sep" }, "›"));
    kids.push(h("button", { onclick: () => onPick(p.value), title: p.label }, p.label));
  });
  el.replaceChildren(...kids);
}

function updateFooters() {
  const ls = S.local.sel.size, rs = S.remote.sel.size;
  const p = peer();
  const connected = p && p.status === "connected";
  $("#local-sel").textContent = ls ? `${ls} selected` : "Select files or folders to send";
  const send = $("#send-btn");
  send.disabled = !ls || !connected || !S.local.path;
  send.textContent = connected ? `Send to ${p.alias} →` : "Send →";
  send.title = !p ? "Pick a device first" : !connected ? "Connect to the device first" : `Goes to ${p.alias}'s receive folder`;
  const dl = $("#download-btn");
  dl.disabled = !rs || !connected || !S.local.path;
  $("#remote-sel").textContent = rs ? `${rs} selected` :
    (connected ? "Select items to download" : "");
  dl.title = S.local.path ? `Download into ${S.local.path}` : "Open a folder on this PC (left) first";
  dl.textContent = S.local.path ? `← Download here` : "← Download";
}

// ---------------------------------------------------------------- local pane
async function loadLocal(path) {
  try {
    const d = await api("GET", `/api/local/browse?path=${encodeURIComponent(path || "")}`);
    Object.assign(S.local, { path: d.path, parent: d.parent, entries: d.entries, quick: d.quick || [], sel: new Set(), anchor: null });
    lsSet("zapdrop-local", d.path);
    renderLocal();
  } catch (e) {
    if (path) { errToast(e); if (!S.local.entries.length) loadLocal(""); }
  }
}

function renderLocal() {
  const st = S.local;
  const list = $("#local-list");
  const parts = [{ label: "This PC", value: "" }];
  if (st.path) {
    const segs = st.path.split("\\").filter(Boolean);
    let acc = "";
    segs.forEach((s, i) => { acc = i === 0 ? s + "\\" : acc.replace(/\\?$/, "\\") + s; parts.push({ label: s, value: acc }); });
  }
  crumbs($("#local-crumbs"), parts, loadLocal);
  const frag = document.createDocumentFragment();
  if (!st.path && st.quick.length) {
    frag.append(h("div", { class: "quick" }, st.quick.map((q) =>
      h("button", { class: "chip", onclick: () => loadLocal(q.path) }, icon("folder"), q.name))));
  }
  frag.append(listHead());
  if (!st.entries.length) frag.append(h("div", { class: "list-empty" }, "This folder is empty"));
  st.entries.forEach((e) => frag.append(rowEl("local", e, e.path, !st.path)));
  list.replaceChildren(frag);
  list.scrollTop = 0;
  updateFooters();
}
const listHead = () => h("div", { class: "row list-head" }, h("span"), h("span", {}, "Name"),
  h("span", { class: "size" }, "Size"), h("span", { class: "date" }, "Modified"));

function localUp() { if (S.local.path) loadLocal(S.local.parent || ""); }

async function localMkdir() {
  if (!S.local.path) return toast("Open a drive or folder first.", "err");
  const name = await promptDialog("New folder", "Folder name", "");
  if (!name) return;
  try { await api("POST", "/api/local/mkdir", { path: S.local.path, name }); loadLocal(S.local.path); } catch (e) { errToast(e); }
}

// ---------------------------------------------------------------- remote pane
function renderRemote() {
  const p = peer();
  const list = $("#remote-list");
  const title = $("#remote-title");
  $("#remote-crumbs").replaceChildren();
  if (!p) {
    title.textContent = "Other PC";
    list.replaceChildren(h("div", { class: "center-card" },
      h("div", { class: "big-icon" }, icon("pc")),
      h("h3", {}, "Pick a device"),
      h("div", { class: "muted" }, "Choose a PC from the Devices list on the left to browse what it shares.")));
    updateFooters();
    return;
  }
  title.replaceChildren(icon("pc"), p.alias, h("span", { class: "state " + (p.online ? "done" : "") }, p.online ? "online" : "offline"));
  if (p.status !== "connected") {
    list.replaceChildren(connectCard(p));
    S.remote.loadedFor = null;
    updateFooters();
    return;
  }
  if (S.remote.loadedFor !== p.id) loadRemote(S.remote.path || "");
  else paintRemote();
}

function connectCard(p) {
  const card = h("div", { class: "center-card" }, h("div", { class: "big-icon" }, icon(MODE_INFO[p.mode]?.icon || "lock")));
  if (!p.online) {
    card.append(h("h3", {}, `${p.alias} is offline`),
      h("div", { class: "muted" }, "Start Zap Drop on that PC and turn on hosting."),
      p.manual ? h("button", { class: "btn danger sm", onclick: () => removePeer(p) }, "Remove device") : null);
    return card;
  }
  const err = h("div", { class: "error" }, p.error || "");
  if (p.mode === "pin" || p.mode === "pin_tls") {
    const inp = h("input", { type: "password", placeholder: "PIN", autocomplete: "off", inputmode: "numeric" });
    const form = h("form", {}, inp, h("button", { class: "btn primary", type: "submit" }, "Connect"));
    form.onsubmit = async (ev) => {
      ev.preventDefault();
      err.textContent = "";
      try { await api("POST", `/api/peers/${p.id}/connect`, { pin: inp.value }); await poll(); }
      catch (e) { err.textContent = e.message; }
    };
    card.append(h("h3", {}, `Connect to ${p.alias}`),
      h("div", { class: "muted" }, p.mode === "pin_tls" ? "Enter the PIN set on that PC. The connection will be encrypted." : "Enter the PIN set on that PC."),
      form, err);
    setTimeout(() => inp.focus(), 50);
  } else if (p.mode === "approve") {
    if (p.status === "pending") {
      card.append(h("h3", {}, "Waiting for approval"), h("div", { class: "muted" }, `Accept the request on ${p.alias}.`), h("span", { class: "spinner" }));
    } else {
      card.append(h("h3", {}, `Request access to ${p.alias}`),
        h("div", { class: "muted" }, "Someone at that PC has to accept the request."),
        h("button", { class: "btn primary", onclick: () => doConnect(p, err) }, "Send request"), err);
    }
  } else {
    card.append(h("h3", {}, `Connect to ${p.alias}`), h("div", { class: "muted" }, "This PC is open to everyone on the network."),
      h("button", { class: "btn primary", onclick: () => doConnect(p, err) }, "Connect"), err);
  }
  if (p.manual) card.append(h("button", { class: "link-btn", onclick: () => removePeer(p) }, "Remove device"));
  return card;
}

async function doConnect(p, errEl) {
  errEl.textContent = "";
  try { await api("POST", `/api/peers/${p.id}/connect`, {}); await poll(); } catch (e) { errEl.textContent = e.message; }
}

async function removePeer(p) {
  await api("DELETE", `/api/peers/${p.id}`);
  S.peerId = null; lsSet("zapdrop-peer", "");
  await poll(); renderRemote();
}

async function loadRemote(path) {
  const p = peer();
  if (!p) return;
  S.remote.loading = true;
  try {
    const d = await api("GET", `/api/peers/${p.id}/browse?path=${encodeURIComponent(path)}`);
    Object.assign(S.remote, { path, entries: d.entries, sel: new Set(), anchor: null, loadedFor: p.id, error: "" });
    paintRemote();
  } catch (e) {
    if (e.peer === 401 || e.peer === 403) { await poll(); renderRemote(); }
    else if (path) { errToast(e); }
    else { S.remote.error = e.message; S.remote.loadedFor = p.id; S.remote.entries = []; paintRemote(); }
  } finally { S.remote.loading = false; }
}

function paintRemote() {
  const st = S.remote, p = peer();
  const parts = [{ label: "Shared", value: "" }];
  if (st.path) {
    let acc = "";
    st.path.split("/").forEach((s) => { acc = acc ? `${acc}/${s}` : s; parts.push({ label: s, value: acc }); });
  }
  crumbs($("#remote-crumbs"), parts, loadRemote);
  const frag = document.createDocumentFragment();
  frag.append(listHead());
  if (st.error) frag.append(h("div", { class: "list-empty error" }, st.error));
  else if (!st.entries.length) frag.append(h("div", { class: "list-empty" },
    st.path ? "This folder is empty" : `${p ? p.alias : "This PC"} isn't sharing any folders yet. You can still send files to it.`));
  st.entries.forEach((e) => frag.append(rowEl("remote", e, entryKey("remote", e), !st.path)));
  $("#remote-list").replaceChildren(frag);
  $("#remote-list").scrollTop = 0;
  updateFooters();
}

function remoteUp() {
  if (!S.remote.path) return;
  const parts = S.remote.path.split("/"); parts.pop();
  loadRemote(parts.join("/"));
}

// ---------------------------------------------------------------- start transfers
async function sendTo(peerId) {
  const p = S.data.peers.find((x) => x.id === peerId);
  const paths = [...S.local.sel];
  if (!paths.length || !S.local.path) return toast("Select files or folders on this PC first.", "err");
  if (!p) return toast("Pick a device first.", "err");
  if (p.status !== "connected") { selectPeer(p.id); return toast(`Connect to ${p.alias} first.`, "err"); }
  toast(`Preparing ${paths.length} item(s) for ${p.alias}...`);
  try { handleDraft(await api("POST", "/api/push", { peer_id: p.id, paths }), p); } catch (e) { errToast(e); }
}

async function downloadTo(dest) {
  const p = peer();
  const paths = [...S.remote.sel];
  if (!paths.length) return toast("Select items on the other PC first.", "err");
  if (!dest) return toast("Open a folder on this PC (left pane) to download into.", "err");
  toast(`Preparing download of ${paths.length} item(s)...`);
  try { handleDraft(await api("POST", "/api/pull", { peer_id: p.id, paths, dest }), p); } catch (e) { errToast(e); }
}

function handleDraft(job, p) {
  if (job.conflicts_total > 0) return conflictDialog(job, p);
  toast(`Transfer started: ${job.label} (${fmtBytes(job.total_bytes)})`, "ok");
  poll();
}

// ---------------------------------------------------------------- transfers tab
function renderJobs() {
  const jobs = S.data ? S.data.jobs : [];
  const serving = (S.data && S.data.serving) || [];
  $("#serving").replaceChildren(serving.length ? h("div", { class: "serving" }, h("span", { class: "lbl" }, "Sharing from this PC"),
    serving.map((s) => h("span", {}, h("b", {}, s.alias), ` ${s.speed ? fmtSpeed(s.speed) : "idle"} · ${fmtBytes(s.bytes)} sent`))) : "");
  if (!jobs.length) {
    $("#job-list").replaceChildren(h("div", { class: "empty" }, "No transfers yet. Select files in the Files tab and send or download them."));
    return;
  }
  $("#job-list").replaceChildren(...jobs.map(jobCard));
}

function jobCard(j) {
  const out = j.kind === "push_out";
  const pct = j.total_bytes ? Math.min(100, (100 * j.done_bytes) / j.total_bytes) : (j.state === "done" ? 100 : 0);
  const stateLabel = { queued: "Queued", running: "Running", paused: "Paused", error: "Stopped", done: j.failed_files ? "Done with errors" : "Done", cancelled: "Cancelled" }[j.state] || j.state;
  const stats = [
    h("span", {}, h("b", {}, fmtBytes(j.done_bytes)), ` of ${fmtBytes(j.total_bytes)} (${pct.toFixed(pct < 10 ? 1 : 0)}%)`),
    h("span", {}, `${j.done_files} of ${j.total_files} files`),
  ];
  if (j.state === "running") {
    stats.push(h("span", {}, h("b", {}, fmtSpeed(j.speed || 0))));
    if (j.eta != null) stats.push(h("span", {}, `${fmtDur(j.eta)} left`));
  } else if (j.state === "done" && j.elapsed) {
    stats.push(h("span", {}, `took ${fmtDur(j.elapsed)} · avg ${fmtSpeed(j.done_bytes / Math.max(1, j.elapsed))}`));
  }
  if (j.failed_files) stats.push(h("span", { class: "error" }, `${j.failed_files} failed`));
  const btn = (ic, title, action, cls = "") => h("button", { class: `icon-btn sm ${cls}`, title, onclick: () => jobAction(j, action) }, icon(ic));
  const actions = [];
  if (j.state === "running") actions.push(btn("pause", "Pause", "pause"), btn("x", "Cancel", "cancel"));
  else if (["paused", "error", "queued"].includes(j.state)) actions.push(btn("play", "Resume", "resume"), btn("x", "Cancel", "cancel"));
  else actions.push(btn("trash", "Remove from list", "remove"));
  const msg = j.error ? h("div", { class: "msg err" }, j.error) :
    j.note ? h("div", { class: "msg note" }, j.note) :
      j.current ? h("div", { class: "msg muted" }, j.current) : null;
  return h("div", { class: `job ${j.state}` },
    h("div", { class: `dir ${out ? "out" : ""}` }, icon(out ? "send" : "down")),
    h("div", { class: "title" }, h("span", { class: "muted" }, out ? `To ${j.peer_alias}: ` : `From ${j.peer_alias}: `), j.label,
      h("span", { class: `state ${j.state}` }, stateLabel)),
    h("div", { class: "actions" }, actions),
    h("div", { class: "bar" }, h("div", { style: `width:${pct}%` })),
    h("div", { class: "stats" }, stats),
    msg,
    j.dest ? h("div", { class: "msg muted" }, (out ? "Into their folder: " : "Saving to: ") + j.dest) : null,
    j.failures && j.failures.length ? h("details", {}, h("summary", {}, `Problems (${j.failures.length})`),
      h("ul", {}, j.failures.map((f) => h("li", {}, f)))) : null);
}

async function jobAction(j, action) {
  if (action === "cancel" && !(await confirmDialog("Cancel transfer?",
    "Files that finished copying are kept; unfinished ones are deleted.", "Cancel transfer", "Keep going"))) return;
  try { await api("POST", `/api/jobs/${j.id}/${action}`); await poll(); renderJobs(); } catch (e) { errToast(e); }
}

async function clearFinished() {
  const done = S.data.jobs.filter((j) => ["done", "cancelled"].includes(j.state));
  for (const j of done) { try { await api("POST", `/api/jobs/${j.id}/remove`); } catch { } }
  await poll(); renderJobs();
}

// ---------------------------------------------------------------- history tab
async function renderHistory() {
  const { history } = await api("GET", "/api/history");
  const t = $("#history-table");
  if (!history.length) { t.replaceChildren(h("tr", {}, h("td", { class: "empty" }, "Nothing here yet."))); return; }
  t.replaceChildren(
    h("tr", {}, ["When", "", "Device", "What", "Files", "Size", "Time", "Avg speed", "Result"].map((x) => h("th", {}, x))),
    ...history.map((r) => h("tr", {},
      h("td", { class: "num" }, fmtDate(r.ts)),
      h("td", {}, r.direction === "sent" ? "↑ Sent to" : "↓ From"),
      h("td", {}, r.peer),
      h("td", {}, r.label, r.dest ? h("div", { class: "path" }, r.dest) : null, r.error ? h("div", { class: "path error" }, r.error) : null),
      h("td", { class: "num" }, r.files),
      h("td", { class: "num" }, fmtBytes(r.bytes)),
      h("td", { class: "num" }, fmtDur(r.duration)),
      h("td", { class: "num" }, r.duration ? fmtSpeed(r.bytes / Math.max(r.duration, 0.001)) : ""),
      h("td", {}, h("span", { class: `state ${r.status.startsWith("done") ? "done" : ""}` }, r.status)))));
}

// ---------------------------------------------------------------- settings tab
function renderSettings() {
  const m = me();
  if (!m.id) return;
  const root = $("#settings");
  const save = async (body, msg) => {
    try { await api("POST", "/api/settings", body); S.settingsDirty = false; await poll(); renderSettings(); if (msg) toast(msg, "ok"); }
    catch (e) { errToast(e); }
  };

  // identity
  const alias = h("input", { type: "text", value: m.alias, maxlength: 40 });
  alias.oninput = () => (S.settingsDirty = true);
  const identity = h("div", { class: "card" }, h("h3", {}, "This PC's name"),
    h("p", { class: "muted" }, "The alias other PCs see in their Devices list."),
    h("div", { class: "field" }, alias, h("button", { class: "btn primary", onclick: () => save({ alias: alias.value }, "Name saved") }, "Save")));

  // hosting
  const hostToggle = h("input", { type: "checkbox" });
  hostToggle.checked = m.hosting;
  hostToggle.onchange = () => toggleHosting().then(renderSettings);
  const modes = h("div", { class: "modes" }, Object.entries(MODE_INFO).map(([k, v]) =>
    h("button", { class: `mode ${m.mode === k ? "active" : ""}`, onclick: () => save({ mode: k }, `Access mode: ${v.label}`) },
      h("b", {}, icon(v.icon), v.label), h("span", {}, v.desc))));
  const pin = h("input", { type: "password", placeholder: m.has_pin ? "PIN is set. Type a new one to change it" : "Choose a PIN (min. 4 characters)", autocomplete: "new-password" });
  pin.oninput = () => (S.settingsDirty = true);
  const pinRow = ["pin", "pin_tls"].includes(m.mode) ? h("div", { class: "field" }, pin,
    h("button", { class: "btn primary", onclick: () => save({ pin: pin.value }, "PIN saved. Connected devices must enter it again.") }, m.has_pin ? "Change PIN" : "Set PIN")) : null;
  const hosting = h("div", { class: "card" },
    h("div", { class: "inline-row" }, h("div", {}, h("h3", {}, "Hosting"),
      h("p", { class: "muted", style: "margin:0" }, "When on, other PCs can see this one, browse its shared folders and send files to it.")),
      h("label", { class: "toggle" }, hostToggle, h("span"))),
    h("div", { style: "height:14px" }),
    h("div", { class: "muted", style: "font-size:12.5px;margin-bottom:6px" }, "Who can connect"),
    modes, pinRow,
    m.mode === "pin_tls" ? h("p", { class: "muted", style: "font-size:12.5px;margin:10px 0 0" },
      "Encryption uses a little more CPU. 1 Gbps is still reachable on most PCs.") : null);

  // shares
  const shares = h("div", { class: "card" }, h("h3", {}, "Shared folders"),
    h("p", { class: "muted" }, "Folders other PCs can browse and download from (read-only)."),
    h("div", { class: "share-list" }, m.shares.length ? m.shares.map((s) => h("div", { class: "share" },
      icon("folder", "folder"),
      h("div", { class: "s-meta" }, h("div", { class: "s-name" }, s.name), h("div", { class: "s-path" }, s.path)),
      h("button", { class: "btn ghost sm danger", onclick: async () => { await api("DELETE", `/api/shares/${encodeURIComponent(s.name)}`); await poll(); renderSettings(); } }, "Stop sharing")))
      : h("div", { class: "muted" }, "Nothing shared yet.")),
    h("button", {
      class: "btn", onclick: async () => {
        const path = await pickFolder("Choose a folder to share", S.local.path);
        if (!path) return;
        try { await api("POST", "/api/shares", { path }); await poll(); renderSettings(); toast("Folder shared", "ok"); } catch (e) { errToast(e); }
      }
    }, "+ Share a folder"));

  // receive dir
  const receive = h("div", { class: "card" }, h("h3", {}, "Receive folder"),
    h("p", { class: "muted" }, "Where files that other PCs send to you are saved."),
    h("div", { class: "field" }, h("input", { type: "text", value: m.receive_dir, readonly: true }),
      h("button", {
        class: "btn", onclick: async () => {
          const path = await pickFolder("Choose the receive folder", m.receive_dir);
          if (path) save({ receive_dir: path }, "Receive folder saved");
        }
      }, "Change")));

  // devices with access
  const clients = (S.data.clients || []);
  const access = h("div", { class: "card" }, h("h3", {}, "Devices with access"),
    h("p", { class: "muted" }, "PCs that connected to this one. Access lasts 7 days, or until you change the PIN or access mode."),
    clients.length ? h("div", { class: "kv", style: "margin-bottom:12px" }, clients.flatMap((c) => [h("div", {}, c.alias), h("div", {}, `${c.ip} · until ${fmtDate(c.expires)}`)]))
      : h("div", { class: "muted", style: "margin-bottom:12px" }, "None."),
    h("button", {
      class: "btn danger", disabled: !clients.length, onclick: async () => {
        await api("POST", "/api/revoke"); await poll(); renderSettings(); toast("All devices disconnected", "ok");
      }
    }, "Disconnect all"));

  // remote UI
  const pw = h("input", { type: "password", placeholder: m.has_admin ? "Password is set. Type a new one to change it" : "Min. 6 characters", autocomplete: "new-password" });
  pw.oninput = () => (S.settingsDirty = true);
  const remote = h("div", { class: "card" }, h("h3", {}, "Control this PC from other devices"),
    h("p", { class: "muted" }, `Open http://${(m.ips || ["this-pc"])[0]}:${m.port} on a phone or laptop on your network and sign in with this password. Without a password, the page only works on this PC.`),
    h("div", { class: "field" }, pw,
      h("button", { class: "btn primary", onclick: () => save({ admin_password: pw.value }, pw.value ? "Admin password saved" : "") }, m.has_admin ? "Change" : "Set password"),
      m.has_admin ? h("button", { class: "btn danger", onclick: () => save({ admin_password: "" }, "Remote control turned off") }, "Turn off") : null));

  const about = h("div", { class: "card" }, h("h3", {}, "About"),
    h("div", { class: "kv" },
      h("div", {}, "Version"), h("div", {}, m.version),
      h("div", {}, "Addresses"), h("div", {}, (m.ips || []).join(", ") || "-"),
      h("div", {}, "Ports"), h("div", {}, `${m.port} (app), ${m.tls_port} (encrypted), 47821 UDP (discovery)`),
      h("div", {}, "Certificate"), h("div", {}, h("code", {}, m.fingerprint))));

  root.replaceChildren(identity, hosting, shares, receive, access, remote, about);
}

// ---------------------------------------------------------------- dialogs
function modal({ title, body, foot, wide = false, onClose }) {
  const close = () => { bd.remove(); document.removeEventListener("keydown", onKey); onClose && onClose(); };
  const onKey = (ev) => { if (ev.key === "Escape") close(); };
  const bd = h("div", { class: "backdrop" },
    h("div", { class: `modal ${wide ? "wide" : ""}` },
      h("div", { class: "modal-head" }, title), h("div", { class: "modal-body" }, body), h("div", { class: "modal-foot" }, foot)));
  bd.addEventListener("mousedown", (ev) => { if (ev.target === bd) close(); });
  document.addEventListener("keydown", onKey);
  $("#modal-root").append(bd);
  return { close, el: bd };
}

function confirmDialog(title, text, okLabel = "OK", cancelLabel = "Cancel") {
  return new Promise((resolve) => {
    let done = false;
    const finish = (v) => { if (!done) { done = true; resolve(v); m.close(); } };
    const m = modal({
      title, body: h("p", {}, text), onClose: () => finish(false),
      foot: [h("button", { class: "btn", onclick: () => finish(false) }, cancelLabel),
      h("button", { class: "btn primary", onclick: () => finish(true) }, okLabel)],
    });
  });
}

function promptDialog(title, label, value) {
  return new Promise((resolve) => {
    let done = false;
    const inp = h("input", { type: "text", value, style: "width:100%" });
    const finish = (v) => { if (!done) { done = true; resolve(v); m.close(); } };
    const form = h("form", {}, h("p", { class: "muted" }, label), inp);
    form.onsubmit = (ev) => { ev.preventDefault(); finish(inp.value.trim()); };
    const m = modal({
      title, body: form, onClose: () => finish(null),
      foot: [h("button", { class: "btn", onclick: () => finish(null) }, "Cancel"),
      h("button", { class: "btn primary", onclick: () => finish(inp.value.trim()) }, "OK")],
    });
    setTimeout(() => inp.focus(), 30);
  });
}

function addPeerDialog() {
  const inp = h("input", { type: "text", placeholder: "e.g. 192.168.1.20 or 192.168.1.20:8765", style: "width:100%" });
  const err = h("p", { class: "error" });
  const go = async () => {
    err.textContent = "";
    try {
      const p = await api("POST", "/api/peers", { address: inp.value });
      m.close(); await poll(); selectPeer(p.id); toast(`Added ${p.alias}`, "ok");
    } catch (e) { err.textContent = e.message; }
  };
  const form = h("form", {}, h("p", { class: "muted" }, "Use this if the other PC doesn't show up automatically. Its address is shown at the bottom left of Zap Drop on that PC."), inp, err);
  form.onsubmit = (ev) => { ev.preventDefault(); go(); };
  const m = modal({
    title: "Add a device by IP", body: form,
    foot: [h("button", { class: "btn", onclick: () => m.close() }, "Cancel"), h("button", { class: "btn primary", onclick: go }, "Add")],
  });
  setTimeout(() => inp.focus(), 30);
}

function pickFolder(title, start) {
  return new Promise((resolve) => {
    let done = false, cur = "";
    const pathEl = h("div", { class: "muted", style: "font-size:12.5px;word-break:break-all" });
    const list = h("div", { class: "picker-list" });
    const okBtn = h("button", { class: "btn primary" }, "Select this folder");
    const finish = (v) => { if (!done) { done = true; resolve(v); m.close(); } };
    const load = async (p) => {
      try {
        const d = await api("GET", `/api/local/browse?path=${encodeURIComponent(p || "")}`);
        cur = d.path;
        pathEl.textContent = cur || "This PC";
        okBtn.disabled = !cur;
        const rows = [];
        if (cur) rows.push(h("div", { class: "row", onclick: () => load(d.parent || "") }, h("div", { class: "name" }, icon("up"), h("span", {}, ".."))));
        if (!cur) (d.quick || []).forEach((q) => rows.push(h("div", { class: "row", onclick: () => load(q.path) }, h("div", { class: "name" }, icon("folder", "folder"), h("span", {}, q.name)))));
        d.entries.filter((e) => e.is_dir).forEach((e) => rows.push(h("div", { class: "row", onclick: () => load(e.path) },
          h("div", { class: "name" }, icon(cur ? "folder" : "drive", cur ? "folder" : "drive"), h("span", {}, e.name)))));
        list.replaceChildren(...rows);
      } catch (e) { errToast(e); }
    };
    okBtn.onclick = () => finish(cur);
    const newBtn = h("button", {
      class: "btn", onclick: async () => {
        if (!cur) return;
        const name = await promptDialog("New folder", "Folder name", "");
        if (!name) return;
        try { const r = await api("POST", "/api/local/mkdir", { path: cur, name }); load(r.path); } catch (e) { errToast(e); }
      }
    }, "New folder");
    const m = modal({
      title, body: [pathEl, list], onClose: () => finish(null),
      foot: [h("div", { class: "left" }, newBtn), h("button", { class: "btn", onclick: () => finish(null) }, "Cancel"), okBtn],
    });
    load(start || "");
  });
}

function conflictDialog(job, p) {
  const out = job.kind === "push_out";
  const LABELS = { rename: "Keep both", overwrite: "Replace", skip: "Skip" };
  let def = "rename";
  const selects = [];
  const rows = job.conflicts.slice(0, 1000).map((c) => {
    const sel = h("select", {}, Object.entries(LABELS).map(([v, l]) => h("option", { value: v }, l)));
    sel.dataset.idx = c.idx;
    selects.push(sel);
    const newer = c.remote_mtime > c.local_mtime + 2 ? " (newer)" : "";
    return h("tr", {},
      h("td", {}, c.rel, c.is_dir ? h("div", { class: "path" }, "A folder with this name exists") : null),
      h("td", { class: "num" }, fmtBytes(c.local_size), h("div", { class: "path" }, fmtDate(c.local_mtime))),
      h("td", { class: "num" }, fmtBytes(c.remote_size), h("div", { class: "path" }, fmtDate(c.remote_mtime) + newer)),
      h("td", {}, sel));
  });
  const setAll = (v) => { def = v; selects.forEach((s) => (s.value = v)); };
  const more = job.conflicts_total > rows.length ? h("p", { class: "muted" }, `Showing ${rows.length} of ${job.conflicts_total}. The rest use the "apply to all" choice.`) : null;
  const body = [
    h("p", {}, `${job.conflicts_total} of ${job.total_files} file(s) already exist ${out ? `in ${p.alias}'s receive folder` : "in the destination"}.`),
    more,
    h("div", { class: "table-wrap" }, h("table", { class: "table conflicts" },
      h("tr", {}, h("th", {}, "File"), h("th", {}, out ? "On their PC" : "Already here"), h("th", {}, "Incoming"), h("th", {}, "Action")), rows)),
  ];
  let started = false;
  const start = async () => {
    const per = {};
    selects.forEach((s) => { if (s.value !== def) per[s.dataset.idx] = s.value; });
    try {
      await api("POST", `/api/jobs/${job.id}/start`, { default: def, per_file: per });
      started = true; m.close(); toast(`Transfer started: ${job.label}`, "ok"); poll();
    } catch (e) { errToast(e); }
  };
  const m = modal({
    title: "Some files already exist", body, wide: true,
    onClose: () => { if (!started) api("POST", `/api/jobs/${job.id}/discard`).catch(() => { }); },
    foot: [
      h("div", { class: "left" }, h("span", { class: "muted", style: "align-self:center;font-size:12.5px" }, "Apply to all:"),
        h("button", { class: "btn sm", onclick: () => setAll("rename") }, "Keep both"),
        h("button", { class: "btn sm", onclick: () => setAll("overwrite") }, "Replace"),
        h("button", { class: "btn sm", onclick: () => setAll("skip") }, "Skip")),
      h("button", { class: "btn", onclick: () => m.close() }, "Cancel"),
      h("button", { class: "btn primary", onclick: start }, "Start transfer"),
    ],
  });
}

function checkApprovals() {
  for (const a of S.data.approvals || []) {
    if (S.shownApprovals.has(a.id)) continue;
    S.shownApprovals.add(a.id);
    const answer = async (allow) => {
      try { await api("POST", `/api/approvals/${a.id}`, { allow }); toast(allow ? `${a.alias} can now access this PC` : `Declined ${a.alias}`, allow ? "ok" : ""); }
      catch (e) { errToast(e); }
      m.close();
    };
    const m = modal({
      title: "Access request",
      body: h("p", {}, h("b", {}, a.alias), ` (${a.ip}) wants to connect to this PC: browse your shared folders and send you files.`),
      foot: [h("button", { class: "btn", onclick: () => answer(false) }, "Decline"), h("button", { class: "btn primary", onclick: () => answer(true) }, "Allow")],
    });
  }
}

boot();
