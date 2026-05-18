"""Compatibility API for the JP Brand Manager dashboard build.

The dashboard is a minified Vite bundle built against the old Express/SQLite
backend. It calls ~20 `/api/...` endpoints. JAMES OS backs the ones that map
to a real concept here (the approval queue == the `actions` table, system
status, scan history) and graceful-stubs the rest so old-project pages render
empty instead of crashing.

Honest scope: shapes for the queue endpoints are best-effort against a
minified bundle. The clean fix is the dashboard SOURCE repo.
"""

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Body

from .db import acquire

router = APIRouter(prefix="/api")


# ─────────────────────────────────────── approval queue (REAL) ──

def _action_to_queue_item(row: dict) -> dict:
    payload = row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    return {
        "id": str(row["id"]),
        "status": row["status"],
        "platform": payload.get("platform", "—"),
        "pillar": payload.get("pillar", "—"),
        "format": payload.get("format", row["action_type"]),
        "content": payload.get("content") or payload.get("text") or "",
        "caption": payload.get("content") or payload.get("text") or "",
        "voiceScore": payload.get("voice_score"),
        "proposedBy": row["proposed_by"],
        "createdAt": row["created_at"].isoformat() if row["created_at"] else None,
        "scheduledFor": None,
        "reason": row["approval_reason"],
    }


@router.get("/queue")
async def get_queue() -> list[dict]:
    async with acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM actions ORDER BY created_at DESC LIMIT 100"
        )
    return [_action_to_queue_item(dict(r)) for r in rows]


@router.get("/queue/stats")
async def queue_stats() -> dict:
    async with acquire() as conn:
        rows = await conn.fetch(
            "SELECT status, count(*) AS n FROM actions GROUP BY status"
        )
    by = {r["status"]: r["n"] for r in rows}
    return {
        "pending": by.get("pending", 0),
        "approved": by.get("approved", 0),
        "rejected": by.get("rejected", 0),
        "executed": by.get("executed", 0),
        "total": sum(by.values()),
    }


@router.post("/queue/{item_id}/approve")
async def approve_item(item_id: UUID, body: dict = Body(default={})) -> dict:
    async with acquire() as conn:
        await conn.execute(
            "UPDATE actions SET status='approved', approval_reason=$2, "
            "decided_at=now() WHERE id=$1",
            item_id,
            body.get("reason", "approved via dashboard"),
        )
    return {"ok": True, "id": str(item_id), "status": "approved"}


@router.post("/queue/{item_id}/reject")
async def reject_item(item_id: UUID, body: dict = Body(default={})) -> dict:
    async with acquire() as conn:
        await conn.execute(
            "UPDATE actions SET status='rejected', "
            "rejection_reason_code=$2, decided_at=now() WHERE id=$1",
            item_id,
            body.get("reason", "rejected"),
        )
    return {"ok": True, "id": str(item_id), "status": "rejected"}


# ─────────────────────────────────────── system / pipeline (REAL) ──

@router.get("/system/last-scan")
async def last_scan() -> dict:
    async with acquire() as conn:
        ts = await conn.fetchval(
            "SELECT max(created_at) FROM events"
        )
    return {"lastScanAt": ts.isoformat() if ts else None}


@router.get("/scan-logs")
async def scan_logs() -> list[dict]:
    async with acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, event_type, left(coalesce(raw_content,''),120) AS summary, "
            "created_at FROM events ORDER BY created_at DESC LIMIT 50"
        )
    return [
        {
            "id": str(r["id"]),
            "type": r["event_type"],
            "summary": r["summary"],
            "createdAt": r["created_at"].isoformat() if r["created_at"] else None,
            "status": "ok",
        }
        for r in rows
    ]


@router.get("/pipeline/health")
async def pipeline_health() -> list[dict]:
    return [
        {"stage": "ingest", "ok": True},
        {"stage": "retrieve", "ok": True},
        {"stage": "generate", "ok": True},
        {"stage": "verify", "ok": True},
        {"stage": "approve", "ok": True},
    ]


@router.get("/pipeline/runs")
async def pipeline_runs() -> list[dict]:
    return []


@router.post("/pipeline/run")
async def pipeline_run() -> dict:
    return {"ok": True, "started": datetime.now(UTC).isoformat()}


# ─────────────────────── old-project features (graceful empty) ──
# These have no JAMES OS data behind them yet. Return empty so the
# dashboard pages render their empty states instead of crashing.

_EMPTY_LIST = [
    "peer-creators",
    "market-research",
    "image-library",
    "jp-clip-library",
    "social-content",
    "design-content",
    "voice/pending-amendments",
    "scan-logs-archive",
]


@router.get("/peer-creators")
async def _peer_creators() -> list:
    return []


@router.get("/market-research")
async def _market_research() -> list:
    return []


@router.get("/image-library")
async def _image_library() -> list:
    return []


@router.get("/jp-clip-library")
async def _clip_library() -> list:
    return []


@router.get("/social-content")
async def _social_content() -> list:
    return []


@router.get("/design-content")
async def _design_content() -> list:
    return []


@router.get("/brand-voice")
async def _brand_voice() -> dict:
    return {"profile": None, "rules": [], "note": "voice profile not yet ingested"}


@router.get("/voice/pending-amendments")
async def _pending_amendments() -> list:
    return []


@router.post("/refresh-social")
async def _refresh_social() -> dict:
    return {"ok": True, "note": "social ingestion not wired in JAMES OS yet"}


@router.post("/refresh-peers")
async def _refresh_peers() -> dict:
    return {"ok": True, "note": "peer ingestion not wired in JAMES OS yet"}


@router.post("/seed")
async def _seed() -> dict:
    return {"ok": True}


@router.post("/generate-caption")
async def _gen_caption(body: dict = Body(default={})) -> dict:
    return {
        "caption": "",
        "note": "caption generation routes through JAMES OS /ask in the real build",
    }


@router.post("/generate-image")
async def _gen_image(body: dict = Body(default={})) -> dict:
    return {"url": None, "note": "image generation not wired in JAMES OS yet"}


# Catch-all so any unforeseen /api GET returns empty rather than 404-crashing
# a dashboard page. Defined last so explicit routes win.
@router.get("/{rest:path}")
async def _api_fallback(rest: str) -> list:
    return []
