import json

import discord
from discord import app_commands

from services.reputation import reputation_service

PURPLE = 0x7C3AED


def _embed(title: str, text: str) -> discord.Embed:
    return discord.Embed(title=f"🟣 Purple Team • {title}", description=text[:4000], color=PURPLE)


def register_provider_commands(bot) -> None:
    reputation = app_commands.Group(name="reputation", description="IP and host reputation intelligence")
    passive = app_commands.Group(name="passive", description="API-backed passive intelligence")

    @reputation.command(name="abuseipdb", description="Check an IP address with AbuseIPDB")
    async def abuseipdb(interaction: discord.Interaction, ip: str):
        await interaction.response.defer(thinking=True)
        try:
            data = await reputation_service.abuseipdb(ip)
            text = "\n".join(f"**{k.replace('_', ' ').title()}:** {v}" for k, v in data.items() if v not in (None, "", []))
            await interaction.followup.send(embed=_embed("AbuseIPDB", text))
        except Exception as exc:
            await interaction.followup.send(embed=_embed("AbuseIPDB", str(exc)), ephemeral=True)

    @reputation.command(name="shodan", description="Get lightweight Shodan host intelligence")
    async def shodan(interaction: discord.Interaction, ip: str):
        await interaction.response.defer(thinking=True)
        try:
            data = await reputation_service.shodan_host(ip)
            await interaction.followup.send(embed=_embed("Shodan Host", f"```json\n{json.dumps(data, indent=2)[:3300]}\n```"))
        except Exception as exc:
            await interaction.followup.send(embed=_embed("Shodan Host", str(exc)), ephemeral=True)

    @passive.command(name="subdomains", description="SecurityTrails passive subdomain discovery")
    async def passive_subdomains(interaction: discord.Interaction, domain: str):
        await interaction.response.defer(thinking=True)
        try:
            names = await reputation_service.securitytrails_subdomains(domain)
            text = "\n".join(f"• `{name}`" for name in names[:50]) or "No subdomains returned."
            await interaction.followup.send(embed=_embed("Passive Subdomains", text))
        except Exception as exc:
            await interaction.followup.send(embed=_embed("Passive Subdomains", str(exc)), ephemeral=True)

    bot.tree.add_command(reputation)
    bot.tree.add_command(passive)
