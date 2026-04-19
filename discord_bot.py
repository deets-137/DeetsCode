"""
Discord bot that bridges Discord messages to the harness WebSocket.

Setup:
  pip install discord.py websockets
  set DISCORD_TOKEN=your_bot_token_here  (or put it in .env)
  python discord_bot.py

Config (edit the block below):
  GAME_CHANNEL_IDS  — channel IDs where the bot responds to every message
  AUTO_APPLY        — auto-write queued files without asking Discord for confirmation
  HARNESS_WS        — WebSocket URL of the running harness
"""

import asyncio
import json
import os

import discord
from discord.ext import commands
import websockets
from dotenv import load_dotenv

load_dotenv()  # reads .env file in the same directory

# ─── Config ──────────────────────────────────────────────────────────────────

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "")

# Channel IDs where the bot responds to ALL messages (no prefix/mention needed).
# Right-click a channel in Discord (developer mode on) → Copy ID
GAME_CHANNEL_IDS: set[int] = {1344481118287695904}  # e.g. {1234567890123456789}

# If True, file writes queued by the AI are applied automatically without asking.
AUTO_APPLY = True

# Server prompt mode to use for this bot ("default", "dnd", etc — see prompts/)
PROMPT_MODE = "dnd"

HARNESS_WS = "ws://localhost:8000/ws"

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

# Game saves live under PROJECT_ROOT/saves/<name>.json. The harness runs out of
# the project dir; the bot just passes the relative path.
import pathlib as _pathlib
_BOT_ROOT = _pathlib.Path(__file__).parent
_SAVES_DIR = _BOT_ROOT / "saves"

# ─── WS helpers ──────────────────────────────────────────────────────────────

async def _get_ws(channel_id: int) -> websockets.ClientConnection:
    """Return an open WS for this channel, reconnecting if needed."""
    ws = _connections.get(channel_id)
    
    # 1. Check if the existing connection is actually alive
    is_alive = False
    if ws is not None:
        # This safely checks for .open or .closed without crashing
        is_alive = getattr(ws, 'open', not getattr(ws, 'closed', False))

    # 2. If it's dead or doesn't exist, make a new one
    if not is_alive:
        # Close the old one just in case it's in a 'hanging' state
        old = _connections.pop(channel_id, None)
        if old is not None:
            try:
                await old.close()
            except Exception:
                pass

        ws = await websockets.connect(HARNESS_WS)
        if AUTO_APPLY:
            await ws.send(json.dumps({"type": "set_auto_apply", "enabled": True}))
        await ws.send(json.dumps({"type": "set_prompt", "prompt": PROMPT_MODE}))
        _connections[channel_id] = ws
        print(f"DEBUG: Reconnected to Harness for channel {channel_id}")

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


async def _ask(channel_id: int, prompt: str) -> tuple[str, list[str]]:
    """
    Send a prompt and collect the full streamed response.
    Returns (reply_text, pending_file_list).
    Pending files are non-empty only when AUTO_APPLY=False and the AI wrote files.
    """
    async with _get_lock(channel_id):
        ws = await _get_ws(channel_id)
        await ws.send(json.dumps({"type": "message", "content": prompt}))

        chunks: list[str] = []
        pending: list[str] = []

        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=RECV_TIMEOUT)
            msg = json.loads(raw)
            t = msg.get("type")

            if t == "text":
                chunks.append(msg.get("content", ""))
            elif t == "pending_writes":
                pending = list(msg.get("writes", {}).keys())
            elif t == "error":
                chunks.append(f"\n⚠️ {msg.get('content', 'Unknown error')}")
            elif t == "done":
                break
            # "thinking", "tool_call", "tool_result", "usage", "writes_applied" → silently skip

        return "".join(chunks).strip(), pending


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

# ─── Events ──────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}  (id: {bot.user.id})")
    print(f"Harness: {HARNESS_WS}")
    print(f"Game channels: {GAME_CHANNEL_IDS or '(none — use @mention or !ask)'}")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash command(s).")
    except Exception as e:
        print(f"Slash sync failed: {e}")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    cid = message.channel.id
    content = message.content

    mentioned = bot.user in message.mentions
    in_game_channel = cid in GAME_CHANNEL_IDS
    ask_prefix = content.startswith("!ask ")

    if not (mentioned or in_game_channel or ask_prefix):
        await bot.process_commands(message)
        return

    # Strip prefix / mention to isolate the prompt
    if ask_prefix:
        prompt = content[5:].strip()
    elif mentioned:
        prompt = content.replace(f"<@{bot.user.id}>", "").replace(f"<@!{bot.user.id}>", "").strip()
    else:
        prompt = content.strip()

    if not prompt:
        return
    # Fastmode: append /no_think so Qwen3 skips its reasoning pass.
    if _fastmode.get(cid):
        prompt = f"{prompt} /no_think"
    # --- Step 2: NEW logic for User Identity ---
    # We wrap the prompt in a JSON package so the server knows who is talking
    user_payload = {
        "name": message.author.display_name,
        "id": message.author.id,
        "text": prompt
    }
    final_prompt = json.dumps(user_payload)

    async with message.channel.typing():
        try:
            reply, pending = await _ask(cid, final_prompt)
        except Exception as e:
            _connections.pop(cid, None)  # drop bad connection; next message reconnects
            await message.channel.send(f"❌ Harness error: `{e}`")
            return

    for chunk in _split(reply or "(no response)"):
        await message.channel.send(chunk)

    if pending:
        listing = "\n".join(f"• `{f}`" for f in pending)
        await message.channel.send(
            f"**Pending file writes:**\n{listing}\n"
            "Reply `!apply` to write them or `!reject` to discard."
        )

# ─── Commands ────────────────────────────────────────────────────────────────

@bot.command(name="apply")
async def cmd_apply(ctx):
    """Apply pending file writes queued by the AI."""
    async with _get_lock(ctx.channel.id):
        try:
            ws = await _get_ws(ctx.channel.id)
            await ws.send(json.dumps({"type": "apply_writes"}))
            msg = await _recv_until(ws, {"writes_applied"})
            files = ", ".join(f"`{f}`" for f in msg.get("files", []))
            await ctx.send(f"✅ Written: {files}" if files else "✅ No pending files.")
        except Exception as e:
            await ctx.send(f"❌ {e}")


@bot.command(name="reject")
async def cmd_reject(ctx):
    """Discard pending file writes."""
    async with _get_lock(ctx.channel.id):
        try:
            ws = await _get_ws(ctx.channel.id)
            await ws.send(json.dumps({"type": "reject_writes"}))
            await _recv_until(ws, {"writes_rejected"})
            await ctx.send("🗑️ Writes discarded.")
        except Exception as e:
            await ctx.send(f"❌ {e}")


@bot.command(name="reset")
async def cmd_reset(ctx):
    """Clear conversation history for this channel."""
    async with _get_lock(ctx.channel.id):
        try:
            ws = await _get_ws(ctx.channel.id)
            await ws.send(json.dumps({"type": "reset"}))
            await _recv_until(ws, {"reset_complete"})
            await ctx.send("🔄 Conversation reset.")
        except Exception as e:
            await ctx.send(f"❌ {e}")


@bot.command(name="compact")
async def cmd_compact(ctx):
    """Summarize and compress the conversation to free context."""
    async with _get_lock(ctx.channel.id):
        try:
            ws = await _get_ws(ctx.channel.id)
            await ws.send(json.dumps({"type": "compact"}))
            msg = await _recv_until(ws, {"compacted", "info", "error"}, timeout=180.0)
            t = msg.get("type")
            if t == "compacted":
                prior = msg.get("prior", "?")
                await ctx.send(f"🗜️ Compacted {prior} messages into a summary.")
            elif t == "error":
                await ctx.send(f"❌ {msg.get('content', 'compact failed')}")
            else:
                await ctx.send(msg.get("content", "Nothing to compact."))
        except Exception as e:
            await ctx.send(f"❌ {e}")


@bot.command(name="cancel")
async def cmd_cancel(ctx):
    """Interrupt the in-progress generation for this channel."""
    try:
        ws = _connections.get(ctx.channel.id)
        if ws is None:
            await ctx.send("Nothing running.")
            return
        await ws.send(json.dumps({"type": "cancel"}))
        await ctx.send("🛑 Cancel sent.")
    except Exception as e:
        await ctx.send(f"❌ {e}")


@bot.command(name="setdir")
async def cmd_setdir(ctx, *, path: str):
    """Point this channel's session at a different project directory."""
    async with _get_lock(ctx.channel.id):
        try:
            ws = await _get_ws(ctx.channel.id)
            await ws.send(json.dumps({"type": "set_dir", "path": path}))
            raw = await ws.recv()
            msg = json.loads(raw)
            await ctx.send(msg.get("content", "Done."))
        except Exception as e:
            await ctx.send(f"❌ {e}")


@bot.command(name="harness")
async def cmd_help(ctx):
    """Show available bot commands."""
    await ctx.send(
        "**Harness bot commands**\n"
        "`!ask <prompt>` — send a prompt (works in any channel)\n"
        "`!apply` — write pending files to disk\n"
        "`!reject` — discard pending file writes\n"
        "`!reset` — clear conversation history for this channel\n"
        "`!compact` — summarize conversation to free context\n"
        "`!cancel` — interrupt in-progress generation\n"
        "`!setdir <path>` — switch project directory\n"
        "`!harness` — show this help\n\n"
        f"**Active prompt mode:** `{PROMPT_MODE}`  |  **Auto-apply:** `{AUTO_APPLY}`\n"
        "In **game channels**, just type normally — no `!ask` needed."
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


@bot.tree.command(name="reset", description="Clear conversation history for this channel.")
async def slash_reset(interaction: discord.Interaction):
    await _simple_ws_action(interaction, {"type": "reset"}, {"reset_complete"}, "🔄 Conversation reset.")


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


@bot.tree.command(name="mode", description="Switch prompt mode (default, dnd, etc).")
async def slash_mode(interaction: discord.Interaction, prompt: str):
    cid = interaction.channel_id
    await interaction.response.defer(thinking=False)
    async with _get_lock(cid):
        try:
            ws = await _get_ws(cid)
            await ws.send(json.dumps({"type": "set_prompt", "prompt": prompt}))
            await interaction.followup.send(f"🎭 Prompt mode → `{prompt}` (takes effect on next turn).")
        except Exception as e:
            await interaction.followup.send(f"❌ {e}")


@bot.tree.command(name="fastmode", description="Toggle appending /no_think to every message (skip reasoning — instant replies).")
async def slash_fastmode(interaction: discord.Interaction):
    cid = interaction.channel_id
    _fastmode[cid] = not _fastmode.get(cid, False)
    state = "ON" if _fastmode[cid] else "OFF"
    hint = "Model will skip its reasoning pass — fast, less thoughtful." if _fastmode[cid] else "Model will think fully before replying."
    await interaction.response.send_message(f"⚡ Fastmode **{state}** for this channel. {hint}")


def _campaign_path() -> _pathlib.Path:
    # Game state lives in the dnd/ folder at project root.
    return _BOT_ROOT / "dnd" / "campaign_state.json"


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


# ─── Run ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise SystemExit("Set DISCORD_TOKEN env var before running.")
    bot.run(DISCORD_TOKEN)
