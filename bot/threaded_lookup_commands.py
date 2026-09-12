import json

import discord
from discord import app_commands

from bot.threaded_intel_commands import _embed, _result_thread
from services.integrations import integrations


def register_threaded_lookup_commands(bot) -> None:
    intel = bot.tree.get_command("intel")
    if not isinstance(intel, app_commands.Group):
        return

    intel.remove_command("lookup")

    @intel.command(name="lookup", description="VirusTotal lookup for a domain, IP, or hash in a results thread")
    async def intel_lookup(interaction: discord.Interaction, value: str):
        await interaction.response.defer(thinking=True)
        thread = None
        try:
            thread = await _result_thread(interaction, "intel-lookup")
            data = await integrations.virustotal_lookup(value)
            panel = _embed(
                "Threat Intelligence",
                f"VirusTotal intelligence for `{value}`.",
                kind="info",
            )
            panel.add_field(
                name="📊 Provider Data",
                value=f"```json\n{json.dumps(data, indent=2)[:900]}\n```",
                inline=False,
            )
            await thread.send(embed=panel)
        except Exception as exc:
            error = _embed("Threat Intelligence Failed", str(exc), kind="error")
            if thread is not None:
                await thread.send(embed=error)
            else:
                await interaction.followup.send(embed=error)
