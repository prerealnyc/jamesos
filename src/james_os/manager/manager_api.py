"""The manager API surface (P2) — action follow-ups, the daily heartbeat,
manual eye/brain triggers, and the peer discover→approve→track lifecycle.
Ported from bm2.0 routers/{actions,peers,planning}.py onto tenant-scoped
routes (tenant comes from the auth middleware; RLS scopes every query).

Failure semantics (the bm2.0 run_agent wrapper): an agent that dies mid-run
has already committed its failed job_run row (runs.finish_run) — the HTTP
layer surfaces a 502 with the reason instead of a fake 200.
"""

import asyncio
import json
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import db
from . import actions as action_service
from .sources_api import require_manager_v2

logger = logging.getLogger("manager.api")

router = APIRouter(tags=["manager"], dependencies=[Depends(require_manager_v2)])


def _502(agent: str, exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=502,
        detail=f"{agent} failed: {str(exc)[:300]} (the failed run is on record in job_runs)",
    )


def _item_out(it: dict) -> dict:
    return {
        **{k: it[k] for k in ("id", "kind", "title", "detail", "status", "related_peer", "dedupe_key")},
        "meta": it["meta"] or {},
        "updates": it["updates"] or [],
        "snooze_until": it["snooze_until"].isoformat() if it["snooze_until"] else None,
        "last_activity_at": it["last_activity_at"].isoformat() if it["last_activity_at"] else None,
    }


# ── action follow-ups ───────────────────────────────────────────────────────


@router.get("/manager/actions")
async def list_actions(status: str | None = None) -> dict:
    statuses = [s.strip() for s in status.split(",")] if status else None
    async with db.acquire() as conn:
        items = await action_service.list_actions(conn, statuses)
    return {"actions": [_item_out(i) for i in items]}


class StatusBody(BaseModel):
    status: str = Field(min_length=1)
    snooze_days: int = 3


@router.post("/manager/actions/{action_id}/status")
async def set_status(action_id: str, body: StatusBody) -> dict:
    try:
        async with db.acquire() as conn:
            item = await action_service.set_status(conn, action_id, body.status, body.snooze_days)
    except ValueError as err:
        raise HTTPException(status_code=404 if "not found" in str(err) else 422, detail=str(err)) from err
    return {"action": _item_out(item)}


class NoteBody(BaseModel):
    note: str = Field(min_length=1, max_length=2000)


@router.post("/manager/actions/{action_id}/note")
async def add_note(action_id: str, body: NoteBody) -> dict:
    try:
        async with db.acquire() as conn:
            item = await action_service.add_note(conn, action_id, body.note)
    except ValueError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    return {"action": _item_out(item)}


@router.delete("/manager/actions/{action_id}")
async def delete_action(action_id: str) -> dict:
    try:
        async with db.acquire() as conn:
            await action_service.delete_action(conn, action_id)
    except ValueError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    return {"deleted": action_id}


@router.post("/manager/actions/{action_id}/research-contact")
async def research_contact(action_id: str) -> dict:
    from . import contact_research

    try:
        return await contact_research.run(action_id=action_id, config={"trigger": "manual"})
    except ValueError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    except Exception as exc:  # noqa: BLE001
        raise _502("contact_research", exc) from exc


# ── the heartbeat ───────────────────────────────────────────────────────────


@router.post("/manager/daily-cycle")
async def run_daily_cycle() -> dict:
    """Manually run the full daily cycle now (the scheduler runs it on cadence)."""
    from . import heartbeat

    try:
        return await heartbeat.run_daily_cycle(config={"trigger": "manual"})
    except Exception as exc:  # noqa: BLE001
        raise _502("daily_cycle", exc) from exc


@router.get("/manager/daily-digest")
async def latest_digest() -> dict:
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT date, summary, items FROM daily_digests ORDER BY date DESC LIMIT 1"
        )
    if row is None:
        return {"date": None, "summary": "", "items": []}
    items = row["items"]
    if isinstance(items, str):
        items = json.loads(items)
    return {"date": row["date"].isoformat(), "summary": row["summary"], "items": items}


# ── manual eye / brain triggers ─────────────────────────────────────────────

_EYES = {
    "content-radar": ("opportunities", "eyes"),
    "trends": ("trends", "eyes"),
    "press": ("press", "eyes"),
    "questions": ("questions", "eyes"),
    "appearances": ("appearances", "eyes"),
}


@router.post("/manager/scan/{eye}")
async def trigger_eye(eye: str) -> dict:
    spec = _EYES.get(eye)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"unknown eye {eye!r}; one of {sorted(_EYES)}")
    module_name, pkg = spec
    import importlib

    module = importlib.import_module(f".{pkg}.{module_name}", package=__package__)
    try:
        return await module.run(config={"trigger": "manual"})
    except Exception as exc:  # noqa: BLE001
        raise _502(module_name, exc) from exc


@router.post("/manager/algorithm/refresh")
async def refresh_algorithm() -> dict:
    from . import algorithm

    try:
        return await algorithm.run(config={"trigger": "manual"})
    except Exception as exc:  # noqa: BLE001
        raise _502("algorithm", exc) from exc


@router.post("/manager/audit")
async def run_audit() -> dict:
    from . import auditor

    try:
        return await auditor.run(config={"trigger": "manual"})
    except Exception as exc:  # noqa: BLE001
        raise _502("auditor", exc) from exc


# ── peers: discover → approve → track ───────────────────────────────────────


async def _running(conn, agent: str) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT 1 FROM job_runs WHERE agent = $1 AND status = 'running' "
            "AND started_at > now() - interval '30 minutes' LIMIT 1",
            agent,
        )
    )


@router.post("/manager/peers/discover", status_code=202)
async def discover_peers() -> dict:
    """Kick discovery in the background; poll GET /manager/peers/discover/status.
    409 while a discovery is already in flight (bm2.0 semantics)."""
    from . import peers

    async with db.acquire() as conn:
        if await _running(conn, "peer_discovery"):
            raise HTTPException(status_code=409, detail="a peer discovery is already running")
        tenant_id = await conn.fetchval(
            "SELECT current_setting('app.current_tenant', true)::uuid"
        )

    async def _bg() -> None:
        try:
            await peers.discover(tenant_id, {"trigger": "manual"})
        except Exception:  # noqa: BLE001 — recorded on the job_run row
            logger.exception("background peer discovery failed")

    asyncio.create_task(_bg())
    return {"status": "started", "poll": "/manager/peers/discover/status"}


@router.get("/manager/peers/discover/status")
async def discover_status() -> dict:
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT status, output, error, started_at, finished_at FROM job_runs
               WHERE agent = 'peer_discovery' ORDER BY started_at DESC LIMIT 1"""
        )
    if row is None:
        return {"status": "never_run"}
    output = row["output"]
    if isinstance(output, str):
        output = json.loads(output or "{}")
    return {
        "status": row["status"],
        "output": output,
        "error": row["error"],
        "started_at": row["started_at"].isoformat(),
        "finished_at": row["finished_at"].isoformat() if row["finished_at"] else None,
    }


@router.get("/manager/peers/candidates")
async def peer_candidates() -> dict:
    from . import peers

    return {"candidates": await peers.candidates()}


class PeerDecision(BaseModel):
    handle: str = Field(min_length=1)


@router.post("/manager/peers/approve")
async def approve_peer(body: PeerDecision) -> dict:
    from . import peers

    try:
        return {"peer": await peers.set_status(None, body.handle, "tracked")}
    except ValueError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err


@router.post("/manager/peers/reject")
async def reject_peer(body: PeerDecision) -> dict:
    from . import peers

    try:
        return {"peer": await peers.set_status(None, body.handle, "rejected")}
    except ValueError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err


@router.post("/manager/peers/snapshot")
async def snapshot_peers() -> dict:
    from . import peers

    try:
        return await peers.snapshot_all(config={"trigger": "manual"})
    except Exception as exc:  # noqa: BLE001
        raise _502("peer", exc) from exc


# ── collaboration: per-peer plays + the visibility floor ────────────────────


@router.post("/manager/collab/generate")
async def generate_collab() -> dict:
    """Collaboration Strategy agent: a per-tracked-peer play + a visibility
    floor (always a next action, even with zero collaboration targets). The
    agent persists the plays as deduped action_items and stores the full
    report on its job_run."""
    from . import collaboration

    try:
        return await collaboration.run(config={"trigger": "manual"})
    except Exception as exc:  # noqa: BLE001
        raise _502("collaboration", exc) from exc


@router.get("/manager/collab/latest")
async def latest_collab() -> dict:
    """Latest collaboration report (from the newest succeeded run), or an
    empty shell if none generated yet."""
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT output FROM job_runs
               WHERE agent = 'collaboration' AND status = 'succeeded'
               ORDER BY started_at DESC LIMIT 1"""
        )
    output = row["output"] if row else None
    if isinstance(output, str):
        output = json.loads(output or "{}")
    report = (output or {}).get("report")
    if not report:
        return {"generated": False, "plays": [], "visibility_plays": []}
    return {"generated": True, **report}


# ── research: entity discovery → confirmed 7-lane fan-out ────────────────────
# The researcher routes live in research_api.py (own require_manager_v2 gate);
# riding this router keeps them registered without touching main.py, which
# only includes manager_api_router.
from .research_api import router as research_router  # noqa: E402

router.include_router(research_router)
