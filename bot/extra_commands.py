import json

import discord
from discord import app_commands

from core.config import settings
from services.analyzers import analyze_email_headers, hash_bytes
from services.integrations import integrations
from services.passive_service import passive_service
from services.tls_service import inspect_tls
from storage.db import record_audit

PURPLE = 0x7C3AED
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_EMAIL_BYTES = 2 * 1024 * 1024


def _embed(title: str, text: str) -> discord.Embed:
    return discord.Embed(title=f"🟣 Purple Team • {title}", description=text[:4000], color=PURPLE)


def _guild_id(interaction: discord.Interaction) -> int:
    if interaction.guild_id is None:
        raise ValueError("This command must be used inside a server.")
    return interaction.guild_id


def _privileged(interaction: discord.Interaction) -> bool:
    if interaction.user.id == settings.bot_owner_id:
        return True
    return isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.manage_guild


async def _reverse_result(interaction: discord.Interaction, action: str, label: str, func) -> None:
    if not _privileged(interaction):
        await interaction.response.send_message("You need Manage Server permission for person intelligence.", ephemeral=True)
        return
    gid = _guild_id(interaction)
    await interaction.response.defer(thinking=True, ephemeral=True)
    await record_audit(gid, interaction.user.id, action, "redacted")
    try:
        data = await func()
        # Never dump rich PII responses into Discord. Confirm match/status and keep the query audited.
        if isinstance(data, dict):
            match = data.get("Match") or data.get("match") or data.get("Result") or data.get("result") or data
            status = "match returned" if match else "no match returned"
        else:
            status = "response returned"
        await interaction.followup.send(embed=_embed(label, f"EnformionGO: **{status}**\n\nDetailed provider records are intentionally not posted into Discord channels."), ephemeral=True)
    except Exception as exc:
        await interaction.followup.send(embed=_embed(label, str(exc)), ephemeral=True)


def register_extra_commands(bot) -> None:
    reverse = app_commands.Group(name="reverse", description="Permission-gated reverse person intelligence")
    analyze = app_commands.Group(name="analyze", description="Defensive file and email analysis")
    recon = app_commands.Group(name="recon", description="Lightweight passive reconnaissance")

    @reverse.command(name="phone", description="Reverse phone lookup through EnformionGO")
    async def reverse_phone(interaction: discord.Interaction, phone: str):
        await _reverse_result(interaction, "person.phone", "Reverse Phone", lambda: integrations.enformion_phone(phone))

    @reverse.command(name="email", description="Reverse email lookup through EnformionGO")
    async def reverse_email(interaction: discord.Interaction, email: str):
        await _reverse_result(interaction, "person.email", "Reverse Email", lambda: integrations.enformion_email(email))

    @reverse.command(name="address", description="Find people associated with an address through EnformionGO")
    async def reverse_address(interaction: discord.Interaction, street: str, city_state_zip: str):
        await _reverse_result(
            interaction,
            "person.address",
            "Address Intelligence",
            lambda: integrations.enformion_address(street, city_state_zip),
        )

    @analyze.command(name="file", description="Hash a small uploaded file and optionally check its SHA-256 in VirusTotal")
    async def analyze_file(interaction: discord.Interaction, attachment: discord.Attachment, virustotal: bool = True):
        if attachment.size > MAX_FILE_BYTES:
            await interaction.response.send_message("File is too large for this lightweight worker (8 MB max).", ephemeral=True)
            return
        await interaction.response.defer(thinking=True, ephemeral=True)
        data = await attachment.read()
        hashes = hash_bytes(data)
        text = "\n".join(f"**{k.upper()}:** `{v}`" for k, v in hashes.items())
        if virustotal and settings.virustotal_api_key:
            try:
                vt = await integrations.virustotal_lookup(hashes["sha256"])
                text += f"\n\n**VirusTotal:** `{json.dumps(vt.get('last_analysis_stats', {}))}`"
            except Exception as exc:
                text += f"\n\n**VirusTotal:** {exc}"
        await interaction.followup.send(embed=_embed("File Analysis", text), ephemeral=True)

    @analyze.command(name="email", description="Parse headers from an uploaded .eml file")
    async def analyze_email(interaction: discord.Interaction, attachment: discord.Attachment):
        if attachment.size > MAX_EMAIL_BYTES:
            await interaction.response.send_message("Email file is too large (2 MB max).", ephemeral=True)
            return
        await interaction.response.defer(thinking=True, ephemeral=True)
        raw = await attachment.read()
        result = analyze_email_headers(raw)
        text = "\n".join(f"**{k.replace('_', ' ').title()}:** {v}" for k, v in result.items())
        await interaction.followup.send(embed=_embed("Email Header Analysis", text), ephemeral=True)

    @recon.command(name="web", description="Probe HTTP response and common security headers")
    async def recon_web(interaction: discord.Interaction, target: str):
        await interaction.response.defer(thinking=True)
        try:
            data = await passive_service.http_probe(target)
            missing = [k for k, v in data.get("security_headers", {}).items() if not v]
            text = f"**URL:** {data.get('url')}\n**Status:** {data.get('status')}\n**Server:** {data.get('server') or 'hidden'}\n**Missing security headers:** {', '.join(missing) if missing else 'none'}"
            await interaction.followup.send(embed=_embed("Web Recon", text))
        except Exception as exc:
            await interaction.followup.send(embed=_embed("Web Recon", str(exc)), ephemeral=True)

    @recon.command(name="tls", description="Inspect the TLS certificate and negotiated protocol")
    async def recon_tls(interaction: discord.Interaction, target: str, port: int = 443):
        await interaction.response.defer(thinking=True)
        try:
            data = await inspect_tls(target, port)
            text = f"**Protocol:** {data['protocol']}\n**Cipher:** {data['cipher']}\n**Expires:** {data['not_after']}\n**Days remaining:** {data['days_remaining']}\n**SAN entries:** {data['san_count']}"
            await interaction.followup.send(embed=_embed("TLS Recon", text))
        except Exception as exc:
            await interaction.followup.send(embed=_embed("TLS Recon", str(exc)), ephemeral=True)

    bot.tree.add_command(reverse)
    bot.tree.add_command(analyze)
    bot.tree.add_command(recon)
