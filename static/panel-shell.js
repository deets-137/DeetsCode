// panel-shell.js — phase 0.5 of the panel-system migration.
//
// Builds the canvas region grid from /api/layout and hoists legacy panel
// sections (still hardcoded in index.html under #legacy-staging) into their
// assigned region. After this script runs, app.js sees the same DOM ids in
// the same effective places — only the wrapping markup is now config-driven.
//
// Phases 1+ will replace the legacy-staging hoist with real panel rendering.

(function () {
  // Region = invisible layout slot. It owns width/height/flex-direction and
  // nothing else — the bounding-rect contract from docs/dev_project.md says the
  // loader gives the panel a rect and the panel fills it. Don't put chrome
  // (glass background, padding, etc.) on the region itself; that belongs to
  // the panel content. The only legacy concession is preserving #context-column
  // as the region id, because app.js's mode-visibility code queries it.
  const REGION_LEGACY = {
    context: { id: "context-column" },
  };

  // Translate a layout `width` value into flex-basis behavior. "auto" means
  // "share remaining space" (flex 1), anything else is a fixed basis.
  function widthToFlex(width) {
    if (!width || width === "auto") return { flex: "1 1 0", basis: "" };
    return { flex: `0 0 ${width}`, basis: width };
  }

  function applyRegionStyle(el, region, override) {
    const width = (override && override.width !== undefined) ? override.width : region.width;
    const hidden = !!(override && override.hidden);
    if (hidden) {
      el.style.display = "none";
      return;
    }
    el.style.display = "";
    const { flex } = widthToFlex(width);
    el.style.flex = flex;
  }

  function buildRegions(layout) {
    const grid = document.getElementById("region-grid");
    if (!grid) {
      console.error("[panel-shell] #region-grid missing");
      return null;
    }
    grid.innerHTML = "";
    // Schema v2: optional grid block. Defaults match GridConfig in
    // panels/loader.py; bento regions inherit them via CSS custom props.
    const gridCfg = layout.grid || { cols: 12, row_height_px: 120, gap_px: 12 };
    const byId = {};
    for (const region of layout.regions) {
      const div = document.createElement("div");
      div.dataset.region = region.id;
      div.dataset.anchor = region.anchor || "";
      div.dataset.kind = region.kind || "stack";
      const legacy = REGION_LEGACY[region.id];
      div.className = `region region-stack-${region.stack || "vertical"} region-kind-${region.kind || "stack"}`;
      if (legacy && legacy.id) div.id = legacy.id;
      if (region.kind === "bento") {
        // Bento regions: CSS Grid; flex-direction is meaningless. The
        // grid template comes from CSS using these custom properties.
        div.style.setProperty("--bento-cols", String(gridCfg.cols || 12));
        div.style.setProperty("--bento-row-h", `${gridCfg.row_height_px || 120}px`);
        div.style.setProperty("--bento-gap", `${gridCfg.gap_px || 12}px`);
      } else {
        div.style.display = "flex";
        div.style.flexDirection = (region.stack === "horizontal") ? "row" : "column";
      }
      // Tray regions get tighter gaps and centered icons.
      if (region.kind === "tray") {
        div.style.gap = "8px";
        div.style.alignItems = "center";
        div.style.paddingTop = "8px";
      }
      applyRegionStyle(div, region, null);
      grid.appendChild(div);
      byId[region.id] = { el: div, region };
    }
    return byId;
  }

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
  function hoistActions(name, contentEl) {
    const wrap = contentEl.closest(".panel-instance");
    if (!wrap) return;
    const slot = wrap.querySelector(`:scope > .panel-header > .panel-actions`);
    if (!slot) return;
    slot.innerHTML = "";
    const src = contentEl.querySelector(":scope > [data-panel-actions]");
    if (!src) return;
    while (src.firstChild) slot.appendChild(src.firstChild);
    src.remove();
  }

  async function fetchAndInject(name, contentEl) {
    const r = await fetch(`/panels/${name}/view`);
    // Clear any subscriptions registered by the prior view's script — the
    // new view's script will re-register fresh ones. Done before the
    // innerHTML swap so old subscribers can't see in-flight events.
    if (window.harness && window.harness._clearPanelSubs) {
      window.harness._clearPanelSubs(name);
    }
    if (!r.ok) {
      contentEl.innerHTML = `<div class="panel-error">panel ${name}: HTTP ${r.status}</div>`;
      return;
    }
    contentEl.innerHTML = await r.text();
    hoistActions(name, contentEl);
    runScripts(contentEl);
  }

  // Build a real panel instance (one declared with `panel: <name>` in layout).
  // Tier 0 (external/untrusted URL) → iframe. Tier 1/3 (host-installed,
  // trusted) → direct DOM injection into a `.panel-content` wrapper. The
  // host CSS reaches into that wrapper freely (host-CSS-wins by design); the
  // panel author writes content that fills the bounding rect, never sets
  // its own width %. See dev_project.md decisions log 2026-05-09.
  //
  // Trust note: tier 1/3 can read any harness DOM, call any /api/*, etc.
  // That is fine in v1 because only the host installs them — same trust gate
  // as tier-3 in-process Python. When shared-host mode lands, guest tier-1
  // panels must be forced into iframes regardless of declared tier.
  function renderPanelInstance(inst, manifest) {
    const wrap = document.createElement("section");
    wrap.className = "panel-instance";
    wrap.dataset.instance = inst.instance;
    wrap.dataset.panelName = manifest.name;
    wrap.dataset.tier = String(manifest.tier);

    // Chrome: title + actions slot + standardized pill. Handlers don't
    // render headers — they optionally include a
    // <div data-panel-actions>...</div> in their returned HTML, which
    // hoistActions moves into the .panel-actions slot. The .panel-pill
    // is shell-owned: every panel gets the same settings+minimize pair
    // in the top-right.
    const header = document.createElement("div");
    header.className = "panel-header";
    const title = document.createElement("span");
    title.className = "panel-title";
    title.textContent = manifest.title || manifest.name;
    header.appendChild(title);
    const actions = document.createElement("div");
    actions.className = "panel-actions";
    header.appendChild(actions);
    header.appendChild(buildPanelPill(inst.instance, manifest));
    wrap.appendChild(header);

    const display = manifest.display || {};
    const pref = display.preferred || {};
    const minH = (display.min || {}).height;
    const prefH = pref.height;

    if (manifest.tier === 0) {
      const iframe = document.createElement("iframe");
      iframe.className = "panel-iframe";
      iframe.src = manifest.url;
      const attrs = manifest.iframe_attrs || {};
      if (attrs.sandbox !== undefined) iframe.setAttribute("sandbox", attrs.sandbox);
      if (attrs.allow) iframe.setAttribute("allow", attrs.allow);
      if (prefH) iframe.style.height = `${prefH}px`;
      if (minH) iframe.style.minHeight = `${minH}px`;
      wrap.appendChild(iframe);
    } else {
      const content = document.createElement("div");
      content.className = "panel-content";
      content.dataset.panelContent = manifest.name;
      content.dataset.panelContentInstance = inst.instance;
      // Min/preferred from manifest land on the outer instance — they
      // describe the panel's natural footprint including chrome.
      if (minH) wrap.style.minHeight = `${minH}px`;
      if (prefH) content.style.maxHeight = `${prefH}px`;
      wrap.appendChild(content);
      // Fire and forget — the panel will replace its own `display: loading…`
      // markup once the fetch lands. Errors render inline.
      fetchAndInject(manifest.name, content);
    }
    return wrap;
  }

  // Resolve a panel content node by instance id first (multi-instance
  // friendly), falling back to panel name (single-instance back-compat:
  // when instance.instance === panel.name the two selectors match the
  // same node, so existing panels that pass their panel name continue to
  // work).
  function resolveContentEl(idOrName) {
    return (
      document.querySelector(`[data-panel-content-instance="${idOrName}"]`) ||
      document.querySelector(`[data-panel-content="${idOrName}"]`)
    );
  }

  // ── harness.* API ───────────────────────────────────────────────────────
  // Surface that direct-DOM panels (tier 1/3) call into. Iframe-bound panels
  // (tier 0/future-2) will get a postMessage proxy with the same shape.
  const harness = window.harness || (window.harness = {});

  // Re-fetch a panel's view and replace its content. Used by the panel's own
  // inline script for polling. Guards against double-registration so a panel
  // that calls `harness.refresh('foo', 5)` inside content that itself gets
  // replaced won't stack intervals.
  const _refreshTimers = {};
  harness.refresh = function (idOrName, seconds) {
    if (_refreshTimers[idOrName]) clearInterval(_refreshTimers[idOrName]);
    _refreshTimers[idOrName] = setInterval(() => {
      const el = resolveContentEl(idOrName);
      if (!el) {
        clearInterval(_refreshTimers[idOrName]);
        delete _refreshTimers[idOrName];
        return;
      }
      fetchAndInject(el.dataset.panelContent, el);
    }, Math.max(1, seconds) * 1000);
  };

  // One-shot fetch + inject. Used by event-driven panels to refresh in
  // response to a WS message via harness.subscribe.
  harness.refreshNow = function (idOrName) {
    const el = resolveContentEl(idOrName);
    if (el) fetchAndInject(el.dataset.panelContent, el);
  };

  // ── harness.subscribe — WS event bridge for panels ───────────────────────
  // Panels register interest in WS message types via:
  //   harness.subscribe('my_panel', 'pending_writes', (msg) => {...})
  // app.js's ws.onmessage hooks into harness._dispatch to fan out events.
  //
  // Subscriptions are keyed by panel name so that a panel re-render
  // (fetchAndInject) wipes old subscriptions before the new view's script
  // re-registers fresh ones — no leaks across refreshes.
  const _subs = {}; // {panel: {event: [callbacks]}}
  harness.subscribe = function (panel, event, callback) {
    const subs = (_subs[panel] = _subs[panel] || {});
    (subs[event] = subs[event] || []).push(callback);
  };
  harness._clearPanelSubs = function (panel) { delete _subs[panel]; };
  harness._dispatch = function (event, msg) {
    for (const panel in _subs) {
      for (const cb of (_subs[panel][event] || [])) {
        try { cb(msg); } catch (e) { console.error(`[harness] subscriber ${panel}/${event}`, e); }
      }
    }
  };

  // ── Tileflow state engine ───────────────────────────────────────────────
  // Per-instance state: dormant | idle | active | focused. Routing (bento
  // vs tray) and size class are decided by `runFlowPass`, which delegates
  // to tileflow-engine.js. This module owns the DOM mutations.
  const _instanceStates = {};       // {instance: state}
  const _lastStateChangeAt = {};    // {instance: ms timestamp — drives recency decay}
  const _instances = {};            // {instance: layoutInstanceObj}
  const _manifests = {};            // {panel-name: manifest} — populated at boot
  let _trayRegionEl = null;
  let _bentoRegionEl = null;

  function tileflowConfig(manifest) {
    const t = (manifest && manifest.tileflow) || {};
    const title = (manifest && manifest.title) || (manifest && manifest.name) || "?";
    return {
      default_state:     t.default_state     || "idle",
      tray_when_dormant: t.tray_when_dormant !== false,
      bubble_on_active:  t.bubble_on_active  === true,
      bubble_on_focused: t.bubble_on_focused !== false,
      icon:              t.icon              || title.charAt(0).toUpperCase(),
      priority:          (typeof t.priority === "number") ? t.priority : 0,
    };
  }

  // Live measurement of the bento grid — column width depends on viewport,
  // so we read it whenever flowPass needs to decide spans. Falls back to
  // sensible defaults when the bento isn't mounted yet (boot ordering).
  function currentGridConfig() {
    const layoutGrid = (_layoutCache && _layoutCache.grid) || {};
    const cols = layoutGrid.cols || 12;
    const rowPx = layoutGrid.row_height_px || 120;
    const gapPx = layoutGrid.gap_px || 12;
    let bentoWidth = 0;
    if (_bentoRegionEl) {
      const r = _bentoRegionEl.getBoundingClientRect();
      bentoWidth = r.width;
    }
    // Each col gets (totalWidth − (cols-1)*gap) / cols.
    const colPx = bentoWidth > 0
      ? Math.max(40, (bentoWidth - (cols - 1) * gapPx) / cols)
      : 91; // pre-mount fallback (12 cols at ~1230px bento)
    return { cols, rowPx, gapPx, colPx, bentoWidthPx: bentoWidth };
  }

  // Standardized top-right pill: dummy settings (left) + minimize-to-tray
  // (right). Two real buttons inside a skinned container; the diagonal seam
  // is a skewed 1px pseudo-element from style.css. Theme-aware via CSS
  // custom properties — no per-theme JS branching.
  function buildPanelPill(instanceId, manifest) {
    const pill = document.createElement("div");
    pill.className = "panel-pill";
    pill.dataset.instance = instanceId;

    const settingsBtn = document.createElement("button");
    settingsBtn.type = "button";
    settingsBtn.className = "panel-pill-btn panel-pill-settings";
    settingsBtn.title = "panel settings";
    settingsBtn.setAttribute("aria-label", `${manifest.title || manifest.name} settings`);
    settingsBtn.textContent = "⚙";
    settingsBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      // Dummy: real settings UI lands with Stage 3 (drag-to-pin / lock floors).
      console.log(`[panel-pill] settings clicked for "${instanceId}"`);
    });

    const minimizeBtn = document.createElement("button");
    minimizeBtn.type = "button";
    minimizeBtn.className = "panel-pill-btn panel-pill-minimize";
    minimizeBtn.title = "minimize to tray";
    minimizeBtn.setAttribute("aria-label", `minimize ${manifest.title || manifest.name} to tray`);
    minimizeBtn.textContent = "−";
    minimizeBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      harness.setState(instanceId, "dormant");
    });

    pill.appendChild(settingsBtn);
    pill.appendChild(minimizeBtn);
    return pill;
  }

  function buildTrayIcon(inst, manifest) {
    const cfg = tileflowConfig(manifest);
    const btn = document.createElement("button");
    btn.className = "tray-icon";
    btn.dataset.instance = inst.instance;
    btn.dataset.panelName = manifest.name;
    btn.title = manifest.title || manifest.name;
    btn.textContent = cfg.icon;
    btn.style.cssText = [
      "width:36px","height:36px","border-radius:8px",
      "border:1px solid var(--glass-border,#444)",
      "background:var(--panel-outer,rgba(255,255,255,0.04))",
      "color:var(--response-text,#ddd)",
      "font-size:18px","line-height:1","cursor:pointer",
      "display:flex","align-items:center","justify-content:center",
      "padding:0","backdrop-filter:blur(12px)",
    ].join(";");
    btn.addEventListener("click", () => {
      harness.setState(inst.instance, "idle");
    });
    return btn;
  }

  // ── FLIP runner ─────────────────────────────────────────────────────────
  // First-Last-Invert-Play. CSS Grid does not natively animate grid-column /
  // grid-row line changes (the transition is a no-op), and remove-then-append
  // shifts peers instantly. The FLIP technique fixes both: measure each
  // tile's rect before the DOM mutation, apply the mutation, measure again,
  // then for any tile that moved/resized, snap it back to its old position
  // via `transform` and animate the transform to identity. The browser sees
  // a smooth glide; the layout sees an instant mutation.
  //
  // Selector covers every tile that participates in layout: bento panels
  // and tray icons. Skipped: the changing instance itself (animated via
  // enter/leave classes), and any element with `data-flip-skip`.
  const FLIP_DURATION = 260;
  const FLIP_EASING = "cubic-bezier(.2,.8,.2,1)";

  function captureLayoutRects() {
    const rects = new Map();
    const els = document.querySelectorAll(
      ".region-kind-bento > .panel-instance, .region-kind-tray > .tray-icon"
    );
    for (const el of els) {
      rects.set(el, el.getBoundingClientRect());
    }
    return rects;
  }

  function playFlip(beforeRects, skipEls) {
    const skip = skipEls instanceof Set ? skipEls : new Set(skipEls || []);
    for (const [el, before] of beforeRects) {
      if (skip.has(el)) continue;
      if (!el.isConnected) continue;  // element was removed; nothing to glide
      const after = el.getBoundingClientRect();
      const dx = before.left - after.left;
      const dy = before.top - after.top;
      // Scale deltas — guard against zero width/height (mode-hidden peers).
      const sx = after.width  > 0 ? before.width  / after.width  : 1;
      const sy = after.height > 0 ? before.height / after.height : 1;
      const moved = Math.abs(dx) > 0.5 || Math.abs(dy) > 0.5
        || Math.abs(1 - sx) > 0.01 || Math.abs(1 - sy) > 0.01;
      if (!moved) continue;
      // Invert: pre-position the element where it used to be, no transition.
      el.style.transformOrigin = "top left";
      el.style.transition = "none";
      el.style.transform = `translate(${dx}px, ${dy}px) scale(${sx}, ${sy})`;
      // Force a style flush so the browser registers the inverted position
      // before we kick off the animation back to identity.
      // eslint-disable-next-line no-unused-expressions
      el.getBoundingClientRect();
      // Play: next frame, transition transform back to none.
      requestAnimationFrame(() => {
        el.style.transition = `transform ${FLIP_DURATION}ms ${FLIP_EASING}`;
        el.style.transform = "";
        const cleanup = (e) => {
          if (e && e.propertyName && e.propertyName !== "transform") return;
          el.style.transition = "";
          el.style.transform = "";
          el.style.transformOrigin = "";
          el.removeEventListener("transitionend", cleanup);
        };
        el.addEventListener("transitionend", cleanup);
        // Safety net for cases where transitionend never fires (display:none
        // sibling, interrupted by another FLIP, etc.).
        setTimeout(cleanup, FLIP_DURATION + 100);
      });
    }
  }

  // Wrap a layout-mutating callback so peers glide instead of snap. The
  // callback is responsible for: (a) removing/inserting the affected tile,
  // (b) returning the *new* element (so we can animate its entrance and
  // skip it from the peer-glide set). May return null when nothing new is
  // entering (pure removal — peers still need to glide).
  function runFlipReflow(mutate) {
    const before = captureLayoutRects();
    const result = mutate();
    const newEl = result && result.nodeType ? result : null;
    // Skip the newly-inserted element from FLIP (it has no before-rect and
    // animates via the entering/active classes instead).
    playFlip(before, newEl ? [newEl] : []);
    return newEl;
  }

  // ── Node builders (no placement decisions — flowPass owns those) ────────
  // Build the DOM node for an instance in the form it should take given its
  // current bin (bento panel vs tray icon). flowPass decides which bin; this
  // function just produces the matching node.
  function buildNodeForBin(inst, manifest, bin, state, mode) {
    let node;
    if (bin === "tray") {
      node = buildTrayIcon(inst, manifest);
    } else {
      node = renderPanelInstance(inst, manifest);
    }
    node.dataset.tileflowState = state;
    if (shouldHideForMode(inst.instance, mode)) node.classList.add("mode-hidden");
    return node;
  }

  // Apply a flowPass decision to an instance's DOM node: place in the right
  // region, set grid span (bento) or just append (tray), tag with order so
  // CSS Grid's auto-flow sorts visually by score. Doesn't build new nodes
  // unless the bin changed — see runFlowPass for the migration path.
  function applyDecision(node, decision, regionMap, instLayoutDef) {
    if (!node) return;
    // Visual order: higher score → lower `order` → earlier in the flow.
    // CSS Grid honors `order` for dense packing without DOM reordering, so
    // iframes (and their playing media) stay intact across reflows.
    node.style.order = String(-decision.score);
    // Debug surface: visible in DevTools Inspect Element, so the score/
    // class/order behind any tile's position is one click away — no need
    // to mentally reconcile source vs visual order.
    node.dataset.tileflowScore = String(decision.score);
    node.dataset.tileflowClass = decision.cls;
    node.dataset.tileflowOrder = String(-decision.score);
    if (decision.bin === "bento" && decision.span) {
      const span = decision.span;
      const pin = instLayoutDef && instLayoutDef.pin;
      if (pin) {
        // Pin is a floor: state-driven span can grow past pin.cols/rows but
        // never shrinks the origin. Auto-flow respects the start cell.
        node.style.gridColumn = `${pin.col} / span ${Math.max(span.cols, pin.cols)}`;
        node.style.gridRow    = `${pin.row} / span ${Math.max(span.rows, pin.rows)}`;
      } else {
        node.style.gridColumn = `span ${span.cols}`;
        node.style.gridRow    = `span ${span.rows}`;
      }
    } else if (decision.bin === "tray") {
      // Tray icons don't claim grid cells; clear any leftover span style.
      node.style.gridColumn = "";
      node.style.gridRow = "";
    }
  }

  // The single mutation point. Reads state, calls the engine, then walks
  // decisions: bin migrations (build/swap node), in-place updates (order +
  // span), and tray reorder. Wrapped in `runFlipReflow` so peers glide.
  let _flowScheduled = false;
  function scheduleFlowPass(animate) {
    if (_flowScheduled) return;
    _flowScheduled = true;
    requestAnimationFrame(() => {
      _flowScheduled = false;
      runFlowPass(animate !== false);
    });
  }

  function runFlowPass(animate) {
    if (!_layoutCache || !_regionMapCache) return;
    const engine = (window.harness && window.harness.tileflow) || null;
    if (!engine || typeof engine.flowPass !== "function") return;
    const mode = bootMode();
    const gridCfg = currentGridConfig();
    // Build the items list flowPass needs.
    const items = [];
    for (const id in _instances) {
      const inst = _instances[id];
      if (!inst.panel) continue;  // legacy dom_id hoists skip flowPass
      const manifest = _manifests[inst.panel];
      if (!manifest) continue;
      const cfg = tileflowConfig(manifest);
      const state = _instanceStates[id] || cfg.default_state;
      _instanceStates[id] = state;
      const overrides = inst.score_overrides || {};
      if (typeof overrides.priority !== "number") overrides.priority = cfg.priority;
      items.push({
        instance: id,
        manifest,
        state,
        overrides,
        lastStateChangeAt: _lastStateChangeAt[id] || 0,
      });
    }
    const result = engine.flowPass(items, gridCfg);
    const decisionsById = {};
    for (const d of result.decisions) decisionsById[d.instance] = d;

    // Mutate inside FLIP so peers glide to their new positions/sizes.
    const mutate = () => {
      let entered = null;
      for (const d of result.decisions) {
        const inst = _instances[d.instance];
        if (!inst) continue;
        const manifest = _manifests[inst.panel];
        const state = _instanceStates[d.instance];
        const node = document.querySelector(`[data-instance="${d.instance}"]`);
        const currentBin = node && node.classList.contains("tray-icon") ? "tray" : (node ? "bento" : null);
        if (currentBin === d.bin && node) {
          // Same bin: in-place update. State changes may have promoted
          // size class without changing bin.
          node.dataset.tileflowState = state;
          applyDecision(node, d, _regionMapCache, inst);
        } else {
          // Bin migration: build new node, drop old. The freshly-built node
          // gets the entering animation; FLIP handles peers around it.
          const targetRegionEl = d.bin === "tray"
            ? _trayRegionEl
            : (_regionMapCache[inst.region] && _regionMapCache[inst.region].el);
          if (!targetRegionEl) continue;
          if (node) node.remove();
          const fresh = buildNodeForBin(inst, manifest, d.bin, state, mode);
          applyDecision(fresh, d, _regionMapCache, inst);
          targetRegionEl.appendChild(fresh);
          // Only flag entry animation for the user-driven change, not for
          // panels that happened to re-order. Heuristic: the panel whose
          // state changed most recently within this frame.
          if (!entered || (_lastStateChangeAt[d.instance] || 0) > (_lastStateChangeAt[entered.dataset.instance] || 0)) {
            entered = fresh;
          }
        }
      }
      return entered;
    };

    if (animate) {
      const entered = runFlipReflow(mutate);
      if (entered) {
        entered.classList.add("tileflow-entering");
        requestAnimationFrame(() => {
          entered.classList.remove("tileflow-entering");
          entered.classList.add("tileflow-enter-active");
          setTimeout(() => entered.classList.remove("tileflow-enter-active"), 380);
        });
      }
    } else {
      mutate();
    }
  }

  // Public API: declare a state change. Updates the per-instance recency
  // timestamp (which feeds into engine score), then schedules a single
  // coalesced reflow on the next frame. Multiple setState calls within the
  // same tick fire one flow pass.
  harness.setState = function (instanceId, state) {
    const inst = _instances[instanceId];
    if (!inst || !inst.panel) {
      console.warn(`[tileflow] setState: unknown instance "${instanceId}"`);
      return;
    }
    const manifest = _manifests[inst.panel];
    if (!manifest) {
      console.warn(`[tileflow] setState: panel "${inst.panel}" not registered`);
      return;
    }
    if (_instanceStates[instanceId] === state) return;
    _instanceStates[instanceId] = state;
    _lastStateChangeAt[instanceId] = Date.now();
    if (!_layoutCache || !_regionMapCache) return;
    scheduleFlowPass(true);
  };

  harness.getState = function (instanceId) {
    return _instanceStates[instanceId] || null;
  };

  // Exposed so tools/console can force a fresh flow pass (e.g. after a
  // weights change via the future settings panel) without nudging state.
  harness.recomputeLayout = function () { scheduleFlowPass(true); };

  // Per-instance mode visibility, mirrored from app.js's _PANEL_HIDE_RULES.
  // Defined here so panel-shell can hide-by-default at render time, before
  // app.js's applyModeVisibility runs (which previously caused blog_ops to
  // flash visible in non-blog modes during boot).
  const INSTANCE_MODE_RULES = {
    blog_ops:         { showOnlyIn: ["blog"] },
    bot_ops:          { hideIn:     ["blog"] },
    in_context_files: { hideIn:     ["blog"] },
    task_list:        { hideIn:     ["blog"] },
    files:            { hideIn:     ["blog"] },
  };

  function shouldHideForMode(instance, mode) {
    const r = INSTANCE_MODE_RULES[instance];
    if (!r) return false;
    if (r.showOnlyIn) return !r.showOnlyIn.includes(mode);
    if (r.hideIn) return r.hideIn.includes(mode);
    return false;
  }

  function bootMode() {
    try { return localStorage.getItem("harness-mode") || "DeetsCode"; }
    catch (_) { return "DeetsCode"; }
  }

  function hoistInstances(layout, regionMap, panelManifests) {
    const staging = document.getElementById("legacy-staging");
    const mode = bootMode();
    // Locate tray + bento regions so Tileflow can route into them and
    // currentGridConfig() can measure live column widths.
    _trayRegionEl = null;
    _bentoRegionEl = null;
    for (const region of layout.regions) {
      const entry = regionMap[region.id];
      if (!entry) continue;
      if (region.kind === "tray" && !_trayRegionEl) _trayRegionEl = entry.el;
      if (region.kind === "bento" && !_bentoRegionEl) _bentoRegionEl = entry.el;
    }
    // Index instances + manifests for harness.setState.
    for (const inst of layout.instances) {
      _instances[inst.instance] = inst;
    }
    for (const name in panelManifests) _manifests[name] = panelManifests[name];

    // Pre-seed state from manifest defaults. flowPass will read these.
    for (const inst of layout.instances) {
      if (!inst.panel) continue;
      const m = panelManifests[inst.panel];
      if (!m) continue;
      const cfg = tileflowConfig(m);
      if (!_instanceStates[inst.instance]) {
        _instanceStates[inst.instance] = cfg.default_state;
      }
    }

    // Initial node creation. Panels get built into their *declared* region
    // first (bento or otherwise); flowPass then re-bins to tray if the
    // initial state demands it. Legacy dom_id hoists stay as today.
    for (const inst of layout.instances) {
      const target = regionMap[inst.region];
      if (!target) {
        console.warn(`[panel-shell] instance "${inst.instance}": region "${inst.region}" not declared`);
        continue;
      }
      if (inst.panel) {
        const m = panelManifests[inst.panel];
        if (!m) {
          console.warn(`[panel-shell] instance "${inst.instance}": panel "${inst.panel}" not in registry`);
          continue;
        }
        const state = _instanceStates[inst.instance];
        const node = renderPanelInstance(inst, m);
        node.dataset.tileflowState = state;
        if (shouldHideForMode(inst.instance, mode)) node.classList.add("mode-hidden");
        target.el.appendChild(node);
      } else {
        // Legacy hoist: locate the section in the staging div by its dom_id
        // and move it into the assigned region. Phases 4+ delete these.
        const sourceId = inst.dom_id || inst.instance;
        const node = document.getElementById(sourceId);
        if (!node) {
          console.warn(`[panel-shell] instance "${inst.instance}": #${sourceId} not found`);
          continue;
        }
        node.dataset.instance = inst.instance;
        if (shouldHideForMode(inst.instance, mode)) node.classList.add("mode-hidden");
        target.el.appendChild(node);
      }
    }
    if (staging) staging.remove();
  }

  // Apply mode_overrides for the active mode. Called once at boot (no mode
  // active yet) and re-called by app.js whenever the prompt changes via
  // window.harnessApplyLayoutMode.
  let _layoutCache = null;
  let _regionMapCache = null;
  function applyMode(mode) {
    if (!_layoutCache || !_regionMapCache) return;
    const overrides = (_layoutCache.mode_overrides || {})[mode] || {};
    const regionOverrides = overrides.regions || {};
    for (const region of _layoutCache.regions) {
      const entry = _regionMapCache[region.id];
      if (!entry) continue;
      applyRegionStyle(entry.el, region, regionOverrides[region.id]);
    }
    const instOverrides = overrides.instances || {};
    for (const inst of _layoutCache.instances) {
      const node = document.querySelector(`[data-instance="${inst.instance}"]`);
      if (!node) continue;
      const ov = instOverrides[inst.instance];
      if (ov && ov.hidden) node.style.display = "none";
      else node.style.display = "";
    }
  }
  window.harnessApplyLayoutMode = applyMode;

  async function fetchPanelManifests() {
    try {
      const r = await fetch("/api/panels");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const body = await r.json();
      const out = {};
      for (const m of (body.panels || [])) out[m.name] = m;
      return out;
    } catch (e) {
      console.warn("[panel-shell] /api/panels unavailable, real-panel instances will be skipped:", e);
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
      console.error("[panel-shell] failed to load /api/layout:", e);
      // Fail soft: leave staging visible so the UI is at least usable.
      const staging = document.getElementById("legacy-staging");
      if (staging) staging.hidden = false;
      return;
    }
    const panelManifests = await fetchPanelManifests();
    const regionMap = buildRegions(layout);
    if (!regionMap) return;
    hoistInstances(layout, regionMap, panelManifests);
    _layoutCache = layout;
    _regionMapCache = regionMap;
    // Initial mode unknown — app.js will call back once /api/prompts resolves.

    // Initial layout flow. Snap (no animation) so the first paint settles
    // immediately rather than gliding from declared positions.
    runFlowPass(false);

    // Recompute when the engine's tunable weights change (settings panel,
    // tools/console). Reads through to harness.tileflow.setWeights/resetWeights.
    const engine = window.harness && window.harness.tileflow;
    if (engine) engine._onWeightsChanged = () => scheduleFlowPass(true);

    // Viewport-class crossings (12 ↔ 6 col breakpoint) and ordinary resize
    // both want a recompute: column widths change → min-width clamps may
    // promote/demote classes. Debounce so dragging the window edge doesn't
    // thrash. RAF coalescing inside scheduleFlowPass guards the inner loop.
    let _resizeTimer = null;
    window.addEventListener("resize", () => {
      if (_resizeTimer) clearTimeout(_resizeTimer);
      _resizeTimer = setTimeout(() => { _resizeTimer = null; scheduleFlowPass(false); }, 120);
    });

    // ── Runtime overlay subscriber ────────────────────────────────────────
    // The server pushes `{type: "tileflow_state", instance, state}` whenever
    // the `set_instance_state` tool fires (from the model) or anyone hits
    // POST /api/tileflow/state/:id. Funnel straight into the existing
    // setState path so the FLIP runner does its thing. The synthetic
    // "_shell" panel name never matches a real panel, so this subscription
    // is never cleared by panel re-renders.
    harness.subscribe("_shell", "tileflow_state", (msg) => {
      if (!msg || !msg.instance || !msg.state) return;
      harness.setState(msg.instance, msg.state);
    });
    // Force-recompute frame from the `recompute_layout` tool. No-op on state;
    // just bumps the engine to re-rank and re-apply.
    harness.subscribe("_shell", "tileflow_recompute", () => scheduleFlowPass(true));
  }

  // panel-shell.js is loaded before app.js but DOMContentLoaded may have
  // already fired if scripts load late; guard either way.
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
