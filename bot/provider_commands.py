import json

import discord
from discord import app_commands

from bot.ui import make_embed
from services.reputation import reputation_service


def _embed(title: str, text: str = "", *, kind: str = "default") -> discord.Embed:
    return make_embed(title, text, kind=kind)


def register_provider_commands(bot) -> None:
    reputation = app_commands.Group(name="reputation", description="IP, host, and IOC reputation intelligence")
    passive = app_commands.Group(name="passive", description="Free-first API-backed passive intelligence")

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

    @reputation.command(name="censys", description="Look up a host with the Censys Free API")
    async def censys(interaction: discord.Interaction, ip: str):
        await interaction.response.defer(thinking=True)
        try:
            data = await reputation_service.censys_host(ip)
            panel = _embed("Censys Host Intelligence", f"Internet-exposure intelligence for `{ip}`.", kind="info")
            if not data.get("found"):
                panel.add_field(name="Result", value="No Censys host record was found.", inline=False)
            else:
                panel.add_field(name="Provider", value="Censys Platform API", inline=True)
                panel.add_field(name="Host", value=f"`{ip}`", inline=True)
                panel.add_field(name="Provider Data", value=f"```json\n{json.dumps(data.get('data', {}), indent=2)[:900]}\n```", inline=False)
            await interaction.followup.send(embed=panel)
        except Exception as exc:
            await interaction.followup.send(embed=_embed("Censys Lookup Failed", str(exc), kind="error"), ephemeral=True)

    @reputation.command(name="otx", description="Enrich an IP, domain, URL, or hash with AlienVault OTX")
    async def otx(interaction: discord.Interaction, value: str):
        await interaction.response.defer(thinking=True)
        try:
            data = await reputation_service.otx_indicator(value)
            panel = _embed("AlienVault OTX Intelligence", f"Threat-intelligence enrichment for `{value}`.", kind="info")
            if not data.get("found"):
                panel.add_field(name="Result", value="No OTX indicator record was found.", inline=False)
            else:
                panel.add_field(name="Indicator Type", value=str(data.get("type", "unknown")), inline=True)
                panel.add_field(name="Pulse Count", value=str(data.get("pulse_count", 0)), inline=True)
                if data.get("reputation") not in (None, ""):
                    panel.add_field(name="Reputation", value=str(data.get("reputation")), inline=True)
                if data.get("asn"):
                    panel.add_field(name="ASN", value=str(data.get("asn")), inline=True)
                if data.get("country_code"):
                    panel.add_field(name="Country", value=str(data.get("country_code")), inline=True)
            await interaction.followup.send(embed=panel)
        except Exception as exc:
            await interaction.followup.send(embed=_embed("OTX Lookup Failed", str(exc), kind="error"), ephemeral=True)

    @passive.command(name="urlscan", description="Search historical public web scans with urlscan.io")
    async def urlscan(interaction: discord.Interaction, value: str):
        await interaction.response.defer(thinking=True)
        try:
            data = await reputation_service.urlscan_search(value)
            rows = data.get("results", [])
            panel = _embed("urlscan.io Intelligence", f"Historical web-scan intelligence for `{value}`.", kind="info")
            panel.add_field(name="Query", value=f"`{data.get('query', value)}`", inline=True)
            panel.add_field(name="Results Returned", value=str(len(rows)), inline=True)
            if rows:
                rendered = []
                for row in rows[:8]:
                    rendered.append(
                        f"• `{row.get('domain') or 'unknown'}` • `{row.get('ip') or 'no IP'}` • {row.get('country') or '??'}"
                    )
                panel.add_field(name="Recent Matches", value="\n".join(rendered)[:1024], inline=False)
            else:
                panel.add_field(name="Recent Matches", value="No matching public scans were returned.", inline=False)
            await interaction.followup.send(embed=panel)
        except Exception as exc:
            await interaction.followup.send(embed=_embed("urlscan.io Lookup Failed", str(exc), kind="error"), ephemeral=True)

    bot.tree.add_command(reputation)
    bot.tree.add_command(passive)
