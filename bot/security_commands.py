import discord
from discord import app_commands

from bot.ui import make_embed
from services.advanced_osint import advanced_osint
from services.assessment import assessment_service
from storage.db import record_audit, target_in_scope
from core.targets import normalize_target


def _embed(title: str, text: str = "", *, kind: str = "default") -> discord.Embed:
    return make_embed(title, text, kind=kind)


def _guild_id(interaction: discord.Interaction) -> int:
    if interaction.guild_id is None:
        raise ValueError("This command must be used inside a server.")
    return interaction.guild_id


async def _require_scope(interaction: discord.Interaction, target: str) -> tuple[int, str] | None:
    gid = _guild_id(interaction)
    target = normalize_target(target)
    if not await target_in_scope(gid, target):
        await interaction.response.send_message(
            embed=_embed("Assessment Blocked", f"This target is not in the server's authorized assessment scope.\n\n🎯 Target: `{target}`", kind="error"),
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
            panel = _embed("Email / Domain Posture", f"Public mail-security posture for `{data['domain']}`.", kind="info")
            panel.add_field(name="📬 MX", value="\n".join(data['mx'][:5]) or "None", inline=False)
            panel.add_field(name="🛡️ SPF", value="\n".join(data['spf'][:3]) or "Not found", inline=False)
            panel.add_field(name="🔐 DMARC", value="\n".join(data['dmarc'][:3]) or "Not found", inline=False)
            panel.add_field(name="🌐 DNSSEC", value="🟢 DNSKEY observed" if data['dnssec_dnskey'] else "⚫ DNSKEY not observed", inline=False)
            await interaction.followup.send(embed=panel)
        except Exception as exc:
            await interaction.followup.send(embed=_embed("Email / Domain Posture Failed", str(exc), kind="error"), ephemeral=True)

    @osintx.command(name="reverse-dns", description="Resolve public PTR/hostname information for an IP")
    async def reverse_dns(interaction: discord.Interaction, ip: str):
        await interaction.response.defer(thinking=True)
        try:
            data = await advanced_osint.reverse_dns(ip)
            panel = _embed("Reverse DNS", f"Public PTR intelligence for `{data['ip']}`.", kind="info")
            panel.add_field(name="🖥️ Hostname", value=data['hostname'] or "Not found", inline=False)
            panel.add_field(name="🔗 Aliases", value="\n".join(data['aliases']) or "None", inline=False)
            await interaction.followup.send(embed=panel)
        except Exception as exc:
            await interaction.followup.send(embed=_embed("Reverse DNS Failed", str(exc), kind="error"), ephemeral=True)

    @osintx.command(name="profiles", description="Correlate a public username across common platforms")
    async def username_profiles(interaction: discord.Interaction, username: str):
        await interaction.response.defer(thinking=True)
        try:
            rows = await advanced_osint.username_profiles(username)
            found = [r for r in rows if r['present']]
            panel = _embed("Username Correlation", f"Public profile correlation for `{username}`.", kind="info")
            panel.add_field(name=f"🔎 Confirmed Profiles ({len(found)})", value="\n".join(f"• **{r['site']}** — {r['url']}" for r in found)[:1024] or "No matching public profiles were confirmed.", inline=False)
            await interaction.followup.send(embed=panel)
        except Exception as exc:
            await interaction.followup.send(embed=_embed("Username Correlation Failed", str(exc), kind="error"), ephemeral=True)

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
            panel = _embed("Technology Fingerprint", f"Authorized web technology fingerprint for `{target}`.", kind="success")
            panel.add_field(name="🌐 URL", value=str(data['url']), inline=False)
            panel.add_field(name="📡 Status", value=str(data['status']), inline=True)
            panel.add_field(name="🖥️ Server", value=data['server'] or "Hidden", inline=True)
            panel.add_field(name="⚙️ Powered By", value=data['powered_by'] or "Hidden", inline=True)
            panel.add_field(name="🧩 Technologies", value=", ".join(data['technologies']) or "None confidently detected", inline=False)
            await interaction.followup.send(embed=panel)
        except Exception as exc:
            await interaction.followup.send(embed=_embed("Technology Fingerprint Failed", str(exc), kind="error"), ephemeral=True)

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
            panel = _embed("Exposure Check", f"Safe metadata-only exposure checks for `{target}`.", kind="warning" if interesting else "success")
            panel.add_field(name=f"🚨 Interesting Paths ({len(interesting)})", value="\n".join(f"• `{r['path']}` — HTTP {r['status']} — {r.get('content_type') or 'unknown type'}" for r in interesting)[:1024] or "✅ No selected exposure paths returned HTTP 200/206.", inline=False)
            panel.add_field(name="🔒 Safety", value="Checks status and metadata only; discovered file contents are not dumped.", inline=False)
            await interaction.followup.send(embed=panel)
        except Exception as exc:
            await interaction.followup.send(embed=_embed("Exposure Check Failed", str(exc), kind="error"), ephemeral=True)

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
            panel = _embed("HTTP Methods", f"Authorized method inspection for `{target}`.", kind="success")
            panel.add_field(name="📡 HTTP Status", value=str(data['status']), inline=True)
            panel.add_field(name="📋 Allow", value=data['allow'] or "Not advertised", inline=True)
            panel.add_field(name="🗂️ WebDAV", value=data['dav'] or "Not advertised", inline=False)
            await interaction.followup.send(embed=panel)
        except Exception as exc:
            await interaction.followup.send(embed=_embed("HTTP Methods Failed", str(exc), kind="error"), ephemeral=True)

    bot.tree.add_command(osintx)
    bot.tree.add_command(assess)
