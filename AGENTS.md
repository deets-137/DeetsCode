# Harness — project notes for Codex

## Paths

All filesystem paths the harness uses (directories, files, per-project subdir/filename constants) live in **`paths.py`**. It is the single source of truth; no other module should compute paths with `Path(__file__).parent / "..."`.

**Add new constants by editing `paths.py` directly.** (The `register_path` tool
that used to own this was retired 2026-08-14 with the rest of the deck trim.)

- Kinds: `dir` (exports a `Path` directory), `file` (exports a `Path` file), `str` (bare string for per-project relative names like `"task.md"`).
- `dir` / `file` values are relative to `HARNESS_ROOT`.
- Never reassign `HARNESS_ROOT` — everything else hangs off it.

Call sites that currently go through `paths.py`: `storage.py` (DB_PATH), `server.py` (PROMPTS_DIR, SESSIONS_DIR, THEME_CSS, PANEL_LAYOUT_FILE), `discord_bot.py` (HARNESS_ROOT, SAVES_DIR), `tools/core.py` (TASK_FILENAME), `panels/loader.py` (PANELS_DIR, PANEL_LAYOUT_FILE). If you're editing any of these and the constant you need isn't there yet, add it to `paths.py` first, then import from `paths`.

## Discord bot media

Per-tool board/image extraction lives in `bot_media.py`. To surface images or links from a new game's tool results, add an extractor function and register it under the tool names in `EXTRACTORS`. No bot changes needed.

## UI tokens (theme × skin)

Styling is three token tiers on `<html>` (ported from DeetsMusic):
`static/palette.css` (raw paints) → `static/theme.css` (color roles per
`data-theme`) → `static/skin.css` (type/shape/material per `data-skin`).
Rules reference tokens, never raw hexes/px/font names; a skin never names a
color (point slots at theme roles or `color-mix()` them). The `[data-skin]`
base block in skin.css is the authoritative skin-token list; theme.css's
header comment lists the color roles. Legacy var names (`--response-text`,
`--glass-border`, …) are aliased in theme.css's shared `[data-theme]` block —
don't remove them while panels still use them. `/api/themes` + `/api/skins`
parse these files for the settings pickers, so a new `[data-theme="x"]` /
`[data-skin="x"]` block is self-registering. See docs/panels.md § Looking
native.

## Panels

The dev UI is a **panel system** — every visible block is a self-contained panel under `panels/<name>/`. Chat lives at [panels/chat/](panels/chat/) like everything else (tier 1, view.html); the legacy `dom_id` hoist was retired in phase 3a. The viewport is divided into named regions by `layout/panel_layout.json`; instances place panels into regions.

Boot-race detail worth knowing: app.js owns the WebSocket singleton and still routes WS messages directly into chat's DOM by id (`#response-text`, `#chat-textbox`, `#stop-btn`, `#dir-input`). Since the chat panel injects asynchronously, app.js's chat-DOM helpers (`appendResponse`, `setInputEnabled`, etc.) are null-tolerant and buffer through `_chatBootBuffer` until the panel mounts. The chat view's inline script calls `window._flushChatBootBuffer` / `window._applyPendingChatState` on mount to drain. If you ever decouple WS from app.js (multi-instance chat / true panel-owned subscriptions), that buffering layer can go.

**[docs/panels.md](docs/panels.md) is the modder reference** — manifest schema, endpoint table, JS API, WS event catalog, hello-world walkthrough. Read it first when adding or modifying a panel. **[docs/diagnostics.md](docs/diagnostics.md) is the introspection catalog** — every console/HTTP/SQL surface for debugging a panel or asking "which panels are actually used." The notes below are orientation only.

### Layout & mode visibility

`layout/panel_layout.json` is **the** UI layout — Codex can edit it directly to rearrange the dev UI without touching panel code. `mode_overrides` hides regions/instances per harness mode (empty today — DeetsCode is the only mode; the schema stays for future modes).

Per-instance show/hide that depends on JS state lives in **two parallel tables** for now: `panel-shell.js`'s `INSTANCE_MODE_RULES` applies at hoist time (synchronous, reads `localStorage.harness-mode`, prevents flash on first paint), and `app.js`'s `_PANEL_HIDE_RULES` re-applies on mode change. Keep them in sync. Consolidating into one source of truth is on the cleanup list.

### Verifying a UI change

The dev_project.md verification rule: any phase with visible UI changes must be confirmed via `preview_start` + `preview_eval` (DOM inspection) or screenshot. Route-shape smoke tests via TestClient are not enough — they miss CSS/DOM regressions.
