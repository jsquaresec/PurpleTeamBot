import asyncio

from bot.client import PurpleTeamBot
from bot.extra_commands import register_extra_commands


async def main() -> None:
    bot = PurpleTeamBot()
    register_extra_commands(bot)
    await bot.start_bot()


if __name__ == "__main__":
    asyncio.run(main())
