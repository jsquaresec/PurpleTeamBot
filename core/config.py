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
    database_url: str = os.getenv("DATABASE_URL", "").strip()
    max_active_scans: int = max(1, _int("MAX_ACTIVE_SCANS", 1))
    scan_timeout_seconds: int = max(20, _int("SCAN_TIMEOUT_SECONDS", 90))
    http_timeout_seconds: int = max(5, _int("HTTP_TIMEOUT_SECONDS", 12))
    user_agent: str = os.getenv("USER_AGENT", "PurpleTeamBot/0.1")

    enformion_ap_name: str = os.getenv("ENFORMION_AP_NAME", "").strip()
    enformion_ap_password: str = os.getenv("ENFORMION_AP_PASSWORD", "").strip()
    enformion_search_type: str = os.getenv("ENFORMION_SEARCH_TYPE", "Person").strip() or "Person"
    enformion_base_url: str = os.getenv(
        "ENFORMION_BASE_URL",
        "https://devapi.enformion.com/PersonSearch",
    ).strip()

    personpages_api_key: str = os.getenv("PERSONPAGES_API_KEY", "").strip()
    you_api_key: str = os.getenv("YOU_API_KEY", "").strip()

    virustotal_api_key: str = os.getenv("VIRUSTOTAL_API_KEY", "").strip()
    abuseipdb_api_key: str = os.getenv("ABUSEIPDB_API_KEY", "").strip()
    censys_pat: str = os.getenv("CENSYS_PAT", "").strip()
    urlscan_api_key: str = os.getenv("URLSCAN_API_KEY", "").strip()
    otx_api_key: str = os.getenv("OTX_API_KEY", "").strip()


settings = Settings()
