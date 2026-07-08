"""job_runs bookkeeping — every manager agent execution is wrapped in one.
Ported from bm2.0 backend/app/services/runs.py; the SQLite lock dance is
gone (asyncpg), and token usage feeds the existing credit meter via the
tokens_in/tokens_out columns.

start_run binds the run as the "active run" contextvar so LLM routers
(providers.live.AnthropicRouter) can accrue token usage onto it without the
agents threading the run object through every call; finish_run writes the
totals and unbinds. Nested runs (e.g. creator -> reviewer) rebind for the
inner run's duration; usage after an inner run finishes is untracked until
the next start_run (accepted v0 simplification).
"""

import json
from contextvars import ContextVar
from dataclasses import dataclass, field
from uuid import UUID

from .. import db


@dataclass
class RunHandle:
    id: str
    agent: str
    tenant_id: UUID | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    input: dict = field(default_factory=dict)


_ACTIVE_RUN: ContextVar[RunHandle | None] = ContextVar("active_job_run", default=None)


def bind_active_run(run: RunHandle | None) -> None:
    _ACTIVE_RUN.set(run)


def active_run() -> RunHandle | None:
    return _ACTIVE_RUN.get()


def add_usage(tokens_in: int = 0, tokens_out: int = 0) -> None:
    """Called by LLM routers after each completion; accrues onto the active run."""
    run = _ACTIVE_RUN.get()
    if run is not None:
        run.tokens_in += tokens_in
        run.tokens_out += tokens_out


async def start_run(
    agent: str, trigger: str = "manual", input: dict | None = None, tenant_id: UUID | None = None
) -> RunHandle:
    """Insert the 'running' row in its own transaction (no write lock held
    while the agent does slow provider I/O), then bind as the active run."""
    async with db.acquire(tenant_id) as conn:
        run_id = await conn.fetchval(
            "INSERT INTO job_runs (agent, trigger, input) VALUES ($1, $2, $3::jsonb) RETURNING id",
            agent, trigger, json.dumps(input or {}),
        )
    handle = RunHandle(id=str(run_id), agent=agent, tenant_id=tenant_id, input=input or {})
    bind_active_run(handle)
    return handle


async def finish_run(run: RunHandle, output: dict | None = None, error: str = "") -> RunHandle:
    async with db.acquire(run.tenant_id) as conn:
        await conn.execute(
            """UPDATE job_runs SET status = $2, output = $3::jsonb, error = $4,
                   tokens_in = $5, tokens_out = $6, finished_at = now()
               WHERE id = $1::uuid""",
            run.id, "failed" if error else "succeeded", json.dumps(output or {}), error,
            run.tokens_in, run.tokens_out,
        )
    if _ACTIVE_RUN.get() is run:
        bind_active_run(None)
    return run
