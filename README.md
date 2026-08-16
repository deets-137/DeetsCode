# DeetsCode

A local agent workspace for llama.cpp-served models, shipped as a frameless Tauri
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
- **Slot UI** — an anchored chat column beside a 2×2 bento of four slots
  ([docs/slots.md](docs/slots.md)). You pick what goes in each one and it stays there; the
  layout persists server-side, so the model can rearrange it by editing one JSON file. This
  replaced a scored engine that repositioned panels on its own — the cleverness wasn't worth
  never knowing where anything would be.
- **Native shell** — a Tauri v2 frameless window (`npm run tauri dev`) that spawns the
  server and draws its own chrome: the DeetsCode title menu hosts theme/skin/model/mode
  flyouts and settings; the token design system (fonts → palette → theme → skin, ported
  from DeetsMusic/DeetsSolutions) is previewable at `/swatch.html`.
- **Discord bridge** — 9 slash commands over the same WebSocket the browser uses, with one
  conversation per channel: a phone as a remote control for a DeetsCode session.

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
- **`static/`** — vanilla JS, no framework and no build step. Two script files
  (`app.js`, `panel-shell.js`), loaded directly. Panels are declared in JSON with
  display hints and trust tiers. Styling is
  three token tiers on `<html>` (`data-theme` × `data-skin`, self-registering) plus
  bundled SIL-OFL fonts, all local — the webview works offline.
- **`panels/`** — self-contained panels, one folder each: a `panel.json` manifest
  plus either a static `view.html` (tier 1) or a `server.py` with `view()` (tier 3).
  See [docs/panels.md](docs/panels.md).
- **`tools/`** — the packs above, registered through a mode map that raises on name collision.
- **`prompts/`** — one system prompt per mode, re-read from disk each turn so edits apply
  without a restart.
- **`core/storage.py`** — SQLite, 8 tables, additive-only DDL.

Model resolution self-heals: if the configured model isn't available, the server falls back
to the first one llama-server reports rather than failing to start. UI model picks persist
across restarts.

## Stack

Rust (Tauri 2) · Python · FastAPI · uvicorn · the `openai` SDK pointed at llama-server's
OpenAI-compatible endpoint (router mode, Vulkan) · WebSockets · SQLite · discord.py ·
vanilla ES6

## Running it

The harness autostarts `llama-server` in router mode if it isn't already up
(see `LLAMA_SERVER_EXE` / `LLAMA_SERVER_ARGS` in config.py). Get llama.cpp
on PATH (Windows: the Vulkan release build; macOS: `brew install llama.cpp`,
Metal is the default) and either drop GGUFs in a `--models-dir` or pull one:

```bash
llama-server -hf <org/repo:quant>      # e.g. ggml-org/Qwen3-8B-GGUF:Q4_K_M
pip install -r requirements.txt
cp config.example.py config.py         # port, temperature; MODEL auto-picks
npm install                        # one-time: Tauri CLI
npm run tauri dev                  # native app (spawns the server itself)
```

Or headless / browser-only: `python server.py`, then open `http://127.0.0.1:8000`.

**Tip:** to keep `__pycache__` directories from cluttering the repo, set
`PYTHONPYCACHEPREFIX=.pycache` in your shell — bytecode for every module
goes under one out-of-tree directory instead of polluting each package.

### Discord bridge

`pip install discord.py websockets python-dotenv`, then `cp .env.example .env` and
fill it in. Every setting is commented there; the ones you'll actually touch:

- `DISCORD_TOKEN` — required; the bot refuses to start without it
- `CHANNEL_IDS` — channels where the bot answers every message; it responds to
  @mentions anywhere regardless
- `DISCORD_GUILD_ID` — guild for instant slash-command sync. Auto-detected when the
  bot is in exactly one guild; set it once it joins a second, or omit for global sync

`/emergency` is a kill switch with a dry-run preview and a typed confirmation, targeting the
session, llama-server, the harness, the bot, or all of them.

## Documentation

[manual/architecture.md](manual/architecture.md) · [manual/tools.md](manual/tools.md) ·
[docs/panels.md](docs/panels.md) · [docs/slots.md](docs/slots.md) ·
[docs/diagnostics.md](docs/diagnostics.md)

## Acknowledgements

See [docs/special_thanks.md](docs/special_thanks.md).

## License

MIT — see [LICENSE](LICENSE).
