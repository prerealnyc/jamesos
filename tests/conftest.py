"""Test fixtures.

Tests run against the local Postgres started by docker-compose. Each test
gets a fresh pool bound to the test's event loop, and tables are truncated
between tests so order doesn't matter.
"""

import os

import pytest_asyncio

# Force stub providers regardless of .env so tests don't burn API quota.
os.environ.setdefault("EMBEDDING_PROVIDER", "stub")
os.environ.setdefault("LLM_PROVIDER", "stub")

from james_os import db as db_module  # noqa: E402
from james_os.db import acquire, close_pool, init_pool  # noqa: E402


@pytest_asyncio.fixture(autouse=True)
async def fresh_pool():
    # Reset the global so each test starts with its own pool bound to
    # this test's event loop. Avoids the classic asyncpg + session-scope
    # "Future attached to a different loop" error.
    db_module._pool = None
    await init_pool()
    async with acquire() as conn:
        await conn.execute(
            "TRUNCATE queries, outbox, actions, events, plug_ins, adapters "
            "RESTART IDENTITY CASCADE"
        )
    yield
    await close_pool()
