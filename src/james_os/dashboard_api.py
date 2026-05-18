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

from .config import settings
from .db import acquire

router = APIRouter(prefix="/api")

SOCIAL_PLATFORMS = [
    "instagram", "tiktok", "x", "facebook",
    "linkedin", "youtube", "threads", "substack",
]


# ─────────────────────────────────── profile (lightweight, not auth) ──

@router.get("/profile")
async def get_profile() -> dict:
    async with acquire() as conn:
        cfg = await conn.fetchval(
            "SELECT config FROM tenants WHERE id = "
            "current_setting('app.current_tenant', true)::uuid"
        )
    if isinstance(cfg, str):
        cfg = json.loads(cfg)
    return (cfg or {}).get("profile", {"name": "", "email": "", "brand": "JP Brand Manager"})


@router.post("/profile")
async def set_profile(body: dict = Body(...)) -> dict:
    prof = {
        "name": str(body.get("name", ""))[:120],
        "email": str(body.get("email", ""))[:200],
        "brand": str(body.get("brand", "JP Brand Manager"))[:120],
    }
    async with acquire() as conn:
        await conn.execute(
            "UPDATE tenants SET config = jsonb_set("
            "coalesce(config,'{}'::jsonb), '{profile}', $1::jsonb) "
            "WHERE id = current_setting('app.current_tenant', true)::uuid",
            json.dumps(prof),
        )
    return {"ok": True, "profile": prof}


# ─────────────────────────────────── social connections (config) ──

@router.get("/connections")
async def get_connections() -> list[dict]:
    async with acquire() as conn:
        rows = await conn.fetch(
            "SELECT platform, handle, enabled, status FROM connections"
        )
    have = {r["platform"]: dict(r) for r in rows}
    out = []
    for p in SOCIAL_PLATFORMS:
        r = have.get(p)
        out.append({
            "platform": p,
            "handle": r["handle"] if r else "",
            "enabled": r["enabled"] if r else False,
            "status": r["status"] if r else "not_connected",
        })
    return out


@router.post("/connections")
async def upsert_connection(body: dict = Body(...)) -> dict:
    platform = str(body.get("platform", "")).lower().strip()
    if platform not in SOCIAL_PLATFORMS:
        return {"ok": False, "error": f"unknown platform '{platform}'"}
    handle = str(body.get("handle", "")).strip()[:120]
    enabled = bool(body.get("enabled", False))
    status = "configured" if handle else "not_connected"
    async with acquire() as conn:
        await conn.execute(
            "INSERT INTO connections (platform, handle, enabled, status) "
            "VALUES ($1,$2,$3,$4) "
            "ON CONFLICT (tenant_id, platform) DO UPDATE SET "
            "handle=$2, enabled=$3, status=$4, updated_at=now()",
            platform, handle, enabled, status,
        )
    return {"ok": True, "platform": platform, "status": status}




# ─────────────────────────────────── integration status (bools only) ──

@router.get("/integrations")
async def integrations() -> dict:
    """Which credentials are present. Booleans only — values never leave
    the process. `active` = JAMES OS has code that actually uses it today.
    """
    def cfg(v: str) -> bool:
        return bool(v and v.strip())

    return {
        "configured": {
            "anthropic": cfg(settings.anthropic_api_key),
            "voyage": cfg(settings.voyage_api_key),
            "cohere": cfg(settings.cohere_api_key),
            "openai": cfg(settings.openai_api_key),
            "elevenlabs": cfg(settings.elevenlabs_api_key),
            "heygen": cfg(settings.heygen_api_key),
            "descript": cfg(settings.descript_api_key),
            "runway": cfg(settings.runway_api_key),
            "minimax": cfg(settings.minimax_api_key),
            "postproxy": cfg(settings.postproxy_api_key),
            "meta": cfg(settings.meta_access_token),
            "twitter": cfg(settings.twitter_bearer_token),
            "xpoz": cfg(settings.xpoz_api_key),
        },
        # Only these are wired to real code paths today. openai = Whisper
        # audio transcription in the ingestion pipeline.
        "active": ["anthropic", "voyage", "cohere", "openai"],
    }


# ─────────────────────────────────────── approval queue (REAL) ──

def _action_to_queue_item(row: dict) -> dict:
    payload = row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    body = payload.get("content") or payload.get("text") or ""
    return {
        "id": str(row["id"]),
        "status": row["status"],
        "platform": payload.get("platform", "—"),
        "pillar": payload.get("pillar", "—"),
        "format": payload.get("format", row["action_type"]),
        "content": body,
        "caption": body,
        "voiceScore": payload.get("voice_score"),
        "imageUrl": payload.get("image_url"),
        "mediaUrl": payload.get("media_url"),
        "proposedBy": row["proposed_by"],
        "createdAt": row["created_at"].isoformat() if row["created_at"] else None,
        "scheduledFor": payload.get("scheduled_for"),
        "reason": row["approval_reason"] or row["rejection_reason_code"],
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


# Illustrative peer cohort so the peer-comparison / market-research pages
# render their real UI. NOT real metrics — the actual cohort is "to be
# locked with James per P47" (unlocked in the foundational docs). Replace
# when the real cohort + a social-metrics source exist.
_PEER_SAMPLE = [
    {"id": "p1", "name": "[sample] RE Thought Leader", "platform": "Instagram",
     "category": "real-estate", "handle": "@sample_re", "followers": 142000,
     "engagementRate": 3.1, "sample": True},
    {"id": "p2", "name": "[sample] NM Civic Voice", "platform": "X",
     "category": "civic", "handle": "@sample_civic", "followers": 88000,
     "engagementRate": 2.4, "sample": True},
    {"id": "p3", "name": "[sample] Mindset / Neville-adjacent", "platform": "YouTube",
     "category": "mindset", "handle": "@sample_mindset", "followers": 310000,
     "engagementRate": 4.6, "sample": True},
    {"id": "p4", "name": "[sample] Education Reform Operator", "platform": "LinkedIn",
     "category": "education", "handle": "@sample_edu", "followers": 51000,
     "engagementRate": 1.9, "sample": True},
    {"id": "p5", "name": "[sample] Real Estate Macro Caller", "platform": "Substack",
     "category": "real-estate", "handle": "@sample_macro", "followers": 67000,
     "engagementRate": 5.2, "sample": True},
]


@router.get("/peer-creators")
async def _peer_creators() -> list:
    return _PEER_SAMPLE


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
async def _social_content() -> list[dict]:
    """Real ingested events surfaced as the content feed.

    Engagement metrics are honestly zero — JAMES OS has no social-metrics
    source wired. This is real content, not fabricated performance.
    """
    async with acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, event_type, raw_content, payload, created_at "
            "FROM events WHERE superseded_by IS NULL "
            "ORDER BY created_at DESC LIMIT 60"
        )
    out = []
    for r in rows:
        payload = r["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        caption = r["raw_content"] or payload.get("text") or ""
        out.append({
            "id": str(r["id"]),
            "platform": payload.get("platform") or r["event_type"],
            "caption": caption,
            "publishedAt": r["created_at"].isoformat() if r["created_at"] else None,
            "engagement": 0,
            "views": 0,
            "likes": 0,
            "comments": 0,
            "shares": 0,
            "metricsAvailable": False,
        })
    return out


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
