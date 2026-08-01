/* Logbook SPA — vanilla JS, no build step. */
"use strict";

const TOKEN_KEY = "lb_token";
const ACCT_KEY = "lb_account";
let TOKEN = localStorage.getItem(TOKEN_KEY) || null;
let ACCOUNTS = [];
let ACTIVE_ACCOUNT = localStorage.getItem(ACCT_KEY) ? Number(localStorage.getItem(ACCT_KEY)) : null;
let MECHANISMS = []; // cached for selects (per active account)
let LOGBOOK = { days: [], page: 0 };

const VIEWS = [
  { id: "inbox", label: "Inbox" },
  { id: "trades", label: "Logbook" },
  { id: "analytics", label: "Analytics" },
  { id: "mechanisms", label: "Mechanisms" },
  { id: "equity", label: "Equity" },
  { id: "settings", label: "Settings" },
];

/* ---------------- utilities ---------------- */
const $ = (sel, root = document) => root.querySelector(sel);
const el = (id) => document.getElementById(id);

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
/* Minimal, dependency-free Markdown → HTML for pasted agent feedback.
   Escapes HTML first, then handles headings, bold/italic/code, lists, tables, hr. */
function mdInline(s) {
  return s
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*\n]+)\*/g, "<em>$1</em>");
}
function mdToHtml(src) {
  if (!src) return "";
  src = String(src).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const lines = src.split(/\r?\n/);
  const out = [];
  let i = 0;
  const isSep = (l) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$/.test(l || "");
  const cells = (l) => l.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
  const isTableStart = (idx) => lines[idx].includes("|") && isSep(lines[idx + 1]);
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    let m = line.match(/^(#{1,6})\s+(.*)$/);
    if (m) { const lvl = Math.min(m[1].length + 1, 4); out.push(`<h${lvl}>${mdInline(m[2])}</h${lvl}>`); i++; continue; }
    if (/^\s*---+\s*$/.test(line)) { out.push("<hr>"); i++; continue; }
    if (isTableStart(i)) {
      const header = cells(line); i += 2; const rows = [];
      while (i < lines.length && lines[i].includes("|") && lines[i].trim()) { rows.push(cells(lines[i])); i++; }
      out.push("<table><thead><tr>" + header.map((c) => `<th>${mdInline(c)}</th>`).join("") +
        "</tr></thead><tbody>" +
        rows.map((r) => "<tr>" + r.map((c) => `<td>${mdInline(c)}</td>`).join("") + "</tr>").join("") +
        "</tbody></table>");
      continue;
    }
    if (/^\s*[-*]\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) { items.push(mdInline(lines[i].replace(/^\s*[-*]\s+/, ""))); i++; }
      out.push("<ul>" + items.map((x) => `<li>${x}</li>`).join("") + "</ul>"); continue;
    }
    if (/^\s*\d+\.\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) { items.push(mdInline(lines[i].replace(/^\s*\d+\.\s+/, ""))); i++; }
      out.push("<ol>" + items.map((x) => `<li>${x}</li>`).join("") + "</ol>"); continue;
    }
    const para = [];
    while (i < lines.length && lines[i].trim()
           && !/^(#{1,6})\s|^\s*[-*]\s+|^\s*\d+\.\s+|^\s*---+\s*$/.test(lines[i])
           && !isTableStart(i)) { para.push(mdInline(lines[i])); i++; }
    out.push(`<p>${para.join("<br>")}</p>`);
  }
  return out.join("\n");
}

function money(v) {
  if (v == null) return "—";
  const n = Number(v);
  const s = (n >= 0 ? "+" : "−") + "$" + Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 });
  return s;
}
function pnlClass(v) { if (v == null) return "flat"; if (v > 0) return "win"; if (v < 0) return "loss"; return "flat"; }
function pct(v) { return v == null ? "—" : (Number(v).toFixed(1) + "%"); }
function timeShort(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return esc(iso);
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}
function toast(msg) {
  const t = document.createElement("div");
  t.className = "toast"; t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 2600);
}

async function api(path, { method = "GET", body } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (TOKEN) headers["Authorization"] = "Bearer " + TOKEN;
  if (ACTIVE_ACCOUNT) headers["X-Account-ID"] = String(ACTIVE_ACCOUNT);
  const res = await fetch("/api" + path, {
    method, headers, body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401) { logout(); throw new Error("Session expired"); }
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (e) {}
    throw new Error(detail);
  }
  if (res.status === 204) return null;
  return res.json();
}

/* ---------------- auth ---------------- */
function showLogin() { el("login-view").classList.remove("hidden"); el("app-view").classList.add("hidden"); }
function showApp() { el("login-view").classList.add("hidden"); el("app-view").classList.remove("hidden"); }

async function doLogin(e) {
  e.preventDefault();
  el("login-error").textContent = "";
  try {
    const data = await (await fetch("/api/auth/login", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: el("login-username").value, password: el("login-password").value }),
    }).then(async (r) => { if (!r.ok) throw new Error((await r.json()).detail || "Login failed"); return r; })).json();
    TOKEN = data.access_token;
    localStorage.setItem(TOKEN_KEY, TOKEN);
    await boot();
  } catch (err) {
    el("login-error").textContent = err.message;
  }
}
function logout() {
  TOKEN = null; localStorage.removeItem(TOKEN_KEY);
  location.hash = ""; showLogin();
}

/* ---------------- accounts ---------------- */
async function loadAccounts() {
  let accts = [];
  try { accts = await api("/auth/accounts"); } catch (e) { return; }
  ACCOUNTS = Array.isArray(accts) ? accts : (accts.accounts || []);
  if (!ACTIVE_ACCOUNT || !ACCOUNTS.some((a) => a.id === ACTIVE_ACCOUNT)) {
    const def = ACCOUNTS.find((a) => a.is_default) || ACCOUNTS[0];
    ACTIVE_ACCOUNT = def ? def.id : null;
    if (ACTIVE_ACCOUNT) localStorage.setItem(ACCT_KEY, String(ACTIVE_ACCOUNT));
  }
}
function renderAccountSwitcher() {
  const sel = el("account-switcher");
  if (!sel) return;
  sel.innerHTML = ACCOUNTS.map((a) =>
    `<option value="${a.id}" ${a.id === ACTIVE_ACCOUNT ? "selected" : ""}>${esc(a.name)}</option>`
  ).join("");
}
async function onAccountSwitch() {
  const sel = el("account-switcher");
  ACTIVE_ACCOUNT = Number(sel.value);
  localStorage.setItem(ACCT_KEY, String(ACTIVE_ACCOUNT));
  MECHANISMS = [];
  LOGBOOK = { days: [], page: 0 };
  route();
}

/* ---------------- nav / routing ---------------- */
function renderNav(active) {
  el("nav").innerHTML = VIEWS.map((v) =>
    `<button data-view="${v.id}" class="${v.id === active ? "active" : ""}">${v.label}</button>`
  ).join("");
  el("nav").querySelectorAll("button").forEach((b) =>
    b.addEventListener("click", () => { location.hash = b.dataset.view; })
  );
}
function currentView() { return (location.hash.replace("#", "") || "inbox"); }
async function route() {
  if (!TOKEN) return;
  const view = currentView();
  renderNav(view);
  const main = el("main");
  main.innerHTML = `<p class="muted">Loading…</p>`;
  try {
    if (view === "inbox") await renderInbox(main);
    else if (view === "trades") await renderTrades(main);
    else if (view === "analytics") await renderAnalytics(main);
    else if (view === "mechanisms") await renderMechanisms(main);
    else if (view === "equity") await renderEquity(main);
    else if (view === "settings") await renderSettings(main);
    else main.innerHTML = `<p class="empty">Not found.</p>`;
  } catch (err) {
    main.innerHTML = `<p class="empty">${esc(err.message)}</p>`;
  }
}

/* ---------------- Inbox ---------------- */
async function renderInbox(main) {
  const [fillsResp, motd, sugResp] = await Promise.all([
    api("/journal/fills"),
    api("/journal/motd").catch(() => null),
    api("/journal/fills/suggestions").catch(() => ({ suggestions: [] })),
  ]);
  const { fills } = fillsResp;
  const suggestions = (sugResp && sugResp.suggestions) || [];
  const fillsById = new Map(fills.map((f) => [f.id, f]));

  // Distinct trade dates, newest first (fills already arrive DESC by executed_at).
  const dateCounts = new Map();
  fills.forEach((f) => {
    const d = f.trade_date || "";
    if (d) dateCounts.set(d, (dateCounts.get(d) || 0) + 1);
  });
  const dates = [...dateCounts.keys()];
  const dateFilter = dates.length > 1
    ? `<select id="date-filter" class="date-filter">
         <option value="">All days (${fills.length})</option>
         ${dates.map((d) => `<option value="${esc(d)}">${esc(fmtDay(d))} (${dateCounts.get(d)})</option>`).join("")}
       </select>`
    : "";

  main.innerHTML = `
    ${motd ? motdBanner(motd) : ""}
    <div class="sticky-actions">
      <span class="title">Inbox</span>
      <div class="group-actions">
        ${dateFilter}
        ${suggestions.length ? `<button class="btn btn-ghost btn-sm" id="suggest-btn">Suggest groups (${suggestions.length})</button>` : ""}
        <button class="btn btn-ghost btn-sm" id="sync-btn">Sync fills</button>
        <button class="btn btn-primary btn-sm" id="group-btn" disabled>Group selected</button>
      </div>
    </div>
    <p class="muted">Ungrouped Tastytrade fills. Select the legs of one trade, then group them.</p>
    ${suggestions.length ? `
    <div id="suggestions-panel" class="suggestions-panel">
      <div class="sug-panel-head">Suggested groups — tap Group to confirm each. Nothing is created until you do.</div>
      ${suggestions.map((s, i) => suggestionCard(s, i, fillsById)).join("")}
    </div>` : ""}
    <div class="card" style="padding:0">
      ${fills.length ? fills.map(fillRow).join("") : `<p class="empty">Inbox empty. Hit “Sync fills”.</p>`}
    </div>`;

  const selected = new Set();
  const groupBtn = el("group-btn");
  const refreshGroupBtn = () => {
    groupBtn.disabled = selected.size === 0;
    groupBtn.textContent = selected.size ? `Group selected (${selected.size})` : "Group selected";
  };
  main.querySelectorAll(".fill-check").forEach((c) =>
    c.addEventListener("change", () => {
      c.checked ? selected.add(Number(c.dataset.id)) : selected.delete(Number(c.dataset.id));
      refreshGroupBtn();
    })
  );
  main.querySelectorAll(".dismiss-btn").forEach((b) =>
    b.addEventListener("click", async (e) => {
      e.stopPropagation();
      await api(`/journal/fills/${b.dataset.id}/dismiss`, { method: "POST" });
      toast("Fill dismissed"); route();
    })
  );
  el("sync-btn").addEventListener("click", async () => {
    el("sync-btn").disabled = true; el("sync-btn").textContent = "Syncing…";
    try { const r = await api("/journal/fills/sync", { method: "POST" }); toast(`Synced ${r.synced}, skipped ${r.skipped}`); route(); }
    catch (err) { toast(err.message); el("sync-btn").disabled = false; el("sync-btn").textContent = "Sync fills"; }
  });
  groupBtn.addEventListener("click", async () => {
    try {
      const trade = await api("/journal/trades", { method: "POST", body: { fill_ids: [...selected] } });
      toast("Trade grouped — add your notes");
      await route();          // refresh inbox in place (grouped fills drop off)
      openTrade(trade.id);    // journal it without leaving the inbox
    } catch (err) { toast(err.message); }
  });

  const suggestBtn = el("suggest-btn");
  if (suggestBtn) {
    const panel = el("suggestions-panel");
    suggestBtn.addEventListener("click", () => panel.classList.toggle("hidden"));
  }
  main.querySelectorAll(".suggest-group-btn").forEach((b) =>
    b.addEventListener("click", async () => {
      const ids = b.dataset.ids.split(",").map(Number);
      b.disabled = true;
      try {
        const trade = await api("/journal/trades", { method: "POST", body: { fill_ids: ids } });
        toast("Trade grouped — add your notes");
        await route();          // refresh; grouped fills + this suggestion drop off
        openTrade(trade.id);
      } catch (err) { toast(err.message); b.disabled = false; }
    })
  );

  const dateSel = el("date-filter");
  if (dateSel) {
    dateSel.addEventListener("change", () => {
      const pick = dateSel.value;
      // Clear selection when the visible set changes so you can't group across hidden days.
      selected.clear();
      main.querySelectorAll(".fill-row").forEach((row) => {
        const show = !pick || row.dataset.date === pick;
        row.classList.toggle("hidden", !show);
        const chk = row.querySelector(".fill-check");
        if (chk) chk.checked = false;
      });
      refreshGroupBtn();
    });
  }
}
function motdBanner(m) {
  const conv = m.avg_conviction ? ` · conviction ${m.avg_conviction}` : "";
  const pnl = m.trades_today ? ` · <span class="pnl ${pnlClass(m.realized_pnl)}">${money(m.realized_pnl)}</span>` : "";
  const stats = m.trades_today
    ? `${m.trades_today} closed today · ${m.system} system / ${m.discretionary} discretionary${conv}${pnl}`
    : `streak holding at ${m.current_streak}`;
  return `<div class="motd">
    <div class="motd-streak"><span class="num">${m.current_streak}</span><span>streak</span></div>
    <div class="motd-body">
      <div class="motd-headline">${esc(m.headline)}</div>
      <div class="motd-stats">${stats}</div>
    </div>
  </div>`;
}
function suggestionCard(s, i, fillsById) {
  const legs = s.fill_ids.map((id) => fillsById.get(id)).filter(Boolean);
  const under = legs.length ? legs[0].underlying : "";
  const legLines = legs.map((f) =>
    `<div class="sug-leg num">${esc(f.action)} ${esc(f.option_type)} ${esc(f.strike)} · ${f.is_opening ? "open" : "close"} · qty ${esc(f.quantity)} · <span class="sug-leg-time">${timeShort(f.executed_at)}</span></div>`
  ).join("");
  return `<div class="suggestion">
    <div class="sug-head">
      <span class="badge">${esc(s.reason)}</span>
      <strong>${esc(under)}</strong>
      <span class="muted">${s.size} legs</span>
      <button class="btn btn-primary btn-sm suggest-group-btn" data-ids="${s.fill_ids.join(",")}">Group</button>
    </div>
    ${legLines}
  </div>`;
}
function fillRow(f) {
  const side = f.is_opening ? "open" : "close";
  return `<div class="fill-row" data-date="${esc(f.trade_date || "")}">
    <input type="checkbox" class="fill-check" data-id="${f.id}" data-date="${esc(f.trade_date || "")}" />
    <div class="desc">
      <span class="action num">${esc(f.action)}</span>
      <span>${esc(f.underlying)} ${esc(f.option_type)} ${esc(f.strike)} · ${esc(f.expiration)}</span>
      <span class="badge">${side}</span>
      <div class="when">${timeShort(f.executed_at)} · qty ${esc(f.quantity)} @ <span class="num">${esc(f.price)}</span></div>
    </div>
    <button class="btn btn-ghost btn-sm dismiss-btn" data-id="${f.id}">Dismiss</button>
  </div>`;
}

/* ---------------- Trades ---------------- */
async function renderTrades(main) {
  const { days } = await api("/journal/logbook");
  LOGBOOK.days = days;
  renderLogbookPage(main);
}
function fmtDay(d) {
  const dt = new Date(d + "T00:00:00");
  if (isNaN(dt)) return d;
  return dt.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric", year: "numeric" });
}
function renderLogbookPage(main) {
  const days = LOGBOOK.days;
  if (!days.length) {
    main.innerHTML = `<div class="page-header"><h1 class="page-title">Logbook</h1></div>
      <p class="empty">No trades yet. Group fills from the Inbox.</p>`;
    return;
  }
  LOGBOOK.page = Math.max(0, Math.min(LOGBOOK.page, days.length - 1));
  const i = LOGBOOK.page;
  const day = days[i];
  const s = day.stats;
  main.innerHTML = `
    <div class="page-header">
      <h1 class="page-title">Logbook</h1>
      <div class="page-actions logbook-nav">
        <button class="btn btn-ghost btn-sm" id="lb-newer" ${i === 0 ? "disabled" : ""}>← Newer</button>
        <span class="muted num">page ${i + 1} / ${days.length}</span>
        <button class="btn btn-ghost btn-sm" id="lb-older" ${i >= days.length - 1 ? "disabled" : ""}>Older →</button>
      </div>
    </div>
    <div class="logbook-page">
      <div class="logbook-date">${esc(fmtDay(day.date))}</div>
      <div class="ai-feedback">
        <div class="ai-feedback-head">
          <span class="ai-feedback-label">Note to self</span>
          <button class="btn btn-ghost btn-sm ${day.note ? "" : "hidden"}" id="edit-note">Edit</button>
          <button class="btn btn-primary btn-sm ${day.note ? "hidden" : ""}" id="save-note">Save</button>
        </div>
        <div class="ai-feedback-rendered ${day.note ? "" : "hidden"}" id="note-view">${mdToHtml(day.note || "")}</div>
        <textarea id="day-note" class="ai-feedback-input ${day.note ? "hidden" : ""}" rows="3" placeholder="A message to yourself for this day — saved and rendered like the feedback box.">${esc(day.note || "")}</textarea>
      </div>
      <hr class="day-divider" />
      <div class="stack">${day.trades.map(tradeEntry).join("")}</div>
      <div class="logbook-footer">
        <div class="logbook-stats num">${s.count} closed · <span class="pnl ${pnlClass(s.total_pnl)}">${money(s.total_pnl)}</span> · win ${pct(s.win_rate)} · payoff ${s.payoff_ratio == null ? "—" : s.payoff_ratio}</div>
        <div class="logbook-msg">${esc(day.message)}</div>
      </div>
      <div class="ai-feedback">
        <div class="ai-feedback-head">
          <span class="ai-feedback-label">AI Agent Feedback</span>
          <button class="btn btn-ghost btn-sm ${day.ai_feedback ? "" : "hidden"}" id="edit-feedback">Edit</button>
          <button class="btn btn-primary btn-sm ${day.ai_feedback ? "hidden" : ""}" id="save-feedback">Save</button>
        </div>
        <div class="ai-feedback-rendered ${day.ai_feedback ? "" : "hidden"}" id="ai-feedback-view">${mdToHtml(day.ai_feedback || "")}</div>
        <textarea id="ai-feedback" class="ai-feedback-input ${day.ai_feedback ? "hidden" : ""}" rows="10" placeholder="Paste the agent's review here, then Save — it renders as formatted text and becomes a permanent part of this page.">${esc(day.ai_feedback || "")}</textarea>
      </div>
    </div>`;
  main.querySelectorAll(".entry").forEach((e) =>
    e.addEventListener("click", () => openTrade(Number(e.dataset.id))));

  const noteEl = el("day-note");
  const saveNoteBtn = el("save-note");
  const editNoteBtn = el("edit-note");
  const noteView = el("note-view");
  const noteDirty = () => noteEl && noteEl.value !== (day.note || "");
  async function saveNoteIfDirty() {
    if (!noteDirty()) return;
    await api(`/journal/day-notes/${day.date}`, { method: "PUT", body: { note: noteEl.value } });
    day.note = noteEl.value;
  }
  editNoteBtn.addEventListener("click", () => {
    noteView.classList.add("hidden"); editNoteBtn.classList.add("hidden");
    noteEl.classList.remove("hidden"); saveNoteBtn.classList.remove("hidden");
    noteEl.focus();
  });
  saveNoteBtn.addEventListener("click", async () => {
    try { await saveNoteIfDirty(); } catch (err) { toast(err.message); return; }
    noteView.innerHTML = mdToHtml(day.note || "");
    noteView.classList.remove("hidden"); editNoteBtn.classList.remove("hidden");
    noteEl.classList.add("hidden"); saveNoteBtn.classList.add("hidden");
    toast("Note saved");
  });

  const fbEl = el("ai-feedback");
  const saveFbBtn = el("save-feedback");
  const editFbBtn = el("edit-feedback");
  const fbView = el("ai-feedback-view");
  const fbDirty = () => fbEl && fbEl.value !== (day.ai_feedback || "");
  async function saveFeedbackIfDirty() {
    if (!fbDirty()) return;
    await api(`/journal/day-notes/${day.date}`, { method: "PUT", body: { ai_feedback: fbEl.value } });
    day.ai_feedback = fbEl.value;
  }
  editFbBtn.addEventListener("click", () => {
    fbView.classList.add("hidden"); editFbBtn.classList.add("hidden");
    fbEl.classList.remove("hidden"); saveFbBtn.classList.remove("hidden");
    fbEl.focus();
  });
  saveFbBtn.addEventListener("click", async () => {
    try { await saveFeedbackIfDirty(); } catch (err) { toast(err.message); return; }
    fbView.innerHTML = mdToHtml(day.ai_feedback || "");
    fbView.classList.remove("hidden"); editFbBtn.classList.remove("hidden");
    fbEl.classList.add("hidden"); saveFbBtn.classList.add("hidden");
    toast("Feedback saved");
  });

  // Turning the page auto-saves any unsaved note or feedback so edits aren't lost.
  const turn = (delta) => async () => {
    try { await saveNoteIfDirty(); await saveFeedbackIfDirty(); }
    catch (err) { toast(err.message); return; }
    LOGBOOK.page += delta;
    renderLogbookPage(main);
  };
  if (el("lb-newer")) el("lb-newer").addEventListener("click", turn(-1));
  if (el("lb-older")) el("lb-older").addEventListener("click", turn(1));
}
function regimeClass(t) {
  const r = (t.regime_read || "").toLowerCase();
  if (r === "range") return "r-range";
  if (r === "trend") return "r-trend";
  return "";
}
function conviction(n) {
  if (!n) return "";
  return "▲".repeat(n) + "△".repeat(Math.max(0, 5 - n));
}
function tradeEntry(t) {
  const sysBadge = t.is_system == null ? "" :
    (t.is_system ? `<span class="badge sys">system</span>` : `<span class="badge disc">discretionary</span>`);
  const pnl = t.status === "closed"
    ? `<span class="pnl ${pnlClass(t.realized_pnl)}">${money(t.realized_pnl)}</span>`
    : `<span class="pnl flat">open</span>`;
  return `<div class="entry ${regimeClass(t)}" data-id="${t.id}">
    <div class="tab"></div>
    <div class="body">
      <div class="meta">
        <span class="sym">${esc(t.underlying || "?")} · ${esc((t.direction || "").replace("_", " "))}</span>
        ${t.is_0dte ? `<span class="badge">0DTE</span>` : ""}
        ${sysBadge}
        ${t.mechanism_name ? `<span class="tags">${esc(t.mechanism_name)}</span>` : ""}
        <span class="conviction">${conviction(t.conviction)}</span>
      </div>
      ${t.why_entered ? `<div class="why">${esc(t.why_entered)}</div>` : ""}
    </div>
    <div class="side">
      ${pnl}
      <span class="delta">${t.strikes ? esc(t.strikes) : ""}${t.exp ? "" : ""}</span>
    </div>
  </div>`;
}

const EXIT_REASONS = ["", "target", "stop", "scratch", "time", "panic", "reversed", "discretionary"];
const EMOTIONS = ["", "calm", "disciplined", "fomo", "revenge", "bored"];
const THESIS = ["", "yes", "no", "partial"];

async function openTrade(id) {
  let trade;
  try { trade = await api(`/journal/trades/${id}`); }
  catch (err) { toast(err.message); return; }
  if (!MECHANISMS.length) { try { MECHANISMS = (await api("/journal/mechanisms")).mechanisms; } catch (e) {} }

  const mechOpts = `<option value="">— none —</option>` + MECHANISMS.map((m) =>
    `<option value="${m.id}" ${m.id === trade.mechanism_id ? "selected" : ""}>${esc(m.name)}</option>`).join("");
  const opt = (arr, cur) => arr.map((v) => `<option value="${v}" ${v === (cur || "") ? "selected" : ""}>${v || "—"}</option>`).join("");

  const greeks = (trade.greeks || []);
  const entryG = greeks.find((g) => g.snapshot_type === "entry");
  const exitG = greeks.find((g) => g.snapshot_type === "exit");
  const greekLine = (entryG || exitG)
    ? `<p class="delta">Δ entry ${entryG && entryG.delta != null ? entryG.delta : "—"} → exit ${exitG && exitG.delta != null ? exitG.delta : "—"}</p>` : "";

  const fillsHtml = (trade.fills || []).map((f) =>
    `<div class="when">${esc(f.action)} ${esc(f.quantity)} ${esc(f.option_type)} ${esc(f.strike)} @ <span class="num">${esc(f.price)}</span> · ${timeShort(f.executed_at)}</div>`).join("");

  const body = `
    <h2>${esc(trade.underlying || "?")} · ${esc((trade.direction || "").replace("_", " "))}
      ${trade.is_0dte ? `<span class="badge">0DTE</span>` : ""}</h2>
    <p class="muted">${esc(trade.strikes || "")} · exp ${esc(trade.expiration || "")} · ${esc(trade.status)}
      ${trade.status === "closed" ? ` · <span class="pnl ${pnlClass(trade.realized_pnl)}">${money(trade.realized_pnl)}</span> (${pct(trade.realized_pnl_pct)})` : ""}</p>
    ${greekLine}
    <div class="card" style="margin:10px 0">${fillsHtml || `<span class="muted">no fills</span>`}</div>

    <div class="field-grid">
      <label class="field">Mechanism<select id="f-mech">${mechOpts}</select></label>
      <label class="field">System vs discretionary
        <select id="f-system"><option value="">—</option><option value="1" ${trade.is_system === 1 ? "selected" : ""}>system</option><option value="0" ${trade.is_system === 0 ? "selected" : ""}>discretionary</option></select></label>
      <label class="field">Conviction
        <select id="f-conv"><option value="">—</option>${[1,2,3,4,5].map((n) => `<option value="${n}" ${trade.conviction === n ? "selected" : ""}>${"▲".repeat(n)}</option>`).join("")}</select></label>
      <label class="field">Regime read
        <select id="f-regime">${opt(["", "range", "trend", "chop"], trade.regime_read)}</select></label>
      <label class="field">Did the thesis work?<select id="f-thesis">${opt(THESIS, trade.thesis_worked)}</select></label>
      <label class="field">Exit reason<select id="f-exit">${opt(EXIT_REASONS, trade.exit_reason)}</select></label>
      <label class="field">Emotional state<select id="f-emotion">${opt(EMOTIONS, trade.emotional_state)}</select></label>
    </div>
    <label class="field">Why entered<textarea id="f-why" rows="2">${esc(trade.why_entered || "")}</textarea></label>
    <label class="field">Reflection<textarea id="f-reflect" rows="2">${esc(trade.reflection || "")}</textarea></label>

    <div class="modal-actions">
      <button class="btn btn-ghost" id="ungroup-trade" style="margin-right:auto;border-color:var(--loss);color:var(--loss)">Ungroup</button>
      ${trade.status === "open" ? `<button class="btn btn-ghost" id="attach-btn">Attach closing fills</button>` : ""}
      <button class="btn btn-ghost" id="close-modal">Close</button>
      <button class="btn btn-primary" id="save-trade">Save</button>
    </div>`;
  openModal(body);

  el("close-modal").addEventListener("click", closeModal);
  el("ungroup-trade").addEventListener("click", async () => {
    if (!confirm("Ungroup this trade? Its fills return to the inbox and the trade is deleted.")) return;
    try { await api(`/journal/trades/${id}`, { method: "DELETE" }); toast("Ungrouped — fills back in inbox"); closeModal(); route(); }
    catch (err) { toast(err.message); }
  });
  el("save-trade").addEventListener("click", async () => {
    const sysVal = el("f-system").value;
    const payload = {
      mechanism_id: el("f-mech").value ? Number(el("f-mech").value) : null,
      is_system: sysVal === "" ? null : sysVal === "1",
      conviction: el("f-conv").value ? Number(el("f-conv").value) : null,
      regime_read: el("f-regime").value || null,
      thesis_worked: el("f-thesis").value || null,
      exit_reason: el("f-exit").value || null,
      emotional_state: el("f-emotion").value || null,
      why_entered: el("f-why").value || null,
      reflection: el("f-reflect").value || null,
    };
    try { await api(`/journal/trades/${id}`, { method: "PUT", body: payload }); toast("Saved"); closeModal(); route(); }
    catch (err) { toast(err.message); }
  });
  if (el("attach-btn")) el("attach-btn").addEventListener("click", () => attachFills(id, trade.underlying));
}

async function attachFills(tradeId, underlying) {
  const { fills } = await api("/journal/fills");
  const candidates = fills.filter((f) => f.underlying === underlying);
  if (!candidates.length) { toast("No matching inbox fills"); return; }
  const rows = candidates.map((f) =>
    `<label class="fill-row" style="cursor:pointer">
      <input type="checkbox" class="att-check" data-id="${f.id}" />
      <span class="desc"><span class="action num">${esc(f.action)}</span> <strong>${esc(f.underlying)}</strong> ${esc(f.option_type)} ${esc(f.strike)} · ${esc(f.expiration)} · ${timeShort(f.executed_at)}</span>
      <span></span></label>`).join("");
  openModal(`<h2>Attach fills · ${esc(underlying)}</h2><div class="card" style="padding:0">${rows}</div>
    <div class="modal-actions"><button class="btn btn-ghost" id="att-cancel">Cancel</button><button class="btn btn-primary" id="att-save">Attach</button></div>`);
  el("att-cancel").addEventListener("click", () => openTrade(tradeId));
  el("att-save").addEventListener("click", async () => {
    const ids = [...document.querySelectorAll(".att-check:checked")].map((c) => Number(c.dataset.id));
    if (!ids.length) { toast("Select at least one fill"); return; }
    try { await api(`/journal/trades/${tradeId}/fills`, { method: "POST", body: { fill_ids: ids } }); toast("Attached"); closeModal(); route(); }
    catch (err) { toast(err.message); }
  });
}

/* ---------------- Mechanisms ---------------- */
async function renderMechanisms(main) {
  const { mechanisms } = await api("/journal/mechanisms");
  MECHANISMS = mechanisms;
  main.innerHTML = `
    <div class="page-header">
      <h1 class="page-title">Mechanisms</h1>
      <div class="page-actions"><button class="btn btn-primary btn-sm" id="add-mech">New edge</button></div>
    </div>
    <p class="muted" style="margin-top:-8px">The edges you trade. Each trade is measured against one.</p>
    <div class="stack">
      ${mechanisms.length ? mechanisms.map(mechCard).join("") : `<p class="empty">No mechanisms yet.</p>`}
    </div>`;
  el("add-mech").addEventListener("click", () => editMechanism(null));
  main.querySelectorAll("[data-edit-mech]").forEach((b) =>
    b.addEventListener("click", () => editMechanism(mechanisms.find((m) => m.id === Number(b.dataset.editMech)))));
  main.querySelectorAll("[data-del-mech]").forEach((b) =>
    b.addEventListener("click", async () => {
      if (!confirm("Delete this mechanism?")) return;
      await api(`/journal/mechanisms/${b.dataset.delMech}`, { method: "DELETE" }); toast("Deleted"); route();
    }));
}
function mechCard(m) {
  return `<div class="card">
    <div class="meta" style="display:flex;justify-content:space-between;gap:10px;align-items:baseline">
      <strong>${esc(m.name)}</strong>
      <span class="badge">${esc(m.status)}${m.regime ? " · " + esc(m.regime) : ""}</span>
    </div>
    ${m.criteria ? `<p class="muted" style="margin:6px 0">${esc(m.criteria)}</p>` : ""}
    ${m.notes ? `<p class="delta">${esc(m.notes)}</p>` : ""}
    <div class="modal-actions" style="margin-top:8px">
      <button class="btn btn-ghost btn-sm" data-edit-mech="${m.id}">Edit</button>
      <button class="btn btn-ghost btn-sm" data-del-mech="${m.id}">Delete</button>
    </div>
  </div>`;
}
function editMechanism(m) {
  const isNew = !m;
  const opt = (arr, cur) => arr.map((v) => `<option value="${v}" ${v === (cur || "") ? "selected" : ""}>${v || "—"}</option>`).join("");
  openModal(`<h2>${isNew ? "New edge" : "Edit edge"}</h2>
    <label class="field">Name<input id="m-name" value="${esc(m ? m.name : "")}" /></label>
    <label class="field">Criteria (the rule)<textarea id="m-criteria" rows="2">${esc(m ? m.criteria || "" : "")}</textarea></label>
    <div class="field-grid">
      <label class="field">Regime<select id="m-regime">${opt(["", "range", "trend", "chop", "n/a"], m && m.regime)}</select></label>
      <label class="field">Status<select id="m-status">${opt(["developing", "validated", "retired"], m ? m.status : "developing")}</select></label>
    </div>
    <label class="field">Notes<textarea id="m-notes" rows="2">${esc(m ? m.notes || "" : "")}</textarea></label>
    <div class="modal-actions"><button class="btn btn-ghost" id="m-cancel">Cancel</button><button class="btn btn-primary" id="m-save">Save</button></div>`);
  el("m-cancel").addEventListener("click", closeModal);
  el("m-save").addEventListener("click", async () => {
    const payload = { name: el("m-name").value.trim(), criteria: el("m-criteria").value || null,
      regime: el("m-regime").value || null, status: el("m-status").value, notes: el("m-notes").value || null };
    if (!payload.name) { toast("Name required"); return; }
    try {
      if (isNew) await api("/journal/mechanisms", { method: "POST", body: payload });
      else await api(`/journal/mechanisms/${m.id}`, { method: "PUT", body: payload });
      toast("Saved"); closeModal(); route();
    } catch (err) { toast(err.message); }
  });
}

/* ---------------- Analytics ---------------- */
async function renderAnalytics(main) {
  const a = await api("/journal/analytics");
  const d = a.discipline;
  const statRows = (rows, keyName) => rows.length ? rows.map((r) =>
    `<tr><td>${esc(r[keyName] != null ? r[keyName] : r.key)}</td><td class="n">${r.count}</td>
     <td class="n"><span class="pnl ${pnlClass(r.total_pnl)}">${money(r.total_pnl)}</span></td>
     <td class="n">${pct(r.win_rate)}</td><td class="n">${r.payoff_ratio == null ? "—" : r.payoff_ratio}</td></tr>`
  ).join("") : `<tr><td colspan="5" class="muted">no closed trades</td></tr>`;
  const svd = a.system_vs_discretionary;

  main.innerHTML = `
    <div class="page-header"><h1 class="page-title">Analytics</h1></div>

    <div class="discipline">
      <div class="streak-n num">${d.current_streak}</div>
      <div class="streak-label">trade discipline streak — consecutive system trades exited by plan, regardless of P&amp;L. Longest: ${d.longest_streak}.</div>
      <div class="dissonance">
        <div class="diss-item"><span class="diss-n">${d.dissonance.won_but_broke_system}</span><span class="diss-l">won but broke the system</span></div>
        <div class="diss-item"><span class="diss-n">${d.dissonance.lost_but_well_executed}</span><span class="diss-l">lost but well executed</span></div>
      </div>
    </div>

    <p class="muted">Overall: <span class="num">${a.overall.count}</span> closed ·
      <span class="pnl ${pnlClass(a.overall.total_pnl)}">${money(a.overall.total_pnl)}</span> ·
      win rate ${pct(a.overall.win_rate)} · payoff ${a.overall.payoff_ratio == null ? "—" : a.overall.payoff_ratio}</p>

    <h3 class="section-title">By mechanism</h3>
    <table><thead><tr><th>Edge</th><th class="n">n</th><th class="n">P&amp;L</th><th class="n">Win%</th><th class="n">Payoff</th></tr></thead>
      <tbody>${statRows(a.by_mechanism, "mechanism_name")}</tbody></table>

    <h3 class="section-title">System vs discretionary</h3>
    <table><thead><tr><th></th><th class="n">n</th><th class="n">P&amp;L</th><th class="n">Win%</th><th class="n">Payoff</th></tr></thead>
      <tbody>
        <tr><td>System</td><td class="n">${svd.system.count}</td><td class="n"><span class="pnl ${pnlClass(svd.system.total_pnl)}">${money(svd.system.total_pnl)}</span></td><td class="n">${pct(svd.system.win_rate)}</td><td class="n">${svd.system.payoff_ratio == null ? "—" : svd.system.payoff_ratio}</td></tr>
        <tr><td>Discretionary</td><td class="n">${svd.discretionary.count}</td><td class="n"><span class="pnl ${pnlClass(svd.discretionary.total_pnl)}">${money(svd.discretionary.total_pnl)}</span></td><td class="n">${pct(svd.discretionary.win_rate)}</td><td class="n">${svd.discretionary.payoff_ratio == null ? "—" : svd.discretionary.payoff_ratio}</td></tr>
      </tbody></table>

    <h3 class="section-title">By instrument</h3>
    <table><thead><tr><th>Underlying</th><th class="n">n</th><th class="n">P&amp;L</th><th class="n">Win%</th><th class="n">Payoff</th></tr></thead>
      <tbody>${statRows(a.by_instrument, "key")}</tbody></table>

    <h3 class="section-title">By regime</h3>
    <table><thead><tr><th>Regime</th><th class="n">n</th><th class="n">P&amp;L</th><th class="n">Win%</th><th class="n">Payoff</th></tr></thead>
      <tbody>${statRows(a.by_regime, "key")}</tbody></table>`;
}

/* ---------------- Equity ---------------- */
async function renderEquity(main) {
  const { curve } = await api("/journal/equity");
  main.innerHTML = `
    <div class="page-header"><h1 class="page-title">Equity</h1></div>
    ${curve.length ? `<table><thead><tr><th>Date</th><th class="n">Net liq</th><th class="n">SPY</th></tr></thead>
      <tbody>${curve.map((p) => `<tr><td>${esc(p.snapshot_date)}</td><td class="n">$${Number(p.portfolio_value || 0).toLocaleString()}</td><td class="n">${p.spy_price ? Number(p.spy_price).toFixed(2) : "—"}</td></tr>`).join("")}</tbody></table>`
      : `<p class="empty">No equity snapshots yet. They are recorded daily after close.</p>`}`;
}

/* ---------------- Settings ---------------- */
async function renderSettings(main) {
  let cur = {};
  try { cur = await api("/journal/settings"); } catch (e) {}

  const today = cur.sync_from_date || "2026-06-26";
  main.innerHTML = `
    <div class="page-header"><h1 class="page-title">Settings</h1>
      <div class="page-actions"><button class="btn btn-primary btn-sm" id="save-settings">Save</button></div>
    </div>

    <h3 class="section-title">Accounts</h3>
    <div class="card">
      <p class="muted" style="margin-top:0">Create one isolated journal per Tastytrade account. Each gets its own trades, mechanisms, notes, and analytics — switch between them in the top bar.</p>
      <button class="btn btn-ghost btn-sm" id="provision-accounts">Sync my Tastytrade accounts</button>
    </div>

    <h3 class="section-title">This journal pulls from</h3>
    <div class="card">
      <p style="margin:0"><strong class="num">${esc(cur.tt_account_number || "— not set —")}</strong>
      <span class="muted">· bound when you sync your Tastytrade accounts. One journal per account; the binding is fixed so trades can't cross accounts.</span></p>
    </div>
    <h3 class="section-title">Sync trades from</h3>
    <div class="card">
      <label class="field" style="max-width:220px">Start date (no history before this)
        <input type="date" id="sync-from" value="${esc(today)}" />
      </label>
      <p class="muted" style="margin:0">Only fills executed on or after this date are pulled.</p>
    </div>`;

  el("provision-accounts").addEventListener("click", async () => {
    const btn = el("provision-accounts");
    btn.disabled = true; btn.textContent = "Syncing…";
    try {
      const res = await api("/journal/accounts/provision", { method: "POST" });
      await loadAccounts();
      renderAccountSwitcher();
      toast(`${res.accounts.length} account${res.accounts.length === 1 ? "" : "s"} ready — pick one in the top bar`);
      route();  // re-render settings with refreshed state
    } catch (err) { toast(err.message); btn.disabled = false; btn.textContent = "Sync my Tastytrade accounts"; }
  });

  el("save-settings").addEventListener("click", async () => {
    // Binding is owned by provisioning; Settings only edits the sync-from date.
    try { await api("/journal/settings", { method: "PUT", body: { sync_from_date: el("sync-from").value || null } }); toast("Settings saved"); }
    catch (err) { toast(err.message); }
  });
}

/* ---------------- modal ---------------- */
function openModal(html) {
  closeModal();
  const wrap = document.createElement("div");
  wrap.className = "modal-backdrop";
  wrap.innerHTML = `<div class="modal">${html}</div>`;
  // Intentionally NOT closing on backdrop click — these forms hold unsaved
  // input, and an accidental outside click must not discard it. Dismiss only
  // via the explicit Close / Cancel / Save buttons inside the modal.
  el("modal-root").appendChild(wrap);
}
function closeModal() { el("modal-root").innerHTML = ""; }

/* ---------------- boot ---------------- */
async function boot() {
  try { await api("/auth/me"); }
  catch (e) { showLogin(); return; }
  showApp();
  await loadAccounts();
  renderAccountSwitcher();
  if (!location.hash) location.hash = "inbox";
  route();
}

window.addEventListener("hashchange", route);
el("login-form").addEventListener("submit", doLogin);
el("logout-btn").addEventListener("click", logout);
el("account-switcher").addEventListener("change", onAccountSwitch);

if (TOKEN) boot(); else showLogin();
