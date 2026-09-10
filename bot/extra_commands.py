import json

import discord
from discord import app_commands

from bot.ui import make_embed
from core.config import settings
from services.analyzers import analyze_email_headers, hash_bytes
from services.integrations import integrations
from services.passive_service import passive_service
from services.tls_service import inspect_tls
from storage.db import record_audit

MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_EMAIL_BYTES = 2 * 1024 * 1024


def _embed(title: str, text: str, *, kind: str = "default") -> discord.Embed:
    return make_embed(title, text, kind=kind)


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
        await interaction.response.send_message(embed=_embed("Access Denied", "You need **Manage Server** permission for person intelligence.", kind="error"), ephemeral=True)
        return
    gid = _guild_id(interaction)
    await interaction.response.defer(thinking=True, ephemeral=True)
    await record_audit(gid, interaction.user.id, action, "redacted")
    try:
        result = await func()
        found = bool(result.get("found")) if isinstance(result, dict) else bool(result)
        panel = _embed(label, "Permission-gated People Data Labs lookup completed.", kind="success" if found else "warning")
        panel.add_field(name="🔎 Match Status", value="**Match found**" if found else "No match returned", inline=False)
        panel.add_field(name="🔒 Free Plan", value="People Data Labs obscures contact-data values on the free plan. Purple Team uses supplied identifiers for matching but does not expose hidden contact fields.", inline=False)
        panel.add_field(name="🛡️ Privacy", value="Detailed provider records are intentionally not posted into Discord channels.", inline=False)
        await interaction.followup.send(embed=panel, ephemeral=True)
    except Exception as exc:
        await interaction.followup.send(embed=_embed(f"{label} Failed", str(exc), kind="error"), ephemeral=True)


def register_extra_commands(bot) -> None:
    reverse = app_commands.Group(name="reverse", description="Permission-gated person identifier intelligence")
    analyze = app_commands.Group(name="analyze", description="Defensive file and email analysis")
    recon = app_commands.Group(name="recon", description="Lightweight passive reconnaissance")

    @reverse.command(name="phone", description="Match a phone identifier through People Data Labs")
    async def reverse_phone(interaction: discord.Interaction, phone: str):
        await _reverse_result(interaction, "person.phone", "Reverse Phone", lambda: integrations.pdl_person_enrich(phone=phone))

    @reverse.command(name="email", description="Match an email identifier through People Data Labs")
    async def reverse_email(interaction: discord.Interaction, email: str):
        await _reverse_result(interaction, "person.email", "Reverse Email", lambda: integrations.pdl_person_enrich(email=email))

    @reverse.command(name="address", description="Match a person from address information through People Data Labs")
    async def reverse_address(interaction: discord.Interaction, street: str, city_state_zip: str):
        await _reverse_result(
            interaction,
            "person.address",
            "Address Intelligence",
            lambda: integrations.pdl_person_enrich(street_address=street, location=city_state_zip),
        )

    @analyze.command(name="file", description="Hash a small uploaded file and optionally check its SHA-256 in VirusTotal")
    async def analyze_file(interaction: discord.Interaction, attachment: discord.Attachment, virustotal: bool = True):
        if attachment.size > MAX_FILE_BYTES:
            await interaction.response.send_message(embed=_embed("File Rejected", "The uploaded file exceeds the **8 MB** analysis limit.", kind="warning"), ephemeral=True)
            return
        await interaction.response.defer(thinking=True, ephemeral=True)
        data = await attachment.read()
        hashes = hash_bytes(data)
        panel = _embed("File Analysis", f"Defensive analysis completed for `{attachment.filename}`.", kind="info")
        for key, value in hashes.items():
            panel.add_field(name=key.upper(), value=f"`{value}`", inline=False)
        if virustotal and settings.virustotal_api_key:
            try:
                vt = await integrations.virustotal_lookup(hashes["sha256"])
                panel.add_field(name="🧪 VirusTotal", value=f"`{json.dumps(vt.get('last_analysis_stats', {}))}`"[:1024], inline=False)
            except Exception as exc:
                panel.add_field(name="⚠️ VirusTotal", value=str(exc)[:1024], inline=False)
        await interaction.followup.send(embed=panel, ephemeral=True)

    @analyze.command(name="email", description="Parse headers from an uploaded .eml file")
    async def analyze_email(interaction: discord.Interaction, attachment: discord.Attachment):
        if attachment.size > MAX_EMAIL_BYTES:
            await interaction.response.send_message(embed=_embed("Email Rejected", "The uploaded email exceeds the **2 MB** analysis limit.", kind="warning"), ephemeral=True)
            return
        await interaction.response.defer(thinking=True, ephemeral=True)
        raw = await attachment.read()
        result = analyze_email_headers(raw)
        panel = _embed("Email Header Analysis", f"Header analysis completed for `{attachment.filename}`.", kind="info")
        for key, value in result.items():
            panel.add_field(name=key.replace('_', ' ').title(), value=str(value)[:1024], inline=False)
        await interaction.followup.send(embed=panel, ephemeral=True)

    @recon.command(name="web", description="Probe HTTP response and common security headers")
    async def recon_web(interaction: discord.Interaction, target: str):
        await interaction.response.defer(thinking=True)
        try:
            data = await passive_service.http_probe(target)
            missing = [k for k, v in data.get("security_headers", {}).items() if not v]
            panel = _embed("Web Recon", f"Passive HTTP posture for `{target}`.", kind="info")
            panel.add_field(name="🌐 URL", value=str(data.get('url') or 'Unknown'), inline=False)
            panel.add_field(name="📡 HTTP Status", value=str(data.get('status') or 'Unknown'), inline=True)
            panel.add_field(name="🖥️ Server", value=str(data.get('server') or 'Hidden'), inline=True)
            panel.add_field(name="🛡️ Missing Security Headers", value="\n".join(f"• `{x}`" for x in missing) if missing else "✅ None detected", inline=False)
            await interaction.followup.send(embed=panel)
        except Exception as exc:
            await interaction.followup.send(embed=_embed("Web Recon Failed", str(exc), kind="error"), ephemeral=True)

    @recon.command(name="tls", description="Inspect the TLS certificate and negotiated protocol")
    async def recon_tls(interaction: discord.Interaction, target: str, port: int = 443):
        await interaction.response.defer(thinking=True)
        try:
            data = await inspect_tls(target, port)
            panel = _embed("TLS Recon", f"TLS posture for `{target}:{port}`.", kind="info")
            panel.add_field(name="🔐 Protocol", value=str(data['protocol']), inline=True)
            panel.add_field(name="🔑 Cipher", value=str(data['cipher'])[:1024], inline=True)
            panel.add_field(name="📅 Expires", value=str(data['not_after']), inline=False)
            panel.add_field(name="⏳ Days Remaining", value=str(data['days_remaining']), inline=True)
            panel.add_field(name="🌐 SAN Entries", value=str(data['san_count']), inline=True)
            await interaction.followup.send(embed=panel)
        except Exception as exc:
            await interaction.followup.send(embed=_embed("TLS Recon Failed", str(exc), kind="error"), ephemeral=True)

    bot.tree.add_command(reverse)
    bot.tree.add_command(analyze)
    bot.tree.add_command(recon)
