"""Test fixtures.

Tests run against the local Postgres started by docker-compose. Each test
gets a fresh pool bound to the test's event loop, and tables are truncated
between tests so order doesn't matter.

Providers are forced to stub by mutating the settings object directly.
This is deterministic — it does not depend on env-var vs .env precedence
(config.py calls load_dotenv(override=True), so env-var tricks don't work).
"""

import pytest_asyncio

from james_os import db as db_module
from james_os import embedder as embedder_module
from james_os import llm as llm_module
from james_os import research as research_module
from james_os import video as video_module
from james_os.config import settings
from james_os.db import acquire, close_pool, init_pool

# Force stub providers for the whole test session, regardless of .env,
# so tests never burn API quota or hit provider rate limits.
settings.embedding_provider = "stub"
settings.llm_provider = "stub"
settings.research_provider = "stub"
settings.video_provider = "stub"
embedder_module._embedder = None
llm_module._llm = None
research_module._provider = None
video_module._provider = None

# CRITICAL SAFETY: tests TRUNCATE tables. load_dotenv(override=True) in
# config.py means .env (which may point at Supabase / production) would
# otherwise win here. Pin the test DB to local Docker so the suite can
# never wipe a cloud database. Override via TEST_DATABASE_URL if needed.
import os  # noqa: E402

settings.database_url = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://james_os:james_os@localhost:5433/james_os",
)
settings.db_ssl = "disable"
if "supabase.co" in settings.database_url or "pooler.supabase.com" in settings.database_url:
    raise RuntimeError(
        "Refusing to run tests against a Supabase database — tests "
        "truncate tables. Point TEST_DATABASE_URL at a local Postgres."
    )


@pytest_asyncio.fixture(autouse=True)
async def fresh_pool():
    # Reset the global so each test starts with its own pool bound to
    # this test's event loop. Avoids the classic asyncpg + session-scope
    # "Future attached to a different loop" error.
    db_module._pool = None
    embedder_module._embedder = None
    llm_module._llm = None
    research_module._provider = None
    video_module._provider = None
    # Re-pin providers to stub before each test — a credentials test that
    # runs earlier may have triggered _auto_select_providers, flipping a
    # real key on, which then breaks unrelated tests downstream.
    settings.embedding_provider = "stub"
    settings.llm_provider = "stub"
    settings.research_provider = "stub"
    settings.video_provider = "stub"
    settings.avatar_provider = "stub"
    settings.assembly_provider = "stub"
    await init_pool()
    async with acquire() as conn:
        await conn.execute(
            "TRUNCATE queries, outbox, actions, events, plug_ins, adapters, "
            "video_jobs RESTART IDENTITY CASCADE"
        )
    yield
    await close_pool()
