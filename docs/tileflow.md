# tileflow.md — dynamic bento engine

Living design doc for Tileflow: the layout engine that sits on top of the
panel system and decides, at runtime, where each panel goes and how big it
is. Keep this current as we build.

The panel system gives us **what** to render. Tileflow decides **how to
arrange it**. The two are deliberately separate — a panel author never
reasons about the bento; a layout engine never reasons about how a panel
talks to the harness.

---

## Vision

A Stage-Manager-style bento where:

- Only `chat` is anchored. Everything else floats in a free bento area.
- Active panels (e.g. a YouTube video playing) bubble up to a hero slot.
- Dormant panels (e.g. bot_ops with no game running) demote themselves to
  icons in a tray docked to the right edge.
- The user can lock a base layout via settings; runtime states modify it
  on top, but never below the user's pinned floors.
- Panels never overflow the viewport, never render below their declared
  `min` size, never crowd each other.

The ruleset is fully configurable: which panels can bubble, which can
demote, what counts as "active," what the tray looks like, what size
classes mean.

---

## Vocabulary

- **Panel** — a folder under `panels/<name>/`. Defined by the panel system;
  Tileflow doesn't redefine it.
- **Instance** — one rendered copy of a panel. Tileflow operates per
  instance, not per panel definition. Two YouTube panels = two independent
  Tileflow citizens.
- **Region** — a named layout slot from the panel system
  (`layout/panel_layout.json`). Tileflow inherits regions but treats
  most as part of one big bento.
- **Bento** — the free-arrangement region(s) where panels float. Today
  that's `right` (and we'll likely fold `middle` in too once chat is
  the only anchored thing).
- **Tray** — a new region docked to the right viewport edge, fixed
  narrow width. Stacks dormant panels as icons.
- **Hero slot** — a runtime designation for the top-left N×M cells. A
  focused/active panel claims it; auto-flow respects the claim.
- **Cell** — one slot in the bento grid. The grid has a fixed column
  count (12) and `row_height_px`. A panel claims one or more cells via
  its `(col, row, cols, rows)` rectangle.
- **Pin** — a user- or layout-committed `(col, row, cols, rows)` for an
  instance, persisted in `panel_layout.json`. Pins are absolute coords
  on the bento grid; unpinned instances auto-flow into unclaimed cells.
- **Floor** — a state or size-class minimum the runtime cannot drop
  below. Set by user pin (Stage 3) or per-instance `locked_floor`.

---

## Panel state model

Every instance has a Tileflow state:

| State      | Meaning                                                    |
|------------|------------------------------------------------------------|
| `dormant`  | Nothing meaningful happening. Demote to tray as icon.      |
| `idle`     | Visible in bento, normal size class. Default for most.     |
| `active`   | Doing something the user might glance at (background).     |
| `focused`  | Claims hero slot. The thing the user is paying attention to.|

State sources, in priority order:

1. **Explicit JS call**: `harness.setState('youtube_a', 'focused')`. The
   panel's own script decides — YouTube goes `active` when its iframe
   src is set, `focused` when the user clicks fullscreen, `dormant` if
   the player is idle for N seconds.
2. **Manifest `default_state`**: a panel that should boot dormant
   (bot_ops with no current game) declares it. Loader sets the state on
   first render before any JS runs.
3. **Mode override**: per harness mode, force a state on an instance
   (e.g. blog mode forces `bot_ops` to `dormant`).
4. **User pin**: settings can lock a state floor ("settings is always at
   least `idle`, never demoted to tray"). This is a floor, not an
   override — runtime can still raise above.

The effective state is the max of all sources by priority `1 > 2 > 3`,
clamped to the user's floor from `4`.

---

## Configurability axes

Tileflow is opinionated by default but every rule is opt-in/out at the
right level. Three configuration surfaces:

### Per-panel manifest (`panel.json`)

```jsonc
{
  // ... standard panel fields ...
  "tileflow": {
    "default_state": "idle",          // dormant|idle|active|focused
    "tray_when_dormant": true,        // demote to tray icon when dormant
    "bubble_on_active": false,        // claim hero on active (media panels: yes; tools: no)
    "bubble_on_focused": true,        // claim hero on focused (almost always yes)
    "icon": "▶",                      // tray glyph (emoji / single char / svg path)
    "size_classes": {                 // override default size-class mapping
      "dormant": "icon",
      "idle":    "small",
      "active":  "medium",
      "focused": "hero"
    }
  }
}
```

Defaults if `tileflow` block is absent:
`{ default_state: "idle", tray_when_dormant: true, bubble_on_active: false,
   bubble_on_focused: true, icon: <first char of title>,
   size_classes: { dormant: "icon", idle: "small", active: "medium", focused: "hero" } }`

### Per-instance layout (`layout/panel_layout.json`)

```jsonc
{
  "instance": "settings",
  "panel": "settings",
  "region": "bento",
  "tileflow": {
    "locked_floor": "idle",           // never demote below this state
    "locked_size": "medium",          // floor for size class
    "never_dormant": true,            // dormant requests are clamped to idle
    "pinned_position": "top-right"    // optional anchor inside bento (auto if null)
  }
}
```

### Mode overrides (`mode_overrides`)

```jsonc
{
  "some_mode": {
    "tileflow": {
      "instances": {
        "bot_ops":   { "force_state": "dormant" },
        "task_list": { "force_state": "focused" }
      }
    }
  }
}
```

---

## The grid (schema v2)

The bento is a **fixed-column** CSS Grid: 12 columns wide, each row a
fixed `row_height_px`, `grid-auto-flow: dense`. Fixed column count is
the design choice that makes coordinates meaningful — at any viewport
width above the 12→6 breakpoint, "col 3" means the same place.

```css
.region-kind-bento {
  display: grid;
  grid-template-columns: repeat(12, 1fr);
  grid-auto-rows: 120px;
  grid-auto-flow: dense;
  gap: 12px;
}
```

A panel claims cells via its rectangle:

```
grid-column: <col> / span <cols>;
grid-row:    <row> / span <rows>;
```

Coords are 1-indexed (CSS Grid convention). `(1, 1)` is top-left.

### Size classes → spans

Size classes are the abstraction between panel state and cells claimed.
On the 12-col grid:

| Class    | Span (cols × rows) | Notes                            |
|----------|--------------------|----------------------------------|
| `icon`   | n/a — renders in tray | no bento cell                 |
| `small`  | 3 × 1              | quarter-width tile               |
| `medium` | 6 × 1              | half-width tile                  |
| `large`  | 6 × 2              | half-width, double-tall          |
| `hero`   | 12 × 2             | full-width, double-tall          |

Below 1200px viewport, the grid drops to 6 columns and span values are
halved (`small: 2×1`, `medium: 3×1`, `hero: 6×2`). Pinned coords are
discarded at that breakpoint — narrow viewports get auto-flow only.
This is intentional simplicity for v1; per-breakpoint pin variants can
land later if needed.

`display.min.width` is enforced as the cell's `min-width`. If a panel's
`min` can't fit in its assigned span at the current cell width, the
engine bumps the span up before squashing — never below `min`.

---

## Layout sheet (schema v2)

Schema v2 of `layout/panel_layout.json` adds a `grid` block and an
optional `pin` per instance. The backend is the **authoritative source
of truth for pinned positions**; the client computes everything else
(auto-flow, runtime state overrides) on top.

```jsonc
{
  "schema": 2,
  "grid": {
    "cols": 12,
    "row_height_px": 120,
    "gap_px": 12,
    "narrow_breakpoint_px": 1200    // below this, halve cols and drop pins
  },
  "regions": [
    { "id": "left",  "anchor": "left",       "width": "30%",  "kind": "stack" },
    { "id": "bento", "anchor": "center",     "width": "auto", "kind": "bento" },
    { "id": "tray",  "anchor": "right-edge", "width": "52px", "kind": "tray"  }
  ],
  "instances": [
    {
      "instance": "youtube_a",
      "panel": "youtube",
      "region": "bento",
      "pin": { "col": 1, "row": 1, "cols": 6, "rows": 2 }
    },
    {
      "instance": "clock",
      "panel": "clock",
      "region": "bento"
      // no pin → auto-flow
    }
  ]
}
```

Schema v1 layouts continue to load (no `grid` block, no `bento` region,
no `pin` fields). The migration is per-region: a region keeps stacking
behavior until it changes `kind` to `bento`.

### Backend validation

On `POST /api/layout` (and `POST /api/layout/instances/:id/pin`), the
backend rejects any pin that:

- **Out-of-bounds**: `col < 1`, `cols < 1`, `col + cols - 1 > grid.cols`,
  or `row < 1`, `rows < 1`.
- **Collides** with another pinned instance's rectangle in the same
  region. Error names the conflicting instance.
- **Min-size violation**: `(grid_cell_width × cols) < panel.display.min.width`.
  Engine could bump the span instead, but at write time we reject — the
  caller (drag-to-pin UI) gets a clear "your span is too narrow" signal.
- **Span exceeds size-class max** for the panel's declared
  `tileflow.size_classes`.

Auto-flow placement is **not** computed server-side. The client uses
`grid-auto-flow: dense` to fill unclaimed cells with unpinned panels.
Backend stores intent; CSS Grid resolves it.

### Runtime overlay

Persisted layout = floor. State changes are a transient client overlay.

- `harness.setState('youtube_a', 'focused')` → client renders that
  instance with the `focused` size-class span at its pinned `(col, row)`
  origin (or claims `(1, 1)` if unpinned).
- The backend layout file does **not** change. State is per-session.
- `focused` collisions resolve by **priority** (`tileflow.priority: int`,
  default 0; higher wins). Loser falls back to its previous state's span.

This split keeps the layout file stable across sessions — runtime
volatility doesn't churn it — and means "what does the layout look
like" has one authoritative answer (`GET /api/layout`).

### API additions

| Method | URL                                       | Purpose                                  |
|--------|-------------------------------------------|------------------------------------------|
| GET    | `/api/layout`                             | Current schema-v2 layout                 |
| PUT    | `/api/layout`                             | Replace whole sheet (validated)          |
| POST   | `/api/layout/instances/:id/pin`           | Pin one instance: `{col, row, cols, rows}` |
| DELETE | `/api/layout/instances/:id/pin`           | Unpin one instance (back to auto-flow)   |

`POST /api/layout/instances/:id/pin` is the friendly path for Stage 3
drag-to-pin. It calls into the same validator as `PUT /api/layout`
under the hood.

---

## Tray

A new region, anchor `right-edge`, fixed width (~48px), vertical stack.
Lives outside the bento grid as a sibling region. Renders only icons.

Behavior:

- When an instance enters `dormant` and `tray_when_dormant: true`, it
  unmounts from the bento and mounts an icon button into the tray.
- Click the icon → set state to `idle` → re-enter the bento at its
  size-class slot (auto-placed unless `pinned_position`).
- Tray icons get the `manifest.tileflow.icon` value, falling back to the
  first character of `manifest.title`. Tooltip = full title.
- Tray itself can be hidden via `mode_overrides` if a mode wants no
  dormant routing.

---

## Stages

Tileflow ships in three stages so each layer can be validated before the
next depends on it.

### Stage 1 — tray + dormant routing  *(complete 2026-05-10)*

Goal: validate the state-declaration API and the bento↔tray transition.

- [x] `harness.setState(instance, state)` + `getState` in
      [panel-shell.js](../static/panel-shell.js). Self-set guard prevents
      re-render loop when an inline script sets its own state during
      injection.
- [x] `manifest.tileflow.default_state` and `tileflow.icon` honored.
      Defaults via Pydantic model in [panels/loader.py](../panels/loader.py).
- [x] New region kind `tray` (anchor `right`, 52px column) in
      [layout/panel_layout.json](../layout/panel_layout.json).
- [x] Dormant routing: instance with `state === "dormant"` AND
      `tray_when_dormant: true` renders as an icon in the tray, NOT in
      its declared region.
- [x] Click tray icon → state flips to `idle` → instance re-mounts in
      its declared bento region.
- [x] Demo: `bot_ops` boots `dormant`, lives in tray, click → enters
      bento. (No game-state introspection yet — later signal.)

**Beyond original Stage 1 scope, also landed:**

- [x] Three more panels routed to tray on boot: `knowledge_packs` 📦,
      `slash_commands` /, alongside `bot_ops` 🤖 — multi-icon tray
      validates the abstraction beyond a sample size of one.
- [x] Standardized top-right **panel pill** (dummy ⚙ settings + −
      minimize-to-tray) on every `.panel-instance`. Shell-rendered in
      [panel-shell.js:buildPanelPill](../static/panel-shell.js); diagonal
      seam via skewed 1px `::after` in
      [style.css:.panel-pill](../static/style.css). Theme-aware via CSS
      custom properties.
- [x] Explicit `tileflow.icon` on all 14 panel manifests (📝 blog · 🤖
      bot_ops · 🕐 clock · 📁 files · 👁 in_context · 📦 knowledge_packs
      · 💻 ollama_ps · ✎ pending_writes · 🎛 settings · / slash_commands
      · ✓ task_list · 🛠 tool_log · 🌐 web · ▶ youtube). No more
      first-letter fallback collisions.
- [x] Event-driven setState wiring in two panels (forward-compat with
      Stage 2 hero behavior, no visible effect yet):
      `pending_writes` flips to `focused` while count > 0;
      `tool_log` flips to `active` on `tool_call`, back to `idle` on
      `done`/`error`.
- [x] `anchored` semantics clarified in
      [panels.md](panels.md) — means "mode_overrides cannot
      hide it", orthogonal to tileflow state. An anchored panel can
      still demote to the tray.
- [x] **Schema-v2 grid + pin spec drafted** (this doc, sections "The
      grid (schema v2)" and "Layout sheet (schema v2)"). Not yet
      implemented — that's Stage 2's first task.

Out of scope for stage 1: hero slot, size classes, reflow, user-locked
floors, motion polish. Bento stays vertically stacked as it is today.

### Stage 2 — fixed-grid bento + size classes + pins  *(complete 2026-05-12; row-span height floor added 2026-07-06)*

- [x] Schema v2 layout file: `grid` block, `bento` region kind,
      optional `pin` per instance. Backwards-compatible with v1.
- [x] `loader.py`: extend `PanelLayout` Pydantic model with
      `GridConfig`, `InstancePin`. Schema v2 detection via the
      `schema` field.
- [x] CSS: `.region-kind-bento` as a 12-col CSS Grid with
      `grid-auto-flow: dense`. Narrow-viewport breakpoint to 6 cols.
      Row tracks use `minmax(row_height, auto)` so over-tall content
      doesn't silently overflow into the next row; `align-items: start`
      on the region and `align-self: start` on `.panel-instance` keep
      each tile at its natural height.
- [x] `panel-shell.js`: render pinned instances with `grid-column`/
      `grid-row` from their pin; let unpinned auto-flow.
- [x] Size-class → span table from manifest's `tileflow.size_classes`,
      defaulted per the table above. State change re-applies span.
- [x] `focused` claims hero span at pin origin (or `(1, 1)` if
      unpinned). Priority field present in schema (collision-resolution
      logic not exercised yet — needs two-panel demo).
- [x] Backend pin validator: out-of-bounds + collision checks landed in
      `validate_layout_pins` / `validate_pin_for_instance`. Min-size and
      size-class span checks still TODO (Stage 3 alongside drag-to-pin).
- [x] `POST /api/layout/instances/:id/pin` + `DELETE` endpoints.
- [x] CSS transitions on `grid-column`/`grid-row` for smooth resize.
- [x] Migrate `middle` + `context` + `right` regions into a single
      `bento` region (2026-05-12). Schema-v2 `panel_layout.json` with
      no pins — all panels auto-flow. Per-panel `size_classes.idle`
      overrides on `settings` (large), `files` (small), `youtube`
      (medium, with `bubble_on_active: true`), and `web` (medium) give
      the default arrangement reasonable proportions; further tuning
      will move to drag-to-pin in Stage 3.
- [x] FLIP runner (`runFlipReflow` in [panel-shell.js](../static/panel-shell.js))
      for smooth glide-to-fill. Captures rects of every `.panel-instance`
      and `.tray-icon`, applies the mutation immediately, then inverts
      transforms on every peer that moved and animates back to identity.
      Verified both bento↔tray (minimize/restore) and intra-bento
      (size-class change). Cleans up `transform`/`transition` styles on
      `transitionend` with a safety timeout. Browser-native
      `grid-column`/`grid-row` transitions removed from CSS — the FLIP
      runner is now the single motion path for layout changes.
- [ ] Demo: YouTube panel goes `focused` when video plays → claims
      hero. Other panels reflow around it. (Manifest now has
      `bubble_on_active: true`, but the player JS doesn't yet emit
      `setState('focused')` from a play event.)
- [x] **Runtime overlay + broadcast** (2026-05-12). Server-side
      `_tileflow_overlay: dict[str, str]` (in-memory) is the transient
      state-per-instance store; `_panel_ws: set[WebSocket]` tracks
      every connected tab. `broadcast_tileflow_state(instance, state)`
      pushes `{type: "tileflow_state", instance, state}` to all
      clients. Endpoints: `POST /api/tileflow/state/:id` (body
      `{state}`), `DELETE /api/tileflow/state/:id` (clears overlay and
      broadcasts `idle`). On WS connect the current overlay is
      replayed, so a tab refresh restores the live arrangement.
- [x] **`set_instance_state` model tool** (2026-05-12). Tool def in
      `tools/core.py` with `instance` + `state` args. The dispatch in
      `tools/core.py` just validates and returns a confirmation
      string; the side-effect broadcast happens in
      `server.py`'s tool-call site so the tool stays sync and so every
      connected client receives the frame (not just the chat WS).
      Client `panel-shell.js` boot installs a single subscriber on the
      synthetic `_shell` panel name that funnels `tileflow_state`
      frames into `harness.setState`, riding the existing FLIP path.

### Stage 3 — model-driven layout + user floors  *(core landed 2026-07-06 — tools, descriptor, floors, live re-sync; drag-to-pin UI still open)*

Goal: the user types "make this YouTube hero and tuck the rest" or
"pull settings up — I'm tweaking themes" into chat, and the local model
rearranges the bento. Drag-to-pin is the manual fallback; the primary
interaction is conversational.

The model gets a small, well-bounded toolset that mirrors the same
Stage-2 endpoints — every change goes through the existing pin
validator, so the model can't produce an invalid layout. Stage 3 also
introduces *user floors*: pins the user (or model) commits become the
floor for runtime rules — settings stays `medium` even if `dormant`
fires, because the floor said so.

#### Tools exposed to the local model  *(built 2026-07-06; consolidated 2026-07-06 into one `layout` tool)*

**Current surface: a single `layout` tool in `tools/core.py`, selected by
an `action` enum** — `get`, `panels`, `state`, `pin`, `unpin`, `floor`,
`recompute`, `preset_apply`, `preset_save`. One name in the deck instead of
nine keeps a small model's tool-selection attention sharp; the enum gives it
the full menu inside one schema. The pre-consolidation names below still
dispatch as compat aliases (old sessions, curl scripts) but no longer ship
in the model's tool list. Semantics per action are unchanged:

- `get` (was `get_layout()`) → full layout sheet (regions, instances, pins,
  per-instance tileflow blocks). What you'd get from `GET /api/layout`,
  but condensed for context: only the fields a model needs to reason
  about placement (no `mode_overrides`, no manifest mirrors). Includes each instance's *current* tileflow state
  (dormant/idle/active/focused) and resolved size class so the model
  has the runtime picture, not just the persisted floor.
- `panels` (was `get_panels()`) → registry of installed panels with their
  declared `display.min`, default size class, and one-line `title`. Lets the
  model answer "is there a clock panel?" and "how small can it go?".
- `pin` (was `pin_instance(instance, col, row, cols, rows)`) → calls
  `POST /api/layout/instances/:id/pin`. Validator errors propagate
  back to the model verbatim ("collides with X", "your span is too
  narrow") so it can self-correct on the next turn.
- `unpin` (was `unpin_instance(instance)`) → `DELETE /api/layout/instances/:id/pin`.
- `state` (was `set_instance_state(instance, state)`) → server-side mirror of
  the client's `harness.setState`; broadcast over WS so the bento updates
  live in front of the user. Lets the model say "focus the YouTube
  panel for ten seconds" without needing the client to do anything.
- `floor` (was `set_instance_floor(instance, locked_size?, locked_floor?, never_dormant?)`)
  → writes per-instance `tileflow` block. The "I want settings to stay
  medium" knob.
- `preset_apply` (was `apply_layout_preset(name)`) → optional convenience:
  looks up a named preset under `layout/presets/<name>.json` and applies it
  as a bulk `PUT /api/layout`. Use case: the model says "I'll save this as
  `coding-mode`" and now the user can ask for that arrangement again
  later. Presets are just layout sheets; same validator gates them.
  (`preset_save` captures the current sheet; `recompute` forces a flow pass.)

The toolset is intentionally narrow — no "render this panel" or
"create a new instance" actions in stage 3. Layout is a closed
universe of installed panels and where they sit; richer actions
(install panel, reload, mode switch) are out of scope.

#### Reasoning support

The model needs context to make good calls. Two pieces of plumbing:

- **Layout descriptor** in the system prompt for any conversation
  where a layout-altering tool is available. Compact: viewport size
  (so it knows whether pins apply), grid dims, list of instances with
  current pin / state / size class. Refreshed on every turn so the
  model sees the live state, not a snapshot from session start.
- **Tool docstrings** spell out the size-class table and the rule that
  pins are floors, not absolutes (focused can still bubble above a
  pin's `cols`). The model gets one chance per turn to self-correct
  via validator errors; clear docs cut down on the avoidable round
  trips.

#### Drag-to-pin UI (manual fallback)

- [ ] Settings UI: drag panels onto bento cells. Drag drops emit
      `POST /api/layout/instances/:id/pin` — the same endpoint the
      model uses, so manual and conversational paths can't diverge.
- [ ] Reset-to-defaults command.

#### User floors (shared with model path)

- [x] Per-instance `tileflow.locked_floor` (state) and
      `tileflow.locked_size` (size class), persisted in the layout
      file. Honored by both the runtime state engine (state max-clamps
      to floor) and the size-class resolver (span lower-bounds to
      floor's class). *(landed 2026-07-06 — `flowPass` clamps state/
      class/tray-routing from the instance `tileflow` block; write via
      the `set_instance_floor` tool.)*
- [x] User layout = floor for runtime rules. Panel can grow above
      floor (active → bigger) but never shrink below ("settings stays
      medium even if you mark it dormant"). Same rule for both pinning
      paths: when the model writes a pin, that pin is the floor for
      that instance until it's unpinned. *(landed — pins were already
      floors in `applyDecision`; floored instances now also never tray.)*

---

## Style guide

The principles the engine encodes (some enforced, some advisory). Useful
when designing a new panel, tuning weights, or reasoning about why the
bento arranged itself the way it did.

### Layout rules (engine-enforced)

1. **12-fits-12.** Every visual row sums to exactly 12 columns. Allowed
   spans with current size classes: `12`, `6+6`, `6+3+3`, `3+3+3+3`.
   `grid-auto-flow: dense` + score-ordered `style.order` packs items so
   gaps backfill automatically.
2. **Heavy items first.** Score = ranking. Highest-scored panel gets the
   top-left cell; dense packing fills around it. State-driven promotions
   (focused +8, active +3) lift a panel's rank above its idle peers.
3. **One hero at a time.** If two instances both want `focused`, the
   higher `score_overrides.priority` wins (priority is a flat additive
   on the score, so the higher-priority one ranks above the other).
   Today there's no explicit "loser falls back" logic — the loser just
   ranks lower and renders at its derived class.
4. **Tall must pair with tall.** A `large` (6×2) panel pairs naturally
   with another `large` or two `small+small` stacks. The score-based
   ordering generally produces this for free; if it doesn't, the
   eyesore is a sign to dial up `score_bonus` on the panel that
   should be alongside.
5. **Strict row tracks.** `grid-auto-rows` is a fixed `row_height_px`,
   not `minmax`. Panels clip to their declared row span; over-tall
   content scrolls internally via `.panel-content`. A panel that
   genuinely needs more rows declares a taller `preferred.height` so
   `naturalClass` derives a 2-row class (large/hero).

### Authoring discipline (manifest-level)

6. **Match size to natural shape.** Let the engine derive size class
   from `display.preferred`/`min`/`max` — don't second-guess by
   declaring overrides. The thresholds:
   - `preferred.width` rounded to nearest `{3, 6, 12}`-col bucket.
   - `preferred.height ≥ 1.5 × row_height_px` promotes one tier
     (small→medium→large).
   - `min.width` is a hard floor; the engine bumps the class up to
     satisfy it. So a `min.width: 400` panel can never render at
     `small` in a 91px-col grid.
   - `max.width` is a hard ceiling; the engine clamps down.
7. **Aspect-ratio respect.** Media panels (videos, images) should set
   `display.aspect_ratio` and use widths that map to even-col spans
   (`medium`/`large`/`hero`). `small` (3 cols) crops 16:9 content
   unpleasantly.
8. **State-class ceiling is per-panel via min/max.** A text widget
   that bubbles to `focused` shouldn't claim hero — give it
   `max.width: 600` and the engine clamps. The engine never violates
   min/max in either direction.
9. **Edge anchoring via pins.** Workspace-stable panels (settings,
   files, anything you'd consider "home") get a `pin` in
   `panel_layout.json` so they don't shuffle on every reflow. Pins
   are floors: state can grow the panel past the pin's `cols`/`rows`
   but never shrink the origin.
10. **Stickiness over optimality.** Don't dial in `score_bonus` to
    fight the engine's choices; if a panel keeps landing somewhere
    "wrong," the right fix is usually adjusting its `min`/`max` or
    its tier in the score table — not chasing the symptom per panel.

### Rationale: why CSS `order` not DOM reorder

CSS `order` reorders visually without moving elements in the DOM.
Critical because iframes (YouTube, web) reload if they move in the
DOM tree — `order` keeps a playing video alive across reflows.
Tradeoff: keyboard tab order and screen-reader order follow source
order, not visual order. Acceptable for a single-user personal
ecosystem where keyboard cycling across panels is a rare workflow.
If that ever becomes friction, add a `visualOrder()` helper and a
custom focus trap.

---

## Debug surface

Two cheap tools surface "why is this panel where it is?":

### `data-tileflow-*` attributes

Every `.panel-instance` and `.tray-icon` is stamped with:

```
data-tileflow-state="idle"        // current state
data-tileflow-class="large"       // effective size class
data-tileflow-score="6"           // engine score (signed)
data-tileflow-order="-6"          // applied as style.order
```

DevTools → Inspect Element → check the attribute pane. No need to
mentally reconcile source order with visual order — the score is right
there.

### `harness.tileflow.dump()`

JS console helper. Returns (and `console.table`-prints) a sorted
decision row for every tile in the bento and tray:

```js
harness.tileflow.dump()
// → [{instance, bin, state, cls, score, order, col, row}, ...] sorted by score desc
```

Useful for understanding ranking after a burst of `setState` calls or
a weight change. Combine with `harness.tileflow.WEIGHTS` to inspect
the current knob values, and `harness.tileflow.setWeights({...})` to
A/B-test new numbers without a reload.

### `harness.recomputeLayout()`

Force a fresh flow pass without changing state. Useful after
`setWeights()` (already wired automatically) or when content sizes
shifted underneath the engine and the recompute hook missed it.

---

## Up next — build docket

Ranked by impact for the personal-ecosystem trajectory. Pulled from the
scattered Stage 2/3 checklists + open questions so a fresh session has
one read for orientation. Update as items land.

1. ~~**YouTube play-event → `harness.setState(instance, 'focused')`**~~
   *(landed 2026-07-06)* — bigger than estimated: a bare `/embed/` iframe
   exposes no playback events, so the view now drives a `YT.Player` via the
   IFrame API (`host: youtube-nocookie.com`). PLAYING→focused,
   PAUSED→active, ENDED→idle; restored videos are *cued*, not auto-bubbled.

2. ~~**`get_layout` + `get_panels` model tools**~~ *(landed 2026-07-06,
   along with the full Stage 3 tool set)* — seven tools in
   `tools/core.py`: `get_layout`, `get_panels`, `pin_instance`,
   `unpin_instance`, `set_instance_floor`, `apply_layout_preset`,
   `save_layout_preset`. Mutations broadcast a new `layout_updated` WS
   frame; panel-shell re-syncs from `/api/layout` live. A compact
   `<layout>` descriptor now rides in every turn's system prompt
   (built by `panels/loader.py:layout_descriptor`). Note: any layout
   write through tools/endpoints canonicalizes `panel_layout.json`
   formatting (pydantic round-trip).

3. ~~**Iframe (tier-0) `setState` bridge via postMessage**~~ *(landed
   2026-07-06)* — host listener in panel-shell.js accepts
   `{type: "tileflow.setState", state}`; sender is identified by
   `contentWindow` so a panel can't spoof a sibling's state. See
   docs/panels.md § Tier-0 panels and tileflow state.

4. ~~**⚙ pill action**~~ *(landed 2026-07-06)* — popover with the four
   states; selections flow through `harness.setState` + `logInteraction`
   (`kind: "custom", act: "pill-state"`).

5. ~~**Sizing-floor bug: `min.height` is not honored.**~~ *(fixed
   2026-07-06)* — root cause was the class table capping rows at 2:
   settings (min 280px) already got its 2-row promotion but 2 rows = 252px
   of track. `flowPass` now grows the row span past the class table to
   satisfy `display.min.height` (`rowsCeilForMin`, MAX_ROWS=4). Settings
   renders 384px tall; the floor generalizes to any panel.

6. ~~**Content-aware flow: default-dormant + `signalContent`**~~ *(landed
   2026-07-06)* — most panels now ship `default_state: dormant` and earn
   their bento slot by having content; cold boot is chat + files + clock.
   New shell API `harness.signalContent(id, hasContent, {wake})` owns the
   wake/sleep loop (app.js WS handlers signal for the built-in content
   panels). User-minimize is *sticky*: content badges the tray icon
   (`.has-badge` dot) instead of re-opening; the tray-icon click clears
   both. tool_log re-renders from a session-long JS buffer on mount
   (client-push panels lose DOM in the tray — see panels.md caveat).
   Chat `grow: true` now fills the left region (shell honors the layout
   `grow` flag in stack regions). Tray icons are styled from style.css
   tokens (was inline cssText), and the tray scrolls when tall.

7. ~~**Tool-deck slim-down for small local models**~~ *(landed 2026-07-06)*
   — the nine layout/tileflow tools collapsed into one `layout` tool with an
   `action` enum (get/panels/state/pin/unpin/floor/recompute/preset_apply/
   preset_save); legacy names remain as dispatch-only compat aliases.
   `roll_dice` now ships only in game modes (`_GAME_MODES` in
   tools/__init__.py). DeetsCode deck: 24 → 15 tools. Also: register_path
   guarded to the harness root, null string args coerced, file tree capped
   at 8k chars in the system prompt, and the DeetsCode prompt examples
   rewritten as real JSON tool calls (the old pseudo-call notation with
   inline `\n` was teaching models to write literal backslash-n).

Lower-priority / can wait:
- Auto-demote by *time* ("idle for N minutes → dormant") — emptiness-based
  demote landed with signalContent; the timer variant is Stage 4+, if ever.
- Drag-to-pin UI (Stage 3) — model-driven layout via tool calls is
  the primary interaction; manual fallback isn't urgent.
- FLIP-on-resize for the breakpoint crossing — CSS Grid handles it
  cleanly enough today; revisit if it feels janky.

---

## Open questions

Things we haven't decided. Append answers here as we resolve them.

- ~~**What demotes a panel to dormant automatically?**~~ Resolved
  2026-07-06: *emptiness*, not time. `harness.signalContent(id, false)`
  returns auto-woken panels to the tray; time-based demote remains
  unbuilt (and may never be needed).
- ~~**Does the tray have a size cap?**~~ Resolved 2026-07-06: the tray
  region scrolls (`overflow-y: auto`, thin scrollbar) — no wrap, no cap.
- **Multi-instance in tray.** Two `youtube_a`/`youtube_b` both dormant —
  do they show distinct icons or merge? Probably distinct (per-instance
  identity matters); icon could append a small numeric badge if needed.
- **Hero slot collision.** ~~Two panels both go `focused` simultaneously —
  who wins?~~ Resolved: `tileflow.priority: int`, default 0, higher
  wins; loser falls back to its previous state's span. (Schema v2.)
- **`anchored` vs. tray-routing.** `anchored: true` today means "layout
  cannot move/hide it." But `bot_ops` is `anchored: true` AND
  `default_state: dormant + tray_when_dormant`. Proposal: `anchored`
  means "cannot be hidden by `mode_overrides`" only. Tray demotion is
  orthogonal — it's tileflow state, not layout omission.
- **Animation budget.** Reflow on every state change can feel noisy.
  Possibly debounce, or only animate "user-visible" transitions and
  snap for boot/mode-switch.
- **Tray on the left vs. right.** Right matches the user's first
  description; left matches "icons next to chat." Configurable per
  mode? Per user setting?
- ~~**Iframe panels (tier 0/2) and state.**~~ Resolved 2026-07-06: host
  postMessage listener accepts `{type: "tileflow.setState", state}` from
  any embedded panel; sender identified by `contentWindow`. See
  [panels.md § Tier-0 panels and tileflow state](panels.md#tier-0-panels-and-tileflow-state).

---

## Backlog / future ideas

Things we want to build but aren't in the active stage plan. Promote
into a stage when we're ready to commit.

- **Panel registry dashboard.** A Notion page (or in-repo notebook) that
  lists every panel and its details — icon, name, title, tier,
  declared `tileflow` block, size classes, default state, what events
  it subscribes to, what `setState` transitions it emits. Source of
  truth stays in each panel's `panel.json`; the dashboard is a
  generated/curated view on top so we can scan inventory at a glance,
  spot gaps (panels missing icons, panels with no bubble behavior
  declared, duplicated first-letters in titles, etc.), and pick which
  panels to ship the next bento polish pass on. Open question: build
  it as a generated table inside `docs/panels.md`, as a
  `panels` mode in the harness itself (a panel that lists panels),
  or as an external Notion sync. Lean toward in-harness — it's
  dogfooding and keeps the data live.

---

## UI polish — margins & motion

Two distinct concerns, scoped separately:

### Small polish pass — do before or alongside Stage 2 loader work

Cheap, no entanglement with the grid model:

- **More margin between panels.** Bump the bento region's `gap` (and
  the per-`.panel-instance` margin if needed) so panels breathe. CSS
  only; ~1-line change, low-risk.
- **Fade on minimize/restore.** The panel↔tray-icon swap today is a
  hard remove + append. A fade-out → replace → fade-in transition is
  ~30 lines and doesn't need to reason about grid reflow. Tray-icon
  scale-in (0.9 → 1) is a nice flourish in the same change. Bento
  panels still "snap" into the gap left behind — that's the part that
  needs Stage 2.

**Estimated cost: ~1 hour.** Worth doing soon — minimize/restore is
the most-exercised affordance in the bento right now and the
abruptness makes Stage 1 look less finished than it is.

### Buttery glide-to-fill — Stage 2 territory

When a panel disappears, the others smoothly slide to claim the space.
This needs Stage 2 because:

- CSS Grid does **not** natively animate `grid-column`/`grid-row` line
  changes. Setting `transition: grid-column 200ms` is a no-op.
- The fix is the **FLIP technique** — First, Last, Invert, Play:
  1. Measure each affected panel's `getBoundingClientRect()` *before*
     the layout change (First).
  2. Apply the change (Last position).
  3. Compute the delta and apply an inverse `transform` so each panel
     visually appears unmoved (Invert).
  4. Animate the transform back to identity (Play). Looks like a glide.
- This is the **same animation surface** Stage 2 needs for size-class
  transitions (`idle → focused` growing a panel from 6×1 to 12×2).
  Building one FLIP runner that handles both mount/unmount AND
  size-class span changes is much cleaner than building motion now
  against the flexbox stack and porting it later.
- Belongs in `panel-shell.js` as `runFlipReflow(beforeRects, mutate)`
  helper; called by `setState` and by Stage 2's pin-change paths.

**Why not now:** building motion against today's flexbox columns then
porting to grid means either two parallel systems for a while, or a
rewrite. Worse than waiting.

**Animation budget reminder.** From the existing open-questions list:
reflow on every state change can feel noisy. Likely policy: animate
user-initiated transitions (click minimize, click tray icon, click
focus) but snap for boot, mode-switch, and any state change marked
`{ animate: false }` on the setState call.

### Recommended order on resume

*(All four steps landed as of 2026-07-06 — schema v2, the CSS Grid bento,
and the FLIP runner are live. Kept for the design rationale above;
current priorities live in the build docket.)*

---

## Scoring + flow engine (2026-05-12)

Auto-arrange is driven by a per-instance score. Lives in
[static/tileflow-engine.js](../static/tileflow-engine.js) as a pure module
that exposes `window.harness.tileflow.{score, naturalClass, effectiveClass,
flowPass, WEIGHTS, setWeights, resetWeights}`. The shell calls `flowPass()`
on every `setState`, on boot, after a viewport resize, and on a
`tileflow_recompute` WS frame (driven by the `recompute_layout` model tool).

### Score formula

```
score(inst) =
    base_by_class[effective_class]      // hero 10, large 6, medium 4, small 2, icon −10
  + state_mod[state]                    // focused 8, active 3, idle 0, dormant −10
  + overrides.priority                  // signed, default 0
  + overrides.score_bonus               // signed, default 0
  + recencyBonus(last_state_change_at)  // 5 → 0 over recency_window_s (default 30)
  clamped by overrides.score_floor / score_ceiling
```

Score below `WEIGHTS.tray_threshold` (default −3) or `state === "dormant"`
routes the instance to the tray instead of the bento (provided
`tray_when_dormant !== false`).

### Sizing

Per-state size classes are **derived**, not declared. `naturalClass()` reads
`display.preferred`, `min`, and `max` from the manifest:

- `preferred.width` → rounded (it's a soft target) to nearest `{3, 6, 12}`-col bucket.
- `min.width` → `ceil` (hard floor). Promotes if preferred bucket would
  violate it.
- `max.width` → `floor` (hard ceiling). Demotes if preferred bucket would
  exceed it.
- `preferred.height ≥ 1.5 × row_height_px` → promote one tier (small→medium→large).

`effectiveClass()` then bumps the natural class by `floor(state_mod /
promotion_step)` tiers — `focused` (+8) promotes 2 tiers, `active` (+3)
promotes 1, `dormant` (−10) demotes 3.

### Per-instance overrides

Live on `LayoutInstance.score_overrides` in `panel_layout.json`
(see `panels/loader.py:InstanceScoreOverrides`):

- `priority`, `score_bonus` — flat additives (signed).
- `score_floor` (e.g. `100`) — keeps a panel pinned high regardless of state.
- `score_ceiling` (e.g. `−1000`) — banishes.
- `force_state` — clamps state regardless of overlay broadcasts.

User actions (drag, "always show this") and model tool calls write into
these — engine knobs themselves remain a single edit point.

### Tunable weights

`WEIGHTS` is the single source of truth for every dial. A future settings
panel will call `harness.tileflow.setWeights(partial)`; today the same API
is reachable from the JS console for tuning. Reflow on weight change is
hooked via `tileflow._onWeightsChanged`, which the shell binds at boot.

### DOM ordering = CSS `order`, not reorder

To preserve iframe contents (playing YouTube, web panel) across reflows,
the engine sets `el.style.order = -score` rather than mutating DOM child
order. CSS Grid's `grid-auto-flow: dense` honors `order` for placement.
Tradeoff: tab/screen-reader order follows DOM, not visual; acceptable for
the personal-ecosystem use case. If keyboard-nav order becomes a friction
point, add a `visualOrder()` helper and a custom focus trap.

### `recompute_layout` tool

Wired in `tools/core.py` + `server.py`. Returns a confirmation; the side
effect is a `tileflow_recompute` WS frame broadcast to every connected
client. The shell subscribes via the synthetic `_shell` panel key.

### Schema additions

- `panels/loader.py`: `PanelTileflow.size_classes` removed (engine derives
  from `display.preferred/min/max`); `PanelTileflow.score_bonus` added.
- `panels/loader.py`: `InstanceScoreOverrides` model + optional
  `LayoutInstance.score_overrides` field. `InstanceTileflow` extends it
  with the user-floor fields (`locked_floor`, `locked_size`,
  `never_dormant`) — honored by the engine since 2026-07-06 (Stage 3).


## Reference

- Panel system: [panels.md](panels.md).
- Layout file: [layout/panel_layout.json](../layout/panel_layout.json).
- Loader / state plumbing: [static/panel-shell.js](../static/panel-shell.js).
