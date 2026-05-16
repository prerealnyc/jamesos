from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

import asyncpg
from pgvector.asyncpg import register_vector

from .config import settings

_pool: asyncpg.Pool | None = None


async def init_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            settings.database_url,
            min_size=1,
            max_size=10,
            init=_init_connection,
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def _init_connection(conn: asyncpg.Connection) -> None:
    await register_vector(conn)


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialized; call init_pool() first")
    return _pool


@asynccontextmanager
async def acquire(tenant_id: UUID | None = None) -> AsyncIterator[asyncpg.Connection]:
    """Acquire a connection with the per-request tenant set on it.

    RLS policies read app.current_tenant; setting it here ensures every query
    on this connection is automatically scoped to the right tenant.

    Wraps the use in a transaction so set_config(..., is_local=true) persists
    across the contained statements and is automatically reset on release.
    """
    tenant = tenant_id or settings.default_tenant_id
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.current_tenant', $1, true)", str(tenant)
            )
            yield conn
