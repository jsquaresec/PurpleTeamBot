from __future__ import annotations

import json
from pathlib import Path

import discord
from discord import app_commands

from bot.ui import make_embed
from core.config import settings


ACCESS_PATH = Path("private/csec_access.json")
DEFAULT_ROLE_NAME = "Almighty Purple"


class CSECAccessStore:
    def __init__(self) -> None:
        self._data = {
            "role_name": DEFAULT_ROLE_NAME,
            "channel_ids": sorted(settings.discord_allowed_channel_ids),
        }
        self.load()

    def load(self) -> None:
        try:
            if ACCESS_PATH.exists():
                raw = json.loads(ACCESS_PATH.read_text(encoding="utf-8"))
                role_name = str(raw.get("role_name") or DEFAULT_ROLE_NAME).strip()
                channels = [int(value) for value in raw.get("channel_ids", [])]
                self._data = {
                    "role_name": role_name or DEFAULT_ROLE_NAME,
                    "channel_ids": sorted(set(channels)),
                }
        except Exception:
            # Keep the last known/default configuration if the file is malformed.
            pass

    def save(self) -> None:
        ACCESS_PATH.parent.mkdir(parents=True, exist_ok=True)
        ACCESS_PATH.write_text(json.dumps(self._data, indent=2), encoding="utf-8")

    @property
    def role_name(self) -> str:
        return str(self._data["role_name"])

    @property
    def channel_ids(self) -> frozenset[int]:
        return frozenset(int(value) for value in self._data["channel_ids"])

    def set_role(self, role_name: str) -> None:
        self._data["role_name"] = role_name.strip() or DEFAULT_ROLE_NAME
        self.save()

    def add_channel(self, channel_id: int) -> bool:
        values = set(self.channel_ids)
        before = len(values)
        values.add(int(channel_id))
        self._data["channel_ids"] = sorted(values)
        self.save()
        return len(values) != before

    def remove_channel(self, channel_id: int) -> bool:
        values = set(self.channel_ids)
        if int(channel_id) not in values:
            return False
        values.remove(int(channel_id))
        self._data["channel_ids"] = sorted(values)
        self.save()
        return True


csec_access = CSECAccessStore()


def member_has_csec_role(member: discord.abc.User) -> bool:
    if member.id == settings.bot_owner_id:
        return True
    if not isinstance(member, discord.Member):
        return False
    wanted = csec_access.role_name.casefold()
    return any(role.name.casefold() == wanted for role in member.roles)


def register_csec_access_commands(bot: discord.Client) -> None:
    group = app_commands.Group(
        name="csec-config",
        description="Configure Purple Team CSEC access",
        default_permissions=discord.Permissions(administrator=True),
    )

    async def owner_only(interaction: discord.Interaction) -> bool:
        if settings.bot_owner_id and interaction.user.id == settings.bot_owner_id:
            return True
        await interaction.response.send_message(
            embed=make_embed(
                "Access Denied",
                "Only the configured Purple Team bot owner can change CSEC access settings.",
                kind="error",
            ),
            ephemeral=True,
        )
        return False

    @group.command(name="channel-add", description="Allow CSEC commands in a channel")
    @app_commands.guild_only()
    async def channel_add(interaction: discord.Interaction, channel: discord.TextChannel):
        if not await owner_only(interaction):
            return
        added = csec_access.add_channel(channel.id)
        await interaction.response.send_message(
            embed=make_embed(
                "CSEC Channel Added" if added else "CSEC Channel Already Allowed",
                f"{channel.mention} is {'now' if added else 'already'} an approved Purple Team CSEC command channel.",
                kind="success" if added else "info",
            ),
            ephemeral=True,
        )

    @group.command(name="channel-remove", description="Remove a CSEC command channel")
    @app_commands.guild_only()
    async def channel_remove(interaction: discord.Interaction, channel: discord.TextChannel):
        if not await owner_only(interaction):
            return
        removed = csec_access.remove_channel(channel.id)
        await interaction.response.send_message(
            embed=make_embed(
                "CSEC Channel Removed" if removed else "CSEC Channel Not Configured",
                f"{channel.mention} {'was removed from' if removed else 'was not in'} the CSEC command-channel list.",
                kind="success" if removed else "warning",
            ),
            ephemeral=True,
        )

    @group.command(name="channels", description="Show approved CSEC command channels")
    @app_commands.guild_only()
    async def channels(interaction: discord.Interaction):
        if not await owner_only(interaction):
            return
        ids = sorted(csec_access.channel_ids)
        lines = [f"• <#{channel_id}> (`{channel_id}`)" for channel_id in ids]
        await interaction.response.send_message(
            embed=make_embed(
                "Purple Team CSEC Channels",
                "\n".join(lines) if lines else "No CSEC command channels are currently configured.",
                kind="info",
            ),
            ephemeral=True,
        )

    @group.command(name="role", description="Set the role required for CSEC commands")
    @app_commands.guild_only()
    async def role(interaction: discord.Interaction, role: discord.Role):
        if not await owner_only(interaction):
            return
        csec_access.set_role(role.name)
        await interaction.response.send_message(
            embed=make_embed(
                "CSEC Access Role Updated",
                f"Purple Team CSEC commands now require {role.mention} (`{role.name}`).",
                kind="success",
            ),
            ephemeral=True,
        )

    bot.tree.add_command(group)
