import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int = 0) -> int:
    value = os.getenv(name, "").strip()
    return int(value) if value else default


@dataclass(frozen=True)
class Settings:
    discord_token: str = os.getenv("DISCORD_TOKEN", "").strip()
    discord_guild_id: int = _int("DISCORD_GUILD_ID")
    bot_owner_id: int = _int("BOT_OWNER_ID")
    database_path: str = os.getenv("DATABASE_PATH", "purple_team.db")
    max_active_scans: int = max(1, _int("MAX_ACTIVE_SCANS", 1))
    scan_timeout_seconds: int = max(20, _int("SCAN_TIMEOUT_SECONDS", 90))
    http_timeout_seconds: int = max(5, _int("HTTP_TIMEOUT_SECONDS", 12))
    user_agent: str = os.getenv("USER_AGENT", "PurpleTeamBot/0.1")


settings = Settings()
