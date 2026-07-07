"""
Discord bot that bridges Discord messages to the harness WebSocket.

Setup:
  pip install discord.py websockets python-dotenv
  set DISCORD_TOKEN=your_bot_token_here  (or put it in .env)
  python discord_bot.py

Usage:
  - In a game channel (listed in GAME_CHANNEL_IDS), type normally.
  - In any other channel, @mention the bot to get its attention.
  - All control actions use Discord slash commands: /reset, /compact, /cancel,
    /apply, /reject, /setdir, /mode, /fastmode, /save, /load, /saves,
    /state, /spectate, /health, /emergency.

Config (defaults in the block below; all overridable via .env):
  GAME_CHANNEL_IDS  — comma-separated channel IDs where the bot answers every message
  DISCORD_GUILD_ID  — guild for instant slash-command sync (omit for global sync)
  AUTO_APPLY        — auto-write queued files without asking Discord for confirmation
  PROMPT_MODE       — default server prompt mode
  HARNESS_WS        — WebSocket URL of the running harness
  OLLAMA_URL        — Ollama base URL (used by /health)
"""

import asyncio
import json
import logging
import os
import pathlib as _pathlib
import platform
import subprocess
import sys
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks
import websockets
from dotenv import load_dotenv

log = logging.getLogger("harness.bot")

import storage
import bot_media

load_dotenv()  # reads .env file in the same directory

# ─── Config ──────────────────────────────────────────────────────────────────

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_ids(name: str, default: set[int]) -> set[int]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    return {int(x) for x in raw.replace(" ", "").split(",") if x}


# All of these can be overridden in .env; the literals are just defaults.

# Channel IDs where the bot responds to ALL messages (no prefix/mention needed).
# Right-click a channel in Discord (developer mode on) → Copy ID.
# .env: GAME_CHANNEL_IDS=1344481118287695904,932359665122177104
GAME_CHANNEL_IDS: set[int] = _env_ids("GAME_CHANNEL_IDS", {1344481118287695904, 932359665122177104})

# Guild to sync slash commands to. Guild-scoped sync is instant; global sync
# can take up to an hour to propagate. .env: DISCORD_GUILD_ID=...
DISCORD_GUILD_ID: int = int(os.environ.get("DISCORD_GUILD_ID", "0") or 0)

# If True, file writes queued by the AI are applied automatically without asking.
AUTO_APPLY = _env_bool("AUTO_APPLY", True)

# Server prompt mode to use for this bot ("DeetsCode", "dnd", "chess", etc — see prompts/)
PROMPT_MODE = os.environ.get("PROMPT_MODE", "dnd")

HARNESS_WS = os.environ.get("HARNESS_WS", "ws://localhost:8000/ws")

# Ollama base URL, used only by /health.
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")

# Seconds to wait on any single WS recv before giving up
RECV_TIMEOUT = 120.0

# ─── Bot setup ───────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Per-channel persistent WS connections and locks.
# Persistent = conversation history is maintained per channel.
_connections: dict[int, websockets.ClientConnection] = {}
_locks: dict[int, asyncio.Lock] = {}
# Channels in fastmode get "/no_think" appended to every player message so Qwen3
# skips the reasoning channel. Good for combat rounds; turn off for RP/rules.
_fastmode: dict[int, bool] = {}
# Per-channel active prompt mode, initialized to PROMPT_MODE and updated on /mode.
_mode_by_channel: dict[int, str] = {}

# Game saves live under PROJECT_ROOT/saves/<name>.json. The harness runs out of
# the project dir; the bot just passes the relative path.
from paths import (
    HARNESS_ROOT as _BOT_ROOT,
    SAVES_DIR as _SAVES_DIR,
    DND_SUBDIR as _DND_SUBDIR,
    CAMPAIGN_STATE_FILENAME as _CAMPAIGN_STATE_FILENAME,
)

# ─── WS helpers ──────────────────────────────────────────────────────────────

def _ws_alive(ws) -> bool:
    """Version-tolerant liveness check. Modern websockets exposes .state;
    the legacy protocol had .open/.closed. Note this only reflects what the
    library knows locally — a half-dead socket still passes, which is why
    _run_turn retries once on send failure."""
    if ws is None:
        return False
    state = getattr(ws, "state", None)
    if state is not None:
        return getattr(state, "name", "") == "OPEN"
    return getattr(ws, "open", not getattr(ws, "closed", False))


async def _get_ws(channel_id: int) -> websockets.ClientConnection:
    """Return an open WS for this channel, reconnecting if needed."""
    ws = _connections.get(channel_id)

    # If it's dead or doesn't exist, make a new one
    if not _ws_alive(ws):
        # Close the old one just in case it's in a 'hanging' state
        old = _connections.pop(channel_id, None)
        if old is not None:
            try:
                await old.close()
            except Exception:
                pass

        ws = await websockets.connect(HARNESS_WS)
        # Identify this session so the server can restore conversation state
        # from sessions/<id>.json. On a fresh install nothing is restored; on
        # a server restart or bot reconnect, the prior messages come back.
        await ws.send(json.dumps({"type": "hello", "session_id": f"discord-{channel_id}"}))
        restored_prompt: str | None = None
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
            ack = json.loads(raw)
            if ack.get("type") == "hello_ack":
                restored_prompt = ack.get("prompt")
                if ack.get("restored"):
                    log.info(f"Session restored for channel {channel_id} "
                             f"({ack.get('messages', 0)} messages, prompt={restored_prompt})")
        except Exception:
            pass  # ack is best-effort; fall through
        if AUTO_APPLY:
            await ws.send(json.dumps({"type": "set_auto_apply", "enabled": True}))
        # Honor the restored prompt if there was one. Otherwise fall back to the
        # channel's last-known mode, and finally to the module default. This
        # keeps /mode sticky across server/bot restarts instead of snapping
        # back to PROMPT_MODE on every reconnect.
        effective_mode = restored_prompt or _mode_by_channel.get(channel_id) or PROMPT_MODE
        await ws.send(json.dumps({"type": "set_prompt", "prompt": effective_mode}))
        _mode_by_channel[channel_id] = effective_mode
        _connections[channel_id] = ws
        log.info(f"Reconnected to Harness for channel {channel_id}")

    return _connections[channel_id]


async def _recv_until(ws: websockets.ClientConnection, types: set[str], timeout: float = RECV_TIMEOUT) -> dict:
    """Read frames until one matches an expected type; skip unrelated frames."""
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            raise asyncio.TimeoutError(f"timed out waiting for {types}")
        raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        msg = json.loads(raw)
        if msg.get("type") in types:
            return msg


def _get_lock(channel_id: int) -> asyncio.Lock:
    if channel_id not in _locks:
        _locks[channel_id] = asyncio.Lock()
    return _locks[channel_id]


async def _run_turn(cid: int, final_prompt: str) -> tuple[str, list[str], list[str], str | None]:
    """Send one player message and stream the reply back.

    Retries once if the connection dies before any frame arrives — the classic
    case being a stale socket after a harness restart that passes the local
    liveness check but dies on first send. Never retries after frames have
    started flowing, to avoid double-generating a turn.

    Returns (reply, pending_write_paths, media_urls, model_used).
    """
    for attempt in (0, 1):
        received_any = False
        try:
            ws = await _get_ws(cid)
            await ws.send(json.dumps({"type": "message", "content": final_prompt}))
            chunks: list[str] = []
            pending: list[str] = []
            media_urls: list[str] = []
            model_used: str | None = None
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=RECV_TIMEOUT)
                received_any = True
                msg = json.loads(raw)
                t = msg.get("type")
                if t == "text":
                    chunks.append(msg.get("content", ""))
                elif t == "pending_writes":
                    pending = list(msg.get("writes", {}).keys())
                elif t == "tool_result":
                    for url in bot_media.extract(msg.get("name", ""), msg.get("content", "")):
                        if not media_urls or media_urls[-1] != url:
                            media_urls.append(url)
                elif t == "error":
                    chunks.append(f"\n⚠️ {msg.get('content', 'Unknown error')}")
                elif t == "done":
                    model_used = msg.get("model") or model_used
                    break
                # If the server starts echoing the model name on any frame,
                # pick it up; harmless no-op until then.
                if not model_used and "model" in msg:
                    model_used = msg.get("model")
            return "".join(chunks).strip(), pending, media_urls, model_used
        except (websockets.ConnectionClosed, OSError) as e:
            _connections.pop(cid, None)
            if attempt == 0 and not received_any:
                log.warning(f"WS died before reply for channel {cid}; retrying once ({e})")
                continue
            raise
    raise RuntimeError("unreachable")  # loop always returns or raises


def _split(text: str, limit: int = 1990) -> list[str]:
    """Split long text into Discord-safe chunks, preferring newline boundaries."""
    if len(text) <= limit:
        return [text]
    out: list[str] = []
    while text:
        if len(text) <= limit:
            out.append(text)
            break
        cut = text.rfind("\n", 0, limit)
        if cut == -1:
            cut = limit
        out.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return out

# ─── Pending-writes buttons ──────────────────────────────────────────────────

class PendingWritesView(discord.ui.View):
    """Apply/Reject buttons attached to a pending-writes notice. One decision
    per view: both buttons disable after either is pressed."""

    def __init__(self, channel_id: int):
        super().__init__(timeout=600)
        self.channel_id = channel_id

    async def _finish(self, interaction: discord.Interaction, note: str):
        for child in self.children:
            child.disabled = True
        self.stop()
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(note)

    @discord.ui.button(label="Apply", style=discord.ButtonStyle.success)
    async def apply_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with _get_lock(self.channel_id):
            try:
                ws = await _get_ws(self.channel_id)
                await ws.send(json.dumps({"type": "apply_writes"}))
                msg = await _recv_until(ws, {"writes_applied"})
                files = ", ".join(f"`{f}`" for f in msg.get("files", []))
                await self._finish(interaction, f"✅ Written: {files}" if files else "✅ No pending files.")
            except Exception as e:
                await self._finish(interaction, f"❌ {e}")

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger)
    async def reject_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with _get_lock(self.channel_id):
            try:
                ws = await _get_ws(self.channel_id)
                await ws.send(json.dumps({"type": "reject_writes"}))
                await _recv_until(ws, {"writes_rejected"})
                await self._finish(interaction, "🗑️ Writes discarded.")
            except Exception as e:
                await self._finish(interaction, f"❌ {e}")


# ─── Events ──────────────────────────────────────────────────────────────────

_cogs_loaded = False
_synced = False


@bot.event
async def on_ready():
    global _cogs_loaded, _synced
    log.info(f"Logged in as {bot.user}  (id: {bot.user.id})")
    log.info(f"Harness: {HARNESS_WS}")
    log.info(f"Game channels: {GAME_CHANNEL_IDS or '(none — use @mention)'}")
    if not _cogs_loaded:
        for ext in ("bot_cogs.notes", "bot_cogs.stats"):
            try:
                await bot.load_extension(ext)
                log.info(f"Loaded cog: {ext}")
            except Exception as e:
                log.error(f"Failed to load {ext}: {e}")
        _cogs_loaded = True
    # Sync once per process, not on every gateway reconnect. Guild-scoped sync
    # (DISCORD_GUILD_ID set) shows up instantly; global sync can take up to an
    # hour to propagate and is rate-limited.
    if not _synced:
        try:
            if DISCORD_GUILD_ID:
                guild = discord.Object(id=DISCORD_GUILD_ID)
                bot.tree.copy_global_to(guild=guild)
                synced = await bot.tree.sync(guild=guild)
                log.info(f"Synced {len(synced)} slash command(s) to guild {DISCORD_GUILD_ID}.")
            else:
                synced = await bot.tree.sync()
                log.info(f"Synced {len(synced)} slash command(s) globally (may take up to 1h to appear).")
            _synced = True
        except Exception as e:
            log.error(f"Slash sync failed: {e}")
    if not _ws_heartbeat.is_running():
        _ws_heartbeat.start()


@tasks.loop(seconds=30)
async def _ws_heartbeat():
    """Prune dead harness connections so the next player message reconnects
    cleanly instead of eating the error. Protocol-level ping — no app frames,
    so it can't interleave with an in-flight turn."""
    for cid, ws in list(_connections.items()):
        if not _ws_alive(ws):
            _connections.pop(cid, None)
            log.warning(f"heartbeat: dropped closed WS for channel {cid}")
            continue
        try:
            pong = await ws.ping()
            await asyncio.wait_for(pong, timeout=10.0)
        except Exception as e:
            _connections.pop(cid, None)
            try:
                await ws.close()
            except Exception:
                pass
            log.warning(f"heartbeat: ping failed for channel {cid}; dropped ({e})")


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """Catch-all so a failed slash command never leaves the interaction
    spinning on 'thinking…' forever."""
    log.exception(f"Slash command {interaction.command.name if interaction.command else '?'} failed", exc_info=error)
    msg = f"❌ Internal error: `{error}`"
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception:
        pass


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    cid = message.channel.id
    content = message.content

    mentioned = bot.user in message.mentions
    in_game_channel = cid in GAME_CHANNEL_IDS

    if not (mentioned or in_game_channel):
        return

    # Strip mention to isolate the prompt
    if mentioned:
        prompt = content.replace(f"<@{bot.user.id}>", "").replace(f"<@!{bot.user.id}>", "").strip()
    else:
        prompt = content.strip()

    if not prompt:
        return
    # Fastmode: append /no_think so Qwen3 skips its reasoning pass.
    if _fastmode.get(cid):
        prompt = f"{prompt} /no_think"
    # Wrap the prompt so the server knows who is talking. Identity is by
    # display name only — we don't ship the raw Discord id since nothing
    # downstream needs it and it just confuses small models that have to
    # quote it verbatim into chess tool calls.
    user_payload = {
        "name": message.author.display_name,
        "text": prompt,
    }
    final_prompt = json.dumps(user_payload)

    channel_name = getattr(message.channel, "name", None) or str(cid)
    user_name = message.author.display_name
    mode = _mode_by_channel.get(cid, PROMPT_MODE)
    model_used: str | None = None
    stat_status = "completed"
    started_at = int(time.time())
    t0 = time.perf_counter()
    try:
        async with _get_lock(cid):
            async with message.channel.typing():
                reply, pending, media_urls, model_used = await _run_turn(cid, final_prompt)
    except Exception as e:
        _connections.pop(cid, None)  # drop bad connection; next message reconnects
        stat_status = "error"
        storage.record_stat(
            channel_name=channel_name, user_name=user_name,
            duration_ms=int((time.perf_counter() - t0) * 1000),
            status=stat_status, mode=mode, model=model_used, started_at=started_at,
        )
        await message.channel.send(f"❌ Harness error: `{e}`")
        return
    storage.record_stat(
        channel_name=channel_name, user_name=user_name,
        duration_ms=int((time.perf_counter() - t0) * 1000),
        status=stat_status, mode=mode, model=model_used, started_at=started_at,
    )

    for chunk in _split(reply or "(no response)"):
        await message.channel.send(chunk)

    # Post each media URL on its own line so Discord auto-embeds one per message.
    for url in media_urls:
        try:
            await message.channel.send(url)
        except Exception:
            pass

    if pending:
        listing = "\n".join(f"• `{f}`" for f in pending)
        await message.channel.send(
            f"**Pending file writes:**\n{listing}",
            view=PendingWritesView(cid),
        )

# ─── Slash (application) commands ────────────────────────────────────────────
# These mirror the `!` prefix commands but appear in Discord's native `/` menu
# with autocomplete. Useful for game channels where players want a clean
# "clear context" button without typing a raw prefix.


async def _simple_ws_action(interaction: discord.Interaction, payload: dict, expect: set[str], ok_msg: str):
    cid = interaction.channel_id
    await interaction.response.defer(thinking=False)
    async with _get_lock(cid):
        try:
            ws = await _get_ws(cid)
            await ws.send(json.dumps(payload))
            if expect:
                await _recv_until(ws, expect)
            await interaction.followup.send(ok_msg)
        except Exception as e:
            await interaction.followup.send(f"❌ {e}")


@bot.tree.command(name="reset", description="Clear conversation history for this channel. Bypasses the turn lock.")
async def slash_reset(interaction: discord.Interaction):
    # Deliberately does NOT acquire _get_lock — reset must work even while
    # an in-flight turn is holding the lock. Fire cancel first so the server
    # aborts any running Ollama stream, then reset to clear state.
    cid = interaction.channel_id
    ws = _connections.get(cid)
    if ws is None:
        await interaction.response.send_message("No active session for this channel.")
        return
    try:
        await ws.send(json.dumps({"type": "cancel"}))
        await ws.send(json.dumps({"type": "reset"}))
        await interaction.response.send_message("🔄 Reset sent. The in-flight turn (if any) will abort.")
    except Exception as e:
        _connections.pop(cid, None)
        await interaction.response.send_message(f"❌ {e}")


@bot.tree.command(name="compact", description="Summarize conversation to free context.")
async def slash_compact(interaction: discord.Interaction):
    cid = interaction.channel_id
    await interaction.response.defer(thinking=True)
    async with _get_lock(cid):
        try:
            ws = await _get_ws(cid)
            await ws.send(json.dumps({"type": "compact"}))
            msg = await _recv_until(ws, {"compacted", "info", "error"}, timeout=180.0)
            t = msg.get("type")
            if t == "compacted":
                await interaction.followup.send(f"🗜️ Compacted {msg.get('prior', '?')} messages.")
            elif t == "error":
                await interaction.followup.send(f"❌ {msg.get('content', 'compact failed')}")
            else:
                await interaction.followup.send(msg.get("content", "Nothing to compact."))
        except Exception as e:
            await interaction.followup.send(f"❌ {e}")


@bot.tree.command(name="cancel", description="Interrupt the in-progress generation for this channel.")
async def slash_cancel(interaction: discord.Interaction):
    cid = interaction.channel_id
    ws = _connections.get(cid)
    if ws is None:
        await interaction.response.send_message("Nothing running.")
        return
    try:
        await ws.send(json.dumps({"type": "cancel"}))
        await interaction.response.send_message("🛑 Cancel sent.")
    except Exception as e:
        await interaction.response.send_message(f"❌ {e}")


@bot.tree.command(name="apply", description="Apply pending file writes queued by the AI.")
async def slash_apply(interaction: discord.Interaction):
    cid = interaction.channel_id
    await interaction.response.defer(thinking=False)
    async with _get_lock(cid):
        try:
            ws = await _get_ws(cid)
            await ws.send(json.dumps({"type": "apply_writes"}))
            msg = await _recv_until(ws, {"writes_applied"})
            files = ", ".join(f"`{f}`" for f in msg.get("files", []))
            await interaction.followup.send(f"✅ Written: {files}" if files else "✅ No pending files.")
        except Exception as e:
            await interaction.followup.send(f"❌ {e}")


@bot.tree.command(name="reject", description="Discard pending file writes.")
async def slash_reject(interaction: discord.Interaction):
    await _simple_ws_action(interaction, {"type": "reject_writes"}, {"writes_rejected"}, "🗑️ Writes discarded.")


@bot.tree.command(name="setdir", description="Point this channel's session at a different project directory.")
async def slash_setdir(interaction: discord.Interaction, path: str):
    cid = interaction.channel_id
    await interaction.response.defer(thinking=False)
    async with _get_lock(cid):
        try:
            ws = await _get_ws(cid)
            await ws.send(json.dumps({"type": "set_dir", "path": path}))
            raw = await ws.recv()
            msg = json.loads(raw)
            await interaction.followup.send(msg.get("content", "Done."))
        except Exception as e:
            await interaction.followup.send(f"❌ {e}")


@bot.tree.command(name="mode", description="Switch prompt mode (DeetsCode, dnd, chess, etc).")
async def slash_mode(interaction: discord.Interaction, prompt: str):
    cid = interaction.channel_id
    await interaction.response.defer(thinking=False)
    async with _get_lock(cid):
        try:
            ws = await _get_ws(cid)
            await ws.send(json.dumps({"type": "set_prompt", "prompt": prompt}))
            _mode_by_channel[cid] = prompt
            await interaction.followup.send(f"🎭 Prompt mode → `{prompt}` (takes effect on next turn).")
        except Exception as e:
            await interaction.followup.send(f"❌ {e}")


@bot.tree.command(name="state", description="Show active game state for this channel (deterministic — no model call).")
async def slash_state(interaction: discord.Interaction):
    cid = str(interaction.channel_id)
    try:
        games = storage.list_games(channel_id=cid, status="active", limit=10)
    except Exception as e:
        await interaction.response.send_message(f"❌ storage error: {e}", ephemeral=True)
        return
    if not games:
        await interaction.response.send_message(
            "No active games in this channel. Use chess/dnd tools to start one.",
            ephemeral=True,
        )
        return
    lines = [f"**Active games in this channel ({len(games)}):**"]
    for g in games:
        full = storage.load_game(g["game_id"])
        if full is None:
            continue
        gtype = full.get("game_type", "?")
        gid = full["game_id"]
        state = full.get("state") or {}
        if gtype == "chess":
            try:
                import chess as _chess  # python-chess
                board = _chess.Board(state["fen"])
                turn = "White" if board.turn == _chess.WHITE else "Black"
                turn_name = state.get("white") if board.turn == _chess.WHITE else state.get("black")
                lines.append(
                    f"\n`{gid}` · chess · {turn} to move ({turn_name})\n"
                    f"FEN: `{board.fen()}`\n"
                    f"```\n{board.unicode(invert_color=True, borders=True)}\n```"
                )
            except Exception as e:
                lines.append(f"\n`{gid}` · chess · (failed to render: {e})")
        else:
            lines.append(f"\n`{gid}` · {gtype} · state keys: {list(state.keys())}")
    msg = "\n".join(lines)
    # Discord message cap is 2000 chars; truncate safely if multiple games.
    if len(msg) > 1900:
        msg = msg[:1900] + "\n…(truncated)"
    await interaction.response.send_message(msg, ephemeral=False)


@bot.tree.command(name="spectate", description="Show the session id to paste into the web UI's spectate picker.")
async def slash_spectate(interaction: discord.Interaction):
    cid = interaction.channel_id
    sid = f"discord-{cid}"
    await interaction.response.send_message(
        f"🔭 Session id for this channel: `{sid}`\nOpen the web UI → pick it in the spectate dropdown → watch live.",
        ephemeral=True,
    )


@bot.tree.command(name="health", description="Ping the harness and Ollama — deterministic, no model call.")
async def slash_health(interaction: discord.Interaction):
    cid = interaction.channel_id
    await interaction.response.defer(thinking=False)
    lines: list[str] = []
    try:
        ws = await _get_ws(cid)
        t0 = time.perf_counter()
        pong = await ws.ping()
        await asyncio.wait_for(pong, timeout=10.0)
        lines.append(f"🟢 Harness WS: {int((time.perf_counter() - t0) * 1000)} ms round trip")
    except Exception as e:
        _connections.pop(cid, None)
        lines.append(f"🔴 Harness WS: `{e}`")
    try:
        t0 = time.perf_counter()
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{OLLAMA_URL}/api/tags", timeout=aiohttp.ClientTimeout(total=5)) as r:
                data = await r.json()
        n_models = len(data.get("models", []))
        lines.append(f"🟢 Ollama: {int((time.perf_counter() - t0) * 1000)} ms, {n_models} model(s) installed")
    except Exception as e:
        lines.append(f"🔴 Ollama: `{e}`")
    await interaction.followup.send("\n".join(lines))


@bot.tree.command(name="fastmode", description="Toggle appending /no_think to every message (skip reasoning — instant replies).")
async def slash_fastmode(interaction: discord.Interaction):
    cid = interaction.channel_id
    _fastmode[cid] = not _fastmode.get(cid, False)
    state = "ON" if _fastmode[cid] else "OFF"
    hint = "Model will skip its reasoning pass — fast, less thoughtful." if _fastmode[cid] else "Model will think fully before replying."
    await interaction.response.send_message(f"⚡ Fastmode **{state}** for this channel. {hint}")


def _campaign_path() -> _pathlib.Path:
    # Game state lives in the dnd/ folder at project root.
    return _BOT_ROOT / _DND_SUBDIR / _CAMPAIGN_STATE_FILENAME


@bot.tree.command(name="save", description="Snapshot campaign_state.json so you can /load it later.")
async def slash_save(interaction: discord.Interaction, name: str):
    await interaction.response.defer(thinking=False)
    safe = _pathlib.Path(name).name  # strip path separators
    if not safe or safe.startswith("."):
        await interaction.followup.send("❌ Invalid save name.")
        return
    src = _campaign_path()
    if not src.is_file():
        await interaction.followup.send("❌ No `campaign_state.json` to save yet.")
        return
    try:
        _SAVES_DIR.mkdir(exist_ok=True)
        dst = _SAVES_DIR / f"{safe}.json"
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        await interaction.followup.send(f"💾 Saved to `saves/{safe}.json`.")
    except Exception as e:
        await interaction.followup.send(f"❌ {e}")


@bot.tree.command(name="load", description="Restore a saved campaign into campaign_state.json. Also clears conversation.")
async def slash_load(interaction: discord.Interaction, name: str):
    cid = interaction.channel_id
    await interaction.response.defer(thinking=False)
    safe = _pathlib.Path(name).name
    src = _SAVES_DIR / f"{safe}.json"
    if not src.is_file():
        await interaction.followup.send(f"❌ No save named `{safe}`. Use `/saves` to list.")
        return
    try:
        dst = _campaign_path()
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    except Exception as e:
        await interaction.followup.send(f"❌ Write failed: {e}")
        return
    # Reset the channel's conversation so the model cold-starts from the
    # restored state rather than continuing whatever was happening.
    async with _get_lock(cid):
        try:
            ws = await _get_ws(cid)
            await ws.send(json.dumps({"type": "reset"}))
            await _recv_until(ws, {"reset_complete"})
        except Exception as e:
            await interaction.followup.send(f"⚠️ Loaded file but reset failed: {e}")
            return
    await interaction.followup.send(f"📂 Loaded `{safe}`. Conversation reset — next message bootstraps from saved state.")


@bot.tree.command(name="saves", description="List available campaign saves.")
async def slash_saves(interaction: discord.Interaction):
    if not _SAVES_DIR.is_dir():
        await interaction.response.send_message("(no saves yet)")
        return
    files = sorted(p.stem for p in _SAVES_DIR.glob("*.json"))
    if not files:
        await interaction.response.send_message("(no saves yet)")
        return
    await interaction.response.send_message("**Saves:** " + ", ".join(f"`{f}`" for f in files))


# ─── Emergency kill switch ───────────────────────────────────────────────────
# Deterministic: no model calls, no prompt tokens. Slash-command handlers send
# typed control frames / run subprocesses directly.

_EMERGENCY_LOG = _BOT_ROOT / "emergency.log"
_VALID_TARGETS = ("session", "ollama", "harness", "bot", "all")


def _audit(entry: str) -> None:
    ts = datetime.now().isoformat(timespec="seconds")
    line = f"[{ts}] {entry}"
    try:
        with _EMERGENCY_LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:
        log.warning(f"emergency audit write failed: {e}")
    log.info(line)


async def _kill_session(cid: int, dry: bool) -> str:
    if dry:
        return "DRY session: would send cancel+reset on this channel's WS"
    ws = _connections.get(cid)
    if ws is None:
        return "session: no active WS for this channel"
    try:
        await ws.send(json.dumps({"type": "cancel"}))
        await ws.send(json.dumps({"type": "reset"}))
        return "session: cancel+reset sent"
    except Exception as e:
        return f"session: failed ({e})"


async def _kill_ollama(dry: bool) -> str:
    is_win = platform.system() == "Windows"
    cmd = ["taskkill", "/F", "/IM", "ollama.exe"] if is_win else ["pkill", "-9", "ollama"]
    if dry:
        return f"DRY ollama: would run `{' '.join(cmd)}`"
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=10, text=True)
        return f"ollama: rc={r.returncode} {r.stdout.strip() or r.stderr.strip()}"
    except Exception as e:
        return f"ollama: failed ({e})"


async def _kill_harness(cid: int, dry: bool) -> str:
    if dry:
        return "DRY harness: would send {'type':'shutdown'} on WS"
    try:
        ws = _connections.get(cid) or await _get_ws(cid)
        await ws.send(json.dumps({"type": "shutdown"}))
        return "harness: shutdown frame sent"
    except Exception as e:
        return f"harness: failed ({e})"


async def _kill_bot(dry: bool) -> str:
    if dry:
        return "DRY bot: would await bot.close() then sys.exit(0)"
    try:
        await bot.close()
    finally:
        # Scheduled after we return so followup.send has a chance to flush.
        asyncio.get_event_loop().call_later(0.5, lambda: sys.exit(0))
    return "bot: close() issued; exiting in 0.5s"


@bot.tree.command(
    name="emergency",
    description="Kill switch. Set dry_run:true to preview. Otherwise confirm must equal EMERGENCY.",
)
@app_commands.describe(
    target="What to kill",
    confirm="Type EMERGENCY to execute (ignored when dry_run:true)",
    dry_run="Preview the plan without executing",
)
@app_commands.choices(target=[app_commands.Choice(name=t, value=t) for t in _VALID_TARGETS])
async def slash_emergency(
    interaction: discord.Interaction,
    target: app_commands.Choice[str],
    confirm: str = "",
    dry_run: bool = False,
):
    tgt = target.value
    user = f"{interaction.user} (id={interaction.user.id})"
    cid = interaction.channel_id
    _audit(f"INVOKE target={tgt} dry_run={dry_run} channel={cid} user={user}")

    if not dry_run and confirm != "EMERGENCY":
        _audit(f"REJECT bad confirm target={tgt}")
        await interaction.response.send_message(
            "Type `EMERGENCY` in `confirm` to execute, or set `dry_run:true` to preview.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(thinking=True)
    results: list[str] = []

    # Chain order for `all`: ollama → harness → bot, so a bot kill at the
    # end doesn't orphan the heavier processes.
    if tgt == "session":
        results.append(await _kill_session(cid, dry_run))
    elif tgt == "ollama":
        results.append(await _kill_ollama(dry_run))
    elif tgt == "harness":
        results.append(await _kill_harness(cid, dry_run))
    elif tgt == "bot":
        results.append(await _kill_bot(dry_run))
    elif tgt == "all":
        results.append(await _kill_ollama(dry_run))
        await asyncio.sleep(0.3)
        results.append(await _kill_harness(cid, dry_run))
        await asyncio.sleep(0.3)
        results.append(await _kill_bot(dry_run))

    for r in results:
        _audit(f"RESULT {r}")

    header = "🧪 **DRY RUN**" if dry_run else "🚨 **EMERGENCY**"
    body = "\n".join(f"• {r}" for r in results)
    try:
        await interaction.followup.send(f"{header} target=`{tgt}`\n{body}")
    except Exception:
        pass  # bot may be mid-shutdown


# ─── Run ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise SystemExit("Set DISCORD_TOKEN env var before running.")
    discord.utils.setup_logging(level=logging.INFO)
    _fh = RotatingFileHandler(_BOT_ROOT / "bot.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    _fh.setFormatter(logging.Formatter(
        "[{asctime}] [{levelname:<8}] {name}: {message}", "%Y-%m-%d %H:%M:%S", style="{",
    ))
    logging.getLogger().addHandler(_fh)
    bot.run(DISCORD_TOKEN, log_handler=None)  # None: we already configured logging above
