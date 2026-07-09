"""Table-driven recurring-job scheduler — the platform's heartbeat.

One lightweight asyncio loop inside the backend process reads
`scheduled_jobs` (tenant_id, kind, cadence_hours, enabled, last_run_at) and
runs due jobs through a registry. Durable across restarts (state lives in
the table), per-tenant fan-out, deliberately NOT Celery/RabbitMQ — one
process is correct at this scale; if job volume ever demands it, a separate
worker service can consume the same table unchanged (see ARCHITECTURE.md).

Jobs must be idempotent and tenant-bound: every handler receives the
tenant_id from its row and passes it explicitly (never relies on a request
contextvar — there is no request here).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

from .db import acquire

_TICK_SECONDS = 300          # check for due work every 5 minutes
_MAX_CONCURRENT = 3          # recurring jobs are background work, not a race


async def _registry() -> dict:
    # Imported lazily so module import order can't bite at startup.
    #
    # The five pre-merge intelligence kinds (daily_brand_research,
    # brand_interview, playbook_refresh, peer_snapshot, weekly_prescription)
    # are RETIRED per the unification plan (P2): the bm2.0 daily cycle + eyes
    # replace them — one brain, no duplicate research spend. Their handler
    # modules survive untouched; heartbeat.ensure_manager_jobs() removes any
    # lingering rows so no dormant job double-spends.
    from .manager.heartbeat import (
        run_daily_cycle,
        run_peer_snapshot,
        run_weekly_strategist,
    )
    return {
        "manager_daily_cycle": run_daily_cycle,
        "manager_peer_snapshot": run_peer_snapshot,
        "manager_weekly_strategist": run_weekly_strategist,
    }


async def _due_jobs() -> list[dict]:
    # Platform-level read across ALL tenants (scheduled_jobs carries no RLS
    # by design — see migration 050). acquire() still needs a tenant for
    # set_config; the default is fine because the table isn't row-filtered.
    async with acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, tenant_id, kind, cadence_hours, config
                 FROM scheduled_jobs
                WHERE enabled
                  AND (last_run_at IS NULL
                       OR last_run_at < now() - (cadence_hours || ' hours')::interval)
                ORDER BY last_run_at ASC NULLS FIRST
                LIMIT 10"""
        )
    return [dict(r) for r in rows]


async def _mark(job_id: UUID, status: str, error: str | None = None) -> None:
    async with acquire() as conn:
        await conn.execute(
            "UPDATE scheduled_jobs SET last_run_at=$2, last_status=$3, "
            "last_error=$4 WHERE id=$1",
            job_id, datetime.now(UTC), status, (error or "")[:500] or None,
        )


async def _run_one(job: dict, registry: dict) -> None:
    handler = registry.get(job["kind"])
    if handler is None:
        await _mark(job["id"], "failed", f"unknown job kind: {job['kind']}")
        return
    # Claim BEFORE running so a slow job isn't re-picked by the next tick.
    await _mark(job["id"], "running")
    import json
    cfg = job.get("config")
    if isinstance(cfg, str):
        cfg = json.loads(cfg)
    try:
        await handler(tenant_id=job["tenant_id"], config=cfg or {})
        await _mark(job["id"], "ok")
    except Exception as e:  # noqa: BLE001 — one tenant's failure never stops the loop
        print(f"[scheduler] {job['kind']} for {job['tenant_id']}: {e}")
        await _mark(job["id"], "failed", str(e))


async def scheduler_loop() -> None:
    """Started from the FastAPI lifespan; cancelled on shutdown."""
    from .config import settings

    if not settings.manager_scheduler_enabled:
        # Kill-switch (MANAGER_SCHEDULER_ENABLED=false): a secondary instance
        # pointed at a shared/production DB must NEVER claim scheduled_jobs
        # rows — the deployed instance owns the heartbeat.
        print("[scheduler] disabled by MANAGER_SCHEDULER_ENABLED — no jobs will run here")
        return
    registry = await _registry()
    sem = asyncio.Semaphore(_MAX_CONCURRENT)
    try:
        from .manager.heartbeat import ensure_manager_jobs

        await ensure_manager_jobs()
    except Exception as e:  # noqa: BLE001 — registration must never kill the loop
        print(f"[scheduler] manager job registration failed: {e}")
    print("[scheduler] loop started")
    while True:
        try:
            jobs = await _due_jobs()

            async def _guarded(j: dict) -> None:
                async with sem:
                    await _run_one(j, registry)

            if jobs:
                await asyncio.gather(*(_guarded(j) for j in jobs))
        except asyncio.CancelledError:
            print("[scheduler] loop stopped")
            raise
        except Exception as e:  # noqa: BLE001 — the heartbeat must survive anything
            print(f"[scheduler] tick failed: {e}")
        await asyncio.sleep(_TICK_SECONDS)


__all__ = ["scheduler_loop"]
