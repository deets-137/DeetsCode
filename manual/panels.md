# Building a new UI panel

Step-by-step recipe for adding a panel to the harness web UI. Load this when
the task involves adding or modifying a panel, a button that talks to the
server, or a new WebSocket message type.

Cross-refs: [ui.md](ui.md) for DOM layout and existing ids.
[server.md](server.md) for the WS protocol tables. [styling.md](styling.md)
for the glass recipe.

## The three-file change

Every interactive panel touches at least three files. Read them all first:

1. `static/index.html` — add the DOM (section + inner controls).
2. `static/app.js` — add the click handlers, the `ws.send(...)` calls, and a
   branch in `ws.onmessage` for the response event.
3. `server.py` — add a branch in `websocket_endpoint` for the new message type
   (or a new HTTP route if this panel reads REST).

Optional fourth:

4. `static/style.css` — new selectors, if existing `.file-panel` /
   `.dev-btn` / `.tool-panel` classes don't cover the layout.

Before editing anything, open the bot-ops panels in `index.html` and
`app.js` — they are the canonical worked example and match the pattern
below one-to-one.

## Worked example: the bot-control panel

Trace this one end-to-end to see the whole shape. Each file has the
section that matters — grep these exact anchors:

- **DOM**: `static/index.html`, search for
  `<!-- [bot-panel:control]`. One `<section class="file-panel bot-panel">`
  with a header, mode `<select>`, and four `<button>`s wired via
  `onclick="remoteControl('reset')"` / `remoteSetPrompt()`.
- **Client JS**: `static/app.js`, search for `function remoteControl(`
  and `function remoteSetPrompt(`. Each does
  `ws.send(JSON.stringify({type: "remote_control", target_session_id, action, ...}))`.
- **Client response handler**: same file, in `ws.onmessage`'s big `switch`.
  For control actions the server replies with an `info` event; no new
  client-side type was needed.
- **Server handler**: `server.py`, search for `"remote_control"`. Single
  branch in `websocket_endpoint` that calls the shared helper
  `enqueue_session_control(target_session_id, action, **extra)` and emits an
  `info` event with the helper's return string.
- **Shared helper**: `server.py`, search for `def enqueue_session_control`.
  Module-level function so the same logic is reusable from an HTTP route
  (`POST /api/session/{sid}/control`) without duplication.

That's the shape. Copy it.

## Step-by-step recipe

### 1. Decide the panel's shape

Two archetypes cover almost everything:

- **Action panel** — buttons fire one-shot actions. Server does work, emits
  `info` / `error`. No new client event type needed. Example: bot-control.
- **View panel** — panel shows live or periodically-refreshed data.
  Needs either (a) an HTTP endpoint the client polls on a button click,
  or (b) a new server→client WS event type pushed on state change.
  Example: bot-inventory uses (a) via `/api/events/sessions`.

Pick the simplest that works. Don't add a new WS event type if a
one-shot HTTP GET on a refresh button is good enough.

### 2. Add the DOM (index.html)

Place the new `<section>` inside the column that matches its purpose.
Right-column = reference/status data. Middle-column = tool/state output.
Left-column = input/conversation. The bot-ops panels sit in
`.right-column > .bot-ops`.

Minimum skeleton:

```html
<section class="file-panel my-panel" data-panel="myname">
  <div class="file-panel-header">
    <span>my panel</span>
    <button onclick="refreshMyPanel()" title="Refresh">↺</button>
  </div>
  <div class="file-panel-inner">
    <!-- controls / output -->
  </div>
</section>
```

Rules:
- Use `.file-panel` as the outer class — inherits the glass recipe from
  `style.css` automatically (see [styling.md](styling.md) § The glass recipe).
- Give every stateful element a stable `id`. The JS hooks by id; the
  manuals document by id. `my-panel-inner`, `my-panel-status`, etc.
- Leave a top-of-section HTML comment describing the panel's purpose and
  what IDs it exposes. Future-you will lift this into a module.

### 3. Wire the client (app.js)

Three things to add:

1. The `onclick` handler(s) referenced in your HTML. Each one either
   calls `ws.send(JSON.stringify({type, ...}))` or `fetch("/api/...")`.
2. If you introduced a new response WS event type, add a branch in
   `ws.onmessage`'s `switch(msg.type)`. For action panels, reuse `info` /
   `error` — they already render.
3. If the panel has a boot-time populate (dropdowns, initial list), call
   your refresh function from `ws.onopen` next to the other boot hooks.

Keep functions top-level on `window` (plain `function foo() {}` in
`app.js`) — the `onclick="foo()"` attributes depend on that. Module state
(`let _myState = new Map()`) goes at file top with the other
underscore-prefixed module globals.

Always `escapeHtml()` anything user-controlled before putting it in
innerHTML. Tool names, session ids, status strings all count.

### 4. Add the server handler (server.py)

For an action panel (new WS message type):

```python
# inside websocket_endpoint's main dispatch
elif msg_type == "my_action":
    target = data.get("target_session_id")
    try:
        result = some_shared_helper(target, ...)
    except SomeError as e:
        await ws.send_json({"type": "error", "content": str(e)})
        continue
    await ws.send_json({"type": "info", "content": result})
```

For a view panel (new HTTP route):

```python
@app.get("/api/my-thing")
async def get_my_thing():
    return {"items": [...]}
```

Before writing the handler inline, ask: is the logic reusable?
If another panel (or the Discord bot, or a script) might want the same
action, extract a module-level helper and call it from both the WS
branch and an HTTP route. The control panel does this with
`enqueue_session_control` — one helper, two call sites, zero duplication.

After editing, remember: the server does NOT auto-reload. Restart uvicorn.
The client reloads on hard refresh.

### 5. Style it (style.css, optional)

If your panel is boring rows-and-buttons, `.file-panel` + `.dev-btn` +
`.dev-row` + `.dev-status` already cover it — see how the bot-ops
panels use them. Add new selectors only when you need them.

Follow [styling.md](styling.md) rules: theme-vars not hardcoded colors,
no backdrop-filter on nested elements.

### 6. Restart and verify

1. Restart the uvicorn process (no auto-reload).
2. Hard refresh the browser (Ctrl+Shift+R).
3. Exercise each button. Watch the tool panel and response panel for
   `info` / `error` events.
4. If something's off, inspect DevTools console — the only place client
   errors surface.

## Update the manuals

After shipping:

- Add the new panel section to [ui.md](ui.md) § DOM layout.
- Add any new DOM ids to the ids table in [ui.md](ui.md).
- Add new WS message types to [server.md](server.md) § WebSocket protocol
  table. New HTTP routes to the HTTP routes table.
- If you introduced a shared server helper, document it under
  [server.md](server.md) like `enqueue_session_control` is.

Stale manuals are worse than no manuals — the model will trust them.

## Where state lives (for view panels)

A panel displaying data has to read it from somewhere. The options:

- **SQLite (`storage.db`, via `storage.py`)** — persists across restarts.
  Tables: `games`, `moves`, `players`, `notes`, `stats`, `events`.
  Query helpers already exposed: `list_games`, `game_history`, `list_notes`,
  `stats_summary`, `query_events`, `list_event_sessions`. Add a new helper
  function to `storage.py` before writing an HTTP route — never call
  `sqlite3` directly from `server.py`.
- **Event log (`events` table)** — the canonical cross-session observability
  feed. Every tool call, tool result, text chunk, thinking chunk, and error
  is recorded via `storage.record_event(session_id, type, payload)` in the
  agent loop. `/api/events/{sid}` and `/api/events/sessions` already expose
  it. If your panel wants to observe another session, read from here rather
  than inventing a new channel.
- **Per-project files** — `task.md` (via `update_task` tool), `pending_writes`
  (module global in `tools/core.py`, exposed at `GET /pending`), `read_files`
  (exposed via the `list_context_files` tool).
- **Module globals in server.py** — `_session_control` for live sessions,
  `messages` / `pending_writes` / `read_files` are per-WebSocket. Do not
  reach into these from a new module; go through a helper.
- **Per-turn `state` dict** — created fresh in `agent_loop`. Ephemeral.
  Do NOT design a panel around reading this; it's gone after the turn.

Rule of thumb: if it has to survive a restart, it's in SQLite. If it's
per-session live state, it's a module global in `server.py` and needs a
helper. If it's per-turn, you can't observe it from a panel.

## Rendering content

Three patterns in [app.js](static/app.js), pick deliberately:

- **Plain strings** — use `escapeHtml()` then set `innerHTML`, or set
  `textContent` directly. Default for anything user/tool-generated:
  session ids, tool names, status strings, filenames.
- **Markdown** — `renderMarkdown(text)` (marked + DOMPurify) for any
  text that should format headings, lists, bold, code blocks. Used by
  the task panel and the streamed assistant output. DOMPurify is already
  loaded; do not add a second sanitizer.
- **Tool-entry style** — if a panel shows tool calls or tool-like output,
  reuse `.tool-entry` / `.tool-name` / `.tool-args` / `.tool-result` so
  styling stays consistent. See `addToolEntry` as the reference.

Never drop raw tool output or server strings into innerHTML without going
through one of these paths.

## Reusable classes (don't reinvent)

These exist and cover most panel layouts. Use them before writing new CSS:

- **Panel shell**: `.file-panel`, `.file-panel-header`, `.file-panel-inner`
  (glass recipe + header row with title + button).
- **Form rows**: `.dev-row` (flex row), `.dev-row-btns` (left-aligned button
  row), `.dev-filters` (checkbox chip row).
- **Controls**: `.dev-btn` (standard button, has a `[disabled]` greyed
  state), `.dev-select` (compact `<select>`), `.dev-chip` (checkbox with
  label), `.dev-label` (small muted label), `.dev-status` (right-aligned
  status with `.live` green variant), `.dev-counts` (small counter line).
- **Tables**: `.inv-table` (full-width compact table), `.inv-state.live` /
  `.inv-state.off` (small status pill), `.inv-empty` (centered empty row).

If your panel needs one of these and the current style.css file hasn't
exposed enough states, extend the existing class (add `.dev-btn.primary`)
rather than making a new one.

## Common mistakes

- **Editing app.js with arrow functions scoped inside another function**
  and then wondering why `onclick="foo()"` fails. `onclick` attrs need
  functions on `window`. Use plain `function foo() {}` at file top.
- **Forgetting to restart uvicorn** after a server.py change. The client
  will look broken; the server hasn't loaded the new branch.
- **Reusing an existing message type** ("I'll just piggyback on `info`").
  Fine for one-shot status. Not fine if the panel needs to distinguish
  its events from general info — add a new type.
- **Hardcoding a color instead of using a theme var** — looks fine in
  one theme, invisible in the other. See [styling.md](styling.md).
- **Inlining logic that two callers will want.** If the WS handler and a
  future HTTP route will do the same thing, extract the helper NOW.
  Modular server-side plumbing is the whole reason we can spectate + control
  sessions from multiple surfaces.

## Checklist

Paste into task.md at the start of a panel-adding task:

```
- [ ] read the three bot-ops panels in index.html + app.js as reference
- [ ] decide action panel vs view panel
- [ ] add <section> to index.html in the right column
- [ ] add onclick handlers + ws.send / fetch calls in app.js
- [ ] add ws.onmessage branch if new response event type
- [ ] add branch in server.py websocket_endpoint OR new HTTP route
- [ ] extract shared helper if logic has >1 caller
- [ ] add new styles in style.css only if needed
- [ ] restart uvicorn, hard refresh, exercise each button
- [ ] update ui.md DOM layout + ids table
- [ ] update server.md WS / HTTP tables
```
