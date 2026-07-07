# Discord Bot Review

Date: 2026-04-19. Reviewed: `discord_bot.py` (pre- and post-fixes from this session).
Use case context: harness is single-user locally, but the Discord bot is multi-user — it's the path for running DnD / Mafia / other shared games off the same harness.

## What the bot does today

- Persistent WebSocket per Discord channel (`_connections[channel_id]`).
- Per-channel `asyncio.Lock` to serialize turns in the same channel.
- On first send per channel: sets `auto_apply`, sets `prompt` to `dnd` (via module-level config).
- Wraps each user message as `{"name": display_name, "id": user_id, "text": prompt}` so the server can render `<current_action>Player: X</current_action>` (see [server.py:602-621](../server.py:602)).
- `!` prefix commands: `!ask`, `!apply`, `!reject`, `!reset`, `!compact`, `!cancel`, `!setdir`, `!harness`.
- After this session: also native Discord **slash commands** `/reset`, `/compact`, `/cancel`, `/apply`, `/reject`, `/setdir`, `/mode` — they register on `on_ready` via `bot.tree.sync()`.

## Bugs

1. **Prompt mode is baked at connect time only.** `_get_ws` sends `set_prompt` on first connect ([discord_bot.py:76](../discord_bot.py:76)). There's no way to switch modes mid-session from the bot — which is why I added `/mode`. But two gotchas you should know:
   - `set_prompt` is per-WS, and a zombie-reconnect re-applies `PROMPT_MODE` from the module constant, silently reverting whatever `/mode` set.
   - Fix idea: track `_prompt_modes[channel_id]` and re-apply inside `_get_ws` after reconnect instead of the constant.
2. **`on_message` ignores bot webhooks indiscriminately** ([discord_bot.py:160](../discord_bot.py:160)). `message.author.bot` is True for the bot's own messages (good), but also for other bots. For a Mafia game you might want to route moderator-bot output *into* the conversation — add an allowlist if that matters.
3. **`!ask` is global across all channels.** A player in any channel the bot can see can invoke `!ask` and it shares the same harness (though with its own WS per channel). For games you probably want `!ask` restricted to game channels too, to avoid leaking tools to random servers.
4. **`_split` can break code fences mid-block.** For DnD narration that's fine, but if the DM pastes stat blocks in triple backticks they'll get split across messages and lose formatting.
5. **No per-user state.** All players in a channel share one conversation and one `messages` list on the server. That's what you want for DnD (shared narrative), but it means a player's whispered aside is visible to every player reading the channel — which is fine on Discord but worth stating.
6. **`RECV_TIMEOUT = 120 s`.** A single long thinking turn (especially pre-Modelfile-fix) can exceed this. Consider raising to 300 s for DnD or streaming progress messages.
7. **No rate limiting.** A fast-typing player can queue messages faster than the model processes them; the lock serializes but the queue grows unbounded. For games, consider dropping to an explicit typing-indicator + "one turn in flight" reject.

## Design / friction

- **Game state is a single JSON file** (`campaign_state.json`, see [server.py:602-621](../server.py:602)). For DnD that works; for Mafia you'll want private-per-role state which this model can't express cleanly. Proposal: move to `game_state/` directory with `public.json` + `roles/<user_id>.json`, and teach the prompt to only read its own player's file.
- **Prompt-mode is conflated with game type.** `PROMPT_MODE = "dnd"` at module top. For Mafia you'd need a different prompt file and different state schema. `/mode` I added takes a string — enumerate with `@app_commands.choices` once you have more than two.
- **No session reset on crash.** If the harness restarts, `_connections` still holds dead WS references until next message. `_get_ws`'s `is_alive` check catches this, but the reconnect drops server-side `messages` history (server is in-memory). For a 4-hour DnD session, this is a real risk — **nothing is persisted across server restarts**.
- **`!harness` help command is stale** — doesn't list the new slash commands. Low severity; players will discover them in Discord's native `/` picker.

## Proposal: first-class game-session support

This is where the "mode for Discord to pull from" request points. A clean design:

1. **Game templates** live in `games/<game_name>/`:
   - `prompt.md` — DM/GM system prompt
   - `state_schema.json` — what the AI should persist
   - `starter.md` — onboarding flow (how to start a new session)
2. **`/newgame <type>`** slash command — copies the template into the project dir and switches `set_prompt` to match.
3. **`/save` and `/load <name>`** — snapshot/restore `campaign_state.json` (or the whole `game_state/` dir) so sessions survive server restarts.
4. **`/roll <expr>`** — evaluates XdY+Z server-side instead of making the model call `run_command`. Faster, no token burn, reproducible. DnD prompt already delegates to `run_command` but it's slow and the model sometimes hallucinates the result.
5. **`/whisper <target> <text>`** — private side-channel for Mafia: bot DMs the target, server stores into role-scoped state.

These are all additive and don't touch the single-user harness use case.

## On the context-clearing question

Your instinct was right: `!reset` already does the hard clear (`type: "reset"` → server's `messages.clear()` + `clear_pending_writes()` + `clear_read_files()`). The new `/reset` slash command is the same action but surfaced in Discord's native UI — a player can hit it without memorizing `!` prefixes. `/compact` is the softer option for long DnD sessions: summarize-and-trim, keep narrative continuity.

For a DnD game that runs 4 hours and hits context pressure, recommend **auto-compact at ~75% context** — would need server-side support (a `ctx_warn` WS frame) and a bot-side reaction. Not implemented; flag for later.

## On `/no_think` (from report.md item #5)

Qwen3 accepts `/no_think` inline in the user message to suppress the reasoning channel. Benefit for Discord:
- For trivial DM responses ("roll initiative"), skip 20-30 s of thinking chunks → near-instant reply, much better game flow.
- For rules adjudication, **keep** thinking on — you want the model to reason about edge cases.

Best approach: let the GM prompt itself decide. Add to the `dnd.md` prompt:
> For simple acknowledgements (rolling, moving, greetings) append `/no_think` to your internal reasoning call. For rules calls, stat block lookups, or consequential decisions, think fully.

The model won't literally append `/no_think` to its own output, but you can implement a server-side heuristic: if the player message is <20 chars and matches known trivial patterns (`roll`, `go`, `attack`), inject `/no_think` into the user content before shipping.

Trade-off: the heuristic is brittle. Simpler fix: add a per-channel `/fastmode` toggle that wraps all user text with `/no_think` until toggled off. Good for combat rounds, off for RP scenes.

## On `clearResponse()` (from report.md)

Context for the question: the web UI `sendMessage` called `clearResponse()` on every turn, giving a "fresh turn" view but no scrollback. For coding, that's fine — the diff is elsewhere. For DnD, destroying the narrative history feels wrong.

**Fix shipped this session:** added a `harness-keep-history` localStorage flag. When `"1"`, `sendMessage` calls `addDivider()` instead of clearing. No UI toggle yet — set it via DevTools `localStorage.setItem("harness-keep-history", "1")`. A proper toggle button is a trivial follow-up if you want it exposed.

Note: this is web-UI only. The Discord bot already preserves history (every message is a new Discord message; the UI is the chat log). So the question only matters for the web panel — which is coding-primary anyway, so keeping default-off is probably right.

## Priority for your Discord use case

**Do next:**
1. Fix the prompt-mode-reset-on-reconnect bug (track per-channel mode in `_prompt_modes`).
2. Add `/save` + `/load` for game state — without this, a server restart kills the session.
3. Add `/roll` server-side so dice are fast and not subject to hallucination.

**Nice to have:**
4. Game template scaffolding under `games/<name>/` with a `/newgame` slash command.
5. Per-role private state for Mafia (blocks on game templates first).
6. `/fastmode` toggle to prepend `/no_think` to player messages.

**Skip unless you hit the pain:**
7. Multi-bot allowlist / rate limiting / code-fence-aware splitter — single-server low-traffic DnD doesn't need these.

## What I didn't test

- Actually running a DnD session (no Discord token in this session).
- The new slash commands live (`bot.tree.sync()` needs the bot to have `applications.commands` scope on the invite URL — if your current invite doesn't, re-invite with that scope added).
- Behavior when two players message simultaneously (lock serializes, but UX of "player 2 waiting" isn't surfaced).
