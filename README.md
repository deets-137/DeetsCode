# DeetsCode

A local agent workspace for Ollama-served models, shipped as a frameless Tauri
(Windows) app: a FastAPI server with a streaming tool-calling loop, a panel UI
the model can rearrange through its own tool calls, and an optional Discord
bridge that turns a phone into a remote control for local development.

**Status:** personal project — single-user, no auth, binds `127.0.0.1`. Not hardened for
multi-tenant or production use. · **Platform:** Tauri shell + Python server + WebView2/browser UI

## What it does

- **Tool-calling agent loop** — mode-gated tool packs dispatched from native
  OpenAI-format streaming deltas, capped at 25 iterations per turn. The pack
  reloads every iteration, so switching modes takes effect mid-session.
- **Context management** — tool results older than the three most recent are replaced with
  elided stubs to hold long sessions inside the context window; a manual `compact` collapses
  history into a summary.
- **Self-arranging UI** — 9 of the 16 core tools are layout tools. The model scores and
  repositions its own panels ([docs/tileflow.md](docs/tileflow.md)), demoting idle ones to
  an icon tray.
- **Native shell** — a Tauri v2 frameless window (`npm run tauri dev`) that spawns the
  server and draws its own chrome: the DeetsCode title menu hosts theme/skin/model/mode
  flyouts and settings; the token design system (fonts → palette → theme → skin, ported
  from DeetsMusic/DeetsSolutions) is previewable at `/swatch.html`.
- **Discord bridge** — 20 slash commands over the same WebSocket the browser uses, with one
  conversation per channel (dormant until game modes return).

## Tool packs

| Mode | Tools | Notes |
|---|---:|---|
| core (always loaded) | 16 | file reads, manuals, layout control, task ledger |
| DeetsCode (coding) | 8 | write/edit/insert, search, symbols, `run_command` |

DeetsCode is the only mode today — the old chess/dnd/blog packs were removed in
Aug 2026 pending redesign; their last state lives in git history.

Most tools the model can call are read-only or state-scoped. Path-taking tools resolve
against the project directory and reject escapes. **`run_command` is not sandboxed** — it
runs `subprocess.run(..., shell=True)` with a 120-second timeout and a 5,000-character
output cap, and nothing else. Only point it at directories you'd hand to a shell.

## Architecture

- **`src-tauri/`** — the native shell: Tauri v2, `decorations: false`, dev command spawns
  `python server.py` and opens the UI in WebView2. The custom titlebar (drag region +
  traffic lights) lives in the web layer and stays hidden in a plain browser tab.
- **`server.py`** — FastAPI. 32 HTTP routes plus one WebSocket carrying token-by-token
  `thinking` / `text` / `tool_call` / `tool_result` frames. A streaming state machine splits
  `<think>` and `<system>` blocks out of the visible channel, including when a tag is split
  across chunks. A dev watcher broadcasts `dev_reload` when frontend sources change, so the
  open UI hot-reloads; `server.py` edits need an app restart.
- **`static/`** — vanilla JS, no framework and no build step. Three script files, loaded
  directly. Panels are declared in JSON with display hints and trust tiers. Styling is
  three token tiers on `<html>` (`data-theme` × `data-skin`, self-registering) plus
  bundled SIL-OFL fonts, all local — the webview works offline.
- **`panels/`** · **`apps/`** — self-contained panels; apps are multi-panel bundles with
  per-instance SQLite state and schema versioning ([docs/app_harness.md](docs/app_harness.md)).
- **`tools/`** — the packs above, registered through a mode map that raises on name collision.
- **`prompts/`** — one system prompt per mode, re-read from disk each turn so edits apply
  without a restart.
- **`core/storage.py`** — SQLite, 8 tables, additive-only DDL.

Model resolution self-heals: if the configured model isn't installed, the server falls back
to the first one Ollama reports rather than failing to start. UI model picks persist across
restarts.

## Stack

Rust (Tauri 2) · Python · FastAPI · uvicorn · the `openai` SDK pointed at Ollama's
OpenAI-compatible endpoint · WebSockets · SQLite · discord.py · vanilla ES6

## Running it

```bash
ollama pull qwen3:8b
pip install -r requirements.txt
cp config.example.py config.py     # model, port, temperature
npm install                        # one-time: Tauri CLI
npm run tauri dev                  # native app (spawns the server itself)
```

Or headless / browser-only: `python server.py`, then open `http://127.0.0.1:8000`.

**Tip:** to keep `__pycache__` directories from cluttering the repo, set
`PYTHONPYCACHEPREFIX=.pycache` in your shell — bytecode for every module
goes under one out-of-tree directory instead of polluting each package.

### Discord bridge

`pip install discord.py websockets python-dotenv`, then set env vars (or `.env`):

- `DISCORD_TOKEN` — required; the bot refuses to start without it
- `GAME_CHANNEL_IDS` — channels where the bot answers every message; it responds to
  @mentions anywhere regardless
- `DISCORD_GUILD_ID` — guild for instant slash-command sync; omit for global

`/emergency` is a kill switch with a dry-run preview and a typed confirmation, targeting the
session, Ollama, the harness, the bot, or all of them.

## Documentation

[manual/architecture.md](manual/architecture.md) · [manual/tools.md](manual/tools.md) ·
[docs/panels.md](docs/panels.md) · [docs/tileflow.md](docs/tileflow.md) ·
[docs/apps.md](docs/apps.md)

## Acknowledgements

See [docs/special_thanks.md](docs/special_thanks.md).

## License

MIT — see [LICENSE](LICENSE).
