import json

import discord
from discord import app_commands

from bot.free_person_commands import _require_privileged, _xposed_account
from bot.threaded_intel_commands import _embed, _result_thread
from core.targets import normalize_target
from services.advanced_osint import advanced_osint
from services.assessment import assessment_service
from services.integrations import integrations
from services.nmap_service import nmap_service
from services.passive_service import passive_service
from services.reputation import reputation_service
from services.tls_service import inspect_tls
from storage.db import record_audit, record_scan, target_in_scope


def _guild_id(interaction: discord.Interaction) -> int:
    if interaction.guild_id is None:
        raise ValueError("This command must be used inside a server.")
    return interaction.guild_id


async def _scoped_target(interaction: discord.Interaction, target: str) -> tuple[int, str] | None:
    gid = _guild_id(interaction)
    target = normalize_target(target)
    if not await target_in_scope(gid, target):
        await interaction.response.send_message(
            embed=_embed(
                "Assessment Blocked",
                f"This target is not in the server's authorized assessment scope.\n\n🎯 Target: `{target}`",
                kind="error",
            ),
            ephemeral=True,
        )
        return None
    return gid, target


def register_threaded_remaining_commands(bot) -> None:
    # Replace remaining result-heavy groups with thread-first variants.
    for name in ("recon", "reputation", "passive", "osintx", "assess", "vuln"):
        bot.tree.remove_command(name)

    recon = app_commands.Group(name="recon", description="Lightweight passive reconnaissance in threads")
    reputation = app_commands.Group(name="reputation", description="IP, host, and IOC reputation intelligence in threads")
    passive = app_commands.Group(name="passive", description="Free-first API-backed passive intelligence in threads")
    osintx = app_commands.Group(name="osintx", description="Extended public-source OSINT in threads")
    assess = app_commands.Group(name="assess", description="Authorized lightweight security assessment in threads")
    vuln = app_commands.Group(name="vuln", description="Vulnerability intelligence in threads")

    @recon.command(name="web", description="Probe HTTP response and common security headers in a results thread")
    async def recon_web(interaction: discord.Interaction, target: str):
        await interaction.response.defer(thinking=True)
        thread = None
        try:
            thread = await _result_thread(interaction, "recon-web")
            data = await passive_service.http_probe(target)
            missing = [k for k, v in data.get("security_headers", {}).items() if not v]
            panel = _embed("Web Recon", f"Passive HTTP posture for `{target}`.", kind="info")
            panel.add_field(name="🌐 URL", value=str(data.get("url") or "Unknown"), inline=False)
            panel.add_field(name="📡 HTTP Status", value=str(data.get("status") or "Unknown"), inline=True)
            panel.add_field(name="🖥️ Server", value=str(data.get("server") or "Hidden"), inline=True)
            panel.add_field(name="🛡️ Missing Security Headers", value="\n".join(f"• `{x}`" for x in missing) if missing else "✅ None detected", inline=False)
            await thread.send(embed=panel)
        except Exception as exc:
            error = _embed("Web Recon Failed", str(exc), kind="error")
            await (thread.send(embed=error) if thread else interaction.followup.send(embed=error))

    @recon.command(name="tls", description="Inspect TLS certificate and negotiated protocol in a results thread")
    async def recon_tls(interaction: discord.Interaction, target: str, port: int = 443):
        await interaction.response.defer(thinking=True)
        thread = None
        try:
            thread = await _result_thread(interaction, "recon-tls")
            data = await inspect_tls(target, port)
            panel = _embed("TLS Recon", f"TLS posture for `{target}:{port}`.", kind="info")
            panel.add_field(name="🔐 Protocol", value=str(data["protocol"]), inline=True)
            panel.add_field(name="🔑 Cipher", value=str(data["cipher"])[:1024], inline=True)
            panel.add_field(name="📅 Expires", value=str(data["not_after"]), inline=False)
            panel.add_field(name="⏳ Days Remaining", value=str(data["days_remaining"]), inline=True)
            panel.add_field(name="🌐 SAN Entries", value=str(data["san_count"]), inline=True)
            await thread.send(embed=panel)
        except Exception as exc:
            error = _embed("TLS Recon Failed", str(exc), kind="error")
            await (thread.send(embed=error) if thread else interaction.followup.send(embed=error))

    @reputation.command(name="abuseipdb", description="Check an IP address with AbuseIPDB in a results thread")
    async def abuseipdb(interaction: discord.Interaction, ip: str):
        await interaction.response.defer(thinking=True)
        thread = None
        try:
            thread = await _result_thread(interaction, "abuseipdb")
            data = await reputation_service.abuseipdb(ip)
            panel = _embed("AbuseIPDB Intelligence", f"Reputation intelligence for `{ip}`.", kind="info")
            for key, value in data.items():
                if value not in (None, "", []):
                    panel.add_field(name=key.replace("_", " ").title(), value=str(value)[:1024], inline=True)
            await thread.send(embed=panel)
        except Exception as exc:
            error = _embed("AbuseIPDB Lookup Failed", str(exc), kind="error")
            await (thread.send(embed=error) if thread else interaction.followup.send(embed=error))

    @reputation.command(name="censys", description="Look up a host with Censys in a results thread")
    async def censys(interaction: discord.Interaction, ip: str):
        await interaction.response.defer(thinking=True)
        thread = None
        try:
            thread = await _result_thread(interaction, "censys")
            data = await reputation_service.censys_host(ip)
            panel = _embed("Censys Host Intelligence", f"Internet-exposure intelligence for `{ip}`.", kind="info")
            if not data.get("found"):
                panel.add_field(name="Result", value="No Censys host record was found.", inline=False)
            else:
                panel.add_field(name="Provider", value="Censys Platform API", inline=True)
                panel.add_field(name="Host", value=f"`{ip}`", inline=True)
                panel.add_field(name="Provider Data", value=f"```json\n{json.dumps(data.get('data', {}), indent=2)[:900]}\n```", inline=False)
            await thread.send(embed=panel)
        except Exception as exc:
            error = _embed("Censys Lookup Failed", str(exc), kind="error")
            await (thread.send(embed=error) if thread else interaction.followup.send(embed=error))

    @reputation.command(name="otx", description="Enrich an IP, domain, URL, or hash with OTX in a results thread")
    async def otx(interaction: discord.Interaction, value: str):
        await interaction.response.defer(thinking=True)
        thread = None
        try:
            thread = await _result_thread(interaction, "otx")
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
            await thread.send(embed=panel)
        except Exception as exc:
            error = _embed("OTX Lookup Failed", str(exc), kind="error")
            await (thread.send(embed=error) if thread else interaction.followup.send(embed=error))

    @passive.command(name="urlscan", description="Search historical public web scans in a results thread")
    async def urlscan(interaction: discord.Interaction, value: str):
        await interaction.response.defer(thinking=True)
        thread = None
        try:
            thread = await _result_thread(interaction, "urlscan")
            data = await reputation_service.urlscan_search(value)
            rows = data.get("results", [])
            panel = _embed("urlscan.io Intelligence", f"Historical web-scan intelligence for `{value}`.", kind="info")
            panel.add_field(name="Query", value=f"`{data.get('query', value)}`", inline=True)
            panel.add_field(name="Results Returned", value=str(len(rows)), inline=True)
            if rows:
                rendered = [f"• `{row.get('domain') or 'unknown'}` • `{row.get('ip') or 'no IP'}` • {row.get('country') or '??'}" for row in rows[:8]]
                panel.add_field(name="Recent Matches", value="\n".join(rendered)[:1024], inline=False)
            else:
                panel.add_field(name="Recent Matches", value="No matching public scans were returned.", inline=False)
            await thread.send(embed=panel)
        except Exception as exc:
            error = _embed("urlscan.io Lookup Failed", str(exc), kind="error")
            await (thread.send(embed=error) if thread else interaction.followup.send(embed=error))

    @osintx.command(name="email", description="Inspect public email-domain security posture in a results thread")
    async def osint_email(interaction: discord.Interaction, email_or_domain: str):
        await interaction.response.defer(thinking=True)
        thread = None
        try:
            thread = await _result_thread(interaction, "osint-email")
            data = await advanced_osint.email_posture(email_or_domain)
            panel = _embed("Email / Domain Posture", f"Public mail-security posture for `{data['domain']}`.", kind="info")
            panel.add_field(name="📬 MX", value="\n".join(data["mx"][:5]) or "None", inline=False)
            panel.add_field(name="🛡️ SPF", value="\n".join(data["spf"][:3]) or "Not found", inline=False)
            panel.add_field(name="🔐 DMARC", value="\n".join(data["dmarc"][:3]) or "Not found", inline=False)
            panel.add_field(name="🌐 DNSSEC", value="🟢 DNSKEY observed" if data["dnssec_dnskey"] else "⚫ DNSKEY not observed", inline=False)
            await thread.send(embed=panel)
        except Exception as exc:
            error = _embed("Email / Domain Posture Failed", str(exc), kind="error")
            await (thread.send(embed=error) if thread else interaction.followup.send(embed=error))

    @osintx.command(name="reverse-dns", description="Resolve public PTR/hostname information in a results thread")
    async def reverse_dns(interaction: discord.Interaction, ip: str):
        await interaction.response.defer(thinking=True)
        thread = None
        try:
            thread = await _result_thread(interaction, "osint-reverse-dns")
            data = await advanced_osint.reverse_dns(ip)
            panel = _embed("Reverse DNS", f"Public PTR intelligence for `{data['ip']}`.", kind="info")
            panel.add_field(name="🖥️ Hostname", value=data["hostname"] or "Not found", inline=False)
            panel.add_field(name="🔗 Aliases", value="\n".join(data["aliases"]) or "None", inline=False)
            await thread.send(embed=panel)
        except Exception as exc:
            error = _embed("Reverse DNS Failed", str(exc), kind="error")
            await (thread.send(embed=error) if thread else interaction.followup.send(embed=error))

    @osintx.command(name="profiles", description="Correlate a public username across platforms in a results thread")
    async def username_profiles(interaction: discord.Interaction, username: str):
        await interaction.response.defer(thinking=True)
        thread = None
        try:
            thread = await _result_thread(interaction, "osint-profiles")
            rows = await advanced_osint.username_profiles(username)
            found = [r for r in rows if r["present"]]
            panel = _embed("Username Correlation", f"Public profile correlation for `{username}`.", kind="info")
            panel.add_field(name=f"🔎 Confirmed Profiles ({len(found)})", value="\n".join(f"• **{r['site']}** — {r['url']}" for r in found)[:1024] or "No matching public profiles were confirmed.", inline=False)
            await thread.send(embed=panel)
        except Exception as exc:
            error = _embed("Username Correlation Failed", str(exc), kind="error")
            await (thread.send(embed=error) if thread else interaction.followup.send(embed=error))

    async def _run_assess(interaction: discord.Interaction, target: str, mode: str):
        scoped = await _scoped_target(interaction, target)
        if not scoped:
            return
        gid, target = scoped
        await interaction.response.defer(thinking=True)
        thread = None
        try:
            thread = await _result_thread(interaction, f"assess-{mode}")
            if mode == "fingerprint":
                data = await assessment_service.web_fingerprint(target)
                panel = _embed("Technology Fingerprint", f"Authorized web technology fingerprint for `{target}`.", kind="success")
                panel.add_field(name="🌐 URL", value=str(data["url"]), inline=False)
                panel.add_field(name="📡 Status", value=str(data["status"]), inline=True)
                panel.add_field(name="🖥️ Server", value=data["server"] or "Hidden", inline=True)
                panel.add_field(name="⚙️ Powered By", value=data["powered_by"] or "Hidden", inline=True)
                panel.add_field(name="🧩 Technologies", value=", ".join(data["technologies"]) or "None confidently detected", inline=False)
            elif mode == "exposure":
                rows = await assessment_service.exposed_paths(target)
                interesting = [r for r in rows if r.get("interesting")]
                panel = _embed("Exposure Check", f"Safe metadata-only exposure checks for `{target}`.", kind="warning" if interesting else "success")
                panel.add_field(name=f"🚨 Interesting Paths ({len(interesting)})", value="\n".join(f"• `{r['path']}` — HTTP {r['status']} — {r.get('content_type') or 'unknown type'}" for r in interesting)[:1024] or "✅ No selected exposure paths returned HTTP 200/206.", inline=False)
                panel.add_field(name="🔒 Safety", value="Checks status and metadata only; discovered file contents are not dumped.", inline=False)
            else:
                data = await assessment_service.allowed_methods(target)
                panel = _embed("HTTP Methods", f"Authorized method inspection for `{target}`.", kind="success")
                panel.add_field(name="📡 HTTP Status", value=str(data["status"]), inline=True)
                panel.add_field(name="📋 Allow", value=data["allow"] or "Not advertised", inline=True)
                panel.add_field(name="🗂️ WebDAV", value=data["dav"] or "Not advertised", inline=False)
            await record_audit(gid, interaction.user.id, f"assess.{mode}", target)
            await thread.send(embed=panel)
        except Exception as exc:
            error = _embed(f"Assessment {mode.title()} Failed", str(exc), kind="error")
            await (thread.send(embed=error) if thread else interaction.followup.send(embed=error))

    @assess.command(name="fingerprint", description="Fingerprint web technologies on an authorized target in a thread")
    async def fingerprint(interaction: discord.Interaction, target: str):
        await _run_assess(interaction, target, "fingerprint")

    @assess.command(name="exposure", description="Check safe commonly exposed web paths in a thread")
    async def exposure(interaction: discord.Interaction, target: str):
        await _run_assess(interaction, target, "exposure")

    @assess.command(name="methods", description="Inspect advertised HTTP methods on an authorized target in a thread")
    async def methods(interaction: discord.Interaction, target: str):
        await _run_assess(interaction, target, "methods")

    @vuln.command(name="cve", description="Combine CVE, EPSS, and CISA KEV intelligence in a results thread")
    async def vuln_cve(interaction: discord.Interaction, cve: str):
        await interaction.response.defer(thinking=True)
        thread = None
        try:
            thread = await _result_thread(interaction, "vuln-cve")
            cve = cve.strip().upper()
            base = await passive_service.cve_lookup(cve)
            epss = await integrations.epss(cve)
            kev = await integrations.cisa_kev(cve)
            meta = base.get("cveMetadata", {})
            panel = _embed("Vulnerability Intelligence", f"Risk intelligence for `{meta.get('cveId', cve)}`.", kind="warning" if kev else "info")
            panel.add_field(name="📌 State", value=str(meta.get("state", "Unknown")), inline=True)
            panel.add_field(name="📈 EPSS", value=str((epss or {}).get("epss", "n/a")), inline=True)
            panel.add_field(name="🎯 EPSS Percentile", value=str((epss or {}).get("percentile", "n/a")), inline=True)
            panel.add_field(name="🚨 CISA KEV", value="**YES — Known Exploited**" if kev else "No", inline=False)
            if kev:
                panel.add_field(name="🛡️ Required Action", value=str(kev.get("requiredAction", "See CISA KEV"))[:1024], inline=False)
            await thread.send(embed=panel)
        except Exception as exc:
            error = _embed("Vulnerability Lookup Failed", str(exc), kind="error")
            await (thread.send(embed=error) if thread else interaction.followup.send(embed=error))

    # Add thread-first breach handling to the existing intel group.
    intel = bot.tree.get_command("intel")
    if isinstance(intel, app_commands.Group):
        intel.remove_command("breach")

        @intel.command(name="breach", description="Check an email address for breach exposure in a results thread")
        async def intel_breach(interaction: discord.Interaction, account: str):
            if not await _require_privileged(interaction, "intel.breach"):
                return
            await interaction.response.defer(thinking=True)
            thread = None
            try:
                thread = await _result_thread(interaction, "intel-breach")
                names = await _xposed_account(account)
                panel = _embed("Breach Exposure", "XposedOrNot free breach exposure check.", kind="warning" if names else "success")
                panel.add_field(name="📊 Breaches Found", value=str(len(names)), inline=True)
                panel.add_field(name="🗂️ Breach Sources", value="\n".join(f"• {x}" for x in names[:20]) if names else "No breaches returned.", inline=False)
                panel.add_field(name="🔒 Privacy", value="The queried email address is not displayed in this response.", inline=False)
                await thread.send(embed=panel)
            except Exception as exc:
                error = _embed("Breach Lookup Failed", str(exc), kind="error")
                await (thread.send(embed=error) if thread else interaction.followup.send(embed=error))

    # Replace top-level investigate with a thread-first version.
    bot.tree.remove_command("investigate")

    @bot.tree.command(name="investigate", description="Run combined passive intelligence and authorized lightweight scanning in a thread")
    async def investigate(interaction: discord.Interaction, target: str):
        gid = _guild_id(interaction)
        target = normalize_target(target)
        await interaction.response.defer(thinking=True)
        thread = None
        try:
            thread = await _result_thread(interaction, "investigate")
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
            active_scoped = await target_in_scope(gid, target)
            if active_scoped:
                try:
                    result = await nmap_service.quick_scan(target)
                    sections.append("**Open selected ports:** " + (", ".join(str(p.port) for p in result.ports) or "none"))
                    await record_scan(gid, interaction.user.id, target, "investigate", "ok", ",".join(str(p.port) for p in result.ports))
                except Exception as exc:
                    sections.append(f"**Active scan:** {exc}")
            else:
                sections.append("**Active scan:** skipped — target not in authorized scope")
            panel = _embed("Investigation Summary", f"Combined passive intelligence for `{target}`.", kind="info")
            panel.add_field(name="🔎 Findings", value="\n".join(sections)[:1024], inline=False)
            panel.add_field(name="🛡️ Active Assessment", value="🟢 Authorized" if active_scoped else "⚫ Not in scope — passive only", inline=False)
            await thread.send(embed=panel)
        except Exception as exc:
            error = _embed("Investigation Failed", str(exc), kind="error")
            await (thread.send(embed=error) if thread else interaction.followup.send(embed=error))

    bot.tree.add_command(recon)
    bot.tree.add_command(reputation)
    bot.tree.add_command(passive)
    bot.tree.add_command(osintx)
    bot.tree.add_command(assess)
    bot.tree.add_command(vuln)
