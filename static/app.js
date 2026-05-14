// ── Chat panel boot buffer ────────────────────────
// Chat is a real panel now (panels/chat/view.html) rather than markup in
// index.html. That means #response-text / #chat-textbox / #stop-btn don't
// exist until panel-shell finishes its async injection — which can race
// against WS messages that arrive immediately after ws.onopen (e.g. the
// "connected to harness" info log).
//
// To handle the gap without losing output we:
//   1. Queue any appendResponse() call into _chatBootBuffer when its target
//      node isn't in the DOM yet.
//   2. Mirror busy / disabled state in _pendingChatInputEnabled.
//   3. Expose two flush hooks that the chat panel's inline script calls
//      once #response-text / #chat-textbox mount. See panels/chat/view.html.
//
// Once the panel is mounted the buffer is drained and these helpers become
// effective no-ops for the rest of the session.
const _chatBootBuffer = [];
let _pendingChatInputEnabled = null;
window._flushChatBootBuffer = function () {
  if (!_chatBootBuffer.length) return;
  const pending = _chatBootBuffer.splice(0, _chatBootBuffer.length);
  for (const { text, type } of pending) appendResponse(text, type);
};
window._applyPendingChatState = function () {
  if (_pendingChatInputEnabled !== null) {
    setInputEnabled(_pendingChatInputEnabled);
    _pendingChatInputEnabled = null;
  }
};

// ── Settings control wiring ───────────────────────
// Called from DOMContentLoaded AND from the settings panel's inline script
// after its content lands (panel content arrives via async fetch, well after
// DOMContentLoaded). Re-callable: each call uses dataset flags to avoid
// double-binding the same element.
function bindSettingsControls() {
  const keepHistoryToggle = document.getElementById("keep-history-toggle");
  if (keepHistoryToggle && !keepHistoryToggle.dataset.bound) {
    keepHistoryToggle.dataset.bound = "1";
    keepHistoryToggle.checked = localStorage.getItem("harness-keep-history") === "1";
    keepHistoryToggle.addEventListener("change", (e) => {
      localStorage.setItem("harness-keep-history", e.target.checked ? "1" : "0");
    });
  }

  const autoApplyToggle = document.getElementById("auto-apply-toggle");
  if (autoApplyToggle && !autoApplyToggle.dataset.bound) {
    autoApplyToggle.dataset.bound = "1";
    autoApplyToggle.addEventListener("change", (e) => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "set_auto_apply", enabled: e.target.checked }));
      }
    });
  }

  const templateBox = document.getElementById("file-click-template");
  if (templateBox && !templateBox.dataset.bound) {
    templateBox.dataset.bound = "1";
    const savedTemplate = localStorage.getItem("harness-click-template");
    if (savedTemplate !== null) {
      templateBox.value = savedTemplate;
    } else {
      templateBox.value = "Read `{path}` and reply with only the single word: ready";
    }
    templateBox.addEventListener("input", (e) => {
      localStorage.setItem("harness-click-template", e.target.value);
    });
  }

  const tempSlider = document.getElementById("temp-slider");
  const tempValue = document.getElementById("temp-value");
  if (tempSlider && !tempSlider.dataset.bound) {
    tempSlider.dataset.bound = "1";
    tempSlider.addEventListener("input", (e) => {
      const val = (parseInt(e.target.value) / 100).toFixed(2);
      if (tempValue) tempValue.textContent = val;
    });
    tempSlider.addEventListener("change", (e) => {
      const val = (parseInt(e.target.value) / 100).toFixed(2);
      if (tempValue) tempValue.textContent = val;
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "set_temperature", temperature: parseFloat(val) }));
      }
    });
  }
}
window.bindSettingsControls = bindSettingsControls;

// ── File tree ─────────────────────────────────────
async function refreshTree() {
  try {
    const res = await fetch("/tree");
    const data = await res.json();
    const container = document.getElementById("file-tree");
    if (!container) return;
    container.innerHTML = "";
    renderNodes(data.tree, container);
    if (data.root) setTitleFromPath(data.root);
  } catch (e) { /* server not up yet */ }
}

// ── Models ────────────────────────────────────────
async function fetchModels() {
  try {
    const res = await fetch("/models");
    const data = await res.json();
    const select = document.getElementById("model-select");
    if (!select) return;
    select.innerHTML = "";
    if (data.models && data.models.length > 0) {
      data.models.forEach(m => {
        const opt = document.createElement("option");
        opt.value = m;
        opt.textContent = m;
        if (m === data.current) opt.selected = true;
        select.appendChild(opt);
      });
      const box = document.getElementById("chat-textbox");
      if (box && data.current) {
        box.placeholder = `Message ${data.current}... (Enter to send, Shift+Enter for newline)`;
      }
    } else {
      const opt = document.createElement("option");
      opt.textContent = "No models found";
      opt.disabled = true;
      select.appendChild(opt);
    }
  } catch (e) {
    console.error("Failed to fetch models:", e);
  }
}

function bindModelSelect() {
  const modelSelect = document.getElementById("model-select");
  if (modelSelect && !modelSelect.dataset.bound) {
    modelSelect.dataset.bound = "1";
    modelSelect.addEventListener("change", (e) => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "set_model", model: e.target.value }));
      }
    });
  }
}
window.bindModelSelect = bindModelSelect;
document.addEventListener("DOMContentLoaded", bindModelSelect);

// ── Task Panel ────────────────────────────────────
async function refreshTaskPanel() {
  try {
    const res = await fetch("/api/task");
    const data = await res.json();
    const inner = document.getElementById("task-inner");
    if (!inner) return;
    if (!data.content || data.content.trim() === "") {
      inner.innerHTML = '<span class="task-empty">no task.md found</span>';
      return;
    }
    inner.innerHTML = renderTaskMarkdown(data.content);
  } catch (e) {
    console.error("Failed to fetch task:", e);
  }
}

function renderTaskMarkdown(md) {
  const lines = md.split("\n");
  let html = "";
  for (const line of lines) {
    const trimmed = line.trimStart();
    const indent = line.length - trimmed.length;
    const pad = Math.floor(indent / 2) * 12;
    // Checked: [x]
    const doneMatch = trimmed.match(/^[-*]\s*\[x\]\s*(.*)$/i);
    if (doneMatch) {
      html += `<div class="task-item" style="padding-left:${pad}px"><span class="task-check done">✓</span><span style="opacity:0.5;text-decoration:line-through">${esc(doneMatch[1])}</span></div>`;
      continue;
    }
    // In-progress: [/]
    const progMatch = trimmed.match(/^[-*]\s*\[\/\]\s*(.*)$/i);
    if (progMatch) {
      html += `<div class="task-item" style="padding-left:${pad}px"><span class="task-check in-progress">◉</span><span>${esc(progMatch[1])}</span></div>`;
      continue;
    }
    // Unchecked: [ ]
    const todoMatch = trimmed.match(/^[-*]\s*\[\s?\]\s*(.*)$/);
    if (todoMatch) {
      html += `<div class="task-item" style="padding-left:${pad}px"><span class="task-check">○</span><span>${esc(todoMatch[1])}</span></div>`;
      continue;
    }
    // Heading
    const headMatch = trimmed.match(/^(#{1,3})\s+(.*)$/);
    if (headMatch) {
      html += `<div style="font-weight:bold;opacity:0.85;padding:4px 0 2px;padding-left:${pad}px">${esc(headMatch[2])}</div>`;
      continue;
    }
    // Blank or other
    if (trimmed.length > 0) {
      html += `<div style="padding-left:${pad}px">${esc(trimmed)}</div>`;
    }
  }
  return html;
}

function esc(s) {
  return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}

// (Knowledge-packs UI removed — packs are now reached only via the
// `list_packs` / `load_pack` model tools in tools/core.py. The packs/
// and manual/ directories on disk are unchanged; only the chip UI +
// `set_packs` WS message + system-prompt manifest are gone.)

function setTitleFromPath(path) {
  if (!path) return;
  const base = String(path).replace(/[\\/]+$/, "").split(/[\\/]/).pop() || path;
  document.title = `Harness — ${base}`;
}

function renderNodes(nodes, container, parentPath = "") {
  for (const node of nodes) {
    const nodePath = parentPath ? `${parentPath}/${node.name}` : node.name;
    const row = document.createElement("div");
    row.className = `file-node ${node.type}`;
    row.textContent = (node.type === "dir" ? "▸ " : "  ") + node.name;

    if (node.type === "dir" && node.children?.length) {
      const children = document.createElement("div");
      children.className = "file-children";
      children.style.display = "none";
      renderNodes(node.children, children, nodePath);
      row.addEventListener("click", () => {
        const open = children.style.display !== "none";
        children.style.display = open ? "none" : "block";
        row.textContent = (open ? "▸ " : "▾ ") + node.name;
      });
      container.appendChild(row);
      container.appendChild(children);
    } else if (node.type === "file") {
      row.title = `click to ask Gemma to read ${nodePath}`;
      row.addEventListener("click", () => requestRead(nodePath));
      container.appendChild(row);
    } else {
      container.appendChild(row);
    }
  }
}

function requestRead(path) {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  if (busy) { log("busy — wait for current turn to finish", "info"); return; }
  
  const templateBox = document.getElementById("file-click-template");
  let template = templateBox ? templateBox.value.trim() : "";
  if (!template) {
    template = "Read `{path}` and reply with only the single word: ready";
  }
  if (!template.includes("{path}")) {
    log("file-click template is missing the `{path}` marker — not sending", "error");
    return;
  }
  const text = template.replace(/{path}/g, path);

  clearResponse();
  clearToolPanel();
  lastMsgType = "user";
  appendResponse(`You: ${text}\n\n`, "user");
  ws.send(JSON.stringify({ type: "message", content: text }));

  busy = true;
  setInputEnabled(false);
  showThinking();
}

// ── Theme ─────────────────────────────────────────
function setTheme(id) {
  document.documentElement.dataset.theme = id;
  localStorage.setItem("harness-theme", id);
}

function loadTheme() {
  const saved = localStorage.getItem("harness-theme");
  if (saved) document.documentElement.dataset.theme = saved;
}

async function fetchThemes() {
  try {
    const res = await fetch("/api/themes");
    const data = await res.json();
    const picker = document.getElementById("theme-picker");
    if (!picker || !data.themes || data.themes.length === 0) return;
    picker.innerHTML = "";
    for (const theme of data.themes) {
      const opt = document.createElement("div");
      opt.className = "theme-option";
      opt.onclick = () => setTheme(theme.id);
      const row = document.createElement("div");
      row.className = "swatch-row";
      for (const color of theme.swatches) {
        const s = document.createElement("span");
        s.className = "swatch";
        s.style.background = color;
        row.appendChild(s);
      }
      opt.appendChild(row);
      const name = document.createElement("span");
      name.className = "theme-name";
      name.textContent = `theme ${theme.id}`;
      opt.appendChild(name);
      picker.appendChild(opt);
    }
  } catch (e) {
    console.error("Failed to fetch themes:", e);
  }
}

// ── WebSocket ─────────────────────────────────────
let ws = null;
let busy = false;
let lastMsgType = null;
let spectating = null;       // session_id currently being spectated, or null
let spectateLastId = 0;      // highest event id we've rendered (for tail/resume)
const spectateCounts = {};   // type -> count, since last attach

function _devFilterAllows(type) {
  const cb = document.querySelector(`#dev-filters input[data-evt="${type}"]`);
  // If we don't have a chip for this type, allow it by default.
  return cb ? cb.checked : true;
}

function _updateDevCounts() {
  const el = document.getElementById("dev-counts");
  if (!el) return;
  const entries = Object.entries(spectateCounts);
  if (entries.length === 0) { el.textContent = spectating ? "waiting for events…" : "no events yet"; return; }
  entries.sort((a, b) => b[1] - a[1]);
  el.textContent = entries.map(([t, n]) => `${t}:${n}`).join("  ");
}

function _setSpectateStatus(text, live = false) {
  const el = document.getElementById("spectate-status");
  if (!el) return;
  el.textContent = text;
  el.classList.toggle("live", !!live);
}

async function refreshSpectateSessions() {
  try {
    const r = await fetch("/api/events/sessions");
    const { sessions } = await r.json();
    const sel = document.getElementById("spectate-select");
    if (!sel) return;
    const keep = sel.value;
    sel.innerHTML = `<option value="">Spectate session…</option>` +
      sessions.map(s => {
        const when = new Date(s.last_ts).toLocaleTimeString();
        return `<option value="${s.session_id}">${s.session_id} · ${s.n} events · ${when}</option>`;
      }).join("");
    if (keep) sel.value = keep;
  } catch (e) { /* ignore */ }
}

function startSpectate() {
  const sel = document.getElementById("spectate-select");
  const sid = sel && sel.value;
  if (!sid) return;
  if (!ws || ws.readyState !== 1) return;
  const rt = document.getElementById("response-text");
  if (rt) rt.innerHTML = "";
  lastMsgType = null;
  spectating = sid;
  spectateLastId = 0;
  for (const k of Object.keys(spectateCounts)) delete spectateCounts[k];
  _updateDevCounts();
  ws.send(JSON.stringify({ type: "spectate", session_id: sid, since_id: 0 }));
  const tb = document.getElementById("chat-textbox");
  if (tb) { tb.disabled = true; tb.placeholder = `SPECTATING ${sid} — input disabled`; }
  _setSpectateStatus(`watching ${sid}`, true);
}

function stopSpectate() {
  if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: "unspectate" }));
  spectating = null;
  spectateLastId = 0;
  const tb = document.getElementById("chat-textbox");
  if (tb) { tb.disabled = false; tb.placeholder = "Message... (Enter to send, Shift+Enter for newline)"; }
  _setSpectateStatus("idle", false);
}

window.startSpectate = startSpectate;
window.stopSpectate = stopSpectate;
window.refreshSpectateSessions = refreshSpectateSessions;

// ─── bot-panel:control ────────────────────────────────────────────────────
// All three control buttons + the mode-switch dropdown target whatever
// session is selected in #spectate-select. The server validates + routes via
// enqueue_session_control — these functions just build the WS frame.
//
// Offline (non-live) sessions can't be controlled; buttons disable themselves
// when _inventoryLive.has(target) is false.
//
// Modularization note: all DOM lookups go through the ids declared in the
// matching <section data-panel="control"> block. Keep those ids stable when
// moving this code.

const _inventoryLive = new Set();            // session_ids currently live
const _inventoryMeta = new Map();            // session_id -> {n, last_ts}
let _controlModes = ["DeetsCode", "dnd", "chess"];  // refreshed from /api/prompts

function _controlTarget() {
  const sel = document.getElementById("spectate-select");
  return (sel && sel.value) || "";
}

function _updateControlPanel() {
  const target = _controlTarget();
  const live = target && _inventoryLive.has(target);
  const label = document.getElementById("control-target");
  if (label) {
    label.textContent = target ? (live ? `→ ${target}` : `${target} (offline)`) : "no target";
    label.classList.toggle("live", !!live);
  }
  for (const btn of document.querySelectorAll('#bot-ops [data-panel="control"] .dev-btn')) {
    btn.disabled = !live;
  }
  const status = document.getElementById("control-status");
  if (status) {
    status.textContent = !target
      ? "pick a session above"
      : live ? "ready" : "session is offline (connect it first)";
  }
}

function onSpectateSelectChange() {
  _updateControlPanel();
}

function remoteControl(action) {
  const target = _controlTarget();
  if (!target || !ws || ws.readyState !== 1) return;
  ws.send(JSON.stringify({ type: "remote_control", target_session_id: target, action }));
}

function remoteSetPrompt() {
  const target = _controlTarget();
  const mode = document.getElementById("control-mode");
  const prompt = mode && mode.value;
  if (!target || !prompt || !ws || ws.readyState !== 1) return;
  ws.send(JSON.stringify({ type: "remote_control", target_session_id: target, action: "set_prompt", prompt }));
}

async function _refreshControlModes() {
  try {
    const r = await fetch("/api/prompts");
    const { prompts } = await r.json();
    if (Array.isArray(prompts) && prompts.length) _controlModes = prompts;
  } catch (e) { /* keep defaults */ }
  const sel = document.getElementById("control-mode");
  if (!sel) return;
  const keep = sel.value;
  sel.innerHTML = _controlModes.map(m => `<option value="${m}">${m}</option>`).join("");
  if (keep && _controlModes.includes(keep)) sel.value = keep;
}

// ─── bot-panel:inventory ──────────────────────────────────────────────────
// Table of every known session. Reuses /api/events/sessions (which now
// includes a `live` flag populated from list_live_sessions on the server).
// Row click pre-selects the session into #spectate-select so the control
// panel targets it.

async function refreshSessionInventory() {
  try {
    const r = await fetch("/api/events/sessions");
    const { sessions } = await r.json();
    _inventoryLive.clear();
    _inventoryMeta.clear();
    for (const s of sessions) {
      if (s.live) _inventoryLive.add(s.session_id);
      _inventoryMeta.set(s.session_id, { n: s.n || 0, last_ts: s.last_ts || 0 });
    }
    const tbody = document.getElementById("inv-tbody");
    if (!tbody) return;
    if (!sessions.length) {
      tbody.innerHTML = `<tr><td colspan="4" class="inv-empty">no sessions yet</td></tr>`;
    } else {
      const selected = _controlTarget();
      tbody.innerHTML = sessions.map(s => {
        const when = s.last_ts ? new Date(s.last_ts).toLocaleTimeString() : "—";
        const cls = s.live ? "live" : "off";
        const txt = s.live ? "live" : "offline";
        const sel = s.session_id === selected ? " class=\"selected\"" : "";
        return `<tr${sel} data-sid="${s.session_id}" onclick="_pickInventoryRow('${s.session_id}')">`
             + `<td>${s.session_id}</td><td>${s.n || 0}</td><td>${when}</td>`
             + `<td><span class="inv-state ${cls}">${txt}</span></td></tr>`;
      }).join("");
    }
    _updateControlPanel();
  } catch (e) { /* ignore */ }
}

function _pickInventoryRow(sid) {
  const sel = document.getElementById("spectate-select");
  if (!sel) return;
  // Ensure the option exists (inventory may know a session the picker doesn't).
  if (!Array.from(sel.options).some(o => o.value === sid)) {
    const opt = document.createElement("option");
    opt.value = sid; opt.textContent = sid;
    sel.appendChild(opt);
  }
  sel.value = sid;
  onSpectateSelectChange();
  refreshSessionInventory();  // re-render selected highlight
}

window.remoteControl = remoteControl;
window.remoteSetPrompt = remoteSetPrompt;
window.refreshSessionInventory = refreshSessionInventory;
window.onSpectateSelectChange = onSpectateSelectChange;
window._pickInventoryRow = _pickInventoryRow;

function connect() {
  ws = new WebSocket(`ws://${location.host}/ws`);
  window.__ws = ws;

  ws.onopen = () => { log("connected to harness", "info"); refreshTree(); fetchModels(); refreshTaskPanel(); fetchThemes(); refreshPendingPanel(); refreshSpectateSessions(); refreshSessionInventory(); _refreshControlModes(); _updateControlPanel(); };
  ws.onclose = () => {
    log("disconnected — retrying in 3s...", "info");
    setTimeout(connect, 3000);
  };
  ws.onerror = () => ws.close();

  ws.onmessage = (event) => {
    let msg = JSON.parse(event.data);
    if (window.__agentLog) window.__agentLog.push({t: Date.now(), ...msg});

    // Spectate control frames.
    if (msg.type === "spectate_ack") {
      spectateLastId = msg.last_id || 0;
      if (spectating) _setSpectateStatus(`watching ${spectating} @ ${spectateLastId}`, true);
      return;
    }
    // Spectated events — unwrap, count, filter, then re-dispatch.
    if (msg.type === "event" && msg.event) {
      if (msg.event.id && msg.event.id > spectateLastId) spectateLastId = msg.event.id;
      msg = msg.event.payload;
      if (!msg || !msg.type) return;
      spectateCounts[msg.type] = (spectateCounts[msg.type] || 0) + 1;
      _updateDevCounts();
      if (!_devFilterAllows(msg.type)) return;
    }

    switch (msg.type) {
      case "thinking":
        if (lastMsgType && lastMsgType !== "thinking") addDivider();
        appendResponse(msg.content, "thinking");
        lastMsgType = "thinking";
        break;

      case "text":
        if (lastMsgType && lastMsgType !== "text") addDivider();
        appendResponse(msg.content);
        lastMsgType = "text";
        break;

      case "tool_call":
        addToolEntry(msg.name, msg.args);
        if (msg.name === "read_file" && msg.args?.path) addContextFile(msg.args.path);
        lastMsgType = "tool_call";
        break;

      case "tool_result":
        updateToolResult(msg.content);
        lastMsgType = "tool_result";
        break;

      case "pending_writes":
        showPendingWrites(msg.writes);
        break;

      case "writes_applied":
        log(`Applied: ${msg.files.join(", ")}`, "info");
        hidePendingWrites();
        refreshTree();
        refreshTaskPanel();
        fetchThemes();
        break;

      case "writes_rejected":
        log("Writes rejected.", "info");
        hidePendingWrites();
        break;

      case "reset_complete":
        clearResponse();
        clearContextFiles();
        clearToolPanel();
        hidePendingWrites();
        busy = false;
        setInputEnabled(true);
        log("Conversation reset.", "info");
        break;

      case "info":
        log(msg.content, "info");
        if (msg.project) {
          // Project switch — refresh UI and clear stale state
          refreshTree();
          clearContextFiles();
          clearToolPanel();
          hidePendingWrites();
          setTitleFromPath(msg.project);
        }
        break;

      case "compacted":
        log(`Compacted ${msg.prior} messages → summary.`, "info");
        clearResponse();
        appendResponse(`[session compacted]\n\n${msg.summary}\n`, "compact");
        addDivider();
        lastMsgType = null;
        break;

      case "error":
        log(msg.content, "error");
        break;

      case "usage":
        updateContextBar(msg.total, msg.max);
        break;

      case "ctx_length":
        updateContextBar(0, msg.max);
        break;

      case "done":
        addDivider();
        busy = false;
        setInputEnabled(true);
        break;

      case "task_updated":
        refreshTaskPanel();
        break;
    }
    // Fan out to panel subscribers (harness.subscribe). Done after the
    // built-in switch so legacy handlers always run first.
    if (window.harness && window.harness._dispatch) {
      window.harness._dispatch(msg.type, msg);
    }
  };
}

// ── Send message ──────────────────────────────────
function sendMessage() {
  const box = document.getElementById("chat-textbox");
  if (!box) return;  // chat panel not mounted — sender can't be invoked
  const text = box.value.trim();
  if (!text || busy || !ws || ws.readyState !== WebSocket.OPEN) return;

  if (text.startsWith("/")) {
    handleSlash(text);
    box.value = "";
    return;
  }

  const keepHistory = localStorage.getItem("harness-keep-history") === "1";
  if (!keepHistory) {
    clearResponse();
    clearToolPanel();
  } else {
    addDivider();
  }
  lastMsgType = "user";
  appendResponse(`You: ${text}\n\n`, "user");
  ws.send(JSON.stringify({ type: "message", content: text }));

  box.value = "";
  busy = true;
  setInputEnabled(false);
  showThinking();
}

// ── Slash commands panel ─────────────────────────
const SLASH_DEFAULTS = [
  "/read <path> [N-M]         — read file, optional line range",
  "/search <pattern> [glob=X] [path=Y] — regex search",
  "/symbols <path>            — list defs/classes with line numbers",
  "/ls [path]                 — list a directory",
  "/tree                      — refresh the file tree panel",
  "/compact                   — summarize + trim conversation",
  "/help                      — this list",
].join("\n");

function renderSlashPanel() {
  const inner = document.getElementById("slash-inner");
  if (!inner) return;
  const raw = localStorage.getItem("harness-slash-commands") || SLASH_DEFAULTS;
  inner.innerHTML = "";
  for (const line of raw.split("\n")) {
    if (!line.trim()) continue;
    const div = document.createElement("div");
    div.className = "slash-cmd-row";
    const m = line.match(/^(.+?)\s*—\s*(.+)$/);
    if (m) {
      div.innerHTML = `<span class="slash-cmd-name">${escapeHtml(m[1].trim())}</span><span class="slash-cmd-sep"> — </span><span class="slash-cmd-desc">${escapeHtml(m[2].trim())}</span>`;
    } else {
      div.innerHTML = `<span class="slash-cmd-name">${escapeHtml(line)}</span>`;
    }
    inner.appendChild(div);
  }
}

function toggleSlashEdit() {
  const inner = document.getElementById("slash-inner");
  const editor = document.getElementById("slash-editor");
  const btn = document.getElementById("slash-edit-btn");
  if (editor.style.display !== "none") {
    localStorage.setItem("harness-slash-commands", editor.value);
    editor.style.display = "none";
    inner.style.display = "";
    btn.textContent = "✎";
    renderSlashPanel();
  } else {
    editor.value = localStorage.getItem("harness-slash-commands") || SLASH_DEFAULTS;
    inner.style.display = "none";
    editor.style.display = "";
    btn.textContent = "✓";
    editor.focus();
  }
}

// ── Slash commands ────────────────────────────────
const SLASH_HELP = [
  "/read <path> [N-M]         — read file, optional line range",
  "/search <pattern> [glob=X] [path=Y] — regex search",
  "/symbols <path>            — list defs/classes with line numbers",
  "/ls [path]                 — list a directory",
  "/tree                      — refresh the file tree panel",
  "/compact                   — summarize + trim conversation",
  "/help                      — this list",
].join("\n");

function handleSlash(text) {
  const parts = text.trim().slice(1).split(/\s+/);
  const cmd = (parts.shift() || "").toLowerCase();

  if (cmd === "help" || cmd === "?") {
    clearToolPanel();
    const entry = document.createElement("div");
    entry.className = "tool-entry";
    entry.innerHTML = `<div class="tool-name">/help</div><div class="tool-result">${escapeHtml(SLASH_HELP)}</div>`;
    document.getElementById("tool-panel-inner").appendChild(entry);
    return;
  }
  if (cmd === "tree") { refreshTree(); log("refreshed file tree", "info"); return; }
  if (cmd === "compact") { ws.send(JSON.stringify({ type: "compact" })); return; }

  if (cmd === "read") {
    if (!parts[0]) { log("usage: /read <path> [N-M]", "error"); return; }
    const args = { path: parts[0] };
    if (parts[1] && /^\d+-\d+$/.test(parts[1])) {
      const [s, e] = parts[1].split("-").map(Number);
      args.start_line = s; args.end_line = e;
    }
    ws.send(JSON.stringify({ type: "slash", tool: "read_file", args }));
    return;
  }

  if (cmd === "search") {
    if (!parts.length) { log("usage: /search <pattern> [glob=*.py] [path=src]", "error"); return; }
    const args = {};
    const patternParts = [];
    for (const tok of parts) {
      const m = tok.match(/^(glob|path)=(.+)$/);
      if (m) args[m[1]] = m[2];
      else patternParts.push(tok);
    }
    args.pattern = patternParts.join(" ");
    if (!args.pattern) { log("usage: /search <pattern> [glob=X] [path=Y]", "error"); return; }
    ws.send(JSON.stringify({ type: "slash", tool: "search", args }));
    return;
  }

  if (cmd === "symbols") {
    if (!parts[0]) { log("usage: /symbols <path>", "error"); return; }
    ws.send(JSON.stringify({ type: "slash", tool: "list_symbols", args: { path: parts[0] } }));
    return;
  }

  if (cmd === "ls") {
    ws.send(JSON.stringify({ type: "slash", tool: "list_dir", args: { path: parts[0] || "." } }));
    return;
  }

  log(`unknown slash: /${cmd} (try /help)`, "error");
}

// ── Cancel / Reset ────────────────────────────────
function cancelRun() {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  if (!busy) return;
  ws.send(JSON.stringify({ type: "cancel" }));
}

function resetConversation() {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  ws.send(JSON.stringify({ type: "reset" }));
}

function compactConversation() {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  ws.send(JSON.stringify({ type: "compact" }));
}

// ── Directory ─────────────────────────────────────
function setDir() {
  const input = document.getElementById("dir-input");
  if (!input) return;
  const path = input.value.trim();
  if (!path || !ws || ws.readyState !== WebSocket.OPEN) return;
  ws.send(JSON.stringify({ type: "set_dir", path }));
}

// ── Thinking indicator ────────────────────────────
function showThinking() {
  const box = document.getElementById("response-text");
  if (!box) return;  // chat panel not mounted yet — skip silently
  const pill = document.createElement("span");
  pill.id = "thinking-indicator";
  pill.className = "thinking-pill";
  pill.innerHTML = `
    <span>thinking</span>
    <span class="pill-dots"><span></span><span></span><span></span></span>
  `;
  box.appendChild(pill);
  box.parentElement.scrollTop = box.parentElement.scrollHeight;
}

function hideThinking() {
  const el = document.getElementById("thinking-indicator");
  if (el) el.remove();
}

// ── Response box helpers ──────────────────────────
// The currently-streaming assistant message div. Normal text chunks append
// into its buffer and the whole thing is re-rendered as markdown on each
// delta — avoids per-chunk parsing of half-closed code fences / lists.
let currentAssistantMsg = null;

function renderMarkdown(text) {
  if (typeof marked === "undefined" || typeof DOMPurify === "undefined") {
    return escapeHtml(text);
  }
  return DOMPurify.sanitize(marked.parse(text, { breaks: true, gfm: true }));
}

function appendResponse(text, type = "normal") {
  hideThinking();
  const box = document.getElementById("response-text");
  if (!box) {
    // Chat panel not mounted yet (boot race). Buffer; chat's inline script
    // calls _flushChatBootBuffer once #response-text exists.
    _chatBootBuffer.push({ text, type });
    return;
  }

  if (type === "normal") {
    if (!currentAssistantMsg) {
      currentAssistantMsg = document.createElement("div");
      currentAssistantMsg.className = "assistant-md";
      currentAssistantMsg._buffer = "";
      box.appendChild(currentAssistantMsg);
    }
    currentAssistantMsg._buffer += text;
    currentAssistantMsg.innerHTML = renderMarkdown(currentAssistantMsg._buffer);
    box.parentElement.scrollTop = box.parentElement.scrollHeight;
    return;
  }

  currentAssistantMsg = null;
  const span = document.createElement("span");
  span.textContent = text;
  if (type === "thinking") { span.style.opacity = "0.35"; span.style.fontStyle = "italic"; }
  else if (type === "tool")  span.style.opacity = "0.55";
  else if (type === "user")  span.style.fontWeight = "bold";
  else if (type === "info")  span.style.opacity = "0.6";
  else if (type === "error") span.style.color = "#c0392b";
  else if (type === "compact") { span.style.opacity = "0.7"; span.style.fontStyle = "italic"; }
  box.appendChild(span);
  box.parentElement.scrollTop = box.parentElement.scrollHeight;
}

function addDivider() {
  const box = document.getElementById("response-text");
  if (!box || !box.hasChildNodes()) return;
  currentAssistantMsg = null;
  const hr = document.createElement("hr");
  hr.className = "response-divider";
  box.appendChild(hr);
}

function clearResponse() {
  currentAssistantMsg = null;
  const box = document.getElementById("response-text");
  if (!box) return;
  box.innerHTML = "";
}

function log(text, type) {
  appendResponse(`${text}\n`, type);
}

// ── Input state ───────────────────────────────────
function setInputEnabled(enabled) {
  const box = document.getElementById("chat-textbox");
  if (!box) {
    // Chat panel not mounted yet — remember intent so the panel can
    // apply it on mount via _applyPendingChatState.
    _pendingChatInputEnabled = enabled;
    return;
  }
  box.disabled = !enabled;
  box.style.opacity = enabled ? "1" : "0.5";
  const stop = document.getElementById("stop-btn");
  if (stop) stop.disabled = enabled;
}

// ── Pending writes panel ──────────────────────────
function showPendingWrites(writes) {
  const files = Object.keys(writes);
  const inner = document.getElementById("pending-panel-inner");
  const count = document.getElementById("pending-count");
  if (!inner || !count) return;
  count.textContent = String(files.length);
  if (files.length === 0) {
    inner.innerHTML = `<span class="pending-empty">queue is empty</span>`;
    return;
  }
  inner.innerHTML = files.map(f => {
    const bytes = writes[f] ? writes[f].length : 0;
    return `<div class="pending-row"><span class="pending-path">${escapeHtml(f)}</span><span class="pending-bytes">${bytes.toLocaleString()} B</span></div>`;
  }).join("");
}

// ── Context files ─────────────────────────────────
const contextFiles = new Set();

function addContextFile(path) {
  if (contextFiles.has(path)) return;
  contextFiles.add(path);
  // Trigger the panel to refresh from server-side tools.read_files. Direct DOM
  // poke retained for backward compat with anyone querying #context-files
  // before the new panel hydrates.
  const container = document.getElementById("context-files");
  if (!container) return;
  const row = document.createElement("div");
  row.className = "file-node file";
  row.textContent = "  " + path;
  container.appendChild(row);
}

function updateContextBar(total, max) {
  const pct = Math.min(100, Math.round((total / max) * 100));
  const pctEl = document.getElementById("ctx-pct");
  const barEl = document.getElementById("ctx-bar");
  const tokEl = document.getElementById("ctx-tokens");
  if (pctEl) pctEl.textContent = pct + "%";
  if (barEl) barEl.style.width = pct + "%";
  if (tokEl) tokEl.textContent = `~${total.toLocaleString()} / ${max.toLocaleString()}`;
}

function clearContextFiles() {
  contextFiles.clear();
  const el = document.getElementById("context-files");
  if (el) el.innerHTML = "";
}

// ── Tool panel ────────────────────────────────────
let currentToolEntry = null;

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

function renderEditArgs(args) {
  const path = escapeHtml(args.path ?? "");
  const oldS = escapeHtml(args.old_string ?? "");
  const newS = escapeHtml(args.new_string ?? "");
  return `
    <span class="tool-args">${path}</span>
    <div class="tool-diff">
      <div class="diff-row diff-old"><span class="diff-marker">-</span><span>${oldS}</span></div>
      <div class="diff-row diff-new"><span class="diff-marker">+</span><span>${newS}</span></div>
    </div>
  `;
}

function renderToolArgs(name, args) {
  if (!args) return "";
  switch (name) {
    case "edit_file":
      return renderEditArgs(args);
    case "read_file": {
      let s = escapeHtml(args.path ?? "");
      if (args.start_line || args.end_line) s += `:${args.start_line ?? ""}–${args.end_line ?? ""}`;
      return `<span class="tool-args">${s}</span>`;
    }
    case "write_file":
      return `<span class="tool-args">${escapeHtml(args.path ?? "")}</span>`;
    case "search": {
      let s = escapeHtml(args.pattern ?? "");
      if (args.glob) s += `  <span style="opacity:0.45">${escapeHtml(args.glob)}</span>`;
      if (args.path) s += `  <span style="opacity:0.45">in ${escapeHtml(args.path)}</span>`;
      return `<span class="tool-args">${s}</span>`;
    }
    case "list_symbols":
    case "list_dir":
      return `<span class="tool-args">${escapeHtml(args.path ?? "")}</span>`;
    case "run_command":
      return `<span class="tool-args">${escapeHtml(args.command ?? "")}</span>`;
    case "update_task": {
      const content = (args.content ?? "").trim();
      if (!content) return `<span class="tool-args" style="opacity:0.4">(read)</span>`;
      return `<pre class="tool-args tool-args-task">${escapeHtml(content)}</pre>`;
    }
    case "list_context_files":
      return "";
    default: {
      const keys = Object.keys(args);
      if (keys.length === 0) return "";
      const summary = keys.map(k => {
        const v = String(args[k]);
        return `<span style="opacity:0.5">${escapeHtml(k)}</span> ${escapeHtml(v.length > 60 ? v.slice(0, 60) + "…" : v)}`;
      }).join("  ");
      return `<span class="tool-args">${summary}</span>`;
    }
  }
}

function addToolEntry(name, args) {
  const inner = document.getElementById("tool-panel-inner");
  if (!inner) return;

  const entry = document.createElement("div");
  entry.className = "tool-entry";
  entry.innerHTML = `
    <span class="tool-name">${escapeHtml(name)}</span>
    ${renderToolArgs(name, args)}
  `;
  inner.appendChild(entry);
  inner.scrollTop = inner.scrollHeight;
  currentToolEntry = entry;
}

function updateToolResult(content) {
  if (!currentToolEntry) return;
  const result = document.createElement("span");
  result.className = "tool-result";
  if (typeof content === "string" && content.startsWith("Error")) {
    result.classList.add("error");
  }
  result.textContent = content.slice(0, 400) + (content.length > 400 ? "…" : "");
  currentToolEntry.appendChild(result);
  const inner = document.getElementById("tool-panel-inner");
  if (inner) inner.scrollTop = 999999;
}

function clearToolPanel() {
  const inner = document.getElementById("tool-panel-inner");
  if (inner) inner.innerHTML = "";
  const wrap = document.getElementById("tool-panel");
  if (wrap) wrap.classList.remove("visible");
  currentToolEntry = null;
}

function hidePendingWrites() {
  showPendingWrites({});
}

function applyWrites() {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  ws.send(JSON.stringify({ type: "apply_writes" }));
}

function rejectWrites() {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  ws.send(JSON.stringify({ type: "reject_writes" }));
}

async function flushPending() {
  try {
    const res = await fetch("/pending", { method: "DELETE" });
    const data = await res.json();
    log(`Flushed ${data.flushed ?? 0} pending write(s).`, "info");
  } catch (e) {
    log("Flush failed: " + (e?.message || e), "error");
  }
  hidePendingWrites();
}

async function refreshPendingPanel() {
  try {
    const res = await fetch("/pending");
    const data = await res.json();
    showPendingWrites(data.writes || {});
  } catch (e) { /* server not up yet */ }
}

// ── Boot ──────────────────────────────────────────
// Enter-to-send is bound inside panels/chat/view.html now — that script
// runs after panel-shell injects #chat-textbox, so the listener attaches
// to a node that actually exists. Escape-to-cancel is global and stays
// here.
document.addEventListener("DOMContentLoaded", () => {
  loadTheme();
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && busy) {
      e.preventDefault();
      cancelRun();
    }
  });

  bindSettingsControls();

  renderSlashPanel();
  connect();
  initBlogPanels();
});


// ─── Blog mode panels ─────────────────────────────────────────────────────
//
// Self-contained module for the DeetsOTD blog authoring hub. Visible only
// when the active prompt is "blog" — the outer <section id="blog-ops"> gets
// its `.mode-hidden` toggled by `applyModeVisibility`.
//
// State lives in this scope only (currentMode, currentDraft, lastSongHits).
// Server contract: every action sends a `blog_*` WS message and the response
// arrives as a `blog_*` WS event handled in the dispatcher below.
// ──────────────────────────────────────────────────────────────────────────

let currentMode = "DeetsCode";
let currentDraft = null;     // the post object loaded into the editor, or a kind sentinel for new posts
let lastSongHits = [];

function isBlogMode() { return currentMode === "blog"; }

// Per-mode panel visibility. Each entry is { hideInModes: [...] }.
// Modularization pays off here — every panel is a self-contained <section>
// keyed by an id, so toggling .mode-hidden hides it without touching layout.
//
// The blog view strips out panels that aren't relevant to blogging:
//   - file tree (no project files in blog mode)
//   - status (in-context files + task.md — neither applies)
//   - bot-ops (Discord remote control)
// Keeps the activity panel (tool calls / pending writes from the model when
// summoned) and settings (model + mode picker).
// Per-instance mode-visibility. Looks up by [data-instance="..."] which
// every panel-shell instance wrapper sets.
const _PANEL_HIDE_RULES = [
  { instance: "blog_ops",         showOnlyIn: ["blog"] },
  { instance: "bot_ops",          hideIn:     ["blog"] },
  { instance: "in_context_files", hideIn:     ["blog"] },
  { instance: "task_list",        hideIn:     ["blog"] },
  { instance: "files",            hideIn:     ["blog"] },
];

function applyModeVisibility() {
  for (const r of _PANEL_HIDE_RULES) {
    const el = document.querySelector(`[data-instance="${r.instance}"]`);
    if (!el) continue;
    let hide;
    if (r.showOnlyIn) hide = !r.showOnlyIn.includes(currentMode);
    else if (r.hideIn) hide = r.hideIn.includes(currentMode);
    else hide = false;
    el.classList.toggle("mode-hidden", hide);
  }
  // Let the panel-shell apply layout-config mode_overrides too (region
  // widths/visibility declared in panel_layout.json).
  if (typeof window.harnessApplyLayoutMode === "function") {
    window.harnessApplyLayoutMode(currentMode);
  }
}

async function loadPromptModes() {
  const sel = document.getElementById("prompt-select");
  if (!sel) return;
  try {
    const r = await fetch("/api/prompts");
    const { prompts } = await r.json();
    sel.innerHTML = "";
    for (const name of prompts) {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name;
      sel.appendChild(opt);
    }
    const saved = localStorage.getItem("harness-mode") || "DeetsCode";
    if (prompts.includes(saved)) sel.value = saved;
    currentMode = sel.value;
    applyModeVisibility();
    // Sync the persisted mode to the server. The server defaults to DeetsCode
    // on each new WS connection; without this, a sticky "blog" choice in
    // localStorage would only update the UI while the server-side prompt
    // and tool pack stayed wrong until the user re-touched the dropdown.
    syncModeToServer();
    if (isBlogMode()) {
      refreshBlogDrafts();
      refreshBlogPassphrase();
    }
  } catch (e) { console.error("loadPromptModes:", e); }

  sel.addEventListener("change", () => {
    currentMode = sel.value;
    localStorage.setItem("harness-mode", currentMode);
    syncModeToServer();
    applyModeVisibility();
    if (isBlogMode()) {
      refreshBlogDrafts();
      refreshBlogComments();
      refreshBlogPreview();
      refreshBlogPassphrase();
    }
  });
}

// Push the current mode to the server. Retries until the WS opens — handles
// the page-load race where loadPromptModes() finishes before connect() does.
function syncModeToServer() {
  if (!currentMode) return;
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "set_prompt", prompt: currentMode }));
    return;
  }
  setTimeout(syncModeToServer, 200);
}

// ── WS dispatcher for blog_* events ────────────────────────────────────────
function handleBlogMessage(data) {
  switch (data.type) {
    case "blog_posts":           renderBlogList(data.posts); break;
    case "blog_post":            loadDraftIntoEditor(data.post); break;
    case "blog_post_saved":      onBlogPostSaved(data.post); break;
    case "blog_post_deleted":    onBlogPostDeleted(data.slug); break;
    case "blog_song_results":    renderSongResults(data.results || []); break;
    case "blog_movie_results":   renderMovieResults(data.results || []); break;
    case "blog_comments":        renderBlogComments(data.comments || []); break;
    case "blog_comment_deleted": refreshBlogComments(); break;
    case "blog_preview_url":     setPreviewUrl(data.url); break;
    case "blog_passphrase":      onBlogPassphrase(data); break;
    case "blog_error":           console.warn("blog error:", data); alertBlog(`${data.op}: ${data.error}`); break;
  }
}

function alertBlog(msg) {
  const box = document.getElementById("blog-editor-status");
  if (box) box.textContent = msg;
}

function blogSend(msg) {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  ws.send(JSON.stringify(msg));
}

// ── Drafts list ────────────────────────────────────────────────────────────
function refreshBlogDrafts() {
  const kind   = (document.getElementById("blog-filter-kind")?.value) || "";
  const status = (document.getElementById("blog-filter-status")?.value) || "";
  const payload = { type: "blog_list_posts" };
  if (kind)   payload.kind = kind;
  if (status) payload.status = status;
  blogSend(payload);
}

function renderBlogList(posts) {
  const el = document.getElementById("blog-list");
  if (!el) return;
  if (!posts || !posts.length) {
    el.innerHTML = '<div class="blog-empty">no posts</div>';
    return;
  }
  el.innerHTML = "";
  for (const p of posts) {
    const row = document.createElement("div");
    row.className = "blog-row";
    row.dataset.slug = p.slug;
    row.innerHTML = `
      <span class="blog-kind blog-kind-${p.kind}">${p.kind}</span>
      <span class="blog-row-title">${escapeHtml(p.title)}</span>
      <span class="blog-row-meta">${p.date}</span>
      <span class="blog-status blog-status-${p.status}">${p.status}</span>
    `;
    row.addEventListener("click", () => loadDraftIntoEditor(p));
    el.appendChild(row);
  }
}

// ── Editor ─────────────────────────────────────────────────────────────────
function openBlogEditor(kind) {
  currentDraft = { kind, slug: null, title: "", date: today(), meta: {}, body_md: "", locked: false, status: "draft" };
  renderEditor();
  const status = document.getElementById("blog-editor-status");
  if (status) status.textContent = `new ${kind}`;
}

function loadDraftIntoEditor(p) {
  if (!p) return;
  currentDraft = JSON.parse(JSON.stringify(p));
  renderEditor();
  const status = document.getElementById("blog-editor-status");
  if (status) status.textContent = `${p.status}: ${p.slug}`;
}

function today() {
  const d = new Date();
  return d.toISOString().slice(0, 10);
}

function renderEditor() {
  const el = document.getElementById("blog-editor");
  const titleEl = document.getElementById("blog-editor-title");
  if (!el) return;
  if (!currentDraft) {
    el.innerHTML = '<div class="blog-empty">click + song / + movie / + journal above, or pick a draft from the list.</div>';
    return;
  }
  const k = currentDraft.kind;
  if (titleEl) titleEl.textContent = `${k} editor`;
  const m = currentDraft.meta || {};
  let kindFields = "";
  if (k === "song") {
    kindFields = `
      <label class="blog-field"><span>artist</span>
        <input type="text" data-meta="artist" value="${escapeAttr(m.artist || "")}" /></label>
      <label class="blog-field"><span>album</span>
        <input type="text" data-meta="album" value="${escapeAttr(m.album || "")}" /></label>
      <label class="blog-field"><span>genre</span>
        <input type="text" data-meta="genre" value="${escapeAttr(m.genre || "")}" /></label>
      <label class="blog-field"><span>length (sec)</span>
        <input type="number" data-meta="length_seconds" value="${m.length_seconds || ""}" /></label>
      <label class="blog-field"><span>iTunes URL</span>
        <input type="url" data-meta="itunes_url" value="${escapeAttr(m.itunes_url || "")}" /></label>
      <label class="blog-field"><span>art URL</span>
        <input type="url" data-meta="art_url" value="${escapeAttr(m.art_url || "")}" /></label>
    `;
  } else if (k === "movie") {
    kindFields = `
      <label class="blog-field"><span>director</span>
        <input type="text" data-meta="director" value="${escapeAttr(m.director || "")}" /></label>
      <label class="blog-field"><span>year</span>
        <input type="number" data-meta="year" value="${m.year || ""}" /></label>
      <label class="blog-field"><span>length (min)</span>
        <input type="number" data-meta="length_minutes" value="${m.length_minutes || ""}" /></label>
      <label class="blog-field"><span>genre</span>
        <input type="text" data-meta="genre" value="${escapeAttr(m.genre || "")}" /></label>
      <label class="blog-field"><span>rating (0-10, .5 steps)</span>
        <input type="number" step="0.5" min="0" max="10" data-meta="rating" value="${m.rating ?? ""}" /></label>
      <label class="blog-field"><span>poster URL</span>
        <input type="url" data-meta="poster_url" value="${escapeAttr(m.poster_url || "")}" /></label>
      <label class="blog-field"><span>TMDB URL</span>
        <input type="url" data-meta="tmdb_url" value="${escapeAttr(m.tmdb_url || "")}" /></label>
    `;
  }
  // Lock is now site-wide: any kind can be passphrase-gated. The passphrase
  // itself lives in BLOG_DIR/.env (JOURNAL_PASSPHRASE) — manage it from the
  // [blog-subpanel:passphrase] section.
  const showLock = true;
  const showBody = (k !== "song");
  const showMedia = true;

  el.innerHTML = `
    <label class="blog-field"><span>title</span>
      <input id="blog-edit-title" type="text" value="${escapeAttr(currentDraft.title || "")}" /></label>
    <label class="blog-field"><span>date</span>
      <input id="blog-edit-date" type="date" value="${escapeAttr(currentDraft.date || today())}" /></label>
    ${kindFields}
    ${showBody ? `
      <label class="blog-field blog-field-wide"><span>body (markdown)</span>
        <textarea id="blog-edit-body" rows="6">${escapeHtml(currentDraft.body_md || "")}</textarea></label>
    ` : ""}
    ${showLock ? `
      <label class="blog-field blog-field-inline">
        <input id="blog-edit-locked" type="checkbox" ${currentDraft.locked ? "checked" : ""} />
        <span>locked (passphrase-gated)</span>
      </label>
    ` : ""}
    ${showMedia ? `
      <div class="blog-field"><span>media</span>
        <div class="blog-drop" id="blog-drop"
             ondragover="event.preventDefault();this.classList.add('blog-drop-hot')"
             ondragleave="this.classList.remove('blog-drop-hot')"
             ondrop="onBlogDrop(event)"
             onclick="document.getElementById('blog-drop-input').click()">
          <span class="blog-drop-label">drop image here, or click to choose</span>
          <input id="blog-drop-input" type="file" accept="image/*" hidden
                 onchange="onBlogFilePicked(event)" />
        </div>
        <span class="blog-field-hint" id="blog-drop-hint">${currentDraft.media_path ? `current: ${escapeHtml(currentDraft.media_path)}` : "none attached"}</span>
      </div>
    ` : ""}
    <div class="dev-row dev-row-btns">
      <button class="dev-btn" onclick="saveBlogDraft()">save draft</button>
      ${currentDraft.slug ? `
        ${currentDraft.status === "published"
          ? `<button class="dev-btn" onclick="unpublishBlogDraft()">unpublish</button>`
          : `<button class="dev-btn" onclick="publishBlogDraft()">publish</button>`}
        <button class="dev-btn" onclick="deleteBlogDraft()">delete</button>
      ` : ""}
    </div>
  `;
}

function readEditorState() {
  const titleEl = document.getElementById("blog-edit-title");
  const dateEl  = document.getElementById("blog-edit-date");
  const bodyEl  = document.getElementById("blog-edit-body");
  const lockEl  = document.getElementById("blog-edit-locked");

  const meta = {};
  document.querySelectorAll("#blog-editor [data-meta]").forEach(inp => {
    const key = inp.dataset.meta;
    let val = inp.value;
    if (val === "") return;
    if (inp.type === "number") val = Number(val);
    meta[key] = val;
  });

  return {
    title: titleEl?.value || "",
    date:  dateEl?.value  || today(),
    body_md: bodyEl ? bodyEl.value : null,
    locked: !!(lockEl && lockEl.checked),
    meta,
  };
}

function saveBlogDraft() {
  if (!currentDraft) return;
  const state = readEditorState();
  if (!state.title.trim()) { alertBlog("title required"); return; }
  if (currentDraft.slug) {
    blogSend({
      type: "blog_update_post",
      slug: currentDraft.slug,
      title: state.title,
      date:  state.date,
      body_md: state.body_md,
      locked: state.locked,
      meta: state.meta,
    });
  } else {
    blogSend({
      type: "blog_create_post",
      kind: currentDraft.kind,
      title: state.title,
      date:  state.date,
      body_md: state.body_md,
      locked: state.locked,
      meta: state.meta,
    });
  }

  // pendingMediaBlob (set by drag-drop / file picker) is attached after save.
}

let pendingMediaBlob = null;  // { filename, content_b64 }

function onBlogDrop(e) {
  e.preventDefault();
  const el = document.getElementById("blog-drop");
  if (el) el.classList.remove("blog-drop-hot");
  const f = e.dataTransfer?.files?.[0];
  if (f) stageBlogMedia(f);
}

function onBlogFilePicked(e) {
  const f = e.target.files?.[0];
  if (f) stageBlogMedia(f);
}

function stageBlogMedia(file) {
  if (!file.type.startsWith("image/")) {
    alertBlog("not an image file");
    return;
  }
  const reader = new FileReader();
  reader.onload = () => {
    // result is "data:<mime>;base64,<b64>"
    const b64 = String(reader.result).split(",", 2)[1] || "";
    pendingMediaBlob = { filename: file.name, content_b64: b64 };
    const hint = document.getElementById("blog-drop-hint");
    if (hint) hint.textContent = `staged: ${file.name} (${Math.round(file.size / 1024)} KB) — saves on next 'save draft'`;
    // If there's already a saved draft, attach immediately.
    if (currentDraft?.slug) flushPendingMedia(currentDraft.slug);
  };
  reader.readAsDataURL(file);
}

function flushPendingMedia(slug) {
  if (!pendingMediaBlob) return false;
  blogSend({ type: "blog_attach_media_blob", slug, ...pendingMediaBlob });
  pendingMediaBlob = null;
  return true;
}

function publishBlogDraft() {
  if (!currentDraft?.slug) return;
  blogSend({ type: "blog_publish", slug: currentDraft.slug });
}
function unpublishBlogDraft() {
  if (!currentDraft?.slug) return;
  blogSend({ type: "blog_unpublish", slug: currentDraft.slug });
}
function deleteBlogDraft() {
  if (!currentDraft?.slug) return;
  if (!confirm(`Delete "${currentDraft.title}" and its comments?`)) return;
  blogSend({ type: "blog_delete_post", slug: currentDraft.slug });
}

function onBlogPostSaved(p) {
  currentDraft = p;
  if (flushPendingMedia(p.slug)) {
    return;  // wait for next blog_post_saved with media_path set
  }
  renderEditor();
  const status = document.getElementById("blog-editor-status");
  if (status) status.textContent = `saved: ${p.slug} [${p.status}]`;
  refreshBlogDrafts();
}

function onBlogPostDeleted(slug) {
  if (currentDraft?.slug === slug) {
    currentDraft = null;
    renderEditor();
  }
  refreshBlogDrafts();
}

// ── Song lookup ────────────────────────────────────────────────────────────
function blogLookupSong() {
  const q = document.getElementById("blog-song-query")?.value.trim();
  if (!q) return;
  blogSend({ type: "blog_lookup_song", query: q, limit: 10 });
}

function renderSongResults(results) {
  lastSongHits = results;
  const el = document.getElementById("blog-song-results");
  if (!el) return;
  if (!results.length) {
    el.innerHTML = '<div class="blog-empty">no matches</div>';
    return;
  }
  el.innerHTML = "";
  results.forEach((r, i) => {
    const secs = r.length_ms ? Math.round(r.length_ms / 1000) : null;
    const len = secs ? `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, "0")}` : "";
    const row = document.createElement("div");
    row.className = "blog-song-hit";
    row.innerHTML = `
      ${r.art_url ? `<img class="blog-song-art" src="${escapeAttr(r.art_url)}" alt="" loading="lazy" />` : `<div class="blog-song-art blog-song-art-placeholder"></div>`}
      <div class="blog-song-meta">
        <div class="blog-song-title">${escapeHtml(r.title || "")}</div>
        <div class="blog-song-sub">${escapeHtml(r.artist || "")} · ${escapeHtml(r.album || "")} · ${escapeHtml(r.genre || "")}${len ? ` · ${len}` : ""}</div>
      </div>
      <button class="dev-btn" onclick="applyBlogSongHit(${i})">use</button>
    `;
    el.appendChild(row);
  });
}

function applyBlogSongHit(i) {
  const hit = lastSongHits[i];
  if (!hit) return;
  // If editor is empty / not a song draft, start a new song draft.
  if (!currentDraft || currentDraft.kind !== "song") {
    openBlogEditor("song");
  }
  const seconds = hit.length_ms ? Math.round(hit.length_ms / 1000) : null;
  currentDraft.title = hit.title || currentDraft.title;
  currentDraft.meta = {
    ...(currentDraft.meta || {}),
    artist:         hit.artist,
    album:          hit.album,
    genre:          hit.genre,
    length_seconds: seconds,
    itunes_url:     hit.itunes_url,
    art_url:        hit.art_url,
  };
  renderEditor();
}

// ── Movie lookup (TMDB) ───────────────────────────────────────────────────
let lastMovieHits = [];

function blogLookupMovie() {
  const q = document.getElementById("blog-movie-query")?.value.trim();
  if (!q) return;
  blogSend({ type: "blog_lookup_movie", query: q, limit: 5 });
}

function renderMovieResults(results) {
  lastMovieHits = results;
  const el = document.getElementById("blog-movie-results");
  if (!el) return;
  if (!results.length) {
    el.innerHTML = '<div class="blog-empty">no matches</div>';
    return;
  }
  el.innerHTML = "";
  results.forEach((r, i) => {
    const yr = r.year ? ` (${r.year})` : "";
    const rt = r.length_minutes ? ` · ${r.length_minutes} min` : "";
    const row = document.createElement("div");
    row.className = "blog-song-hit";
    row.innerHTML = `
      ${r.poster_url ? `<img class="blog-song-art" src="${escapeAttr(r.poster_url)}" alt="" loading="lazy" />` : `<div class="blog-song-art blog-song-art-placeholder"></div>`}
      <div class="blog-song-meta">
        <div class="blog-song-title">${escapeHtml(r.title || "")}${escapeHtml(yr)}</div>
        <div class="blog-song-sub">dir. ${escapeHtml(r.director || "?")} · ${escapeHtml(r.genre || "")}${escapeHtml(rt)}</div>
      </div>
      <button class="dev-btn" onclick="applyBlogMovieHit(${i})">use</button>
    `;
    el.appendChild(row);
  });
}

function applyBlogMovieHit(i) {
  const hit = lastMovieHits[i];
  if (!hit) return;
  if (!currentDraft || currentDraft.kind !== "movie") {
    openBlogEditor("movie");
  }
  currentDraft.title = hit.title || currentDraft.title;
  currentDraft.meta = {
    ...(currentDraft.meta || {}),
    director:       hit.director,
    year:           hit.year,
    length_minutes: hit.length_minutes,
    genre:          hit.genre,
    poster_url:     hit.poster_url,
    tmdb_url:       hit.tmdb_url,
  };
  renderEditor();
}

// ── Passphrase (site-wide lock for any kind) ───────────────────────────────
let _blogPassRevealed = false;

function refreshBlogPassphrase() {
  blogSend({ type: "blog_get_passphrase" });
}

function saveBlogPassphrase() {
  const inp = document.getElementById("blog-pass-input");
  if (!inp) return;
  const v = inp.value;
  blogSend({ type: "blog_set_passphrase", value: v });
}

function toggleBlogPassReveal() {
  const inp = document.getElementById("blog-pass-input");
  const btn = document.getElementById("blog-pass-toggle");
  if (!inp || !btn) return;
  _blogPassRevealed = !_blogPassRevealed;
  inp.type = _blogPassRevealed ? "text" : "password";
  btn.textContent = _blogPassRevealed ? "hide" : "show";
}

function onBlogPassphrase(data) {
  const inp = document.getElementById("blog-pass-input");
  if (inp) inp.value = data.value || "";
  const status = document.getElementById("blog-pass-status");
  if (status) status.textContent = data.saved ? "saved · live now" : "";
}

// ── Comments inbox ─────────────────────────────────────────────────────────
function refreshBlogComments() {
  blogSend({ type: "blog_list_comments", limit: 100 });
}

function renderBlogComments(comments) {
  const el = document.getElementById("blog-comments");
  if (!el) return;
  if (!comments.length) {
    el.innerHTML = '<div class="blog-empty">no comments yet</div>';
    return;
  }
  el.innerHTML = "";
  for (const c of comments) {
    const row = document.createElement("div");
    row.className = "blog-comment-row";
    row.innerHTML = `
      <div class="blog-comment-head">
        <span class="blog-comment-author">${escapeHtml(c.author)}</span>
        <span class="blog-comment-time">${escapeHtml(c.created_at)}</span>
      </div>
      <div class="blog-comment-body">${escapeHtml(c.body)}</div>
      <div class="blog-comment-foot">
        <span class="blog-comment-post">post: ${c.post_id.slice(0, 8)}</span>
        <button class="dev-btn" onclick="deleteBlogComment('${c.id}')">delete</button>
      </div>
    `;
    el.appendChild(row);
  }
}

function deleteBlogComment(id) {
  if (!confirm("delete comment?")) return;
  blogSend({ type: "blog_delete_comment", comment_id: id });
}

// ── Preview iframe ─────────────────────────────────────────────────────────
function refreshBlogPreview() {
  const u = document.getElementById("blog-preview-url")?.value || "http://localhost:8080";
  setPreviewUrl(u);
}
function setPreviewUrl(url) {
  const frame = document.getElementById("blog-preview-frame");
  const open  = document.getElementById("blog-preview-open");
  const inp   = document.getElementById("blog-preview-url");
  if (frame) frame.src = url;
  if (open)  open.href = url;
  if (inp)   inp.value = url;
}

// ── Helpers ────────────────────────────────────────────────────────────────
function escapeAttr(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;").replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function initBlogPanels() {
  loadPromptModes();
  // The connect()'s ws.onmessage already routes events; we hook in by
  // wrapping the existing handler so we don't have to edit it inline.
  const origConnect = window.connect;
  // No-op: handleBlogMessage is invoked from the patched ws.onmessage
  // below, set up after the WS connects.
  const tryAttach = () => {
    if (!ws) return setTimeout(tryAttach, 200);
    const prev = ws.onmessage;
    ws.onmessage = (event) => {
      if (typeof prev === "function") prev(event);
      try {
        const data = JSON.parse(event.data);
        if (data && typeof data.type === "string" && data.type.startsWith("blog_")) {
          handleBlogMessage(data);
        }
      } catch (e) { /* ignore */ }
    };
  };
  tryAttach();
}

