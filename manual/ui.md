# UI structure

Single-page app. No frameworks, no build step — edit → hard-refresh.

> **Scope note (updated 2026-08-15):** the *outer* layout is the slot
> system — an anchored chat column plus four fixed slots, built by
> `static/panel-shell.js` from `layout/panel_layout.json` (see
> `docs/slots.md`, `docs/panels.md`). This doc remains
> accurate for what lives *inside* panel content (the `.nested-panel`
> primitives, chat DOM ids, event flow in `static/app.js`) — but the
> top-level tree below is historical: those blocks are individual panels'
> view content, not hardcoded index.html structure.

## DOM layout (historical shape — now distributed across panel views)

```
body.canvas
├─ .left-panel                    chat column
│   ├─ .chat-input-panel          dir picker (text input + "set" button)
│   ├─ .chat-input-panel          textarea + stop/compact/reset
│   └─ .response-panel            streamed assistant output
├─ .middle-column
│   ├─ .file-panel.activity-panel  activity — side-by-side sub-panels:
│   │   ├─ .nested-panel#tool-panel     tool_call log
│   │   └─ .nested-panel#pending-panel  queued writes + approve/reject/flush
│   └─ .settings-grid             bento row of two settings tiles
│       ├─ .settings-panel[data-panel="model"]
│       │   ├─ .nested-panel     selection (model <select>)
│       │   ├─ .nested-panel     behavior (keep-history, auto-apply, temp)
│       │   └─ .nested-panel     context (ctx-bar, tokens)
│       └─ .settings-panel[data-panel="customization"]
│           ├─ .nested-panel     file click action (template textarea)
│           └─ .nested-panel     theme (swatch list)
├─ .context-column
│   ├─ .file-panel.status-panel   status — side-by-side sub-panels:
│   │   ├─ .nested-panel#context-panel  files the model has read
│   │   └─ .nested-panel#task-panel     task.md checklist
│   └─ .file-panel.info-panel     reference — stacked sub-panels:
│       └─ .nested-panel         editable slash commands
│                                (pack chips retired — manuals are
│                                 model-pulled via list_manual/load_manual)
└─ .right-column
    ├─ .right-grid
    │   └─ .file-panel            project file tree
    └─ .file-panel.bot-ops-panel  (Discord-bot observability — designed to
                                   be liftable as a standalone module)
        ├─ .nested-panel.bot-subpanel[data-panel="spectate"]  read-only frame tail
        ├─ .nested-panel.bot-subpanel[data-panel="control"]   reset/compact/cancel/set_prompt
        └─ .nested-panel.bot-subpanel[data-panel="inventory"] session table (row-click pre-selects spectate)
```

### Panel primitives (shell types)

Two outer shell types + one inset. Every new panel picks one:

| Class              | Role                                                                                     |
| ------------------ | ---------------------------------------------------------------------------------------- |
| `.file-panel`      | Outer grid tile with the default glass recipe. Use for main canvas panels (files, tools, bot-ops, info-panel). Pairs with `.file-panel-header` + `.file-panel-inner`. |
| `.settings-panel`  | Outer bento tile for grouped settings. Lives inside `.settings-grid`. Pairs with `.settings-header` + `.settings-inner`. |
| `.nested-panel`    | Inset sub-tile. Use *inside* either outer shell to group related controls (e.g. bot-ops subpanels, model/customization sections, info-panel sections). Pairs with `.nested-header` for a small uppercase caption + optional action button. |

### Panel archetypes (behavior)

Orthogonal to the shell. Declared implicitly by the JS a panel wires up:

- **Action** — buttons fire one-shot WS messages; server replies with `info`/`error`. Ex: bot-control, pending-writes.
- **View** — renders state from an HTTP endpoint or pushed WS event. Ex: file tree, bot-inventory, bot-spectate.
- **Passive** — pure UI, no server round-trips beyond initial load. Ex: theme picker, file-click template (client-side only until used).

When adding a panel, pick the shell type first (`.file-panel` for a grid tile, `.nested-panel` for a sub-tile inside an existing shell), then wire the archetype per [panels.md](panels.md).

### Modularization note — `.bot-ops`

The three `.bot-panel` siblings under `.bot-ops` share no DOM state with each
other except one signal: the value of `#spectate-select` is also read by the
control panel to decide the target session. When lifting this section out as
a standalone module or Web Component, preserve that id (or expose an
equivalent setter). Server side, all four actions go through
`enqueue_session_control` in `server.py` — keep any new observability panel
on that path rather than reimplementing the routing.

Elements with ids the JS hooks into:

| id              | what it is                                   |
| --------------- | -------------------------------------------- |
| `dir-input`     | project path textbox                         |
| `chat-textbox`  | main input textarea                         |
| `stop-btn`      | cancel button (disabled unless `busy`)       |
| `response-text` | streamed assistant output target             |
| `tool-panel`    | tool log panel (sits center)                 |
| `tool-panel-inner` | tool entry container                      |
| `context-panel` / `context-files` | read-files list              |
| `file-tree`     | project tree                                 |
| `ctx-bar` / `ctx-pct` / `ctx-tokens` | context usage bar         |
| `task-inner`    | task.md checklist render                     |
| `pending-panel-inner` / `pending-count` | queued writes display         |
| `spectate-select` | session picker — **also the control panel's target signal** |
| `spectate-status` / `dev-counts` | bot-spectate tail status        |
| `control-target` / `control-mode` / `control-status` | bot-control panel state |
| `inv-table` / `inv-tbody` | bot-sessions inventory table        |

## Event flow

`connect()` opens the WebSocket. `ws.onmessage` is one big `switch(msg.type)`:

```
thinking      → showThinking() ; appendResponse(content, "thinking")
text          → hideThinking() ; appendResponse(content, "normal")
tool_call     → addToolEntry(name, args) ; addContextFile(path) if read_file
tool_result   → updateToolResult(content)
pending_writes → showPendingWrites(writes)    (apply/reject banner)
writes_applied / writes_rejected → hidePendingWrites()
reset_complete → clearResponse / clearToolPanel / clearContextFiles
info          → log() + refreshTree() + retitle
error         → log(..., "error")
usage         → updateContextBar(total, max)
done          → setInputEnabled(true) ; addDivider()
```

Outbound calls are all simple `ws.send(JSON.stringify({...}))`:

- `sendMessage()` — from Enter key in `#chat-textbox`. If input starts with `/` it routes to `handleSlash()` instead.
- `cancelRun()` — from the stop button or Esc
- `resetConversation()` — from the reset button
- `compactConversation()` — from the compact button
- `setDir()` — from the "set" button next to the dir input

## Slash commands

Typing a `/…` in the textarea runs a tool directly without invoking the model.
Parser lives in `handleSlash()`. Available:

| Slash                 | Effect                                                       |
| --------------------- | ------------------------------------------------------------ |
| `/read <path> [N-M]`  | `read_file` with optional line range                         |
| `/search <pat> [glob=X] [path=Y]` | regex search                                     |
| `/symbols <path>`     | list defs/classes/headings + line numbers                    |
| `/ls [path]`          | directory listing                                            |
| `/tree`               | refresh local file tree panel (no server call)               |
| `/compact`            | summarize + trim conversation                                |
| `/help`               | list available slashes in the tool panel                     |

Results render in the tool panel like any other tool call but don't pollute
`messages`.

## Busy state

`busy` is a module-level bool.
- `setInputEnabled(false)` on send → disables textarea, enables stop.
- `setInputEnabled(true)` on `done` → re-enables textarea, disables stop.
- Esc keydown calls `cancelRun()` while busy.

The server's `done` event is emitted in a `finally`, so the UI never gets
stuck disabled even on cancel or server exception.

## Tool entry rendering

`addToolEntry(name, args)` creates `.tool-entry` with:

- `.tool-name` — monospace tool name
- `.tool-args` — rendered by `renderToolArgs(name, args)`
  - For `edit_file`: `renderEditArgs` shows a diff (`-` old / `+` new)
  - For everything else: `JSON.stringify(args, null, 2)`, HTML-escaped
- `.tool-result` — filled by `updateToolResult(content)`. If content starts with "Error", gets `.error` class for red styling.

All user-visible strings go through `escapeHtml()`. Never use innerHTML with
unescaped tool args or results.

## Pack chips

- `refreshPacks()` fetches `/packs` → `renderPackChips(packs)`.
- `activePacks` is a `Set<string>` of selected pack names.
- Each chip: click → `togglePack()` → `syncPacksToServer()`.
- `.scoped-project` class adds a left border for packs from `manual/`.
- `refreshPacks()` is called on WS open AND on every `info` event (so switching project refreshes project-scoped chips).

## Pending-write banner

`showPendingWrites({path: content, ...})` injects a `.pending-banner` at the
top of `.response-panel` with:

- Filenames (HTML-escaped) with `<em>` wrapping
- `.banner-btn.apply` → `applyWrites()` → sends `apply_writes`
- `.banner-btn.reject` → `rejectWrites()` → sends `reject_writes`

## Bot-ops panels (`.bot-ops`)

Three sibling panels that turn the web UI into a debug/control surface for
Discord bot sessions. Each is self-contained with its own header and inner.

**Spectate** (`data-panel="spectate"`) — read-only frame tail.
- `refreshSpectateSessions()` fetches `/api/events/sessions`, populates
  `#spectate-select`. `onSpectateSelectChange()` fires when the picker
  changes — it stores the selection in `_controlTarget()` and updates the
  control panel header.
- `startSpectate()` / `stopSpectate()` open/close a dedicated spectator WS.
- Event-type filter chips under `#dev-filters` control which frames render.

**Control** (`data-panel="control"`) — fires actions against the selected session.
- `remoteControl(action)` sends `{type: "remote_control", target_session_id, action}`.
  `action ∈ {reset, compact, cancel}`.
- `remoteSetPrompt()` reads `#control-mode` and sends
  `remote_control` with `action: "set_prompt", prompt`.
- `_updateControlPanel()` greys out buttons when no target is selected or the
  target isn't in the live set. `_refreshControlModes()` fills `#control-mode`
  from `/api/prompts`.

**Inventory** (`data-panel="inventory"`) — `<table>` of every known session.
- `refreshSessionInventory()` hits `/api/events/sessions`, renders rows with
  live/off state. Row click calls `_pickInventoryRow(sid)` which pre-selects
  that session in `#spectate-select` (the cross-panel signal).
- Module state: `_inventoryLive: Set<sid>`, `_inventoryMeta: Map<sid, row>`.

Boot hook: `ws.onopen` calls `refreshSessionInventory`, `_refreshControlModes`,
`_updateControlPanel`.

All four control actions (`reset`/`compact`/`cancel`/`set_prompt`) route
server-side through `enqueue_session_control` — shared by the `remote_control`
WS handler and the `POST /api/session/{sid}/control` HTTP route. See
`manual/server.md` § Cross-session control.

## Adding a button

1. Add `<button onclick="myFn()">` to `index.html` inside the right panel.
2. Define `function myFn() { ws.send(JSON.stringify({type: "my_action"})); }` in `app.js`.
3. Add a matching branch in `server.py` `websocket_endpoint` and return a response event.
4. Add a handler for the response event in `ws.onmessage`.
5. Style it with `.chat-action-btn` or a new class in `style.css`.

## Adding a panel

For an interactive panel (buttons that hit the server, live-updating view,
new WS message type) see [panels.md](panels.md) — full three-file recipe
with a worked example. The DeetsCode prompt auto-loads it on UI tasks.

For a purely decorative / static panel:

1. Add a `<div class="my-panel">…</div>` inside `.canvas` in `index.html`.
2. The canvas is `display: flex`, so just set `flex: 0 0 <width>` or let it take `flex: 1`.
3. Copy the glass recipe from an existing outer panel — see `styling.md`.
