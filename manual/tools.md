# Building tools

How tools are structured, dispatched, and gated in the harness. Read this before
adding or editing anything under `tools/`.

## Package layout

```
tools/
  __init__.py   load_tools(mode) entry point + shared re-exports
  core.py       Always-loaded tools (read_file, list_dir, roll_dice, update_task,
                list_packs, load_pack, register_path). Shared state (pending_writes, read_files).
  coding.py     DeetsCode-mode pack: write_file, edit_file, search, list_symbols,
                list_context_files, run_command.
  chess.py      Chess-mode pack: new_game, move, board, resign, etc.
```

Core tools ship in every mode. Mode packs add domain tools on top and are
gated by `_MODE_PACKS` in `__init__.py`. Adding `"mafia": "mafia"` there plus
a `tools/mafia.py` is how a new mode shows up.

## The load_tools entry point

```python
from tools import load_tools
tool_defs, execute = load_tools(selected_prompt)   # e.g. "DeetsCode", "chess"
```

Returns `(list[dict], Callable)`:
- `tool_defs` — OpenAI-style function schemas for every tool available in this
  mode (core + the mode pack, merged).
- `execute` — single dispatch function that routes by tool name.

Called at the top of every agent-loop iteration so `/mode` switches pick up
the new schema without a restart. Collisions between core and a mode pack
raise `RuntimeError` at load — don't shadow a core tool name.

## Tool definition shape

```python
{
    "type": "function",
    "function": {
        "name": "do_thing",
        "description": "One tight sentence. The model picks tools by this string.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to project root"},
            },
            "required": ["path"],
        },
    },
}
```

Rules:
- **Description is the primary interface.** A local model picks tools by
  matching intent to description text. Lead with the verb, name the artifact.
- **No negative instructions in description.** "Don't use this for X" is read
  as weak. Say what it IS for.
- **Never name a tool `python`, `shell`, `browser`, `search`, `web_search`, `code_interpreter`.** Qwen (and most tuned models) have training-time priors
  on those names and will hallucinate capabilities you didn't implement.

## execute_tool signature (unified across packs)

```python
def execute_tool(
    name: str,
    args: dict,
    session_id: str,
    project_dir: Path,
    user_name: Optional[str] = None,
) -> str:
    ...
```

- Always returns a string (never dict, never None). Error cases return
  `"Error: …"` as a string — the model handles it from there.
- Core tools ignore `session_id` / `user_name`. Game packs use them for
  per-channel state and per-player action enforcement. `user_name` is the
  caller's display name (Discord display name in the bot frontend, or a
  default). Chess uses it to enforce whose turn it is.
- Wrap the whole body in `try/except Exception as e: return f"Error: {e}"`.
  A raised exception crashes the agent loop; a returned error string lets the
  model recover.

## Standard guards every new tool needs

### Path escape (any tool taking a path)

```python
path = (project_dir / args["path"]).resolve()
if not path.is_relative_to(project_dir.resolve()):
    return "Error: path escapes project directory"
```

Not optional. The model will occasionally emit `../../etc/passwd`-style paths.

### Argument presence

Check required args before using them. Return `"Error: Missing required argument 'x'"`
— do not let a `KeyError` propagate.

### Size caps

Anything returning file contents caps at `MAX_READ_CHARS = 100_000` (shared
via `tools/core.py`). Long tool output is worse than truncated output — it
fills context and triggers the stale-result trimmer.

## Writes are queued, not applied

Tools never touch disk. `write_file` and `edit_file` mutate
`pending_writes: dict[str, str]` (shared in `tools/core.py`). The UI shows an
apply/reject banner when the turn ends; disk writes happen on `apply_writes`.

- Return `"Queued write: {path}"` — once. Don't call `list_dir` / `read_file`
  to "confirm" the write.
- `edit_file` chains with pending writes: editing a file already in
  `pending_writes` amends the pending version, not the on-disk one.

## read_file's line-numbered output

`read_file` returns lines prefixed with `N<TAB>`:

```
[42 lines in server.py]
# Line numbers are prefixed as `N<TAB>`. They are NOT part of the file …
 1	import asyncio
 2	import json
...
```

Implications for any tool that consumes file content:
- `edit_file` **defensively strips** the `N<TAB>` prefix from `old_string` and
  `new_string` before applying. New tools that match on exact file content
  must do the same (copy the `_strip_line_numbers` helper from `coding.py`).
- Tool descriptions for content-consuming tools should include:
  "when copying from read_file output, strip the `N<TAB>` line-number prefix."

## The register_path tool (filesystem path SSOT)

Core tool. All harness filesystem path constants live in `paths.py`. When a
new module needs a path (a directory, a file, or a per-project filename
string), use `register_path` instead of hand-editing `paths.py` — idempotent,
keeps formatting consistent, replaces in place if the name already exists.

Kinds:
- `"dir"` → exported as `Path = HARNESS_ROOT / "<value>"`
- `"file"` → same as `dir`, just semantic intent
- `"str"` → bare string constant (for per-project relative paths like `"task.md"`)

After registering, import from `paths`. Don't compute `Path(__file__).parent / ...`
anywhere else — the whole point of `paths.py` is that future directory reorgs
are a one-line edit. See `CLAUDE.md` for the enforcement note.

## The update_task tool (planning)

Core tool. Writes to `{project_dir}/task.md` as a markdown checklist:
`- [ ]` todo, `- [/]` in-progress, `- [x]` done.

Every prompt's Rule 1 requires the model to call this first, before any other
tool. The server enforces it: on turn 1 with no `[/]` step, `tool_choice` is
set to `"required"` (see `_agent_loop_impl`). Do not add tools that bypass or
duplicate this mechanism — strengthen `update_task` instead.

## list_packs / load_pack (lazy reference docs)

Reference packs (this folder + global `packs/`) are NOT dumped into the system
prompt. Only a manifest is — names + section headings. The model calls:

- `list_packs()` — enumerate available packs with their section headings.
- `load_pack(name, section?)` — pull one section (split by `## ` headings) or
  the whole pack into context on demand.

Implication for pack authors: **structure packs with `## ` level-2 headings**.
A pack with no subheadings can only be loaded whole, which defeats the point.

## Stale tool-result trimming

The agent loop replaces `role: tool` message bodies older than the 3 most
recent with a stub (`_trim_stale_tool_results` in `server.py`). Tool output
that's >400 chars and hasn't been referenced recently gets elided.

Implications:
- **Don't rely on the model re-reading old tool output.** If a result matters
  past three iterations, the model has to call the tool again — factor that
  into cost estimates.
- Tool output that's small and frequently-referenced (e.g. `list_context_files`)
  sits below the 400-char threshold and survives.

## File-tree cache

`build_file_tree` is computed once per user turn and stashed on the per-turn
`state` dict. Tools that queue writes which create new files don't invalidate
the cache within the turn — that's fine, the tree refreshes on the next turn
regardless. If you add a tool that creates files and the model needs to see
them in the tree immediately, invalidate with `state["file_tree"] = None`.

## Mode switching clears tool artifacts

On `set_prompt` to a different mode, the server scrubs `role: tool` messages
and strips `tool_calls` off assistant messages in history. Rationale:

- The new mode's tool schema won't match the old mode's recorded calls.
- Stale tool output (chess boards in a DeetsCode turn, or vice versa)
  confuses the model.

So: don't design a tool that relies on tool-call history surviving a mode
switch. State that must persist across modes belongs on disk
(`campaign_state.json`, `storage.db`, `task.md`).

## Slash-tool allowlist

Slash commands (`/read_file foo.py` etc.) bypass the model and run the tool
directly. Only tools that are safe without model judgment should be on the
allowlist (see `_slash_execute` dispatch in `server.py`). Never add a
destructive or write-queuing tool to the allowlist.

## Adding a new tool — checklist

1. Decide: core (every mode) or mode-specific pack? Put it in
   `tools/core.py` or `tools/<mode>.py` accordingly.
2. Append the schema to that file's `TOOL_DEFINITIONS`.
3. Add a handler branch in that file's `execute_tool`.
4. If it takes a path, add the path-escape guard.
5. If it queues a change, mutate `pending_writes` and return `"Queued …: {path}"`.
6. If it consumes file content, strip `N<TAB>` line-number prefixes defensively.
7. If it returns large output, cap at `MAX_READ_CHARS`.
8. Restart the server (no auto-reload).
9. Verify with a fresh turn — `load_tools(mode)` re-reads the schema each turn,
   so no session reset needed once the process is restarted.

## Adding a new mode pack — checklist

1. Create `tools/<mode>.py` exporting `TOOL_DEFINITIONS: list[dict]` and
   `execute_tool(name, args, session_id, project_dir, user_name=None) -> str`.
2. Register in `_MODE_PACKS` in `tools/__init__.py`: `"<mode>": "<mode>"`.
3. Drop a matching `prompts/<mode>.md` — see `manual/writing_prompts.md`.
4. (Optional) Add mode-gated branching in `server.py` if the mode needs
   special lifecycle hooks (see how `"dnd"` and `"chess"` branches look now).
5. Restart.

## Anti-patterns

- **Reading `tool_defs` at module import time.** Modes rebuild it every turn
  via `load_tools`. Don't cache it at the top of a pack.
- **Module-level state that game packs need.** Put per-session state in
  `storage.db` (see `chess.py` for the pattern). Process-globals reset on
  restart and don't distinguish between sessions.
- **Tool descriptions that describe *when* to use the tool.** That belongs in
  `prompt.md` — the system prompt has global priority. Tool descriptions say
  what the tool DOES.
- **Negative constraints in schemas.** JSON Schema can express "required" and
  "enum" — use those. "must not be empty" as prose is ignored by local models.
- **Returning non-strings.** Breaks the dispatcher. Always coerce to str.
