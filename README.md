# harness

A local, single-user agent harness for Ollama-served models. Browser UI + WebSocket
streaming + a small set of file/code tools, plus mode-specific tool packs for
chess, D&D, blog editing, and coding.

> **Status:** personal toy. Single-user, no auth, runs on `127.0.0.1`. Not hardened
> for multi-tenant or production use.

## What's in the box

- **`server.py`** — FastAPI server. Streams thinking + text tokens from any
  OpenAI-compatible endpoint (defaults to local Ollama). Hosts the static UI.
- **`static/`** — vanilla JS browser client: chat, file tree, packs, themes,
  task panel, dev controls.
- **`tools/`** — tool packs the model can call. `core.py` has file/edit/search
  primitives; `chess.py`, `blog.py`, `coding.py` are mode-specific.
- **`discord_bot.py`** — optional Discord bridge that proxies messages into the
  same WS pipeline (used for the D&D mode).
- **`prompts/`** — system prompts per mode (chess, blog, dnd, coding).
- **`paths.py`** — single source of truth for filesystem paths.

## Setup

```bash
# 1. Install Ollama and pull a model.
ollama pull qwen3:8b

# 2. Python deps.
pip install -r requirements.txt

# 3. Local config.
cp config.example.py config.py
# edit config.py if you want a different model / port / temperature

# 4. (Optional) Discord bot — set DISCORD_TOKEN in env or .env.

# 5. Run.
python server.py
# open http://127.0.0.1:8000
```

## Modes

The active mode controls which tool pack is loaded and which system prompt is used.
Switch modes from the dev panel in the UI, or via slash commands.

| Mode    | Tools                              | Notes                              |
|---------|------------------------------------|------------------------------------|
| coding  | file ops, search, run_command      | default                            |
| chess   | python-chess engine, board image   | rules-correct via python-chess     |
| dnd     | campaign state, dice, character ops| paired with the Discord bot        |
| blog    | TMDB lookup, media upload, locks   | edits a separate blog repo         |

## Acknowledgements

The chess mode is built entirely on Niklas Fiekas's
[python-chess](https://github.com/niklasf/python-chess) and
[web-boardimage](https://github.com/niklasf/web-boardimage). See
[special_thanks.md](special_thanks.md).

## License

MIT — see [LICENSE](LICENSE).
