import json
from pathlib import Path
from typing import Any

import discord
from discord import app_commands

from bot.ui import make_embed
from core.config import settings


DEFAULT_TEMPLATE_PATH = Path("private/j2_server_template.json")


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


async def _ensure_roles(guild: discord.Guild, definitions: list[dict[str, Any]]) -> dict[str, discord.Role]:
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

    me = guild.me
    if me is None:
        raise RuntimeError("Purple Team member object is unavailable in this guild.")
    ceiling = me.top_role.position - 1
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


async def install_private_template(guild: discord.Guild) -> dict[str, int]:
    data = _load_template()
    role_map = await _ensure_roles(guild, data["roles"])
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
    }


def register_owner_template_commands(bot: discord.Client) -> None:
    owner = app_commands.Group(
        name="owner",
        description="Purple Team bot-owner operations",
        default_permissions=discord.Permissions(administrator=True),
    )

    @owner.command(name="template-install", description="Install or repair the private J2 Discord server template")
    @app_commands.guild_only()
    async def template_install(interaction: discord.Interaction, confirm: bool = False):
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
        if not confirm:
            await interaction.response.send_message(
                embed=make_embed(
                    "Private J2 Template",
                    "This synchronizes the Linux-style roles, matching categories/channels, private staff area, ordering, and permission overwrites.\n\nRun `/owner template-install confirm:true` to continue.",
                    kind="warning",
                ),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            result = await install_private_template(interaction.guild)
            panel = make_embed(
                "J2 Template Synchronized",
                "Purple Team finished applying the private server blueprint.",
                kind="success",
            )
            panel.add_field(name="Roles", value=str(result["roles"]), inline=True)
            panel.add_field(name="New Categories", value=str(result["categories_created"]), inline=True)
            panel.add_field(name="New Channels", value=str(result["channels_created"]), inline=True)
            panel.add_field(name="Safety", value="Existing matching roles/channels were synchronized instead of duplicated.", inline=False)
            await interaction.followup.send(embed=panel, ephemeral=True)
        except Exception as exc:
            await interaction.followup.send(
                embed=make_embed("Template Install Failed", f"```text\n{str(exc)[:1500]}\n```", kind="error"),
                ephemeral=True,
            )

    bot.tree.add_command(owner)
