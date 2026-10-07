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
from datetime import UTC, datetime, timedelta
from uuid import UUID

from . import spend
from .db import acquire

_TICK_SECONDS = 300          # check for due work every 5 minutes
_MAX_CONCURRENT = 3          # recurring jobs are background work, not a race
_PAUSE_RETRY_SECONDS = 3600  # a spend-blocked job comes back this soon, not a cadence later


async def _registry() -> dict:
    # Imported lazily so module import order can't bite at startup.
    from .brand_research import run_daily_brand_research
    from .competitor_kickoff import run_first_posts
    from .competitor_media import run_competitor_media
    from .competitor_profile import run_competitor_profiles
    from .competitor_sync import run_competitor_refresh, run_competitor_sync
    from .competitor_template import run_templatize
    from .competitor_vision import run_competitor_vision
    from .intake_agent import run_brand_interview
    from .strategy import (
        run_peer_snapshot,
        run_playbook_refresh,
        run_weekly_prescription,
    )
    return {
        "daily_brand_research": run_daily_brand_research,
        "brand_interview": run_brand_interview,
        "playbook_refresh": run_playbook_refresh,
        "peer_snapshot": run_peer_snapshot,
        "weekly_prescription": run_weekly_prescription,
        "competitor_sync": run_competitor_sync,
        "competitor_refresh": run_competitor_refresh,
        "competitor_first_posts": run_first_posts,
        "competitor_templatize": run_templatize,
        "competitor_vision": run_competitor_vision,
        "competitor_profiles": run_competitor_profiles,
        "competitor_media": run_competitor_media,
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


async def _mark_skipped(job_id: UUID, reason: str, retry_in_seconds: float) -> None:
    """Stamp a spend-blocked job so it comes due again in `retry_in_seconds`
    (never later than its own cadence would have).

    The first version reused _mark(), whose last_run_at=now() deferred the job
    by its WHOLE cadence: a weekly_prescription that met the cap waited a week,
    and one 5-minute PAUSE_SPEND tick pushed every brand's jobs back a full
    cycle with nothing to re-queue them. Backdating by the cadence still moves
    the job off the head of the due queue for this tick, so a blocked tenant
    cannot starve the others out of the LIMIT 10."""
    async with acquire() as conn:
        await conn.execute(
            """UPDATE scheduled_jobs
                  SET last_run_at = now() - make_interval(hours => cadence_hours)
                                  + LEAST(make_interval(secs => $3::float8),
                                          make_interval(hours => cadence_hours)),
                      last_status = 'skipped', last_error = $2
                WHERE id = $1""",
            job_id, (reason or "")[:500] or None, float(max(60.0, retry_in_seconds)),
        )


def _seconds_until_utc_midnight(now: datetime | None = None) -> float:
    now = now or datetime.now(UTC)
    nxt = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return (nxt - now).total_seconds() + 300   # a few minutes past the reset


async def _run_one(job: dict, registry: dict) -> None:
    handler = registry.get(job["kind"])
    if handler is None:
        await _mark(job["id"], "failed", f"unknown job kind: {job['kind']}")
        return
    # The spend gate: PAUSE_SPEND or the brand's daily cap. Checked here, at the
    # one door every recurring job walks through, so no handler has to remember.
    # Marked 'skipped' and re-due soon: after the pause is likely lifted (an
    # hour) or just past the UTC midnight the cap resets at — not left due
    # (it would hog the LIMIT 10 every tick) and not deferred a whole cadence.
    gate = await spend.cap_status(job["tenant_id"])
    if gate["blocked"]:
        print(f"[scheduler] {job['kind']} for {job['tenant_id']}: skipped — {gate['reason']}")
        retry = _PAUSE_RETRY_SECONDS if gate["paused"] else _seconds_until_utc_midnight()
        await _mark_skipped(job["id"], gate["reason"], retry)
        return
    # Claim BEFORE running so a slow job isn't re-picked by the next tick.
    await _mark(job["id"], "running")
    import json
    cfg = job.get("config")
    if isinstance(cfg, str):
        cfg = json.loads(cfg)
    try:
        # The scope carries the job's TENANT as well as its name: llm.py and the
        # vision call sites record with no tenant_id, and without this every
        # brand's scheduled spend landed on the default tenant's ledger.
        with spend.job_scope(job["kind"], tenant_id=job["tenant_id"]):
            await handler(tenant_id=job["tenant_id"], config=cfg or {})
        await _mark(job["id"], "ok")
        # Log successes too, not only failures — else a healthy scheduler leaves no
        # evidence in the logs that it ran.
        print(f"[scheduler] {job['kind']} for {job['tenant_id']}: ok")
    except Exception as e:  # noqa: BLE001 — one tenant's failure never stops the loop
        print(f"[scheduler] {job['kind']} for {job['tenant_id']}: {e}")
        await _mark(job["id"], "failed", str(e))


async def weekly_roster_refresh(tenant_id: UUID | None = None) -> dict:
    """The autopilot loop's weekly research-roster refresh, behind the spend
    gate. It ends in a paid Apify scrape (trends.refresh_watchlist), and it was
    the one recurring entry point PAUSE_SPEND did not stop. Skips (logged,
    never raised) when paused or over the cap."""
    from .research_roster import maybe_weekly_refresh

    gate = await spend.cap_status(tenant_id)
    if gate["blocked"]:
        print(f"[scheduler] weekly roster refresh: skipped — {gate['reason']}")
        return {"skipped": True, "reason": gate["reason"]}
    with spend.job_scope("research_roster.weekly_refresh", tenant_id=tenant_id):
        return await maybe_weekly_refresh(tenant_id)


async def scheduler_loop() -> None:
    """Started from the FastAPI lifespan; cancelled on shutdown."""
    registry = await _registry()
    sem = asyncio.Semaphore(_MAX_CONCURRENT)
    print("[scheduler] loop started")
    while True:
        try:
            jobs = await _due_jobs()

            async def _guarded(j: dict) -> None:
                async with sem:
                    await _run_one(j, registry)

            if jobs:
                await asyncio.gather(*(_guarded(j) for j in jobs))
                print(f"[scheduler] tick ran {len(jobs)} job(s)")
        except asyncio.CancelledError:
            print("[scheduler] loop stopped")
            raise
        except Exception as e:  # noqa: BLE001 — the heartbeat must survive anything
            print(f"[scheduler] tick failed: {e}")
        await asyncio.sleep(_TICK_SECONDS)


__all__ = ["scheduler_loop"]
