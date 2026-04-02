"""
PostgreSQL connection and schema init.
"""
import asyncpg
from pathlib import Path

# Lazy connection pool
_pool: asyncpg.Pool | None = None


async def get_pool(database_url: str) -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            database_url,
            min_size=1,
            max_size=10,
            command_timeout=60,
        )
    return _pool


async def init_schema(pool: asyncpg.Pool) -> None:
    """Apply schema.sql if present."""
    schema_path = Path(__file__).resolve().parent.parent / "schema.sql"
    if schema_path.exists():
        sql = schema_path.read_text()
        async with pool.acquire() as conn:
            await conn.execute(sql)


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
