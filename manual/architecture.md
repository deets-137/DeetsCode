# Harness architecture

The harness is a local single-user agent. User types in the browser (or Discord
via `discord_bot.py`) → FastAPI server relays to a local Ollama model
(OpenAI-compatible API) → model emits tool calls → server executes them →
result streams back.

Modes swap the system prompt AND the available tool pack together. DeetsCode
(coding) is the only live mode — the chess/dnd/blog packs were deleted Aug
2026 pending redesign. One process, one user, one active mode per session.

## File map

```
server.py         FastAPI app. WebSocket handler, HTTP routes, agent loop.
paths.py          Single source of truth for filesystem paths. Every module
                  imports from here instead of deriving paths locally. Add
                  new constants via the `register_path` core tool; don't
                  hand-edit. See CLAUDE.md.
tools/            Tool package (see manual/tools.md).
  __init__.py       load_tools(mode) — returns (schemas, dispatcher).
  core.py           Always-loaded: read_file, list_dir, update_task,
                    list_manual, load_manual, register_path, plus the
                    consolidated `layout` tool (get/panels/state/pin/unpin/
                    floor/recompute/presets). Shared state.
  coding.py         DeetsCode pack: write_file, edit_file, search,
                    list_symbols, list_context_files, run_command.
panels/           Self-contained UI panels (see docs/panels.md); loader.py is
                  discovery + layout schema + tier-3 rendering.
storage.py        SQLite wrapper: sessions, games, moves, events, system_log.
discord_bot.py    Alternate frontend (dormant). Shares server-side logic.
bot_cogs/         Bot helpers: bot_media.py per-tool media extractors,
                  notes.py, stats.py. Dormant with the bot.
config.py         MODEL, OLLAMA_BASE_URL, HOST, PORT, TEMPERATURE, etc.
CLAUDE.md         Project notes for Claude sessions: paths.py rule, modes
                  status, panel-system pointers.
prompts/          Per-mode prompt files (DeetsCode.md only today).
                  All have {project_dir} and {file_tree} slots. server.py's
                  load_prompt_template() reads prompts/<mode>.md per turn.
manual/           Project-scoped manual docs (this project's self-docs;
                  loaded on demand via list_manual/load_manual).
layout/           panel_layout.json — the slot sheet (schema v3: which panel
                  is in which of the four slots). See docs/slots.md.
static/
  index.html      Single-page UI shell.
  app.js          WS singleton, chat DOM, mode switching.
  panel-shell.js  The slot shell: anchor column + four slots, the picker,
                  panel mount/unmount, harness.* API.
  style.css       Layout + glass design language.
  theme.css       Palette variables per theme.
requirements.txt  fastapi, uvicorn[standard], openai, httpx
```

## Lifecycle of a user turn

1. User hits Enter → `sendMessage()` in app.js → `{type: "message", content}` over WebSocket.
2. `websocket_endpoint()` dispatches by `type` → spawns `agent_loop(…)` as an asyncio task.
3. `agent_loop` wraps `_agent_loop_impl` and guarantees a `done` event fires in `finally`.
4. `_agent_loop_impl`:
   - Builds system prompt: `prompts/<mode>.md` + file tree (cached on `state`).
   - Calls `load_tools(mode)` to get the mode-gated schema + dispatcher.
   - Trims stale `role: tool` messages older than the 3 most recent (>400 chars → stub).
   - On turn 1, if no `[/]` step in `task.md` and the message isn't conversational, sets `tool_choice="required"` to force an initial `update_task` call.
   - Opens a streaming `chat.completions.create`.
5. Streamed chunks forward as `thinking` / `text` / `tool_call` / `tool_result` events. `<think>` and `<system>` blocks are filtered out by `ThinkStreamFilter` before reaching the client or `messages` (echoed system directives route to the thinking stream).
6. Each tool call dispatches to `execute(name, args, session_id, project_dir, user_name=user_name)`. Result goes to the client AND appended to `loop_messages` as `{role: "tool", …}`.
7. Loop exits when the model emits a response with no tool calls, or `MAX_ITERATIONS = 25` trips.
8. Final assistant message (think-filtered) appended to `messages`, session saved via `storage.save_session`.

## State that persists across turns

- `messages: list[dict]` — per-WebSocket conversation. Cleared on `set_dir` or `reset`. **`set_prompt` (mode switch) scrubs tool artifacts** from it but keeps user/assistant text.
- `pending_writes: dict[str, str]` — module global in `tools/core.py`. File writes queued until user Applies/Rejects.
- `read_files: list[str]` — module global in `tools/core.py`. Tracks which files the model has read.
- `selected_prompt: str` — per-WebSocket. Active mode ("DeetsCode" today).
- `project_dir: Path` — module global in `server.py`. The project the agent is working on.
- `state: dict` — per-turn only (created fresh in `agent_loop`). Holds the cached `file_tree` and streaming handles.
- SQLite (`storage.db`) — sessions, events, system_log (plus a legacy games/moves schema kept for future game modes). Persists across restarts.

## Where to intervene

- **Adding a tool:** see `manual/tools.md`. Core vs mode pack decision, then append to `TOOL_DEFINITIONS` + add a handler branch. Restart.
- **Adding a mode:** new `tools/<mode>.py` pack, register in `tools/__init__.py` `_MODE_PACKS`, drop `prompts/<mode>.md`. See `manual/tools.md` and `manual/writing_prompts.md`.
- **Adding a WS message type:** new branch in `websocket_endpoint`. Client sends via `ws.send(JSON.stringify(…))` and handles replies in `ws.onmessage`.
- **Changing prompt behavior:** edit `prompts/<mode>.md`. Re-read every turn — no restart.
- **Changing panel design:** `theme.css` (vars) and `style.css` (rules). Live on hard refresh.
- **Changing server behavior or tools:** `server.py` or anything under `tools/`, then restart uvicorn (no auto-reload).
- **Adding a filesystem path constant:** call the `register_path` core tool — don't hand-edit `paths.py`. See `manual/tools.md`.
- **Remote-controlling another session:** call `enqueue_session_control(target_session_id, action, **kwargs)` in `server.py`. See `manual/server.md` § Cross-session control.
- **Surfacing tool-produced media in Discord:** register an extractor in `bot_media.py`'s `EXTRACTORS` dict keyed by tool name.
