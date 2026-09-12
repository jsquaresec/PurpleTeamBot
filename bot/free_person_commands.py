import asyncio
import json
import re
from urllib.parse import quote

import discord
import httpx
from discord import app_commands

from bot.ui import always_status, local_status, make_embed, status_dot
from core.config import settings
from services.advanced_osint import advanced_osint
from services.passive_service import passive_service
from storage.db import database_status, record_audit


def _embed(title: str, text: str = "", *, kind: str = "default") -> discord.Embed:
    return make_embed(title, text, kind=kind)


def _guild_id(interaction: discord.Interaction) -> int:
    if interaction.guild_id is None:
        raise ValueError("This command must be used inside a server.")
    return interaction.guild_id


def _privileged(interaction: discord.Interaction) -> bool:
    if interaction.user.id == settings.bot_owner_id:
        return True
    return isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.manage_guild


async def _require_privileged(interaction: discord.Interaction, action: str) -> bool:
    if not _privileged(interaction):
        await interaction.response.send_message(
            embed=_embed("Access Denied", "You need **Manage Server** permission for person intelligence.", kind="error"),
            ephemeral=True,
        )
        return False
    await record_audit(_guild_id(interaction), interaction.user.id, action, "redacted")
    return True


async def _usa_caller_lookup(phone: str) -> dict:
    phone = phone.strip()
    if not phone:
        raise ValueError("A phone number is required")
    url = f"https://www.usacallerlookup.com/wp-json/ucl/v1/number/{quote(phone, safe='')}"
    timeout = httpx.Timeout(settings.http_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent": settings.user_agent}) as client:
        response = await client.get(url, follow_redirects=True)
        if response.status_code == 400:
            raise ValueError("USACallerLookup requires a valid 10-digit US phone number")
        response.raise_for_status()
        return response.json()


async def _xposed_account(email: str) -> list[str]:
    email = email.strip()
    if not email or "@" not in email:
        raise ValueError("A valid email address is required")
    timeout = httpx.Timeout(settings.http_timeout_seconds)
    url = f"https://api.xposedornot.com/v1/check-email/{quote(email, safe='')}"
    async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent": settings.user_agent}, follow_redirects=True) as client:
        response = await client.get(url, params={"details": "false"})
        if response.status_code == 404:
            return []
        response.raise_for_status()
        data = response.json()
        if data.get("Error"):
            return []
        breaches = data.get("breaches", [])
        if breaches and isinstance(breaches[0], list):
            breaches = breaches[0]
        return [str(item) for item in breaches if item]


async def _github_people(first_name: str, last_name: str, city: str = "", state: str = "") -> list[dict]:
    full_name = " ".join(x for x in (first_name.strip(), last_name.strip()) if x)
    terms = [f'"{full_name}" in:fullname']
    location = " ".join(x for x in (city.strip(), state.strip()) if x)
    if location:
        terms.append(f'location:"{location}"')
    timeout = httpx.Timeout(settings.http_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent": settings.user_agent}) as client:
        response = await client.get("https://api.github.com/search/users", params={"q": " ".join(terms), "per_page": 5})
        if response.status_code in (403, 429):
            return []
        response.raise_for_status()
        results = []
        for item in response.json().get("items", [])[:5]:
            login = str(item.get("login") or "")
            if not login:
                continue
            profile = await client.get(f"https://api.github.com/users/{quote(login, safe='')}")
            if profile.status_code != 200:
                continue
            data = profile.json()
            results.append({
                "site": "GitHub",
                "name": data.get("name") or login,
                "username": login,
                "location": data.get("location") or "",
                "company": data.get("company") or "",
                "url": data.get("html_url") or item.get("html_url") or "",
            })
        return results


async def _gitlab_people(first_name: str, last_name: str) -> list[dict]:
    full_name = " ".join(x for x in (first_name.strip(), last_name.strip()) if x)
    timeout = httpx.Timeout(settings.http_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent": settings.user_agent}) as client:
        response = await client.get("https://gitlab.com/api/v4/users", params={"search": full_name, "per_page": 5})
        if response.status_code in (403, 429):
            return []
        response.raise_for_status()
        return [
            {
                "site": "GitLab",
                "name": item.get("name") or item.get("username") or "",
                "username": item.get("username") or "",
                "location": "",
                "company": "",
                "url": item.get("web_url") or "",
            }
            for item in response.json()[:5]
        ]


def _username_variants(first_name: str, last_name: str, email: str = "") -> list[str]:
    first = re.sub(r"[^a-z0-9]", "", first_name.lower())
    last = re.sub(r"[^a-z0-9]", "", last_name.lower())
    variants = []
    for value in (
        f"{first}{last}",
        f"{first}.{last}",
        f"{first}_{last}",
        f"{first}-{last}",
        f"{first[:1]}{last}" if first else "",
        f"{first}{last[:1]}" if last else "",
        f"{last}{first}",
    ):
        if value and 2 <= len(value) <= 64 and value not in variants:
            variants.append(value)
    if email and "@" in email:
        local = re.sub(r"[^a-z0-9._-]", "", email.split("@", 1)[0].lower())
        if local and local not in variants:
            variants.insert(0, local)
    return variants[:6]


async def _cross_platform_candidates(first_name: str, last_name: str, email: str = "") -> list[dict]:
    variants = _username_variants(first_name, last_name, email)
    if not variants:
        return []

    async def check(username: str) -> list[dict]:
        rows = await advanced_osint.username_profiles(username)
        return [
            {"site": row["site"], "username": username, "url": row["url"]}
            for row in rows
            if row.get("present")
        ]

    batches = await asyncio.gather(*(check(username) for username in variants), return_exceptions=True)
    found: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for batch in batches:
        if isinstance(batch, Exception):
            continue
        for row in batch:
            key = (row["site"], row["username"])
            if key not in seen:
                seen.add(key)
                found.append(row)
    return found[:15]


def _phone_summary(data: dict) -> str:
    lines = []
    for key, label in (
        ("phone", "Number"),
        ("carrier", "Carrier"),
        ("line_type", "Line Type"),
        ("city", "Assigned City"),
        ("state", "Assigned State"),
        ("toll_free", "Toll Free"),
        ("total_complaints", "FTC Complaints"),
        ("robocall_complaints", "Robocall Reports"),
        ("first_reported", "First Reported"),
        ("last_reported", "Last Reported"),
    ):
        value = data.get(key)
        if value not in (None, "", []):
            lines.append(f"**{label}:** {value}")
    if not lines:
        return f"```json\n{json.dumps(data, indent=2, ensure_ascii=False)[:850]}\n```"
    return "\n".join(lines)[:1024]


def register_free_person_commands(bot) -> None:
    person = app_commands.Group(name="person", description="Permission-gated public-source person intelligence")
    reverse = app_commands.Group(name="reverse", description="Permission-gated public identifier intelligence")

    @person.command(name="search", description="Multi-source public name, email, phone, and profile correlation")
    async def person_search(
        interaction: discord.Interaction,
        first_name: str,
        last_name: str,
        city: str = "",
        state: str = "",
        email: str = "",
        phone: str = "",
    ):
        if not await _require_privileged(interaction, "person.search"):
            return
        await interaction.response.defer(thinking=True)
        try:
            github_task = _github_people(first_name, last_name, city, state)
            gitlab_task = _gitlab_people(first_name, last_name)
            profile_task = _cross_platform_candidates(first_name, last_name, email)
            github, gitlab, cross_profiles = await asyncio.gather(github_task, gitlab_task, profile_task)
            rows = github + gitlab

            panel = _embed(
                "Person Intelligence",
                "Multi-source public correlation completed. Results are leads for verification, not proof of identity.",
                kind="info",
            )
            panel.add_field(name="👤 Query", value=f"**{first_name.strip()} {last_name.strip()}**", inline=True)
            if city or state:
                panel.add_field(name="📍 Location Filter", value=" ".join(x for x in (city.strip(), state.strip()) if x), inline=True)

            directory_text = "\n".join(
                f"• **{row['site']}** — {row['name']} (`{row['username']}`)"
                + (f" — {row['location']}" if row.get("location") else "")
                + (f" — {row['company']}" if row.get("company") else "")
                + f"\n  {row['url']}"
                for row in rows[:8]
            )
            panel.add_field(
                name=f"🔎 Name-Based Candidates ({len(rows)})",
                value=directory_text[:1024] or "No GitHub/GitLab name matches were returned.",
                inline=False,
            )

            cross_text = "\n".join(
                f"• **{row['site']}** — `{row['username']}`\n  {row['url']}"
                for row in cross_profiles[:12]
            )
            panel.add_field(
                name=f"🌐 Cross-Platform Profile Candidates ({len(cross_profiles)})",
                value=cross_text[:1024] or "No derived username matches were confirmed across GitHub, GitLab, Reddit, Keybase, or HackerOne.",
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

            panel.add_field(name="🛡️ Verification Note", value="Name and derived-username matches can belong to unrelated people. Verify using location, employer, bio, linked sites, and other independent context before treating a match as relevant.", inline=False)
            await interaction.followup.send(embed=panel)
        except Exception as exc:
            await interaction.followup.send(embed=_embed("Person Search Failed", str(exc), kind="error"))

    @person.command(name="public", description="Lightweight public-source identifier correlation")
    async def person_public(interaction: discord.Interaction, query: str):
        if not await _require_privileged(interaction, "person.public"):
            return
        await interaction.response.defer(thinking=True)
        try:
            data = await passive_service.person_search(query)
            await interaction.followup.send(embed=_embed("Public Person OSINT", f"```json\n{json.dumps(data, indent=2, ensure_ascii=False)[:3300]}\n```", kind="info"))
        except Exception as exc:
            await interaction.followup.send(embed=_embed("Public Person OSINT Failed", str(exc), kind="error"))

    @reverse.command(name="phone", description="Free US phone carrier/location/complaint intelligence")
    async def reverse_phone(interaction: discord.Interaction, phone: str):
        if not await _require_privileged(interaction, "person.phone"):
            return
        await interaction.response.defer(thinking=True)
        try:
            data = await _usa_caller_lookup(phone)
            panel = _embed("Reverse Phone Intelligence", "USACallerLookup public-data lookup completed.", kind="info")
            panel.add_field(name="📞 Phone Context", value=_phone_summary(data), inline=False)
            panel.add_field(name="ℹ️ Data Note", value="Carrier/location are numbering-plan assignments. Complaint records can involve spoofed caller ID and are not proof of wrongdoing.", inline=False)
            await interaction.followup.send(embed=panel)
        except Exception as exc:
            await interaction.followup.send(embed=_embed("Reverse Phone Failed", str(exc), kind="error"))

    @reverse.command(name="email", description="Free breach and email-domain intelligence")
    async def reverse_email(interaction: discord.Interaction, email: str):
        if not await _require_privileged(interaction, "person.email"):
            return
        await interaction.response.defer(thinking=True)
        try:
            breaches = await _xposed_account(email)
            posture = await advanced_osint.email_posture(email)
            panel = _embed("Reverse Email Intelligence", "Free public-source email intelligence completed.", kind="info")
            panel.add_field(name="🛡️ Breach Exposure", value="\n".join(f"• {x}" for x in breaches[:20])[:1024] if breaches else "No XposedOrNot breach records returned.", inline=False)
            panel.add_field(name="📬 Domain", value=f"`{posture['domain']}`", inline=True)
            panel.add_field(name="MX", value=str(len(posture['mx'])), inline=True)
            panel.add_field(name="SPF / DMARC", value=f"{'✓' if posture['spf'] else '—'} / {'✓' if posture['dmarc'] else '—'}", inline=True)
            await interaction.followup.send(embed=panel)
        except Exception as exc:
            await interaction.followup.send(embed=_embed("Reverse Email Failed", str(exc), kind="error"))

    @reverse.command(name="username", description="Correlate a username across public platforms")
    async def reverse_username(interaction: discord.Interaction, username: str):
        if not await _require_privileged(interaction, "person.username"):
            return
        await interaction.response.defer(thinking=True)
        try:
            rows = await advanced_osint.username_profiles(username)
            found = [row for row in rows if row.get("present")]
            panel = _embed("Username Footprint", f"Public-source username correlation for `{username}`.", kind="info")
            panel.add_field(name=f"🔎 Confirmed Profiles ({len(found)})", value="\n".join(f"• **{row['site']}** — {row['url']}" for row in found)[:1024] or "No matching public profiles were confirmed.", inline=False)
            await interaction.followup.send(embed=panel)
        except Exception as exc:
            await interaction.followup.send(embed=_embed("Username Footprint Failed", str(exc), kind="error"))

    @bot.tree.command(name="status", description="Show Purple Team runtime and free-first intelligence stack")
    async def status(interaction: discord.Interaction):
        db = await database_status()
        panel = _embed(
            "Operations Status",
            "**Security operations platform is online and ready.**\nFree-first intelligence, assessment capacity, and storage are shown below.",
            kind="success",
        )
        panel.add_field(name="🗄️ Storage", value=f"Backend  `{db['backend']}`\n{db['detail'][:80]}", inline=True)
        panel.add_field(name="🎯 Assessment", value=f"Concurrent scans  `{settings.max_active_scans}`\nScope enforcement  `Enabled`", inline=True)
        panel.add_field(
            name="🌐 Optional Keyed Providers",
            value="\n".join([
                status_dot(bool(settings.virustotal_api_key), label="**VirusTotal**"),
                status_dot(bool(settings.abuseipdb_api_key), label="**AbuseIPDB**"),
                status_dot(bool(settings.censys_pat), label="**Censys**"),
                status_dot(bool(settings.urlscan_api_key), label="**urlscan.io**"),
                status_dot(bool(settings.otx_api_key), label="**AlienVault OTX**"),
            ]),
            inline=False,
        )
        panel.add_field(
            name="🧠 Free / Built-in Intelligence",
            value="\n".join([
                always_status("**USACallerLookup**"),
                always_status("**XposedOrNot**"),
                always_status("**GitHub / GitLab Name Search**"),
                always_status("**GitHub / GitLab / Reddit / Keybase / HackerOne Profile Correlation**"),
                always_status("**HIBP Pwned Passwords**"),
                always_status("**FIRST EPSS**"),
                always_status("**CISA KEV**"),
                always_status("**crt.sh / DNS / RDAP**"),
                local_status("**Nmap Engine**"),
            ]),
            inline=False,
        )
        panel.add_field(name="🛡️ Security Mode", value="Active scanning requires explicit server scope authorization. Person intelligence is permission-gated and audited.", inline=False)
        await interaction.response.send_message(embed=panel)

    bot.tree.add_command(person)
    bot.tree.add_command(reverse)
