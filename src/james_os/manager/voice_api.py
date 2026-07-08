"""Brand Voice harvest endpoints — the onboarding 'here's the voice we
learned' surface: the AUTO-pull twin of the manual Voice Studio
(voice_ingest_api's /voice/* Drive door, which stays untouched). Ported from
bm2.0 backend/app/routers/voice.py onto tenant-scoped /manager/voice/* routes
so nothing collides with the existing manual door.

    POST /manager/voice/harvest        pull + distil the brand's voice (202, background)
    POST /manager/voice/add-source     transcribe one operator-supplied URL into voice (202)
    GET  /manager/voice/profile        derived voice.* fields + exemplar counts by origin
    GET  /manager/voice/status         latest voice_harvester job_run (the poll target)

Harvest runs in the background (transcription can be slow), mirroring
manager_api's discover_peers: 202 + a job_runs poll, 409 while a harvest is
already in flight. All routes gated on manager_v2; tenancy comes from the
auth middleware (RLS scopes every query).
"""

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import db
from . import profile as profile_service
from . import voice_harvester
from .sources_api import require_manager_v2

logger = logging.getLogger("manager.voice")

router = APIRouter(tags=["manager-voice"], dependencies=[Depends(require_manager_v2)])


async def _running(conn) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT 1 FROM job_runs WHERE agent = $1 AND status = 'running' "
            "AND started_at > now() - interval '30 minutes' LIMIT 1",
            voice_harvester.AGENT,
        )
    )


async def _kick(extra_urls: list[str]) -> dict:
    """Start a background harvest (bm2.0 semantics: 409 while one is in
    flight; the failed run is on record in job_runs either way)."""
    async with db.acquire() as conn:
        if await _running(conn):
            raise HTTPException(status_code=409, detail="a voice harvest is already running")
        tenant_id = await conn.fetchval(
            "SELECT current_setting('app.current_tenant', true)::uuid"
        )

    async def _bg() -> None:
        try:
            await voice_harvester.run(tenant_id, {"trigger": "manual", "extra_urls": extra_urls})
        except Exception:  # noqa: BLE001 — recorded on the job_run row
            logger.exception("background voice harvest failed")

    asyncio.create_task(_bg())
    return {"status": "started", "poll": "/manager/voice/status"}


@router.post("/manager/voice/harvest", status_code=202)
async def harvest() -> dict:
    """Pull the brand's own voice from its channel + posts and distil a
    profile. Runs in the background; poll GET /manager/voice/status."""
    return await _kick([])


class AddSourceBody(BaseModel):
    url: str = Field(min_length=4)


@router.post("/manager/voice/add-source", status_code=202)
async def add_source(body: AddSourceBody) -> dict:
    """Transcribe one operator-supplied talk/interview/podcast URL into the
    voice corpus (the Voice Studio's manual upload, but by URL)."""
    url = body.url.strip()
    return {**await _kick([url]), "url": url}


@router.get("/manager/voice/status")
async def harvest_status() -> dict:
    """Latest voice_harvester job_run — the 202's poll target (mirrors
    /manager/peers/discover/status)."""
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT status, output, error, started_at, finished_at FROM job_runs
               WHERE agent = $1 ORDER BY started_at DESC LIMIT 1""",
            voice_harvester.AGENT,
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


@router.get("/manager/voice/profile")
async def voice_profile() -> dict:
    """The derived voice profile (voice.* fields) + the shared voice corpus
    broken down by origin (harvested / audited / Voice Studio uploads)."""
    async with db.acquire() as conn:
        running = await _running(conn)

        voice: dict = {}
        for f in await profile_service.current_fields(conn, "voice"):
            key = f["field_key"].split(".", 1)[1] if "." in f["field_key"] else f["field_key"]
            # rows arrive version DESC per key — first one wins
            voice.setdefault(
                key, f["value"].get("v") if isinstance(f["value"], dict) else f["value"]
            )

        origin_rows = await conn.fetch(
            """SELECT coalesce(payload->>'origin', 'uploaded') AS origin, count(*) AS n
               FROM events
               WHERE payload->>'category' = 'voice_corpus' AND superseded_by IS NULL
               GROUP BY 1 ORDER BY 2 DESC"""
        )

    by_origin = {r["origin"]: int(r["n"]) for r in origin_rows}
    total = sum(by_origin.values())
    return {
        "state": "running" if running else ("ready" if total else "empty"),
        "voice": voice,
        "exemplar_count": total,
        "exemplars_by_origin": by_origin,
    }
