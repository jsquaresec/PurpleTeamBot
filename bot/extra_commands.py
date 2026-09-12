import json

import discord
from discord import app_commands

from bot.ui import make_embed
from core.config import settings
from services.advanced_osint import advanced_osint
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


def _compact_json(data: object) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False)[:900]


async def _check_privileged(interaction: discord.Interaction, action: str) -> bool:
    if not _privileged(interaction):
        await interaction.response.send_message(
            embed=_embed("Access Denied", "You need **Manage Server** permission for person intelligence.", kind="error"),
            ephemeral=True,
        )
        return False
    await record_audit(_guild_id(interaction), interaction.user.id, action, "redacted")
    return True


def register_extra_commands(bot) -> None:
    reverse = app_commands.Group(name="reverse", description="Permission-gated identifier intelligence")
    analyze = app_commands.Group(name="analyze", description="Defensive file and email analysis")
    recon = app_commands.Group(name="recon", description="Lightweight passive reconnaissance")

    @reverse.command(name="phone", description="EnformionGO reverse phone intelligence")
    async def reverse_phone(interaction: discord.Interaction, phone: str):
        if not await _check_privileged(interaction, "person.phone"):
            return
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            data = await integrations.enformion_phone(phone)
            panel = _embed("Reverse Phone Intelligence", "EnformionGO phone intelligence completed.", kind="info")
            panel.add_field(name="📞 Provider Result", value=f"```json\n{_compact_json(data)}\n```", inline=False)
            try:
                caller = await integrations.usa_caller_lookup(phone)
                panel.add_field(name="📡 Public Caller Context", value=f"```json\n{_compact_json(caller)}\n```", inline=False)
            except AttributeError:
                pass
            except Exception:
                pass
            panel.add_field(name="🔒 Access", value="Permission-gated, ephemeral, and audit logged.", inline=False)
            await interaction.followup.send(embed=panel, ephemeral=True)
        except Exception as exc:
            await interaction.followup.send(embed=_embed("Reverse Phone Failed", str(exc), kind="error"), ephemeral=True)

    @reverse.command(name="email", description="EnformionGO reverse email intelligence")
    async def reverse_email(interaction: discord.Interaction, email: str):
        if not await _check_privileged(interaction, "person.email"):
            return
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            data = await integrations.enformion_email(email)
            panel = _embed("Reverse Email Intelligence", "EnformionGO email intelligence completed.", kind="info")
            panel.add_field(name="📧 Provider Result", value=f"```json\n{_compact_json(data)}\n```", inline=False)
            try:
                breaches = await integrations.xposed_account(email)
                panel.add_field(
                    name="🛡️ Breach Exposure",
                    value="\n".join(f"• {x}" for x in breaches[:20]) if breaches else "No XposedOrNot breach records returned.",
                    inline=False,
                )
            except Exception:
                pass
            await interaction.followup.send(embed=panel, ephemeral=True)
        except Exception as exc:
            await interaction.followup.send(embed=_embed("Reverse Email Failed", str(exc), kind="error"), ephemeral=True)

    @reverse.command(name="address", description="EnformionGO reverse address intelligence")
    async def reverse_address(interaction: discord.Interaction, address_line1: str, city_state_zip: str):
        if not await _check_privileged(interaction, "person.address"):
            return
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            data = await integrations.enformion_address(address_line1, city_state_zip)
            panel = _embed("Reverse Address Intelligence", "EnformionGO address intelligence completed.", kind="info")
            panel.add_field(name="🏠 Provider Result", value=f"```json\n{_compact_json(data)}\n```", inline=False)
            panel.add_field(name="🔒 Access", value="Permission-gated, ephemeral, and audit logged.", inline=False)
            await interaction.followup.send(embed=panel, ephemeral=True)
        except Exception as exc:
            await interaction.followup.send(embed=_embed("Reverse Address Failed", str(exc), kind="error"), ephemeral=True)

    @reverse.command(name="username", description="Correlate a username across public platforms")
    async def reverse_username(interaction: discord.Interaction, username: str):
        if not await _check_privileged(interaction, "person.username"):
            return
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            rows = await advanced_osint.username_profiles(username)
            found = [row for row in rows if row.get("present")]
            panel = _embed("Username Footprint", f"Public-source username correlation for `{username}`.", kind="info")
            panel.add_field(
                name=f"🔎 Confirmed Profiles ({len(found)})",
                value="\n".join(f"• **{row['site']}** — {row['url']}" for row in found)[:1024] or "No matching public profiles were confirmed.",
                inline=False,
            )
            await interaction.followup.send(embed=panel, ephemeral=True)
        except Exception as exc:
            await interaction.followup.send(embed=_embed("Username Footprint Failed", str(exc), kind="error"), ephemeral=True)

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
