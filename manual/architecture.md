# Harness architecture

The harness is a local single-user agent. User types in the browser (or Discord
via `discord_bot.py`) → FastAPI server relays to a local Ollama model
(OpenAI-compatible API) → model emits tool calls → server executes them →
result streams back.

Modes swap the system prompt AND the available tool pack together — DeetsCode
for coding, chess for chess, etc. One process, one user, one active mode per
session.

## File map

```
server.py         FastAPI app. WebSocket handler, HTTP routes, agent loop.
tools/            Tool package (see manual/tools.md).
  __init__.py       load_tools(mode) — returns (schemas, dispatcher).
  core.py           Always-loaded: read_file, list_dir, roll_dice, update_task,
                    list_packs, load_pack. Shared state.
  coding.py         DeetsCode pack: write_file, edit_file, search,
                    list_symbols, list_context_files, run_command.
  chess.py          Chess pack.
storage.py        SQLite wrapper: sessions, games, moves.
discord_bot.py    Alternate frontend. Shares server-side logic.
config.py         MODEL, OLLAMA_BASE_URL, HOST, PORT, TEMPERATURE, etc.
prompt.md         Base system prompt; has {project_dir} and {file_tree} slots.
prompts/          Per-mode prompt overrides (DeetsCode.md, chess.md, dnd.md).
packs/            Global knowledge packs (cross-project).
manual/           Project-scoped knowledge packs (this project's self-docs).
static/
  index.html      Single-page UI shell.
  app.js          All client logic: WS, rendering, packs, theme, input.
  style.css       Layout + glass design language.
  theme.css       Palette variables per theme.
requirements.txt  fastapi, uvicorn[standard], openai, python-chess, …
```

## Lifecycle of a user turn

1. User hits Enter → `sendMessage()` in app.js → `{type: "message", content}` over WebSocket.
2. `websocket_endpoint()` dispatches by `type` → spawns `agent_loop(…)` as an asyncio task.
3. `agent_loop` wraps `_agent_loop_impl` and guarantees a `done` event fires in `finally`.
4. `_agent_loop_impl`:
   - Builds system prompt: `prompts/<mode>.md` + file tree (cached on `state`) + a lazy **manifest** of selected packs (names + section headings only; bodies not inlined).
   - Calls `load_tools(mode)` to get the mode-gated schema + dispatcher.
   - Trims stale `role: tool` messages older than the 3 most recent (>400 chars → stub).
   - On turn 1, if no `[/]` step in `task.md` and the message isn't conversational, sets `tool_choice="required"` to force an initial `update_task` call.
   - Opens a streaming `chat.completions.create`.
5. Streamed chunks forward as `thinking` / `text` / `tool_call` / `tool_result` events. `<think>` blocks are filtered out by `ThinkStreamFilter` before reaching the client or `messages`.
6. Each tool call dispatches to `execute(name, args, session_id, project_dir, user_id=user_id)`. Result goes to the client AND appended to `loop_messages` as `{role: "tool", …}`.
7. Loop exits when the model emits a response with no tool calls, or `MAX_ITERATIONS = 25` trips.
8. Final assistant message (think-filtered) appended to `messages`, session saved via `storage.save_session`.

## State that persists across turns

- `messages: list[dict]` — per-WebSocket conversation. Cleared on `set_dir` or `reset`. **`set_prompt` (mode switch) scrubs tool artifacts** from it but keeps user/assistant text.
- `pending_writes: dict[str, str]` — module global in `tools/core.py`. File writes queued until user Applies/Rejects.
- `read_files: list[str]` — module global in `tools/core.py`. Tracks which files the model has read.
- `selected_packs: list[str]` — per-WebSocket. Which pack chips are currently on.
- `selected_prompt: str` — per-WebSocket. Active mode ("DeetsCode", "chess", etc).
- `project_dir: Path` — module global in `server.py`. The project the agent is working on.
- `state: dict` — per-turn only (created fresh in `agent_loop`). Holds the cached `file_tree` and streaming handles.
- SQLite (`storage.db`) — sessions, chess games, chess moves. Persists across restarts.

## Where to intervene

- **Adding a tool:** see `manual/tools.md`. Core vs mode pack decision, then append to `TOOL_DEFINITIONS` + add a handler branch. Restart.
- **Adding a mode:** new `tools/<mode>.py` pack, register in `tools/__init__.py` `_MODE_PACKS`, drop `prompts/<mode>.md`. See `manual/tools.md` and `manual/writing_prompts.md`.
- **Adding a WS message type:** new branch in `websocket_endpoint`. Client sends via `ws.send(JSON.stringify(…))` and handles replies in `ws.onmessage`.
- **Changing prompt behavior:** edit `prompts/<mode>.md` or `prompt.md`. Re-read every turn — no restart.
- **Changing panel design:** `theme.css` (vars) and `style.css` (rules). Live on hard refresh.
- **Changing server behavior or tools:** `server.py` or anything under `tools/`, then restart uvicorn (no auto-reload).
