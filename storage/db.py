import aiosqlite
import asyncpg

from core.config import settings
from core.targets import normalize_scope, normalize_target, target_matches_scope

SQLITE_SCHEMA = """
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
CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    target TEXT,
    details TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS authorized_scope (
    id BIGSERIAL PRIMARY KEY,
    guild_id BIGINT NOT NULL,
    target TEXT NOT NULL,
    added_by BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(guild_id, target)
);
CREATE TABLE IF NOT EXISTS scan_history (
    id BIGSERIAL PRIMARY KEY,
    guild_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    target TEXT NOT NULL,
    scan_type TEXT NOT NULL,
    status TEXT NOT NULL,
    summary TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS audit_events (
    id BIGSERIAL PRIMARY KEY,
    guild_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    action TEXT NOT NULL,
    target TEXT,
    details TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_scan_history_guild_id ON scan_history(guild_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_audit_events_guild_id ON audit_events(guild_id, id DESC);
"""

_pool: asyncpg.Pool | None = None


def using_postgres() -> bool:
    return bool(settings.database_url)


async def _pg_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=settings.database_url,
            min_size=1,
            max_size=3,
            command_timeout=20,
        )
    return _pool


async def init_db() -> None:
    if using_postgres():
        pool = await _pg_pool()
        async with pool.acquire() as conn:
            await conn.execute(POSTGRES_SCHEMA)
        return
    async with aiosqlite.connect(settings.database_path) as db:
        await db.executescript(SQLITE_SCHEMA)
        await db.commit()


async def add_scope(guild_id: int, target: str, added_by: int) -> bool:
    target = normalize_scope(target)
    if using_postgres():
        pool = await _pg_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                "INSERT INTO authorized_scope (guild_id, target, added_by) VALUES ($1, $2, $3) ON CONFLICT (guild_id, target) DO NOTHING",
                guild_id, target, added_by,
            )
            return result.endswith("1")
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
    if using_postgres():
        pool = await _pg_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM authorized_scope WHERE guild_id = $1 AND target = $2",
                guild_id, target,
            )
            return not result.endswith("0")
    async with aiosqlite.connect(settings.database_path) as db:
        cur = await db.execute(
            "DELETE FROM authorized_scope WHERE guild_id = ? AND target = ?",
            (guild_id, target),
        )
        await db.commit()
        return cur.rowcount > 0


async def list_scope(guild_id: int) -> list[str]:
    if using_postgres():
        pool = await _pg_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT target FROM authorized_scope WHERE guild_id = $1 ORDER BY target",
                guild_id,
            )
            return [str(row["target"]) for row in rows]
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
    summary = summary[:2000]
    if using_postgres():
        pool = await _pg_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO scan_history (guild_id, user_id, target, scan_type, status, summary) VALUES ($1, $2, $3, $4, $5, $6)",
                guild_id, user_id, target, scan_type, status, summary,
            )
        return
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            "INSERT INTO scan_history (guild_id, user_id, target, scan_type, status, summary) VALUES (?, ?, ?, ?, ?, ?)",
            (guild_id, user_id, target, scan_type, status, summary),
        )
        await db.commit()


async def record_audit(guild_id: int, user_id: int, action: str, target: str = "", details: str = "") -> None:
    action = action[:100]
    target = target[:300]
    details = details[:2000]
    if using_postgres():
        pool = await _pg_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO audit_events (guild_id, user_id, action, target, details) VALUES ($1, $2, $3, $4, $5)",
                guild_id, user_id, action, target, details,
            )
        return
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(
            "INSERT INTO audit_events (guild_id, user_id, action, target, details) VALUES (?, ?, ?, ?, ?)",
            (guild_id, user_id, action, target, details),
        )
        await db.commit()


async def recent_history(guild_id: int, limit: int = 10) -> list[tuple]:
    limit = max(1, min(limit, 25))
    if using_postgres():
        pool = await _pg_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT target, scan_type, status, created_at FROM scan_history WHERE guild_id = $1 ORDER BY id DESC LIMIT $2",
                guild_id, limit,
            )
            return [(row["target"], row["scan_type"], row["status"], row["created_at"].isoformat()) for row in rows]
    async with aiosqlite.connect(settings.database_path) as db:
        cur = await db.execute(
            "SELECT target, scan_type, status, created_at FROM scan_history WHERE guild_id = ? ORDER BY id DESC LIMIT ?",
            (guild_id, limit),
        )
        return await cur.fetchall()


async def database_status() -> dict[str, str]:
    if using_postgres():
        pool = await _pg_pool()
        async with pool.acquire() as conn:
            version = await conn.fetchval("SELECT version()")
        return {"backend": "PostgreSQL", "detail": str(version).split(",")[0]}
    async with aiosqlite.connect(settings.database_path) as db:
        cur = await db.execute("SELECT sqlite_version()")
        row = await cur.fetchone()
    return {"backend": "SQLite", "detail": f"SQLite {row[0] if row else 'unknown'}"}
