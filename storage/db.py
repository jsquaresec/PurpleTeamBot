import aiosqlite
from core.config import settings
from core.targets import normalize_scope, normalize_target, target_matches_scope

SCHEMA = """
CREATE TABLE IF NOT EXISTS authorized_scope (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    target TEXT NOT NULL,
    added_by INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(guild_id, target)
);
CREATE TABLE IF NOT EXISTS scan_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    target TEXT NOT NULL,
    scan_type TEXT NOT NULL,
    status TEXT NOT NULL,
    summary TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


async def init_db() -> None:
    async with aiosqlite.connect(settings.database_path) as db:
        await db.executescript(SCHEMA)
        await db.commit()


async def add_scope(guild_id: int, target: str, added_by: int) -> bool:
    target = normalize_scope(target)
    try:
        async with aiosqlite.connect(settings.database_path) as db:
            await db.execute(
                "INSERT INTO authorized_scope (guild_id, target, added_by) VALUES (?, ?, ?)",
                (guild_id, target, added_by),
            )
            await db.commit()
        return True
    except aiosqlite.IntegrityError:
        return False


async def remove_scope(guild_id: int, target: str) -> bool:
    target = normalize_scope(target)
    async with aiosqlite.connect(settings.database_path) as db:
        cur = await db.execute(
            "DELETE FROM authorized_scope WHERE guild_id = ? AND target = ?",
            (guild_id, target),
        )
        await db.commit()
        return cur.rowcount > 0


async def list_scope(guild_id: int) -> list[str]:
    async with aiosqlite.connect(settings.database_path) as db:
        cur = await db.execute(
            "SELECT target FROM authorized_scope WHERE guild_id = ? ORDER BY target",
            (guild_id,),
        )
        return [row[0] for row in await cur.fetchall()]


async def target_in_scope(guild_id: int, target: str) -> bool:
    target = normalize_target(target)
    for scope in await list_scope(guild_id):
        if target_matches_scope(target, scope):
            return True
    return False


async def record_scan(guild_id: int, user_id: int, target: str, scan_type: str, status: str, summary: str = "") -> None:
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            "INSERT INTO scan_history (guild_id, user_id, target, scan_type, status, summary) VALUES (?, ?, ?, ?, ?, ?)",
            (guild_id, user_id, target, scan_type, status, summary[:2000]),
        )
        await db.commit()
