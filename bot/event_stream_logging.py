from __future__ import annotations

from datetime import datetime, timezone

import discord


LOG_CHANNELS = {
    "moderation": "📜・moderation-log",
    "member": "👤・member-log",
    "message": "💬・message-log",
    "voice": "🔊・voice-log",
    "server": "🛰️・server-log",
    "automation": "🤖・automation-log",
    "security": "🛡️・security-log",
}

PROTECTED_ROLE_NAMES = {
    "Almighty Purple",
    "root",
    "sudo",
    "wheel",
    "operator",
    "sysadmin",
    "daemon",
    "/dev/null",
}


def _clip(value: str | None, limit: int = 900) -> str:
    text = (value or "").strip()
    if not text:
        return "`not available`"
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


def _embed(title: str, description: str, *, colour: discord.Colour | None = None) -> discord.Embed:
    panel = discord.Embed(
        title=title,
        description=description,
        colour=colour or discord.Colour.from_rgb(139, 92, 246),
        timestamp=datetime.now(timezone.utc),
    )
    panel.set_footer(text="CyberSpace Event Stream")
    return panel


async def _channel(guild: discord.Guild, key: str) -> discord.TextChannel | None:
    wanted = LOG_CHANNELS[key]
    try:
        channels = await guild.fetch_channels()
    except discord.HTTPException:
        return None
    return next(
        (item for item in channels if isinstance(item, discord.TextChannel) and item.name == wanted),
        None,
    )


async def _send(guild: discord.Guild, key: str, embed: discord.Embed) -> None:
    target = await _channel(guild, key)
    if target is None:
        return
    try:
        await target.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException:
        pass


async def _best_effort_leave_kind(guild: discord.Guild, member: discord.Member) -> str:
    """Distinguish a recent kick from a normal leave when audit-log access exists."""
    try:
        async for entry in guild.audit_logs(limit=6, action=discord.AuditLogAction.kick):
            target = getattr(entry, "target", None)
            if target and target.id == member.id:
                age = (datetime.now(timezone.utc) - entry.created_at).total_seconds()
                if age <= 12:
                    actor = getattr(entry.user, "mention", None) or str(entry.user)
                    return f"Kicked by {actor}"
    except (discord.Forbidden, discord.HTTPException):
        pass
    return "Left server"


def install_event_stream_logging(bot: discord.Client) -> None:
    # The community module used to send departure banners to #welcome. Replace
    # only its member-remove listener so departures live exclusively in member-log.
    if hasattr(bot, "extra_events"):
        bot.extra_events["on_member_remove"] = []

    async def on_member_join(member: discord.Member) -> None:
        kind = "BOT / SERVICE" if member.bot else "MEMBER"
        panel = _embed(
            f"NODE CONNECTED // {kind}",
            f"{member.mention} (`{member.id}`) joined CyberSpace.",
            colour=discord.Colour.green(),
        )
        panel.add_field(name="Account", value=str(member), inline=True)
        panel.add_field(name="Bot", value="Yes" if member.bot else "No", inline=True)
        panel.add_field(name="Created", value=discord.utils.format_dt(member.created_at, style="R"), inline=True)
        await _send(member.guild, "member", panel)

        if member.bot:
            auto = _embed(
                "AUTOMATION NODE CONNECTED",
                f"Bot/service **{member}** (`{member.id}`) was added to the server.",
                colour=discord.Colour.blurple(),
            )
            await _send(member.guild, "automation", auto)

    async def on_member_remove(member: discord.Member) -> None:
        leave_kind = await _best_effort_leave_kind(member.guild, member)
        panel = _embed(
            "NODE DISCONNECTED",
            f"**{member}** (`{member.id}`) disconnected from CyberSpace.",
            colour=discord.Colour.red(),
        )
        panel.add_field(name="Type", value="Bot / Service" if member.bot else "Member", inline=True)
        panel.add_field(name="Event", value=leave_kind, inline=True)
        if member.roles:
            roles = [role.name for role in member.roles if role.name != "@everyone"]
            if roles:
                panel.add_field(name="Roles at departure", value=_clip(", ".join(roles), 900), inline=False)
        await _send(member.guild, "member", panel)

        if member.bot:
            await _send(
                member.guild,
                "automation",
                _embed(
                    "AUTOMATION NODE DISCONNECTED",
                    f"Bot/service **{member}** (`{member.id}`) left or was removed.",
                    colour=discord.Colour.red(),
                ),
            )

    async def on_member_ban(guild: discord.Guild, user: discord.User | discord.Member) -> None:
        panel = _embed("MEMBER BANNED", f"**{user}** (`{user.id}`) was banned.", colour=discord.Colour.red())
        await _send(guild, "moderation", panel)
        await _send(guild, "security", panel.copy())

    async def on_member_unban(guild: discord.Guild, user: discord.User) -> None:
        panel = _embed("MEMBER UNBANNED", f"**{user}** (`{user.id}`) was unbanned.", colour=discord.Colour.green())
        await _send(guild, "moderation", panel)

    async def on_member_update(before: discord.Member, after: discord.Member) -> None:
        if before.nick != after.nick:
            panel = _embed(
                "MEMBER PROFILE UPDATED",
                f"{after.mention} changed nickname.",
            )
            panel.add_field(name="Before", value=before.nick or before.name, inline=True)
            panel.add_field(name="After", value=after.nick or after.name, inline=True)
            await _send(after.guild, "member", panel)

        before_roles = {role.id: role for role in before.roles}
        after_roles = {role.id: role for role in after.roles}
        added = [role for rid, role in after_roles.items() if rid not in before_roles and role.name != "@everyone"]
        removed = [role for rid, role in before_roles.items() if rid not in after_roles and role.name != "@everyone"]
        if added or removed:
            panel = _embed("MEMBER ROLES UPDATED", f"Role state changed for {after.mention}.")
            if added:
                panel.add_field(name="Added", value=", ".join(role.name for role in added), inline=False)
            if removed:
                panel.add_field(name="Removed", value=", ".join(role.name for role in removed), inline=False)
            await _send(after.guild, "member", panel)

            touched = [role for role in added + removed if role.name in PROTECTED_ROLE_NAMES]
            if touched:
                sec = _embed(
                    "PROTECTED ROLE CHANGE",
                    f"Protected access changed for {after.mention}.",
                    colour=discord.Colour.orange(),
                )
                sec.add_field(name="Roles", value=", ".join(role.name for role in touched), inline=False)
                await _send(after.guild, "security", sec)

        before_timeout = before.timed_out_until
        after_timeout = after.timed_out_until
        if before_timeout != after_timeout:
            text = "Timeout removed." if after_timeout is None else f"Timed out until {discord.utils.format_dt(after_timeout, style='F')}."
            await _send(
                after.guild,
                "moderation",
                _embed("TIMEOUT UPDATED", f"{after.mention}: {text}", colour=discord.Colour.orange()),
            )

    async def on_raw_message_delete(payload: discord.RawMessageDeleteEvent) -> None:
        if payload.guild_id is None:
            return

        guild = bot.get_guild(payload.guild_id)
        if guild is None:
            try:
                guild = await bot.fetch_guild(payload.guild_id)
            except discord.HTTPException:
                return

        target = await _channel(guild, "message")
        if target and payload.channel_id == target.id:
            return

        cached = payload.cached_message
        if cached is not None and cached.author.bot:
            return

        channel_mention = f"<#{payload.channel_id}>"
        if cached is not None:
            description = f"Message by {cached.author.mention} in {channel_mention} was deleted."
        else:
            description = f"A message in {channel_mention} was deleted. The message was not cached."

        panel = _embed("MESSAGE DELETED", description, colour=discord.Colour.orange())
        panel.add_field(name="Message ID", value=f"`{payload.message_id}`", inline=True)
        panel.add_field(name="Channel ID", value=f"`{payload.channel_id}`", inline=True)

        if cached is not None:
            panel.add_field(name="Content", value=_clip(cached.content), inline=False)
            panel.add_field(name="Author ID", value=f"`{cached.author.id}`", inline=True)
            if cached.attachments:
                panel.add_field(
                    name="Attachments",
                    value="\n".join(a.filename for a in cached.attachments[:8]),
                    inline=False,
                )
        else:
            panel.add_field(
                name="Content",
                value="`not cached — enable Message Content Intent for richer delete logs`",
                inline=False,
            )

        await _send(guild, "message", panel)

    async def on_raw_bulk_message_delete(payload: discord.RawBulkMessageDeleteEvent) -> None:
        if payload.guild_id is None:
            return
        guild = bot.get_guild(payload.guild_id)
        if guild is None:
            try:
                guild = await bot.fetch_guild(payload.guild_id)
            except discord.HTTPException:
                return
        target = await _channel(guild, "message")
        if target and payload.channel_id == target.id:
            return
        panel = _embed(
            "BULK MESSAGE DELETE",
            f"**{len(payload.message_ids)}** messages were deleted from <#{payload.channel_id}>.",
            colour=discord.Colour.red(),
        )
        panel.add_field(name="Channel ID", value=f"`{payload.channel_id}`", inline=True)
        await _send(guild, "message", panel)

    async def on_message_edit(before: discord.Message, after: discord.Message) -> None:
        if after.guild is None or after.author.bot or before.content == after.content:
            return
        target = await _channel(after.guild, "message")
        if target and after.channel.id == target.id:
            return
        panel = _embed("MESSAGE EDITED", f"Message by {after.author.mention} in {after.channel.mention} was edited.")
        panel.add_field(name="Before", value=_clip(before.content), inline=False)
        panel.add_field(name="After", value=_clip(after.content), inline=False)
        panel.add_field(name="Jump", value=f"[Open message]({after.jump_url})", inline=False)
        await _send(after.guild, "message", panel)

    async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState) -> None:
        if before.channel == after.channel and before.mute == after.mute and before.deaf == after.deaf:
            return
        if before.channel is None and after.channel is not None:
            action = f"joined **{after.channel.name}**"
        elif before.channel is not None and after.channel is None:
            action = f"left **{before.channel.name}**"
        elif before.channel != after.channel:
            action = f"moved **{before.channel.name if before.channel else 'unknown'}** → **{after.channel.name if after.channel else 'unknown'}**"
        else:
            action = "voice state changed"
        await _send(member.guild, "voice", _embed("VOICE STATE", f"{member.mention} {action}."))

    async def on_guild_channel_create(channel: discord.abc.GuildChannel) -> None:
        await _send(channel.guild, "server", _embed("CHANNEL CREATED", f"**{channel.name}** (`{channel.id}`) was created."))

    async def on_guild_channel_delete(channel: discord.abc.GuildChannel) -> None:
        await _send(channel.guild, "server", _embed("CHANNEL DELETED", f"**{channel.name}** (`{channel.id}`) was deleted.", colour=discord.Colour.red()))

    async def on_guild_channel_update(before: discord.abc.GuildChannel, after: discord.abc.GuildChannel) -> None:
        if before.name == after.name and before.position == after.position:
            return
        panel = _embed("CHANNEL UPDATED", f"Channel `{after.id}` was updated.")
        panel.add_field(name="Before", value=before.name, inline=True)
        panel.add_field(name="After", value=after.name, inline=True)
        await _send(after.guild, "server", panel)

    async def on_guild_role_create(role: discord.Role) -> None:
        await _send(role.guild, "server", _embed("ROLE CREATED", f"**{role.name}** (`{role.id}`) was created."))

    async def on_guild_role_delete(role: discord.Role) -> None:
        panel = _embed("ROLE DELETED", f"**{role.name}** (`{role.id}`) was deleted.", colour=discord.Colour.red())
        await _send(role.guild, "server", panel)
        if role.name in PROTECTED_ROLE_NAMES:
            await _send(role.guild, "security", panel.copy())

    async def on_guild_role_update(before: discord.Role, after: discord.Role) -> None:
        if before.name == after.name and before.permissions == after.permissions and before.colour == after.colour:
            return
        panel = _embed("ROLE UPDATED", f"**{before.name}** (`{after.id}`) was modified.")
        if before.name != after.name:
            panel.add_field(name="Rename", value=f"{before.name} → {after.name}", inline=False)
        if before.permissions != after.permissions:
            panel.add_field(name="Permissions", value="Role permissions changed.", inline=False)
        await _send(after.guild, "server", panel)
        if before.name in PROTECTED_ROLE_NAMES or after.name in PROTECTED_ROLE_NAMES:
            await _send(after.guild, "security", panel.copy())

    async def on_webhooks_update(channel: discord.abc.GuildChannel) -> None:
        await _send(channel.guild, "server", _embed("WEBHOOK STATE UPDATED", f"Webhook configuration changed in **{channel.name}**."))
        await _send(channel.guild, "security", _embed("WEBHOOK CHANGE", f"Webhook configuration changed in **{channel.name}**.", colour=discord.Colour.orange()))

    listeners = {
        "on_member_join": on_member_join,
        "on_member_remove": on_member_remove,
        "on_member_ban": on_member_ban,
        "on_member_unban": on_member_unban,
        "on_member_update": on_member_update,
        "on_raw_message_delete": on_raw_message_delete,
        "on_raw_bulk_message_delete": on_raw_bulk_message_delete,
        "on_message_edit": on_message_edit,
        "on_voice_state_update": on_voice_state_update,
        "on_guild_channel_create": on_guild_channel_create,
        "on_guild_channel_delete": on_guild_channel_delete,
        "on_guild_channel_update": on_guild_channel_update,
        "on_guild_role_create": on_guild_role_create,
        "on_guild_role_delete": on_guild_role_delete,
        "on_guild_role_update": on_guild_role_update,
        "on_webhooks_update": on_webhooks_update,
    }
    for event_name, func in listeners.items():
        bot.add_listener(func, event_name)
