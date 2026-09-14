from __future__ import annotations

import discord
from discord import app_commands

from bot.csec_access import csec_access
from bot.ui import make_embed
from core.config import settings


SECTION_SPEC = [
    (
        "╭── ALMIGHTY PURPLE // COMMAND CENTER ──╮",
        [
            ("🟣・command-center", "Private Almighty Purple coordination and command discussion."),
            ("🤖・csec-commands", "Dedicated channel for Purple Team CSEC bot commands."),
            ("📡・live-intel", "Live security intelligence, indicators, and operational updates."),
            ("🎯・authorized-targets", "Authorized scope, approved targets, and assessment coordination."),
        ],
    ),
    (
        "╭── ALMIGHTY PURPLE // OPERATIONS ──╮",
        [
            ("⚔️・red-blue-collab", "Red and Blue Team collaboration inside the Almighty Purple operation space."),
            ("🧪・assessments", "Authorized assessments, validation work, and technical testing notes."),
            ("🔎・investigations", "Security investigations, evidence correlation, and research threads."),
            ("🚨・active-incidents", "Active security incidents, response coordination, and containment tracking."),
        ],
    ),
    (
        "╭── ALMIGHTY PURPLE // ARCHIVE ──╮",
        [
            ("📁・findings", "Validated findings, observations, and technical evidence."),
            ("📝・reports", "Finished and in-progress security reports."),
            ("🧠・tradecraft", "Purple Team procedures, detection ideas, techniques, and lessons learned."),
            ("🗃️・resources", "Private Almighty Purple references, tooling notes, and operational resources."),
        ],
    ),
]


async def _owner_only(interaction: discord.Interaction) -> bool:
    if settings.bot_owner_id and interaction.user.id == settings.bot_owner_id:
        return True
    await interaction.response.send_message(
        embed=make_embed(
            "Access Denied",
            "Only the configured Purple Team bot owner can load or repair the Almighty Purple section.",
            kind="error",
        ),
        ephemeral=True,
    )
    return False


async def _resolve_access_role(guild: discord.Guild) -> tuple[discord.Role, discord.Role]:
    roles = await guild.fetch_roles()
    everyone = next((role for role in roles if role.id == guild.id), None)
    wanted = csec_access.role_name.casefold()
    access_role = next((role for role in roles if role.name.casefold() == wanted), None)

    if everyone is None:
        raise RuntimeError("Discord did not return the @everyone role.")
    if access_role is None:
        raise RuntimeError(
            f"The linked CSEC role `{csec_access.role_name}` does not exist. "
            "Set it first with `/csec-config role`."
        )
    return everyone, access_role


def _private_overwrites(
    everyone: discord.Role,
    access_role: discord.Role,
) -> dict[discord.abc.Snowflake, discord.PermissionOverwrite]:
    return {
        everyone: discord.PermissionOverwrite(view_channel=False),
        access_role: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            add_reactions=True,
            attach_files=True,
            embed_links=True,
            use_application_commands=True,
            connect=True,
            speak=True,
        ),
    }


async def load_almighty_purple_section(guild: discord.Guild) -> dict[str, int]:
    everyone, access_role = await _resolve_access_role(guild)
    overwrites = _private_overwrites(everyone, access_role)

    fetched = await guild.fetch_channels()
    categories_by_name = {
        channel.name: channel
        for channel in fetched
        if isinstance(channel, discord.CategoryChannel)
    }

    created_categories = 0
    created_channels = 0
    repaired_categories = 0
    repaired_channels = 0
    csec_channel_ids: list[int] = []

    for category_name, channels in SECTION_SPEC:
        category = categories_by_name.get(category_name)
        if category is None:
            category = await guild.create_category(
                category_name,
                overwrites=overwrites,
                reason="Load private Almighty Purple section",
            )
            created_categories += 1
        else:
            await category.edit(
                overwrites=overwrites,
                reason="Repair Almighty Purple private permissions",
            )
            repaired_categories += 1

        current = await guild.fetch_channels()
        text_by_name = {
            channel.name: channel
            for channel in current
            if isinstance(channel, discord.TextChannel) and channel.category_id == category.id
        }

        for channel_name, topic in channels:
            channel = text_by_name.get(channel_name)
            if channel is None:
                channel = await guild.create_text_channel(
                    channel_name,
                    category=category,
                    topic=topic,
                    overwrites=overwrites,
                    reason="Load private Almighty Purple section",
                )
                created_channels += 1
            else:
                await channel.edit(
                    overwrites=overwrites,
                    topic=topic,
                    reason="Repair Almighty Purple private permissions",
                )
                repaired_channels += 1

            if channel_name == "🤖・csec-commands":
                csec_channel_ids.append(channel.id)

    # Automatically authorize the dedicated command channel for CSEC commands.
    for channel_id in csec_channel_ids:
        csec_access.add_channel(channel_id)

    return {
        "categories_created": created_categories,
        "channels_created": created_channels,
        "categories_repaired": repaired_categories,
        "channels_repaired": repaired_channels,
    }


def register_almighty_purple_loader(bot: discord.Client) -> None:
    group = app_commands.Group(
        name="almighty-purple",
        description="Manage the private Almighty Purple section",
        default_permissions=discord.Permissions(administrator=True),
    )

    @group.command(name="load", description="Create or repair the private Almighty Purple section")
    @app_commands.guild_only()
    async def load(interaction: discord.Interaction):
        if not await _owner_only(interaction):
            return
        if interaction.guild is None:
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            stats = await load_almighty_purple_section(interaction.guild)
        except Exception as exc:
            await interaction.followup.send(
                embed=make_embed(
                    "Almighty Purple Loader Failed",
                    f"```text\n{str(exc)[:1500]}\n```",
                    kind="error",
                ),
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            embed=make_embed(
                "Almighty Purple Section Online",
                (
                    f"Access role: **{csec_access.role_name}**\n\n"
                    f"Categories created: **{stats['categories_created']}**\n"
                    f"Channels created: **{stats['channels_created']}**\n"
                    f"Categories repaired: **{stats['categories_repaired']}**\n"
                    f"Channels repaired: **{stats['channels_repaired']}**\n\n"
                    "`🤖・csec-commands` was automatically added to the CSEC command allowlist."
                ),
                kind="success",
            ),
            ephemeral=True,
        )

    bot.tree.add_command(group)
