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
    const byId = {};
    for (const region of layout.regions) {
      const div = document.createElement("div");
      div.dataset.region = region.id;
      div.dataset.anchor = region.anchor || "";
      const legacy = REGION_LEGACY[region.id];
      div.className = `region region-stack-${region.stack || "vertical"}`;
      if (legacy && legacy.id) div.id = legacy.id;
      div.style.display = "flex";
      div.style.flexDirection = (region.stack === "horizontal") ? "row" : "column";
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
    wrap.className = "panel-instance file-panel";
    wrap.dataset.instance = inst.instance;
    wrap.dataset.panelName = manifest.name;
    wrap.dataset.tier = String(manifest.tier);

    const header = document.createElement("div");
    header.className = "file-panel-header";
    const title = document.createElement("span");
    title.textContent = manifest.title || manifest.name;
    header.appendChild(title);
    wrap.appendChild(header);

    const inner = document.createElement("div");
    inner.className = "panel-instance-inner";
    const display = manifest.display || {};
    const pref = display.preferred || {};
    const minH = (display.min || {}).height;
    const prefH = pref.height;
    if (prefH) inner.style.height = `${prefH}px`;
    if (minH) inner.style.minHeight = `${minH}px`;

    if (manifest.tier === 0) {
      const iframe = document.createElement("iframe");
      iframe.className = "panel-iframe";
      iframe.src = manifest.url;
      const attrs = manifest.iframe_attrs || {};
      if (attrs.sandbox !== undefined) iframe.setAttribute("sandbox", attrs.sandbox);
      if (attrs.allow) iframe.setAttribute("allow", attrs.allow);
      iframe.style.width = "100%";
      iframe.style.height = "100%";
      iframe.style.border = "0";
      inner.appendChild(iframe);
    } else {
      const content = document.createElement("div");
      content.className = "panel-content";
      content.dataset.panelContent = manifest.name;
      inner.appendChild(content);
      // Fire and forget — the panel will replace its own `display: loading…`
      // markup once the fetch lands. Errors render inline.
      fetchAndInject(manifest.name, content);
    }
    wrap.appendChild(inner);
    return wrap;
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
  harness.refresh = function (name, seconds) {
    if (_refreshTimers[name]) clearInterval(_refreshTimers[name]);
    _refreshTimers[name] = setInterval(() => {
      const el = document.querySelector(`[data-panel-content="${name}"]`);
      if (!el) {
        clearInterval(_refreshTimers[name]);
        delete _refreshTimers[name];
        return;
      }
      fetchAndInject(name, el);
    }, Math.max(1, seconds) * 1000);
  };

  // One-shot fetch + inject. Used by event-driven panels to refresh in
  // response to a WS message via harness.subscribe.
  harness.refreshNow = function (name) {
    const el = document.querySelector(`[data-panel-content="${name}"]`);
    if (el) fetchAndInject(name, el);
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

  function hoistInstances(layout, regionMap, panelManifests) {
    const staging = document.getElementById("legacy-staging");
    for (const inst of layout.instances) {
      const target = regionMap[inst.region];
      if (!target) {
        console.warn(`[panel-shell] instance "${inst.instance}": region "${inst.region}" not declared`);
        continue;
      }
      let node = null;
      if (inst.panel) {
        const m = panelManifests[inst.panel];
        if (!m) {
          console.warn(`[panel-shell] instance "${inst.instance}": panel "${inst.panel}" not in registry`);
          continue;
        }
        node = renderPanelInstance(inst, m);
      } else {
        // Legacy hoist: locate the section in the staging div by its dom_id
        // and move it into the assigned region. Phases 4+ delete these.
        const sourceId = inst.dom_id || inst.instance;
        node = document.getElementById(sourceId);
        if (!node) {
          console.warn(`[panel-shell] instance "${inst.instance}": #${sourceId} not found`);
          continue;
        }
      }
      node.dataset.instance = inst.instance;
      target.el.appendChild(node);
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
  }

  // panel-shell.js is loaded before app.js but DOMContentLoaded may have
  // already fired if scripts load late; guard either way.
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
