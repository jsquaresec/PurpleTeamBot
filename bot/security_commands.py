import discord
from discord import app_commands

from services.advanced_osint import advanced_osint
from services.assessment import assessment_service
from storage.db import record_audit, target_in_scope
from core.targets import normalize_target

PURPLE = 0x7C3AED


def _embed(title: str, text: str) -> discord.Embed:
    return discord.Embed(title=f"🟣 Purple Team • {title}", description=text[:4000], color=PURPLE)


def _guild_id(interaction: discord.Interaction) -> int:
    if interaction.guild_id is None:
        raise ValueError("This command must be used inside a server.")
    return interaction.guild_id


async def _require_scope(interaction: discord.Interaction, target: str) -> tuple[int, str] | None:
    gid = _guild_id(interaction)
    target = normalize_target(target)
    if not await target_in_scope(gid, target):
        await interaction.response.send_message(
            embed=_embed("Blocked", "Target is not in this server's authorized assessment scope."),
            ephemeral=True,
        )
        return None
    return gid, target


def register_security_commands(bot) -> None:
    osintx = app_commands.Group(name="osintx", description="Extended public-source OSINT")
    assess = app_commands.Group(name="assess", description="Authorized lightweight security assessment")

    @osintx.command(name="email", description="Inspect public email-domain security posture")
    async def osint_email(interaction: discord.Interaction, email_or_domain: str):
        await interaction.response.defer(thinking=True)
        try:
            data = await advanced_osint.email_posture(email_or_domain)
            text = (
                f"**Domain:** `{data['domain']}`\n"
                f"**MX:** {', '.join(data['mx'][:5]) or 'none'}\n"
                f"**SPF:** {', '.join(data['spf'][:3]) or 'not found'}\n"
                f"**DMARC:** {', '.join(data['dmarc'][:3]) or 'not found'}\n"
                f"**DNSSEC DNSKEY:** {'present' if data['dnssec_dnskey'] else 'not observed'}"
            )
            await interaction.followup.send(embed=_embed("Email / Domain Posture", text))
        except Exception as exc:
            await interaction.followup.send(embed=_embed("Email / Domain Posture", str(exc)), ephemeral=True)

    @osintx.command(name="reverse-dns", description="Resolve public PTR/hostname information for an IP")
    async def reverse_dns(interaction: discord.Interaction, ip: str):
        await interaction.response.defer(thinking=True)
        try:
            data = await advanced_osint.reverse_dns(ip)
            text = f"**IP:** `{data['ip']}`\n**Hostname:** {data['hostname'] or 'not found'}\n**Aliases:** {', '.join(data['aliases']) or 'none'}"
            await interaction.followup.send(embed=_embed("Reverse DNS", text))
        except Exception as exc:
            await interaction.followup.send(embed=_embed("Reverse DNS", str(exc)), ephemeral=True)

    @osintx.command(name="profiles", description="Correlate a public username across common platforms")
    async def username_profiles(interaction: discord.Interaction, username: str):
        await interaction.response.defer(thinking=True)
        try:
            rows = await advanced_osint.username_profiles(username)
            found = [r for r in rows if r['present']]
            text = "\n".join(f"• **{r['site']}** — {r['url']}" for r in found) or "No matching public profiles were confirmed."
            await interaction.followup.send(embed=_embed("Username Correlation", text))
        except Exception as exc:
            await interaction.followup.send(embed=_embed("Username Correlation", str(exc)), ephemeral=True)

    @assess.command(name="fingerprint", description="Fingerprint web technologies on an authorized target")
    async def fingerprint(interaction: discord.Interaction, target: str):
        scoped = await _require_scope(interaction, target)
        if not scoped:
            return
        gid, target = scoped
        await interaction.response.defer(thinking=True)
        try:
            data = await assessment_service.web_fingerprint(target)
            await record_audit(gid, interaction.user.id, "assess.fingerprint", target)
            text = (
                f"**URL:** {data['url']}\n**Status:** {data['status']}\n"
                f"**Server:** {data['server'] or 'hidden'}\n"
                f"**Powered By:** {data['powered_by'] or 'hidden'}\n"
                f"**Generator:** {data['generator'] or 'not exposed'}\n"
                f"**Technologies:** {', '.join(data['technologies']) or 'none confidently detected'}"
            )
            await interaction.followup.send(embed=_embed("Technology Fingerprint", text))
        except Exception as exc:
            await interaction.followup.send(embed=_embed("Technology Fingerprint", str(exc)), ephemeral=True)

    @assess.command(name="exposure", description="Check a small safe list of commonly exposed web paths")
    async def exposure(interaction: discord.Interaction, target: str):
        scoped = await _require_scope(interaction, target)
        if not scoped:
            return
        gid, target = scoped
        await interaction.response.defer(thinking=True)
        try:
            rows = await assessment_service.exposed_paths(target)
            await record_audit(gid, interaction.user.id, "assess.exposure", target)
            interesting = [r for r in rows if r.get('interesting')]
            text = "\n".join(
                f"• `{r['path']}` — HTTP {r['status']} — {r.get('content_type') or 'unknown type'}"
                for r in interesting
            ) or "No selected exposure paths returned HTTP 200/206."
            text += "\n\nChecks only status/metadata and does not dump discovered file contents."
            await interaction.followup.send(embed=_embed("Exposure Check", text))
        except Exception as exc:
            await interaction.followup.send(embed=_embed("Exposure Check", str(exc)), ephemeral=True)

    @assess.command(name="methods", description="Inspect advertised HTTP methods on an authorized target")
    async def methods(interaction: discord.Interaction, target: str):
        scoped = await _require_scope(interaction, target)
        if not scoped:
            return
        gid, target = scoped
        await interaction.response.defer(thinking=True)
        try:
            data = await assessment_service.allowed_methods(target)
            await record_audit(gid, interaction.user.id, "assess.methods", target)
            text = f"**HTTP status:** {data['status']}\n**Allow:** {data['allow'] or 'not advertised'}\n**WebDAV:** {data['dav'] or 'not advertised'}"
            await interaction.followup.send(embed=_embed("HTTP Methods", text))
        except Exception as exc:
            await interaction.followup.send(embed=_embed("HTTP Methods", str(exc)), ephemeral=True)

    bot.tree.add_command(osintx)
    bot.tree.add_command(assess)
