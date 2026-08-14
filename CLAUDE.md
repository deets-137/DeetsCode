# Harness — project notes for Claude

## Paths

All filesystem paths the harness uses (directories, files, per-project subdir/filename constants) live in **`paths.py`**. It is the single source of truth; no other module should compute paths with `Path(__file__).parent / "..."`.

**When you need a new path constant, use the `register_path` core tool — do not hand-edit `paths.py`.**

- Kinds: `dir` (exports a `Path` directory), `file` (exports a `Path` file), `str` (bare string for per-project relative names like `"task.md"`).
- `dir` / `file` values are relative to `HARNESS_ROOT`.
- The tool replaces the constant in place if it already exists, so re-registering is safe.

Call sites that currently go through `paths.py`: `core/storage.py` (DB_PATH), `server.py` (PROMPTS_DIR, SESSIONS_DIR, THEME_CSS, PANEL_LAYOUT_FILE), `discord_bot.py` (HARNESS_ROOT), `tools/core.py` (PROJECT_MANUAL_SUBDIR, TASK_FILENAME), `panels/loader.py` (PANELS_DIR, PANEL_LAYOUT_FILE). If you're editing any of these and the constant you need isn't there yet, call `register_path` first, then import from `paths`.

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
remains on disk as the server-side renderer for the Context flyout but has
no layout instance.

**Dev livereload**: a startup watcher in server.py broadcasts `dev_reload`
over the panel WS when anything under `static/`, `panels/`, or `apps/`
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

## Panels

The dev UI is a **panel system** — every visible block is a self-contained panel under `panels/<name>/`. Chat lives at [panels/chat/](panels/chat/) like everything else (tier 1, view.html); the legacy `dom_id` hoist was retired in phase 3a. The viewport is divided into named regions by `layout/panel_layout.json`; instances place panels into regions.

Boot-race detail worth knowing: app.js owns the WebSocket singleton and still routes WS messages directly into chat's DOM by id (`#response-text`, `#chat-textbox`, `#stop-btn`, `#dir-input`). Since the chat panel injects asynchronously, app.js's chat-DOM helpers (`appendResponse`, `setInputEnabled`, etc.) are null-tolerant and buffer through `_chatBootBuffer` until the panel mounts. The chat view's inline script calls `window._flushChatBootBuffer` / `window._applyPendingChatState` on mount to drain. If you ever decouple WS from app.js (multi-instance chat / true panel-owned subscriptions), that buffering layer can go.

**[docs/panels.md](docs/panels.md) is the modder reference** — manifest schema, endpoint table, JS API, WS event catalog, hello-world walkthrough. Read it first when adding or modifying a panel. **[docs/apps.md](docs/apps.md) covers the apps layer** — multi-panel bundles under `apps/<name>/` with shared per-instance state (`harness_ctx`), app-scoped events, a launcher, and a zip update endpoint; `apps/hello/` is the living reference and `apps/clock/` the dogfood migration. **[docs/diagnostics.md](docs/diagnostics.md) is the introspection catalog** — every console/HTTP/SQL surface for debugging a panel or asking "which panels are actually used." The notes below are orientation only.

### Layout & mode visibility

`layout/panel_layout.json` is **the** UI layout — Claude can edit it directly to rearrange the dev UI without touching panel code. `mode_overrides` hides regions/instances per harness mode (empty today — DeetsCode is the only mode; the schema stays for future modes).

Per-instance show/hide that depends on JS state lives in **two parallel tables** for now: `panel-shell.js`'s `INSTANCE_MODE_RULES` applies at hoist time (synchronous, reads `localStorage.harness-mode`, prevents flash on first paint), and `app.js`'s `_PANEL_HIDE_RULES` re-applies on mode change. Keep them in sync. Consolidating into one source of truth is on the cleanup list.

### Verifying a UI change

The dev_project.md verification rule: any phase with visible UI changes must be confirmed via `preview_start` + `preview_eval` (DOM inspection) or screenshot. Route-shape smoke tests via TestClient are not enough — they miss CSS/DOM regressions.
