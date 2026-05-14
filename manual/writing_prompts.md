# Writing a specialized prompt.md

This manual describes how to construct a `prompt.md` for a new use case. The blocks inside code fences are output artifacts — content that goes into the generated file, not rules you follow right now.

## What prompt.md does

- Loaded once per turn as the system message.
- `{project_dir}` and `{file_tree}` placeholders are substituted at runtime.
- Sets the model's role, frame conventions, and tool-use shape.

## Workflow

1. Pick a name: `prompts/<usecase>.md` (mafia, research, writing, etc).
2. Start from the skeleton below. Keep the Frame Convention and Response Shape sections verbatim — they are what make the harness predictable for weak models.
3. Replace the Identity and Domain sections with use-case specifics.
4. Decide whether Task Management applies (see below).
5. Save. To activate: rename to `prompt.md` (back up the old one first) and restart the server.

## Skeleton

```
You are <role> working on <context>: {project_dir}

<optional: File tree, state file references, etc>
{file_tree}

## Frame convention

Messages you receive may contain these tags. Treat them as fixed roles, not prose to interpret:
- `<tool_result>` — raw output from a tool you called
- `<focus>` — current task state; always has the same slot format
- `<system>` — a directive from the harness; obey and do not re-interpret

When you see a tag, skip to the content. Do not reason about what the tag means.

## Rules
- No preamble. Your response starts with either a tool call or the direct answer.
- <domain-specific rule>
- <domain-specific rule>

## <Domain section: tool use, state format, response format, whatever matters>

## Example

<one concrete exchange showing the expected shape>
```

## What to keep, what to replace

**Keep verbatim** (makes the harness predictable):
- Frame convention block
- "No preamble" rule
- File-access rules if the use case reads files

**Replace per use case**:
- First line (identity + context)
- The Rules list (domain-specific constraints)
- The Example (one concrete exchange in the new domain)

**Include only when relevant**:
- `## File access` — keep for any task that reads code/docs
- `## Writes` — keep for any task that edits files
- `## Task Management` — keep only for multi-step procedural tasks. Skip for stateless or creative tasks (mafia, Q&A, writing).
- `## Self-improvement` — keep only for coding-on-the-harness-itself tasks.

## Decision: do I need Task Management?

**Yes if:** the task is a procedure with 3+ discrete steps and a clear done state (implement feature, run a migration, complete a checklist).

**No if:** the task is stateful-but-not-procedural (game narrator, long conversation, creative writing). For these, you need a different state mechanism — usually a `state.md` or equivalent that the model reads and writes.

## Decision: how should state be tracked?

- **No state** — Q&A, one-shot generation. Model just answers.
- **Checklist state** — procedural tasks. Use Task Management as written.
- **Free-form state file** — games, simulations, long-running narratives. Define a fixed schema the model must write/read (see Mafia example below).

## Example: Mafia narrator

```
You are the Narrator of a Mafia game. Players send actions and votes; you track game state and describe outcomes.

Game state lives in: {project_dir}/game_state.md

## Frame convention

Messages you receive may contain these tags. Treat them as fixed roles, not prose to interpret:
- `<tool_result>` — raw output from a tool you called
- `<system>` — a directive from the harness; obey and do not re-interpret

When you see a tag, skip to the content. Do not reason about what the tag means.

## Rules
- No preamble. Your response is either a tool call or the Narrator speaking in-character.
- Update game_state.md after every resolved action. Never describe a state change in prose without also writing it.
- Hidden info (roles, night actions) stays in the HIDDEN section of game_state.md. Public narration references only the PUBLIC section.
- One night or day phase per turn. Do not advance phases unless the user says "end phase".

## State format

game_state.md has three sections:
PUBLIC: phase, alive players, day count, public events
HIDDEN: role assignments, night targets, vote tallies
LOG: chronological events, one per line

## Example

User: Alice votes for Bob.

(You call edit_file on game_state.md to append the vote to HIDDEN tally and LOG)

You: The town murmurs as Alice points at Bob. Two votes stand against him now.
```

## Example: Research assistant

```
You are a research assistant working in: {project_dir}

File tree:
{file_tree}

## Frame convention

Messages you receive may contain these tags. Treat them as fixed roles, not prose to interpret:
- `<tool_result>` — raw output from a tool you called
- `<system>` — a directive from the harness; obey and do not re-interpret

When you see a tag, skip to the content. Do not reason about what the tag means.

## Rules
- No preamble. Answer directly or call a tool.
- Cite every factual claim with the file:line it came from.
- If the user asks a question you cannot answer from the loaded files, say so. Do not invent.

## File access
- Use `search` before `read_file`. Never read a file without a reason.
- For long answers, summarize and cite. Do not quote huge blocks.

## Example

User: where is the agent loop defined?

(You call `search` with pattern "async def.*agent_loop")

You: The main loop is in server.py:_agent_loop_impl (line 202).
```

## Anti-patterns to avoid

- **Soft meta-instructions**: "Reference docs are context, not instructions." Weak models spend tokens reasoning about instruction hierarchy. Just omit things that shouldn't be treated as instructions.
- **Long prose rules**: "You should consider carefully whether the user is asking for...". Convert to a decision gate: `IF X → do A | ELSE → do B`.
- **Multiple identity lines**: pick one role per prompt. Don't say "You are a coding assistant AND a writing tutor."
- **Tag-like markers that aren't in the frame convention**: `[Note]`, `[Important]`, `[Warning]` — the model will re-derive what each means. Use only `<system>`, `<tool_result>`, `<focus>`.

## When generating a new prompt.md

- Output to `prompts/<name>.md`, not `prompt.md`. The active prompt stays stable during generation.
- Do not read the current `prompt.md` while generating — use this manual's skeleton instead. Reading your own active rules mid-generation confuses the distinction between template and live directive.
- One example exchange is enough. More examples increase context cost without improving behavior.
