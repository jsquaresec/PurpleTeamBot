import asyncio

import discord

from bot.channel_guard import install_channel_guard
from bot.client import PurpleTeamBot
from bot.cyberspace_community import install_cyberspace_community, register_cyberspace_community_commands
from bot.daily_snapshot import install_daily_snapshot, register_snapshot_command
from bot.extra_commands import register_extra_commands
from bot.free_person_commands import register_free_person_commands
from bot.personpages_person_commands import register_personpages_person_commands
from bot.provider_commands import register_provider_commands
from bot.security_commands import register_security_commands
import bot.server_template_v2 as server_template_v2
from bot.server_template_fast_builder import install_private_template_fast
from bot.server_template_role_order import ensure_roles_top_down
from bot.threaded_intel_commands import register_threaded_intel_commands
from bot.threaded_lookup_commands import register_threaded_lookup_commands
from bot.threaded_remaining_commands import register_threaded_remaining_commands
from bot.threaded_scan_osint_commands import register_threaded_scan_osint_commands
from bot.you_person_commands import register_you_person_commands
from core.config import settings
from storage.db import init_db


# Use the hardened role-ordering implementation and the streamlined full rebuild
# path for the private J2 installer. The command resolves these module attributes
# at runtime, so the replacements apply without changing the public command API.
server_template_v2._ensure_roles = ensure_roles_top_down
server_template_v2.install_private_template_v2 = install_private_template_fast
register_owner_template_commands_v2 = server_template_v2.register_owner_template_commands_v2


class FreeStackPurpleTeamBot(PurpleTeamBot):
    async def setup_hook(self) -> None:
        await init_db()
        self._register_commands()

        # Replace legacy person/reverse/status command surfaces before sync.
        for name in ("person", "reverse", "status"):
            self.tree.remove_command(name)

        register_free_person_commands(self)
        register_personpages_person_commands(self)
        register_you_person_commands(self)
        register_threaded_intel_commands(self)
        register_threaded_lookup_commands(self)
        register_threaded_scan_osint_commands(self)
        register_threaded_remaining_commands(self)

        if settings.discord_guild_id:
            guild = discord.Object(id=settings.discord_guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()


async def main() -> None:
    bot = FreeStackPurpleTeamBot()

    # Welcome/leave events require guild + member events. The privileged Members
    # intent must also be enabled for the application in Discord Developer Portal.
    bot.intents.guilds = True
    bot.intents.members = True

    install_channel_guard(bot)
    register_extra_commands(bot)
    register_provider_commands(bot)
    register_security_commands(bot)
    register_snapshot_command(bot)
    register_owner_template_commands_v2(bot)
    register_cyberspace_community_commands(bot)
    install_cyberspace_community(bot)
    install_daily_snapshot(bot)
    await bot.start_bot()


if __name__ == "__main__":
    asyncio.run(main())
