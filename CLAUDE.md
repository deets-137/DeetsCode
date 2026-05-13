# Harness — project notes for Claude

## Paths

All filesystem paths the harness uses (directories, files, per-project subdir/filename constants) live in **`paths.py`**. It is the single source of truth; no other module should compute paths with `Path(__file__).parent / "..."`.

**When you need a new path constant, use the `register_path` core tool — do not hand-edit `paths.py`.**

- Kinds: `dir` (exports a `Path` directory), `file` (exports a `Path` file), `str` (bare string for per-project relative names like `"task.md"`).
- `dir` / `file` values are relative to `HARNESS_ROOT`.
- The tool replaces the constant in place if it already exists, so re-registering is safe.

Call sites that currently go through `paths.py`: `storage.py` (DB_PATH), `server.py` (PROMPTS_DIR, PACKS_DIR, SESSIONS_DIR, THEME_CSS, DND_SUBDIR, CAMPAIGN_STATE_FILENAME, PANEL_LAYOUT_FILE), `discord_bot.py` (HARNESS_ROOT, SAVES_DIR), `tools/core.py` (PACKS_DIR, PROJECT_MANUAL_SUBDIR, TASK_FILENAME), `panels/loader.py` (PANELS_DIR, PANEL_LAYOUT_FILE). If you're editing any of these and the constant you need isn't there yet, call `register_path` first, then import from `paths`.

## Discord bot media

Per-tool board/image extraction lives in `bot_media.py`. To surface images or links from a new game's tool results, add an extractor function and register it under the tool names in `EXTRACTORS`. No bot changes needed.

## Chess identity

Chess uses display names end-to-end — no Discord user ids. The envelope is `{"name": ..., "text": ...}`; chess state has `{white, black, fen, result}` where white/black are name strings or the literal `"computer"`.

## Panels

The dev UI is a **panel system** — every visible block is a self-contained panel under `panels/<name>/` **except `chat`**, which is still a legacy `dom_id` hoist (deferred because it owns the WS lifecycle that drives every other panel). The viewport is divided into named regions by `layout/panel_layout.json`; instances place panels into regions.

**[docs/panels.md](docs/panels.md) is the modder reference** — manifest schema, endpoint table, JS API, WS event catalog, hello-world walkthrough. Read it first when adding or modifying a panel. The notes below are orientation only.

### Layout & mode visibility

`layout/panel_layout.json` is **the** UI layout — Claude can edit it directly to rearrange the dev UI without touching panel code. `mode_overrides` hides regions/instances per harness mode (e.g. `blog` mode hides the file tree).

Per-instance show/hide that depends on JS state lives in **two parallel tables** for now: `panel-shell.js`'s `INSTANCE_MODE_RULES` applies at hoist time (synchronous, reads `localStorage.harness-mode`, prevents flash on first paint), and `app.js`'s `_PANEL_HIDE_RULES` re-applies on mode change. Keep them in sync. Consolidating into one source of truth is on the cleanup list.

### Verifying a UI change

The dev_project.md verification rule: any phase with visible UI changes must be confirmed via `preview_start` + `preview_eval` (DOM inspection) or screenshot. Route-shape smoke tests via TestClient are not enough — they miss CSS/DOM regressions.
