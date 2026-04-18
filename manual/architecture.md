# Harness architecture

The harness is a local single-user coding agent. User types in the browser →
FastAPI server relays to a local Ollama model (OpenAI-compatible API) → model
emits tool calls → server executes them → result streams back to the browser.

## File map

```
server.py         FastAPI app. WebSocket handler, HTTP routes, agent loop.
tools.py          Tool schemas + execute_tool() dispatcher. Read/edit/search/run_bash.
config.py         MODEL, OLLAMA_BASE_URL, ALLOWED_COMMANDS, HOST, PORT, TEMPERATURE.
prompt.md         System prompt template. Has {project_dir} and {file_tree} placeholders.
packs/            Global knowledge packs (cross-project).
manual/           Project-scoped knowledge packs (this project's self-docs).
static/
  index.html      Single-page UI shell.
  app.js          All client logic: WS, rendering, packs, theme, input.
  style.css       Layout + glass design language.
  theme.css       Palette variables per theme.
requirements.txt  fastapi, uvicorn[standard], openai.
```

## Lifecycle of a user turn

1. User hits Enter in the textarea → `sendMessage()` in app.js.
2. Client sends `{type: "message", content: "..."}` over WebSocket.
3. Server handler at `server.py websocket_endpoint()` dispatches based on `type`.
4. For `"message"`: creates asyncio task running `agent_loop(ws, content, messages, selected_packs)`.
5. `agent_loop` = wrapper around `_agent_loop_impl` that catches `CancelledError` and guarantees a `done` event fires in `finally`.
6. `_agent_loop_impl` builds the system prompt (prompt.md + file tree + pack contents), prepends to history, calls Ollama with streaming on.
7. Streamed chunks are forwarded as `thinking` / `text` / `tool_call` / `tool_result` events over the WS.
8. If any tool calls came back, each is dispatched to `execute_tool(name, args, project_dir)` in tools.py. Result is sent to the client AND appended to `loop_messages` as a `{role: "tool", ...}` entry.
9. Loop continues until the model emits a response with no tool calls, or `MAX_ITERATIONS = 25` trips.
10. Final assistant message (with `<think>` blocks stripped) is appended to `messages` for next turn's context.

## State that persists across turns

- `messages: list[dict]` — per-WebSocket conversation. Cleared on `set_dir` or `reset`.
- `pending_writes: dict[str, str]` — module global in tools.py. File writes queued until user Applies/Rejects.
- `read_files: list[str]` — module global in tools.py. Tracks which files the model has read (so `list_context_files` can surface them).
- `selected_packs: list[str]` — per-WebSocket. Which pack chips are currently on.
- `project_dir: Path` — module global in server.py. The project Gemma is working on.

## Where to intervene

- **Adding a tool:** extend `TOOL_DEFINITIONS` in tools.py and add an `elif name == "..."` in `execute_tool`.
- **Adding a WS message type:** new branch in the `while True` loop inside `websocket_endpoint`. Client sends via `ws.send(JSON.stringify(...))` and handles replies in `ws.onmessage`.
- **Changing prompt behavior:** edit prompt.md. It's re-read every turn, no restart needed.
- **Changing panel design:** theme.css (vars) and style.css (rules). Live on hard refresh, no restart.
- **Changing server behavior:** server.py, then restart (no auto-reload configured).
