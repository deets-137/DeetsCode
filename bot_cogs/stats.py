"""
Stats cog — request latency reporting from the `stats` table.

Every `@mention` the relay handles writes one row (channel, user, mode,
model, duration_ms, status). This cog reads those rows back as aggregates,
sliceable by channel and by prompt mode (DeetsCode, chess, dnd, …).
"""

import discord
from discord import app_commands
from discord.ext import commands

from core import storage


class StatsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="stats", description="Show request-latency stats for this channel.")
    @app_commands.describe(
        mode="Filter to one prompt mode (e.g. DeetsCode, chess, dnd). Omit for all modes.",
        limit="How many recent requests to aggregate (default 100, max 500).",
    )
    async def slash_stats(
        self,
        interaction: discord.Interaction,
        mode: str | None = None,
        limit: int = 100,
    ):
        limit = max(1, min(limit, 500))
        channel_name = getattr(interaction.channel, "name", None) or str(interaction.channel_id)
        s = storage.stats_summary(channel_name=channel_name, mode=mode, limit=limit)
        if s["count"] == 0:
            scope = f"`{mode}` " if mode else ""
            await interaction.response.send_message(
                f"No {scope}stats recorded yet in this channel.", ephemeral=True
            )
            return
        header = f"**Last {s['count']} requests in #{channel_name}**"
        if mode:
            header += f" · mode `{mode}`"
        header += f"  ({s['completed']} ok, {s['errors']} err)"
        lines = [
            header,
            f"avg **{s['avg_ms']} ms** · min {s['min_ms']} · max {s['max_ms']}",
        ]
        if s["by_model"]:
            lines.append("")
            lines.append("**By model:**")
            for model, d in sorted(s["by_model"].items(), key=lambda kv: -kv[1]["count"]):
                lines.append(f"  `{model}` — {d['count']} req, avg {d['avg_ms']} ms, max {d['max_ms']} ms")
        await interaction.response.send_message("\n".join(lines))


async def setup(bot: commands.Bot):
    await bot.add_cog(StatsCog(bot))
