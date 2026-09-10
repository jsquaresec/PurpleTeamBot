import json
import platform

import discord
from discord import app_commands
from discord.ext import commands

from core.config import settings
from core.targets import normalize_target
from services.integrations import integrations
from services.nmap_service import nmap_service
from services.passive_service import passive_service
from storage.db import (
    add_scope,
    init_db,
    list_scope,
    recent_history,
    record_audit,
    record_scan,
    remove_scope,
    target_in_scope,
)

PURPLE = 0x7C3AED


def embed(title: str, description: str = "") -> discord.Embed:
    return discord.Embed(title=f"🟣 Purple Team • {title}", description=description[:4000], color=PURPLE)


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
        super().__init__(command_prefix="!", intents=discord.Intents.none())

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
            await interaction.response.send_message(embed=embed("Scope", f"{'Added' if added else 'Already present'}: `{target}`"), ephemeral=True)

        @scope.command(name="remove", description="Remove a target from authorized scope")
        @app_commands.default_permissions(manage_guild=True)
        async def scope_remove(interaction: discord.Interaction, target: str):
            gid = guild_id(interaction)
            removed = await remove_scope(gid, target)
            await record_audit(gid, interaction.user.id, "scope.remove", target)
            await interaction.response.send_message(embed=embed("Scope", f"{'Removed' if removed else 'Not found'}: `{target}`"), ephemeral=True)

        @scope.command(name="list", description="List authorized active-scan targets")
        async def scope_list(interaction: discord.Interaction):
            items = await list_scope(guild_id(interaction))
            text = "\n".join(f"• `{x}`" for x in items) if items else "No active-scan scope configured."
            await interaction.response.send_message(embed=embed("Authorized Scope", text), ephemeral=True)

        async def run_scan(interaction: discord.Interaction, target: str, service: bool):
            gid = guild_id(interaction)
            target = normalize_target(target)
            if not await target_in_scope(gid, target):
                await interaction.response.send_message(embed=embed("Blocked", "Target is not in this server's authorized scope."), ephemeral=True)
                return
            await interaction.response.defer(thinking=True)
            try:
                result = await (nmap_service.service_scan(target) if service else nmap_service.quick_scan(target))
                lines = [f"`{p.port}/{p.protocol}` **{p.service}** {p.version}".strip() for p in result.ports]
                summary = "\n".join(lines) if lines else "No selected ports reported open."
                await record_scan(gid, interaction.user.id, target, "service" if service else "quick", "ok", summary)
                await interaction.followup.send(embed=embed("Service Scan" if service else "Quick Scan", f"Target: `{target}`\n\n{summary[:3500]}"))
            except Exception as exc:
                await record_scan(gid, interaction.user.id, target, "service" if service else "quick", "error", str(exc))
                await interaction.followup.send(embed=embed("Scan Error", str(exc)), ephemeral=True)

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
            text = "\n".join(f"**{k}:** {', '.join(v[:8]) or 'none'}" for k, v in data.items())
            await interaction.followup.send(embed=embed("DNS", text[:3900]))

        @osint.command(name="rdap", description="Look up public registration data for a domain or IP")
        async def osint_rdap(interaction: discord.Interaction, target: str):
            await interaction.response.defer(thinking=True)
            data = await passive_service.rdap(target)
            text = f"**Name:** {data.get('name') or data.get('handle') or 'unknown'}\n**Country:** {data.get('country', 'unknown')}\n**Status:** {', '.join(data.get('status', [])[:8]) or 'unknown'}"
            await interaction.followup.send(embed=embed("RDAP", text))

        @osint.command(name="subdomains", description="Find certificate-transparency names for a domain")
        async def osint_subdomains(interaction: discord.Interaction, domain: str):
            await interaction.response.defer(thinking=True)
            names = await passive_service.certificate_names(domain)
            text = "\n".join(f"• `{x}`" for x in names[:40]) or "No certificate names found."
            await interaction.followup.send(embed=embed("Certificate Transparency", text[:3900]))

        @osint.command(name="username", description="Check public GitHub profile data for a username")
        async def osint_username(interaction: discord.Interaction, username: str):
            await interaction.response.defer(thinking=True)
            data = await passive_service.github_username(username)
            if not data:
                await interaction.followup.send(embed=embed("Username", "No GitHub profile found."))
                return
            text = "\n".join(f"**{k.replace('_', ' ').title()}:** {v}" for k, v in data.items() if v not in (None, ""))
            await interaction.followup.send(embed=embed("Username", text[:3900]))

        @person.command(name="search", description="Admin-only Enformion/public-source person search")
        async def person_search(interaction: discord.Interaction, first_name: str, last_name: str, city: str = "", state: str = "", email: str = "", phone: str = ""):
            gid = guild_id(interaction)
            if not can_use_person_search(interaction):
                await interaction.response.send_message("You need Manage Server permission for person intelligence.", ephemeral=True)
                return
            await interaction.response.defer(thinking=True, ephemeral=True)
            payload = {"FirstName": first_name.strip(), "LastName": last_name.strip(), "Page": 1, "ResultsPerPage": 10}
            if city or state:
                payload["Addresses"] = [{"AddressLine2": f"{city}, {state}".strip(", ")}]
            if email:
                payload["Emails"] = [email.strip()]
            if phone:
                payload["PhoneNumbers"] = [phone.strip()]
            await record_audit(gid, interaction.user.id, "person.search", f"{first_name} {last_name}", f"city={city};state={state};email_supplied={bool(email)};phone_supplied={bool(phone)}")
            try:
                data = await integrations.enformion_person_search(payload)
                # Keep Discord output deliberately summarized; detailed provider payload is never posted publicly.
                if isinstance(data, dict):
                    candidates = data.get("Persons") or data.get("Results") or data.get("persons") or []
                    count = len(candidates) if isinstance(candidates, list) else "available"
                else:
                    count = "available"
                text = f"Provider: **EnformionGO**\nQuery: **{first_name} {last_name}**\nCandidate results: **{count}**\n\nSensitive provider responses are not posted into public channels."
                await interaction.followup.send(embed=embed("Person Search", text), ephemeral=True)
            except Exception as exc:
                await interaction.followup.send(embed=embed("Person Search", str(exc)), ephemeral=True)

        @person.command(name="public", description="Lightweight public-source person/identifier correlation")
        async def person_public(interaction: discord.Interaction, query: str):
            await interaction.response.defer(thinking=True, ephemeral=True)
            data = await passive_service.person_search(query)
            await interaction.followup.send(embed=embed("Public Person OSINT", f"```json\n{json.dumps(data, indent=2)[:3300]}\n```"), ephemeral=True)

        @intel.command(name="lookup", description="VirusTotal lookup for a domain, IP, or hash")
        async def intel_lookup(interaction: discord.Interaction, value: str):
            await interaction.response.defer(thinking=True)
            try:
                data = await integrations.virustotal_lookup(value)
                await interaction.followup.send(embed=embed("Threat Intel", f"```json\n{json.dumps(data, indent=2)[:3300]}\n```"))
            except Exception as exc:
                await interaction.followup.send(embed=embed("Threat Intel", str(exc)), ephemeral=True)

        @intel.command(name="breach", description="Check an account identifier for known breach exposure via HIBP")
        async def intel_breach(interaction: discord.Interaction, account: str):
            gid = guild_id(interaction)
            if not can_use_person_search(interaction):
                await interaction.response.send_message("You need Manage Server permission for breach-account lookups.", ephemeral=True)
                return
            await interaction.response.defer(thinking=True, ephemeral=True)
            await record_audit(gid, interaction.user.id, "intel.breach", "redacted-account")
            try:
                rows = await integrations.hibp_account(account)
                names = [str(x.get("Name", "unknown")) for x in rows[:20]]
                text = f"Breaches found: **{len(rows)}**\n" + ("\n".join(f"• {x}" for x in names) if names else "No breaches returned.") + "\n\nSource: Have I Been Pwned"
                await interaction.followup.send(embed=embed("Breach Exposure", text), ephemeral=True)
            except Exception as exc:
                await interaction.followup.send(embed=embed("Breach Exposure", str(exc)), ephemeral=True)

        @vuln.command(name="cve", description="Combine CVE, EPSS, and CISA KEV intelligence")
        async def vuln_cve(interaction: discord.Interaction, cve: str):
            await interaction.response.defer(thinking=True)
            cve = cve.strip().upper()
            try:
                base = await passive_service.cve_lookup(cve)
                epss = await integrations.epss(cve)
                kev = await integrations.cisa_kev(cve)
                meta = base.get("cveMetadata", {})
                text = f"**CVE:** {meta.get('cveId', cve)}\n**State:** {meta.get('state', 'unknown')}\n**EPSS:** {(epss or {}).get('epss', 'n/a')}\n**EPSS percentile:** {(epss or {}).get('percentile', 'n/a')}\n**CISA KEV:** {'YES' if kev else 'No'}"
                if kev:
                    text += f"\n**Required action:** {kev.get('requiredAction', 'See CISA KEV')}"
                await interaction.followup.send(embed=embed("Vulnerability Intelligence", text[:3900]))
            except Exception as exc:
                await interaction.followup.send(embed=embed("Vulnerability Intelligence", str(exc)), ephemeral=True)

        @self.tree.command(name="investigate", description="Run passive domain intelligence and, when authorized, a lightweight active scan")
        async def investigate(interaction: discord.Interaction, target: str):
            gid = guild_id(interaction)
            target = normalize_target(target)
            await interaction.response.defer(thinking=True)
            sections = []
            try:
                dns_data = await passive_service.dns_records(target)
                sections.append("**DNS:** " + ", ".join(dns_data.get("A", [])[:5]))
            except Exception:
                sections.append("**DNS:** unavailable")
            try:
                names = await passive_service.certificate_names(target)
                sections.append(f"**Certificate names:** {len(names)}")
            except Exception:
                sections.append("**Certificate names:** unavailable")
            try:
                http_data = await passive_service.http_probe(target)
                sections.append(f"**HTTP:** {http_data.get('status')} • {http_data.get('server') or 'server hidden'}")
                missing = [k for k, v in http_data.get("security_headers", {}).items() if not v]
                sections.append(f"**Missing security headers:** {len(missing)}")
            except Exception:
                sections.append("**HTTP:** unavailable")
            if await target_in_scope(gid, target):
                try:
                    result = await nmap_service.quick_scan(target)
                    sections.append("**Open selected ports:** " + (", ".join(str(p.port) for p in result.ports) or "none"))
                    await record_scan(gid, interaction.user.id, target, "investigate", "ok", ",".join(str(p.port) for p in result.ports))
                except Exception as exc:
                    sections.append(f"**Active scan:** {exc}")
            else:
                sections.append("**Active scan:** skipped — target not in authorized scope")
            await interaction.followup.send(embed=embed("Investigation", f"Target: `{target}`\n\n" + "\n".join(sections)))

        @self.tree.command(name="history", description="Show recent scan history for this server")
        async def history(interaction: discord.Interaction):
            rows = await recent_history(guild_id(interaction), 10)
            text = "\n".join(f"• `{r[0]}` • {r[1]} • **{r[2]}** • {r[3]}" for r in rows) or "No scan history yet."
            await interaction.response.send_message(embed=embed("History", text), ephemeral=True)

        @self.tree.command(name="status", description="Show Purple Team runtime and configured integrations")
        async def status(interaction: discord.Interaction):
            configured = [
                f"EnformionGO: {'✓' if settings.enformion_ap_name and settings.enformion_ap_password else '—'}",
                f"VirusTotal: {'✓' if settings.virustotal_api_key else '—'}",
                f"HIBP: {'✓' if settings.hibp_api_key else '—'}",
                "FIRST EPSS: ✓",
                "CISA KEV: ✓",
                "Nmap: local",
            ]
            text = f"Python: `{platform.python_version()}`\nMax active scans: `{settings.max_active_scans}`\n\n" + "\n".join(configured)
            await interaction.response.send_message(embed=embed("Status", text), ephemeral=True)

        self.tree.add_command(scope)
        self.tree.add_command(scan)
        self.tree.add_command(osint)
        self.tree.add_command(person)
        self.tree.add_command(intel)
        self.tree.add_command(vuln)
