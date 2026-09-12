import discord
from discord import app_commands

from bot.threaded_intel_commands import _embed, _result_thread
from core.config import settings
from core.targets import normalize_target
from services.nmap_service import nmap_service
from services.passive_service import passive_service
from storage.db import add_scope, record_audit, record_scan, target_in_scope


def _guild_id(interaction: discord.Interaction) -> int:
    if interaction.guild_id is None:
        raise ValueError("This command must be used inside a server.")
    return interaction.guild_id


def _can_authorize(interaction: discord.Interaction) -> bool:
    if interaction.user.id == settings.bot_owner_id:
        return True
    return isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.manage_guild


async def _ensure_scan_scope(interaction: discord.Interaction, target: str, authorize: bool) -> tuple[int, str] | None:
    gid = _guild_id(interaction)
    target = normalize_target(target)
    if await target_in_scope(gid, target):
        return gid, target

    if authorize and _can_authorize(interaction):
        await add_scope(gid, target, interaction.user.id)
        await record_audit(gid, interaction.user.id, "scope.add.scan", target, "authorized_inline=true")
        return gid, target

    message = (
        f"This target is not in the server's authorized scope.\n\n🎯 Target: `{target}`\n\n"
        "A server manager can rerun the scan with `authorize: True` to authorize and scan it in one step, "
        "or use `/scope add` first."
    )
    if authorize and not _can_authorize(interaction):
        message = "Only the bot owner or a member with **Manage Server** can authorize a new scan target."

    await interaction.response.send_message(
        embed=_embed("Assessment Blocked", message, kind="error"),
        ephemeral=True,
    )
    return None


def register_threaded_scan_osint_commands(bot) -> None:
    bot.tree.remove_command("scan")
    bot.tree.remove_command("osint")

    scan = app_commands.Group(name="scan", description="Authorized lightweight network scanning in threads")
    osint = app_commands.Group(name="osint", description="Passive OSINT with results organized in threads")

    async def run_scan(interaction: discord.Interaction, target: str, scan_type: str, authorize: bool) -> None:
        scoped = await _ensure_scan_scope(interaction, target, authorize)
        if not scoped:
            return
        gid, target = scoped

        runners = {
            "quick": ("Quick", nmap_service.quick_scan),
            "service": ("Service", nmap_service.service_scan),
            "web": ("Web Surface", nmap_service.web_scan),
            "infrastructure": ("Infrastructure", nmap_service.infrastructure_scan),
            "database": ("Database / Data Service", nmap_service.database_scan),
            "extended": ("Extended", nmap_service.extended_scan),
        }
        label, runner = runners[scan_type]

        await interaction.response.defer(thinking=True)
        thread = None
        try:
            thread = await _result_thread(interaction, f"{scan_type}-scan")
            await thread.send(
                f"🛰️ **{label} scan started by {interaction.user.mention}**\n"
                f"🎯 Target: `{target}`"
            )
            result = await runner(target)
            lines = [
                f"`{p.port}/{p.protocol}`  •  **{p.service}**  {p.version}".strip()
                for p in result.ports
            ]
            summary = "\n".join(lines) if lines else "No selected ports reported open."
            await record_scan(gid, interaction.user.id, target, scan_type, "ok", summary)

            panel = _embed(
                f"{label} Scan Complete",
                "Authorized active assessment finished successfully.",
                kind="success",
            )
            panel.add_field(name="🎯 Target", value=f"`{target}`", inline=True)
            panel.add_field(name="📡 Open Ports", value=str(len(result.ports)), inline=True)
            panel.add_field(name="🧭 Preset", value=scan_type.title(), inline=True)
            panel.add_field(name="🔬 Results", value=summary[:1024], inline=False)
            await thread.send(embed=panel)
        except Exception as exc:
            await record_scan(gid, interaction.user.id, target, scan_type, "error", str(exc))
            error = _embed(
                "Scan Failed",
                f"The assessment could not be completed.\n\n```text\n{str(exc)[:1200]}\n```",
                kind="error",
            )
            if thread is not None:
                await thread.send(embed=error)
            else:
                await interaction.followup.send(embed=error)

    @scan.command(name="quick", description="Scan a compact high-value TCP port set")
    async def scan_quick(interaction: discord.Interaction, target: str, authorize: bool = False):
        await run_scan(interaction, target, "quick", authorize)

    @scan.command(name="service", description="Run light service/version detection on common services")
    async def scan_service(interaction: discord.Interaction, target: str, authorize: bool = False):
        await run_scan(interaction, target, "service", authorize)

    @scan.command(name="web", description="Scan common HTTP, HTTPS, proxy, and admin web ports")
    async def scan_web(interaction: discord.Interaction, target: str, authorize: bool = False):
        await run_scan(interaction, target, "web", authorize)

    @scan.command(name="infrastructure", description="Scan common infrastructure and remote-management ports")
    async def scan_infrastructure(interaction: discord.Interaction, target: str, authorize: bool = False):
        await run_scan(interaction, target, "infrastructure", authorize)

    @scan.command(name="database", description="Scan common database, cache, search, and data-service ports")
    async def scan_database(interaction: discord.Interaction, target: str, authorize: bool = False):
        await run_scan(interaction, target, "database", authorize)

    @scan.command(name="extended", description="Run a broader curated TCP service scan")
    async def scan_extended(interaction: discord.Interaction, target: str, authorize: bool = False):
        await run_scan(interaction, target, "extended", authorize)

    @osint.command(name="dns", description="Query common DNS records in a results thread")
    async def osint_dns(interaction: discord.Interaction, domain: str):
        await interaction.response.defer(thinking=True)
        try:
            thread = await _result_thread(interaction, "osint-dns")
            data = await passive_service.dns_records(domain)
            panel = _embed("DNS Intelligence", f"Public DNS records for `{domain}`.", kind="info")
            for key, values in data.items():
                panel.add_field(
                    name=f"▸ {key}",
                    value="\n".join(f"`{v}`" for v in values[:8]) or "`none`",
                    inline=False,
                )
            await thread.send(embed=panel)
        except Exception as exc:
            await interaction.followup.send(embed=_embed("DNS Intelligence Failed", str(exc), kind="error"))

    @osint.command(name="rdap", description="Look up public domain/IP registration data in a results thread")
    async def osint_rdap(interaction: discord.Interaction, target: str):
        await interaction.response.defer(thinking=True)
        try:
            thread = await _result_thread(interaction, "osint-rdap")
            data = await passive_service.rdap(target)
            panel = _embed("RDAP Intelligence", f"Registration and ownership metadata for `{target}`.", kind="info")
            panel.add_field(name="🏷️ Name / Handle", value=str(data.get("name") or data.get("handle") or "Unknown"), inline=True)
            panel.add_field(name="🌎 Country", value=str(data.get("country") or "Unknown"), inline=True)
            panel.add_field(name="📌 Status", value=", ".join(data.get("status", [])[:8]) or "Unknown", inline=False)
            await thread.send(embed=panel)
        except Exception as exc:
            await interaction.followup.send(embed=_embed("RDAP Intelligence Failed", str(exc), kind="error"))

    @osint.command(name="subdomains", description="Find certificate-transparency names in a results thread")
    async def osint_subdomains(interaction: discord.Interaction, domain: str):
        await interaction.response.defer(thinking=True)
        try:
            thread = await _result_thread(interaction, "osint-subdomains")
            names = await passive_service.certificate_names(domain)
            text = "\n".join(f"• `{x}`" for x in names[:40]) or "No certificate names found."
            panel = _embed("Certificate Transparency", f"Observed certificate names for `{domain}`.", kind="info")
            panel.add_field(name=f"🔐 Names ({len(names)})", value=text[:1024], inline=False)
            await thread.send(embed=panel)
        except Exception as exc:
            await interaction.followup.send(embed=_embed("Subdomain Discovery Failed", str(exc), kind="error"))

    @osint.command(name="username", description="Check public GitHub profile data in a results thread")
    async def osint_username(interaction: discord.Interaction, username: str):
        await interaction.response.defer(thinking=True)
        try:
            thread = await _result_thread(interaction, "osint-username")
            data = await passive_service.github_username(username)
            if not data:
                await thread.send(embed=_embed("Username Intelligence", f"No GitHub profile was found for `{username}`.", kind="warning"))
                return
            panel = _embed("Username Intelligence", f"Public profile intelligence for `{username}`.", kind="info")
            for key, value in data.items():
                if value not in (None, ""):
                    panel.add_field(name=key.replace("_", " ").title(), value=str(value)[:1024], inline=True)
            await thread.send(embed=panel)
        except Exception as exc:
            await interaction.followup.send(embed=_embed("Username Intelligence Failed", str(exc), kind="error"))

    bot.tree.add_command(scan)
    bot.tree.add_command(osint)
