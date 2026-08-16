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
    _mirrorSelectFlyout("model-select", "model-picker", "model-current");
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

// ── Content signals ───────────────────────────────
// Bridge WS-driven content into the slot shell. The 4-state model is gone;
// what survives is "this panel has something new" (docs/slots.md "States
// collapse to a signal"):
//
//   signalPanel(name, true)                 → a dot on the picker entry
//   signalPanel(name, true, {summon: true}) → and a slot, if it isn't placed
//   signalPanel(name, false)                → clears the dot
//
// Only an approval gate earns `summon`. Null-tolerant for boot races.
function signalPanel(panel, hasContent, opts) {
  const h = window.harness;
  if (!h) return;
  if (!hasContent) {
    if (h.clearNotify) h.clearNotify(panel);
    return;
  }
  if (opts && opts.summon && h.requestPanel) h.requestPanel(panel);
  else if (h.notify) h.notify(panel);
}

// ── Task Panel ────────────────────────────────────
async function refreshTaskPanel() {
  try {
    const res = await fetch("/api/task");
    const data = await res.json();
    const hasTask = !!(data.content && data.content.trim() !== "");
    signalPanel("task_list", hasTask);
    const inner = document.getElementById("task-inner");
    if (!inner) return;
    if (!hasTask) {
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

// (Knowledge-packs UI removed. The chip UI, the `set_packs` WS message, and
// the system-prompt manifest are gone; the global packs/ slot retired too.
// The manual tools that replaced them were themselves retired 2026-08-14.)

function setTitleFromPath(path) {
  if (!path) return;
  const base = String(path).replace(/[\\/]+$/, "").split(/[\\/]/).pop() || path;
  document.title = `DeetsCode — ${base}`;
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

// ── Theme & skin ──────────────────────────────────
// Two independent tiers, both attributes on <html> (ported from the
// DeetsMusic token system): data-theme picks color roles (theme.css),
// data-skin picks type/shape/material (skin.css). Any theme × any skin.

// Retired ids → successor (both eras: the numbered pre-port ids AND the
// 2026-08 rename to DeetsSolutions' "named for what they are" ids:
// fairy→lilac, glade→green, hornet→black-yellow, viper→black-red).
// Mirrored by the pre-paint script in index.html (RT/RS maps) — keep in sync.
const LEGACY_THEME_NAMES = {
  1: "lilac", 2: "moonlight", 3: "green", 4: "black-yellow",
  5: "moonlight", 6: "green", 7: "black-red", 8: "sepia",
  blush: "lilac",
  fairy: "lilac",
  graphite: "moonlight",
  solar: "green",
  glade: "green",
  midnight: "moonlight",
  grove: "green",
  hornet: "black-yellow",
  abyss: "black-red",
  viper: "black-red",
};

// Retired skin ids → successor (paper/desk→press, cyberstorm→retro-future).
const LEGACY_SKIN_NAMES = {
  paper: "press",
  desk: "press",
  cyberstorm: "retro-future",
};

function setTheme(id) {
  document.documentElement.dataset.theme = id;
  localStorage.setItem("harness-theme", id);
}

function loadTheme() {
  let saved = localStorage.getItem("harness-theme");
  if (saved && LEGACY_THEME_NAMES[saved]) {
    saved = LEGACY_THEME_NAMES[saved];
    localStorage.setItem("harness-theme", saved);
  }
  if (saved) document.documentElement.dataset.theme = saved;
}

function setSkin(id) {
  document.documentElement.dataset.skin = id;
  localStorage.setItem("harness-skin", id);
}

function loadSkin() {
  let saved = localStorage.getItem("harness-skin");
  if (saved && LEGACY_SKIN_NAMES[saved]) {
    saved = LEGACY_SKIN_NAMES[saved];
    localStorage.setItem("harness-skin", saved);
  }
  if (saved) document.documentElement.dataset.skin = saved;
}

// ── App chrome: Tauri lights + DeetsCode title menu ────────────
// The titlebar is always visible (it hosts the settings menu). The traffic
// lights + drag region only act inside the Tauri webview (window.__TAURI__
// injected via withGlobalTauri) — in a browser tab they stay hidden.
// Ocean layer — three seamless wave-train patterns injected into <body>,
// inert (display:none) unless the skin opts in via --ocean-display (ocean).
// Each pattern tile is ONE full sine period (Q + T reflection), so the
// curve's value AND tangent match at the tile edge — no seam, no crossings.
// Each train is an opaque --canvas fill below a hairline crest, so a nearer
// swell occludes the ones behind it. Ink/motion live in CSS (skin tokens).
function buildOcean() {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("class", "ocean");
  svg.setAttribute("aria-hidden", "true");
  const defs = document.createElementNS(NS, "defs");
  svg.appendChild(defs);
  // [tile width, tile height, crest baseline, amplitude], farthest first
  // so the nearest train paints last (on top).
  const SWELLS = { 3: [80, 46, 26, 4], 2: [64, 38, 22, 5], 1: [48, 30, 17, 6] };
  for (const n of [3, 2, 1]) {
    const [W, H, c, a] = SWELLS[n];
    const crest = `M0 ${c} Q${W / 4} ${c - a} ${W / 2} ${c} T${W} ${c}`;
    const pat = document.createElementNS(NS, "pattern");
    pat.setAttribute("id", `ocean-swell-${n}`);
    pat.setAttribute("width", W);
    pat.setAttribute("height", H);
    pat.setAttribute("patternUnits", "userSpaceOnUse");
    const fill = document.createElementNS(NS, "path");
    fill.setAttribute("class", "ocean__fill");
    fill.setAttribute("d", `${crest} L${W} ${H} L0 ${H} Z`);
    pat.appendChild(fill);
    const line = document.createElementNS(NS, "path");
    line.setAttribute("class", `ocean__crest ocean__crest--${n}`);
    line.setAttribute("d", crest);
    pat.appendChild(line);
    defs.appendChild(pat);
    // bob (g) and roll (rect) are separate elements so their transform
    // animations compose instead of overwriting each other.
    const g = document.createElementNS(NS, "g");
    g.setAttribute("class", `ocean__bob ocean__bob--${n}`);
    const rect = document.createElementNS(NS, "rect");
    rect.setAttribute("class", `ocean__roll ocean__roll--${n}`);
    rect.setAttribute("fill", `url(#ocean-swell-${n})`);
    g.appendChild(rect);
    svg.appendChild(g);
  }
  return svg;
}

(function initAppChrome() {
  const boot = () => {
    if (!document.body.querySelector(":scope > .ocean")) {
      document.body.insertBefore(buildOcean(), document.body.firstChild);
    }

    if (window.__TAURI__) {
      document.documentElement.classList.add("is-tauri");
      // macOS keeps native decorations (tauri.macos.conf.json: Overlay
      // titlebar) — its real traffic lights overlay our chrome top-left, so
      // is-mac hides the painted ones and pads the titlebar clear of them.
      if (/Mac/i.test(navigator.platform || navigator.userAgent)) {
        document.documentElement.classList.add("is-mac");
      }
      const win = window.__TAURI__.window.getCurrentWindow();
      document.getElementById("tl-min")?.addEventListener("click", () => win.minimize());
      document.getElementById("tl-max")?.addEventListener("click", () => win.toggleMaximize());
      document.getElementById("tl-close")?.addEventListener("click", () => win.close());
    }

    // DeetsCode title menu (DeetsMusic dropdown pattern: click toggles,
    // click-outside / Escape dismiss; flyouts open on row hover via CSS).
    const root = document.getElementById("app-menu-root");
    const trigger = document.getElementById("app-menu-trigger");
    const menu = document.getElementById("app-menu");
    if (root && trigger && menu) {
      // Context flyout mirrors the in_context_files panel view (server-
      // rendered from tools.read_files) every 3s while the menu is open.
      const ctxFly = document.getElementById("ctx-files-flyout");
      let ctxTimer = null;
      const pullCtx = async () => {
        if (!ctxFly) return;
        try {
          const r = await fetch("/panels/in_context_files/view?instance=menu");
          if (r.ok) ctxFly.innerHTML = (await r.text()).replace(/<script[\s\S]*?<\/script>/gi, "");
        } catch (e) { /* server hiccup — keep last render */ }
      };
      const close = () => {
        menu.hidden = true;
        trigger.setAttribute("aria-expanded", "false");
        if (ctxTimer) { clearInterval(ctxTimer); ctxTimer = null; }
      };
      const open = () => {
        menu.hidden = false;
        trigger.setAttribute("aria-expanded", "true");
        pullCtx();
        if (!ctxTimer) ctxTimer = setInterval(pullCtx, 3000);
      };
      trigger.addEventListener("click", (e) => {
        e.stopPropagation();
        menu.hidden ? open() : close();
      });
      document.addEventListener("click", (e) => {
        if (!menu.hidden && !root.contains(e.target)) close();
      });
      document.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });

      // The settings panel's inline script used to kick these after its
      // fragment landed; the menu markup is static so kick them here.
      // All are re-callable and no-op when their IDs are absent.
      if (window.fetchModels) fetchModels();
      if (window.loadPromptModes) loadPromptModes();
      if (window.fetchThemes) fetchThemes();
      if (window.fetchSkins) fetchSkins();
      if (window.bindSettingsControls) bindSettingsControls();
      if (window.bindModelSelect) bindModelSelect();

      // DeetsMusic toggle rows: the ROW is the control (dot in the right
      // gutter shows state); the hidden checkbox keeps bindSettingsControls'
      // change-listener wiring intact.
      for (const [rowId, boxId] of [["keep-history-row", "keep-history-toggle"],
                                    ["auto-apply-row", "auto-apply-toggle"]]) {
        const row = document.getElementById(rowId);
        const box = document.getElementById(boxId);
        if (!row || !box) continue;
        const sync = () => row.setAttribute("aria-checked", box.checked ? "true" : "false");
        row.addEventListener("click", () => {
          box.checked = !box.checked;
          box.dispatchEvent(new Event("change"));
          sync();
        });
        sync();
      }
    }
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();

async function fetchThemes() {
  try {
    const res = await fetch("/api/themes");
    const data = await res.json();
    const picker = document.getElementById("theme-picker");
    if (!picker || !data.themes || data.themes.length === 0) return;
    picker.innerHTML = "";
    for (const theme of data.themes) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "flyout__item";
      b.setAttribute("role", "menuitemradio");
      // The item carries its own data-theme, so --canvas / --title / --border
      // resolve to THAT theme — the button is a taste of what it selects
      // (DeetsMusic flyout pattern). Selection is the dot in the right gutter.
      b.dataset.theme = theme.id;
      b.textContent = theme.id;
      b.onclick = () => { setTheme(theme.id); _syncPickerDots(); };
      picker.appendChild(b);
    }
    _syncPickerDots();
  } catch (e) {
    console.error("Failed to fetch themes:", e);
  }
}

// Mirror a hidden <select> into a DeetsMusic-style flyout: one menuitemradio
// per option, dot on the selected one, and a row value chip showing the
// current choice. The select stays the source of truth — clicking an item
// sets select.value and fires `change`, so existing wiring is untouched.
function _mirrorSelectFlyout(selectId, pickerId, valueId) {
  const sel = document.getElementById(selectId);
  const picker = document.getElementById(pickerId);
  if (!sel || !picker) return;
  picker.innerHTML = "";
  for (const opt of sel.options) {
    if (opt.disabled) continue;
    const b = document.createElement("button");
    b.type = "button";
    b.className = "flyout__item flyout__item--plain";
    b.setAttribute("role", "menuitemradio");
    b.textContent = opt.textContent;
    b.setAttribute("aria-checked", opt.value === sel.value ? "true" : "false");
    b.onclick = () => {
      sel.value = opt.value;
      sel.dispatchEvent(new Event("change"));
      _mirrorSelectFlyout(selectId, pickerId, valueId);
    };
    picker.appendChild(b);
  }
  const val = document.getElementById(valueId);
  if (val) val.textContent = sel.value || "—";
}

// Reflect the active theme/skin as aria-checked dots in both flyouts.
function _syncPickerDots() {
  const html = document.documentElement;
  document.querySelectorAll("#theme-picker .flyout__item").forEach((b) =>
    b.setAttribute("aria-checked", b.dataset.theme === html.dataset.theme ? "true" : "false"));
  document.querySelectorAll("#skin-picker .flyout__item").forEach((b) =>
    b.setAttribute("aria-checked", b.dataset.skin === html.dataset.skin ? "true" : "false"));
}

async function fetchSkins() {
  try {
    const res = await fetch("/api/skins");
    const data = await res.json();
    const picker = document.getElementById("skin-picker");
    if (!picker || !data.skins || data.skins.length === 0) return;
    picker.innerHTML = "";
    for (const skin of data.skins) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "flyout__item";
      b.setAttribute("role", "menuitemradio");
      // data-skin on the item resolves --font-title etc. to THAT skin, so
      // each label renders in its own title face (DeetsSolutions pattern).
      b.dataset.skin = skin.id;
      b.textContent = skin.id;
      b.onclick = () => { setSkin(skin.id); _syncPickerDots(); };
      picker.appendChild(b);
    }
    _syncPickerDots();
  } catch (e) {
    console.error("Failed to fetch skins:", e);
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
let _controlModes = ["DeetsCode"];  // refreshed from /api/prompts

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
      // No dividers between thinking/text within a turn — the thinking
      // block is its own visual separator, and per-transition hrs stacked
      // into line spam around multi-bout reasoning (think → tool → think).
      // The turn boundary divider lives in "done".
      case "thinking":
        appendResponse(msg.content, "thinking");
        lastMsgType = "thinking";
        break;

      case "text":
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

      case "dev_reload":
        // Server-side file watcher saw a frontend source change. Don't yank
        // the page out from under an in-flight run — skip while busy.
        if (!busy) location.reload();
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
        lastMsgType = null;
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
    closeSlashMenu();
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

// ── Slash commands ────────────────────────────────
// One table, two consumers: /help and the composer typeahead. The
// slash_commands panel that used to hold a hand-edited copy of this list in
// localStorage is gone — a list you click was strictly worse than typing "/"
// with completion, and it cost a tile (docs/slots.md "Demoted out of the
// tile system").
const SLASH_COMMANDS = [
  { name: "read",    args: "<path> [N-M]",                desc: "read file, optional line range" },
  { name: "search",  args: "<pattern> [glob=X] [path=Y]", desc: "regex search" },
  { name: "symbols", args: "<path>",                      desc: "list defs/classes with line numbers" },
  { name: "ls",      args: "[path]",                      desc: "list a directory" },
  { name: "tree",    args: "",                            desc: "refresh the file tree panel" },
  { name: "compact", args: "",                            desc: "summarize + trim conversation" },
  { name: "help",    args: "",                            desc: "this list" },
];

const SLASH_HELP = SLASH_COMMANDS
  .map(c => `/${c.name} ${c.args}`.trimEnd().padEnd(36) + `— ${c.desc}`)
  .join("\n");

// ── Composer typeahead ────────────────────────────
// A "/" at the start of the composer opens a filtered list under the
// textarea: ↑/↓ to move, Tab or Enter to complete, Escape to dismiss. Enter
// only completes while the menu is open — otherwise it sends, as always.
const _slashMenu = {
  el: null,
  items: [],
  index: 0,
  get open() { return !!this.el; },
};

function _slashQuery(box) {
  // Only from the very start, only on one line — a "/" mid-message is just
  // a slash, and a multi-line draft isn't a command.
  const v = box.value;
  if (!v.startsWith("/") || v.includes("\n")) return null;
  const m = /^\/([a-z?]*)$/i.exec(v);
  return m ? m[1].toLowerCase() : null;
}

function closeSlashMenu() {
  if (_slashMenu.el) _slashMenu.el.remove();
  _slashMenu.el = null;
  _slashMenu.items = [];
  _slashMenu.index = 0;
}

function _renderSlashMenu(box, matches) {
  if (!_slashMenu.el) {
    const host = box.closest(".chat-input-panel") || box.parentElement;
    if (!host) return;
    _slashMenu.el = document.createElement("div");
    _slashMenu.el.className = "slash-typeahead";
    _slashMenu.el.setAttribute("role", "listbox");
    host.appendChild(_slashMenu.el);
  }
  _slashMenu.el.innerHTML = "";
  matches.forEach((c, i) => {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "slash-typeahead-item" + (i === _slashMenu.index ? " is-active" : "");
    row.setAttribute("role", "option");
    row.innerHTML =
      `<span class="slash-cmd-name">/${escapeHtml(c.name)}</span>` +
      (c.args ? `<span class="slash-cmd-args">${escapeHtml(c.args)}</span>` : "") +
      `<span class="slash-cmd-desc">${escapeHtml(c.desc)}</span>`;
    // mousedown, not click: a click would blur the composer first.
    row.addEventListener("mousedown", (e) => {
      e.preventDefault();
      _applySlashCompletion(box, c);
    });
    _slashMenu.el.appendChild(row);
  });
}

function _applySlashCompletion(box, cmd) {
  box.value = `/${cmd.name}` + (cmd.args ? " " : "");
  closeSlashMenu();
  box.focus();
}

function updateSlashMenu(box) {
  const q = _slashQuery(box);
  if (q === null) { closeSlashMenu(); return; }
  const matches = SLASH_COMMANDS.filter(c => c.name.startsWith(q));
  if (!matches.length) { closeSlashMenu(); return; }
  if (_slashMenu.index >= matches.length) _slashMenu.index = 0;
  _slashMenu.items = matches;
  _renderSlashMenu(box, matches);
}

// Returns true when it consumed the key — the caller must not also send.
function handleSlashKey(box, e) {
  if (!_slashMenu.open) return false;
  const n = _slashMenu.items.length;
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    _slashMenu.index = (_slashMenu.index + (e.key === "ArrowDown" ? 1 : n - 1)) % n;
    _renderSlashMenu(box, _slashMenu.items);
    return true;
  }
  if (e.key === "Escape") { e.preventDefault(); closeSlashMenu(); return true; }
  if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey)) {
    e.preventDefault();
    _applySlashCompletion(box, _slashMenu.items[_slashMenu.index]);
    return true;
  }
  return false;
}

window.updateSlashMenu = updateSlashMenu;
window.handleSlashKey = handleSlashKey;
window.closeSlashMenu = closeSlashMenu;

function handleSlash(text) {
  const parts = text.trim().slice(1).split(/\s+/);
  const cmd = (parts.shift() || "").toLowerCase();

  if (cmd === "help" || cmd === "?") {
    clearToolPanel();
    // The tool stream lives in Activity, which may not be placed — summon it
    // before writing, or /help would print into nothing.
    if (window.harness && window.harness.requestPanel) window.harness.requestPanel("activity");
    const inner = document.getElementById("tool-panel-inner");
    if (!inner) { log(SLASH_HELP, "info"); return; }
    const entry = document.createElement("div");
    entry.className = "tool-entry";
    entry.innerHTML = `<div class="tool-name">/help</div><div class="tool-result">${escapeHtml(SLASH_HELP)}</div>`;
    inner.appendChild(entry);
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

// The currently-streaming thinking block: consecutive thinking chunks group
// into one collapsible <details>. Open while streaming; folds shut the
// moment any other content type arrives, so reasoning stays reviewable
// without shouting over the answer.
let currentThinkingBlock = null;

function collapseThinkingBlock() {
  if (!currentThinkingBlock) return;
  currentThinkingBlock.open = false;
  currentThinkingBlock = null;
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

  if (type !== "thinking") collapseThinkingBlock();

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

  if (type === "thinking") {
    if (!currentThinkingBlock) {
      currentThinkingBlock = document.createElement("details");
      currentThinkingBlock.className = "thinking-block";
      currentThinkingBlock.open = true;
      const summary = document.createElement("summary");
      summary.textContent = "thinking";
      const body = document.createElement("div");
      body.className = "thinking-body";
      currentThinkingBlock.appendChild(summary);
      currentThinkingBlock.appendChild(body);
      box.appendChild(currentThinkingBlock);
    }
    currentThinkingBlock.querySelector(".thinking-body")
      .appendChild(document.createTextNode(text));
    box.parentElement.scrollTop = box.parentElement.scrollHeight;
    return;
  }

  const span = document.createElement("span");
  span.textContent = text;
  span.className = `msg-${type}`;
  box.appendChild(span);
  box.parentElement.scrollTop = box.parentElement.scrollHeight;
}

function addDivider() {
  const box = document.getElementById("response-text");
  if (!box || !box.hasChildNodes()) return;
  currentAssistantMsg = null;
  collapseThinkingBlock();
  const hr = document.createElement("hr");
  hr.className = "response-divider";
  box.appendChild(hr);
}

function clearResponse() {
  currentAssistantMsg = null;
  currentThinkingBlock = null;
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

// ── Pending writes ────────────────────────────────
// The queue itself is rendered server-side by the Activity panel (it reads
// tools.pending_writes directly and re-renders on the same WS events). All
// this has to do is decide whether the panel deserves a slot: an approval
// gate is the one thing worth interrupting the layout for.
function showPendingWrites(writes) {
  const files = Object.keys(writes);
  signalPanel("activity", files.length > 0, { summon: true });
}

// ── Context files ─────────────────────────────────
const contextFiles = new Set();

function addContextFile(path) {
  if (contextFiles.has(path)) return;
  contextFiles.add(path);
  signalPanel("files", true);
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
  signalPanel("files", false);
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

// The tool stream is client-push-only (no server-side hydration), and the
// Activity panel's DOM is rebuilt empty on every slot swap. This buffer is
// the session-long source of truth: every entry lands here, and the panel
// view re-renders the whole log from it on mount (_flushToolLogBuffer).
// Capped so a marathon session can't grow it unbounded.
const _toolLogBuffer = [];
const _TOOL_LOG_MAX = 200;

function _renderToolEntry(name, args) {
  const entry = document.createElement("div");
  entry.className = "tool-entry";
  entry.innerHTML = `
    <span class="tool-name">${escapeHtml(name)}</span>
    ${renderToolArgs(name, args)}
  `;
  return entry;
}

function _renderToolResult(content) {
  const result = document.createElement("span");
  result.className = "tool-result";
  if (typeof content === "string" && content.startsWith("Error")) {
    result.classList.add("error");
  }
  result.textContent = content.slice(0, 400) + (content.length > 400 ? "…" : "");
  return result;
}

function addToolEntry(name, args) {
  signalPanel("activity", true);
  _toolLogBuffer.push({ name, args, result: undefined });
  if (_toolLogBuffer.length > _TOOL_LOG_MAX) _toolLogBuffer.shift();
  const inner = document.getElementById("tool-panel-inner");
  if (!inner) return;  // Activity not placed — the mount flush will render it
  const entry = _renderToolEntry(name, args);
  inner.appendChild(entry);
  inner.scrollTop = inner.scrollHeight;
  currentToolEntry = entry;
}

function updateToolResult(content) {
  // The buffer record is canonical (it's what a remount re-renders from).
  const last = _toolLogBuffer[_toolLogBuffer.length - 1];
  if (last && last.result === undefined) last.result = content;
  if (!currentToolEntry) return;  // not placed — flush renders result with entry
  currentToolEntry.appendChild(_renderToolResult(content));
  const inner = document.getElementById("tool-panel-inner");
  if (inner) inner.scrollTop = 999999;
}

// Called by the Activity view's inline script on mount: re-render the whole
// session log into the fresh (empty) container.
window._flushToolLogBuffer = function () {
  const inner = document.getElementById("tool-panel-inner");
  if (!inner || !_toolLogBuffer.length) return;
  for (const { name, args, result } of _toolLogBuffer) {
    const entry = _renderToolEntry(name, args);
    if (result !== undefined) entry.appendChild(_renderToolResult(result));
    inner.appendChild(entry);
    currentToolEntry = entry;
  }
  inner.scrollTop = inner.scrollHeight;
};

function clearToolPanel() {
  signalPanel("activity", false);
  _toolLogBuffer.length = 0;
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


// ── Titlebar status strip ─────────────────────────
// Time and llama-server's loaded model. Both were bento tiles until the slot
// rework; neither was ever worth a tile (docs/slots.md "Demoted out of the
// tile system"). Read-only, so the titlebar is the right home.

function _tickClock() {
  const el = document.getElementById("status-clock");
  if (!el) return;
  const now = new Date();
  el.textContent = now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  el.title = now.toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" });
}

async function _tickLlm() {
  const el = document.getElementById("status-llm");
  if (!el) return;
  try {
    const r = await fetch("/api/llm/status");
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const { reachable, models } = await r.json();
    const active = (models || []).filter(m => m.status === "loaded" || m.status === "loading");
    if (!reachable || !active.length) {
      // Server down and "nothing loaded yet" look the same on purpose —
      // in both cases there is nothing resident to report.
      el.textContent = "";
      el.hidden = true;
      return;
    }
    el.hidden = false;
    // One resident model is the common case; more than one, show the count
    // next to the first (the strip is a glance, not a table). Router-mode ids
    // can be long HF repo paths — keep the last segment.
    const m = active[0];
    const short = m.name.split("/").pop();
    const extra = active.length > 1 ? ` +${active.length - 1}` : "";
    el.textContent = (m.status === "loading" ? `${short} …` : short) + extra;
    el.title = (models || []).map(x => `${x.name} — ${x.status}`).join("\n");
    el.classList.toggle("is-loading", active.some(x => x.status === "loading"));
  } catch (e) {
    el.hidden = true;
  }
}

function startStatusStrip() {
  _tickClock();
  _tickLlm();
  // Clock on the minute boundary, so it never shows a stale minute; the model
  // strip every 5s, matching the cadence the old panel polled at.
  setInterval(_tickClock, 15000);
  setInterval(_tickLlm, 5000);
}

// ── Boot ──────────────────────────────────────────
// Enter-to-send is bound inside panels/chat/view.html now — that script
// runs after panel-shell injects #chat-textbox, so the listener attaches
// to a node that actually exists. Escape-to-cancel is global and stays
// here.
document.addEventListener("DOMContentLoaded", () => {
  loadTheme();
  loadSkin();
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && busy) {
      e.preventDefault();
      cancelRun();
    }
  });

  bindSettingsControls();
  startStatusStrip();

  connect();
  loadPromptModes();
});


// ─── Mode state & visibility ──────────────────────────────────────────────

let currentMode = "DeetsCode";

// Per-panel mode-visibility, looked up by [data-panel-name="..."]. Empty
// since the game/blog modes were deleted (2026-08); repopulate when a mode
// needs per-panel visibility again.
//
// This used to need keeping in sync with a second table in panel-shell.js
// (INSTANCE_MODE_RULES, applied at hoist time to prevent a first-paint
// flash). The slot shell mounts from a server-resolved layout and applies
// mode_overrides in the same pass, so there is one table again — this one.
const _PANEL_HIDE_RULES = [];

function applyModeVisibility() {
  for (const r of _PANEL_HIDE_RULES) {
    const el = document.querySelector(`[data-panel-name="${r.panel}"]`);
    if (!el) continue;
    let hide;
    if (r.showOnlyIn) hide = !r.showOnlyIn.includes(currentMode);
    else if (r.hideIn) hide = r.hideIn.includes(currentMode);
    else hide = false;
    el.classList.toggle("mode-hidden", hide);
  }
  // Let the panel-shell apply layout-config mode_overrides too (per-slot
  // visibility declared in panel_layout.json).
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
    _mirrorSelectFlyout("prompt-select", "mode-picker", "mode-current");
    applyModeVisibility();
    // Sync the persisted mode to the server. The server defaults to DeetsCode
    // on each new WS connection; without this, a sticky stale choice in
    // localStorage would only update the UI while the server-side prompt
    // and tool pack stayed wrong until the user re-touched the dropdown.
    syncModeToServer();
  } catch (e) { console.error("loadPromptModes:", e); }

  sel.addEventListener("change", () => {
    currentMode = sel.value;
    localStorage.setItem("harness-mode", currentMode);
    syncModeToServer();
    applyModeVisibility();
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
