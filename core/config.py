import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


DEFAULT_ALLOWED_CHANNEL_IDS = frozenset(
    {
        1547700463611289640,
        1548426555934376096,
        1548416136436129983,
        1548426462120386672,
        1548437367331881030,
    }
)
DEFAULT_SNAPSHOT_CHANNEL_ID = 1547422897054818355


def _int(name: str, default: int = 0) -> int:
    value = os.getenv(name, "").strip()
    return int(value) if value else default


def _int_set(name: str, default: frozenset[int] = frozenset()) -> frozenset[int]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default

    values: set[int] = set()
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            values.add(int(item))
        except ValueError as exc:
            raise RuntimeError(f"{name} contains an invalid Discord ID: {item!r}") from exc

    return frozenset(values)


@dataclass(frozen=True)
class Settings:
    discord_token: str = os.getenv("DISCORD_TOKEN", "").strip()
    discord_guild_id: int = _int("DISCORD_GUILD_ID")
    discord_allowed_channel_ids: frozenset[int] = _int_set(
        "DISCORD_ALLOWED_CHANNEL_IDS",
        DEFAULT_ALLOWED_CHANNEL_IDS,
    )
    discord_snapshot_channel_id: int = _int(
        "DISCORD_SNAPSHOT_CHANNEL_ID",
        DEFAULT_SNAPSHOT_CHANNEL_ID,
    )
    snapshot_timezone: str = os.getenv("SNAPSHOT_TIMEZONE", "America/Chicago").strip() or "America/Chicago"
    snapshot_startup_delay_seconds: int = max(5, _int("SNAPSHOT_STARTUP_DELAY_SECONDS", 15))
    snapshot_state_path: str = os.getenv("SNAPSHOT_STATE_PATH", ".purple_team_snapshot_date").strip() or ".purple_team_snapshot_date"
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
