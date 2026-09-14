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


async def _resolve_bot_member_and_top_role(
    guild: discord.Guild,
    bot_user_id: int,
) -> tuple[discord.Member, discord.Role]:
    """Fetch the bot member from Discord and resolve its real highest assigned role."""
    member = guild.get_member(bot_user_id)
    if member is None or len(member.roles) <= 1:
        member = await guild.fetch_member(bot_user_id)

    assigned_roles = [role for role in member.roles if role != guild.default_role]
    if not assigned_roles:
        raise RuntimeError(
            "Purple Team has no assigned Discord role above @everyone. "
            "Give the bot a role with Administrator and place it near the top of the role list."
        )

    return member, max(assigned_roles, key=lambda role: role.position)


async def _preflight_replace(
    guild: discord.Guild,
    template: dict,
    *,
    bot_user_id: int,
    effective_permissions: discord.Permissions,
) -> discord.Role:
    _, top_role = await _resolve_bot_member_and_top_role(guild, bot_user_id)

    if not (
        effective_permissions.administrator
        or (effective_permissions.manage_channels and effective_permissions.manage_roles)
    ):
        raise RuntimeError(
            "Purple Team needs Administrator, or both Manage Channels and Manage Roles, "
            "before it can replace the server layout."
        )

    blocked_roles = [
        role.name
        for role in guild.roles
        if role != guild.default_role
        and not role.managed
        and role.position >= top_role.position
    ]
    if blocked_roles:
        preview = ", ".join(blocked_roles[:8])
        extra = " ..." if len(blocked_roles) > 8 else ""
        raise RuntimeError(
            "Purple Team cannot delete one or more existing roles because they are at or above "
            f"its highest assigned role ({top_role.name}): {preview}{extra}. "
            "Move Purple Team's highest role above them first."
        )

    role_count = len(template.get("roles", []))
    if top_role.position - 1 < role_count:
        raise RuntimeError(
            f"Purple Team's highest role ({top_role.name}) is not high enough to place all "
            f"{role_count} J2 roles beneath it. Move the Purple Team role higher and try again."
        )

    return top_role


async def install_private_template_v2(
    guild: discord.Guild,
    *,
    bot_user_id: int,
    effective_permissions: discord.Permissions,
) -> dict[str, int]:
    data = _load_template()
    bot_role = await _preflight_replace(
        guild,
        data,
        bot_user_id=bot_user_id,
        effective_permissions=effective_permissions,
    )

    wipe_stats = await _wipe_existing_layout(guild, bot_role=bot_role)
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

        try:
            data = _load_template()
            await _preflight_replace(
                interaction.guild,
                data,
                bot_user_id=interaction.client.user.id,
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
