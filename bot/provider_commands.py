import json

import discord
from discord import app_commands

from bot.ui import make_embed
from services.reputation import reputation_service


def _embed(title: str, text: str = "", *, kind: str = "default") -> discord.Embed:
    return make_embed(title, text, kind=kind)


def register_provider_commands(bot) -> None:
    reputation = app_commands.Group(name="reputation", description="IP and host reputation intelligence")
    passive = app_commands.Group(name="passive", description="API-backed passive intelligence")

    @reputation.command(name="abuseipdb", description="Check an IP address with AbuseIPDB")
    async def abuseipdb(interaction: discord.Interaction, ip: str):
        await interaction.response.defer(thinking=True)
        try:
            data = await reputation_service.abuseipdb(ip)
            panel = _embed("AbuseIPDB Intelligence", f"Reputation intelligence for `{ip}`.", kind="info")
            for key, value in data.items():
                if value not in (None, "", []):
                    panel.add_field(name=key.replace('_', ' ').title(), value=str(value)[:1024], inline=True)
            await interaction.followup.send(embed=panel)
        except Exception as exc:
            await interaction.followup.send(embed=_embed("AbuseIPDB Lookup Failed", str(exc), kind="error"), ephemeral=True)

    @reputation.command(name="shodan", description="Get lightweight Shodan host intelligence")
    async def shodan(interaction: discord.Interaction, ip: str):
        await interaction.response.defer(thinking=True)
        try:
            data = await reputation_service.shodan_host(ip)
            panel = _embed("Shodan Host Intelligence", f"Internet-exposure intelligence for `{ip}`.", kind="info")
            panel.add_field(name="🔎 Provider Data", value=f"```json\n{json.dumps(data, indent=2)[:900]}\n```", inline=False)
            await interaction.followup.send(embed=panel)
        except Exception as exc:
            await interaction.followup.send(embed=_embed("Shodan Lookup Failed", str(exc), kind="error"), ephemeral=True)

    @passive.command(name="subdomains", description="SecurityTrails passive subdomain discovery")
    async def passive_subdomains(interaction: discord.Interaction, domain: str):
        await interaction.response.defer(thinking=True)
        try:
            names = await reputation_service.securitytrails_subdomains(domain)
            text = "\n".join(f"• `{name}`" for name in names[:50]) or "No subdomains returned."
            panel = _embed("Passive Subdomain Intelligence", f"SecurityTrails results for `{domain}`.", kind="info")
            panel.add_field(name=f"🌐 Subdomains ({len(names)})", value=text[:1024], inline=False)
            await interaction.followup.send(embed=panel)
        except Exception as exc:
            await interaction.followup.send(embed=_embed("Passive Subdomain Lookup Failed", str(exc), kind="error"), ephemeral=True)

    bot.tree.add_command(reputation)
    bot.tree.add_command(passive)
