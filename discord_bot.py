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
    /apply, /reject, /setdir, /mode, /fastmode, /save, /load, /saves.

Config (edit the block below):
  GAME_CHANNEL_IDS  — channel IDs where the bot responds to every message
  AUTO_APPLY        — auto-write queued files without asking Discord for confirmation
  HARNESS_WS        — WebSocket URL of the running harness
"""

import asyncio
import json
import os
import platform
import subprocess
import sys
import time
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands
import websockets
from dotenv import load_dotenv

import storage

load_dotenv()  # reads .env file in the same directory

# ─── Config ──────────────────────────────────────────────────────────────────

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "")

# Channel IDs where the bot responds to ALL messages (no prefix/mention needed).
# Right-click a channel in Discord (developer mode on) → Copy ID
GAME_CHANNEL_IDS: set[int] = {1344481118287695904,932359665122177104}  # e.g. {1234567890123456789}

# If True, file writes queued by the AI are applied automatically without asking.
AUTO_APPLY = True

# Server prompt mode to use for this bot ("DeetsCode", "dnd", "chess", etc — see prompts/)
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
# Per-channel active prompt mode, initialized to PROMPT_MODE and updated on /mode.
_mode_by_channel: dict[int, str] = {}

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
                    print(f"DEBUG: Session restored for channel {channel_id} "
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

_cogs_loaded = False


@bot.event
async def on_ready():
    global _cogs_loaded
    print(f"Logged in as {bot.user}  (id: {bot.user.id})")
    print(f"Harness: {HARNESS_WS}")
    print(f"Game channels: {GAME_CHANNEL_IDS or '(none — use @mention)'}")
    if not _cogs_loaded:
        for ext in ("bot_cogs.notes", "bot_cogs.stats"):
            try:
                await bot.load_extension(ext)
                print(f"Loaded cog: {ext}")
            except Exception as e:
                print(f"Failed to load {ext}: {e}")
        _cogs_loaded = True
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
    # --- Step 2: NEW logic for User Identity ---
    # We wrap the prompt in a JSON package so the server knows who is talking
    user_payload = {
        "name": message.author.display_name,
        "id": message.author.id,
        "text": prompt
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
                ws = await _get_ws(cid)
                await ws.send(json.dumps({"type": "message", "content": final_prompt}))
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
                        model_used = msg.get("model") or model_used
                        break
                    # If the server starts echoing the model name on any frame,
                    # pick it up; harmless no-op until then.
                    if not model_used and "model" in msg:
                        model_used = msg.get("model")
                reply = "".join(chunks).strip()
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

    if pending:
        listing = "\n".join(f"• `{f}`" for f in pending)
        await message.channel.send(
            f"**Pending file writes:**\n{listing}\n"
            "Reply `!apply` to write them or `!reject` to discard."
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


@bot.tree.command(name="spectate", description="Show the session id to paste into the web UI's spectate picker.")
async def slash_spectate(interaction: discord.Interaction):
    cid = interaction.channel_id
    sid = f"discord-{cid}"
    await interaction.response.send_message(
        f"🔭 Session id for this channel: `{sid}`\nOpen the web UI → pick it in the spectate dropdown → watch live.",
        ephemeral=True,
    )


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
        print(f"WARN: emergency audit write failed: {e}")
    print(line)


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
    bot.run(DISCORD_TOKEN)
