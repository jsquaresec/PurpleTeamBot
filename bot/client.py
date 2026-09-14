import json
import platform

import discord
from discord import app_commands
from discord.ext import commands

from bot.ui import always_status, local_status, make_embed, status_dot
from core.config import settings
from core.targets import normalize_target
from services.integrations import integrations
from services.nmap_service import nmap_service
from services.passive_service import passive_service
from storage.db import (
    add_scope,
    database_status,
    init_db,
    list_scope,
    recent_history,
    record_audit,
    record_scan,
    remove_scope,
    target_in_scope,
)


def embed(title: str, description: str = "", *, kind: str = "default") -> discord.Embed:
    return make_embed(title, description, kind=kind)


def guild_id(interaction: discord.Interaction) -> int:
    if interaction.guild_id is None:
        raise ValueError("This command must be used inside a server.")
    return interaction.guild_id


def can_use_person_search(interaction: discord.Interaction) -> bool:
    if interaction.user.id == settings.bot_owner_id:
        return True
    return isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.manage_guild


class PurpleTeamBot(commands.Bot):
    def __init__(self) -> None:
        # CyberSpace community + event-stream logging needs these gateway events.
        # Members and Message Content must also be enabled in the Discord Developer
        # Portal under Privileged Gateway Intents.
        intents = discord.Intents.none()
        intents.guilds = True
        intents.members = True
        intents.moderation = True
        intents.voice_states = True
        intents.messages = True
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self) -> None:
        await init_db()
        self._register_commands()
        if settings.discord_guild_id:
            guild = discord.Object(id=settings.discord_guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    async def on_ready(self) -> None:
        await self.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="purple-team operations"))
        print(f"PurpleTeamBot online as {self.user} ({self.user.id if self.user else 'unknown'})")

    async def start_bot(self) -> None:
        if not settings.discord_token:
            raise RuntimeError("DISCORD_TOKEN is missing")
        async with self:
            await self.start(settings.discord_token)

    def _register_commands(self) -> None:
        scope = app_commands.Group(name="scope", description="Manage authorized active-scan scope")
        scan = app_commands.Group(name="scan", description="Authorized lightweight network scanning")
        osint = app_commands.Group(name="osint", description="Passive OSINT and public-source research")
        person = app_commands.Group(name="person", description="Permission-gated person intelligence")
        intel = app_commands.Group(name="intel", description="Threat-intelligence lookups")
        vuln = app_commands.Group(name="vuln", description="Vulnerability intelligence")

        @scope.command(name="add", description="Authorize a domain, IP, or CIDR for active scanning")
        @app_commands.default_permissions(manage_guild=True)
        async def scope_add(interaction: discord.Interaction, target: str):
            gid = guild_id(interaction)
            added = await add_scope(gid, target, interaction.user.id)
            await record_audit(gid, interaction.user.id, "scope.add", target)
            kind = "success" if added else "info"
            await interaction.response.send_message(
                embed=embed(
                    "Authorized Scope",
                    f"{'Target added to authorized scope.' if added else 'Target is already authorized.'}\n\n`{target}`",
                    kind=kind,
                ),
                ephemeral=True,
            )

        @scope.command(name="remove", description="Remove a target from authorized scope")
        @app_commands.default_permissions(manage_guild=True)
        async def scope_remove(interaction: discord.Interaction, target: str):
            gid = guild_id(interaction)
            removed = await remove_scope(gid, target)
            await record_audit(gid, interaction.user.id, "scope.remove", target)
            kind = "success" if removed else "warning"
            await interaction.response.send_message(
                embed=embed(
                    "Authorized Scope",
                    f"{'Target removed from authorized scope.' if removed else 'Target was not found in authorized scope.'}\n\n`{target}`",
                    kind=kind,
                ),
                ephemeral=True,
            )

        @scope.command(name="list", description="List authorized active-scan targets")
        async def scope_list(interaction: discord.Interaction):
            items = await list_scope(guild_id(interaction))
            text = "\n".join(f"`{index:02}`  •  `{target}`" for index, target in enumerate(items, 1)) if items else "No active-scan targets are currently authorized."
            panel = embed("Authorized Scope", "Targets explicitly approved for active assessment.", kind="info")
            panel.add_field(name=f"🎯 Targets ({len(items)})", value=text[:1024], inline=False)
            await interaction.response.send_message(embed=panel, ephemeral=True)

        async def run_scan(interaction: discord.Interaction, target: str, service: bool):
            gid = guild_id(interaction)
            target = normalize_target(target)
            if not await target_in_scope(gid, target):
                await interaction.response.send_message(
                    embed=embed(
                        "Assessment Blocked",
                        f"This target is not in the server's authorized scope.\n\n🎯 Target: `{target}`\n\nUse `/scope add` before running active assessment commands.",
                        kind="error",
                    ),
                    ephemeral=True,
                )
                return
            await interaction.response.defer(thinking=True)
            try:
                result = await (nmap_service.service_scan(target) if service else nmap_service.quick_scan(target))
                lines = [f"`{p.port}/{p.protocol}`  •  **{p.service}**  {p.version}".strip() for p in result.ports]
                summary = "\n".join(lines) if lines else "No selected ports reported open."
                await record_scan(gid, interaction.user.id, target, "service" if service else "quick", "ok", summary)
                panel = embed("Service Scan Complete" if service else "Quick Scan Complete", "Authorized active assessment finished successfully.", kind="success")
                panel.add_field(name="🎯 Target", value=f"`{target}`", inline=True)
                panel.add_field(name="📡 Open Ports", value=str(len(result.ports)), inline=True)
                panel.add_field(name="🔬 Results", value=summary[:1024], inline=False)
                await interaction.followup.send(embed=panel)
            except Exception as exc:
                await record_scan(gid, interaction.user.id, target, "service" if service else "quick", "error", str(exc))
                await interaction.followup.send(embed=embed("Scan Failed", f"The assessment could not be completed.\n\n```text\n{str(exc)[:1200]}\n```", kind="error"), ephemeral=True)

        @scan.command(name="quick", description="Scan a small high-value TCP port set")
        async def scan_quick(interaction: discord.Interaction, target: str):
            await run_scan(interaction, target, False)

        @scan.command(name="service", description="Run light service/version detection on a compact port set")
        async def scan_service(interaction: discord.Interaction, target: str):
            await run_scan(interaction, target, True)

        @osint.command(name="dns", description="Query common DNS records")
        async def osint_dns(interaction: discord.Interaction, domain: str):
            await interaction.response.defer(thinking=True)
            data = await passive_service.dns_records(domain)
            panel = embed("DNS Intelligence", f"Public DNS records for `{domain}`.", kind="info")
            for key, values in data.items():
                panel.add_field(name=f"▸ {key}", value="\n".join(f"`{v}`" for v in values[:8]) or "`none`", inline=False)
            await interaction.followup.send(embed=panel)

        @osint.command(name="rdap", description="Look up public registration data for a domain or IP")
        async def osint_rdap(interaction: discord.Interaction, target: str):
            await interaction.response.defer(thinking=True)
            data = await passive_service.rdap(target)
            panel = embed("RDAP Intelligence", f"Registration and ownership metadata for `{target}`.", kind="info")
            panel.add_field(name="🏷️ Name / Handle", value=str(data.get('name') or data.get('handle') or 'Unknown'), inline=True)
            panel.add_field(name="🌎 Country", value=str(data.get('country') or 'Unknown'), inline=True)
            panel.add_field(name="📌 Status", value=', '.join(data.get('status', [])[:8]) or 'Unknown', inline=False)
            await interaction.followup.send(embed=panel)

        @osint.command(name="subdomains", description="Find certificate-transparency names for a domain")
        async def osint_subdomains(interaction: discord.Interaction, domain: str):
            await interaction.response.defer(thinking=True)
            names = await passive_service.certificate_names(domain)
            text = "\n".join(f"• `{x}`" for x in names[:40]) or "No certificate names found."
            panel = embed("Certificate Transparency", f"Observed certificate names for `{domain}`.", kind="info")
            panel.add_field(name=f"🔐 Names ({len(names)})", value=text[:1024], inline=False)
            await interaction.followup.send(embed=panel)

        @osint.command(name="username", description="Check public GitHub profile data for a username")
        async def osint_username(interaction: discord.Interaction, username: str):
            await interaction.response.defer(thinking=True)
            data = await passive_service.github_username(username)
            if not data:
                await interaction.followup.send(embed=embed("Username Intelligence", f"No GitHub profile was found for `{username}`.", kind="warning"))
                return
            panel = embed("Username Intelligence", f"Public profile intelligence for `{username}`.", kind="info")
            for key, value in data.items():
                if value not in (None, ""):
                    panel.add_field(name=key.replace('_', ' ').title(), value=str(value)[:1024], inline=True)
            await interaction.followup.send(embed=panel)

        @person.command(name="search", description="Permission-gated EnformionGO and public-source person search")
        async def person_search(interaction: discord.Interaction, first_name: str, last_name: str, city: str = "", state: str = "", email: str = "", phone: str = ""):
            gid = guild_id(interaction)
            if not can_use_person_search(interaction):
                await interaction.response.send_message(embed=embed("Access Denied", "You need **Manage Server** permission to use person intelligence.", kind="error"), ephemeral=True)
                return
            await interaction.response.defer(thinking=True, ephemeral=True)
            await record_audit(gid, interaction.user.id, "person.search", f"{first_name} {last_name}", f"city={city};state={state};email_supplied={bool(email)};phone_supplied={bool(phone)}")
            try:
                payload = {
                    "FirstName": first_name.strip(),
                    "LastName": last_name.strip(),
                    "Page": 1,
                    "ResultsPerPage": 10,
                }
                if city or state:
                    payload["Addresses"] = [{"AddressLine2": f"{city}, {state}".strip(", ")}]
                if email:
                    payload["Email"] = email.strip()
                if phone:
                    payload["Phone"] = phone.strip()

                provider_data = await integrations.enformion_person_search(payload)
                query = " ".join(x for x in [first_name.strip(), last_name.strip(), city.strip(), state.strip()] if x)
                public_data = await passive_service.person_search(query)

                candidates = []
                if isinstance(provider_data, dict):
                    candidates = provider_data.get("Persons") or provider_data.get("Results") or provider_data.get("persons") or []
                count = len(candidates) if isinstance(candidates, list) else "available"

                panel = embed("Person Intelligence", "EnformionGO and public-source correlation completed.", kind="info")
                panel.add_field(name="👤 Query", value=f"**{first_name} {last_name}**", inline=True)
                panel.add_field(name="🧠 EnformionGO", value=f"Candidate results: **{count}**\nProvider response retained only in this ephemeral lookup.", inline=False)
                panel.add_field(name="🔎 Public Sources", value=f"```json\n{json.dumps(public_data, indent=2)[:850]}\n```", inline=False)
                panel.add_field(name="🔒 Privacy", value="Permission-gated, ephemeral, and audit logged.", inline=False)
                await interaction.followup.send(embed=panel, ephemeral=True)
            except Exception as exc:
                await interaction.followup.send(embed=embed("Person Search Failed", str(exc), kind="error"), ephemeral=True)

        @person.command(name="public", description="Lightweight public-source person/identifier correlation")
        async def person_public(interaction: discord.Interaction, query: str):
            await interaction.response.defer(thinking=True, ephemeral=True)
            data = await passive_service.person_search(query)
            await interaction.followup.send(embed=embed("Public Person OSINT", f"```json\n{json.dumps(data, indent=2)[:3300]}\n```", kind="info"), ephemeral=True)

        @intel.command(name="lookup", description="VirusTotal lookup for a domain, IP, or hash")
        async def intel_lookup(interaction: discord.Interaction, value: str):
            await interaction.response.defer(thinking=True)
            try:
                data = await integrations.virustotal_lookup(value)
                panel = embed("Threat Intelligence", f"VirusTotal intelligence for `{value}`.", kind="info")
                panel.add_field(name="📊 Provider Data", value=f"```json\n{json.dumps(data, indent=2)[:900]}\n```", inline=False)
                await interaction.followup.send(embed=panel)
            except Exception as exc:
                await interaction.followup.send(embed=embed("Threat Intelligence Failed", str(exc), kind="error"), ephemeral=True)

        @intel.command(name="breach", description="Check an email address for known breach exposure via XposedOrNot")
        async def intel_breach(interaction: discord.Interaction, account: str):
            gid = guild_id(interaction)
            if not can_use_person_search(interaction):
                await interaction.response.send_message(embed=embed("Access Denied", "You need **Manage Server** permission for breach-account lookups.", kind="error"), ephemeral=True)
                return
            await interaction.response.defer(thinking=True, ephemeral=True)
            await record_audit(gid, interaction.user.id, "intel.breach", "redacted-account")
            try:
                names = await integrations.xposed_account(account)
                panel = embed("Breach Exposure", "XposedOrNot free breach exposure check.", kind="warning" if names else "success")
                panel.add_field(name="📊 Breaches Found", value=str(len(names)), inline=True)
                panel.add_field(name="🗂️ Breach Sources", value="\n".join(f"• {x}" for x in names[:20]) if names else "No breaches returned.", inline=False)
                panel.add_field(name="🔒 Privacy", value="The queried email address is not displayed in this response.", inline=False)
                await interaction.followup.send(embed=panel, ephemeral=True)
            except Exception as exc:
                await interaction.followup.send(embed=embed("Breach Lookup Failed", str(exc), kind="error"), ephemeral=True)

        @vuln.command(name="cve", description="Combine CVE, EPSS, and CISA KEV intelligence")
        async def vuln_cve(interaction: discord.Interaction, cve: str):
            await interaction.response.defer(thinking=True)
            cve = cve.strip().upper()
            try:
                base = await passive_service.cve_lookup(cve)
                epss = await integrations.epss(cve)
                kev = await integrations.cisa_kev(cve)
                meta = base.get("cveMetadata", {})
                panel = embed("Vulnerability Intelligence", f"Risk intelligence for `{meta.get('cveId', cve)}`.", kind="warning" if kev else "info")
                panel.add_field(name="📌 State", value=str(meta.get('state', 'Unknown')), inline=True)
                panel.add_field(name="📈 EPSS", value=str((epss or {}).get('epss', 'n/a')), inline=True)
                panel.add_field(name="🎯 EPSS Percentile", value=str((epss or {}).get('percentile', 'n/a')), inline=True)
                panel.add_field(name="🚨 CISA KEV", value="Listed" if kev else "Not listed", inline=True)
                await interaction.followup.send(embed=panel)
            except Exception as exc:
                await interaction.followup.send(embed=embed("Vulnerability Intelligence Failed", str(exc), kind="error"), ephemeral=True)

        for group in (scope, scan, osint, person, intel, vuln):
            self.tree.add_command(group)
