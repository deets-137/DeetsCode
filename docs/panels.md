# Panels — author & modder guide

The harness UI is an anchored chat column beside a 2x2 bento of four
**slots**. Each panel is a folder under `panels/<name>/`;
`layout/panel_layout.json` says which panel sits in which slot, and the user
swaps them from the tile title. See [slots.md](slots.md) for the layout
system — this doc is about writing the panel that goes in one.

This doc is the cold-start reference. If you're a human building a
panel by hand or a model building one via tool calls, start here.

If something is wrong, fix it.

---

## 60-second mental model

- A panel is a folder. The folder has at minimum a `panel.json`
  manifest.
- The harness renders every panel inside a uniform chrome
  (`.panel-instance` — header with the picker title, an actions slot, and a
  content slot). You return **content only** — never your own title bar.
- **A panel is its own instance.** One panel can never occupy two slots, so
  the panel name is the id everywhere: `harness.*` calls, `data-instance`,
  the `?instance=` on your view fetch.
- **You don't get a say in where you go.** Every slot is the same size and
  the user picks what's in it. `display.min` / `preferred` still describe
  your footprint, but nothing scores you into a bigger cell.
- Two verbs replace the old four-state model: `harness.notify(panel)` puts a
  dot on your tile and your picker entry; `harness.requestPanel(panel)` asks
  for a slot outright. Use the second one sparingly.
- **If you start something, stop it.** Slot swaps destroy your DOM. Register
  teardown with `harness.onUnmount(panel, fn)` for every timer, observer, and
  document-level listener your view creates. WS subscriptions are handled
  for you.

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
  "icon": "👋"
}
```

**`panels/hello/server.py`**

```python
def view() -> str:
    return "<div style='padding:8px'>hello, world</div>"
```

No layout edit needed. Every registered panel joins the **pool** — the list
the picker offers — automatically. Hot reload, no server restart:

```
curl -X POST http://127.0.0.1:8000/api/panels/reload
```

Refresh the tab and pick "hello" from any tile's title menu. To have it on
screen at boot, name it in a slot:

```json
{ "schema": 3,
  "slots": { "nw": "hello", "ne": "files", "sw": "task_list", "se": "web" },
  "anchored": ["chat"], "mode_overrides": {} }
```

---

## Pick a tier

| Tier | What you ship                       | Render path                                | When to use |
|------|-------------------------------------|--------------------------------------------|-------------|
| 1    | A static `view.html` fragment       | Direct DOM injection, host CSS wins        | UI-heavy panels with their own JS (the web URL launcher, chat). |
| 2    | (reserved — subprocess Python)      | Not implemented                            | Future, for untrusted Python. |
| 3    | A Python `server.py` with `view()`  | Direct DOM injection, full harness access  | Panels that need filesystem / subprocess / live data (file browser, activity, task list). |

Tier 1 and 3 inject into a `.panel-content[data-panel-content="<name>"]`
wrapper. There is **no shadow DOM** — host CSS wins by design, so panels
look native without effort. Inline `<script>` tags re-execute on every
view fetch via clone-and-replace.

The host identifies the sender by its `contentWindow` — the message carries
no panel name, so a panel can only signal for *itself*, never a sibling.
Anything else in the message is ignored.

---

## Manifest reference (`panel.json`)

Every field. Note how little of it is about placement: under the slot
schema the shell decides nothing about your size, so `display` is a
description of your panel rather than an input to a scoring function.

```jsonc
{
  "schema": 1,                   // bump only if breaking change
  "name": "my_panel",            // [a-z0-9_-]+, must match folder name
  "title": "human title",        // shown in chrome
  "tier": 1 | 3,
  "author": "you",

  // Tier-specific entry point. Exactly one of these is set:
  "view":    "view.html",        // tier 1 only
  "handler": "server:view",      // tier 3 only ("module:function")

  // Tier 3 only: module-level functions callable via
  // POST /panels/<name>/action/<fn>. Called as fn(body=<parsed JSON>).
  "actions": ["submit_turn"],

  // Display-only in v1 — no enforcement yet. Future tiers will gate.
  "permissions": {
    "network": ["https://en.wikipedia.org"],
    "reads":   ["./"],
    "writes":  []
  },

  // What size + shape your panel naturally is. Slots are all the same
  // size, so nothing scores you into a bigger cell — this is documentation
  // for your own internal layout, plus the floor the narrow breakpoint
  // respects.
  "display": {
    "shape": "wide" | "tall" | "square" | "free",
    "aspect_ratio": "16:9" | null,            // for media panels
    "min":       { "width": 200, "height": 80 },   // hard floor
    "preferred": { "width": 400, "height": 120 },  // soft target
    "max":       { "width": null, "height": null }, // hard ceiling, null = unbounded
    "scroll": "internal" | "grow",
    "growable": false
  },

  // True = mode_overrides cannot hide it. NOT the same as being in the
  // layout's `anchored` list, which means "mounted outside the slot system
  // entirely" — chat, and only chat.
  "anchored": false,

  // Vestigial under the slot schema: one panel can never occupy two slots,
  // so a second instance has nowhere to go. Leave it false.
  "multi_instance": false,

  // Glyph for the picker. STRONGLY RECOMMENDED — the fallback is the first
  // letter of the title, which collides.
  "icon": "📦",

  // False = "I exist to be embedded in another panel; don't offer me a
  // slot." Only in_context_files sets this — it renders inside Files and
  // inside the title menu's Context flyout.
  "pool": true
}
```

---

## Signals and the mount lifecycle

The four-state model (`dormant`/`idle`/`active`/`focused`), the score, and
the tray are gone, deleted with the Tileflow engine in Aug 2026. What was
actually worth keeping was never the score; it was **"this panel has
something new."** That survives as two calls.

| Call | What it does | When |
|------|--------------|------|
| `harness.notify(panel)` | A dot on the panel's tile header (if placed) and on its picker entry (always). | Nearly always. It's free and it never moves anything. |
| `harness.requestPanel(panel)` | Puts the panel in the least-recently-touched slot, displacing whatever was there. No-op (just a notify) if it's already placed. | Only for things the user must act on — an approval gate. Not for "a tool call happened." |

Notify clears when the user clicks the tile, or when it's picked into a
slot, or on `harness.clearNotify(panel)`.

Being deliberate about `requestPanel` is the whole discipline here. The old
engine bubbled panels automatically and the layout was never where you left
it. A summon is a tile disappearing out from under someone — spend it on
pending writes, not on chatter.

### Teardown — the one thing you must get right

A slot swap **destroys your DOM and re-runs your view from scratch**. So
does a `harness.refresh` tick. Anything your inline script starts must be
registered for teardown:

```js
const timer = setInterval(poll, 3000);
const ro = new ResizeObserver(onResize);
ro.observe(root);
harness.onUnmount("my_panel", () => { clearInterval(timer); ro.disconnect(); });
```

What's handled for you, and what isn't:

| Started by | Cleaned up by |
|------------|---------------|
| `harness.subscribe(...)` | The shell (`_clearPanelSubs`), on every render and unmount. |
| `harness.refresh(...)` | The shell — the timer is shell-owned. |
| `setInterval` / `setTimeout` you called | **You**, via `onUnmount`. |
| `ResizeObserver`, `MutationObserver`, `IntersectionObserver` | **You**. |
| `document` / `window` listeners | **You**. Listeners on your own nodes die with them; these don't. |

`onUnmount` registrations are dropped and re-collected on every render, so
calling it unconditionally at the top of your script is correct — you won't
stack duplicates.

**Canary.** `harness._subCounts()` returns `{panels, subs, appSubs,
unmountFns, refreshTimers, tiles}`. Swap a slot 20× and assert none of them
grow. If `refreshTimers` or `tiles` climbs, something is leaking.

### Client-push panels

A panel that isn't placed has no DOM and no running script. If your content
arrives by WS push rather than by re-rendering from server state, you need a
session-long buffer outside the panel that the view replays on mount — see
`_toolLogBuffer` + `_flushToolLogBuffer` in app.js, which is how the Activity
panel survives being swapped out mid-session. Server-rendered tier-3 panels
get this for free: their `view()` re-reads server state every mount.

### Drilling

If your panel navigates into a sub-view, tell the shell — otherwise a swap
could strand the user inside it:

```js
harness.setHeader("my_panel", { title: "src/components", atRoot: false, onBack: goUp });
harness.setHeader("my_panel", { atRoot: true });   // back at root
```

While `atRoot: false`, the title shows the directory name and a back
chevron, and clicking it calls `onBack` instead of opening the picker.
Picking is root-only, and that is exactly what makes destroy-and-remount
safe. A panel that never drills simply never calls this.

### Summoning from outside the page

```
POST /api/panels/<name>/summon
```

Broadcasts to every connected tab; same semantics as `harness.requestPanel`.
The model has no layout tool — it edits `layout/panel_layout.json` directly
(or `PUT /api/layout`), and connected tabs reconcile on `layout_updated`.

---

## `window.harness.*` — complete API

Available in every tier-1 and tier-3 panel's inline script — which is
every panel there is.

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
| Layout       | `layout_updated`, `panel_summon` (the shell handles both — you don't subscribe directly; `layout_updated` re-syncs from `/api/layout`, remounting only the slots whose panel actually changed) |

### Slots and signals

| Call | Purpose |
|------|---------|
| `harness.onUnmount(panel, fn)` | Register teardown for a timer / observer / document listener. Runs on slot swap, on narrow-surface unmount, and before every re-render. Re-register on every render — the list is cleared first, so it never stacks. |
| `harness.notify(panel)` | Mark the panel "has something new": a dot on its tile header and its picker entry. Cheap, and it moves nothing. |
| `harness.clearNotify(panel)` | Drop the dot. Also happens when the user clicks the tile or picks the panel. |
| `harness.requestPanel(panel)` | Summon: place the panel in the least-recently-touched slot. Falls back to `notify` if it is already placed. Spend this only on things the user must act on. |
| `harness.setHeader(panel, {title, atRoot, onBack})` | Report drill state. `atRoot: false` turns the picker into a back chevron so a swap cannot strand the user mid-drill. |
| `harness.slots()` | `{nw, ne, sw, se}` — which panel is where. Read-only copy. |
| `harness.pool()` | Panel names eligible for a slot, in picker order. |
| `harness._subCounts()` | Leak canary: `{panels, subs, unmountFns, refreshTimers, tiles}`. Swap a slot 20x and assert none of them grow. |

### Interaction logging (`system_log`)

Every click on a `.panel-instance`, every slot placement, and every notify
is emitted as a `system_log` event by panel-shell — debounced ~500ms and
batched over WS to the server, which
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
| `custom` | the shell (`{act: "place"|"notify", ...}`) and panel scripts via `harness.logInteraction` | per-panel |

Server-side query endpoints:

| Method | URL | Purpose |
|---|---|---|
| GET | `/api/system_log?since=&until=&instance=&kind=&limit=` | Recent events newest-first. Timestamps are unix ms; `limit` is capped at 5000. |
| GET | `/api/system_log/summary?window_ms=` | Per-(instance, kind) counts, sorted by most recent. Omit `window_ms` for all-time. |

The Python side is `storage.record_system_event` / `record_system_events`
/ `query_system_log` / `panel_usage_summary` / `prune_system_log`. The
prune helper exists for maintenance but isn't auto-called; storage.db can
hold millions of rows without trouble.

## Panel chrome — the shell owns it

Every panel renders as:

```
.panel-instance
├── .panel-header
│   ├── .panel-title       ← from manifest.title; in a slot it IS the picker
│   └── .panel-actions     ← slot for your buttons (optional)
└── .panel-content         ← your HTML lands here
```

Glass surface, padding, border-radius, and title styling are all
shell-rendered. **Your handler returns content only — never a title bar.**

The title does double duty: on a slot tile it is a button that opens the
picker (the pool of panels you can swap in), and it carries the notify dot.
The old `⚙ / −` pill is gone — it existed to cycle the four panel states and
to minimize to the tray, and neither of those exists any more.

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

Your buttons are re-hoisted on every view fetch, so a handler that renders
different actions per state doesn't need to do anything special.

---

## One panel, one instance

There is no such thing as two instances of a panel any more. A slot holds
exactly one panel, a panel can only be in one slot, and the picker exchanges
rather than duplicates — so the panel name is a stable, unique id.

Practically, this means you can stop scoping things:

```html
<script>
(function () {
  const root = document.currentScript.closest(".panel-instance");
  // root.dataset.instance === root.dataset.panelName === "my_panel"
  const KEY = "harness.myPanel.value";   // no instance suffix needed
})();
</script>
```

`harness.refresh(name, seconds)` and `harness.refreshNow(name)` take the
panel name. View fetches still carry `?instance=<name>` for route symmetry,
but a tier-3 `view()` takes no arguments — the `harness_ctx` parameter went
away with the apps layer. A panel that needs configuration reads it from its
own state on disk.

`multi_instance` survives in the manifest only for the apps loader's
cross-check. Leave it false.

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

Adding a new field to `panel.json` or to the layout sheet requires touching
**four places** — miss any one and the field appears to work but silently
disappears in transit:

1. **The JSON file itself.**
2. **`panels/loader.py` Pydantic model** — `PanelManifest` or
   `PanelLayout`. Pydantic v2 drops unknown fields, so anything the model
   doesn't declare is gone before it reaches the rest of the system.
3. **`server.py` serializer** — `/api/panels` hand-picks fields into the
   response. Add yours to the dict in `list_panels()`. `/api/layout` uses
   `model_dump`, so layout fields usually pass through — but double-check.
4. **`static/panel-shell.js` reader** — the JS that uses the field at
   render time.

The failure mode is annoyingly quiet. Quick verification:
`fetch('/api/panels').then(r => r.json())` in the browser console —
if your new field isn't in the response, it's been stripped at step
2 or 3.

---

## Layout file (`layout/panel_layout.json`)

The whole thing. Four slots, one panel each, plus the panels mounted outside
the slot system. Schema v3 — the v2 grid/regions/pins/score-overrides block
went with the Tileflow engine.

```jsonc
{
  "schema": 3,

  // Exactly these four keys, each naming exactly one pool panel. Four
  // distinct panels — a panel cannot occupy two slots.
  "slots": {
    "nw": "activity",
    "ne": "files",
    "sw": "task_list",
    "se": "web"
  },

  // Mounted outside the slot system entirely: never swapped, never
  // unmounted. Chat, and realistically only chat — app.js routes WS frames
  // straight into its DOM by id, and that coupling only stays out of scope
  // while chat never unmounts.
  "anchored": ["chat"],

  // Per-mode slot visibility. Empty today (DeetsCode is the only mode);
  // the schema stays for future modes.
  "mode_overrides": {
    "some_future_mode": { "slots": { "se": { "hidden": true } } }
  }
}
```

**Validating load.** The pool changes between builds — a panel gets merged
away, an app is uninstalled — and a stale sheet must not brick the bento.
`GET /api/layout` resolves it: any slot naming a panel that is missing, not
in the pool, or already placed falls back to its default (then to the first
unused pool panel), and the substitutions come back in a `warnings` array
that the shell logs. `PUT /api/layout` is stricter — it rejects duplicates,
empties, and non-pool panels with a 400 rather than persisting them.

`GET /api/layout` also returns `pool`: the panel names the picker offers,
sorted by title. That is every registered panel except the anchored ones and
any that set `"pool": false`.

## Endpoints

| Method | URL                                          | Purpose |
|--------|----------------------------------------------|---------|
| GET    | `/api/panels`                                | Registry of discovered panels + load errors |
| GET    | `/api/panels/<name>`                         | One panel's manifest + status |
| POST   | `/api/panels/reload`                         | Re-scan `panels/` (hot reload) |
| GET    | `/api/layout`                                | Resolved slot sheet + `pool` + `warnings` |
| PUT    | `/api/layout`                                | Replace the slot sheet (four distinct pool panels, else 400; broadcasts `layout_updated`) |
| POST   | `/api/panels/<name>/summon`                  | Ask every client to give this panel a slot (broadcasts `panel_summon`) |
| GET    | `/api/llm/status`                            | llama-server model roster + load states — feeds the titlebar status strip |
| GET    | `/panels/<name>/view?instance=<name>`        | Tier-1/3 rendered HTML body |
| POST   | `/panels/<name>/action/<fn>`                 | Invoke a whitelisted tier-3 action (manifest `actions`); called as `fn(body=<parsed JSON>)` |
| GET    | `/panels/<name>/static/<file>`               | Panel-local static asset |


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
  `--subtext`, `--title`, `--border`, `--panel-border`, `--divider`,
  `--accent`, `--ink-2`, `--error`, `--focus-glow`, `--scrollbar`,
  `--scrollbar-hover`, and the traffic lights `--go` / `--stop` /
  `--pause` / `--traffic-glyph` (function-named, never color-named).
  (Legacy names — `--response-text`, `--textbox-bg`, `--glass-border`,
  … — are aliased to these and still work.)
- **Skin** (`static/skin.css`) — everything non-color per
  `[data-skin="name"]`: `--font-body`, `--font-mono`, the `--fs-*` type
  scale, the `--radius-*` ladder, the `--space-1..5` spacing ladder
  (4/8/12/16/24px), `--icon-sm/-md/-lg`, `--panel-fill` /
  `--panel-inset-fill` / `--panel-backdrop` / `--shadow-panel` /
  `--menu-surface` / `--menu-backdrop` materials, motion
  (`--dur-fast/-med/-theme`, `--ease-ui`, `--hover-lift`), the focus
  ring (`--focus-ring-w`, `--focus-ring-off`), scrollbar geometry, and
  the titlebar chrome (`--titlebar-h`, `--traffic-*`). The `[data-skin]`
  base block is the authoritative token list; a skin never names a
  color — it points a slot at a theme role or `color-mix()`es one, and
  effects only one skin wants get a no-op base token so others never
  inherit them by accident.

`/api/themes` and `/api/skins` parse those files for the theme/skin
pickers (now flyouts in the DeetsCode title menu), so adding a
`[data-theme="x"]` or `[data-skin="x"]` block is all it takes to ship a
new one. Keep theme blocks flat — the parser regex can't see past a
nested rule.

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
- Tier-1 `view.html` panels reload trivially (no Python state).

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
  "icon": "<EMOJI>"
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

Then `curl -X POST http://127.0.0.1:8000/api/panels/reload` and refresh the
tab. The panel is in the pool immediately — pick it from any tile's title
menu. Put it in `layout/panel_layout.json`'s `slots` only if you want it on
screen at boot.

---

## Reference panels

Working examples in the repo:

- [`panels/activity/`](../panels/activity/) — tier 3. The `tool_log` +
  `pending_writes` merge: a client-push stream with a server-rendered
  actionable banner above it, and the one panel that spends
  `requestPanel`.
- [`panels/files/`](../panels/files/) — tier 3. The `in_context_files`
  merge: one browser, two contexts behind a pill, and the reference use of
  `harness.onUnmount` for a poll timer.
- [`panels/web/`](../panels/web/) — tier 1, freeform URL browser.
- [`panels/chat/`](../panels/chat/) — tier 1, **anchored**. Demonstrates the
  "panel mounts after app.js boots" race: app.js buffers chat-bound writes in
  `_chatBootBuffer` and the view's inline script drains them via
  `window._flushChatBootBuffer`. Also hosts the slash typeahead.
- [`panels/in_context_files/`](../panels/in_context_files/) — tier 3 with
  `"pool": false`: a renderer with no tile of its own, embedded by Files and
  by the title menu's Context flyout.

---

## Cross-references

- [slots.md](slots.md) — the layout system: the four slots, the picker, the
  teardown contract, the summon bus. (Its predecessor, the Tileflow scored
  engine, lives in git history — docs/tileflow.md, deleted 2026-08-16.)
