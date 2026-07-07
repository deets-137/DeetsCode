# Panels — author & modder guide

The harness UI is a grid of **panels**. Each panel is a folder under
`panels/<name>/`. Regions in `layout/panel_layout.json` declare where
panels go; the **Tileflow engine** (see [tileflow.md](tileflow.md))
decides their size and order at runtime based on per-panel state.

This doc is the cold-start reference. If you're a human building a
panel by hand or a model building one via tool calls, start here.

If something is wrong, fix it.

---

## 60-second mental model

- A panel is a folder. The folder has at minimum a `panel.json`
  manifest.
- The harness renders every panel inside a uniform chrome
  (`.panel-instance` with header, optional pill, content slot). You
  return **content only** — never your own title bar.
- Tileflow scores each panel from its manifest and current state. The
  score decides size class, ordering, and tray-vs-bento routing. You
  don't pick a size; you describe your panel's preferred footprint and
  the engine derives one.
- State changes (idle → active → focused → dormant) drive
  re-arrangement. Your panel emits state via `harness.setState()`; the
  bento glides to accommodate.

---

## Hello, world (tier 3, Python)

The minimum viable panel. Three files plus a one-line layout edit.

**`panels/hello/panel.json`**

```json
{
  "schema": 1,
  "name": "hello",
  "title": "hello",
  "tier": 3,
  "author": "you",
  "handler": "server:view",
  "permissions": { "network": [], "reads": [], "writes": [] },
  "display": {
    "shape": "wide",
    "preferred": { "width": 280, "height": 80 },
    "min":       { "width": 160, "height": 40 },
    "max":       { "width": null, "height": null },
    "scroll": "internal",
    "growable": false
  },
  "anchored": false,
  "tileflow": { "icon": "👋" }
}
```

**`panels/hello/server.py`**

```python
def view() -> str:
    return "<div style='padding:8px'>hello, world</div>"
```

**`layout/panel_layout.json`** — add to `instances`:

```json
{ "instance": "hello", "panel": "hello", "region": "bento" }
```

Hot reload — no server restart:

```
curl -X POST http://127.0.0.1:8000/api/panels/reload
```

Refresh the tab; the panel appears in the bento, sized by the engine
to roughly your preferred footprint.

---

## Pick a tier

| Tier | What you ship                       | Render path                                | When to use |
|------|-------------------------------------|--------------------------------------------|-------------|
| 0    | A URL (yours or someone else's)     | `<iframe src=manifest.url>` — sandbox      | Embedding an existing web page (Wikipedia article, YouTube, dashboards). Cheapest to ship. |
| 1    | A static `view.html` fragment       | Direct DOM injection, host CSS wins        | UI-heavy panels with their own JS (slash commands, web URL launcher). |
| 2    | (reserved — subprocess Python)      | Not implemented                            | Future, for untrusted Python. |
| 3    | A Python `server.py` with `view()`  | Direct DOM injection, full harness access  | Panels that need filesystem / subprocess / live data (file browser, ollama_ps, settings). |

Tier 1 and 3 inject into a `.panel-content[data-panel-content="<name>"]`
wrapper. There is **no shadow DOM** — host CSS wins by design, so panels
look native without effort. Inline `<script>` tags re-execute on every
view fetch via clone-and-replace.

### Tier-0 panels and tileflow state

Sandboxed iframes can't reach `window.harness`, so tier-0 panels default to
their manifest `default_state`. To participate in the bento anyway, post a
message to the host from inside the iframe:

```js
parent.postMessage({ type: "tileflow.setState", state: "focused" }, "*");
```

`state` is one of `dormant | idle | active | focused`. The host identifies
the sender by its `contentWindow` — the message carries no instance id, so a
panel can only change *its own* state, never a sibling's. Anything else in
the message is ignored.

---

## Manifest reference (`panel.json`)

Every field, current as of the latest engine. The Tileflow block is
sizing-by-derivation now — don't declare per-state size classes; the
engine derives them from `display.preferred`/`min`/`max`. See
[tileflow.md § Style guide](tileflow.md#style-guide).

```jsonc
{
  "schema": 1,                   // bump only if breaking change
  "name": "my_panel",            // [a-z0-9_-]+, must match folder name
  "title": "human title",        // shown in chrome
  "tier": 0 | 1 | 3,
  "author": "you",

  // Tier-specific entry point. Exactly one of these is set:
  "url":     "...",              // tier 0 only
  "view":    "view.html",        // tier 1 only
  "handler": "server:view",      // tier 3 only ("module:function")

  // Tier 3 only: module-level functions callable via
  // POST /panels/<name>/action/<fn> (see docs/apps.md § Actions).
  "actions": ["submit_turn"],

  // Display-only in v1 — no enforcement yet. Future tiers will gate.
  "permissions": {
    "network": ["https://en.wikipedia.org"],
    "reads":   ["./"],
    "writes":  []
  },

  // What size + shape your panel naturally is. The engine reads this
  // and picks a size class — DO NOT also declare size_classes overrides.
  "display": {
    "shape": "wide" | "tall" | "square" | "free",
    "aspect_ratio": "16:9" | null,            // for media panels
    "min":       { "width": 200, "height": 80 },   // hard floor
    "preferred": { "width": 400, "height": 120 },  // soft target
    "max":       { "width": null, "height": null }, // hard ceiling, null = unbounded
    "scroll": "internal" | "grow",
    "growable": false
  },

  "iframe_attrs": {              // tier 0 only
    "sandbox": "allow-scripts allow-same-origin",
    "allow":   ""
  },

  // True = mode_overrides cannot hide it. Orthogonal to tileflow state —
  // an anchored panel can still go dormant and route to the tray.
  "anchored": false,

  // True = additional instances of this panel may exist at runtime.
  // Singletons (settings, files, clock) leave this false; per-content
  // panels (youtube, web) opt in. The app launcher enforces it for app
  // panels (a multi_instance app requires multi_instance panels), and
  // view fetches carry `?instance=` so each instance can render its own
  // content. NOT declared: `app` — the loader derives it from folder
  // location for panels living under apps/<app>/panels/.
  "multi_instance": false,

  // Tileflow knobs (see tileflow.md for the engine).
  "tileflow": {
    "default_state":     "idle",   // dormant|idle|active|focused
    "tray_when_dormant": true,     // demote to tray icon when dormant
    "bubble_on_active":  false,    // hint: this panel WANTS to bubble on active
    "bubble_on_focused": true,
    "icon":              "📦",     // tray glyph. STRONGLY RECOMMENDED — falls
                                   //   back to first letter of title (collides).
    "score_bonus":       0          // optional flat additive (sparingly)
  }
}
```

---

## State, scoring, and live rearrangement

A panel has a state: `dormant` | `idle` | `active` | `focused`. The
state plus the manifest's `display` dimensions drive a per-panel score.
Higher score = better position + bigger size.

| State    | Meaning                                                       | Score impact |
|----------|---------------------------------------------------------------|--------------|
| dormant  | Nothing meaningful happening. Engine routes to tray as icon.  | -10          |
| idle     | Default. Normal size.                                         | 0            |
| active   | Background activity worth a glance.                           | +3 (promotes one size tier) |
| focused  | What the user is paying attention to.                         | +8 (promotes two size tiers, can hit hero) |

A recent state change adds a **recency bonus** (+5 decaying to 0 over
~30s), so panels that just bubbled stay near the top briefly even if
they don't keep emitting events.

State sources, in priority order:
1. Explicit JS call (`harness.setState`) from the panel itself.
2. Server-side overlay broadcast (`POST /api/tileflow/state/:id`,
   `set_instance_state` model tool).
3. Manifest `default_state` on first render.
4. Per-instance `score_overrides.force_state` (a hard override).

### Recipe: a panel that bubbles itself

This is what makes the bento feel alive. Your panel's inline script
calls `harness.setState` whenever something interesting happens.

```html
<!-- panels/my_player/view.html -->
<div class="player-wrap">
  <button class="play-btn">▶ play</button>
  <audio class="audio" src="/panels/my_player/static/song.mp3"></audio>
</div>

<script>
(function () {
  const root = document.currentScript.closest(".panel-instance");
  const instance = root.dataset.instance;
  const audio = root.querySelector(".audio");
  const btn = root.querySelector(".play-btn");

  btn.addEventListener("click", () => {
    if (audio.paused) audio.play(); else audio.pause();
  });

  // When playback starts, mark the panel `active` — engine bumps it up
  // a tier and slides it forward in the bento. When playback stops or
  // the audio ends, back to `idle`.
  audio.addEventListener("play",  () => harness.setState(instance, "active"));
  audio.addEventListener("pause", () => harness.setState(instance, "idle"));
  audio.addEventListener("ended", () => harness.setState(instance, "idle"));
})();
</script>
```

Same pattern works for: a `web` panel becoming `active` when its
iframe navigates, a chat panel going `focused` on user typing, a
status panel going `dormant` when its data is stale.

### Recipe: a model bubbles a panel via tool call

Both endpoints are wired and broadcast over WS to every connected tab:

```
POST /api/tileflow/state/<instance_id>   body: {"state": "focused"}
DELETE /api/tileflow/state/<instance_id> // clears to idle
```

The local model gets the same affordance via two tools:

- `set_instance_state(instance, state)` — bubble a panel.
- `recompute_layout()` — force a fresh flow pass (e.g. after a burst
  of state changes, to refresh recency decay).

These are defined in `tools/core.py` and don't require any setup per
panel — they just work against any registered instance.

---

## `window.harness.*` — complete API

Available in every tier-1 and tier-3 panel's inline script. Iframe
panels (tier 0) don't have direct access yet — postMessage proxy
arrives when an iframe panel actually needs it.

### Lifecycle

| Call | Purpose |
|------|---------|
| `harness.refresh(name, seconds)` | Re-fetch this panel's view on a recurring interval. Self-deregisters when the content node disappears. |
| `harness.refreshNow(name)` | One-shot re-fetch. Use after an action that changes server state. |

### WebSocket subscriptions

| Call | Purpose |
|------|---------|
| `harness.subscribe(panel, event, callback)` | Subscribe to a WS message type. `callback(msg)` fires on every match. Subs keyed by `panel` and wiped on the next re-render — no leaks. |

WS message types you can subscribe to (current; grep `static/app.js`
for the source of truth):

| Category | Types |
|---|---|
| Conversation | `thinking`, `text`, `done`, `info`, `error`, `compacted`, `usage`, `ctx_length`, `reset_complete` |
| Tools        | `tool_call`, `tool_result` |
| Writes       | `pending_writes`, `writes_applied`, `writes_rejected` |
| Tasks        | `task_updated` |
| Tileflow     | `tileflow_state`, `tileflow_recompute`, `layout_updated` (shell handles these — usually you don't subscribe directly; `layout_updated` triggers a live re-sync from `/api/layout`) |
| Apps         | `app_event` — app-scoped panel events; subscribe via `harness.app.subscribe`, not `harness.subscribe` (see [apps.md](apps.md)) |
| Blog mode    | `blog_posts`, `blog_post`, `blog_post_saved`, `blog_post_deleted`, `blog_song_results`, `blog_movie_results`, `blog_comments`, `blog_comment_deleted`, `blog_preview_url`, `blog_passphrase`, `blog_error` |

### Tileflow state

| Call | Purpose |
|------|---------|
| `harness.setState(instanceId, state)` | Set state for an instance. Triggers a coalesced flow pass on the next frame (falls back to `setTimeout` in hidden tabs where rAF is suspended). No-op if state unchanged. |
| `harness.getState(instanceId)` | Read the current state. |
| `harness.setSpan(instanceId, {cols, rows} \| null)` | Panel-controlled span override past the class table (e.g. match a video's aspect). `null` releases it. |
| `harness.gridConfig()` | Live grid metrics: `{cols, rowPx, gapPx, colPx, bentoWidthPx}`. |
| `harness.recomputeLayout()` | Force a fresh flow pass without changing state. |
| `harness.debugInstances()` | Read-only snapshot of the live instance index (post layout re-syncs). Debug only. |

### App-scoped API (`harness.app.*`)

For panels that belong to an app — subscription scope is discovered from
the calling script's enclosing tile. Full contract in [apps.md](apps.md).

| Call | Purpose |
|------|---------|
| `harness.app.subscribe(eventName, cb, scopeEl?)` | Hear `app_event` frames for *your own* app instance. `cb(payload, frame)`. |
| `harness.app.of(el)` | `{app, appInstance, instance, panel}` for any element inside a tile. |

### Interaction logging (`system_log`)

Every click on a `.panel-instance`, every real `setState` transition, and
every bin migration (bento ↔ tray) is emitted as a `system_log` event by
panel-shell — debounced ~500ms and batched over WS to the server, which
appends to the `system_log` SQLite table. Designed primarily as analytics
("is this panel ever used?") with a secondary debug surface.

> For the full introspection catalog (DOM attrs, console API, HTTP
> endpoints, SQL tables, plus example queries) see
> [diagnostics.md](diagnostics.md). This subsection is the panel-author
> view — emit events from a panel script and you're done.

| Call | Purpose |
|------|---------|
| `harness.logInteraction(instance, kind, meta?)` | Emit a custom event from a panel script. `kind` is free-form but stick to short snake_case tags so aggregations group cleanly. Fire-and-forget — never throws. |
| `harness.activity.dump(limit?)` | Console-table the in-memory ring (last 1000 events). Returns the rows. |
| `harness.activity.flush()` | Force the debounced WS flush immediately. Mostly for tests. |

Built-in `kind` values:

| Kind | Emitted by | Meta shape |
|---|---|---|
| `click` | shell capture-phase click listener | `{tag, bin}` — clicked element's tag name, panel's current bin |
| `state` | `harness.setState` (real transitions only) | `{from, to}` |
| `bin` | `runFlowPass` on bento↔tray migration | `{from, to}` |
| `custom` | panel scripts via `harness.logInteraction` | per-panel |

Server-side query endpoints:

| Method | URL | Purpose |
|---|---|---|
| GET | `/api/system_log?since=&until=&instance=&kind=&limit=` | Recent events newest-first. Timestamps are unix ms; `limit` is capped at 5000. |
| GET | `/api/system_log/summary?window_ms=` | Per-(instance, kind) counts, sorted by most recent. Omit `window_ms` for all-time. |

The Python side is `storage.record_system_event` / `record_system_events`
/ `query_system_log` / `panel_usage_summary` / `prune_system_log`. The
prune helper exists for maintenance but isn't auto-called; storage.db can
hold millions of rows without trouble.

### Tileflow engine (debug / tuning)

| Call | Purpose |
|------|---------|
| `harness.tileflow.WEIGHTS` | Live weights table (`base_by_class`, `state_mod`, `tray_threshold`, `recency_max`, `recency_window_s`, `promotion_step`). |
| `harness.tileflow.setWeights(partial)` | Shallow-merge a partial weights object. Auto-recomputes the layout. |
| `harness.tileflow.resetWeights()` | Restore defaults. |
| `harness.tileflow.dump()` | Console-table the current decisions, sorted by score desc. Returns the rows. |
| `harness.tileflow.flowPass(items, gridCfg)` | The engine's pure decision function. Inputs/outputs in `static/tileflow-engine.js`. |
| `harness.tileflow.naturalClass(manifest, gridCfg)` | Derive a panel's natural size class from its manifest. |
| `harness.tileflow.effectiveClass(state, naturalCls, manifest, gridCfg)` | Apply state promotion/demotion + min/max clamping. |
| `harness.tileflow.score(state, cls, overrides, lastStateChangeAt, nowMs)` | Compute a single panel's score. |

---

## Panel chrome — the shell owns it

Every panel renders as:

```
.panel-instance
├── .panel-header
│   ├── .panel-title       ← from manifest.title
│   ├── .panel-actions     ← slot for your buttons (optional)
│   └── .panel-pill        ← shell-owned: ⚙ settings + − minimize
└── .panel-content         ← your HTML lands here
```

Glass surface, padding, border-radius, title styling, and the pill
are all shell-rendered. **Your handler returns content only — never
a title bar.** The ⚙ button opens a popover with the four tileflow
states (manual state cycling); − minimizes to the tray. App panels
additionally get a `.panel-app-chip` after the title (see
[apps.md](apps.md)).

To put buttons in the chrome's title bar, return a
`<div data-panel-actions>` block — the shell hoists its children into
the `.panel-actions` slot on every view fetch:

```python
def view() -> str:
    return """
<div data-panel-actions>
  <button onclick="harness.refreshNow('my_panel')" title="Refresh">↺</button>
</div>
<div id="my-content">…</div>
""".strip()
```

The pill's minimize button (−) calls `harness.setState(<instance>, "dormant")`
for free, so any panel that opts into `tray_when_dormant` gets a
working "send to tray" affordance with zero code.

---

## Multi-instance panels

Multiple instances of the same panel are supported — the layout file
declares two entries with different `instance` ids but the same
`panel` name. Your inline script must resolve the *instance* id, not
the panel name, so two copies of the panel don't collide on selectors
or localStorage keys.

```html
<script>
(function () {
  const root = document.currentScript.closest(".panel-instance");
  const instance = root.dataset.instance;  // "youtube_a" or "youtube_b"
  const KEY = "harness.myPanel." + instance + ".value";
  // Scope every storage key, query selector, and harness.setState arg
  // to `instance`, not to manifest.name.
})();
</script>
```

`harness.refresh(name, seconds)` and `harness.refreshNow(name)` accept
either an instance id or a panel name — instance id is more specific
and the right call for multi-instance panels.

Server side, the shell appends `?instance=<id>` to every view fetch, so
tier-3 handlers can render per-instance content: declare a `harness_ctx`
keyword parameter and read `harness_ctx.instance_id` /
`harness_ctx.config` (the layout instance's `config` dict). Tier-1 views
get the config as a `data-instance-config` JSON attribute on their
`.panel-content` element. New instances can be added at runtime — any
layout write broadcasts `layout_updated` and every client mounts the
difference live (the app launcher uses this; see [apps.md](apps.md)).

---

## Tier 3 → harness state

Tier-3 panels can reach harness globals by importing modules **at
call time**. The loader re-imports your handler module on every
request, so reads are always live:

```python
def view():
    import server
    project_dir = server.project_dir
    ...
```

Module-level globals in your `server.py` are wiped on every reload
(`POST /api/panels/reload`). To persist state, write
`panels/<name>/state.json` and read it back.

---

## Extending the manifest schema

Adding a new field to `panel.json` or to a layout region requires
touching **four places** — miss any one and the field appears to work
but silently disappears in transit:

1. **The JSON file itself.**
2. **`panels/loader.py` Pydantic model** — `PanelManifest`,
   `LayoutRegion`, `LayoutInstance`, `InstanceScoreOverrides`, etc.
   Pydantic v2 strict mode drops unknown fields, so anything the
   model doesn't declare is gone before reaching the rest of the
   system.
3. **`server.py` serializer** — `/api/panels` hand-picks fields into
   the response. Add yours to the dict in `list_panels()`.
   `/api/layout` uses `model_dump` so regions/instances usually pass
   through automatically — but double-check.
4. **`static/panel-shell.js` reader** (or `tileflow-engine.js` for
   engine-relevant fields) — the JS that uses the field at render
   time.

The failure mode is annoyingly quiet. Quick verification:
`fetch('/api/panels').then(r => r.json())` in the browser console —
if your new field isn't in the response, it's been stripped at step
2 or 3.

---

## Layout file (`layout/panel_layout.json`)

```jsonc
{
  "schema": 2,
  "grid": {
    "cols": 12,
    "row_height_px": 120,
    "gap_px": 12,
    "narrow_breakpoint_px": 1200
  },
  "regions": [
    { "id": "left",  "anchor": "left",   "width": "30%",  "stack": "vertical" },
    { "id": "bento", "anchor": "center", "width": "auto", "kind": "bento" },
    { "id": "tray",  "anchor": "right",  "width": "52px", "stack": "vertical", "kind": "tray" }
  ],
  "instances": [
    { "instance": "hello",     "panel": "hello",     "region": "bento" },
    { "instance": "my_player", "panel": "my_player", "region": "bento",
      "score_overrides": { "score_floor": 5 } }   // never demotes to tray
  ],
  "mode_overrides": {
    "blog": {
      "instances": { "hello": { "hidden": true } }
    }
  }
}
```

`region.kind`:
- `"stack"` (default): legacy vertical/horizontal flex column.
- `"bento"`: CSS Grid with engine-driven placement. New panels go here.
- `"tray"`: narrow column for dormant panels' icons. The engine
  routes panels here automatically based on score.

`score_overrides` keys (all optional, see
[tileflow.md § Per-instance overrides](tileflow.md#per-instance-overrides)):

| Key | Purpose |
|---|---|
| `priority` | Signed additive on score. |
| `score_bonus` | Same, separate knob for "non-priority" bias. |
| `score_floor` | Clamp from below (e.g. `100` = always near top). |
| `score_ceiling` | Clamp from above (e.g. `-1000` = exile). |
| `force_state` | Hard-pin state regardless of overlay broadcasts. |

---

## Endpoints

| Method | URL                                          | Purpose |
|--------|----------------------------------------------|---------|
| GET    | `/api/panels`                                | Registry of discovered panels (top-level + app-contributed) + load errors |
| GET    | `/api/panels/<name>`                         | One panel's manifest + status |
| POST   | `/api/panels/reload`                         | Re-discover apps, then panels (hot reload) |
| GET    | `/api/layout`                                | Current layout JSON |
| PUT    | `/api/layout`                                | Replace the whole layout (validated; broadcasts `layout_updated`) |
| POST   | `/api/layout/instances/<id>/pin`             | Pin an instance to `{col,row,cols,rows}` (broadcasts `layout_updated`) |
| DELETE | `/api/layout/instances/<id>/pin`             | Unpin (broadcasts `layout_updated`) |
| POST   | `/api/tileflow/state/<instance_id>`          | Push runtime state overlay (broadcast over WS) |
| DELETE | `/api/tileflow/state/<instance_id>`          | Clear overlay (broadcasts `idle`) |
| GET    | `/panels/<name>/view?instance=<id>`          | Tier-1/3 rendered HTML body; instance id reaches tier-3 handlers via `harness_ctx` |
| POST   | `/panels/<name>/action/<fn>?instance=<id>`   | Invoke a whitelisted tier-3 action (manifest `actions`) — see [apps.md](apps.md) |
| GET    | `/panels/<name>/static/<file>`               | Panel-local static asset |

App lifecycle endpoints (`/api/apps*` — list/launch/unmount/update/reload)
live in [apps.md § Endpoints](apps.md#endpoints).

---

## Static assets

Anything under `panels/<name>/static/` is served at
`/panels/<name>/static/<file>`. Reference them with relative URLs:

```html
<img src="/panels/my_panel/static/logo.svg">
```

---

## Looking native

Host CSS wins. The easiest way to look native is to use the harness's
CSS custom properties on your inner elements rather than hard-coded
colors, fonts, or sizes.

Styling is three token tiers (ported from the DeetsMusic UI system),
all set as attributes on `<html>` (`data-theme` + `data-skin`, any
theme × any skin):

- **Palette** (`static/palette.css`) — raw named paints. Panels never
  reference these.
- **Theme** (`static/theme.css`) — color roles per `[data-theme="name"]`.
  Use these for anything colored: `--canvas`, `--surface`,
  `--surface-input`, `--surface-hover`, `--text`, `--text-input`,
  `--subtext`, `--border`, `--divider`, `--accent`, `--focus-glow`,
  `--scrollbar`. (Legacy names — `--response-text`, `--textbox-bg`,
  `--glass-border`, … — are aliased to these and still work.)
- **Skin** (`static/skin.css`) — everything non-color per
  `[data-skin="name"]`: `--font-body`, `--font-mono`, the `--fs-*` type
  scale, the `--radius-*` ladder, `--panel-fill` / `--panel-inset-fill`
  / `--panel-backdrop` / `--shadow-panel` materials. The `[data-skin]`
  base block is the authoritative token list (the "glass" skin); a skin
  never names a color — it points a slot at a theme role or
  `color-mix()`es one.

`/api/themes` and `/api/skins` parse those files for the settings-panel
pickers, so adding a `[data-theme="x"]` or `[data-skin="x"]` block is
all it takes to ship a new one.

Direct CSS works fine — but theme/skin toggles only follow custom
properties.

---

## Errors

If `view()` raises, the panel renders a visible red-tinted error
block with the exception name and an optional `<details>` traceback.
Server log has the full Python traceback; browser console has
client-side errors.

---

## Hot reload caveats

`POST /api/panels/reload` re-imports tier-3 handler modules. That
means:

- Module-level globals are wiped. Persist via `state.json` or by
  storing on disk.
- Open files / network connections held in module state will leak.
  Open them inside `view()`, not at import time.
- Tier-1 `view.html` and tier-0 `url` panels reload trivially (no
  Python state).

---

## Skeleton for fast scaffolding

If you're a model (or a human) building a panel from a one-sentence
brief, this is the smallest concrete starting point. Replace the
placeholders, save, hot-reload.

**`panels/<NAME>/panel.json`**

```json
{
  "schema": 1,
  "name": "<NAME>",
  "title": "<HUMAN_TITLE>",
  "tier": 3,
  "author": "you",
  "handler": "server:view",
  "permissions": { "network": [], "reads": [], "writes": [] },
  "display": {
    "shape": "wide",
    "preferred": { "width": 400, "height": 200 },
    "min":       { "width": 240, "height": 80 },
    "max":       { "width": null, "height": null },
    "scroll": "internal",
    "growable": false
  },
  "anchored": false,
  "tileflow": { "icon": "<EMOJI>" }
}
```

**`panels/<NAME>/server.py`**

```python
def view() -> str:
    return """
<div data-panel-actions>
  <button onclick="harness.refreshNow('<NAME>')" title="Refresh">↺</button>
</div>
<div class="<NAME>-wrap" style="padding:8px;font-family:monospace;">
  hello from <NAME>
</div>
""".strip()
```

**`layout/panel_layout.json`** — add to `instances`:

```json
{ "instance": "<NAME>", "panel": "<NAME>", "region": "bento" }
```

Then `curl -X POST http://127.0.0.1:8000/api/panels/reload` and
refresh the tab.

For a tier-0 (iframe) panel — e.g. a Wikipedia or Gmail embed —
swap `tier: 3` + `handler` for `tier: 0` + `"url": "https://..."` and
delete `server.py`. The harness embeds the URL in a sandboxed iframe.

---

## Reference panels

Working examples in the repo:

- [`apps/clock/panels/clock/`](../apps/clock/panels/clock/) — tier 0 (self-served URL), minimal. Also the app-migration dogfood (`apps/clock/`).
- [`panels/slash_commands/`](../panels/slash_commands/) — tier 1, single `view.html`.
- [`panels/ollama_ps/`](../panels/ollama_ps/) — tier 3, polls a subprocess and renders bars.
- [`panels/pending_writes/`](../panels/pending_writes/) — tier 3, WS-subscribing + `setState` to `focused` while writes are pending.
- [`panels/youtube/`](../panels/youtube/) — multi-instance-capable tier 1. Drives a `YT.Player` (IFrame API, nocookie host): playback events map to tileflow states (PLAYING→focused, PAUSED→active, ENDED→idle); restored videos are cued, not auto-bubbled. Uses `harness.setSpan` to claim an aspect-ratio-matched cell.
- [`panels/web/`](../panels/web/) — tier 1, freeform URL browser.
- [`panels/settings/`](../panels/settings/) — tier 3, anchored, larger preferred footprint.
- [`panels/chat/`](../panels/chat/) — tier 1, anchored to the `left` region. Demonstrates the "panel mounts after app.js boots" race: app.js buffers chat-bound writes in `_chatBootBuffer` and the view's inline script drains them via `window._flushChatBootBuffer`.
- [`apps/hello/`](../apps/hello/) — the reference *app*: two tier-3 panels sharing state via `harness_ctx`, an `actions` endpoint, and an `app_event` subscription. Start here for anything app-shaped.

---

## Cross-references

- [apps.md](apps.md) — the apps layer: multi-panel bundles, `harness_ctx`,
  app events, launcher + zip update endpoints.
- [tileflow.md](tileflow.md) — the layout engine, scoring formula,
  size-class derivation, style guide.
- [tileflow.md § Style guide](tileflow.md#style-guide) — design rules
  for a panel that plays well with auto-arrangement.
- [tileflow.md § Debug surface](tileflow.md#debug-surface) — `data-tileflow-*`
  attributes and `harness.tileflow.dump()`.
- [CLAUDE.md](../CLAUDE.md) — project orientation for working with
  the harness as a whole.
