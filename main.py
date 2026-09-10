import asyncio
from bot.client import PurpleTeamBot


async def main() -> None:
    bot = PurpleTeamBot()
    await bot.start_bot()


if __name__ == "__main__":
    asyncio.run(main())
