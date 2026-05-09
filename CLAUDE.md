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

Call sites that currently go through `paths.py`: `storage.py` (DB_PATH), `server.py` (PROMPTS_DIR, PACKS_DIR, SESSIONS_DIR, THEME_CSS, LEGACY_PROMPT_FILE, DND_SUBDIR, CAMPAIGN_STATE_FILENAME), `discord_bot.py` (HARNESS_ROOT, SAVES_DIR), `tools/core.py` (PACKS_DIR, PROJECT_MANUAL_SUBDIR, TASK_FILENAME). If you're editing any of these and the constant you need isn't there yet, call `register_path` first, then import from `paths`.

## Discord bot media

Per-tool board/image extraction lives in `bot_media.py`. To surface images or links from a new game's tool results, add an extractor function and register it under the tool names in `EXTRACTORS`. No bot changes needed.

## Chess identity

Chess uses display names end-to-end — no Discord user ids. The envelope is `{"name": ..., "text": ...}`; chess state has `{white, black, fen, result}` where white/black are name strings or the literal `"computer"`.
