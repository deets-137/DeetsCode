// ── File tree ─────────────────────────────────────
async function refreshTree() {
  try {
    const res = await fetch("/tree");
    const data = await res.json();
    const container = document.getElementById("file-tree");
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

document.addEventListener("DOMContentLoaded", () => {
  const modelSelect = document.getElementById("model-select");
  if (modelSelect) {
    modelSelect.addEventListener("change", (e) => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "set_model", model: e.target.value }));
      }
    });
  }
});

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

// ── Knowledge packs ───────────────────────────────
const activePacks = new Set();

async function refreshPacks() {
  try {
    const res = await fetch("/packs");
    const data = await res.json();
    renderPackChips(data.packs || []);
  } catch (e) { /* server not up */ }
}

function renderPackChips(packs) {
  const container = document.getElementById("packs-chips");
  container.innerHTML = "";
  if (!packs.length) {
    const empty = document.createElement("span");
    empty.className = "packs-empty";
    empty.textContent = "no packs in /packs/";
    container.appendChild(empty);
    return;
  }
  // Prune removed packs from the active set
  const available = new Set(packs.map(p => p.name));
  for (const name of [...activePacks]) if (!available.has(name)) activePacks.delete(name);

  for (const p of packs) {
    const chip = document.createElement("span");
    chip.className = "pack-chip" + (activePacks.has(p.name) ? " active" : "") + (p.scope === "project" ? " scoped-project" : "");
    chip.title = p.scope === "project" ? "project-scoped (from manual/)" : "global pack (from packs/)";
    chip.innerHTML = `
      <span>${escapeHtml(p.name.replace(/-/g, " "))}</span>
      <span class="pack-size">${formatBytes(p.chars)}</span>
    `;
    chip.addEventListener("click", () => togglePack(p.name, chip));
    container.appendChild(chip);
  }
  syncPacksToServer();
}

function togglePack(name, chip) {
  if (activePacks.has(name)) {
    activePacks.delete(name);
    chip.classList.remove("active");
  } else {
    activePacks.add(name);
    chip.classList.add("active");
  }
  syncPacksToServer();
}

function syncPacksToServer() {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  ws.send(JSON.stringify({ type: "set_packs", names: [...activePacks] }));
}

function formatBytes(n) {
  if (n < 1024) return `${n}b`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)}k`;
  return `${(n / 1024 / 1024).toFixed(1)}m`;
}

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

function connect() {
  ws = new WebSocket(`ws://${location.host}/ws`);
  window.__ws = ws;

  ws.onopen = () => { log("connected to harness", "info"); refreshTree(); refreshPacks(); fetchModels(); refreshTaskPanel(); fetchThemes(); };
  ws.onclose = () => {
    log("disconnected — retrying in 3s...", "info");
    setTimeout(connect, 3000);
  };
  ws.onerror = () => ws.close();

  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    if (window.__agentLog) window.__agentLog.push({t: Date.now(), ...msg});

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
          refreshPacks();
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
  };
}

// ── Send message ──────────────────────────────────
function sendMessage() {
  const box = document.getElementById("chat-textbox");
  const text = box.value.trim();
  if (!text || busy || !ws || ws.readyState !== WebSocket.OPEN) return;

  if (text.startsWith("/")) {
    handleSlash(text);
    box.value = "";
    return;
  }

  clearResponse();
  clearToolPanel();
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
  const path = input.value.trim();
  if (!path || !ws || ws.readyState !== WebSocket.OPEN) return;
  ws.send(JSON.stringify({ type: "set_dir", path }));
}

// ── Thinking indicator ────────────────────────────
function showThinking() {
  const box = document.getElementById("response-text");
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
function appendResponse(text, type = "normal") {
  hideThinking();
  const box = document.getElementById("response-text");

  // render <think>...</think> blocks dimmed
  const thinkRe = /<think>([\s\S]*?)<\/think>/g;
  let last = 0, match;
  const parts = [];
  while ((match = thinkRe.exec(text)) !== null) {
    if (match.index > last) parts.push({ t: text.slice(last, match.index), think: false });
    parts.push({ t: match[1], think: true });
    last = match.index + match[0].length;
  }
  if (last < text.length) parts.push({ t: text.slice(last), think: false });
  if (parts.length === 0) parts.push({ t: text, think: false });

  for (let i = 0; i < parts.length; i++) {
    const part = parts[i];
    const span = document.createElement("span");
    span.textContent = part.t;
    if (part.think || type === "thinking") { span.style.opacity = "0.35"; span.style.fontStyle = "italic"; }
    else if (type === "tool")  span.style.opacity = "0.55";
    else if (type === "user")  span.style.fontWeight = "bold";
    else if (type === "info")  span.style.opacity = "0.6";
    else if (type === "error") span.style.color = "#c0392b";
    else if (type === "compact") { span.style.opacity = "0.7"; span.style.fontStyle = "italic"; }
    box.appendChild(span);
    if (part.think && i < parts.length - 1) {
      const hr = document.createElement("hr");
      hr.className = "response-divider";
      box.appendChild(hr);
    }
  }
  box.parentElement.scrollTop = box.parentElement.scrollHeight;
}

function addDivider() {
  const box = document.getElementById("response-text");
  if (!box.hasChildNodes()) return;
  const hr = document.createElement("hr");
  hr.className = "response-divider";
  box.appendChild(hr);
}

function clearResponse() {
  document.getElementById("response-text").innerHTML = "";
}

function log(text, type) {
  appendResponse(`${text}\n`, type);
}

// ── Input state ───────────────────────────────────
function setInputEnabled(enabled) {
  const box = document.getElementById("chat-textbox");
  box.disabled = !enabled;
  box.style.opacity = enabled ? "1" : "0.5";
  const stop = document.getElementById("stop-btn");
  if (stop) stop.disabled = enabled;
}

// ── Pending writes banner ─────────────────────────
function showPendingWrites(writes) {
  const files = Object.keys(writes);
  let banner = document.getElementById("pending-banner");
  if (!banner) {
    banner = document.createElement("div");
    banner.id = "pending-banner";
    banner.className = "pending-banner";
    document.body.appendChild(banner);
  }
  const label = `${files.length} pending write${files.length > 1 ? "s" : ""}`;
  banner.innerHTML = `
    <span>${label}: <em>${files.map(escapeHtml).join(", ")}</em></span>
    <button class="banner-btn apply" onclick="applyWrites()">apply</button>
    <button class="banner-btn reject" onclick="rejectWrites()">reject</button>
  `;
}

// ── Context files ─────────────────────────────────
const contextFiles = new Set();

function addContextFile(path) {
  if (contextFiles.has(path)) return;
  contextFiles.add(path);
  const container = document.getElementById("context-files");
  const row = document.createElement("div");
  row.className = "file-node file";
  row.textContent = "  " + path;
  container.appendChild(row);
}

function updateContextBar(total, max) {
  const pct = Math.min(100, Math.round((total / max) * 100));
  document.getElementById("ctx-pct").textContent = pct + "%";
  document.getElementById("ctx-bar").style.width = pct + "%";
  document.getElementById("ctx-tokens").textContent = `~${total.toLocaleString()} / ${max.toLocaleString()}`;
}

function clearContextFiles() {
  contextFiles.clear();
  document.getElementById("context-files").innerHTML = "";
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
  document.getElementById("tool-panel-inner").scrollTop = 999999;
}

function clearToolPanel() {
  document.getElementById("tool-panel-inner").innerHTML = "";
  document.getElementById("tool-panel").classList.remove("visible");
  currentToolEntry = null;
}

function hidePendingWrites() {
  const banner = document.getElementById("pending-banner");
  if (banner) banner.remove();
}

function applyWrites() {
  ws.send(JSON.stringify({ type: "apply_writes" }));
}

function rejectWrites() {
  ws.send(JSON.stringify({ type: "reject_writes" }));
}

// ── Keyboard shortcut (Enter to send) ────────────
document.addEventListener("DOMContentLoaded", () => {
  loadTheme();
  const box = document.getElementById("chat-textbox");
  box.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && busy) {
      e.preventDefault();
      cancelRun();
    }
  });

  const autoApplyToggle = document.getElementById("auto-apply-toggle");
  if (autoApplyToggle) {
    autoApplyToggle.addEventListener("change", (e) => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "set_auto_apply", enabled: e.target.checked }));
      }
    });
  }

  const templateBox = document.getElementById("file-click-template");
  if (templateBox) {
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
  if (tempSlider) {
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

  renderSlashPanel();
  connect();
});
