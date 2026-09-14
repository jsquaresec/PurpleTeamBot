import discord

from bot.csec_access import csec_access, member_has_csec_role
from bot.ui import make_embed
from core.config import settings


async def _deny(interaction: discord.Interaction, title: str, message: str) -> None:
    embed = make_embed(title, message, kind="error")
    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=True)


def install_channel_guard(bot: discord.Client) -> None:
    """Central authorization boundary for Purple Team application commands.

    The configured owner may perform setup/recovery anywhere in the authorized
    guild. Normal CSEC commands require the configured access role and an approved
    CSEC command channel. Channel and role settings are read dynamically from the
    persistent CSEC access store, so changes do not require editing .env.
    """

    async def interaction_check(interaction: discord.Interaction) -> bool:
        if interaction.guild_id is None:
            await _deny(
                interaction,
                "Server Only",
                "Purple Team commands can only be used inside the configured Discord server.",
            )
            return False

        if settings.discord_guild_id and interaction.guild_id != settings.discord_guild_id:
            await _deny(
                interaction,
                "Unauthorized Server",
                "Purple Team is not authorized to operate in this Discord server.",
            )
            return False

        # Owner recovery/setup remains available even if channel IDs or access-role
        # configuration are being changed.
        if settings.bot_owner_id and interaction.user.id == settings.bot_owner_id:
            return True

        command_name = interaction.command.qualified_name if interaction.command else ""

        # CSEC configuration is owner-only at the command implementation itself.
        # Let it reach that handler so it can return the dedicated access message.
        if command_name.startswith("csec-config "):
            return True

        if not member_has_csec_role(interaction.user):
            await _deny(
                interaction,
                "Almighty Purple Required",
                f"Purple Team CSEC commands are restricted to members with the `{csec_access.role_name}` role.",
            )
            return False

        allowed_channels = csec_access.channel_ids
        if not allowed_channels:
            await _deny(
                interaction,
                "CSEC Channel Guard Not Configured",
                "No approved Purple Team CSEC command channels are configured. Ask the bot owner to use `/csec-config channel-add`.",
            )
            return False

        if interaction.channel_id not in allowed_channels:
            await _deny(
                interaction,
                "Restricted CSEC Channel",
                "Purple Team CSEC commands can only be used in approved operational channels.",
            )
            return False

        return True

    bot.tree.interaction_check = interaction_check
