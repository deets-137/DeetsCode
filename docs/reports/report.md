# Harness Review & Stress Test

*Point-in-time self-review — kept for the record; some items below may since have been fixed or gone stale.*

Date: 2026-04-18. Model used: `qwen3.6:latest` (qwen35moe, 36B Q4_K_M).

## What I tested

- Boot: `python server.py` came up clean on `127.0.0.1:8000`.
- Browser session via Chrome MCP: tree, packs, models, themes, task-panel all load on `onopen`.
- Slash commands exercised: `/help`, `/ls .`, `/tree`, `/symbols server.py`, `/search def\s+build_file_tree glob=*.py`, `/read config.py 1-5`, plus three hostile inputs.
- Hostile inputs:
  - `/read ../outside-project` → `Error: path escapes project directory` ✅
  - `/read does-not-exist.py` → `Error: file not found` ✅
  - `/search [invalid(` → `Error: invalid regex: unterminated character set` ✅
  - `/frobnicate` → client-side `unknown slash` error, no WS traffic ✅
- Control surfaces: `set_auto_apply`, `set_packs`, `set_temperature`, `compact`, `reset` all emit the expected WS frames.
- `/compact` on empty history → server replied `info: "Nothing to compact."` ✅
- End-to-end LLM turn: `"reply with only the single word: pong"` → streamed 13 thinking chunks + 1 text chunk + usage + done. Total ~30 s wall-clock, 3188 tokens used. Output correct.
- Browser console: zero JS errors across the whole session.
- Layout at 900×700: right-hand columns clip — see Issues.

---

## Server / harness issues

### Bugs

1. **`_get_ws` branch can leak a zombie connection.** [discord_bot.py:55](discord_bot.py:55) — when `is_alive` is false, the old entry is popped but the old `ws` object is never `close()`d. On Windows especially this leaves half-open sockets until GC. Minor but adds up.
2. **`apply_writes` ignores directory traversal.** [server.py:552-560](server.py:552) writes `project_dir / rel_path` without re-checking `is_relative_to`. A queued write with `../../etc/passwd` would escape the sandbox. `write_file`/`edit_file` check the read path, but the final disk-write step doesn't. Add the check before `full_path.write_text`.
3. **`set_dir` has the same gap.** [server.py:471-482](server.py:471) resolves the new path and only checks `is_dir()` — any directory on the machine is fair game, which then becomes the project root for tool ops. Fine for a single-user local toy (per memory), but worth stating.
4. **Context-length bar defaults to 131,072 until a model switch.** [server.py:56](server.py:56) sets `current_context_length = 262144` but `ctx_length` is only emitted on `set_model`; the HTML fallback in [static/index.html:31](static/index.html:31) reads "0 / 131072" until the first `usage` event overwrites it. Emit `ctx_length` on WS connect, or compute `current_context_length` from the model at startup.
5. **Placeholder text is stale.** [static/index.html:58](static/index.html:58) says `Message Gemma…` but the active model is `qwen3.6:latest`. Purely cosmetic. Consider pulling from `/models` on load.
6. **`!reset` in Discord still used `ws.open`** — fixed in this session, but reminder: the `websockets` package changed that API between major versions. The new `_recv_until` helper keeps us compatible.
7. **`list_dir` shows `__pycache__`.** [tools.py:337-340](tools.py:337) filters dotfiles but not `SKIP_DIRS`. `/ls .` returned `[dir] __pycache__`. Low severity but noisy.
8. **`run_command` has a 30 s timeout and no output streaming.** [tools.py:353](tools.py:353) — long-running tests silently fail. Consider raising the cap or streaming stdout as it arrives (right now the model has to wait the full wall-clock).
9. **`agent_loop` catches `Exception` to send `done`**, but if the exception happens before `agent_loop` is entered (e.g. JSON parse of a malformed user message), the server crashes the whole WS without a `done`. [server.py:594-600](server.py:594) — wrap user-payload parsing in try/except and fall through.

### Design / friction notes

- **Global state shared across WS clients.** `project_dir`, `auto_apply_enabled`, `current_model`, `current_temperature` are module-level. Your own memory notes harness is single-user so this is OK, but: two browser tabs on the same server silently fight over these settings. If you ever want two — even briefly — isolate per-WS.
- **`pending_writes` / `read_files` are also module-level.** Two parallel agent loops would corrupt each other's queue. Single-user saves you here.
- **No broadcast of streaming events.** Earlier conversation thread — Discord's messages don't mirror to the web UI. You already decided to skip this; noted for posterity.
- **Iteration cap = 25.** [server.py:255](server.py:255) — fine for most work, but a single file-heavy task can hit it. Worth surfacing in the error message what the next action could be (e.g., suggest `/compact`).
- **`strip_think` regex removes `<think>` blocks from the saved assistant turn** [server.py:313-315](server.py:313) — but the streamed-to-UI text still contains them, and `appendResponse` re-strips them client-side. Double work, and the two strippers aren't guaranteed to match (client regex is inline in [static/app.js:577](static/app.js:577)). Consider stripping once, server-side, before sending `text` frames.
- **No WebSocket message size or auth bound.** Local only, so not a security issue, but pasting a multi-MB file into the chat box will ship it through `ws.send` and straight into `messages` and the model. Frontend could truncate with a warning.

---

## UI / UX

- Layout is a CSS grid that assumes ≥ ~1200 px. At 900 px, the right column (files / packs / tasks) gets pushed off-screen with no horizontal scroll to recover. Add a `min-width` and either a responsive stack or an overflow-x hint.
- The `addDivider` between `thinking` and `text` renders cleanly — visual streaming of qwen3's thought process works well. Confirmed on live turn.
- Response panel scrolls to bottom on every append — good. But on `reset_complete` the scroll state is fine because we clear.
- File click → `requestRead(path)` uses the localStorage template but doesn't protect against the template containing no `{path}` marker (then nothing gets substituted and the model sees the raw template). Consider validating.
- `clearResponse()` is called in `sendMessage` [static/app.js:400](static/app.js:400) — this wipes the turn that just completed. Intentional ("fresh turn") but it does mean there's no scrollback in the UI. If you want history, remove this line and just `addDivider` instead.

---

## Qwen config review (answering your follow-up)

Checked `ollama show qwen3.6` and what `server.py` is sending. Issues:

### 1. Empty chat template in the Modelfile

```
template: {{ .Prompt }}
system:   (none)
```

Your imported qwen3.6 GGUF has **no chat template**. Ollama's OpenAI-compat endpoint still builds a prompt from your `messages` list, but with this template it falls back to generic role tagging that doesn't match what qwen3 was trained on (qwen3 uses `<|im_start|>role\ncontent<|im_end|>`). This is a likely explanation for why even a trivial "pong" reply burned 3188 tokens of thinking — the model isn't cleanly entering `assistant` mode. **Fix:** re-pull with the official Modelfile (`ollama pull qwen3:latest` if a matching tag exists) or write a Modelfile that sets the proper ChatML template.

### 2. Modelfile parameters don't match Qwen's recommended settings

Ollama defaults baked in:

| param | current | Qwen3 recommended |
|---|---|---|
| temperature | 1 (Modelfile) / 0.5 (your server overrides) | **0.6** for thinking mode, 0.7 for non-thinking |
| top_p | 0.95 | 0.95 ✓ |
| top_k | 20 | 20 ✓ |
| min_p | 0 | 0 ✓ |
| presence_penalty | **1.5** | **0** (Qwen docs explicitly warn against non-zero) |

`presence_penalty = 1.5` is the big one. It penalizes tokens that have already appeared — which on structured tool-call output discourages the model from re-emitting valid JSON keys like `"path"`, `"name"`, etc. This is a plausible cause of tool-call weirdness. Set it to 0 in the Modelfile or pass `"presence_penalty": 0` in your `extra_body`.

### 3. Context length

You pin `num_ctx = 262144` in [server.py:273](server.py:273). The model supports this maximum, but a 36B Q4_K_M MoE at 256k context needs ~60 GB of KV cache RAM on top of the model weights. On typical consumer hardware this silently spills to disk and dominates latency. **Recommend:** drop to 32768 or 65536 by default, expose it as a config knob, and only raise it when you actually need it. You'd likely see a dramatic speedup.

### 4. `num_predict = 16384`

Also pinned in [server.py:274](server.py:274). Allows 16k tokens of output per turn, including thinking. Qwen3 will happily use all of it. Halving this to 4096–8192 would cap worst-case turn time without hurting practical replies. Combine with a system-prompt cue like `/no_think` (qwen3's inline toggle) for trivial prompts.

### 5. Qwen3's thinking toggle isn't exposed

Qwen3 accepts `/think` and `/no_think` magic strings in the user message to switch modes at runtime. The harness never uses them. Easy win: when the default prompt is selected, append `/no_think` for simple queries (or let the user toggle it via a UI switch). You just saw "pong" cost 13 thinking chunks and ~30 s — `/no_think` would turn that into < 2 s.

### 6. `MODEL = "qwen3.6"` is an unversioned bare name

[config.py:1](config.py:1) — Ollama resolves this to `qwen3.6:latest` by substring match, but it's brittle. Prefer the fully-qualified tag.

---

## Priority ordering

**Fix first (real bugs):**
1. Path-escape check in `apply_writes` ([server.py:552](server.py:552)).
2. Set `presence_penalty=0` in Modelfile or `extra_body` — likely improves tool-call reliability.
3. Drop `num_ctx` default from 262144 → 32768 or make it configurable — likely large speedup.

**Fix soon (footguns):**
4. Close popped zombie WS connections in `_get_ws` ([discord_bot.py:55](discord_bot.py:55)).
5. Filter `SKIP_DIRS` in `list_dir` ([tools.py:337](tools.py:337)).
6. Protect user-message JSON parse in `server.py` ([server.py:594](server.py:594)).

**Nice to have:**
7. Responsive CSS for < 1200 px widths.
8. Dynamic textbox placeholder ("Message qwen3.6…").
9. Expose `/no_think` toggle or auto-apply for trivial queries.
10. Emit `ctx_length` on WS connect so the bar doesn't show the stale HTML fallback.
11. Strip `<think>` blocks once, server-side only.
12. Single clear-and-canonical chat template via a proper qwen3 Modelfile.

---

## What I didn't test

- Concurrent WS sessions (you're single-user).
- Very large file reads hitting the `MAX_READ_CHARS = 100000` cap.
- `edit_file` on a file with Windows line endings (potential `old_string` mismatch).
- Any Discord bot flow — only reviewed code.
- Token-bar behavior over the 100k boundary.
