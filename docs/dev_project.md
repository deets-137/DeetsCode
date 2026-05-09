# dev_project.md — User-moddable panel system

Living plan for the panel-system rebuild. **This file is the durable context** —
keep it up to date as we make decisions or finish phases. If a section here
contradicts something said in chat, this file wins (or update it).

---

## Handoff — start here

**Status as of 2026-05-09:** phases 0 → 7 **structurally** complete in
a single overnight session. Every panel except `chat` is migrated and
functionally working — controls wire up, WS events route, mode visibility
toggles. **The panels are visually rough.** The migration prioritized
correctness over polish: each handler reproduced legacy header markup
ad-hoc instead of using a shared chrome, the settings bento is stacked
where it should be side-by-side, padding is inconsistent, and
`index.html` has dead hidden divs littered around. **Next session: phase
8 (UI polish) is the highest-priority work — see Outstanding Work
below.** After polish, migrate `chat`, then PR.

**What already exists — do not rebuild:**

- `paths.PANELS_DIR` = `panels/`, `paths.PANEL_LAYOUT_FILE` =
  `layout/panel_layout.json`. Registered.
- [layout/panel_layout.json](layout/panel_layout.json) — 4 regions,
  12 panel instances (1 legacy: chat). Mode_overrides for blog mode.
- [panels/loader.py](../panels/loader.py) — Pydantic `PanelManifest` /
  `PanelLayout`, `discover()`, `registry()`, `errors()`, `get(name)`,
  `panel_dir(name)`, `load_layout()`, `render_view(name)` (handles tier 1
  and tier 3 — tier 3 hot-reloads the handler module each call).
- [server.py](../server.py) — routes: `/api/panels`, `/api/panels/{name}`,
  `/api/panels/reload`, `/api/layout`, `/panels/{name}/view`,
  `/panels/{name}/static/{file:path}`. Visible `_panel_error_html()`
  placeholder for view() failures. Discovery runs at import time.
- [static/panel-shell.js](../static/panel-shell.js) — region grid,
  legacy hoist, real panel render. **Tier 0** → iframe to `manifest.url`.
  **Tier 1/3** → fetch + direct DOM injection into `.panel-content`
  wrapper (host CSS wins, no shadow DOM). Inline scripts re-execute via
  clone-and-replace on each fetch. Subscriptions wiped on every fetch
  via `harness._clearPanelSubs(name)` so refreshes don't leak listeners.
- `window.harness.*` API:
  - `refresh(name, seconds)` — recurring poll
  - `refreshNow(name)` — one-shot fetch
  - `subscribe(panel, event, callback)` — WS event bridge (app.js's
    `ws.onmessage` fans out via `harness._dispatch`)
- All panels:
  - **clock** (tier 0) — self-served iframe
  - **ollama_ps** (tier 3) — `ollama ps` parser, polling refresh
  - **task_list** (tier 3) — task.md checklist
  - **in_context_files** (tier 3) — reads `tools.read_files`
  - **knowledge_packs** (tier 3) — hybrid: chrome + #packs-chips
  - **slash_commands** (tier 1) — self-contained, localStorage state
  - **pending_writes** (tier 3) — first WS-subscribing panel
  - **tool_log** (tier 3) — hybrid: chrome + #tool-panel-inner
  - **files** (tier 3, anchored) — hybrid: chrome + #file-tree
  - **settings** (tier 3, anchored) — full bento (model + customization);
    NOT split into settings_model/settings_custom (deviation from doc;
    revisit when sub-region primitive lands)
  - **bot_ops** (tier 3, anchored) — single panel with three subsections
  - **blog_ops** (tier 3) — single panel with seven subsections, mode-gated
- `chat` is the **only** remaining legacy `dom_id` instance.

**Read these sections in order before writing any code:**

1. *Trajectory / north star* — what this is becoming, beyond v1
2. *Concepts* (Panel, Tier, Manifest, Sizing model, Layout config) — the data model
3. *Endpoints* — server URL conventions
4. *Phased plan* — pick up at phase 3
5. *Migration risk register* — order matters; respect it
6. *Decisions log* — read every entry; the *why* matters

**Hard constraints from CLAUDE.md (don't violate):**

- All paths live in `paths.py` and are added via the `register_path` core
  tool, never hand-edited.
- Single-user local toy assumption is being relaxed *toward* friend-shared
  mode but not yet — no auth, no multi-user code in v1.
- Don't add features beyond what the phase requires. Don't add error
  handling for scenarios that can't happen. The migration phases will
  delete code; that's fine and expected.

**Verification rule (learned the hard way):** any phase that produces
visible UI must be confirmed by a real screenshot before being marked
complete. Route-shape smoke tests via TestClient are not sufficient —
they will not catch CSS/DOM regressions like double-applied width rules.
Use the launch preview (configured in `.claude/launch.json` as `harness`
on port 8765) to load the page and screenshot it.

**Bounding-rect contract — do not violate.** `static/style.css` has the
rule `.region > * { width: 100% }` enforcing that any element placed in a
region fills its assigned rect. Panels do not get to pick their own width.
Tier-3 work in phase 3 must respect this — `view()` returns content that
fills the host-provided container, never sets its own width %.

---

## Trajectory / north star

This is being built as the spine of what may eventually become a hub-style UI
for varied apps and panels — coding tools today, but the design should bend
toward "browser/OS-shaped surface for entertainment, productivity, embeds,
mini-apps." Concrete implications baked into v1:

- Hybrid render: trusted panels render direct-DOM, untrusted in iframe. We do
  not pay iframe overhead for the 90% case.
- Panel content vs. panel chrome (titlebar, controls) is split — chrome is
  loader-owned, so future window-manager features (drag, minimize, focus) do
  not require touching panels.
- Single `harness.*` API surface across both render paths so a panel author
  writes one code regardless of tier. Promoting a tier-1 panel to tier 0
  (extract to a public URL) is a deploy change, not a rewrite.
- Bounding-rect rendering contract — loader gives each panel a rect; panel
  fills it. Works for tiled v1 layout; works for floating-window future.
- Layout schema uses **instances** referencing panels (not panels directly),
  even though v1 only supports one instance per panel. Frees us to add
  multi-instance later (two YouTube panels playing different videos) without
  migrating every layout file.
- Config keys are panel-shaped, not widget-shaped, so a future "app" concept
  (multiple panels sharing state under an app id) slots in alongside.

**Not built in v1, but designed against:** apps-above-panels, multi-instance
panels, floating-window layout, system-wide notifications, theming v2.

## Goal

Replace the hardcoded dev-column markup in `static/index.html` with a panel
system where:

1. Each panel is a self-contained folder under `panels/<name>/`.
2. Panels come in **trust tiers** — iframe-only, host-served HTML, in-process
   Python, and (later) sandboxed-subprocess Python.
3. The **layout** (which panels are visible, where, at what size) is a separate
   declarative config file that Claude Code can edit to rearrange the UI without
   touching panel code.
4. Existing harness UI (task list, packs, slash commands, blog-ops, bot-ops,
   etc.) gets **dogfood-migrated** into the new system, easiest first.
5. The system is **distributable** — designed so a friend could one day clone
   the harness, or even connect to a host-shared instance, without the
   architecture needing a rewrite.

## Non-goals (explicitly deferred)

- Tier 2 (subprocess) panels — manifest field reserved, loader rejects with
  "not yet supported." Add when a real medium-trust use case appears.
- Auth / passphrase gate for shared-host mode. Keep `127.0.0.1` only for now.
- postMessage bridge between iframe panels and harness state. Add when the
  first panel actually needs it.
- True sandboxing (seccomp, gVisor, Docker). Honor-system + install-time
  permission display is the v1 story.

---

## Concepts

### Panel

A folder under `panels/<name>/` with at minimum a `panel.json` manifest. A
panel renders into a slot in the UI. **Render path is tier-determined
(hybrid model):**

- **Tier 0 (external URL)** → `<iframe src="...">`. Browser sandbox is the
  trust boundary. Required for arbitrary third-party URLs (YouTube etc.).
- **Tier 1 (host-served HTML)** → direct DOM injection of the panel's HTML
  fragment (with style scoping via a wrapper class or shadow root —
  decision in phase 1). Host-installed, already trusted. No iframe overhead.
- **Tier 3 (in-process Python)** → direct DOM injection of HTML returned by
  the panel's `view()` function. Same trust story as tier 1.
- **Tier 2 (subprocess)** → iframe to `/panels/<name>/view` so the
  browser-side render boundary matches the server-side process boundary.
  Reserved, not yet implemented.

**The panel author writes the same code regardless of tier.** A unified
`harness.*` JS API is exposed in both render paths: synchronously in trusted
direct-DOM panels, proxied over postMessage in iframe panels. Promoting a
tier-1 panel to tier 0 (publish at a URL) is a deploy change, not a rewrite.

**Panel content vs. panel chrome** (titlebar, refresh button, close, future
drag handles) is split: panels render *content only*; the loader wraps every
panel in a chrome shell. This means future window-manager features land
without touching panels.

Polling/refresh is the panel's own job — usually a `meta refresh` or a tiny
inline `<script setInterval>`. The harness loader stays dumb.

### Tier (security/trust)

| Tier | Execution                          | Who can install              |
|------|------------------------------------|------------------------------|
| 0    | Browser iframe to external URL     | Anyone (URL allowlist later) |
| 1    | Browser iframe to host-served HTML | Host                         |
| 2    | Subprocess (deferred)              | Host (later: medium-trust)   |
| 3    | In-process Python                  | Host                         |

### Manifest (`panel.json`)

Source of truth for one panel. Schema v1 — bump the `schema` field if we
break-change.

```json
{
  "schema": 1,
  "name": "ollama_ps",
  "title": "GPU / CPU split",
  "tier": 3,
  "author": "host",
  "handler": "server:view",
  "url": null,
  "view": null,
  "permissions": {
    "network": ["http://localhost:11434"],
    "reads": [],
    "writes": []
  },
  "display": {
    "shape": "free",
    "aspect_ratio": null,
    "min":       { "width": 200, "height": 150 },
    "preferred": { "width": 400, "height": 300 },
    "max":       { "width": null, "height": null },
    "scroll": "internal",
    "growable": true
  },
  "iframe_attrs": {
    "sandbox": "allow-scripts allow-same-origin",
    "allow": ""
  },
  "anchored": false
}
```

**Field semantics:**

- `name` — slug, must match folder name, `[a-z0-9_-]+`.
- `tier` — 0/1/3 in v1.
- `url` — required for tier 0, must be `null` for others.
- `view` — file path relative to panel dir, required for tier 1, `null` for others.
- `handler` — `module:function` reference, required for tier 3, `null` for others.
- `permissions` — display only in v1 (no enforcement); shown at install time.
- `display` — declarative constraints/hints, **never absolute pixel slots**.
  See "Sizing model" below for how these feed the runtime.
- `iframe_attrs` — passed straight through to the rendered `<iframe>`. Default
  to most-restrictive sandbox; panels opt in to capabilities.
- `anchored` — when `true`, the panel cannot be removed or moved by layout
  edits. Used for permanently-placed UI (chat, settings, file tree). The
  layout config can still position an anchored panel into a specific region,
  but cannot omit it or place it where the manifest's `anchored` flag would
  be violated. Anchored is not the same as `hidden: true` in
  `mode_overrides` — anchored = "always present"; hidden = "this mode hides
  it." A non-anchored panel can be both visible and movable.

### Sizing model

A panel author cannot know the host's layout grid, viewport size, or sibling
panels — especially when panels come from strangers. So the manifest **never
specifies an absolute size**. Instead, three inputs feed a runtime sizing
function:

1. **Manifest `display` constraints** — what the panel needs to be readable.
   - `shape` — `wide | tall | square | free`. A hint about content's natural
     orientation; the layout uses it to pick a sensible slot.
   - `aspect_ratio` — `"16:9"`, `"1:1"`, or `null`. Locks the iframe's ratio
     when set; ignored when null.
   - `min` / `preferred` / `max` — pixel hints, not promises. Loader treats
     them as fluid bounds for the real CSS.
   - `scroll` — `internal` (panel scrolls inside its frame) or `grow` (panel
     wants the frame to grow with content).
   - `growable` — whether the panel can ask the parent for more space at
     runtime (see input 3).

2. **Layout slot policy** — `layout/panel_layout.json` declares per-slot
   rules: total column width budget, whether the slot honors grow requests,
   max-grow ceiling. The slot's dimensions come from the viewport and the
   layout config — never from the manifest.

3. **Runtime panel measurement** — the loader injects a tiny shim into every
   tier-1/3 panel (and offers it to tier-0 panels that opt in via
   postMessage). The shim runs `ResizeObserver` on `document.body` and
   `postMessage`s actual content size up to the parent. Parent reconciles:
   does the panel want more space? Does the slot allow it? Does the
   manifest's `growable` permit it? Grant or deny.

The dimension fields in the manifest **never become CSS values directly** —
they're inputs to a sizing function the loader owns. This means phase 1–4 can
ship with a dumb sizing function ("always render at preferred dimensions,
internal scroll") and the full three-way negotiation can land later without
changing any existing panel manifests.

**For the YouTube case**: panel declares `aspect_ratio: "16:9"`, `growable:
true`. When playback starts, the panel postMessages a grow request; the
parent checks slot policy and grants. No code in the panel knows or cares
about the host's grid.

### Layout config (`layout/panel_layout.json`)

**Separate from any panel.** This is what Claude Code edits to move things
around. Lives at the repo root under `config/` (new dir) so it's per-checkout,
not per-user-home.

The viewport is divided into **named regions**; panels are placed into
regions. The layout file fully owns the viewport — no hardcoded columns in
HTML survive migration. Regions are first-class so other code (including
Claude Code itself) can reason about UI structure declaratively.

```json
{
  "schema": 1,
  "regions": [
    { "id": "left",        "anchor": "left",   "width": "30%",  "stack": "vertical" },
    { "id": "middle",      "anchor": "center", "width": "35%",  "stack": "vertical" },
    { "id": "context",     "anchor": "center", "width": "0%",   "stack": "vertical", "after": "middle" },
    { "id": "right",       "anchor": "right",  "width": "35%",  "stack": "vertical" }
  ],
  "instances": [
    { "instance": "chat",             "panel": "chat",             "region": "left",    "anchored": true },
    { "instance": "activity",         "panel": "activity",         "region": "middle",  "anchored": true },
    { "instance": "settings_model",   "panel": "settings_model",   "region": "middle",  "anchored": true },
    { "instance": "settings_custom",  "panel": "settings_custom",  "region": "middle",  "anchored": true },
    { "instance": "task_list",        "panel": "task_list",        "region": "context", "grow": false },
    { "instance": "in_context_files", "panel": "in_context_files", "region": "context", "grow": false },
    { "instance": "ollama_ps",        "panel": "ollama_ps",        "region": "right",   "grow": false },
    { "instance": "files",            "panel": "files",            "region": "right",   "anchored": true },
    { "instance": "blog_ops",         "panel": "blog_ops",         "region": "right",   "grow": true, "grow_max_height": 800 }
  ],
  "mode_overrides": {
    "blog": {
      "regions": { "context": { "width": "0%", "hidden": true } }
    }
  }
}
```

**Region fields:**

- `id` — slug, referenced by `instances[].region`.
- `anchor` — `left | center | right` (or future: `top | bottom`). The
  viewport-level position.
- `width` — percentage (`"30%"`) or px (`"320px"`). For row-anchored
  regions, `height` instead. Layout engine resolves to actual pixels.
- `stack` — `vertical` | `horizontal` — how panels arrange within the
  region.
- `after` — optional sibling region this one sits after at the same anchor;
  lets a single anchor have multiple stacked regions (e.g. `middle` and
  `context` both center-anchored, `context` immediately after `middle`).

**Instance fields:**

- `instance` — unique id within the layout file. v1: usually equals the
  panel name. Multi-instance future: `yt-1`, `yt-2`.
- `panel` — name of the panel to render.
- `region` — which region to place it into.
- `anchored` — must match the panel manifest's anchored flag if the panel
  declares one. A panel that declares `anchored: true` may not be omitted
  from the layout (validation error at load time).
- `grow`, `grow_max_height`, `grow_max_width` — slot-level grow policy.
- `config` — optional per-instance config blob, passed to the panel at
  render time. Reserved for multi-instance: `{ "video": "..." }` for a
  YouTube panel.

`mode_overrides` covers per-mode UI changes — currently the existing
"blog mode hides the context column" behavior. Override targets either
`regions` (resize/hide whole regions) or `instances` (override per-panel
flags).

### Slot policy fields

In `layout/panel_layout.json`, each panel entry within a column accepts:

- `grow` — boolean. Slot honors panel grow requests.
- `grow_max_height` / `grow_max_width` — px ceilings on grow.
- `hidden` — boolean (used by `mode_overrides`).

Width and height of a panel at rest come from: viewport → column width budget
→ panel's `display.preferred` clamped by `display.min`/`display.max`. There
are no named sizes (`small`/`medium`/`large`); the runtime is fluid.

Fullscreen overlay mode is deferred to a later phase; when added, it'll be a
runtime mode (panel-requested via postMessage, granted by host policy), not a
size enum.

---

## Endpoints

- `GET  /api/panels` — registry: `[ {name, title, tier, size, ...} ]`
- `GET  /api/panels/<name>` — manifest + last-fetch status (debug)
- `POST /api/panels/reload` — rescan `panels/` dir
- `GET  /api/layout` — current `panel_layout.json`
- `POST /api/layout` — update layout (used by future drag-rearrange UI)
- `GET  /panels/<name>/view` — HTML body (tier 1/3)
- `GET  /panels/<name>/static/<file>` — panel-local assets

---

## Phased plan

### Phase 0 — Decisions (this file)

- [x] Manifest schema v1 frozen (with `display`, `anchored`)
- [x] Layout config schema v1 frozen (regions + instances)
- [x] URL conventions decided
- [x] Tier model + deferral decisions
- [x] Hybrid render path decided
- [x] UI placement decided (whole-screen region model)
- [x] Settings/chat/files etc. become anchored panels
- [x] Layout config location: `layout/panel_layout.json` at repo root
- [x] Hot reload: yes, day 1
- [x] `register_path PANELS_DIR` and `PANEL_LAYOUT_FILE`

### Phase 0.5 — Define the screen region grid

Before any panel migration, replace the hardcoded canvas in
[index.html](../static/index.html) with a region-driven shell that reads
`layout/panel_layout.json` and renders the regions. Initial region layout
should match the existing UI shape so nothing visually changes:

- `left` (30%) — currently the chat column
- `middle` (35%) — activity + settings + reference, stacked
- `context` (0% by default, 35% when not blog mode) — status column
- `right` (35%) — files + bot-ops + blog-ops

Initial `instances` list **just points each region at the existing
hardcoded sections by id** — no panel migration yet, no manifest files yet.
The shell is just CSS grid/flex driven by the layout config. Verify: app
renders identically to before, but the column markup is now generated from
config rather than hardcoded.

This phase is the first place where it's safe to delete a section of
`index.html` — once the shell renders the regions, the old top-level
`canvas` div is replaceable.

### Phase 1 — Loader skeleton ✓

- [x] `register_path PANELS_DIR` and `PANEL_LAYOUT_FILE`
- [x] Pydantic model for `PanelManifest` (panels/loader.py)
- [x] Pydantic model for `PanelLayout` (panels/loader.py)
- [x] `panels/loader.py` — discovery + registry + manifest validation
- [x] FastAPI routes mounted: `/api/panels`, `/api/panels/<name>`,
      `/api/panels/reload`, `/api/layout`, `/panels/<name>/view`,
      `/panels/<name>/static/<file>` (server.py)
- [x] **Verified:** `/api/panels` returns the registry (initially `[]`, now `[clock]`).

### Phase 2 — Tier 0 reference panel ✓

- [x] Built `panels/clock/` (tier 0, self-served from `panels/clock/static/clock.html`).
      Self-served because no need to take an external dependency for a reference.
      Still tier 0 — render path is iframe; origin is irrelevant to the trust model.
- [x] Client renderer in [static/panel-shell.js](../static/panel-shell.js):
      reads `/api/panels` + `/api/layout`, builds region grid, hoists legacy
      DOM and renders real panel instances as iframes with declared
      sandbox/allow attrs and preferred dimensions.
- [x] **Verified:** clock instance shows up in layout instances list and
      `/api/panels/clock` returns the manifest.

### Phase 3 — Tier 3 reference: GPU panel (THE original ask)

- [x] `panels/ollama_ps/` — tier 3, `server.py` with `fetch()` (parses
      `ollama ps` into per-model dicts) and `view()` (renders one bar per
      running model, refreshed via `harness.refresh('ollama_ps', 5)` instead
      of meta-refresh — see decisions log).
- [x] Tier-1/3 direct-DOM render path landed in `static/panel-shell.js`.
      Tier 0 still iframes to `manifest.url`; tier 1/3 fetch into a
      `.panel-content` wrapper. Scripts inside re-execute via the
      clone-and-replace trick (innerHTML alone won't run them).
- [x] `window.harness.refresh(name, seconds)` exposed as the first piece of
      the `harness.*` API. Self-deregisters when the panel content node
      goes away.
- [~] **Verify:** screenshot tool (Claude Preview MCP) hung repeatedly on
      the harness page. Verified instead via `preview_eval` DOM inspection:
      `[data-instance="ollama_ps"]` present (tier=3, h=260px),
      `.panel-content[data-panel-content="ollama_ps"]` populated with the
      view fragment (`.ollama-empty` "no models loaded" since none running),
      `window.harness.refresh` is a function. Re-screenshot when the
      preview MCP cooperates.

### Phase 4 — Migration: easy / unimportant panels first ✓

Migrate in this order. Each one validates a different aspect of the loader.
After each migration: visible UI parity, rip out the old hardcoded markup +
the now-dead JS, commit.

1. **`task_list`** (currently `#task-panel` in the context column).
   Tier 3. `server.py` reads `task.md` via existing helpers. Pure read.
   *Tests:* file-reading panels work end-to-end.

2. **`in_context_files`** (currently `#context-panel`).
   Tier 3. Reads ephemeral state — list of files the model has touched this
   turn. *Tests:* live-state panels (state owned by harness, not the panel).
   May need a small `harness_state` API exposed to tier-3 handlers — design
   note: pass a `request` arg to `view()` from which panels can `await
   request.app.state.context_files()`. Document this contract.

3. **`knowledge_packs`** (currently `info-panel/packs` section).
   Tier 3. Lists discovered packs + click-to-toggle. *Tests:* panels with
   interactive elements posting back to the harness — the panel's HTML calls
   `/api/packs/...` directly, harness routes are unchanged.

4. **`slash_commands`** (currently `info-panel/slash` section).
   Tier 3. Reads slash command list + edit. *Tests:* panels that mutate
   harness state via existing API endpoints.

After (1)–(4), the `info-panel` and the context column's `task-panel` are
gone from `index.html`. **Quality gate before continuing:** layout still
renders; blog mode still hides what it should; nothing in app.js calls
removed DOM ids.

### Phase 4.5 — Migration: anchored panels (chat, settings, files) ✓ (chat deferred)

These are anchored — always present, can't be removed by layout edits — and
they contain interactive controls that talk back to the harness. Migrate
once enough easy panels have shaken out the loader and the postMessage
bridge is built (so anchored panels with controls can use it).

- `chat` panel (the entire left column today) — biggest single panel,
  contains chat input + response + actions. Tier 3, anchored.
- `settings_model` and `settings_custom` — currently in the middle column.
  Tier 3, anchored. Contain all the toggles that mutate global harness
  state (model, temperature, history toggle, theme picker, etc.) — these
  use the `harness.*` API to read/write state.
- `files` — file tree in the right column. Tier 3, anchored.

**Migrating these is the inflection point**: after phase 4.5, the entire
top-level `index.html` body is generated from the layout config. The
hardcoded `canvas` markup is gone.

### Phase 5 — Migration: activity panels (medium difficulty) ✓

5. **`pending_writes`** (currently `#pending-panel`).
   Tier 3. Lists queued writes + approve/reject buttons. *Tests:* panels
   that need to refresh in response to WS events, not just on a timer. Solve
   by: WS message broadcasts a `panel_invalidate` event, panels listen via
   the `harness.subscribe(...)` API and refresh themselves. **First time we
   need the harness-event-subscription API — design it carefully here,
   document the contract.**

6. **`tool_log`** (currently `#tool-panel`).
   Tier 3. Live tool_call/tool_result stream. *Tests:* high-frequency update
   panels. Same event-subscription API as above.

### Phase 6 — Migration: heavy mode-specific panels ✓

8. **`bot_ops`** (currently `#bot-ops`).
   Big, has cross-panel signals (`#spectate-select`). Migrate as a single
   tier-3 panel that owns its three sub-sections internally — don't try to
   split into three loader-level panels until it's clear that's better.
   *Tests:* large panels with internal state and cross-section signals.

9. **`blog_ops`** (currently `#blog-ops`).
   Largest, most subpanels, most WS event types, mode-gated. Last because:
   any rough edges in the loader by now will have been smoothed. Probably
   one tier-3 panel with `mode_overrides` in layout config showing it only
   in blog mode.

### Phase 7 — Polish & forward-looking ✓ (partial)

- [ ] Install-time permissions display (CLI prompt for now): when the loader
      sees a new panel folder, dump its declared permissions and ask
      "approve y/N", store result in `panels/<name>/.installed.json`.
- [x] `panel-error` placeholder rendering when `view()` raises. server.py
      `_panel_error_html()` returns visible red-tinted error block with
      optional traceback `<details>` — no more silent failures.
- [x] Update [CLAUDE.md](../CLAUDE.md) with a "Panels" section describing
      the structure, where to add a new panel, and the dogfooded examples.
- [ ] Layout drag-rearrange UI (stretch — only if it pays for itself).
- [ ] Tier 2 subprocess panels (when needed).
- [ ] Dynamic resize via panel→harness postMessage (when needed).
- [ ] Auth/passphrase gate (only when shared-host mode is actually wanted).

### Outstanding work

#### Phase 8 — UI polish (next priority; the panels work but look rough)

The migration prioritized structural correctness over visual polish. The
panels render and behave correctly but the look is inconsistent in
several specific ways. Fix in this order:

- **Panel chrome is fragmented.** Each migrated handler reproduced the
  legacy header markup with its own ad-hoc class:
  `.tool-log-header`, `.files-panel-header`, `.pending-header-row`,
  `.bot-ops-chrome`, `.blog-ops-chrome`, `.pending-panel-chrome`,
  `.tool-log-chrome`. The original `.file-panel-header` was the single
  source of truth. Pick one shape and use it everywhere — probably
  refactor `panel-shell.js` to render the chrome itself (header with
  title + actions slot) so panel handlers return *content only*, per
  the trajectory section's "panel content vs. panel chrome" split.
- **Panel handlers shouldn't be writing headers at all.** Right now most
  do (`<div class="*-chrome"><div class="*-header">title</div>...`).
  The shell already renders a `.file-panel-header` from `manifest.title`
  in `renderPanelInstance`. So every migrated panel has TWO headers
  visible. Strip the duplicates from the handlers; let the shell own it.
  Add an `actions` mechanism (e.g. handler returns `{actions: [{label,
  onclick}], body: html}`) so refresh/clear buttons land in the
  shell-rendered header.
- **Settings bento is broken.** The `<div class="settings-grid">` inside
  `panels/settings/server.py` was originally a flex container giving
  side-by-side model + customization sections. After migration it's
  now inside `.panel-content` inside `.panel-instance-inner` with
  different parent constraints; the two sections likely stack vertically
  at the current width, defeating the bento. Either fix the grid CSS to
  hold up under the new parent chain, or accept the stack and rework
  the layout (this is the same reason the doc originally wanted it
  split into two panels).
- **`.panel-instance` styling is thinner than `.file-panel` was.** The
  shell sets `className = "panel-instance file-panel"` so the legacy
  glass background + radius apply, but the inner padding came from
  `.file-panel-inner` which the new wrapper doesn't have. Content
  abuts the panel edge in places. Add padding to `.panel-content`
  (or the shell-injected inner wrapper) once chrome ownership is
  unified.
- **Empty hidden divs litter `index.html`.** Stubs left from the
  migration: `<div id="activity-panel" hidden></div>`,
  `<div id="legacy-settings" hidden></div>`,
  `<div id="legacy-files" hidden></div>`,
  `<div id="blog-ops" hidden></div>`,
  `<div id="bot-ops" hidden></div>`. None are referenced any more —
  delete them. Same for the surrounding `<div>` wrappers in
  `#legacy-staging` that no longer wrap anything.
- **Mode visibility for context-region panels.** `task_list` and
  `in_context_files` were originally inside the context column which
  hid entirely in blog mode. The new panels live in `context` region
  which `mode_overrides.blog` collapses. Verify they actually disappear
  in blog mode (the layout override should handle it; double-check).
- **`.mode-hidden` race on first paint.** `app.js`'s `applyModeVisibility`
  runs once `selectedPrompt` resolves; before that, blog_ops flashes
  visible briefly in non-blog mode. Render layout-config-driven
  visibility in `panel-shell.js` *before* the panel content fetches.
- **Tool-log scroll position.** Was tied to a fixed-height parent
  before; now scrolls inside `.panel-content` which may resize
  differently. Verify auto-scroll-to-bottom on new tool entries still
  pins.
- **No visual differentiation between tier-0 / tier-1 / tier-3** in
  dev mode. Optional but useful: a tiny tier badge in the chrome
  during development, hideable.

#### Structural work still pending

- **chat panel migration.** The whole left column (textarea + response +
  WS lifecycle) is the only remaining `dom_id`'d legacy block. Risk:
  `app.js`'s `connect()` and the entire `ws.onmessage` switch reference
  `#chat-textbox`, `#response-text`, `#stop-btn` — moving them into a
  panel handler means either keeping `app.js`'s references valid via the
  hybrid pattern (panel renders chrome containing those IDs) OR
  refactoring `connect()` to be re-callable post-hydration. Hybrid is
  the safer first cut.
- **settings split.** Currently one `settings` panel (model + customization
  bento). Doc originally specified `settings_model` + `settings_custom`.
  Blocked on v1 regions stacking vertically — splitting would stack them
  instead of the bento side-by-side. Add a `subgrid` region primitive
  (or horizontal sub-region) and revisit. Related to the bento-broken
  item under UI polish.
- **install-time permissions display.** Phase 7 polish item not done; v1
  is still the honor system.
- **Consolidate app.js handlers into their panels.** Several existing
  app.js handlers (`addToolEntry`, `updateToolResult`, `clearToolPanel`,
  `refreshPacks`, `renderPackChips`, `togglePack`, `refreshTree`,
  `bindSettingsControls`, etc.) still live globally rather than in
  their panels. Hybrid pattern works but means panel internals leak
  into a shared `app.js`. Consolidate when the chat migration forces
  a broader app.js cleanup. Once done, `panel-shell.js`'s
  `harness._dispatch` becomes the only WS fan-out; the legacy `switch`
  in `app.js` shrinks to "connection lifecycle" only.

---

## Migration risk register

| Panel             | Risk                                                   | Mitigation                                                    |
|-------------------|--------------------------------------------------------|---------------------------------------------------------------|
| task_list         | Low — pure read, no live state                         | Migrate first                                                 |
| in_context_files  | Medium — needs harness state inside tier-3 handler     | Design `request.app.state` contract; document                 |
| knowledge_packs   | Low                                                    | Migrate after (1)(2)                                          |
| slash_commands    | Low                                                    | —                                                             |
| pending_writes    | High — first WS-driven panel                           | Postpone until 4/5 done; design postMessage bridge carefully  |
| tool_log          | High — high-frequency updates                          | Same as above                                                 |
| chat / settings / files (anchored) | Big surface, anchored, mutate global state | Migrate after postMessage bridge + harness.* API are stable    |
| bot_ops           | High — cross-panel `#spectate-select` signal           | Keep as single panel; don't split                             |
| blog_ops          | Highest — many subpanels, mode-gated, many WS types    | Migrate last                                                  |

---

## Resolved (was: open questions)

All four resolved 2026-05-09. See decisions log for full reasoning.

1. **UI placement** → **whole-screen region model**, replacing as we migrate.
   The viewport is divided into named regions declared in
   `layout/panel_layout.json`; panels render into regions; existing
   hardcoded sections in [index.html](../static/index.html) get replaced
   region-by-region. **First implementation step (new phase 0.5) is defining
   the region grid before any panel migration.**
2. **Settings panels** → **in**, as anchored tier-3 panels. Everything in the
   harness UI is a panel except notifications and other "system" pieces.
   Settings, chat, file tree etc. all become panels with
   `anchored: true` (cannot be removed/moved by layout edits, always
   present). Permanently placed UI is just a panel marked anchored.
3. **Layout config location** → **repo root under `config/`** for v1. Move
   to user-home only when shared-host mode actually arrives; doing it
   prematurely adds path-resolution + first-run-copy + multi-user-schema
   complexity for no v1 benefit.
4. **Hot reload** → **yes, day 1**, with security notes (below).

### Hot reload security notes

`POST /api/panels/reload` re-imports tier-3 panels' `server.py` files. In
single-user local mode this is fine — same trust as the rest of the
process. Flags for the future:

- **Localhost-only.** Already true since the whole server binds 127.0.0.1.
  When/if shared-host mode arrives, the reload endpoint must be host-only
  (auth required), not exposed to connected guests.
- **File-write-to-`panels/`-becomes-RCE-on-reload.** True today, fine
  because only the user can write to their own filesystem. Matters once
  guests can install panels in shared-host mode — at that point,
  guest-installed code panels (tier 2 when it lands) reload to subprocess
  isolation, not in-process.
- **In-flight panel state survives reload only via `panels/<name>/state.json`.**
  Don't rely on Python module-level globals to persist across reloads —
  they get blown away. Document this in the panel-author guide.

---

## Decisions log (append as we go)

- **2026-05-09** — Manifest schema v1 frozen as above.
- **2026-05-09** — Tier 2 deferred. Tiers 0/1/3 in v1.
- **2026-05-09** — Polling not push for refresh. Each panel owns its cadence.
- **2026-05-09** — Iframe is the universal display primitive. Even tier-3
  panels render inside iframes. Browser sandbox is the trust boundary.
- **2026-05-09** — Layout is a separate config file, edited independently of
  panels, designed for Claude-Code-driven rearrangement.
- **2026-05-09** — Manifest dimensions are **declarative constraints, not
  absolute sizes**. Replaced `size: { default, supported }` with `display:
  { shape, aspect_ratio, min, preferred, max, scroll, growable }`. Real
  rendering size is computed by the runtime from three inputs: manifest
  constraints, layout slot policy, and runtime panel measurement
  (postMessage from a `ResizeObserver` shim). Reasoning: a panel author —
  especially a stranger — cannot know the host's layout grid, so they can
  only describe their content's needs, not pick a slot. Phase 1–4 ships
  with a dumb sizing function (preferred + internal scroll); the
  three-way negotiation lands later without breaking existing manifests.
- **2026-05-09** — **Hybrid render**: tier 0 (external URL) and future tier 2
  (subprocess) render in iframes; tier 1 (host-served HTML) and tier 3
  (in-process Python) render via direct DOM injection. Trade: two render
  paths in the loader, in exchange for not paying iframe overhead on the
  trusted-host majority. Mitigated by a single `harness.*` JS API surface
  exposed identically in both paths (synchronous in DOM panels, postMessage
  proxy in iframe panels) — panel authors write one code regardless of tier.
- **2026-05-09** — **Trajectory framing: hub/OS-shaped UI for varied apps**,
  not just a coding-harness dev panel. v1 builds the panel spine, but
  designs against multi-instance panels, an "app" concept above panels,
  floating-window layouts, and notifications. Concrete v1 carve-outs:
  layout schema uses `instance` keys (singletons today, multi-instance
  later); panel chrome is loader-owned (windowing-friendly); panels render
  into a bounding rect (tile today, float tomorrow); config keys are
  panel-shaped not widget-shaped (apps slot in alongside).
- **2026-05-09** — **Whole-screen region model** for layout. The viewport is
  divided into named regions declared in `layout/panel_layout.json`; panels
  are placed into regions; the layout file fully owns viewport structure
  with no hardcoded columns. Phase 0.5 builds the region shell before any
  panel migration starts.
- **2026-05-09** — **Everything in the UI is a panel** except notifications
  and other "system" pieces. Settings, chat, file tree, etc. become tier-3
  panels with `anchored: true` (cannot be removed/moved by layout edits).
  Phase 4.5 migrates these after the easy panels (4.1–4.4) shake out the
  loader and the postMessage bridge.
- **2026-05-09** — **Hot reload** (`POST /api/panels/reload`) ships day 1.
  Security boundary is "localhost-only, single-user trust" — fine for v1.
  Future shared-host mode requires host-only auth on the endpoint and
  subprocess isolation (tier 2) for guest-installed code panels. Module-
  level globals don't survive reload; panels persist via state.json.
- **2026-05-09** — **Layout config stays at repo root** (`layout/panel_layout.json`)
  for v1. Move to `~/.harness/` when shared-host mode actually arrives —
  premature relocation adds path-resolution + first-run-copy + multi-user-
  schema complexity for no v1 benefit.
- **2026-05-09** — **Host CSS wins over panel CSS** for tier-1/3 direct-DOM
  panels. We deliberately do NOT use shadow DOM, because shadow DOM would
  isolate panel CSS *from* the host (the host couldn't reach in to enforce
  consistent look-and-feel). Instead, panels render into a
  `.panel-content[data-panel-content="<name>"]` wrapper; host CSS targets
  that wrapper and outranks panel CSS by load order + specificity. The
  trust story is unchanged — CSS isolation is not a security boundary, and
  tier 3 already has unrestricted in-process Python so direct-DOM tier 1/3
  cannot lower the bar. Shared-host mode is the inflection point: guest
  panels would need iframe (tier 1) + subprocess (tier 2) regardless of
  rendering choice. Loader has a comment to this effect.
- **2026-05-09** — **`harness.refresh(name, seconds)` is the v1 polling
  primitive** for tier-1/3 panels. Replaces the originally-planned
  meta-refresh (which only worked when panels were iframes). The panel's
  inline script calls it; the host runs the interval, fetches
  `/panels/<name>/view`, and replaces innerHTML (re-running scripts via
  clone-and-replace). Self-deregisters when the content node disappears.
  This is the first piece of the `harness.*` API surface promised in the
  trajectory section. Future iframe panels will get the same call shape via
  postMessage proxy.
- **2026-05-09** — **Tier-3 panels reach harness state via `import server`**
  (or `import tools`, etc.) at call time. The loader re-imports the
  handler module on every request, so module-level reads like
  `server.project_dir` are always live. No `request.app.state` plumbing
  needed in v1. Document this as the contract — when shared-host mode
  arrives and tier-3 untrusted panels can't be allowed direct module
  access, swap to a thin `harness_state` facade.
- **2026-05-09** — **`harness.subscribe(panel, event, callback)` is the WS
  event bridge.** Subscriptions are keyed by panel name so `fetchAndInject`
  can wipe stale subs on every re-render via `harness._clearPanelSubs(panel)`
  — no listener leaks. `app.js`'s `ws.onmessage` calls
  `harness._dispatch(msg.type, msg)` *after* the legacy switch, so existing
  handlers always run first and panels are additive. Companion
  `harness.refreshNow(name)` for one-shot fetches in event handlers.
- **2026-05-09** — **Settings is one panel, not two.** Doc originally
  specified `settings_model` + `settings_custom`. v1 regions stack
  vertically; splitting into two panels would stack the bento sections
  instead of placing them side-by-side. One `settings` panel ships now;
  split when a horizontal sub-region or `subgrid` primitive lands.
- **2026-05-09** — **Hybrid pattern for legacy-glue panels.** Panels like
  `tool_log`, `pending_writes`, `knowledge_packs`, `files`, and the big
  `bot_ops`/`blog_ops` panels render their chrome plus the legacy DOM ids
  the existing app.js handlers expect. Inline scripts kick the relevant
  initializer (`refreshTree`, `refreshSpectateSessions`, etc.) on
  hydration. Lets us migrate the layout without rewriting all of app.js
  in one pass — the panels OWN the markup; the JS migration is incremental.
  Null-guarded all the global handlers that touch removed legacy ids so
  they no-op gracefully during the hybrid era.
- **2026-05-09** — **Per-instance mode visibility moves to
  `[data-instance="..."]` selectors.** `app.js`'s `_PANEL_HIDE_RULES`
  used legacy DOM ids (`#blog-ops`, `#bot-ops`); rewritten to query
  `[data-instance="..."]` so it works for real panel instances (the
  panel-shell wrapper sets that attribute). Layout-config
  `mode_overrides` covers region-level + simple instance-hidden cases;
  `_PANEL_HIDE_RULES` covers the more expressive "show only in mode X"
  pattern that the layout schema doesn't yet support.
- **2026-05-09** — **`chat` panel migration deferred.** The entire left
  column (textarea + response box + WS lifecycle in `connect()`) was left
  as the last `dom_id` legacy instance. Migrating it without iterative
  human verification risks breaking the harness's main loop (the WS
  handler that drives every other panel). Will be the first thing to
  pick up next session.
