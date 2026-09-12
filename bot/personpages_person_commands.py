import asyncio
import json

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
from bot.ui import make_embed
from core.config import settings
from services.advanced_osint import advanced_osint
from services.passive_service import passive_service


def _embed(title: str, text: str = "", *, kind: str = "default") -> discord.Embed:
    return make_embed(title, text, kind=kind)


async def _personpages_lookup(
    first_name: str,
    last_name: str,
    *,
    age: int = 0,
    city: str = "",
    country: str = "",
) -> dict:
    if not settings.personpages_api_key:
        raise RuntimeError("PersonPages is not configured")

    name = " ".join(x for x in (first_name.strip(), last_name.strip()) if x)
    payload: dict[str, object] = {"name": name}
    if age:
        if age < 13 or age > 120:
            raise ValueError("PersonPages age must be between 13 and 120")
        payload["age"] = age
    if city.strip():
        payload["city"] = city.strip()
    if country.strip():
        payload["country"] = country.strip()

    headers = {
        "Authorization": f"Bearer {settings.personpages_api_key}",
        "Content-Type": "application/json",
        "User-Agent": settings.user_agent,
    }
    timeout = httpx.Timeout(max(settings.http_timeout_seconds, 60))
    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        response = await client.post("https://personpages.com/api/public/v1/lookup", json=payload)
        if response.is_error:
            try:
                body = response.json()
                detail = body.get("message") or body.get("error") or body
            except Exception:
                detail = response.text.strip() or response.reason_phrase
            raise RuntimeError(f"PersonPages HTTP {response.status_code}: {str(detail)[:800]}")
        return response.json()


def _profile_summary(data: dict) -> str:
    profile = data.get("profile") or {}
    if not isinstance(profile, dict):
        return "PersonPages returned a profile, but its structure was unexpected."

    lines = []
    for key, label in (
        ("full_name", "Name"),
        ("age", "Age"),
        ("city", "City"),
        ("country", "Country"),
        ("employer", "Employer"),
        ("job_title", "Title"),
        ("occupation", "Occupation"),
        ("education", "Education"),
        ("confidence", "Confidence"),
    ):
        value = profile.get(key)
        if value not in (None, "", [], {}):
            if isinstance(value, (list, dict)):
                value = json.dumps(value, ensure_ascii=False)[:180]
            lines.append(f"**{label}:** {value}")

    profile_url = data.get("profile_url") or data.get("url")
    if profile_url:
        lines.append(f"**Profile:** {profile_url}")

    quota = data.get("quota") or {}
    if isinstance(quota, dict) and quota.get("remaining") is not None:
        lines.append(f"**Free lookups remaining:** {quota.get('remaining')}")

    return "\n".join(lines)[:1024] or "Profile returned with no compact summary fields."


def register_personpages_person_commands(bot) -> None:
    # Replace the lighter /person group registered by free_person_commands while
    # leaving /reverse and the rest of the free stack untouched.
    bot.tree.remove_command("person")
    person = app_commands.Group(name="person", description="Multi-source public person intelligence")

    @person.command(name="search", description="PersonPages plus multi-source public profile correlation")
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
            personpages_task = _personpages_lookup(
                first_name,
                last_name,
                age=age,
                city=city,
                country=country,
            ) if settings.personpages_api_key else None
            github_task = _github_people(first_name, last_name, city, state)
            gitlab_task = _gitlab_people(first_name, last_name)
            profile_task = _cross_platform_candidates(first_name, last_name, email)

            base_tasks = [github_task, gitlab_task, profile_task]
            github, gitlab, cross_profiles = await asyncio.gather(*base_tasks)
            rows = github + gitlab

            panel = _embed(
                "Person Intelligence",
                "Multi-source public correlation completed. PersonPages fields are AI-estimated from public signals and must be independently verified.",
                kind="info",
            )
            panel.add_field(name="👤 Query", value=f"**{first_name.strip()} {last_name.strip()}**", inline=True)
            if city or state:
                panel.add_field(name="📍 Location Filter", value=" ".join(x for x in (city.strip(), state.strip()) if x), inline=True)
            if age:
                panel.add_field(name="🎂 Age Filter", value=str(age), inline=True)

            if personpages_task is not None:
                try:
                    pp_data = await personpages_task
                    panel.add_field(name="🧠 PersonPages", value=_profile_summary(pp_data), inline=False)
                except Exception as exc:
                    panel.add_field(name="⚠️ PersonPages", value=str(exc)[:1024], inline=False)
            else:
                panel.add_field(name="🧠 PersonPages", value="Not configured. Add `PERSONPAGES_API_KEY` to enable the primary structured person lookup.", inline=False)

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

            panel.add_field(
                name="🛡️ Verification Note",
                value="PersonPages explicitly labels its profile data as AI-estimated, and name/username matches can belong to unrelated people. Corroborate important findings with independent sources.",
                inline=False,
            )
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
            await interaction.followup.send(
                embed=_embed(
                    "Public Person OSINT",
                    f"```json\n{json.dumps(data, indent=2, ensure_ascii=False)[:3300]}\n```",
                    kind="info",
                )
            )
        except Exception as exc:
            await interaction.followup.send(embed=_embed("Public Person OSINT Failed", str(exc), kind="error"))

    bot.tree.add_command(person)
