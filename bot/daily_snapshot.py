from __future__ import annotations

import asyncio
import io
from datetime import datetime, time
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import tasks
from PIL import Image, ImageDraw, ImageFont

from core.config import settings
from storage.db import snapshot_stats


CARD_WIDTH = 1400
CARD_HEIGHT = 1120
BG = (5, 8, 18)
PANEL = (12, 17, 31)
WHITE = (245, 247, 255)
MUTED = (160, 169, 190)
RED = (255, 47, 76)
BLUE = (52, 133, 255)
PURPLE = (150, 63, 255)
CYAN = (50, 224, 255)
GREEN = (45, 225, 120)


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _rounded_panel(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], accent: tuple[int, int, int]) -> None:
    draw.rounded_rectangle(box, radius=20, fill=PANEL, outline=accent, width=2)
    x1, y1, x2, _ = box
    draw.line((x1 + 18, y1 + 1, x2 - 18, y1 + 1), fill=accent, width=3)


def _metric_card(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    accent: tuple[int, int, int],
    label: str,
    value: str,
    subtext: str,
) -> None:
    _rounded_panel(draw, box, accent)
    x1, y1, _, _ = box
    draw.ellipse((x1 + 24, y1 + 28, x1 + 42, y1 + 46), fill=accent)
    draw.text((x1 + 56, y1 + 20), label, font=_font(22, True), fill=MUTED)
    draw.text((x1 + 24, y1 + 68), value, font=_font(52, True), fill=WHITE)
    draw.text((x1 + 24, y1 + 132), subtext, font=_font(18), fill=MUTED)


def _section_header(draw: ImageDraw.ImageDraw, y: int, title: str, right: str = "") -> None:
    draw.line((48, y + 21, 78, y + 21), fill=RED, width=6)
    draw.line((82, y + 21, 112, y + 21), fill=PURPLE, width=6)
    draw.line((116, y + 21, 146, y + 21), fill=BLUE, width=6)
    draw.text((165, y), title, font=_font(26, True), fill=WHITE)
    if right:
        bbox = draw.textbbox((0, 0), right, font=_font(17, True))
        draw.text((CARD_WIDTH - 48 - (bbox[2] - bbox[0]), y + 6), right, font=_font(17, True), fill=CYAN)


def render_snapshot(stats: dict[str, int | str], now: datetime) -> bytes:
    image = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), BG)
    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle((16, 16, CARD_WIDTH - 16, CARD_HEIGHT - 16), radius=28, outline=RED, width=3)
    draw.line((22, 18, 470, 18), fill=RED, width=4)
    draw.line((470, 18, 930, 18), fill=PURPLE, width=4)
    draw.line((930, 18, CARD_WIDTH - 22, 18), fill=BLUE, width=4)

    draw.text((48, 42), "PURPLE TEAM", font=_font(48, True), fill=WHITE)
    draw.text((365, 42), "SNAPSHOT", font=_font(48, True), fill=PURPLE)
    draw.text((50, 99), "SECURITY OPERATIONS • OSINT • THREAT INTELLIGENCE", font=_font(19, True), fill=MUTED)
    draw.rounded_rectangle((1060, 46, 1335, 104), radius=18, outline=CYAN, width=2)
    draw.ellipse((1084, 65, 1104, 85), fill=GREEN)
    draw.text((1118, 61), "SYSTEMS ONLINE", font=_font(20, True), fill=WHITE)

    _section_header(draw, 150, "NETWORK INTELLIGENCE", "GLOBAL SNAPSHOT")
    card_y1, card_y2 = 198, 382
    gap = 24
    card_w = (CARD_WIDTH - 96 - (gap * 2)) // 3
    x1 = 48
    x2 = x1 + card_w + gap
    x3 = x2 + card_w + gap
    _metric_card(draw, (x1, card_y1, x1 + card_w, card_y2), RED, "PROTECTED GUILDS", str(stats["protected_guilds"]), "Authorized Purple Team communities")
    _metric_card(draw, (x2, card_y1, x2 + card_w, card_y2), PURPLE, "MEMBERS PROTECTED", str(stats["members_protected"]), "Members in the protected guild")
    _metric_card(draw, (x3, card_y1, x3 + card_w, card_y2), BLUE, "AUTHORIZED TARGETS", str(stats["scope_targets"]), "Approved active-assessment scope")

    _section_header(draw, 410, "NETWORK SECURITY OPERATIONS")
    ops_box = (48, 458, CARD_WIDTH - 48, 630)
    _rounded_panel(draw, ops_box, PURPLE)
    col_x = [78, 500, 922]
    labels = ["SCAN FAILURES (24H)", "SCANS PROCESSED", "ENFORCEMENT"]
    values = [str(stats["failed_scans_24h"]), str(stats["total_scans"]), "ONLINE"]
    subs = ["Requires review", "Assessment history", "Channel + scope protection"]
    for idx, x in enumerate(col_x):
        draw.text((x, 486), labels[idx], font=_font(19, True), fill=MUTED)
        draw.text((x, 522), values[idx], font=_font(42, True), fill=GREEN if idx == 2 else WHITE)
        draw.text((x, 575), subs[idx], font=_font(17), fill=MUTED)
        if idx < 2:
            draw.line((x + 350, 484, x + 350, 596), fill=(55, 62, 82), width=2)

    _section_header(draw, 660, "DAILY SECURITY ANALYTICS", "LAST 24 HOURS")
    card_y1, card_y2 = 708, 892
    _metric_card(draw, (x1, card_y1, x1 + card_w, card_y2), RED, "SECURITY EVENTS", str(stats["audit_events_24h"]), "Recorded audit activity")
    _metric_card(draw, (x2, card_y1, x2 + card_w, card_y2), PURPLE, "ASSESSMENTS", str(stats["scans_24h"]), "Active scans executed")
    _metric_card(draw, (x3, card_y1, x3 + card_w, card_y2), BLUE, "SUCCESSFUL SCANS", str(stats["successful_scans_24h"]), "Completed without error")

    _section_header(draw, 920, "PROTECTION OUTCOME")
    outcome = (48, 968, CARD_WIDTH - 48, 1050)
    _rounded_panel(draw, outcome, BLUE)
    draw.text((78, 986), "TOP ACTIVITY", font=_font(18, True), fill=MUTED)
    draw.text((78, 1017), str(stats["top_action_24h"]), font=_font(23, True), fill=WHITE)
    draw.text((570, 986), "TOTAL AUDIT EVENTS", font=_font(18, True), fill=MUTED)
    draw.text((570, 1017), str(stats["total_audits"]), font=_font(23, True), fill=WHITE)
    draw.text((960, 986), "DATABASE", font=_font(18, True), fill=MUTED)
    draw.text((960, 1017), str(stats["database_backend"]), font=_font(23, True), fill=WHITE)

    footer = f"PURPLE TEAM SNAPSHOT • {now.strftime('%B %d, %Y').upper()} • {settings.snapshot_timezone.upper()}"
    draw.text((48, 1072), footer, font=_font(15, True), fill=MUTED)
    right = "J2SEC • SECURITY WITHOUT COMPROMISE"
    rb = draw.textbbox((0, 0), right, font=_font(15, True))
    draw.text((CARD_WIDTH - 48 - (rb[2] - rb[0]), 1072), right, font=_font(15, True), fill=RED)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


async def collect_snapshot_stats(bot: discord.Client) -> dict[str, int | str]:
    gid = settings.discord_guild_id
    stats = await snapshot_stats(gid)

    protected_guilds = 1 if gid else len(getattr(bot, "guilds", []))
    members = 0
    if gid:
        try:
            guild = await bot.fetch_guild(gid, with_counts=True)
            members = int(guild.approximate_member_count or guild.member_count or 0)
        except Exception:
            cached = bot.get_guild(gid)
            members = int(cached.member_count or 0) if cached else 0
    else:
        members = sum(int(g.member_count or 0) for g in getattr(bot, "guilds", []))

    stats["protected_guilds"] = protected_guilds
    stats["members_protected"] = members
    return stats


async def post_snapshot(bot: discord.Client) -> discord.Message:
    channel = bot.get_channel(settings.discord_snapshot_channel_id)
    if channel is None:
        channel = await bot.fetch_channel(settings.discord_snapshot_channel_id)
    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        raise RuntimeError("DISCORD_SNAPSHOT_CHANNEL_ID is not a text-capable Discord channel")

    tz = ZoneInfo(settings.snapshot_timezone)
    now = datetime.now(tz)
    stats = await collect_snapshot_stats(bot)
    png = await asyncio.to_thread(render_snapshot, stats, now)
    file = discord.File(io.BytesIO(png), filename="purple-team-daily-snapshot.png")
    return await channel.send(
        content="📊 **Purple Team Daily Security Snapshot**\nAutomated operational summary for the last 24 hours.",
        file=file,
    )


def install_daily_snapshot(bot: discord.Client) -> None:
    tz = ZoneInfo(settings.snapshot_timezone)
    run_at = time(hour=settings.snapshot_hour, minute=settings.snapshot_minute, tzinfo=tz)

    @tasks.loop(time=run_at)
    async def daily_snapshot_loop() -> None:
        try:
            await post_snapshot(bot)
        except Exception as exc:
            print(f"Daily snapshot failed: {exc}")

    @daily_snapshot_loop.before_loop
    async def before_snapshot_loop() -> None:
        await bot.wait_until_ready()

    daily_snapshot_loop.start()
    setattr(bot, "_daily_snapshot_loop", daily_snapshot_loop)


def register_snapshot_command(bot: discord.Client) -> None:
    snapshot = app_commands.Group(name="snapshot", description="Purple Team daily snapshot controls")

    @snapshot.command(name="post", description="Post the Purple Team snapshot now")
    @app_commands.default_permissions(manage_guild=True)
    async def snapshot_post(interaction: discord.Interaction) -> None:
        if interaction.user.id != settings.bot_owner_id:
            member = interaction.user if isinstance(interaction.user, discord.Member) else None
            if member is None or not member.guild_permissions.manage_guild:
                await interaction.response.send_message("You need Manage Server permission to post a snapshot.", ephemeral=True)
                return
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            message = await post_snapshot(bot)
            await interaction.followup.send(f"Snapshot posted: {message.jump_url}", ephemeral=True)
        except Exception as exc:
            await interaction.followup.send(f"Snapshot failed: `{str(exc)[:1500]}`", ephemeral=True)

    bot.tree.add_command(snapshot)
