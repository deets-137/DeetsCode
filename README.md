# DeetsCode

A local, single-user agent workspace for Ollama-served models, shipped as a
frameless Tauri (Windows) app: WebSocket streaming chat, a self-arranging
panel UI, and a small set of file/code tools.

> **Status:** personal toy. Single-user, no auth, runs on `127.0.0.1`. Not hardened
> for multi-tenant or production use.

## Vision

DeetsCode is meant to be **the hub for sitting at my desk** — one surface
that replaces the pile of browser tabs and desktop apps: coding, playing
music/video, checking on things. Everything lands here as a panel or an app,
not as another window somewhere else.

The second half of the idea: **the model is a first-class operator of the
workspace, not just a chat endpoint.** It sees the current layout in every
turn (a `<layout>` descriptor rides in the system prompt) and can command it
dynamically — pin panels, apply presets, bubble what matters right now via
tileflow state. The UI self-organizes around attention like a good desk,
rather than being a fixed dashboard.

Everything is built to keep growth cheap and mechanical, for me *or* for a
model doing it one-shot: self-contained panels with a manifest
([docs/panels.md](docs/panels.md)), zip-updatable app bundles with preserved
state ([docs/apps.md](docs/apps.md)), self-registering themes/skins, paths
through one registry.

When judging a UI change, the north star is: *does the workspace foreground
what currently matters and get out of the way otherwise?*

## What's in the box

- **`src-tauri/`** — the native shell: a Tauri v2 frameless window whose dev
  command spawns the Python server and opens the UI in WebView2. The custom
  titlebar hosts the DeetsCode menu (theme/skin/model/mode/settings).
- **`server.py`** — FastAPI server. Streams thinking + text tokens from any
  OpenAI-compatible endpoint (defaults to local Ollama). Hosts the static UI
  and a dev livereload watcher.
- **`static/`** — vanilla JS client. The UI is a panel system with a
  self-arranging bento layout ([docs/panels.md](docs/panels.md),
  [docs/tileflow.md](docs/tileflow.md)) that the model itself can rearrange
  via layout tools. Styling is the token system ported from
  DeetsMusic/DeetsSolutions (fonts → palette → theme → skin; preview at
  `/swatch.html`).
- **`panels/`** — self-contained UI panels (chat, files, tool log, youtube, …).
- **`apps/`** — multi-panel app bundles with shared per-instance state
  ([docs/apps.md](docs/apps.md)); `apps/hello/` is the reference app.
- **`tools/`** — tool packs the model can call. `core.py` has file/layout
  primitives; `coding.py` is the DeetsCode mode pack.
- **`discord_bot.py`** — optional Discord bridge that proxies messages into
  the same WS pipeline (dormant until game modes return).
- **`prompts/`** — system prompts per mode (currently DeetsCode only).
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

# 4. Node deps for the native shell (one-time).
npm install

# 5. Run as the native app (spawns the server itself):
npm run tauri dev

# ...or headless / browser-only:
python server.py
# open http://127.0.0.1:8000
```

Frontend edits (static/, panels/, apps/) hot-reload the open UI via the
server's dev watcher; `server.py` changes need an app restart.

**Tip:** to keep `__pycache__` directories from cluttering the repo, set
`PYTHONPYCACHEPREFIX=.pycache` in your shell — bytecode for every module
goes under one out-of-tree directory instead of polluting each package.

## Modes

The active mode controls which tool pack is loaded and which system prompt
is used (switch from the DeetsCode title menu). **DeetsCode** (coding) is
the only mode today — the old chess/dnd/blog packs were removed in Aug 2026
pending redesign; their last state lives in git history.

## Acknowledgements

See [docs/special_thanks.md](docs/special_thanks.md).

## License

MIT — see [LICENSE](LICENSE).
