// panel-shell.js — the slot shell. Owns the anchored chat column, the 2×2
// bento of four slots, the picker, and the panel mount/unmount contract.
//
// Boot: fetch /api/layout → build the anchor column + four slot hosts →
// fetch /api/panels → mount the anchored panels and one pool panel per slot.
//
// The scored Tileflow engine this replaced decided placement at runtime from
// a score over state, manifest sizes, and recency. Now: four fixed positions,
// one panel each, you choose which, it persists. See docs/slots.md.

(function () {
  const SLOTS = ["nw", "ne", "sw", "se"];
  // Below this the 2×2 falls under the tiles' manifest min widths. One
  // breakpoint, no hysteresis — see docs/slots.md "Narrow surface".
  const NARROW_PX = 1100;
  // The slot that survives on a narrow surface. Fixed rather than
  // "most-recently-touched" so the same tile is there every time you shrink
  // the window; the picker still reaches the whole pool from it.
  const NARROW_SLOT = "nw";

  let _layout = null;              // {schema, slots, anchored, mode_overrides}
  let _pool = [];                  // panel names eligible for a slot
  const _manifests = {};           // {panel-name: manifest}
  const _slotEls = {};             // {slot: host element}
  const _slotTouchedAt = {};       // {slot: ms} — drives the summon bus's LRU
  const _unmounts = {};            // {panel: [fn]} — registered teardown
  const _notified = {};            // {panel: true} — "has something new"
  const _headers = {};             // {panel: {title, atRoot}} — drill state
  let _anchorEl = null;
  let _bentoEl = null;

  // ── Chrome construction ─────────────────────────────────────────────────

  function buildShell() {
    const grid = document.getElementById("region-grid");
    if (!grid) {
      console.error("[panel-shell] #region-grid missing");
      return false;
    }
    grid.innerHTML = "";

    _anchorEl = document.createElement("div");
    _anchorEl.className = "anchor-column";
    _anchorEl.id = "anchor-column";
    grid.appendChild(_anchorEl);

    _bentoEl = document.createElement("div");
    _bentoEl.className = "bento";
    _bentoEl.id = "bento";
    for (const slot of SLOTS) {
      const host = document.createElement("div");
      host.className = "slot";
      host.dataset.slot = slot;
      // Capture phase so a click anywhere inside the tile — including one an
      // inline handler swallows — still counts as "I care about this slot".
      // Feeds requestPanel's least-recently-touched pick.
      host.addEventListener("pointerdown", () => { _slotTouchedAt[slot] = Date.now(); }, true);
      _slotEls[slot] = host;
      _slotTouchedAt[slot] = 0;
      _bentoEl.appendChild(host);
    }
    grid.appendChild(_bentoEl);
    return true;
  }

  // ── View fetch / inject ─────────────────────────────────────────────────

  // Re-execute scripts inside `root`. innerHTML assignment does not run
  // <script> tags; we clone each one into a fresh element so the browser
  // executes it. Used after every tier-1/3 fragment swap.
  function runScripts(root) {
    for (const old of root.querySelectorAll("script")) {
      const s = document.createElement("script");
      for (const a of old.attributes) s.setAttribute(a.name, a.value);
      s.text = old.text;
      old.parentNode.replaceChild(s, old);
    }
  }

  // Hoist any [data-panel-actions] container in the freshly-injected content
  // into the chrome's .panel-actions slot. Lets panels declare buttons that
  // belong in the title bar without each rendering its own header markup.
  function hoistActions(contentEl) {
    const wrap = contentEl.closest(".panel-instance");
    if (!wrap) return;
    const slot = wrap.querySelector(":scope > .panel-header > .panel-actions");
    if (!slot) return;
    slot.innerHTML = "";
    const src = contentEl.querySelector(":scope > [data-panel-actions]");
    if (!src) return;
    while (src.firstChild) slot.appendChild(src.firstChild);
    src.remove();
  }

  async function fetchAndInject(name, contentEl) {
    const r = await fetch(`/panels/${name}/view?instance=${encodeURIComponent(name)}`);
    // Clear the prior view's subscriptions before the swap so old subscribers
    // can't see in-flight events. A *refresh* is not an unmount, but the
    // panel's script re-registers its onUnmount fns on every render, so we
    // drop the previous ones here too or the list would accumulate copies.
    if (window.harness && window.harness._clearPanelSubs) {
      window.harness._clearPanelSubs(name);
    }
    delete _unmounts[name];
    if (!r.ok) {
      contentEl.innerHTML = `<div class="panel-error">panel ${name}: HTTP ${r.status}</div>`;
      return;
    }
    contentEl.innerHTML = await r.text();
    hoistActions(contentEl);
    runScripts(contentEl);
  }

  // ── Tile construction ───────────────────────────────────────────────────

  // Build one panel's tile. `pickable` gives the title the picker affordance
  // (slot tiles); the anchored chat tile gets a plain title. Every panel is
  // host-rendered (tier 1/3) — the tier-0 iframe branch went with `clock`.
  //
  // Trust note: tier 1/3 can read any harness DOM, call any /api/*, etc. That
  // is fine while only the host installs them — the same trust gate as tier-3
  // in-process Python.
  function buildTile(manifest, opts) {
    const pickable = !!(opts && opts.pickable);
    const slot = opts && opts.slot;

    const wrap = document.createElement("section");
    wrap.className = "panel-instance";
    wrap.dataset.instance = manifest.name;   // one panel = one instance
    wrap.dataset.panelName = manifest.name;
    wrap.dataset.tier = String(manifest.tier);
    if (slot) wrap.dataset.slot = slot;
    const header = document.createElement("div");
    header.className = "panel-header";

    // [Title ▾] — the picker. DeetsMusic's `.panel__title.is-pickable`.
    const title = document.createElement(pickable ? "button" : "span");
    title.className = "panel-title" + (pickable ? " is-pickable" : "");
    const label = document.createElement("span");
    label.className = "panel-title-label";
    const hdr = _headers[manifest.name];
    label.textContent = (hdr && hdr.title) || manifest.title || manifest.name;
    title.appendChild(label);
    if (pickable) {
      title.type = "button";
      title.setAttribute("aria-haspopup", "true");
      title.setAttribute("aria-expanded", "false");
      const chev = document.createElement("span");
      chev.className = "panel-title-chev";
      chev.setAttribute("aria-hidden", "true");
      chev.textContent = "▾";
      title.appendChild(chev);
      title.addEventListener("click", (e) => {
        e.stopPropagation();
        // Root-only: while a panel is drilled its title is context, not a
        // picker, and the click goes back instead. This is what makes
        // destroy-and-remount safe — a swap can never strand a drilled panel.
        const state = _headers[manifest.name];
        if (state && state.atRoot === false) {
          if (typeof state.onBack === "function") state.onBack();
          return;
        }
        togglePicker(header, slot, manifest.name);
      });
    }
    header.appendChild(title);

    const actions = document.createElement("div");
    actions.className = "panel-actions";
    header.appendChild(actions);
    wrap.appendChild(header);

    const content = document.createElement("div");
    content.className = "panel-content";
    content.dataset.panelContent = manifest.name;
    content.dataset.panelContentInstance = manifest.name;
    wrap.appendChild(content);
    // Fire and forget — the panel replaces its own loading markup once the
    // fetch lands. Errors render inline.
    fetchAndInject(manifest.name, content);
    return wrap;
  }

  // ── Mount / unmount ─────────────────────────────────────────────────────

  // The teardown contract (docs/slots.md "A teardown contract"). WS
  // subscriptions are handled centrally by _clearPanelSubs; everything else a
  // panel's inline script starts — setInterval, ResizeObserver, document
  // listeners — is the panel's own to reverse, via harness.onUnmount.
  function unmountPanel(name) {
    for (const fn of (_unmounts[name] || [])) {
      try { fn(); } catch (e) { console.error(`[harness] onUnmount ${name}`, e); }
    }
    delete _unmounts[name];
    if (harness._clearPanelSubs) harness._clearPanelSubs(name);
    // harness.refresh timers are shell-owned, so the shell reverses them.
    if (_refreshTimers[name]) {
      clearInterval(_refreshTimers[name]);
      delete _refreshTimers[name];
    }
    delete _headers[name];
    const node = document.querySelector(`.panel-instance[data-panel-name="${name}"]`);
    if (node) node.remove();
  }

  function mountSlot(slot, name, opts) {
    const host = _slotEls[slot];
    const manifest = _manifests[name];
    if (!host || !manifest) return null;
    const tile = buildTile(manifest, { pickable: true, slot });
    if (_notified[name]) tile.classList.add("has-notify");
    host.appendChild(tile);
    // Crossfade in. The slot rect is fixed, so there is nothing to glide —
    // the swap is purely a content change, and a fade reads as one.
    if (opts && opts.animate) {
      tile.classList.add("tile-entering");
      requestAnimationFrame(() => tile.classList.remove("tile-entering"));
    }
    return tile;
  }

  function clearSlot(slot) {
    const name = _layout && _layout.slots[slot];
    if (name) unmountPanel(name);
    const host = _slotEls[slot];
    if (host) host.innerHTML = "";
  }

  // ── The picker ──────────────────────────────────────────────────────────
  // The tile title opens a flyout of the pool. Marked entry for the panel in
  // this slot; a dot for any panel with a pending notify. This is also the
  // only discovery surface for unplaced panels — there is no tray.

  let _openPicker = null;

  function closePicker() {
    if (!_openPicker) return;
    const header = _openPicker.parentElement;
    const trigger = header && header.querySelector(".panel-title.is-pickable");
    if (trigger) trigger.setAttribute("aria-expanded", "false");
    _openPicker.remove();
    _openPicker = null;
  }

  function togglePicker(header, slot, currentName) {
    if (_openPicker && _openPicker.dataset.slot === slot) { closePicker(); return; }
    closePicker();

    const menu = document.createElement("div");
    menu.className = "panel-picker";
    menu.dataset.slot = slot;
    menu.setAttribute("role", "menu");

    const placed = new Set(Object.values((_layout && _layout.slots) || {}));
    for (const name of _pool) {
      const m = _manifests[name];
      if (!m) continue;
      const b = document.createElement("button");
      b.type = "button";
      b.className = "panel-picker-item";
      b.dataset.panel = name;
      b.setAttribute("role", "menuitem");
      if (name === currentName) b.classList.add("is-current");
      else if (placed.has(name)) b.classList.add("is-placed");
      if (_notified[name]) b.classList.add("has-notify");

      const icon = document.createElement("span");
      icon.className = "panel-picker-icon";
      icon.textContent = m.icon || (m.title || m.name).charAt(0).toUpperCase();
      const label = document.createElement("span");
      label.className = "panel-picker-label";
      label.textContent = m.title || m.name;
      b.appendChild(icon);
      b.appendChild(label);
      b.title = (placed.has(name) && name !== currentName)
        ? `${m.title || m.name} — placed elsewhere; picking swaps the two`
        : (m.title || m.name);

      b.addEventListener("click", (ev) => {
        ev.stopPropagation();
        closePicker();
        placePanel(slot, name);
      });
      menu.appendChild(b);
    }

    header.appendChild(menu);
    const trigger = header.querySelector(".panel-title.is-pickable");
    if (trigger) trigger.setAttribute("aria-expanded", "true");
    _openPicker = menu;
  }

  document.addEventListener("click", closePicker);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closePicker(); });

  // ── Placement ───────────────────────────────────────────────────────────

  // The slot invariants, in one function (docs/slots.md "Slot invariants"):
  // one instance per panel; picking a panel that's in another slot exchanges
  // the two; picking an unplaced panel replaces this slot's occupant, and the
  // displaced one goes unplaced.
  function placePanel(slot, name) {
    if (!_layout || !_slotEls[slot] || !_manifests[name]) return;
    const current = _layout.slots[slot];
    if (current === name) return;

    const otherSlot = SLOTS.find((s) => s !== slot && _layout.slots[s] === name);

    // Swap = destroy + remount. Unmount both sides before mounting either:
    // a panel moving ne→nw would otherwise briefly exist in two slots.
    clearSlot(slot);
    if (otherSlot) clearSlot(otherSlot);

    _layout.slots[slot] = name;
    if (otherSlot) _layout.slots[otherSlot] = current;

    mountSlot(slot, name, { animate: true });
    if (otherSlot && !_slotEls[otherSlot].hidden) {
      mountSlot(otherSlot, current, { animate: true });
    }

    clearNotify(name);
    _slotTouchedAt[slot] = Date.now();
    persistLayout();
    harness.logInteraction(name, "custom", { act: "place", slot, swappedWith: otherSlot || null });
  }

  let _persistTimer = null;
  function persistLayout() {
    // Server-side, not localStorage: the model reads and edits the layout,
    // which is the harness's whole point (docs/slots.md "Schema v3").
    // Debounced so a burst of picks is one write.
    if (_persistTimer) clearTimeout(_persistTimer);
    _persistTimer = setTimeout(() => {
      _persistTimer = null;
      fetch("/api/layout", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          schema: 3,
          slots: _layout.slots,
          anchored: _layout.anchored || [],
          mode_overrides: _layout.mode_overrides || {},
        }),
      }).catch((e) => console.warn("[panel-shell] layout persist failed:", e));
    }, 250);
  }

  // ── Narrow surface ──────────────────────────────────────────────────────
  // `data-surface="wide|narrow"` on <html> — the same lever as data-theme /
  // data-skin. Narrow keeps chat plus a single slot; the picker stays live,
  // so the whole pool is still one click away. Hidden slots are properly
  // unmounted, not just display:none — a hidden panel must not keep polling.

  function currentSurface() {
    return window.innerWidth < NARROW_PX ? "narrow" : "wide";
  }

  let _surface = null;
  function applySurface() {
    const next = currentSurface();
    const changed = next !== _surface;
    _surface = next;
    document.documentElement.setAttribute("data-surface", next);
    if (!_layout) return;
    for (const slot of SLOTS) {
      const host = _slotEls[slot];
      if (!host) continue;
      const visible = (next === "wide") || (slot === NARROW_SLOT);
      host.hidden = !visible;
      if (!changed) continue;
      const name = _layout.slots[slot];
      if (!name || !_manifests[name]) continue;
      if (visible && !host.firstChild) mountSlot(slot, name, { animate: false });
      else if (!visible && host.firstChild) clearSlot(slot);
    }
  }

  // ── harness.* API ───────────────────────────────────────────────────────
  // Surface that panels call into. Every live panel is host-rendered and
  // reaches this directly; tier 2 (subprocess) is still unimplemented.
  const harness = window.harness || (window.harness = {});

  function resolveContentEl(name) {
    return (
      document.querySelector(`[data-panel-content-instance="${name}"]`) ||
      document.querySelector(`[data-panel-content="${name}"]`)
    );
  }

  // Poll a panel's view. Guarded against double-registration so a panel that
  // calls harness.refresh inside content that itself gets replaced won't
  // stack intervals; unmountPanel clears whatever is left.
  const _refreshTimers = {};
  harness.refresh = function (name, seconds) {
    if (_refreshTimers[name]) clearInterval(_refreshTimers[name]);
    _refreshTimers[name] = setInterval(() => {
      const el = resolveContentEl(name);
      if (!el) {
        clearInterval(_refreshTimers[name]);
        delete _refreshTimers[name];
        return;
      }
      fetchAndInject(el.dataset.panelContent, el);
    }, Math.max(1, seconds) * 1000);
  };

  harness.refreshNow = function (name) {
    const el = resolveContentEl(name);
    if (el) fetchAndInject(el.dataset.panelContent, el);
  };

  // Register teardown for a panel's inline-script side effects (timers,
  // observers, document listeners). Called again on every render — the shell
  // drops the previous registrations first, so the list never accumulates
  // duplicates. WS subs are handled centrally; don't re-do them here.
  harness.onUnmount = function (name, fn) {
    if (typeof name !== "string" || typeof fn !== "function") return;
    (_unmounts[name] = _unmounts[name] || []).push(fn);
  };

  // A panel that drills reports its header state. `atRoot: false` turns the
  // picker into a back chevron; `onBack` runs when it's clicked. A panel that
  // never drills simply never calls this and is treated as always-at-root.
  harness.setHeader = function (name, state) {
    const next = {
      title: (state && state.title) || null,
      atRoot: !state || state.atRoot !== false,
      onBack: state && state.onBack,
    };
    _headers[name] = next;
    const tile = document.querySelector(`.panel-instance[data-panel-name="${name}"]`);
    if (!tile) return;
    const title = tile.querySelector(":scope > .panel-header > .panel-title");
    if (!title) return;
    const m = _manifests[name] || {};
    const label = title.querySelector(".panel-title-label");
    if (label) label.textContent = next.title || m.title || name;
    title.classList.toggle("is-drilled", !next.atRoot);
    const chev = title.querySelector(".panel-title-chev");
    if (chev) chev.textContent = next.atRoot ? "▾" : "‹";
  };

  // ── harness.notify — "this panel has something new" ─────────────────────
  // What was worth keeping in the 4-state model was never the score, it was
  // this. A dot on the picker entry, and a badge on the tile header if the
  // panel is placed. Cleared by looking at the panel.
  harness.notify = function (name) {
    if (!name || !_manifests[name] || _notified[name]) return;
    _notified[name] = true;
    const tile = document.querySelector(`.panel-instance[data-panel-name="${name}"]`);
    if (tile) tile.classList.add("has-notify");
    if (_openPicker) {
      const entry = _openPicker.querySelector(`.panel-picker-item[data-panel="${name}"]`);
      if (entry) entry.classList.add("has-notify");
    }
    harness.logInteraction(name, "custom", { act: "notify" });
  };

  function clearNotify(name) {
    if (!_notified[name]) return;
    delete _notified[name];
    const tile = document.querySelector(`.panel-instance[data-panel-name="${name}"]`);
    if (tile) tile.classList.remove("has-notify");
  }
  harness.clearNotify = clearNotify;

  // ── harness.requestPanel — the summon bus ───────────────────────────────
  // The replacement for wake-from-dormant: on a pending write, Activity
  // summons itself. Explicit and predictable rather than emergent from a
  // score.
  //
  // Deviation from docs/slots.md, deliberate: when the panel is *already*
  // placed we notify instead of exchanging it into the LRU slot. Summon means
  // "make sure this is visible" — it already is, and relocating a tile the
  // user is looking at is exactly the jitter this rework exists to remove.
  harness.requestPanel = function (name) {
    if (!_layout || !_manifests[name]) return;
    if (Object.values(_layout.slots).includes(name)) {
      harness.notify(name);
      return;
    }
    // Least-recently-touched *visible* slot — the tile you care about least.
    const candidates = SLOTS.filter((s) => !_slotEls[s].hidden);
    if (!candidates.length) return;
    let target = candidates[0];
    for (const s of candidates) {
      if ((_slotTouchedAt[s] || 0) < (_slotTouchedAt[target] || 0)) target = s;
    }
    placePanel(target, name);
  };

  // Read-only views, for the console and for panels that want to know
  // whether a sibling is on screen.
  harness.slots = function () {
    return _layout ? JSON.parse(JSON.stringify(_layout.slots)) : {};
  };
  harness.pool = function () { return _pool.slice(); };

  // ── harness.subscribe — WS event bridge for panels ───────────────────────
  //   harness.subscribe('my_panel', 'pending_writes', (msg) => {...})
  // app.js's ws.onmessage hooks into harness._dispatch to fan out events.
  // Keyed by panel name so a re-render (fetchAndInject) wipes old
  // subscriptions before the new view's script registers fresh ones.
  const _subs = {}; // {panel: {event: [callbacks]}}
  harness.subscribe = function (panel, event, callback) {
    const subs = (_subs[panel] = _subs[panel] || {});
    (subs[event] = subs[event] || []).push(callback);
  };
  harness._clearPanelSubs = function (panel) {
    delete _subs[panel];
  };
  harness._dispatch = function (event, msg) {
    for (const panel in _subs) {
      for (const cb of (_subs[panel][event] || [])) {
        try { cb(msg); } catch (e) { console.error(`[harness] subscriber ${panel}/${event}`, e); }
      }
    }
  };
  // Canary surface for the "swap a slot 20× and assert nothing grows" test in
  // docs/slots.md. Cheap enough to ship.
  harness._subCounts = function () {
    let subs = 0;
    for (const p in _subs) for (const e in _subs[p]) subs += _subs[p][e].length;
    let unmountFns = 0;
    for (const p in _unmounts) unmountFns += _unmounts[p].length;
    return {
      panels: Object.keys(_subs).length,
      subs,
      unmountFns,
      refreshTimers: Object.keys(_refreshTimers).length,
      tiles: document.querySelectorAll(".panel-instance").length,
    };
  };

  // ── system_log: client-side interaction stream ──────────────────────────
  // Clicks, placements, notifies, and panel-emitted custom events flow
  // through harness.logInteraction → in-memory ring (debug) + debounced WS
  // flush (analytics → storage.system_log). See docs/diagnostics.md.
  //
  // Ring is bounded so a runaway emitter can't OOM the tab; flush every
  // 500ms while events accumulate, or immediately on unload.
  const _activityRing = [];
  const _ACTIVITY_RING_MAX = 1000;
  const _activityFlushQueue = [];
  let _activityFlushTimer = null;
  const _ACTIVITY_FLUSH_MS = 500;

  function _enqueueActivity(evt) {
    _activityRing.push(evt);
    if (_activityRing.length > _ACTIVITY_RING_MAX) _activityRing.shift();
    _activityFlushQueue.push(evt);
    if (_activityFlushTimer) return;
    _activityFlushTimer = setTimeout(_flushActivity, _ACTIVITY_FLUSH_MS);
  }

  function _flushActivity() {
    _activityFlushTimer = null;
    if (!_activityFlushQueue.length) return;
    const ws = window.__ws;
    if (!ws || ws.readyState !== 1) {
      // WS not ready — try again next tick. Events stay in the queue.
      _activityFlushTimer = setTimeout(_flushActivity, _ACTIVITY_FLUSH_MS);
      return;
    }
    const batch = _activityFlushQueue.splice(0, _activityFlushQueue.length);
    try {
      ws.send(JSON.stringify({ type: "system_log", events: batch }));
    } catch (e) {
      // Send failed — put the batch back at the head and retry later.
      _activityFlushQueue.unshift(...batch);
      _activityFlushTimer = setTimeout(_flushActivity, _ACTIVITY_FLUSH_MS * 2);
    }
  }

  window.addEventListener("beforeunload", () => {
    if (_activityFlushQueue.length) _flushActivity();
  });

  // Fire-and-forget; never throws — analytics must not break interactions.
  harness.logInteraction = function (panel, kind, meta) {
    if (typeof panel !== "string" || typeof kind !== "string") return;
    try {
      _enqueueActivity({
        ts: Date.now(),
        instance: panel,     // one panel = one instance; the column stays
        panel,
        kind,
        meta: (meta && typeof meta === "object") ? meta : {},
      });
    } catch (e) { /* swallow */ }
  };

  const activity = (harness.activity = harness.activity || {});
  activity.dump = function (limit) {
    const n = Math.min(limit || 50, _activityRing.length);
    const rows = _activityRing.slice(_activityRing.length - n);
    if (console.table) console.table(rows.map(r => ({
      ts: new Date(r.ts).toISOString().slice(11, 23),
      panel: r.panel, kind: r.kind, meta: JSON.stringify(r.meta),
    })));
    return rows;
  };
  activity.flush = _flushActivity;   // force-flush, mostly for tests

  // ── Mode visibility ─────────────────────────────────────────────────────
  // Empty since the game/blog modes were deleted (2026-08); the schema stays
  // for future modes. DeetsCode is the only mode today.
  function bootMode() {
    try { return localStorage.getItem("harness-mode") || "DeetsCode"; }
    catch (_) { return "DeetsCode"; }
  }

  function applyMode(mode) {
    if (!_layout) return;
    const overrides = (_layout.mode_overrides || {})[mode] || {};
    const slotOverrides = overrides.slots || {};
    for (const slot of SLOTS) {
      const host = _slotEls[slot];
      if (!host) continue;
      const ov = slotOverrides[slot];
      host.classList.toggle("mode-hidden", !!(ov && ov.hidden));
    }
  }
  window.harnessApplyLayoutMode = applyMode;

  // ── Boot ────────────────────────────────────────────────────────────────

  async function fetchPanelManifests() {
    try {
      const r = await fetch("/api/panels");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const body = await r.json();
      const out = {};
      for (const m of (body.panels || [])) out[m.name] = m;
      return out;
    } catch (e) {
      console.warn("[panel-shell] /api/panels unavailable:", e);
      return {};
    }
  }

  async function boot() {
    let layout;
    try {
      const r = await fetch("/api/layout");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      layout = await r.json();
    } catch (e) {
      // Hard failure: with chat mounted as a panel there is no fallback UI to
      // show. The console error is the user-visible signal.
      console.error("[panel-shell] failed to load /api/layout:", e);
      return;
    }
    const manifests = await fetchPanelManifests();
    for (const name in manifests) _manifests[name] = manifests[name];
    if (!buildShell()) return;

    _layout = layout;
    _pool = (layout.pool || []).filter((n) => _manifests[n]);
    if ((layout.warnings || []).length) {
      console.warn("[panel-shell] layout fell back:", layout.warnings.join("; "));
    }

    // Anchored panels first — chat must exist before app.js's WS frames start
    // arriving. Its boot buffer covers the gap, but a shorter gap is better.
    for (const name of (layout.anchored || [])) {
      const m = _manifests[name];
      if (!m) { console.warn(`[panel-shell] anchored panel "${name}" not registered`); continue; }
      _anchorEl.appendChild(buildTile(m, { pickable: false }));
    }

    _surface = currentSurface();
    document.documentElement.setAttribute("data-surface", _surface);
    for (const slot of SLOTS) {
      const host = _slotEls[slot];
      const visible = (_surface === "wide") || (slot === NARROW_SLOT);
      host.hidden = !visible;
      const name = layout.slots[slot];
      if (visible && name && _manifests[name]) mountSlot(slot, name, { animate: false });
    }
    applyMode(bootMode());

    // One breakpoint, so only a crossing matters. Debounced: dragging the
    // window edge must not thrash mount/unmount.
    let _resizeTimer = null;
    window.addEventListener("resize", () => {
      if (_resizeTimer) clearTimeout(_resizeTimer);
      _resizeTimer = setTimeout(() => { _resizeTimer = null; applySurface(); }, 120);
    });

    // Auto-instrument clicks. Capture phase so this fires before any inline
    // handler stops propagation. Looking at a tile clears its notify.
    document.addEventListener("click", (e) => {
      const target = e.target;
      if (!target || !(target instanceof Element)) return;
      const node = target.closest(".panel-instance");
      if (!node || !node.dataset.panelName) return;
      clearNotify(node.dataset.panelName);
      harness.logInteraction(node.dataset.panelName, "click", {
        tag: target.tagName ? target.tagName.toLowerCase() : null,
        slot: node.dataset.slot || null,
      });
    }, true);

    // Server-side layout edits (the model rewriting panel_layout.json).
    harness.subscribe("_shell", "layout_updated", syncLayoutFromServer);
    // Server-side summon, e.g. an app launcher asking for its panel.
    harness.subscribe("_shell", "panel_summon", (msg) => {
      if (msg && msg.panel) harness.requestPanel(msg.panel);
    });
  }

  // Reconcile a server-side layout change without rebooting: remount only the
  // slots whose panel actually changed, so an unrelated edit never discards
  // the scroll position of a tile you're reading.
  async function syncLayoutFromServer() {
    let layout;
    try {
      const r = await fetch("/api/layout");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      layout = await r.json();
    } catch (e) {
      console.warn("[panel-shell] layout re-sync failed:", e);
      return;
    }
    const manifests = await fetchPanelManifests();
    for (const name in manifests) _manifests[name] = manifests[name];
    _pool = (layout.pool || []).filter((n) => _manifests[n]);

    const before = (_layout && _layout.slots) || {};
    const changed = SLOTS.filter((s) => before[s] !== layout.slots[s]);
    // Unmount every changed slot before mounting any of them: a panel moving
    // nw→ne would otherwise briefly occupy two slots.
    for (const slot of changed) clearSlot(slot);
    _layout.slots = layout.slots;
    _layout.anchored = layout.anchored || _layout.anchored;
    _layout.mode_overrides = layout.mode_overrides || {};
    for (const slot of changed) {
      const name = layout.slots[slot];
      if (name && _manifests[name] && !_slotEls[slot].hidden) {
        mountSlot(slot, name, { animate: true });
      }
    }
    applyMode(bootMode());
  }

  // panel-shell.js loads before app.js but DOMContentLoaded may already have
  // fired if scripts load late; guard either way.
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
