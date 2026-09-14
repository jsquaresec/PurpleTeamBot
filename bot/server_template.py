import json
from pathlib import Path
from typing import Any

import discord
from discord import app_commands

from bot.ui import make_embed
from core.config import settings


DEFAULT_TEMPLATE_PATH = Path("private/j2_server_template.json")
REPLACE_CONFIRMATION = "REPLACE"


def _load_template() -> dict[str, Any]:
    path = Path(getattr(settings, "server_template_path", "") or DEFAULT_TEMPLATE_PATH)
    if not path.exists():
        raise RuntimeError(
            f"Private server template not found at {path}. "
            "This file is intentionally excluded from Git."
        )
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or not isinstance(data.get("roles"), list) or not isinstance(data.get("categories"), list):
        raise RuntimeError("Private server template is invalid: expected roles[] and categories[].")
    return data


def _permissions(items: list[str]) -> discord.Permissions:
    try:
        return discord.Permissions(**{name: True for name in items})
    except TypeError as exc:
        raise RuntimeError(f"Invalid role permission in private template: {exc}") from exc


def _colour(value: str | int | None) -> discord.Colour:
    if value in (None, ""):
        return discord.Colour.default()
    if isinstance(value, int):
        return discord.Colour(value)
    text = str(value).strip().lower().removeprefix("#").removeprefix("0x")
    return discord.Colour(int(text, 16))


def _overwrite_from_spec(spec: dict[str, Any]) -> discord.PermissionOverwrite:
    payload: dict[str, bool] = {}
    for name in spec.get("allow", []):
        payload[name] = True
    for name in spec.get("deny", []):
        payload[name] = False
    try:
        return discord.PermissionOverwrite(**payload)
    except TypeError as exc:
        raise RuntimeError(f"Invalid channel permission in private template: {exc}") from exc


def _build_overwrites(
    guild: discord.Guild,
    role_map: dict[str, discord.Role],
    spec: dict[str, Any] | None,
    *,
    base: dict[discord.abc.Snowflake, discord.PermissionOverwrite] | None = None,
) -> dict[discord.abc.Snowflake, discord.PermissionOverwrite]:
    result = dict(base or {})
    for target, overwrite_spec in (spec or {}).items():
        if target == "@everyone":
            result[guild.default_role] = _overwrite_from_spec(overwrite_spec)
            continue
        role = role_map.get(target)
        if role is None:
            raise RuntimeError(f"Template references missing role {target!r} in permission overwrites.")
        result[role] = _overwrite_from_spec(overwrite_spec)
    return result


def _bot_role(guild: discord.Guild, bot_user_id: int) -> discord.Role:
    """Resolve the managed Discord role owned by this bot without member-cache dependence."""
    for role in guild.roles:
        if not role.managed:
            continue
        tags = getattr(role, "tags", None)
        if tags is not None and getattr(tags, "bot_id", None) == bot_user_id:
            return role

    # Fallback for discord.py builds where RoleTags.bot_id is not populated.
    me = guild.me
    if me is not None:
        roles = list(getattr(me, "roles", []) or [])
        managed = [role for role in roles if role.managed and role != guild.default_role]
        if managed:
            return max(managed, key=lambda role: role.position)

    raise RuntimeError(
        "Purple Team could not resolve its managed Discord role. Restart the bot and verify it is still in this server."
    )


def _preflight_replace(
    guild: discord.Guild,
    template: dict[str, Any],
    *,
    bot_user_id: int,
    effective_permissions: discord.Permissions | None = None,
) -> discord.Role:
    bot_role = _bot_role(guild, bot_user_id)

    if effective_permissions is None:
        me = guild.me
        perms = me.guild_permissions if me is not None else discord.Permissions.none()
    else:
        perms = effective_permissions

    if not (perms.administrator or (perms.manage_channels and perms.manage_roles)):
        raise RuntimeError(
            "Purple Team needs Administrator, or both Manage Channels and Manage Roles, "
            "before it can replace the server layout."
        )

    blocked_roles = [
        role.name
        for role in guild.roles
        if role != guild.default_role
        and not role.managed
        and role.position >= bot_role.position
    ]
    if blocked_roles:
        preview = ", ".join(blocked_roles[:8])
        extra = " ..." if len(blocked_roles) > 8 else ""
        raise RuntimeError(
            "Purple Team cannot delete one or more existing roles because they are at or above "
            f"its managed bot role: {preview}{extra}. Move the Purple Team role above them first."
        )

    role_count = len(template.get("roles", []))
    if bot_role.position - 1 < role_count:
        raise RuntimeError(
            "Purple Team's role is not high enough to place the full J2 role hierarchy. "
            "Move the Purple Team bot role higher and run the command again."
        )

    return bot_role


async def _wipe_existing_layout(
    guild: discord.Guild,
    *,
    bot_role: discord.Role,
) -> dict[str, int]:
    """Delete all deletable user-created channels and roles."""
    deleted_channels = 0
    deleted_categories = 0
    deleted_roles = 0

    ordinary_channels = [c for c in guild.channels if not isinstance(c, discord.CategoryChannel)]
    categories = list(guild.categories)

    for channel in ordinary_channels:
        await channel.delete(reason="Purple Team J2 full server replacement")
        deleted_channels += 1

    for category in categories:
        await category.delete(reason="Purple Team J2 full server replacement")
        deleted_categories += 1

    for role in sorted(guild.roles, key=lambda r: r.position, reverse=True):
        if role == guild.default_role or role.managed:
            continue
        if role.position >= bot_role.position:
            raise RuntimeError(
                f"Role {role.name!r} is at or above Purple Team and could not be deleted."
            )
        await role.delete(reason="Purple Team J2 full server replacement")
        deleted_roles += 1

    return {
        "channels_deleted": deleted_channels,
        "categories_deleted": deleted_categories,
        "roles_deleted": deleted_roles,
    }


async def _ensure_roles(
    guild: discord.Guild,
    definitions: list[dict[str, Any]],
    *,
    bot_role: discord.Role,
) -> dict[str, discord.Role]:
    role_map: dict[str, discord.Role] = {role.name: role for role in guild.roles}

    for item in reversed(definitions):
        name = item["name"]
        role = role_map.get(name)
        perms = _permissions(item.get("permissions", []))
        colour = _colour(item.get("colour"))
        if role is None:
            role = await guild.create_role(
                name=name,
                permissions=perms,
                colour=colour,
                hoist=bool(item.get("hoist", False)),
                mentionable=bool(item.get("mentionable", False)),
                reason="Purple Team private J2 template install",
            )
            role_map[name] = role
        elif not role.managed:
            await role.edit(
                permissions=perms,
                colour=colour,
                hoist=bool(item.get("hoist", False)),
                mentionable=bool(item.get("mentionable", False)),
                reason="Purple Team private J2 template sync",
            )

    ceiling = bot_role.position - 1
    if ceiling < len(definitions):
        raise RuntimeError(
            "Purple Team's bot role is not high enough to place the full role hierarchy. "
            "Move the Purple Team role above all template-managed roles and run the command again."
        )

    for index in range(len(definitions) - 1, -1, -1):
        item = definitions[index]
        role = role_map[item["name"]]
        if role.managed:
            continue
        target_position = ceiling - index
        if role.position != target_position:
            await role.edit(position=target_position, reason="Purple Team J2 role hierarchy")

    return role_map


async def _ensure_category(
    guild: discord.Guild,
    name: str,
    overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite],
) -> tuple[discord.CategoryChannel, bool]:
    existing = discord.utils.get(guild.categories, name=name)
    if existing is None:
        return await guild.create_category(name=name, overwrites=overwrites, reason="Purple Team private J2 template install"), True
    await existing.edit(overwrites=overwrites, reason="Purple Team private J2 template sync")
    return existing, False


async def _ensure_channel(
    guild: discord.Guild,
    category: discord.CategoryChannel,
    item: dict[str, Any],
    overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite],
) -> tuple[discord.abc.GuildChannel, bool]:
    name = item["name"]
    kind = item.get("type", "text")
    existing = discord.utils.get(category.channels, name=name)
    topic = item.get("topic")

    expected_types = {
        "text": discord.TextChannel,
        "voice": discord.VoiceChannel,
        "forum": discord.ForumChannel,
    }
    expected = expected_types.get(kind)
    if expected is None:
        raise RuntimeError(f"Unsupported channel type {kind!r} for {name!r}.")
    if existing is not None and not isinstance(existing, expected):
        raise RuntimeError(f"Channel {name!r} already exists with the wrong Discord channel type.")

    if existing is not None:
        kwargs: dict[str, Any] = {"overwrites": overwrites, "reason": "Purple Team private J2 template sync"}
        if isinstance(existing, (discord.TextChannel, discord.ForumChannel)) and topic is not None:
            kwargs["topic"] = topic
        await existing.edit(**kwargs)
        return existing, False

    common = {
        "name": name,
        "category": category,
        "overwrites": overwrites,
        "reason": "Purple Team private J2 template install",
    }
    if kind == "voice":
        channel = await guild.create_voice_channel(**common)
    elif kind == "forum":
        channel = await guild.create_forum(topic=topic, **common)
    else:
        channel = await guild.create_text_channel(topic=topic, **common)
    return channel, True


async def install_private_template(
    guild: discord.Guild,
    *,
    bot_user_id: int,
    replace: bool = False,
    effective_permissions: discord.Permissions | None = None,
) -> dict[str, int]:
    data = _load_template()
    bot_role = _preflight_replace(
        guild,
        data,
        bot_user_id=bot_user_id,
        effective_permissions=effective_permissions,
    )

    wipe_stats = {
        "channels_deleted": 0,
        "categories_deleted": 0,
        "roles_deleted": 0,
    }
    if replace:
        wipe_stats = await _wipe_existing_layout(guild, bot_role=bot_role)

    role_map = await _ensure_roles(guild, data["roles"], bot_role=bot_role)
    created_categories = 0
    created_channels = 0

    for category_index, category_spec in enumerate(data["categories"]):
        category_overwrites = _build_overwrites(guild, role_map, category_spec.get("overwrites"))
        category, was_created = await _ensure_category(guild, category_spec["name"], category_overwrites)
        created_categories += int(was_created)
        await category.edit(position=category_index, reason="Purple Team J2 category order")

        for channel_index, channel_spec in enumerate(category_spec.get("channels", [])):
            channel_overwrites = _build_overwrites(
                guild,
                role_map,
                channel_spec.get("overwrites"),
                base=category_overwrites,
            )
            channel, was_created = await _ensure_channel(guild, category, channel_spec, channel_overwrites)
            created_channels += int(was_created)
            await channel.edit(position=channel_index, reason="Purple Team J2 channel order")

    root_role = role_map.get(data.get("root_role", "root"))
    if root_role is not None:
        try:
            owner_member = guild.get_member(guild.owner_id) or await guild.fetch_member(guild.owner_id)
            await owner_member.add_roles(root_role, reason="Purple Team J2 owner/root mapping")
        except discord.HTTPException:
            pass

    return {
        "roles": len(data["roles"]),
        "categories_created": created_categories,
        "channels_created": created_channels,
        **wipe_stats,
    }


async def _dm_result(user: discord.abc.User, title: str, description: str, *, kind: str) -> None:
    try:
        await user.send(embed=make_embed(title, description, kind=kind))
    except discord.HTTPException:
        pass


def register_owner_template_commands(bot: discord.Client) -> None:
    owner = app_commands.Group(
        name="owner",
        description="Purple Team bot-owner operations",
        default_permissions=discord.Permissions(administrator=True),
    )

    @owner.command(name="template-install", description="Replace this server with the private J2 template")
    @app_commands.guild_only()
    async def template_install(interaction: discord.Interaction, confirm: str = ""):
        if not settings.bot_owner_id or interaction.user.id != settings.bot_owner_id:
            await interaction.response.send_message(
                embed=make_embed("Access Denied", "This command is restricted to the configured Purple Team bot owner.", kind="error"),
                ephemeral=True,
            )
            return
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=make_embed("Server Only", "Run this command inside the configured Discord server.", kind="error"),
                ephemeral=True,
            )
            return
        if settings.discord_guild_id and interaction.guild.id != settings.discord_guild_id:
            await interaction.response.send_message(
                embed=make_embed("Unauthorized Server", "The private J2 template can only be installed in the configured Purple Team guild.", kind="error"),
                ephemeral=True,
            )
            return

        if confirm.strip().upper() != REPLACE_CONFIRMATION:
            await interaction.response.send_message(
                embed=make_embed(
                    "Full Server Replacement",
                    "This command will permanently delete all deletable existing channels, categories, messages, and normal roles, then rebuild the server from the private J2 blueprint. Discord-managed/bot/integration roles and `@everyone` cannot be deleted.\n\nRun `/owner template-install confirm:REPLACE` to proceed.",
                    kind="warning",
                ),
                ephemeral=True,
            )
            return

        if interaction.client.user is None:
            await interaction.response.send_message(
                embed=make_embed("Replacement Blocked", "Purple Team has not finished identifying its Discord user yet. Try again in a few seconds.", kind="error"),
                ephemeral=True,
            )
            return

        data = _load_template()
        try:
            _preflight_replace(
                interaction.guild,
                data,
                bot_user_id=interaction.client.user.id,
                effective_permissions=interaction.app_permissions,
            )
        except Exception as exc:
            await interaction.response.send_message(
                embed=make_embed("Replacement Blocked", f"```text\n{str(exc)[:1500]}\n```", kind="error"),
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            embed=make_embed(
                "J2 Replacement Started",
                "Preflight passed. Purple Team is deleting the existing layout and rebuilding the server from the private J2 template. This channel may disappear. I will DM you when the operation finishes.",
                kind="warning",
            ),
            ephemeral=True,
        )

        try:
            result = await install_private_template(
                interaction.guild,
                bot_user_id=interaction.client.user.id,
                replace=True,
                effective_permissions=interaction.app_permissions,
            )
            description = (
                "The J2 server replacement completed successfully.\n\n"
                f"Deleted: **{result['channels_deleted']}** channels, **{result['categories_deleted']}** categories, "
                f"**{result['roles_deleted']}** normal roles.\n"
                f"Created/synchronized: **{result['roles']}** template roles, **{result['categories_created']}** categories, "
                f"**{result['channels_created']}** channels."
            )
            await _dm_result(interaction.user, "J2 Replacement Complete", description, kind="success")
        except Exception as exc:
            await _dm_result(
                interaction.user,
                "J2 Replacement Failed",
                f"The replacement encountered an error after starting.\n\n```text\n{str(exc)[:1500]}\n```",
                kind="error",
            )

    bot.tree.add_command(owner)
