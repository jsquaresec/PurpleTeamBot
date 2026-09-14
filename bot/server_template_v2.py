import re

import discord
from discord import app_commands

from bot.server_template import (
    REPLACE_CONFIRMATION,
    _build_overwrites,
    _dm_result,
    _ensure_category,
    _ensure_channel,
    _ensure_roles,
    _load_template,
    _wipe_existing_layout,
)
from bot.ui import make_embed
from core.config import settings


ROLE_NAME_FALLBACK = "[ Demon Scope ]"


def _normalize_role_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


async def _resolve_bot_top_role(
    guild: discord.Guild,
    bot_user_id: int,
    bot_display_name: str = "",
) -> discord.Role:
    """Resolve Purple Team's highest controlling role from fresh Discord role data.

    This intentionally avoids member-cache dependence because Purple Team currently
    runs with Intents.none().
    """
    roles = await guild.fetch_roles()

    # Best case: Discord marks the managed integration role with this bot's ID.
    tagged = []
    for role in roles:
        if not role.managed:
            continue
        tags = getattr(role, "tags", None)
        if tags is not None and getattr(tags, "bot_id", None) == bot_user_id:
            tagged.append(role)
    if tagged:
        return max(tagged, key=lambda role: role.position)

    # This deployment uses a manually positioned Administrator role named
    # "[ Demon Scope ]". Resolve it directly from fresh guild role data.
    exact = discord.utils.get(roles, name=ROLE_NAME_FALLBACK)
    if exact is not None:
        return exact

    # Tolerate cosmetic brackets/spacing and a future rename matching the bot's
    # display name, while still requiring an Administrator-capable role.
    wanted_names = {_normalize_role_name(ROLE_NAME_FALLBACK)}
    if bot_display_name:
        wanted_names.add(_normalize_role_name(bot_display_name))

    candidates = [
        role
        for role in roles
        if role != guild.default_role
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
    top_role = await _resolve_bot_top_role(
        guild,
        bot_user_id,
        bot_display_name,
    )

    if not (
        effective_permissions.administrator
        or (effective_permissions.manage_channels and effective_permissions.manage_roles)
    ):
        raise RuntimeError(
            "Purple Team needs Administrator, or both Manage Channels and Manage Roles, "
            "before it can replace the server layout."
        )

    fresh_roles = await guild.fetch_roles()
    blocked_roles = [
        role.name
        for role in fresh_roles
        if role != guild.default_role
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

    wipe_stats = await _wipe_existing_layout(guild, bot_role=bot_role)

    # Role positions change as existing roles are deleted. Resolve the controlling
    # role again from Discord before creating/reordering the J2 hierarchy.
    bot_role = await _resolve_bot_top_role(
        guild,
        bot_user_id,
        bot_display_name,
    )
    role_map = await _ensure_roles(guild, data["roles"], bot_role=bot_role)

    created_categories = 0
    created_channels = 0

    for category_index, category_spec in enumerate(data["categories"]):
        category_overwrites = _build_overwrites(
            guild,
            role_map,
            category_spec.get("overwrites"),
        )
        category, was_created = await _ensure_category(
            guild,
            category_spec["name"],
            category_overwrites,
        )
        created_categories += int(was_created)
        await category.edit(position=category_index, reason="Purple Team J2 category order")

        for channel_index, channel_spec in enumerate(category_spec.get("channels", [])):
            channel_overwrites = _build_overwrites(
                guild,
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
                embed=make_embed(
                    "Access Denied",
                    "This command is restricted to the configured Purple Team bot owner.",
                    kind="error",
                ),
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
                embed=make_embed(
                    "Unauthorized Server",
                    "The private J2 template can only be installed in the configured Purple Team guild.",
                    kind="error",
                ),
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
                embed=make_embed(
                    "Replacement Blocked",
                    "Purple Team has not finished identifying its Discord user yet. Try again in a few seconds.",
                    kind="error",
                ),
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
                embed=make_embed(
                    "Replacement Blocked",
                    f"```text\n{str(exc)[:1500]}\n```",
                    kind="error",
                ),
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
                f"The replacement encountered an error after starting.\n\n```text\n{str(exc)[:1500]}\n```",
                kind="error",
            )

    bot.tree.add_command(owner)
