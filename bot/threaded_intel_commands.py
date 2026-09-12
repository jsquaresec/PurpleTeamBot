import asyncio
import ipaddress
import re

import discord
import httpx
from discord import app_commands

from bot.free_person_commands import (
    _cross_platform_candidates,
    _github_people,
    _gitlab_people,
    _phone_summary,
    _require_privileged,
    _usa_caller_lookup,
    _xposed_account,
)
from bot.personpages_person_commands import _personpages_lookup, _profile_summary
from bot.ui import make_embed
from bot.you_person_commands import _web_results_text, _you_search
from core.config import settings
from services.advanced_osint import advanced_osint
from services.passive_service import passive_service


def _embed(title: str, text: str = "", *, kind: str = "default") -> discord.Embed:
    return make_embed(title, text, kind=kind)


def _safe_thread_name(prefix: str, user: discord.abc.User) -> str:
    username = re.sub(r"[^a-zA-Z0-9_-]+", "-", getattr(user, "display_name", "user")).strip("-") or "user"
    return f"{prefix}-{username}"[:90]


async def _result_thread(interaction: discord.Interaction, prefix: str) -> discord.Thread:
    channel = interaction.channel
    if isinstance(channel, discord.Thread):
        return channel
    if not isinstance(channel, discord.TextChannel):
        raise RuntimeError("Run this command in a server text channel so Purple Team can create a results thread.")

    thread = await channel.create_thread(
        name=_safe_thread_name(prefix, interaction.user),
        type=discord.ChannelType.public_thread,
        auto_archive_duration=60,
        reason=f"Purple Team results for {interaction.user}",
    )
    await interaction.followup.send(f"🧵 Results posted in {thread.mention}")
    return thread


async def _geoip_lookup(value: str) -> dict:
    value = value.strip()
    try:
        ip = str(ipaddress.ip_address(value))
    except ValueError:
        records = await passive_service.dns_records(value)
        addresses = (records.get("A") or []) + (records.get("AAAA") or [])
        if not addresses:
            raise ValueError("No A or AAAA records were found for that domain.")
        ip = addresses[0]

    try:
        parsed = ipaddress.ip_address(ip)
        if parsed.is_private or parsed.is_loopback or parsed.is_link_local or parsed.is_reserved:
            raise ValueError("GeoIP requires a public IP address.")
    except ValueError as exc:
        if "GeoIP requires" in str(exc):
            raise
        raise ValueError("GeoIP requires a valid public IP or resolvable domain.") from exc

    timeout = httpx.Timeout(settings.http_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent": settings.user_agent}) as client:
        response = await client.get(f"https://ipapi.co/{ip}/json/")
        response.raise_for_status()
        data = response.json()
        if data.get("error"):
            raise RuntimeError(str(data.get("reason") or "GeoIP provider returned an error"))
        return data


def register_threaded_intel_commands(bot) -> None:
    # Replace person/reverse with thread-first variants after all other command
    # providers have registered their versions.
    bot.tree.remove_command("person")
    bot.tree.remove_command("reverse")

    person = app_commands.Group(name="person", description="Threaded multi-source public person intelligence")
    reverse = app_commands.Group(name="reverse", description="Threaded public identifier intelligence")

    @person.command(name="search", description="Open-web and public-profile person correlation in a thread")
    async def person_search(
        interaction: discord.Interaction,
        first_name: str,
        last_name: str,
        city: str = "",
        state: str = "",
        email: str = "",
        phone: str = "",
        age: int = 0,
        country: str = "United States",
    ):
        if not await _require_privileged(interaction, "person.search"):
            return
        await interaction.response.defer(thinking=True)
        try:
            thread = await _result_thread(interaction, "person-search")
            await thread.send(f"🔎 **Person search started by {interaction.user.mention}**")

            you_task = _you_search(first_name, last_name, city, state, email) if settings.you_api_key else None
            personpages_task = _personpages_lookup(first_name, last_name, age=age, city=city, country=country) if settings.personpages_api_key else None
            github_task = _github_people(first_name, last_name, city, state)
            gitlab_task = _gitlab_people(first_name, last_name)
            profile_task = _cross_platform_candidates(first_name, last_name, email)
            github, gitlab, cross_profiles = await asyncio.gather(github_task, gitlab_task, profile_task)
            rows = github + gitlab

            panel = _embed(
                "Person Intelligence",
                "Multi-source public OSINT completed. Treat all matches as correlation leads until independently verified.",
                kind="info",
            )
            panel.add_field(name="👤 Query", value=f"**{first_name.strip()} {last_name.strip()}**", inline=True)
            if city or state:
                panel.add_field(name="📍 Location Filter", value=" ".join(x for x in (city.strip(), state.strip()) if x), inline=True)

            if you_task is not None:
                try:
                    web_rows = await you_task
                    panel.add_field(name=f"🌍 Open-Web Results ({len(web_rows)})", value=_web_results_text(web_rows), inline=False)
                except Exception as exc:
                    panel.add_field(name="⚠️ You.com Web Search", value=str(exc)[:1024], inline=False)
            else:
                panel.add_field(name="🌍 Open-Web Results", value="You.com is not configured. Add `YOU_API_KEY` to enable open-web search.", inline=False)

            if personpages_task is not None:
                try:
                    panel.add_field(name="🧠 PersonPages (Optional)", value=_profile_summary(await personpages_task), inline=False)
                except Exception as exc:
                    panel.add_field(name="⚠️ PersonPages", value=str(exc)[:1024], inline=False)

            directory_text = "\n".join(
                f"• **{row['site']}** — {row['name']} (`{row['username']}`)"
                + (f" — {row['location']}" if row.get("location") else "")
                + (f" — {row['company']}" if row.get("company") else "")
                + f"\n  {row['url']}"
                for row in rows[:6]
            )
            panel.add_field(
                name=f"🔎 Name-Based Profile Candidates ({len(rows)})",
                value=directory_text[:1024] or "No GitHub/GitLab name matches were returned.",
                inline=False,
            )

            cross_text = "\n".join(
                f"• **{row['site']}** — `{row['username']}`\n  {row['url']}"
                for row in cross_profiles[:10]
            )
            panel.add_field(
                name=f"🌐 Username-Correlation Leads ({len(cross_profiles)})",
                value=cross_text[:1024] or "No derived username matches were confirmed.",
                inline=False,
            )

            if phone.strip():
                try:
                    panel.add_field(name="📞 USACallerLookup", value=_phone_summary(await _usa_caller_lookup(phone)), inline=False)
                except Exception as exc:
                    panel.add_field(name="⚠️ Phone Context", value=str(exc)[:1024], inline=False)

            if email.strip():
                try:
                    breaches = await _xposed_account(email)
                    panel.add_field(
                        name="🛡️ Email Breach Context",
                        value="\n".join(f"• {x}" for x in breaches[:20])[:1024] if breaches else "No XposedOrNot breach records returned.",
                        inline=False,
                    )
                    posture = await advanced_osint.email_posture(email)
                    panel.add_field(
                        name="📬 Email Domain Posture",
                        value=f"Domain: `{posture['domain']}`\nMX: **{len(posture['mx'])}**\nSPF: **{'Yes' if posture['spf'] else 'No'}**\nDMARC: **{'Yes' if posture['dmarc'] else 'No'}**",
                        inline=False,
                    )
                except Exception as exc:
                    panel.add_field(name="⚠️ Email Context", value=str(exc)[:1024], inline=False)

            await thread.send(embed=panel)
        except Exception as exc:
            await interaction.followup.send(embed=_embed("Person Search Failed", str(exc), kind="error"))

    @person.command(name="public", description="Public-source identifier correlation in a thread")
    async def person_public(interaction: discord.Interaction, query: str):
        if not await _require_privileged(interaction, "person.public"):
            return
        await interaction.response.defer(thinking=True)
        try:
            thread = await _result_thread(interaction, "person-public")
            data = await passive_service.person_search(query)
            import json
            await thread.send(embed=_embed("Public Person OSINT", f"```json\n{json.dumps(data, indent=2, ensure_ascii=False)[:3300]}\n```", kind="info"))
        except Exception as exc:
            await interaction.followup.send(embed=_embed("Public Person OSINT Failed", str(exc), kind="error"))

    @reverse.command(name="phone", description="US phone carrier/location/complaint intelligence in a thread")
    async def reverse_phone(interaction: discord.Interaction, phone: str):
        if not await _require_privileged(interaction, "person.phone"):
            return
        await interaction.response.defer(thinking=True)
        try:
            thread = await _result_thread(interaction, "reverse-phone")
            data = await _usa_caller_lookup(phone)
            panel = _embed("Reverse Phone Intelligence", "USACallerLookup public-data lookup completed.", kind="info")
            panel.add_field(name="📞 Phone Context", value=_phone_summary(data), inline=False)
            panel.add_field(name="ℹ️ Data Note", value="Carrier/location are numbering-plan assignments. Complaint records can involve spoofed caller ID.", inline=False)
            await thread.send(embed=panel)
        except Exception as exc:
            await interaction.followup.send(embed=_embed("Reverse Phone Failed", str(exc), kind="error"))

    @reverse.command(name="email", description="Email breach/domain intelligence in a thread")
    async def reverse_email(interaction: discord.Interaction, email: str):
        if not await _require_privileged(interaction, "person.email"):
            return
        await interaction.response.defer(thinking=True)
        try:
            thread = await _result_thread(interaction, "reverse-email")
            breaches = await _xposed_account(email)
            posture = await advanced_osint.email_posture(email)
            panel = _embed("Reverse Email Intelligence", "Free public-source email intelligence completed.", kind="info")
            panel.add_field(name="🛡️ Breach Exposure", value="\n".join(f"• {x}" for x in breaches[:20])[:1024] if breaches else "No XposedOrNot breach records returned.", inline=False)
            panel.add_field(name="📬 Domain", value=f"`{posture['domain']}`", inline=True)
            panel.add_field(name="MX", value=str(len(posture['mx'])), inline=True)
            panel.add_field(name="SPF / DMARC", value=f"{'✓' if posture['spf'] else '—'} / {'✓' if posture['dmarc'] else '—'}", inline=True)
            await thread.send(embed=panel)
        except Exception as exc:
            await interaction.followup.send(embed=_embed("Reverse Email Failed", str(exc), kind="error"))

    @reverse.command(name="username", description="Cross-platform username correlation in a thread")
    async def reverse_username(interaction: discord.Interaction, username: str):
        if not await _require_privileged(interaction, "person.username"):
            return
        await interaction.response.defer(thinking=True)
        try:
            thread = await _result_thread(interaction, "reverse-username")
            rows = await advanced_osint.username_profiles(username)
            found = [row for row in rows if row.get("present")]
            panel = _embed("Username Footprint", f"Public-source username correlation for `{username}`.", kind="info")
            panel.add_field(
                name=f"🔎 Confirmed Profiles ({len(found)})",
                value="\n".join(f"• **{row['site']}** — {row['url']}" for row in found)[:1024] or "No matching public profiles were confirmed.",
                inline=False,
            )
            await thread.send(embed=panel)
        except Exception as exc:
            await interaction.followup.send(embed=_embed("Username Footprint Failed", str(exc), kind="error"))

    bot.tree.add_command(person)
    bot.tree.add_command(reverse)

    intel = bot.tree.get_command("intel")
    if isinstance(intel, app_commands.Group):
        @intel.command(name="resolve", description="Resolve a domain to its public DNS/IP records")
        async def intel_resolve(interaction: discord.Interaction, domain: str):
            await interaction.response.defer(thinking=True)
            try:
                thread = await _result_thread(interaction, "domain-resolve")
                records = await passive_service.dns_records(domain)
                panel = _embed("Domain Resolution", f"Public DNS resolution for `{domain}`.", kind="info")
                for record_type in ("A", "AAAA", "MX", "NS", "TXT"):
                    values = records.get(record_type, [])
                    panel.add_field(name=record_type, value="\n".join(f"`{x}`" for x in values[:10]) or "None", inline=False)
                await thread.send(embed=panel)
            except Exception as exc:
                await interaction.followup.send(embed=_embed("Domain Resolution Failed", str(exc), kind="error"))

        @intel.command(name="geoip", description="GeoIP, ASN, organization, and timezone for a public IP/domain")
        async def intel_geoip(interaction: discord.Interaction, target: str):
            await interaction.response.defer(thinking=True)
            try:
                thread = await _result_thread(interaction, "geoip")
                data = await _geoip_lookup(target)
                panel = _embed("GeoIP Intelligence", f"Approximate network geolocation for `{target}`.", kind="info")
                panel.add_field(name="IP", value=f"`{data.get('ip', 'unknown')}`", inline=True)
                panel.add_field(name="ASN", value=str(data.get("asn") or "Unknown"), inline=True)
                panel.add_field(name="Organization", value=str(data.get("org") or "Unknown")[:1024], inline=False)
                panel.add_field(name="Location", value=", ".join(str(x) for x in (data.get("city"), data.get("region"), data.get("country_name")) if x) or "Unknown", inline=False)
                panel.add_field(name="Coordinates", value=f"{data.get('latitude', 'n/a')}, {data.get('longitude', 'n/a')}", inline=True)
                panel.add_field(name="Timezone", value=str(data.get("timezone") or "Unknown"), inline=True)
                panel.add_field(name="ℹ️ Accuracy", value="GeoIP is approximate network registration/routing intelligence, not a person's precise physical location.", inline=False)
                await thread.send(embed=panel)
            except Exception as exc:
                await interaction.followup.send(embed=_embed("GeoIP Lookup Failed", str(exc), kind="error"))

        @intel.command(name="reverse-dns", description="Resolve a public IP address back to a hostname")
        async def intel_reverse_dns(interaction: discord.Interaction, ip: str):
            await interaction.response.defer(thinking=True)
            try:
                thread = await _result_thread(interaction, "reverse-dns")
                data = await advanced_osint.reverse_dns(ip)
                panel = _embed("Reverse DNS", f"PTR/host correlation for `{ip}`.", kind="info")
                panel.add_field(name="Hostname", value=str(data.get("hostname") or "No PTR hostname returned"), inline=False)
                aliases = data.get("aliases") or []
                panel.add_field(name="Aliases", value="\n".join(f"`{x}`" for x in aliases[:10]) or "None", inline=False)
                await thread.send(embed=panel)
            except Exception as exc:
                await interaction.followup.send(embed=_embed("Reverse DNS Failed", str(exc), kind="error"))
