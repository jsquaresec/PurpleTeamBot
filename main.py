import asyncio

import discord

from bot.client import PurpleTeamBot
from bot.extra_commands import register_extra_commands
from bot.free_person_commands import register_free_person_commands
from bot.personpages_person_commands import register_personpages_person_commands
from bot.provider_commands import register_provider_commands
from bot.security_commands import register_security_commands
from bot.you_person_commands import register_you_person_commands
from core.config import settings
from storage.db import init_db


class FreeStackPurpleTeamBot(PurpleTeamBot):
    async def setup_hook(self) -> None:
        await init_db()
        self._register_commands()

        # Replace the Enformion-backed person/reverse/status surfaces with the
        # free-first stack before Discord command sync occurs.
        for name in ("person", "reverse", "status"):
            self.tree.remove_command(name)

        register_free_person_commands(self)
        register_personpages_person_commands(self)
        register_you_person_commands(self)

        if settings.discord_guild_id:
            guild = discord.Object(id=settings.discord_guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()


async def main() -> None:
    bot = FreeStackPurpleTeamBot()
    register_extra_commands(bot)
    register_provider_commands(bot)
    register_security_commands(bot)
    await bot.start_bot()


if __name__ == "__main__":
    asyncio.run(main())
