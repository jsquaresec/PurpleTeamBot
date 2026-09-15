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


BITCOIN_CHANNEL = "🪙・bitcoin"
POLL_SECONDS = 600
MAX_POSTS_PER_PERSON_PER_CHECK = 2
STATE_PATH = Path("private/rss_bitcoin_state.json")

# Five Bitcoin voices. We consume their public X timelines through RSSHub so
# Purple Team does not need an X API key. Multiple public instances are tried
# in order because any single public RSSHub instance can occasionally fail.
PEOPLE = [
    ("Michael Saylor", "saylor"),
    ("Jack Dorsey", "jack"),
    ("Adam Back", "adam3us"),
    ("Jameson Lopp", "lopp"),
    ("Lyn Alden", "LynAldenContact"),
]

RSSHUB_BASES = [
    "https://rsshub.stsecurity.moe",
    "https://rsshub.yfi.moe",
    "https://rsshub.umzzz.com",
]

# Keep the channel focused on Bitcoin rather than every post these accounts make.
BITCOIN_TERMS = (
    "bitcoin",
    "#bitcoin",
    "$btc",
    " btc",
    "btc ",
    "satoshi",
    "sats",
    "lightning",
    "mempool",
    "hashrate",
    "hash rate",
    "proof of work",
    "proof-of-work",
    "mining",
    "miner",
    "self custody",
    "self-custody",
    "cold storage",
    "block reward",
    "halving",
    "utxo",
    "taproot",
    "bitcoin core",
)

_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")


def _clean(value: str | None, limit: int = 1000) -> str:
    text = html.unescape(_TAG_RE.sub(" ", value or ""))
    text = _SPACE_RE.sub(" ", text).strip()
    if not text:
        return "No text provided by the feed."
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _entry_key(handle: str, entry) -> str:
    raw = str(
        entry.get("id")
        or entry.get("guid")
        or entry.get("link")
        or f"{entry.get('title', '')}|{entry.get('published', '')}"
    )
    return hashlib.sha256(f"{handle}|{raw}".encode("utf-8", "ignore")).hexdigest()


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


def _post_text(entry) -> str:
    title = _clean(str(entry.get("title") or ""), 1200)
    summary = _clean(str(entry.get("summary") or entry.get("description") or ""), 1600)
    if summary == "No text provided by the feed.":
        return title
    if title and title.lower() not in summary.lower():
        return f"{title} {summary}".strip()
    return summary or title


def _is_bitcoin_post(entry) -> bool:
    text = f" {_post_text(entry).lower()} "
    return any(term in text for term in BITCOIN_TERMS)


def _signal_id(handle: str, entry) -> str:
    return f"BTC-{handle[:5].upper()}-{_entry_key(handle, entry)[:6].upper()}"


class BitcoinRSSState:
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


class BitcoinPeopleRSS:
    def __init__(self, bot: discord.Client) -> None:
        self.bot = bot
        self.state = BitcoinRSSState()
        self.task: asyncio.Task | None = None
        self._running_check = asyncio.Lock()
        self.last_check: datetime | None = None
        self.last_errors: dict[str, str] = {}
        self.last_instance: dict[str, str] = {}

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
            (c for c in channels if isinstance(c, discord.TextChannel) and c.name == BITCOIN_CHANNEL),
            None,
        )

    async def fetch_person(self, name: str, handle: str):
        headers = {"User-Agent": "CyberSpace-Bitcoin-RSS/1.0 PurpleTeamBot"}
        errors: list[str] = []

        async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers=headers) as client:
            for base in RSSHUB_BASES:
                url = f"{base}/twitter/user/{handle}/exclude_rts_replies"
                try:
                    response = await client.get(url)
                    response.raise_for_status()
                    parsed = feedparser.parse(response.content)
                    if getattr(parsed, "bozo", False) and not parsed.entries:
                        raise RuntimeError(str(getattr(parsed, "bozo_exception", "invalid RSS/Atom feed")))
                    if not parsed.entries:
                        raise RuntimeError("feed returned no entries")
                    self.last_instance[name] = base
                    return parsed
                except Exception as exc:
                    errors.append(f"{base}: {str(exc)[:120]}")

        raise RuntimeError(" | ".join(errors)[:500])

    def make_embed(self, name: str, handle: str, entry) -> discord.Embed:
        text = _post_text(entry)
        link = str(entry.get("link") or "").strip()
        published = _published_datetime(entry)
        signal_id = _signal_id(handle, entry)

        embed = discord.Embed(
            title=f"₿ BITCOIN SIGNAL // @{handle}",
            url=link or None,
            description=(
                "```ansi\n"
                "\u001b[1;33mCYBERSPACE // BITCOIN SIGNAL\u001b[0m\n"
                "\u001b[2;37mPUBLIC TIMELINE // BTC FILTER MATCH\u001b[0m\n"
                "```\n"
                f"> {_clean(text, 900)}"
            ),
            colour=discord.Colour.from_rgb(247, 147, 26),
            timestamp=published or datetime.now(timezone.utc),
        )
        embed.add_field(name="◢ SIGNAL SOURCE", value=f"`{name}`", inline=True)
        embed.add_field(name="◢ HANDLE", value=f"`@{handle}`", inline=True)
        embed.add_field(name="◢ PUBLISHED", value=_published_display(entry), inline=True)
        embed.add_field(name="◢ SIGNAL ID", value=f"`{signal_id}`", inline=True)
        embed.add_field(name="◢ CLASS", value="`PUBLIC // BITCOIN`", inline=True)
        embed.add_field(name="◢ ROUTE", value="`BITCOIN`", inline=True)
        if link:
            embed.add_field(name="OPEN SOURCE", value=f"**[VIEW ORIGINAL POST ↗]({link})**", inline=False)
        embed.set_author(name="CYBERSPACE // BITCOIN WATCH")
        embed.set_footer(text=f"PURPLE TEAM • BTC SIGNAL SERVICE • {signal_id}")
        return embed

    async def check(self) -> tuple[int, int]:
        async with self._running_check:
            channel = await self.find_channel()
            if channel is None:
                raise RuntimeError(f"Could not find {BITCOIN_CHANNEL}")

            total_new = 0
            source_successes = 0
            first_seed = not self.state.seeded
            self.last_errors = {}

            for name, handle in PEOPLE:
                try:
                    parsed = await self.fetch_person(name, handle)
                    source_successes += 1
                    entries = list(parsed.entries)
                    unseen_matches = []

                    for entry in entries:
                        key = _entry_key(handle, entry)
                        if key in self.state.seen:
                            continue
                        # Mark every observed item as seen so old non-Bitcoin posts
                        # do not keep getting reconsidered on every poll.
                        if not _is_bitcoin_post(entry):
                            self.state.seen.add(key)
                            continue
                        unseen_matches.append((key, entry))

                    if first_seed:
                        for key, _ in unseen_matches:
                            self.state.seen.add(key)
                        continue

                    unseen_matches = list(reversed(unseen_matches[:MAX_POSTS_PER_PERSON_PER_CHECK]))
                    for key, entry in unseen_matches:
                        try:
                            await channel.send(
                                embed=self.make_embed(name, handle, entry),
                                allowed_mentions=discord.AllowedMentions.none(),
                            )
                            self.state.seen.add(key)
                            total_new += 1
                        except discord.HTTPException as exc:
                            self.last_errors[name] = f"Discord post failed: {exc}"
                            break
                except Exception as exc:
                    self.last_errors[name] = str(exc)[:500]

            if first_seed:
                self.state.seeded = True
            self.state.save()
            self.last_check = datetime.now(timezone.utc)
            return total_new, source_successes

    async def push_latest(self) -> tuple[int, int]:
        """Post the newest Bitcoin-matching item from each person once."""
        async with self._running_check:
            channel = await self.find_channel()
            if channel is None:
                raise RuntimeError(f"Could not find {BITCOIN_CHANNEL}")

            posted = 0
            source_successes = 0
            self.last_errors = {}

            for name, handle in PEOPLE:
                try:
                    parsed = await self.fetch_person(name, handle)
                    source_successes += 1
                    match = next((entry for entry in parsed.entries if _is_bitcoin_post(entry)), None)
                    if match is None:
                        self.last_errors[name] = "No recent Bitcoin-matching post in feed window"
                        continue
                    await channel.send(
                        embed=self.make_embed(name, handle, match),
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                    self.state.seen.add(_entry_key(handle, match))
                    posted += 1
                except Exception as exc:
                    self.last_errors[name] = str(exc)[:500]

            self.state.seeded = True
            self.state.save()
            self.last_check = datetime.now(timezone.utc)
            return posted, source_successes

    async def loop(self) -> None:
        await self.bot.wait_until_ready()
        await asyncio.sleep(20)
        while not self.bot.is_closed():
            try:
                await self.check()
            except Exception as exc:
                self.last_errors["service"] = str(exc)[:500]
            await asyncio.sleep(POLL_SECONDS)

    def start(self) -> None:
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.loop(), name="cyberspace-bitcoin-rss")


def install_rss_bitcoin(bot: discord.Client) -> None:
    service = BitcoinPeopleRSS(bot)
    bot.bitcoin_rss = service  # type: ignore[attr-defined]

    async def on_ready() -> None:
        service.start()

    bot.add_listener(on_ready, "on_ready")


def register_bitcoin_rss_commands(bot: discord.Client) -> None:
    group = app_commands.Group(
        name="bitcoin-rss",
        description="CyberSpace Bitcoin signal feed controls",
        default_permissions=discord.Permissions(administrator=True),
    )

    async def owner_only(interaction: discord.Interaction) -> bool:
        if settings.bot_owner_id and interaction.user.id == settings.bot_owner_id:
            return True
        await interaction.response.send_message("Only the configured bot owner can manage Bitcoin feeds.", ephemeral=True)
        return False

    @group.command(name="status", description="Show Bitcoin RSS service status")
    @app_commands.guild_only()
    async def bitcoin_status(interaction: discord.Interaction):
        if not await owner_only(interaction):
            return
        service: BitcoinPeopleRSS = getattr(bot, "bitcoin_rss")
        check_text = discord.utils.format_dt(service.last_check, style="R") if service.last_check else "Not checked yet"
        errors = "\n".join(f"• **{name}:** {error}" for name, error in service.last_errors.items()) or "None"
        embed = discord.Embed(
            title="CYBERSPACE // BITCOIN RSS STATUS",
            description=f"Destination: `{BITCOIN_CHANNEL}`\nPolling: every **{POLL_SECONDS // 60} minutes**\nLast check: {check_text}",
            colour=discord.Colour.from_rgb(247, 147, 26),
        )
        embed.add_field(
            name="People",
            value="\n".join(f"• {name} (`@{handle}`)" for name, handle in PEOPLE),
            inline=False,
        )
        embed.add_field(name="Last errors", value=errors[:1024], inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @group.command(name="check-now", description="Poll all Bitcoin people feeds immediately")
    @app_commands.guild_only()
    async def bitcoin_check_now(interaction: discord.Interaction):
        if not await owner_only(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        service: BitcoinPeopleRSS = getattr(bot, "bitcoin_rss")
        try:
            posted, successes = await service.check()
            await interaction.followup.send(
                f"Bitcoin check complete: **{posted}** new post(s); **{successes}/{len(PEOPLE)}** people reached.",
                ephemeral=True,
            )
        except Exception as exc:
            await interaction.followup.send(f"Bitcoin check failed: `{str(exc)[:1000]}`", ephemeral=True)

    @group.command(name="latest", description="Push the newest Bitcoin-matching post from each person")
    @app_commands.guild_only()
    async def bitcoin_latest(interaction: discord.Interaction):
        if not await owner_only(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        service: BitcoinPeopleRSS = getattr(bot, "bitcoin_rss")
        try:
            posted, successes = await service.push_latest()
            await interaction.followup.send(
                f"Latest Bitcoin push complete: **{posted}** post(s) sent to `{BITCOIN_CHANNEL}`; **{successes}/{len(PEOPLE)}** people reached.",
                ephemeral=True,
            )
        except Exception as exc:
            await interaction.followup.send(f"Latest Bitcoin push failed: `{str(exc)[:1000]}`", ephemeral=True)

    @group.command(name="test", description="Post a Bitcoin RSS test card")
    @app_commands.guild_only()
    async def bitcoin_test(interaction: discord.Interaction):
        if not await owner_only(interaction):
            return
        service: BitcoinPeopleRSS = getattr(bot, "bitcoin_rss")
        channel = await service.find_channel()
        if channel is None:
            await interaction.response.send_message(f"Could not find `{BITCOIN_CHANNEL}`.", ephemeral=True)
            return
        embed = discord.Embed(
            title="₿ BITCOIN SIGNAL LINK ONLINE",
            description="Purple Team is ready to route Bitcoin-related public posts from the five monitored people into this channel.",
            colour=discord.Colour.from_rgb(247, 147, 26),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Status", value="ONLINE", inline=True)
        embed.add_field(name="People", value=str(len(PEOPLE)), inline=True)
        embed.add_field(name="Poll interval", value=f"{POLL_SECONDS // 60} minutes", inline=True)
        embed.set_footer(text="CYBERSPACE // BITCOIN WATCH")
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        await interaction.response.send_message(f"Bitcoin RSS test posted in {channel.mention}.", ephemeral=True)

    bot.tree.add_command(group)
