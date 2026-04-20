# Server & tool layer

Everything in `server.py` and the `tools/` package. No auth — single process,
single user, state in module globals + per-WebSocket locals + SQLite.

## HTTP routes

| Method | Path         | Returns                                     | Caller              |
| ------ | ------------ | ------------------------------------------- | ------------------- |
| GET    | `/tree`      | `{tree: [...], root: str}` JSON of files    | `refreshTree()`     |
| GET    | `/packs`     | `{packs: [{name, chars, scope}]}`           | `refreshPacks()`    |
| GET    | `/pending`   | `{writes: {path: content}}` (debug)         | manual curl only    |
| GET    | `/api/prompts` | `{prompts: [name, …]}`                    | mode picker         |
| GET    | `/models`    | Ollama model list                           | model picker        |
| WS     | `/ws`        | bidirectional JSON events                   | `app.js connect()`  |
| GET    | `/*`         | static files under `static/`                | browser             |

## WebSocket protocol

All frames are JSON. Client sends `{"type": "...", ...}`, server sends events
with a `type` the client's `ws.onmessage` switches on.

### Client → server

| type             | fields                      | effect                                                        |
| ---------------- | --------------------------- | ------------------------------------------------------------- |
| `hello`          | `session_id?: str`          | restore prior session or start fresh                          |
| `message`        | `content: str`              | new user turn; cancels any in-flight task, starts agent loop  |
| `cancel`         | —                           | cancels current agent task                                    |
| `reset`          | —                           | cancels task + clears messages/pending/read-files             |
| `set_dir`        | `path: str`                 | switches `project_dir`, clears state                          |
| `set_packs`      | `names: list[str]`          | stores selected pack names for next turn                      |
| `set_prompt`     | `prompt: str`               | switches mode; **scrubs tool artifacts from history**         |
| `slash`          | `tool: str, args: dict`     | run a whitelisted tool directly (no model call)               |
| `compact`        | —                           | summarize + replace `messages` with a 2-entry summary         |
| `apply_writes`   | —                           | flushes `pending_writes` to disk                              |
| `reject_writes`  | —                           | drops `pending_writes`                                        |

### Server → client

| type             | fields                              | meaning                                          |
| ---------------- | ----------------------------------- | ------------------------------------------------ |
| `thinking`       | `content: str`                      | streamed reasoning chunk (OpenAI `reasoning`)    |
| `text`           | `content: str`                      | streamed assistant text chunk                    |
| `tool_call`      | `name: str, args: dict`             | model invoked a tool                             |
| `tool_result`    | `name: str, content: str`           | tool returned (may start with "Error")           |
| `pending_writes` | `writes: {path: content}`           | show apply/reject banner                         |
| `writes_applied` | `files: list[str]`                  | apply confirmation                               |
| `writes_rejected`| —                                   | reject confirmation                              |
| `compacted`      | `prior: int, summary: str`          | conversation replaced with summary               |
| `usage`          | `total: int, max: int`              | context bar update                               |
| `ctx_length`     | `max: int`                          | emitted on connect                               |
| `info`           | `content: str, project?: str`       | status message; if `project` set, UI retitles    |
| `hello_ack`      | `restored: bool, messages: int, prompt: str` | session handshake                        |
| `error`          | `content: str`                      | red-flagged error line                           |
| `reset_complete` | —                                   | clear UI after a reset                           |
| `done`           | —                                   | turn over; re-enable input                       |

`done` is guaranteed by the `finally` block in `agent_loop`, even on cancel or
exception. The client relies on this to un-gray the input.

## The agent loop

`_agent_loop_impl(ws, user_content, messages, state, selected_packs, selected_prompt, session_id, user_id)`:

1. Reads `prompts/<selected_prompt>.md` (so edits take effect without restart).
2. Substitutes `{project_dir}` and `{file_tree}` (tree is cached on `state` — built once per turn, not per iteration).
3. Appends a `## Reference Documentation (on-demand)` manifest — pack names and `## ` section headings only, NOT bodies.
4. `tool_defs, execute = load_tools(selected_prompt)` — re-loaded every iteration so `/mode` changes take effect mid-session without a restart.
5. Builds `loop_messages = [system] + messages`.
6. Per iteration:
   - `_trim_stale_tool_results(loop_messages)` — older `role: tool` bodies (>400 chars, not in the most recent 3) replaced with a stub.
   - `force_tool = iteration == 1 AND task.md has no [/] step AND message is not conversational`. If true, `tool_choice="required"`. Otherwise `"auto"`.
   - Open streaming `chat.completions.create` with `tool_defs`.
7. Accumulates `reasoning_buf`, `content_buf`, `tool_calls_buf`. `<think>…</think>` blocks inside `content` are filtered by `ThinkStreamFilter` before client and before append.
8. If no tool calls in the final chunk → append assistant text to `messages`, save session, exit.
9. Else append assistant-with-tool-calls to `loop_messages`, execute each tool, append `{role: "tool", content}` results, loop.
10. Cap: `MAX_ITERATIONS = 25`. Past that, emits an error and stops.

`state["usage_tokens"]` comes from `chunk.usage` on streams that include it,
emitted by the outer wrapper in `finally`.

## Tool definitions

Schemas live in `tools/core.py` (always loaded) and per-mode packs in
`tools/coding.py`, `tools/chess.py`, etc. Each is an OpenAI-style function:

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

Dispatched by name via the unified signature:

```python
execute(name, args, session_id, project_dir, user_id=None) -> str
```

Core tools ignore `session_id` / `user_id`. Game packs use them for
per-channel state and per-player action enforcement.

### Current tools

**Core (always loaded):**
- `read_file(path, start_line?, end_line?)` — 100k char cap, line-numbered `N<TAB>` output, records `read_files`.
- `list_dir(path)` — directory listing, hides dotfiles.
- `roll_dice(sides, count?, modifier?, advantage?, label?)` — instant probabilistic outcomes.
- `update_task(content?)` — writes `task.md` (markdown checklist). Empty `content` returns the current file.
- `list_packs()` — manifest of available reference packs and their sections.
- `load_pack(name, section?)` — pull a pack (or one `## ` section) into context.

**DeetsCode pack (mode = "DeetsCode"):**
- `write_file(path, content)` — queues into `pending_writes`, never touches disk.
- `edit_file(path, old_string, new_string)` — exact-string replace, uniqueness enforced, chains with pending writes. Defensively strips `N<TAB>` prefixes.
- `search(pattern, path?, glob?)` — regex, up to 50 matches, skips bin/vendor dirs.
- `list_symbols(path)` — defs/classes/headings + line numbers.
- `list_context_files()` — dumps `read_files`.
- `run_command(command)` — allowlisted, metachar-rejected, `shell=False` + `shlex.split`, timeout + output cap.

**Chess pack (mode = "chess"):** new_game, move, board, resign, etc. See `tools/chess.py`.

## Adding a new tool / new mode

See `manual/tools.md` for the full checklist. Short version:

- **New tool:** edit `tools/core.py` (always on) or the relevant mode pack. Append a schema to `TOOL_DEFINITIONS`, add a handler branch in `execute_tool`. Path-taking tools need the `.is_relative_to(project_dir.resolve())` guard. Restart.
- **New mode:** new `tools/<mode>.py`, register in `_MODE_PACKS` in `tools/__init__.py`, drop `prompts/<mode>.md`.

## Slash commands

User-typed input starting with `/` is intercepted client-side and dispatched
as a `slash` WS message. The server runs the named tool directly — the model
is never called, nothing is added to `messages`. See `_slash_execute` in
`server.py` for the allowlist. Destructive/write-queuing tools are
intentionally not reachable this way.

## Compact

On `{type: "compact"}`:

1. Cancels any running agent task and awaits it.
2. Calls the model once, non-streaming, for a 5–10 bullet summary of `messages`.
3. Replaces `messages` with `[{user: "Summary of prior conversation:"}, {assistant: summary}]`.
4. Emits a `compacted` event.

Pending writes, read-files, and pack selection are untouched.

## Security guards

- **Path escape**: every path is `(project_dir / arg).resolve()` then checked against `project_dir.resolve()`.
- **Pack name injection**: `Path(name).name` strips any separators before building the `.md` filename.
- **run_command**: metachar reject + allowlist + `shell=False`. Do not loosen without thought.
- **XSS**: tool names/args/results go through `escapeHtml()` on the client. Don't bypass that helper.

## State that resets

| Action          | `messages`    | `pending_writes` | `read_files` | `selected_packs` | `selected_prompt` | `project_dir` |
| --------------- | :-----------: | :--------------: | :----------: | :--------------: | :---------------: | :-----------: |
| `set_dir`       | ✓             | ✓                | ✓            | —                | —                 | →new          |
| `reset`         | ✓             | ✓                | ✓            | —                | —                 | —             |
| `set_prompt`    | tool calls scrubbed | —          | —            | —                | →new              | —             |
| WS disconnect   | —             | ✓                | ✓            | —                | —                 | —             |
| `set_packs`     | —             | —                | —            | →incoming        | —                 | —             |

(`✓` = fully cleared. `set_prompt` preserves user/assistant text turns but drops `role: tool` messages and `tool_calls` fields on assistant messages.)
