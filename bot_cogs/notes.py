"""
Notes cog — a lightweight /note system that writes to storage.notes.
Used during testing/play sessions to track feedback, bugs, or ideas without
leaving Discord. Open/closed status lets the group track what's been dealt
with.
"""

import discord
from discord import app_commands
from discord.ext import commands

import storage


class NotesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="note", description="Save a quick note (user, channel, text, timestamp) to storage.db.")
    @app_commands.describe(text="The note text to save.")
    async def slash_note(self, interaction: discord.Interaction, text: str):
        text = text.strip()
        if not text:
            await interaction.response.send_message("❌ Empty note.", ephemeral=True)
            return
        user_name = interaction.user.display_name or interaction.user.name
        channel_name = getattr(interaction.channel, "name", None) or str(interaction.channel_id)
        try:
            note_id = storage.add_note(user_name, channel_name, text)
        except Exception as e:
            await interaction.response.send_message(f"❌ {e}", ephemeral=True)
            return
        await interaction.response.send_message(f"📝 Note #{note_id} saved from **{user_name}**.")

    @app_commands.command(name="notes", description="Show notes from this channel (default: open only).")
    @app_commands.describe(
        status="Which notes to show.",
        limit="How many notes to show (default 10, max 25).",
    )
    @app_commands.choices(status=[
        app_commands.Choice(name="open", value="open"),
        app_commands.Choice(name="closed", value="closed"),
        app_commands.Choice(name="all", value="all"),
    ])
    async def slash_notes(
        self,
        interaction: discord.Interaction,
        status: app_commands.Choice[str] = None,
        limit: int = 10,
    ):
        limit = max(1, min(limit, 25))
        status_val = status.value if status else "open"
        filter_status = None if status_val == "all" else status_val
        channel_name = getattr(interaction.channel, "name", None) or str(interaction.channel_id)
        rows = storage.list_notes(channel_name=channel_name, status=filter_status, limit=limit)
        if not rows:
            await interaction.response.send_message(f"No {status_val} notes in this channel.", ephemeral=True)
            return
        lines = []
        for r in rows:
            marker = "✅" if r["status"] == "closed" else "📝"
            lines.append(f"{marker} `#{r['id']}` <t:{r['ts']}:R> **{r['user_name']}**: {r['text']}")
        body = "\n".join(lines)
        if len(body) > 1900:
            body = body[:1900] + "\n…(truncated)"
        await interaction.response.send_message(body)

    @app_commands.command(name="note_close", description="Mark a note as closed/dealt with.")
    @app_commands.describe(id="The note id shown in /notes (the number after #).")
    async def slash_note_close(self, interaction: discord.Interaction, id: int):
        ok = storage.set_note_status(id, "closed")
        if not ok:
            await interaction.response.send_message(f"❌ Note #{id} not found.", ephemeral=True)
            return
        await interaction.response.send_message(f"✅ Note #{id} closed.")

    @app_commands.command(name="note_reopen", description="Reopen a previously closed note.")
    @app_commands.describe(id="The note id to reopen.")
    async def slash_note_reopen(self, interaction: discord.Interaction, id: int):
        ok = storage.set_note_status(id, "open")
        if not ok:
            await interaction.response.send_message(f"❌ Note #{id} not found.", ephemeral=True)
            return
        await interaction.response.send_message(f"📝 Note #{id} reopened.")


async def setup(bot: commands.Bot):
    await bot.add_cog(NotesCog(bot))
