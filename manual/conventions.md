# Conventions

Keep these in mind when changing the harness.

## Scope

- **Single user, single process, localhost.** No auth. No multi-tenant. Module globals are fine.
- **No build step on the client.** Edit `static/*.js|css|html`, hard-refresh.
- **No auto-reload on the server.** Edit `server.py` or `tools.py`, restart uvicorn.
- **`prompt.md` is hot.** Re-read every turn — no restart needed for prompt tweaks.
- **Packs are hot.** `/packs` scan runs on every request.

## Security guards that must stay

Nothing here runs behind auth, but Gemma can still write tool-call JSON that
includes shell commands or path traversal. These guards catch that:

- **Path escape** (`read_file`, `edit_file`, `write_file`, `list_dir`, `search`):
  `(project_dir / arg).resolve()` then `.is_relative_to(project_dir.resolve())`.
  Don't skip this on any new path-taking tool.
- **Pack names**: `Path(name).name` strips separators before filename construction.
- **`run_bash`**: three layers — (1) metacharacter reject `; & | \` $ > < \n`, (2) `shlex.split` + `shell=False`, (3) allowlist from `config.ALLOWED_COMMANDS`. Don't loosen without thought.
- **XSS**: `escapeHtml()` wraps every tool arg/result/filename before innerHTML.

## Writes are queued, not applied

Tools never touch disk. `write_file` and `edit_file` mutate
`pending_writes: dict[str, str]`. The server emits a `pending_writes` event at
the end of the turn; the UI shows an apply/reject banner. Disk writes happen
only on `apply_writes`.

- Gemma should say "queued {path}" once and stop — don't call list/read to
  confirm. The banner is user-facing confirmation.
- `edit_file` chains with pending writes — editing a file you already queued
  amends the pending version, not the on-disk one.

## Iteration cap

`MAX_ITERATIONS = 25` in `_agent_loop_impl`. If Gemma loops (repeats the same
tool, re-plans endlessly), the loop stops with an error message. If you raise
this, you are probably papering over a prompt or tool-description problem.

## `<think>` blocks

Gemma's reasoning is streamed in two ways:
1. OpenAI `reasoning` field in `model_extra` → server forwards as `thinking` events.
2. Inline `<think>…</think>` blocks in content.

`strip_think()` removes (2) before appending to `messages` so it doesn't
leak into the next turn's prompt.

## Prompt + packs pattern

System prompt = `prompt.md` + file tree + (optional) `## Reference Documentation`
block built from selected packs.

- Global packs live in `packs/*.md`.
- Project-scoped packs live in `<project_dir>/manual/*.md` and override globals on name collision.
- `readme.md` in either folder is skipped (it's documentation about the folder, not a pack).
- Pack files should be terse and factual. Gemma is small; verbose prose dilutes the signal.

## Prompt authoring

- Short imperatives > paragraphs.
- List tool-specific rules ("prefer `edit_file` over `write_file` for small changes") in `prompt.md`, not tool descriptions — the prompt has global priority.
- Tool descriptions tell Gemma what the tool *does*; the system prompt tells her when to use it.

## Testing changes

- No test suite. Manual verification:
  - Set the project dir to this harness, ask Gemma to read `server.py` → confirm context bar updates, file appears in "in context" panel.
  - Ask for a small edit → confirm `edit_file` queues with a diff preview, banner appears, apply writes to disk.
  - Hit stop mid-stream → confirm input re-enables and context bar updates.
  - Toggle a pack chip → confirm it persists across reset, clears only on `set_dir`.
- Parse checks before restarting: `python -c "import ast; ast.parse(open('server.py').read())"`.

## File naming

- Knowledge pack files: kebab-case (`discord-bot-api.md`), displayed as spaced text in chips.
- Manual files: this folder — plain nouns (`server.md`, `ui.md`), no prefix.
