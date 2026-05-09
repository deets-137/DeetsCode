# Harness — project notes for Claude

## Active project: panel system rebuild

**Read [dev_project.md](dev_project.md) first.** It is the durable plan for an
in-progress rewrite of the dev UI into a user-moddable panel system (hybrid
trust tiers, region-driven layout, OS-hub trajectory). All design decisions
are resolved; implementation starts at phase 0.5. If your task touches
`static/`, `index.html`, or anything UI-shaped, the answer is in there.

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

The dev UI is a **panel system** — every visible block (chat, settings, file tree, blog ops, etc.) is a self-contained panel under `panels/<name>/`. The viewport is divided into named regions by `layout/panel_layout.json`; instances place panels into regions. **`docs/dev_project.md` is the authoritative design doc** — read it before touching the system. What you need to know to add or modify a panel:

### Trust tiers

| Tier | Render path | Use when |
|------|-------------|----------|
| 0    | `<iframe src=manifest.url>` | external URL (YouTube, etc.) |
| 1    | direct DOM injection of `view.html` | host-served static HTML+JS |
| 2    | (reserved — subprocess) | not yet implemented |
| 3    | direct DOM injection of `server.py:view()` output | needs Python / harness state |

Tier 1 and 3 inject into a `.panel-content` wrapper — **host CSS wins** (no shadow DOM, deliberate). Inline `<script>` tags re-execute on every fetch via clone-and-replace.

### Adding a panel

1. `panels/<name>/panel.json` with the schema-v1 manifest (see `panels/ollama_ps/panel.json` for a tier-3 reference, `panels/clock/panel.json` for tier-0, `panels/slash_commands/panel.json` for tier-1).
2. For tier 1: `panels/<name>/view.html` (static fragment).
3. For tier 3: `panels/<name>/server.py` exporting `view() -> str` (HTML fragment). Re-imported on every request — module-level state is wiped, so persist via `panels/<name>/state.json` if needed.
4. Add an instance to `layout/panel_layout.json`: `{ "instance": "<name>", "panel": "<name>", "region": "<region-id>" }`.
5. Hot reload: `POST /api/panels/reload`. No server restart needed.

### Tier-3 → harness state contract

Tier-3 panels reach harness globals by `import server` (or `from tools import ...`) at call time. The loader re-imports the handler on every request, so reads are always live:

```python
def view():
    import server
    project_dir = server.project_dir   # always the current value
    ...
```

### `harness.*` JS API (panel-side)

Exposed by `static/panel-shell.js`, available to any direct-DOM panel:

- `harness.refresh(name, seconds)` — recurring poll. Re-fetches the panel's view at the given interval. Self-deregisters when the content node disappears.
- `harness.refreshNow(name)` — one-shot fetch. Use after a state-change action.
- `harness.subscribe(panel, event, callback)` — WS event bridge. `app.js`'s `ws.onmessage` fans out to subscribers via `harness._dispatch`. Subs are keyed by panel name and wiped on the next `fetchAndInject`, so re-renders don't leak listeners.

### Layout & mode visibility

`layout/panel_layout.json` is **the** UI layout — Claude can edit it directly to rearrange the dev UI without touching panel code. `mode_overrides` hides regions/instances per harness mode (e.g. `blog` mode hides the file tree). Per-instance show/hide that depends on JS state lives in `app.js`'s `_PANEL_HIDE_RULES` (queries by `[data-instance="..."]`).

### Verifying a UI change

The dev_project.md verification rule: any phase with visible UI changes must be confirmed via `preview_start` + `preview_eval` (DOM inspection) or screenshot. Route-shape smoke tests via TestClient are not enough — they miss CSS/DOM regressions.
