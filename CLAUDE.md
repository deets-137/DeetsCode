# Harness — project notes for Claude

## Paths

All filesystem paths the harness uses (directories, files, per-project subdir/filename constants) live in **`paths.py`**. It is the single source of truth; no other module should compute paths with `Path(__file__).parent / "..."`.

**Add new constants by editing `paths.py` directly.** (The `register_path` tool
that used to own this was retired 2026-08-14 with the rest of the deck trim.)

- Kinds: `dir` (exports a `Path` directory), `file` (exports a `Path` file), `str` (bare string for per-project relative names like `"task.md"`).
- `dir` / `file` values are relative to `HARNESS_ROOT`.
- Never reassign `HARNESS_ROOT` — everything else hangs off it.

Call sites that currently go through `paths.py`: `core/storage.py` (DB_PATH), `server.py` (PROMPTS_DIR, SESSIONS_DIR, THEME_CSS, PANEL_LAYOUT_FILE), `discord_bot.py` (HARNESS_ROOT), `tools/core.py` (TASK_FILENAME), `panels/loader.py` (PANELS_DIR, PANEL_LAYOUT_FILE, APPS_DIR). If you're editing any of these and the constant you need isn't there yet, add it to `paths.py` first, then import from `paths`.

## Discord bot media

Per-tool image/link extraction lives in `bot_cogs/bot_media.py`. To surface media from a tool's results, add an extractor function and register it under the tool name in `EXTRACTORS`. No bot changes needed. `EXTRACTORS` is empty today — the chess extractors went with the game modes and no DeetsCode tool emits media yet.

## Modes

**Modes:** DeetsCode (coding) is the only live mode. The chess/dnd/blog
packs, prompts, and panels were deleted in Aug 2026 (git history has
their last state); all blog plumbing (blog_service, server.py WS
handlers, app.js module, CSS) was torn out with them. The Discord bridge
was de-gamed to match: it is now purely a way to drive a DeetsCode
session from a Discord channel (9 slash commands, one conversation per
channel). `bot_cogs/` holds only `bot_media.py`; the notes and stats cogs
were removed.

## Native shell (Tauri)

The harness runs as a frameless Windows app: `npm run tauri dev` from the
repo root (Tauri's `beforeDevCommand` spawns `python server.py`, waits for
`http://127.0.0.1:8000`, then opens the WebView2 window). Shell code lives in
`src-tauri/`; it is a bare Tauri v2 builder — no custom commands. The custom
titlebar in `static/index.html` is window chrome, not a panel: always visible
(it hosts the DeetsCode menu), while the traffic lights + drag region only
act when app.js detects `window.__TAURI__` and adds `html.is-tauri`. The
browser tab at `http://127.0.0.1:8000` remains fully supported.

**Settings live in the DeetsCode title menu** (top-left, DeetsMusic dropdown
pattern) — theme/skin flyouts (each item rendered in its own theme/skin via
its data-theme/data-skin attribute), model/mode selects, keep-history/
auto-apply dot-toggle rows, temp, and a Context flyout that mirrors the
`in_context_files` panel view while open. The old `panels/settings/` panel
was deleted; the menu markup in index.html carries the same element ids,
which app.js's re-callable initializers populate. `panels/in_context_files/`
remains on disk as the server-side renderer for the Context flyout *and* for
the Files tile's "In context" tab; it sets `"pool": false` so it is never
offered a slot of its own.

**The titlebar right side is a status strip** — the clock and Ollama's
GPU/CPU split (`GET /api/ollama/ps`, parsed in `core/ollama.py`). Both used
to be bento tiles; neither was ever worth one.

> **Gotcha, paid for once:** `core/ollama.py` shells out to `ollama ps`, and
> the endpoint polls on a timer. Two rules hold it together. (1) It runs via
> `asyncio.to_thread` — a blocking subprocess called inline from an `async def`
> freezes the whole event loop, and because uvicorn only logs on response
> completion, the symptom is every panel rendering blank with *nothing* in the
> log. (2) It captures to a temp file, not a pipe: with no daemon running,
> `ollama ps` auto-starts one, the daemon inherits the pipe's write handle and
> never closes it, so subprocess's reader threads never see EOF — and
> `subprocess.run`'s `timeout=` does **not** cover that final thread join, so
> it hangs forever. Don't "simplify" either back to a plain `capture_output`
> call.

**Dev livereload**: a startup watcher in server.py broadcasts `dev_reload`
over the panel WS when anything under `static/` or `panels/`
changes; app.js reloads the page (skipped mid-run). Edits to server.py
itself still need an app restart. Known gap: if the Tauri window dies
abnormally, the spawned `python server.py` can be orphaned holding port
8000 — kill it before relaunching.

## UI tokens (theme × skin)

Styling is token tiers on `<html>` (ported from DeetsMusic):
`static/fonts.css` (bundled @font-face, SIL OFL — see static/fonts/NOTICE.txt)
→ `static/palette.css` (raw paints) → `static/theme.css` (color roles per
`data-theme`) → `static/skin.css` (type/shape/material per `data-skin`).
Rules reference tokens, never raw hexes/px/font names; a skin never names a
color (point slots at theme roles or `color-mix()` them). The `[data-skin]`
base block in skin.css is the authoritative skin-token list; theme.css's
header comment lists the color roles. Legacy var names (`--response-text`,
`--glass-border`, …) are aliased in theme.css's shared `[data-theme]` block —
don't remove them while panels still use them. `/api/themes` + `/api/skins`
parse these files for the theme/skin pickers, so a new `[data-theme="x"]` /
`[data-skin="x"]` block is self-registering — but keep theme blocks flat
(the parser regex can't see past a nested rule). `static/index.html` carries
a pre-paint script mirroring app.js's `LEGACY_THEME_NAMES` — keep the two
maps in sync. `static/swatch.html` (served at `/swatch.html`) previews every
role × theme against the live sheets. See docs/panels.md § Looking native.

## Panels & slots

The dev UI is a **panel system** — every visible block is a self-contained panel under `panels/<name>/` — arranged by the **slot system**: an anchored chat column beside a 2×2 bento of four fixed slots (`nw`/`ne`/`sw`/`se`), one panel each, swapped by the user from the tile title. Chat lives at [panels/chat/](panels/chat/) like everything else; it is *anchored*, which is what keeps app.js's direct WS→chat-DOM routing out of the slot system's scope.

**[docs/slots.md](docs/slots.md) is the layout reference** — the four slots, the picker, the teardown contract, the summon bus, and a list of what Tileflow's deletion took with it. **[docs/panels.md](docs/panels.md) is the modder reference** — manifest schema, endpoint table, JS API, WS event catalog, hello-world walkthrough. Read it first when adding or modifying a panel. **[docs/diagnostics.md](docs/diagnostics.md) is the introspection catalog** — every console/HTTP/SQL surface for debugging a panel. **[docs/tileflow.md](docs/tileflow.md) is retired** — the scored engine that used to arrange the bento; nothing in it is live.

The notes below are orientation only.

### The pool and the layout

Every registered panel joins the **pool** — the list the picker offers — automatically. There is no layout edit to add a panel; `layout/panel_layout.json` (schema v3: `slots`, `anchored`, `mode_overrides`) only says which four are on screen at boot, and Claude can edit it directly. A panel opts out of the pool with `"pool": false` (only `in_context_files` does).

`GET /api/layout` is a *validating* read: a slot naming a panel that has been merged away or uninstalled falls back to a default and reports it in `warnings`. `PUT /api/layout` is stricter — four distinct pool panels or a 400.

Today's pool: `activity` (tool calls + a pending-writes banner), `files` (tree / in-context), `task_list`, `web`, `bot_ops`.

### Tiers, and what a panel is

Every panel is a flat folder under `panels/<name>/`. There are two live tiers:
**tier 1** (a static `view.html` fragment) and **tier 3** (a Python `server.py`
with a no-argument `view()` returning HTML). Tier 2 (subprocess) is a
placeholder that raises on load.

Deleted 2026-08-15, in one pass: **tier 0** (the sandboxed-iframe tier, with
its `url`/`iframe_attrs` manifest fields and the postMessage bridge), the
**apps layer** (`apps/`, multi-panel bundles with shared state via
`harness_ctx`, app-scoped events, and the `/api/apps*` routes), and the two
things built on them — the `clock` app and the `hello` reference app. No live
panel ever shipped on either primitive. Git history has them if embeds or
shared panel state are wanted back; don't reintroduce the vocabulary
piecemeal.

### The teardown contract

A slot swap **destroys a panel's DOM and re-runs its view**. WS subscriptions and `harness.refresh` timers are reversed by the shell; anything else a panel's inline script starts — `setInterval`, observers, document listeners — must be registered via `harness.onUnmount(panel, fn)`. `harness._subCounts()` is the leak canary: swap a slot 20× and assert nothing grows. `panels/files/` is the worked example.

### Boot race

app.js owns the WebSocket singleton and routes WS messages directly into chat's DOM by id (`#response-text`, `#chat-textbox`, `#stop-btn`, `#dir-input`). Since panels inject asynchronously, app.js's chat-DOM helpers are null-tolerant and buffer through `_chatBootBuffer` until the panel mounts; the chat view's inline script calls `window._flushChatBootBuffer` / `window._applyPendingChatState` on mount to drain. This survives the slot rework only because chat is anchored and never unmounts. Decoupling WS from app.js is the prerequisite for ever making chat slottable.

### Mode visibility

`mode_overrides` hides slots per harness mode (empty today — DeetsCode is the only mode; the schema stays for future modes). The parallel `INSTANCE_MODE_RULES` / `_PANEL_HIDE_RULES` tables are down to one: app.js's `_PANEL_HIDE_RULES`. The slot shell mounts from a server-resolved layout and applies `mode_overrides` in the same pass, so there is no longer a second table to keep in sync.

### Verifying a UI change

The dev_project.md verification rule: any phase with visible UI changes must be confirmed via `preview_start` + `preview_eval` (DOM inspection) or screenshot. Route-shape smoke tests via TestClient are not enough — they miss CSS/DOM regressions.
