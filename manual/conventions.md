# Conventions

Keep these in mind when changing the harness.

## Scope

- **Single user, single process, localhost.** No auth. No multi-tenant. Module globals are fine.
- **No build step on the client.** Edit `static/*.js|css|html`, hard-refresh.
- **No auto-reload on the server.** Edit `server.py` or any file under `tools/`, restart uvicorn.
- **`prompts/<mode>.md` is hot.** Re-read every turn — no restart needed for prompt tweaks.
- **Manual docs are hot.** `list_manual` scans the folder per call. Bodies are not in the standing prompt — the model pulls them via `load_manual` on demand.
- **Panels/layout are hot-ish.** `POST /api/panels/reload` (or `/api/apps/reload`) rescans without a restart; any layout write broadcasts `layout_updated` and every tab re-syncs live.
- **Tool schema is hot-per-turn.** `load_tools(mode)` is called at the top of every agent-loop iteration, so `/mode` switches pick up a new schema without a restart.

## Security guards that must stay

Nothing here runs behind auth, but Gemma can still write tool-call JSON that
includes shell commands or path traversal. These guards catch that:

- **Path escape** (`read_file`, `edit_file`, `write_file`, `list_dir`, `search`, `load_manual`):
  `(project_dir / arg).resolve()` then `.is_relative_to(project_dir.resolve())`.
  Don't skip this on any new path-taking tool.
- **Manual/preset names**: `Path(name).name` strips separators before filename construction.
- **`run_command`**: metacharacter reject + `shlex.split` + `shell=False` + a short allowlist in `tools/coding.py`. Don't loosen without thought.
- **XSS**: `escapeHtml()` wraps every tool arg/result/filename before innerHTML.

## Writes are queued, not applied

Tools never touch disk. `write_file` and `edit_file` mutate
`pending_writes: dict[str, str]`. The server emits a `pending_writes` event at
the end of the turn; the UI shows an apply/reject banner. Disk writes happen
only on `apply_writes`.

- The agent should say "queued {path}" once and stop — don't call list/read to
  confirm. The banner is user-facing confirmation.
- `edit_file` chains with pending writes — editing a file you already queued
  amends the pending version, not the on-disk one.

## Iteration cap

`MAX_ITERATIONS = 25` in `_agent_loop_impl`. If the model loops (repeats the
same tool, re-plans endlessly), the loop stops with an error message. If you
raise this, you are probably papering over a prompt or tool-description
problem.

## Forced first tool call

On turn 1, when `task.md` has no `[/]` step and the user message is not
conversational (short + no action verbs), `tool_choice="required"` is set on
the API call. This stops the model from narrating its plan as prose instead
of calling `update_task`. Conversational bypass and the action-verb list live
in `_agent_loop_impl`.

## Tool-result trimming

After each iteration, `_trim_stale_tool_results` replaces tool-output bodies
older than the 3 most recent (and >400 chars) with a short stub. Large
`read_file` dumps stop accumulating across a 25-iteration loop. Tools that
return frequently-referenced small output (`list_context_files`) stay intact.

## `<think>` / `<system>` blocks

The model's reasoning is streamed in two ways:
1. OpenAI `reasoning` field in `model_extra` → server forwards as `thinking` events.
2. Inline `<think>…</think>` blocks in content.

`ThinkStreamFilter` strips (2) before forwarding to the client and before
appending to `messages`, so it doesn't leak into the next turn's prompt.
It hides `<system>…</system>` blocks the same way: the agent loop injects
system directives after tool results, and models sometimes echo them back
verbatim — those echoes route to the thinking stream, never visible chat
(fixed 2026-07-06; see friction.md).

## Prompt + manual pattern

System prompt = `prompts/<mode>.md` + file tree.
Manual bodies are NOT inlined — the model calls `load_manual(name, section?)`
to pull them in as tool output only when needed.

- Manual docs live in `<project_dir>/manual/*.md` (project-scoped only; the
  legacy global `packs/` folder was retired).
- `readme.md` in the folder is skipped (it's documentation about the folder, not a manual doc).
- Manual files should be terse and factual. The local model is small; verbose prose dilutes the signal.
- **Structure packs with `## ` level-2 headings.** A pack with no subheadings can only be loaded whole, defeating the on-demand loader.

## Prompt authoring

- Short imperatives > paragraphs.
- List tool-specific rules ("prefer `edit_file` over `write_file` for small changes") in `prompts/<mode>.md`, not tool descriptions — the prompt has global priority.
- Tool descriptions tell Gemma what the tool *does*; the system prompt tells her when to use it.

## Testing changes

- No test suite. Manual verification:
  - Set the project dir to this harness, ask the model to read `server.py` → confirm context bar updates, file appears in "in context" panel.
  - Ask for a small edit → confirm `edit_file` queues with a diff preview, banner appears, apply writes to disk.
  - Hit stop mid-stream → confirm input re-enables and context bar updates.
  - Toggle a pack chip → confirm it persists across reset, clears only on `set_dir`.
  - Switch `/mode` mid-session → confirm prior mode's tool_calls disappear from the transcript but user/assistant text survives.
- Parse checks before restarting: `python -c "import ast; ast.parse(open('server.py').read())"`.

## File naming

- Knowledge pack files: kebab-case (`discord-bot-api.md`), displayed as spaced text in chips.
- Manual files: this folder — plain nouns (`server.md`, `ui.md`), no prefix.
