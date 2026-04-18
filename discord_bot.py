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

GAME_CHANNEL_IDS=1344481118287695904
AUTO_APPLY=True

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

HARNESS_WS = "ws://localhost:8000/ws"

# ─── Bot setup ───────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Per-channel persistent WS connections and locks.
# Persistent = conversation history is maintained per channel.
_connections: dict[int, websockets.ClientConnection] = {}
_locks: dict[int, asyncio.Lock] = {}

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
        _connections.pop(channel_id, None)
        
        ws = await websockets.connect(HARNESS_WS)
        if AUTO_APPLY:
            await ws.send(json.dumps({"type": "set_auto_apply", "enabled": True}))
        _connections[channel_id] = ws
        print(f"DEBUG: Reconnected to Harness for channel {channel_id}")

    return _connections[channel_id]


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
            raw = await ws.recv()
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
            raw = await ws.recv()
            msg = json.loads(raw)
            if msg.get("type") == "writes_applied":
                files = ", ".join(f"`{f}`" for f in msg.get("files", []))
                await ctx.send(f"✅ Written: {files}" if files else "✅ No pending files.")
            else:
                await ctx.send("Nothing to apply.")
        except Exception as e:
            await ctx.send(f"❌ {e}")


@bot.command(name="reject")
async def cmd_reject(ctx):
    """Discard pending file writes."""
    async with _get_lock(ctx.channel.id):
        try:
            ws = await _get_ws(ctx.channel.id)
            await ws.send(json.dumps({"type": "reject_writes"}))
            await ws.recv()  # writes_rejected
            await ctx.send("🗑️ Writes discarded.")
        except Exception as e:
            await ctx.send(f"❌ {e}")


@bot.command(name="reset")
async def cmd_reset(ctx):
    """Clear conversation history for this channel."""
    async with _get_lock(ctx.channel.id):
        try:
            ws = _connections.get(ctx.channel.id)
            if ws and ws.open:
                await ws.send(json.dumps({"type": "reset"}))
                await ws.recv()  # reset_complete
            await ctx.send("🔄 Conversation reset.")
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
        "`!setdir <path>` — switch project directory\n"
        "`!harness` — show this help\n\n"
        "In **game channels**, just type normally — no `!ask` needed."
    )


# ─── Run ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise SystemExit("Set DISCORD_TOKEN env var before running.")
    bot.run(DISCORD_TOKEN)
