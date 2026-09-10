import asyncio

from bot.client import PurpleTeamBot
from bot.extra_commands import register_extra_commands
from bot.provider_commands import register_provider_commands
from bot.security_commands import register_security_commands


async def main() -> None:
    bot = PurpleTeamBot()
    register_extra_commands(bot)
    register_provider_commands(bot)
    register_security_commands(bot)
    await bot.start_bot()


if __name__ == "__main__":
    asyncio.run(main())
