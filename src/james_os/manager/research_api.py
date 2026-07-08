"""Research routes — the onboarding discover/confirm/status trio ported from
bm2.0 routers/onboarding.py onto tenant-scoped manager routes (tenant comes
from the auth middleware; RLS scopes every query).

Discover stays synchronous — it is the fast path ("Is this your brand?"
candidates only; nothing is written). Confirmed research is asynchronous:
the 7-lane fan-out can exceed the Next dev proxy's ~30s timeout, so POST
/manager/research/confirmed schedules a background task and returns 202
immediately; the browser polls GET /manager/research/status (backed by the
latest researcher job_runs row). 409 while a run is already in flight —
the same background/poll/409 shape as manager_api.discover_peers.

The discover seed {name, entity_type, website, socials, location} is
persisted in tenants.config['research_seed'] so the confirm step can rebuild
the primary-source sets without re-asking the client (donor rule).
"""

import asyncio
import json
import logging

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import db
from .contracts import EntityType
from .sources_api import require_manager_v2

logger = logging.getLogger("manager.research")

router = APIRouter(tags=["manager-research"], dependencies=[Depends(require_manager_v2)])


class DiscoverBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    entity_type: EntityType = EntityType.COMPANY
    website: str | None = None
    socials: list[str] = Field(default_factory=list)
    location: str | None = None


class ConfirmBody(BaseModel):
    confirmed_urls: list[str] = Field(min_length=1)


async def _get_seed(conn: asyncpg.Connection) -> dict:
    cfg = await conn.fetchval(
        "SELECT config FROM tenants WHERE id = current_setting('app.current_tenant', true)::uuid"
    )
    if isinstance(cfg, str):
        cfg = json.loads(cfg or "{}")
    return (cfg or {}).get("research_seed") or {}


async def _set_seed(conn: asyncpg.Connection, seed: dict) -> None:
    await conn.execute(
        "UPDATE tenants SET config = jsonb_set(coalesce(config,'{}'::jsonb), "
        "'{research_seed}', $1::jsonb) "
        "WHERE id = current_setting('app.current_tenant', true)::uuid",
        json.dumps(seed),
    )


async def _running(conn: asyncpg.Connection) -> bool:
    """A committed 'running' researcher row is the in-flight signal —
    runs.start_run commits it in its own transaction before the fan-out, so
    the double-confirm 409 guard sees it immediately (donor semantics)."""
    return bool(
        await conn.fetchval(
            "SELECT 1 FROM job_runs WHERE agent = 'researcher' AND status = 'running' "
            "AND started_at > now() - interval '30 minutes' LIMIT 1"
        )
    )


@router.post("/manager/research/discover")
async def research_discover(body: DiscoverBody) -> dict:
    """Entity resolution for "Is this your brand?" — synchronous, candidates
    only; nothing is written until a human confirms."""
    from . import researcher

    seed = {
        "name": body.name.strip(),
        "entity_type": body.entity_type.value,
        "website": body.website,
        "socials": body.socials,
        "location": body.location,
    }
    async with db.acquire() as conn:
        await _set_seed(conn, seed)
    try:
        report = await researcher.run(seed=seed, mode="discover", config={"trigger": "manual"})
    except Exception as exc:  # noqa: BLE001 — failed run already on record in job_runs
        raise HTTPException(
            status_code=502,
            detail=f"researcher failed: {str(exc)[:300]} (the failed run is on record in job_runs)",
        ) from exc
    return {
        "candidates": [c.model_dump() for c in report.candidates],
        "failures": report.failures,
    }


@router.post("/manager/research/confirmed", status_code=202)
async def research_confirmed(body: ConfirmBody) -> dict:
    """Kick the confirmed 7-lane fan-out in the background; poll
    GET /manager/research/status. 409 while a run is already in flight."""
    from . import researcher

    async with db.acquire() as conn:
        if await _running(conn):
            raise HTTPException(status_code=409, detail="research already running")
        tenant_id = await conn.fetchval(
            "SELECT current_setting('app.current_tenant', true)::uuid"
        )
        seed = await _get_seed(conn)
    if not str(seed.get("name") or "").strip():
        raise HTTPException(
            status_code=422,
            detail="no research seed for this tenant — POST /manager/research/discover first",
        )
    confirmed_urls = [str(u) for u in body.confirmed_urls]

    async def _bg() -> None:
        try:
            await researcher.run(
                tenant_id,
                seed=seed,
                mode="confirmed",
                confirmed_urls=confirmed_urls,
                config={"trigger": "manual"},
            )
        except Exception:  # noqa: BLE001 — recorded on the job_run row
            logger.exception("background confirmed research failed")

    asyncio.create_task(_bg())
    return {"status": "started", "state": "running", "poll": "/manager/research/status"}


def _parse_json(value: object) -> dict:
    if isinstance(value, str):
        return json.loads(value or "{}")
    return value or {}


@router.get("/manager/research/status")
async def research_status() -> dict:
    """Poll target for the async confirm flow: the latest researcher job_run
    (state running | succeeded | failed), with the lane_stats / fields_written
    / failures the donor status route exposed."""
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT status, input, output, error, started_at, finished_at FROM job_runs
               WHERE agent = 'researcher' ORDER BY started_at DESC LIMIT 1"""
        )
    if row is None:
        return {
            "state": "none",
            "mode": None,
            "started_at": None,
            "finished_at": None,
            "lane_stats": {},
            "fields_written": 0,
            "failures": [],
            "error": "",
        }
    output = _parse_json(row["output"])
    run_input = _parse_json(row["input"])
    return {
        "state": row["status"],  # running | succeeded | failed
        "mode": run_input.get("mode") or output.get("mode"),
        "started_at": row["started_at"].isoformat() if row["started_at"] else None,
        "finished_at": row["finished_at"].isoformat() if row["finished_at"] else None,
        "lane_stats": output.get("lane_stats") or {},
        "fields_written": int(output.get("fields_written") or 0),
        "failures": output.get("failures") or [],
        "error": row["error"] or "",
    }
