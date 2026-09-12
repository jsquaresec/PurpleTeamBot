import discord
from discord import app_commands

from bot.threaded_intel_commands import _embed, _result_thread
from core.targets import normalize_target
from services.nmap_service import nmap_service
from services.passive_service import passive_service
from storage.db import record_scan, target_in_scope


def _guild_id(interaction: discord.Interaction) -> int:
    if interaction.guild_id is None:
        raise ValueError("This command must be used inside a server.")
    return interaction.guild_id


def register_threaded_scan_osint_commands(bot) -> None:
    # Replace the core scan/OSINT groups with thread-first versions.
    bot.tree.remove_command("scan")
    bot.tree.remove_command("osint")

    scan = app_commands.Group(name="scan", description="Authorized lightweight network scanning in threads")
    osint = app_commands.Group(name="osint", description="Passive OSINT with results organized in threads")

    async def run_scan(interaction: discord.Interaction, target: str, service: bool) -> None:
        gid = _guild_id(interaction)
        target = normalize_target(target)
        if not await target_in_scope(gid, target):
            await interaction.response.send_message(
                embed=_embed(
                    "Assessment Blocked",
                    f"This target is not in the server's authorized scope.\n\n🎯 Target: `{target}`\n\nUse `/scope add` before running active assessment commands.",
                    kind="error",
                ),
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True)
        thread = None
        try:
            thread = await _result_thread(interaction, "service-scan" if service else "quick-scan")
            await thread.send(
                f"🛰️ **{'Service' if service else 'Quick'} scan started by {interaction.user.mention}**\n"
                f"🎯 Target: `{target}`"
            )
            result = await (nmap_service.service_scan(target) if service else nmap_service.quick_scan(target))
            lines = [
                f"`{p.port}/{p.protocol}`  •  **{p.service}**  {p.version}".strip()
                for p in result.ports
            ]
            summary = "\n".join(lines) if lines else "No selected ports reported open."
            scan_type = "service" if service else "quick"
            await record_scan(gid, interaction.user.id, target, scan_type, "ok", summary)

            panel = _embed(
                "Service Scan Complete" if service else "Quick Scan Complete",
                "Authorized active assessment finished successfully.",
                kind="success",
            )
            panel.add_field(name="🎯 Target", value=f"`{target}`", inline=True)
            panel.add_field(name="📡 Open Ports", value=str(len(result.ports)), inline=True)
            panel.add_field(name="🔬 Results", value=summary[:1024], inline=False)
            await thread.send(embed=panel)
        except Exception as exc:
            await record_scan(
                gid,
                interaction.user.id,
                target,
                "service" if service else "quick",
                "error",
                str(exc),
            )
            error = _embed(
                "Scan Failed",
                f"The assessment could not be completed.\n\n```text\n{str(exc)[:1200]}\n```",
                kind="error",
            )
            if thread is not None:
                await thread.send(embed=error)
            else:
                await interaction.followup.send(embed=error)

    @scan.command(name="quick", description="Scan a small high-value TCP port set in a results thread")
    async def scan_quick(interaction: discord.Interaction, target: str):
        await run_scan(interaction, target, False)

    @scan.command(name="service", description="Run light service/version detection in a results thread")
    async def scan_service(interaction: discord.Interaction, target: str):
        await run_scan(interaction, target, True)

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
