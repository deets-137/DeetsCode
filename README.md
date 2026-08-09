# DeetsCode

A local agent harness for Ollama-served models: a FastAPI server with a streaming
tool-calling loop, a browser UI whose panels the model can rearrange through its own
tool calls, and an optional Discord bridge that turns a phone into a remote control
for local development.

**Status:** personal project — single-user, no auth, binds `127.0.0.1`. Not hardened for
multi-tenant or production use. · **Platform:** Python server + browser UI + Discord bot

## What it does

- **Tool-calling agent loop** — 49 tool definitions across 5 mode-gated packs, dispatched
  from native OpenAI-format streaming deltas, capped at 25 iterations per turn. The pack
  reloads every iteration, so switching modes takes effect mid-session.
- **Context management** — tool results older than the three most recent are replaced with
  elided stubs to hold long sessions inside the context window; a manual `compact` collapses
  history into a summary.
- **Self-arranging UI** — 9 of the 16 core tools are layout tools. The model scores and
  repositions its own panels ([docs/tileflow.md](docs/tileflow.md)), demoting idle ones to
  an icon tray.
- **Discord bridge** — 20 slash commands over the same WebSocket the browser uses, with one
  conversation per channel.

## Tool packs

| Mode | Tools | Notes |
|---|---:|---|
| core (always loaded) | 16 | file reads, dice, manuals, layout control, task ledger |
| coding | 8 | write/edit/insert, search, symbols, `run_command` |
| chess | 8 | game state via python-chess; the model never rules on legality |
| dnd | 6 | campaign ledger, scenes, combat |
| blog | 11 | post CRUD, publish, media, TMDB/iTunes lookup, comments |

Most tools the model can call are read-only or state-scoped. Path-taking tools resolve
against the project directory and reject escapes. **`run_command` is not sandboxed** — it
runs `subprocess.run(..., shell=True)` with a 120-second timeout and a 5,000-character
output cap, and nothing else. Only point it at directories you'd hand to a shell.

## Architecture

- **`server.py`** — FastAPI. 32 HTTP routes plus one WebSocket carrying token-by-token
  `thinking` / `text` / `tool_call` / `tool_result` frames. A streaming state machine splits
  `<think>` and `<system>` blocks out of the visible channel, including when a tag is split
  across chunks.
- **`static/`** — vanilla JS, no framework and no build step. Three script files, loaded
  directly. Panels are declared in JSON with display hints and trust tiers.
- **`panels/`** · **`apps/`** — 16 panels; apps are multi-panel bundles with per-instance
  SQLite state and schema versioning ([docs/app_harness.md](docs/app_harness.md)).
- **`tools/`** — the packs above, registered through a mode map that raises on name collision.
- **`prompts/`** — one system prompt per mode, re-read from disk each turn so edits apply
  without a restart.
- **`core/storage.py`** — SQLite, 8 tables, additive-only DDL.

Model resolution self-heals: if the configured model isn't installed, the server falls back
to the first one Ollama reports rather than failing to start. UI model picks persist across
restarts.

## Stack

Python · FastAPI · uvicorn · the `openai` SDK pointed at Ollama's OpenAI-compatible endpoint ·
WebSockets · SQLite · python-chess · discord.py · vanilla ES6

## Running it

```bash
ollama pull qwen3:8b
pip install -r requirements.txt
cp config.example.py config.py     # model, port, temperature
python server.py                   # http://127.0.0.1:8000
```

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

Chess mode is built on Niklas Fiekas's [python-chess](https://github.com/niklasf/python-chess)
and [web-boardimage](https://github.com/niklasf/web-boardimage). See
[docs/special_thanks.md](docs/special_thanks.md).

## License

MIT — see [LICENSE](LICENSE).
