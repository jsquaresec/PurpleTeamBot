from __future__ import annotations

import asyncio
import calendar
import hashlib
import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import discord
from discord import app_commands
import feedparser
import httpx

from core.config import settings


CYBER_NEWS_CHANNEL = "📰・cyber-news"
POLL_SECONDS = 600
MAX_POSTS_PER_FEED_PER_CHECK = 3
STATE_PATH = Path("private/rss_cyber_news_state.json")

FEEDS = [
    ("BleepingComputer", "https://www.bleepingcomputer.com/feed/"),
    ("The Hacker News", "https://thehackernews.com/feeds/posts/default"),
    ("CISA Advisories", "https://www.cisa.gov/cybersecurity-advisories/all.xml"),
    ("Krebs on Security", "https://krebsonsecurity.com/feed/"),
    ("Dark Reading", "https://www.darkreading.com/rss.xml"),
]

SOURCE_CODES = {
    "BleepingComputer": "BC",
    "The Hacker News": "THN",
    "CISA Advisories": "CISA",
    "Krebs on Security": "KREBS",
    "Dark Reading": "DR",
}

_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")


def _clean(value: str | None, limit: int = 800) -> str:
    text = html.unescape(_TAG_RE.sub(" ", value or ""))
    text = _SPACE_RE.sub(" ", text).strip()
    if not text:
        return "No summary provided by the feed."
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _entry_key(source: str, entry) -> str:
    raw = str(
        entry.get("id")
        or entry.get("guid")
        or entry.get("link")
        or f"{entry.get('title', '')}|{entry.get('published', '')}"
    )
    return hashlib.sha256(f"{source}|{raw}".encode("utf-8", "ignore")).hexdigest()


def _published_datetime(entry) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    try:
        return datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _published_display(entry) -> str:
    published = _published_datetime(entry)
    if published is not None:
        return discord.utils.format_dt(published, style="R")
    return _clean(str(entry.get("published") or entry.get("updated") or "Unknown"), 90)


def _intel_id(source: str, entry) -> str:
    code = SOURCE_CODES.get(source, "RSS")
    return f"{code}-{_entry_key(source, entry)[:6].upper()}"


class RSSState:
    def __init__(self) -> None:
        self.seen: set[str] = set()
        self.seeded = False
        self.load()

    def load(self) -> None:
        try:
            raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            self.seen = {str(value) for value in raw.get("seen", [])}
            self.seeded = bool(raw.get("seeded", False))
        except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError):
            pass

    def save(self) -> None:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        values = list(self.seen)[-5000:]
        STATE_PATH.write_text(
            json.dumps({"seeded": self.seeded, "seen": values}, indent=2),
            encoding="utf-8",
        )


class CyberNewsRSS:
    def __init__(self, bot: discord.Client) -> None:
        self.bot = bot
        self.state = RSSState()
        self.task: asyncio.Task | None = None
        self._running_check = asyncio.Lock()
        self.last_check: datetime | None = None
        self.last_errors: dict[str, str] = {}

    async def find_channel(self) -> discord.TextChannel | None:
        guild_id = settings.discord_guild_id
        if not guild_id:
            return None
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            try:
                guild = await self.bot.fetch_guild(guild_id)
            except discord.HTTPException:
                return None
        try:
            channels = await guild.fetch_channels()
        except discord.HTTPException:
            return None
        return next(
            (c for c in channels if isinstance(c, discord.TextChannel) and c.name == CYBER_NEWS_CHANNEL),
            None,
        )

    async def fetch_feed(self, name: str, url: str):
        headers = {"User-Agent": "CyberSpace-RSS/1.0 PurpleTeamBot"}
        async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers=headers) as client:
            response = await client.get(url)
            response.raise_for_status()
        parsed = feedparser.parse(response.content)
        if getattr(parsed, "bozo", False) and not parsed.entries:
            raise RuntimeError(str(getattr(parsed, "bozo_exception", "invalid RSS/Atom feed")))
        return parsed

    def make_embed(self, source: str, entry) -> discord.Embed:
        title = _clean(str(entry.get("title") or "Untitled cyber news item"), 220)
        link = str(entry.get("link") or "").strip()
        summary = _clean(str(entry.get("summary") or entry.get("description") or ""), 720)
        published = _published_datetime(entry)
        intel_id = _intel_id(source, entry)

        embed = discord.Embed(
            title=f"◈ {title}",
            url=link or None,
            description=(
                "```ansi\n"
                "\u001b[1;35mCYBERSPACE // INCOMING INTEL\u001b[0m\n"
                "\u001b[2;36mPUBLIC FEED INTERCEPTED // SIGNAL VERIFIED\u001b[0m\n"
                "```\n"
                f"> {summary}"
            ),
            colour=discord.Colour.from_rgb(139, 92, 246),
            timestamp=published or datetime.now(timezone.utc),
        )

        embed.add_field(
            name="◢ SOURCE NODE",
            value=f"`{source}`",
            inline=True,
        )
        embed.add_field(
            name="◢ INTEL CLASS",
            value="`PUBLIC // RSS`",
            inline=True,
        )
        embed.add_field(
            name="◢ PUBLISHED",
            value=_published_display(entry),
            inline=True,
        )
        embed.add_field(
            name="◢ INTEL ID",
            value=f"`{intel_id}`",
            inline=True,
        )
        embed.add_field(
            name="◢ STATUS",
            value="`NEW // VERIFIED`",
            inline=True,
        )
        embed.add_field(
            name="◢ ROUTE",
            value="`CYBER NEWS`",
            inline=True,
        )

        if link:
            embed.add_field(
                name="ACCESS NODE",
                value=f"**[OPEN SOURCE INTELLIGENCE ↗]({link})**",
                inline=False,
            )

        embed.set_author(name="CYBERSPACE // NEWSWIRE INTELLIGENCE")
        embed.set_footer(text=f"DEMON SCOPE • RSS INTEL SERVICE • {intel_id}")
        return embed

    async def check(self) -> tuple[int, int]:
        async with self._running_check:
            channel = await self.find_channel()
            if channel is None:
                raise RuntimeError(f"Could not find {CYBER_NEWS_CHANNEL}")

            total_new = 0
            feed_successes = 0
            first_seed = not self.state.seeded
            self.last_errors = {}

            for source, url in FEEDS:
                try:
                    parsed = await self.fetch_feed(source, url)
                    feed_successes += 1
                    entries = list(parsed.entries)
                    unseen = []
                    for entry in entries:
                        key = _entry_key(source, entry)
                        if key not in self.state.seen:
                            unseen.append((key, entry))

                    if first_seed:
                        for key, _ in unseen:
                            self.state.seen.add(key)
                        continue

                    unseen = list(reversed(unseen[:MAX_POSTS_PER_FEED_PER_CHECK]))
                    for key, entry in unseen:
                        try:
                            await channel.send(
                                embed=self.make_embed(source, entry),
                                allowed_mentions=discord.AllowedMentions.none(),
                            )
                            self.state.seen.add(key)
                            total_new += 1
                        except discord.HTTPException as exc:
                            self.last_errors[source] = f"Discord post failed: {exc}"
                            break
                except Exception as exc:
                    self.last_errors[source] = str(exc)[:300]

            if first_seed:
                self.state.seeded = True
            self.state.save()
            self.last_check = datetime.now(timezone.utc)
            return total_new, feed_successes

    async def push_latest(self) -> tuple[int, int]:
        """One-time validation push: newest real item from each configured feed.

        This intentionally ignores deduplication for the selected article so an
        owner can prove the full RSS -> Discord path without resetting state.
        Normal polling remains deduplicated afterward.
        """
        async with self._running_check:
            channel = await self.find_channel()
            if channel is None:
                raise RuntimeError(f"Could not find {CYBER_NEWS_CHANNEL}")

            posted = 0
            feed_successes = 0
            self.last_errors = {}

            for source, url in FEEDS:
                try:
                    parsed = await self.fetch_feed(source, url)
                    feed_successes += 1
                    entries = list(parsed.entries)
                    if not entries:
                        self.last_errors[source] = "Feed returned no entries"
                        continue

                    entry = entries[0]
                    await channel.send(
                        embed=self.make_embed(source, entry),
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                    self.state.seen.add(_entry_key(source, entry))
                    posted += 1
                except Exception as exc:
                    self.last_errors[source] = str(exc)[:300]

            self.state.seeded = True
            self.state.save()
            self.last_check = datetime.now(timezone.utc)
            return posted, feed_successes

    async def loop(self) -> None:
        await self.bot.wait_until_ready()
        await asyncio.sleep(10)
        while not self.bot.is_closed():
            try:
                await self.check()
            except Exception as exc:
                self.last_errors["service"] = str(exc)[:300]
            await asyncio.sleep(POLL_SECONDS)

    def start(self) -> None:
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.loop(), name="cyberspace-rss-news")


def install_rss_cyber_news(bot: discord.Client) -> None:
    service = CyberNewsRSS(bot)
    bot.cyber_news_rss = service  # type: ignore[attr-defined]

    async def on_ready() -> None:
        service.start()

    bot.add_listener(on_ready, "on_ready")


def register_rss_commands(bot: discord.Client) -> None:
    group = app_commands.Group(
        name="rss",
        description="CyberSpace public RSS feed controls",
        default_permissions=discord.Permissions(administrator=True),
    )

    async def owner_only(interaction: discord.Interaction) -> bool:
        if settings.bot_owner_id and interaction.user.id == settings.bot_owner_id:
            return True
        await interaction.response.send_message("Only the configured bot owner can manage RSS feeds.", ephemeral=True)
        return False

    @group.command(name="status", description="Show CyberSpace RSS service status")
    @app_commands.guild_only()
    async def rss_status(interaction: discord.Interaction):
        if not await owner_only(interaction):
            return
        service: CyberNewsRSS = getattr(bot, "cyber_news_rss")
        check_text = discord.utils.format_dt(service.last_check, style="R") if service.last_check else "Not checked yet"
        errors = "\n".join(f"• **{name}:** {error}" for name, error in service.last_errors.items()) or "None"
        embed = discord.Embed(
            title="CYBERSPACE // RSS STATUS",
            description=f"Destination: `{CYBER_NEWS_CHANNEL}`\nPolling: every **{POLL_SECONDS // 60} minutes**\nLast check: {check_text}",
            colour=discord.Colour.from_rgb(139, 92, 246),
        )
        embed.add_field(name="Feeds", value="\n".join(f"• {name}" for name, _ in FEEDS), inline=False)
        embed.add_field(name="Last errors", value=errors[:1024], inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @group.command(name="check-now", description="Poll all configured RSS feeds immediately")
    @app_commands.guild_only()
    async def rss_check_now(interaction: discord.Interaction):
        if not await owner_only(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        service: CyberNewsRSS = getattr(bot, "cyber_news_rss")
        try:
            posted, successes = await service.check()
            await interaction.followup.send(
                f"RSS check complete: **{posted}** new article(s) posted; **{successes}/{len(FEEDS)}** feeds reached.",
                ephemeral=True,
            )
        except Exception as exc:
            await interaction.followup.send(f"RSS check failed: `{str(exc)[:1000]}`", ephemeral=True)

    @group.command(name="latest", description="One-time push of the newest article from every RSS feed")
    @app_commands.guild_only()
    async def rss_latest(interaction: discord.Interaction):
        if not await owner_only(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        service: CyberNewsRSS = getattr(bot, "cyber_news_rss")
        try:
            posted, successes = await service.push_latest()
            await interaction.followup.send(
                f"Latest RSS push complete: **{posted}** article(s) posted to `{CYBER_NEWS_CHANNEL}`; **{successes}/{len(FEEDS)}** feeds reached.",
                ephemeral=True,
            )
        except Exception as exc:
            await interaction.followup.send(f"Latest RSS push failed: `{str(exc)[:1000]}`", ephemeral=True)

    @group.command(name="test", description="Post a CyberSpace RSS test card in cyber-news")
    @app_commands.guild_only()
    async def rss_test(interaction: discord.Interaction):
        if not await owner_only(interaction):
            return
        service: CyberNewsRSS = getattr(bot, "cyber_news_rss")
        channel = await service.find_channel()
        if channel is None:
            await interaction.response.send_message(f"Could not find `{CYBER_NEWS_CHANNEL}`.", ephemeral=True)
            return
        embed = discord.Embed(
            title="RSS INTELLIGENCE LINK ONLINE",
            description="Purple Team can post public RSS/Atom intelligence into this channel.",
            colour=discord.Colour.from_rgb(139, 92, 246),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Status", value="ONLINE", inline=True)
        embed.add_field(name="Feeds", value=str(len(FEEDS)), inline=True)
        embed.add_field(name="Poll interval", value=f"{POLL_SECONDS // 60} minutes", inline=True)
        embed.set_footer(text="CYBERSPACE // CYBER NEWS")
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        await interaction.response.send_message(f"RSS test posted in {channel.mention}.", ephemeral=True)

    bot.tree.add_command(group)
