# Server & tool layer

Everything in `server.py` and `tools.py`. No database, no auth — single process,
single user, state lives in module globals and per-WebSocket locals.

## HTTP routes

| Method | Path     | Returns                                     | Caller              |
| ------ | -------- | ------------------------------------------- | ------------------- |
| GET    | `/tree`  | `{tree: [...], root: str}` JSON of files    | `refreshTree()`     |
| GET    | `/packs` | `{packs: [{name, chars, scope}]}`           | `refreshPacks()`    |
| GET    | `/pending` | `{writes: {path: content}}` (debug)       | manual curl only    |
| WS     | `/ws`    | bidirectional JSON events                   | `app.js connect()`  |
| GET    | `/*`     | static files under `static/`                | browser             |

## WebSocket protocol

All frames are JSON. Client sends `{"type": "...", ...}`, server sends events
with a `type` field the client's `ws.onmessage` dispatch switches on.

### Client → server

| type             | fields                  | effect                                                        |
| ---------------- | ----------------------- | ------------------------------------------------------------- |
| `message`        | `content: str`          | new user turn; cancels any in-flight task, starts agent loop  |
| `cancel`         | —                       | cancels current agent task                                    |
| `reset`          | —                       | cancels task + clears messages/pending/read-files             |
| `set_dir`        | `path: str`             | switches `project_dir`, clears state                          |
| `set_packs`      | `names: list[str]`      | stores selected pack names for next turn                      |
| `slash`          | `tool: str, args: dict` | run a whitelisted tool directly (no model call)               |
| `compact`        | —                       | summarize + replace `messages` with a 2-entry summary         |
| `apply_writes`   | —                       | flushes `pending_writes` to disk                              |
| `reject_writes`  | —                       | drops `pending_writes`                                        |

### Server → client

| type             | fields                              | meaning                                          |
| ---------------- | ----------------------------------- | ------------------------------------------------ |
| `thinking`       | `content: str`                      | streamed reasoning chunk (`<think>` / reasoning) |
| `text`           | `content: str`                      | streamed assistant text chunk                    |
| `tool_call`      | `name: str, args: dict`             | model invoked a tool                             |
| `tool_result`    | `name: str, content: str`           | tool returned (may start with "Error")           |
| `pending_writes` | `writes: {path: content}`           | show apply/reject banner                         |
| `writes_applied` | `files: list[str]`                  | apply confirmation                               |
| `writes_rejected`| —                                   | reject confirmation                              |
| `compacted`      | `prior: int, summary: str`          | conversation replaced with summary               |
| `usage`          | `total: int, max: int`              | context bar update                               |
| `info`           | `content: str, project?: str`       | status message; if `project` set, UI retitles    |
| `error`          | `content: str`                      | red-flagged error line                           |
| `reset_complete` | —                                   | clear UI after a reset                           |
| `done`           | —                                   | turn over; re-enable input                       |

The `done` event is guaranteed by the `finally` block in `agent_loop`, even on
cancel or exception. The client relies on this to un-gray the input.

## The agent loop

`_agent_loop_impl(ws, user_content, messages, state, selected_packs)`:

1. Re-reads `prompt.md` (so edits take effect without restart).
2. Substitutes `{project_dir}` and `{file_tree}`.
3. Appends `## Reference Documentation` block built from `selected_packs`.
4. Builds `loop_messages = [system] + messages` (the already-appended user turn is part of `messages`).
5. Opens a streaming `chat.completions.create` with `TOOL_DEFINITIONS`.
6. Accumulates `reasoning_buf`, `content_buf`, and `tool_calls_buf` across chunks.
7. If no tool calls in the final chunk → strip `<think>` blocks, append to `messages`, exit.
8. Else append assistant-with-tool-calls to `loop_messages`, execute each tool, append `{role: "tool", ...}` results, loop.
9. Cap: `MAX_ITERATIONS = 25`. Past that, emits an error and stops.

`state["usage_tokens"]` is captured from the streaming `chunk.usage` and
emitted by the outer wrapper in `finally`, so the context bar updates even
on cancel.

## Tool definitions

Each entry in `TOOL_DEFINITIONS` is an OpenAI-style function schema:

```python
{
    "type": "function",
    "function": {
        "name": "…",
        "description": "…",
        "parameters": {"type": "object", "properties": {...}, "required": [...]},
    },
}
```

Dispatched by name in `execute_tool(name, args, project_dir)`.

Current tools:

- `read_file(path, start_line?, end_line?)` — 100k char cap, records `read_files`.
- `write_file(path, content)` — queues into `pending_writes`, never touches disk.
- `edit_file(path, old_string, new_string)` — exact-string replace, uniqueness enforced, chains with pending writes.
- `search(pattern, path?, glob?)` — regex, up to 50 matches, skips bin/vendor dirs.
- `list_symbols(path)` — defs/classes/headings + line numbers. Cheap skim before a targeted read.
- `list_dir(path)` — directory listing, hides dotfiles.
- `list_context_files()` — dumps `read_files`.
- `run_bash(command)` — allowlist in `config.ALLOWED_COMMANDS`, rejects shell metachars, runs with `shell=False` + `shlex.split`, 30s timeout, 5k output cap.

## Adding a new tool

1. Append a new entry to `TOOL_DEFINITIONS` (name + JSON-schema params).
2. Add an `elif name == "your_tool":` branch in `execute_tool` returning a string.
3. Restart the server (no auto-reload).
4. If the tool takes a path argument, guard with `path.is_relative_to(project_dir.resolve())`.
5. If it queues a change, write into `pending_writes` and return `"Queued …: {path}"` — the client renders the apply/reject banner automatically when the loop ends.

## Slash commands

User-typed input starting with `/` is intercepted client-side (see `handleSlash`
in app.js) and dispatched via a `slash` WS message. The server runs the named
tool *directly* — the model is never called, nothing is added to `messages`.

Whitelisted tools for slash: `read_file`, `search`, `list_dir`, `list_symbols`.
Anything else returns an error. Destructive tools (`write_file`, `edit_file`,
`run_bash`) are intentionally not reachable this way.

## Compact

On a `{type: "compact"}` message the server:

1. Cancels any running agent task and awaits it.
2. Calls the model once, non-streaming, asking for a 5–10 bullet summary of `messages`.
3. Replaces `messages` with `[{user: "Summary of prior conversation:"}, {assistant: summary}]`.
4. Emits a `compacted` event with the count of prior messages and the summary text.

The model continues the session on the next turn with the summary as its only
prior context. Pending writes, read-files, and pack selection are untouched.

## Security guards

- **Path escape**: every path is `(project_dir / arg).resolve()` then checked against `project_dir.resolve()`.
- **Pack name injection**: `Path(name).name` strips any separators before building the `.md` filename.
- **Bash**: metacharacter block + allowlist + `shell=False`. Do not loosen without thought.
- **XSS**: tool names/args/results go through `escapeHtml()` on the client. Don't bypass that helper.

## State that resets

| Action          | `messages` | `pending_writes` | `read_files` | `selected_packs` | `project_dir` |
| --------------- | :--------: | :--------------: | :----------: | :--------------: | :-----------: |
| `set_dir`       | ✓          | ✓                | ✓            | —                | →new          |
| `reset`         | ✓          | ✓                | ✓            | —                | —             |
| WS disconnect   | —          | ✓                | ✓            | —                | —             |
| `set_packs`     | —          | —                | —            | →incoming        | —             |

(`✓` = cleared. `selected_packs` intentionally survives to avoid re-picking chips after a reset.)
