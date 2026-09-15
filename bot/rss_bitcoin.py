from __future__ import annotations

import asyncio
import hashlib
import html
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import discord
from discord import app_commands
import httpx

from core.config import settings


BITCOIN_CHANNEL = "🪙・bitcoin"
POLL_SECONDS = 600
MAX_POSTS_PER_PERSON_PER_CHECK = 2
MAX_POST_AGE_DAYS = 8
REQUEST_TIMEOUT_SECONDS = 12
SOURCE_VERSION = "x-syndication-v1"
STATE_PATH = Path("private/rss_bitcoin_state.json")

# Active Bitcoin-focused public accounts.
PEOPLE = [
    ("Michael Saylor", "saylor"),
    ("Jack Mallers", "jackmallers"),
    ("Samson Mow", "Excellion"),
    ("Pierre Rochard", "BitcoinPierre"),
    ("Natalie Brunell", "natbrunell"),
]

# X's own public embed timeline endpoint. RSSHub's Twitter routes were returning
# cached timelines that were 10-12+ months old, so they are intentionally no
# longer used here.
SYNDICATION_URL = "https://syndication.twitter.com/srv/timeline-profile/screen-name/{handle}"
NEXT_DATA_RE = re.compile(
    r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")

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


def _clean(value: str | None, limit: int = 1000) -> str:
    text = html.unescape(_TAG_RE.sub(" ", value or ""))
    text = _SPACE_RE.sub(" ", text).strip()
    if not text:
        return "No text provided by the source."
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _parse_x_datetime(value: object) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    formats = (
        "%a %b %d %H:%M:%S %z %Y",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
    )
    for fmt in formats:
        try:
            dt = datetime.strptime(text.replace("Z", "+0000"), fmt)
            return dt.astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def _published_datetime(entry: dict) -> datetime | None:
    value = entry.get("published_dt")
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    return _parse_x_datetime(entry.get("published"))


def _is_fresh(entry: dict) -> bool:
    published = _published_datetime(entry)
    if published is None:
        return False
    return published >= datetime.now(timezone.utc) - timedelta(days=MAX_POST_AGE_DAYS)


def _published_display(entry: dict) -> str:
    published = _published_datetime(entry)
    return discord.utils.format_dt(published, style="R") if published else "Unknown"


def _post_text(entry: dict) -> str:
    return _clean(str(entry.get("text") or entry.get("title") or ""), 1600)


def _is_bitcoin_post(entry: dict) -> bool:
    text = f" {_post_text(entry).lower()} "
    return any(term in text for term in BITCOIN_TERMS)


def _entry_key(handle: str, entry: dict) -> str:
    raw = str(entry.get("id") or entry.get("link") or f"{_post_text(entry)}|{entry.get('published', '')}")
    return hashlib.sha256(f"{handle}|{raw}".encode("utf-8", "ignore")).hexdigest()


def _signal_id(handle: str, entry: dict) -> str:
    return f"BTC-{handle[:5].upper()}-{_entry_key(handle, entry)[:6].upper()}"


def _extract_timeline_entries(payload: dict, expected_handle: str) -> list[dict]:
    raw_entries = (
        payload.get("props", {})
        .get("pageProps", {})
        .get("timeline", {})
        .get("entries", [])
    )
    items: list[dict] = []

    for raw in raw_entries:
        if not isinstance(raw, dict) or raw.get("type") != "tweet":
            continue
        content = raw.get("content") or {}
        tweet = content.get("tweet") if isinstance(content, dict) else None
        if not isinstance(tweet, dict):
            continue

        user = tweet.get("user") or {}
        screen_name = str(user.get("screen_name") or expected_handle)
        # Skip retweets/foreign timeline objects. Replies authored by the account
        # are fine as long as they are Bitcoin-related.
        if screen_name.lower() != expected_handle.lower():
            continue

        tweet_id = str(tweet.get("id_str") or raw.get("entry_id") or "").strip()
        text = str(tweet.get("full_text") or tweet.get("text") or "").strip()
        published = _parse_x_datetime(tweet.get("created_at"))
        if not tweet_id or not text or published is None:
            continue

        link = str(tweet.get("permalink") or "").strip()
        if not link:
            link = f"https://x.com/{expected_handle}/status/{tweet_id}"
        elif link.startswith("/"):
            link = f"https://x.com{link}"
        else:
            link = link.replace("https://twitter.com/", "https://x.com/")

        items.append(
            {
                "id": tweet_id,
                "text": text,
                "title": text,
                "link": link,
                "published": published.isoformat(),
                "published_dt": published,
            }
        )

    items.sort(key=lambda item: _published_datetime(item) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return items


class BitcoinRSSState:
    def __init__(self) -> None:
        self.seen: set[str] = set()
        self.seeded = False
        self.load()

    def load(self) -> None:
        try:
            raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            if raw.get("source_version") != SOURCE_VERSION:
                return
            self.seen = {str(value) for value in raw.get("seen", [])}
            self.seeded = bool(raw.get("seeded", False))
        except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError):
            pass

    def save(self) -> None:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(
            json.dumps(
                {
                    "source_version": SOURCE_VERSION,
                    "seeded": self.seeded,
                    "seen": list(self.seen)[-5000:],
                },
                indent=2,
            ),
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

    async def fetch_person(self, name: str, handle: str) -> list[dict]:
        url = SYNDICATION_URL.format(handle=handle)
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
                "Mobile/15E148 Safari/604.1"
            ),
            "Accept": "text/html,application/xhtml+xml",
        }
        timeout = httpx.Timeout(REQUEST_TIMEOUT_SECONDS)

        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
            response = await client.get(url)
            if response.status_code == 429:
                raise RuntimeError("X syndication rate limited this profile (HTTP 429)")
            response.raise_for_status()

        match = NEXT_DATA_RE.search(response.text)
        if not match:
            raise RuntimeError("X syndication response did not contain __NEXT_DATA__")

        try:
            payload = json.loads(html.unescape(match.group(1)))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"X syndication JSON parse failed: {exc}") from exc

        entries = _extract_timeline_entries(payload, handle)
        if not entries:
            raise RuntimeError("X syndication returned no usable recent timeline entries")

        self.last_instance[name] = url
        return entries

    async def _fetch_all_people(self):
        # X's embed endpoint is IP-rate-limited. Five requests are staggered by
        # 1.5 seconds so we stay fast without hammering it simultaneously.
        async def one(index: int, name: str, handle: str):
            await asyncio.sleep(index * 1.5)
            try:
                entries = await self.fetch_person(name, handle)
                return name, handle, entries, None
            except Exception as exc:
                return name, handle, None, str(exc)[:700]

        return await asyncio.gather(
            *(one(index, name, handle) for index, (name, handle) in enumerate(PEOPLE))
        )

    def make_embed(self, name: str, handle: str, entry: dict) -> discord.Embed:
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
                "\u001b[2;37mLIVE X TIMELINE // BTC FILTER MATCH\u001b[0m\n"
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
        embed.add_field(name="◢ ROUTE", value="`LIVE X`", inline=True)
        if link:
            embed.add_field(name="OPEN SOURCE", value=f"**[VIEW ORIGINAL POST ↗]({link})**", inline=False)
        embed.set_author(name="CYBERSPACE // BITCOIN WATCH")
        embed.set_footer(text=f"PURPLE TEAM • LIVE BTC SIGNAL • {signal_id}")
        return embed

    def _eligible(self, entries: list[dict]) -> list[dict]:
        return [entry for entry in entries if _is_fresh(entry) and _is_bitcoin_post(entry)]

    async def check(self) -> tuple[int, int]:
        async with self._running_check:
            channel = await self.find_channel()
            if channel is None:
                raise RuntimeError(f"Could not find {BITCOIN_CHANNEL}")

            total_new = 0
            source_successes = 0
            first_seed = not self.state.seeded
            self.last_errors = {}

            results = await self._fetch_all_people()
            for name, handle, entries, error in results:
                if error is not None or entries is None:
                    self.last_errors[name] = error or "Unknown X timeline error"
                    continue

                source_successes += 1
                matches = self._eligible(entries)
                unseen: list[tuple[str, dict]] = []
                for entry in matches:
                    key = _entry_key(handle, entry)
                    if key not in self.state.seen:
                        unseen.append((key, entry))

                if first_seed:
                    for key, _ in unseen:
                        self.state.seen.add(key)
                    continue

                # Send older-to-newer among the small selected batch.
                for key, entry in reversed(unseen[:MAX_POSTS_PER_PERSON_PER_CHECK]):
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

            if first_seed:
                self.state.seeded = True
            self.state.save()
            self.last_check = datetime.now(timezone.utc)
            return total_new, source_successes

    async def push_latest(self) -> tuple[int, int]:
        async with self._running_check:
            channel = await self.find_channel()
            if channel is None:
                raise RuntimeError(f"Could not find {BITCOIN_CHANNEL}")

            posted = 0
            source_successes = 0
            self.last_errors = {}

            results = await self._fetch_all_people()
            for name, handle, entries, error in results:
                if error is not None or entries is None:
                    self.last_errors[name] = error or "Unknown X timeline error"
                    continue

                source_successes += 1
                matches = self._eligible(entries)
                if not matches:
                    self.last_errors[name] = f"No Bitcoin post found in the last {MAX_POST_AGE_DAYS} days"
                    continue

                entry = matches[0]
                try:
                    await channel.send(
                        embed=self.make_embed(name, handle, entry),
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                    self.state.seen.add(_entry_key(handle, entry))
                    posted += 1
                except discord.HTTPException as exc:
                    self.last_errors[name] = f"Discord post failed: {exc}"

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
            self.task = asyncio.create_task(self.loop(), name="cyberspace-bitcoin-live-x")


def install_rss_bitcoin(bot: discord.Client) -> None:
    service = BitcoinPeopleRSS(bot)
    bot.bitcoin_rss = service  # type: ignore[attr-defined]

    async def on_ready() -> None:
        service.start()

    bot.add_listener(on_ready, "on_ready")


def register_bitcoin_rss_commands(bot: discord.Client) -> None:
    group = app_commands.Group(
        name="bitcoin-rss",
        description="CyberSpace live Bitcoin signal controls",
        default_permissions=discord.Permissions(administrator=True),
    )

    async def owner_only(interaction: discord.Interaction) -> bool:
        if settings.bot_owner_id and interaction.user.id == settings.bot_owner_id:
            return True
        await interaction.response.send_message(
            "Only the configured bot owner can manage Bitcoin feeds.", ephemeral=True
        )
        return False

    @group.command(name="status", description="Show live Bitcoin feed status")
    @app_commands.guild_only()
    async def bitcoin_status(interaction: discord.Interaction):
        if not await owner_only(interaction):
            return
        service: BitcoinPeopleRSS = getattr(bot, "bitcoin_rss")
        check_text = (
            discord.utils.format_dt(service.last_check, style="R")
            if service.last_check
            else "Not checked yet"
        )
        errors = "\n".join(
            f"• **{name}:** {error}" for name, error in service.last_errors.items()
        ) or "None"
        embed = discord.Embed(
            title="CYBERSPACE // LIVE BITCOIN STATUS",
            description=(
                f"Destination: `{BITCOIN_CHANNEL}`\n"
                f"Polling: every **{POLL_SECONDS // 60} minutes**\n"
                f"Freshness cutoff: **{MAX_POST_AGE_DAYS} days**\n"
                f"Source: **X public syndication timeline**\n"
                f"Last check: {check_text}"
            ),
            colour=discord.Colour.from_rgb(247, 147, 26),
        )
        embed.add_field(
            name="People",
            value="\n".join(f"• {name} (`@{handle}`)" for name, handle in PEOPLE),
            inline=False,
        )
        embed.add_field(name="Last errors", value=errors[:1024], inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @group.command(name="check-now", description="Check all live Bitcoin timelines immediately")
    @app_commands.guild_only()
    async def bitcoin_check_now(interaction: discord.Interaction):
        if not await owner_only(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        service: BitcoinPeopleRSS = getattr(bot, "bitcoin_rss")
        try:
            posted, successes = await service.check()
            await interaction.followup.send(
                f"Bitcoin check complete: **{posted}** new post(s); "
                f"**{successes}/{len(PEOPLE)}** live timelines reached.",
                ephemeral=True,
            )
        except Exception as exc:
            await interaction.followup.send(
                f"Bitcoin check failed: `{str(exc)[:1000]}`", ephemeral=True
            )

    @group.command(name="latest", description="Push the newest fresh Bitcoin post from each person")
    @app_commands.guild_only()
    async def bitcoin_latest(interaction: discord.Interaction):
        if not await owner_only(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        service: BitcoinPeopleRSS = getattr(bot, "bitcoin_rss")
        try:
            posted, successes = await service.push_latest()
            await interaction.followup.send(
                f"Latest Bitcoin push complete: **{posted}** fresh post(s) sent to "
                f"`{BITCOIN_CHANNEL}`; **{successes}/{len(PEOPLE)}** live timelines reached.",
                ephemeral=True,
            )
        except Exception as exc:
            await interaction.followup.send(
                f"Latest Bitcoin push failed: `{str(exc)[:1000]}`", ephemeral=True
            )

    @group.command(name="test", description="Post a live Bitcoin feed test card")
    @app_commands.guild_only()
    async def bitcoin_test(interaction: discord.Interaction):
        if not await owner_only(interaction):
            return
        service: BitcoinPeopleRSS = getattr(bot, "bitcoin_rss")
        channel = await service.find_channel()
        if channel is None:
            await interaction.response.send_message(
                f"Could not find `{BITCOIN_CHANNEL}`.", ephemeral=True
            )
            return
        embed = discord.Embed(
            title="₿ LIVE BITCOIN SIGNAL LINK ONLINE",
            description=(
                "Purple Team is using X's public syndication timeline and will "
                f"reject Bitcoin posts older than {MAX_POST_AGE_DAYS} days."
            ),
            colour=discord.Colour.from_rgb(247, 147, 26),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Status", value="ONLINE", inline=True)
        embed.add_field(name="People", value=str(len(PEOPLE)), inline=True)
        embed.add_field(name="Poll interval", value=f"{POLL_SECONDS // 60} minutes", inline=True)
        embed.set_footer(text="CYBERSPACE // LIVE BITCOIN WATCH")
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        await interaction.response.send_message(
            f"Bitcoin feed test posted in {channel.mention}.", ephemeral=True
        )

    bot.tree.add_command(group)
