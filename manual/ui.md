# UI structure

Single-page app. All logic is in `static/app.js`. No frameworks, no build step —
edit → hard-refresh.

## DOM layout (index.html)

```
body
├─ .br-stack                    (bottom-right floating stack)
│   ├─ .ctx-panel               context token bar
│   └─ .theme-picker            rose / slate swatches
└─ .canvas
    ├─ .left-panel              30% column, chat side
    │   ├─ .chat-input-panel    dir picker (text input + "set" button)
    │   ├─ .chat-input-panel.packs-panel   knowledge-pack chips
    │   ├─ .chat-input-panel    textarea + stop/reset buttons
    │   └─ .response-panel      streamed assistant output
    ├─ .tool-panel              center column, tool_call log
    ├─ .file-panel (#context-panel)   files the model has read
    └─ .file-panel              project file tree
```

Elements with ids the JS hooks into:

| id              | what it is                                   |
| --------------- | -------------------------------------------- |
| `dir-input`     | project path textbox                         |
| `packs-chips`   | pack chip container                          |
| `chat-textbox`  | main input textarea                          |
| `stop-btn`      | cancel button (disabled unless `busy`)       |
| `response-text` | streamed assistant output target             |
| `tool-panel`    | tool log panel (sits center)                 |
| `tool-panel-inner` | tool entry container                      |
| `context-panel` / `context-files` | read-files list              |
| `file-tree`     | project tree                                 |
| `ctx-bar` / `ctx-pct` / `ctx-tokens` | context usage bar         |

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
info          → log() + refreshTree() + refreshPacks() + retitle
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
- `syncPacksToServer()` — whenever a pack chip is toggled

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

## Adding a button

1. Add `<button onclick="myFn()">` to `index.html` inside the right panel.
2. Define `function myFn() { ws.send(JSON.stringify({type: "my_action"})); }` in `app.js`.
3. Add a matching branch in `server.py` `websocket_endpoint` and return a response event.
4. Add a handler for the response event in `ws.onmessage`.
5. Style it with `.chat-action-btn` or a new class in `style.css`.

## Adding a panel

1. Add a `<div class="my-panel">…</div>` inside `.canvas` in `index.html`.
2. The canvas is `display: flex`, so just set `flex: 0 0 <width>` or let it take `flex: 1`.
3. Copy the glass recipe from an existing outer panel — see `styling.md`.
4. No JS wiring needed unless the panel is interactive.
