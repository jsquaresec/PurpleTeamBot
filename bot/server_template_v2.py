import re
from typing import Any

import discord
from discord import app_commands

from bot.server_template import REPLACE_CONFIRMATION, _colour, _dm_result, _load_template, _permissions, _overwrite_from_spec
from bot.ui import make_embed
from core.config import settings


ROLE_NAME_FALLBACK = "[ Demon Scope ]"


def _normalize_role_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


async def _fresh_roles(guild: discord.Guild) -> list[discord.Role]:
    return await guild.fetch_roles()


async def _fresh_channels(guild: discord.Guild) -> list[discord.abc.GuildChannel]:
    return await guild.fetch_channels()


def _everyone_role(guild: discord.Guild, roles: list[discord.Role]) -> discord.Role:
    role = next((item for item in roles if item.id == guild.id), None)
    if role is None:
        raise RuntimeError("Discord did not return the @everyone role for this server.")
    return role


async def _resolve_bot_top_role(
    guild: discord.Guild,
    bot_user_id: int,
    bot_display_name: str = "",
) -> discord.Role:
    roles = await _fresh_roles(guild)

    tagged: list[discord.Role] = []
    for role in roles:
        if not role.managed:
            continue
        tags = getattr(role, "tags", None)
        if tags is not None and getattr(tags, "bot_id", None) == bot_user_id:
            tagged.append(role)
    if tagged:
        return max(tagged, key=lambda role: role.position)

    exact = next((role for role in roles if role.name == ROLE_NAME_FALLBACK), None)
    if exact is not None:
        return exact

    wanted_names = {_normalize_role_name(ROLE_NAME_FALLBACK)}
    if bot_display_name:
        wanted_names.add(_normalize_role_name(bot_display_name))

    candidates = [
        role
        for role in roles
        if role.id != guild.id
        and _normalize_role_name(role.name) in wanted_names
        and role.permissions.administrator
    ]
    if candidates:
        return max(candidates, key=lambda role: role.position)

    raise RuntimeError(
        "Purple Team could not resolve its Discord control role. Expected the role "
        f"`{ROLE_NAME_FALLBACK}` near the top of the role list."
    )


async def _preflight_replace(
    guild: discord.Guild,
    *,
    bot_user_id: int,
    bot_display_name: str,
    effective_permissions: discord.Permissions,
) -> discord.Role:
    top_role = await _resolve_bot_top_role(guild, bot_user_id, bot_display_name)

    if not (
        effective_permissions.administrator
        or (effective_permissions.manage_channels and effective_permissions.manage_roles)
    ):
        raise RuntimeError(
            "Purple Team needs Administrator, or both Manage Channels and Manage Roles, "
            "before it can replace the server layout."
        )

    fresh_roles = await _fresh_roles(guild)
    blocked_roles = [
        role.name
        for role in fresh_roles
        if role.id != guild.id
        and not role.managed
        and role.id != top_role.id
        and role.position >= top_role.position
    ]
    if blocked_roles:
        preview = ", ".join(blocked_roles[:8])
        extra = " ..." if len(blocked_roles) > 8 else ""
        raise RuntimeError(
            "Purple Team cannot delete one or more existing roles because they are at or above "
            f"its control role ({top_role.name}): {preview}{extra}. "
            "Move the Demon Scope role above them first."
        )

    return top_role


async def _wipe_existing_layout(guild: discord.Guild, bot_role: discord.Role) -> dict[str, int]:
    deleted_channels = 0
    deleted_categories = 0
    deleted_roles = 0

    try:
        fresh_channels = await _fresh_channels(guild)
    except Exception as exc:
        raise RuntimeError(f"wipe-channels: could not fetch current channels: {exc}") from exc

    ordinary_channels = [
        channel for channel in fresh_channels
        if not isinstance(channel, discord.CategoryChannel)
    ]
    categories = [
        channel for channel in fresh_channels
        if isinstance(channel, discord.CategoryChannel)
    ]

    for channel in ordinary_channels:
        try:
            await channel.delete(reason="Purple Team J2 full server replacement")
            deleted_channels += 1
        except discord.NotFound:
            continue
        except Exception as exc:
            raise RuntimeError(
                f"wipe-channels: failed deleting {channel.name!r} ({channel.id}): {exc}"
            ) from exc

    # Re-fetch before categories so we do not act on stale objects after deleting children.
    try:
        fresh_channels = await _fresh_channels(guild)
    except Exception as exc:
        raise RuntimeError(f"wipe-categories: could not refresh channels: {exc}") from exc

    categories = [
        channel for channel in fresh_channels
        if isinstance(channel, discord.CategoryChannel)
    ]
    for category in categories:
        try:
            await category.delete(reason="Purple Team J2 full server replacement")
            deleted_categories += 1
        except discord.NotFound:
            continue
        except Exception as exc:
            raise RuntimeError(
                f"wipe-categories: failed deleting {category.name!r} ({category.id}): {exc}"
            ) from exc

    try:
        fresh_roles = await _fresh_roles(guild)
    except Exception as exc:
        raise RuntimeError(f"wipe-roles: could not fetch current roles: {exc}") from exc

    current_bot_role = next((role for role in fresh_roles if role.id == bot_role.id), None)
    if current_bot_role is None:
        raise RuntimeError("wipe-roles: Purple Team control role disappeared before role cleanup.")

    for role in sorted(fresh_roles, key=lambda item: item.position, reverse=True):
        if role.id == guild.id or role.managed or role.id == current_bot_role.id:
            continue
        if role.position >= current_bot_role.position:
            raise RuntimeError(
                f"wipe-roles: role {role.name!r} is at or above Purple Team and cannot be deleted."
            )
        try:
            await role.delete(reason="Purple Team J2 full server replacement")
            deleted_roles += 1
        except discord.NotFound:
            continue
        except Exception as exc:
            raise RuntimeError(
                f"wipe-roles: failed deleting {role.name!r} ({role.id}): {exc}"
            ) from exc

    return {
        "channels_deleted": deleted_channels,
        "categories_deleted": deleted_categories,
        "roles_deleted": deleted_roles,
    }


async def _ensure_roles(
    guild: discord.Guild,
    definitions: list[dict[str, Any]],
    bot_role: discord.Role,
) -> dict[str, discord.Role]:
    try:
        fresh_roles = await _fresh_roles(guild)
    except Exception as exc:
        raise RuntimeError(f"create-roles: could not fetch roles: {exc}") from exc

    role_map: dict[str, discord.Role] = {role.name: role for role in fresh_roles}

    for item in reversed(definitions):
        name = item["name"]
        role = role_map.get(name)
        perms = _permissions(item.get("permissions", []))
        colour = _colour(item.get("colour"))
        try:
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
        except Exception as exc:
            raise RuntimeError(f"create-roles: failed on role {name!r}: {exc}") from exc

    fresh_roles = await _fresh_roles(guild)
    current_bot_role = next((role for role in fresh_roles if role.id == bot_role.id), None)
    if current_bot_role is None:
        raise RuntimeError("create-roles: Purple Team's control role disappeared while building the hierarchy.")

    # Refresh newly created role objects before reordering them.
    role_map = {role.name: role for role in fresh_roles}
    ceiling = current_bot_role.position - 1
    for index in range(len(definitions) - 1, -1, -1):
        name = definitions[index]["name"]
        role = role_map.get(name)
        if role is None:
            raise RuntimeError(f"create-roles: newly created role {name!r} could not be re-fetched.")
        target_position = max(1, ceiling - index)
        if role.position != target_position:
            try:
                await role.edit(position=target_position, reason="Purple Team J2 role hierarchy")
            except Exception as exc:
                raise RuntimeError(f"create-roles: failed positioning {name!r}: {exc}") from exc

    # Return one last fresh map because role edits can change object positions.
    return {role.name: role for role in await _fresh_roles(guild)}


def _build_overwrites(
    guild: discord.Guild,
    roles: list[discord.Role],
    role_map: dict[str, discord.Role],
    spec: dict[str, Any] | None,
    *,
    base: dict[discord.abc.Snowflake, discord.PermissionOverwrite] | None = None,
) -> dict[discord.abc.Snowflake, discord.PermissionOverwrite]:
    result = dict(base or {})
    everyone = _everyone_role(guild, roles)

    for target, overwrite_spec in (spec or {}).items():
        if target == "@everyone":
            result[everyone] = _overwrite_from_spec(overwrite_spec)
            continue
        role = role_map.get(target)
        if role is None:
            raise RuntimeError(f"Template references missing role {target!r} in permission overwrites.")
        result[role] = _overwrite_from_spec(overwrite_spec)
    return result


async def _find_category(guild: discord.Guild, name: str) -> discord.CategoryChannel | None:
    channels = await _fresh_channels(guild)
    return next(
        (
            channel for channel in channels
            if isinstance(channel, discord.CategoryChannel) and channel.name == name
        ),
        None,
    )


async def _ensure_category(
    guild: discord.Guild,
    name: str,
    overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite],
) -> tuple[discord.CategoryChannel, bool]:
    existing = await _find_category(guild, name)
    try:
        if existing is not None:
            await existing.edit(overwrites=overwrites, reason="Purple Team private J2 template sync")
            return existing, False
        category = await guild.create_category(
            name=name,
            overwrites=overwrites,
            reason="Purple Team private J2 template install",
        )
        return category, True
    except Exception as exc:
        raise RuntimeError(f"create-categories: failed on category {name!r}: {exc}") from exc


async def _ensure_channel(
    guild: discord.Guild,
    category: discord.CategoryChannel,
    item: dict[str, Any],
    overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite],
) -> tuple[discord.abc.GuildChannel, bool]:
    name = item["name"]
    kind = item.get("type", "text")
    topic = item.get("topic")

    fresh_channels = await _fresh_channels(guild)
    existing = next(
        (
            channel for channel in fresh_channels
            if channel.name == name and getattr(channel, "category_id", None) == category.id
        ),
        None,
    )

    try:
        if existing is not None:
            kwargs: dict[str, Any] = {
                "overwrites": overwrites,
                "reason": "Purple Team private J2 template sync",
            }
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
    except Exception as exc:
        raise RuntimeError(f"create-channels: failed on channel {name!r}: {exc}") from exc


async def install_private_template_v2(
    guild: discord.Guild,
    *,
    bot_user_id: int,
    bot_display_name: str,
    effective_permissions: discord.Permissions,
) -> dict[str, int]:
    data = _load_template()
    bot_role = await _preflight_replace(
        guild,
        bot_user_id=bot_user_id,
        bot_display_name=bot_display_name,
        effective_permissions=effective_permissions,
    )

    wipe_stats = await _wipe_existing_layout(guild, bot_role)
    bot_role = await _resolve_bot_top_role(guild, bot_user_id, bot_display_name)
    role_map = await _ensure_roles(guild, data["roles"], bot_role)

    created_categories = 0
    created_channels = 0

    for category_index, category_spec in enumerate(data["categories"]):
        roles = await _fresh_roles(guild)
        category_overwrites = _build_overwrites(
            guild,
            roles,
            role_map,
            category_spec.get("overwrites"),
        )
        category, was_created = await _ensure_category(
            guild,
            category_spec["name"],
            category_overwrites,
        )
        created_categories += int(was_created)
        try:
            await category.edit(position=category_index, reason="Purple Team J2 category order")
        except Exception as exc:
            raise RuntimeError(
                f"create-categories: failed positioning {category_spec['name']!r}: {exc}"
            ) from exc

        for channel_index, channel_spec in enumerate(category_spec.get("channels", [])):
            roles = await _fresh_roles(guild)
            role_map = {role.name: role for role in roles}
            channel_overwrites = _build_overwrites(
                guild,
                roles,
                role_map,
                channel_spec.get("overwrites"),
                base=category_overwrites,
            )
            channel, was_created = await _ensure_channel(
                guild,
                category,
                channel_spec,
                channel_overwrites,
            )
            created_channels += int(was_created)
            try:
                await channel.edit(position=channel_index, reason="Purple Team J2 channel order")
            except Exception as exc:
                raise RuntimeError(
                    f"create-channels: failed positioning {channel_spec['name']!r}: {exc}"
                ) from exc

    roles = await _fresh_roles(guild)
    role_map = {role.name: role for role in roles}
    root_role = role_map.get(data.get("root_role", "root"))
    if root_role is not None:
        try:
            owner_member = await guild.fetch_member(guild.owner_id)
            await owner_member.add_roles(root_role, reason="Purple Team J2 owner/root mapping")
        except discord.HTTPException:
            pass

    return {
        "roles": len(data["roles"]),
        "categories_created": created_categories,
        "channels_created": created_channels,
        **wipe_stats,
    }


def register_owner_template_commands_v2(bot: discord.Client) -> None:
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
                    "This permanently deletes all deletable existing channels, categories, messages, and normal roles, then rebuilds the server from the private J2 blueprint. Discord-managed/bot/integration roles and `@everyone` remain.\n\nRun `/owner template-install confirm:REPLACE` to proceed.",
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

        bot_display_name = interaction.client.user.display_name
        try:
            await _preflight_replace(
                interaction.guild,
                bot_user_id=interaction.client.user.id,
                bot_display_name=bot_display_name,
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
                "Preflight passed. Purple Team is deleting the existing layout and rebuilding the server from fresh Discord data. This channel may disappear. I will DM you when the operation finishes.",
                kind="warning",
            ),
            ephemeral=True,
        )

        try:
            result = await install_private_template_v2(
                interaction.guild,
                bot_user_id=interaction.client.user.id,
                bot_display_name=bot_display_name,
                effective_permissions=interaction.app_permissions,
            )
            description = (
                "The J2 server replacement completed successfully.\n\n"
                f"Deleted: **{result['channels_deleted']}** channels, **{result['categories_deleted']}** categories, "
                f"**{result['roles_deleted']}** normal roles.\n"
                f"Created: **{result['roles']}** template roles, **{result['categories_created']}** categories, "
                f"**{result['channels_created']}** channels."
            )
            await _dm_result(interaction.user, "J2 Replacement Complete", description, kind="success")
        except Exception as exc:
            await _dm_result(
                interaction.user,
                "J2 Replacement Failed",
                f"The replacement encountered an error after starting.\n\n```text\n{str(exc)[:1800]}\n```",
                kind="error",
            )

    bot.tree.add_command(owner)
