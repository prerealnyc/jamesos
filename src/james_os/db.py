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
        ssl = None if settings.db_ssl == "disable" else settings.db_ssl
        _pool = await asyncpg.create_pool(
            settings.database_url,
            min_size=1,
            max_size=10,
            init=_init_connection,
            ssl=ssl,
            # 0 disables prepared statements — required behind a
            # transaction-mode connection pooler (e.g. Supabase :6543).
            statement_cache_size=settings.db_statement_cache_size,
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


# Per-request tenant binding. The auth middleware sets this from the
# session cookie before the route runs. Routes that take a tenant_id
# explicitly still win (passed-in arg shadows the contextvar) — used
# by background tasks that don't have an HTTP request scope.
import contextvars
_request_tenant: contextvars.ContextVar[UUID | None] = contextvars.ContextVar(
    "request_tenant", default=None,
)


def set_request_tenant(tenant_id: UUID | None) -> None:
    """Called by the auth middleware on every authenticated request."""
    _request_tenant.set(tenant_id)


@asynccontextmanager
async def acquire(tenant_id: UUID | None = None) -> AsyncIterator[asyncpg.Connection]:
    """Acquire a connection with the per-request tenant set on it.

    Resolution order: explicit arg → request contextvar (set by auth
    middleware) → settings.default_tenant_id. The default exists for
    background tasks / migrations / startup hooks that legitimately
    run without an authenticated request.

    RLS policies read app.current_tenant; setting it here ensures every query
    on this connection is automatically scoped to the right tenant.

    Wraps the use in a transaction so set_config(..., is_local=true) persists
    across the contained statements and is automatically reset on release.
    """
    tenant = tenant_id or _request_tenant.get() or settings.default_tenant_id
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.current_tenant', $1, true)", str(tenant)
            )
            yield conn
