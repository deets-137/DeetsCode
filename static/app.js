// ── File tree ─────────────────────────────────────
async function refreshTree() {
  try {
    const res = await fetch("/tree");
    const data = await res.json();
    const container = document.getElementById("file-tree");
    container.innerHTML = "";
    renderNodes(data.tree, container);
  } catch (e) { /* server not up yet */ }
}

function renderNodes(nodes, container) {
  for (const node of nodes) {
    const row = document.createElement("div");
    row.className = `file-node ${node.type}`;
    row.textContent = (node.type === "dir" ? "▸ " : "  ") + node.name;

    if (node.type === "dir" && node.children?.length) {
      const children = document.createElement("div");
      children.className = "file-children";
      children.style.display = "none";
      renderNodes(node.children, children);
      row.addEventListener("click", () => {
        const open = children.style.display !== "none";
        children.style.display = open ? "none" : "block";
        row.textContent = (open ? "▸ " : "▾ ") + node.name;
      });
      container.appendChild(row);
      container.appendChild(children);
    } else {
      container.appendChild(row);
    }
  }
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

// ── WebSocket ─────────────────────────────────────
let ws = null;
let busy = false;
let lastMsgType = null;

function connect() {
  ws = new WebSocket(`ws://${location.host}/ws`);

  ws.onopen = () => { log("connected to harness", "info"); refreshTree(); };
  ws.onclose = () => {
    log("disconnected — retrying in 3s...", "info");
    setTimeout(connect, 3000);
  };
  ws.onerror = () => ws.close();

  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);

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
        break;

      case "writes_rejected":
        log("Writes rejected.", "info");
        hidePendingWrites();
        break;

      case "info":
        log(msg.content, "info");
        refreshTree();
        clearContextFiles();
        break;

      case "error":
        log(msg.content, "error");
        break;

      case "usage":
        updateContextBar(msg.total, msg.max);
        break;

      case "done":
        addDivider();
        busy = false;
        setInputEnabled(true);
        break;
    }
  };
}

// ── Send message ──────────────────────────────────
function sendMessage() {
  const box = document.getElementById("chat-textbox");
  const text = box.value.trim();
  if (!text || busy || !ws || ws.readyState !== WebSocket.OPEN) return;

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
  const el = document.createElement("span");
  el.id = "thinking-indicator";
  el.textContent = "gemma is thinking...";
  el.style.cssText = "opacity:0.4; font-style:italic; animation: pulse 1.2s ease-in-out infinite;";
  box.appendChild(el);
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
}

// ── Pending writes banner ─────────────────────────
function showPendingWrites(writes) {
  const files = Object.keys(writes);
  let banner = document.getElementById("pending-banner");
  if (!banner) {
    banner = document.createElement("div");
    banner.id = "pending-banner";
    banner.style.cssText = `
      position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%);
      background: #2c2c2c; color: #eee; font-family: monospace; font-size: 13px;
      padding: 12px 20px; border-radius: 10px; display: flex; gap: 12px;
      align-items: center; box-shadow: 0 4px 16px rgba(0,0,0,0.4); z-index: 999;
    `;
    document.body.appendChild(banner);
  }
  banner.innerHTML = `
    <span>📝 ${files.length} pending write${files.length > 1 ? "s" : ""}: <em>${files.join(", ")}</em></span>
    <button onclick="applyWrites()" style="background:#4a9;border:none;color:#fff;padding:4px 10px;border-radius:6px;cursor:pointer;font-family:monospace;">Apply</button>
    <button onclick="rejectWrites()" style="background:#944;border:none;color:#fff;padding:4px 10px;border-radius:6px;cursor:pointer;font-family:monospace;">Reject</button>
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

function addToolEntry(name, args) {
  const panel = document.getElementById("tool-panel");
  const inner = document.getElementById("tool-panel-inner");

  const entry = document.createElement("div");
  entry.className = "tool-entry";
  entry.innerHTML = `
    <span class="tool-name">${name}</span>
    <span class="tool-args">${JSON.stringify(args, null, 2)}</span>
  `;
  inner.appendChild(entry);
  inner.scrollTop = inner.scrollHeight;
  currentToolEntry = entry;
}

function updateToolResult(content) {
  if (!currentToolEntry) return;
  const result = document.createElement("span");
  result.className = "tool-result";
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
  connect();
});
