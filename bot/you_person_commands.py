import asyncio

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
from core.config import settings
from services.advanced_osint import advanced_osint
from services.passive_service import passive_service


def _embed(title: str, text: str = "", *, kind: str = "default") -> discord.Embed:
    return make_embed(title, text, kind=kind)


def _build_web_query(first_name: str, last_name: str, city: str = "", state: str = "", email: str = "") -> str:
    name = " ".join(x for x in (first_name.strip(), last_name.strip()) if x)
    parts = [f'"{name}"']
    location = " ".join(x for x in (city.strip(), state.strip()) if x)
    if location:
        parts.append(f'"{location}"')
    if email.strip():
        parts.append(f'"{email.strip()}"')
    parts.append("profile OR bio OR directory OR resume OR speaker OR author")
    return " ".join(parts)


async def _you_search(first_name: str, last_name: str, city: str = "", state: str = "", email: str = "") -> list[dict]:
    if not settings.you_api_key:
        return []
    payload = {"query": _build_web_query(first_name, last_name, city, state, email), "count": 10}
    headers = {"X-API-Key": settings.you_api_key, "Content-Type": "application/json", "User-Agent": settings.user_agent}
    timeout = httpx.Timeout(max(settings.http_timeout_seconds, 20))
    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        response = await client.post("https://api.you.com/v1/search", json=payload)
        if response.is_error:
            try:
                body = response.json()
                detail = body.get("detail") or body.get("message") or body.get("error") or body
            except Exception:
                detail = response.text.strip() or response.reason_phrase
            raise RuntimeError(f"You.com HTTP {response.status_code}: {str(detail)[:800]}")
        data = response.json()

    rows = []
    results = data.get("results") or {}
    for section in ("web", "news"):
        for item in results.get(section) or []:
            url = str(item.get("url") or "").strip()
            title = str(item.get("title") or "Untitled result").strip()
            snippets = item.get("snippets") or []
            if isinstance(snippets, str):
                snippets = [snippets]
            snippet = " ".join(str(x).strip() for x in snippets[:2] if x).strip()
            if url:
                rows.append({"section": section, "title": title, "url": url, "snippet": snippet})
            if len(rows) >= 10:
                return rows
    return rows


def _web_results_text(rows: list[dict]) -> str:
    if not rows:
        return "No You.com web results were returned."
    chunks = []
    for row in rows[:6]:
        snippet = row.get("snippet") or ""
        if len(snippet) > 180:
            snippet = snippet[:177] + "..."
        text = f"• **{row['title']}**\n  {row['url']}"
        if snippet:
            text += f"\n  {snippet}"
        chunks.append(text)
    return "\n".join(chunks)[:1024]


def register_you_person_commands(bot) -> None:
    bot.tree.remove_command("person")
    person = app_commands.Group(name="person", description="Multi-source public person intelligence")

    @person.command(name="search", description="You.com web OSINT plus public profile correlation")
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
            you_task = _you_search(first_name, last_name, city, state, email) if settings.you_api_key else None
            github_task = _github_people(first_name, last_name, city, state)
            gitlab_task = _gitlab_people(first_name, last_name)
            profile_task = _cross_platform_candidates(first_name, last_name, email)
            personpages_task = _personpages_lookup(first_name, last_name, age=age, city=city, country=country) if settings.personpages_api_key else None

            github, gitlab, cross_profiles = await asyncio.gather(github_task, gitlab_task, profile_task)
            rows = github + gitlab

            panel = _embed("Person Intelligence", "Multi-source public OSINT completed. Search results are correlation leads and must be independently verified.", kind="info")
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
                panel.add_field(name="🌍 Open-Web Results", value="You.com is not configured. Add `YOU_API_KEY` to enable open-web person OSINT.", inline=False)

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
            panel.add_field(name=f"🔎 Name-Based Profile Candidates ({len(rows)})", value=directory_text[:1024] or "No GitHub/GitLab name matches were returned.", inline=False)

            cross_text = "\n".join(f"• **{row['site']}** — `{row['username']}`\n  {row['url']}" for row in cross_profiles[:10])
            panel.add_field(name=f"🌐 Username-Correlation Leads ({len(cross_profiles)})", value=cross_text[:1024] or "No derived username matches were confirmed across the public profile checks.", inline=False)

            if phone.strip():
                try:
                    panel.add_field(name="📞 USACallerLookup", value=_phone_summary(await _usa_caller_lookup(phone)), inline=False)
                except Exception as exc:
                    panel.add_field(name="⚠️ Phone Context", value=str(exc)[:1024], inline=False)

            if email.strip():
                try:
                    breaches = await _xposed_account(email)
                    panel.add_field(name="🛡️ Email Breach Context", value="\n".join(f"• {x}" for x in breaches[:20])[:1024] if breaches else "No XposedOrNot breach records returned.", inline=False)
                    posture = await advanced_osint.email_posture(email)
                    panel.add_field(name="📬 Email Domain Posture", value=f"Domain: `{posture['domain']}`\nMX: **{len(posture['mx'])}**\nSPF: **{'Yes' if posture['spf'] else 'No'}**\nDMARC: **{'Yes' if posture['dmarc'] else 'No'}**", inline=False)
                except Exception as exc:
                    panel.add_field(name="⚠️ Email Context", value=str(exc)[:1024], inline=False)

            panel.add_field(name="🛡️ Verification Note", value="Only public/open-web sources are queried. Do not treat name, username, snippet, or profile matches as proof of identity without independent corroboration.", inline=False)
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
            import json
            await interaction.followup.send(embed=_embed("Public Person OSINT", f"```json\n{json.dumps(data, indent=2, ensure_ascii=False)[:3300]}\n```", kind="info"))
        except Exception as exc:
            await interaction.followup.send(embed=_embed("Public Person OSINT Failed", str(exc), kind="error"))

    bot.tree.add_command(person)
