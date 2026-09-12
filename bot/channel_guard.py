import discord

from bot.ui import make_embed
from core.config import settings


async def _deny(interaction: discord.Interaction, title: str, message: str) -> None:
    embed = make_embed(title, message, kind="error")
    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=True)


def install_channel_guard(bot: discord.Client) -> None:
    """Restrict all application commands to explicitly approved Discord channels.

    The guard fails closed: when no channel IDs are configured, commands are denied
    instead of silently falling back to unrestricted operation.
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

        allowed_channels = settings.discord_allowed_channel_ids
        if not allowed_channels:
            await _deny(
                interaction,
                "Channel Guard Not Configured",
                "No approved Purple Team channels are configured. Set `DISCORD_ALLOWED_CHANNEL_IDS` before using commands.",
            )
            return False

        if interaction.channel_id not in allowed_channels:
            await _deny(
                interaction,
                "Restricted Channel",
                "Purple Team commands can only be used in designated operational channels.",
            )
            return False

        return True

    # discord.py calls CommandTree.interaction_check before every application
    # command. Replacing it on this tree gives every current and future slash
    # command the same centralized channel authorization boundary.
    bot.tree.interaction_check = interaction_check
